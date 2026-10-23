"""Bounded, process-local timing observations; missing observations stay unknown."""

import asyncio
import time
from contextlib import suppress


class RuntimeMetrics:
    def __init__(self) -> None:
        self.task: asyncio.Task[None] | None = None
        self.loop_last_ms: float | None = None
        self.loop_max_ms: float | None = None
        self.persistence_last_ms: float | None = None
        self.persistence_max_ms: float | None = None
        self.persisted_at: float | None = None

    async def start(self) -> None:
        self.loop_last_ms = self.loop_max_ms = None
        self.task = asyncio.create_task(self._probe(), name="event-loop-lag")

    async def _probe(self) -> None:
        while True:
            deadline = time.monotonic() + 0.1
            await asyncio.sleep(0.1)
            self.loop_last_ms = max(0.0, (time.monotonic() - deadline) * 1000)
            self.loop_max_ms = max(self.loop_max_ms or 0, self.loop_last_ms)

    async def close(self) -> None:
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
            self.task = None

    def persisted(self, started: float) -> None:
        self.persistence_last_ms = (time.monotonic() - started) * 1000
        self.persistence_max_ms = max(self.persistence_max_ms or 0, self.persistence_last_ms)
        self.persisted_at = time.monotonic()

    def snapshot(self) -> dict[str, float | None]:
        return {
            "loop_last_ms": self.loop_last_ms,
            "loop_max_ms": self.loop_max_ms,
            "persistence_last_ms": self.persistence_last_ms,
            "persistence_max_ms": self.persistence_max_ms,
            "persistence_age_seconds": time.monotonic() - self.persisted_at if self.persisted_at else None,
        }


runtime_metrics = RuntimeMetrics()
