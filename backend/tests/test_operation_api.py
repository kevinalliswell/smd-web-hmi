"""实际REST入口的幂等键、状态查询及访问边界。"""

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.deps import CurrentUser, get_current_user
from app.db.database import get_db
from app.hostcomm.client import HostCommTimeoutError
from app.main import create_app
from app.services.cache import status_cache


async def test_repeated_rest_command_can_query_its_own_operation(db_session):
    class Board:
        is_online = True
        sent = 0

        async def send_command(self, command, params, **kwargs):
            self.sent += 1
            raise HostCommTimeoutError("lost ack")

    app, board = create_app(), Board()
    app.state.hostcomm_client = board
    app.dependency_overrides[get_current_user] = lambda: CurrentUser("a", "operator")

    async def database():
        yield db_session

    app.dependency_overrides[get_db] = database
    await status_cache.update({"state_machine": {"current_state": "Standby"}})
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        first = await client.post(
            "/api/commands", json={"command": "tare_balance"}, headers={"Idempotency-Key": "api-op"}
        )
        assert first.status_code == 504
        assert first.json()["operation_id"] == "api-op"
        repeated = await client.post(
            "/api/commands", json={"command": "tare_balance"}, headers={"Idempotency-Key": "api-op"}
        )
        assert repeated.json()["data"]["operation_status"] == "unknown"
        status = await client.get("/api/commands/operations/api-op")
        assert status.json()["data"]["operation_status"] == "unknown"
        app.dependency_overrides[get_current_user] = lambda: CurrentUser("b", "operator")
        assert (await client.get("/api/commands/operations/api-op")).status_code == 404
    assert board.sent == 1
