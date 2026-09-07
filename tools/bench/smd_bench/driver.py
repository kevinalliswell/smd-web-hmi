"""Inert simulator subprocess; all test controls stay on its private stdio pipe."""

import asyncio
import hmac
import json
import secrets
import sys
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from app.hostcomm.v2_security import create_server_context, load_psk, psk_identity
from app.hostcomm.v2_simulator import SimulatorPairing, V2Simulator, synthetic_profile
from app.hostcomm.v2_simulator.state import DeviceError

from .contracts import DriverAction, DriverConfig


class BoundedTrace(list):
    def append(self, item):
        if len(self) >= 10000:
            raise RuntimeError("acceptance trace capacity exceeded")
        super().append(item)


class BenchSimulator(V2Simulator):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.reply_loss = None
        self.disconnect_chunk = False
        self.fault_receipts = BoundedTrace()
        self.wire_commands = BoundedTrace()
        self.tls_handshakes = BoundedTrace()
        self.psk_attempts = 0
        self.tls_accepts = BoundedTrace()
        self._request = None

    def arm_reply_loss(self, command: str) -> None:
        if self.reply_loss:
            raise ValueError("a reply fault is already armed")
        self.reply_loss = command

    def consume_reply_loss(self, kind: str, message: dict | None) -> bool:
        if (
            kind == "command_result"
            and message
            and message["type"] == "command"
            and self.reply_loss == message["payload"]["command"]
        ):
            self.fault_receipts.append(
                {
                    "fault": "lost_reply",
                    "command": self.reply_loss,
                    "operation_id": message["payload"].get("operation_id"),
                }
            )
            self.reply_loss = None
            return True
        return False

    async def _accept(self, reader, writer):
        tls = writer.get_extra_info("ssl_object")
        if tls:
            self.tls_accepts.append(
                {
                    "version": tls.version(),
                    "cipher": tls.cipher()[0],
                    "ticket": tls.session.has_ticket,
                    "reused": tls.session_reused,
                    "sessions": len(self._sessions),
                }
            )
        await super()._accept(reader, writer)

    def _hello(self, session, message):
        result = super()._hello(session, message)
        tls = session.writer.get_extra_info("ssl_object")
        if tls:
            self.tls_handshakes.append(
                {
                    "version": tls.version(),
                    "cipher": tls.cipher()[0],
                    "device_id": self.device_id,
                    "boot_id": self.boot_id,
                }
            )
        return result

    def _dispatch(self, session, message):
        self._request = message
        try:
            if message["type"] == "command":
                p = message["payload"]
                self.wire_commands.append({key: p[key] for key in ("command", "command_seq", "operation_id")})
            if message["type"] == "recipe_chunk" and self.disconnect_chunk:
                self.disconnect_chunk = False
                self.fault_receipts.append({"fault": "half_recipe", "transfer_id": message["payload"]["transfer_id"]})
                session.writer.close()
                return
            return super()._dispatch(session, message)
        finally:
            self._request = None

    def _queue(self, session, kind, payload, reply_to=None):
        if not self.consume_reply_loss(kind, self._request):
            super()._queue(session, kind, payload, reply_to)


