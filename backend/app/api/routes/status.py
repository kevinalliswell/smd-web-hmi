"""实时状态路由 /api/status（规格 3.2）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.api.deps import get_current_user, get_hostcomm_client
from app.api.schemas import ok
from app.services.cache import status_cache

router = APIRouter(prefix="/api", tags=["status"])


@router.get("/status", dependencies=[Depends(get_current_user)])
async def get_status(request: Request):
    """返回内存缓存中最新 status_snapshot，含 comm_quality。权限：Observer+。"""
    client = get_hostcomm_client(request)
    link_online = bool(getattr(client, "is_online", False)) if client else False
    snapshot = await status_cache.get_snapshot()
    quality = status_cache.comm_quality(link_online)

    payload = dict(snapshot)
    payload["comm_quality"] = quality
    payload["last_update"] = status_cache.last_update
    return ok(payload)
