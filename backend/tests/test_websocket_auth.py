"""WebSocket 用户状态与连接权限测试。"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import WebSocketDisconnect
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


async def _authenticate(
    monkeypatch,
    db_session,
    *,
    user: UserAccount,
    token_role: str,
    token_version: int | None = None,
):
    db_session.add(user)
    await db_session.commit()
    sessionmaker = async_sessionmaker(db_session.bind, expire_on_commit=False)
    monkeypatch.setattr(websocket_module, "get_sessionmaker", lambda: sessionmaker)
    token, _ = create_access_token(
        user.username,
        token_role,
        token_version=user.token_version if token_version is None else token_version,
    )
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


async def test_websocket_rejects_revoked_token_version(monkeypatch, db_session) -> None:
    user = UserAccount(
        username="revoked-user",
        hashed_pw="unused",
        role="operator",
        display_name=None,
        is_active=1,
        token_version=3,
        created_at=now_iso(),
    )

    context = await _authenticate(
        monkeypatch,
        db_session,
        user=user,
        token_role="operator",
        token_version=2,
    )

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


class _ProtocolWebSocket:
    def __init__(self, messages: list[dict], *, origin: str = "http://testserver") -> None:
        self.headers = {"origin": origin, "host": "testserver"}
        self._messages = [json.dumps(item) for item in messages]
        self.accepted = False
        self.sent: list[dict] = []
        self.closed: list[int] = []

    async def accept(self) -> None:
        self.accepted = True

    async def receive_text(self) -> str:
        if not self._messages:
            raise WebSocketDisconnect(code=1000)
        return self._messages.pop(0)

    async def send_json(self, message: dict) -> None:
        self.sent.append(message)

    async def close(self, code: int) -> None:
        self.closed.append(code)


async def test_realtime_authenticates_in_first_message_not_url(monkeypatch) -> None:
    ws = _ProtocolWebSocket(
        [
            {"type": "authenticate", "token": "jwt-token"},
            {"type": "ping"},
        ]
    )
    monkeypatch.setattr(
        websocket_module,
        "authenticate_websocket",
        lambda token: asyncio.sleep(0, result=ConnectionContext("operator1", "operator")),
    )
    monkeypatch.setattr(
        websocket_module,
        "get_settings",
        lambda: SimpleNamespace(
            smd_ws_auth_timeout_seconds=5,
            smd_ws_idle_timeout_seconds=45,
            smd_ws_max_message_bytes=65_536,
            smd_ws_rate_limit_per_minute=120,
            smd_ws_max_connections_per_user=3,
            cors_origins=[],
        ),
    )

    await websocket_module.realtime(ws)

    assert ws.accepted is True
    assert ws.sent[0] == {"type": "auth_ok"}
    assert ws.sent[1] == {"type": "pong"}
    assert not ws.closed


async def test_realtime_rejects_invalid_origin_before_authentication(monkeypatch) -> None:
    ws = _ProtocolWebSocket(
        [{"type": "authenticate", "token": "jwt-token"}],
        origin="https://attacker.example",
    )
    monkeypatch.setattr(
        websocket_module,
        "get_settings",
        lambda: SimpleNamespace(cors_origins=[]),
    )

    await websocket_module.realtime(ws)

    assert ws.accepted is False
    assert ws.closed == [websocket_module.WS_CLOSE_FORBIDDEN_ORIGIN]
