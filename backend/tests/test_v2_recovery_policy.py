"""Final cache/WS enrichment cannot undo reviewed recovery ownership gates."""

import copy

import pytest

from app.services.state_policy import enrich_status_snapshot


def snapshot(recovery, *, phase="completed"):
    return {
        "control_ready": True,
        "system": {"protocol_version": "2.0", "current_state": phase, "can_ack_run": False},
        "state_machine": {"current_state": phase},
        "alarm": {"ack_required": False, "latched_alarm_count": 0},
        "_v2": {
            "online": True,
            "granted_role": "control",
            "alarms_reconciled": True,
            "recovery": recovery,
            "status": {
                "payload": {
                    "run": {"state": phase, "run_id": "a" * 32, "safe_complete": phase == "completed"},
                    "safety": {"exhaust_ok": True, "hardwired_permit": True},
                }
            },
        },
    }


@pytest.mark.parametrize("review_state", ["unreviewed", "conflict"])
def test_recovery_ownership_gate_survives_repeated_final_enrichment(review_state):
    original = snapshot({"review_state": review_state, "replay_status": "complete"})
    before = copy.deepcopy(original)
    enriched = enrich_status_snapshot(enrich_status_snapshot(original))
    assert enriched["system"]["can_ack_run"] is False
    assert enriched["system"]["can_ack_alarm"] is True
    assert original == before


@pytest.mark.parametrize("replay_status", ["pending", "running", "failed", "complete"])
def test_bound_recovery_can_ack_safe_board_independently_of_report_replay(replay_status):
    enriched = enrich_status_snapshot(snapshot({"review_state": "bound", "replay_status": replay_status}))
    assert enriched["system"]["can_ack_run"] is True


def test_unknown_active_run_keeps_safety_stop_even_without_review():
    enriched = enrich_status_snapshot(
        snapshot({"review_state": "unreviewed", "replay_status": "not_bound"}, phase="measuring")
    )
    assert enriched["system"]["can_stop_test"] is True
    assert enriched["system"]["can_ack_run"] is False
