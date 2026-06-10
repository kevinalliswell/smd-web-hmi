"""报警事件处理与落库测试（alarm_service / event→alarm_log）。"""

from __future__ import annotations

from sqlalchemy import func, select

from app.db.models import AlarmLog, EventLog
from app.services import alarm_service


def _alarm_payload(kind, code="ALM-CO-L1", level=2, text="CO 一级报警", test_id="T-1"):
    return {
        "event_code": code,
        "level": level,
        "current_state": "Reducing",
        "text": text,
        "latched": level >= 3,
        "ack_required": True,
        "test_id": test_id,
        "kind": kind,
    }


# ----------------------------------------------------- alarm_new → 落库 + 广播描述
async def test_alarm_new_persisted(db_session):
    ws = await alarm_service.handle_event(db_session, _alarm_payload("alarm_new", level=3))
    # 写入 alarm_log 与 event_log
    assert await db_session.scalar(select(func.count()).select_from(AlarmLog)) == 1
    assert await db_session.scalar(select(func.count()).select_from(EventLog)) == 1
    row = (await db_session.execute(select(AlarmLog))).scalar_one()
    assert row.alarm_code == "ALM-CO-L1"
    assert row.level == 3
    assert row.clear_time is None
    # 广播描述含 alarm_id（供前端确认引用）
    assert ws[0] == "alarm_new"
    assert ws[1]["alarm_id"] == row.id
    assert ws[1]["level"] == 3


# ----------------------------------------------------- alarm_clear → 仅更新 clear_time
async def test_alarm_clear_updates_only(db_session):
    await alarm_service.handle_event(db_session, _alarm_payload("alarm_new"))
    ws = await alarm_service.handle_event(db_session, _alarm_payload("alarm_clear"))
    # 仍只有 1 条报警记录（不新增、不删除），clear_time 被填充
    assert await db_session.scalar(select(func.count()).select_from(AlarmLog)) == 1
    row = (await db_session.execute(select(AlarmLog))).scalar_one()
    assert row.clear_time is not None
    assert ws[0] == "alarm_clear"
    assert ws[1]["alarm_code"] == "ALM-CO-L1"


# ----------------------------------------------------- 通用事件仅写 event_log
async def test_generic_event_logged(db_session):
    ws = await alarm_service.handle_event(
        db_session, {"event_code": "EVT-STATE", "level": 0, "kind": "state_change", "to_state": "Heating"}
    )
    assert await db_session.scalar(select(func.count()).select_from(EventLog)) == 1
    assert await db_session.scalar(select(func.count()).select_from(AlarmLog)) == 0
    assert ws[0] == "state_change"


# ----------------------------------------------------- 活跃报警按级别可排序
async def test_multiple_alarms_levels(db_session):
    await alarm_service.handle_event(db_session, _alarm_payload("alarm_new", code="A1", level=1))
    await alarm_service.handle_event(db_session, _alarm_payload("alarm_new", code="A3", level=3))
    await alarm_service.handle_event(db_session, _alarm_payload("alarm_new", code="A2", level=2))
    rows = (
        await db_session.execute(
            select(AlarmLog).where(AlarmLog.clear_time.is_(None)).order_by(AlarmLog.level.desc())
        )
    ).scalars().all()
    assert [r.level for r in rows] == [3, 2, 1]
