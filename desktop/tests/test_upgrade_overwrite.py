"""Overwrite-install behavior with real DB/config and durable interruption boundaries."""

import json
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from smd_desktop.bundle import build_manifest, sha256
from smd_desktop.upgrade import UpgradeError, UpgradeTransaction
from test_upgrade import Platform, installation, read_value, sqlite_handles_closed


def authorize(data, package, permit, **changes):
    permit.update(changes)
    permit["package_sha256"] = sha256(package / "manifest.json")
    (data / "maintenance.json").write_text(json.dumps(permit), encoding="utf-8")


def set_value(source, value):
    with closing(sqlite3.connect(source)) as db, db:
        db.execute("UPDATE sample SET value=?", (value,))


def test_payload_is_promoted_without_copying_runtime(installation, monkeypatch):
    root, data, package, source, permit = installation
    copytree = shutil.copytree

    def only_config_copy(source_dir, destination, *args, **kwargs):
        assert "SmdService" not in {p.name for p in Path(source_dir).iterdir()}
        return copytree(source_dir, destination, *args, **kwargs)

    monkeypatch.setattr(shutil, "copytree", only_config_copy)
    UpgradeTransaction(root, data, Platform(source)).apply(package, permit)
    assert not package.exists()
    pointer = json.loads((data / "installation.json").read_text(encoding="utf-8"))
    assert pointer["manifest_sha256"] == permit["package_sha256"]


@pytest.mark.parametrize("target", ["0.2.99", "0.3.0-rc.99"])
def test_ordinary_downgrade_is_rejected_before_stopping(installation, target):
    root, data, package, source, permit = installation
    build_manifest(package, version=target, commit="a" * 40, webview2_version="135.0.1.2")
    authorize(data, package, permit, target_version=target)
    platform = Platform(source)
    with pytest.raises(UpgradeError, match="降级"):
        UpgradeTransaction(root, data, platform).apply(package, permit)
    assert platform.running
    assert read_value(source) == "original"
    assert not (data / "updates/active.json").exists()


@pytest.mark.parametrize(
    "mutation",
    [
        {"issuer": "legacy_api"},
        {"request_sha256": "0" * 64},
        {"admin_sid": "S-1-5-21-9"},
        {"operation": "uninstall"},
        {"physical_shutdown_confirmed": True},
        {"package_sha256": "0" * 64},
    ],
)
def test_authorization_cannot_change_bound_intent(installation, mutation):
    root, data, package, source, permit = installation
    platform = Platform(source)
    with pytest.raises(UpgradeError):
        UpgradeTransaction(root, data, platform).apply(package, {**permit, **mutation})
    assert platform.running
    assert not (data / "updates/active.json").exists()


def make_repair(root, data, package, permit):
    old = root / "versions/0.3.0"
    shutil.rmtree(old)
    build_manifest(package, version="0.3.0", commit="a" * 40, webview2_version="135.0.1.2")
    shutil.copytree(package, old)
    (data / "installation.json").write_text(
        json.dumps({"version": "0.3.0", "manifest_sha256": sha256(package / "manifest.json")}), encoding="utf-8"
    )
    authorize(data, package, permit, operation="repair", target_version="0.3.0")
    return old


def test_same_release_repairs_damaged_program_and_preserves_data(installation):
    root, data, package, source, permit = installation
    old = make_repair(root, data, package, permit)
    (old / "frontend/index.html").write_bytes(b"damaged")
    UpgradeTransaction(root, data, Platform(source)).apply(package, permit)
    assert (old / "frontend/index.html").read_bytes() == b"fixture"
    assert (data / "config/service.env").read_text(encoding="utf-8") == "old-config"
    assert (
        read_value(
            Path(json.loads((data / "updates/active.json").read_text(encoding="utf-8"))["backup_dir"])
            / "database.sqlite"
        )
        == "original"
    )
    assert (root / ".rollback" / permit["upgrade_id"] / "program/frontend/index.html").read_bytes() == b"damaged"


