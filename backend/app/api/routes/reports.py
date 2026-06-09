"""报告路由 /api/reports（规格 3.8）。D3 骨架。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.api.deps import DbDep, get_current_user, require_role
from app.api.schemas import ok
from app.db.models import ReportExport

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("", dependencies=[Depends(get_current_user)])
async def list_reports(db: DbDep):
    """报告列表。权限：Observer+。"""
    result = await db.execute(select(ReportExport).order_by(ReportExport.id.desc()).limit(100))
    rows = result.scalars().all()
    return ok([{"id": r.id, "test_id": r.test_id, "generated_at": r.generated_at, "format": r.format} for r in rows])


@router.post("/generate", dependencies=[Depends(require_role("operator"))])
async def generate_report():
    """生成报告（异步）。权限：Operator+。D3 骨架占位。"""
    return ok({"task_id": None, "status": "not_implemented"})
