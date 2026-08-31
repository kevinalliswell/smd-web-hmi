"""用户管理路由 /api/users（规格 3.9）。Admin only。D3 骨架。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import DbDep, UserDep, require_role
from app.api.schemas import err, ok
from app.core.security import hash_password
from app.db.models import OperatorAction, UserAccount
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


class UpdateUserRequest(BaseModel):
    role: str | None = None
    is_active: bool | None = None
    display_name: str | None = None
    new_password: str | None = None


@router.get("", dependencies=[Depends(require_role("admin"))])
async def list_users(db: DbDep):
    """用户列表。权限：Admin。"""
    result = await db.execute(select(UserAccount).order_by(UserAccount.id))
    rows = result.scalars().all()
    return ok(
        [
            {
                "id": u.id,
                "username": u.username,
                "role": u.role,
                "display_name": u.display_name,
                "is_active": bool(u.is_active),
            }
            for u in rows
        ]
    )


@router.post("", dependencies=[Depends(require_role("admin"))])
async def create_user(body: CreateUserRequest, db: DbDep):
    """创建用户。权限：Admin。"""
    if body.role not in {"observer", "operator", "admin", "maintainer"}:
        raise HTTPException(status_code=400, detail=err("invalid_role", "非法角色"))
    exists = await db.scalar(select(UserAccount).where(UserAccount.username == body.username))
    if exists is not None:
        raise HTTPException(status_code=400, detail=err("username_exists", "用户名已存在"))
    db.add(
        UserAccount(
            username=body.username,
            hashed_pw=hash_password(body.password),
            role=body.role,
            display_name=body.display_name,
            is_active=1,
            must_change_password=1,
            created_at=now_iso(),
        )
    )
    await db.commit()
    return ok({"username": body.username, "role": body.role})


@router.put("/{user_id}", dependencies=[Depends(require_role("admin"))])
async def update_user(user_id: int, body: UpdateUserRequest, user: UserDep, db: DbDep):
    """修改角色 / 激活状态 / 显示名 / 重置密码。权限：Admin。"""
    account = await db.get(UserAccount, user_id)
    if account is None:
        raise HTTPException(status_code=404, detail=err("not_found", "用户不存在"))
    # 防自锁：不允许管理员停用或降级自己的账户
    if account.username == user.username and (
        body.is_active is False or (body.role is not None and body.role != "admin")
    ):
        raise HTTPException(status_code=400, detail=err("self_lockout", "不能停用或降级当前登录的管理员账户"))
    if body.role is not None:
        if body.role not in {"observer", "operator", "admin", "maintainer"}:
            raise HTTPException(status_code=400, detail=err("invalid_role", "非法角色"))
        account.role = body.role
    if body.is_active is not None:
        account.is_active = int(body.is_active)
    if body.display_name is not None:
        account.display_name = body.display_name
    if body.new_password:
        account.hashed_pw = hash_password(body.new_password)
        account.must_change_password = 1
    await db.commit()
    return ok(
        {"id": account.id, "username": account.username, "role": account.role, "is_active": bool(account.is_active)}
    )


@router.post("/change-password")
async def change_password(body: ChangePasswordRequest, request: Request, user: UserDep, db: DbDep):
    """修改自己的密码。权限：登录用户。"""
    from app.core.security import verify_password

    result = await db.execute(select(UserAccount).where(UserAccount.username == user.username))
    account = result.scalar_one_or_none()
    if account is None or not verify_password(body.old_password, account.hashed_pw):
        raise HTTPException(status_code=400, detail=err("invalid_credentials", "原密码错误"))
    if verify_password(body.new_password, account.hashed_pw):
        raise HTTPException(status_code=400, detail=err("password_reuse", "新密码不能与当前密码相同"))
    if len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail=err("weak_password", "新密码至少需要 8 个字符"))
    account.hashed_pw = hash_password(body.new_password)
    account.must_change_password = 0
    account.failed_login_attempts = 0
    account.locked_until = None
    db.add(
        OperatorAction(
            ts=now_iso(),
            operator_id=account.username,
            operator_role=account.role,
            action_type="change_password",
            result="success",
            reason_code="self_service",
            client_ip=request.client.host if request.client else "unknown",
        )
    )
    await db.commit()
    return ok({"message": "密码已更新，请重新登录", "must_change_password": False})
