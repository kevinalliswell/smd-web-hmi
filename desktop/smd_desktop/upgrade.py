"""Installer-owned overwrite transactions with data-preserving, resumable rollback."""

from __future__ import annotations

import json
import logging
import re
import shutil
import uuid
from pathlib import Path
from typing import Protocol

from app.services.sqlite_backup import backup_sqlite

from .bundle import sha256, verify_bundle
from .snapshot import SnapshotError, configuration_hashes, restore_snapshot
from .space import MIN_DB_FREE_BYTES, check_upgrade_space
from .storage import atomic_json
from .versions import compare_versions


class UpgradeError(RuntimeError):
    """升级或回退未成功；维护门禁必须保持关闭。"""


class Platform(Protocol):
    def stop(self) -> None: ...
    def configure(self, version_dir: Path) -> None: ...
    def migrate(self, version_dir: Path) -> None: ...
    def start(self) -> None: ...
    def healthy(self, version: str) -> None: ...


class UpgradeTransaction:
    """Caller owns the updater mutex and validates the ACL-bound local authorization.

    New installs use schema 2 installer permissions. Schema 1 API claims are accepted
    only by recovery, so existing failed RC transactions remain recoverable without
    reviving the removed user-facing prepare/claim workflow.
    """

    TERMINAL = {"committed", "rolled_back"}
    AUTH_FIELDS = (
        "schema_version",
        "issuer",
        "state",
        "upgrade_id",
        "operation",
        "current_version",
        "target_version",
        "package_sha256",
        "request_sha256",
        "admin_sid",
        "physical_shutdown_confirmed",
        "db_path",
    )
    RESTORED_STEPS = {"data_restored", "program_restored", "previous_configured", "awaiting_previous_health"}

    def __init__(
        self, install_dir: Path, data_dir: Path, platform: Platform, *, min_db_free_bytes: int = MIN_DB_FREE_BYTES
    ):
        self.install = install_dir.resolve()
        self.data = data_dir.resolve()
        self.platform = platform
        self.min_db_free_bytes = min_db_free_bytes
        self.journal_path = self.data / "updates/active.json"
        self.pointer_path = self.data / "installation.json"
        self.gate_path = self.data / "maintenance.json"

    def _record(self, journal: dict, phase: str) -> None:
        journal["phase"] = phase
        atomic_json(self.journal_path, journal)
        # active.json is a cursor, not the only retained copy of recovery provenance.
        atomic_json(self.data / "updates/transactions" / f"{journal['upgrade_id']}.json", journal)
        backup_dir = Path(journal["backup_dir"])
        if backup_dir.is_dir():
            atomic_json(backup_dir / "journal.json", journal)

    def _clear_gate(self, journal: dict) -> None:
        if not self.gate_path.exists():
            return
        gate = json.loads(self.gate_path.read_text(encoding="utf-8"))
        if gate.get("upgrade_id") == journal["upgrade_id"]:
            self.gate_path.unlink()

    def _staged_package(self, package: Path) -> Path:
        absolute = package.absolute()
        stage_root = self.install / ".staging"
        # Check the input ancestry before resolve(), which would erase evidence of a junction.
        for path in (absolute, absolute.parent, absolute.parent.parent):
            if path.is_symlink() or path.is_junction():
                raise UpgradeError("安装暂存目录不得包含重解析路径")
        resolved = absolute.resolve()
        if (
            resolved.name != "payload"
            or resolved.parent.parent != stage_root
            or not re.fullmatch(r"[0-9a-f]{32}", resolved.parent.name)
        ):
            raise UpgradeError("完整程序包必须来自安装目录中本轮受保护的暂存目录")
        if resolved.stat().st_dev != self.install.stat().st_dev:
            raise UpgradeError("安装暂存目录必须与程序目录位于同一卷")
        return resolved

    def validate_target(self, package: Path) -> dict:
        """Reject downgrade/version reuse before any maintenance request or service stop."""
        package = self._staged_package(package)
        manifest = verify_bundle(package)
        previous = json.loads(self.pointer_path.read_text(encoding="utf-8"))
        ordering = compare_versions(manifest["version"], previous["version"])
        if ordering < 0:
            raise UpgradeError("普通安装不允许降级；恢复必须使用对应程序及数据库备份")
        if ordering == 0:
            installed_hash = previous.get("manifest_sha256")
            if installed_hash is None:
                # Legacy RC manifests are in the ACL-protected version directory.
                old_manifest = self.install / "versions" / previous["version"] / "manifest.json"
                if not old_manifest.is_file() or old_manifest.is_symlink():
                    raise UpgradeError("缺少可信已安装清单，无法证明同版修复来自相同构建")
                installed_hash = sha256(old_manifest)
            if installed_hash != sha256(package / "manifest.json"):
                raise UpgradeError("同版安装来自不同构建，禁止复用已发布版本号")
        return manifest

    def _validate_permit(self, manifest: dict, package: Path, previous: dict, permit: dict) -> Path:
        stored = json.loads(self.gate_path.read_text(encoding="utf-8"))
        if (
            any(permit.get(key) != stored.get(key) for key in self.AUTH_FIELDS)
            or permit.get("schema_version") != 2
            or permit.get("issuer") != "windows_installer"
            or permit.get("state") != "claimed"
            or permit.get("operation") not in {"upgrade", "repair"}
            or permit.get("target_version") != manifest["version"]
            or permit.get("current_version") != previous["version"]
            or permit.get("package_sha256") != sha256(package / "manifest.json")
            or not re.fullmatch(r"[0-9a-f]{32}", str(permit.get("upgrade_id", "")))
            or not re.fullmatch(r"[0-9a-f]{64}", str(permit.get("request_sha256", "")))
            or not re.fullmatch(r"S-1-(?:\d+-)+\d+", str(permit.get("admin_sid", "")))
            or not isinstance(permit.get("physical_shutdown_confirmed"), bool)
        ):
            raise UpgradeError("安装器授权与本次操作、程序包或已安装版本不一致")
        source = Path(permit["db_path"])
        if not source.is_absolute() or not source.is_file():
            raise UpgradeError("安装器授权数据库不是后台实际数据库")
        if manifest["version"] == previous["version"]:
            if permit["operation"] != "repair":
                raise UpgradeError("同版安装必须使用修复操作")
        elif permit["operation"] != "upgrade":
            raise UpgradeError("修复操作的目标必须为当前版本")
        return source.resolve()

    def _space(self, source: Path, *, preserve_current: bool = False) -> None:
        check_upgrade_space(
            self.install,
            self.data,
            source,
            min_db_free_bytes=self.min_db_free_bytes,
            preserve_current=preserve_current,
        )

    def apply(self, package: Path, permit: dict) -> None:
        package = self._staged_package(package)
        manifest = self.validate_target(package)
        previous = json.loads(self.pointer_path.read_text(encoding="utf-8"))
        if self.journal_path.exists():
            existing = json.loads(self.journal_path.read_text(encoding="utf-8"))
            if existing["phase"] not in self.TERMINAL:
                raise UpgradeError("存在未完成升级，必须先恢复")
        source = self._validate_permit(manifest, package, previous, permit)
        self._space(source)  # Refuse before stopping the existing service.
        identity = permit["upgrade_id"]
        backup_dir = self.data / "updates" / identity
        target = self.install / "versions" / manifest["version"]
        displaced = self.install / ".rollback" / identity / "program"
        if backup_dir.exists() or displaced.parent.exists():
            raise UpgradeError("本次安装编号已经存在备份产物，禁止覆盖")
        if target.exists() and manifest["version"] != previous["version"] and verify_bundle(target) != manifest:
            raise UpgradeError("目标版本目录已存在且内容不同")
        journal = {
            "schema_version": 2,
            "upgrade_id": identity,
            "operation": permit["operation"],
            "previous": previous,
            "target_version": manifest["version"],
            "package_sha256": permit["package_sha256"],
            "request_sha256": permit["request_sha256"],
            "admin_sid": permit["admin_sid"],
            "physical_shutdown_confirmed": permit["physical_shutdown_confirmed"],
            "db_path": str(source),
            "backup_dir": str(backup_dir),
            "backup_ready": False,
            "staging_path": str(package),
            "displaced_program": str(displaced),
            "target_existed": target.exists(),
        }
        self._record(journal, "prepared")
        try:
            self.platform.stop()
            self._record(journal, "stopped")
            validator = getattr(self.platform, "validate_stopped", None)
            if validator is not None:
                validator(permit)
            self._space(source)  # Recheck DB/WAL growth after the actual process has exited.
            backup_dir.mkdir(parents=True, exist_ok=False)
            backup = backup_dir / "database.sqlite"
            backup_sqlite(source, backup)
            shutil.copytree(self.data / "config", backup_dir / "config")
            journal["backup_sha256"] = sha256(backup)
            journal["config_hashes"] = self._config_hashes(backup_dir / "config")
            journal["backup_ready"] = True
            self._record(journal, "backed_up")
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                displaced.parent.mkdir(parents=True, exist_ok=False)
                target.replace(displaced)
            self._record(journal, "program_displaced")
            package.replace(target)  # A same-volume rename, never a second full payload copy.
            self._record(journal, "migrating")
            self.platform.migrate(target)
            self.platform.configure(target)
            atomic_json(
                self.pointer_path,
                {**previous, "version": manifest["version"], "manifest_sha256": permit["package_sha256"]},
            )
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
                raise UpgradeError(
                    "升级失败且回退未完成；已保留数据和恢复记录，请重新运行安装器恢复"
                ) from rollback_error
            raise UpgradeError(f"升级已回退: {error}") from error
        try:
            self._cleanup_programs(journal)
        except (OSError, ValueError):
            # A committed healthy installation cannot become a failed installation
            # merely because antivirus or a still-open desktop delayed old-file cleanup.
            logging.warning("安装已提交，旧程序清理暂未完成；已保留相关目录")

    @staticmethod
    def _config_hashes(root: Path) -> dict[str, str]:
        try:
            return configuration_hashes(root)
        except SnapshotError as error:
            raise UpgradeError(str(error)) from error

    def _restore_data(self, journal: dict) -> None:
        try:
            restore_snapshot(self.data, journal)
        except SnapshotError as error:
            raise UpgradeError(str(error)) from error

    def _restore_program(self, journal: dict) -> None:
        if not journal.get("displaced_program"):
            return
        displaced = Path(journal["displaced_program"])
        expected = self.install / ".rollback" / journal["upgrade_id"] / "program"
        if displaced != expected:
            raise UpgradeError("恢复程序路径不属于本次事务")
        if not displaced.exists():
            return
        target = self.install / "versions" / journal["target_version"]
        if target.exists():
            rejected = displaced.with_name("rejected-program")
            if rejected.exists():
                raise UpgradeError("恢复程序目录存在冲突，保持原始产物")
            target.replace(rejected)
        displaced.replace(target)

    def _rollback(self, journal: dict) -> None:
        step = journal.get("rollback_step") if journal.get("schema_version") == 2 else None
        previous = journal["previous"]
        if step == "awaiting_previous_health":
            # Proof is durable and version-bound: old DB/config have already been restored.
            # Retrying readiness must not erase records written by the restarted old service.
            pointer = json.loads(self.pointer_path.read_text(encoding="utf-8"))
            if pointer != previous:
                raise UpgradeError("待验活回退的已安装指针与原版本不一致，禁止推断恢复完成")
            self.platform.start()
            self.platform.healthy(previous["version"])
        else:
            self._record(journal, "rolling_back")
            self.platform.stop()
            if step not in self.RESTORED_STEPS:
                self._restore_data(journal)
                journal["schema_version"] = 2
                journal["rollback_step"] = "data_restored"
                self._record(journal, "rolling_back")
            if step not in {"program_restored", "previous_configured"}:
                self._restore_program(journal)
                journal["rollback_step"] = "program_restored"
                self._record(journal, "rolling_back")
            self.platform.configure(self.install / "versions" / previous["version"])
            atomic_json(self.pointer_path, previous)
            journal["rollback_step"] = "previous_configured"
            self._record(journal, "rolling_back")
            # Record before start(): start may succeed while its caller is interrupted.
            journal["rollback_step"] = "awaiting_previous_health"
            self._record(journal, "rolling_back")
            self.platform.start()
            self.platform.healthy(previous["version"])
        self._record(journal, "rolled_back")
        self._clear_gate(journal)

    def _preserve_current(self, journal: dict) -> None:
        if journal.get("pre_recovery_snapshot"):
            preserved = Path(journal["pre_recovery_snapshot"])
            snapshot = json.loads((preserved / "snapshot.json").read_text(encoding="utf-8"))
            if sha256(preserved / "database.sqlite") != snapshot["database_sha256"]:
                raise UpgradeError("恢复前保留副本摘要不一致，禁止继续覆盖")
            if self._config_hashes(preserved / "config") != snapshot["config_hashes"]:
                raise UpgradeError("恢复前配置副本摘要不一致，禁止继续覆盖")
            return
        source = Path(journal["db_path"])
        self._space(source, preserve_current=True)
        self.platform.stop()
        self._space(source, preserve_current=True)
        preserved = self.data / "updates" / journal["upgrade_id"] / f"before-recovery-{uuid.uuid4().hex}"
        preserved.mkdir(parents=True, exist_ok=False)
        atomic_json(preserved / "journal.json", journal)
        backup_sqlite(source, preserved / "database.sqlite")
        shutil.copytree(self.data / "config", preserved / "config")
        atomic_json(
            preserved / "snapshot.json",
            {
                "schema_version": 1,
                "upgrade_id": journal["upgrade_id"],
                "db_path": str(source),
                "database_sha256": sha256(preserved / "database.sqlite"),
                "config_hashes": self._config_hashes(preserved / "config"),
            },
        )
        journal["pre_recovery_snapshot"] = str(preserved)
        self._record(journal, journal["phase"])

    def _recover_unstarted_claim(self) -> dict | None:
        """Recover either a legacy API claim or an authorized installer before its journal."""
        if not self.gate_path.exists():
            return None
        gate = json.loads(self.gate_path.read_text(encoding="utf-8"))
        if gate.get("state") != "claimed":
            return None
        if gate.get("schema_version") == 2 and gate.get("issuer") != "windows_installer":
            raise UpgradeError("无法识别安装器维护授权来源")
        previous = json.loads(self.pointer_path.read_text(encoding="utf-8"))
        identity = gate.get("upgrade_id", "")
        if not re.fullmatch(r"[0-9a-f]{32}", identity) or gate.get("current_version") != previous.get("version"):
            raise UpgradeError("未记录的领取票据与已安装版本不一致，保持维护门禁")
        source = Path(gate.get("db_path", "")).resolve()
        if not source.is_file():
            raise UpgradeError("无法验证未开始升级的实际数据库，保持维护门禁")
        backup_dir = self.data / "updates" / identity
        if backup_dir.exists() and any(backup_dir.iterdir()):
            raise UpgradeError("存在备份产物但升级日志缺失，需人工核对，禁止推断尚未修改数据库")
        journal = {
            "schema_version": 2,
            "upgrade_id": identity,
            "previous": previous,
            "target_version": gate.get("target_version"),
            "db_path": str(source),
            "backup_dir": str(backup_dir),
            "backup_ready": False,
            "recovered_unstarted_claim": True,
        }
        self._record(journal, "claim_recovery")
        return journal

    def recover(self, *, preserve_current: bool = False) -> None:
        """Resume one recovery attempt; callers obtain administrator confirmation first.

        preserve_current is mandatory in the installer for a schema 1 rollback_failed
        journal. Automatic scheduled recovery retains the legacy no-UI behavior, while
        all new journals persist each completed restore step and never replay a DB
        replacement just because the previous service readiness is still pending.
        """
        journal = None
        if self.journal_path.exists():
            journal = json.loads(self.journal_path.read_text(encoding="utf-8"))
            if journal["phase"] in self.TERMINAL:
                self._clear_gate(journal)
                journal = None
        if journal is None:
            journal = self._recover_unstarted_claim()
            if journal is None:
                return
        try:
            if preserve_current and journal.get("schema_version") == 1 and journal["phase"] == "rollback_failed":
                self._preserve_current(journal)
            self._rollback(journal)
        except Exception as error:
            journal["rollback_error"] = str(error)
            self._record(journal, "rollback_failed")
            raise

    def _cleanup_programs(self, journal: dict) -> None:
        """Only redundant version directories are disposable; no DB/config backup is removed."""
        keep = {journal["target_version"], journal["previous"]["version"]}
        records = [
            *(self.data / "updates").glob("*/journal.json"),
            *(self.data / "updates/transactions").glob("*.json"),
        ]
        pending_ids = set()
        known_ids = set()
        for record in records:
            try:
                other = json.loads(record.read_text(encoding="utf-8"))
                identity = other["upgrade_id"]
                if not re.fullmatch(r"[0-9a-f]{32}", identity):
                    return
                known_ids.add(identity)
                if other.get("phase") not in self.TERMINAL:
                    pending_ids.add(identity)
                    keep.add(other["target_version"])
                    keep.add(other["previous"]["version"])
            except (OSError, ValueError, KeyError):
                return  # Unknown recovery references disable collection conservatively.
        for path in (self.install / "versions").iterdir():
            if path.name in keep or not path.is_dir() or path.is_symlink() or path.is_junction():
                continue
            try:
                verify_bundle(path)
                shutil.rmtree(path)
            except (OSError, ValueError):
                logging.warning("保留无法验证或暂时占用的旧程序目录 %s", path.name)
        rollback_root = self.install / ".rollback"
        if rollback_root.is_dir() and not rollback_root.is_symlink() and not rollback_root.is_junction():
            for path in rollback_root.iterdir():
                if (
                    path.name not in known_ids
                    or path.name in pending_ids
                    or path.name == journal["upgrade_id"]
                    or not path.is_dir()
                    or path.is_symlink()
                    or path.is_junction()
                ):
                    continue
                try:
                    # Only our two program artifacts are eligible; unknown additions
                    # could be a field recovery copy and are retained conservatively.
                    if any(child.name not in {"program", "rejected-program"} for child in path.iterdir()):
                        continue
                    if any(child.is_symlink() or child.is_junction() for child in path.rglob("*")):
                        continue
                    shutil.rmtree(path)
                except OSError:
                    logging.warning("保留暂时占用的历史修复程序目录 %s", path.name)
