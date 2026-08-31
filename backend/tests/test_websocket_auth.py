"""WebSocket 用户状态与连接权限测试。"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.api.websocket as websocket_module
from app.api.ws_manager import ConnectionContext, ConnectionLimitExceeded, ConnectionManager
from app.core.security import create_access_token
from app.db.models import UserAccount
from app.hostcomm.protocol import now_iso


class _FakeWebSocket:
    def __init__(self) -> None:
        self.accepted = False

    async def accept(self) -> None:
        self.accepted = True


class _BlockingWebSocket(_FakeWebSocket):
    def __init__(self) -> None:
        super().__init__()
        self.accept_started = asyncio.Event()
        self.release_accept = asyncio.Event()

    async def accept(self) -> None:
        self.accept_started.set()
        await self.release_accept.wait()
        self.accepted = True


async def _authenticate(monkeypatch, db_session, *, user: UserAccount, token_role: str):
    db_session.add(user)
    await db_session.commit()
    sessionmaker = async_sessionmaker(db_session.bind, expire_on_commit=False)
    monkeypatch.setattr(websocket_module, "get_sessionmaker", lambda: sessionmaker)
    token, _ = create_access_token(user.username, token_role)
    return await websocket_module.authenticate_websocket(token)


async def test_websocket_accepts_active_user_with_current_role(monkeypatch, db_session) -> None:
    user = UserAccount(
        username="operator1",
        hashed_pw="unused",
        role="operator",
        display_name=None,
        is_active=1,
        created_at=now_iso(),
    )

    context = await _authenticate(monkeypatch, db_session, user=user, token_role="operator")

    assert context == ConnectionContext(username="operator1", role="operator")


@pytest.mark.parametrize(
    ("is_active", "database_role", "token_role"),
    [
        (0, "operator", "operator"),
        (1, "observer", "operator"),
        (1, "operator", "forged-role"),
    ],
)
async def test_websocket_rejects_inactive_mismatched_or_invalid_role(
    monkeypatch,
    db_session,
    is_active: int,
    database_role: str,
    token_role: str,
) -> None:
    user = UserAccount(
        username=f"user-{is_active}-{database_role}-{token_role}",
        hashed_pw="unused",
        role=database_role,
        display_name=None,
        is_active=is_active,
        created_at=now_iso(),
    )

    context = await _authenticate(monkeypatch, db_session, user=user, token_role=token_role)

    assert context is None


async def test_connection_manager_limits_each_user_and_keeps_context() -> None:
    manager = ConnectionManager()
    first = _FakeWebSocket()
    second = _FakeWebSocket()
    context = ConnectionContext(username="operator1", role="operator")

    await manager.connect(first, context, max_connections_per_user=1)

    assert manager.context_for(first) == context
    with pytest.raises(ConnectionLimitExceeded):
        await manager.connect(second, context, max_connections_per_user=1)
    assert second.accepted is False


async def test_connection_limit_reserves_slot_during_websocket_accept() -> None:
    manager = ConnectionManager()
    first = _BlockingWebSocket()
    second = _FakeWebSocket()
    context = ConnectionContext(username="operator1", role="operator")
    first_connect = asyncio.create_task(manager.connect(first, context, max_connections_per_user=1))
    await first.accept_started.wait()

    with pytest.raises(ConnectionLimitExceeded):
        await manager.connect(second, context, max_connections_per_user=1)

    first.release_accept.set()
    await first_connect
    assert manager.count == 1
