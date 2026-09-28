"""Durable operations must remain safe across retries, concurrency and restarts."""

import asyncio
import copy
import json
import os
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path

import aiosqlite
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.database import _create_engine
from app.db.models import Base
from app.db.v2_models import V2ControllerIdentity, V2Operation, V2OperationReview
from app.hostcomm.v2_contract.codec import command_digest
from app.hostcomm.v2_lease import LeaseContext
from app.services.v2_operations import V2OperationCoordinator, ensure_v2_identity


@pytest.fixture
async def factory(tmp_path):
    engine = _create_engine(f"sqlite+aiosqlite:///{tmp_path}/v2.db")
    async with engine.begin() as connection:
        # Keep schema DDL in one durable transaction, preserving runtime DML behavior.
        await connection.exec_driver_sql("BEGIN")
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


class Transport:
    boot_id = "1" * 32
    session_id = "2" * 32
    is_online = True
    hello_payload = {"last_command_seq": "0", "granted_role": "control"}

    def __init__(self, factory):
        self.factory, self.sent = factory, []
        self.timeout = False
        self.block = None
        self.leases = LeaseContext("4" * 32)
        self.leases.reset(self.session_id, self.boot_id)

    @property
    def lease(self):
        return self.leases.lease_id

    def lease_token(self):
        return self.leases.token()

    def confirm_lease(self, frame, token, lease_id):
        try:
            return self.leases.confirm(frame, token, lease_id, started=100, now=100)
        except ValueError:
            return False

    async def request(self, kind, payload, **kwargs):
        self.sent.append((kind, copy.deepcopy(payload), kwargs))
        if kind == "command":
            async with self.factory() as db:
                stored = await db.get(V2Operation, payload["operation_id"])
                assert stored is not None and stored.status == "sent"
                assert stored.msg_id == kwargs["msg_id"]
            if self.block and payload["command"] != "stop_run":
                await self.block.wait()
            if self.timeout:
                raise TimeoutError("lost receipt")
        result = {
            "controller_epoch": payload["controller_epoch"],
            "operation_id": payload["operation_id"],
            "command_seq": payload["command_seq"],
            "request_digest": payload.get("request_digest"),
            "result_boot_id": self.boot_id,
            "status": "applied",
            "reason": "ok",
            "state_revision": "2",
            "run_id": payload.get("params", {}).get("run_id"),
            "lease_id": None,
            "lease_expires_uptime_ms": None,
        }
        return {"type": "command_result" if kind == "command" else "operation_snapshot", "payload": result}


async def coordinator(factory, transport=None, *, write_lock=None):
    transport = transport or Transport(factory)
    value = V2OperationCoordinator(
        factory, transport, device_id="3" * 32, controller_id="4" * 32, controller_epoch="5" * 32, write_lock=write_lock
    )
    await value.initialize()
    return value, transport


async def stop(value, operation_id=None):
    return await value.submit(
        "stop_run",
        {"run_id": "6" * 32, "reason": "operator_stop"},
        actor="alice",
        role="operator",
        operation_id=operation_id,
    )


async def test_intent_exists_before_wire_and_retry_never_resends(factory):
    value, transport = await coordinator(factory)
    operation_id = uuid.uuid4().hex
    first = await stop(value, operation_id)
    second = await stop(value, operation_id)
    assert first["status"] == second["status"] == "applied"
    assert len(transport.sent) == 1
    assert first["command_seq"] == "1"


async def test_concurrent_commands_share_production_writer_and_keep_unique_sequences(factory):
    # The production V2Client shares its short-write lock across operation and archive services.
    write_lock = asyncio.Lock()
    a, _ = await coordinator(factory, write_lock=write_lock)
    b, _ = await coordinator(factory, write_lock=write_lock)
    results = await asyncio.gather(*(stop(a if i % 2 else b) for i in range(12)), return_exceptions=True)
    assert all(isinstance(row, dict) and row["status"] == "applied" for row in results), results
    assert sorted(int(row["command_seq"]) for row in results) == list(range(1, 13))


