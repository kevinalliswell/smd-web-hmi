"""日志导出路由 /api/logs（规格 3.7）。

上位机侧导出已归档试验数据为 CSV zip（后台流式生成，task_id 即文件名）。
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.deps import DbDep, UserDep, require_role
from app.api.routes.jobs import visible_job
from app.api.schemas import err, ok
from app.api.validation import LogType, TaskIdPath, TestId
from app.db.models import BackgroundJob
from app.services import log_export_service
from app.services.artifact_integrity import verify
from app.services.background_jobs import BackgroundJobCapacityError, background_jobs
from app.services.export_jobs import submit_export
from app.services.job_store import public_job
from app.services.test_id import InvalidTestIdError, validate_test_id

router = APIRouter(prefix="/api/logs", tags=["logs"], dependencies=[Depends(require_role("operator"))])


class ExportLogRequest(BaseModel):
    test_id: TestId
    log_types: list[LogType] | None = Field(default=None, max_length=4)


@router.post("/export", status_code=status.HTTP_202_ACCEPTED)
async def export_log(body: ExportLogRequest, user: UserDep, db: DbDep):
    """提交日志导出任务并立即返回。权限：Operator+。"""
    try:
        test_id = validate_test_id(body.test_id)
    except InvalidTestIdError as exc:
        raise HTTPException(status_code=422, detail=err("invalid_test_id", str(exc)))

    await db.rollback()
    try:
        task_id = await submit_export(
            "log_export",
            body.model_dump(),
            user.username,
            manager=background_jobs,
            factory=async_sessionmaker(db.bind, expire_on_commit=False),
        )
    except BackgroundJobCapacityError as exc:
        raise HTTPException(status_code=503, detail=err("job_queue_full", "后台任务队列已满，请稍后重试")) from exc
    return ok({"task_id": task_id, "status": "pending", "progress": 0})


@router.get("/export/{task_id}")
async def export_status(task_id: TaskIdPath, db: DbDep, user: UserDep):
    """查询导出任务状态。权限：Operator+。"""
    if await db.get(BackgroundJob, task_id) is None and user.has_role("admin"):
        path = log_export_service.export_path(task_id)
        if path.is_file():
            return ok(
                {
                    "task_id": task_id,
                    "status": "completed",
                    "progress": 100,
                    "result": {"download_url": f"/api/logs/export/{task_id}/download", "legacy_unverified": True},
                }
            )
    return ok(public_job(await visible_job(db, task_id, user, kind="log_export")))


@router.get("/export/{task_id}/download")
async def export_download(task_id: TaskIdPath, db: DbDep, user: UserDep):
    """下载导出文件。权限：Operator+。"""
    row = await db.get(BackgroundJob, task_id)
    legacy = row is None and user.has_role("admin")
    if not legacy:
        row = await visible_job(db, task_id, user, kind="log_export")
    if not legacy and row.status != "completed":
        raise HTTPException(409, detail=err("job_not_completed", "任务尚未归档"))
    try:
        path = log_export_service.export_path(task_id)
    except ValueError:
        path = None
    if path is None or not path.exists():
        raise HTTPException(status_code=404, detail=err("not_found", "导出文件不存在"))
    if not legacy:
        try:
            await verify(path, json.loads(row.result_json or "{}").get("sha256"))
        except (OSError, ValueError) as exc:
            raise HTTPException(409, detail=err("artifact_digest_mismatch", "归档文件核验失败，请重新导出")) from exc
    return FileResponse(path, media_type="application/zip", filename=f"{task_id}.zip")
