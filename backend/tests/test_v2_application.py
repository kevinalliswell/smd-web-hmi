"""REST → production callbacks → actual TCP simulator → durable experiment archive."""

import asyncio
import json

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.main as main
from app.api import deps
from app.api.routes import recipes
from app.core.config import Settings
from app.db.database import get_db
from app.db.models import Base, SamplePoint, TestSession
from app.hostcomm.v2_simulator import V2Simulator, synthetic_profile
from app.services.cache import StatusCache
from app.services.maintenance_service import maintenance_manager
from app.services.test_runtime import active_test


@pytest.fixture
async def system(tmp_path, monkeypatch):
    sim = V2Simulator(tmp_path / "board.sqlite", test_plaintext=True, profile=synthetic_profile(approved=True))
    await sim.start()
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'app.sqlite'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    cache = StatusCache()
    for module in (main, deps, recipes):
        monkeypatch.setattr(module, "status_cache", cache)
    monkeypatch.setattr(main, "get_sessionmaker", lambda: factory)
    maintenance_manager.configure_upgrade(tmp_path / "maintenance.json")
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
        for _ in range(200):
            if client._ready:
                break
            await asyncio.sleep(0.01)
        assert client._ready, client.stats
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            yield http, client, sim, factory
    finally:
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


async def test_full_application_continues_recording_until_safe_completion(system):
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
    for _ in range(100):
        if sim.state.run["state"] == "preparing":
            break
        await asyncio.sleep(0.01)
    assert sim.state.run["state"] == "preparing", (await pending).text if pending.done() else client.stats
    await sim.tick()
    response = await pending
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
    await sim.raise_alarm("clock_unsynced", severity="warning")
    for _ in range(100):
        alarms = (await http.get("/api/alarms/active")).json()["data"]
        if alarms:
            break
        await asyncio.sleep(0.01)
    assert len(alarms) == 1
    alarm = alarms[0]
    assert alarm["occurrence_seq"] == "1" and len(alarm["wire_alarm_id"]) == 32
    response = await http.post(f"/api/alarms/{alarm['id']}/ack")
    assert response.status_code == 200, response.text
    assert response.json()["data"]["device_confirmed"]
    await sim.clear_alarm(alarm["wire_alarm_id"])
    await asyncio.sleep(0.05)
    assert not (await http.get("/api/alarms/active")).json()["data"]
    await sim.raise_alarm("measurement_sensor_invalid", severity="warning")
    await asyncio.sleep(0.05)
    alarm = (await http.get("/api/alarms/active")).json()["data"][0]
    await client.close()
    response = await http.post(f"/api/alarms/{alarm['id']}/ack")
    assert response.status_code == 503
    unchanged = (await http.get("/api/alarms/active")).json()["data"][0]
    assert unchanged["ack_time"] is None


async def test_stop_bypasses_a_lost_ordinary_receipt_at_both_http_and_wire_layers(system):
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
    await sim.raise_alarm("clock_unsynced", severity="warning")
    await asyncio.sleep(0.05)
    alarm = (await http.get("/api/alarms/active")).json()["data"][0]
    sim.drop_reply("command_result")
    pending = asyncio.create_task(http.post(f"/api/alarms/{alarm['id']}/ack"))
    await asyncio.sleep(0.1)
    try:
        result = await asyncio.wait_for(command(http, "stop_test", {}, "priority-stop"), 1.0)
        assert result.status_code == 200, result.text
        assert not pending.done()
        assert sim.state.run["state"] in {"safe_disposal", "cooling"}
    finally:
        await pending


async def test_stop_uses_current_session_run_identity_when_all_read_slots_are_busy(system):
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
    sim.drop_reply("status_snapshot", count=4)
    reads = [asyncio.create_task(client.transport.request("get_status", {})) for _ in range(4)]
    await asyncio.sleep(0.03)
    try:
        result = await asyncio.wait_for(command(http, "stop_test", {}, "busy-read-stop"), 1)
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
    for _ in range(100):
        if epoch in sim.state.data["highwater"]:
            break
        await asyncio.sleep(0.01)
    sequence = sim.state.data["highwater"][epoch]
    sim.evict_result(epoch, sequence)
    reply = await pending
    assert reply.status_code == 504, reply.text
    assert sim.state.data["active_recipe"] is None
    for _ in range(400):
        if client.is_online and client._ready:
            break
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
    for _ in range(100):
        task = (await http.get(f"/api/system/maintenance/source-logs/{task_id}")).json()["data"]
        if task["status"] in {"completed", "failed"}:
            break
        await asyncio.sleep(0.01)
    assert task["status"] == "completed", task
    assert (
        await http.post("/api/system/maintenance/source-logs", json={"first_record_seq": str(2**64)})
    ).status_code == 422


async def test_cleared_unacknowledged_alarm_can_be_confirmed_before_run_ack(system):
    http, client, sim, factory = system
    recipe = await deploy(http)
    await sim.raise_alarm("clock_unsynced", severity="warning")
    await asyncio.sleep(0.05)
    alarm = (await http.get("/api/alarms/active")).json()["data"][0]
    await sim.clear_alarm(alarm["wire_alarm_id"])
    await asyncio.sleep(0.05)
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
    acknowledged = await http.post(f"/api/alarms/{alarm['id']}/ack")
    assert acknowledged.status_code == 200, acknowledged.text
    assert acknowledged.json()["data"]["device_confirmed"] is True
    snapshot = await client.get_status()
    assert snapshot["alarm"]["ack_required"] is False
    assert snapshot["system"]["can_ack_run"] is True
    finished = await command(http, "ack_run", {}, "alarm-history-finish")
    assert finished.status_code == 200, finished.text
    assert sim.state.run["state"] == "idle"
