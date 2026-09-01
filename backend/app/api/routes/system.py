"""系统路由 /api/system（规格 3.10）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app import __version__
from app.api.deps import CurrentUser, DbDep, get_command_service, get_hostcomm_client, require_role
from app.api.schemas import err, ok
from app.core.config import get_settings
from app.services.command_service import CommandError

router = APIRouter(prefix="/api/system", tags=["system"])

# HostComm 调试工具：仅允许只读操作（安全红线——不得提供任何控制/强制/绕过能力）
DEBUG_READONLY_ACTIONS = {"get_status", "get_parameters"}


class HostCommDebugRequest(BaseModel):
    action: str


@router.get("/health")
async def health():
    """服务健康检查。权限：无（公开）。"""
    return ok({"status": "ok", "version": __version__})


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
        }
    )


@router.post("/sync-time")
async def sync_time(request: Request, db: DbDep, user: CurrentUser = Depends(require_role("admin"))):
    """通过 HostComm 下发 sync_time 校时命令。权限：Admin。

    走 CommandService 而非直接 send_command：校时会改变控制板时钟，也就是此后
    所有设备侧日志的时间基准，必须留下 operator_action 审计并记录真实操作员身份
    （原实现硬编码 operator_id="system"，事后无从追溯是谁校的时）。
    """
    from app.hostcomm.protocol import now_iso

    service = get_command_service(request)
    ts = now_iso()
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
