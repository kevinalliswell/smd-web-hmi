"""参数路由 /api/parameters（规格 3.6）。D3 骨架。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.api.deps import get_current_user, get_hostcomm_client, require_role
from app.api.schemas import ok

router = APIRouter(prefix="/api/parameters", tags=["parameters"])


@router.get("", dependencies=[Depends(get_current_user)])
async def get_parameters(request: Request):
    """读取当前参数快照。权限：Observer+。"""
    client = get_hostcomm_client(request)
    if client is None or not getattr(client, "is_online", False):
        return ok({"params": None, "comm": "offline"})
    payload = await client.get_parameters()
    return ok(payload)


@router.put("", dependencies=[Depends(require_role("admin"))])
async def put_parameters(request: Request):
    """下发参数（校验 CRC + 回读确认）。权限：Admin。D3 骨架占位。"""
    # 完整实现见 command_service.set_parameters 流程；此处保留接口契约。
    return ok({"status": "not_implemented", "detail": "参数下发将在 D3 后续迭代接入命令服务"})
