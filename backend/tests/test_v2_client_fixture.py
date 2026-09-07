"""Shared TCP fixture owns partial startup and waits for durable recovery."""

import asyncio
import sqlite3

import pytest
from sqlalchemy import event, func, select

from app.db.v2_models import V2SourceRecord
from app.hostcomm.v2_client import V2Client
from app.hostcomm.v2_simulator import V2Simulator
from app.services.v2_archive import V2ArchiveProjector
from tests import test_v2_client as fixture_module


@pytest.fixture
async def resources(monkeypatch):
    created = {"V2Client": [], "V2Simulator": [], "create_async_engine": []}
    open_connections = set()

    def capture(name):
        original = getattr(fixture_module, name)

        def construct(*args, **kwargs):
            item = original(*args, **kwargs)
            created[name].append(item)
            if name == "create_async_engine":
                event.listen(
                    item.sync_engine, "connect", lambda connection, _record: open_connections.add(id(connection))
                )
                event.listen(
                    item.sync_engine, "close", lambda connection, _record: open_connections.discard(id(connection))
                )
            return item

        monkeypatch.setattr(fixture_module, name, construct)

    for name in created:
        capture(name)
    created["open_connections"] = open_connections
    try:
        yield created
    finally:
        # The regression tests must also clean up the unfixed fixture's leaks.
        for client in created["V2Client"]:
            await client.close()
        for simulator in created["V2Simulator"]:
            await simulator.close()
        for engine in created["create_async_engine"]:
            await engine.dispose()


def assert_closed(resources):
    for client in resources["V2Client"]:
        assert client._closed
        if client.transport:
            assert not client.transport.is_online
            for task in (
                client.transport._main_task,
                client.transport._callback_task,
                client.transport._connection_task,
            ):
                assert task is None or task.done()
    for simulator in resources["V2Simulator"]:
        assert simulator._closed
        assert simulator._server is None or not simulator._server.is_serving()
        with pytest.raises(sqlite3.ProgrammingError):
            simulator.store.db.execute("SELECT 1")
    assert not resources["open_connections"]


async def test_connected_waits_for_archive_slower_than_one_second(tmp_path, monkeypatch, resources):
    entered, release = asyncio.Event(), asyncio.Event()
    original = V2ArchiveProjector.on_record

    async def delayed_record(self, *args, **kwargs):
        entered.set()
        await release.wait()
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(V2ArchiveProjector, "on_record", delayed_record)
    fixture = fixture_module.connected.__wrapped__(tmp_path)
    setup = asyncio.create_task(anext(fixture))
    try:
        await asyncio.wait_for(entered.wait(), 15)
        await asyncio.sleep(1.25)
        assert not setup.done(), "valid durable recovery must not fail at the old one-second fixture budget"
        client = resources["V2Client"][0]
        assert client.is_online and not client._ready
        assert client.transport.heartbeat_interval == 2
        assert client.transport.response_timeout == 3
        assert client.transport.lease_id is None
        release.set()
        client, simulator, factory = await asyncio.wait_for(setup, 10)
        assert client._ready and (await client.get_status())["_v2"]["online"]
        async with factory() as db:
            assert await db.scalar(select(func.count()).select_from(V2SourceRecord)) > 0
    finally:
        release.set()
        setup.cancel()
        await asyncio.gather(setup, return_exceptions=True)
        await fixture.aclose()
    assert_closed(resources)


@pytest.mark.parametrize("stage", ["simulator", "client"])
async def test_connected_closes_resources_when_start_raises(tmp_path, monkeypatch, resources, stage):
    target = V2Simulator if stage == "simulator" else V2Client
    original = target.start

    async def fail_after_start(self):
        await original(self)
        raise RuntimeError("injected partial startup failure")

    monkeypatch.setattr(target, "start", fail_after_start)
    fixture = fixture_module.connected.__wrapped__(tmp_path)
    try:
        with pytest.raises(RuntimeError, match="partial startup"):
            await anext(fixture)
        assert_closed(resources)
    finally:
        await fixture.aclose()


@pytest.mark.parametrize("cancel", [False, True])
async def test_connected_never_ready_is_bounded_and_closes_resources(tmp_path, monkeypatch, resources, cancel):
    entered = asyncio.Event()

    async def never_recovers(self, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(V2Client, "recover_logs", never_recovers)
    fixture = fixture_module.connected.__wrapped__(tmp_path)
    setup = asyncio.create_task(anext(fixture))
    try:
        await asyncio.wait_for(entered.wait(), 15)
        if cancel:
            setup.cancel()
            with pytest.raises(asyncio.CancelledError):
                await setup
        else:
            with pytest.raises(TimeoutError, match="fixture recovery timed out"):
                await asyncio.wait_for(asyncio.shield(setup), 15)
        assert_closed(resources)
    finally:
        setup.cancel()
        await asyncio.gather(setup, return_exceptions=True)
        await fixture.aclose()
