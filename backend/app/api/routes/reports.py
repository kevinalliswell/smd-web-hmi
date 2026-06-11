"""报告路由 /api/reports（规格 3.8）。"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import DbDep, UserDep, get_current_user, require_role
from app.api.schemas import err, ok
from app.db.models import ReportExport
from app.services import report_service

router = APIRouter(prefix="/api/reports", tags=["reports"])


class GenerateReportRequest(BaseModel):
    test_id: str
    format: str = "html"
    options: dict = {}


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


@router.post("/generate", dependencies=[Depends(require_role("operator"))])
async def generate_report(body: GenerateReportRequest, user: UserDep, db: DbDep):
    """生成报告。权限：Operator+。"""
    try:
        record = await report_service.generate_report(
            db, body.test_id, operator_id=user.username, fmt=body.format, options=body.options
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=err("test_not_found", str(exc)))
    return ok(
        {
            "id": record.id,
            "test_id": record.test_id,
            "generated_at": record.generated_at,
            "format": record.format,
            "file_size_bytes": record.file_size_bytes,
        }
    )


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
