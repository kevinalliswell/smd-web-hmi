"""Durable job lookup: operators see their own jobs; admin/maintainer see all."""

import json
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import CurrentUser, DbDep, UserDep, require_role
from app.api.schemas import err, ok
from app.api.validation import TaskIdPath
from app.db.models import BackgroundJob
from app.services.background_jobs import BackgroundJobCapacityError
from app.services.export_jobs import submit_export
from app.services.job_store import public_job

router = APIRouter(prefix="/api/jobs", tags=["jobs"], dependencies=[Depends(require_role("operator"))])


class JobView(BaseModel):
    task_id: str
    kind: str
    operator_id: str
    status: Literal["pending", "running", "completed", "failed", "interrupted"]
    progress: int
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    retry_of: str | None = None
    error_code: str | None = None
    message: str | None = None
    result: dict[str, Any] | None = None


class JobResponse(BaseModel):
    data: JobView
    ts: str


class JobPage(BaseModel):
    items: list[JobView]
    total: int
    page: int
    page_size: int


class JobPageResponse(BaseModel):
    data: JobPage
    ts: str


class JobAdmission(BaseModel):
    task_id: str
    retry_of: str
    status: Literal["pending"]
    progress: int


class JobAdmissionResponse(BaseModel):
    data: JobAdmission
    ts: str


async def visible_job(db: AsyncSession, task_id: str, user: CurrentUser, *, kind: str | None = None) -> BackgroundJob:
    row = await db.get(BackgroundJob, task_id)
    if row is None or (kind and row.kind != kind) or (not user.has_role("admin") and row.operator_id != user.username):
        raise HTTPException(404, detail=err("not_found", "任务不存在"))
    if row.kind == "v2_source_recovery" and not user.has_role("admin"):
        raise HTTPException(404, detail=err("not_found", "任务不存在"))
    return row


@router.get("", response_model=JobPageResponse)
async def list_jobs(db: DbDep, user: UserDep, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    condition = (
        True
        if user.has_role("admin")
        else ((BackgroundJob.operator_id == user.username) & (BackgroundJob.kind != "v2_source_recovery"))
    )
    total = await db.scalar(select(func.count()).select_from(BackgroundJob).where(condition))
    rows = await db.scalars(
        select(BackgroundJob)
        .where(condition)
        .order_by(BackgroundJob.created_at.desc(), BackgroundJob.task_id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return ok({"items": [public_job(row) for row in rows], "total": total, "page": page, "page_size": page_size})


@router.get("/{task_id}", response_model=JobResponse)
async def job_status(task_id: TaskIdPath, db: DbDep, user: UserDep):
    return ok(public_job(await visible_job(db, task_id, user)))


@router.post("/{task_id}/retry", status_code=202, response_model=JobAdmissionResponse)
async def retry_job(task_id: TaskIdPath, db: DbDep, user: UserDep):
    row = await visible_job(db, task_id, user)
    if row.kind not in {"report", "log_export"} or row.status not in {"failed", "interrupted"}:
        raise HTTPException(409, detail=err("job_not_retryable", "仅失败或中断的报告、日志导出可显式重试"))
    kind, inputs = row.kind, json.loads(row.input_json)
    await db.rollback()
    try:
        new_id = await submit_export(
            kind, inputs, user.username, retry_of=task_id, factory=async_sessionmaker(db.bind, expire_on_commit=False)
        )
    except BackgroundJobCapacityError as exc:
        raise HTTPException(503, detail=err("job_queue_full", "后台任务队列已满")) from exc
    return ok({"task_id": new_id, "retry_of": task_id, "status": "pending", "progress": 0})
