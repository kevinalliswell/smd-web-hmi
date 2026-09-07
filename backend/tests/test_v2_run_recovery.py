"""Unknown runs stay raw until a reviewed, durable and replayable association exists."""

import asyncio
import copy
import hashlib
import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.database import _create_engine
from app.db.models import AlarmLog, Base, EventLog, SamplePoint
from app.db.models import TestSession as Experiment
from app.db.operation_models import Operation
from app.db.v2_models import V2Operation, V2RunBinding, V2RunRecovery, V2SourceRecord
from app.hostcomm.v2_contract.codec import command_digest, digest
from app.services.recovery_queries import recovery_log_condition
from app.services.v2_archive import V2ArchiveProjector
from app.services.v2_recovery_evidence import json_text
from app.services.v2_run_recovery import V2RecoveryError, V2RunRecoveryService, require_recovery_report_ready
from app.services.v2_source_logs import V2SourceLogStore

VECTORS = json.loads(
    (Path(__file__).resolve().parents[2] / "contracts/hostcomm/v2/vectors.json").read_text(encoding="utf-8")
)
DEVICE = "a" * 32


def message(name):
    return copy.deepcopy(next(v["value"] for v in VECTORS["valid_messages"] if v["name"] == name))


@pytest.fixture
async def system(tmp_path):
    engine = _create_engine(f"sqlite+aiosqlite:///{tmp_path}/recovery.db")
    async with engine.begin() as connection:
        await connection.exec_driver_sql("BEGIN")
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    lock = asyncio.Lock()
    projector = V2ArchiveProjector(DEVICE)
    store = V2SourceLogStore(factory, device_id=DEVICE, on_record=projector.on_record, write_lock=lock)
    service = V2RunRecoveryService(factory, write_lock=lock)
    yield factory, projector, store, service
    await engine.dispose()


async def discover(system):
    factory, projector, store, service = system
    await store.ingest_live(message("telemetry"))
    frame = message("status_snapshot")
    async with factory() as db, db.begin():
        await projector.reconcile_status(db, frame["payload"], boot_id=frame["boot_id"])
    return (await service.list())[0]


async def bind(service, case, **overrides):
    return await service.bind(
        case["id"],
        actor="admin",
        role="admin",
        reason="Reviewed original source identity",
        idempotency_key="bind-1",
        expected_review_revision=case["review_revision"],
        **overrides,
    )


async def test_discovery_is_durable_deduped_and_never_invents_start(system):
    factory, _, store, service = system
    case = await discover(system)
    await store.ingest_live(message("telemetry"))
    assert len(await service.list()) == 1
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(Experiment)) == 0
        assert await db.scalar(select(func.count()).select_from(V2SourceRecord)) == 1
    bound = await bind(service, case)
    async with factory() as db:
        row = await db.scalar(select(Experiment))
        assert row.start_time is None and row.discovered_at == case["first_seen_at"]
        assert row.mode == "unknown" and row.sample_label is None and row.original_height_mm is None
        with pytest.raises(V2RecoveryError, match="replay"):
            await require_recovery_report_ready(db, bound["test_id"])
    assert await bind(service, case) == bound
    await service.replay_batch(case["id"])
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(SamplePoint)) == 1
        await require_recovery_report_ready(db, bound["test_id"])
    await service.replay_batch(case["id"])
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(SamplePoint)) == 1


