"""命令路由 /api/commands（规格 3.3）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.deps import DbDep, UserDep, get_command_service
from app.api.schemas import CommandRequest, ConfirmIntentRequest, err, ok
from app.services.command_service import CO_COMMANDS, CommandError, confirm_tokens

router = APIRouter(prefix="/api/commands", tags=["commands"])


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
async def post_command(body: CommandRequest, request: Request, user: UserDep, db: DbDep):
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
        )
    except CommandError as exc:
        raise HTTPException(status_code=exc.status_code, detail=err(exc.error_code, exc.message))
    return ok(result)
