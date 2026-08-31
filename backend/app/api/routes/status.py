"""实时状态路由 /api/status（规格 3.2）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select

from app.api.deps import DbDep, get_current_user, get_hostcomm_client
from app.api.schemas import err, ok
from app.api.validation import MaxPoints, TestId
from app.core.time import normalize_utc_iso
from app.db.models import SamplePoint
from app.services.cache import status_cache
from app.services.sampling_health import sampling_health
from app.services.state_policy import enrich_status_snapshot

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
    test_id: TestId | None = None,
    max_points: MaxPoints = 2000,
):
    """跨试验的历史趋势查询（按时间窗 + 等距降采样）。权限：Observer+。

    时间参数为 ISO 8601 字符串，进入查询前统一换算为 UTC。
    """
    stmt = select(SamplePoint)
    try:
        if from_ts:
            stmt = stmt.where(SamplePoint.ts >= normalize_utc_iso(from_ts))
        if to_ts:
            stmt = stmt.where(SamplePoint.ts <= normalize_utc_iso(to_ts))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=err("invalid_timestamp", str(exc))) from exc
    if test_id:
        stmt = stmt.where(SamplePoint.test_id == test_id)
    stmt = stmt.order_by(SamplePoint.ts)

    rows = (await db.execute(stmt)).scalars().all()
    total = len(rows)
    stride = max(1, (total + max_points - 1) // max_points)
    sampled = rows[::stride]
    return ok(
        {
            "total": total,
            "stride": stride,
            "points": [
                {
                    "ts": s.ts,
                    "test_id": s.test_id,
                    "furnace_pv": s.furnace_pv,
                    "burden_temp": s.burden_temp,
                    "delta_p": s.delta_p,
                    "displacement": s.displacement,
                    "drip_weight": s.drip_weight,
                    "n2_pv": s.n2_pv,
                    "co_pv": s.co_pv,
                    "current_state": s.current_state,
                }
                for s in sampled
            ],
        }
    )
