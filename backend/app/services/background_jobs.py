"""有界的进程内后台任务注册表。

适用于单机工控部署的报告与日志导出。任务结果保存在内存中；已完成文件和报告
记录仍持久化在磁盘/数据库。多实例部署时应替换为外部任务队列。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.core.logging import get_logger

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


class BackgroundJobManager:
    """限制并发并提供 pending/running/completed/failed 状态。"""

    def __init__(
        self,
        *,
        max_concurrency: int = 2,
        max_records: int = 200,
        job_timeout_seconds: float = 900.0,
    ) -> None:
        if max_concurrency < 1 or max_records < 1 or job_timeout_seconds <= 0:
            raise ValueError("background job limits must be positive")
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._max_records = max_records
        self._job_timeout_seconds = job_timeout_seconds
        self._records: dict[str, _JobRecord] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def submit(self, kind: str, runner: JobRunner, *, task_id: str | None = None) -> str:
        task_id = task_id or uuid.uuid4().hex
        if task_id in self._records:
            raise ValueError("task_id already exists")
        self._prune()
        if len(self._records) >= self._max_records:
            raise BackgroundJobCapacityError("background job queue is full")
        record = _JobRecord(task_id=task_id, kind=kind, created_at=time.monotonic())
        self._records[task_id] = record
        self._tasks[task_id] = asyncio.create_task(self._run(record, runner), name=f"background-{kind}-{task_id}")
        return task_id

    async def _run(self, record: _JobRecord, runner: JobRunner) -> None:
        try:
            async with asyncio.timeout(self._job_timeout_seconds):
                async with self._semaphore:
                    record.status = "running"
                    record.progress = 10
                    record.result = await runner()
                    record.status = "completed"
                    record.progress = 100
        except asyncio.CancelledError:
            record.status = "failed"
            record.message = "任务已取消"
            raise
        except TimeoutError:
            record.status = "failed"
            record.message = "任务执行超时"
            logger.error("background_job.timeout", task_id=record.task_id, kind=record.kind)
        except Exception as exc:  # noqa: BLE001
            record.status = "failed"
            record.message = "任务执行失败，请查看服务日志"
            logger.exception("background_job.failed", task_id=record.task_id, kind=record.kind, error=str(exc))
        finally:
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
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    def _prune(self) -> None:
        overflow = len(self._records) - self._max_records + 1
        if overflow <= 0:
            return
        finished = sorted(
            (record for record in self._records.values() if record.status in {"completed", "failed"}),
            key=lambda record: record.created_at,
        )
        for record in finished[:overflow]:
            self._records.pop(record.task_id, None)


background_jobs = BackgroundJobManager()
