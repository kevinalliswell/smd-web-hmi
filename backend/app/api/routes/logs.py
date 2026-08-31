"""日志导出路由 /api/logs（规格 3.7）。

上位机侧导出已归档试验数据为 CSV zip（后台流式生成，task_id 即文件名）。
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.api.deps import require_role
from app.api.schemas import err, ok
from app.api.validation import LogType, TaskIdPath, TestId
from app.db.database import get_sessionmaker
from app.services import log_export_service
from app.services.background_jobs import BackgroundJobCapacityError, background_jobs
from app.services.test_id import InvalidTestIdError, validate_test_id

router = APIRouter(prefix="/api/logs", tags=["logs"], dependencies=[Depends(require_role("operator"))])


class ExportLogRequest(BaseModel):
    test_id: TestId
    log_types: list[LogType] | None = Field(default=None, max_length=4)


@router.post("/export", status_code=status.HTTP_202_ACCEPTED)
async def export_log(body: ExportLogRequest):
    """提交日志导出任务并立即返回。权限：Operator+。"""
    try:
        test_id = validate_test_id(body.test_id)
    except InvalidTestIdError as exc:
        raise HTTPException(status_code=422, detail=err("invalid_test_id", str(exc)))

    task_id = uuid.uuid4().hex

    async def run_export():
        sessionmaker = get_sessionmaker()
        async with sessionmaker() as session:
            result = await log_export_service.export_test_logs(
                session,
                test_id,
                log_types=body.log_types,
                task_id=task_id,
            )
        return {
            "task_id": task_id,
            "entries": result["entries"],
            "size": result["size"],
            "download_url": f"/api/logs/export/{task_id}/download",
        }

    try:
        background_jobs.submit("log_export", run_export, task_id=task_id)
    except BackgroundJobCapacityError as exc:
        raise HTTPException(status_code=503, detail=err("job_queue_full", "后台任务队列已满，请稍后重试")) from exc
    return ok({"task_id": task_id, "status": "pending", "progress": 0})


@router.get("/export/{task_id}")
async def export_status(task_id: TaskIdPath):
    """查询导出任务状态。权限：Operator+。"""
    snapshot = background_jobs.snapshot(task_id)
    if snapshot is not None and snapshot["kind"] == "log_export":
        return ok(snapshot)
    try:
        path = log_export_service.export_path(task_id)
    except ValueError:
        path = None
    if path is None or not path.exists():
        raise HTTPException(status_code=404, detail=err("not_found", "导出任务不存在"))
    return ok(
        {
            "task_id": task_id,
            "kind": "log_export",
            "status": "completed",
            "progress": 100,
            "result": {
                "task_id": task_id,
                "size": path.stat().st_size,
                "download_url": f"/api/logs/export/{task_id}/download",
            },
        }
    )


@router.get("/export/{task_id}/download")
async def export_download(task_id: TaskIdPath):
    """下载导出文件。权限：Operator+。"""
    try:
        path = log_export_service.export_path(task_id)
    except ValueError:
        path = None
    if path is None or not path.exists():
        raise HTTPException(status_code=404, detail=err("not_found", "导出文件不存在"))
    return FileResponse(path, media_type="application/zip", filename=f"{task_id}.zip")
