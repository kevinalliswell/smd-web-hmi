"""用户管理接口测试（规格 3.9）：创建/更新/自锁防护。"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api.deps import CurrentUser
from app.api.routes import users as users_route
from app.db.models import UserAccount


async def _mk_user(db, username="bob", role="operator"):
    res = await users_route.create_user(
        users_route.CreateUserRequest(username=username, password="test-password", role=role),
        db,
    )
    row = await db.scalar(select(UserAccount).where(UserAccount.username == username))
    return row


async def test_create_and_duplicate(db_session):
    await _mk_user(db_session, "alice", "observer")
    with pytest.raises(HTTPException) as ei:
        await users_route.create_user(
            users_route.CreateUserRequest(username="alice", password="another-password", role="observer"), db_session
        )
    assert ei.value.status_code == 400
    assert ei.value.detail["error_code"] == "username_exists"


async def test_update_role_and_active(db_session):
    row = await _mk_user(db_session, "carol", "observer")
    admin = CurrentUser("admin", "admin")
    res = await users_route.update_user(
        row.id, users_route.UpdateUserRequest(role="operator", is_active=False), admin, db_session
    )
    assert res["data"]["role"] == "operator"
    assert res["data"]["is_active"] is False


async def test_self_lockout_blocked(db_session):
    # admin 不能停用/降级自己
    res = await users_route.create_user(
        users_route.CreateUserRequest(username="root", password="test-password", role="admin"), db_session
    )
    row = await db_session.scalar(select(UserAccount).where(UserAccount.username == "root"))
    me = CurrentUser("root", "admin")
    with pytest.raises(HTTPException) as ei:
        await users_route.update_user(row.id, users_route.UpdateUserRequest(is_active=False), me, db_session)
    assert ei.value.detail["error_code"] == "self_lockout"
    with pytest.raises(HTTPException) as ei2:
        await users_route.update_user(row.id, users_route.UpdateUserRequest(role="observer"), me, db_session)
    assert ei2.value.detail["error_code"] == "self_lockout"
