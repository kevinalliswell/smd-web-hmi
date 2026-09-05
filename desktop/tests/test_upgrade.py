"""真实临时 SQLite 证明升级失败与断电能恢复完整旧版本。"""

import json
import sqlite3
from pathlib import Path

import pytest
from smd_desktop.bundle import build_manifest
from smd_desktop.upgrade import UpgradeError, UpgradeTransaction


def read_value(path):
    with sqlite3.connect(path) as db:
        return db.execute("SELECT value FROM sample").fetchone()[0]


class Platform:
    def __init__(self, db_path, fail=None):
        self.db_path = db_path
        self.fail = fail
        self.version = "0.3.0"
        self.running = True

    def stop(self):
        self.running = False

    def configure(self, version_dir):
        self.version = version_dir.name

    def migrate(self, version_dir):
        with sqlite3.connect(self.db_path) as db:
            db.execute("UPDATE sample SET value='migrated'")
        if self.fail == "power":
            raise SystemExit("power cut")

    def start(self):
        self.running = True

    def healthy(self, version):
        if self.fail == "health" and version != "0.3.0":
            raise UpgradeError("new service did not become healthy")
        assert self.running and self.version == version


@pytest.fixture
def installation(tmp_path):
    root, data, package = tmp_path / "program", tmp_path / "data", tmp_path / "package"
    root.mkdir()
    data.mkdir()
    package.mkdir()
    (root / "versions/0.3.0").mkdir(parents=True)
    (root / "versions/0.3.0/runtime.txt").write_text("old-runtime")
    (data / "config").mkdir()
    (data / "config/service.env").write_text("old-config")
    db_path = tmp_path / "actual-custom-location.db"
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE sample(value TEXT)")
        db.execute("INSERT INTO sample VALUES ('original')")
    (data / "installation.json").write_text(json.dumps({"version": "0.3.0"}))
    for name in (
        "SmdService/SmdService.exe",
        "SmdDesktop/SmdDesktop.exe",
        "SmdUpdate/SmdUpdate.exe",
        "frontend/index.html",
        "webview2/msedgewebview2.exe",
        "webview2/icudtl.dat",
        "sbom.cdx.json",
        "configure-acl.ps1",
        "configure-recovery.ps1",
    ):
        path = package / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")
    build_manifest(package, version="0.4.0-rc.1", commit="a" * 40, webview2_version="135.0.1.2")
    permit = {
        "state": "claimed",
        "upgrade_id": "b" * 32,
        "target_version": "0.4.0-rc.1",
        "current_version": "0.3.0",
        "db_path": str(db_path),
    }
    (data / "maintenance.json").write_text(json.dumps(permit))
    return root, data, package, db_path, permit


def test_health_failure_rolls_back_actual_database_config_and_runtime(installation):
    root, data, package, db_path, permit = installation
    platform = Platform(db_path, "health")
    with pytest.raises(UpgradeError, match="healthy"):
        UpgradeTransaction(root, data, platform).apply(package, permit)
    assert read_value(db_path) == "original"
    assert (data / "config/service.env").read_text() == "old-config"
    assert (root / "versions/0.3.0/runtime.txt").read_text() == "old-runtime"
    assert json.loads((data / "installation.json").read_text())["version"] == "0.3.0"
    assert platform.version == "0.3.0" and platform.running
    assert not (data / "maintenance.json").exists()


def test_power_cut_during_migration_is_recovered_idempotently(installation):
    root, data, package, db_path, permit = installation
    platform = Platform(db_path, "power")
    transaction = UpgradeTransaction(root, data, platform)
    with pytest.raises(SystemExit):
        transaction.apply(package, permit)
    assert read_value(db_path) == "migrated"
    assert (data / "maintenance.json").exists()
    restarted = UpgradeTransaction(root, data, platform)
    restarted.recover()
    restarted.recover()
    assert read_value(db_path) == "original"
    assert platform.version == "0.3.0"
    assert json.loads((data / "updates/active.json").read_text())["phase"] == "rolled_back"


def test_commit_preserves_old_runtime_and_clears_gate(installation):
    root, data, package, db_path, permit = installation
    platform = Platform(db_path)
    UpgradeTransaction(root, data, platform).apply(package, permit)
    assert read_value(db_path) == "migrated"
    assert (root / "versions/0.3.0/runtime.txt").exists()
    assert platform.version == "0.4.0-rc.1"
    assert not (data / "maintenance.json").exists()


def test_unclaimed_or_wrong_version_permit_cannot_stop_service(installation):
    root, data, package, db_path, permit = installation
    platform = Platform(db_path)
    with pytest.raises(UpgradeError):
        UpgradeTransaction(root, data, platform).apply(package, {**permit, "state": "prepared"})
    assert platform.running
    assert not (root / "versions/0.4.0-rc.1").exists()


