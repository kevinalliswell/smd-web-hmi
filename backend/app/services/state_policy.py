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


def classify_state(state: str | None) -> str:
    """返回 ``idle`` / ``running`` / ``fault`` / ``unknown``。"""
    normalized = normalize_state(state)
    if normalized in _IDLE_STATES:
        return "idle"
    if "fault" in normalized:
        return "fault"
    if normalized in _RUNNING_STATES:
        return "running"
    return "unknown"


def parameter_changes_allowed(state: str | None) -> bool:
    """参数仅允许在明确的非运行态修改；未知状态一律拒绝。"""
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
