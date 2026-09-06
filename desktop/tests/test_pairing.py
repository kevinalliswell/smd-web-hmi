"""Offline pairing never races the backend or rewrites unresolved experiment history."""

import json
import os
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path

import pytest
from dotenv import dotenv_values
from smd_desktop.pairing import PairingTransaction
from smd_desktop.single_instance import AlreadyRunning, single_instance
from sqlalchemy import create_engine

from app.db.models import Base
from app.db.operation_models import Operation  # noqa: F401 — register the actual HTTP operation table.
from app.hostcomm.v2_contract.codec import canonical_bytes, command_digest, digest

NOW = "2026-09-06T00:00:00.000Z"


class Platform:
    running = False

    def require_stopped(self):
        if self.running:
            raise RuntimeError("service must already be stopped")


@pytest.fixture
def installation(tmp_path):
    data = tmp_path / "data"
    (data / "config").mkdir(parents=True)
    database = tmp_path / "actual.db"
    engine = create_engine(f"sqlite:///{database}")
    with engine.begin() as connection:
        connection.exec_driver_sql("BEGIN")
        Base.metadata.create_all(connection)
    engine.dispose()
    (data / "config/service.env").write_text(
        f'SMD_DB_PATH={json.dumps(str(database))}\nSMD_JWT_SECRET="keep-me"\nHOSTCOMM_DEVICE_ID=""\n',
        encoding="utf-8",
    )
    return data, database, Platform()


def prepare(installation, **kwargs):
    data, _, platform = installation
    return PairingTransaction(data, platform).apply(device_id="a" * 32, reason="offline commissioning", **kwargs)


def insert(db, table, values):
    db.execute(
        f"INSERT INTO {table} ({','.join(values)}) VALUES ({','.join('?' for _ in values)})",
        list(values.values()),
    )


def operation_evidence(installation, *, command="ack_alarm", status="applied", outer_status="accepted"):
    """Seed the exact durable HTTP→UUID5→wire identities written by the runtime."""
    _, database, _ = installation
    epoch, device, run = "c" * 32, "a" * 32, "d" * 32
    with closing(sqlite3.connect(database)) as db, db:
        identity = db.execute(
            "SELECT controller_epoch FROM v2_controller_identity WHERE device_id=?",
            (device,),
        ).fetchone()
        if identity:
            epoch = identity[0]
        else:
            insert(
                db,
                "v2_controller_identity",
                {
                    "device_id": device,
                    "controller_id": "b" * 32,
                    "controller_epoch": epoch,
                    "last_seq": "1",
                    "created_at": NOW,
                },
            )
        outer_id, msg_id = "http-operation", "pc-cmd-pairing-proof"
        wire_id = uuid.uuid5(uuid.UUID(hex=epoch), msg_id).hex
        params = (
            {
                "run_id": run,
                "recipe_digest": "e" * 64,
                "safety_profile_digest": "f" * 64,
            }
            if command == "start_run"
            else {"alarm_id": run, "occurrence_seq": "1"}
        )
        request = {
            "operation_id": wire_id,
            "controller_epoch": epoch,
            "command_seq": "1",
            "lease_id": "1" * 32,
            "expected_boot_id": "2" * 32,
            "expected_state_revision": "1",
            "request_digest": "0" * 64,
            "command": command,
            "params": params,
        }
        request["request_digest"] = command_digest(request)
        result = {
            "operation_id": wire_id,
            "controller_epoch": epoch,
            "command_seq": "1",
            "request_digest": request["request_digest"],
            "result_boot_id": "2" * 32,
            "status": status,
            "reason": "ok" if status == "applied" else "state_conflict",
            "state_revision": "2",
            "run_id": None,
            "lease_id": None,
            "lease_expires_uptime_ms": None,
        }
        outer_command = "start_test" if command == "start_run" else command
        outer_params = {"test_id": "RESERVED"} if command == "start_run" else {"alarm_id": 1}
        insert(
            db,
            "operation",
            {
                "operation_id": outer_id,
                "msg_id": msg_id,
                "command": outer_command,
                "operator_id": "admin",
                "operator_role": "admin",
                "request_hash": digest({"command": outer_command, "params": outer_params}),
                "params_json": json.dumps(outer_params),
                "status": outer_status,
                "created_at": NOW,
                "updated_at": NOW,
            },
        )
        insert(
            db,
            "v2_operation",
            {
                "operation_id": wire_id,
                "device_id": device,
                "controller_epoch": epoch,
                "command_seq": "1",
                "msg_id": "3" * 32,
                "command": command,
                "business_digest": digest(
                    {
                        "command": command,
                        "params": params,
                        "actor": "admin",
                        "role": "admin",
                    }
                ),
                "request_digest": request["request_digest"],
                "actor": "admin",
                "role": "admin",
                "status": status,
                "reason": result["reason"],
                "request_json": canonical_bytes(request).decode(),
                "result_json": (
                    canonical_bytes(result).decode() if status in {"applied", "rejected", "interrupted"} else None
                ),
                "reconciled": 0,
                "created_at": NOW,
                "updated_at": NOW,
            },
        )
    return {
        "operation_id": outer_id,
        "wire_id": wire_id,
        "request": request,
        "result": result,
        "run_id": run,
    }


