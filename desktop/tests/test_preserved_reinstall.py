"""Reinstall after an owned uninstall preserves real DB/config and survives interruption."""

import json
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from smd_desktop.reinstall import PreservedReinstall, ReinstallError, finish_committed_reinstall
from smd_desktop.storage import atomic_json


class Platform:
    def __init__(self):
        self.running = True

    def stop(self):
        self.running = False


def value(database):
    with closing(sqlite3.connect(database)) as connection:
        return connection.execute("SELECT value FROM sample").fetchone()[0]


def change(database, result):
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("UPDATE sample SET value=?", (result,))


@pytest.fixture
def existing(tmp_path, monkeypatch):
    monkeypatch.setattr("smd_desktop.reinstall.protected_json", atomic_json)
    install, data, database = tmp_path / "program", tmp_path / "data", tmp_path / "custom database.sqlite"
    install.mkdir()
    (data / "config").mkdir(parents=True)
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("CREATE TABLE sample(value TEXT)")
        connection.execute("INSERT INTO sample VALUES ('original')")
    (data / "config/service.env").write_text(
        f'SMD_DB_PATH={json.dumps(str(database), ensure_ascii=False)}\nSMD_JWT_SECRET="unchanged-secret"\n',
        encoding="utf-8",
    )
    (data / "config/device-psk.key").write_bytes(b"private-pairing-key")
    atomic_json(data / "client.json", {"url": "https://custom-host:9443"})
    previous = {"version": "0.3.0-rc.5", "manifest_sha256": "a" * 64}
    archive = {**previous, "uninstall_id": "b" * 32, "install_dir": str(install)}
    uninstall = {
        "schema_version": 1,
        "install_dir": str(install),
        "previous": previous,
        "upgrade_id": "b" * 32,
        "authorized": True,
        "phase": "committed",
    }
    atomic_json(data / "uninstalled.json", archive)
    atomic_json(data / "updates/uninstall.json", uninstall)
    platform = Platform()
    return install, data, database, platform


def helper(existing, **kwargs):
    install, data, _, platform = existing
    return PreservedReinstall(install, data, platform, target_version="0.3.0", package_sha256="c" * 64, **kwargs)


def test_owned_reinstall_backs_up_custom_database_without_replacing_any_configuration(existing):
    install, data, database, platform = existing
    transaction = helper(existing)
    assert transaction.before_migrate() is True
    assert not platform.running
    journal = json.loads((data / "updates/reinstall.json").read_text(encoding="utf-8"))
    backup = Path(journal["backup_dir"])
    assert value(backup / "database.sqlite") == "original"
    assert (backup / "config/device-psk.key").read_bytes() == b"private-pairing-key"
    assert (data / "config/device-psk.key").read_bytes() == b"private-pairing-key"
    assert json.loads((data / "client.json").read_text(encoding="utf-8"))["url"] == "https://custom-host:9443"
    assert (data / "uninstalled.json").exists()


@pytest.mark.parametrize("proof", ["uninstalled.json", "updates/uninstall.json"])
def test_orphan_configuration_without_both_owned_uninstall_proofs_is_rejected(existing, proof):
    _, data, database, platform = existing
    (data / proof).unlink()
    with pytest.raises(ReinstallError, match="卸载"):
        helper(existing)
    assert platform.running and value(database) == "original"


def test_mismatched_uninstall_identity_does_not_adopt_data(existing):
    _, data, _, platform = existing
    path = data / "uninstalled.json"
    archived = json.loads(path.read_text(encoding="utf-8"))
    atomic_json(path, {**archived, "uninstall_id": "d" * 32})
    with pytest.raises(ReinstallError, match="卸载"):
        helper(existing)
    assert platform.running


def test_reinstall_failure_restores_data_and_config_but_never_claims_old_program_is_running(existing):
    _, data, database, platform = existing
    transaction = helper(existing)
    transaction.before_migrate()
    change(database, "migration-modified")
    (data / "config/device-psk.key").write_bytes(b"modified")
    platform.running = True
    transaction.failed(RuntimeError("new health failed"))
    assert value(database) == "original"
    assert (data / "config/device-psk.key").read_bytes() == b"private-pairing-key"
    assert not platform.running
    assert (data / "uninstalled.json").exists()
    assert not (data / "installation.json").exists()
    assert json.loads((data / "updates/reinstall.json").read_text(encoding="utf-8"))["phase"] == "rolled_back"


def test_interruption_after_migration_retries_health_without_restoring_new_records(existing):
    _, _, database, _ = existing
    transaction = helper(existing)
    transaction.before_migrate()
    change(database, "migrated")
    transaction.mark_migrated()
    change(database, "new-data-after-service-restart")
    resumed = helper(existing)
    assert resumed.before_migrate() is False
    assert value(database) == "new-data-after-service-restart"


