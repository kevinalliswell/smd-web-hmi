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
