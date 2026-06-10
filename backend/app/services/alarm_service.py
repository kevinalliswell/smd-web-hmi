"""报警/事件处理服务：将 STM32 推送的 event 帧落库并生成 WebSocket 广播描述。

规格见协议第 11 节（事件推送）与开发规格说明书 §4.1。SOP §12 异常分级：
level 2 = L2（关 CO/N2 置换/暂停），level 3 = L3（切加热/急停级）。

安全红线 7：alarm_log / event_log 只追加。报警"消除/确认"只更新 clear_time /
ack_time 字段，不删除、不覆盖原始发生记录。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import AlarmLog
from app.hostcomm.protocol import now_iso
from app.services import logging_service

logger = get_logger("service.alarm")

# event payload.kind 取值
KIND_ALARM_NEW = "alarm_new"
KIND_ALARM_CLEAR = "alarm_clear"
KIND_ALARM_ACK = "alarm_ack"
KIND_STATE_CHANGE = "state_change"


async def handle_event(session: AsyncSession, payload: dict[str, Any]) -> tuple[str, dict] | None:
    """处理一条 event payload：写库并返回 (ws_type, ws_data) 供广播；无需广播返回 None。"""
    kind = payload.get("kind", "event")
    code = payload.get("event_code") or "EVT"
    level = int(payload.get("level", 0) or 0)
    text = payload.get("text")
    test_id = payload.get("test_id")
    latched = bool(payload.get("latched", False))

    # 所有事件均追加 event_log（原始 payload 存 detail_json）
    await logging_service.append_event(
        session,
        source="stm32_event",
        event_code=code,
        level=level,
        text=text,
        test_id=test_id,
        detail=payload,
    )

    if kind == KIND_ALARM_NEW:
        alarm = AlarmLog(
            test_id=test_id,
            alarm_code=code,
            level=level,
            occur_time=now_iso(),
            text=text,
            latched=int(latched),
        )
        session.add(alarm)
        await session.commit()
        await session.refresh(alarm)
        return (
            "alarm_new",
            {
                "alarm_id": alarm.id,
                "alarm_code": code,
                "level": level,
                "text": text,
                "occur_time": alarm.occur_time,
                "latched": bool(latched),
            },
        )

    if kind == KIND_ALARM_CLEAR:
        # 消除最近一条同码未消除报警（只更新 clear_time，不删除）
        row = await session.scalar(
            select(AlarmLog)
            .where(AlarmLog.alarm_code == code, AlarmLog.clear_time.is_(None))
            .order_by(AlarmLog.id.desc())
        )
        clear_time = now_iso()
        if row is not None:
            row.clear_time = clear_time
            await session.commit()
        return (
            "alarm_clear",
            {"alarm_id": row.id if row else None, "alarm_code": code, "clear_time": clear_time},
        )

    if kind == KIND_STATE_CHANGE:
        return ("state_change", payload)

    # 其它事件：仅广播通用 event
    return ("event", payload)
