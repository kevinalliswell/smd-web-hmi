"""日志写库服务：sample_point / event_log / alarm_log / parameter_snapshot（只追加）。

安全红线 7：这些表只追加，本服务只提供 INSERT，不提供 DELETE / UPDATE。
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AlarmLog, DeviceStatus, EventLog, ParameterSnapshot, SamplePoint
from app.hostcomm.protocol import now_iso
from app.services.snapshot_data import object_value
from app.services.standard_metrics import number
from app.services.telemetry_integrity import track_sample

DEVICE_STATUS_RETENTION_HOURS = 24
DEVICE_STATUS_CLEANUP_INTERVAL_SECONDS = 300


class DeviceStatusPruner:
    """按 UTC 时间窗批量清理，且在进程内限制执行频率。"""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        utc_clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._clock = clock
        self._utc_clock = utc_clock
        self._last_cleanup_at: float | None = None
        self._lock = asyncio.Lock()

    async def prune_if_due(
        self,
        session: AsyncSession,
        *,
        retention_hours: int,
        cleanup_interval_seconds: int,
    ) -> int:
        """到达清理间隔时删除窗口外记录，否则不访问数据库。"""
        now_tick = self._clock()
        if self._last_cleanup_at is not None and now_tick - self._last_cleanup_at < cleanup_interval_seconds:
            return 0

        async with self._lock:
            now_tick = self._clock()
            if self._last_cleanup_at is not None and now_tick - self._last_cleanup_at < cleanup_interval_seconds:
                return 0
            cutoff = (self._utc_clock() - timedelta(hours=retention_hours)).isoformat(timespec="seconds")
            result = await session.execute(
                delete(DeviceStatus).where(func.datetime(DeviceStatus.ts) < func.datetime(cutoff))
            )
            await session.commit()
            self._last_cleanup_at = now_tick
            return result.rowcount or 0


device_status_pruner = DeviceStatusPruner()


async def append_parameter_snapshot(
    session: AsyncSession,
    readback: dict[str, Any],
    *,
    test_id: str | None,
    operator_id: str | None,
    source: str,
) -> None:
    """追加一条设备参数回读快照，并保留其试验归属。"""
    values = readback.get("params") or readback.get("values") or {}
    session.add(
        ParameterSnapshot(
            test_id=test_id,
            ts=now_iso(),
            operator_id=operator_id,
            source=source,
            fw_version=readback.get("fw_version"),
            profile_version=readback.get("device_profile_version"),
            param_crc=readback.get("parameter_crc"),
            params_json=json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        )
    )
    await session.commit()


async def append_sample_point(
    session: AsyncSession, test_id: str, snapshot: dict[str, Any], *, source: str = "live_poll", commit: bool = True
) -> None:
    """从 status_snapshot 提取核心曲线字段写入 sample_point。"""
    snapshot = await track_sample(session, test_id, snapshot)
    temp = object_value(snapshot.get("temperature"))
    gas = object_value(snapshot.get("gas"))
    meas = object_value(snapshot.get("measurement"))
    sm = object_value(snapshot.get("state_machine"))
    safety = object_value(snapshot.get("safety"))

    session.add(
        SamplePoint(
            test_id=test_id,
            ts=object_value(snapshot.get("_hostcomm")).get("received_at") or now_iso(),
            source=source,
            furnace_pv=number(temp.get("furnace_pv_deg_c")),
            furnace_sv=number(temp.get("furnace_sv_deg_c")),
            burden_temp=number(meas.get("burden_temp_deg_c")),
            burden_temp_v=(
                1 if meas.get("burden_temp_valid") is True else 0 if meas.get("burden_temp_valid") is False else -1
            ),
            temp_output_pct=number(temp.get("temp_output_percent")),
            program_step=temp.get("program_step") if type(temp.get("program_step")) is int else None,
            n2_sp=number(gas.get("n2_sp_l_min")),
            n2_pv=number(gas.get("n2_pv_l_min")),
            co_sp=number(gas.get("co_sp_l_min")),
            co_pv=number(gas.get("co_pv_l_min")),
            drip_weight=number(meas.get("drip_weight_g")),
            delta_p=number(meas.get("delta_p_pa")),
            delta_p_v=(1 if meas.get("delta_p_valid") is True else 0 if meas.get("delta_p_valid") is False else -1),
            displacement=number(meas.get("displacement_mm")),
            displacement_v=(
                1 if meas.get("displacement_valid") is True else 0 if meas.get("displacement_valid") is False else -1
            ),
            current_state=sm.get("current_state") if isinstance(sm.get("current_state"), str) else None,
            safety_relay=(
                1
                if safety.get("safety_relay_allowed") is True
                else 0 if safety.get("safety_relay_allowed") is False else None
            ),
            ext_json=json.dumps(snapshot, ensure_ascii=False),
        )
    )
    if commit:
        await session.commit()
    else:
        await session.flush()


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
    session: AsyncSession,
    snapshot: dict[str, Any],
    *,
    retention_hours: int = DEVICE_STATUS_RETENTION_HOURS,
    cleanup_interval_seconds: int = DEVICE_STATUS_CLEANUP_INTERVAL_SECONDS,
    pruner: DeviceStatusPruner = device_status_pruner,
) -> None:
    """写入滚动缓冲，并按 UTC 时间窗定期批量裁剪。

    注意：device_status 是规格中**唯一**允许 DELETE 旧记录的表（第 2.8 节）。
    sample_point / event_log / alarm_log 严禁删除。
    """
    session.add(DeviceStatus(ts=now_iso(), status_json=json.dumps(snapshot, ensure_ascii=False)))
    await session.commit()
    await pruner.prune_if_due(
        session,
        retention_hours=retention_hours,
        cleanup_interval_seconds=cleanup_interval_seconds,
    )


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
