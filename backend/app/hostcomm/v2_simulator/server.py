"""Loopback-only HostComm 2.0 software device. No physical I/O exists here."""

import asyncio
import base64
import copy
import ipaddress
import time
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from app.hostcomm.v2_contract.codec import FrameDecoder, RecipeAssembler, encode_message
from app.hostcomm.v2_contract.messages import Measurements
from app.hostcomm.v2_security import create_server_context, psk_identity, verify_tls

from .fixtures import SimulatorPairing, synthetic_profile
from .state import DeviceError, DeviceState
from .storage import DeviceStore, StorageUnavailable
from .transfers import LogTransfer, Upload


@dataclass
class Session:
    writer: asyncio.StreamWriter
    id: str = field(default_factory=lambda: uuid4().hex)
    controller_id: str = ""
    epoch: str = ""
    role: str = "diagnostic"
    handshaken: bool = False
    upload: Upload | None = None
    log: LogTransfer | None = None
    outgoing: asyncio.PriorityQueue = field(default_factory=lambda: asyncio.PriorityQueue(maxsize=256))
    order: int = 0


class V2Simulator:
    def __init__(
        self,
        storage_path: str | Path,
        host="127.0.0.1",
        port=0,
        *,
        ssl_context=None,
        psk_file=None,
        pairing=None,
        test_plaintext=False,
        clock=None,
        profile=None,
        sample_period_ms=1000,
        auto_sample=False,
    ):
        if not ipaddress.ip_address(host).is_loopback:
            raise ValueError("inert simulator may bind only a numeric loopback address")
        if ssl_context is not None and psk_file is not None:
            raise ValueError("configure a TLS context or a PSK file, not both")
        if test_plaintext and (ssl_context is not None or psk_file is not None):
            raise ValueError("plaintext test mode cannot also configure TLS")
        if not test_plaintext and ssl_context is None and psk_file is None:
            raise ValueError("explicit TLS configuration or test_plaintext=True is required")
        self.host, self.port, self.pairing = host, port, pairing or SimulatorPairing()
        if psk_file is not None:
            ssl_context = create_server_context(
                psk_file,
                psk_identity(self.pairing.device_id, self.pairing.controller_id, self.pairing.controller_epoch),
            )
        self.ssl_context, self.test_plaintext = ssl_context, test_plaintext
        self._clock = clock or (lambda: int(time.monotonic() * 1000))
        self._origin, self._offset = self._clock(), 0
        self.store = DeviceStore(storage_path)
        self.state = DeviceState(self.store, profile or synthetic_profile(), self.pairing.device_id, self.now)
        self.sample_period_ms, self.auto_sample = sample_period_ms, auto_sample
        minimum = (1000000 + self.profile["resources"]["max_sample_rate_millihz"] - 1) // self.profile["resources"][
            "max_sample_rate_millihz"
        ]
        if sample_period_ms < minimum:
            self.store.close()
            raise ValueError("sampling period exceeds synthetic profile maximum")
        self._server = self._maintenance = None
        self._sessions = {}
        self._tasks = set()
        self._drop_replies = {}
        self._closed = False
        self.invalid_frames = 0

    def now(self):
        return max(0, self._clock() - self._origin + self._offset)

    @property
    def address(self):
        return self.host, self._server.sockets[0].getsockname()[1]

    @property
    def device_id(self):
        return self.pairing.device_id

    @property
    def boot_id(self):
        return self.state.data["boot_id"]

    @property
    def profile(self):
        return copy.deepcopy(self.state.profile)

    @property
    def fail_storage(self):
        return self.store.fail_writes

    @fail_storage.setter
    def fail_storage(self, value):
        self.store.fail_writes = value

    async def start(self):
        if self._closed:
            raise RuntimeError("closed simulator cannot restart; create a new instance with its storage path")
        if self._server:
            return self
        self.state.sample()
        self.state.commit()
        self.state.notifications.clear()
        options = {"ssl": self.ssl_context}
        if self.ssl_context:
            options["ssl_handshake_timeout"] = 5
        self._server = await asyncio.start_server(self._accept, self.host, self.port, **options)
        self._maintenance = asyncio.create_task(self._maintain())
        return self

    async def close(self):
        if self._closed:
            return
        self._closed = True
        if self._maintenance:
            self._maintenance.cancel()
            await asyncio.gather(self._maintenance, return_exceptions=True)
        if self._server:
            self._server.close()
        for session in list(self._sessions.values()):
            session.writer.close()
        if self._server and hasattr(self._server, "close_clients"):
            self._server.close_clients()  # Includes failed/unfinished TLS handshakes, before wait_closed().
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._server:
            await asyncio.wait_for(self._server.wait_closed(), 5)
        self.store.close()

    async def reboot(self):
        for session in list(self._sessions.values()):
            session.writer.close()
        await asyncio.gather(*(s.writer.wait_closed() for s in list(self._sessions.values())), return_exceptions=True)
        self._origin, self._offset = self._clock(), 0
        self.state.recover()
        self.state.sample()
        self.state.run_changed()
        self.state.commit()
        self._broadcast()

    def drop_reply(self, message_type, count=1):
        self._drop_replies[message_type] = count

    def evict_result(self, epoch, sequence):
        """Fault injection: force historical-detail loss while preserving the waterline."""
        key = f"{epoch}:{sequence}"
        result = self.state.data["operations"].get(key)
        if result and result["status"] == "accepted":
            raise ValueError("cannot evict an accepted operation")
        self.state.data["operations"].pop(key, None)
        self.state.commit()

    def remove_log_records(self, sequences, reason="storage_fault"):
        self.store.remove_records(sequences, reason)

    def set_measurements(self, **values):
        updated = copy.deepcopy(self.state.data["values"])
        for name, value in values.items():
            if name not in updated:
                raise ValueError("unknown measurement name")
            updated[name] = value if isinstance(value, dict) else {"value": value, "quality": "good", "age_ms": 0}
        Measurements.model_validate(updated)
        self.state.data["values"] = updated

    async def tick(self, milliseconds=0):
        if type(milliseconds) is not int or milliseconds < 0:
            raise ValueError("clock advance must be nonnegative integer milliseconds")
        self._offset += milliseconds
        self.state.tick()
        self._broadcast()

    advance = tick

    async def finish_measurement(self):
        self.state.finish_measurement()
        self._broadcast()

    async def complete_purge(self):
        if self.state.run["state"] != "safe_disposal":
            raise DeviceError("state_conflict")
        self.state.data["disposal_started"] = self.now() - self.profile["limits"]["purge_duration_ms"]
        await self.tick()

    async def complete_cooling(self):
        if self.state.run["state"] != "cooling":
            raise DeviceError("state_conflict")
        from app.hostcomm.v2_contract.codec import validate_recipe_bytes

        recipe = validate_recipe_bytes(self.state.data["active_recipe"]["raw"].encode())
        self.set_measurements(
            burden_mc=min(self.profile["limits"]["safe_end_burden_mc"], recipe.stages[-1].exit.value) - 1
        )
        self.state.data["stable_started"] = self.now() - 604800000
        await self.tick()

    async def first_drip(self, is_valid=True):
        self.state.first_drip(is_valid)
        self._broadcast()

    async def raise_alarm(self, code, **kwargs):
        alarm_id = self.state.raise_alarm(code, **kwargs)
        self._broadcast()
        return alarm_id

    async def clear_alarm(self, alarm_id, occurrence_seq="1"):
        self.state.alarm_transition(alarm_id, occurrence_seq, "cleared")
        self._broadcast()

    def _queue(self, session, kind, payload, reply_to=None):
        if self._drop_replies.get(kind, 0) and reply_to:
            self._drop_replies[kind] -= 1
            return
        msg = {
            "protocol_version": "2.0",
            "msg_id": uuid4().hex,
            "reply_to": reply_to,
            "session_id": session.id,
            "boot_id": self.boot_id,
            "timestamp": None,
            "uptime_ms": str(self.now()),
            "type": kind,
            "payload": payload,
        }
        raw = encode_message(msg)
        session.order += 1
        priority = 3 if kind.startswith("log_") else 2 if kind == "telemetry" else 1 if kind == "event" else 0
        try:
            session.outgoing.put_nowait((priority, session.order, raw))
        except asyncio.QueueFull:
            session.writer.close()  # Source logs remain durable for replay; slow readers cannot block other clients.

    def _broadcast(self):
        notifications, self.state.notifications = self.state.notifications, []
        for kind, payload in notifications:
            for session in list(self._sessions.values()):
                if session.handshaken:
                    self._queue(session, kind, payload)

    async def _writer(self, session):
        try:
            while True:
                _, _, raw = await session.outgoing.get()
                session.writer.write(raw)
                await asyncio.wait_for(session.writer.drain(), 3)
        finally:
            session.writer.close()

    async def _accept(self, reader, writer):
        task = asyncio.current_task()
        self._tasks.add(task)
        session = Session(writer)
        sender = None
        try:
            if len(self._sessions) >= 2:
                return
            if not self.test_plaintext:
                verify_tls(writer)
            self._sessions[session.id] = session
            sender = asyncio.create_task(self._writer(session))
            decoder, consecutive, partial_started = FrameDecoder(), 0, None
            hello_deadline = time.monotonic() + 3
            while not writer.is_closing():
                if partial_started is not None and time.monotonic() - partial_started >= 5:
                    return
                timeout = max(0.001, 5 - (time.monotonic() - partial_started)) if partial_started else None
                if not session.handshaken:
                    timeout = min(timeout or 3, max(0.001, hello_deadline - time.monotonic()))
                data = await asyncio.wait_for(reader.read(4096), timeout)
                if not data:
                    break
                for byte in data:
                    if partial_started is None:
                        partial_started = time.monotonic()
                    before = decoder.error_count
                    messages = decoder.feed(bytes([byte]))
                    errors = decoder.error_count - before
                    consecutive += errors
                    self.invalid_frames += errors
                    if consecutive >= 3 or (errors and not session.handshaken):
                        return
                    if byte == 10:
                        partial_started = None
                    for model in messages:
                        consecutive = 0
                        message = model.model_dump(mode="python")
                        if not session.handshaken:
                            if not self._hello(session, message):
                                return
                        else:
                            try:
                                self._dispatch(session, message)
                            except DeviceError as exc:
                                self._queue(
                                    session,
                                    "error",
                                    {"code": exc.code, "message": exc.code, "retryable": False},
                                    message["msg_id"],
                                )
                        self._broadcast()
        except (asyncio.TimeoutError, ConnectionError, ValueError, StorageUnavailable):
            pass
        finally:
            if sender:
                sender.cancel()
                await asyncio.gather(sender, return_exceptions=True)
            self._sessions.pop(session.id, None)
            try:
                self.state.lose_lease(session.id)
                self._broadcast()
            except StorageUnavailable:
                pass
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, ValueError):
                pass
            self._tasks.discard(task)

    def _hello(self, session, message):
        p, pair = message["payload"], self.pairing
        if message["type"] != "hello" or (p["controller_id"], p["controller_epoch"], p["expected_device_id"]) != (
            pair.controller_id,
            pair.controller_epoch,
            pair.device_id,
        ):
            return False
        session.controller_id, session.epoch, session.role = pair.controller_id, pair.controller_epoch, pair.role
        session.handshaken = True
        self._queue(
            session,
            "hello_ack",
            {
                "design_revision": "2.0-design.1",
                "device_id": pair.device_id,
                "controller_epoch": pair.controller_epoch,
                "fw_version": "inert-simulator-2.0",
                "hw_version": "NO-PHYSICAL-IO",
                "capabilities": ["durable_operations", "atomic_recipe", "sample_log", "alarm_log"],
                "profile_digest": self.profile["profile_digest"],
                "last_command_seq": str(self.state.data["highwater"].get(pair.controller_epoch, 0)),
                "control_ready": self.profile["approved"] and pair.role == "control",
                "granted_role": pair.role,
            },
            message["msg_id"],
        )
        return True

    def _dispatch(self, session, message):
        if message["session_id"] != session.id:
            raise DeviceError("stale_session")
        if message["boot_id"] != self.boot_id:
            raise DeviceError("boot_mismatch")
        kind, p, request_id = message["type"], message["payload"], message["msg_id"]
        lease = self.state.data["lease"]
        if lease and self.now() >= lease["expires"]:
            self.state.lose_lease()
        response, payload = None, None
        if kind == "heartbeat":
            response, payload = "heartbeat_ack", self.state.heartbeat(session.id, p["lease_id"])
        elif kind == "get_status":
            response, payload = "status_snapshot", self.state.status()
        elif kind == "get_profile":
            response, payload = "profile_snapshot", self.profile
        elif kind == "get_operation":
            response, payload = "operation_snapshot", self.state.lookup(p)
        elif kind == "command":
            raw = None
            if p["command"] == "activate_recipe" and session.upload:
                upload = session.upload.assembler
                if (
                    upload.begin.transfer_id == p["params"]["transfer_id"]
                    and upload.begin.recipe_digest == p["params"]["recipe_digest"]
                    and not upload.aborted
                    and len(upload.data) == upload.begin.byte_length
                ):
                    raw = bytes(upload.data)
            response, payload = "command_result", self.state.command(session, p, raw)
            if p["command"] == "activate_recipe" and payload["status"] == "applied":
                session.upload = None
        elif kind == "get_alarms":
            revision = str(self.state.data["alarm_revision"])
            if p["expected_revision"] not in (None, revision):
                raise DeviceError("state_conflict")
            alarms = [a for a in self.state.data["alarms"].values() if a["active"] or not a["acknowledged"]]
            offset = p["page_offset"]
            items = alarms[offset : offset + 8]  # Bounded below 8192 including event/source metadata.
            response, payload = "alarms_snapshot", {
                "revision": revision,
                "page_offset": offset,
                "items": items,
                "next_offset": offset + len(items) if offset + len(items) < len(alarms) else None,
            }
        elif kind in {"recipe_begin", "recipe_chunk"}:
            response, payload = "recipe_transfer_result", self._upload(session, kind, p)
        elif kind == "get_recipe":
            recipe = self.state.data["active_recipe"]
            if not recipe or recipe["digest"] != p["recipe_digest"]:
                raise DeviceError("recipe_invalid")
            raw = recipe["raw"].encode()
            if p["offset"] >= len(raw):
                raise DeviceError("offset_mismatch")
            response, payload = "recipe_snapshot", {
                "recipe_digest": recipe["digest"],
                "byte_length": len(raw),
                "offset": p["offset"],
                "data_b64": base64.b64encode(raw[p["offset"] : p["offset"] + 1536]).decode(),
                "active": True,
            }
        elif kind == "log_request":
            if session.log:
                raise DeviceError("busy")
            session.log = LogTransfer(self.state, p, request_id)
            self._log_send(session)
        elif kind == "log_ack":
            if not session.log:
                raise DeviceError("range_unavailable")
            if session.log.ack(p):
                self._log_send(session)
        else:
            raise DeviceError("invalid_frame")
        if response:
            self._queue(session, response, payload, request_id)

    def _upload(self, session, kind, p):
        if session.role != "control":
            raise DeviceError("permission_denied")
        if kind == "recipe_begin":
            if not self.state.lease_valid(session.id, p["lease_id"]):
                raise DeviceError("lease_required")
            if session.upload:
                raise DeviceError("busy")
            session.upload = Upload(RecipeAssembler(p), self.now())
        if not session.upload:
            raise DeviceError("recipe_invalid")
        upload = session.upload
        if not self.state.lease_valid(session.id, upload.assembler.begin.lease_id):
            session.upload = None
            raise DeviceError("lease_required")
        status, reason = "receiving", "ok"
        if kind == "recipe_chunk":
            previous = len(upload.assembler.data)
            try:
                recipe = upload.assembler.add(p)
                if len(upload.assembler.data) > previous:
                    upload.progressed_ms = self.now()
                if recipe is not None:
                    self.state.validate_recipe(bytes(upload.assembler.data))
                    status = "validated"
            except ValueError:
                status, reason = "rejected", "recipe_invalid"
                upload.assembler.aborted = True
        result = {
            "transfer_id": upload.assembler.begin.transfer_id,
            "recipe_digest": upload.assembler.begin.recipe_digest,
            "status": status,
            "next_offset": len(upload.assembler.data),
            "reason": reason,
        }
        if status == "rejected":
            session.upload = None
        return result

    def _log_send(self, session):
        transfer = session.log
        chunk = transfer.chunk()
        if chunk:
            self._queue(session, "log_chunk", chunk, transfer.request_id)
            transfer.sent_ms = self.now()
        else:
            self._queue(session, "log_result", transfer.result, transfer.request_id)
            session.log = None

    async def _maintain(self):
        last_sample = self.now()
        while True:
            await asyncio.sleep(0.05)
            try:
                lease = self.state.data["lease"]
                if lease and self.now() >= lease["expires"]:
                    self.state.lose_lease()
                if self.auto_sample and self.now() - last_sample >= self.sample_period_ms:
                    self.state.tick()
                    last_sample = self.now()
                for session in list(self._sessions.values()):
                    if session.upload and (
                        self.now() - session.upload.progressed_ms >= 120000
                        or not self.state.lease_valid(session.id, session.upload.assembler.begin.lease_id)
                    ):
                        session.upload = None
                    if session.log and self.now() - session.log.sent_ms >= 3000:
                        if session.log.retries >= 3:
                            session.writer.close()
                        else:
                            session.log.retries += 1
                            self._log_send(session)
                self._broadcast()
            except StorageUnavailable:
                pass
