"""Reinstall only data preserved by a verifiable, completed uninstall of this device."""

from __future__ import annotations

import json
import re
import shutil
import uuid
from pathlib import Path

from dotenv import dotenv_values

from app.services.sqlite_backup import backup_sqlite

from .bundle import sha256, verify_version
from .installer_authorization import environment, protected_json, regular_path
from .snapshot import configuration_hashes, restore_snapshot
from .space import MIN_DB_FREE_BYTES, check_upgrade_space
from .storage import atomic_json
from .versions import compare_versions


class ReinstallError(RuntimeError):
    pass


def _read(path: Path) -> dict:
    regular_path(path)
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ReinstallError("缺少可核实的卸载或重装记录，请保留已有数据") from error
    if not isinstance(result, dict):
        raise ReinstallError("卸载或重装记录格式错误")
    return result


def _record(data: Path, journal: dict, phase: str) -> None:
    journal["phase"] = phase
    atomic_json(data / "updates/reinstall.json", journal)
    atomic_json(Path(journal["backup_dir"]) / "journal.json", journal)


def _blocking_gate(journal: dict) -> dict:
    """This gate denies writes only; it is never an authorization/claimed ticket."""
    return {
        "schema_version": 2,
        "state": "prepared",
        "issuer": "windows_installer",
        "operation": "install",
        "authorization_source": "preserved_reinstall",
        "upgrade_id": journal["upgrade_id"],
        "current_version": journal["uninstalled"]["version"],
        "target_version": journal["target_version"],
        "package_sha256": journal["package_sha256"],
        "db_path": journal["db_path"],
    }


def _validate_journal(install: Path, data: Path, journal: dict) -> None:
    identity = str(journal.get("upgrade_id", ""))
    if (
        journal.get("schema_version") != 1
        or journal.get("install_dir") != str(install)
        or not re.fullmatch(r"[0-9a-f]{32}", identity)
        or Path(journal.get("backup_dir", "")) != data / "updates" / f"reinstall-{identity}"
        or journal.get("phase")
        not in {
            "prepared",
            "migrating",
            "awaiting_health",
            "restoring",
            "rolled_back",
            "rollback_failed",
            "committing",
            "committed",
        }
    ):
        raise ReinstallError("重装恢复记录不属于当前程序和数据目录")
    for path in (Path(journal["backup_dir"]), Path(journal["db_path"])):
        regular_path(path)


def finish_committed_reinstall(install: Path, data: Path) -> bool:
    """Complete only metadata cleanup; a published pointer proves initialize passed health.

    initialize must publish installation.json only after target readiness succeeds.
    This also handles interruption between pointer publication and commit(). No DB
    or service operation occurs here, and unrelated later uninstall records are kept.
    """
    regular_path(install)
    regular_path(data)
    install, data = install.resolve(), data.resolve()
    path = data / "updates/reinstall.json"
    if not path.exists() or not (data / "installation.json").exists():
        return False
    journal = _read(path)
    _validate_journal(install, data, journal)
    if journal["phase"] not in {"awaiting_health", "committing", "committed"}:
        return False
    pointer = _read(data / "installation.json")
    if (
        pointer.get("version") != journal["target_version"]
        or pointer.get("manifest_sha256") != journal["package_sha256"]
    ):
        return False
    old = ((data / "uninstalled.json", "uninstalled"), (data / "updates/uninstall.json", "uninstall"))
    for active, key in old:
        if active.exists() and _read(active) != journal[key]:
            return False
    backup_dir = Path(journal["backup_dir"])
    if (
        journal.get("backup_ready") is not True
        or sha256(backup_dir / "database.sqlite") != journal.get("backup_sha256")
        or configuration_hashes(backup_dir / "config") != journal.get("config_hashes")
    ):
        raise ReinstallError("重装前数据库或配置备份校验失败，不能提交清理")
    _record(data, journal, "committing")
    for active, key in old:
        saved = backup_dir / f"{key}.json"
        if not saved.exists() or _read(saved) != journal[key]:
            raise ReinstallError("重装前卸载证据归档不完整，保留活动记录")
        active.unlink(missing_ok=True)
    _record(data, journal, "committed")
    gate_path = data / "maintenance.json"
    if gate_path.exists() and _read(gate_path) == _blocking_gate(journal):
        gate_path.unlink()
    return True


