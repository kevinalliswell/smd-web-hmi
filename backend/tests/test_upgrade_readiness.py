from types import SimpleNamespace

import pytest

from app.api.routes.maintenance import validate_upgrade_ready
from app.services.maintenance_service import MaintenanceBlockedError


@pytest.mark.parametrize(
    "online,fresh,state,open_session",
    [
        (False, True, "Idle", False),
        (True, False, "Idle", False),
        (True, True, "Cooling", False),
        (True, True, "Idle", True),
        (True, True, None, False),
    ],
)
async def test_upgrade_rejects_unsafe_or_unfinished(online, fresh, state, open_session):
    class Db:
        async def scalar(self, query):
            return "unfinished" if open_session else None

    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(hostcomm_client=SimpleNamespace(is_online=online)))
    )
    cache = SimpleNamespace(is_fresh=fresh, current_state=state)
    with pytest.raises(MaintenanceBlockedError):
        await validate_upgrade_ready(request, Db(), cache=cache)


async def test_upgrade_allows_only_fresh_idle_without_open_session():
    class Db:
        async def scalar(self, query):
            return None

    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(hostcomm_client=SimpleNamespace(is_online=True)))
    )
    await validate_upgrade_ready(request, Db(), cache=SimpleNamespace(is_fresh=True, current_state="Idle"))


@pytest.mark.parametrize("endpoint", ["prepare", "cancel", "claim"])
async def test_manual_version_endpoints_are_retired(endpoint):
    from fastapi import HTTPException

    from app.api.routes import maintenance as route

    with pytest.raises(HTTPException) as error:
        await getattr(route, endpoint)()
    assert error.value.status_code == 410
    assert "安装器" in error.value.detail and "无需填写版本号" in error.value.detail


async def test_browser_status_exposes_version_without_local_authorization(monkeypatch):
    from app import __version__
    from app.api.routes import maintenance as route

    monkeypatch.setattr(
        route.maintenance_manager,
        "upgrade_state",
        lambda: {
            "state": "claimed",
            "token": "secret",
            "db_path": "private",
            "admin_sid": "private",
            "request_sha256": "private",
            "operation": "upgrade",
            "target_version": "0.4.0",
        },
    )
    result = (await route.status())["data"]
    assert result == {
        "state": "claimed",
        "operation": "upgrade",
        "target_version": "0.4.0",
        "current_version": __version__,
    }


async def test_retired_maintenance_routes_return_http_410_and_keep_read_status(monkeypatch):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from app.api import deps
    from app.api.routes import maintenance as route

    app = FastAPI()
    app.include_router(route.router)
    app.dependency_overrides[deps.get_current_user] = lambda: deps.CurrentUser("admin", "admin")
    monkeypatch.setattr(route.maintenance_manager, "upgrade_state", lambda: {"state": "idle"})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        for method, path in (("POST", "/prepare"), ("DELETE", ""), ("POST", "/claim")):
            response = await http.request(method, "/api/system/maintenance" + path, json={"target_version": "0.4.0"})
            assert response.status_code == 410 and "安装器" in response.json()["detail"]
        assert (await http.get("/api/system/maintenance")).json()["data"]["state"] == "idle"
