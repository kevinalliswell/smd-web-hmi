"""Loopback tests for the actual async transport; no physical actuators are connected."""

import asyncio
import copy
import json
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.hostcomm.v2_contract.codec import command_digest, encode_message, strict_loads
from app.hostcomm.v2_transport import V2CapacityError, V2OfflineError, V2RemoteError, V2Transport, V2TransportError

VECTORS = json.loads(
    (Path(__file__).resolve().parents[2] / "contracts/hostcomm/v2/vectors.json").read_text(encoding="utf-8")
)


def example(name):
    return copy.deepcopy(next(item["value"] for item in VECTORS["valid_messages"] if item["name"] == name))


class Peer:
    def __init__(self, mode="normal"):
        self.mode = mode
        self.connections = 0
        self.commands = 0
        self.writers = []
        self.session = None

    async def start(self):
        self.server = await asyncio.start_server(self.serve, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def reply(self, writer, request, kind, payload):
        frame = example(kind)
        frame.update(msg_id=uuid.uuid4().hex, reply_to=request["msg_id"], session_id=self.session)
        frame["payload"] = payload
        if request["type"] == "get_status" and self.mode in {"wrong_session", "wrong_boot", "wrong_reply"}:
            frame[{"wrong_session": "session_id", "wrong_boot": "boot_id", "wrong_reply": "reply_to"}[self.mode]] = (
                "f" * 32
            )
        writer.write(encode_message(frame))
        await writer.drain()

    async def serve(self, reader, writer):
        self.connections += 1
        self.writers.append(writer)
        try:
            while line := await reader.readline():
                request = strict_loads(line[:-1])
                kind = request["type"]
                if kind == "hello":
                    self.session = uuid.uuid4().hex
                    payload = example("hello_ack")["payload"]
                    if self.mode == "wrong_device":
                        payload["device_id"] = "f" * 32
                    await self.reply(writer, request, "hello_ack", payload)
                    if self.mode == "push_immediately":
                        frame = example("telemetry")
                        frame.update(session_id=self.session, msg_id=uuid.uuid4().hex)
                        writer.write(encode_message(frame))
                        await writer.drain()
                elif kind == "heartbeat" and self.mode != "ignore_heartbeat":
                    payload = example("heartbeat_ack")["payload"]
                    payload.update(
                        lease_id=request["payload"]["lease_id"],
                        lease_expires_uptime_ms=None if request["payload"]["lease_id"] is None else "20000",
                    )
                    if self.mode == "report_current_lease":
                        payload.update(lease_id="a" * 32, lease_expires_uptime_ms="20000")
                    elif self.mode == "report_no_lease":
                        payload.update(lease_id=None, lease_expires_uptime_ms=None)
                    await self.reply(writer, request, "heartbeat_ack", payload)
                elif kind == "get_status":
                    await self.reply(writer, request, "status_snapshot", example("status_snapshot")["payload"])
                elif kind == "command":
                    self.commands += 1  # Deliberately lose ACK after the first request bytes arrive.
                elif kind == "log_request":
                    self.log_request = request
                    await self.reply(writer, request, "log_chunk", example("log_chunk")["payload"])
                    if self.mode == "premature_terminal":
                        await self.reply(writer, request, "log_result", example("log_result")["payload"])
                elif kind == "log_ack":
                    await self.reply(writer, self.log_request, "log_result", example("log_result")["payload"])
                elif kind == "get_alarms":
                    await self.reply(
                        writer,
                        request,
                        "error",
                        {"code": "state_conflict", "message": "Alarm revision changed", "retryable": True},
                    )
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()

    async def close(self):
        for writer in self.writers:
            writer.close()
        self.server.close()
        await self.server.wait_closed()


def transport(peer, **options):
    hello = example("hello")["payload"]
    defaults = dict(mock=True, response_timeout=0.15, heartbeat_interval=0.05, frame_timeout=0.12, reconnect_base=1)
    defaults.update(options)
    return V2Transport(
        "127.0.0.1",
        peer.port,
        device_id=hello["expected_device_id"],
        controller_id=hello["controller_id"],
        controller_epoch=hello["controller_epoch"],
        psk_file=None,
        client_version="test",
        **defaults,
    )


def seed_lease(client, lease_id):
    if lease_id is None:
        client.clear_lease(client.lease_token())
        return
    frame = example("status_snapshot")
    frame.update(session_id=client.session_id, boot_id=client.boot_id)
    frame["payload"].update(
        lease_id=lease_id, lease_owner_controller_id=client.controller_id, lease_owner_session_id=client.session_id
    )
    assert client.leases.confirm(frame, client.lease_token(), lease_id, started=time.monotonic(), now=time.monotonic())


async def eventually(check, seconds=1):
    async with asyncio.timeout(seconds):
        while not check():
            await asyncio.sleep(0.005)


async def test_handshake_and_correlated_request_preserve_receipt_metadata():
    peer = await Peer().start()
    client = transport(peer)
    try:
        await client.start()
        assert client.is_online
        reply = await client.request("get_status", {})
        assert reply["session_id"] == client.session_id
        assert client.receipt_metadata(reply["msg_id"])["received_monotonic"] > 0
    finally:
        await client.close()
        await peer.close()


@pytest.mark.parametrize("mode", ["wrong_device", "wrong_session", "wrong_boot", "wrong_reply"])
async def test_wrong_peer_or_response_context_fails_closed(mode):
    peer = await Peer(mode).start()
    client = transport(peer)
    try:
        await client.start()
        if mode == "wrong_device":
            assert not client.is_online
        else:
            with pytest.raises(V2TransportError):
                await client.request("get_status", {})
            assert not client.is_online
    finally:
        await client.close()
        await peer.close()


async def test_command_timeout_never_retransmits_and_reconnect_clears_lease():
    peer = await Peer().start()
    client = transport(peer)
    try:
        await client.start()
        seed_lease(client, "a" * 32)
        payload = example("command")["payload"]
        payload["lease_id"] = client.lease_id
        payload["expected_state_revision"] = str(client.leases.minimum_revision)
        payload["request_digest"] = command_digest(payload)
        with pytest.raises(TimeoutError):
            await client.request("command", payload)
        assert peer.commands == 1
        peer.writers[0].close()
        await eventually(lambda: not client.is_online)
        assert client.lease_id is None
        assert peer.commands == 1
    finally:
        await client.close()
        await peer.close()


async def test_heartbeat_deadline_and_half_frame_absolute_deadline_disconnect():
    for mode in ("ignore_heartbeat", "normal"):
        peer = await Peer(mode).start()
        client = transport(peer, heartbeat_interval=0.02)
        try:
            await client.start()
            assert client.is_online
            if mode == "normal":
                peer.writers[0].write(b"{")
                await peer.writers[0].drain()
            await eventually(lambda: not client.is_online)
        finally:
            await client.close()
            await peer.close()


@pytest.mark.parametrize("local_lease", [None, "b" * 32])
async def test_connectivity_heartbeat_never_adopts_or_erases_a_lease(local_lease):
    peer = await Peer("report_current_lease").start()
    client = transport(peer, heartbeat_interval=30)
    try:
        await client.start()
        seed_lease(client, local_lease)
        reply = await client.request("heartbeat", {"lease_id": None})
        assert reply["payload"]["lease_id"] == "a" * 32
        assert client.is_online
        assert client.lease_id == local_lease
    finally:
        await client.close()
        await peer.close()


@pytest.mark.parametrize("mode", ["report_current_lease", "report_no_lease"])
async def test_renewal_heartbeat_rejects_different_or_missing_lease(mode):
    peer = await Peer(mode).start()
    client = transport(peer, heartbeat_interval=30)
    try:
        await client.start()
        seed_lease(client, "b" * 32)
        with pytest.raises(V2TransportError):
            await client.request("heartbeat", {"lease_id": "b" * 32})
        assert not client.is_online
        assert client.lease_id is None
    finally:
        await client.close()
        await peer.close()


async def test_slow_callback_cannot_block_request_ack_and_overflow_is_visible():
    gate = asyncio.Event()
    notices = []

    async def callback(message):
        await gate.wait()

    async def connection(message):
        notices.append(message)

    peer = await Peer().start()
    client = transport(peer, on_message=callback, on_connection=connection, queue_capacity=2)
    try:
        await client.start()
        frame = example("telemetry")
        frame.update(session_id=client.session_id, msg_id=uuid.uuid4().hex)
        peer.writers[0].write(encode_message(frame))
        await peer.writers[0].drain()
        await asyncio.sleep(0.01)
        assert (await client.request("get_status", {}))["type"] == "status_snapshot"
        for _ in range(5):
            frame["msg_id"] = uuid.uuid4().hex
            peer.writers[0].write(encode_message(frame))
        await peer.writers[0].drain()
        await eventually(lambda: not client.is_online)
        await eventually(lambda: any(item["reason"] == "callback_overload" for item in notices))
    finally:
        gate.set()
        await client.close()
        await peer.close()


async def test_callback_admission_includes_inflight_but_not_later_callbacks():
    entered = [asyncio.Event(), asyncio.Event()]
    release = [asyncio.Event(), asyncio.Event()]

    async def callback(message):
        index = message["index"]
        entered[index].set()
        await release[index].wait()

    peer = await Peer().start()
    client = transport(peer, on_message=callback, response_timeout=1)
    barrier = None
    try:
        await client.start()
        client._enqueue({"index": 0})
        await asyncio.wait_for(entered[0].wait(), 1)
        barrier = asyncio.create_task(client.wait_for_callbacks())
        await eventually(lambda: len(client._callback_waiters) == 1)
        client._enqueue({"index": 1})
        assert not barrier.done()
        assert (await client.request("get_status", {}))["type"] == "status_snapshot"
        release[0].set()
        await asyncio.wait_for(entered[1].wait(), 1)
        context = await asyncio.wait_for(barrier, 1)
        client.check_callback_context(context)
        assert not release[1].is_set()
        assert not client._callback_waiters
        assert client.is_online and client.dropped_callbacks == 0
    finally:
        for event in release:
            event.set()
        if barrier:
            barrier.cancel()
            await asyncio.gather(barrier, return_exceptions=True)
        await client.close()
        await peer.close()


@pytest.mark.parametrize("end", ["timeout", "cancel", "disconnect", "close"])
async def test_callback_admission_waiters_are_removed_on_every_exit(end):
    entered, release = asyncio.Event(), asyncio.Event()

    async def callback(_message):
        entered.set()
        await release.wait()

    peer = await Peer().start()
    client = transport(peer, on_message=callback)
    barrier = None
    try:
        await client.start()
        client._enqueue({})
        await asyncio.wait_for(entered.wait(), 1)
        barrier = asyncio.create_task(client.wait_for_callbacks(timeout=0.1 if end == "timeout" else 3))
        await eventually(lambda: len(client._callback_waiters) == 1)
        if end == "cancel":
            barrier.cancel()
        elif end == "disconnect":
            client._disconnect("test_session_change")
        elif end == "close":
            await client.close()
        error = {"timeout": V2CapacityError, "cancel": asyncio.CancelledError}.get(end, V2OfflineError)
        with pytest.raises(error):
            await asyncio.wait_for(barrier, 1)
        assert not client._callback_waiters
        assert client._callbacks.qsize() == 0  # Admission never leaves queue markers behind.
        assert client.dropped_callbacks == 0
        if end in {"cancel", "timeout"}:
            assert client.is_online
            release.set()
            await client.wait_for_callbacks()
    finally:
        release.set()
        if barrier:
            barrier.cancel()
            await asyncio.gather(barrier, return_exceptions=True)
        await client.close()
        await peer.close()


async def test_callback_admission_has_bounded_capacity_without_consuming_live_queue_slots():
    entered, release = asyncio.Event(), asyncio.Event()

    async def callback(_message):
        entered.set()
        await release.wait()

    peer = await Peer().start()
    client = transport(peer, on_message=callback, queue_capacity=1)
    barriers = []
    try:
        await client.start()
        client._enqueue({})
        await asyncio.wait_for(entered.wait(), 1)
        barriers = [asyncio.create_task(client.wait_for_callbacks()) for _ in range(4)]
        await eventually(lambda: len(client._callback_waiters) == 4)
        with pytest.raises(V2CapacityError, match="capacity"):
            await client.wait_for_callbacks()
        for barrier in barriers:
            barrier.cancel()
        await asyncio.gather(*barriers, return_exceptions=True)
        client._enqueue({})
        assert client._callbacks.full()
        with pytest.raises(V2CapacityError, match="capacity"):
            await client.wait_for_callbacks()
        assert client.is_online and client.dropped_callbacks == 0
        assert not client._callback_waiters
        release.set()
        await asyncio.wait_for(client._callbacks.join(), 1)
        await client.wait_for_callbacks()
    finally:
        release.set()
        for barrier in barriers:
            barrier.cancel()
        await asyncio.gather(*barriers, return_exceptions=True)
        await client.close()
        await peer.close()


async def test_callback_admission_rejects_self_wait_and_context_reuse(monkeypatch):
    checked = asyncio.Event()

    async def callback(_message):
        with pytest.raises(V2CapacityError, match="own consumer"):
            await client.wait_for_callbacks()
        checked.set()

    peer = await Peer().start()
    client = transport(peer, on_message=callback)
    try:
        with pytest.raises(V2OfflineError):
            await client.wait_for_callbacks()
        await client.start()
        for timeout in (0, 3.001):
            with pytest.raises(ValueError):
                await client.wait_for_callbacks(timeout=timeout)
        client._enqueue({})
        await asyncio.wait_for(checked.wait(), 1)
        context = await client.wait_for_callbacks()
        for field in ("session_id", "boot_id", "_writer"):
            with monkeypatch.context() as patch:
                patch.setattr(client, field, object())
                with pytest.raises(V2OfflineError):
                    client.check_callback_context(context)
        client.check_callback_context(context)
        assert client.is_online and client.dropped_callbacks == 0
    finally:
        await client.close()
        await peer.close()


async def test_missing_key_keeps_backend_usable_without_plaintext_fallback():
    peer = await Peer().start()
    notices = []

    async def connection(message):
        notices.append(message)

    client = transport(peer, mock=False, on_connection=connection)
    try:
        await client.start()
        assert not client.is_online
        assert peer.connections == 0
        await eventually(lambda: bool(notices))
        assert notices[-1]["status"] == "offline"
    finally:
        await client.close()
        await peer.close()


async def test_telemetry_immediately_after_hello_ack_does_not_race_session_installation():
    received = []

    async def callback(frame):
        received.append(frame)

    peer = await Peer("push_immediately").start()
    client = transport(peer, on_message=callback)
    try:
        await client.start()
        await eventually(lambda: bool(received))
        assert client.is_online
        assert received[0]["session_id"] == client.session_id
    finally:
        await client.close()
        await peer.close()


async def test_oversized_line_tail_cannot_become_telemetry_and_three_bad_frames_disconnect():
    received = []

    async def callback(frame):
        received.append(frame)

    peer = await Peer().start()
    client = transport(peer, on_message=callback)
    try:
        await client.start()
        frame = example("telemetry")
        frame.update(session_id=client.session_id, msg_id=uuid.uuid4().hex)
        peer.writers[0].write(b"x" * 8193 + encode_message(frame))
        await peer.writers[0].drain()
        await eventually(lambda: client.invalid_frames == 1)
        assert not received
        peer.writers[0].write(b"{}\n{}\n")
        await peer.writers[0].drain()
        await eventually(lambda: not client.is_online)
        assert not received
    finally:
        await client.close()
        await peer.close()


@pytest.mark.parametrize("premature", [False, True])
async def test_log_terminal_waits_for_acknowledged_chunks(premature):
    delivered = []

    async def callback(message):
        if message["type"] == "log_chunk":
            delivered.append(message)
            if not premature:
                await client.send("log_ack", example("log_ack")["payload"])

    peer = await Peer("premature_terminal" if premature else "normal").start()
    client = transport(peer, on_message=callback)
    try:
        await client.start()
        if premature:
            with pytest.raises(V2TransportError):
                await client.request("log_request", example("log_request")["payload"])
        else:
            result = await client.request("log_request", example("log_request")["payload"])
            assert len(delivered) == 1
            assert result["type"] == "log_result"
    finally:
        await client.close()
        await peer.close()


async def test_remote_error_retains_structured_code_without_becoming_operation_result():
    peer = await Peer().start()
    client = transport(peer)
    try:
        await client.start()
        with pytest.raises(V2RemoteError) as error:
            await client.request("get_alarms", example("get_alarms")["payload"])
        assert error.value.code == "state_conflict"
        assert error.value.payload["retryable"] is True
        assert client.is_online
    finally:
        await client.close()
        await peer.close()


def test_saturated_read_slots_have_structured_capacity_error_but_do_not_take_the_stop_slot():
    client = transport(SimpleNamespace(port=1))
    client._pending = {str(i): SimpleNamespace(kind="get_status", payload={}) for i in range(4)}
    with pytest.raises(V2CapacityError):
        client._check_capacity("get_status", {})
    client._check_capacity("command", {"command": "stop_run"})
    client._check_capacity("heartbeat", {})


@pytest.mark.parametrize("lease,expiry", [(None, "20000"), ("a" * 32, None), ("a" * 32, "50000")])
async def test_single_correlated_invalid_lease_ack_disconnects_immediately(lease, expiry):
    class InvalidLeasePeer(Peer):
        async def reply(self, writer, request, kind, payload):
            if kind != "heartbeat_ack":
                return await super().reply(writer, request, kind, payload)
            frame = example(kind)
            frame.update(msg_id=uuid.uuid4().hex, reply_to=request["msg_id"], session_id=self.session)
            frame["payload"].update(lease_id=lease, lease_expires_uptime_ms=expiry)
            writer.write((json.dumps(frame) + "\n").encode("utf-8"))
            await writer.drain()

    peer = await InvalidLeasePeer().start()
    client = transport(peer, response_timeout=3, heartbeat_interval=30)
    request = None
    try:
        await client.start()
        request = asyncio.create_task(client.request("heartbeat", {"lease_id": None}))
        await eventually(lambda: not client.is_online, seconds=0.5)
        with pytest.raises(V2TransportError):
            await request
        assert client.invalid_frames <= 1
    finally:
        if request:
            request.cancel()
            await asyncio.gather(request, return_exceptions=True)
        await client.close()
        await peer.close()