class DeviceActions:
    def __init__(self, board: BenchSimulator):
        self.board = board

    def snapshot(self) -> dict:
        board = self.board
        if any(len(rows) > 10000 for rows in (board.fault_receipts, board.wire_commands, board.tls_handshakes)):
            raise RuntimeError("bounded acceptance trace exceeded capacity")
        return {
            "run": dict(board.state.run),
            "sample": dict(board.state.data["latest_sample"]),
            "boot_id": board.boot_id,
            "log": board.state.catalog(),
            "fault_receipts": list(board.fault_receipts),
            "wire_commands": list(board.wire_commands),
            "tls_handshakes": list(board.tls_handshakes),
            "psk_attempts": board.psk_attempts,
            "tls_accepts": list(board.tls_accepts),
            "invalid_frames": board.invalid_frames,
            "active_recipe_digest": (board.state.data.get("active_recipe") or {}).get("digest"),
            "fault_reset_required": board.state.data.get("fault_reset_required", False),
            "alarms": list(board.state.data["alarms"].values()),
        }

    async def execute(self, request: DriverAction) -> dict:
        board, action = self.board, request.action
        if action == "seed_unknown_run":
            state = board.state
            active = state.data.get("active_recipe")
            if state.run["state"] != "idle" or not active:
                raise ValueError("unknown-run fixture requires idle and an activated recipe")
            state.run.update(
                run_id=uuid4().hex,
                state="preparing",
                outcome="pending",
                recipe_digest=active["digest"],
                safety_profile_digest=state.profile["profile_digest"],
                stage_index=0,
            )
            state.data.update(first_drip_latched=False, stable_started=None)
            state.data.pop("ramp_initial_mc", None)
            state.revision()
            state.run_changed()
            state.commit()
            board.fault_receipts.append({"fault": "seed_unknown_run", "run_id": state.run["run_id"]})
            await board.tick()
        elif action == "sample":
            board.set_measurements(**request.values)
            await board.tick()  # Never fast-forward the lease or transport clock.
        elif action == "first_drip":
            await board.first_drip(request.valid)
        elif action in {"finish_measurement", "complete_purge", "complete_cooling", "reboot"}:
            await getattr(board, action)()
        elif action == "raise_alarm":
            if not request.code:
                raise ValueError("alarm code is required")
            await board.raise_alarm(request.code)
        elif action == "clear_alarm":
            if not request.alarm_id:
                raise ValueError("alarm identity is required")
            await board.clear_alarm(request.alarm_id)
        elif action == "drop_reply":
            if not request.command:
                raise ValueError("a matching command is required")
            board.arm_reply_loss(request.command)
        elif action == "half_recipe":
            board.disconnect_chunk = True
        elif action == "log_gap":
            if not request.sequences or any(not s.isdecimal() or int(s) < 1 for s in request.sequences):
                raise ValueError("positive source record sequences required")
            board.remove_log_records([int(s) for s in request.sequences])
            board.fault_receipts.append({"fault": "log_gap", "sequences": request.sequences})
        elif action == "disconnect":
            for session in list(board._sessions.values()):
                session.writer.close()
        return self.snapshot()


async def serve() -> None:
    first = await asyncio.to_thread(sys.stdin.buffer.readline, 8193)
    if len(first) > 8192 or not first.endswith(b"\n"):
        raise ValueError("driver configuration frame exceeds limit")
    config = DriverConfig.model_validate_json(first)
    pairing = json.loads(config.pairing_file.read_text(encoding="utf-8"))
    if Path(pairing["psk_file"]).name != pairing["psk_file"] or "\\" in pairing["psk_file"]:
        raise ValueError("pairing key must be a sibling file")
    key_path = config.pairing_file.parent / pairing["psk_file"]
    load_psk(key_path)
    if config.wrong_psk:
        key_path = config.storage.parent / "wrong-private.psk"
        key_path.write_text(secrets.token_hex(32) + "\n", encoding="ascii")
        key_path.chmod(0o600)
    tls_identity = psk_identity(pairing["device_id"], pairing["controller_id"], pairing["controller_epoch"])
    context = create_server_context(key_path, tls_identity)
    key = load_psk(key_path)
    board = BenchSimulator(
        config.storage,
        port=config.port,
        ssl_context=context,
        pairing=SimulatorPairing(pairing["device_id"], pairing["controller_id"], pairing["controller_epoch"]),
        profile=synthetic_profile(approved=True),
        auto_sample=True,
    )

    def counted_key(presented):
        board.psk_attempts += 1
        if board.psk_attempts > 10000:
            raise RuntimeError("handshake attempt capacity exceeded")
        return key if presented and hmac.compare_digest(presented, tls_identity) else b""

    context.set_psk_server_callback(counted_key)
    actions = DeviceActions(board)
    try:
        await board.start()
        print(json.dumps({"id": "ready", "ok": True, "result": actions.snapshot()}), flush=True)
        while True:
            raw = await asyncio.to_thread(sys.stdin.buffer.readline, 8193)
            if not raw:
                break
            if len(raw) > 8192 or not raw.endswith(b"\n"):
                raise ValueError("driver frame exceeds limit")
            identity = "invalid"
            try:
                request = DriverAction.model_validate_json(raw)
                identity = request.id
                result = await actions.execute(request)
                response = {"id": identity, "ok": True, "result": result}
            except (ValidationError, ValueError, DeviceError) as error:
                response = {"id": identity, "ok": False, "error": type(error).__name__}
            print(json.dumps(response), flush=True)
            if response["ok"] and request.action == "shutdown":
                break
    finally:
        await board.close()


def main() -> None:
    asyncio.run(serve())
