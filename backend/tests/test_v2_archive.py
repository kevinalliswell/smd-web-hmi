"""V2 source projections preserve run identity, source boundaries and transaction ownership."""

import copy
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import AlarmLog, Base, EventLog, SamplePoint
from app.db.models import TestSession as Experiment
from app.db.v2_models import V2AlarmProjection, V2RunBinding, V2SourceRecord
from app.services.v2_archive import V2ArchiveProjector
from app.services.v2_source_logs import V2SourceLogStore

VECTORS = json.loads((Path(__file__).resolve().parents[2] / "contracts/hostcomm/v2/vectors.json").read_text())
DEVICE = "a" * 32


def message(name):
    return copy.deepcopy(next(v["value"] for v in VECTORS["valid_messages"] if v["name"] == name))


def run_state():
    return message("status_snapshot")["payload"]["run"]


@pytest.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/archive.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    run = run_state()
    async with factory() as db, db.begin():
        db.add(
            Experiment(
                test_id="local-test",
                operator_id="alice",
                start_time="2026-09-06T00:00:00Z",
                measurement_basis_json=json.dumps({"sample_metadata": {"height_mm": 10.5}}),
            )
        )
        db.add(
            V2RunBinding(
                device_id=DEVICE,
                run_id=run["run_id"],
                test_id="local-test",
                recipe_digest=run["recipe_digest"],
                profile_digest=run["safety_profile_digest"],
                created_at="2026-09-06T00:00:00Z",
            )
        )
    yield factory
    await engine.dispose()


async def test_unknown_run_keeps_only_raw_evidence(factory):
    frame = message("telemetry")
    frame["payload"]["run_id"] = "b" * 32
    store = V2SourceLogStore(factory, device_id=DEVICE, on_record=V2ArchiveProjector(DEVICE).on_record)
    await store.ingest_live(frame)
    async with factory() as db:
        assert not (await db.scalars(select(SamplePoint))).all()
        raw = await db.scalar(select(V2SourceRecord))
        assert raw.run_id == "b" * 32 and raw.archived == 0


async def test_sample_projection_keeps_source_sequence_quality_and_units(factory):
    frame = message("telemetry")
    frame["payload"]["sample"]["sample_seq"] = str(2**63 + 7)
    frame["payload"]["values"]["burden_mc"].update(quality="invalid", value=999999)
    frame["payload"]["sample_timestamp"] = None
    projector = V2ArchiveProjector(DEVICE)
    store = V2SourceLogStore(factory, device_id=DEVICE, on_record=projector.on_record)
    await store.ingest_live(frame)
    async with factory() as db:
        row = await db.scalar(select(SamplePoint))
        assert row.source_sequence == str(2**63 + 7)
        assert row.source_boot_id == frame["boot_id"] and row.source_run_id == frame["payload"]["run_id"]
        assert row.source_uptime_ms == "12000"
        assert row.furnace_pv == 501 and row.n2_pv == 5 and row.displacement == 10
        assert row.burden_temp is None and row.burden_temp_v == 0
        extra = json.loads(row.ext_json)
        assert extra["v2"]["telemetry"]["values"]["burden_mc"]["value"] == 999999
        assert extra["v2"]["timestamp_basis"] == "received_at"


async def test_measurement_end_does_not_close_cooling_and_source_safe_end_does(factory):
    projector = V2ArchiveProjector(DEVICE)
    frame = message("run_changed")
    run = frame["payload"]["run"]
    run.update(
        state="safe_disposal",
        state_revision="20",
        measurement_complete=True,
        outcome="valid_candidate",
        measurement_end={"boot_id": frame["boot_id"], "sample_seq": "99"},
    )
    store = V2SourceLogStore(factory, device_id=DEVICE, on_record=projector.on_record)
    await store.ingest_live(frame)
    async with factory() as db:
        row = await db.scalar(select(Experiment))
        assert row.measurement_completed_at is not None and row.end_time is None
        assert row.phase == "safe_disposal"
        basis = json.loads(row.measurement_basis_json)
        assert basis["v2"]["measurement_end"]["sample_seq"] == "99"
        assert "measurement_end_sample_id" not in basis
    frame["payload"].update(event_id="c" * 32, event_seq="4", event_timestamp="2026-09-06T02:00:00.000Z")
    run.update(
        state="completed",
        state_revision="21",
        safe_complete=True,
        safe_boundary={"boot_id": frame["boot_id"], "sample_seq": "120"},
    )
    await store.ingest_live(frame)
    async with factory() as db:
        row = await db.scalar(select(Experiment))
        assert row.end_time == row.safety_completed_at == "2026-09-06T02:00:00+00:00"
        assert row.phase == "completed" and row.end_reason == "completed"
        assert not (await db.scalars(select(SamplePoint))).all()