async def test_independent_allocators_reserve_writer_before_reading_sequence(factory, monkeypatch):
    a, first_wire = await coordinator(factory)
    b, second_wire = await coordinator(factory)
    assert a.write_lock is not b.write_lock
    reserved, contender_started, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    loop = asyncio.get_running_loop()
    execute = aiosqlite.Connection._execute
    connections = []

    async def observe_reservation(connection, function, *args, **kwargs):
        if not args or not isinstance(args[0], str) or not args[0].startswith("UPDATE v2_controller_identity SET"):
            return await execute(connection, function, *args, **kwargs)
        connections.append(connection)
        current = len(connections)
        if current == 2:
            original = function

            def function(*values, **options):
                # The contender is in its own SQLite worker, not queued on the first allocator's asyncio lock.
                loop.call_soon_threadsafe(contender_started.set)
                return original(*values, **options)

        result = await execute(connection, function, *args, **kwargs)
        if current == 1:
            reserved.set()  # The first real UPDATE has reserved the SQLite writer, before the counter SELECT.
            await release.wait()
        return result

    monkeypatch.setattr(aiosqlite.Connection, "_execute", observe_reservation)
    tasks = []
    try:
        tasks.append(asyncio.create_task(stop(a)))
        await asyncio.wait_for(reserved.wait(), 10)
        tasks.append(asyncio.create_task(stop(b)))
        await asyncio.wait_for(contender_started.wait(), 10)
        assert connections[0] is not connections[1]
        assert not tasks[1].done() and not first_wire.sent and not second_wire.sent
        release.set()
        first, second = await asyncio.wait_for(asyncio.gather(*tasks), 15)
        assert [first["command_seq"], second["command_seq"]] == ["1", "2"]
        assert first["status"] == second["status"] == "applied"
        assert len(first_wire.sent) == len(second_wire.sent) == 1
        async with factory() as db:
            rows = list((await db.scalars(select(V2Operation))).all())
            identity = await db.get(V2ControllerIdentity, "3" * 32)
        assert identity.last_seq == "2" and len(rows) == 2
        assert {row.operation_id: row.command_seq for row in rows} == {
            first["operation_id"]: "1",
            second["operation_id"]: "2",
        }
        for row in rows:
            request = json.loads(row.request_json)
            result = json.loads(row.result_json)
            assert row.status == result["status"] == "applied"
            assert row.command_seq == request["command_seq"] == result["command_seq"]
            assert row.operation_id == request["operation_id"] == result["operation_id"]
            assert row.controller_epoch == request["controller_epoch"] == result["controller_epoch"]
            assert row.request_digest == command_digest(request) == result["request_digest"]
    finally:
        release.set()
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 15)


async def test_timeout_restart_and_unknown_request_do_not_retry_side_effect(factory):
    value, transport = await coordinator(factory)
    transport.timeout = True
    result = await stop(value)
    assert result["status"] == "unknown"
    fresh, new_transport = await coordinator(factory)
    stored = await fresh.get(result["operation_id"])
    assert stored["status"] == "unknown"
    assert not new_transport.sent
    assert (await stop(fresh, result["operation_id"]))["status"] == "unknown"
    assert not new_transport.sent


async def test_identity_and_operator_conflicts_do_not_send(factory):
    value, transport = await coordinator(factory)
    result = await stop(value)
    with pytest.raises(ValueError, match="conflict"):
        await value.submit(
            "stop_run",
            {"run_id": "7" * 32, "reason": "operator_stop"},
            actor="alice",
            role="operator",
            operation_id=result["operation_id"],
        )
    with pytest.raises(ValueError, match="identity"):
        await ensure_v2_identity(factory, "3" * 32, "4" * 32, "8" * 32)
    assert len(transport.sent) == 1


async def test_board_watermark_larger_than_signed_sqlite_integer(factory):
    value, _ = await coordinator(factory)
    await value.reconcile_highwater(str(2**63 + 30))
    assert (await stop(value))["command_seq"] == str(2**63 + 31)


