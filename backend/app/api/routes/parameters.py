"""参数路由 /api/parameters（规格 3.6）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import DbDep, UserDep, get_current_user, get_parameter_service, require_role
from app.api.operation_api import OPERATION_ERRORS, operation_http_error, request_operation_id
from app.api.schemas import err, ok
from app.api.validation import Page, PageSize
from app.db.models import ParameterSnapshot
from app.services.command_service import CommandError

router = APIRouter(prefix="/api/parameters", tags=["parameters"])


class SetParametersRequest(BaseModel):
    values: dict
    param_crc: str | None = None
    operation_id: str | None = Field(default=None, min_length=1, max_length=128)


@router.get("", dependencies=[Depends(get_current_user)])
async def get_parameters(request: Request):
    """读取当前参数快照（发 get_parameters）。权限：Observer+。"""
    service = get_parameter_service(request)
    try:
        payload = await service.get_parameters()
    except CommandError as exc:
        # 离线时返回空而非报错，便于前端只读展示
        if exc.error_code == "device_comm_fault":
            return ok({"params": None, "comm": "offline"})
        raise HTTPException(status_code=exc.status_code, detail=err(exc.error_code, exc.message))
    return ok(payload)


@router.put("", dependencies=[Depends(require_role("admin"))])
async def put_parameters(
    body: SetParametersRequest,
    request: Request,
    user: UserDep,
    db: DbDep,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """下发参数：校验 CRC → 下发 → 回读确认 → 写快照。权限：Admin。"""
    service = get_parameter_service(request)
    client_ip = request.client.host if request.client else None
    try:
        result = await service.set_parameters(
            body.values,
            body.param_crc,
            operator_id=user.username,
            role=user.role,
            client_ip=client_ip,
            db_session=db,
            operation_id=request_operation_id(body.operation_id, idempotency_key),
        )
    except OPERATION_ERRORS as exc:
        raise operation_http_error(exc) from exc
    return ok(result)


@router.get("/history", dependencies=[Depends(require_role("admin"))])
async def parameters_history(db: DbDep, page: Page = 1, size: PageSize = 20):
    """历史参数快照列表。权限：Admin。"""
    offset = (page - 1) * size
    result = await db.execute(
        select(ParameterSnapshot).order_by(ParameterSnapshot.id.desc()).limit(size).offset(offset)
    )
    rows = result.scalars().all()
    return ok(
        [
            {
                "id": r.id,
                "ts": r.ts,
                "operator_id": r.operator_id,
                "source": r.source,
                "fw_version": r.fw_version,
                "param_crc": r.param_crc,
            }
            for r in rows
        ]
    )