async def test_status_boundary_reconciliation_does_not_invent_samples(factory):
    frame = message("status_snapshot")
    frame["payload"]["run"].update(
        state="fault",
        outcome="invalid",
        safe_complete=True,
        safe_boundary={"boot_id": frame["boot_id"], "sample_seq": "100"},
    )
    async with factory() as db, db.begin():
        assert await V2ArchiveProjector(DEVICE).reconcile_status(db, frame["payload"], boot_id=frame["boot_id"])
    async with factory() as db:
        row = await db.scalar(select(Experiment))
        assert row.end_time is not None and row.end_reason == "invalid"
        assert row.data_integrity == "incomplete"
        assert not (await db.scalars(select(SamplePoint))).all()
        assert json.loads(row.measurement_basis_json)["v2"]["safe_boundary"]["sample_seq"] == "100"


async def test_historical_run_state_cannot_regress_completed_archive(factory):
    projector = V2ArchiveProjector(DEVICE)
    frame = message("status_snapshot")
    run = frame["payload"]["run"]
    run.update(
        state="completed",
        state_revision="1",
        outcome="aborted",
        safe_complete=True,
        safe_boundary={"boot_id": "d" * 32, "sample_seq": "4"},
    )
    async with factory() as db, db.begin():
        await projector.reconcile_status(db, frame["payload"], boot_id="d" * 32)
    older = message("run_changed")
    store = V2SourceLogStore(factory, device_id=DEVICE, on_record=projector.on_record)
    await store.ingest_live(older)
    async with factory() as db:
        row = await db.scalar(select(Experiment))
        assert row.phase == "completed"
        assert json.loads(row.measurement_basis_json)["v2"]["safe_boundary"]["boot_id"] == "d" * 32
        assert len((await db.scalars(select(EventLog))).all()) == 1


async def test_digest_conflict_rolls_back_event_projection(factory):
    frame = message("run_changed")
    frame["payload"]["run"]["recipe_digest"] = "0" * 64
    store = V2SourceLogStore(factory, device_id=DEVICE, on_record=V2ArchiveProjector(DEVICE).on_record)
    with pytest.raises(ValueError, match="binding"):
        await store.ingest_live(frame)
    async with factory() as db:
        assert not (await db.scalars(select(EventLog))).all()
        assert not (await db.scalars(select(V2SourceRecord))).all()


async def test_global_alarm_keeps_occurrence_clear_and_ack_without_run_binding(factory):
    frame = message("alarm")
    frame["payload"]["run_id"] = None
    store = V2SourceLogStore(factory, device_id=DEVICE, on_record=V2ArchiveProjector(DEVICE).on_record)
    await store.ingest_live(frame)
    frame["payload"].update(
        event_id="b" * 32, event_seq="4", event_uptime_ms="13000", transition="cleared", active=False
    )
    await store.ingest_live(frame)
    frame["payload"].update(
        event_id="c" * 32, event_seq="5", event_uptime_ms="14000", transition="acknowledged", acknowledged=True
    )
    await store.ingest_live(frame)
    async with factory() as db:
        alarm = await db.scalar(select(AlarmLog))
        assert alarm.test_id is None and alarm.clear_time is not None and alarm.ack_time is not None
        projection = await db.scalar(select(V2AlarmProjection))
        assert projection.alarm_id == frame["payload"]["alarm_id"] and not projection.active
        assert len((await db.scalars(select(EventLog))).all()) == 3


