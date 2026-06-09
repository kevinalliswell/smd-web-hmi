"""认证路由 /api/auth（规格 3.1）。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.api.deps import DbDep, UserDep
from app.api.schemas import LoginRequest, err, ok
from app.core.security import create_access_token, verify_password
from app.db.models import UserAccount
from app.hostcomm.protocol import now_iso

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login")
async def login(body: LoginRequest, db: DbDep):
    """登录，返回 JWT。权限：无（公开）。"""
    result = await db.execute(select(UserAccount).where(UserAccount.username == body.username))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active or not verify_password(body.password, user.hashed_pw):
        raise HTTPException(status_code=401, detail=err("invalid_credentials", "用户名或密码错误"))

    token, expires_at = create_access_token(user.username, user.role)
    user.last_login = now_iso()
    await db.commit()
    return ok(
        {
            "token": token,
            "role": user.role,
            "display_name": user.display_name,
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
    return ok({"username": user.username, "role": user.role})
