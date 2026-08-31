"""报警确认与校时命令的审计完整性回归测试（issue #17）。"""

from __future__ import annotations

import json
from types import SimpleNamespace

from sqlalchemy import select

from app.api.deps import CurrentUser
from app.api.routes import alarms, system
from app.core.time import normalize_utc_iso
from app.db.models import AlarmLog, OperatorAction


def _request(client=None, host="10.20.30.40"):
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(hostcomm_client=client)),
        client=SimpleNamespace(host=host),
    )


async def test_repeated_alarm_ack_preserves_first_operator_and_time(db_session, monkeypatch):
    alarm = AlarmLog(
        alarm_code="ALM-CO-L3",
        level=3,
        occur_time="2026-08-30T10:00:00Z",
        text="CO 三级报警",
    )
    db_session.add(alarm)
    await db_session.commit()
    await db_session.refresh(alarm)

    ack_times = iter(("2026-08-30T10:01:00Z", "2026-08-30T10:02:00Z"))
    monkeypatch.setattr(alarms, "now_iso", lambda: next(ack_times))

    first = await alarms.ack_alarm(
        alarm.id,
        _request(),
        CurrentUser("operator-a", "operator"),
        db_session,
    )
    second = await alarms.ack_alarm(
        alarm.id,
        _request(),
        CurrentUser("operator-b", "operator"),
        db_session,
    )

    await db_session.refresh(alarm)
    assert alarm.ack_operator == "operator-a"
    assert normalize_utc_iso(alarm.ack_time) == "2026-08-30T10:01:00+00:00"
    assert first["data"]["ack_operator"] == "operator-a"
    assert second["data"]["ack_operator"] == "operator-a"
    assert normalize_utc_iso(second["data"]["ack_time"]) == "2026-08-30T10:01:00+00:00"


class _RecordingClient:
    is_online = True

    def __init__(self):
        self.calls = []

    async def send_command(self, command, params, *, operator_id, role, confirm_token=None):
        self.calls.append(
            {
                "command": command,
                "params": params,
                "operator_id": operator_id,
                "role": role,
                "confirm_token": confirm_token,
            }
        )
        return {"result": "accepted", "reason_code": "ok"}


async def test_sync_time_uses_real_operator_and_appends_audit(db_session):
    client = _RecordingClient()
    response = await system.sync_time(
        _request(client),
        CurrentUser("admin-a", "admin"),
        db_session,
    )

    assert response["data"]["result"] == "accepted"
    assert client.calls[0]["command"] == "sync_time"
    assert client.calls[0]["operator_id"] == "admin-a"
    assert client.calls[0]["role"] == "admin"

    action = (await db_session.execute(select(OperatorAction))).scalar_one()
    assert action.action_type == "sync_time"
    assert action.operator_id == "admin-a"
    assert action.operator_role == "admin"
    assert action.client_ip == "10.20.30.40"
    assert json.loads(action.params_json)["timestamp"] == response["data"]["sent_time"]
