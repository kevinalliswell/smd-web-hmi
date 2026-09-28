"""A device session reset between awaits must never let one session's checks authorize the next."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.hostcomm.client import HostCommNotConnectedError
from app.hostcomm.v2_client import V2Client
from app.services.command_service import CommandError

CONTROLLER = "c" * 32
SESSION, NEXT_SESSION = "s" * 32, "n" * 32


def status_frame(session_id=SESSION, *, run_id="r" * 32, state="complete"):
    return {
        "session_id": session_id,
        "boot_id": "b" * 32,
        "payload": {
            "lease_id": "l" * 32,
            "lease_owner_controller_id": CONTROLLER,
            "lease_owner_session_id": session_id,
            "active_recipe_digest": "d" * 64,
            "run": {"run_id": run_id, "state": state, "state_revision": "7", "fault_revision": "0"},
        },
    }


@pytest.fixture
async def client():
    client = V2Client(
        "localhost",
        1,
        factory=None,
        device_id="1" * 32,
        controller_id=CONTROLLER,
        controller_epoch="2" * 32,
        client_version="test",
    )
    client.transport = SimpleNamespace(
        is_online=True,
        close=AsyncMock(),
        session_id=SESSION,
        boot_id="b" * 32,
        lease_id="l" * 32,
        hello_payload={"granted_role": "control"},
        lease_evidence=lambda: {"valid": True},
    )
    accepted = {"status": "accepted", "reason": "ok", "operation_id": "o" * 32, "command_seq": "1"}
    client.operations = SimpleNamespace(
        get=AsyncMock(return_value=None),
        submit=AsyncMock(return_value=accepted),
        acquire_lease=AsyncMock(),
        confirm_owned_lease=AsyncMock(return_value=True),
    )
    client.refresh_operations = AsyncMock()
    client.get_status = AsyncMock()
    client._ready = True
    client._status_frame = status_frame()
    yield client
    await client.close()


async def reset_session(client, *, reconnect):
    await client._on_connection({"status": "offline", "reason": "test"})
    if reconnect:
        # The next session finished its own recovery before the suspended command resumed.
        client.transport.session_id = NEXT_SESSION
        client._status_frame = status_frame(NEXT_SESSION)
        client._ready = True


def reset_on_status_read(client, number, *, reconnect):
    reads = 0

    async def status_read():
        nonlocal reads
        reads += 1
        if reads == number:
            await reset_session(client, reconnect=reconnect)
        return {"system": {"can_ack_run": True}}

    return status_read


async def test_unchanged_session_still_submits_the_command(client):
    result = await client.send_command("ack_run", {}, operator_id="a", role="operator")
    assert result["result"] == "accepted"
    submitted = client.operations.submit.await_args
    assert submitted.args == ("ack_run", {"run_id": "r" * 32})
    assert submitted.kwargs["state_revision"] == "7"


@pytest.mark.parametrize("reconnect", [False, True])
async def test_lease_check_refuses_a_session_reset_during_its_status_read(client, reconnect):
    client.get_status = reset_on_status_read(client, 1, reconnect=reconnect)
    with pytest.raises(CommandError) as refused:
        await client.send_command("ack_run", {}, operator_id="a", role="operator")
    assert refused.value.error_code == "device_recovery_pending"
    client.operations.confirm_owned_lease.assert_not_awaited()
    client.operations.submit.assert_not_awaited()


@pytest.mark.parametrize("reconnect", [False, True])
async def test_command_is_not_submitted_when_the_session_resets_after_lease_confirmation(client, reconnect):
    client.get_status = reset_on_status_read(client, 2, reconnect=reconnect)
    with pytest.raises(CommandError) as refused:
        await client.send_command("ack_run", {}, operator_id="a", role="operator")
    assert refused.value.error_code == "device_recovery_pending"
    client.operations.confirm_owned_lease.assert_awaited_once()
    client.operations.submit.assert_not_awaited()


async def test_start_binding_refuses_a_session_reset_during_its_parameter_read(client):
    client._status_frame = status_frame(run_id=None, state="idle")

    async def parameter_read():
        await reset_session(client, reconnect=True)
        return {"params": {}, "safety_profile": {"profile_digest": "p" * 64}}

    client.get_parameters = parameter_read
    with pytest.raises(CommandError) as refused:
        await client.send_command("start_test", {"test_id": "T-1"}, operator_id="a", role="operator")
    assert refused.value.error_code == "device_recovery_pending"
    client.operations.submit.assert_not_awaited()


async def test_recipe_activation_is_not_submitted_after_a_session_reset(client, monkeypatch):
    client._status_frame = status_frame(run_id=None, state="idle")
    compiled = SimpleNamespace(digest="e" * 64, data=b"{}")
    monkeypatch.setattr("app.hostcomm.v2_client.compile_recipe", lambda bundle, profile: compiled)
    client.get_profile = AsyncMock(return_value={"profile_digest": "p" * 64})
    client._persist_recipe_binding = AsyncMock()
    client._request = AsyncMock(return_value={"payload": {}})
    client._check_upload = lambda *args: None
    # Reads 1 and 2 belong to the lease check; read 3 follows the recipe upload.
    client.get_status = reset_on_status_read(client, 3, reconnect=True)
    with pytest.raises(CommandError) as refused:
        await client.send_command("set_parameters", {"values": {"recipe": {}}}, operator_id="a", role="admin")
    assert refused.value.error_code == "device_recovery_pending"
    client._request.assert_awaited()
    client.operations.submit.assert_not_awaited()


@pytest.mark.parametrize("reconnect", [False, True])
async def test_preflight_refuses_a_session_reset_during_its_status_read(client, reconnect):
    client._operation_ids = AsyncMock(return_value=[])
    client.get_status = reset_on_status_read(client, 1, reconnect=reconnect)
    with pytest.raises(CommandError) as refused:
        await client.preflight("ack_run")
    assert refused.value.error_code == "device_recovery_pending"


async def test_stop_reports_a_lost_session_instead_of_crashing(client):
    client._status_frame = None
    client.get_status = reset_on_status_read(client, 1, reconnect=False)
    with pytest.raises(HostCommNotConnectedError):
        await client.send_command("stop_test", {}, operator_id="a", role="operator")
    client.operations.submit.assert_not_awaited()


async def test_parameter_read_reports_a_lost_session_instead_of_crashing(client):
    async def profile_read():
        await reset_session(client, reconnect=False)
        return {"profile_digest": "p" * 64}

    client.get_profile = profile_read
    with pytest.raises(HostCommNotConnectedError):
        await client.get_parameters()


async def test_log_recovery_reports_a_lost_session_instead_of_crashing(client):
    client.get_status = reset_on_status_read(client, 1, reconnect=False)
    with pytest.raises(HostCommNotConnectedError):
        await client.recover_logs()
