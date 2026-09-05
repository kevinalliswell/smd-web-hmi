"""试验会话生命周期与日志落库测试（sample_point / device_status）。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app import main as main_module
from app.db.models import DeviceStatus, OperatorAction, ParameterSnapshot, SamplePoint, TestSession
from app.hostcomm.client import HostCommTimeoutError
from app.services import logging_service
from app.services.command_service import CommandError, CommandService, confirm_tokens
from app.services.sampling_health import sampling_health
from app.services.test_runtime import active_test
from app.services.test_session_service import reconcile_test_sessions


class _FakeClient:
    is_online = True

    def __init__(self, parameter_error=None):
        self.commands = []
        self.command_params = []
        self.parameter_error = parameter_error

    async def send_command(self, command, params, *, operator_id, role, confirm_token=None, msg_id=None):
        self.commands.append(command)
        self.command_params.append(params)
        # start/stop 受控停止均推进到一个具体状态
        state = "Precheck" if command == "start_test" else "Cooling"
        return {
            "request_msg_id": "x",
            "command": command,
            "result": "accepted",
            "reason_code": "ok",
            "current_state": state,
        }

    async def get_parameters(self):
        if self.parameter_error is not None:
            raise self.parameter_error
        return {
            "fw_version": "FW-START",
            "device_profile_version": "DP-START",
            "parameter_crc": "crc-start",
            "params": {"process": {"end_temp_deg_c": 1580}},
        }


class _FakeCache:
    @property
    def current_state(self):
        return self.get_field("system.current_state")

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
        {
            "test_id": "TEST-20260610-001",
            "original_height_mm": 25.5,
            "sample_label": "SAMPLE-A",
            "notes": "commercial closure",
        },
        operator_id="op001",
        role="operator",
        confirm_token=token,
        db_session=db_session,
    )
    assert active_test.active_test_id == "TEST-20260610-001"
    row = await db_session.scalar(select(TestSession).where(TestSession.test_id == "TEST-20260610-001"))
    assert row is not None
    assert row.operator_id == "op001"
    assert row.original_height_mm == 25.5
    assert row.sample_label == "SAMPLE-A"
    assert row.notes == "commercial closure"
    assert row.end_time is None
    assert service._client.command_params[0] == {"test_id": "TEST-20260610-001"}
    snapshot = (await db_session.execute(select(ParameterSnapshot))).scalar_one()
    assert snapshot.test_id == "TEST-20260610-001"
    assert snapshot.source == "test_start"
    assert snapshot.fw_version == "FW-START"


async def test_start_parameter_readback_failure_prevents_send_and_is_audited(db_session):
    """参数必须在启动前可读；读回失败不能创建缺乏归档的实验。"""
    service = CommandService(_FakeClient(parameter_error=HostCommTimeoutError("timeout")), _FakeCache())
    with pytest.raises(HostCommTimeoutError):
        await service.execute(
            "start_test",
            {"test_id": "TEST-PARAM-TIMEOUT"},
            operator_id="op001",
            role="operator",
            confirm_token=confirm_tokens.issue(),
            db_session=db_session,
        )
    assert service._client.commands == []
    assert await db_session.scalar(select(func.count()).select_from(ParameterSnapshot)) == 0
    assert await db_session.scalar(select(func.count()).select_from(TestSession)) == 0
    actions = (await db_session.scalars(select(OperatorAction).where(OperatorAction.action_type == "start_test"))).all()
    assert [(row.result, row.reason_code) for row in actions] == [("error", "device_comm_timeout")]


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
async def test_stop_test_keeps_session_until_safe_completion(db_session):
    """受理停止不等于冷却完成，继续采样并保留会话。"""
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
    assert active_test.active_test_id == "TEST-20260610-002"
    row = await db_session.scalar(select(TestSession).where(TestSession.test_id == "TEST-20260610-002"))
    assert row.end_time is None
    assert row.stop_requested_at is not None
    assert row.phase == "stopping"


async def test_stop_test_restores_latest_open_session_after_runtime_loss(db_session):
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
    assert row.end_time is None
    assert row.stop_requested_at is not None
    assert active_test.active_test_id == row.test_id


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


async def test_reconcile_requires_review_when_device_is_idle(db_session):
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
    assert result["closed"] == 0
    assert active_test.active_test_id is None
    assert row.end_time is None
    assert row.phase == "needs_review"
    assert row.data_integrity == "incomplete"


# ---------------------------------------------------- device_status 滚动裁剪
async def test_device_status_prune(db_session):
    """按时间而非 id 裁剪，并限制清理执行频率。"""
    now = [datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)]
    monotonic = [100.0]
    pruner = logging_service.DeviceStatusPruner(
        clock=lambda: monotonic[0],
        utc_clock=lambda: now[0],
    )
    recent = DeviceStatus(ts=(now[0] - timedelta(hours=1)).isoformat(), status_json="{}")
    old_with_newer_id = DeviceStatus(ts=(now[0] - timedelta(hours=25)).isoformat(), status_json="{}")
    db_session.add_all([recent, old_with_newer_id])
    await db_session.commit()

    await logging_service.append_device_status(
        db_session,
        _snapshot(pv=1),
        retention_hours=24,
        cleanup_interval_seconds=300,
        pruner=pruner,
    )
    assert await db_session.get(DeviceStatus, recent.id) is not None
    assert await db_session.get(DeviceStatus, old_with_newer_id.id) is None

    old_during_cooldown = DeviceStatus(ts=(now[0] - timedelta(hours=26)).isoformat(), status_json="{}")
    db_session.add(old_during_cooldown)
    await db_session.commit()
    await logging_service.append_device_status(
        db_session,
        _snapshot(pv=2),
        retention_hours=24,
        cleanup_interval_seconds=300,
        pruner=pruner,
    )
    assert await db_session.get(DeviceStatus, old_during_cooldown.id) is not None

    monotonic[0] += 301
    await logging_service.append_device_status(
        db_session,
        _snapshot(pv=3),
        retention_hours=24,
        cleanup_interval_seconds=300,
        pruner=pruner,
    )
    assert await db_session.get(DeviceStatus, old_during_cooldown.id) is None
    assert await db_session.scalar(select(func.count()).select_from(DeviceStatus)) == 4


async def test_snapshot_write_failures_raise_and_clear_visible_alarm(monkeypatch):
    """连续写库失败达到阈值后广播报警，下一次成功写入后广播清除。"""

    class FakeSessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    broadcasts: list[tuple[str, dict]] = []

    async def broadcast(msg_type, data):
        broadcasts.append((msg_type, data))

    async def fail_write(session, payload, **kwargs):
        raise RuntimeError("database is locked")

    async def successful_write(*args, **kwargs):
        return None

    monkeypatch.setattr(main_module, "get_sessionmaker", lambda: FakeSessionContext)
    monkeypatch.setattr(main_module.ws_manager, "broadcast", broadcast)
    monkeypatch.setattr(logging_service, "append_device_status", fail_write)
    sampling_health.reset()
    active_test.start("TEST-LOCK")

    try:
        for _ in range(3):
            await main_module._persist_snapshot(_snapshot())

        assert [item[0] for item in broadcasts] == ["alarm_new"]
        assert broadcasts[0][1]["alarm_code"] == "HMI-DATA-PERSISTENCE"
        assert broadcasts[0][1]["test_id"] == "TEST-LOCK"

        monkeypatch.setattr(logging_service, "append_device_status", successful_write)
        monkeypatch.setattr(logging_service, "append_sample_point", successful_write)
        monkeypatch.setattr(main_module, "advance_test_session", successful_write)
        await main_module._persist_snapshot(_snapshot())

        assert [item[0] for item in broadcasts] == ["alarm_new", "alarm_clear"]
        assert broadcasts[-1][1]["alarm_id"] == "HMI-DATA-PERSISTENCE"
    finally:
        active_test.stop()
        sampling_health.reset()


async def test_explicit_safe_end_requires_cool_temperature_and_records_final_sample(db_session):
    from app.services.test_session_service import advance_test_session

    row = TestSession(test_id="SAFE-END", operator_id="op", start_time="2026-09-05T00:00:00Z")
    db_session.add(row)
    await db_session.commit()
    active_test.start(row.test_id)
    snapshot = {
        "_hostcomm": {"capabilities": ["run_lifecycle_v1", "measurement_events_v1"]},
        "state_machine": {"test_id": row.test_id, "measurement_complete": True, "safe_complete": True},
        "measurement": {"burden_temp_deg_c": 220, "burden_temp_valid": True},
    }
    await advance_test_session(db_session, row.test_id, snapshot)
    assert row.measurement_completed_at is not None
    assert row.end_time is None
    snapshot["measurement"]["burden_temp_deg_c"] = 199
    await logging_service.append_sample_point(db_session, row.test_id, snapshot)
    await advance_test_session(db_session, row.test_id, snapshot)
    assert row.end_time is not None
    assert row.safety_completed_at is not None
    assert active_test.active_test_id is None
    assert await db_session.scalar(select(func.count()).select_from(SamplePoint)) == 1


async def test_legacy_end_name_does_not_certify_completion(db_session):
    from app.services.test_session_service import advance_test_session

    row = TestSession(test_id="LEGACY-END", operator_id="op", start_time="2026-09-05T00:00:00Z")
    db_session.add(row)
    await db_session.commit()
    active_test.start(row.test_id)
    await advance_test_session(
        db_session,
        row.test_id,
        {"state_machine": {"test_id": row.test_id, "current_state": "Complete", "safe_complete": True}},
    )
    assert row.end_time is None


async def test_sample_keeps_transport_evidence_and_unknown_quality(db_session):
    snapshot = _snapshot()
    snapshot["_hostcomm"] = {"device_timestamp": "2026-09-05T00:00:00Z", "received_at": "2026-09-05T00:00:02Z"}
    await logging_service.append_sample_point(db_session, "RAW", snapshot)
    sample = (await db_session.execute(select(SamplePoint))).scalar_one()
    assert sample.burden_temp_v == -1
    assert sample.delta_p_v == -1
    assert "device_timestamp" in sample.ext_json
    assert sample.ts == "2026-09-05T00:00:02+00:00"
