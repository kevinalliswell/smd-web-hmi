"""进程内后台任务状态与并发边界测试。"""

from __future__ import annotations

import asyncio

import pytest

from app.services.background_jobs import BackgroundJobCapacityError, BackgroundJobManager


async def test_background_job_returns_immediately_and_completes():
    manager = BackgroundJobManager(max_concurrency=1)
    release = asyncio.Event()

    async def runner():
        await release.wait()
        return {"value": 42}

    task_id = manager.submit("report", runner)
    assert manager.snapshot(task_id)["status"] in {"pending", "running"}

    release.set()
    for _ in range(20):
        if manager.snapshot(task_id)["status"] == "completed":
            break
        await asyncio.sleep(0)

    assert manager.snapshot(task_id)["progress"] == 100
    assert manager.snapshot(task_id)["result"] == {"value": 42}
    await manager.shutdown()


async def test_background_job_exposes_sanitized_failure():
    manager = BackgroundJobManager(max_concurrency=1)

    async def runner():
        raise RuntimeError("secret database path")

    task_id = manager.submit("log_export", runner)
    for _ in range(20):
        if manager.snapshot(task_id)["status"] == "failed":
            break
        await asyncio.sleep(0)

    snapshot = manager.snapshot(task_id)
    assert snapshot["status"] == "failed"
    assert snapshot["message"] == "任务执行失败，请查看服务日志"
    assert "secret" not in snapshot["message"]
    await manager.shutdown()


async def test_background_job_registry_rejects_unbounded_pending_work():
    manager = BackgroundJobManager(max_concurrency=1, max_records=1)
    release = asyncio.Event()

    async def runner():
        await release.wait()
        return {}

    manager.submit("report", runner)
    with pytest.raises(BackgroundJobCapacityError):
        manager.submit("report", runner)

    release.set()
    await manager.shutdown()


async def test_background_job_has_runtime_timeout():
    manager = BackgroundJobManager(job_timeout_seconds=0.01)

    async def runner():
        await asyncio.Event().wait()

    task_id = manager.submit("report", runner)
    for _ in range(20):
        if manager.snapshot(task_id)["status"] == "failed":
            break
        await asyncio.sleep(0.005)

    assert manager.snapshot(task_id)["message"] == "任务执行超时"
    await manager.shutdown()


async def test_background_job_timeout_includes_queue_wait():
    manager = BackgroundJobManager(max_concurrency=1, job_timeout_seconds=0.01)
    queued_started = False

    async def queued_runner():
        nonlocal queued_started
        queued_started = True
        return {}

    await manager._semaphore.acquire()
    queued_id = manager.submit("report", queued_runner)
    for _ in range(20):
        if manager.snapshot(queued_id)["status"] == "failed":
            break
        await asyncio.sleep(0.005)

    assert manager.snapshot(queued_id)["message"] == "任务执行超时"
    assert queued_started is False
    manager._semaphore.release()
    await manager.shutdown()