def test_same_version_different_build_cannot_replace_existing_release(installation):
    root, data, package, source, permit = installation
    old = make_repair(root, data, package, permit)
    (package / "frontend/index.html").write_bytes(b"different-build")
    build_manifest(package, version="0.3.0", commit="f" * 40, webview2_version="135.0.1.2")
    authorize(data, package, permit)
    platform = Platform(source)
    with pytest.raises(UpgradeError, match="同版"):
        UpgradeTransaction(root, data, platform).apply(package, permit)
    assert (old / "frontend/index.html").read_bytes() == b"fixture"
    assert platform.running


def test_retry_old_health_does_not_restore_database_again(installation):
    root, data, package, source, permit = installation
    platform = Platform(source, fail="health")
    healthy = platform.healthy
    failed_old = False

    def fail_new_and_first_old(version):
        nonlocal failed_old
        if version == "0.3.0" and not failed_old:
            failed_old = True
            raise UpgradeError("old health still starting")
        healthy(version)

    platform.healthy = fail_new_and_first_old
    transaction = UpgradeTransaction(root, data, platform)
    with pytest.raises(UpgradeError, match="回退未完成"):
        transaction.apply(package, permit)
    assert read_value(source) == "original"
    set_value(source, "newly-recorded-after-rollback")
    journal = json.loads(transaction.journal_path.read_text(encoding="utf-8"))
    assert journal["rollback_step"] == "awaiting_previous_health"
    transaction.recover()
    assert read_value(source) == "newly-recorded-after-rollback"
    assert json.loads(transaction.journal_path.read_text(encoding="utf-8"))["phase"] == "rolled_back"


def test_legacy_failed_recovery_keeps_current_snapshot_before_restoring(installation):
    root, data, package, source, permit = installation
    platform = Platform(source, fail="power")
    transaction = UpgradeTransaction(root, data, platform)
    with pytest.raises(SystemExit):
        transaction.apply(package, permit)
    old = json.loads(transaction.journal_path.read_text(encoding="utf-8"))
    old.update(schema_version=1, phase="rollback_failed")
    old.pop("rollback_step", None)
    transaction.journal_path.write_text(json.dumps(old), encoding="utf-8")
    set_value(source, "field-record-after-failed-rollback")
    (data / "config/service.env").write_text("current-field-config", encoding="utf-8")
    transaction.recover(preserve_current=True)
    new = json.loads(transaction.journal_path.read_text(encoding="utf-8"))
    preserved = Path(new["pre_recovery_snapshot"])
    assert read_value(preserved / "database.sqlite") == "field-record-after-failed-rollback"
    assert (preserved / "config/service.env").read_text(encoding="utf-8") == "current-field-config"
    assert json.loads((preserved / "journal.json").read_text(encoding="utf-8")) == old
    assert read_value(source) == "original"
    assert (data / "config/service.env").read_text(encoding="utf-8") == "old-config"


def test_payload_outside_owned_staging_is_rejected(installation, tmp_path):
    root, data, package, source, permit = installation
    moved = tmp_path / "foreign"
    package.rename(moved)
    platform = Platform(source)
    with pytest.raises(UpgradeError, match="暂存"):
        UpgradeTransaction(root, data, platform).apply(moved, permit)
    assert platform.running


@pytest.mark.parametrize(
    "phase", ["prepared", "stopped", "backed_up", "program_displaced", "migrating", "starting", "committed"]
)
def test_each_durable_upgrade_boundary_survives_interruption(installation, phase):
    root, data, package, source, permit = installation
    platform = Platform(source)
    transaction = UpgradeTransaction(root, data, platform)
    record = transaction._record

    def interrupted(journal, reached):
        record(journal, reached)
        if reached == phase:
            raise SystemExit("power interrupted after durable phase")

    transaction._record = interrupted
    with pytest.raises(SystemExit):
        transaction.apply(package, permit)
    restarted = UpgradeTransaction(root, data, platform)
    restarted.recover()
    journal = json.loads(restarted.journal_path.read_text(encoding="utf-8"))
    assert journal["phase"] == ("committed" if phase == "committed" else "rolled_back")
    assert read_value(source) == ("migrated" if phase == "committed" else "original")
    assert not restarted.gate_path.exists()


