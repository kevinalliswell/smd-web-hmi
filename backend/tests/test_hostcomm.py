"""HostComm 基础测试 T01-T06（开发规格说明书 9.2）。"""

from __future__ import annotations

import asyncio
import time

import pytest

from app.hostcomm.client import HostCommClient, HostCommTimeoutError
from app.hostcomm.mock_server import MockHostCommServer
from app.hostcomm.protocol import FrameParser
from tests.conftest import free_port, make_client


# ---------------------------------------------------------- T01 连接与 hello
async def test_t01_hello_handshake(mock_server):
    """T01：连接 Mock Server 并完成 hello，hello_ack 与 capabilities 正确解析。"""
    client = await make_client(mock_server)
    await client.connect()
    try:
        assert client.is_online
        assert client.hello_ack is not None
        assert client.hello_ack["payload"]["result"] == "accepted"
        assert "status_snapshot" in client.capabilities
        assert "command" in client.capabilities
    finally:
        await client.close()


# ---------------------------------------------------------- T02 心跳稳定
async def test_t02_heartbeat_stable(mock_server):
    """T02：连续多个心跳周期保持在线，无异常（缩短周期模拟长期稳定）。"""
    client = await make_client(mock_server, heartbeat_interval=0.1)
    await client.connect()
    try:
        # 等待服务端实际返回多个 heartbeat_ack，而不是依赖固定 sleep。
        # 慢速 CI（尤其 Windows + coverage）可能恰好在新心跳发出、ACK 尚未
        # 被 reader 处理的瞬间唤醒，此时 missed_heartbeats 会短暂为 1。
        initial_frames = client.stats["frames_parsed"]
        deadline = time.monotonic() + 5.0
        while True:
            stats = client.stats
            if stats["frames_parsed"] >= initial_frames + 10 and stats["missed_heartbeats"] == 0:
                break
            if time.monotonic() >= deadline:
                pytest.fail(f"等待稳定心跳 ACK 超时：{stats}")
            await asyncio.sleep(0.02)

        assert client.is_online
        assert client.comm_quality == "online"
        assert stats["missed_heartbeats"] == 0
        assert stats["heartbeat_age_s"] is not None
    finally:
        await client.close()


# ---------------------------------------------------------- T03 断线重连
async def test_t03_reconnect():
    """T03：Mock Server 关闭再启动，客户端自动重连，comm_quality 恢复 online。"""
    port = free_port()
    srv = MockHostCommServer(port=port, status_interval=None)
    await srv.start()
    client = HostCommClient("127.0.0.1", port, heartbeat_interval=0.2, command_timeout=1.0, reconnect_base=0.2)
    await client.connect()
    assert client.is_online

    # 关闭服务端 → 客户端应转为 offline
    await srv.stop()
    for _ in range(30):
        if client.comm_quality == "offline":
            break
        await asyncio.sleep(0.1)
    assert client.comm_quality == "offline"

    # 重新启动同端口服务端 → 客户端应自动重连
    srv2 = MockHostCommServer(port=port, status_interval=None)
    await srv2.start()
    try:
        for _ in range(50):
            if client.is_online:
                break
            await asyncio.sleep(0.1)
        assert client.is_online
    finally:
        await client.close()
        await srv2.stop()


async def test_mock_disconnect_after_fault_closes_connection() -> None:
    """disconnect_after 故障必须真实断开连接，供重连/报警演练使用。"""
    srv = MockHostCommServer(port=free_port(), status_interval=None, disconnect_after=0.05)
    await srv.start()
    client = HostCommClient(
        "127.0.0.1",
        srv.port,
        heartbeat_interval=0.02,
        command_timeout=0.2,
        auto_reconnect=False,
    )
    await client.connect()
    try:
        deadline = time.monotonic() + 1.0
        while client.is_online and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        assert client.is_online is False
    finally:
        await client.close()
        await srv.stop()


# ---------------------------------------------------------- T04 状态快照解析
async def test_t04_status_snapshot(mock_server):
    """T04：get_status 返回完整字段，on_status 回调被触发。"""
    received: list[dict] = []

    async def on_status(payload):
        received.append(payload)

    client = await make_client(mock_server, on_status=on_status)
    await client.connect()
    try:
        payload = await client.get_status()
        assert payload["system"]["current_state"] == "Standby"
        assert "furnace_pv_deg_c" in payload["temperature"]
        assert "safety_relay_allowed" in payload["safety"]
        await asyncio.sleep(0)  # on_status 与收帧循环解耦，在独立任务中运行
        assert received, "on_status 回调应被触发"
    finally:
        await client.close()


# ---------------------------------------------------------- T05 命令超时
async def test_t05_command_timeout():
    """T05：Mock 不响应命令，3s（测试用 1s）后抛出 HostCommTimeoutError。"""
    srv = MockHostCommServer(port=free_port(), command_mode="ignore", status_interval=None)
    await srv.start()
    client = await make_client(srv, command_timeout=1.0)
    await client.connect()
    try:
        with pytest.raises(HostCommTimeoutError):
            await client.send_command("tare_balance", {}, operator_id="op001", role="operator")
    finally:
        await client.close()
        await srv.stop()


# ---------------------------------------------------------- T06 错误 JSON
async def test_t06_bad_json_no_crash():
    """T06：Mock 发送格式错误帧，client 不 crash，记录 json_errors 并仍可工作。"""
    srv = MockHostCommServer(port=free_port(), inject_bad_json=True, status_interval=None)
    await srv.start()
    client = await make_client(srv)
    await client.connect()
    try:
        await asyncio.sleep(0.3)  # 等待非法帧被处理
        assert client.is_online, "非法 JSON 不应导致连接崩溃"
        assert client.stats["json_errors"] >= 1
        # 仍能正常请求状态
        payload = await client.get_status()
        assert payload["system"]["current_state"] == "Standby"
    finally:
        await client.close()
        await srv.stop()