@pytest.mark.parametrize("event_first", [False, True])
async def test_unchanged_run_sources_do_not_invalidate_review_or_erase_status(system, event_first):
    factory, projector, store, service = system
    frame = message("status_snapshot")
    if event_first:
        await store.ingest_live(message("run_changed"))
    async with factory() as db, db.begin():
        await projector.reconcile_status(db, frame["payload"], boot_id=frame["boot_id"])
    reviewed = (await service.list())[0]
    assert reviewed["evidence"]["latest_run"]["status"] == frame["payload"]
    for index in range(4):
        event = message("run_changed")
        event["payload"].update(event_id=uuid.uuid4().hex, event_seq=str(10 + index))
        await store.ingest_live(event)
        after_event = await service.detail(reviewed["id"])
        assert after_event["review_revision"] == reviewed["review_revision"]
        assert after_event["evidence"]["latest_run"] == reviewed["evidence"]["latest_run"]
        # A renewed lease or a later received snapshot is not a changed run.
        refreshed = copy.deepcopy(frame["payload"])
        refreshed["lease_expires_uptime_ms"] = str(21000 + index)
        async with factory() as db, db.begin():
            await projector.reconcile_status(db, refreshed, boot_id=frame["boot_id"])
    current = await service.detail(reviewed["id"])
    assert current["review_revision"] == reviewed["review_revision"]
    assert set(current["evidence"]["origins"]) == {"live", "status_snapshot"}
    assert current["evidence"]["latest_run"] == reviewed["evidence"]["latest_run"]
    await bind(service, reviewed)  # The operator's reviewed revision remains usable.
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(V2SourceRecord)) == 4 + int(event_first)


async def test_material_run_completion_still_invalidates_an_older_review(system):
    factory, projector, _, service = system
    case = await discover(system)
    frame = message("status_snapshot")
    frame["payload"]["run"].update(
        state="completed",
        state_revision="13",
        outcome="valid_candidate",
        measurement_complete=True,
        safe_complete=True,
        measurement_end={"boot_id": frame["boot_id"], "sample_seq": "45"},
        safe_boundary={"boot_id": frame["boot_id"], "sample_seq": "50"},
    )
    async with factory() as db, db.begin():
        await projector.reconcile_status(db, frame["payload"], boot_id=frame["boot_id"])
    current = await service.detail(case["id"])
    assert current["review_revision"] > case["review_revision"]
    assert current["evidence"]["latest_run"]["status"] == frame["payload"]
    assert current["evidence"]["safe_boundary"]["sample_seq"] == "50"
    with pytest.raises(V2RecoveryError, match="revision"):
        await bind(service, case)


async def test_review_conflicts_permissions_and_target_proof_are_enforced(system):
    factory, _, _, service = system
    case = await discover(system)
    with pytest.raises(V2RecoveryError, match="revision"):
        await service.bind(
            case["id"],
            actor="a",
            role="admin",
            reason="reviewed",
            idempotency_key="x",
            expected_review_revision=case["review_revision"] + 1,
        )
    with pytest.raises(V2RecoveryError, match="role"):
        await service.bind(
            case["id"],
            actor="a",
            role="operator",
            reason="reviewed",
            idempotency_key="x",
            expected_review_revision=case["review_revision"],
        )
    async with factory() as db, db.begin():
        db.add(Experiment(test_id="existing", operator_id="a", start_time="2026-09-06T00:00:00Z"))
    with pytest.raises(V2RecoveryError, match="proof"):
        await bind(service, case, target_test_id="existing")
    await bind(service, case)
    with pytest.raises(V2RecoveryError, match="idempotency"):
        await bind(service, case, target_test_id="existing")


async def test_replay_projection_and_cursor_rollback_together_then_resume(system, monkeypatch):
    factory, _, _, service = system
    case = await discover(system)
    await bind(service, case)
    original = V2ArchiveProjector.on_record

    async def fail_after_projection(self, db, record, origin):
        await original(self, db, record, origin)
        raise ValueError("injected projection failure")

    monkeypatch.setattr(V2ArchiveProjector, "on_record", fail_after_projection)
    with pytest.raises(ValueError):
        await service.replay_batch(case["id"])
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(SamplePoint)) == 0
        row = await db.get(V2RunRecovery, case["id"])
        assert row.replay_through_id == 0 and row.replay_status == "failed"
    monkeypatch.setattr(V2ArchiveProjector, "on_record", original)
    restarted = V2RunRecoveryService(factory, write_lock=service.write_lock)
    await restarted.replay_batch(case["id"])
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(SamplePoint)) == 1
        assert (await db.get(V2RunRecovery, case["id"])).replay_status == "complete"


