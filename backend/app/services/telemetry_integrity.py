"""按能力声明核对源端 status_snapshot 序列；cursor 与原始采样同事务持久化。

telemetry_sequence_v1: payload.telemetry={boot_id, sequence}，同一次板端启动的每个
状态采样序号递增1，不能复用 HostComm 请求/响应 msg_id。未声明能力不能推定完整。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import select

from app.db.models import EventLog, TestSession
from app.hostcomm.protocol import now_iso
from app.services.snapshot_data import capabilities, json_object, object_value
from app.services.standard_metrics import number


def timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None
    except ValueError:
        return None


def advance_cursor(cursor: dict, snapshot: dict) -> tuple[dict, list[str]]:
    """有限空间状态；传入数据库保存的cursor，服务重启不会丢失上次源端位置。"""
    current = dict(cursor)
    issues = []
    integer_fields = ("verified_samples", "sequence", "callback_count")
    flags = ("gap_seen", "unsupported_seen", "measurement_start_observed")
    malformed = any(
        key in current and (type(current[key]) is not int or current[key] < 0) for key in integer_fields
    ) or any(key in current and type(current[key]) is not bool for key in flags)
    if malformed:
        current = {"gap_seen": True}
        issues.append("invalid_persisted_cursor")
    transport = object_value(snapshot.get("_hostcomm"))
    hmi = object_value(snapshot.get("_hmi"))
    callbacks = transport.get("dropped_callbacks")
    if type(callbacks) is int:
        previous = current.get("callback_count")
        if type(previous) is int and callbacks > previous:
            issues.append("callback_gap")
        current["callback_count"] = callbacks
    if hmi.get("persistence_failures"):
        issues.append("persistence_gap")
    if "telemetry_sequence_v1" not in capabilities(snapshot):
        current["unsupported_seen"] = True
        current["gap_seen"] = bool(current.get("gap_seen") or issues)
        return current, issues
    source = object_value(snapshot.get("telemetry"))
    boot, seq = source.get("boot_id"), source.get("sequence")
    device_time = timestamp(transport.get("device_timestamp"))
    if not isinstance(boot, str) or not boot.strip() or len(boot) > 128 or type(seq) is not int or not 0 <= seq < 2**64:
        issues.append("invalid_telemetry_identity")
    if device_time is None:
        issues.append("invalid_device_timestamp")
    if issues and ("invalid_telemetry_identity" in issues or "invalid_device_timestamp" in issues):
        current["gap_seen"] = True
        return current, issues
    old_boot, old_seq = current.get("boot_id"), current.get("sequence")
    if old_boot is not None:
        if boot != old_boot:
            issues.append("device_restarted")
        elif type(old_seq) is not int:
            issues.append("invalid_persisted_cursor")
        elif seq == old_seq:
            issues.append("sequence_duplicate")
        elif seq < old_seq:
            issues.append("sequence_reordered")
        elif seq > old_seq + 1:
            issues.append("sequence_gap")
    previous_time = timestamp(current.get("device_timestamp"))
    if previous_time is not None and device_time < previous_time:
        issues.append("device_clock_rollback")
    if "sequence_duplicate" not in issues and "sequence_reordered" not in issues:
        current.update(boot_id=boot, sequence=seq, device_timestamp=transport["device_timestamp"])
    furnace = number(object_value(snapshot.get("temperature")).get("furnace_pv_deg_c"))
    measurement = object_value(snapshot.get("measurement"))
    if (
        furnace is not None
        and furnace <= 600
        and measurement.get("displacement_valid") is True
        and number(measurement.get("displacement_mm")) is not None
    ):
        current["measurement_start_observed"] = True
    current["verified_samples"] = current.get("verified_samples", 0) + 1
    current["gap_seen"] = bool(current.get("gap_seen") or issues)
    return current, issues


def continuity_proven(cursor: dict) -> bool:
    return (
        cursor.get("measurement_start_observed") is True
        and type(cursor.get("verified_samples")) is int
        and cursor["verified_samples"] >= 2
        and isinstance(cursor.get("boot_id"), str)
        and bool(cursor["boot_id"])
        and type(cursor.get("sequence")) is int
        and cursor["sequence"] >= 0
        and cursor.get("gap_seen", False) is False
        and cursor.get("unsupported_seen", False) is False
    )


async def track_sample(session, test_id: str, snapshot: dict) -> dict:
    """不自行commit；调用方必须把返回快照、样本和会话cursor一起提交。"""
    row = await session.scalar(select(TestSession).where(TestSession.test_id == test_id))
    if row is None:
        return snapshot
    basis, valid = json_object(row.measurement_basis_json)
    previous = object_value(basis.get("telemetry"))
    cursor, issues = advance_cursor(previous, snapshot)
    if not valid or ("telemetry" in basis and not isinstance(basis["telemetry"], dict)):
        issues.append("invalid_measurement_basis")
        cursor["gap_seen"] = True
    sm = object_value(snapshot.get("state_machine"))
    if sm.get("test_id") != test_id:
        issues.append("test_identity_missing_or_conflicting")
        cursor["gap_seen"] = True
    if issues:
        row.data_integrity = "incomplete"
    if issues and issues != basis.get("telemetry_last_issues"):
        session.add(
            EventLog(
                test_id=test_id,
                ts=now_iso(),
                source="hmi",
                event_code="HMI-TELEMETRY-INTEGRITY",
                level=1,
                text="原始遥测完整性证据不足",
                detail_json=json.dumps(
                    {
                        "issues": issues,
                        "previous": previous,
                        "telemetry": snapshot.get("telemetry"),
                        "transport": snapshot.get("_hostcomm"),
                    },
                    ensure_ascii=False,
                ),
            )
        )
    basis["telemetry_last_issues"] = issues
    basis["telemetry"] = cursor
    row.measurement_basis_json = json.dumps(basis, ensure_ascii=False)
    return {**snapshot, "_hmi": {**object_value(snapshot.get("_hmi")), "telemetry_issues": issues}}
