"""Source-order and event-time report evidence for HostComm 2.0 archives."""

import copy
import json
from uuid import uuid4

import pytest

from app.db.models import SamplePoint, TestSession
from app.hostcomm.v2_contract.messages import Measurements
from app.hostcomm.v2_simulator import synthetic_profile
from app.services.report_service import compute_metrics_from_database

BOOT = "b" * 32
RUN = "a" * 32
PROFILE = synthetic_profile(approved=True)


def reference(sequence, boot=BOOT):
    return {"boot_id": boot, "sample_seq": str(sequence)}


def telemetry(sequence, furnace, burden, height, pressure=0, *, boot=BOOT, latched=False):
    values = {name: {"value": 0, "quality": "good", "age_ms": 0} for name in Measurements.model_fields}
    for key, value in {
        "furnace_mc": furnace * 1000,
        "furnace_setpoint_mc": furnace * 1000,
        "burden_mc": burden * 1000,
        "displacement_um": height * 1000,
        "pressure_drop_pa": pressure,
    }.items():
        values[key]["value"] = value
    return {
        "sample": reference(sequence, boot),
        "sample_uptime_ms": str(sequence),
        "sample_timestamp": None,
        "run_id": RUN,
        "state_revision": "3",
        "values": values,
        "first_drip_latched": latched,
        "first_drip_detector_quality": "good",
    }


def drip(sequence, temperature=1290, **overrides):
    return {
        "boot_id": BOOT,
        "kind": "first_drip",
        "event_id": uuid4().hex,
        "event_seq": "9",
        "run_id": RUN,
        "event_uptime_ms": str(sequence),
        "event_timestamp": None,
        "sample": reference(sequence),
        "burden_mc": {"value": temperature * 1000, "quality": "good", "age_ms": 0},
        "is_valid": True,
        **overrides,
    }


async def archive(db, points, *, start=9, end=12, events=None, changes=None):
    v2 = {
        "run_id": RUN,
        "measurement_start": reference(start),
        "measurement_end": reference(end),
        "safe_boundary": reference(end + 2),
        "measurement_complete": True,
        "state": "completed",
        "outcome": "valid_candidate",
        "profile_snapshot": copy.deepcopy(PROFILE),
        "safety_profile_digest": PROFILE["profile_digest"],
        "first_drip_events": events or [],
    }
    v2.update(changes or {})
    test = TestSession(
        test_id="v2-metrics",
        operator_id="tester",
        start_time="2026-09-06T00:00:00Z",
        original_height_mm=20,
        measurement_completed_at="2026-09-06T00:00:01Z",
        data_integrity="complete",
        measurement_basis_json=json.dumps({"v2": v2, "detector_verified": True}),
    )
    db.add(test)
    for item in points:
        db.add(
            SamplePoint(
                test_id=test.test_id,
                ts="2026-09-06T00:00:02Z",  # UTC unavailable at device; reception time cannot order sources.
                source="hostcomm_v2_log",
                source_boot_id=item["sample"]["boot_id"],
                source_sequence=item["sample"]["sample_seq"],
                source_run_id=item["run_id"],
                source_uptime_ms=item["sample_uptime_ms"],
                furnace_pv=9999,  # Deliberately wrong projections must not replace raw fixed-point evidence.
                burden_temp=9999,
                burden_temp_v=1,
                displacement=9999,
                displacement_v=1,
                delta_p=9999,
                delta_p_v=1,
                ext_json=json.dumps({"v2": {"telemetry": item}}),
            )
        )
    await db.commit()
    return test


async def test_v2_reports_use_source_order_h600_exact_boundaries_and_event_temperature(db_session):
    rows = [
        telemetry(12, 1600, 1580, 20, 1000, latched=True),
        telemetry(10, 900, 890, 28, 100),
        telemetry(13, 1700, 1650, 0, 9000, latched=True),
        telemetry(9, 600, 590, 30),
        telemetry(11, 1300, 1300, 22, 500, latched=True),
        telemetry(8, 700, 690, 100, 8000),
    ]
    await archive(db_session, rows, events=[drip(11, 1290)])
    result = await compute_metrics_from_database(db_session, "v2-metrics", 20)
    assert result["reference_displacement_mm"] == 30
    assert result["t10"] == 890 and result["t40"] == 1300
    assert result["ts"] == 1300 and result["td_drip_temp"] == 1290
    assert result["delta_h_mm"] == 0  # Td displacement is source sample 11, not the late latched frame 12.
    assert result["furnace_pv_max"] == 1600 and result["delta_p_max"] == 1000
    assert result["sample_count"] == 6 and result["excluded_sample_count"] == 2
    assert result["algorithm_version"] != "gb34211-2017/2"


