"""REST → production callbacks → actual TCP simulator → durable experiment archive."""

import asyncio
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.main as main
from app.api import deps
from app.api.routes import maintenance, recipes
from app.core.config import Settings
from app.db.database import _create_engine, get_db
from app.db.models import Base, SamplePoint, TestSession
from app.hostcomm.v2_simulator import V2Simulator, synthetic_profile
from app.services.background_jobs import BackgroundJobManager
from app.services.cache import StatusCache
from app.services.maintenance_service import maintenance_manager
from app.services.test_runtime import active_test


@pytest.fixture
async def system(tmp_path, monkeypatch):
    sim = V2Simulator(tmp_path / "board.sqlite", test_plaintext=True, profile=synthetic_profile(approved=True))
    await sim.start()
    # Exercise the same WAL/busy-timeout configuration used by the service.
    engine = _create_engine(f"sqlite+aiosqlite:///{tmp_path / 'app.sqlite'}")
    async with engine.begin() as conn:
        # SQLite legacy transaction mode otherwise commits each DDL separately.
        await conn.exec_driver_sql("BEGIN")
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    cache = StatusCache()
    for module in (main, deps, recipes):
        monkeypatch.setattr(module, "status_cache", cache)
    monkeypatch.setattr(main, "get_sessionmaker", lambda: factory)
    maintenance_manager.configure_upgrade(tmp_path / "maintenance.json")
    jobs = BackgroundJobManager()
    monkeypatch.setattr(maintenance, "background_jobs", jobs)
    p = sim.pairing
    settings = Settings(
        _env_file=None,
        hostcomm_mock=True,
        protocol_version="2.0",
        hostcomm_port=sim.address[1],
        hostcomm_device_id=p.device_id,
        hostcomm_controller_id=p.controller_id,
        hostcomm_controller_epoch=p.controller_epoch,
    )
    client = main._build_hostcomm_client(settings)
    app = main.create_app()
    app.state.hostcomm_client = client
    app.dependency_overrides[deps.get_current_user] = lambda: deps.CurrentUser("admin", "admin")

    async def database():
        async with factory() as db:
            yield db

    app.dependency_overrides[get_db] = database
    active_test.stop()
    await client.start()
    try:
        try:
            async with asyncio.timeout(10):
                while not (client._ready and client._recovery_task is not None and client._recovery_task.done()):
                    await asyncio.sleep(0.01)
        except TimeoutError:
            pytest.fail(f"Device recovery did not complete: {client.stats}")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            yield http, client, sim, factory
    finally:
        # Do not let a failed HTTP task-status assertion leave recovery running
        # against a disposed database or the next test's event loop.
        await jobs.shutdown()
        await client.close()
        await sim.close()
        await engine.dispose()
        active_test.stop()


async def deploy(http):
    template = (await http.get("/api/recipes/template/standard")).json()["data"]["definition"]
    response = await http.post("/api/recipes", json={"definition": template})
    assert response.status_code == 200, response.text
    recipe = response.json()["data"]
    response = await http.post(
        f"/api/recipes/{recipe['recipe_id']}/activate", json={"version": 1, "operation_id": "activate-v2"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["operation_status"] == "verified"
    return recipe


async def command(http, name, params, operation_id):
    token = (await http.post("/api/commands/confirm-intent", json={"command": name})).json()["data"]["confirm_token"]
    return await http.post(
        "/api/commands", json={"command": name, "params": params, "operation_id": operation_id, "confirm_token": token}
    )


async def check_offline_pairing_evidence(client, factory, monkeypatch):
    """Use the installed updater's guard against actual REST/board archive rows."""
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "desktop"))
    from smd_desktop.pairing import PairingTransaction

    await client.close()
    with closing(sqlite3.connect(factory.kw["bind"].url.database)) as connection:
        PairingTransaction._guard(connection)
        assert connection.total_changes == 0


