"""报告路由 /api/reports（规格 3.8）。"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import DbDep, UserDep, get_current_user, require_role
from app.api.schemas import err, ok
from app.api.validation import TaskIdPath, TestId
from app.db.database import get_sessionmaker
from app.db.models import ReportExport, TestSession
from app.services import report_service
from app.services.background_jobs import BackgroundJobCapacityError, background_jobs
from app.services.test_id import InvalidTestIdError, validate_test_id

router = APIRouter(prefix="/api/reports", tags=["reports"])


class GenerateReportRequest(BaseModel):
    test_id: TestId
    format: str = Field(default="html", pattern=r"^html$")
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

    async def run_report():
        sessionmaker = get_sessionmaker()
        async with sessionmaker() as session:
            record = await report_service.generate_report(
                session,
                test_id,
                operator_id=user.username,
                fmt=body.format,
                options=body.options,
            )
        return {
            "id": record.id,
            "test_id": record.test_id,
            "generated_at": record.generated_at,
            "format": record.format,
            "file_size_bytes": record.file_size_bytes,
            "download_url": f"/api/reports/{record.id}/download",
        }

    try:
        task_id = background_jobs.submit("report", run_report)
    except BackgroundJobCapacityError as exc:
        raise HTTPException(status_code=503, detail=err("job_queue_full", "后台任务队列已满，请稍后重试")) from exc
    return ok({"task_id": task_id, "status": "pending", "progress": 0})


@router.get("/tasks/{task_id}", dependencies=[Depends(require_role("operator"))])
async def report_task_status(task_id: TaskIdPath):
    """查询报告生成任务状态。"""
    snapshot = background_jobs.snapshot(task_id)
    if snapshot is None or snapshot["kind"] != "report":
        raise HTTPException(status_code=404, detail=err("not_found", "报告任务不存在"))
    return ok(snapshot)


@router.get("/{report_id}/download", dependencies=[Depends(get_current_user)])
async def download_report(report_id: int, db: DbDep):
    """下载报告文件。权限：Observer+。"""
    record = await db.get(ReportExport, report_id)
    if record is None or not Path(record.file_path).exists():
        raise HTTPException(status_code=404, detail=err("not_found", "报告文件不存在"))
    return FileResponse(
        record.file_path,
        media_type="text/html",
        filename=Path(record.file_path).name,
    )
