"""通用 API 依赖：当前用户、角色校验、HostComm 客户端、命令服务。"""

from __future__ import annotations

from typing import Annotated

import jwt
from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_access_token
from app.db.database import get_db
from app.db.models import UserAccount
from app.services.cache import status_cache
from app.services.command_service import CommandService

# 角色等级（用于 require_role 的 ">=" 比较）
ROLE_LEVEL = {"observer": 0, "operator": 1, "admin": 2, "maintainer": 3}


_PASSWORD_CHANGE_ALLOWED_PATHS = {
    "/api/auth/logout",
    "/api/auth/me",
    "/api/users/change-password",
}


class CurrentUser:
    """当前登录用户（从 JWT 解析）。"""

    def __init__(self, username: str, role: str, *, must_change_password: bool = False) -> None:
        self.username = username
        self.role = role
        self.must_change_password = must_change_password

    def has_role(self, minimum: str) -> bool:
        return ROLE_LEVEL.get(self.role, -1) >= ROLE_LEVEL.get(minimum, 99)


async def get_current_user(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    authorization: Annotated[str | None, Header()] = None,
) -> CurrentUser:
    """解析 token，并与数据库中的当前账户状态对账。"""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail={"error_code": "no_token", "message": "缺少认证令牌"})
    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = decode_access_token(token)
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail={"error_code": "invalid_token", "message": "令牌无效或已过期"})
    username = payload.get("sub")
    role = payload.get("role")
    account = None
    if isinstance(username, str) and username and role in ROLE_LEVEL:
        account = await db.scalar(select(UserAccount).where(UserAccount.username == username))
    if (
        account is None
        or not account.is_active
        or account.role != role
        or account.token_version != payload.get("ver", 0)
    ):
        raise HTTPException(status_code=401, detail={"error_code": "invalid_token", "message": "令牌已失效"})
    user = CurrentUser(
        username=account.username,
        role=account.role,
        must_change_password=bool(account.must_change_password),
    )
    if user.must_change_password and request.url.path not in _PASSWORD_CHANGE_ALLOWED_PATHS:
        raise HTTPException(
            status_code=403,
            detail={"error_code": "password_change_required", "message": "必须先修改初始密码"},
        )
    return user


def require_role(minimum: str):
    """生成一个要求最低角色的依赖。"""

    async def _checker(user: Annotated[CurrentUser, Depends(get_current_user)]) -> CurrentUser:
        if not user.has_role(minimum):
            raise HTTPException(
                status_code=403,
                detail={"error_code": "operator_permission_denied", "message": f"需要 {minimum} 及以上角色"},
            )
        return user

    return _checker


def get_hostcomm_client(request: Request):
    """返回应用启动时创建的 HostComm 客户端单例（可能为 None）。"""
    return getattr(request.app.state, "hostcomm_client", None)


def get_command_service(request: Request) -> CommandService:
    """构造命令服务（绑定当前 HostComm 客户端与状态缓存）。"""
    return CommandService(get_hostcomm_client(request), status_cache)


def get_parameter_service(request: Request):
    """构造参数下发服务（绑定当前 HostComm 客户端与状态缓存）。"""
    from app.services.parameter_service import ParameterService

    return ParameterService(get_hostcomm_client(request), status_cache)


# 类型别名
DbDep = Annotated[AsyncSession, Depends(get_db)]
UserDep = Annotated[CurrentUser, Depends(get_current_user)]