def rejected_reservation(installation):
    evidence = operation_evidence(installation, command="start_run", status="rejected", outer_status="rejected")
    with closing(sqlite3.connect(installation[1])) as db, db:
        insert(
            db,
            "test_session",
            {
                "test_id": "RESERVED",
                "operator_id": "admin",
                "start_time": NOW,
                "end_time": NOW,
                "end_reason": "start_rejected:state_conflict",
                "phase": "start_rejected",
                "recipe_snapshot_json": json.dumps(
                    {
                        "operation_id": evidence["operation_id"],
                        "protocol_version": "2.0",
                    }
                ),
                "measurement_basis_json": json.dumps(
                    {
                        "v2": {
                            "profile_digest": "f" * 64,
                            "profile_snapshot": {"profile_digest": "f" * 64},
                        }
                    }
                ),
            },
        )
        insert(
            db,
            "v2_run_binding",
            {
                "device_id": "a" * 32,
                "run_id": evidence["run_id"],
                "test_id": "RESERVED",
                "recipe_digest": "e" * 64,
                "profile_digest": "f" * 64,
                "created_at": NOW,
            },
        )
    return evidence


@pytest.mark.parametrize("status", ["applied", "rejected", "interrupted"])
def test_applied_wire_receipt_allows_repairing_an_accepted_http_operation(installation, status):
    first = prepare(installation)
    evidence = operation_evidence(installation, status=status)
    second = prepare(installation, replace=True)
    assert second["controller_epoch"] == first["controller_epoch"]
    with closing(sqlite3.connect(installation[1])) as db:
        assert db.execute(
            "SELECT status FROM operation WHERE operation_id=?",
            (evidence["operation_id"],),
        ).fetchone() == ("accepted",)


def test_rejected_unstarted_reservation_allows_repairing_without_fake_safe_completion(
    installation,
):
    prepare(installation)
    rejected_reservation(installation)
    prepare(installation, replace=True)
    with closing(sqlite3.connect(installation[1])) as db:
        assert db.execute("SELECT phase,safety_completed_at FROM test_session").fetchone() == ("start_rejected", None)


def test_new_pairing_keeps_key_private_and_returns_only_material_paths(installation, capsys):
    result = prepare(installation)
    data, database, _ = installation
    settings = dotenv_values(data / "config/service.env", interpolate=False)
    secret = Path(settings["HOSTCOMM_PSK_FILE"]).read_text(encoding="ascii").strip()
    assert len(secret) == 64
    assert secret not in json.dumps(result) and secret not in capsys.readouterr().out
    assert settings["SMD_JWT_SECRET"] == "keep-me" and settings["PROTOCOL_VERSION"] == "2.0"
    assert not (data / "maintenance.json").exists()
    with closing(sqlite3.connect(database)) as db:
        row = db.execute("SELECT controller_id,controller_epoch,last_seq FROM v2_controller_identity").fetchone()
        assert row == (
            settings["HOSTCOMM_CONTROLLER_ID"],
            settings["HOSTCOMM_CONTROLLER_EPOCH"],
            "0",
        )
    if os.name != "nt":
        assert os.stat(settings["HOSTCOMM_PSK_FILE"]).st_mode & 0o077 == 0


def test_private_write_refuses_before_any_secret_bytes_if_file_security_cannot_be_established(tmp_path, monkeypatch):
    from smd_desktop import pairing

    target = tmp_path / "existing.hex"
    target.write_bytes(b"preserve previous bytes")

    def refuse(path):
        raise RuntimeError("cannot establish private file owner")

    monkeypatch.setattr(pairing, "_private_fd", refuse, raising=False)
    with pytest.raises(RuntimeError, match="private file owner"):
        pairing._private_bytes(target, b"synthetic replacement secret")
    assert target.read_bytes() == b"preserve previous bytes"
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.skipif(os.name != "nt", reason="Requires actual Windows ownership and DACL enforcement")
def test_pairing_files_have_administrators_owner_before_service_use(installation):
    from smd_desktop import windows_powershell

    from app.hostcomm.v2_security import load_psk

    result = prepare(installation)
    key = Path(result["psk_file"])
    check = windows_powershell.run(
        [
            "-Command",
            "(Get-Acl -LiteralPath $env:SMD_TEST_PAIRING_KEY).GetOwner("
            "[System.Security.Principal.SecurityIdentifier]).Value",
        ],
        env={**os.environ, "SMD_TEST_PAIRING_KEY": str(key)},
        check=True,
        capture_output=True,
        text=True,
    )
    assert check.stdout.strip() == "S-1-5-32-544"
    assert len(load_psk(key)) == 32


