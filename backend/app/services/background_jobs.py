"""Bounded runners with durable admission/state for public export and recovery jobs."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger
from app.db.models import BackgroundJob
from app.hostcomm.protocol import now_iso
from app.services.job_store import encode, interrupt_unfinished, transition
from app.services.maintenance_service import MaintenanceManager, maintenance_manager

logger = get_logger("service.background_jobs")
JobRunner = Callable[[], Awaitable[dict[str, Any]]]


class BackgroundJobCapacityError(RuntimeError):
    """任务注册表已满且没有可清理的已完成记录。"""


@dataclass
class _JobRecord:
    task_id: str
    kind: str
    status: str = "pending"
    progress: int = 0
    result: dict[str, Any] | None = None
    message: str | None = None
    created_at: float = 0.0
    factory: async_sessionmaker[AsyncSession] | None = None
    error_code: str | None = None


class BackgroundJobManager:
    """限制并发并提供 pending/running/completed/failed 状态。"""

    def __init__(
        self,
        *,
        max_concurrency: int = 2,
        max_records: int = 200,
        job_timeout_seconds: float = 900.0,
        maintenance: MaintenanceManager = maintenance_manager,
    ) -> None:
        if max_concurrency < 1 or max_records < 1 or job_timeout_seconds <= 0:
            raise ValueError("background job limits must be positive")
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._max_records = max_records
        self._job_timeout_seconds = job_timeout_seconds
        self._records: dict[str, _JobRecord] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._maintenance = maintenance
        self._admissions = 0
        self.persistence_failures = 0

    async def submit_durable(
        self,
        kind: str,
        runner: JobRunner,
        *,
        operator_id: str,
        inputs: dict[str, Any],
        task_id: str | None = None,
        retry_of: str | None = None,
        factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> str:
        from app.db.database import get_sessionmaker

        factory = factory or get_sessionmaker()
        self._prune()
        if len(self._tasks) + self._admissions >= self._max_records:
            raise BackgroundJobCapacityError("background job queue is full")
        release = self._maintenance.reserve_business_mutation()
        self._admissions += 1
        task_id = task_id or uuid.uuid4().hex
        try:
            async with factory() as db:
                db.add(
                    BackgroundJob(
                        task_id=task_id,
                        kind=kind,
                        operator_id=operator_id,
                        input_json=encode(inputs),
                        status="pending",
                        progress=0,
                        retry_of=retry_of,
                        created_at=now_iso(),
                    )
                )
                await db.commit()
            record = _JobRecord(task_id=task_id, kind=kind, created_at=time.monotonic(), factory=factory)
            task = asyncio.create_task(self._run(record, runner), name=f"background-{kind}-{task_id}")
            self._prune()
            self._records[task_id] = record
            self._tasks[task_id] = task
            task.add_done_callback(lambda _task: release())
        except BaseException:
            release()
            raise
        finally:
            self._admissions -= 1
        return task_id

    async def _persist(self, record: _JobRecord, **values: Any) -> None:
        if record.factory is not None:
            try:
                await transition(record.factory, record.task_id, **values)
            except Exception:
                self.persistence_failures += 1
                raise

    def metrics(self) -> dict[str, int]:
        return {
            "active": len(self._tasks),
            "admitting": self._admissions,
            "queued": sum(r.status == "pending" for r in self._records.values()),
            "running": sum(r.status == "running" for r in self._records.values()),
            "capacity": self._max_records,
            "persistence_failures": self.persistence_failures,
        }

    def submit(self, kind: str, runner: JobRunner, *, task_id: str | None = None) -> str:
        task_id = task_id or uuid.uuid4().hex
        if task_id in self._records:
            raise ValueError("task_id already exists")
        self._prune()
        if len(self._records) >= self._max_records:
            raise BackgroundJobCapacityError("background job queue is full")
        release = self._maintenance.reserve_business_mutation()
        record = _JobRecord(task_id=task_id, kind=kind, created_at=time.monotonic())
        try:
            task = asyncio.create_task(self._run(record, runner), name=f"background-{kind}-{task_id}")
        except BaseException:
            release()
            raise
        self._records[task_id] = record
        self._tasks[task_id] = task
        # A coroutine cancelled before its first step never executes its finally.
        task.add_done_callback(lambda _task: release())
        return task_id

    async def _run(self, record: _JobRecord, runner: JobRunner) -> None:
        try:
            async with asyncio.timeout(self._job_timeout_seconds):
                async with self._semaphore:
                    await self._persist(record, status="running", progress=10, started_at=now_iso())
                    record.status = "running"
                    record.progress = 10
                    record.result = await runner()
                    await self._persist(
                        record,
                        status="completed",
                        progress=100,
                        result_json=encode(record.result),
                        finished_at=now_iso(),
                    )
                    record.status = "completed"
                    record.progress = 100
        except asyncio.CancelledError:
            record.status = "interrupted"
            record.message = "服务中断；请核查后显式重试导出"
            raise
        except TimeoutError:
            record.status = "failed"
            record.error_code = "job_timeout"
            record.message = "任务执行超时"
            logger.error("background_job.timeout", task_id=record.task_id, kind=record.kind)
        except Exception as exc:  # noqa: BLE001
            record.status = "failed"
            record.error_code = getattr(exc, "code", "job_failed")
            record.message = "任务执行失败，请查看服务日志"
            logger.exception("background_job.failed", task_id=record.task_id, kind=record.kind, error=str(exc))
        finally:
            if record.status in {"failed", "interrupted"}:
                try:
                    await self._persist(
                        record,
                        status=record.status,
                        message=record.message,
                        error_code=record.error_code or record.status,
                        finished_at=now_iso(),
                    )
                except Exception:
                    logger.exception("background_job.persist_failed", task_id=record.task_id)
            self._tasks.pop(record.task_id, None)

    def snapshot(self, task_id: str) -> dict[str, Any] | None:
        record = self._records.get(task_id)
        if record is None:
            return None
        data: dict[str, Any] = {
            "task_id": record.task_id,
            "kind": record.kind,
            "status": record.status,
            "progress": record.progress,
        }
        if record.result is not None:
            data["result"] = record.result
        if record.message is not None:
            data["message"] = record.message
        return data

    async def shutdown(self) -> None:
        factories = {r.factory for r in self._records.values() if r.factory is not None}
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        for factory in factories:
            await interrupt_unfinished(factory)

    def _prune(self) -> None:
        overflow = len(self._records) - self._max_records + 1
        if overflow <= 0:
            return
        finished = sorted(
            (record for record in self._records.values() if record.status in {"completed", "failed", "interrupted"}),
            key=lambda record: record.created_at,
        )
        for record in finished[:overflow]:
            self._records.pop(record.task_id, None)


background_jobs = BackgroundJobManager()
