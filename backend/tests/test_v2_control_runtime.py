"""Real TCP lease coordination, with receipt delivery controlled at an explicit boundary."""

import asyncio

import pytest

from app.hostcomm.v2_transport import V2TransportError
from tests.test_v2_client import connected  # noqa: F401


async def test_release_waits_for_inflight_renewal_and_does_not_resume_it(connected, monkeypatch):
    client, sim, factory = connected
    result = await client.operations.acquire_lease(actor="admin", role="admin")
    assert result["status"] == "applied"
    lease_id = client.transport.lease_id
    assert lease_id
    client.transport._heartbeat_task.cancel()
    await asyncio.gather(client.transport._heartbeat_task, return_exceptions=True)
    arrived, held = asyncio.Event(), []
    dispatch = client.transport._dispatch

    def hold(frame):
        if frame["type"] == "heartbeat_ack":
            held.append(frame)
            arrived.set()
        else:
            dispatch(frame)

    monkeypatch.setattr(client.transport, "_dispatch", hold)
    heartbeat = asyncio.create_task(client.transport.request("heartbeat", {"lease_id": lease_id}))
    release = None
    try:
        await asyncio.wait_for(arrived.wait(), 1)
        revision = sim.state.run["state_revision"]
        release = asyncio.create_task(
            client.operations.release_lease(actor="admin", role="admin", lease_id=lease_id, state_revision=revision)
        )
        await asyncio.sleep(0)
        assert client.transport.leases.renewals_paused
        assert not release.done()
        assert sim.state.data["lease"]["id"] == lease_id
        dispatch(held.pop())
        await heartbeat
        result = await asyncio.wait_for(release, 3)
        assert result["status"] == "applied"
        assert client.transport.is_online
        assert client.transport.lease_id is None
        assert sim.state.data["lease"] is None
    finally:
        for task in (heartbeat, release):
            if task:
                task.cancel()
        await asyncio.gather(*(task for task in (heartbeat, release) if task), return_exceptions=True)


async def test_release_lost_receipt_keeps_unknown_and_revokes_connection(connected):
    client, sim, factory = connected
    await client.operations.acquire_lease(actor="admin", role="admin")
    lease = client.transport.lease_id
    sim.drop_reply("command_result")
    client.transport.response_timeout = 0.15
    row = await client.operations.release_lease(
        actor="admin", role="admin", lease_id=lease, state_revision=sim.state.run["state_revision"]
    )
    assert row["status"] == "unknown"
    assert sim.state.data["lease"] is None
    assert not client.transport.is_online
    assert client.transport.lease_id is None
    assert (await client.operations.get(row["operation_id"]))["status"] == "unknown"


async def test_expired_lease_is_checked_again_after_waiting_for_writer(connected):
    client, sim, factory = connected
    await client.operations.acquire_lease(actor="admin", role="admin")
    from app.hostcomm.v2_contract.codec import command_digest
    from tests.test_hostcomm_v2_transport import example

    payload = example("command")["payload"]
    payload.update(expected_boot_id=client.transport.boot_id, lease_id=client.transport.lease_id)
    payload["request_digest"] = command_digest(payload)
    await client.transport._write_lock.acquire()
    task = asyncio.create_task(client.transport.request("command", payload))
    try:
        await asyncio.sleep(0)
        client.transport.leases.expires_at = 0
        client.transport._write_lock.release()
        with pytest.raises(V2TransportError, match="lease"):
            await task
        assert sim.state.run["run_id"] is None
    finally:
        if client.transport._write_lock.locked():
            client.transport._write_lock.release()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_cancelling_release_while_renewal_is_pending_revokes_without_sending_release(connected):
    client, sim, factory = connected
    await client.operations.acquire_lease(actor="admin", role="admin")
    lease_id = client.transport.lease_id
    client.transport._heartbeat_task.cancel()
    await asyncio.gather(client.transport._heartbeat_task, return_exceptions=True)
    sim.drop_reply("heartbeat_ack")
    heartbeat = asyncio.create_task(client.transport.request("heartbeat", {"lease_id": lease_id}))
    release = None
    try:
        async with asyncio.timeout(1):
            while sim._drop_replies["heartbeat_ack"]:
                await asyncio.sleep(0.001)
        release = asyncio.create_task(
            client.operations.release_lease(
                actor="admin", role="admin", lease_id=lease_id, state_revision=sim.state.run["state_revision"]
            )
        )
        await asyncio.sleep(0)
        assert client.transport.leases.renewals_paused
        release.cancel()
        with pytest.raises(asyncio.CancelledError):
            await release
        assert not client.transport.is_online
        assert client.transport.lease_id is None
        from sqlalchemy import select

        from app.db.v2_models import V2Operation

        async with factory() as db:
            assert not await db.scalar(select(V2Operation).where(V2Operation.command == "release_lease"))
    finally:
        for task in (heartbeat, release):
            if task:
                task.cancel()
        await asyncio.gather(*(task for task in (heartbeat, release) if task), return_exceptions=True)


