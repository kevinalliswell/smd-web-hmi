"""统一状态策略回归测试（issue #15）。"""

from app.services.state_policy import classify_state, enrich_status_snapshot


def test_state_classification_is_case_and_separator_insensitive():
    assert classify_state("Standby") == "idle"
    assert classify_state("gas-switch") == "running"
    assert classify_state("HOLDING") == "running"
    assert classify_state("End") == "running"
    assert classify_state("Complete") == "idle"
    assert classify_state("Fault/Purge") == "fault"
    assert classify_state("vendor-new-state") == "unknown"


def test_status_snapshot_carries_authoritative_operation_policy():
    raw = {
        "system": {"current_state": "GasSwitch", "fw_version": "FW-1"},
        "state_machine": {"current_state": "GasSwitch"},
    }

    enriched = enrich_status_snapshot(raw)

    assert enriched is not raw
    assert enriched["system"]["operation_state"] == "running"
    assert enriched["system"]["is_running"] is True
    assert enriched["system"]["can_start_test"] is False
    assert enriched["system"]["can_stop_test"] is True
    assert "operation_state" not in raw["system"]


def test_unknown_state_fails_safe_for_operator_controls():
    enriched = enrich_status_snapshot({"system": {"current_state": "vendor-new-state"}})
    system = enriched["system"]
    assert system["operation_state"] == "unknown"
    assert system["can_start_test"] is False
    assert system["can_stop_test"] is True
