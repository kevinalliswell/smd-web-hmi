"""实时状态路由 /api/status（规格 3.2）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.api.deps import DbDep, get_current_user, get_hostcomm_client
from app.api.schemas import ok
from app.services.cache import status_cache
from app.services.sampling_health import sampling_health
from app.services.state_policy import enrich_status_snapshot
from app.services.trend_service import query_downsampled_points

router = APIRouter(prefix="/api", tags=["status"])


@router.get("/status", dependencies=[Depends(get_current_user)])
async def get_status(request: Request):
    """返回内存缓存中最新 status_snapshot，含 comm_quality。权限：Observer+。"""
    client = get_hostcomm_client(request)
    link_online = bool(getattr(client, "is_online", False)) if client else False
    snapshot = await status_cache.get_snapshot()
    quality = status_cache.comm_quality(link_online)

    payload = enrich_status_snapshot(snapshot)
    payload["comm_quality"] = quality
    payload["last_update"] = status_cache.last_update
    payload["data_persistence"] = sampling_health.snapshot()
    return ok(payload)


@router.get("/trends", dependencies=[Depends(get_current_user)])
async def get_trends(
    db: DbDep,
    from_ts: str | None = None,
    to_ts: str | None = None,
    test_id: str | None = None,
    max_points: int = 2000,
):
    """跨试验的历史趋势查询（按时间窗 + 等距降采样）。权限：Observer+。

    时间参数为 ISO 8601 字符串（同一部署时区下字符串可比）。
    """
    result = await query_downsampled_points(
        db,
        from_ts=from_ts,
        to_ts=to_ts,
        test_id=test_id,
        max_points=max_points,
    )
    return ok(result)