async def test_alarm_recovery_associates_original_global_rows_without_cloning_or_reactivation(system):
    factory, _, store, service = system
    frame = message("alarm")
    frame["payload"]["run_id"] = message("telemetry")["payload"]["run_id"]
    await store.ingest_live(frame)
    cleared = copy.deepcopy(frame)
    cleared["payload"].update(
        event_id="c" * 32, event_seq="4", event_uptime_ms="14000", transition="cleared", active=False
    )
    await store.ingest_live(cleared)
    case = (await service.list())[0]
    bound = await bind(service, case)
    await service.replay_batch(case["id"])
    await service.replay_batch(case["id"])
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(AlarmLog)) == 1
        assert await db.scalar(select(func.count()).select_from(EventLog)) == 2
        alarm = await db.scalar(select(AlarmLog).where(recovery_log_condition(AlarmLog, bound["test_id"])))
        assert alarm.test_id is None and alarm.clear_time is not None
        assert (
            len((await db.scalars(select(EventLog).where(recovery_log_condition(EventLog, bound["test_id"])))).all())
            == 2
        )


async def test_conflicting_boundary_preserves_raw_and_blocks_review(system):
    factory, _, store, service = system
    case = await discover(system)
    frame = message("run_changed")
    frame["payload"]["run"]["measurement_start"]["sample_seq"] = "40"
    await store.ingest_live(frame)
    latest = await service.detail(case["id"])
    assert latest["review_state"] == "conflict"
    with pytest.raises(V2RecoveryError, match="evidence_conflict"):
        await bind(service, latest)
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(V2SourceRecord)) == 2
        assert await db.scalar(select(func.count()).select_from(SamplePoint)) == 0


