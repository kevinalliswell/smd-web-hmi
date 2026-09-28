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


async def test_snapshot_received_in_the_same_clock_tick_as_invalidate_is_still_stale():
    """与失效时刻读数完全相等的链路帧必须判为旧帧。

    回归：守卫原本用严格小于。time.monotonic() 的分辨率在 Windows 上约 15.6 ms，
    失效前最后一帧的回执时刻与 invalidate() 常落在同一个 tick、读数完全相等，
    于是失效前的状态会被重新接受并重新授权控制。这里直接读内部失效时刻来构造
    "完全相等"这个边界，避免测试结论依赖运行机器的时钟分辨率。
    """
    cache = StatusCache()
    cache.invalidate()
    same_tick = cache._invalidated_at

    await cache.update(
        {
            "state_machine": {"current_state": "Standby"},
            "_hostcomm": {"received_monotonic": same_tick},
        }
    )

    assert cache.current_state is None
    assert cache.is_fresh is False


async def test_snapshot_received_after_invalidate_is_accepted():
    """同一边界的另一侧：严格晚于失效时刻的链路帧必须被接受，不能一起挡掉。"""
    cache = StatusCache()
    cache.invalidate()
    after = cache._invalidated_at + 1e-6

    await cache.update(
        {
            "state_machine": {"current_state": "Standby"},
            "_hostcomm": {"received_monotonic": after},
        }
    )

    assert cache.current_state == "Standby"
    assert cache.is_fresh is True


def test_status_controls_use_single_state_and_availability_policy():
    from app.services.state_policy import enrich_status_snapshot

    ready = enrich_status_snapshot({"state_machine": {"current_state": "Standby"}}, control_ready=True)
    blocked = enrich_status_snapshot({"state_machine": {"current_state": "Standby"}}, control_ready=False)
    assert ready["system"]["can_set_parameters"] is True
    assert blocked["system"]["can_set_parameters"] is False
    assert blocked["system"]["can_start_test"] is False
