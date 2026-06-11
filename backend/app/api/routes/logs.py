"""日志导出路由 /api/logs（规格 3.7）。

上位机侧导出已归档试验数据为 CSV zip（同步生成，task_id 即文件名）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.api.deps import DbDep, require_role
from app.api.schemas import err, ok
from app.services import log_export_service

router = APIRouter(prefix="/api/logs", tags=["logs"], dependencies=[Depends(require_role("operator"))])


class ExportLogRequest(BaseModel):
    test_id: str
    log_types: list[str] | None = None


@router.post("/export")
async def export_log(body: ExportLogRequest, db: DbDep):
    """导出指定试验的日志为 zip。权限：Operator+。"""
    result = await log_export_service.export_test_logs(db, body.test_id, log_types=body.log_types)
    return ok(
        {
            "task_id": result["task_id"],
            "status": "completed",
            "entries": result["entries"],
            "size": result["size"],
            "download_url": f"/api/logs/export/{result['task_id']}/download",
        }
    )


@router.get("/export/{task_id}")
async def export_status(task_id: str):
    """查询导出任务状态。权限：Operator+。"""
    path = log_export_service.export_path(task_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail=err("not_found", "导出任务不存在"))
    return ok({"task_id": task_id, "status": "completed", "size": path.stat().st_size})


@router.get("/export/{task_id}/download")
async def export_download(task_id: str):
    """下载导出文件。权限：Operator+。"""
    path = log_export_service.export_path(task_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail=err("not_found", "导出文件不存在"))
    return FileResponse(path, media_type="application/zip", filename=f"{task_id}.zip")
