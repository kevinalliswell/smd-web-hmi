"""命令路由 /api/commands（规格 3.3）。"""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import Field
from sqlalchemy import select

from app.api.deps import DbDep, UserDep, get_command_service
from app.api.operation_api import OPERATION_ERRORS, operation_http_error, request_operation_id
from app.api.schemas import CommandRequest, ConfirmIntentRequest, err, ok
from app.api.validation import Page, PageSize
from app.db.operation_models import Operation
from app.services.command_service import CO_COMMANDS, confirm_tokens
from app.services.operations import get_operation, operation_payload

router = APIRouter(prefix="/api/commands", tags=["commands"])


class OperationCommandRequest(CommandRequest):
    operation_id: str | None = Field(default=None, min_length=1, max_length=128)


@router.get("/operations")
async def list_operations(user: UserDep, db: DbDep, page: Page = 1, size: PageSize = 20):
    """查询本人操作；Admin可查全部，不访问设备。"""
    query = select(Operation)
    if not user.has_role("admin"):
        query = query.where(Operation.operator_id == user.username)
    rows = (
        (
            await db.execute(
                query.order_by(Operation.created_at.desc(), Operation.operation_id)
                .offset((page - 1) * size)
                .limit(size)
            )
        )
        .scalars()
        .all()
    )
    return ok([operation_payload(row) for row in rows])


@router.get("/operations/{operation_id}")
async def operation_status(operation_id: str, user: UserDep, db: DbDep):
    """读取持久操作结果；本人或Admin，不会重发命令。"""
    row = await get_operation(db, operation_id)
    if row is None or (row.operator_id != user.username and not user.has_role("admin")):
        raise HTTPException(status_code=404, detail=err("not_found", "操作记录不存在"))
    data = operation_payload(row)
    data.update({"command": row.command, "created_at": row.created_at, "updated_at": row.updated_at})
    return ok(data)


@router.post("/confirm-intent")
async def confirm_intent(body: ConfirmIntentRequest, user: UserDep):
    """为 CO 相关命令签发一次性二次确认令牌（60s 有效）。权限：Operator+。

    安全：仅签发令牌，真正的安全裁决仍在 STM32 与硬接线联锁。
    """
    if user.role == "observer":
        raise HTTPException(status_code=403, detail=err("operator_permission_denied", "无操作权限"))
    if body.command not in CO_COMMANDS:
        # 非 CO 命令无需令牌，返回空令牌即可
        return ok({"confirm_token": None, "required": False})
    token = confirm_tokens.issue()
    return ok({"confirm_token": token, "required": True, "ttl_seconds": 60})


@router.post("")
@router.post("/")
async def post_command(
    body: OperationCommandRequest,
    request: Request,
    user: UserDep,
    db: DbDep,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """下发命令到 STM32（经权限/状态/确认/CRC 校验）。权限：Operator+。"""
    service = get_command_service(request)
    client_ip = request.client.host if request.client else None
    try:
        result = await service.execute(
            body.command,
            body.params,
            operator_id=user.username,
            role=user.role,
            confirm_token=body.confirm_token,
            client_ip=client_ip,
            db_session=db,
            operation_id=request_operation_id(body.operation_id, idempotency_key),
        )
    except OPERATION_ERRORS as exc:
        raise operation_http_error(exc) from exc
    return ok(result)