async def stop_with_pending_work(http, client, operation_id, pending, monkeypatch):
    """Measure the stop wire slot separately from durable HTTP audit commits."""
    request = client.transport.request
    witnessed = False

    async def observe(kind, payload, **kwargs):
        nonlocal witnessed
        if kind == "command" and payload["command"] == "stop_run":
            assert pending(), "The ordinary/read work finished before stop was sent"
            result = await asyncio.wait_for(request(kind, payload, **kwargs), 1)
            assert pending(), "Stop waited for ordinary/read work before its receipt"
            witnessed = True
            return result
        return await request(kind, payload, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(client.transport, "request", observe)
        # Confirmation and durable intent/result/audit commits have their own
        # bounded integration budget; the wire receipt above remains under 1s.
        result = await asyncio.wait_for(command(http, "stop_test", {}, operation_id), 10)
    assert witnessed, "Stop never reached its independent wire slot"
    return result


async def wait_alarm(http, wire_id, *, active=True):
    """Wait for the committed REST projection, not an assumed disk/runner speed."""
    for _ in range(400):
        rows = (await http.get("/api/alarms/active")).json()["data"]
        matched = next((row for row in rows if row.get("wire_alarm_id") == wire_id), None)
        if (matched is not None) == active:
            return matched
        await asyncio.sleep(0.01)
    pytest.fail(f"Alarm {wire_id} did not reach active={active}")


async def test_full_application_continues_recording_until_safe_completion(system, monkeypatch):
    http, client, sim, factory = system
    recipe = await deploy(http)
    result = await command(
        http,
        "start_test",
        {"test_id": "GB-2026-01", "original_height_mm": 40, "recipe_id": recipe["recipe_id"], "recipe_version": 1},
        "start-v2",
    )
    assert result.status_code == 200, result.text
    assert sim.state.run["state"] == "preparing"
    await sim.tick()
    await asyncio.sleep(0.05)
    assert sim.state.run["state"] == "measuring"
    sim.set_measurements(furnace_mc=700000, burden_mc=600000, displacement_um=0)
    await sim.tick()
    await asyncio.sleep(0.05)
    response = await command(http, "stop_test", {}, "stop-v2")
    assert response.status_code == 200, response.text
    await asyncio.sleep(0.05)
    async with factory() as db:
        run = await db.scalar(select(TestSession).where(TestSession.test_id == "GB-2026-01"))
        assert run.end_time is None
        count = await db.scalar(select(func.count()).select_from(SamplePoint))
    await sim.complete_purge()
    await sim.complete_cooling()
    await asyncio.sleep(0.1)
    # Production polling, not a test-only explicit backfill call, closes the source log.
    from app.db.v2_models import V2LogCursor

    for _ in range(400):
        async with factory() as db:
            cursor = await db.scalar(select(V2LogCursor))
            scanned = cursor.scanned_through_seq if cursor else None
        if scanned and int(scanned) >= int(sim.state.status()["log"]["newest_record_seq"]):
            break
        await asyncio.sleep(0.01)
    assert scanned and int(scanned) >= int(sim.state.status()["log"]["newest_record_seq"])
    async with factory() as db:
        run = await db.scalar(select(TestSession).where(TestSession.test_id == "GB-2026-01"))
        assert run.end_time is not None
        assert json.loads(run.measurement_basis_json)["v2"]["safe_complete"]
        assert await db.scalar(select(func.count()).select_from(SamplePoint)) > count
    snapshot = await client.get_status()
    assert snapshot["system"]["can_start_test"] is False
    assert snapshot["system"]["can_ack_run"] is True
    response = await command(http, "ack_run", {}, "ack-v2")
    assert response.status_code == 200, response.text
    assert sim.state.run["state"] == "idle"
    await check_offline_pairing_evidence(client, factory, monkeypatch)


async def test_rejected_start_allows_offline_pairing_without_fabricating_safe_completion(system, monkeypatch):
    from app.hostcomm.v2_simulator.state import DeviceError

    http, client, sim, factory = system
    recipe = await deploy(http)
    permission = sim.state._command_permission

    def reject_start(session, payload, raw):
        permission(session, payload, raw)
        if payload["command"] == "start_run":
            raise DeviceError("safety_condition_changed")

    monkeypatch.setattr(sim.state, "_command_permission", reject_start)
    response = await command(
        http,
        "start_test",
        {"test_id": "NEVER-STARTED", "original_height_mm": 40, "recipe_id": recipe["recipe_id"], "recipe_version": 1},
        "rejected-start-pairing",
    )
    assert response.status_code == 200, response.text
    assert sim.state.run["state"] == "idle"
    async with factory() as db:
        run = await db.scalar(select(TestSession).where(TestSession.test_id == "NEVER-STARTED"))
        assert run.phase == "start_rejected" and run.end_time is not None
        assert run.safety_completed_at is None and run.measurement_completed_at is None
        assert await db.scalar(select(func.count()).select_from(SamplePoint)) == 0
    await check_offline_pairing_evidence(client, factory, monkeypatch)


async def test_lost_receipt_is_queried_without_replaying_start_and_body_conflicts_reject(system):
    http, client, sim, factory = system
    recipe = await deploy(http)
    parameters = {
        "test_id": "LOST-ACK",
        "original_height_mm": 40,
        "recipe_id": recipe["recipe_id"],
        "recipe_version": 1,
    }
    sim.drop_reply("command_result")
    pending = asyncio.create_task(command(http, "start_test", parameters, "lost-v2"))
    try:
        async with asyncio.timeout(10):
            while sim.state.run["state"] != "preparing" or sim._drop_replies["command_result"]:
                assert not pending.done(), "Start finished before the intended start-reply loss"
                await asyncio.sleep(0.01)
        await sim.tick()
        response = await pending
    finally:
        # A failed setup assertion must not leave this HTTP request owning the
        # process-wide ordinary-command guard when the fixture/event loop ends.
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
    assert response.status_code == 504, response.text
    highwater = sim.state.data["highwater"][sim.pairing.controller_epoch]
    repeated = await command(http, "start_test", parameters, "lost-v2")
    assert repeated.status_code == 200
    assert repeated.json()["data"]["operation_status"] == "unknown"
    assert sim.state.data["highwater"][sim.pairing.controller_epoch] == highwater
    queried = await http.post("/api/commands/operations/lost-v2/query")
    assert queried.status_code == 200, queried.text
    assert queried.json()["data"]["wire_operation"]["status"] == "applied"
    assert queried.json()["data"]["operation_status"] == "accepted"
    assert sim.state.data["highwater"][sim.pairing.controller_epoch] == highwater
    conflicting = await command(http, "start_test", {**parameters, "test_id": "DIFFERENT"}, "lost-v2")
    assert conflicting.status_code == 409


async def test_v2_generic_parameter_and_unsupported_command_do_not_reach_board(system):
    http, client, sim, factory = system
    from app.services.command_service import compute_param_crc

    values = {"force_do": 1}
    result = await command(http, "set_parameters", {"values": values, "param_crc": compute_param_crc(values)}, "bad-v2")
    assert result.status_code == 422, result.text
    response = await command(http, "tare_balance", {}, "tare-v2")
    assert response.status_code == 422, response.text
    assert not sim.state.data["highwater"]


async def test_global_alarm_uses_wire_occurrence_and_offline_ack_cannot_fake_board_confirmation(system):
    http, client, sim, factory = system
    wire_id = await sim.raise_alarm("clock_unsynced", severity="warning")
    alarm = await wait_alarm(http, wire_id)
    assert alarm["occurrence_seq"] == "1" and len(alarm["wire_alarm_id"]) == 32
    response = await http.post(f"/api/alarms/{alarm['id']}/ack")
    assert response.status_code == 200, response.text
    assert response.json()["data"]["device_confirmed"]
    await sim.clear_alarm(alarm["wire_alarm_id"])
    await wait_alarm(http, wire_id, active=False)
    assert not (await http.get("/api/alarms/active")).json()["data"]
    wire_id = await sim.raise_alarm("measurement_sensor_invalid", severity="warning")
    alarm = await wait_alarm(http, wire_id)
    await client.close()
    response = await http.post(f"/api/alarms/{alarm['id']}/ack")
    assert response.status_code == 503
    unchanged = (await http.get("/api/alarms/active")).json()["data"][0]
    assert unchanged["ack_time"] is None


async def test_stop_bypasses_a_lost_ordinary_receipt_at_both_http_and_wire_layers(system, monkeypatch):
    http, client, sim, factory = system
    recipe = await deploy(http)
    started = await command(
        http,
        "start_test",
        {"test_id": "PRIORITY", "original_height_mm": 40, "recipe_id": recipe["recipe_id"], "recipe_version": 1},
        "priority-start",
    )
    assert started.status_code == 200
    await sim.tick()
    await asyncio.sleep(0.05)
    wire_id = await sim.raise_alarm("clock_unsynced", severity="warning")
    alarm = await wait_alarm(http, wire_id)
    sim.drop_reply("command_result")
    pending = asyncio.create_task(http.post(f"/api/alarms/{alarm['id']}/ack"))
    try:
        # Wait for the intended ordinary reply to be dropped. A fixed sleep can
        # let stop overtake a slow SQLite intent and consume the injected loss.
        async with asyncio.timeout(10):
            while sim._drop_replies["command_result"]:
                assert not pending.done(), "Alarm confirmation finished before the intended reply loss"
                await asyncio.sleep(0.01)
        assert sim.state.data["alarms"][f"{alarm['wire_alarm_id']}:{alarm['occurrence_seq']}"]["acknowledged"]
        assert not pending.done()
        result = await stop_with_pending_work(http, client, "priority-stop", lambda: not pending.done(), monkeypatch)
        assert result.status_code == 200, result.text
        assert sim.state.run["state"] in {"safe_disposal", "cooling"}
    finally:
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


async def test_stop_uses_current_session_run_identity_when_all_read_slots_are_busy(system, monkeypatch):
    http, client, sim, factory = system
    recipe = await deploy(http)
    result = await command(
        http,
        "start_test",
        {"test_id": "BUSY-READ", "original_height_mm": 40, "recipe_id": recipe["recipe_id"], "recipe_version": 1},
        "busy-read-start",
    )
    assert result.status_code == 200
    await sim.tick()
    await asyncio.sleep(0.05)
    await client.get_status()
    # The refreshed status response follows tick's frames on the same TCP
    # connection. Drain their optional refresh before deliberately filling all
    # four read slots, so none consumes an injected reply loss for this test.
    await asyncio.wait_for(client.transport._callbacks.join(), 10)
    if client._refresh_task is not None:
        await asyncio.wait_for(asyncio.shield(client._refresh_task), 10)
    sim.drop_reply("status_snapshot", count=4)
    reads = [asyncio.create_task(client.transport.request("get_status", {})) for _ in range(4)]
    try:
        async with asyncio.timeout(10):
            while sim._drop_replies["status_snapshot"]:
                assert all(not task.done() for task in reads), "A read finished before all four reply losses"
                await asyncio.sleep(0.01)
        result = await stop_with_pending_work(
            http, client, "busy-read-stop", lambda: all(not task.done() for task in reads), monkeypatch
        )
        assert result.status_code == 200, result.text
        assert result.json()["data"]["wire_status"] in {"accepted", "applied"}
        assert client.is_online
    finally:
        for task in reads:
            task.cancel()
        await asyncio.gather(*reads, return_exceptions=True)


async def test_internal_lease_loss_is_exposed_and_can_be_reconciled_without_sending_recipe(system):
    http, client, sim, factory = system
    template = (await http.get("/api/recipes/template/standard")).json()["data"]["definition"]
    recipe = (await http.post("/api/recipes", json={"definition": template})).json()["data"]
    sim.drop_reply("command_result")
    pending = asyncio.create_task(
        http.post(f"/api/recipes/{recipe['recipe_id']}/activate", json={"version": 1, "operation_id": "lost-lease"})
    )
    epoch = sim.pairing.controller_epoch
    try:
        async with asyncio.timeout(10):
            while sim._drop_replies["command_result"]:
                assert not pending.done(), "Activation finished before the intended lease-reply loss"
                await asyncio.sleep(0.01)
        sequence = sim.state.data["highwater"][epoch]
        sim.evict_result(epoch, sequence)
        reply = await pending
    finally:
        # A failed precondition must not leave an HTTP request owning the global
        # ordinary-command guard when the next test gets a fresh event loop.
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
    assert reply.status_code == 504, reply.text
    assert sim.state.data["active_recipe"] is None
    async with asyncio.timeout(10):
        while not (client.is_online and client._ready):
            await asyncio.sleep(0.01)
    query = await http.post("/api/commands/operations/lost-lease/query")
    assert query.status_code == 200, query.text
    assert query.json()["data"]["prerequisite_only"]
    assert query.json()["data"]["wire_operation"]["status"] == "result_expired"
    reconciled = await http.post("/api/commands/operations/lost-lease/reconcile", json={"reason": "空闲模拟板核查"})
    assert reconciled.status_code == 200, reconciled.text
    assert reconciled.json()["data"]["operation_status"] == "rejected"
    assert reconciled.json()["data"]["wire_operation"]["reconciled"]
    stored = (await http.get("/api/commands/operations/lost-lease")).json()["data"]
    assert stored["wire_reconciled"] is True
    assert stored["wire_status"] == "result_expired"
    listed = (await http.get("/api/commands/operations")).json()["data"]
    historical = next(row for row in listed if row["operation_id"] == "lost-lease")
    assert historical["command"] == "set_parameters"
    assert historical["created_at"] and historical["updated_at"]
    assert historical["wire_reconciled"] is True
    assert sim.state.data["highwater"][epoch] == sequence
    assert sim.state.data["active_recipe"] is None


async def test_lease_receipt_can_be_archived_across_a_connectivity_heartbeat(system, monkeypatch):
    http, client, sim, factory = system
    observed = asyncio.Event()
    record_result = client.operations._record_result
    dispatch = client.transport._dispatch
    expires = None

    async def hold_receipt(stored, response, expected_type):
        nonlocal expires
        if stored["command"] == "acquire_lease":
            assert response["payload"]["status"] == "applied"
            assert client.transport.lease_id is None
            expires = sim.state.data["lease"]["expires"]
            # Exercise the real periodic heartbeat in the exact board-applied /
            # locally-unconfirmed window, without changing any protocol deadline.
            await asyncio.wait_for(observed.wait(), 10)
        return await record_result(stored, response, expected_type)

    def witness_heartbeat(frame):
        if frame["type"] == "heartbeat_ack" and expires is not None and not observed.is_set():
            pending = client.transport._pending[frame["reply_to"]]
            assert pending.payload["lease_id"] is None
            assert frame["payload"]["lease_id"] == sim.state.data["lease"]["id"]
            assert sim.state.data["lease"]["expires"] == expires
            observed.set()
        dispatch(frame)

    monkeypatch.setattr(client.operations, "_record_result", hold_receipt)
    monkeypatch.setattr(client.transport, "_dispatch", witness_heartbeat)
    await deploy(http)
    assert observed.is_set()
    assert client.is_online and client.transport.lease_id == sim.state.data["lease"]["id"]
    assert client.transport.response_timeout == 3


async def test_recovery_pending_rejects_start_before_creating_an_experiment(system):
    http, client, sim, factory = system
    recipe = await deploy(http)
    client._ready = False
    reply = await command(
        http,
        "start_test",
        {"test_id": "RECOVERING", "original_height_mm": 40, "recipe_id": recipe["recipe_id"], "recipe_version": 1},
        "recovering-start",
    )
    assert reply.status_code == 409
    assert reply.json()["error_code"] == "device_recovery_pending"
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(TestSession)) == 0
    client._ready = True


