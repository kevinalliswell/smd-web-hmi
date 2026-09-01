"""单机生产部署的 SQLite 在线备份与临时导出清理。"""

from __future__ import annotations

import asyncio
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.core.logging import get_logger

logger = get_logger("service.maintenance")


class MaintenanceManager:
    """执行有校验的在线备份，并按保留期清理临时产物。"""

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._backup_lock = asyncio.Lock()
        self._last_backup_path: str | None = None
        self._last_backup_at: str | None = None
        self._last_error: str | None = None
        self._last_cleanup_count = 0

    async def create_backup(self, source: Path, backup_dir: Path) -> Path:
        async with self._backup_lock:
            source = source.resolve()
            backup_dir = backup_dir.resolve()
            backup_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            target = backup_dir / f"smd-{stamp}-{uuid.uuid4().hex[:8]}.db"
            temporary = target.with_suffix(".tmp")

            def copy_and_verify() -> None:
                if not source.is_file():
                    raise FileNotFoundError("数据库文件不存在")
                try:
                    with sqlite3.connect(source) as source_db, sqlite3.connect(temporary) as backup_db:
                        source_db.backup(backup_db)
                        self._verify_connection(backup_db)
                    temporary.replace(target)
                finally:
                    temporary.unlink(missing_ok=True)

            try:
                await asyncio.to_thread(copy_and_verify)
            except Exception as exc:
                self._last_error = type(exc).__name__
                raise
            self._remember_backup(target)
            logger.info("maintenance.backup_completed", file=target.name)
            return target

    @staticmethod
    def _verify_connection(connection: sqlite3.Connection) -> None:
        result = connection.execute("PRAGMA integrity_check").fetchone()
        if result is None or result[0] != "ok":
            raise RuntimeError("备份完整性检查失败")

    async def _restore_latest_backup_state(self, backup: Path) -> bool:
        def verify() -> None:
            with sqlite3.connect(backup.resolve()) as connection:
                self._verify_connection(connection)

        try:
            await asyncio.to_thread(verify)
        except Exception as exc:  # noqa: BLE001
            self._last_error = type(exc).__name__
            logger.warning("maintenance.existing_backup_invalid", file=backup.name, error=str(exc))
            return False
        self._remember_backup(backup, completed_at=backup.stat().st_mtime)
        return True

    def _remember_backup(self, backup: Path, *, completed_at: float | None = None) -> None:
        timestamp = completed_at if completed_at is not None else time.time()
        self._last_backup_path = str(backup)
        self._last_backup_at = datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec="seconds")
        self._last_error = None

    async def cleanup_files(self, directory: Path, *, retention_days: int, pattern: str = "*") -> int:
        directory.mkdir(parents=True, exist_ok=True)
        cutoff = time.time() - retention_days * 86400

        def cleanup() -> int:
            removed = 0
            for path in directory.glob(pattern):
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            return removed

        removed = await asyncio.to_thread(cleanup)
        self._last_cleanup_count = removed
        if removed:
            logger.info("maintenance.files_removed", directory=directory.name, count=removed)
        return removed

    async def run_once(self, settings, *, force_backup: bool = False) -> None:
        backup_dir = settings.backups_dir
        latest = max(backup_dir.glob("smd-*.db"), key=lambda path: path.stat().st_mtime, default=None)
        due = latest is None or time.time() - latest.stat().st_mtime >= settings.smd_backup_interval_hours * 3600
        if latest is not None and self._last_backup_at is None and not await self._restore_latest_backup_state(latest):
            due = True
        if force_backup or due:
            await self.create_backup(settings.db_path_resolved, backup_dir)
        await self.cleanup_files(
            backup_dir,
            retention_days=settings.smd_backup_retention_days,
            pattern="smd-*.db",
        )
        await self.cleanup_files(settings.exports_dir, retention_days=settings.smd_export_retention_days)

    async def start(self, settings) -> None:
        if self._task is not None and not self._task.done():
            return
        await self.run_once(settings)

        async def loop() -> None:
            while True:
                await asyncio.sleep(settings.smd_maintenance_interval_seconds)
                try:
                    await self.run_once(settings)
                except Exception as exc:  # noqa: BLE001
                    self._last_error = type(exc).__name__
                    logger.exception("maintenance.run_failed", error=str(exc))

        self._task = asyncio.create_task(loop(), name="maintenance-loop")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        self._task = None

    def snapshot(self) -> dict[str, str | int | None]:
        return {
            "last_backup_path": self._last_backup_path,
            "last_backup_at": self._last_backup_at,
            "last_error": self._last_error,
            "last_cleanup_count": self._last_cleanup_count,
        }


maintenance_manager = MaintenanceManager()
