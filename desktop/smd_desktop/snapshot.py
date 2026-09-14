"""Shared DB/config restore, called only after service exit under the Backend guard."""

import shutil
from pathlib import Path

from app.services.sqlite_backup import backup_sqlite

from .bundle import sha256


class SnapshotError(RuntimeError):
    pass


def configuration_hashes(root: Path) -> dict[str, str]:
    result = {}
    if root.is_symlink() or root.is_junction():
        raise SnapshotError("配置备份包含重解析路径")
    if not root.is_dir():
        raise SnapshotError("配置备份目录不存在")
    for path in root.rglob("*"):
        if path.is_symlink() or path.is_junction():
            raise SnapshotError("配置备份包含重解析路径")
        if path.is_file():
            result[path.relative_to(root).as_posix()] = sha256(path)
    return result


def restore_snapshot(data: Path, journal: dict) -> None:
    """Restore verified actual DB and config; never start or reconfigure a service."""
    if not journal["backup_ready"]:
        return
    backup_dir = Path(journal["backup_dir"])
    backup = backup_dir / "database.sqlite"
    if backup.is_symlink() or backup.is_junction():
        raise SnapshotError("数据库备份包含重解析路径")
    if sha256(backup) != journal["backup_sha256"]:
        raise SnapshotError("备份摘要不一致，禁止恢复")
    hashes = configuration_hashes(backup_dir / "config")
    if "config_hashes" in journal and hashes != journal["config_hashes"]:
        raise SnapshotError("配置备份摘要不一致，禁止恢复")
    source = Path(journal["db_path"])
    restored = source.with_name(f".{source.name}.{journal['upgrade_id']}.restore.sqlite")
    restored.unlink(missing_ok=True)
    backup_sqlite(backup, restored)
    for suffix in ("-wal", "-shm"):
        Path(str(source) + suffix).unlink(missing_ok=True)
    restored.replace(source)
    config = data / "config"
    staging = data / f".config-restore-{journal['upgrade_id']}"
    if staging.exists():
        shutil.rmtree(staging)
    shutil.copytree(backup_dir / "config", staging)
    if configuration_hashes(staging) != hashes:
        raise SnapshotError("配置恢复副本校验失败")
    if config.exists():
        shutil.rmtree(config)
    staging.replace(config)