def test_replacement_requires_explicit_flag_and_preserves_epoch_watermark(installation):
    first = prepare(installation)
    data, database, _ = installation
    previous = (data / "config/service.env").read_text(encoding="utf-8")
    with closing(sqlite3.connect(database)) as db, db:
        db.execute("UPDATE v2_controller_identity SET last_seq='9223372036854776000'")
    with pytest.raises(RuntimeError, match="replace"):
        prepare(installation)
    assert (data / "config/service.env").read_text(encoding="utf-8") == previous
    second = prepare(installation, replace=True)
    with closing(sqlite3.connect(database)) as db:
        assert db.execute("SELECT last_seq FROM v2_controller_identity").fetchone() == ("9223372036854776000",)
    assert first["controller_epoch"] == second["controller_epoch"]
    assert first["psk_file"] != second["psk_file"]
    assert os.path.isfile(first["psk_file"])


@pytest.mark.parametrize("row", ["unknown", "sent", "accepted"])
def test_unresolved_command_refuses_even_explicit_replacement(installation, row):
    operation_evidence(installation, status=row, outer_status="rejected")
    with pytest.raises(RuntimeError, match="unresolved"):
        prepare(installation, replace=True)


def test_open_run_and_running_service_are_not_overridden(installation):
    _, database, platform = installation
    platform.running = True
    with pytest.raises(RuntimeError, match="stopped"):
        prepare(installation)
    platform.running = False
    with closing(sqlite3.connect(database)) as db, db:
        db.execute(
            "INSERT INTO test_session(test_id,operator_id,start_time) VALUES ('open','admin',?)",
            (NOW,),
        )
    with pytest.raises(RuntimeError, match="run"):
        prepare(installation)


def test_backend_mutex_prevents_offline_pairing(installation):
    data, _, _ = installation
    with single_instance("SmdHmi.Backend", data / "backend.lock"):
        with pytest.raises(AlreadyRunning):
            prepare(installation)


def test_interrupted_pairing_recovers_fixed_identity_and_secret(installation, monkeypatch):
    data, _, platform = installation
    transaction = PairingTransaction(data, platform)
    record = transaction._record

    def interrupted(journal, phase):
        record(journal, phase)
        if phase == "identity_committed":
            raise SystemExit("power loss")

    monkeypatch.setattr(transaction, "_record", interrupted)
    with pytest.raises(SystemExit):
        transaction.apply(device_id="a" * 32, reason="offline commissioning")
    journal = json.loads(transaction.journal_path.read_text(encoding="utf-8"))
    assert (data / "maintenance.json").exists()
    result = PairingTransaction(data, platform).recover()
    assert result["controller_epoch"] == journal["controller_epoch"]
    assert not (data / "maintenance.json").exists()


def test_explicit_new_epoch_retains_old_database_and_key(installation):
    first = prepare(installation)
    _, database, _ = installation
    with closing(sqlite3.connect(database)) as db, db:
        db.execute("UPDATE v2_controller_identity SET last_seq='42'")
    replacement = prepare(installation, replace=True, controller_epoch="e" * 32)
    with closing(sqlite3.connect(database)) as db:
        assert db.execute("SELECT controller_epoch,last_seq FROM v2_controller_identity").fetchone() == ("e" * 32, "0")
    with closing(sqlite3.connect(Path(replacement["folder"]) / "previous-database.sqlite")) as db:
        assert db.execute("SELECT controller_epoch,last_seq FROM v2_controller_identity").fetchone() == (
            first["controller_epoch"],
            "42",
        )
    assert Path(first["psk_file"]).exists()


def test_import_uses_private_file_without_exposing_key(installation, tmp_path):
    imported = tmp_path / "import.hex"
    imported.write_text("12" * 32 + "\n", encoding="ascii")
    imported.chmod(0o600)
    result = prepare(installation, import_psk=imported)
    assert Path(result["psk_file"]).read_text(encoding="ascii").strip() == "12" * 32
    assert "12" * 32 not in json.dumps(result)