async def test_concurrent_review_has_one_winner_and_replay_uses_original_start(system):
    factory, _, store, service = system
    case = await discover(system)
    sample = message("telemetry")
    sample["payload"]["sample"]["sample_seq"] = "41"
    sample["payload"]["sample_timestamp"] = "2026-09-01T01:00:00.000Z"
    await store.ingest_live(sample)
    case = await service.detail(case["id"])
    other = V2RunRecoveryService(factory)
    results = await asyncio.gather(
        bind(service, case),
        other.bind(
            case["id"],
            actor="b",
            role="maintainer",
            reason="independent review",
            idempotency_key="another",
            expected_review_revision=case["review_revision"],
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(result, V2RecoveryError) for result in results) == 1
    await service.replay_batch(case["id"], batch_size=1)
    async with factory() as db:
        assert (await db.get(V2RunRecovery, case["id"])).replay_status == "pending"
    while (await service.replay_batch(case["id"], batch_size=1))["replay_status"] != "complete":
        pass
    async with factory() as db:
        row = await db.scalar(select(Experiment))
        assert row.start_time.startswith("2026-09-01T01:00:00")
        assert row.start_time != row.discovered_at


async def test_api_roles_idempotency_and_manual_checkbox_cannot_close_v2(system):
    from app.api import deps
    from app.api.routes import experiment_review, run_recoveries
    from app.db.database import get_db
    from app.services.v2_recovery_worker import V2RecoveryWorker

    factory, _, _, service = system
    case = await discover(system)
    app = FastAPI()
    app.include_router(run_recoveries.router)
    app.include_router(experiment_review.router)
    app.state.run_recovery_worker = V2RecoveryWorker(service)
    role = "observer"
    app.dependency_overrides[deps.get_current_user] = lambda: deps.CurrentUser("reviewer", role)

    async def database():
        async with factory() as db:
            yield db

    app.dependency_overrides[get_db] = database
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/api/run-recoveries")).status_code == 200
        body = dict(
            reason="original source reviewed", idempotency_key="http1", expected_review_revision=case["review_revision"]
        )
        path = f"/api/run-recoveries/{case['id']}/binding"
        assert (await client.post(path, json=body)).status_code == 403
        role = "admin"
        assert (await client.post(path, json={**body, "safety_verified": True})).status_code == 422
        response = await client.post(path, json=body)
        assert response.status_code == 200
        assert (await client.post(path, json=body)).json()["data"] == response.json()["data"]
        assert (await client.post(path, json={**body, "reason": "different"})).status_code == 409
        test_id = response.json()["data"]["test_id"]
        closed = await client.post(
            f"/api/tests/{test_id}/review-close",
            json={"reason": "manual checkbox cannot prove", "physical_safety_confirmed": True},
        )
        assert closed.status_code == 409 and closed.json()["detail"]["error_code"] == "v2_safe_evidence_required"
        async with factory() as db:
            row = await db.scalar(select(Experiment))
            assert row.end_time is None and row.safety_completed_at is None


async def test_existing_target_requires_exact_durable_start_request_and_matching_digests(system):
    factory, _, _, service = system
    case = await discover(system)
    request = message("command")["payload"]
    epoch = request["controller_epoch"]
    request["operation_id"] = uuid.uuid5(uuid.UUID(hex=epoch), "original-http-msg").hex
    request["request_digest"] = command_digest(request)
    params = {"test_id": "existing"}
    async with factory() as db, db.begin():
        db.add(
            Experiment(
                test_id="existing",
                operator_id="original",
                start_time="2026-09-01T00:00:00Z",
                recipe_snapshot_json=json_text({"operation_id": "original-http"}),
            )
        )
        db.add(
            Operation(
                operation_id="original-http",
                msg_id="original-http-msg",
                command="start_test",
                operator_id="original",
                operator_role="admin",
                request_hash=hashlib.sha256(
                    json_text({"command": "start_test", "params": params}).encode()
                ).hexdigest(),
                params_json=json_text(params),
                status="unknown",
                created_at="2026-09-01T00:00:00Z",
                updated_at="2026-09-01T00:00:00Z",
            )
        )
        db.add(
            V2Operation(
                operation_id=request["operation_id"],
                device_id=DEVICE,
                controller_epoch=epoch,
                command_seq=request["command_seq"],
                msg_id="f" * 32,
                command="start_run",
                business_digest=digest(
                    {"command": "start_run", "params": request["params"], "actor": "original", "role": "admin"}
                ),
                request_digest=request["request_digest"],
                actor="original",
                role="admin",
                status="unknown",
                reason="receipt_lost",
                request_json=json_text(request),
                created_at="now",
                updated_at="now",
            )
        )
    bound = await bind(service, case, target_test_id="existing")
    assert bound["test_id"] == "existing"
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(Experiment)) == 1
        assert (await db.scalar(select(Experiment))).start_time.startswith("2026-09-01")


async def test_bootstrap_recovers_preupgrade_raw_rows_and_worker_survives_lookup_failure(system, monkeypatch):
    from sqlalchemy import delete

    from app.services.v2_recovery_worker import V2RecoveryWorker

    factory, _, store, service = system
    await store.ingest_live(message("telemetry"))
    async with factory() as db, db.begin():
        await db.execute(delete(V2RunRecovery))  # Model the published schema's existing raw-only sources.
    worker = V2RecoveryWorker(service)
    original = worker._pending
    called = asyncio.Event()
    count = 0

    async def transient():
        nonlocal count
        count += 1
        if count == 1:
            raise RuntimeError("injected database lookup failure")
        called.set()
        return await original()

    monkeypatch.setattr(worker, "_pending", transient)
    await worker.start()
    try:
        async with asyncio.timeout(10):
            await called.wait()
        cases = await service.list()
        assert len(cases) == 1 and "existing_source" in cases[0]["evidence"]["origins"]
        assert not worker.task.done()
        await bind(service, cases[0])
        worker.wake.set()
        async with asyncio.timeout(10):
            while (await service.detail(cases[0]["id"]))["replay_status"] != "complete":
                await asyncio.sleep(0.01)
    finally:
        await worker.close()


