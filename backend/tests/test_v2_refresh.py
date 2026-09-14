"""Optional UI refreshes coalesce and remain owned by the device session."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.hostcomm.client import HostCommProtocolError, HostCommTimeoutError
from app.hostcomm.v2_client import V2Client
from app.hostcomm.v2_transport import V2CapacityError, V2RemoteError
from app.services.command_service import CommandError


def client_stub():
    client = V2Client("localhost", 1, factory=None, device_id="1" * 32, client_version="test")
    client.transport = SimpleNamespace(is_online=True, close=AsyncMock())
    return client


@pytest.mark.parametrize(
    "error",
    [
        CommandError(503, "another_error", "unrelated failure"),
        CommandError(409, "device_read_capacity", "not the local transport translation"),
        HostCommTimeoutError("unknown response outcome"),
        HostCommProtocolError("invalid response"),
        V2RemoteError({"code": "busy", "message": "device rejected request", "retryable": True}),
    ],
)
async def test_unrelated_source_failures_do_not_become_local_capacity_retries(error):
    client = client_stub()
    client.recover_logs = AsyncMock(side_effect=error)
    await client._recover_completed_sources()
    assert not client._source_recovery_pending
    client.get_status = AsyncMock(side_effect=error)
    with pytest.raises(type(error)):
        await client._optional_status()


@pytest.mark.parametrize(
    "error",
    [V2CapacityError("local queue full"), CommandError(503, "device_read_capacity", "local read slots full")],
)
async def test_source_scan_and_optional_refresh_recognize_the_same_local_capacity(error):
    client = client_stub()
    client.recover_logs = AsyncMock(side_effect=error)
    await client._recover_completed_sources()
    assert client._source_recovery_pending
    client.get_status = AsyncMock(side_effect=error)
    await client._optional_status()


async def test_refresh_burst_coalesces_and_preserves_required_status_read():
    client = client_stub()
    first, second, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = 0

    async def refresh():
        nonlocal calls
        calls += 1
        (first if calls == 1 else second).set()
        await release.wait()

    client._optional_status = refresh
    client._publish = AsyncMock()
    try:
        client._schedule_refresh(needs_status=True)
        task = client._refresh_task
        await asyncio.wait_for(first.wait(), 1)
        for _ in range(100):
            client._schedule_refresh(needs_status=False)
        client._schedule_refresh(needs_status=True)
        assert client._refresh_task is task and calls == 1
        release.set()
        await asyncio.wait_for(task, 1)
        assert second.is_set() and calls == 2
        client._publish.assert_not_awaited()
    finally:
        await client.close()


@pytest.mark.parametrize("boundary", ["close", "disconnect"])
async def test_refresh_is_drained_at_its_session_boundary(boundary):
    client = client_stub()
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def refresh():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    client._optional_status = refresh
    client._schedule_refresh(needs_status=True)
    task = client._refresh_task
    await asyncio.wait_for(entered.wait(), 1)
    client._schedule_refresh(needs_status=True)
    if boundary == "close":
        await client.close()
        client._schedule_refresh(needs_status=True)
        assert client._refresh_task is task  # Closing cannot spawn a replacement.
    else:
        await client._on_connection({"status": "offline", "reason": "test"})
        assert not client._refresh_pending and not client._ready
        await client.close()
    assert cancelled.is_set() and task.cancelled()


async def test_failed_refresh_disables_control_and_reports_recovery_requirement():
    client = client_stub()
    client._ready = True
    client._optional_status = AsyncMock(side_effect=RuntimeError("status projection failed"))
    client.on_comm_status = AsyncMock()
    client._schedule_refresh(needs_status=True)
    await asyncio.wait_for(client._refresh_task, 1)
    assert not client._ready and client.last_error == "RuntimeError"
    client.on_comm_status.assert_awaited_once_with({"status": "degraded", "reason": "v2_status_refresh_incomplete"})
    failed = client._refresh_task
    client._optional_status = AsyncMock()
    client._schedule_refresh(needs_status=True)
    assert client._refresh_task is not failed
    await asyncio.wait_for(client._refresh_task, 1)
    client._optional_status.assert_awaited_once()
    assert not client._ready  # Only full reconciliation may restore control.
    await client.close()
