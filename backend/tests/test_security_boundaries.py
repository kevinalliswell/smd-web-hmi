"""生产 HTTP 边界与首次管理员初始化测试。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.main as main_module
from app.core.security import verify_password
from app.db.models import UserAccount
from app.main import create_app


async def test_production_disables_api_docs_and_adds_security_headers() -> None:
    app = create_app()
    assert app.docs_url is None
    assert app.redoc_url is None
    assert app.openapi_url is None

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://test") as client:
        response = await client.get("/health", headers={"X-Request-ID": "trace-123"})

    assert response.headers["X-Request-ID"] == "trace-123"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["Strict-Transport-Security"].startswith("max-age=")


async def test_production_requires_explicit_bootstrap_admin_password(monkeypatch, db_session) -> None:
    sessionmaker = async_sessionmaker(db_session.bind, expire_on_commit=False)
    monkeypatch.setattr(main_module, "get_sessionmaker", lambda: sessionmaker)
    monkeypatch.setattr(
        main_module,
        "get_settings",
        lambda: SimpleNamespace(hostcomm_mock=False, smd_bootstrap_admin_password=""),
    )

    with pytest.raises(RuntimeError, match="SMD_BOOTSTRAP_ADMIN_PASSWORD"):
        await main_module._seed_admin()


async def test_explicit_bootstrap_password_creates_forced_change_admin(monkeypatch, db_session) -> None:
    sessionmaker = async_sessionmaker(db_session.bind, expire_on_commit=False)
    monkeypatch.setattr(main_module, "get_sessionmaker", lambda: sessionmaker)
    monkeypatch.setattr(
        main_module,
        "get_settings",
        lambda: SimpleNamespace(
            hostcomm_mock=False,
            smd_bootstrap_admin_password="initial-commercial-password",
        ),
    )

    await main_module._seed_admin()

    account = await db_session.scalar(select(UserAccount).where(UserAccount.username == "admin"))
    assert account is not None
    assert verify_password("initial-commercial-password", account.hashed_pw)
    assert account.must_change_password == 1
