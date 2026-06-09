"""试验管理路由 /api/tests（规格 3.4）。D3 骨架：基础列表/详情。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.api.deps import DbDep, get_current_user
from app.api.schemas import ok
from app.db.models import TestSession

router = APIRouter(prefix="/api/tests", tags=["tests"], dependencies=[Depends(get_current_user)])


@router.get("")
async def list_tests(db: DbDep, page: int = 1, size: int = 20):
    """历史试验列表（分页）。权限：Observer+。"""
    offset = max(0, (page - 1) * size)
    result = await db.execute(
        select(TestSession).order_by(TestSession.id.desc()).limit(size).offset(offset)
    )
    rows = result.scalars().all()
    return ok([{"test_id": r.test_id, "operator_id": r.operator_id, "start_time": r.start_time, "end_time": r.end_time} for r in rows])


@router.get("/current")
async def current_test(db: DbDep):
    """当前进行中的试验（end_time 为空）。权限：Observer+。"""
    result = await db.execute(
        select(TestSession).where(TestSession.end_time.is_(None)).order_by(TestSession.id.desc())
    )
    r = result.scalars().first()
    if r is None:
        return ok(None)
    return ok({"test_id": r.test_id, "operator_id": r.operator_id, "start_time": r.start_time})


@router.get("/{test_id}")
async def test_detail(test_id: str, db: DbDep):
    """试验详情。权限：Observer+。"""
    result = await db.execute(select(TestSession).where(TestSession.test_id == test_id))
    r = result.scalar_one_or_none()
    if r is None:
        return ok(None)
    return ok(
        {
            "test_id": r.test_id,
            "operator_id": r.operator_id,
            "start_time": r.start_time,
            "end_time": r.end_time,
            "end_reason": r.end_reason,
            "sample_label": r.sample_label,
            "notes": r.notes,
        }
    )