def test_tampered_staged_key_keeps_recovery_locked(installation, monkeypatch):
    data, _, platform = installation
    transaction = PairingTransaction(data, platform)
    record = transaction._record

    def interrupted(journal, phase):
        record(journal, phase)
        if phase == "identity_committed":
            raise SystemExit("power loss")

    monkeypatch.setattr(transaction, "_record", interrupted)
    with pytest.raises(SystemExit):
        transaction.apply(device_id="a" * 32, reason="offline commissioning")
    journal = json.loads(transaction.journal_path.read_text(encoding="utf-8"))
    Path(journal["psk_file"]).write_text("ff" * 32 + "\n", encoding="ascii")
    with pytest.raises(RuntimeError, match="changed"):
        PairingTransaction(data, platform).recover()
    assert (data / "maintenance.json").exists()


def test_windows_crlf_configuration_is_preserved_byte_for_byte_in_backup(installation):
    data, _, _ = installation
    config = data / "config/service.env"
    original = config.read_bytes().replace(b"\n", b"\r\n")
    config.write_bytes(original)
    result = prepare(installation)
    assert (Path(result["folder"]) / "previous-service.env").read_bytes() == original
    assert dotenv_values(config, interpolate=False)["SMD_JWT_SECRET"] == "keep-me"


def test_pairing_and_recovery_read_unicode_journals_under_legacy_windows_locale(installation, monkeypatch):
    data, database, platform = installation
    renamed = database.with_name("配对数据库.sqlite")
    database.rename(renamed)
    config = data / "config/service.env"
    config.write_text(
        f"SMD_DB_PATH={json.dumps(str(renamed), ensure_ascii=False)}\n",
        encoding="utf-8",
    )
    original_read = Path.read_text

    def windows_read(path, encoding=None, errors=None, **kwargs):
        return original_read(path, encoding=encoding or "cp1252", errors=errors, **kwargs)

    monkeypatch.setattr(Path, "read_text", windows_read)
    first = PairingTransaction(data, platform).apply(device_id="a" * 32, reason="配对维护")
    recovered = PairingTransaction(data, platform).recover()
    assert recovered["controller_epoch"] == first["controller_epoch"]
    second = PairingTransaction(data, platform).apply(device_id="a" * 32, reason="配对替换", replace=True)
    assert second["controller_epoch"] == first["controller_epoch"]
    journal = json.loads((data / "updates/pairing.json").read_text(encoding="utf-8"))
    assert journal["reason"] == "配对替换"
    assert not (data / "maintenance.json").exists()


def test_terminal_history_remains_verifiable_after_explicit_epoch_rotation(
    installation,
):
    first = prepare(installation)
    evidence = operation_evidence(installation)
    second = prepare(installation, replace=True, controller_epoch="e" * 32)
    third = prepare(installation, replace=True)
    assert second["controller_epoch"] == third["controller_epoch"] == "e" * 32
    with closing(sqlite3.connect(installation[1])) as db:
        assert db.execute(
            "SELECT controller_epoch FROM v2_operation WHERE operation_id=?",
            (evidence["wire_id"],),
        ).fetchone() == (first["controller_epoch"],)


@pytest.mark.parametrize(
    "conflict",
    [
        "missing_wire",
        "http_msg",
        "wire_epoch",
        "wire_device",
        "wire_command",
        "wire_actor",
        "wire_role",
        "wire_accepted",
        "result_digest",
        "result_id",
        "request_bytes",
        "outer_actor",
        "outer_role",
        "outer_hash",
        "outer_pointer",
    ],
)
def test_accepted_operation_requires_matching_terminal_wire_evidence(installation, conflict):
    proof = operation_evidence(installation)
    with closing(sqlite3.connect(installation[1])) as db, db:
        changes = {
            "missing_wire": ("DELETE FROM v2_operation", ()),
            "http_msg": ("UPDATE operation SET msg_id=?", ("pc-cmd-other",)),
            "wire_epoch": ("UPDATE v2_operation SET controller_epoch=?", ("4" * 32,)),
            "wire_device": ("UPDATE v2_operation SET device_id=?", ("4" * 32,)),
            "wire_command": ("UPDATE v2_operation SET command='ack_run'", ()),
            "wire_actor": ("UPDATE v2_operation SET actor='other'", ()),
            "wire_role": ("UPDATE v2_operation SET role='operator'", ()),
            "wire_accepted": ("UPDATE v2_operation SET status='accepted'", ()),
            "result_digest": (
                "UPDATE v2_operation SET result_json=?",
                (json.dumps({**proof["result"], "request_digest": "0" * 64}),),
            ),
            "result_id": (
                "UPDATE v2_operation SET result_json=?",
                (json.dumps({**proof["result"], "operation_id": "0" * 32}),),
            ),
            "request_bytes": (
                "UPDATE v2_operation SET request_json=?",
                (
                    json.dumps(
                        {
                            **proof["request"],
                            "params": {"alarm_id": "4" * 32, "occurrence_seq": "1"},
                        }
                    ),
                ),
            ),
            "outer_actor": ("UPDATE operation SET operator_id='other'", ()),
            "outer_role": ("UPDATE operation SET operator_role='operator'", ()),
            "outer_hash": ("UPDATE operation SET request_hash=?", ("0" * 64,)),
            "outer_pointer": (
                "UPDATE operation SET result_json=?",
                (
                    json.dumps(
                        {
                            "result": "accepted",
                            "wire_status": "applied",
                            "wire_operation_id": "4" * 32,
                        }
                    ),
                ),
            ),
        }
        db.execute(*changes[conflict])
        with pytest.raises(RuntimeError, match="unresolved"):
            PairingTransaction._guard(db)


