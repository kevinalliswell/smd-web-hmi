"""登录限流、锁定与审计测试。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api.routes import auth as auth_route
from app.api.schemas import LoginRequest
from app.core.security import hash_password
from app.db.models import OperatorAction, UserAccount
from app.services.auth_service import IpRateLimiter, LoginProtector, LoginRejected


def test_ip_rate_limiter_resets_after_window() -> None:
    now = [100.0]
    limiter = IpRateLimiter(clock=lambda: now[0])

    assert limiter.allow("10.0.0.8", limit=2, window_seconds=60)
    assert limiter.allow("10.0.0.8", limit=2, window_seconds=60)
    assert not limiter.allow("10.0.0.8", limit=2, window_seconds=60)

    now[0] += 61
    assert limiter.allow("10.0.0.8", limit=2, window_seconds=60)


async def test_failed_passwords_lock_account_and_success_after_expiry(db_session) -> None:
    now = [datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)]
    protector = LoginProtector(clock=lambda: now[0])
    user = UserAccount(
        username="operator1",
        hashed_pw=hash_password("correct-password"),
        role="operator",
        display_name=None,
        is_active=1,
        created_at=now[0].isoformat(),
    )
    db_session.add(user)
    await db_session.commit()

    for _ in range(3):
        with pytest.raises(LoginRejected):
            await protector.authenticate(
                db_session,
                username=user.username,
                password="wrong-password",
                client_ip="10.0.0.8",
                max_failures=3,
                lock_minutes=15,
            )

    await db_session.refresh(user)
    assert user.failed_login_attempts == 3
    assert user.locked_until == (now[0] + timedelta(minutes=15)).isoformat(timespec="seconds")
    action = await db_session.scalar(select(OperatorAction).where(OperatorAction.action_type == "login_lockout"))
    assert action is not None
    assert action.client_ip == "10.0.0.8"

    with pytest.raises(LoginRejected) as locked:
        await protector.authenticate(
            db_session,
            username=user.username,
            password="correct-password",
            client_ip="10.0.0.8",
            max_failures=3,
            lock_minutes=15,
        )
    assert locked.value.reason == "account_locked"

    now[0] += timedelta(minutes=16)
    authenticated = await protector.authenticate(
        db_session,
        username=user.username,
        password="correct-password",
        client_ip="10.0.0.8",
        max_failures=3,
        lock_minutes=15,
    )
    assert authenticated.username == user.username
    assert authenticated.failed_login_attempts == 0
    assert authenticated.locked_until is None


async def test_unknown_user_still_runs_password_verification(monkeypatch, db_session) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "app.services.auth_service.verify_password",
        lambda password, hashed: calls.append((password, hashed)) or False,
    )

    with pytest.raises(LoginRejected):
        await LoginProtector().authenticate(
            db_session,
            username="missing-user",
            password="candidate",
            client_ip="10.0.0.8",
            max_failures=5,
            lock_minutes=15,
        )

    assert len(calls) == 1
    assert calls[0][0] == "candidate"
    assert calls[0][1].startswith("pbkdf2_sha256$")


async def test_login_route_returns_429_without_querying_credentials(monkeypatch, db_session) -> None:
    auth_route.login_rate_limiter.reset()
    monkeypatch.setattr(
        auth_route,
        "get_settings",
        lambda: SimpleNamespace(
            smd_login_rate_limit=1,
            smd_login_rate_window_seconds=60,
            smd_login_max_failures=5,
            smd_login_lock_minutes=15,
        ),
    )
    request = SimpleNamespace(client=SimpleNamespace(host="10.0.0.8"))
    body = LoginRequest(username="missing_user", password="candidate")

    with pytest.raises(HTTPException) as first:
        await auth_route.login(body, request, db_session)
    assert first.value.status_code == 401

    with pytest.raises(HTTPException) as second:
        await auth_route.login(body, request, db_session)
    assert second.value.status_code == 429
    assert second.value.detail["error_code"] == "login_rate_limited"
    assert second.value.headers == {"Retry-After": "60"}