async def test_latched_first_drip_without_event_is_unknown_never_no_drip_1580(db_session):
    await archive(db_session, [telemetry(9, 600, 590, 30), telemetry(10, 1600, 1580, 20, latched=True)], end=10)
    result = await compute_metrics_from_database(db_session, "v2-metrics", 20)
    assert result["td_drip_temp"] is None
    assert "first_drip_event_missing" in result["limitations"]


@pytest.mark.parametrize("quality,age", [("stale", 0), ("good", 3001), ("invalid", 0)])
async def test_raw_channel_quality_and_profile_age_limit_override_good_legacy_columns(db_session, quality, age):
    invalid = telemetry(10, 1000, 990, 20, 500)
    invalid["values"]["burden_mc"].update(quality=quality, age_ms=age)
    await archive(db_session, [telemetry(9, 600, 590, 30), invalid], end=10)
    result = await compute_metrics_from_database(db_session, "v2-metrics", 20)
    assert result["t10"] is None and result["ts"] is None
    assert result["invalid_sample_count"] == 1


async def test_v2_source_sequence_uint64_is_sorted_without_signed_cast_or_arrival_id(db_session):
    maximum = 18446744073709551615
    await archive(
        db_session,
        [telemetry(maximum, 1300, 1290, 20, 500), telemetry(maximum - 1, 600, 590, 30)],
        start=maximum - 1,
        end=maximum,
        changes={"safe_boundary": reference(maximum)},
    )
    result = await compute_metrics_from_database(db_session, "v2-metrics", 20)
    assert result["reference_displacement_mm"] == 30 and result["t40"] == 1290


async def test_missing_source_sample_breaks_h600_interpolation_and_no_drip_proof(db_session):
    await archive(db_session, [telemetry(9, 590, 580, 31), telemetry(11, 610, 600, 29)], end=11)
    result = await compute_metrics_from_database(db_session, "v2-metrics", 20)
    assert result["reference_displacement_mm"] is None
    assert "transport_data_gap" in result["limitations"]


async def test_boot_reuse_sequence_cannot_enter_the_original_measurement_window(db_session):
    await archive(
        db_session,
        [telemetry(9, 600, 590, 30), telemetry(10, 900, 890, 28), telemetry(10, 1600, 1580, 0, 9000, boot="c" * 32)],
        end=10,
    )
    result = await compute_metrics_from_database(db_session, "v2-metrics", 20)
    assert result["burden_temp_max"] == 890 and result["excluded_sample_count"] == 1


async def test_manually_marked_complete_does_not_replace_verified_source_log_proof(db_session):
    await archive(db_session, [telemetry(9, 600, 590, 30), telemetry(10, 1600, 1580, 20)], end=10)
    result = await compute_metrics_from_database(db_session, "v2-metrics", 20)
    assert result["td_drip_temp"] is None and result["measurement_data_integrity"] == "unknown"


@pytest.mark.parametrize(
    "changes,limitation",
    [
        ({"measurement_end": None}, "measurement_source_boundary_missing_or_invalid"),
        ({"measurement_end": reference(12, "c" * 32)}, "measurement_boundary_cross_boot"),
        ({"measurement_start": reference(13)}, "measurement_source_boundary_missing_or_invalid"),
    ],
)
async def test_unproven_or_cross_boot_measurement_window_never_falls_back_to_arrival_order(
    db_session, changes, limitation
):
    await archive(db_session, [telemetry(9, 600, 590, 30), telemetry(12, 1600, 1580, 20)], changes=changes)
    result = await compute_metrics_from_database(db_session, "v2-metrics", 20)
    assert result["t40"] is None and result["td_drip_temp"] is None
    assert limitation in result["limitations"]