@pytest.mark.parametrize(
    "conflict",
    [
        "snapshot_identity",
        "snapshot_json",
        "missing_wire",
        "wrong_run",
        "wrong_device",
        "result_run",
        "phase_only",
        "end_open",
        "measurement",
        "stop_requested",
        "basis_state",
        "basis_boundary",
        "basis_json",
        "sample",
        "raw_source",
        "interrupted",
    ],
)
def test_rejected_start_exemption_refuses_unknown_or_observed_run_evidence(installation, conflict):
    proof = rejected_reservation(installation)
    with closing(sqlite3.connect(installation[1])) as db, db:
        if conflict == "sample":
            insert(
                db,
                "sample_point",
                {
                    "test_id": "RESERVED",
                    "ts": NOW,
                    "source": "hostcomm_v2_live",
                    "burden_temp_v": 0,
                    "delta_p_v": 0,
                    "displacement_v": 0,
                },
            )
        elif conflict == "raw_source":
            insert(
                db,
                "v2_source_record",
                {
                    "device_id": "a" * 32,
                    "record_type": "event",
                    "boot_id": "2" * 32,
                    "source_seq": "1",
                    "run_id": proof["run_id"],
                    "payload_bytes": b"{}",
                    "archived": 0,
                    "received_at": NOW,
                },
            )
        else:
            changes = {
                "snapshot_identity": (
                    "UPDATE test_session SET recipe_snapshot_json=?",
                    (json.dumps({"operation_id": "other", "protocol_version": "2.0"}),),
                ),
                "snapshot_json": (
                    "UPDATE test_session SET recipe_snapshot_json='broken'",
                    (),
                ),
                "missing_wire": ("DELETE FROM v2_operation", ()),
                "wrong_run": ("UPDATE v2_run_binding SET run_id=?", ("4" * 32,)),
                "wrong_device": ("UPDATE v2_run_binding SET device_id=?", ("4" * 32,)),
                "result_run": (
                    "UPDATE v2_operation SET result_json=?",
                    (json.dumps({**proof["result"], "run_id": "4" * 32}),),
                ),
                "phase_only": (
                    "UPDATE test_session SET end_reason='start_not_sent'",
                    (),
                ),
                "end_open": ("UPDATE test_session SET end_time=NULL", ()),
                "measurement": (
                    "UPDATE test_session SET measurement_completed_at=?",
                    (NOW,),
                ),
                "stop_requested": (
                    "UPDATE test_session SET stop_requested_at=?",
                    (NOW,),
                ),
                "basis_state": (
                    "UPDATE test_session SET measurement_basis_json=?",
                    (json.dumps({"v2": {"state": "preparing"}}),),
                ),
                "basis_boundary": (
                    "UPDATE test_session SET measurement_basis_json=?",
                    (
                        json.dumps(
                            {
                                "v2": {
                                    "measurement_start": {
                                        "boot_id": "2" * 32,
                                        "sample_seq": "1",
                                    }
                                }
                            }
                        ),
                    ),
                ),
                "basis_json": (
                    "UPDATE test_session SET measurement_basis_json='broken'",
                    (),
                ),
                "interrupted": (
                    "UPDATE v2_operation SET status='interrupted', result_json=?",
                    (json.dumps({**proof["result"], "status": "interrupted"}),),
                ),
            }
            db.execute(*changes[conflict])
        with pytest.raises(RuntimeError, match="run"):
            PairingTransaction._guard(db)
