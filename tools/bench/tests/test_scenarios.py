import json
from types import SimpleNamespace

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
