"""默认口令强制修改流程测试。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from starlette.testclient import TestClient

from app.api.deps import CurrentUser, get_current_user
from app.api.routes.users import ChangePasswordRequest, change_password
from app.core.security import create_access_token, decode_access_token, hash_password, verify_password
from app.db.models import OperatorAction, UserAccount
from app.main import create_app


async def test_forced_password_token_can_only_access_password_change(db_session) -> None:
    account = UserAccount(
        username="admin",
        hashed_pw=hash_password("admin"),
        role="admin",
        display_name="系统管理员",
        is_active=1,
        must_change_password=1,
        token_version=4,
        created_at="2026-08-30T12:00:00+00:00",
    )
    db_session.add(account)
    await db_session.commit()
    token, _ = create_access_token("admin", "admin", must_change_password=True, token_version=4)
    assert decode_access_token(token)["must_change_password"] is True

    with pytest.raises(HTTPException) as blocked:
        await get_current_user(
            request=SimpleNamespace(url=SimpleNamespace(path="/api/status")),
            authorization=f"Bearer {token}",
            db=db_session,
        )
    assert blocked.value.status_code == 403
    assert blocked.value.detail["error_code"] == "password_change_required"

    allowed = await get_current_user(
        request=SimpleNamespace(url=SimpleNamespace(path="/api/users/change-password")),
        authorization=f"Bearer {token}",
        db=db_session,
    )
    assert allowed.must_change_password is True


async def test_rest_auth_rejects_revoked_or_inactive_account(db_session) -> None:
    account = UserAccount(
        username="operator1",
        hashed_pw="unused",
        role="operator",
        display_name=None,
        is_active=1,
        token_version=2,
        created_at="2026-08-30T12:00:00+00:00",
    )
    db_session.add(account)
    await db_session.commit()
    revoked, _ = create_access_token("operator1", "operator", token_version=1)

    with pytest.raises(HTTPException) as rejected:
        await get_current_user(
            request=SimpleNamespace(url=SimpleNamespace(path="/api/status")),
            authorization=f"Bearer {revoked}",
            db=db_session,
        )
    assert rejected.value.status_code == 401
    assert rejected.value.detail["error_code"] == "invalid_token"

    current, _ = create_access_token("operator1", "operator", token_version=2)
    account.is_active = 0
    await db_session.commit()
    with pytest.raises(HTTPException) as inactive:
        await get_current_user(
            request=SimpleNamespace(url=SimpleNamespace(path="/api/status")),
            authorization=f"Bearer {current}",
            db=db_session,
        )
    assert inactive.value.detail["error_code"] == "invalid_token"


def test_forced_password_token_cannot_open_websocket() -> None:
    token, _ = create_access_token("admin", "admin", must_change_password=True)
    client = TestClient(create_app())

    with pytest.raises(Exception) as rejected:  # noqa: PT011
        with client.websocket_connect(f"/ws/realtime?token={token}") as ws:
            ws.receive_text()
    assert getattr(rejected.value, "code", None) == 4001 or "4001" in str(rejected.value)


async def test_change_password_clears_forced_flag_and_writes_audit(db_session) -> None:
    account = UserAccount(
        username="admin",
        hashed_pw=hash_password("admin"),
        role="admin",
        display_name="系统管理员",
        is_active=1,
        must_change_password=1,
        created_at="2026-08-30T12:00:00+00:00",
    )
    db_session.add(account)
    await db_session.commit()

    request = SimpleNamespace(client=SimpleNamespace(host="10.0.0.8"))
    user = CurrentUser("admin", "admin", must_change_password=True)
    response = await change_password(
        ChangePasswordRequest(old_password="admin", new_password="new-secure-password"),
        request,
        user,
        db_session,
    )

    await db_session.refresh(account)
    assert response["data"]["must_change_password"] is False
    assert account.must_change_password == 0
    assert verify_password("new-secure-password", account.hashed_pw)
    action = await db_session.scalar(select(OperatorAction).where(OperatorAction.action_type == "change_password"))
    assert action is not None
    assert action.operator_id == "admin"
    assert action.client_ip == "10.0.0.8"


async def test_change_password_rejects_reusing_current_password(db_session) -> None:
    current_password = "secure-admin-password"
    account = UserAccount(
        username="admin",
        hashed_pw=hash_password(current_password),
        role="admin",
        display_name=None,
        is_active=1,
        must_change_password=1,
        created_at="2026-08-30T12:00:00+00:00",
    )
    db_session.add(account)
    await db_session.commit()

    with pytest.raises(HTTPException) as rejected:
        await change_password(
            ChangePasswordRequest(old_password=current_password, new_password=current_password),
            SimpleNamespace(client=None),
            CurrentUser("admin", "admin", must_change_password=True),
            db_session,
        )
    assert rejected.value.detail["error_code"] == "password_reuse"