async def test_profile_digest_mismatch_rejects_aged_values_and_no_drip_proof(db_session):
    profile = copy.deepcopy(PROFILE)
    profile["resources"]["channel_freshness_ms"]["burden_mc"] = 999999
    item = telemetry(10, 1600, 1580, 20)
    item["values"]["burden_mc"]["age_ms"] = 3001
    await archive(db_session, [telemetry(9, 600, 590, 30), item], end=10, changes={"profile_snapshot": profile})
    result = await compute_metrics_from_database(db_session, "v2-metrics", 20)
    assert result["t40"] is None and result["td_drip_temp"] is None
    assert "freshness_profile_missing_or_invalid" in result["limitations"]


async def test_original_event_temperature_survives_missing_matching_sample_without_invented_height(db_session):
    await archive(
        db_session, [telemetry(9, 600, 590, 30), telemetry(11, 1600, 1580, 20, latched=True)], end=11, events=[drip(10)]
    )
    result = await compute_metrics_from_database(db_session, "v2-metrics", 20)
    assert result["td_drip_temp"] == 1290 and result["delta_h_mm"] is None
    assert "first_drip_sample_missing" in result["limitations"]


@pytest.mark.parametrize(
    "event_change", [{"is_valid": False}, {"burden_mc": {"value": 1290000, "quality": "good", "age_ms": 3001}}]
)
async def test_invalid_or_stale_first_drip_event_does_not_create_td(db_session, event_change):
    await archive(
        db_session,
        [telemetry(9, 600, 590, 30), telemetry(10, 1600, 1580, 20, latched=True)],
        end=10,
        events=[drip(10, **event_change)],
    )
    result = await compute_metrics_from_database(db_session, "v2-metrics", 20)
    assert result["td_drip_temp"] is None
    assert "invalid_first_drip_event" in result["limitations"]


async def test_report_preserves_raw_evidence_and_profile_fallback_is_digest_checked(db_session):
    points = [telemetry(9, 600, 590, 30), telemetry(10, 1600, 1580, 20)]
    test = await archive(db_session, points, end=10)
    basis = json.loads(test.measurement_basis_json)
    profile = basis["v2"].pop("profile_snapshot")
    test.measurement_basis_json = json.dumps(basis)
    test.recipe_snapshot_json = json.dumps({"safety_profile": profile})
    await db_session.commit()
    before = test.measurement_basis_json
    result = await compute_metrics_from_database(db_session, "v2-metrics", 20)
    assert "freshness_profile_missing_or_invalid" not in result["limitations"]
    assert result["t40"] == 1580
    await db_session.refresh(test)
    assert test.measurement_basis_json == before


