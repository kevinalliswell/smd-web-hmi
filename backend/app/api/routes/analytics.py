"""数据分析路由 /api/analytics：多试验关键指标对比（规格 7.3 AnalyticsPage）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select

from app.api.deps import DbDep, get_current_user
from app.api.schemas import ok
from app.db.models import SamplePoint, TestSession
from app.services import report_service

router = APIRouter(prefix="/api/analytics", tags=["analytics"], dependencies=[Depends(get_current_user)])


@router.get("/compare")
async def compare(db: DbDep, test_ids: str = "", original_height_mm: float | None = None):
    """对比多个试验的关键指标。test_ids 为逗号分隔列表。权限：Observer+。"""
    ids = [t.strip() for t in test_ids.split(",") if t.strip()]
    out = []
    for tid in ids[:8]:  # 限制对比数量，避免一次拉取过多
        test = await db.scalar(select(TestSession).where(TestSession.test_id == tid))
        if test is None:
            continue
        sample_count = await db.scalar(select(func.count()).select_from(SamplePoint).where(SamplePoint.test_id == tid))
        height = test.original_height_mm if test.original_height_mm is not None else original_height_mm
        metrics = await report_service.compute_metrics_from_database(db, tid, height)
        out.append(
            {
                "test_id": tid,
                "operator_id": test.operator_id,
                "start_time": test.start_time,
                "end_time": test.end_time,
                "sample_count": int(sample_count or 0),
                "metrics": metrics,
            }
        )
    return ok(out)
