"""HostComm边界：拒绝协商、关联响应、有限背压与禁止重复发送。"""

import asyncio

import pytest

from app.hostcomm.client import HostCommClient, HostCommError
from app.hostcomm.protocol import make_frame
from tests.conftest import make_client


@pytest.mark.parametrize("change", ["rejected", "version", "capabilities"])
async def test_unusable_handshake_never_enables_control(mock_server, monkeypatch, change):
    original = mock_server._hello_ack

    def hello():
        frame = original()
        if change == "rejected":
            frame["payload"]["result"] = "rejected"
        elif change == "version":
            frame["protocol_version"] = "99.0"
        else:
            frame["payload"]["capabilities"] = []
        return frame

    monkeypatch.setattr(mock_server, "_hello_ack", hello)
    client = await make_client(mock_server, auto_reconnect=False)
    try:
        with pytest.raises(HostCommError):
            await client.connect()
        assert not client.is_online
        assert not client.stats["connected"]
    finally:
        await client.close()


async def test_wire_msg_id_cannot_be_resent_without_verified_device_dedup(mock_server):
    client = await make_client(mock_server)
    await client.connect()
    try:
        first = await client.send_command("tare_balance", {}, operator_id="a", role="operator", msg_id="operation-1")
        assert first["result"] == "accepted"
        with pytest.raises(HostCommError):
            await client.send_command("tare_balance", {}, operator_id="a", role="operator", msg_id="operation-1")
    finally:
        await client.close()


async def test_correlated_parameter_reply_cannot_resolve_another_request(monkeypatch):
    client = HostCommClient("127.0.0.1", 1, auto_reconnect=False)
    client.hello_ack = make_frame("hello_ack", {"result": "accepted", "capabilities": ["request_correlation_v1"]})
    sent = []

    async def send(frame):
        sent.append(frame)

    monkeypatch.setattr(client, "_send", send)
    request = asyncio.create_task(client.get_parameters())
    try:
        await asyncio.sleep(0)
        await client._dispatch(
            make_frame("parameters_snapshot", {"request_msg_id": "someone-else", "params": {"x": 1}})
        )
        await asyncio.sleep(0)
        assert not request.done()
        await client._dispatch(
            make_frame("parameters_snapshot", {"request_msg_id": sent[0]["msg_id"], "params": {"x": 2}})
        )
        assert (await request)["params"] == {"x": 2}
    finally:
        request.cancel()
        await asyncio.gather(request, return_exceptions=True)
        await client.close()


async def test_callback_overload_is_bounded_and_disables_control():
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow(payload):
        started.set()
        await release.wait()

    client = HostCommClient("127.0.0.1", 1, on_status=slow, callback_queue_size=2)
    client._connected = True
    client._handshake_complete = True
    try:
        await client._dispatch(make_frame("status_snapshot", {}))
        await started.wait()
        for _ in range(8):
            await client._dispatch(make_frame("status_snapshot", {}))
        assert client.stats["callback_queue_depth"] <= 2
        assert client.stats["callback_dropped"] > 0
        assert not client.is_online
        assert client.comm_quality == "degraded"
    finally:
        release.set()
        await client.close()


async def test_legacy_snapshot_timeout_invalidates_session(monkeypatch):
    from app.hostcomm.client import HostCommTimeoutError

    client = HostCommClient("127.0.0.1", 1, command_timeout=0.01, auto_reconnect=False)
    client._connected = True

    async def send(frame):
        pass

    monkeypatch.setattr(client, "_send", send)
    with pytest.raises(HostCommTimeoutError):
        await client.get_parameters()
    assert client.stats["connected"] is False
    await client.close()


async def test_mismatching_command_result_is_never_accepted(mock_server, monkeypatch):
    async def wrong_result(frame, payload, writer):
        await mock_server._send(
            writer,
            make_frame(
                "command_result",
                {
                    "request_msg_id": frame["msg_id"],
                    "command": "another_command",
                    "result": "accepted",
                },
            ),
        )

    monkeypatch.setattr(mock_server, "_on_command", wrong_result)
    client = await make_client(mock_server)
    await client.connect()
    try:
        with pytest.raises(HostCommError):
            await client.send_command("tare_balance", {}, operator_id="a", role="operator")
    finally:
        await client.close()


async def test_data_before_validated_handshake_is_not_published():
    received = []
    client = HostCommClient("127.0.0.1", 1, on_status=received.append, on_event=received.append)
    try:
        await client._dispatch(make_frame("status_snapshot", {"state_machine": {"current_state": "Standby"}}))
        await client._dispatch(make_frame("event", {"event_code": "measurement_complete"}))
        await asyncio.sleep(0)
        assert received == []
    finally:
        await client.close()