@pytest.mark.parametrize("fault", [None, "log_gap", "invalid_detector", "missing_drip_event"])
async def test_real_source_store_and_projector_prove_no_drip_only_after_verified_backfill(db_session, fault):
    import base64
    import hashlib

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.db.v2_models import V2RunBinding, V2SourceRecord
    from app.hostcomm.v2_contract.codec import canonical_bytes
    from app.services.v2_archive import V2ArchiveProjector
    from app.services.v2_source_logs import V2SourceLogStore

    device_id, log_id = "d" * 32, "e" * 32
    profile_basis = {"v2": {"profile_snapshot": copy.deepcopy(PROFILE)}}
    db_session.add(
        TestSession(
            test_id="proven-source",
            operator_id="tester",
            original_height_mm=20,
            start_time="2026-09-06T00:00:00Z",
            measurement_basis_json=json.dumps(profile_basis),
        )
    )
    db_session.add(
        V2RunBinding(
            device_id=device_id,
            run_id=RUN,
            test_id="proven-source",
            recipe_digest="f" * 64,
            profile_digest=PROFILE["profile_digest"],
            created_at="2026-09-06T00:00:00Z",
        )
    )
    await db_session.commit()
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    store = V2SourceLogStore(factory, device_id=device_id, on_record=V2ArchiveProjector(device_id).on_record)
    points = [telemetry(1, 600, 590, 30), telemetry(2, 900, 890, 28), telemetry(3, 1600, 1580, 20)]
    if fault == "invalid_detector":
        points[1]["first_drip_detector_quality"] = "invalid"
    if fault == "missing_drip_event":
        points[2]["first_drip_latched"] = True
    run = {
        "run_id": RUN,
        "state": "completed",
        "state_revision": "4",
        "outcome": "valid_candidate",
        "recipe_digest": "f" * 64,
        "safety_profile_digest": PROFILE["profile_digest"],
        "stage_index": 1,
        "measurement_complete": True,
        "safe_complete": True,
        "measurement_start": reference(1),
        "measurement_end": reference(3),
        "safe_boundary": reference(3),
        "fault_revision": "0",
    }
    event = {
        "kind": "run_changed",
        "event_id": uuid4().hex,
        "event_seq": "1",
        "run_id": RUN,
        "event_uptime_ms": "4",
        "event_timestamp": None,
        "run": run,
    }
    records = [
        {"record_type": "sample", "log_id": log_id, "record_seq": str(i), "data": point}
        for i, point in enumerate(points, 1)
    ]
    records.append(
        {
            "record_type": "event",
            "log_id": log_id,
            "record_seq": "4",
            "boot_id": BOOT,
            "uptime_ms": "4",
            "timestamp": None,
            "data": event,
        }
    )
    for record in [records[2], records[0], records[1], records[3]]:
        await store.ingest_live(
            {
                "protocol_version": "2.0",
                "msg_id": uuid4().hex,
                "reply_to": None,
                "session_id": "c" * 32,
                "boot_id": BOOT,
                "timestamp": None,
                "uptime_ms": "4",
                "type": "telemetry" if record["record_type"] == "sample" else "event",
                "payload": record["data"],
            }
        )
    before = await compute_metrics_from_database(db_session, "proven-source", 20)
    assert before["measurement_data_integrity"] != "complete" and before["td_drip_temp"] is None
    await db_session.rollback()
    requested = {"log_id": log_id, "first_record_seq": "1", "last_record_seq": "4"}
    query = {"transfer_id": uuid4().hex, "requested": requested, "max_records": 4, "max_bytes": 16777216}
    retained = [record for record in records if fault != "log_gap" or record["record_seq"] != "2"]
    raw = b"".join(canonical_bytes(record) + b"\n" for record in retained)
    await store.begin(query)
    for index, offset in enumerate(range(0, len(raw), 1536)):
        chunk = raw[offset : offset + 1536]
        await store.append_chunk(
            {
                "transfer_id": query["transfer_id"],
                "log_id": log_id,
                "chunk_index": index,
                "snapshot_highwater": "4",
                "offset": offset,
                "data_b64": base64.b64encode(chunk).decode(),
                "chunk_digest": hashlib.sha256(chunk).hexdigest(),
            }
        )
    await store.finish(
        {
            "transfer_id": query["transfer_id"],
            "requested": requested,
            "snapshot_highwater": "4",
            "available_first_seq": "1",
            "available_last_seq": "4",
            "status": "partial" if fault == "log_gap" else "complete",
            "byte_length": len(raw),
            "content_digest": hashlib.sha256(raw).hexdigest(),
            "record_count": len(retained),
            "missing": (
                [{"log_id": log_id, "first_record_seq": "2", "last_record_seq": "2", "reason": "storage_fault"}]
                if fault == "log_gap"
                else []
            ),
        }
    )
    after = await compute_metrics_from_database(db_session, "proven-source", 20)
    assert after["measurement_data_integrity"] == ("complete" if fault is None else "incomplete")
    assert after["td_drip_temp"] == (1580 if fault is None else None)
    test = await db_session.scalar(select(TestSession).where(TestSession.test_id == "proven-source"))
    basis = json.loads(test.measurement_basis_json)
    assert "detector_verified" not in basis and test.data_integrity == "unknown"
    source = await db_session.scalar(
        select(V2SourceRecord).where(V2SourceRecord.record_type == "sample", V2SourceRecord.source_seq == "1")
    )
    assert source.payload_bytes == canonical_bytes(points[0])
