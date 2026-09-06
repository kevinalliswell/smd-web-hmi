"""Bounded HostComm 2.0 connection transport; business actions are never retried here."""

from __future__ import annotations

import asyncio
import copy
import ipaddress
import random
import time
import uuid
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.core.logging import get_logger
from app.hostcomm.v2_contract.codec import decode_chunk, encode_message, strict_loads, validate_message
from app.hostcomm.v2_contract.types import MAX_FRAME_BYTES
from app.hostcomm.v2_security import V2SecurityError, create_client_context, psk_identity, verify_tls

logger = get_logger("hostcomm.v2_transport")
Callback = Callable[[dict], Awaitable[None]]
REPLIES = {
    "hello": "hello_ack",
    "heartbeat": "heartbeat_ack",
    "get_status": "status_snapshot",
    "get_profile": "profile_snapshot",
    "get_alarms": "alarms_snapshot",
    "command": "command_result",
    "get_operation": "operation_snapshot",
    "recipe_begin": "recipe_transfer_result",
    "recipe_chunk": "recipe_transfer_result",
    "get_recipe": "recipe_snapshot",
    "log_request": "log_result",
}


class V2TransportError(Exception):
    pass


class V2CapacityError(V2TransportError):
    """A bounded request slot is occupied; no new bytes were sent."""


class V2OfflineError(V2TransportError):
    pass


class V2ProtocolError(V2TransportError):
    pass


class V2RemoteError(V2ProtocolError):
    def __init__(self, payload: dict):
        self.code = payload["code"]
        self.payload = copy.deepcopy(payload)
        super().__init__("Device rejected request: " + self.code)


class V2RequestTimeout(TimeoutError, V2TransportError):
    """Request bytes may have reached the peer; no claim about business outcome."""