async def test_new_revision_ack_schedules_refresh_without_waiting_for_source_callbacks(connected, monkeypatch):
    client, sim, factory = connected
    gate, callback_started, refresh = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def slow_source(_frame):
        callback_started.set()
        await gate.wait()

    def schedule(*, needs_status):
        assert needs_status
        refresh.set()

    monkeypatch.setattr(client.transport, "on_message", slow_source)
    monkeypatch.setattr(client, "_schedule_refresh", schedule)
    client.transport._callbacks.put_nowait({"test": "blocked source callback"})
    try:
        await asyncio.wait_for(callback_started.wait(), 1)
        revision = int(client._status_frame["payload"]["run"]["state_revision"]) + 1
        sim.state.run["state_revision"] = str(revision)
        await client.transport.request("heartbeat", {"lease_id": None})
        assert refresh.is_set()
        assert not gate.is_set()
        assert client.transport.leases.minimum_revision == revision
    finally:
        gate.set()
        await client.transport._callbacks.join()


@pytest.mark.parametrize(
    "review,replay,may_ack", [("bound", "pending", True), ("pending", "complete", False), ("bound", "conflict", False)]
)
async def test_safe_run_ack_is_separate_from_report_replay_completion(connected, monkeypatch, review, replay, may_ack):
    from app.services import v2_run_recovery
    from tests.test_hostcomm_v2_transport import example

    client, sim, factory = connected
    frame = client._status_frame
    run = example("status_snapshot")["payload"]["run"]
    run.update(
        state="completed",
        safe_complete=True,
        safety_profile_digest=client.profile["profile_digest"],
        safe_boundary={"boot_id": frame["boot_id"], "sample_seq": "99"},
        fault_revision="0",
    )
    frame["payload"]["run"] = run

    async def summary(*args):
        return {"review_state": review, "replay_status": replay}

    monkeypatch.setattr(v2_run_recovery, "recovery_for_run", summary)
    snapshot = await client._publish()
    assert snapshot["system"]["can_ack_run"] is may_ack
    assert snapshot["system"]["run_recovery_required"]


@pytest.mark.parametrize("kind", ["recipe_begin", "recipe_chunk"])
@pytest.mark.parametrize("change", ["release", "expired", "generation", "new_revision"])
async def test_queued_recipe_writes_recheck_lease_context(connected, kind, change, monkeypatch):
    client, sim, factory = connected
    await client.operations.acquire_lease(actor="admin", role="admin")
    begin = {
        "transfer_id": "e" * 32,
        "lease_id": client.transport.lease_id,
        "recipe_digest": "f" * 64,
        "byte_length": 100,
    }
    if kind == "recipe_chunk":
        await client.transport.request("recipe_begin", begin)
    payload = begin if kind == "recipe_begin" else {"transfer_id": "e" * 32, "offset": 0, "data_b64": "e30="}
    handled = []
    original = sim._dispatch

    def observe(*args, **kwargs):
        handled.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(sim, "_dispatch", observe)
    await client.transport._write_lock.acquire()
    task = asyncio.create_task(client.transport.request(kind, payload))
    try:
        await asyncio.sleep(0)
        if change == "release":
            client.transport.leases.renewals_paused = True
        elif change == "expired":
            client.transport.leases.expires_at = 0
        elif change == "new_revision":
            client.transport.leases.minimum_revision = client.transport.leases.status_revision + 1
        else:
            client.transport.leases.generation += 1
        client.transport._write_lock.release()
        with pytest.raises(V2TransportError):
            await task
        assert not handled
    finally:
        if client.transport._write_lock.locked():
            client.transport._write_lock.release()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("kind", ["recipe_begin", "recipe_chunk"])
async def test_recipe_upload_cannot_rebind_another_lease_context(connected, kind):
    client, sim, factory = connected
    await client.operations.acquire_lease(actor="admin", role="admin")
    begin = {
        "transfer_id": "e" * 32,
        "lease_id": client.transport.lease_id,
        "recipe_digest": "f" * 64,
        "byte_length": 100,
    }
    if kind == "recipe_begin":
        begin["lease_id"] = "a" * 32
        payload = begin
    else:
        await client.transport.request("recipe_begin", begin)
        client.transport.leases.generation += 1
        payload = {"transfer_id": "e" * 32, "offset": 0, "data_b64": "e30="}
    with pytest.raises(V2TransportError, match="lease|ownership"):
        await client.transport.request(kind, payload)
    assert sim.state.data["active_recipe"] is None
