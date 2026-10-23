import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from smd_bench.scenarios import Scenarios


@pytest.mark.asyncio
async def test_state_timeout_records_only_fixed_public_progress(monkeypatch):
    async def snapshot(_action):
        return {
            "run": {"state": "preparing", "secret": "private payload"},
            "boot_id": "a" * 32,
            "sample": {"sample_seq": "2"},
            "wire_commands": ["private request body"],
        }

    async def expire(fetch, predicate):
        assert not predicate(await fetch())
        raise TimeoutError("private timeout message")

    monkeypatch.setattr("smd_bench.scenarios.eventually", expire)
    result = {}
    scenarios = Scenarios(None, SimpleNamespace(request=snapshot), None, "b" * 32, result)
    with pytest.raises(TimeoutError):
        await scenarios.state("measuring")
    assert result["state_wait"] == {
        "expected": "measuring",
        "observed": "preparing",
        "boot_id": "a" * 32,
        "sample_seq": "2",
    }
    assert "private" not in json.dumps(result)


def test_start_diagnostic_keeps_status_but_no_body_or_unknown_reason():
    result = {}
    scenarios = Scenarios(None, None, None, "a" * 32, result)
    scenarios._record_start(
        {"result": "accepted", "wire_status": "accepted", "reason_code": "private-message", "device_result": "secret"},
        200,
    )
    assert result["last_start_response"]["result"] == "accepted"
    assert result["last_start_response"]["reason_code"] == "unavailable"
    assert "private-message" not in json.dumps(result) and "secret" not in json.dumps(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,payload,confirmed,code",
    [
        (409, {"error_code": "state_not_allowed", "message": "private"}, False, "state_not_allowed"),
        (409, {"error_code": "private-code", "token": "secret"}, False, "unavailable"),
        (200, {"data": {"device_confirmed": False, "wire_status": "unknown"}}, False, "unavailable"),
        (200, {"data": {"device_confirmed": True}}, True, "unavailable"),
    ],
)
async def test_alarm_ui_requires_board_confirmation_and_records_only_known_codes(status, payload, confirmed, code):
    from smd_bench.faults import confirm_alarm

    response = SimpleNamespace(status=status, json=AsyncMock(return_value=payload))

    class ResponseWait:
        async def __aenter__(self):
            async def reply():
                return response

            return SimpleNamespace(value=reply())

        async def __aexit__(self, *args):
            return False

    def expect_response(predicate, **kwargs):
        assert not predicate(
            SimpleNamespace(request=SimpleNamespace(method="GET"), url="http://local/api/alarms/2/ack")
        )
        assert not predicate(SimpleNamespace(request=SimpleNamespace(method="POST"), url="http://local/api/commands"))
        assert predicate(SimpleNamespace(request=SimpleNamespace(method="POST"), url="http://local/api/alarms/2/ack"))
        return ResponseWait()

    scenes = SimpleNamespace(result={}, ui=SimpleNamespace(page=SimpleNamespace(expect_response=expect_response)))
    button = SimpleNamespace(click=AsyncMock())
    if confirmed:
        await confirm_alarm(scenes, button)
    else:
        with pytest.raises(AssertionError, match="board confirmation"):
            await confirm_alarm(scenes, button)
    button.click.assert_awaited_once()
    assert scenes.result["last_alarm_ack"] == {
        "http_status": status,
        "error_code": code,
        "device_confirmed": confirmed,
    }
    assert "private" not in json.dumps(scenes.result) and "secret" not in json.dumps(scenes.result)