async def test_stop_does_not_wait_for_ordinary_receipt(factory):
    value, transport = await coordinator(factory)
    transport.block = asyncio.Event()
    ordinary = asyncio.create_task(
        value.submit(
            "ack_alarm",
            {"alarm_id": "a" * 32, "occurrence_seq": "1"},
            actor="alice",
            role="operator",
            lease_id="b" * 32,
            state_revision="1",
        )
    )
    for _ in range(2000):
        if transport.sent:
            break
        await asyncio.sleep(0.005)
    # Stop goes out only once the ordinary command awaits its receipt. Both 10 s bounds merely absorb slow SQLite
    # commits on busy runners; a stop that waited for that receipt would never finish.
    assert transport.sent
    result = await asyncio.wait_for(stop(value), 10)
    assert result["status"] == "applied" and not ordinary.done()
    transport.block.set()
    await ordinary


async def test_wrong_result_identity_is_unknown(factory):
    value, transport = await coordinator(factory)
    original = transport.request

    async def wrong(*args, **kwargs):
        response = await original(*args, **kwargs)
        response["payload"]["operation_id"] = "f" * 32
        return response

    transport.request = wrong
    result = await stop(value)
    assert result["status"] == "unknown"
    async with factory() as db:
        assert len((await db.scalars(select(V2Operation))).all()) == 1


@pytest.mark.parametrize("status", ["result_expired", "not_found"])
async def test_query_cache_eviction_preserves_locally_proved_result(factory, status):
    value, transport = await coordinator(factory)
    applied = await stop(value)
    original = transport.request

    async def expired(*args, **kwargs):
        response = await original(*args, **kwargs)
        response["payload"].update(status=status, reason=status, request_digest=None, result_boot_id=None)
        return response

    transport.request = expired
    assert (await value.query(applied["operation_id"]))["status"] == "applied"
    assert len(transport.sent) == 2


async def test_conflicting_terminal_result_cannot_replace_proved_outcome(factory):
    value, transport = await coordinator(factory)
    applied = await stop(value)
    original = transport.request

    async def conflict(*args, **kwargs):
        response = await original(*args, **kwargs)
        response["payload"].update(status="rejected", reason="state_conflict", request_digest=applied["request_digest"])
        return response

    transport.request = conflict
    with pytest.raises(ValueError, match="conflict"):
        await value.query(applied["operation_id"])
    assert (await value.get(applied["operation_id"]))["status"] == "applied"


async def test_sequence_exhaustion_never_wraps_or_sends(factory):
    value, transport = await coordinator(factory)
    await value.reconcile_highwater(str(2**64 - 1))
    with pytest.raises(ValueError, match="sequence_exhausted"):
        await stop(value)
    assert not transport.sent


async def test_unknown_stop_blocks_new_ordinary_action_until_fresh_safe_audit(factory):
    value, transport = await coordinator(factory)
    transport.timeout = True
    unknown = await stop(value)
    with pytest.raises(ValueError, match="operation_unresolved"):
        await value.submit("acquire_lease", {"lease_ms": 8000}, actor="alice", role="operator")
    vectors = json.loads(
        (Path(__file__).resolve().parents[2] / "contracts/hostcomm/v2/vectors.json").read_text(encoding="utf-8")
    )
    snapshot = copy.deepcopy(next(v["value"] for v in vectors["valid_messages"] if v["name"] == "status_snapshot"))
    snapshot.update(boot_id=transport.boot_id, session_id=transport.session_id)
    snapshot["payload"]["run"].update(
        state="fault",
        outcome="invalid",
        safe_complete=True,
        safe_boundary={"boot_id": transport.boot_id, "sample_seq": "100"},
    )

    async def status(*args, **kwargs):
        return snapshot

    transport.request = status
    snapshot["payload"]["safety"].update(hardwired_permit=True, profile_approved=True)
    snapshot["payload"]["safety"]["co_alarm"] = True
    with pytest.raises(ValueError, match="unsafe_recovery"):
        await value.reconcile_unknown(unknown["operation_id"], actor="alice", reason="verified on device")
    snapshot["payload"]["safety"]["co_alarm"] = False
    audited = await value.reconcile_unknown(unknown["operation_id"], actor="alice", reason="verified on device")
    assert audited["reconciled"] and audited["status"] == "unknown"
    async with factory() as db:
        assert len((await db.scalars(select(V2OperationReview))).all()) == 1


