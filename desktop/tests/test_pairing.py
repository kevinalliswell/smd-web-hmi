"""Offline pairing never races the backend or rewrites unresolved experiment history."""

import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from dotenv import dotenv_values
from smd_desktop.pairing import PairingTransaction
from smd_desktop.single_instance import AlreadyRunning, single_instance


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
    with closing(sqlite3.connect(database)) as db, db:
        db.executescript(
            """
            CREATE TABLE test_session (test_id TEXT, end_time TEXT, safety_completed_at TEXT);
            CREATE TABLE operation (status TEXT);
            CREATE TABLE v2_operation (status TEXT);
            CREATE TABLE v2_controller_identity (device_id TEXT PRIMARY KEY, controller_id TEXT,
                controller_epoch TEXT, last_seq TEXT, created_at TEXT);
            CREATE TABLE v2_run_binding (device_id TEXT, run_id TEXT, test_id TEXT);
        """
        )
    (data / "config/service.env").write_text(
        f'SMD_DB_PATH={json.dumps(str(database))}\nSMD_JWT_SECRET="keep-me"\nHOSTCOMM_DEVICE_ID=""\n', encoding="utf-8"
    )
    return data, database, Platform()


def prepare(installation, **kwargs):
    data, _, platform = installation
    return PairingTransaction(data, platform).apply(device_id="a" * 32, reason="offline commissioning", **kwargs)


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
        assert row == (settings["HOSTCOMM_CONTROLLER_ID"], settings["HOSTCOMM_CONTROLLER_EPOCH"], "0")
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
    _, database, _ = installation
    with closing(sqlite3.connect(database)) as db, db:
        db.execute("INSERT INTO v2_operation(status) VALUES (?)", (row,))
    with pytest.raises(RuntimeError, match="unresolved"):
        prepare(installation, replace=True)


def test_open_run_and_running_service_are_not_overridden(installation):
    _, database, platform = installation
    platform.running = True
    with pytest.raises(RuntimeError, match="stopped"):
        prepare(installation)
    platform.running = False
    with closing(sqlite3.connect(database)) as db, db:
        db.execute("INSERT INTO test_session(test_id) VALUES ('open')")
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
    config.write_text(f"SMD_DB_PATH={json.dumps(str(renamed), ensure_ascii=False)}\n", encoding="utf-8")
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
