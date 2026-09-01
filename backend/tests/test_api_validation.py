"""API 输入长度、格式、枚举与数值边界测试。"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.api.routes.reports import GenerateReportRequest
from app.api.routes.users import ChangePasswordRequest, CreateUserRequest
from app.api.schemas import CommandRequest, LoginRequest
from app.core.security import create_access_token
from app.db.database import get_db
from app.db.models import UserAccount
from app.hostcomm.protocol import now_iso
from app.main import create_app


@pytest.mark.parametrize("username", ["", "a", "bad-name", "bad/name", "bad\nname", "x" * 33])
def test_login_rejects_invalid_username(username: str) -> None:
    with pytest.raises(ValidationError):
        LoginRequest(username=username, password="candidate")


def test_login_bounds_password_without_blocking_legacy_admin() -> None:
    assert LoginRequest(username="admin", password="admin").password == "admin"
    with pytest.raises(ValidationError):
        LoginRequest(username="admin", password="")
    with pytest.raises(ValidationError):
        LoginRequest(username="admin", password="x" * 129)


def test_user_mutations_validate_password_role_and_display_name() -> None:
    with pytest.raises(ValidationError):
        CreateUserRequest(username="operator_1", password="short", role="operator")
    with pytest.raises(ValidationError):
        CreateUserRequest(username="operator_1", password="secure-password", role="root")
    with pytest.raises(ValidationError):
        CreateUserRequest(
            username="operator_1",
            password="secure-password",
            role="operator",
            display_name="x" * 65,
        )
    with pytest.raises(ValidationError):
        ChangePasswordRequest(old_password="old", new_password="short")


@pytest.mark.parametrize("test_id", ["../secret", "bad/id", "bad id", "x" * 65])
def test_report_request_rejects_unsafe_test_id(test_id: str) -> None:
    with pytest.raises(ValidationError):
        GenerateReportRequest(test_id=test_id)


def test_start_command_rejects_unsafe_nested_test_id() -> None:
    with pytest.raises(ValidationError):
        CommandRequest(command="start_test", params={"test_id": "../unsafe"})


async def test_test_routes_reject_unsafe_path_and_unbounded_max_points(db_session) -> None:
    app = create_app()

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    db_session.add(
        UserAccount(
            username="tester",
            hashed_pw="unused",
            role="observer",
            display_name=None,
            is_active=1,
            created_at=now_iso(),
        )
    )
    await db_session.commit()
    token, _ = create_access_token("tester", "observer")
    headers = {"Authorization": f"Bearer {token}"}
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        invalid_id = await client.get("/api/tests/bad$id/samples", headers=headers)
        too_many = await client.get("/api/tests/TEST-1/samples?max_points=10001", headers=headers)
        negative = await client.get("/api/trends?max_points=0", headers=headers)

    assert invalid_id.status_code == 422
    assert too_many.status_code == 422
    assert negative.status_code == 422


async def test_validation_response_never_echoes_password_input() -> None:
    app = create_app()
    transport = ASGITransport(app=app)
    secret = "never-return-this-secret-value"
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/auth/login",
            json={"username": "admin", "password": secret * 10},
        )

    assert response.status_code == 422
    assert response.json()["error_code"] == "validation_error"
    assert secret not in response.text
