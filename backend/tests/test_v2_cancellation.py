"""A cancelled cursor must finish its transaction before another writer is admitted."""

import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiosqlite
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.cancellation import finish_db_work
from app.db.models import Base
from app.hostcomm.v2_client import V2Client
from app.services.v2_operations import V2OperationCoordinator
from app.services.v2_source_logs import V2SourceLogStore


@pytest.mark.parametrize("journal_mode", ["DELETE", "WAL"])
@pytest.mark.parametrize("cancel_from", ["direct", "close", "reconnect"])
async def test_source_cancellation_drains_open_cursor_before_releasing_writer(
    tmp_path, monkeypatch, journal_mode, cancel_from
):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'cancel.db'}", connect_args={"timeout": 0.1})
    async with engine.begin() as connection:
        await connection.execute(text(f"PRAGMA journal_mode={journal_mode}"))
        await connection.run_sync(Base.metadata.create_all)
        await connection.execute(text("CREATE TABLE writer_probe (value TEXT)"))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    store = V2SourceLogStore(factory, device_id="a" * 32)
    vectors = json.loads(
        (Path(__file__).resolve().parents[2] / "contracts/hostcomm/v2/vectors.json").read_text(encoding="utf-8")
    )
    frame = copy.deepcopy(next(row["value"] for row in vectors["valid_messages"] if row["name"] == "telemetry"))
    await store.ingest_live(frame)
    cursor_open, release = asyncio.Event(), asyncio.Event()
    original = aiosqlite.Cursor.execute

    async def pause_after_select(cursor, sql, parameters=None):
        result = await original(cursor, sql, parameters)
        if "FROM v2_source_record" in sql and not cursor_open.is_set():
            cursor_open.set()
            await release.wait()  # Real SQLite statement exists; SQLAlchemy has not fetched/closed it.
        return result

    monkeypatch.setattr(aiosqlite.Cursor, "execute", pause_after_select)
    task = asyncio.create_task(store.ingest_live(frame))
    try:
        await asyncio.wait_for(cursor_open.wait(), 2)
        shutdown = None
        if cancel_from == "direct":
            task.cancel()
        else:
            client = V2Client("127.0.0.1", 34211, factory=factory, device_id="a" * 32, client_version="test")
            client._recovery_task = task
            client.transport = SimpleNamespace(close=AsyncMock())
            shutdown = asyncio.create_task(
                client.close() if cancel_from == "close" else client._on_connection({"status": "offline"})
            )
        await asyncio.sleep(0)
        task.cancel()  # Close and reconnect can both request cancellation.
        release.set()
        if shutdown:
            await asyncio.wait_for(shutdown, 2)
        result = await asyncio.gather(task, return_exceptions=True)
        assert isinstance(result[0], asyncio.CancelledError)
        assert not store.write_lock.locked()
        async with engine.begin() as connection:
            await connection.execute(text("INSERT INTO writer_probe VALUES ('after cancellation')"))
            assert await connection.scalar(text("SELECT count(*) FROM writer_probe")) == 1
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await engine.dispose()


@pytest.mark.parametrize("cancel_stage", ["intent", "mark_sent"])
async def test_cancelled_command_database_work_never_continues_to_network(tmp_path, monkeypatch, cancel_stage):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'intent.db'}", connect_args={"timeout": 0.1})
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    transport = SimpleNamespace(is_online=True, boot_id="1" * 32, request=AsyncMock())
    coordinator = V2OperationCoordinator(
        factory, transport, device_id="a" * 32, controller_id="b" * 32, controller_epoch="c" * 32
    )
    await coordinator.initialize()
    writing, release = asyncio.Event(), asyncio.Event()
    original = aiosqlite.Cursor.execute
    target = "UPDATE v2_controller_identity" if cancel_stage == "intent" else "UPDATE v2_operation SET"

    async def pause_write(cursor, sql, parameters=None):
        result = await original(cursor, sql, parameters)
        if sql.startswith(target) and not writing.is_set():
            writing.set()
            await release.wait()
        return result

    monkeypatch.setattr(aiosqlite.Cursor, "execute", pause_write)
    task = asyncio.create_task(
        coordinator.submit("acquire_lease", {"lease_ms": 8000}, actor="admin", role="admin", operation_id="d" * 32)
    )
    try:
        await asyncio.wait_for(writing.wait(), 2)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        release.set()
        result = await asyncio.gather(task, return_exceptions=True)
        assert isinstance(result[0], asyncio.CancelledError)
        transport.request.assert_not_awaited()
        row = await coordinator.get("d" * 32)
        assert row["status"] == ("pending" if cancel_stage == "intent" else "unknown")
        assert row["command_seq"] == "1"
        async with engine.begin() as connection:
            await connection.execute(text("UPDATE v2_controller_identity SET last_seq=last_seq"))
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await engine.dispose()


async def test_cancelled_database_failure_rolls_back_and_preserves_cancellation(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'rollback.db'}")
    async with engine.begin() as connection:
        await connection.execute(text("CREATE TABLE sample (value TEXT)"))
    entered, release = asyncio.Event(), asyncio.Event()

    @finish_db_work
    async def transaction():
        async with engine.begin() as connection:
            await connection.execute(text("INSERT INTO sample VALUES ('must rollback')"))
            entered.set()
            await release.wait()
            raise ValueError("invalid source")

    task = asyncio.create_task(transaction())
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    async with engine.begin() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM sample")) == 0
        await connection.execute(text("INSERT INTO sample VALUES ('available')"))
    await engine.dispose()


async def test_database_unit_internal_cancellation_is_not_swallowed():
    @finish_db_work
    async def cancelled():
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(cancelled(), 1)
