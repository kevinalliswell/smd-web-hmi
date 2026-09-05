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