class PreservedReinstall:
    """No password generation, config merge, run completion or legacy runtime restart."""

    def __init__(
        self,
        install: Path,
        data: Path,
        platform,
        *,
        target_version: str,
        package_sha256: str,
        min_db_free_bytes: int = MIN_DB_FREE_BYTES,
    ):
        for path in (install, data):
            if not path.is_absolute():
                raise ReinstallError("重装程序和数据目录必须为绝对路径")
            regular_path(path)
        self.install, self.data, self.platform = install.resolve(), data.resolve(), platform
        self.target_version, self.package_sha256 = verify_version(target_version, target_version), package_sha256
        self.minimum = min_db_free_bytes
        if not re.fullmatch(r"[0-9a-f]{64}", package_sha256):
            raise ReinstallError("重装清单摘要无效")
        self.archive = _read(self.data / "uninstalled.json")
        self.uninstall = _read(self.data / "updates/uninstall.json")
        old = self.uninstall.get("previous")
        if (
            not isinstance(old, dict)
            or self.uninstall.get("schema_version") != 1
            or self.uninstall.get("phase") != "committed"
            or self.uninstall.get("authorized") is not True
            or self.archive.get("install_dir") != str(self.install)
            or self.uninstall.get("install_dir") != str(self.install)
            or not re.fullmatch(r"[0-9a-f]{32}", str(self.archive.get("uninstall_id", "")))
            or self.archive.get("uninstall_id") != self.uninstall.get("upgrade_id")
            or self.archive.get("version") != old.get("version")
            or any(self.archive.get(key) != value for key, value in old.items())
        ):
            raise ReinstallError("已有配置缺少本目录已完成卸载的对应证据，禁止接管")
        ordering = compare_versions(target_version, old["version"])
        if ordering < 0:
            raise ReinstallError("保留数据重装不允许降级")
        if ordering == 0 and old.get("manifest_sha256") != package_sha256:
            raise ReinstallError("同版重装无法证明与原构建相同，禁止复用版本号")
        self.path = self.data / "updates/reinstall.json"
        self.journal = _read(self.path) if self.path.exists() else None
        if self.journal is not None:
            _validate_journal(self.install, self.data, self.journal)
        restoring = self.journal is not None and self.journal["phase"] in {"restoring", "rollback_failed"}
        if restoring and self.journal.get("backup_ready"):
            # A power loss can leave config absent between directory replacements.
            # In that phase the verified backup is the configuration authority.
            backup = Path(self.journal["backup_dir"])
            if configuration_hashes(backup / "config") != self.journal["config_hashes"]:
                raise ReinstallError("重装配置备份摘要不一致")
            values = dotenv_values(backup / "config/service.env", interpolate=False, encoding="utf-8")
            self.database = Path(values.get("SMD_DB_PATH") or "")
            if not self.database.is_absolute() or self.database.resolve() != Path(self.journal["db_path"]):
                raise ReinstallError("重装恢复数据库与已核实配置不一致")
            regular_path(self.database)
        else:
            values, self.database = environment(self.data)
        try:
            configured_minimum = int(values.get("SMD_STORAGE_MIN_FREE_BYTES") or MIN_DB_FREE_BYTES)
        except (TypeError, ValueError) as error:
            raise ReinstallError("已保存的数据库空间安全余量无效") from error
        if type(min_db_free_bytes) is not int or min_db_free_bytes < 0 or configured_minimum < 0:
            raise ReinstallError("数据库空间安全余量必须为非负整数")
        # A lower bootstrap/default argument must not erase a higher field setting,
        # including when the only readable configuration is the recovery snapshot.
        self.minimum = max(min_db_free_bytes, configured_minimum)
        declared_database = self.uninstall.get("db_path")
        if declared_database is not None and Path(declared_database).resolve() != self.database:
            raise ReinstallError("卸载记录与当前实际数据库路径不一致")
        if (self.data / "config").exists() or not restoring:
            configuration_hashes(self.data / "config")
        if self.journal is not None:
            if self.journal.get("phase") == "committed":
                self.journal = None  # A later owned uninstall can start a new reinstall.
            elif (
                self.journal.get("target_version") != target_version
                or self.journal.get("package_sha256") != package_sha256
                or self.journal.get("db_path") != str(self.database)
                or self.journal.get("uninstalled") != self.archive
                or self.journal.get("uninstall") != self.uninstall
            ):
                raise ReinstallError("未完成重装必须使用同一版本、清单和原卸载资料恢复")
        gate_path = self.data / "maintenance.json"
        if gate_path.exists() and (self.journal is None or _read(gate_path) != _blocking_gate(self.journal)):
            raise ReinstallError("存在其他维护记录，保留数据重装不能接管或覆盖")

    def _ensure_gate(self) -> None:
        if self.journal is None:
            raise ReinstallError("缺少本轮重装记录，无法建立维护门禁")
        expected = _blocking_gate(self.journal)
        path = self.data / "maintenance.json"
        if path.exists() and _read(path) != expected:
            raise ReinstallError("重装维护门禁与本轮记录不同，禁止覆盖")
        if self.journal.get("maintenance_gate") != expected:
            self.journal["maintenance_gate"] = expected
            _record(self.data, self.journal, self.journal["phase"])
        if not path.exists():
            protected_json(path, expected)

    def _space(self):
        check_upgrade_space(self.install, self.data, self.database, min_db_free_bytes=self.minimum)

    def _stop(self, *, check_configuration=True):
        self.platform.stop()  # Caller has registered the new service; stop owns Backend guard.
        if not check_configuration:
            return
        _, actual = environment(self.data)
        if actual != self.database:
            raise ReinstallError("重装停止期间实际数据库发生变化")

    def before_migrate(self) -> bool:
        if self.journal is not None and self.journal["phase"] == "awaiting_health":
            # There is no remaining backup or migration allocation. The normal
            # health probe alone checks the actual application's storage minimum.
            self._ensure_gate()
            return False
        self._space()
        restoring = self.journal is not None and self.journal["phase"] in {"restoring", "rollback_failed"}
        self._stop(check_configuration=not restoring)
        self._space()
        if self.journal is not None:
            phase = self.journal["phase"]
            if phase in {"migrating", "restoring", "rollback_failed"}:
                self.failed(ReinstallError("上次重装中断，恢复原始数据后继续相同版本"))
            if phase in {"committing", "committed"}:
                raise ReinstallError("重装已提交，不能重新迁移原始快照")
        else:
            identity = uuid.uuid4().hex
            backup = self.data / "updates" / f"reinstall-{identity}"
            backup.mkdir(parents=True, exist_ok=False)
            self.journal = {
                "schema_version": 1,
                "upgrade_id": identity,
                "install_dir": str(self.install),
                "target_version": self.target_version,
                "package_sha256": self.package_sha256,
                "db_path": str(self.database),
                "backup_dir": str(backup),
                "backup_ready": False,
                "uninstalled": self.archive,
                "uninstall": self.uninstall,
            }
            atomic_json(backup / "uninstalled.json", self.archive)
            atomic_json(backup / "uninstall.json", self.uninstall)
            _record(self.data, self.journal, "prepared")
        journal, backup = self.journal, Path(self.journal["backup_dir"])
        self._ensure_gate()
        if not journal["backup_ready"]:
            # Partial backup files never become restoration evidence. Keep their
            # bytes and write a new snapshot directory for the same transaction.
            partial = backup / "database.sqlite"
            if partial.exists() or (backup / "config").exists():
                retained = backup / f"partial-{uuid.uuid4().hex}"
                retained.mkdir()
                for child in (partial, backup / "config"):
                    if child.exists():
                        child.replace(retained / child.name)
            backup_sqlite(self.database, partial)
            shutil.copytree(self.data / "config", backup / "config")
            journal["backup_sha256"] = sha256(partial)
            journal["config_hashes"] = configuration_hashes(backup / "config")
            journal["backup_ready"] = True
        _record(self.data, journal, "migrating")
        return True

    def mark_migrated(self) -> None:
        if self.journal is None or self.journal["phase"] != "migrating" or not self.journal["backup_ready"]:
            raise ReinstallError("重装迁移阶段不匹配")
        _record(self.data, self.journal, "awaiting_health")

    def failed(self, error: BaseException) -> None:
        if self.journal is None:
            return
        if self.journal["phase"] in {"committing", "committed"}:
            raise ReinstallError("已提交重装不能恢复旧数据库")
        pointer_path = self.data / "installation.json"
        if pointer_path.exists():
            pointer = _read(pointer_path)
            if pointer.get("version") == self.target_version and pointer.get("manifest_sha256") == self.package_sha256:
                raise ReinstallError("新版本已通过健康检查并发布；仅重装记录收尾待完成，禁止恢复旧数据库")
            raise ReinstallError("存在不匹配的安装状态，禁止恢复旧数据库")
        self.journal["last_error"] = str(error)
        try:
            self._stop(check_configuration=False)
            self._ensure_gate()
            _record(self.data, self.journal, "restoring")
            restore_snapshot(self.data, self.journal)
            _record(self.data, self.journal, "rolled_back")
        except (OSError, RuntimeError, ValueError) as restore_error:
            self.journal["rollback_error"] = str(restore_error)
            _record(self.data, self.journal, "rollback_failed")
            raise ReinstallError(
                "重装失败且数据恢复未完成；新服务保持停止，旧备份和卸载记录均已保留"
            ) from restore_error

    def commit(self) -> None:
        if self.journal is None or self.journal["phase"] != "awaiting_health":
            raise ReinstallError("重装尚未完成迁移和新版本健康检查")
        if not finish_committed_reinstall(self.install, self.data):
            raise ReinstallError("新版本安装状态尚未发布，不能清理原卸载证据")
        self.journal = _read(self.path)
