"""真实TCP模拟板→操作服务→生产回调→SQLite，覆盖停止后的冷却归档。"""

import asyncio
from types import SimpleNamespace

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.main as main_module
from app.db.models import SamplePoint, TestSession
from app.hostcomm.mock_server import MockHostCommServer
from app.services.cache import StatusCache
from app.services.command_service import CommandService, confirm_tokens
from app.services.maintenance_service import MaintenanceManager
from app.services.recipe_definition import standard_template
from app.services.recipe_service import RecipeService
from app.services.test_runtime import active_test


async def test_recipe_control_and_cooling_through_real_tcp_and_database(db_session, monkeypatch, tmp_path):
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    cache = StatusCache()
    monkeypatch.setattr(main_module, "get_sessionmaker", lambda: factory)
    monkeypatch.setattr(main_module, "status_cache", cache)
    manager = MaintenanceManager()
    manager.configure_upgrade(tmp_path / "maintenance.json")
    monkeypatch.setattr(main_module, "maintenance_manager", manager)
    active_test.stop()
    server = MockHostCommServer(port=0, status_interval=None, extended_contract=True)
    await server.start()
    settings = SimpleNamespace(
        hostcomm_mock=True,
        hostcomm_host="127.0.0.1",
        hostcomm_port=server.port,
        hostcomm_heartbeat_interval=0.2,
        hostcomm_timeout_count=3,
        hostcomm_command_timeout=2,
        client_id="recipe-integration",
    )
    client = main_module._build_hostcomm_client(settings)
    await client.start()

    async def collect():
        await client.get_status()
        # A TCP snapshot reply precedes its asynchronous archive callback.
        # Wait for the actual persistence work before inspecting database rows.
        await asyncio.wait_for(client._callback_queues["data"].join(), 10)

    try:
        await collect()
        service = RecipeService(db_session, client, cache)
        recipe = await service.save(standard_template(), operator_id="admin", role="admin")
        result = await service.activate(
            recipe["recipe_id"], 1, operator_id="admin", role="admin", operation_id="activate-tcp"
        )
        assert result["operation_status"] == "verified"
        commands = CommandService(client, cache)
        await commands.execute(
            "start_test",
            {"test_id": "TCP-COOL", "original_height_mm": 40, "recipe_id": recipe["recipe_id"], "recipe_version": 1},
            operator_id="admin",
            role="admin",
            db_session=db_session,
            confirm_token=confirm_tokens.issue(),
            operation_id="start-tcp",
        )
        await collect()
        server.runtime.furnace = 900
        server.runtime.burden = 850
        await commands.execute(
            "stop_test",
            {},
            operator_id="admin",
            role="admin",
            db_session=db_session,
            confirm_token=confirm_tokens.issue(),
            operation_id="stop-tcp",
        )
        await collect()
        row = await db_session.scalar(select(TestSession).where(TestSession.test_id == "TCP-COOL"))
        await db_session.refresh(row)
        assert row.end_time is None and row.stop_requested_at is not None
        server.runtime.advance(4000)
        server._state = server.runtime.state
        await collect()
        await db_session.refresh(row)
        assert row.end_reason == "operator_stop" and row.end_time is not None
        assert row.measurement_completed_at is None and row.data_integrity == "incomplete"
        assert (
            await db_session.scalar(
                select(func.count()).select_from(SamplePoint).where(SamplePoint.test_id == "TCP-COOL")
            )
            >= 3
        )
    finally:
        await client.close()
        await server.stop()
        active_test.stop()
