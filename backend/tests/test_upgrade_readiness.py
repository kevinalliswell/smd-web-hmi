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


async def test_prepare_hides_token_claim_requires_loopback(monkeypatch, tmp_path):
    from fastapi import HTTPException

    from app.api.routes import maintenance as route
    from app.services.maintenance_service import MaintenanceManager

    manager = MaintenanceManager()
    manager.configure_upgrade(tmp_path / "maintenance.json")
    monkeypatch.setattr(route, "maintenance_manager", manager)
    monkeypatch.setattr(route, "get_settings", lambda: SimpleNamespace(db_path_resolved=tmp_path / "actual.db"))

    async def ready(*args, **kwargs):
        return None

    monkeypatch.setattr(route, "validate_upgrade_ready", ready)
    request = SimpleNamespace(client=SimpleNamespace(host="192.168.1.2"))
    response = await route.prepare(
        route.PrepareRequest(target_version="0.4.0-rc.1"), request, SimpleNamespace(username="admin"), None
    )
    assert "token" not in response["data"] and "db_path" not in response["data"]
    token = manager.upgrade_state()["token"]
    with pytest.raises(HTTPException) as error:
        await route.claim(request, None, token)
    assert error.value.status_code == 403
    request.client.host = "127.0.0.1"
    result = await route.claim(request, None, token)
    assert result["data"]["state"] == "claimed" and "token" not in result["data"]
