"""API 测试 T13-T15（开发规格说明书 9.2）。"""

from __future__ import annotations

import time

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.ws_manager import ws_manager
from app.core.security import create_access_token
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
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/status", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "comm_quality" in data
    assert data["system"]["current_state"] == "Standby"


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
    await ws_manager.connect(ws)
    try:
        t0 = time.monotonic()
        await ws_manager.broadcast("status_update", {"system": {"current_state": "Standby"}})
        elapsed = time.monotonic() - t0
        assert elapsed < 1.0
        assert ws.sent and ws.sent[-1]["type"] == "status_update"
        assert ws.sent[-1]["data"]["system"]["current_state"] == "Standby"
    finally:
        await ws_manager.disconnect(ws)


# ---------------------------------------------------- WS 接入即推 comm_status 快照
# 注：这两个用例刻意不走 ws.receive_json()——若补推逻辑被改回旧行为，接收会永久
# 阻塞（把 CI 挂死到超时）而不是快速失败。改为直接验证补推函数与端点的调用。
async def test_ws_comm_status_snapshot_uses_hostcomm_quality():
    """补推的 comm_status 取自 HostComm 链路质量，而非"浏览器已连上后端"（审查 #15 之 2）。"""
    from types import SimpleNamespace

    from app.api.websocket import send_comm_status_snapshot

    class FakeWS:
        def __init__(self, client):
            self.sent: list[dict] = []
            self.app = SimpleNamespace(state=SimpleNamespace(hostcomm_client=client))

        async def send_json(self, message):
            self.sent.append(message)

    # 设备链路降级时，补推的就应是 degraded（不得因浏览器已连上而报 online）
    ws = FakeWS(SimpleNamespace(comm_quality="degraded"))
    await send_comm_status_snapshot(ws)
    assert ws.sent[-1]["type"] == "comm_status"
    assert ws.sent[-1]["data"]["status"] == "degraded"

    # HostComm 客户端尚未就绪时按最保守值上报
    ws_no_client = FakeWS(None)
    await send_comm_status_snapshot(ws_no_client)
    assert ws_no_client.sent[-1]["data"]["status"] == "offline"


def test_ws_endpoint_pushes_comm_status_on_connect(monkeypatch):
    """/ws/realtime 建连后立即补推 comm_status 快照。"""
    from starlette.testclient import TestClient

    from app.api import websocket as ws_module

    pushed: list[object] = []

    async def spy(ws):
        pushed.append(ws)

    monkeypatch.setattr(ws_module, "send_comm_status_snapshot", spy)

    token, _ = create_access_token("tester", "observer")
    client = TestClient(create_app())
    with client.websocket_connect(f"/ws/realtime?token={token}"):
        pass
    assert pushed, "建连后未补推 comm_status 快照"