async def test_query_timeout_is_504_and_later_recipe_cannot_regress_verified_history(system):
    http, client, sim, factory = system
    recipe = await deploy(http)
    original = (await http.get("/api/commands/operations/activate-v2")).json()["data"]
    definition = {**recipe["definition"], "name": "下一版本"}
    revised = await http.put(f"/api/recipes/{recipe['recipe_id']}", json={"definition": definition})
    assert revised.status_code == 200
    activated = await http.post(
        f"/api/recipes/{recipe['recipe_id']}/activate", json={"version": 2, "operation_id": "activate-v2-next"}
    )
    assert activated.status_code == 200, activated.text
    queried = await http.post("/api/commands/operations/activate-v2/query")
    assert queried.status_code == 200, queried.text
    assert queried.json()["data"]["operation_status"] == "verified"
    assert queried.json()["data"]["params"] == original["params"]
    sim.drop_reply("operation_snapshot")
    timeout = await http.post("/api/commands/operations/activate-v2/query")
    assert timeout.status_code == 504, timeout.text
    assert (await http.get("/api/commands/operations/activate-v2")).json()["data"]["operation_status"] == "verified"


async def test_manual_source_scan_is_bounded_background_work(system):
    http, client, sim, factory = system
    response = await http.post("/api/system/maintenance/source-logs", json={"first_record_seq": "1"})
    assert response.status_code == 200, response.text
    task_id = response.json()["data"]["task_id"]
    async with asyncio.timeout(10):
        while True:
            task = (await http.get(f"/api/system/maintenance/source-logs/{task_id}")).json()["data"]
            if task["status"] in {"completed", "failed"}:
                break
            await asyncio.sleep(0.01)
    assert task["status"] == "completed", task
    assert (
        await http.post("/api/system/maintenance/source-logs", json={"first_record_seq": str(2**64)})
    ).status_code == 422


