"""HostComm 异常映射与受控命令审计（审查 #13 之 4、#17 之 2）。

要点：
- 控制板不响应时必须返回带 error_code 的规范错误体，而非裸 500（CLAUDE.md §5）。
- sync_time 会改写控制板时钟（此后所有设备侧日志的时间基准），必须留审计。
- set_parameters 回读失败也要写 operator_action（安全红线 8：整个流程记录入库）。
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.security import create_access_token
from app.db.models import OperatorAction
from app.hostcomm.client import HostCommNotConnectedError, HostCommTimeoutError
from app.main import create_app
from app.services.cache import status_cache


class FakeClient:
    """在线的假 HostComm 客户端；send_command / get_parameters 行为可注入。"""

    def __init__(self, *, command_exc=None, command_result=None, readback_exc=None):
        self.is_online = True
        self.comm_quality = "online"
        self._command_exc = command_exc
        self._command_result = command_result or {"result": "accepted", "reason_code": None}
        self._readback_exc = readback_exc
        self.sent: list[tuple[str, dict, str]] = []

    async def send_command(self, command, params, *, operator_id, role, confirm_token=None):
        self.sent.append((command, params, operator_id))
        if self._command_exc is not None:
            raise self._command_exc
        return self._command_result

    async def get_parameters(self):
        if self._readback_exc is not None:
            raise self._readback_exc
        return {"params": {}, "parameter_crc": "0000"}


def _app_with(client, db_session):
    """构造应用：注入假 HostComm 客户端，并把 DB 依赖指向测试会话（审计需写库）。"""
    from app.api import deps

    app = create_app()
    app.state.hostcomm_client = client

    async def _override_db():
        yield db_session

    app.dependency_overrides[deps.get_db] = _override_db
    return app


async def _client_for(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _auth(role: str = "admin"):
    token, _ = create_access_token("tester", role)
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------- 异常映射
@pytest.mark.parametrize(
    ("exc", "expected_status", "expected_code"),
    [
        (HostCommTimeoutError("命令 sync_time 超时（3s）"), 504, "device_timeout"),
        (HostCommNotConnectedError("connection closed"), 503, "device_comm_fault"),
    ],
)
async def test_hostcomm_exceptions_map_to_structured_errors(exc, expected_status, expected_code, db_session):
    """控制板超时/断链返回规范错误体，而不是裸 500（审查 #13 之 4）。"""
    await status_cache.update({"system": {"current_state": "Standby"}})
    app = _app_with(FakeClient(command_exc=exc), db_session)
    async with await _client_for(app) as ac:
        resp = await ac.post("/api/system/sync-time", headers=_auth())
    assert resp.status_code == expected_status
    assert resp.json()["error_code"] == expected_code


# ---------------------------------------------------- sync_time 审计
async def test_sync_time_writes_audit_with_real_operator(db_session):
    """sync_time 走 CommandService：写 operator_action，且记录真实操作员。"""
    await status_cache.update({"system": {"current_state": "Standby"}})
    fake = FakeClient()
    app = _app_with(fake, db_session)
    async with await _client_for(app) as ac:
        resp = await ac.post("/api/system/sync-time", headers=_auth())
    assert resp.status_code == 200

    rows = (
        (await db_session.execute(select(OperatorAction).where(OperatorAction.action_type == "sync_time")))
        .scalars()
        .all()
    )
    assert rows, "sync_time 未写入 operator_action"
    assert rows[-1].operator_id == "tester", "审计里应是真实操作员而非硬编码 system"
    # 下发帧中的身份同样不得是硬编码 "system"
    assert fake.sent and fake.sent[-1][2] == "tester"


# ---------------------------------------------------- 参数回读失败审计
async def test_set_parameters_readback_timeout_is_audited(db_session):
    """回读超时也要写 operator_action（红线 8：整个流程记录入库）。"""
    from app.services.command_service import compute_param_crc
    from app.services.parameter_service import ParameterService

    await status_cache.update({"system": {"current_state": "Standby"}})
    client = FakeClient(readback_exc=HostCommTimeoutError("等待 parameters_snapshot 超时（3s）"))
    service = ParameterService(client, status_cache)

    values = {"furnace_sv_deg_c": 1580}
    with pytest.raises(HostCommTimeoutError):
        await service.set_parameters(
            values,
            compute_param_crc(values),
            operator_id="tester",
            role="admin",
            db_session=db_session,
        )

    rows = (await db_session.execute(select(OperatorAction))).scalars().all()
    reasons = [r.reason_code for r in rows]
    assert "parameter_readback_failed" in reasons, f"回读失败未写审计：{reasons}"
