"""用户管理路由 /api/users（规格 3.9）。Admin only。D3 骨架。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import DbDep, UserDep, require_role
from app.api.schemas import err, ok
from app.core.security import hash_password
from app.db.models import UserAccount
from app.hostcomm.protocol import now_iso

router = APIRouter(prefix="/api/users", tags=["users"])


class CreateUserRequest(BaseModel):
    username: str
    password: str
    role: str
    display_name: str | None = None


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


@router.get("", dependencies=[Depends(require_role("admin"))])
async def list_users(db: DbDep):
    """用户列表。权限：Admin。"""
    result = await db.execute(select(UserAccount).order_by(UserAccount.id))
    rows = result.scalars().all()
    return ok(
        [
            {"id": u.id, "username": u.username, "role": u.role, "display_name": u.display_name, "is_active": bool(u.is_active)}
            for u in rows
        ]
    )


@router.post("", dependencies=[Depends(require_role("admin"))])
async def create_user(body: CreateUserRequest, db: DbDep):
    """创建用户。权限：Admin。"""
    if body.role not in {"observer", "operator", "admin", "maintainer"}:
        raise HTTPException(status_code=400, detail=err("invalid_role", "非法角色"))
    db.add(
        UserAccount(
            username=body.username,
            hashed_pw=hash_password(body.password),
            role=body.role,
            display_name=body.display_name,
            is_active=1,
            created_at=now_iso(),
        )
    )
    await db.commit()
    return ok({"username": body.username, "role": body.role})


@router.post("/change-password")
async def change_password(body: ChangePasswordRequest, user: UserDep, db: DbDep):
    """修改自己的密码。权限：登录用户。"""
    from app.core.security import verify_password

    result = await db.execute(select(UserAccount).where(UserAccount.username == user.username))
    account = result.scalar_one_or_none()
    if account is None or not verify_password(body.old_password, account.hashed_pw):
        raise HTTPException(status_code=400, detail=err("invalid_credentials", "原密码错误"))
    account.hashed_pw = hash_password(body.new_password)
    await db.commit()
    return ok({"message": "密码已更新"})