def test_interruption_during_migration_restores_before_retrying_same_package(existing):
    _, _, database, _ = existing
    transaction = helper(existing)
    transaction.before_migrate()
    change(database, "partial-migration")
    resumed = helper(existing)
    assert resumed.before_migrate() is True
    assert value(database) == "original"


def test_pending_reinstall_rejects_different_target_manifest(existing):
    install, data, _, platform = existing
    helper(existing).before_migrate()
    with pytest.raises(ReinstallError, match="同一"):
        PreservedReinstall(install, data, platform, target_version="0.3.0", package_sha256="d" * 64)


def test_commit_archives_uninstall_evidence_only_after_target_pointer_is_published(existing):
    install, data, database, _ = existing
    transaction = helper(existing)
    transaction.before_migrate()
    change(database, "migrated")
    transaction.mark_migrated()
    with pytest.raises(ReinstallError, match="安装状态"):
        transaction.commit()
    assert (data / "uninstalled.json").exists()
    atomic_json(data / "installation.json", {"version": "0.3.0", "manifest_sha256": "c" * 64})
    transaction.commit()
    assert not (data / "uninstalled.json").exists()
    assert not (data / "updates/uninstall.json").exists()
    assert value(database) == "migrated"
    journal = json.loads((data / "updates/reinstall.json").read_text(encoding="utf-8"))
    assert journal["phase"] == "committed"
    assert (Path(journal["backup_dir"]) / "uninstalled.json").exists()
    assert finish_committed_reinstall(install, data) is True


def test_interruption_after_publishing_healthy_pointer_finishes_metadata_without_touching_database(existing):
    install, data, database, _ = existing
    transaction = helper(existing)
    transaction.before_migrate()
    transaction.mark_migrated()
    atomic_json(data / "installation.json", {"version": "0.3.0", "manifest_sha256": "c" * 64})
    change(database, "record-after-installation")
    assert finish_committed_reinstall(install, data) is True
    assert value(database) == "record-after-installation"
    assert not (data / "updates/uninstall.json").exists()


def test_cleanup_for_an_old_reinstall_never_consumes_a_new_uninstall(existing):
    install, data, _, _ = existing
    transaction = helper(existing)
    transaction.before_migrate()
    transaction.mark_migrated()
    atomic_json(data / "installation.json", {"version": "0.3.0", "manifest_sha256": "c" * 64})
    transaction.commit()
    atomic_json(data / "uninstalled.json", {"uninstall_id": "e" * 32})
    assert finish_committed_reinstall(install, data) is False
    assert json.loads((data / "uninstalled.json").read_text(encoding="utf-8"))["uninstall_id"] == "e" * 32


def test_interrupted_config_restore_can_resume_when_live_config_is_missing(existing, monkeypatch):
    from smd_desktop import snapshot

    _, data, database, _ = existing
    transaction = helper(existing)
    transaction.before_migrate()
    change(database, "changed")
    copytree = snapshot.shutil.copytree

    def lose_power_after_copy(source, target, *args, **kwargs):
        result = copytree(source, target, *args, **kwargs)
        if str(target).endswith(transaction.journal["upgrade_id"]):
            shutil.rmtree(data / "config")
            raise SystemExit("power lost during config replacement")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(snapshot.shutil, "copytree", lose_power_after_copy)
        with pytest.raises(SystemExit):
            transaction.failed(RuntimeError("health failed"))
    assert not (data / "config").exists()
    resumed = helper(existing)
    assert resumed.before_migrate() is True
    assert value(database) == "original"
    assert (data / "config/device-psk.key").read_bytes() == b"private-pairing-key"


def test_health_only_retry_does_not_require_room_for_another_database_copy(existing, monkeypatch):
    transaction = helper(existing)
    transaction.before_migrate()
    transaction.mark_migrated()
    resumed = helper(existing)
    monkeypatch.setattr(resumed, "_space", lambda: (_ for _ in ()).throw(AssertionError("no extra backup needed")))
    assert resumed.before_migrate() is False


def test_metadata_failure_after_healthy_pointer_cannot_restore_old_database(existing):
    _, data, database, _ = existing
    transaction = helper(existing)
    transaction.before_migrate()
    transaction.mark_migrated()
    change(database, "healthy-new-record")
    atomic_json(data / "installation.json", {"version": "0.3.0", "manifest_sha256": "c" * 64})
    with pytest.raises(ReinstallError, match="禁止恢复"):
        transaction.failed(RuntimeError("metadata cleanup interrupted"))
    assert value(database) == "healthy-new-record"


