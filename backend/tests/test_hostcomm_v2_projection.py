"""V2 domain projection must not turn stale samples or terminal runs into control permission."""

import copy
import json
from pathlib import Path

from app.hostcomm.v2_contract.codec import digest
from app.hostcomm.v2_projection import project_status
from app.services.state_policy import classify_state, enrich_status_snapshot

VECTORS = json.loads(
    (Path(__file__).resolve().parents[2] / "contracts/hostcomm/v2/vectors.json").read_text(encoding="utf-8")
)


def example(name):
    return copy.deepcopy(next(item["value"] for item in VECTORS["valid_messages"] if item["name"] == name))


def project(status=None, telemetry=None, profile=None, **options):
    return project_status(
        status or example("status_snapshot"),
        telemetry or example("telemetry"),
        profile or example("profile_snapshot"),
        hello_payload=example("hello_ack")["payload"],
        online=True,
        control_ready=True,
        status_receipt={"received_at": "2026-09-06T01:02:03.004Z", "received_monotonic": 99.0},
        telemetry_receipt={"received_at": "2026-09-06T01:02:03.004Z", "received_monotonic": 99.0},
        now_monotonic=options.pop("now_monotonic", 100.0),
        **options,
    )


def test_fixed_point_projection_keeps_raw_quality_without_fake_legacy_capabilities():
    snapshot = project()
    assert snapshot["temperature"]["furnace_pv_deg_c"] == 501
    assert snapshot["gas"]["n2_pv_l_min"] == 5
    assert snapshot["measurement"]["displacement_mm"] == 10
    assert snapshot["_v2"]["telemetry"]["payload"]["values"]["furnace_mc"]["value"] == 501000
    assert "recipe_v1" not in snapshot["_hostcomm"]["capabilities"]
    assert snapshot["system"]["protocol_version"] == "2.0"


def test_invalid_or_stale_point_is_none_in_current_ui_but_retained_raw():
    telemetry = example("telemetry")
    telemetry["payload"]["values"]["burden_mc"].update(value=495000, quality="invalid")
    snapshot = project(telemetry=telemetry)
    assert snapshot["measurement"]["burden_temp_deg_c"] is None
    assert snapshot["measurement"]["burden_temp_valid"] is False
    assert snapshot["_v2"]["telemetry"]["payload"]["values"]["burden_mc"]["value"] == 495000
    stale = project(now_monotonic=110.0)
    assert stale["temperature"]["furnace_pv_deg_c"] is None
    assert stale["system"]["can_start_test"] is False
    assert stale["data_fresh"] is False


def test_previous_run_or_revision_cannot_supply_current_values_or_phase():
    for field, value in (("run_id", "f" * 32), ("state_revision", "11")):
        telemetry = example("telemetry")
        telemetry["payload"][field] = value
        snapshot = project(telemetry=telemetry)
        assert snapshot["temperature"]["furnace_pv_deg_c"] is None
        assert snapshot["state_machine"]["current_state"] == "measuring"


def test_completed_v2_run_requires_ack_instead_of_restarting():
    status = example("status_snapshot")
    status["payload"]["run"].update(
        state="completed", safe_complete=True, safe_boundary={"boot_id": status["boot_id"], "sample_seq": "99"}
    )
    snapshot = project(status=status)
    assert snapshot["system"]["operation_state"] == "terminal"
    assert snapshot["system"]["can_start_test"] is False
    assert snapshot["system"]["can_set_parameters"] is False
    assert classify_state("completed") == "idle"  # Existing 1.0 interpretation is deliberately preserved.
    assert classify_state("completed", protocol_version="2.0") == "terminal"


def test_v2_running_phases_and_unacknowledged_idle_identity_fail_closed():
    for phase in ("preparing", "measuring", "safe_disposal", "cooling"):
        assert classify_state(phase, protocol_version="2.0") == "running"
    status = example("status_snapshot")
    status["payload"]["run"]["state"] = "idle"
    snapshot = project(status=status)
    enriched = enrich_status_snapshot(snapshot, control_ready=True)
    assert enriched["system"]["can_start_test"] is False
    assert enriched["system"]["can_set_parameters"] is False


def test_latched_first_drip_is_display_only_not_valid_td_event():
    telemetry = example("telemetry")
    telemetry["payload"]["first_drip_latched"] = True
    snapshot = project(telemetry=telemetry)
    assert snapshot["measurement"]["first_drip_latched"] is True
    assert snapshot["measurement"]["first_drip_valid"] is False


def test_ready_idle_requires_approved_profile_and_own_current_lease():
    status, profile = example("status_snapshot"), example("profile_snapshot")
    profile["payload"]["approved"] = True  # Synthetic permission test only, never an engineering approval.
    profile["payload"]["profile_digest"] = digest(
        {k: v for k, v in profile["payload"].items() if k != "profile_digest"}
    )
    status["payload"]["profile_digest"] = profile["payload"]["profile_digest"]
    status["payload"]["safety"].update(profile_approved=True, hardwired_permit=True)
    status["payload"]["run"].update(
        state="idle",
        run_id=None,
        recipe_digest=None,
        safety_profile_digest=None,
        stage_index=None,
        measurement_start=None,
    )
    snapshot = project(status=status, profile=profile)
    assert snapshot["system"]["can_start_test"] is True
    assert snapshot["system"]["can_activate_recipe"] is True
    assert snapshot["system"]["can_set_parameters"] is False
    status["payload"]["lease_owner_session_id"] = "f" * 32
    snapshot = project(status=status, profile=profile)
    assert snapshot["system"]["can_start_test"] is False
    status["payload"].update(
        lease_id=None, lease_owner_controller_id=None, lease_owner_session_id=None, lease_expires_uptime_ms=None
    )
    snapshot = project(status=status, profile=profile)
    assert snapshot["system"]["can_activate_recipe"] is True
    assert snapshot["system"]["control_lease_acquire_required"] is True


def test_fault_reset_permission_covers_completed_and_idle_without_resuming_an_unsafe_run():
    def permissions(phase, *, safe=False, run_id="1" * 32, revision="7", trip=False):
        return enrich_status_snapshot(
            {
                "control_ready": True,
                "system": {"protocol_version": "2.0", "current_state": phase},
                "state_machine": {"current_state": phase},
                "_v2": {
                    "granted_role": "control",
                    "online": True,
                    "status": {
                        "payload": {
                            "run": {
                                "state": phase,
                                "run_id": run_id,
                                "fault_revision": revision,
                                "safe_complete": safe,
                            },
                            "safety": {
                                "emergency_stop": trip,
                                "co_alarm": False,
                                "overtemperature": False,
                                "exhaust_ok": True,
                            },
                        }
                    },
                },
            }
        )["system"]

    assert permissions("completed", safe=True)["can_reset_fault"]
    assert permissions("idle", run_id=None)["can_reset_fault"]
    assert permissions("fault", run_id=None)["can_reset_fault"]
    assert not permissions("completed", safe=True, trip=True)["can_reset_fault"]
    assert not permissions("completed", safe=True, revision="0")["can_reset_fault"]
    assert permissions("fault", safe=False)["can_reset_fault"]
    assert not permissions("idle", safe=True)["can_reset_fault"]
    for phase in ("preparing", "measuring", "safe_disposal", "cooling", "maintenance"):
        assert not permissions(phase, safe=True)["can_reset_fault"]
