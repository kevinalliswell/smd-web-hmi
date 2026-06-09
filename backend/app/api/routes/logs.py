"""日志导出路由 /api/logs（规格 3.7）。D3 骨架占位。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import require_role
from app.api.schemas import ok

router = APIRouter(prefix="/api/logs", tags=["logs"], dependencies=[Depends(require_role("operator"))])


@router.post("/export")
async def export_log():
    """触发从 STM32 导出日志（异步分块）。权限：Operator+。D3 骨架占位。"""
    return ok({"task_id": None, "status": "not_implemented"})
