"""试验会话启动对账与进程内运行态恢复。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import TestSession
from app.hostcomm.protocol import now_iso
from app.services.state_policy import classify_state
from app.services.test_runtime import active_test


async def reconcile_test_sessions(
    session: AsyncSession,
    device_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """恢复唯一未闭合会话，或按设备状态闭合重启遗留记录。

    无设备快照时保留最新会话并等待首次状态帧；有快照时仅当设备仍处于
    运行/未知状态且试验编号可对应时恢复。其余遗留会话以
    ``backend_restart``（或冲突原因）闭合。
    """
    rows = (
        (
            await session.execute(
                select(TestSession).where(TestSession.end_time.is_(None)).order_by(TestSession.id.desc())
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        active_test.stop()
        return {"restored_test_id": None, "closed": 0, "pending_device": False}

    current_state = None
    device_test_id = None
    restored = None
    pending_device = device_snapshot is None

    if device_snapshot is None:
        restored = rows[0]
    else:
        state_machine = device_snapshot.get("state_machine") or {}
        system = device_snapshot.get("system") or {}
        current_state = state_machine.get("current_state") or system.get("current_state")
        device_test_id = state_machine.get("test_id")
        operation_state = classify_state(current_state)

        if operation_state in {"running", "unknown"}:
            if device_test_id:
                restored = next((row for row in rows if row.test_id == device_test_id), None)
            elif len(rows) == 1:
                restored = rows[0]

    closed = 0
    closed_at = now_iso()
    for row in rows:
        if restored is not None and row.id == restored.id:
            continue
        row.end_time = closed_at
        row.end_reason = "backend_restart_conflict" if restored is not None else "backend_restart"
        row.state_at_end = current_state
        closed += 1

    if restored is None:
        active_test.stop()
    else:
        active_test.restore(restored.test_id, needs_device_reconcile=pending_device)

    if closed:
        await session.commit()

    return {
        "restored_test_id": restored.test_id if restored is not None else None,
        "closed": closed,
        "pending_device": pending_device and restored is not None,
        "device_test_id": device_test_id,
        "current_state": current_state,
    }
