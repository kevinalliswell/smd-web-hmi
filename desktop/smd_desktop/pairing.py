"""Administrator-only offline pairing transaction; no network or key-bearing output."""

import hashlib
import json
import os
import secrets
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path

from dotenv import dotenv_values
from pydantic import TypeAdapter

from app.hostcomm.protocol import now_iso
from app.hostcomm.v2_contract.codec import command_digest, digest
from app.hostcomm.v2_contract.messages import OperationResult
from app.hostcomm.v2_contract.types import Identifier
from app.hostcomm.v2_security import _windows_permissions, load_psk, psk_identity
from app.services.sqlite_backup import backup_sqlite
from app.services.v2_operations import COMMAND_ADAPTER

from .single_instance import single_instance
from .storage import atomic_json


def _private_fd(path: Path) -> int:
    """Create an empty file with verified access before the caller writes secrets."""
    if os.name != "nt":
        return os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    pointer = ctypes.c_void_p
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        pointer,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.LocalFree.argtypes = [pointer]
    advapi.ConvertStringSidToSidW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(pointer)]
    advapi.ConvertStringSidToSidW.restype = wintypes.BOOL
    advapi.SetSecurityInfo.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
    ] + [pointer] * 4
    advapi.SetSecurityInfo.restype = wintypes.DWORD
    # GENERIC_READ | GENERIC_WRITE | WRITE_OWNER; CREATE_NEW prevents replacing a
    # pre-existing path. No sharing while ownership, DACL and contents are set.
    handle = kernel.CreateFileW(str(path), 0xC0080000, 0, None, 1, 0x00200080, None)
    if handle == pointer(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    administrator = pointer()
    fd = None
    try:
        if not advapi.ConvertStringSidToSidW("S-1-5-32-544", ctypes.byref(administrator)):
            raise ctypes.WinError(ctypes.get_last_error())
        # The creator's TokenOwner may be an individual administrator, which is
        # not the later LocalService process identity. Use the stable group SID.
        error = advapi.SetSecurityInfo(handle, 1, 1, administrator, None, None, None)
        if error:
            raise ctypes.WinError(error)
        fd = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
        handle = None  # The CRT descriptor now owns and closes this handle.
        _windows_permissions(fd)
        return fd
    except BaseException:
        if fd is not None:
            os.close(fd)
        raise
    finally:
        if handle is not None:
            kernel.CloseHandle(handle)
        if administrator.value:
            kernel.LocalFree(administrator)


def _private_folder(folder: Path):
    if folder.is_symlink():
        raise RuntimeError("Pairing directory cannot be a symlink")
    folder.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        os.chmod(folder, 0o700)
    probe = folder / (".acl-probe-" + uuid.uuid4().hex)
    fd = None
    try:
        fd = _private_fd(probe)  # Verify inherited ProgramData ACL before writing secrets.
    finally:
        if fd is not None:
            os.close(fd)
        probe.unlink(missing_ok=True)


def _private_text(path: Path, value: str):
    _private_bytes(path, value.encode("utf-8"))


def _private_bytes(path: Path, value: bytes):
    """Preserve exact bytes on Windows and never create a broadly readable temporary key."""
    _private_folder(path.parent)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        fd = _private_fd(temporary)
        with os.fdopen(fd, "wb") as output:
            output.write(value)
            output.flush()
            os.fsync(output.fileno())
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.MoveFileExW.argtypes = [
                wintypes.LPCWSTR,
                wintypes.LPCWSTR,
                wintypes.DWORD,
            ]
            kernel.MoveFileExW.restype = wintypes.BOOL
            if not kernel.MoveFileExW(str(temporary), str(path), 0x1 | 0x8):
                raise ctypes.WinError(ctypes.get_last_error())
        else:
            os.replace(temporary, path)
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def _rows(connection, query, parameters=()):
    cursor = connection.execute(query, parameters)
    names = [item[0] for item in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def _object(raw):
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Expected a stored object")
    return value


def _wire_evidence(row, device_ids):
    """Retained terminal evidence, including identities and the exact request digest."""
    try:
        TypeAdapter(Identifier).validate_python(row["device_id"])
        TypeAdapter(Identifier).validate_python(row["msg_id"])
        request = COMMAND_ADAPTER.validate_json(row["request_json"]).model_dump()
        result = OperationResult.model_validate_json(row["result_json"]).model_dump()
        if row["device_id"] not in device_ids or row["status"] not in {
            "applied",
            "rejected",
            "interrupted",
        }:
            return None
        for key in (
            "operation_id",
            "controller_epoch",
            "command_seq",
            "request_digest",
        ):
            if request[key] != row[key] or result[key] != row[key]:
                return None
        if (
            request["command"] != row["command"]
            or result["status"] != row["status"]
            or result["reason"] != row["reason"]
            or command_digest(request) != row["request_digest"]
            or digest(
                {
                    "command": row["command"],
                    "params": request["params"],
                    "actor": row["actor"],
                    "role": row["role"],
                }
            )
            != row["business_digest"]
        ):
            return None
        return {**row, "request": request, "result": result}
    except (ValueError, TypeError, KeyError):
        return None


def _linked_wire(outer, wires, epochs):
    """The HTTP message is namespaced by its historical controller epoch, not today's pairing."""
    commands = {
        "start_test": "start_run",
        "stop_test": "stop_run",
        "set_parameters": "activate_recipe",
        "ack_run": "ack_run",
        "ack_alarm": "ack_alarm",
        "reset_fault": "reset_fault",
    }
    try:
        matches = [
            wires[key] for epoch in epochs if (key := uuid.uuid5(uuid.UUID(hex=epoch), outer["msg_id"]).hex) in wires
        ]
        if len(matches) != 1:
            return None
        row = matches[0]
        parameters = _object(outer["params_json"])
        request_hash = hashlib.sha256(
            json.dumps(
                {"command": outer["command"], "params": parameters},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        if (
            commands.get(outer["command"]) != row["command"]
            or outer["operator_id"] != row["actor"]
            or outer["operator_role"] != row["role"]
            or outer["request_hash"] != request_hash
        ):
            return None
        for raw in (outer["result_json"], outer["device_result_json"]):
            if raw:
                summary = _object(raw)
                if (
                    summary.get("wire_operation_id", row["operation_id"]) != row["operation_id"]
                    or summary.get("command_seq", row["command_seq"]) != row["command_seq"]
                ):
                    return None
        return row
    except (ValueError, TypeError, KeyError, AttributeError):
        return None


def _unstarted_reservation(connection, session, operations, wires, epochs):
    """A rejected reservation never had a physical run; do not invent a safe-completion time."""
    try:
        if (
            session["end_time"] is None
            or session["phase"] != "start_rejected"
            or not (session["end_reason"] or "").startswith("start_rejected:")
            or any(
                session[key] is not None
                for key in (
                    "measurement_completed_at",
                    "safety_completed_at",
                    "stop_requested_at",
                )
            )
        ):
            return False
        snapshot = _object(session["recipe_snapshot_json"])
        outer = operations.get(snapshot.get("operation_id"))
        if (
            not outer
            or snapshot.get("protocol_version") != "2.0"
            or outer["command"] != "start_test"
            or outer["status"] != "rejected"
        ):
            return False
        wire = _linked_wire(outer, wires, epochs)
        if not wire or wire["command"] != "start_run" or wire["status"] != "rejected":
            return False
        params = wire["request"]["params"]
        bindings = _rows(
            connection,
            "SELECT * FROM v2_run_binding WHERE test_id=?",
            (session["test_id"],),
        )
        if len(bindings) != 1:
            return False
        binding = bindings[0]
        if (
            _object(outer["params_json"]).get("test_id") != session["test_id"]
            or outer["operator_id"] != session["operator_id"]
            or binding["device_id"] != wire["device_id"]
            or binding["run_id"] != params["run_id"]
            or binding["recipe_digest"] != params["recipe_digest"]
            or binding["profile_digest"] != params["safety_profile_digest"]
            or wire["result"]["run_id"] not in {None, params["run_id"]}
        ):
            return False
        basis = _object(session["measurement_basis_json"])
        v2 = basis.get("v2", {})
        if (
            basis.get("invalid_previous_basis")
            or not isinstance(v2, dict)
            or set(v2) - {"profile_snapshot", "profile_digest"}
        ):
            return False
        if v2.get("profile_digest", binding["profile_digest"]) != binding["profile_digest"]:
            return False
        if connection.execute("SELECT 1 FROM sample_point WHERE test_id=? LIMIT 1", (session["test_id"],)).fetchone():
            return False
        return (
            connection.execute(
                "SELECT 1 FROM v2_source_record WHERE device_id=? AND run_id=? LIMIT 1",
                (binding["device_id"], binding["run_id"]),
            ).fetchone()
            is None
        )
    except (ValueError, TypeError, KeyError):
        return False


class PairingTransaction:
    """Caller holds Updater mutex; this class also holds the backend process mutex.

    Existing identities and keys are retained in protected backups. Recovery completes
    one staged transaction, never creates a fresh key or rolls the command watermark back.
    """

    def __init__(self, data: Path, platform):
        self.data, self.platform = data.resolve(), platform
        self.env_path = self.data / "config/service.env"
        self.journal_path = self.data / "updates/pairing.json"
        self.gate_path = self.data / "maintenance.json"

    def _stopped(self):
        checker = getattr(self.platform, "require_stopped", None)
        if checker is not None:
            checker()
            return
        import win32service as ws

        if self.platform._service(ws.QueryServiceStatus, ws.SERVICE_QUERY_STATUS)[1] != ws.SERVICE_STOPPED:
            raise RuntimeError("Pairing requires the service to be already stopped")

    def _record(self, journal, phase):
        journal["phase"] = phase
        atomic_json(self.journal_path, journal)

    @staticmethod
    def _guard(connection):
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required = {
            "test_session",
            "operation",
            "v2_operation",
            "v2_controller_identity",
            "v2_run_binding",
            "sample_point",
            "v2_source_record",
        }
        if not required <= tables:
            raise RuntimeError("Upgrade the database schema before offline pairing")
        if connection.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise RuntimeError("Database integrity check failed")
        device_ids = {row[0] for row in connection.execute("SELECT device_id FROM v2_controller_identity")}
        evidence = [_wire_evidence(row, device_ids) for row in _rows(connection, "SELECT * FROM v2_operation")]
        if any(row is None for row in evidence):
            raise RuntimeError("An unresolved command prevents pairing changes")
        wires = {row["operation_id"]: row for row in evidence}
        epochs = {row["controller_epoch"] for row in evidence}
        operations = {row["operation_id"]: row for row in _rows(connection, "SELECT * FROM operation")}
        if any(
            row["status"] not in {"verified", "rejected"}
            and not (row["status"] == "accepted" and _linked_wire(row, wires, epochs))
            for row in operations.values()
        ):
            raise RuntimeError("An unresolved command prevents pairing changes")
        for session in _rows(
            connection,
            "SELECT * FROM test_session WHERE end_time IS NULL OR (safety_completed_at IS NULL AND test_id IN (SELECT test_id FROM v2_run_binding))",
        ):
            if not _unstarted_reservation(connection, session, operations, wires, epochs):
                raise RuntimeError("An experiment run lacks a proved safe closure")

    def _environment(self):
        if self.env_path.is_symlink() or not self.env_path.is_file():
            raise RuntimeError("A regular installed service.env is required")
        values = dotenv_values(self.env_path, interpolate=False, encoding="utf-8")
        database = Path(values.get("SMD_DB_PATH") or "")
        if not database.is_absolute() or not database.is_file() or database.is_symlink():
            raise RuntimeError("The actual configured database must be an existing absolute regular file")
        return {key: value for key, value in values.items() if value is not None}, database.resolve()

    def apply(
        self,
        *,
        device_id,
        reason,
        controller_id=None,
        controller_epoch=None,
        import_psk=None,
        replace=False,
    ):
        if not reason.strip():
            raise RuntimeError("An offline maintenance reason is required")
        self._stopped()
        with single_instance("SmdHmi.Backend", self.data / "backend.lock"):
            self._stopped()
            if (
                self.journal_path.exists()
                and json.loads(self.journal_path.read_text(encoding="utf-8"))["phase"] != "committed"
            ):
                raise RuntimeError("Recover the existing pairing transaction first")
            if self.gate_path.exists():
                raise RuntimeError("Another maintenance ticket is active")
            for name in ("active.json", "uninstall.json"):
                path = self.data / "updates" / name
                if path.exists() and json.loads(path.read_text(encoding="utf-8")).get("phase") not in {
                    "committed",
                    "rolled_back",
                }:
                    raise RuntimeError("Finish the pending installation maintenance first")
            values, database = self._environment()
            with closing(sqlite3.connect(f"{database.as_uri()}?mode=rw", uri=True)) as db:
                self._guard(db)
                existing = db.execute(
                    "SELECT controller_id,controller_epoch,last_seq,created_at FROM v2_controller_identity WHERE device_id=?",
                    (device_id,),
                ).fetchone()
            old_id = values.get("HOSTCOMM_DEVICE_ID")
            paired = bool(values.get("HOSTCOMM_PSK_FILE"))
            if paired and not replace:
                raise RuntimeError("Existing pairing requires explicit --replace-pairing")
            chosen_id = (
                controller_id
                or (existing[0] if existing else None)
                or (values.get("HOSTCOMM_CONTROLLER_ID") if old_id == device_id else None)
                or uuid.uuid4().hex
            )
            chosen_epoch = (
                controller_epoch
                or (existing[1] if existing else None)
                or (values.get("HOSTCOMM_CONTROLLER_EPOCH") if old_id == device_id else None)
                or uuid.uuid4().hex
            )
            identity = psk_identity(device_id, chosen_id, chosen_epoch)
            if existing and (existing[0], existing[1]) != (chosen_id, chosen_epoch) and not replace:
                raise RuntimeError("Persistent controller identity change requires explicit --replace-pairing")
            # Changing controller identity without changing its epoch would reuse another authority's sequence space.
            if existing and chosen_id != existing[0] and chosen_epoch == existing[1]:
                raise RuntimeError("A different controller requires an explicitly new controller epoch")
            key = load_psk(import_psk) if import_psk is not None else secrets.token_bytes(32)
            transaction_id = uuid.uuid4().hex
            folder = self.data / "config/pairings" / transaction_id
            _private_folder(folder)
            old_env = self.env_path.read_bytes()
            _private_bytes(folder / "previous-service.env", old_env)
            backup_sqlite(database, folder / "previous-database.sqlite")
            old_psk = values.get("HOSTCOMM_PSK_FILE")
            if old_psk:
                _private_text(folder / "previous-psk.hex", load_psk(old_psk).hex() + "\n")
            psk_file = folder / "controller-psk.hex"
            _private_text(psk_file, key.hex() + "\n")
            load_psk(psk_file)
            material = {
                "schema_version": 1,
                "protocol_version": "2.0",
                "device_id": device_id,
                "controller_id": chosen_id,
                "controller_epoch": chosen_epoch,
                "tls_identity": identity,
                "role": "control",
                "key_bits": 256,
                "psk_file": psk_file.name,
            }
            atomic_json(folder / "firmware-pairing.json", material)
            values.update(
                PROTOCOL_VERSION="2.0",
                HOSTCOMM_MOCK="false",
                HOSTCOMM_DEVICE_ID=device_id,
                HOSTCOMM_CONTROLLER_ID=chosen_id,
                HOSTCOMM_CONTROLLER_EPOCH=chosen_epoch,
                HOSTCOMM_PSK_FILE=str(psk_file),
            )
            staged_env = folder / "next-service.env"
            _private_text(
                staged_env,
                "\n".join(key + "=" + json.dumps(value, ensure_ascii=False) for key, value in values.items()) + "\n",
            )
            journal = {
                "transaction_id": transaction_id,
                "database": str(database),
                "folder": str(folder),
                "device_id": device_id,
                "controller_id": chosen_id,
                "controller_epoch": chosen_epoch,
                "previous_identity": list(existing) if existing else None,
                "reason": reason,
                "created_at": now_iso(),
                "psk_file": str(psk_file),
                "next_env": str(staged_env),
                "previous_env_digest": hashlib.sha256(old_env).hexdigest(),
                "next_env_digest": hashlib.sha256(staged_env.read_bytes()).hexdigest(),
                "key_digest": hashlib.sha256(key).hexdigest(),
            }
            self._record(journal, "prepared")
            atomic_json(
                self.gate_path,
                {
                    "state": "claimed",
                    "purpose": "hostcomm_pairing",
                    "upgrade_id": transaction_id,
                    "db_path": str(database),
                },
            )
            return self._finish(journal)

    def _finish(self, journal):
        if self.gate_path.exists():
            gate = json.loads(self.gate_path.read_text(encoding="utf-8"))
            if gate.get("purpose") != "hostcomm_pairing" or gate.get("upgrade_id") != journal["transaction_id"]:
                raise RuntimeError("Another maintenance transaction owns the gate")
        elif journal["phase"] != "committed":
            atomic_json(
                self.gate_path,
                {
                    "state": "claimed",
                    "purpose": "hostcomm_pairing",
                    "upgrade_id": journal["transaction_id"],
                    "db_path": journal["database"],
                },
            )
        if journal["phase"] == "committed":
            self._clear_gate(journal)
            return self._result(journal)
        database = Path(journal["database"])
        key = load_psk(journal["psk_file"])
        next_env = Path(journal["next_env"]).read_bytes()
        if (
            hashlib.sha256(key).hexdigest() != journal["key_digest"]
            or hashlib.sha256(next_env).hexdigest() != journal["next_env_digest"]
            or hashlib.sha256(self.env_path.read_bytes()).hexdigest()
            not in {journal["previous_env_digest"], journal["next_env_digest"]}
        ):
            raise RuntimeError("Staged pairing material or current configuration changed; keep maintenance locked")
        configured_database = self._environment()[1]
        if configured_database != database:
            raise RuntimeError("Pairing journal does not refer to the configured database")
        with closing(sqlite3.connect(f"{database.as_uri()}?mode=rw", uri=True)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            self._guard(db)
            previous = journal["previous_identity"]
            current = db.execute(
                "SELECT controller_id,controller_epoch,last_seq,created_at FROM v2_controller_identity WHERE device_id=?",
                (journal["device_id"],),
            ).fetchone()
            same_epoch = previous is not None and previous[1] == journal["controller_epoch"]
            next_row = [
                journal["controller_id"],
                journal["controller_epoch"],
                previous[2] if same_epoch else "0",
                previous[3] if same_epoch else journal["created_at"],
            ]
            if (list(current) if current else None) != next_row:
                if (list(current) if current else None) != previous:
                    raise RuntimeError("Persistent pairing changed outside this maintenance transaction")
                db.execute(
                    "INSERT INTO v2_controller_identity(device_id,controller_id,controller_epoch,last_seq,created_at) "
                    "VALUES(?,?,?,?,?) ON CONFLICT(device_id) DO UPDATE SET controller_id=excluded.controller_id,"
                    "controller_epoch=excluded.controller_epoch,last_seq=excluded.last_seq,created_at=excluded.created_at",
                    [journal["device_id"], *next_row],
                )
        self._record(journal, "identity_committed")
        _private_text(self.env_path, next_env.decode("utf-8"))
        self._record(journal, "committed")
        self._clear_gate(journal)
        return self._result(journal)

    def _clear_gate(self, journal):
        if self.gate_path.exists():
            gate = json.loads(self.gate_path.read_text(encoding="utf-8"))
            if gate.get("purpose") == "hostcomm_pairing" and gate.get("upgrade_id") == journal["transaction_id"]:
                self.gate_path.unlink()

    @staticmethod
    def _result(journal):
        return {
            key: journal[key]
            for key in (
                "device_id",
                "controller_id",
                "controller_epoch",
                "psk_file",
                "folder",
            )
        }

    def recover(self):
        self._stopped()
        with single_instance("SmdHmi.Backend", self.data / "backend.lock"):
            self._stopped()
            if not self.journal_path.exists():
                raise RuntimeError("No staged pairing transaction exists")
            journal = json.loads(self.journal_path.read_text(encoding="utf-8"))
            return self._finish(journal)
