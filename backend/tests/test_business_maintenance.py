"""Installer authorization drains accepted writes and blocks new business work."""

import asyncio
import json
from contextlib import suppress

import httpx
import pytest
from fastapi import BackgroundTasks, Depends
from sqlalchemy import func, select

from app.api.deps import CurrentUser, get_current_user
from app.db.database import get_db
from app.db.models import UserAccount
from app.main import create_app
from app.services.background_jobs import BackgroundJobManager
from app.services.maintenance_service import MaintenanceBlockedError, MaintenanceManager


@pytest.fixture
def manager(tmp_path, monkeypatch):
    result = MaintenanceManager()
    result.configure_upgrade(tmp_path / "maintenance.json")
    monkeypatch.setattr("app.main.maintenance_manager", result)
    return result


@pytest.fixture
async def client(manager, db_session):
    app = create_app()

    async def database():
        yield db_session

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = lambda: CurrentUser("admin", "admin")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as result:
        yield result


@pytest.mark.parametrize("state", ["prepared", "claimed"])
async def test_restart_gate_blocks_account_writes_but_keeps_reads(client, manager, db_session, state):
    manager._upgrade_path.write_text(json.dumps({"state": state}), encoding="utf-8")
    response = await client.post(
        "/api/users", json={"username": "newuser", "password": "Newuser-Password123", "role": "observer"}
    )
    assert response.status_code == 503
    assert response.json()["error_code"] == "maintenance_active"
    assert await db_session.scalar(select(func.count()).select_from(UserAccount)) == 0
    assert (await client.get("/health")).status_code == 200
    assert (await client.get("/api/users")).json()["data"] == []
    manager._upgrade_path.unlink()
    response = await client.post(
        "/api/users", json={"username": "newuser", "password": "Newuser-Password123", "role": "observer"}
    )
    assert response.status_code == 200
    assert await db_session.scalar(select(func.count()).select_from(UserAccount)) == 1


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/api/auth/login"),
        ("POST", "/api/auth/logout"),
        ("POST", "/api/recipes"),
        ("PUT", "/api/recipes/saved"),
        ("POST", "/api/reports/generate"),
        ("POST", "/api/logs/export"),
        ("POST", "/api/run-recoveries/run/binding"),
        ("POST", "/api/run-recoveries/run/replays"),
        ("PATCH", "/api/tests/TEST-20260913-001/metadata"),
        ("POST", "/api/system/backup"),
    ],
)
async def test_every_business_entry_is_rejected_before_dependencies(client, manager, method, path):
    manager._upgrade_path.write_text('{"state":"claimed"}', encoding="utf-8")
    response = await client.request(method, path, json={})
    assert response.status_code == 503
    assert response.json()["error_code"] == "maintenance_active"


async def test_installer_drains_writes_without_deadlocking_nested_commands(manager):
    entered, release, authorized = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def business_write():
        with manager.business_guard():
            entered.set()
            await release.wait()
            async with manager.command_guard():
                assert not authorized.is_set()

    async def installer():
        async with manager.installer_guard():
            authorized.set()
            manager._write_upgrade({"state": "claimed"})

    write_task = asyncio.create_task(business_write())
    await asyncio.wait_for(entered.wait(), 1)
    installing = asyncio.create_task(installer())
    await asyncio.sleep(0)
    with pytest.raises(MaintenanceBlockedError):
        with manager.business_guard():
            pytest.fail("new request overtook installer drain")
    assert not authorized.is_set()
    release.set()
    await asyncio.wait_for(asyncio.gather(write_task, installing), 1)


async def test_cancelled_installer_reopens_admission_without_cancelling_work(manager):
    with manager.business_guard():

        async def installer():
            async with manager.installer_guard():
                pytest.fail("active write has not drained")

        installing = asyncio.create_task(installer())
        await asyncio.sleep(0)
        installing.cancel()
        with suppress(asyncio.CancelledError):
            await installing
        with manager.business_guard():
            pass
    assert manager.upgrade_state()["state"] == "idle"


async def test_submitted_background_jobs_are_drained_even_before_task_starts(manager):
    jobs = BackgroundJobManager(maintenance=manager)
    release, finished = asyncio.Event(), asyncio.Event()

    async def report():
        await release.wait()
        finished.set()
        return {"report_id": 1}

    job_id = jobs.submit("report", report)

    async def installer():
        async with manager.installer_guard():
            assert finished.is_set()

    installing = asyncio.create_task(installer())
    await asyncio.sleep(0)
    assert not installing.done()
    with pytest.raises(MaintenanceBlockedError):
        jobs.submit("report", report)
    release.set()
    await asyncio.wait_for(installing, 1)
    assert jobs.snapshot(job_id)["status"] == "completed"
    await jobs.shutdown()


async def test_cancelled_queued_job_does_not_leak_admission(manager):
    jobs = BackgroundJobManager(maintenance=manager)

    async def report():
        pytest.fail("job cancelled before its coroutine starts")

    jobs.submit("report", report)
    await jobs.shutdown()
    async with asyncio.timeout(1), manager.installer_guard():
        pass


async def test_complete_http_lifetime_is_drained_including_response_background(manager):
    app = create_app()
    finalizing, finish, dependency_closed = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def dependency():
        try:
            yield
        finally:
            dependency_closed.set()

    async def persist_result():
        finalizing.set()
        await finish.wait()

    @app.post("/api/write-lifetime", dependencies=[Depends(dependency)])
    async def write_lifetime(tasks: BackgroundTasks):
        tasks.add_task(persist_result)
        return {"accepted": True}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        writing = asyncio.create_task(client.post("/api/write-lifetime"))
        await asyncio.wait_for(finalizing.wait(), 1)

        async def installer():
            async with manager.installer_guard():
                assert dependency_closed.is_set()
                manager._write_upgrade({"state": "claimed"})

        installing = asyncio.create_task(installer())
        await asyncio.sleep(0)
        assert not installing.done()
        assert (await client.post("/api/write-lifetime")).status_code == 503
        assert (await client.get("/health")).status_code == 200
        finish.set()
        response, _ = await asyncio.wait_for(asyncio.gather(writing, installing), 1)
        assert response.status_code == 200


async def test_priority_command_remains_independent_of_other_business_requests(manager):
    held, release, stopped = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def ordinary():
        with manager.business_guard():
            async with manager.command_guard():
                held.set()
                await release.wait()

    async def stop():
        with manager.business_guard():
            async with manager.command_guard(priority=True):
                stopped.set()

    task = asyncio.create_task(ordinary())
    await held.wait()
    await asyncio.wait_for(stop(), 1)
    assert stopped.is_set() and not task.done()
    release.set()
    await task


async def test_recovery_worker_pauses_projection_at_durable_gate(manager, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.services.v2_recovery_worker import V2RecoveryWorker

    monkeypatch.setattr("app.services.v2_recovery_worker.maintenance_manager", manager)
    manager._write_upgrade({"state": "prepared"})
    projected = asyncio.Event()

    async def replay(_case):
        projected.set()

    worker = V2RecoveryWorker(SimpleNamespace(replay_batch=replay))
    worker.bootstrapped = True
    worker._pending = AsyncMock(return_value="case")
    await worker.start()
    try:
        for _ in range(4):
            await asyncio.sleep(0)
        assert worker._pending.await_count == 1
        assert not projected.is_set()
        manager._upgrade_path.unlink()
        await asyncio.wait_for(projected.wait(), 2)
    finally:
        await worker.close()
