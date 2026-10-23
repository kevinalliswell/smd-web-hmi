"""Short, independent transactions for task state; never store closures or credentials."""

import json
from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import BackgroundJob
from app.hostcomm.protocol import now_iso


def encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def public_job(row: BackgroundJob) -> dict[str, Any]:
    data = {
        key: getattr(row, key)
        for key in (
            "task_id",
            "kind",
            "operator_id",
            "status",
            "progress",
            "created_at",
            "started_at",
            "finished_at",
            "retry_of",
            "error_code",
            "message",
        )
    }
    if row.result_json is not None:
        data["result"] = json.loads(row.result_json)
    return data


async def interrupt_unfinished(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as db:
        await db.execute(
            update(BackgroundJob)
            .where(BackgroundJob.status.in_(["pending", "running"]))
            .values(
                status="interrupted",
                error_code="service_interrupted",
                message="服务中断；请核查后显式重试导出",
                finished_at=now_iso(),
            )
        )
        await db.commit()


async def transition(factory: async_sessionmaker[AsyncSession], task_id: str, **values: Any) -> None:
    async with factory() as db:
        result = await db.execute(
            update(BackgroundJob)
            .where(
                BackgroundJob.task_id == task_id,
                BackgroundJob.status.in_(["pending", "running"]),
            )
            .values(**values)
        )
        if result.rowcount != 1:
            raise RuntimeError("job is no longer active")
        await db.commit()