@pytest.mark.parametrize(
    "step", ["data_restored", "program_restored", "previous_configured", "awaiting_previous_health"]
)
def test_each_durable_restore_boundary_resumes_without_replacing_later_records(installation, step):
    root, data, package, source, permit = installation
    platform = Platform(source, fail="health")
    transaction = UpgradeTransaction(root, data, platform)
    record = transaction._record

    def interrupted(journal, phase):
        record(journal, phase)
        if journal.get("rollback_step") == step:
            raise SystemExit("power interrupted after durable restore")

    transaction._record = interrupted
    with pytest.raises(SystemExit):
        transaction.apply(package, permit)
    assert read_value(source) == "original"
    set_value(source, "later-record")
    restarted = UpgradeTransaction(root, data, platform)
    restarted.recover()
    assert read_value(source) == "later-record"
    assert json.loads(restarted.journal_path.read_text(encoding="utf-8"))["phase"] == "rolled_back"


def test_lost_space_after_stop_keeps_original_data_and_resumes_old_service(installation, monkeypatch):
    from smd_desktop.space import SpaceError

    root, data, package, source, permit = installation
    platform = Platform(source)
    transaction = UpgradeTransaction(root, data, platform)
    calls = 0

    def changed_space(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise SpaceError("DB grew after preflight")

    monkeypatch.setattr(transaction, "_space", changed_space)
    with pytest.raises(UpgradeError, match="grew"):
        transaction.apply(package, permit)
    assert read_value(source) == "original"
    assert platform.running and platform.version == "0.3.0"
    assert json.loads(transaction.journal_path.read_text(encoding="utf-8"))["backup_ready"] is False


def test_stopped_activity_recheck_cannot_be_bypassed(installation):
    root, data, package, source, permit = installation
    platform = Platform(source)

    def concurrent_command_seen(bound):
        assert bound == permit and not platform.running
        raise UpgradeError("activity became unknown while service stopped")

    platform.validate_stopped = concurrent_command_seen
    with pytest.raises(UpgradeError, match="activity"):
        UpgradeTransaction(root, data, platform).apply(package, permit)
    assert read_value(source) == "original"
    assert platform.running


def test_retained_journals_survive_next_active_cursor_and_backups_are_not_collected(installation):
    root, data, package, source, permit = installation
    transaction = UpgradeTransaction(root, data, Platform(source))
    transaction.apply(package, permit)
    active = json.loads(transaction.journal_path.read_text(encoding="utf-8"))
    retained = data / "updates/transactions" / f"{permit['upgrade_id']}.json"
    assert json.loads(retained.read_text(encoding="utf-8")) == active
    assert json.loads((Path(active["backup_dir"]) / "journal.json").read_text(encoding="utf-8")) == active
    unrelated = data / "updates/historical-backup"
    unrelated.mkdir()
    (unrelated / "database.sqlite").write_bytes(b"never-auto-delete")
    transaction._cleanup_programs(active)
    assert (unrelated / "database.sqlite").read_bytes() == b"never-auto-delete"


def test_target_validation_needs_no_manual_permission_and_has_no_side_effects(installation):
    root, data, package, source, permit = installation
    (data / "maintenance.json").unlink()
    platform = Platform(source)
    assert UpgradeTransaction(root, data, platform).validate_target(package)["version"] == "0.4.0-rc.1"
    assert not (data / "maintenance.json").exists()
    assert not (data / "updates").exists()
    assert platform.running


def test_cleanup_preserves_any_version_referenced_by_an_unfinished_transaction(installation):
    root, data, package, source, permit = installation
    pending_program = root / "versions/0.2.1"
    obsolete_program = root / "versions/0.2.0"
    for path, version in ((pending_program, "0.2.1"), (obsolete_program, "0.2.0")):
        shutil.copytree(package, path)
        build_manifest(path, version=version, commit="f" * 40, webview2_version="135.0.1.2")
    record = data / "updates/transactions" / f"{'f' * 32}.json"
    record.parent.mkdir(parents=True)
    record.write_text(
        json.dumps(
            {
                "upgrade_id": "f" * 32,
                "phase": "rollback_failed",
                "target_version": "0.2.1",
                "previous": {"version": "0.1.9"},
            }
        ),
        encoding="utf-8",
    )
    UpgradeTransaction(root, data, Platform(source)).apply(package, permit)
    assert pending_program.exists()
    assert not obsolete_program.exists()
    assert (root / "versions/0.3.0").exists()


def test_same_version_repair_failed_health_restores_original_program(installation):
    root, data, package, source, permit = installation
    old = make_repair(root, data, package, permit)
    (old / "frontend/index.html").write_bytes(b"damaged-before-repair")
    platform = Platform(source)
    calls = 0

    def unhealthy_once(version):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise UpgradeError("repair readiness failed")

    platform.healthy = unhealthy_once
    with pytest.raises(UpgradeError, match="回退"):
        UpgradeTransaction(root, data, platform).apply(package, permit)
    assert (old / "frontend/index.html").read_bytes() == b"damaged-before-repair"
    assert read_value(source) == "original"
    assert not (data / "maintenance.json").exists()


def test_failed_program_collection_does_not_turn_committed_install_into_error(installation, monkeypatch):
    root, data, package, source, permit = installation
    transaction = UpgradeTransaction(root, data, Platform(source))

    def antivirus_busy(journal):
        raise PermissionError("old files are in use")

    monkeypatch.setattr(transaction, "_cleanup_programs", antivirus_busy)
    transaction.apply(package, permit)
    assert json.loads(transaction.journal_path.read_text(encoding="utf-8"))["phase"] == "committed"
    assert read_value(source) == "migrated"


def test_legacy_api_claim_remains_recoverable_but_cannot_authorize_a_new_install(installation):
    root, data, package, source, permit = installation
    legacy = {key: permit[key] for key in ("state", "upgrade_id", "current_version", "target_version", "db_path")}
    (data / "maintenance.json").write_text(json.dumps(legacy), encoding="utf-8")
    platform = Platform(source)
    transaction = UpgradeTransaction(root, data, platform)
    with pytest.raises(UpgradeError, match="授权"):
        transaction.apply(package, legacy)
    assert platform.running
    transaction.recover()
    assert json.loads(transaction.journal_path.read_text(encoding="utf-8"))["phase"] == "rolled_back"
    assert read_value(source) == "original"
    assert not (data / "maintenance.json").exists()


def test_corrupt_backup_configuration_cannot_replace_live_configuration(installation):
    root, data, package, source, permit = installation
    platform = Platform(source, fail="power")
    transaction = UpgradeTransaction(root, data, platform)
    with pytest.raises(SystemExit):
        transaction.apply(package, permit)
    journal = json.loads(transaction.journal_path.read_text(encoding="utf-8"))
    (Path(journal["backup_dir"]) / "config/service.env").write_text("tampered", encoding="utf-8")
    with pytest.raises(UpgradeError, match="配置备份摘要"):
        transaction.recover()
    assert (data / "config/service.env").read_text(encoding="utf-8") == "old-config"
    assert not platform.running
    assert (data / "maintenance.json").exists()


def test_new_waiting_health_journal_cannot_resume_against_an_unrelated_pointer(installation):
    root, data, package, source, permit = installation
    platform = Platform(source, fail="health")
    platform.healthy = lambda version: (_ for _ in ()).throw(UpgradeError("still starting"))
    transaction = UpgradeTransaction(root, data, platform)
    with pytest.raises(UpgradeError, match="回退未完成"):
        transaction.apply(package, permit)
    set_value(source, "current-record")
    (data / "installation.json").write_text(json.dumps({"version": "0.1.0"}), encoding="utf-8")
    with pytest.raises(UpgradeError, match="指针"):
        transaction.recover()
    assert read_value(source) == "current-record"
    assert (data / "maintenance.json").exists()