async def test_optional_status_refresh_cannot_hold_up_durable_log_ack(system, monkeypatch):
    http, client, sim, factory = system
    entered, release = asyncio.Event(), asyncio.Event()
    original = client._optional_status

    async def slow_refresh():
        entered.set()
        await release.wait()
        await original()

    monkeypatch.setattr(client, "_optional_status", slow_refresh)
    await sim.raise_alarm("clock_unsynced", severity="warning")
    try:
        await asyncio.wait_for(entered.wait(), 10)
        # The real transport retains its 3s progress deadline. An optional
        # refresh must not keep a received chunk waiting behind it for an ACK.
        await asyncio.wait_for(client.recover_logs(first_record_seq="1"), 5)
        assert client.is_online and client._ready
        assert client.transport.stats["dropped_callbacks"] == 0
    finally:
        release.set()


async def test_known_live_backlog_defers_log_transfer_without_blocking_alarm_ack(system, monkeypatch):
    from app.db.v2_models import V2LogTransfer
    from app.hostcomm.v2_transport import V2CapacityError

    http, client, sim, factory = system
    await deploy(http)
    wire_id = await sim.raise_alarm("clock_unsynced", severity="warning")
    alarm = await wait_alarm(http, wire_id)
    entered, release = asyncio.Event(), asyncio.Event()
    heartbeat_completed = asyncio.Event()
    original_ingest = client.logs.ingest_live
    original_request = client.transport.request
    log_requests = []
    async with factory() as db:
        transfer_count = await db.scalar(select(func.count()).select_from(V2LogTransfer))

    async def blocked_live(frame):
        if frame["type"] == "telemetry":
            entered.set()
            await release.wait()
        return await original_ingest(frame)

    async def record_request(kind, payload, **kwargs):
        if kind == "log_request":
            log_requests.append(payload["transfer_id"])
        response = await original_request(kind, payload, **kwargs)
        if kind == "heartbeat":
            heartbeat_completed.set()
        return response

    monkeypatch.setattr(client.logs, "ingest_live", blocked_live)
    monkeypatch.setattr(client.transport, "request", record_request)
    await sim.tick()
    await asyncio.wait_for(entered.wait(), 5)
    heartbeat_completed.clear()
    recovery = asyncio.create_task(client.recover_logs(first_record_seq="1"))
    try:
        # The source callback remains blocked beyond the real 3s wire deadline.
        # Read/command replies and lease renewal must still use the independent lane.
        acknowledged = await http.post(f"/api/alarms/{alarm['id']}/ack")
        assert acknowledged.status_code == 200, acknowledged.text
        assert acknowledged.json()["data"]["device_confirmed"] is True
        await asyncio.wait_for(heartbeat_completed.wait(), 5)
        with pytest.raises(V2CapacityError, match="admission"):
            await asyncio.wait_for(recovery, 5)
        assert not log_requests
        assert client.is_online and client._ready
        assert client.transport.stats["dropped_callbacks"] == 0
        assert not client.transport._callback_waiters
        assert not any(item.kind == "log_request" for item in client.transport._pending.values())
        async with factory() as db:
            assert await db.scalar(select(func.count()).select_from(V2LogTransfer)) == transfer_count
        release.set()
        await asyncio.wait_for(client.recover_logs(first_record_seq="1"), 10)
        assert log_requests
        assert client.is_online and client._ready
        assert client.transport.stats["dropped_callbacks"] == 0
    finally:
        release.set()
        if not recovery.done():
            recovery.cancel()
        await asyncio.gather(recovery, return_exceptions=True)


