"""STM32 状态词汇的统一安全策略。

固件状态名允许大小写和分隔符差异，但所有涉及参数下发和操作按钮的判定都在
后端完成。未知状态按保守策略处理：禁止启动/改参，同时保留受控停止入口。
"""

from __future__ import annotations

import re
from typing import Any

STATE_POLICY_VERSION = 1


def normalize_state(state: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", str(state or "").lower())


_IDLE_STATES = {
    "standby",
    "idle",
    "setup",
    "load",
    "ready",
    "done",
    "finished",
    "complete",
    "completed",
}

_RUNNING_STATES = {
    # Precheck / LeakCheck
    "precheck",
    "prestart",
    "permitcheck",
    "permit",
    "check",
    "leakcheck",
    "leak",
    "airtight",
    "tightness",
    # N2 protection / CO switch / heating
    "n2purge",
    "purge",
    "inert",
    "protect",
    "preheatpurge",
    "n2heat",
    "gasswitch",
    "switchco",
    "reduce",
    "reducing",
    "cogas",
    "heating",
    "rampup",
    "ramp",
    "program",
    "heat",
    # Hold / replacement / cooling
    "hold1580",
    "holding",
    "soak",
    "endhold",
    "hightemphold",
    "hold",
    "pause",
    "n2replace",
    "replace",
    "cooling",
    "cool",
    "endpurge",
    "purge2",
    "end",
    "cooldown",
}


def classify_state(state: str | None, *, protocol_version: str = "1.0") -> str:
    """返回 ``idle`` / ``running`` / ``fault`` / ``unknown``。"""
    normalized = normalize_state(state)
    if protocol_version == "2.0":
        if normalized == "idle":
            return "idle"
        if normalized in {"preparing", "measuring", "safedisposal", "cooling"}:
            return "running"
        if normalized == "completed":
            return "terminal"
        return "fault" if normalized == "fault" else "unknown"
    if normalized in _IDLE_STATES:
        return "idle"
    if "fault" in normalized:
        return "fault"
    if normalized in _RUNNING_STATES:
        return "running"
    return "unknown"


def parameter_changes_allowed(state: str | None, *, protocol_version: str = "1.0") -> bool:
    """参数仅允许在明确的非运行态修改；未知状态一律拒绝。"""
    if protocol_version == "2.0":
        return False  # Atomic recipe activation is a separate operation, never generic set_parameters.
    return classify_state(state) == "idle" or normalize_state(state) == "fault"


def snapshot_state(snapshot: dict[str, Any]) -> str | None:
    """规范状态路径优先；两个位置有冲突时禁止据此放行控制。"""
    canonical = (snapshot.get("state_machine") or {}).get("current_state")
    legacy = (snapshot.get("system") or {}).get("current_state")
    if canonical and legacy and normalize_state(canonical) != normalize_state(legacy):
        return None
    state = canonical or legacy
    return state if isinstance(state, str) and state.strip() else None


def enrich_status_snapshot(snapshot: dict[str, Any] | None, *, control_ready: bool = True) -> dict[str, Any]:
    """给状态快照附加前端操作所需的权威分类，不修改输入对象。"""
    enriched = dict(snapshot or {})
    system = dict(enriched.get("system") or {})
    current_state = snapshot_state(enriched)
    if system.get("protocol_version") == "2.0":
        source = enriched.get("_v2") or {}
        frame = source.get("status") or {}
        native = frame.get("payload") or {}
        run, safety = native.get("run") or {}, native.get("safety") or {}
        phase = run.get("state")
        operation_state = classify_state(phase, protocol_version="2.0")
        coherent = current_state == phase
        control_role = source.get("granted_role") == "control"
        ready = bool(control_ready and enriched.get("control_ready") and coherent and control_role)
        idle = phase == "idle" and run.get("run_id") is None
        no_trip = (
            not any(safety.get(key) for key in ("emergency_stop", "co_alarm", "overtemperature"))
            and safety.get("exhaust_ok") is True
        )
        permit = no_trip and safety.get("hardwired_permit") is True
        # fault_revision is a version, not a pending-fault flag. Offer an explicit
        # recovery request after safe completion; the board checks alarm evidence.
        revision = run.get("fault_revision")
        known_fault_revision = isinstance(revision, str) and revision.isdecimal() and int(revision) > 0
        safe_reset_context = (
            phase == "fault"  # A controlled reset may permit continuing safety disposal.
            or (phase == "completed" and run.get("safe_complete") is True)
            or (phase == "idle" and run.get("run_id") is None)
        )
        recovery = source.get("recovery")
        # This enrichment runs again for cache/HTTP/WS delivery. Recompute the
        # ownership gate from durable recovery evidence instead of overwriting
        # the adapter's refusal. Replay completeness governs reports separately.
        recovery_ack_allowed = recovery is None or (
            isinstance(recovery, dict) and recovery.get("review_state") == "bound"
        )
        system.update(
            {
                "operation_state": operation_state if coherent else "unknown",
                "state_policy_version": 2,
                "is_running": coherent and operation_state == "running",
                "can_start_test": bool(ready and idle and permit and native.get("active_recipe_digest")),
                "can_set_parameters": False,
                "can_activate_recipe": ready and idle,
                "can_stop_test": bool(
                    coherent
                    and control_role
                    and source.get("online")
                    and run.get("run_id")
                    and phase in {"preparing", "measuring", "safe_disposal", "cooling", "fault"}
                ),
                "can_ack_run": bool(
                    ready
                    and recovery_ack_allowed
                    and phase == "completed"
                    and run.get("safe_complete") is True
                    and no_trip
                    and source.get("alarms_reconciled") is True
                    and (enriched.get("alarm") or {}).get("ack_required") is False
                    and (enriched.get("alarm") or {}).get("latched_alarm_count") == 0
                ),
                "can_ack_alarm": ready,
                "can_reset_fault": ready and known_fault_revision and safe_reset_context and no_trip,
            }
        )
        enriched["system"] = system
        return enriched
    operation_state = classify_state(current_state)

    system.update(
        {
            "operation_state": operation_state,
            "state_policy_version": STATE_POLICY_VERSION,
            "is_running": operation_state == "running",
            "can_start_test": control_ready and operation_state == "idle",
            "can_set_parameters": control_ready and parameter_changes_allowed(current_state),
            "can_stop_test": operation_state in {"running", "fault", "unknown"},
        }
    )
    enriched["system"] = system
    return enriched
