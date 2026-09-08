"""Project verified V2 data into existing page fields without inventing V1 wire capabilities."""

from __future__ import annotations

import copy
import math
import time

from app.hostcomm.v2_contract.codec import validate_message
from app.services.state_policy import enrich_status_snapshot

CHANNELS = {
    "furnace_mc": ("temperature", "furnace_pv_deg_c", 1000),
    "furnace_setpoint_mc": ("temperature", "furnace_sv_deg_c", 1000),
    "burden_mc": ("measurement", "burden_temp_deg_c", 1000),
    "pressure_drop_pa": ("measurement", "delta_p_pa", 1),
    "displacement_um": ("measurement", "displacement_mm", 1000),
    "drip_mass_mg": ("measurement", "drip_weight_g", 1000),
    "n2_measured_ml_min": ("gas", "n2_pv_l_min", 1000),
    "n2_setpoint_ml_min": ("gas", "n2_sp_l_min", 1000),
    "co_measured_ml_min": ("gas", "co_pv_l_min", 1000),
    "co_setpoint_ml_min": ("gas", "co_sp_l_min", 1000),
}


def _payload(frame: dict | None, expected: str) -> dict:
    if frame is None:
        return {}
    validated = validate_message(frame)
    if validated.type != expected:
        raise ValueError("Projection received the wrong message type")
    return validated.payload.model_dump(mode="python")


def _receipt_age(receipt: dict | None, now: float) -> float | None:
    received = (receipt or {}).get("received_monotonic")
    if type(received) not in (int, float) or not math.isfinite(received) or received < 0 or received > now:
        return None
    return now - received


