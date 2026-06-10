"""set_parameters 完整链路测试（规格 3.6 / 安全红线 8）。"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.db.models import OperatorAction, ParameterSnapshot
from app.services.command_service import CommandError, compute_param_crc
from app.services.parameter_service import ParameterService

VALUES = {
    "process": {"gas_switch_temp_deg_c": 500, "end_temp_deg_c": 1580, "hold_minutes": 30},
    "mfc": {"n2_reduce_l_min": 3.5, "co_reduce_l_min": 1.5},
}


class _FakeClient:
    is_online = True

    def __init__(self, accept=True, reason_code="ok", readback=None):
        self._accept = accept
        self._reason = reason_code
        self._readback = readback
        self.last_set = None

    async def send_command(self, command, params, *, operator_id, role, confirm_token=None):
        self.last_set = params
        return {
            "command": command,
            "result": "accepted" if self._accept else "rejected",
            "reason_code": self._reason,
            "reason_text": "mock",
            "current_state": "Standby",
        }

    async def get_parameters(self):
        vals = self._readback if self._readback is not None else (self.last_set or {}).get("values", {})
        return {
            "fw_version": "FW-X",
            "device_profile_version": "DP-X",
            "parameter_crc": "0xABCD1234",
            "params": vals,
        }


class _FakeCache:
    def __init__(self, state="Standby"):
        self._state = state

    def get_field(self, path):
        return self._state if path == "system.current_state" else None


def _crc():
    return compute_param_crc(VALUES)


# ----------------------------------------------------- happy path：下发+回读+快照
async def test_set_parameters_full_chain(db_session):
    client = _FakeClient(accept=True)
    service = ParameterService(client, _FakeCache("Standby"))
    result = await service.set_parameters(
        VALUES, _crc(), operator_id="adm", role="admin", db_session=db_session
    )
    assert result["readback_ok"] is True
    assert result["parameter_crc"] == "0xABCD1234"
    # 下发到控制板的载荷含 values + param_crc
    assert client.last_set["values"] == VALUES
    # 写了 1 条参数快照 + 1 条审计
    assert await db_session.scalar(select(func.count()).select_from(ParameterSnapshot)) == 1
    assert await db_session.scalar(select(func.count()).select_from(OperatorAction)) == 1
    snap = (await db_session.execute(select(ParameterSnapshot))).scalar_one()
    assert snap.source == "set_by_hmi"
    assert snap.param_crc == "0xABCD1234"


# ----------------------------------------------------- 运行态拒绝（安全红线 8）
async def test_set_parameters_running_rejected(db_session):
    service = ParameterService(_FakeClient(), _FakeCache("Reducing"))
    with pytest.raises(CommandError) as ei:
        await service.set_parameters(
            VALUES, _crc(), operator_id="adm", role="admin", db_session=db_session
        )
    assert ei.value.error_code == "state_not_allowed"
    # 未下发，无审计
    assert await db_session.scalar(select(func.count()).select_from(OperatorAction)) == 0


# ----------------------------------------------------- CRC 不匹配，不下发
async def test_set_parameters_bad_crc(db_session):
    service = ParameterService(_FakeClient(), _FakeCache("Standby"))
    with pytest.raises(CommandError) as ei:
        await service.set_parameters(
            VALUES, "deadbeef", operator_id="adm", role="admin", db_session=db_session
        )
    assert ei.value.error_code == "parameter_crc_error"


# ----------------------------------------------------- 控制板拒绝
async def test_set_parameters_device_rejected(db_session):
    service = ParameterService(_FakeClient(accept=False, reason_code="invalid_state"), _FakeCache())
    with pytest.raises(CommandError) as ei:
        await service.set_parameters(
            VALUES, _crc(), operator_id="adm", role="admin", db_session=db_session
        )
    assert ei.value.status_code == 400
    assert ei.value.error_code == "invalid_state"
    # 有 1 条 rejected 审计，但无快照
    assert await db_session.scalar(select(func.count()).select_from(OperatorAction)) == 1
    assert await db_session.scalar(select(func.count()).select_from(ParameterSnapshot)) == 0


# ----------------------------------------------------- 回读不一致
async def test_set_parameters_readback_mismatch(db_session):
    service = ParameterService(
        _FakeClient(accept=True, readback={"process": {"end_temp_deg_c": 9999}}), _FakeCache()
    )
    with pytest.raises(CommandError) as ei:
        await service.set_parameters(
            VALUES, _crc(), operator_id="adm", role="admin", db_session=db_session
        )
    assert ei.value.status_code == 409
    assert ei.value.error_code == "parameter_readback_mismatch"
    # accepted 审计 + mismatch 审计 = 2，无快照
    assert await db_session.scalar(select(func.count()).select_from(OperatorAction)) == 2
    assert await db_session.scalar(select(func.count()).select_from(ParameterSnapshot)) == 0
