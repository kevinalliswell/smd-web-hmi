"""数据分析路由 /api/analytics：多试验关键指标对比（规格 7.3 AnalyticsPage）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select

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
        samples = list(
            (await db.execute(select(SamplePoint).where(SamplePoint.test_id == tid).order_by(SamplePoint.ts))).scalars()
        )
        metrics = report_service.compute_metrics(samples, original_height_mm)
        out.append(
            {
                "test_id": tid,
                "operator_id": test.operator_id,
                "start_time": test.start_time,
                "end_time": test.end_time,
                "sample_count": len(samples),
                "metrics": metrics,
            }
        )
    return ok(out)
