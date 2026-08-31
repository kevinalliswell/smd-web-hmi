"""认证路由 /api/auth（规格 3.1）。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.api.deps import DbDep, UserDep
from app.api.schemas import LoginRequest, err, ok
from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.security import create_access_token
from app.hostcomm.protocol import now_iso
from app.services.auth_service import LoginRejected, login_protector, login_rate_limiter

router = APIRouter(prefix="/api/auth", tags=["auth"])
logger = get_logger("auth.login")


@router.post("/login")
async def login(body: LoginRequest, request: Request, db: DbDep):
    """登录，返回 JWT。权限：无（公开）。"""
    settings = get_settings()
    client_ip = request.client.host if request.client else "unknown"
    if not login_rate_limiter.allow(
        client_ip,
        limit=settings.smd_login_rate_limit,
        window_seconds=settings.smd_login_rate_window_seconds,
    ):
        logger.warning(
            "auth.login_rejected",
            username=body.username,
            client_ip=client_ip,
            reason="rate_limited",
        )
        raise HTTPException(
            status_code=429,
            detail=err("login_rate_limited", "登录尝试过于频繁，请稍后再试"),
            headers={"Retry-After": str(settings.smd_login_rate_window_seconds)},
        )

    try:
        user = await login_protector.authenticate(
            db,
            username=body.username,
            password=body.password,
            client_ip=client_ip,
            max_failures=settings.smd_login_max_failures,
            lock_minutes=settings.smd_login_lock_minutes,
        )
    except LoginRejected as exc:
        logger.warning(
            "auth.login_rejected",
            username=body.username,
            client_ip=client_ip,
            reason=exc.reason,
        )
        raise HTTPException(status_code=401, detail=err("invalid_credentials", "用户名或密码错误"))

    must_change_password = bool(user.must_change_password)
    token, expires_at = create_access_token(
        user.username,
        user.role,
        must_change_password=must_change_password,
    )
    user.last_login = now_iso()
    await db.commit()
    return ok(
        {
            "token": token,
            "role": user.role,
            "display_name": user.display_name,
            "must_change_password": must_change_password,
            "expires_at": expires_at.isoformat(),
        }
    )


@router.post("/logout")
async def logout(user: UserDep):
    """登出（本地短期 token，前端丢弃即可）。权限：登录用户。"""
    return ok({"message": "已登出"})


@router.get("/me")
async def me(user: UserDep):
    """返回当前用户信息。权限：登录用户。"""
    return ok(
        {
            "username": user.username,
            "role": user.role,
            "must_change_password": user.must_change_password,
        }
    )
