"""实际快照路径及新鲜度必须决定控制写入权限。"""

import time

import pytest

from app.services.cache import StatusCache
from app.services.command_service import CommandError, CommandService, compute_param_crc
from app.services.parameter_service import ParameterService


class Board:
    is_online = True

    def __init__(self):
        self.sent = []
        self.readbacks = 0

    async def send_command(self, command, params, **kwargs):
        self.sent.append((command, params))
        return {"result": "accepted", "command": command, "reason_code": "ok"}

    async def get_parameters(self):
        self.readbacks += 1
        return {"params": self.sent[-1][1]["values"], "parameter_crc": "device-crc"}


async def test_canonical_standby_allows_parameter_write_and_running_rejects(db_session):
    cache, board = StatusCache(), Board()
    service = ParameterService(board, cache)
    values = {"process": {"end_temp_deg_c": 1580}}
    await cache.update({"state_machine": {"current_state": "Standby"}})
    assert (
        await service.set_parameters(
            values, compute_param_crc(values), operator_id="a", role="admin", db_session=db_session
        )
    )["readback_ok"]
    await cache.update({"state_machine": {"current_state": "Reducing"}})
    with pytest.raises(CommandError, match=""):
        await service.set_parameters(
            values, compute_param_crc(values), operator_id="a", role="admin", db_session=db_session
        )
    assert len(board.sent) == 1


@pytest.mark.parametrize("mode", ["missing", "stale", "conflict"])
async def test_uncertain_state_never_sends_parameters(mode, db_session):
    cache, board = StatusCache(), Board()
    if mode != "missing":
        payload = {"state_machine": {"current_state": "Standby"}}
        if mode == "conflict":
            payload["system"] = {"current_state": "Reducing"}
        await cache.update(payload)
    if mode == "stale":
        cache._last_update_monotonic = time.monotonic() - 10
    with pytest.raises(CommandError):
        await ParameterService(board, cache).set_parameters(
            {}, compute_param_crc({}), operator_id="a", role="admin", db_session=db_session
        )
    assert board.sent == []


async def test_generic_parameter_command_uses_verified_path(db_session):
    cache, board = StatusCache(), Board()
    await cache.update({"state_machine": {"current_state": "Standby"}})
    values = {"process": {"end_temp_deg_c": 1580}}
    result = await CommandService(board, cache).execute(
        "set_parameters",
        {"values": values, "param_crc": compute_param_crc(values)},
        operator_id="a",
        role="admin",
        db_session=db_session,
    )
    assert result["readback_ok"] is True
    assert board.readbacks == 1


async def test_backlogged_snapshot_keeps_receipt_age():
    cache = StatusCache()
    await cache.update(
        {
            "state_machine": {"current_state": "Standby"},
            "_hostcomm": {"received_monotonic": time.monotonic() - 10},
        }
    )
    assert cache.is_fresh is False
    assert cache.current_state is None


async def test_invalidated_snapshot_cannot_be_refreshed_by_old_callback():
    cache = StatusCache()
    snapshot = {"state_machine": {"current_state": "Standby"}, "_hostcomm": {"received_monotonic": time.monotonic()}}
    await cache.update(snapshot)
    cache.invalidate()
    await cache.update(snapshot)
    assert cache.current_state is None
    assert not cache.is_fresh
    await cache.update({"state_machine": {"current_state": "Standby"}})
    assert cache.current_state == "Standby"


def test_status_controls_use_single_state_and_availability_policy():
    from app.services.state_policy import enrich_status_snapshot

    ready = enrich_status_snapshot({"state_machine": {"current_state": "Standby"}}, control_ready=True)
    blocked = enrich_status_snapshot({"state_machine": {"current_state": "Standby"}}, control_ready=False)
    assert ready["system"]["can_set_parameters"] is True
    assert blocked["system"]["can_set_parameters"] is False
    assert blocked["system"]["can_start_test"] is False
