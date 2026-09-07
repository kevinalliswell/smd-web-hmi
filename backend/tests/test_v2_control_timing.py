"""Control deadlines and lease evidence must not be extended by scheduling or stale replies."""

import asyncio
from types import SimpleNamespace

import pytest

from app.hostcomm import v2_transport
from app.hostcomm.v2_contract.messages import HeartbeatAck
from tests.test_hostcomm_v2_transport import transport


@pytest.mark.parametrize("lease,expiry", [(None, "9000"), ("a" * 32, None)])
def test_heartbeat_lease_identity_and_expiry_are_a_pair(lease, expiry):
    with pytest.raises(ValueError):
        HeartbeatAck.model_validate({"lease_id": lease, "lease_expires_uptime_ms": expiry, "state_revision": "1"})


async def test_heartbeat_write_and_response_share_one_deadline(monkeypatch):
    client = transport(SimpleNamespace(port=1), response_timeout=0.12)
    handles = []

    async def send(kind, payload, **kwargs):
        await asyncio.sleep(0.08)
        future = client._pending[kwargs["msg_id"]].future
        handles.append(
            asyncio.get_running_loop().call_later(0.08, lambda: None if future.done() else future.set_result({}))
        )

    monkeypatch.setattr(client, "send", send)
    try:
        with pytest.raises(TimeoutError):
            await client.request("heartbeat", {"lease_id": None})
    finally:
        for handle in handles:
            handle.cancel()


async def test_heartbeat_schedule_is_based_on_send_start_not_receipt(monkeypatch):
    client = transport(SimpleNamespace(port=1), heartbeat_interval=2)
    client._online = True
    clock, starts = [100.0], []

    async def sleep(delay):
        clock[0] += max(0, delay)

    async def request(*args, **kwargs):
        starts.append(clock[0])
        clock[0] += 0.7
        if len(starts) == 3:
            client._online = False

    monkeypatch.setattr(v2_transport.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(v2_transport.asyncio, "sleep", sleep)
    monkeypatch.setattr(client, "request", request)
    await client._heartbeats()
    assert starts == pytest.approx([102, 104, 106])


def test_actual_reconnect_wait_is_bounded_at_thirty_seconds(monkeypatch):
    client = transport(SimpleNamespace(port=1))
    monkeypatch.setattr(v2_transport.random, "uniform", lambda low, high: high)
    assert client._retry_delay(30) == 30
    assert client._retry_delay(1) == pytest.approx(1.1)


def test_only_authenticated_stable_connection_resets_retry_backoff(monkeypatch):
    client = transport(SimpleNamespace(port=1))
    monkeypatch.setattr(v2_transport.time, "monotonic", lambda: 100)
    client._connected_at = 69
    assert not client._stable_connection()
    client._valid_heartbeat = True
    assert client._stable_connection()
    client._connected_at = 71
    assert not client._stable_connection()


async def test_slow_valid_ack_does_not_add_another_interval(monkeypatch):
    client = transport(SimpleNamespace(port=1), heartbeat_interval=2)
    client._online = True
    clock, starts = [100.0], []

    async def sleep(delay):
        clock[0] += max(0, delay)

    async def request(*args, **kwargs):
        starts.append(clock[0])
        clock[0] += 2.5
        if len(starts) == 3:
            client._online = False

    monkeypatch.setattr(v2_transport.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(v2_transport.asyncio, "sleep", sleep)
    monkeypatch.setattr(client, "request", request)
    await client._heartbeats()
    assert starts == pytest.approx([102, 104.5, 107])


@pytest.mark.parametrize("remaining", [0, -1, 8001])
def test_wire_ack_rejects_invalid_advertised_lifetime_even_for_connectivity_probe(remaining):
    from app.hostcomm.v2_contract.codec import validate_message
    from tests.test_hostcomm_v2_transport import example

    ack = example("heartbeat_ack")
    ack["payload"].update(lease_id="a" * 32, lease_expires_uptime_ms=str(int(ack["uptime_ms"]) + remaining))
    with pytest.raises(ValueError):
        validate_message(ack)


async def test_brief_authenticated_flaps_keep_backoff_and_stability_resets_it(monkeypatch):
    client = transport(SimpleNamespace(port=1), reconnect_base=1)
    client._initial = asyncio.get_running_loop().create_future()
    clock, waits, openings = [100.0], [], [0]

    async def open_connection():
        openings[0] += 1
        client._connected_at = clock[0]
        client._valid_heartbeat = True
        clock[0] += 31 if openings[0] == 7 else 0.1
        client._disconnected.set()

    async def teardown():
        pass

    async def wait(delay):
        waits.append(delay)
        clock[0] += delay
        if len(waits) == 7:
            client._stopping = True

    monkeypatch.setattr(client, "_open", open_connection)
    monkeypatch.setattr(client, "_teardown", teardown)
    monkeypatch.setattr(v2_transport.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(v2_transport.random, "uniform", lambda low, high: high)
    monkeypatch.setattr(v2_transport.asyncio, "sleep", wait)
    await client._run()
    assert waits == pytest.approx([1.1, 2.2, 4.4, 8.8, 17.6, 30, 1.1])


async def test_ack_dispatch_enforces_deadline_before_renewal(monkeypatch):
    from app.hostcomm.v2_transport import Pending, V2ProtocolError
    from tests.test_hostcomm_v2_transport import example

    client = transport(SimpleNamespace(port=1))
    ack = example("heartbeat_ack")
    client.session_id, client.boot_id = ack["session_id"], ack["boot_id"]
    pending = Pending("heartbeat", {"lease_id": None}, asyncio.get_running_loop().create_future())
    pending.deadline = 99
    client._pending[ack["reply_to"]] = pending
    monkeypatch.setattr(v2_transport.time, "monotonic", lambda: 100)
    try:
        with pytest.raises(V2ProtocolError, match="deadline"):
            client._dispatch(ack)
        assert not pending.future.done()
    finally:
        pending.future.cancel()