@pytest.mark.parametrize(
    "current,expires,expected", [(True, "20000", True), (True, "12000", False), (False, "20000", False)]
)
async def test_lease_readback_must_be_current_owned_and_unexpired(factory, current, expires, expected):
    value, transport = await coordinator(factory)
    vectors = json.loads(
        (Path(__file__).resolve().parents[2] / "contracts/hostcomm/v2/vectors.json").read_text(encoding="utf-8")
    )
    snapshot = copy.deepcopy(next(v["value"] for v in vectors["valid_messages"] if v["name"] == "status_snapshot"))
    snapshot.update(boot_id=transport.boot_id, session_id=transport.session_id, uptime_ms="12345")
    snapshot["payload"].update(
        lease_id="a" * 32,
        lease_owner_controller_id=value.controller_id,
        lease_owner_session_id=transport.session_id if current else "b" * 32,
        lease_expires_uptime_ms=expires,
    )
    original = transport.request

    async def lease(kind, payload, **kwargs):
        if kind == "get_status":
            return snapshot
        response = await original(kind, payload, **kwargs)
        response["payload"].update(lease_id="a" * 32, lease_expires_uptime_ms=expires)
        return response

    transport.request = lease
    result = await value.acquire_lease(actor="alice", role="operator")
    assert result["status"] == "applied"
    assert (transport.lease == "a" * 32) is expected


async def test_recovered_accepted_result_reclaims_execution_slot(factory):
    value, transport = await coordinator(factory)
    transport.timeout = True
    old = await stop(value)
    async with factory() as db, db.begin():
        row = await db.get(V2Operation, old["operation_id"])
        row.reconciled = 1  # A previously completed safety review retained the unknown history.
    transport.timeout = False
    original = transport.request

    async def accepted(*args, **kwargs):
        response = await original(*args, **kwargs)
        response["payload"].update(status="accepted", request_digest=old["request_digest"])
        return response

    transport.request = accepted
    result = await value.query(old["operation_id"])
    assert not result["reconciled"]
    with pytest.raises(ValueError, match="operation_unresolved"):
        await value.submit("acquire_lease", {"lease_ms": 8000}, actor="alice", role="operator")


def test_v2_migration_preserves_old_source_order_as_unknown(tmp_path):
    database = tmp_path / "v2-upgrade.db"
    env = {**os.environ, "SMD_DB_PATH": str(database)}
    backend = Path(__file__).resolve().parents[1]
    for revision in ("recipe001", "hostcommv2001", "head"):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", revision],
            cwd=backend,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        if revision == "recipe001":
            with sqlite3.connect(database) as db:
                db.execute(
                    "INSERT INTO sample_point(test_id,ts,source,burden_temp_v,delta_p_v,displacement_v) VALUES(?,?,?,1,1,1)",
                    ("old", "2026-09-05T00:00:00Z", "live_poll"),
                )
    with sqlite3.connect(database) as db:
        assert db.execute(
            "SELECT test_id,source_boot_id,source_sequence,source_run_id,source_uptime_ms FROM sample_point"
        ).fetchall() == [("old", None, None, None, None)]
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert len({name for name in tables if name.startswith("v2_")}) == 15
        db.execute(
            "INSERT INTO v2_log_cursor(device_id,log_id,verified_from_seq,verified_through_seq,updated_at) VALUES(?,?,?,?,?)",
            ("a" * 32, "b" * 32, "1", "2", "2026-09-06T00:00:00Z"),
        )
        assert db.execute("SELECT scanned_through_seq FROM v2_log_cursor").fetchone() == (None,)
        assert db.execute("SELECT version_num FROM alembic_version").fetchone() == ("hostcommv2002",)
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