def test_recover_old_committed_journal_does_not_clear_new_maintenance(installation):
    root, data, package, db_path, permit = installation
    transaction = UpgradeTransaction(root, data, Platform(db_path))
    transaction.apply(package, permit)
    new_permit = {**permit, "upgrade_id": "c" * 32, "state": "prepared"}
    (data / "maintenance.json").write_text(json.dumps(new_permit))
    transaction.recover()
    assert (data / "maintenance.json").exists()


def test_corrupt_backup_keeps_gate_and_service_stopped(installation):
    root, data, package, source, permit = installation
    platform = Platform(source, fail="power")
    transaction = UpgradeTransaction(root, data, platform)
    with pytest.raises(SystemExit):
        transaction.apply(package, permit)
    journal = json.loads(transaction.journal_path.read_text())
    (Path(journal["backup_dir"]) / "database.sqlite").write_bytes(b"corrupt")
    with pytest.raises(UpgradeError, match="摘要"):
        transaction.recover()
    assert (data / "maintenance.json").exists()
    assert not platform.running
    assert json.loads(transaction.journal_path.read_text())["phase"] == "rollback_failed"


def test_copy_failure_is_inside_journal_and_rolls_back(installation, monkeypatch):
    from smd_desktop import upgrade

    root, data, package, source, permit = installation
    transaction = UpgradeTransaction(root, data, Platform(source))

    def fail_copy(*args, **kwargs):
        raise OSError("disk full during staging")

    monkeypatch.setattr(upgrade.shutil, "copytree", fail_copy)
    with pytest.raises(UpgradeError, match="回退"):
        transaction.apply(package, permit)
    assert json.loads(transaction.journal_path.read_text())["phase"] == "rolled_back"
    assert not transaction.gate_path.exists()
    assert read_value(source) == "original"


def test_claimed_before_journal_can_recover_without_bypassing_transaction(installation):
    root, data, package, source, permit = installation
    platform = Platform(source)
    transaction = UpgradeTransaction(root, data, platform)
    assert not transaction.journal_path.exists()
    transaction.recover()
    journal = json.loads(transaction.journal_path.read_text())
    assert journal["upgrade_id"] == permit["upgrade_id"]
    assert journal["phase"] == "rolled_back"
    assert journal["recovered_unstarted_claim"] is True
    assert platform.version == "0.3.0" and platform.running
    assert not transaction.gate_path.exists()
    assert read_value(source) == "original"


def test_orphan_claim_recovery_keeps_gate_if_old_service_cannot_be_verified(installation):
    root, data, package, source, permit = installation
    platform = Platform(source)

    def fail_health(version):
        raise UpgradeError("old schema cannot be verified")

    platform.healthy = fail_health
    transaction = UpgradeTransaction(root, data, platform)
    with pytest.raises(UpgradeError):
        transaction.recover()
    assert transaction.gate_path.exists()
    assert json.loads(transaction.journal_path.read_text())["phase"] == "rollback_failed"


def test_power_cut_during_staging_is_recoverable(installation, monkeypatch):
    from smd_desktop import upgrade

    root, data, package, source, permit = installation
    platform = Platform(source)
    transaction = UpgradeTransaction(root, data, platform)

    def power_cut(*args, **kwargs):
        raise SystemExit("power cut copying payload")

    with monkeypatch.context() as patch:
        patch.setattr(upgrade.shutil, "copytree", power_cut)
        with pytest.raises(SystemExit):
            transaction.apply(package, permit)
    transaction.recover()
    assert json.loads(transaction.journal_path.read_text())["phase"] == "rolled_back"
    assert not transaction.gate_path.exists()


def test_failed_initial_journal_write_is_recoverable_after_storage_recovers(installation, monkeypatch):
    root, data, package, source, permit = installation
    transaction = UpgradeTransaction(root, data, Platform(source))

    def no_space(*args, **kwargs):
        raise OSError("journal disk full")

    with monkeypatch.context() as patch:
        patch.setattr(transaction, "_record", no_space)
        with pytest.raises(OSError, match="disk full"):
            transaction.apply(package, permit)
    assert transaction.gate_path.exists()
    assert not transaction.journal_path.exists()
    transaction.recover()
    assert json.loads(transaction.journal_path.read_text())["phase"] == "rolled_back"
    assert not transaction.gate_path.exists()


def test_missing_journal_with_backup_evidence_never_assumes_no_database_change(installation):
    root, data, package, source, permit = installation
    backup_dir = data / "updates" / permit["upgrade_id"]
    backup_dir.mkdir(parents=True)
    (backup_dir / "database.sqlite").write_bytes(b"unaccounted backup")
    transaction = UpgradeTransaction(root, data, Platform(source))
    with pytest.raises(UpgradeError, match="备份产物"):
        transaction.recover()
    assert transaction.gate_path.exists()
    assert not transaction.journal_path.exists()