async def test_log_admission_cannot_cross_reboot_during_database_begin(system, monkeypatch):
    from app.hostcomm.v2_transport import V2OfflineError

    http, client, sim, factory = system
    original_begin = client.logs.begin
    original_request = client.transport.request
    log_requests = []

    async def reboot_after_begin(*args, **kwargs):
        result = await original_begin(*args, **kwargs)
        await sim.reboot()
        async with asyncio.timeout(3):
            while client.transport.is_online:
                await asyncio.sleep(0.005)
        return result

    async def record_request(kind, payload, **kwargs):
        if kind == "log_request":
            log_requests.append(payload["transfer_id"])
        return await original_request(kind, payload, **kwargs)

    monkeypatch.setattr(client.logs, "begin", reboot_after_begin)
    monkeypatch.setattr(client.transport, "request", record_request)
    with pytest.raises(V2OfflineError, match="previous connection"):
        await asyncio.wait_for(client.recover_logs(first_record_seq="1"), 5)
    assert not log_requests
    assert not client.transport._callback_waiters


async def test_deferred_source_recovery_resumes_after_run_ack_returns_device_to_idle(system, monkeypatch):
    from app.hostcomm.v2_transport import V2CapacityError

    http, client, sim, factory = system
    recipe = await deploy(http)
    started = await command(
        http,
        "start_test",
        {"test_id": "DEFERRED-SOURCE", "original_height_mm": 40, "recipe_id": recipe["recipe_id"], "recipe_version": 1},
        "deferred-source-start",
    )
    assert started.status_code == 200, started.text
    sim.set_measurements(furnace_mc=700000, burden_mc=600000, displacement_um=0)
    await sim.tick()
    stopped = await command(http, "stop_test", {}, "deferred-source-stop")
    assert stopped.status_code == 200, stopped.text
    await client.transport.wait_for_callbacks()
    entered, release, refused, completed = (asyncio.Event() for _ in range(4))
    original_ingest = client.logs.ingest_live
    original_admission = client.transport.wait_for_callbacks
    original_finish = client.logs.finish

    async def blocked_live(frame):
        if frame["type"] == "telemetry":
            entered.set()
            await release.wait()
        return await original_ingest(frame)

    async def observed_admission(**kwargs):
        try:
            return await original_admission(**kwargs)
        except V2CapacityError:
            refused.set()
            raise

    async def observed_finish(frame):
        result = await original_finish(frame)
        completed.set()
        return result

    monkeypatch.setattr(client.logs, "ingest_live", blocked_live)
    monkeypatch.setattr(client.transport, "wait_for_callbacks", observed_admission)
    monkeypatch.setattr(client.logs, "finish", observed_finish)
    try:
        await sim.complete_purge()
        await sim.complete_cooling()
        await asyncio.wait_for(entered.wait(), 5)
        await asyncio.wait_for(refused.wait(), 8)
        await asyncio.wait_for(asyncio.shield(client._source_recovery_task), 1)
        assert client.is_online and client._ready
        finished = await command(http, "ack_run", {}, "deferred-source-finish")
        assert finished.status_code == 200, finished.text
        assert sim.state.run["state"] == "idle"
        assert not completed.is_set()
        release.set()
        # No manual scan/reconnect: the ordinary background poll must retain the
        # deferred read even though this run is no longer the live terminal run.
        await asyncio.wait_for(completed.wait(), 8)
        await asyncio.wait_for(asyncio.shield(client._source_recovery_task), 1)
        assert not client._source_recovery_pending
        assert client.is_online and client._ready
        assert client.transport.stats["dropped_callbacks"] == 0
    finally:
        release.set()


