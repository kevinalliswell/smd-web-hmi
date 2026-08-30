"""数据库层等距降采样查询。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import SamplePoint

_POINT_COLUMNS = (
    "ts",
    "test_id",
    "furnace_pv",
    "burden_temp",
    "delta_p",
    "displacement",
    "drip_weight",
    "n2_pv",
    "co_pv",
    "current_state",
)


async def query_downsampled_points(
    session: AsyncSession,
    *,
    max_points: int,
    test_id: str | None = None,
    from_ts: str | None = None,
    to_ts: str | None = None,
) -> dict[str, Any]:
    """用 ROW_NUMBER 在 SQLite 中等距抽样，最多把 ``max_points`` 行送入 Python。"""
    if max_points < 1:
        raise ValueError("max_points must be positive")

    filters = []
    if from_ts:
        filters.append(SamplePoint.ts >= from_ts)
    if to_ts:
        filters.append(SamplePoint.ts <= to_ts)
    if test_id:
        filters.append(SamplePoint.test_id == test_id)

    total = int((await session.scalar(select(func.count()).select_from(SamplePoint).where(*filters))) or 0)
    stride = max(1, (total + max_points - 1) // max_points)
    if total == 0:
        return {"total": 0, "stride": stride, "points": []}

    ranked = (
        select(
            *(getattr(SamplePoint, name).label(name) for name in _POINT_COLUMNS),
            func.row_number().over(order_by=(SamplePoint.ts, SamplePoint.id)).label("row_number"),
        )
        .where(*filters)
        .subquery()
    )
    statement = (
        select(*(ranked.c[name] for name in _POINT_COLUMNS))
        .where((ranked.c.row_number - 1) % stride == 0)
        .order_by(ranked.c.row_number)
        .limit(max_points)
    )
    rows = (await session.execute(statement)).mappings().all()
    return {
        "total": total,
        "stride": stride,
        "points": [{name: row[name] for name in _POINT_COLUMNS} for row in rows],
    }
