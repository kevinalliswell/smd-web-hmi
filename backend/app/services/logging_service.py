"""日志写库服务：sample_point / event_log / alarm_log（只追加）。

安全红线 7：这些表只追加，本服务只提供 INSERT，不提供 DELETE / UPDATE。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AlarmLog, DeviceStatus, EventLog, SamplePoint
from app.hostcomm.protocol import now_iso

# device_status 滚动缓冲保留条数（唯一允许 DELETE 的表）
DEVICE_STATUS_KEEP = 1000


async def append_sample_point(
    session: AsyncSession, test_id: str, snapshot: dict[str, Any], *, source: str = "live_poll"
) -> None:
    """从 status_snapshot 提取核心曲线字段写入 sample_point。"""
    temp = snapshot.get("temperature", {})
    gas = snapshot.get("gas", {})
    meas = snapshot.get("measurement", {})
    sm = snapshot.get("state_machine", {})
    safety = snapshot.get("safety", {})

    session.add(
        SamplePoint(
            test_id=test_id,
            ts=snapshot.get("timestamp") or now_iso(),
            source=source,
            furnace_pv=temp.get("furnace_pv_deg_c"),
            furnace_sv=temp.get("furnace_sv_deg_c"),
            burden_temp=meas.get("burden_temp_deg_c"),
            burden_temp_v=int(bool(meas.get("burden_temp_valid", True))),
            temp_output_pct=temp.get("temp_output_percent"),
            program_step=temp.get("program_step"),
            n2_sp=gas.get("n2_sp_l_min"),
            n2_pv=gas.get("n2_pv_l_min"),
            co_sp=gas.get("co_sp_l_min"),
            co_pv=gas.get("co_pv_l_min"),
            drip_weight=meas.get("drip_weight_g"),
            delta_p=meas.get("delta_p_pa"),
            delta_p_v=int(bool(meas.get("delta_p_valid", True))),
            displacement=meas.get("displacement_mm"),
            displacement_v=int(bool(meas.get("displacement_valid", True))),
            current_state=sm.get("current_state"),
            safety_relay=int(bool(safety.get("safety_relay_allowed", False))),
            ext_json=None,
        )
    )
    await session.commit()


async def append_event(
    session: AsyncSession,
    *,
    source: str,
    event_code: str,
    level: int = 0,
    text: str | None = None,
    test_id: str | None = None,
    operator_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """写入 event_log（只追加）。"""
    session.add(
        EventLog(
            test_id=test_id,
            ts=now_iso(),
            source=source,
            event_code=event_code,
            level=level,
            text=text,
            operator_id=operator_id,
            detail_json=json.dumps(detail, ensure_ascii=False) if detail else None,
        )
    )
    await session.commit()


async def append_device_status(
    session: AsyncSession, snapshot: dict[str, Any], *, keep: int = DEVICE_STATUS_KEEP
) -> None:
    """写入 device_status 滚动缓冲，并裁剪到最近 ``keep`` 条。

    注意：device_status 是规格中**唯一**允许 DELETE 旧记录的表（第 2.8 节）。
    sample_point / event_log / alarm_log 严禁删除。
    """
    session.add(DeviceStatus(ts=now_iso(), status_json=json.dumps(snapshot, ensure_ascii=False)))
    await session.commit()

    # 裁剪：删除超出保留窗口的最旧记录
    ids = (
        await session.execute(
            select(DeviceStatus.id).order_by(DeviceStatus.id.desc()).offset(keep)
        )
    ).scalars().all()
    if ids:
        await session.execute(delete(DeviceStatus).where(DeviceStatus.id.in_(ids)))
        await session.commit()


async def append_alarm(
    session: AsyncSession,
    *,
    alarm_code: str,
    level: int,
    text: str | None = None,
    test_id: str | None = None,
    latched: bool = False,
) -> None:
    """写入 alarm_log（只追加）。"""
    session.add(
        AlarmLog(
            test_id=test_id,
            alarm_code=alarm_code,
            level=level,
            occur_time=now_iso(),
            text=text,
            latched=int(latched),
        )
    )
    await session.commit()