@pytest.mark.parametrize("later_error", [asyncio.CancelledError, ConnectionError])
async def test_deferred_source_scan_survives_interruption_until_success(system, monkeypatch, later_error):
    from app.hostcomm.v2_transport import V2CapacityError

    http, client, sim, factory = system
    original = client.recover_logs

    async def refused():
        raise V2CapacityError("Source callback admission deadline exceeded; no request sent")

    async def interrupted():
        raise later_error()

    monkeypatch.setattr(client, "recover_logs", refused)
    await client._recover_completed_sources()
    assert client._source_recovery_pending
    monkeypatch.setattr(client, "recover_logs", interrupted)
    if later_error is asyncio.CancelledError:
        with pytest.raises(asyncio.CancelledError):
            await client._recover_completed_sources()
    else:
        await client._recover_completed_sources()
    assert client._source_recovery_pending
    monkeypatch.setattr(client, "recover_logs", original)
    await client._recover_completed_sources()
    assert not client._source_recovery_pending
    assert client.is_online and client._ready


@pytest.mark.parametrize("renew_before_confirm", [False, True])
async def test_cleared_unacknowledged_alarm_can_be_confirmed_before_run_ack(system, monkeypatch, renew_before_confirm):
    http, client, sim, factory = system
    recipe = await deploy(http)
    wire_id = await sim.raise_alarm("clock_unsynced", severity="warning")
    alarm = await wait_alarm(http, wire_id)
    await sim.clear_alarm(alarm["wire_alarm_id"])
    await wait_alarm(http, wire_id, active=False)
    started = await command(
        http,
        "start_test",
        {"test_id": "ALARM-HISTORY", "original_height_mm": 40, "recipe_id": recipe["recipe_id"], "recipe_version": 1},
        "alarm-history-start",
    )
    assert started.status_code == 200, started.text
    sim.set_measurements(furnace_mc=700000, burden_mc=600000, displacement_um=0)
    await sim.tick()
    stopped = await command(http, "stop_test", {}, "alarm-history-stop")
    assert stopped.status_code == 200, stopped.text
    await sim.complete_purge()
    await sim.complete_cooling()
    snapshot = await client.get_status()
    assert snapshot["alarm"]["ack_required"] is True
    assert snapshot["system"]["can_ack_run"] is False
    if renew_before_confirm:
        client.transport._heartbeat_task.cancel()
        await asyncio.gather(client.transport._heartbeat_task, return_exceptions=True)
        confirm_owned = client.operations.confirm_owned_lease

        async def renewal_arrives_before_confirmation(frame):
            # Reproduce a renewal arriving after the status read but before
            # its application/database processing finishes, for both commands.
            async with asyncio.timeout(1):
                while True:
                    ack = await client.transport.request("heartbeat", {"lease_id": client.transport.lease_id})
                    if int(ack["uptime_ms"]) > int(frame["uptime_ms"]):
                        break
                    await asyncio.sleep(0.001)
            assert ack["payload"]["state_revision"] == frame["payload"]["run"]["state_revision"]
            return await confirm_owned(frame)

        monkeypatch.setattr(client.operations, "confirm_owned_lease", renewal_arrives_before_confirmation)
    acknowledged = await http.post(f"/api/alarms/{alarm['id']}/ack")
    assert acknowledged.status_code == 200, acknowledged.text
    assert acknowledged.json()["data"]["device_confirmed"] is True
    snapshot = await client.get_status()
    assert snapshot["alarm"]["ack_required"] is False
    assert snapshot["system"]["can_ack_run"] is True
    finished = await command(http, "ack_run", {}, "alarm-history-finish")
    assert finished.status_code == 200, finished.text
    assert sim.state.run["state"] == "idle"
