"""Upgrade admission uses durable unresolved work and an acknowledged board idle state."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.routes.maintenance import validate_upgrade_ready
from app.db.models import Base, TestSession
from app.db.v2_models import V2Operation
from app.hostcomm.v2_client import V2Client
from app.services.maintenance_service import MaintenanceBlockedError


@pytest.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'upgrade.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def request_for(client):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(hostcomm_client=client)))


async def test_unpaired_new_install_can_upgrade_without_a_device(factory):
    client = V2Client("127.0.0.1", 9000, factory=factory, device_id="", client_version="test")
    async with factory() as db:
        await validate_upgrade_ready(request_for(client), db)


async def test_completed_run_requires_acknowledged_idle_even_when_archive_closed(factory):
    async with factory() as db, db.begin():
        db.add(
            TestSession(
                test_id="DONE",
                operator_id="admin",
                start_time="2026-09-06T00:00:00Z",
                end_time="2026-09-06T01:00:00Z",
                phase="completed",
            )
        )
    client = SimpleNamespace(
        protocol_version="2.0",
        device_id="1" * 32,
        is_online=True,
        get_status=AsyncMock(),
        _status_frame={"payload": {"run": {"state": "completed", "run_id": "2" * 32, "safe_complete": True}}},
    )
    async with factory() as db:
        with pytest.raises(MaintenanceBlockedError, match="确认结束"):
            await validate_upgrade_ready(request_for(client), db)
        client._status_frame["payload"]["run"] = {"state": "idle", "run_id": None}
        await validate_upgrade_ready(request_for(client), db)


@pytest.mark.parametrize("device_id", ["", "1" * 32])
async def test_unknown_unreviewed_command_blocks_paired_and_unpaired_upgrade(factory, device_id):
    async with factory() as db, db.begin():
        db.add(
            V2Operation(
                operation_id="3" * 32,
                msg_id="4" * 32,
                device_id="1" * 32,
                controller_epoch="5" * 32,
                command_seq="18446744073709551615",
                command="stop_run",
                business_digest="a" * 64,
                request_digest="b" * 64,
                actor="admin",
                role="admin",
                status="unknown",
                reason="receipt_lost",
                request_json="{}",
                reconciled=0,
                created_at="2026-09-06T00:00:00Z",
                updated_at="2026-09-06T00:00:00Z",
            )
        )
    client = SimpleNamespace(
        protocol_version="2.0",
        device_id=device_id,
        is_online=True,
        get_status=AsyncMock(),
        _status_frame={"payload": {"run": {"state": "idle", "run_id": None}}},
    )
    async with factory() as db:
        with pytest.raises(MaintenanceBlockedError, match="未核查的设备操作"):
            await validate_upgrade_ready(request_for(client), db)
    client.get_status.assert_not_awaited()
