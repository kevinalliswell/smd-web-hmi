"""试验管理路由 /api/tests（规格 3.4）。D3 骨架：基础列表/详情。"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select

from app.api.deps import DbDep, get_current_user
from app.api.schemas import err, ok
from app.db.models import AlarmLog, EventLog, SamplePoint, TestSession
from app.hostcomm.protocol import now_iso
from app.services.trend_service import query_downsampled_points

router = APIRouter(prefix="/api/tests", tags=["tests"], dependencies=[Depends(get_current_user)])


@router.get("")
async def list_tests(db: DbDep, page: int = 1, size: int = 20):
    """历史试验列表（分页）。权限：Observer+。"""
    offset = max(0, (page - 1) * size)
    result = await db.execute(select(TestSession).order_by(TestSession.id.desc()).limit(size).offset(offset))
    rows = result.scalars().all()
    return ok(
        [
            {
                "test_id": r.test_id,
                "operator_id": r.operator_id,
                "start_time": r.start_time,
                "end_time": r.end_time,
                "end_reason": r.end_reason,
                "report_path": r.report_path,
            }
            for r in rows
        ]
    )


@router.get("/current")
async def current_test(db: DbDep):
    """当前进行中的试验（end_time 为空）。权限：Observer+。"""
    result = await db.execute(select(TestSession).where(TestSession.end_time.is_(None)).order_by(TestSession.id.desc()))
    r = result.scalars().first()
    if r is None:
        return ok(None)
    return ok({"test_id": r.test_id, "operator_id": r.operator_id, "start_time": r.start_time})


@router.get("/next-id")
async def next_test_id(db: DbDep, day: str | None = None):
    """生成当日下一个建议编号；最终唯一性仍由 start_test 原子校验保证。"""
    target_day = day or now_iso()[:10].replace("-", "")
    if not re.fullmatch(r"\d{8}", target_day):
        raise HTTPException(status_code=422, detail=err("invalid_day", "日期必须为 YYYYMMDD"))

    prefix = f"TEST-{target_day}-"
    rows = (await db.execute(select(TestSession.test_id).where(TestSession.test_id.like(f"{prefix}%")))).scalars()
    sequence = 0
    for test_id in rows:
        match = re.fullmatch(rf"{re.escape(prefix)}(\d+)", test_id)
        if match:
            sequence = max(sequence, int(match.group(1)))
    return ok({"test_id": f"{prefix}{sequence + 1:03d}"})


@router.get("/{test_id}")
async def test_detail(test_id: str, db: DbDep):
    """试验详情 + 统计摘要。权限：Observer+。"""
    result = await db.execute(select(TestSession).where(TestSession.test_id == test_id))
    r = result.scalar_one_or_none()
    if r is None:
        return ok(None)
    sample_count = await db.scalar(select(func.count()).select_from(SamplePoint).where(SamplePoint.test_id == test_id))
    alarm_count = await db.scalar(select(func.count()).select_from(AlarmLog).where(AlarmLog.test_id == test_id))
    return ok(
        {
            "test_id": r.test_id,
            "operator_id": r.operator_id,
            "start_time": r.start_time,
            "end_time": r.end_time,
            "end_reason": r.end_reason,
            "state_at_end": r.state_at_end,
            "sample_label": r.sample_label,
            "notes": r.notes,
            "report_path": r.report_path,
            "sample_count": int(sample_count or 0),
            "alarm_count": int(alarm_count or 0),
        }
    )


@router.get("/{test_id}/samples")
async def test_samples(test_id: str, db: DbDep, max_points: int = 1000):
    """试验曲线数据（等距降采样到 max_points 以内）。权限：Observer+。"""
    return ok(await query_downsampled_points(db, test_id=test_id, max_points=max_points))


@router.get("/{test_id}/events")
async def test_events(test_id: str, db: DbDep, limit: int = 500):
    """该试验事件日志。权限：Observer+。"""
    rows = (
        (await db.execute(select(EventLog).where(EventLog.test_id == test_id).order_by(EventLog.ts).limit(limit)))
        .scalars()
        .all()
    )
    return ok(
        [{"ts": e.ts, "source": e.source, "event_code": e.event_code, "level": e.level, "text": e.text} for e in rows]
    )


@router.get("/{test_id}/alarms")
async def test_alarms(test_id: str, db: DbDep):
    """该试验报警记录。权限：Observer+。"""
    rows = (
        (await db.execute(select(AlarmLog).where(AlarmLog.test_id == test_id).order_by(AlarmLog.occur_time)))
        .scalars()
        .all()
    )
    return ok(
        [
            {
                "id": a.id,
                "alarm_code": a.alarm_code,
                "level": a.level,
                "occur_time": a.occur_time,
                "clear_time": a.clear_time,
                "ack_time": a.ack_time,
                "ack_operator": a.ack_operator,
                "text": a.text,
            }
            for a in rows
        ]
    )