@dataclass
class Pending:
    kind: str
    payload: dict
    future: asyncio.Future
    progress: float = field(default_factory=time.monotonic)
    log_received_offset: int = 0
    log_acked_offset: int = 0


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class V2Transport:
    def __init__(
        self,
        host: str,
        port: int,
        *,
        device_id: str,
        controller_id: str,
        controller_epoch: str,
        psk_file,
        client_version: str,
        on_message: Callback | None = None,
        on_connection: Callback | None = None,
        mock: bool = False,
        response_timeout: float = 3.0,
        heartbeat_interval: float = 2.0,
        frame_timeout: float = 5.0,
        connect_timeout: float = 5.0,
        reconnect_base: float = 1.0,
        queue_capacity: int = 256,
    ):
        self.host, self.port = host, port
        self.device_id, self.controller_id, self.controller_epoch = device_id, controller_id, controller_epoch
        self.psk_file, self.client_version, self.mock = psk_file, client_version, mock
        self.on_message, self.on_connection = on_message, on_connection
        self.response_timeout, self.heartbeat_interval = response_timeout, heartbeat_interval
        self.frame_timeout, self.connect_timeout, self.reconnect_base = frame_timeout, connect_timeout, reconnect_base
        if (
            not 1 <= queue_capacity <= 4096
            or min(response_timeout, heartbeat_interval, frame_timeout, connect_timeout, reconnect_base) <= 0
        ):
            raise ValueError("Transport timeouts and bounded queue capacity must be positive")
        self.boot_id = self.session_id = self.lease_id = None
        self.hello_payload: dict | None = None
        self._online = False
        self._callback_fault = False
        self._stopping = False
        self._started = time.monotonic()
        self._reader = self._writer = None
        self._main_task = self._reader_task = self._heartbeat_task = None
        self._callback_task = self._connection_task = None
        self._initial = None
        self._disconnected = asyncio.Event()
        self._pending: dict[str, Pending] = {}
        self._retired = OrderedDict()
        self._receipts = OrderedDict()
        self._write_lock = asyncio.Lock()
        self._callbacks = asyncio.Queue(maxsize=queue_capacity)
        self._connections = asyncio.Queue(maxsize=8)
        self.frames_received = self.invalid_frames = self.dropped_callbacks = 0
        self.last_error: str | None = None
        self._credentials_notice_identity = None

    @property
    def is_online(self) -> bool:
        return self._online and not self._stopping and not self._callback_fault

    def set_lease(self, lease_id: str | None) -> None:
        if lease_id is not None:
            if not self.is_online:
                raise V2OfflineError("Cannot retain a lease for an offline connection")
            uuid.UUID(hex=lease_id)
            if len(lease_id) != 32 or lease_id != lease_id.lower():
                raise ValueError("lease_id must be lowercase UUID hex")
        self.lease_id = lease_id

    def receipt_metadata(self, msg_id: str) -> dict:
        result = dict(self._receipts.get(msg_id, {}))
        if result:
            result["queue_delay_s"] = max(0, time.monotonic() - result["received_monotonic"])
        return result

    @property
    def stats(self) -> dict:
        return {
            "frames_received": self.frames_received,
            "invalid_frames": self.invalid_frames,
            "dropped_callbacks": self.dropped_callbacks,
            "callback_queue_depth": self._callbacks.qsize(),
            "pending_requests": len(self._pending),
            "last_error": self.last_error,
        }

    async def start(self) -> None:
        if self._main_task and not self._main_task.done():
            return
        self._stopping = False
        self._initial = asyncio.get_running_loop().create_future()
        self._callback_task = asyncio.create_task(self._callback_loop(), name="hostcomm-v2-callback")
        self._connection_task = asyncio.create_task(self._connection_loop(), name="hostcomm-v2-connection")
        self._main_task = asyncio.create_task(self._run(), name="hostcomm-v2-manager")
        await asyncio.shield(self._initial)

    async def close(self) -> None:
        self._stopping = True
        self._disconnect("closed")
        tasks = [self._main_task, self._reader_task, self._heartbeat_task, self._callback_task, self._connection_task]
        for task in tasks:
            if task and task is not asyncio.current_task():
                task.cancel()
        await asyncio.gather(
            *(task for task in tasks if task and task is not asyncio.current_task()), return_exceptions=True
        )
        await self._teardown()

    async def _run(self) -> None:
        delay = self.reconnect_base
        try:
            while not self._stopping:
                if self._callback_fault:
                    await asyncio.sleep(min(self.reconnect_base, 0.1))
                    continue
                self._disconnected.clear()
                try:
                    await self._open()
                    delay = self.reconnect_base
                    if not self._initial.done():
                        self._initial.set_result(None)
                    await self._disconnected.wait()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.last_error = (
                        str(exc) if isinstance(exc, (V2SecurityError, V2TransportError)) else type(exc).__name__
                    )
                    self._disconnect(
                        "security_configuration" if isinstance(exc, V2SecurityError) else "connection_failed"
                    )
                finally:
                    if not self._initial.done():
                        self._initial.set_result(None)
                    await self._teardown()
                if not self._stopping:
                    await asyncio.sleep(delay * random.uniform(0.9, 1.1))
                    delay = min(delay * 2, 30.0)
        finally:
            self._online = False

    async def _open(self) -> None:
        identity = psk_identity(self.device_id, self.controller_id, self.controller_epoch)
        if self.mock:
            try:
                if not ipaddress.ip_address(self.host).is_loopback:
                    raise ValueError
            except ValueError as exc:
                raise V2SecurityError("Plaintext simulator transport requires an explicit loopback IP address") from exc
            options = {}
        else:
            if not self.psk_file:
                raise V2SecurityError("No HostComm 2.0 pairing key is configured")
            options = {
                "ssl": create_client_context(self.psk_file, identity),
                "server_hostname": "",
                "ssl_handshake_timeout": self.connect_timeout,
            }
            credentials_identity = (identity, str(self.psk_file))
            if self._credentials_notice_identity != credentials_identity:
                # This proves only local credential/ACL/runtime loading; no TCP
                # connection or authenticated device handshake has happened yet.
                logger.info("v2.tls_credentials_loaded", device_id=self.device_id, handshake="not_started")
                self._credentials_notice_identity = credentials_identity
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port, limit=MAX_FRAME_BYTES + 1, **options), self.connect_timeout
        )
        if not self.mock:
            verify_tls(self._writer)
        self.boot_id = self.session_id = None
        self.hello_payload = None
        self._reader_task = asyncio.create_task(self._read_loop(), name="hostcomm-v2-reader")
        response = await self.request(
            "hello",
            {
                "design_revision": "2.0-design.1",
                "controller_id": self.controller_id,
                "controller_epoch": self.controller_epoch,
                "expected_device_id": self.device_id,
                "client_name": "smd-web-hmi",
                "client_version": self.client_version,
            },
        )
        payload = response["payload"]
        if payload["device_id"] != self.device_id or payload["controller_epoch"] != self.controller_epoch:
            raise V2ProtocolError("hello_ack does not match the configured authenticated pairing")
        if self._disconnected.is_set():
            raise V2OfflineError("Peer disconnected during handshake completion")
        self.boot_id, self.session_id, self.hello_payload = response["boot_id"], response["session_id"], payload
        self._online = True
        self.last_error = None
        self._notify("online", "handshake_complete")
        self._heartbeat_task = asyncio.create_task(self._heartbeats(), name="hostcomm-v2-heartbeat")

    def _notify(self, status: str, reason: str) -> None:
        notice = {
            "status": status,
            "reason": reason,
            "session_id": self.session_id,
            "boot_id": self.boot_id,
            "dropped_callbacks": self.dropped_callbacks,
            "detail": self.last_error,
        }
        if self._connections.full():
            self._connections.get_nowait()
            self._connections.task_done()
        self._connections.put_nowait(notice)

    def _disconnect(self, reason: str) -> None:
        already = self._disconnected.is_set()
        self._online = False
        self.lease_id = None
        self._disconnected.set()
        for pending in self._pending.values():
            if not pending.future.done():
                pending.future.set_exception(V2OfflineError("HostComm connection lost; request outcome may be unknown"))
        if self._writer:
            self._writer.close()
        if not already:
            self._notify("offline", reason)

    async def _teardown(self) -> None:
        for task in (self._reader_task, self._heartbeat_task):
            if task and task is not asyncio.current_task():
                task.cancel()
        await asyncio.gather(
            *(
                task
                for task in (self._reader_task, self._heartbeat_task)
                if task and task is not asyncio.current_task()
            ),
            return_exceptions=True,
        )
        writer, self._writer = self._writer, None
        self._reader = None
        if writer:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), 1)
            except (TimeoutError, ConnectionError):
                pass
        self._reader_task = self._heartbeat_task = None
        self.boot_id = self.session_id = self.lease_id = None

    def _frame(self, kind: str, payload: dict, msg_id: str | None, reply_to: str | None) -> dict:
        return {
            "protocol_version": "2.0",
            "msg_id": msg_id or uuid.uuid4().hex,
            "reply_to": reply_to,
            "session_id": None if kind == "hello" else self.session_id,
            "boot_id": None if kind == "hello" else self.boot_id,
            "timestamp": utc_now(),
            "uptime_ms": "0" if kind == "hello" else str(int((time.monotonic() - self._started) * 1000)),
            "type": kind,
            "payload": payload,
        }

    async def send(self, message_type: str, payload: dict, *, reply_to=None, msg_id=None) -> None:
        if message_type not in {*REPLIES, "log_ack"} or reply_to is not None:
            raise V2ProtocolError("Client may only send defined requests and log acknowledgements")
        if (not self.is_online and message_type != "hello") or self._writer is None:
            raise V2OfflineError("HostComm 2.0 handshake is not complete")
        frame = self._frame(message_type, payload, msg_id, reply_to)
        raw = encode_message(frame)
        writer = self._writer
        log_pending = None
        if message_type == "log_ack":
            log_pending = next(
                (
                    item
                    for item in self._pending.values()
                    if item.kind == "log_request" and item.payload["transfer_id"] == payload["transfer_id"]
                ),
                None,
            )
            if (
                log_pending is None
                or payload["log_id"] != log_pending.payload["requested"]["log_id"]
                or not log_pending.log_acked_offset <= payload["next_offset"] <= log_pending.log_received_offset
            ):
                raise V2ProtocolError("Log ACK does not match a pending transfer and received byte range")

        async def write():
            async with self._write_lock:
                if writer is not self._writer or writer.is_closing():
                    raise V2OfflineError("Connection changed before write")
                writer.write(raw)
                if log_pending is not None:
                    log_pending.log_acked_offset = payload["next_offset"]
                await writer.drain()

        try:
            await asyncio.wait_for(write(), self.response_timeout)
        except (TimeoutError, ConnectionError):
            self._disconnect("write_failed")
            raise V2OfflineError("HostComm write failed; request outcome may be unknown") from None

    def _check_capacity(self, kind: str, payload: dict) -> None:
        active = list(self._pending.values())
        if kind == "command":
            stop = payload.get("command") == "stop_run"
            if any(item.kind == "command" and (item.payload.get("command") == "stop_run") == stop for item in active):
                raise V2CapacityError("One ordinary command and one independent safety stop may be pending")
        elif kind == "heartbeat":
            if any(item.kind == kind for item in active):
                raise V2CapacityError("A heartbeat is already pending")
        elif kind != "hello":
            reads = [item for item in active if item.kind not in {"heartbeat", "hello", "command"}]
            if len(reads) >= 4 or (kind == "log_request" and any(item.kind == kind for item in reads)):
                raise V2CapacityError("Read request capacity exhausted")

    async def request(
        self, message_type: str, payload: dict, timeout: float | None = None, msg_id: str | None = None
    ) -> dict:
        if message_type not in REPLIES:
            raise V2ProtocolError("Message does not have a request/response contract")
        self._check_capacity(message_type, payload)
        mid = msg_id or uuid.uuid4().hex
        if mid in self._pending or mid in self._retired:
            raise V2ProtocolError("A sent message ID cannot be reused; query the persistent operation")
        total = timeout if timeout is not None else (60.0 if message_type == "log_request" else self.response_timeout)
        if not 0 < total <= 1800:
            raise ValueError("Request deadline must be within 0..1800 seconds")
        pending = Pending(message_type, payload, asyncio.get_running_loop().create_future())
        self._pending[mid] = pending
        try:
            await self.send(message_type, payload, msg_id=mid)
            deadline = time.monotonic() + total
            while True:
                remaining = deadline - time.monotonic()
                if message_type == "log_request":
                    remaining = min(remaining, pending.progress + self.response_timeout - time.monotonic())
                if remaining <= 0:
                    raise V2RequestTimeout("HostComm response deadline exceeded; operation outcome may be unknown")
                try:
                    return await asyncio.wait_for(asyncio.shield(pending.future), remaining)
                except TimeoutError:
                    if (
                        message_type != "log_request"
                        or time.monotonic() >= deadline
                        or time.monotonic() >= pending.progress + self.response_timeout
                    ):
                        raise V2RequestTimeout(
                            "HostComm response deadline exceeded; operation outcome may be unknown"
                        ) from None
        finally:
            self._pending.pop(mid, None)
            self._retired[mid] = None
            if len(self._retired) > 256:
                self._retired.popitem(last=False)
            if not pending.future.done():
                pending.future.cancel()
            elif not pending.future.cancelled():
                pending.future.exception()  # Retrieve failure if send/cancellation won the race.

    async def _read_loop(self) -> None:
        buffer = bytearray()
        started = None
        discarding = False
        consecutive = 0
        try:
            while not self._stopping:
                remaining = None if started is None else self.frame_timeout - (time.monotonic() - started)
                if remaining is not None and remaining <= 0:
                    raise V2ProtocolError("Incomplete frame exceeded absolute deadline")
                data = await asyncio.wait_for(self._reader.read(1024), remaining)
                if not data:
                    raise V2OfflineError("Peer closed connection")
                for byte in data:
                    if started is None:
                        started = time.monotonic()
                    if byte == 10:
                        if discarding:
                            discarding = False
                        else:
                            try:
                                if b"\r" in buffer:
                                    raise ValueError("CRLF is forbidden")
                                message = validate_message(strict_loads(bytes(buffer))).model_dump(mode="python")
                            except (ValueError, UnicodeError):
                                self.invalid_frames += 1
                                consecutive += 1
                            else:
                                self._dispatch(message)
                                consecutive = 0
                        buffer.clear()
                        started = None
                    elif not discarding:
                        if len(buffer) == MAX_FRAME_BYTES:
                            buffer.clear()
                            discarding = True
                            self.invalid_frames += 1
                            consecutive += 1
                        else:
                            buffer.append(byte)
                    if consecutive >= 3:
                        raise V2ProtocolError("Three consecutive malformed frames")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.last_error = type(exc).__name__
            self._disconnect(
                "protocol_error" if isinstance(exc, (ValueError, V2ProtocolError, TimeoutError)) else "connection_lost"
            )

    def _dispatch(self, message: dict) -> None:
        kind, reply = message["type"], message["reply_to"]
        pending = self._pending.get(reply)
        if kind != "hello_ack" and (message["session_id"] != self.session_id or message["boot_id"] != self.boot_id):
            raise V2ProtocolError("Message does not belong to current session and boot")
        self.frames_received += 1
        self._receipts[message["msg_id"]] = {"received_at": utc_now(), "received_monotonic": time.monotonic()}
        if len(self._receipts) > 512:
            self._receipts.popitem(last=False)
        if reply is not None:
            if pending is None:
                if reply in self._retired:
                    return  # A late response cannot settle a newer operation.
                raise V2ProtocolError("Response refers to an unknown request")
            if kind == "error":
                if not pending.future.done():
                    pending.future.set_exception(V2RemoteError(message["payload"]))
                return
            if kind == "log_chunk" and pending.kind == "log_request":
                part = message["payload"]
                if (
                    part["transfer_id"] != pending.payload["transfer_id"]
                    or part["log_id"] != pending.payload["requested"]["log_id"]
                ):
                    raise V2ProtocolError("Log transfer identity mismatch")
                end = part["offset"] + len(decode_chunk(part["data_b64"]))
                if part["offset"] > pending.log_received_offset or end > pending.payload["max_bytes"]:
                    raise V2ProtocolError("Log chunk exceeds contiguous receive window or byte budget")
                if end > pending.log_received_offset and pending.log_received_offset != pending.log_acked_offset:
                    raise V2ProtocolError("Log peer exceeded its one-unacknowledged-chunk window")
                pending.log_received_offset = max(pending.log_received_offset, end)
                pending.progress = time.monotonic()
                self._enqueue(message)
                return
            if kind != REPLIES[pending.kind]:
                raise V2ProtocolError("Response type does not match its request")
            if kind == "log_result" and (
                message["payload"]["transfer_id"] != pending.payload["transfer_id"]
                or message["payload"]["requested"] != pending.payload["requested"]
                or message["payload"]["byte_length"] != pending.log_received_offset
                or pending.log_received_offset != pending.log_acked_offset
            ):
                raise V2ProtocolError("Log terminal arrived before complete acknowledged byte transfer")
            if kind == "hello_ack":
                payload = message["payload"]
                if (
                    self.session_id is not None
                    or payload["device_id"] != self.device_id
                    or payload["controller_epoch"] != self.controller_epoch
                ):
                    raise V2ProtocolError("hello_ack does not match the authenticated pairing or handshake state")
                # The next frame can already be in this TCP read. Install verified
                # context before resolving the handshake waiter, not after a task switch.
                self.boot_id, self.session_id, self.hello_payload = message["boot_id"], message["session_id"], payload
                self._online = True
            if pending.kind in {"command", "get_operation"}:
                for key in ("operation_id", "controller_epoch", "command_seq"):
                    if message["payload"][key] != pending.payload[key]:
                        raise V2ProtocolError("Operation result identity mismatch")
            # A connectivity-only heartbeat may overlap lease acquisition or
            # release. Its reply never grants ownership; the operation layer
            # must still confirm the current boot/session/owner via get_status.
            if (
                pending.kind == "heartbeat"
                and pending.payload["lease_id"] is not None
                and message["payload"]["lease_id"] != pending.payload["lease_id"]
            ):
                raise V2ProtocolError("Heartbeat did not confirm the requested lease")
            if not pending.future.done():
                pending.future.set_result(message)
            return
        if kind not in {"telemetry", "event"} or not self.is_online:
            raise V2ProtocolError("Unexpected uncorrelated peer message")
        self._enqueue(message)

    def _enqueue(self, message: dict) -> None:
        try:
            self._callbacks.put_nowait(message)
        except asyncio.QueueFull:
            self.dropped_callbacks += 1
            self._callback_fault = True
            self._disconnect("callback_overload")

    async def _callback_loop(self) -> None:
        while True:
            message = await self._callbacks.get()
            try:
                if self.on_message:
                    await self.on_message(message)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.dropped_callbacks += 1
                self._callback_fault = True
                self._disconnect("callback_failed")
            finally:
                self._callbacks.task_done()
                if self._callbacks.empty():
                    self._callback_fault = False

    async def _connection_loop(self) -> None:
        while True:
            notice = await self._connections.get()
            try:
                if self.on_connection:
                    await self.on_connection(notice)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("connection_callback_failed", error=type(exc).__name__)
            finally:
                self._connections.task_done()

    async def _heartbeats(self) -> None:
        try:
            while self.is_online:
                await asyncio.sleep(self.heartbeat_interval)
                await self.request("heartbeat", {"lease_id": self.lease_id})
        except asyncio.CancelledError:
            raise
        except Exception:
            self._disconnect("heartbeat_timeout")
