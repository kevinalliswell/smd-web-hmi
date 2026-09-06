"""单机生产部署的 SQLite 在线备份与临时导出清理。"""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, closing
from datetime import datetime, timezone
from pathlib import Path

from app.core.logging import get_logger
from app.services.sqlite_backup import backup_sqlite

logger = get_logger("service.maintenance")


class MaintenanceBlockedError(RuntimeError):
    """维护期间或设备状态不确定时拒绝新的设备变更。"""


class MaintenanceManager:
    """执行有校验的在线备份，并按保留期清理临时产物。"""

    def __init__(self) -> None:
        self._operation_lock = asyncio.Lock()
        self._priority_lock = asyncio.Lock()
        self._upgrade_path: Path | None = None
        self._task: asyncio.Task[None] | None = None
        self._backup_lock = asyncio.Lock()
        self._last_backup_path: str | None = None
        self._last_backup_at: str | None = None
        self._last_error: str | None = None
        self._last_cleanup_count = 0

    def configure_upgrade(self, state_path: Path) -> None:
        """启动时设置受服务账户/管理员 ACL 保护的维护票据位置。"""
        self._upgrade_path = state_path.resolve()

    def upgrade_state(self) -> dict:
        if self._upgrade_path is None or not self._upgrade_path.exists():
            return {"state": "idle"}
        try:
            state = json.loads(self._upgrade_path.read_text(encoding="utf-8"))
            if state.get("state") not in {"prepared", "claimed"}:
                raise ValueError("unknown maintenance state")
            return state
        except (ValueError, OSError, AttributeError) as exc:
            raise MaintenanceBlockedError("维护票据损坏，需通过本机升级恢复流程处理") from exc

    def _write_upgrade(self, state: dict) -> None:
        if self._upgrade_path is None:
            raise MaintenanceBlockedError("维护票据目录未配置")
        self._upgrade_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._upgrade_path.with_suffix(".tmp")
        try:
            with temporary.open("w", encoding="utf-8") as output:
                os.chmod(temporary, 0o600)
                json.dump(state, output, ensure_ascii=False)
                output.flush()
                os.fsync(output.fileno())
            temporary.replace(self._upgrade_path)
        finally:
            temporary.unlink(missing_ok=True)

    @asynccontextmanager
    async def command_guard(self, *, priority: bool = False) -> AsyncIterator[None]:
        """设备写操作共用此锁，准备升级不能与在途命令交错。"""
        async with self._priority_lock if priority else self._operation_lock:
            if self.upgrade_state()["state"] != "idle":
                raise MaintenanceBlockedError("设备处于离线升级维护状态，禁止新命令")
            yield

    async def prepare_upgrade(
        self,
        *,
        target_version: str,
        current_version: str,
        db_path: Path,
        operator_id: str,
        validate: Callable[[], Awaitable[None]],
    ) -> dict:
        """管理员准备升级；validate 必须检查新鲜板端待机及未闭合会话。"""
        if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?", target_version):
            raise ValueError("非法目标版本")
        async with self._operation_lock, self._priority_lock:
            if self.upgrade_state()["state"] != "idle":
                raise MaintenanceBlockedError("已有维护操作，请先取消或恢复")
            await validate()
            state = {
                "state": "prepared",
                "upgrade_id": uuid.uuid4().hex,
                "token": secrets.token_hex(32),
                "created_at": time.time(),
                "target_version": target_version,
                "current_version": current_version,
                "db_path": str(db_path.resolve()),
                "operator_id": operator_id,
            }
            self._write_upgrade(state)
            return state

    async def claim_upgrade(self, token: str, *, validate: Callable[[], Awaitable[None]]) -> dict:
        """本机提权升级器领取票据前再核对实时状态；票据只使用一次。"""
        async with self._operation_lock:
            state = self.upgrade_state()
            if state["state"] != "prepared" or not hmac.compare_digest(state.get("token", ""), token):
                raise MaintenanceBlockedError("维护票据无效或已领取")
            if time.time() - state["created_at"] > 600:
                raise MaintenanceBlockedError("维护票据超过十分钟，请取消并重新准备")
            await validate()
            state = {**state, "state": "claimed", "claimed_at": time.time()}
            self._write_upgrade(state)
            return state

    async def cancel_upgrade(self) -> None:
        async with self._operation_lock:
            if self.upgrade_state()["state"] == "claimed":
                raise MaintenanceBlockedError("升级器已领取票据，只能执行本机恢复")
            if self._upgrade_path is not None:
                self._upgrade_path.unlink(missing_ok=True)

    async def create_backup(self, source: Path, backup_dir: Path) -> Path:
        async with self._backup_lock:
            source = source.resolve()
            backup_dir = backup_dir.resolve()
            backup_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            target = backup_dir / f"smd-{stamp}-{uuid.uuid4().hex[:8]}.db"
            try:
                await asyncio.to_thread(backup_sqlite, source, target)
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
            with closing(sqlite3.connect(backup.resolve())) as connection:
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
        latest = max(
            backup_dir.glob("smd-*.db"),
            key=lambda path: path.stat().st_mtime,
            default=None,
        )
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
