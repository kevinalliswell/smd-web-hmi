"""HostComm 基础测试 T01-T06（开发规格说明书 9.2）。"""

from __future__ import annotations

import asyncio

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
        await asyncio.sleep(1.2)  # ~12 个心跳周期
        assert client.is_online
        assert client.comm_quality == "online"
        assert client.stats["missed_heartbeats"] == 0
        assert client.stats["heartbeat_age_s"] is not None
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


# ---------------------------------------------------- 连接生命周期回归（审查 #13）
async def test_failed_handshake_leaves_no_orphan_connection():
    """握手失败必须就地清理，不遗留收帧任务与半开套接字（审查 #13 之 1）。

    修复前 connect() 失败会留下 _connected=True、存活的收帧任务与未关闭的套接字：
    状态失真（自称已连接），且清理只能等 CPython 引用计数回收 writer 时才发生。
    """
    srv = MockHostCommServer(port=free_port(), status_interval=None, hello_mode="ignore")
    await srv.start()
    client = await make_client(srv, command_timeout=0.3, auto_reconnect=False)
    try:
        with pytest.raises(Exception):  # noqa: PT011  握手超时
            await client.connect()
        assert client._reader_task is None, "遗留了孤儿收帧任务"
        assert client._writer is None, "遗留了半开套接字"
        assert client._connected is False
        assert client.comm_quality == "offline"
    finally:
        await client.close()
        await srv.stop()


async def test_heartbeat_timeout_triggers_reconnect():
    """心跳持续超时须判定断链并重连，不能停在 degraded（审查 #13 之 2，红线 6）。

    复现半开连接：TCP 未断（收帧协程仍阻塞在 read），但对端不再响应心跳。
    """
    srv = MockHostCommServer(port=free_port(), status_interval=None, heartbeat_mode="ignore")
    await srv.start()
    client = await make_client(srv, heartbeat_interval=0.1, timeout_count=2, command_timeout=0.5)
    await client.connect()
    assert client.is_online
    try:
        for _ in range(60):
            if client.comm_quality == "offline":
                break
            await asyncio.sleep(0.1)
        assert client.comm_quality == "offline", "心跳超时后仍未判定断链"
        assert client._reconnect_task is not None, "断链后未启动重连任务"
        assert not client._reconnect_task.done(), "重连任务未在运行"
    finally:
        await client.close()
        await srv.stop()
