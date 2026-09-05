import json

import pytest
from sqlalchemy import select

from app.db.models import SamplePoint, TestSession
from app.services.logging_service import append_sample_point
from app.services.test_session_service import advance_test_session


def snapshot(*, seq=1, boot="boot-a", complete=False, safe=False, temp=590, furnace=600, caps=True):
    return {
        "state_machine": {
            "test_id": "RUN-I",
            "current_state": "Heating",
            "measurement_complete": complete,
            "safe_complete": safe,
        },
        "temperature": {"furnace_pv_deg_c": furnace},
        "measurement": {
            "burden_temp_deg_c": temp,
            "burden_temp_valid": True,
            "displacement_mm": 30,
            "displacement_valid": True,
            "delta_p_pa": 0,
            "delta_p_valid": True,
            "first_drip": False,
            "first_drip_valid": True,
        },
        "telemetry": {"boot_id": boot, "sequence": seq},
        "_hostcomm": {
            "capabilities": ["run_lifecycle_v1", "measurement_events_v1", "telemetry_sequence_v1"] if caps else [],
            "device_timestamp": f"2026-09-05T00:00:{seq:02d}Z",
            "received_at": f"2026-09-05T00:00:{seq:02d}Z",
        },
    }


async def run_row(db):
    row = TestSession(test_id="RUN-I", operator_id="op", start_time="2026-09-05T00:00:00Z")
    db.add(row)
    await db.commit()
    return row


async def test_incomplete_flag_commits_even_without_lifecycle_capability(db_session):
    row = await run_row(db_session)
    frame = snapshot(caps=False)
    frame["_hmi"] = {"persistence_failures": 1}
    await advance_test_session(db_session, "RUN-I", frame)
    await db_session.rollback()
    await db_session.refresh(row)
    assert row.data_integrity == "incomplete"


async def test_measurement_completion_is_sticky_and_basis_tolerates_invalid_json(db_session):
    row = await run_row(db_session)
    row.measurement_basis_json = "[]"
    await db_session.commit()
    await append_sample_point(db_session, "RUN-I", snapshot(complete=True))
    await advance_test_session(db_session, "RUN-I", snapshot(complete=True))
    await append_sample_point(db_session, "RUN-I", snapshot(seq=2))
    await advance_test_session(db_session, "RUN-I", snapshot(seq=2))
    assert row.phase == "safe_disposal"
    assert row.measurement_completed_at is not None
    assert row.data_integrity == "incomplete"
    assert json.loads(row.measurement_basis_json)["measurement_end_sample_id"] is not None


async def test_sequence_gap_is_persisted_with_raw_device_evidence(db_session):
    row = await run_row(db_session)
    await append_sample_point(db_session, "RUN-I", snapshot(seq=5))
    frame = snapshot(seq=7)
    await append_sample_point(db_session, "RUN-I", frame)
    assert row.data_integrity == "incomplete"
    latest = await db_session.scalar(select(SamplePoint).order_by(SamplePoint.id.desc()))
    extra = json.loads(latest.ext_json)
    assert extra["telemetry"] == frame["telemetry"]
    assert extra["_hostcomm"]["device_timestamp"] == frame["_hostcomm"]["device_timestamp"]
    assert "sequence_gap" in extra["_hmi"]["telemetry_issues"]


async def test_verified_sequence_can_complete_only_from_observed_measurement_start(db_session):
    row = await run_row(db_session)
    await append_sample_point(db_session, "RUN-I", snapshot())
    await advance_test_session(db_session, "RUN-I", snapshot())
    end = snapshot(seq=2, complete=True, temp=1580, furnace=1600)
    await append_sample_point(db_session, "RUN-I", end)
    await advance_test_session(db_session, "RUN-I", end)
    assert row.data_integrity == "complete"


async def test_sample_and_cursor_can_be_rolled_back_as_one_transaction(db_session):
    row = await run_row(db_session)
    await append_sample_point(db_session, "RUN-I", snapshot(), commit=False)
    await db_session.rollback()
    await db_session.refresh(row)
    assert row.measurement_basis_json is None
    assert await db_session.scalar(select(SamplePoint.id)) is None


async def test_malformed_numeric_reading_does_not_destroy_raw_snapshot(db_session):
    await run_row(db_session)
    frame = snapshot()
    frame["measurement"]["delta_p_pa"] = {"malformed": "value"}
    await append_sample_point(db_session, "RUN-I", frame)
    point = await db_session.scalar(select(SamplePoint))
    assert point.delta_p is None
    assert json.loads(point.ext_json)["measurement"]["delta_p_pa"] == {"malformed": "value"}


async def test_cooling_gap_marks_run_incomplete_without_erasing_complete_measurement(db_session):
    from app.services.report_service import compute_metrics_from_database

    row = await run_row(db_session)
    for frame in [snapshot(), snapshot(seq=2, complete=True, temp=1580, furnace=1600)]:
        await append_sample_point(db_session, "RUN-I", frame, commit=False)
        await advance_test_session(db_session, "RUN-I", frame)
    cooling = snapshot(seq=4, temp=190, furnace=900, safe=True)
    cooling["state_machine"]["current_state"] = "Cooling"
    await append_sample_point(db_session, "RUN-I", cooling, commit=False)
    await advance_test_session(db_session, "RUN-I", cooling)
    assert row.data_integrity == "incomplete"
    result = await compute_metrics_from_database(db_session, "RUN-I", 20)
    assert result["td_drip_temp"] == 1580
    assert result["measurement_data_integrity"] == "complete"
    assert result["excluded_sample_count"] == 1


async def test_missing_measurement_start_cannot_be_promoted_to_complete(db_session):
    row = await run_row(db_session)
    for frame in [snapshot(furnace=700), snapshot(seq=2, complete=True, temp=1580, furnace=1600)]:
        await append_sample_point(db_session, "RUN-I", frame, commit=False)
        await advance_test_session(db_session, "RUN-I", frame)
    assert row.measurement_completed_at is not None
    assert row.data_integrity == "unknown"


async def test_new_database_session_detects_gap_after_hmi_restart(db_session):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    row = await run_row(db_session)
    await append_sample_point(db_session, "RUN-I", snapshot(seq=10))
    new_session = async_sessionmaker(db_session.bind, expire_on_commit=False)
    async with new_session() as restarted:
        await append_sample_point(restarted, "RUN-I", snapshot(seq=12))
    await db_session.refresh(row)
    assert row.data_integrity == "incomplete"
    assert json.loads(row.measurement_basis_json)["telemetry"]["sequence"] == 12
