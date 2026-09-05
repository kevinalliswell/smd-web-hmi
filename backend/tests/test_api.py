"""API 测试 T13-T15（开发规格说明书 9.2）。"""

from __future__ import annotations

import asyncio
import json
import time

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.deps import CurrentUser, get_current_user
from app.api.ws_manager import ConnectionContext, ConnectionManager, ws_manager
from app.core.security import create_access_token
from app.hostcomm.client import HostCommNotConnectedError, HostCommTimeoutError
from app.main import create_app
from app.services.cache import status_cache


# ---------------------------------------------------- T13 GET /api/status 结构
async def test_t13_status_structure():
    """T13：/api/status 返回含 comm_quality 与 system.current_state。"""
    await status_cache.update(
        {
            "system": {"current_state": "Standby", "fw_version": "FW-TEST"},
            "temperature": {"furnace_pv_deg_c": 25.0},
        }
    )
    token, _ = create_access_token("tester", "observer")
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: CurrentUser("tester", "observer")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/status", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "comm_quality" in data
    assert data["system"]["current_state"] == "Standby"
    assert data["system"]["operation_state"] == "idle"
    assert data["system"]["can_start_test"] is False
    assert data["data_fresh"] is True
    assert data["control_ready"] is False
    assert data["system"]["can_stop_test"] is False


# ---------------------------------------------------- T13b 未认证 401
async def test_status_requires_auth():
    """无 token 访问受保护路由返回 401。"""
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/status")
    assert resp.status_code == 401


# ---------------------------------------------------- T14 WebSocket 认证
def test_t14_ws_requires_token():
    """T14：无 token 连接 WebSocket → 以 4001 关闭。"""
    from starlette.testclient import TestClient

    client = TestClient(create_app())
    with pytest.raises(Exception) as ei:  # noqa: PT011
        with client.websocket_connect("/ws/realtime") as ws:
            ws.receive_text()
    # Starlette 在握手期关闭时抛 WebSocketDisconnect，code 应为 4001
    assert getattr(ei.value, "code", None) == 4001 or "4001" in str(ei.value)


# ---------------------------------------------------- T15 status_update 推送
async def test_t15_ws_status_update_push():
    """T15：广播 status_update 后，已连接客户端 1s 内收到推送。"""

    class FakeWS:
        def __init__(self):
            self.sent: list[dict] = []

        async def accept(self):
            pass

        async def send_json(self, message):
            self.sent.append(message)

    ws = FakeWS()
    await ws_manager.connect(
        ws,
        ConnectionContext(username="test", role="observer"),
        max_connections_per_user=1,
    )
    try:
        t0 = time.monotonic()
        await ws_manager.broadcast("status_update", {"system": {"current_state": "Standby"}})
        elapsed = time.monotonic() - t0
        assert elapsed < 1.0
        assert ws.sent and ws.sent[-1]["type"] == "status_update"
        assert ws.sent[-1]["data"]["system"]["current_state"] == "Standby"
    finally:
        await ws_manager.disconnect(ws)


async def test_broadcast_times_out_slow_client_without_blocking_others():
    """单个慢客户端不得串行拖住实时广播，并在超时后从连接池移除。"""

    class SlowWS:
        async def accept(self):
            pass

        async def send_json(self, message):
            await asyncio.Event().wait()

    class FastWS:
        def __init__(self):
            self.sent = []

        async def accept(self):
            pass

        async def send_json(self, message):
            self.sent.append(message)

    manager = ConnectionManager(send_timeout=0.01)
    slow, fast = SlowWS(), FastWS()
    context = ConnectionContext(username="test", role="observer")
    await manager.connect(slow, context, max_connections_per_user=2)
    await manager.connect(fast, context, max_connections_per_user=2)

    started = time.monotonic()
    await manager.broadcast("status_update", {"value": 1})

    assert time.monotonic() - started < 0.1
    assert fast.sent[-1]["data"] == {"value": 1}
    assert manager.count == 1


@pytest.mark.parametrize(
    ("exception", "expected_status", "expected_code"),
    [
        (HostCommTimeoutError("timeout"), 504, "device_comm_timeout"),
        (HostCommNotConnectedError("offline"), 503, "device_comm_fault"),
    ],
)
async def test_hostcomm_errors_have_stable_http_mapping(exception, expected_status, expected_code):
    """HostComm 原生异常必须映射为稳定的 REST 错误契约，而不是裸 500。"""
    app = create_app()
    handler = app.exception_handlers[type(exception)]
    response = await handler(None, exception)

    assert response.status_code == expected_status
    assert json.loads(response.body)["error_code"] == expected_code
