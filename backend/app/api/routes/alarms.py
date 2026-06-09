"""报警路由 /api/alarms（规格 3.5）。D3 骨架。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from app.api.deps import DbDep, UserDep, get_current_user
from app.api.schemas import err, ok
from app.db.models import AlarmLog
from app.hostcomm.protocol import now_iso

router = APIRouter(prefix="/api/alarms", tags=["alarms"])


@router.get("/active", dependencies=[Depends(get_current_user)])
async def active_alarms(db: DbDep):
    """当前活跃（未消除）报警。权限：Observer+。"""
    result = await db.execute(
        select(AlarmLog).where(AlarmLog.clear_time.is_(None)).order_by(AlarmLog.level.desc())
    )
    rows = result.scalars().all()
    return ok([_row(r) for r in rows])


@router.get("/history", dependencies=[Depends(get_current_user)])
async def alarm_history(db: DbDep, page: int = 1, size: int = 50):
    """历史报警（分页）。权限：Observer+。"""
    offset = max(0, (page - 1) * size)
    result = await db.execute(
        select(AlarmLog).order_by(AlarmLog.id.desc()).limit(size).offset(offset)
    )
    rows = result.scalars().all()
    return ok([_row(r) for r in rows])


@router.post("/{alarm_id}/ack")
async def ack_alarm(alarm_id: int, user: UserDep, db: DbDep):
    """确认报警（写确认信息）。权限：Operator+。"""
    if user.role == "observer":
        raise HTTPException(status_code=403, detail=err("operator_permission_denied", "无操作权限"))
    alarm = await db.get(AlarmLog, alarm_id)
    if alarm is None:
        raise HTTPException(status_code=404, detail=err("not_found", "报警不存在"))
    # 仅更新确认字段（不删除/覆盖原始报警记录）
    alarm.ack_time = now_iso()
    alarm.ack_operator = user.username
    await db.commit()
    return ok({"alarm_id": alarm_id, "ack_operator": user.username, "ack_time": alarm.ack_time})


def _row(r: AlarmLog) -> dict:
    return {
        "id": r.id,
        "alarm_code": r.alarm_code,
        "level": r.level,
        "occur_time": r.occur_time,
        "clear_time": r.clear_time,
        "ack_time": r.ack_time,
        "text": r.text,
    }