def project_status(
    status_frame: dict | None,
    telemetry_frame: dict | None = None,
    profile_frame: dict | None = None,
    *,
    hello_payload: dict | None = None,
    online: bool = False,
    control_ready: bool = False,
    status_receipt: dict | None = None,
    telemetry_receipt: dict | None = None,
    alarm_snapshot: dict | None = None,
    lease_evidence: dict | None = None,
    now_monotonic: float | None = None,
) -> dict:
    """Only status.run determines phase; source/receipt ages determine whether points remain usable."""
    now = time.monotonic() if now_monotonic is None else now_monotonic
    if not math.isfinite(now) or now < 0:
        raise ValueError("Projection requires a valid monotonic timestamp")
    status = _payload(status_frame, "status_snapshot")
    telemetry = _payload(telemetry_frame, "telemetry")
    profile = _payload(profile_frame, "profile_snapshot")
    hello = hello_payload or {}
    run, safety = status.get("run") or {}, status.get("safety") or {}
    phase = run.get("state")
    status_age, telemetry_age = _receipt_age(status_receipt, now), _receipt_age(telemetry_receipt, now)
    status_fresh = bool(online and status and status_age is not None and status_age <= 5.0)
    alarms_current = bool(
        status_fresh
        and alarm_snapshot
        and alarm_snapshot.get("boot_id") == status_frame["boot_id"]
        and alarm_snapshot.get("revision") == status.get("active_alarm_revision")
    )
    alarms = (alarm_snapshot or {}).get("items", [])
    profile_matches = bool(profile and profile.get("profile_digest") == status.get("profile_digest"))
    sample_context = bool(
        status_frame
        and telemetry_frame
        and telemetry
        and telemetry_frame["session_id"] == status_frame["session_id"]
        and telemetry_frame["boot_id"] == status_frame["boot_id"]
        and telemetry["run_id"] == run.get("run_id")
        and telemetry["state_revision"] == run.get("state_revision")
        and int(telemetry["sample_uptime_ms"]) <= int(telemetry_frame["uptime_ms"])
    )
    latest = status.get("latest_sample")
    if sample_context and latest is not None:
        sample_context = telemetry["sample"]["boot_id"] == latest["boot_id"] and int(
            telemetry["sample"]["sample_seq"]
        ) >= int(latest["sample_seq"])
    wire_delay_ms = (
        max(0, int(telemetry_frame["uptime_ms"]) - int(telemetry["sample_uptime_ms"])) if sample_context else 0
    )
    limits = (profile.get("resources") or {}).get("channel_freshness_ms") or {}
    quality = {}
    snapshot = {"temperature": {}, "measurement": {}, "gas": {}}
    for channel, (group, name, scale) in CHANNELS.items():
        point = (telemetry.get("values") or {}).get(channel) or {"value": None, "quality": "unavailable", "age_ms": 0}
        total_age = (
            point["age_ms"] + wire_delay_ms + telemetry_age * 1000
            if telemetry_age is not None and sample_context
            else None
        )
        good = bool(
            online
            and sample_context
            and profile_matches
            and point["quality"] == "good"
            and point["value"] is not None
            and total_age is not None
            and total_age <= limits.get(channel, -1)
        )
        effective = point["quality"] if point["quality"] != "good" else "good" if good else "stale"
        quality[channel] = {
            "quality": effective,
            "source_quality": point["quality"],
            "age_ms": total_age,
            "source_value": point["value"],
        }
        snapshot[group][name] = point["value"] / scale if good else None
    sample_usable = sample_context and any(item["quality"] == "good" for item in quality.values())
    lease_current = bool(
        status_fresh
        and status_frame
        and status.get("lease_id")
        and status.get("lease_owner_session_id") == status_frame["session_id"]
        and int(status.get("lease_expires_uptime_ms") or "0")
        > int(status_frame["uptime_ms"]) + (status_age or 0) * 1000
    )
    if lease_evidence is not None:
        status_fresh = bool(
            status_fresh
            and lease_evidence["session_id"] == status_frame["session_id"]
            and lease_evidence["boot_id"] == status_frame["boot_id"]
            and int(run["state_revision"]) >= lease_evidence["minimum_state_revision"]
        )
        lease_current = bool(
            lease_current and lease_evidence["valid"] and lease_evidence["lease_id"] == status.get("lease_id")
        )
    lease_available = (status.get("lease_id") is None or lease_current) and not (
        lease_evidence and lease_evidence["renewals_paused"]
    )
    ready = bool(
        control_ready
        and status_fresh
        and profile_matches
        and profile.get("approved") is True
        and safety.get("profile_approved") is True
        and lease_available
        and hello.get("granted_role") == "control"
    )
    snapshot.update(
        {
            "system": {
                "protocol_version": "2.0",
                "handshake_control_ready_hint": hello.get("control_ready"),
                "fw_version": hello.get("fw_version"),
                "hw_version": hello.get("hw_version"),
                "control_lease_acquire_required": bool(ready and status.get("lease_id") is None),
                "device_profile_version": profile.get("profile_id"),
                "current_state": phase,
                "uptime_s": int(status_frame["uptime_ms"]) / 1000 if status_frame else None,
                "rtc_valid": status_frame is not None and status_frame["timestamp"] is not None,
            },
            "state_machine": {
                "current_state": phase,
                "phase": phase,
                "run_id": run.get("run_id"),
                "test_id": run.get("run_id"),
                "measurement_complete": run.get("measurement_complete", False),
                "safe_complete": run.get("safe_complete", False),
                "outcome": run.get("outcome"),
                "stage_index": run.get("stage_index"),
                "recipe_digest": run.get("recipe_digest"),
                "safety_profile_digest": run.get("safety_profile_digest"),
                "measurement_start": run.get("measurement_start"),
                "measurement_end": run.get("measurement_end"),
                "safe_boundary": run.get("safe_boundary"),
                "operator_ack_required": phase == "completed",
                "state_elapsed_s": None,
                "fault_reason": "device_fault" if phase == "fault" else None,
            },
            "safety": {
                "safety_relay_allowed": bool(status_fresh and safety.get("hardwired_permit")),
                "emergency_stop": safety.get("emergency_stop"),
                "co_alarm_l1": safety.get("co_alarm"),
                "co_alarm_l2": safety.get("co_alarm"),
                "exhaust_ok": safety.get("exhaust_ok") if status_fresh else None,
                "overtemp_alarm": safety.get("overtemperature"),
            },
            "alarm": {
                "revision": status.get("active_alarm_revision"),
                "alarm_level": None,
                "latched_alarm_count": sum(item["active"] for item in alarms) if alarms_current else None,
                "ack_required": any(not item["acknowledged"] for item in alarms) if alarms_current else None,
            },
            "log": copy.deepcopy(status.get("log") or {}),
            "comm_quality": "offline" if not online else "online" if status_fresh else "degraded",
            "data_fresh": bool(status_fresh),
            "control_ready": ready,
            "last_update": (status_receipt or {}).get("received_at"),
            "_hostcomm": {
                "protocol_version": "2.0",
                "capabilities": list(hello.get("capabilities") or []),
                "msg_id": status_frame.get("msg_id") if status_frame else None,
                "device_timestamp": status_frame.get("timestamp") if status_frame else None,
                "session_id": status_frame.get("session_id") if status_frame else None,
                "received_at": (status_receipt or {}).get("received_at"),
                "received_monotonic": (status_receipt or {}).get("received_monotonic"),
            },
            "_v2": {
                "status": copy.deepcopy(status_frame),
                "telemetry": copy.deepcopy(telemetry_frame),
                "profile": copy.deepcopy(profile_frame),
                "channel_quality": quality,
                "status_fresh": status_fresh,
                "sample_usable": bool(sample_usable),
                "profile_matches": profile_matches,
                "granted_role": hello.get("granted_role"),
                "online": online,
                "alarms_reconciled": alarms_current,
            },
        }
    )
    measurement = snapshot["measurement"]
    for name, channel in (
        ("burden_temp_valid", "burden_mc"),
        ("delta_p_valid", "pressure_drop_pa"),
        ("displacement_valid", "displacement_um"),
        ("drip_weight_valid", "drip_mass_mg"),
    ):
        measurement[name] = quality[channel]["quality"] == "good"
    measurement.update(
        {
            "first_drip_latched": telemetry.get("first_drip_latched") if sample_context else None,
            "first_drip_valid": False,
            "balance_stable": None,
            "tare_allowed": False,
        }
    )
    snapshot["gas"].update(
        n2_status=quality["n2_measured_ml_min"]["quality"], co_status=quality["co_measured_ml_min"]["quality"]
    )
    return enrich_status_snapshot(snapshot, control_ready=ready)
