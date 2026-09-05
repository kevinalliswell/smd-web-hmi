"""实时状态路由 /api/status（规格 3.2）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.deps import DbDep, get_current_user, get_hostcomm_client
from app.api.schemas import err, ok
from app.api.validation import MaxPoints, TestId
from app.core.time import normalize_utc_iso
from app.services.cache import status_cache
from app.services.maintenance_service import maintenance_manager
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

    control_ready = quality == "online" and maintenance_manager.upgrade_state()["state"] == "idle"
    payload = enrich_status_snapshot(snapshot, control_ready=control_ready)
    payload["comm_quality"] = quality
    payload["data_fresh"] = status_cache.is_fresh
    payload["control_ready"] = control_ready
    payload["last_update"] = status_cache.last_update
    payload["data_persistence"] = sampling_health.snapshot()
    return ok(payload)


@router.get("/trends", dependencies=[Depends(get_current_user)])
async def get_trends(
    db: DbDep,
    from_ts: str | None = None,
    to_ts: str | None = None,
    test_id: TestId | None = None,
    max_points: MaxPoints = 2000,
):
    """跨试验的历史趋势查询（按时间窗 + 等距降采样）。权限：Observer+。

    时间参数为 ISO 8601 字符串，进入查询前统一换算为 UTC。
    """
    try:
        normalized_from = normalize_utc_iso(from_ts) if from_ts else None
        normalized_to = normalize_utc_iso(to_ts) if to_ts else None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=err("invalid_timestamp", str(exc))) from exc
    result = await query_downsampled_points(
        db,
        from_ts=normalized_from,
        to_ts=normalized_to,
        test_id=test_id,
        max_points=max_points,
    )
    return ok(result)