def test_invalid_backup_keeps_failed_reinstall_stopped_and_preserves_uninstall_records(existing):
    _, data, database, platform = existing
    transaction = helper(existing)
    transaction.before_migrate()
    change(database, "migration-state")
    (Path(transaction.journal["backup_dir"]) / "database.sqlite").write_bytes(b"broken-backup")
    with pytest.raises(ReinstallError, match="恢复未完成"):
        transaction.failed(RuntimeError("migration failed"))
    assert value(database) == "migration-state"
    assert not platform.running
    assert (data / "uninstalled.json").exists()
    assert json.loads((data / "updates/reinstall.json").read_text(encoding="utf-8"))["phase"] == "rollback_failed"


def test_custom_database_symlink_does_not_satisfy_preserved_reinstall_proof(existing, tmp_path):
    _, data, database, platform = existing
    link = tmp_path / "db-link.sqlite"
    try:
        link.symlink_to(database)
    except OSError:
        pytest.skip("Windows runner has no symlink privilege")
    (data / "config/service.env").write_text(
        f"SMD_DB_PATH={json.dumps(str(link), ensure_ascii=False)}\n", encoding="utf-8"
    )
    with pytest.raises(RuntimeError, match="重解析"):
        helper(existing)
    assert platform.running


def test_same_version_reinstall_uses_saved_manifest_identity(existing):
    install, data, _, platform = existing
    with pytest.raises(ReinstallError, match="同版"):
        PreservedReinstall(install, data, platform, target_version="0.3.0-rc.5", package_sha256="c" * 64)
    transaction = PreservedReinstall(install, data, platform, target_version="0.3.0-rc.5", package_sha256="a" * 64)
    assert transaction.before_migrate() is True


def test_reinstall_preserves_actual_storage_minimum_even_if_caller_uses_a_lower_default(existing):
    _, data, _, _ = existing
    env = data / "config/service.env"
    env.write_text(env.read_text(encoding="utf-8") + "SMD_STORAGE_MIN_FREE_BYTES=2147483648\n", encoding="utf-8")
    assert helper(existing, min_db_free_bytes=1024).minimum == 2147483648


def test_recovery_reads_storage_minimum_from_verified_backup_when_config_is_missing(existing, monkeypatch):
    _, data, _, _ = existing
    env = data / "config/service.env"
    env.write_text(env.read_text(encoding="utf-8") + "SMD_STORAGE_MIN_FREE_BYTES=2147483648\n", encoding="utf-8")
    transaction = helper(existing)
    transaction.before_migrate()
    original_replace = Path.replace

    def power_during_config_replace(source, target):
        if Path(target) == data / "config":
            raise SystemExit("power lost")
        return original_replace(source, target)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "replace", power_during_config_replace)
        with pytest.raises(SystemExit):
            transaction.failed(RuntimeError("recover"))
    assert not (data / "config").exists()
    assert helper(existing, min_db_free_bytes=1024).minimum == 2147483648


def test_reinstall_gate_is_blocking_only_and_survives_failed_health(existing):
    _, data, _, _ = existing
    transaction = helper(existing)
    transaction.before_migrate()
    gate = json.loads((data / "maintenance.json").read_text(encoding="utf-8"))
    assert gate["state"] == "prepared"
    assert gate["issuer"] == "windows_installer"
    assert gate["operation"] == "install"
    assert gate["authorization_source"] == "preserved_reinstall"
    assert gate["upgrade_id"] == transaction.journal["upgrade_id"]
    assert gate["package_sha256"] == transaction.package_sha256
    assert "token" not in gate
    transaction.failed(RuntimeError("health failed"))
    assert json.loads((data / "maintenance.json").read_text(encoding="utf-8")) == gate


def test_reinstall_cannot_take_over_a_different_existing_gate(existing):
    _, data, database, platform = existing
    gate = {"state": "prepared", "upgrade_id": "e" * 32, "operation": "upgrade"}
    atomic_json(data / "maintenance.json", gate)
    with pytest.raises(ReinstallError, match="维护"):
        helper(existing)
    assert json.loads((data / "maintenance.json").read_text(encoding="utf-8")) == gate
    assert platform.running and value(database) == "original"


def test_committed_reinstall_never_clears_a_subsequent_different_maintenance_gate(existing):
    install, data, _, _ = existing
    transaction = helper(existing)
    transaction.before_migrate()
    transaction.mark_migrated()
    atomic_json(data / "installation.json", {"version": "0.3.0", "manifest_sha256": "c" * 64})
    transaction.commit()
    assert not (data / "maintenance.json").exists()
    later = {"state": "prepared", "upgrade_id": "e" * 32, "operation": "upgrade"}
    atomic_json(data / "maintenance.json", later)
    finish_committed_reinstall(install, data)
    assert json.loads((data / "maintenance.json").read_text(encoding="utf-8")) == later
