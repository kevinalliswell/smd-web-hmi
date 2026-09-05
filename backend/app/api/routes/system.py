"""系统路由 /api/system（规格 3.10）。"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app import __version__
from app.api.deps import DbDep, UserDep, get_command_service, get_hostcomm_client, require_role
from app.api.schemas import err, ok
from app.core.config import get_settings
from app.db.database import get_schema_status
from app.hostcomm.protocol import now_iso
from app.services.command_service import CommandError
from app.services.maintenance_service import maintenance_manager

router = APIRouter(prefix="/api/system", tags=["system"])

# HostComm 调试工具：仅允许只读操作（安全红线——不得提供任何控制/强制/绕过能力）
DEBUG_READONLY_ACTIONS = {"get_status", "get_parameters"}


class HostCommDebugRequest(BaseModel):
    action: str


@router.get("/health")
async def health(request: Request):
    """生产就绪状态：数据库、schema、存储、备份及设备链路。权限：无。"""
    settings = get_settings()
    schema = await get_schema_status()
    try:
        storage = shutil.disk_usage(settings.data_dir)
        storage_writable = os.access(settings.data_dir, os.W_OK)
        storage_free_bytes = storage.free
    except OSError:
        storage_writable = False
        storage_free_bytes = 0
    backup = maintenance_manager.snapshot()
    schema_ok = bool(schema["ok"]) or settings.hostcomm_mock
    backup_ok = settings.hostcomm_mock or (backup["last_backup_at"] is not None and backup["last_error"] is None)
    storage_ok = storage_writable and storage_free_bytes >= settings.smd_storage_min_free_bytes
    core_ready = schema_ok and storage_ok and backup_ok
    client = get_hostcomm_client(request)
    return ok(
        {
            "status": "ready" if core_ready else "not_ready",
            "version": __version__,
            "checks": {
                "database": "ok" if schema["current"] is not None or settings.hostcomm_mock else "error",
                "schema": "ok" if schema_ok else "outdated",
                "storage": "ok" if storage_ok else ("low" if storage_writable else "error"),
                "storage_free_bytes": storage_free_bytes,
                "backup": "ok" if backup_ok else "error",
                "hostcomm": getattr(client, "comm_quality", "offline") if client else "offline",
            },
        }
    )


@router.get("/info", dependencies=[Depends(require_role("admin"))])
async def info(request: Request):
    """后端版本、HostComm 连接状态等。权限：Admin。"""
    client = get_hostcomm_client(request)
    settings = get_settings()
    return ok(
        {
            "version": __version__,
            "hostcomm": {
                "mock": settings.hostcomm_mock,
                "comm_quality": getattr(client, "comm_quality", "offline") if client else "offline",
            },
            "db_path": str(settings.db_path_resolved),
            "backup": {
                "last_backup_at": maintenance_manager.snapshot()["last_backup_at"],
                "last_error": maintenance_manager.snapshot()["last_error"],
            },
        }
    )


@router.post("/backup", dependencies=[Depends(require_role("admin"))])
async def create_backup():
    """立即执行一次 SQLite 在线备份并校验完整性。权限：Admin。"""
    settings = get_settings()
    try:
        path = await maintenance_manager.create_backup(settings.db_path_resolved, settings.backups_dir)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=err("backup_failed", "数据库备份失败")) from exc
    return ok({"file": Path(path).name, "completed_at": maintenance_manager.snapshot()["last_backup_at"]})


@router.post("/sync-time", dependencies=[Depends(require_role("admin"))])
async def sync_time(request: Request, user: UserDep, db: DbDep):
    """通过 HostComm 下发 sync_time 校时命令。权限：Admin。"""
    ts = now_iso()
    service = get_command_service(request)
    try:
        result = await service.execute(
            "sync_time",
            {"timestamp": ts},
            operator_id=user.username,
            role=user.role,
            client_ip=request.client.host if request.client else None,
            db_session=db,
        )
    except CommandError as exc:
        raise HTTPException(status_code=exc.status_code, detail=err(exc.error_code, exc.message))
    return ok({"sent_time": ts, "result": result.get("result"), "reason_code": result.get("reason_code")})


@router.get("/hostcomm/status", dependencies=[Depends(require_role("maintainer"))])
async def hostcomm_status(request: Request):
    """HostComm 连接详情、帧统计、心跳延迟。权限：Maintainer。"""
    client = get_hostcomm_client(request)
    if client is None:
        return ok({"connected": False})
    return ok(client.stats)


@router.post("/hostcomm/debug", dependencies=[Depends(require_role("maintainer"))])
async def hostcomm_debug(body: HostCommDebugRequest, request: Request):
    """HostComm 只读调试：发送 get_status / get_parameters 并回显。权限：Maintainer。

    安全：动作白名单仅含只读请求；不接受 command / set_parameters 等任何有副作用操作。
    """
    if body.action not in DEBUG_READONLY_ACTIONS:
        raise HTTPException(
            status_code=400,
            detail=err("action_not_allowed", f"调试工具仅允许只读操作: {sorted(DEBUG_READONLY_ACTIONS)}"),
        )
    client = get_hostcomm_client(request)
    if client is None or not getattr(client, "is_online", False):
        raise HTTPException(status_code=503, detail=err("device_comm_fault", "HostComm 未连接"))
    payload = await (client.get_status() if body.action == "get_status" else client.get_parameters())
    return ok({"action": body.action, "response": payload})