# ---------------------------------------------------------- FrameParser 单测
def test_frame_parser_partial_and_bad():
    """FrameParser 处理粘包/拆包，并跳过非法 JSON 不抛异常。"""
    parser = FrameParser()
    # 拆包：半帧
    assert parser.feed(b'{"type":"a","msg') == []
    frames = parser.feed(b'_id":"1"}\n')
    assert len(frames) == 1 and frames[0]["type"] == "a"
    # 粘包 + 非法 JSON 混合
    frames = parser.feed(b'{"type":"b"}\nnot-json\n{"type":"c"}\n')
    types = [f["type"] for f in frames]
    assert types == ["b", "c"]
    assert parser.json_errors >= 1


# ---------------------------------------------------------- 链路可靠性回归
async def test_failed_handshake_tears_down_partial_connection(monkeypatch):
    """握手失败必须关闭半连接并清理 reader task，避免重连后双读。"""

    class FakeReader:
        async def read(self, size):
            await asyncio.Event().wait()

    class FakeWriter:
        closed = False
        waited = False

        def close(self):
            self.closed = True

        async def wait_closed(self):
            self.waited = True

    writer = FakeWriter()

    async def fake_open_connection(host, port):
        return FakeReader(), writer

    async def failed_handshake():
        raise HostCommTimeoutError("hello timeout")

    monkeypatch.setattr(asyncio, "open_connection", fake_open_connection)
    client = HostCommClient("127.0.0.1", 34211, auto_reconnect=False)
    monkeypatch.setattr(client, "_handshake", failed_handshake)

    try:
        with pytest.raises(HostCommTimeoutError):
            await client.connect()
        assert writer.closed is True
        assert writer.waited is True
        assert client._reader is None
        assert client._writer is None
        assert client._reader_task is None
        assert client._connected is False
    finally:
        await client.close()


async def test_heartbeat_transport_error_marks_connection_lost(monkeypatch):
    """心跳发送异常不能静默退出，必须触发断线清理/重连流程。"""
    client = HostCommClient("127.0.0.1", 34211, heartbeat_interval=0, auto_reconnect=False)
    client._connected = True
    lost = asyncio.Event()

    async def no_sleep(delay):
        return None

    async def failed_send(frame):
        raise ConnectionResetError("socket reset")

    async def on_connection_lost():
        client._connected = False
        lost.set()

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    monkeypatch.setattr(client, "_send", failed_send)
    monkeypatch.setattr(client, "_on_connection_lost", on_connection_lost)

    await client._heartbeat_loop()
    assert lost.is_set()


async def test_missed_heartbeat_threshold_marks_connection_lost(monkeypatch):
    """连续心跳应答超时达到阈值后必须断线，而不是永久停在 degraded。"""
    client = HostCommClient("127.0.0.1", 34211, heartbeat_interval=0.01, timeout_count=3, auto_reconnect=False)
    client._connected = True
    client._last_heartbeat_ack = time.monotonic() - 0.04
    lost = asyncio.Event()

    async def no_sleep(delay):
        return None

    async def sent(frame):
        # 让旧实现在第一轮检查后自然退出，避免失败测试忙循环。
        client._connected = False
        return None

    async def on_connection_lost():
        client._connected = False
        lost.set()

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    monkeypatch.setattr(client, "_send", sent)
    monkeypatch.setattr(client, "_on_connection_lost", on_connection_lost)

    await client._heartbeat_loop()
    assert lost.is_set()


async def test_status_callback_does_not_block_frame_dispatch():
    """慢 DB/WS 回调不能阻塞 HostComm 收帧和请求响应匹配。"""
    callback_started = asyncio.Event()
    callback_release = asyncio.Event()

    async def slow_callback(payload):
        callback_started.set()
        await callback_release.wait()

    client = HostCommClient("127.0.0.1", 34211, auto_reconnect=False, on_status=slow_callback)
    client._handshake_complete = True
    frame = {"type": "status_snapshot", "payload": {"system": {"current_state": "Standby"}}}

    try:
        await asyncio.wait_for(client._dispatch(frame), timeout=0.05)
        await asyncio.wait_for(callback_started.wait(), timeout=0.05)
    finally:
        callback_release.set()
        await client.close()


async def test_status_callbacks_preserve_frame_order():
    """后台回调必须串行消费，避免较慢的旧快照覆盖较新的缓存状态。"""
    first_started = asyncio.Event()
    first_release = asyncio.Event()
    completed: list[int] = []

    async def ordered_callback(payload):
        if payload["seq"] == 1:
            first_started.set()
            await first_release.wait()
        completed.append(payload["seq"])

    client = HostCommClient("127.0.0.1", 34211, auto_reconnect=False, on_status=ordered_callback)
    client._handshake_complete = True

    try:
        await client._dispatch({"type": "status_snapshot", "payload": {"seq": 1}})
        await asyncio.wait_for(first_started.wait(), timeout=0.05)
        await client._dispatch({"type": "status_snapshot", "payload": {"seq": 2}})
        await asyncio.sleep(0)
        assert completed == []

        first_release.set()
        for _ in range(10):
            if completed == [1, 2]:
                break
            await asyncio.sleep(0)
        assert completed == [1, 2]
    finally:
        first_release.set()
        await client.close()
