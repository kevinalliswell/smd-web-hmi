"""试验会话生命周期与日志落库测试（sample_point / device_status）。"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.db.models import DeviceStatus, SamplePoint, TestSession
from app.services import logging_service
from app.services.command_service import CommandError, CommandService, confirm_tokens
from app.services.test_runtime import active_test
from app.services.test_session_service import reconcile_test_sessions


class _FakeClient:
    is_online = True

    def __init__(self):
        self.commands = []

    async def send_command(self, command, params, *, operator_id, role, confirm_token=None):
        self.commands.append(command)
        # start/stop 受控停止均推进到一个具体状态
        state = "Precheck" if command == "start_test" else "Cooling"
        return {
            "request_msg_id": "x",
            "command": command,
            "result": "accepted",
            "reason_code": "ok",
            "current_state": state,
        }


class _FakeCache:
    def get_field(self, path):
        return "Standby"  # 非运行态，允许 start


def _snapshot(state="Precheck", pv=1234.5):
    return {
        "state_machine": {"current_state": state},
        "temperature": {"furnace_pv_deg_c": pv, "furnace_sv_deg_c": 1580.0},
        "gas": {"n2_pv_l_min": 2.0, "co_pv_l_min": 1.0},
        "measurement": {"drip_weight_g": 0.3, "delta_p_pa": 12.0, "displacement_mm": 0.4},
        "safety": {"safety_relay_allowed": True},
    }


@pytest.fixture(autouse=True)
def _reset_runtime():
    active_test.stop()
    yield
    active_test.stop()


# ---------------------------------------------------- start → 建会话 + 标记进行中
async def test_start_test_creates_session(db_session):
    """start_test 受理后创建 test_session 并标记当前试验。"""
    service = CommandService(_FakeClient(), _FakeCache())
    token = confirm_tokens.issue()
    await service.execute(
        "start_test",
        {"test_id": "TEST-20260610-001"},
        operator_id="op001",
        role="operator",
        confirm_token=token,
        db_session=db_session,
    )
    assert active_test.active_test_id == "TEST-20260610-001"
    row = await db_session.scalar(select(TestSession).where(TestSession.test_id == "TEST-20260610-001"))
    assert row is not None
    assert row.operator_id == "op001"
    assert row.end_time is None


async def test_start_test_rejects_existing_id_before_device_command(db_session):
    db_session.add(
        TestSession(
            test_id="TEST-20260610-001",
            operator_id="old-op",
            start_time="2026-06-10T01:00:00Z",
            end_time="2026-06-10T02:00:00Z",
        )
    )
    await db_session.commit()
    client = _FakeClient()
    service = CommandService(client, _FakeCache())

    with pytest.raises(CommandError) as exc:
        await service.execute(
            "start_test",
            {"test_id": "TEST-20260610-001"},
            operator_id="op002",
            role="operator",
            confirm_token=confirm_tokens.issue(),
            db_session=db_session,
        )

    assert exc.value.status_code == 409
    assert exc.value.error_code == "test_id_exists"
    assert client.commands == []


# ---------------------------------------------------- 进行中写入 sample_point
async def test_sample_point_written(db_session):
    """进行中试验写入 sample_point，核心字段正确。"""
    await logging_service.append_sample_point(db_session, "TEST-20260610-001", _snapshot())
    count = await db_session.scalar(select(func.count()).select_from(SamplePoint))
    assert count == 1
    sp = (await db_session.execute(select(SamplePoint))).scalar_one()
    assert sp.test_id == "TEST-20260610-001"
    assert sp.furnace_pv == 1234.5
    assert sp.current_state == "Precheck"
    assert sp.safety_relay == 1


# ---------------------------------------------------- stop → 会话收尾
async def test_stop_test_closes_session(db_session):
    """stop_test 受理后 test_session 收尾（写 end_time / end_reason）。"""
    service = CommandService(_FakeClient(), _FakeCache())
    start_token = confirm_tokens.issue()
    await service.execute(
        "start_test",
        {"test_id": "TEST-20260610-002"},
        operator_id="op001",
        role="operator",
        confirm_token=start_token,
        db_session=db_session,
    )
    stop_token = confirm_tokens.issue()
    await service.execute(
        "stop_test",
        {},
        operator_id="op001",
        role="operator",
        confirm_token=stop_token,
        db_session=db_session,
    )
    assert active_test.active_test_id is None
    row = await db_session.scalar(select(TestSession).where(TestSession.test_id == "TEST-20260610-002"))
    assert row.end_time is not None
    assert row.end_reason == "operator_stop"
    assert row.state_at_end == "Cooling"


async def test_stop_test_closes_latest_open_session_after_runtime_loss(db_session):
    row = TestSession(
        test_id="TEST-20260610-003",
        operator_id="op001",
        start_time="2026-06-10T03:00:00Z",
    )
    db_session.add(row)
    await db_session.commit()
    active_test.stop()  # 模拟后端重启导致内存单例丢失

    service = CommandService(_FakeClient(), _FakeCache())
    await service.execute(
        "stop_test",
        {},
        operator_id="op001",
        role="operator",
        confirm_token=confirm_tokens.issue(),
        db_session=db_session,
    )

    await db_session.refresh(row)
    assert row.end_time is not None
    assert row.end_reason == "operator_stop"


async def test_reconcile_restores_matching_running_session(db_session):
    row = TestSession(
        test_id="TEST-20260610-004",
        operator_id="op001",
        start_time="2026-06-10T04:00:00Z",
    )
    db_session.add(row)
    await db_session.commit()

    result = await reconcile_test_sessions(
        db_session,
        {"state_machine": {"test_id": row.test_id, "current_state": "GasSwitch"}},
    )

    await db_session.refresh(row)
    assert result["restored_test_id"] == row.test_id
    assert active_test.active_test_id == row.test_id
    assert row.end_time is None


async def test_reconcile_closes_stale_session_when_device_is_idle(db_session):
    row = TestSession(
        test_id="TEST-20260610-005",
        operator_id="op001",
        start_time="2026-06-10T05:00:00Z",
    )
    db_session.add(row)
    await db_session.commit()

    result = await reconcile_test_sessions(
        db_session,
        {"state_machine": {"test_id": None, "current_state": "Standby"}},
    )

    await db_session.refresh(row)
    assert result["closed"] == 1
    assert active_test.active_test_id is None
    assert row.end_time is not None
    assert row.end_reason == "backend_restart"


# ---------------------------------------------------- device_status 滚动裁剪
async def test_device_status_prune(db_session):
    """device_status 写入后裁剪到保留窗口（唯一允许 DELETE 的表）。"""
    for i in range(5):
        await logging_service.append_device_status(db_session, _snapshot(pv=i), keep=3)
    count = await db_session.scalar(select(func.count()).select_from(DeviceStatus))
    assert count == 3
