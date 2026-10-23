"""报告路由 /api/reports（规格 3.8）。"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.deps import DbDep, UserDep, get_current_user, require_role
from app.api.routes.jobs import visible_job
from app.api.schemas import err, ok
from app.api.validation import TaskIdPath, TestId
from app.db.models import BackgroundJob, ReportExport, TestSession
from app.services.artifact_integrity import verify
from app.services.background_jobs import BackgroundJobCapacityError, background_jobs
from app.services.export_jobs import submit_export
from app.services.job_store import public_job
from app.services.snapshot_data import json_object
from app.services.test_id import InvalidTestIdError, validate_test_id
from app.services.v2_run_recovery import V2RecoveryError, require_recovery_report_ready

router = APIRouter(prefix="/api/reports", tags=["reports"])


class GenerateReportRequest(BaseModel):
    test_id: TestId
    format: str = Field(default="html", pattern=r"^(html|pdf|xlsx)$")
    options: dict = Field(default_factory=dict, max_length=20)


@router.get("", dependencies=[Depends(get_current_user)])
async def list_reports(db: DbDep):
    """报告列表。权限：Observer+。"""
    rows = (await db.execute(select(ReportExport).order_by(ReportExport.id.desc()).limit(200))).scalars().all()
    return ok(
        [
            {
                "id": r.id,
                "test_id": r.test_id,
                "generated_at": r.generated_at,
                "operator_id": r.operator_id,
                "format": r.format,
                "file_size_bytes": r.file_size_bytes,
            }
            for r in rows
        ]
    )


@router.post(
    "/generate",
    dependencies=[Depends(require_role("operator"))],
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_report(body: GenerateReportRequest, user: UserDep, db: DbDep):
    """提交报告生成任务并立即返回。权限：Operator+。"""
    try:
        test_id = validate_test_id(body.test_id)
    except InvalidTestIdError as exc:
        raise HTTPException(status_code=422, detail=err("invalid_test_id", str(exc)))
    if await db.scalar(select(TestSession.id).where(TestSession.test_id == test_id)) is None:
        raise HTTPException(status_code=404, detail=err("test_not_found", f"试验不存在: {test_id}"))
    try:
        await require_recovery_report_ready(db, test_id)
    except V2RecoveryError as exc:
        raise HTTPException(status_code=exc.status_code, detail=err(exc.code, exc.message)) from exc

    # Release the request read transaction before durable admission on its own connection.
    await db.rollback()
    try:
        task_id = await submit_export(
            "report",
            body.model_dump(),
            user.username,
            manager=background_jobs,
            factory=async_sessionmaker(db.bind, expire_on_commit=False),
        )
    except BackgroundJobCapacityError as exc:
        raise HTTPException(status_code=503, detail=err("job_queue_full", "后台任务队列已满，请稍后重试")) from exc
    return ok({"task_id": task_id, "status": "pending", "progress": 0})


@router.get("/tasks/{task_id}", dependencies=[Depends(require_role("operator"))])
async def report_task_status(task_id: TaskIdPath, db: DbDep, user: UserDep):
    """查询报告生成任务状态。"""
    return ok(public_job(await visible_job(db, task_id, user, kind="report")))


@router.get("/{report_id}/download", dependencies=[Depends(get_current_user)])
async def download_report(report_id: int, db: DbDep):
    """下载报告文件。权限：Observer+。"""
    record = await db.get(ReportExport, report_id)
    if record is None or not Path(record.file_path).exists():
        raise HTTPException(status_code=404, detail=err("not_found", "报告文件不存在"))
    notes, _ = json_object(record.notes)
    if notes.get("task_id"):
        job = await db.get(BackgroundJob, notes["task_id"])
        if job is None or job.status != "completed":
            raise HTTPException(409, detail=err("job_not_completed", "任务尚未归档"))
    if notes.get("artifact_sha256"):
        try:
            await verify(Path(record.file_path), notes["artifact_sha256"])
        except (OSError, ValueError) as exc:
            raise HTTPException(409, detail=err("artifact_digest_mismatch", "归档文件核验失败，请重新生成")) from exc
    return FileResponse(
        record.file_path,
        media_type={
            "html": "text/html; charset=utf-8",
            "pdf": "application/pdf",
            "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        }.get(record.format, "application/octet-stream"),
        filename=Path(record.file_path).name,
    )
