"""人工离线升级事务：版本目录不变更旧运行时，失败/断电统一回退。"""

from __future__ import annotations

import json
import re
import shutil
import uuid
from pathlib import Path
from typing import Protocol

from app.services.sqlite_backup import backup_sqlite

from .bundle import sha256, verify_bundle
from .storage import atomic_json


class UpgradeError(RuntimeError):
    """升级或回退未成功；维护门禁必须保持关闭。"""


class Platform(Protocol):
    def stop(self) -> None: ...
    def configure(self, version_dir: Path) -> None: ...
    def migrate(self, version_dir: Path) -> None: ...
    def start(self) -> None: ...
    def healthy(self, version: str) -> None: ...


class UpgradeTransaction:
    """调用方必须持有系统级升级互斥锁并已从旧后台领取维护票据。"""

    TERMINAL = {"committed", "rolled_back"}

    def __init__(self, install_dir: Path, data_dir: Path, platform: Platform):
        self.install = install_dir.resolve()
        self.data = data_dir.resolve()
        self.platform = platform
        self.journal_path = self.data / "updates/active.json"
        self.pointer_path = self.data / "installation.json"
        self.gate_path = self.data / "maintenance.json"

    def _record(self, journal: dict, phase: str) -> None:
        journal["phase"] = phase
        atomic_json(self.journal_path, journal)

    def _clear_gate(self, journal: dict) -> None:
        if not self.gate_path.exists():
            return
        gate = json.loads(self.gate_path.read_text(encoding="utf-8"))
        if gate.get("upgrade_id") == journal["upgrade_id"]:
            self.gate_path.unlink()

    def apply(self, package: Path, permit: dict) -> None:
        manifest = verify_bundle(package)
        previous = json.loads(self.pointer_path.read_text(encoding="utf-8"))
        if self.journal_path.exists():
            existing = json.loads(self.journal_path.read_text(encoding="utf-8"))
            if existing["phase"] not in self.TERMINAL:
                raise UpgradeError("存在未完成升级，必须先恢复")
        stored = json.loads(self.gate_path.read_text(encoding="utf-8"))
        if (
            permit.get("state") != "claimed"
            or stored.get("state") != "claimed"
            or permit.get("upgrade_id") != stored.get("upgrade_id")
            or permit.get("target_version") != manifest["version"]
            or permit.get("current_version") != previous["version"]
            or manifest["version"] == previous["version"]
            or not re.fullmatch(r"[0-9a-f]{32}", permit.get("upgrade_id", ""))
        ):
            raise UpgradeError("维护票据未领取或版本不一致")
        source = Path(permit["db_path"]).resolve()
        if source != Path(stored["db_path"]).resolve() or not source.is_file():
            raise UpgradeError("维护票据数据库不是后台实际数据库")
        target = self.install / "versions" / manifest["version"]
        if target.exists():
            if verify_bundle(target) != manifest:
                raise UpgradeError("目标版本目录已存在且内容不同")
        else:
            staged = target.with_name(f".{target.name}.{uuid.uuid4().hex}.staging")
            try:
                shutil.copytree(package, staged)
                verify_bundle(staged)
                staged.replace(target)
            finally:
                if staged.exists():
                    shutil.rmtree(staged)
        backup_dir = self.data / "updates" / permit["upgrade_id"]
        backup_dir.mkdir(parents=True, exist_ok=False)
        journal = {
            "schema_version": 1,
            "upgrade_id": permit["upgrade_id"],
            "previous": previous,
            "target_version": manifest["version"],
            "db_path": str(source),
            "backup_dir": str(backup_dir),
            "backup_ready": False,
        }
        self._record(journal, "prepared")
        try:
            self.platform.stop()
            self._record(journal, "stopped")
            backup = backup_dir / "database.sqlite"
            backup_sqlite(source, backup)
            shutil.copytree(self.data / "config", backup_dir / "config")
            journal["backup_sha256"] = sha256(backup)
            journal["backup_ready"] = True
            self._record(journal, "migrating")
            self.platform.migrate(target)
            self.platform.configure(target)
            atomic_json(self.pointer_path, {**previous, "version": manifest["version"]})
            self._record(journal, "starting")
            self.platform.start()
            self.platform.healthy(manifest["version"])
            self._record(journal, "committed")
            self._clear_gate(journal)
        except Exception as error:
            journal["last_error"] = str(error)
            try:
                self._rollback(journal)
            except Exception as rollback_error:
                journal["rollback_error"] = str(rollback_error)
                self._record(journal, "rollback_failed")
                raise UpgradeError("升级失败且回退未完成；保持维护锁，请执行本机恢复") from rollback_error
            raise UpgradeError(f"升级已回退: {error}") from error

    def _rollback(self, journal: dict) -> None:
        self._record(journal, "rolling_back")
        self.platform.stop()  # 必须确认进程退出后才恢复 DB/WAL。
        if journal["backup_ready"]:
            backup_dir = Path(journal["backup_dir"])
            backup = backup_dir / "database.sqlite"
            if sha256(backup) != journal["backup_sha256"]:
                raise UpgradeError("备份摘要不一致，禁止恢复")
            source = Path(journal["db_path"])
            restored = source.with_suffix(".restore.sqlite")
            restored.unlink(missing_ok=True)
            backup_sqlite(backup, restored)
            for suffix in ("-wal", "-shm"):
                Path(str(source) + suffix).unlink(missing_ok=True)
            restored.replace(source)
            config = self.data / "config"
            if config.exists():
                shutil.rmtree(config)
            shutil.copytree(backup_dir / "config", config)
        previous = journal["previous"]
        self.platform.configure(self.install / "versions" / previous["version"])
        atomic_json(self.pointer_path, previous)
        self.platform.start()
        self.platform.healthy(previous["version"])
        self._record(journal, "rolled_back")
        self._clear_gate(journal)

    def recover(self) -> None:
        """断电重启后显式执行；重复恢复不会再次改动已提交数据库。"""
        if not self.journal_path.exists():
            return
        journal = json.loads(self.journal_path.read_text(encoding="utf-8"))
        if journal["phase"] in self.TERMINAL:
            self._clear_gate(journal)
            return
        try:
            self._rollback(journal)
        except Exception:
            self._record(journal, "rollback_failed")
            raise
