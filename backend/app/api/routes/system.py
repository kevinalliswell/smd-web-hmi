"""系统路由 /api/system（规格 3.10）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app import __version__
from app.api.deps import get_hostcomm_client, require_role
from app.api.schemas import ok
from app.core.config import get_settings

router = APIRouter(prefix="/api/system", tags=["system"])


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


@router.get("/hostcomm/status", dependencies=[Depends(require_role("maintainer"))])
async def hostcomm_status(request: Request):
    """HostComm 连接详情、帧统计。权限：Maintainer。"""
    client = get_hostcomm_client(request)
    if client is None:
        return ok({"connected": False})
    return ok(client.stats)