async def test_alarm_snapshot_rejects_older_revision_and_does_not_cross_devices(factory):
    occurrence = message("alarms_snapshot")["payload"]["items"][0]
    projector = V2ArchiveProjector(DEVICE)
    boot = "e" * 32
    async with factory() as db, db.begin():
        await projector.reconcile_alarms(
            db, {"revision": "9", "items": [occurrence], "snapshot_uptime_ms": "100"}, boot_id=boot
        )
    async with factory() as db, db.begin():
        assert not await projector.reconcile_alarms(
            db, {"revision": "8", "items": [], "snapshot_uptime_ms": "110"}, boot_id=boot
        )
        await V2ArchiveProjector("f" * 32).reconcile_alarms(
            db, {"revision": "10", "items": [], "snapshot_uptime_ms": "200"}, boot_id=boot
        )
    async with factory() as db:
        assert (await db.scalar(select(AlarmLog))).clear_time is None


async def test_alarm_missing_from_new_snapshot_is_observed_cleared_but_newer_event_is_preserved(factory):
    projector = V2ArchiveProjector(DEVICE)
    frame = message("alarm")
    frame["payload"]["run_id"] = None
    store = V2SourceLogStore(factory, device_id=DEVICE, on_record=projector.on_record)
    await store.ingest_live(frame)
    async with factory() as db, db.begin():
        await projector.reconcile_alarms(
            db, {"revision": "9", "items": [], "snapshot_uptime_ms": "11000"}, boot_id=frame["boot_id"]
        )
    async with factory() as db:
        assert (await db.scalar(select(AlarmLog))).clear_time is None
    async with factory() as db, db.begin():
        await projector.reconcile_alarms(
            db, {"revision": "10", "items": [], "snapshot_uptime_ms": "13000"}, boot_id=frame["boot_id"]
        )
    # Historical raised evidence may be added, but cannot reactivate a reconciled occurrence.
    frame["payload"].update(event_id="d" * 32, event_seq="2", event_uptime_ms="10000")
    async with factory() as db, db.begin():
        await projector.on_record(
            db, {"record_type": "event", "boot_id": frame["boot_id"], "data": frame["payload"]}, "backfill"
        )
    async with factory() as db:
        alarm = await db.scalar(select(AlarmLog))
        assert alarm.clear_time is not None and alarm.ack_time is not None
        assert len((await db.scalars(select(AlarmLog))).all()) == 1


async def test_newly_backfilled_old_alarm_does_not_become_current_after_empty_snapshot(factory):
    projector = V2ArchiveProjector(DEVICE)
    frame = message("alarm")
    async with factory() as db, db.begin():
        await projector.reconcile_alarms(
            db, {"revision": "10", "items": [], "snapshot_uptime_ms": "13000"}, boot_id=frame["boot_id"]
        )
    async with factory() as db, db.begin():
        await projector.on_record(
            db, {"record_type": "event", "boot_id": frame["boot_id"], "data": frame["payload"]}, "backfill"
        )
    async with factory() as db:
        row = await db.scalar(select(AlarmLog))
        assert row.clear_time is not None and row.ack_time is not None


async def test_emergency_stop_archive_and_rest_use_critical_level_three(factory):
    from app.api.deps import get_current_user
    from app.api.routes.alarms import router
    from app.db.database import get_db

    frame = message("alarm")
    frame["payload"].update(run_id=None, code="emergency_stop", severity="trip")
    store = V2SourceLogStore(factory, device_id=DEVICE, on_record=V2ArchiveProjector(DEVICE).on_record)
    await store.ingest_live(frame)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: object()

    async def database():
        async with factory() as db:
            yield db

    app.dependency_overrides[get_db] = database
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as http:
        response = await http.get("/api/alarms/active")
    assert response.status_code == 200
    assert response.json()["data"][0]["level"] == 3
    async with factory() as db:
        assert (await db.scalar(select(EventLog))).level == 3
