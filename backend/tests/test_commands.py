"""命令服务测试 T07-T10、T12（开发规格说明书 9.2）。"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.db.models import OperatorAction
from app.hostcomm.client import HostCommTimeoutError
from app.services.command_service import (
    CommandError,
    CommandService,
    check_confirm_token,
    check_parameter_crc,
    check_permission,
    check_state,
    compute_param_crc,
    confirm_tokens,
)


class _FakeClient:
    is_online = True

    def __init__(self, result="accepted", error=None):
        self._result = result
        self._error = error
        self.sent: list[tuple] = []

    async def send_command(self, command, params, *, operator_id, role, confirm_token=None, msg_id=None):
        self.sent.append((command, params))
        if self._error is not None:
            raise self._error
        return {
            "request_msg_id": "x",
            "command": command,
            "result": self._result,
            "reason_code": "ok",
            "current_state": "Precheck",
        }


class _FakeCache:
    def __init__(self, state="Standby"):
        self._state = state

    @property
    def current_state(self):
        return self.get_field("system.current_state")

    def get_field(self, path):
        if path == "system.current_state":
            return self._state
        return None


# ---------------------------------------------------- T07 start_test 权限校验
def test_t07_permission():
    """T07：Observer 无权 start_test（403）；Operator 通过。"""
    with pytest.raises(CommandError) as ei:
        check_permission("start_test", "observer")
    assert ei.value.status_code == 403
    assert ei.value.error_code == "operator_permission_denied"
    # Operator 不抛异常
    check_permission("start_test", "operator")


# ---------------------------------------------------- T08 start_test 二次确认
def test_t08_confirm_token():
    """T08：CO 命令无 confirm_token → 400；有效 token → 通过。"""
    with pytest.raises(CommandError) as ei:
        check_confirm_token("start_test", None)
    assert ei.value.status_code == 400
    assert ei.value.error_code == "confirm_token_required"

    token = confirm_tokens.issue()
    check_confirm_token("start_test", token)  # 不抛异常
    # 单次使用：再次使用应失效
    with pytest.raises(CommandError):
        check_confirm_token("start_test", token)


# ---------------------------------------------- T09 set_parameters 运行中拒绝
def test_t09_set_parameters_running_rejected():
    """T09：current_state=Reducing 时 set_parameters → 400 state_not_allowed。"""
    with pytest.raises(CommandError) as ei:
        check_state("set_parameters", "Reducing")
    assert ei.value.status_code == 400
    assert ei.value.error_code == "state_not_allowed"
    # 非运行态允许
    check_state("set_parameters", "Standby")


@pytest.mark.parametrize(
    "state",
    ["GasSwitch", "reducing", "HOLDING", "Leak-Check", "End", "Fault/Purge", "mystery-state", None],
)
def test_t09_set_parameters_rejects_active_or_unknown_states(state):
    """固件别名、大小写变体、未知/缺失状态均不得绕过参数下发防线。"""
    with pytest.raises(CommandError) as ei:
        check_state("set_parameters", state)
    assert ei.value.error_code == "state_not_allowed"


@pytest.mark.parametrize("state", ["Standby", "idle", "Complete", "Fault"])
def test_t09_set_parameters_allows_explicit_non_running_states(state):
    check_state("set_parameters", state)


# ---------------------------------------------------- T10 操作日志写库
async def test_t10_operator_action_logged(db_session):
    """T10：命令执行后 operator_action 表有记录。"""
    service = CommandService(_FakeClient(), _FakeCache("Standby"))
    await service.execute(
        "tare_balance",
        {},
        operator_id="op001",
        role="operator",
        db_session=db_session,
    )
    count = await db_session.scalar(
        select(func.count()).select_from(OperatorAction).where(OperatorAction.action_type == "tare_balance")
    )
    assert count == 1
    row = (
        await db_session.execute(select(OperatorAction).where(OperatorAction.action_type == "tare_balance"))
    ).scalar_one()
    assert row.action_type == "tare_balance"
    assert row.operator_id == "op001"
    assert row.result == "accepted"


async def test_command_timeout_has_explicit_audit_reason(db_session):
    """命令通信超时应保留原异常，并写入稳定的审计原因码。"""
    service = CommandService(_FakeClient(error=HostCommTimeoutError("timeout")), _FakeCache("Standby"))

    with pytest.raises(HostCommTimeoutError):
        await service.execute(
            "tare_balance",
            {},
            operator_id="op001",
            role="operator",
            db_session=db_session,
        )

    row = (
        await db_session.execute(select(OperatorAction).where(OperatorAction.action_type == "tare_balance"))
    ).scalar_one()
    assert row.result == "error"
    assert row.reason_code == "device_comm_timeout"


@pytest.mark.parametrize("test_id", ["../startup", r"..\startup", "bad:name", "bad*name", "x" * 65])
async def test_start_rejects_unsafe_test_id_before_device_command(db_session, test_id):
    client = _FakeClient()
    service = CommandService(client, _FakeCache("Standby"))

    with pytest.raises(CommandError) as exc:
        await service.execute(
            "start_test",
            {"test_id": test_id},
            operator_id="op001",
            role="operator",
            confirm_token=confirm_tokens.issue(),
            db_session=db_session,
        )

    assert exc.value.status_code == 400
    assert exc.value.error_code == "invalid_test_id"
    assert client.sent == []


# ---------------------------------------------------- T12 set_parameters CRC
def test_t12_parameter_crc():
    """T12：CRC 不匹配 → 400 parameter_crc_error，不下发；匹配则通过。"""
    values = {"gas_switch_temp_deg_c": 500, "end_temp_deg_c": 1580}
    # 错误 CRC
    with pytest.raises(CommandError) as ei:
        check_parameter_crc("set_parameters", {"values": values, "param_crc": "deadbeef"})
    assert ei.value.error_code == "parameter_crc_error"
    # 正确 CRC
    good = compute_param_crc(values)
    check_parameter_crc("set_parameters", {"values": values, "param_crc": good})
