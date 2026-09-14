"""Scenario assertions must reject unrelated installer failures and changed archives."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from smd_bench import maintenance
from smd_desktop.installer_authorization import request_digest


def offline_scenes(api, close):
    """Only the UI navigation is stubbed; no installer may run before admission."""
    page = SimpleNamespace(
        get_by_role=Mock(return_value=SimpleNamespace(wait_for=AsyncMock())),
        locator=Mock(return_value=SimpleNamespace(count=AsyncMock(return_value=0))),
    )
    return SimpleNamespace(
        installation=SimpleNamespace(),
        state=AsyncMock(),
        stage=Mock(),
        ui=SimpleNamespace(go=AsyncMock(), page=page, api=api),
        worker=SimpleNamespace(close=close),
    )


@pytest.mark.asyncio
async def test_offline_confirmation_uses_current_api_link_not_last_v2_snapshot(monkeypatch):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from smd_bench.browser import eventually

    from app.api.deps import CurrentUser, get_current_user
    from app.api.routes import status as status_route
    from app.services.cache import StatusCache
    from app.services.maintenance_service import MaintenanceManager

    cache = StatusCache()
    await cache.update({"system": {"protocol_version": "2.0", "current_state": "idle"}, "_v2": {"online": True}})
    link = SimpleNamespace(is_online=True)
    monkeypatch.setattr(status_route, "status_cache", cache)
    monkeypatch.setattr(status_route, "maintenance_manager", MaintenanceManager())
    app = FastAPI()
    app.include_router(status_route.router)
    app.state.hostcomm_client = link
    app.dependency_overrides[get_current_user] = lambda: CurrentUser("admin", "admin")
    observations = []

    async def close():
        # The production disconnect callback invalidates freshness, retaining the
        # last board snapshot for display. An offline board cannot send a new one.
        link.is_online = False
        cache.invalidate()

    async def short_wait(function, predicate):
        return await eventually(function, predicate, timeout=0.02)

    class OfflineObserved(Exception):
        pass

    def snapshot(_installation):
        raise OfflineObserved

    monkeypatch.setattr(maintenance, "eventually", short_wait)
    monkeypatch.setattr(maintenance, "_snapshot", snapshot)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:

        async def api(path):
            response = await client.get(path)
            assert response.status_code == 200
            value = response.json()["data"]
            observations.append(value)
            return value

        with pytest.raises(OfflineObserved):
            await maintenance.offline_confirmation(offline_scenes(api, close))
    assert observations[-1]["comm_quality"] == "offline"
    assert observations[-1]["data_fresh"] is False and observations[-1]["control_ready"] is False
    assert observations[-1]["_v2"]["online"] is True  # Historical snapshot is deliberately retained.


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [
        {},
        {"comm_quality": "online", "control_ready": False, "data_fresh": False},
        {"comm_quality": "degraded", "control_ready": False, "data_fresh": False},
        {"comm_quality": "offline", "control_ready": True, "data_fresh": False},
        {"comm_quality": "offline", "control_ready": False, "data_fresh": True},
        {"comm_quality": "offline", "control_ready": False},
    ],
)
async def test_offline_confirmation_never_accepts_missing_or_contradictory_live_evidence(monkeypatch, status):
    from smd_bench.browser import eventually

    async def short_wait(function, predicate):
        return await eventually(function, predicate, timeout=0.01)

    snapshot = Mock(side_effect=AssertionError("installer preparation must not start"))
    monkeypatch.setattr(maintenance, "eventually", short_wait)
    monkeypatch.setattr(maintenance, "_snapshot", snapshot)
    status = {**status, "_v2": {"online": False}}
    with pytest.raises(TimeoutError):
        await maintenance.offline_confirmation(offline_scenes(AsyncMock(return_value=status), AsyncMock()))
    snapshot.assert_not_called()


def request_and_reply(tmp_path, *, reason="device_busy", confirmed=True):
    request = {
        "transaction_id": "a" * 32,
        "physical_shutdown_confirmed": confirmed,
        "current_version": "0.3.0",
        "target_version": "0.3.0",
    }
    reply = {
        "transaction_id": request["transaction_id"],
        "request_sha256": request_digest(request),
        "state": "blocked",
        "reason_code": reason,
        "gate": {"token": "secret", "db_path": "private"},
    }
    (tmp_path / "maintenance-request.json").write_text(json.dumps(request), encoding="utf-8")
    (tmp_path / "maintenance-reply.json").write_text(json.dumps(reply), encoding="utf-8")
    return request, reply


@pytest.mark.parametrize(
    "code,reason,pid,run",
    [
        (1, "version_mismatch", 5, "run"),
        (20, "device_busy", 5, "run"),
        (1, "device_busy", 6, "run"),
        (1, "device_busy", 5, "another"),
    ],
)
@pytest.mark.asyncio
async def test_busy_scenario_never_accepts_an_unrelated_error_or_service_cutover(
    tmp_path, monkeypatch, code, reason, pid, run
):
    request_and_reply(tmp_path, reason=reason)
    snapshots = iter([{"pid": 5}, {"pid": pid}])
    monkeypatch.setattr(maintenance, "_snapshot", lambda install: next(snapshots))
    install = SimpleNamespace(data=tmp_path, version="0.3.0", maintenance_attempt=Mock(return_value=code))
    scenes = SimpleNamespace(
        installation=install,
        stage=Mock(),
        state=AsyncMock(return_value={"run": {"run_id": "run"}}),
        worker=SimpleNamespace(request=AsyncMock(return_value={"run": {"run_id": run, "state": "cooling"}})),
    )
    with pytest.raises(AssertionError):
        await maintenance.busy_rejected(scenes, "cooling")
    install.maintenance_attempt.assert_called_once_with(physical_shutdown_confirmed=True)


@pytest.mark.asyncio
async def test_busy_scenario_requires_current_bound_reply_and_preserves_public_whitelist(tmp_path, monkeypatch):
    request_and_reply(tmp_path)
    monkeypatch.setattr(maintenance, "_snapshot", lambda install: {"pid": 5})
    scenes = SimpleNamespace(
        installation=SimpleNamespace(data=tmp_path, version="0.3.0", maintenance_attempt=lambda **kwargs: 1),
        stage=Mock(),
        state=AsyncMock(return_value={"run": {"run_id": "run"}}),
        worker=SimpleNamespace(request=AsyncMock(return_value={"run": {"run_id": "run", "state": "measuring"}})),
    )
    result = await maintenance.busy_rejected(scenes, "measuring")
    assert result["exit_code"] == 1 and result["same_run_id"] is True
    assert "secret" not in json.dumps(result) and "private" not in json.dumps(result)
    request, reply = request_and_reply(tmp_path)
    reply["transaction_id"] = "different"
    (tmp_path / "maintenance-reply.json").write_text(json.dumps(reply), encoding="utf-8")
    with pytest.raises(AssertionError, match="current, matching"):
        await maintenance.busy_rejected(scenes, "measuring")


@pytest.mark.asyncio
async def test_archive_snapshot_uses_actual_api_records_counts_and_report_bytes():
    payload = b"existing report bytes"
    calls = []

    async def api(path):
        calls.append(path)
        return {
            "/api/tests?page=1&size=100": [{"test_id": "BENCH-TEST", "end_time": "completed"}],
            "/api/tests/BENCH-TEST": {"sample_count": 42},
            "/api/reports": [{"id": 8, "format": "html"}],
        }[path]

    client = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(status_code=200, content=payload)))
    ui = SimpleNamespace(api=api, api_client=client)
    first = await maintenance._archives(ui)
    assert first["sample_counts"] == {"BENCH-TEST": 42}
    client.get.assert_called_once_with("/api/reports/8/download")
    client.get.return_value.content = b"unexpected replacement"
    assert await maintenance._archives(ui) != first
    assert all(path.startswith("/api/") for path in calls)


@pytest.mark.asyncio
async def test_archive_preservation_cannot_pass_with_no_experiments():
    with pytest.raises(AssertionError, match="actual completed"):
        await maintenance._archives(SimpleNamespace(api=AsyncMock(return_value=[])))


def test_repair_attempt_verifies_installer_identity_and_passes_confirmation_flag(tmp_path, monkeypatch):
    from smd_bench import installation as module
    from smd_bench.package import sha256

    installer = tmp_path / "installer.exe"
    installer.write_bytes(b"verified package")
    install = module.Installation(
        "a" * 32, {"program_files": tmp_path / "program", "program_data": tmp_path / "data"}, "0.3.0"
    )
    install.installer = installer
    install.state["installer_sha256"] = sha256(installer)
    monkeypatch.setattr(module, "check_claim", lambda *args: None)
    launch = Mock(return_value=20)
    monkeypatch.setattr(module, "checked_process", launch)
    assert install.maintenance_attempt(physical_shutdown_confirmed=True) == 20
    assert launch.call_args.args[1] == ["/PHYSICALSHUTDOWN=1", str(install.install)]
    assert launch.call_args.kwargs == {"nsis": True}
    installer.write_bytes(b"different package")
    with pytest.raises(ValueError, match="bytes changed"):
        install.maintenance_attempt(physical_shutdown_confirmed=False)
    assert launch.call_count == 1


@pytest.mark.asyncio
async def test_installer_wait_does_not_block_the_async_scenario_loop(tmp_path, monkeypatch):
    import asyncio
    import time

    request_and_reply(tmp_path)
    monkeypatch.setattr(maintenance, "_snapshot", lambda install: {"pid": 5})
    entered = asyncio.Event()
    loop = asyncio.get_running_loop()

    def installer(**kwargs):
        loop.call_soon_threadsafe(entered.set)
        time.sleep(0.2)
        return 1

    scenes = SimpleNamespace(
        installation=SimpleNamespace(data=tmp_path, version="0.3.0", maintenance_attempt=installer),
        stage=Mock(),
        state=AsyncMock(return_value={"run": {"run_id": "run"}}),
        worker=SimpleNamespace(request=AsyncMock(return_value={"run": {"run_id": "run", "state": "measuring"}})),
    )
    task = asyncio.create_task(maintenance.busy_rejected(scenes, "measuring"))
    await entered.wait()
    ticks = 0
    while not task.done():
        await asyncio.sleep(0.01)
        ticks += 1
    assert (await task)["exit_code"] == 1 and ticks >= 5


def test_scenario_evidence_binds_ci_execution_without_collecting_secrets(tmp_path):
    result = {"ci_run_id": "12345", "commit": "a" * 40, "installer_sha256": "b" * 64}
    scenes = SimpleNamespace(
        ui=SimpleNamespace(evidence=tmp_path),
        run_id="c" * 32,
        installation=SimpleNamespace(version="0.3.0"),
        result=result,
        passed=Mock(),
    )
    maintenance._record(scenes, "busy_rejected", [{"exit_code": 1}])
    log = json.loads((tmp_path / "installer-busy-rejected.json").read_text())
    assert log["ci_run_id"] == "12345"
    assert scenes.passed.call_args.kwargs["evidence"]["sha256"] == maintenance.sha256(
        tmp_path / "installer-busy-rejected.json"
    )
