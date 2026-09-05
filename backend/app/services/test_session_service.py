"""试验身份对账及板端确认的测定/安全终态。"""

from __future__ import annotations

import json
import math
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import EventLog, TestSession
from app.hostcomm.protocol import now_iso
from app.services.state_policy import classify_state
from app.services.test_id import InvalidTestIdError, validate_test_id
from app.services.test_runtime import active_test


def _event(session, row, code, detail):
    session.add(
        EventLog(
            test_id=row.test_id,
            ts=now_iso(),
            source="hmi",
            event_code=code,
            level=1,
            text=code,
            detail_json=json.dumps(detail, ensure_ascii=False),
        )
    )


async def reconcile_test_sessions(
    session: AsyncSession, device_snapshot: dict[str, Any] | None = None
) -> dict[str, Any]:
    """身份缺失/冲突保留为待核对，不把当前待机解释为历史正常完成。"""
    rows = list(
        (
            await session.scalars(
                select(TestSession).where(TestSession.end_time.is_(None)).order_by(TestSession.id.desc())
            )
        ).all()
    )
    restored = None
    if device_snapshot is None:
        if len(rows) == 1:
            restored = rows[0]
    else:
        sm = device_snapshot.get("state_machine") or {}
        device_id = sm.get("test_id")
        if device_id:
            restored = next((row for row in rows if row.test_id == device_id), None)
            if restored is None and classify_state(sm.get("current_state")) != "idle":
                try:
                    validate_test_id(device_id)
                except (InvalidTestIdError, TypeError):
                    device_id = None
                if (
                    device_id
                    and await session.scalar(select(TestSession.id).where(TestSession.test_id == device_id)) is None
                ):
                    restored = TestSession(
                        test_id=device_id,
                        operator_id="device-recovery",
                        start_time=now_iso(),
                        phase="needs_review",
                        data_integrity="incomplete",
                        notes="上位机首次观察到外部运行；开始时间为观察时间，样品及参数待核对",
                    )
                    session.add(restored)
                    _event(session, restored, "HMI-EXTERNAL-RUN-OBSERVED", sm)
        for row in rows:
            if row is not restored:
                if row.phase != "needs_review":
                    _event(session, row, "HMI-RUN-RECONCILIATION-REQUIRED", sm)
                row.phase, row.data_integrity = "needs_review", "incomplete"
        await session.commit()
    if restored is None:
        active_test.stop()
    else:
        active_test.restore(restored.test_id, needs_device_reconcile=device_snapshot is None)
    return {
        "restored_test_id": restored.test_id if restored else None,
        "closed": 0,
        "pending_device": device_snapshot is None and restored is not None,
    }


async def advance_test_session(session: AsyncSession, test_id: str, snapshot: dict[str, Any]) -> None:
    """必须在本帧采样入库之后调用，保证冷却终帧也保留在原试验内。"""
    row = await session.scalar(select(TestSession).where(TestSession.test_id == test_id))
    if row is None or row.end_time is not None:
        return
    sm = snapshot.get("state_machine") or {}
    transport = snapshot.get("_hostcomm") or {}
    capabilities = transport.get("capabilities") or []
    if sm.get("test_id") != row.test_id:
        return
    previous_phase = row.phase
    if transport.get("dropped_callbacks", 0) or (snapshot.get("_hmi") or {}).get("persistence_failures", 0):
        row.data_integrity = "incomplete"
    if "run_lifecycle_v1" not in capabilities:
        return
    if row.phase == "needs_review":
        row.data_integrity = "incomplete"
    row.phase = "stopping" if row.stop_requested_at else "measuring"
    if sm.get("measurement_complete") is True:
        row.measurement_completed_at = row.measurement_completed_at or now_iso()
        row.phase = "safe_disposal"
    basis = json.loads(row.measurement_basis_json or "{}")
    if "measurement_events_v1" in capabilities:
        basis["detector_verified"] = True
    row.measurement_basis_json = json.dumps(basis)
    measurement = snapshot.get("measurement") or {}
    temperature = measurement.get("burden_temp_deg_c")
    cool = (
        measurement.get("burden_temp_valid") is True
        and type(temperature) in (float, int)
        and math.isfinite(temperature)
        and temperature < 200
    )
    if sm.get("safe_complete") is True and cool:
        row.safety_completed_at = row.end_time = now_iso()
        row.end_reason = (
            "operator_stop"
            if row.stop_requested_at
            else "completed" if row.measurement_completed_at else "safe_end_incomplete"
        )
        row.state_at_end = sm.get("current_state")
        row.phase = "completed"
        # 缺少源端序号/补传证据时，网络看似正常也不能推定数据完整。
        if not row.measurement_completed_at:
            row.data_integrity = "incomplete"
    if row.phase != previous_phase:
        _event(session, row, "HMI-RUN-PHASE", {"previous": previous_phase, "phase": row.phase, "device": sm})
    await session.commit()
    if row.end_time and active_test.active_test_id == row.test_id:
        active_test.stop()
