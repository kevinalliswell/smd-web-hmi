"""Recovered command rejection must not orphan a reservation or imply safe completion."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import Base, EventLog, TestSession
from app.db.operation_models import Operation
from app.db.v2_models import V2RunBinding
from app.services.command_service import CommandService
from app.services.test_runtime import active_test
from app.services.v2_operation_api import query_operation

DEVICE, RUN, WIRE = "1" * 32, "2" * 32, "3" * 32


@pytest.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'operation.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db, db.begin():
        db.add(
            Operation(
                operation_id="start-http",
                msg_id="pc-cmd-start",
                command="start_test",
                operator_id="admin",
                operator_role="admin",
                request_hash="a" * 64,
                params_json=json.dumps({"test_id": "RESERVED"}),
                status="unknown",
                created_at="2026-09-06T00:00:00Z",
                updated_at="2026-09-06T00:00:00Z",
            )
        )
        db.add(
            TestSession(
                test_id="RESERVED",
                operator_id="admin",
                start_time="2026-09-06T00:00:00Z",
                phase="needs_review",
                measurement_basis_json=json.dumps({"v2": {}}),
            )
        )
        db.add(
            V2RunBinding(
                device_id=DEVICE,
                run_id=RUN,
                test_id="RESERVED",
                recipe_digest="b" * 64,
                profile_digest="c" * 64,
                created_at="2026-09-06T00:00:00Z",
            )
        )
    yield factory
    active_test.stop()
    await engine.dispose()


def client_with_result(status):
    wire = {
        "operation_id": WIRE,
        "device_id": DEVICE,
        "command": "start_run",
        "command_seq": "2",
        "status": status,
        "reason": "safety_condition_changed",
        "reconciled": False,
        "request": {"params": {"run_id": RUN}},
        "result": {"status": status},
    }
    return SimpleNamespace(
        protocol_version="2.0",
        is_online=True,
        device_id=DEVICE,
        wire_operation_id=lambda _: WIRE,
        operations=SimpleNamespace(get=AsyncMock(return_value=wire), query=AsyncMock(return_value=wire)),
    )


async def test_recovered_rejected_start_closes_only_its_unstarted_reservation(factory):
    client = client_with_result("rejected")
    async with factory() as db:
        operation = await db.get(Operation, "start-http")
        result = await query_operation(db, operation, client, actor="admin")
        assert result["operation_status"] == "rejected"
        session = await db.scalar(select(TestSession).where(TestSession.test_id == "RESERVED"))
        assert session.end_time is not None
        assert session.phase == "start_rejected"
        assert session.safety_completed_at is None  # No physical run was accepted.
        assert session.measurement_completed_at is None
        events = list(await db.scalars(select(EventLog).where(EventLog.test_id == "RESERVED")))
        assert len(events) == 1
        assert WIRE in events[0].detail_json
        await query_operation(db, operation, client, actor="admin")
        assert len(list(await db.scalars(select(EventLog).where(EventLog.test_id == "RESERVED")))) == 1


@pytest.mark.parametrize("status", ["interrupted", "unknown", "result_expired", "not_found"])
async def test_recovery_without_proof_of_nonacceptance_keeps_run_open(factory, status):
    async with factory() as db:
        await query_operation(db, await db.get(Operation, "start-http"), client_with_result(status), actor="admin")
        session = await db.scalar(select(TestSession).where(TestSession.test_id == "RESERVED"))
        assert session.end_time is None
        assert session.safety_completed_at is None


async def test_immediate_interrupted_start_cannot_close_before_safe_completion(factory):
    service = CommandService(client_with_result("interrupted"), None)
    async with factory() as db:
        await service._handle_lifecycle(
            db,
            "start_test",
            {"test_id": "RESERVED"},
            "admin",
            "admin",
            None,
            {"result": "rejected", "wire_status": "interrupted"},
        )
        session = await db.scalar(select(TestSession).where(TestSession.test_id == "RESERVED"))
        assert session.end_time is None
        assert session.phase == "needs_review"


@pytest.mark.parametrize("conflict", ["different_run", "accepted_run_evidence"])
async def test_rejected_history_cannot_close_unrelated_or_observed_run(factory, conflict):
    client = client_with_result("rejected")
    async with factory() as db:
        session = await db.scalar(select(TestSession).where(TestSession.test_id == "RESERVED"))
        if conflict == "different_run":
            client.operations.query.return_value["request"]["params"]["run_id"] = "9" * 32
        else:
            session.measurement_basis_json = json.dumps({"v2": {"state": "preparing"}})
            await db.commit()
        await query_operation(db, await db.get(Operation, "start-http"), client, actor="admin")
        assert session.end_time is None
        assert not list(await db.scalars(select(EventLog)))
