"""Bounded private pipe client for a fresh OpenSSL-configured simulator process."""

import asyncio
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

from app.hostcomm.v2_security import OPENSSL_AES128_POLICY

from .contracts import DriverAction, DriverConfig


class Worker:
    def __init__(self, private: Path, pairing: Path, *, port=34212):
        self.private, self.pairing, self.port = private, pairing, port
        self.process = self.stderr = None
        self.lock = asyncio.Lock()

    async def start(self, *, wrong_psk=False):
        if self.process:
            raise RuntimeError("simulator worker already started")
        policy = self.private / "openssl.cnf"
        policy.write_text(OPENSSL_AES128_POLICY, encoding="ascii")
        command = (
            [sys.executable, "driver"]
            if getattr(sys, "frozen", False)
            else [sys.executable, str(Path(__file__).resolve().parents[1] / "entry.py"), "driver"]
        )
        self.stderr = (self.private / "driver-private.log").open("ab")
        self.process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=self.stderr,
            env={**os.environ, "OPENSSL_CONF": str(policy)},
            limit=1024 * 1024,
        )
        config = DriverConfig(
            storage=self.private / "device.sqlite", pairing_file=self.pairing, port=self.port, wrong_psk=wrong_psk
        )
        self.process.stdin.write(config.model_dump_json().encode() + b"\n")
        await self.process.stdin.drain()
        return await self._receive("ready", 30)

    async def _receive(self, identity: str, timeout: float):
        raw = await asyncio.wait_for(self.process.stdout.readline(), timeout)
        if not raw or len(raw) > 1024 * 1024:
            raise RuntimeError("simulator pipe closed or response exceeds capacity")
        response = json.loads(raw)
        if response.get("id") != identity or response.get("ok") is not True:
            raise RuntimeError("simulator action failed or response identity differs")
        return response["result"]

    async def request(self, action: str, **parameters):
        message = DriverAction(id=uuid4().hex, action=action, **parameters)
        async with self.lock:
            self.process.stdin.write(message.model_dump_json().encode() + b"\n")
            await self.process.stdin.drain()
            return await self._receive(message.id, 15)

    async def close(self):
        if not self.process:
            return
        try:
            if self.process.returncode is None:
                self.process.stdin.close()  # EOF is an explicit graceful worker shutdown.
                try:
                    await asyncio.wait_for(self.process.wait(), 10)
                except TimeoutError:
                    self.process.kill()
                    await asyncio.wait_for(self.process.wait(), 5)
                    raise RuntimeError("simulator did not shut down gracefully")
            if self.process.returncode != 0:
                raise RuntimeError("simulator worker returned failure")
        finally:
            self.stderr.close()
            self.process = self.stderr = None
