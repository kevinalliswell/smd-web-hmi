"""端到端集成测试：Mock 控制板 → HostComm 客户端 → 回调 → WebSocket 广播。

验证 D3 交付项"前端实时总览页能通过 WebSocket 显示模拟状态数据"的后端全链路：
真实 TCP（Mock Server）+ 真实客户端 + 生产回调接线（app.main._build_hostcomm_client）。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import app.main as main_module
from app.api.ws_manager import ws_manager
from app.hostcomm.mock_server import MockHostCommServer
from app.services.cache import status_cache
from tests.conftest import free_port


class _FakeWS:
    def __init__(self):
        self.sent: list[dict] = []

    async def accept(self):
        pass

    async def send_json(self, message):
        self.sent.append(message)


async def test_e2e_realtime_status_pipeline(monkeypatch):
    """Mock 周期推送 → 1s 内经生产回调广播 status_update 到已连接 WS，且缓存更新。"""

    # 本测试聚焦实时推送链路，跳过真实 DB 落库
    async def _noop(_payload):
        return None

    monkeypatch.setattr(main_module, "_persist_snapshot", _noop)

    mock_port = free_port()
    srv = MockHostCommServer(port=mock_port, status_interval=0.3)
    await srv.start()

    settings = SimpleNamespace(
        hostcomm_mock=True,
        hostcomm_host="ignored",
        hostcomm_port=mock_port,
        hostcomm_heartbeat_interval=0.2,
        hostcomm_timeout_count=3,
        hostcomm_command_timeout=1.0,
        client_id="hmi-test",
    )
    client = main_module._build_hostcomm_client(settings)
    await client.start()

    ws = _FakeWS()
    await ws_manager.connect(ws)
    try:
        # 等待 Mock 推送经全链路到达 WS（应在 1s 内）
        for _ in range(20):
            if any(m["type"] == "status_update" for m in ws.sent):
                break
            await asyncio.sleep(0.1)

        updates = [m for m in ws.sent if m["type"] == "status_update"]
        assert updates, "应在 1s 内收到 status_update 推送"
        assert updates[-1]["data"]["system"]["current_state"] == "Standby"

        # 内存缓存也应被同一回调更新
        snapshot = await status_cache.get_snapshot()
        assert snapshot["system"]["current_state"] == "Standby"
        assert client.is_online
    finally:
        await ws_manager.disconnect(ws)
        await client.close()
        await srv.stop()
        await asyncio.sleep(0.1)  # 让任务取消与传输关闭在事件循环关闭前完成