async def test_historical_recovery_does_not_hijack_current_or_startup_runtime(system):
    from app.api import deps
    from app.api.routes import tests as tests_route
    from app.db.database import get_db
    from app.services.test_runtime import active_test
    from app.services.test_session_service import reconcile_test_sessions

    factory, _, _, service = system
    async with factory() as db, db.begin():
        db.add(Experiment(test_id="actual-current", operator_id="operator", start_time="2026-09-08T00:00:00Z"))
        db.add(
            V2RunBinding(
                device_id="f" * 32,
                run_id="e" * 32,
                test_id="actual-current",
                recipe_digest=None,
                profile_digest=None,
                created_at="now",
            )
        )
    case = await discover(system)
    await bind(service, case)
    app = FastAPI()
    app.include_router(tests_route.router)
    identity = {"device_id": "f" * 32, "run_id": "e" * 32}
    adapter = SimpleNamespace(protocol_version="2.0", current_run_identity=identity)
    app.state.hostcomm_client = adapter
    app.dependency_overrides[deps.get_current_user] = lambda: deps.CurrentUser("a", "observer")

    async def database():
        async with factory() as db:
            yield db

    app.dependency_overrides[get_db] = database
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/tests/current")
        assert response.json()["data"]["test_id"] == "actual-current"
        adapter.current_run_identity = {"device_id": "f" * 32, "run_id": "d" * 32}
        assert (await client.get("/api/tests/current")).json()["data"] is None
    try:
        async with factory() as db:
            result = await reconcile_test_sessions(db)
            assert result["restored_test_id"] == "actual-current"
    finally:
        active_test.stop()


async def test_late_start_sample_fills_original_time_and_report_gate_reads_fresh_state(system):
    factory, _, store, service = system
    case = await discover(system)
    bound = await bind(service, case)
    await service.replay_batch(case["id"])
    async with factory() as db:
        cached = await db.get(V2RunRecovery, case["id"])
        await require_recovery_report_ready(db, bound["test_id"])
        current = await service.detail(case["id"])
        await service.request_replay(
            case["id"],
            actor="admin",
            role="admin",
            reason="new verified records",
            idempotency_key="replay-fresh",
            expected_review_revision=current["review_revision"],
        )
        assert cached.replay_status == "complete"  # Deliberately stale identity-map instance.
        with pytest.raises(V2RecoveryError):
            await require_recovery_report_ready(db, bound["test_id"])
    sample = message("telemetry")
    sample["payload"]["sample"]["sample_seq"] = "41"
    sample["payload"]["sample_timestamp"] = "2026-09-01T01:00:00.000Z"
    await store.ingest_live(sample)
    async with factory() as db:
        row = await db.scalar(select(Experiment))
        assert row.start_time.startswith("2026-09-01T01:00:00")


async def test_worker_marks_malformed_record_failed_without_spin_and_can_resume(system):
    from app.services.v2_recovery_worker import V2RecoveryWorker

    factory, _, _, service = system
    case = await discover(system)
    await bind(service, case)
    async with factory() as db, db.begin():
        raw = await db.scalar(select(V2SourceRecord))
        original = raw.payload_bytes
        raw.payload_bytes = b"corrupt stored bytes"
    worker = V2RecoveryWorker(service)
    await worker.start()
    try:
        async with asyncio.timeout(10):
            while (await service.detail(case["id"]))["replay_status"] != "failed":
                await asyncio.sleep(0.01)
        assert not worker.task.done()
        async with factory() as db, db.begin():
            assert await db.scalar(select(func.count()).select_from(SamplePoint)) == 0
            raw = await db.scalar(select(V2SourceRecord))
            raw.payload_bytes = original  # Test fixture repairs exact bytes, production offers no overwrite API.
        current = await service.detail(case["id"])
        await service.request_replay(
            case["id"],
            actor="admin",
            role="admin",
            reason="verified original backup restored",
            idempotency_key="resume",
            expected_review_revision=current["review_revision"],
        )
        worker.wake.set()
        async with asyncio.timeout(10):
            while (await service.detail(case["id"]))["replay_status"] != "complete":
                await asyncio.sleep(0.01)
    finally:
        await worker.close()
