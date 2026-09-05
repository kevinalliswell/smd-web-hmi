"""源端序号契约的故障序列；不替代板端联调证明。"""

from copy import deepcopy

import pytest

from app.services.telemetry_integrity import advance_cursor, continuity_proven


def frame(sequence=10, boot="boot-a", device_time="2026-09-05T00:00:00Z"):
    return {
        "telemetry": {"boot_id": boot, "sequence": sequence},
        "_hostcomm": {"capabilities": ["telemetry_sequence_v1"], "device_timestamp": device_time},
        "temperature": {"furnace_pv_deg_c": 600},
        "measurement": {"displacement_mm": 30, "displacement_valid": True},
    }


@pytest.mark.parametrize(
    "second,issue",
    [
        (frame(12), "sequence_gap"),
        (frame(10), "sequence_duplicate"),
        (frame(9), "sequence_reordered"),
        (frame(11, boot="boot-b"), "device_restarted"),
        (frame(11, device_time="2026-09-04T23:59:00Z"), "device_clock_rollback"),
        (frame(True), "invalid_telemetry_identity"),
        (frame(11, device_time="broken"), "invalid_device_timestamp"),
    ],
)
def test_invalid_sequences_are_sticky(second, issue):
    cursor, _ = advance_cursor({}, frame())
    cursor, issues = advance_cursor(cursor, second)
    assert issue in issues
    assert not continuity_proven(cursor)


def test_contiguous_cursor_survives_serialization_and_does_not_count_msg_id():
    import json

    cursor, _ = advance_cursor({}, frame())
    cursor = json.loads(json.dumps(cursor))
    next_frame = frame(11)
    next_frame["_hostcomm"]["msg_id"] = 9999
    cursor, issues = advance_cursor(cursor, next_frame)
    assert issues == [] and continuity_proven(cursor)


def test_missing_capability_cannot_be_promoted_by_later_samples():
    first = frame()
    first["_hostcomm"]["capabilities"] = []
    cursor, _ = advance_cursor({}, first)
    cursor, _ = advance_cursor(cursor, frame(11))
    cursor, _ = advance_cursor(cursor, frame(12))
    assert not continuity_proven(cursor)


def test_legacy_callback_total_does_not_mislabel_new_experiment():
    first = frame()
    first["_hostcomm"]["dropped_callbacks"] = 9
    cursor, issues = advance_cursor({}, first)
    assert issues == []
    second = frame(11)
    second["_hostcomm"]["dropped_callbacks"] = 10
    _, issues = advance_cursor(cursor, second)
    assert issues == ["callback_gap"]


@pytest.mark.parametrize("cursor", [{"verified_samples": "bad"}, {"gap_seen": "yes"}, {"unsupported_seen": 1}])
def test_malformed_persisted_cursor_fails_closed(cursor):
    current, issues = advance_cursor(deepcopy(cursor), frame())
    assert "invalid_persisted_cursor" in issues
    assert not continuity_proven(current)
