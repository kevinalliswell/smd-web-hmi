"""Real loopback protocol tests against an inert, durable software device."""

import asyncio
import hashlib
import ssl
from uuid import uuid4

import pytest

from app.hostcomm.v2_contract.codec import (
    canonical_bytes,
    command_digest,
    encode_message,
    split_chunks,
    strict_loads,
    validate_log_transfer,
    validate_message,
)
from app.hostcomm.v2_simulator import V2Simulator, synthetic_profile, synthetic_recipe


class Peer:
    def __init__(self, simulator):
        self.simulator = simulator
        self.session_id = None
        self.boot_id = None
        self.seq = 0
        self.lease_id = None

    async def connect(self, **kwargs):
        self.reader, self.writer = await asyncio.open_connection(*self.simulator.address, **kwargs)
        p = self.simulator.pairing
        ack = await self.request(
            "hello",
            {
                "design_revision": "2.0-design.1",
                "controller_id": p.controller_id,
                "controller_epoch": p.controller_epoch,
                "expected_device_id": p.device_id,
                "client_name": "simulator-test",
                "client_version": "test",
            },
        )
        self.session_id, self.boot_id = ack["session_id"], ack["boot_id"]
        self.seq = int(ack["payload"]["last_command_seq"])
        return ack

    def frame(self, kind, payload):
        return {
            "protocol_version": "2.0",
            "msg_id": uuid4().hex,
            "reply_to": None,
            "session_id": self.session_id,
            "boot_id": self.boot_id,
            "timestamp": None,
            "uptime_ms": "0",
            "type": kind,
            "payload": payload,
        }

    async def request(self, kind, payload):
        frame = self.frame(kind, payload)
        self.writer.write(encode_message(frame))
        await self.writer.drain()
        return await self.response(frame["msg_id"])

    async def response(self, request_id):
        while True:
            raw = await asyncio.wait_for(self.reader.readline(), 1)
            assert raw, "connection ended before response"
            msg = validate_message(strict_loads(raw[:-1])).model_dump(mode="python")
            if msg["reply_to"] == request_id:
                return msg

    async def command(self, name, params):
        status = (await self.request("get_status", {}))["payload"]
        self.seq += 1
        unguarded = name in {"acquire_lease", "stop_run"}
        p = {
            "operation_id": uuid4().hex,
            "controller_epoch": self.simulator.pairing.controller_epoch,
            "command_seq": str(self.seq),
            "lease_id": None if unguarded else self.lease_id,
            "expected_boot_id": self.boot_id,
            "expected_state_revision": None if unguarded else status["run"]["state_revision"],
            "command": name,
            "params": params,
        }
        p["request_digest"] = command_digest(p)
        self.last_command = p
        reply = await self.request("command", p)
        if name == "acquire_lease" and reply["type"] == "command_result":
            self.lease_id = reply["payload"]["lease_id"]
        return p, reply

    async def close(self):
        self.writer.close()
        await self.writer.wait_closed()


async def test_reboot_resets_automatic_sampling_schedule_and_next_start(tmp_path):
    clock = [0]
    device = V2Simulator(
        tmp_path / "reboot-sampling.sqlite",
        test_plaintext=True,
        clock=lambda: clock[0],
        profile=synthetic_profile(approved=True),
        auto_sample=True,
    )

    async def wait_for(predicate):
        async with asyncio.timeout(3):
            while not predicate():
                await asyncio.sleep(0.01)

    peer = None
    try:
        await device.start()
        await asyncio.sleep(0)  # Let the periodic sampler establish its first boot's schedule.
        clock[0] = 180000
        await wait_for(lambda: int(device.state.data["sample_seq"]) >= 2)
        previous_boot = device.boot_id
        await device.reboot()
        assert device.now() == 0 and device.boot_id != previous_boot
        peer = Peer(device)
        await peer.connect()
        run_id, _ = await running(peer, tick=False)
        assert device.state.run["state"] == "preparing"
        clock[0] += device.sample_period_ms
        # No private tick or resent start: the first new-boot scheduled sample
        # must apply the accepted command without waiting 180 seconds to catch up.
        await wait_for(lambda: device.state.run["state"] == "measuring")
        assert device.state.run["run_id"] == run_id
        assert device.state.run["measurement_start"]["boot_id"] == device.boot_id
        assert device.now() == device.sample_period_ms
    finally:
        if peer:
            await peer.close()
        await device.close()


@pytest.fixture
async def device(tmp_path):
    sim = V2Simulator(
        storage_path=tmp_path / "device.sqlite", test_plaintext=True, profile=synthetic_profile(approved=True)
    )
    await sim.start()
    yield sim
    await sim.close()


async def test_handshake_status_does_not_allocate_source_samples(device):
    peer = Peer(device)
    ack = await peer.connect()
    assert set(ack["payload"]["capabilities"]) == {"durable_operations", "atomic_recipe", "sample_log", "alarm_log"}
    first = (await peer.request("get_status", {}))["payload"]
    second = (await peer.request("get_status", {}))["payload"]
    assert first["latest_sample"] == second["latest_sample"]
    assert first["run"]["state"] == "idle"
    await peer.close()


async def test_lease_replay_is_durable_and_eviction_never_reexecutes(device):
    peer = Peer(device)
    await peer.connect()
    command, first = await peer.command("acquire_lease", {"lease_ms": 8000})
    assert first["payload"]["status"] == "applied"
    duplicate = await peer.request("command", command)
    assert duplicate["payload"] == first["payload"]
    device.evict_result(command["controller_epoch"], command["command_seq"])
    expired = await peer.request("command", command)
    assert expired["payload"]["status"] == "result_expired"
    assert expired["payload"]["lease_id"] is None
    await peer.close()


def test_simulator_requires_explicit_inert_loopback_transport(tmp_path):
    with pytest.raises(ValueError):
        V2Simulator(storage_path=tmp_path / "a.sqlite")
    with pytest.raises(ValueError):
        V2Simulator(storage_path=tmp_path / "a.sqlite", host="0.0.0.0", test_plaintext=True)


async def deployed(peer, recipe=None):
    await peer.command("acquire_lease", {"lease_ms": 8000})
    raw = canonical_bytes(recipe or synthetic_recipe(peer.simulator.profile["profile_digest"]))
    digest = hashlib.sha256(raw).hexdigest()
    transfer = uuid4().hex
    await peer.request(
        "recipe_begin",
        {"transfer_id": transfer, "lease_id": peer.lease_id, "recipe_digest": digest, "byte_length": len(raw)},
    )
    offset = 0
    for chunk in split_chunks(raw):
        ack = await peer.request("recipe_chunk", {"transfer_id": transfer, "offset": offset, "data_b64": chunk})
        offset = ack["payload"]["next_offset"]
    assert ack["payload"]["status"] == "validated"
    _, active = await peer.command(
        "activate_recipe", {"transfer_id": transfer, "recipe_digest": digest, "expected_active_digest": None}
    )
    assert active["payload"]["status"] == "applied"
    readback = await peer.request("get_recipe", {"recipe_digest": digest, "offset": 0})
    import base64

    assert base64.b64decode(readback["payload"]["data_b64"]) == raw
    return digest


async def running(peer, *, tick=True, recipe=None):
    digest = await deployed(peer, recipe)
    run_id = uuid4().hex
    command, reply = await peer.command(
        "start_run",
        {"run_id": run_id, "recipe_digest": digest, "safety_profile_digest": peer.simulator.profile["profile_digest"]},
    )
    assert reply["payload"]["status"] == "accepted"
    if tick:
        await peer.simulator.tick()
    return run_id, command


async def test_natural_end_has_original_boundaries_and_cooling_samples(device):
    peer = Peer(device)
    await peer.connect()
    run_id, _ = await running(peer)
    await device.first_drip()
    await device.finish_measurement()
    ended = (await peer.request("get_status", {}))["payload"]["run"]
    assert ended["state"] == "safe_disposal" and ended["measurement_complete"]
    assert not ended["safe_complete"]
    await device.complete_purge()
    await device.complete_cooling()
    status = (await peer.request("get_status", {}))["payload"]
    assert status["run"]["state"] == "completed"
    assert status["run"]["outcome"] == "valid_candidate"
    assert status["run"]["measurement_end"] == ended["measurement_end"]
    assert int(status["run"]["safe_boundary"]["sample_seq"]) > int(ended["measurement_end"]["sample_seq"])
    _, reply = await peer.command("ack_run", {"run_id": run_id})
    assert reply["payload"]["status"] == "applied"
    assert (await peer.request("get_status", {}))["payload"]["run"]["state"] == "idle"
    await peer.close()


@pytest.mark.parametrize("preparing", [False, True])
async def test_stop_can_cancel_reserved_start_and_does_not_end_sampling(device, preparing):
    peer = Peer(device)
    await peer.connect()
    run_id, _ = await running(peer, tick=not preparing)
    _, reply = await peer.command("stop_run", {"run_id": run_id, "reason": "operator_stop"})
    assert reply["payload"]["status"] == "applied"
    stopped = (await peer.request("get_status", {}))["payload"]
    assert stopped["run"]["state"] == "safe_disposal"
    assert stopped["run"]["outcome"] == "aborted"
    assert not stopped["run"]["measurement_complete"]
    await device.tick()
    assert (await peer.request("get_status", {}))["payload"]["latest_sample"] != stopped["latest_sample"]
    await device.complete_purge()
    await device.complete_cooling()
    assert (await peer.request("get_status", {}))["payload"]["run"]["safe_complete"]
    await peer.close()


async def test_lost_start_reply_queries_same_run_without_reexecution(device):
    peer = Peer(device)
    await peer.connect()
    recipe_digest = await deployed(peer)
    device.drop_reply("command_result")
    run_id = uuid4().hex
    with pytest.raises(asyncio.TimeoutError):
        await peer.command(
            "start_run",
            {
                "run_id": run_id,
                "recipe_digest": recipe_digest,
                "safety_profile_digest": device.profile["profile_digest"],
            },
        )
    query = {key: peer.last_command[key] for key in ("controller_epoch", "operation_id", "command_seq")}
    result = await peer.request("get_operation", query)
    assert result["payload"]["status"] == "accepted" and result["payload"]["run_id"] == run_id
    repeated = await peer.request("command", peer.last_command)
    assert repeated["payload"] == result["payload"]
    assert (await peer.request("get_status", {}))["payload"]["run"]["run_id"] == run_id
    await peer.close()


async def test_device_reboot_preserves_run_and_original_boundaries(device):
    peer = Peer(device)
    await peer.connect()
    run_id, command = await running(peer)
    old_boot = peer.boot_id
    start = (await peer.request("get_status", {}))["payload"]["run"]["measurement_start"]
    await device.reboot()
    replacement = Peer(device)
    await replacement.connect()
    status = (await replacement.request("get_status", {}))["payload"]
    assert replacement.boot_id != old_boot
    assert status["run"]["run_id"] == run_id
    assert status["run"]["measurement_start"] == start
    assert status["run"]["state"] == "safe_disposal" and status["lease_id"] is None
    assert status["run"]["outcome"] == "invalid"
    assert replacement.seq >= int(command["command_seq"])
    await replacement.close()


async def test_upload_disconnect_retains_previous_active_recipe(device):
    peer = Peer(device)
    await peer.connect()
    active = await deployed(peer)
    # A fresh connection discards the completed previous staging transaction.
    await peer.close()
    peer = Peer(device)
    await peer.connect()
    await peer.command("acquire_lease", {"lease_ms": 8000})
    raw = canonical_bytes(synthetic_recipe(device.profile["profile_digest"]))
    transfer = uuid4().hex
    await peer.request(
        "recipe_begin",
        {
            "transfer_id": transfer,
            "lease_id": peer.lease_id,
            "recipe_digest": hashlib.sha256(raw).hexdigest(),
            "byte_length": len(raw),
        },
    )
    import base64

    await peer.request(
        "recipe_chunk", {"transfer_id": transfer, "offset": 0, "data_b64": base64.b64encode(raw[:100]).decode()}
    )
    await peer.close()
    await device.reboot()
    fresh = Peer(device)
    await fresh.connect()
    assert (await fresh.request("get_status", {}))["payload"]["active_recipe_digest"] == active
    await fresh.close()


async def test_log_fixed_cut_reports_deleted_source_records(device):
    peer = Peer(device)
    await peer.connect()
    for _ in range(4):
        await device.tick()
    catalog = (await peer.request("get_status", {}))["payload"]["log"]
    device.remove_log_records([2], "storage_fault")
    query = {
        "transfer_id": uuid4().hex,
        "requested": {
            "log_id": catalog["log_id"],
            "first_record_seq": "1",
            "last_record_seq": catalog["newest_record_seq"],
        },
        "max_records": 100,
        "max_bytes": 100000,
    }
    request = peer.frame("log_request", query)
    peer.writer.write(encode_message(request))
    await peer.writer.drain()
    chunks, raw = [], b""
    import base64

    while True:
        message = await peer.response(request["msg_id"])
        if message["type"] == "log_result":
            terminal = message["payload"]
            break
        part = message["payload"]
        chunks.append(part)
        raw += base64.b64decode(part["data_b64"])
        complete = raw[: raw.rfind(b"\n") + 1].splitlines()
        committed = strict_loads(complete[-1])["record_seq"] if complete else None
        ack = {key: part[key] for key in ("transfer_id", "log_id", "chunk_index", "chunk_digest")}
        ack.update(next_offset=len(raw), committed_record_seq=committed)
        peer.writer.write(encode_message(peer.frame("log_ack", ack)))
        await peer.writer.drain()
    records = validate_log_transfer(query, chunks, terminal)
    assert terminal["status"] == "partial" and terminal["missing"][0]["first_record_seq"] == "2"
    assert len(records) == 4
    await peer.close()


async def test_unapproved_profile_rejects_activation(tmp_path):
    sim = V2Simulator(tmp_path / "unapproved.sqlite", test_plaintext=True)
    await sim.start()
    try:
        peer = Peer(sim)
        ack = await peer.connect()
        assert not ack["payload"]["control_ready"]
        await peer.command("acquire_lease", {"lease_ms": 8000})
        _, result = await peer.command(
            "activate_recipe", {"transfer_id": uuid4().hex, "recipe_digest": "1" * 64, "expected_active_digest": None}
        )
        assert result["payload"]["status"] == "rejected" and result["payload"]["reason"] == "profile_unapproved"
        await peer.close()
    finally:
        await sim.close()


async def test_consumed_rejection_waterline_survives_new_object(tmp_path):
    path = tmp_path / "persistent.sqlite"
    first = V2Simulator(path, test_plaintext=True)
    await first.start()
    peer = Peer(first)
    await peer.connect()
    await peer.command("acquire_lease", {"lease_ms": 8000})
    rejected, _ = await peer.command(
        "start_run",
        {"run_id": uuid4().hex, "recipe_digest": "f" * 64, "safety_profile_digest": first.profile["profile_digest"]},
    )
    await peer.close()
    await first.close()
    second = V2Simulator(path, test_plaintext=True)
    await second.start()
    try:
        fresh = Peer(second)
        ack = await fresh.connect()
        assert int(ack["payload"]["last_command_seq"]) >= int(rejected["command_seq"])
        query = {key: rejected[key] for key in ("controller_epoch", "operation_id", "command_seq")}
        assert (await fresh.request("get_operation", query))["payload"]["status"] == "rejected"
        await fresh.close()
    finally:
        await second.close()


async def test_all_core_alarm_occurrences_page_ack_and_clear(device):
    from typing import get_args

    from app.hostcomm.v2_contract.messages import AlarmCode

    peer = Peer(device)
    await peer.connect()
    await peer.command("acquire_lease", {"lease_ms": 8000})
    codes = get_args(AlarmCode)
    for code in codes:
        await device.raise_alarm(code)
    revision, offset, seen = None, 0, []
    while True:
        reply = await peer.request("get_alarms", {"expected_revision": revision, "page_offset": offset, "limit": 16})
        page = reply["payload"]
        revision = page["revision"]
        seen.extend(page["items"])
        if page["next_offset"] is None:
            break
        offset = page["next_offset"]
    assert {alarm["code"] for alarm in seen} == set(codes)
    alarm = seen[0]
    _, ack = await peer.command("ack_alarm", {"alarm_id": alarm["alarm_id"], "occurrence_seq": alarm["occurrence_seq"]})
    assert ack["payload"]["status"] == "applied"
    mismatch = await peer.request("get_alarms", {"expected_revision": revision, "page_offset": 0, "limit": 16})
    assert mismatch["payload"]["code"] == "state_conflict"
    await device.clear_alarm(alarm["alarm_id"])
    await peer.close()


async def test_lease_expiry_stops_and_cannot_resume_from_heartbeat(device):
    peer = Peer(device)
    await peer.connect()
    run_id, _ = await running(peer)
    await device.tick(8000)
    status = (await peer.request("get_status", {}))["payload"]
    assert status["lease_id"] is None and status["run"]["outcome"] == "aborted"
    heartbeat = await peer.request("heartbeat", {"lease_id": peer.lease_id})
    assert heartbeat["payload"]["lease_id"] is None
    assert (await peer.request("get_status", {}))["payload"]["run"]["run_id"] == run_id
    await peer.close()


async def test_stop_with_failed_storage_is_unknown_but_still_latches(device):
    peer = Peer(device)
    await peer.connect()
    run_id, _ = await running(peer)
    device.fail_storage = True
    _, stop = await peer.command("stop_run", {"run_id": run_id, "reason": "operator_stop"})
    assert stop["payload"]["status"] == "unknown" and stop["payload"]["reason"] == "storage_unavailable"
    state = (await peer.request("get_status", {}))["payload"]["run"]
    assert state["state"] == "safe_disposal" and not state["safe_complete"]
    device.fail_storage = False
    await peer.close()


async def test_stage_timeout_precedes_threshold_on_same_tick(device):
    peer = Peer(device)
    await peer.connect()
    await running(peer)
    device.set_measurements(furnace_mc=500000)
    # Keep the lease valid independently of a stage timer to isolate timeout precedence.
    device.state.data["stage_started"] = device.now() - 21600000
    await device.tick()
    run = (await peer.request("get_status", {}))["payload"]["run"]
    assert run["outcome"] == "invalid" and not run["measurement_complete"]
    await peer.close()


async def test_cooling_honors_recipe_stricter_temperature(device):
    peer = Peer(device)
    await peer.connect()
    recipe = synthetic_recipe(device.profile["profile_digest"])
    recipe["stages"][-1]["exit"]["value"] = 100000
    await running(peer, recipe=recipe)
    await device.finish_measurement()
    await device.complete_purge()
    device.set_measurements(burden_mc=150000)
    await device.tick(1000)
    await device.tick(1000)
    run = (await peer.request("get_status", {}))["payload"]["run"]
    assert not run["safe_complete"] and run["state"] == "cooling"
    await peer.close()


@pytest.mark.skipif(not getattr(ssl, "HAS_PSK", False), reason="TLS-PSK requires Python 3.13/OpenSSL")
async def test_wrong_psk_cannot_reach_hello(tmp_path):
    import secrets

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = context.maximum_version = ssl.TLSVersion.TLSv1_3
    context.set_psk_server_callback(lambda identity: key)
    context.num_tickets = 0
    key = secrets.token_bytes(32)
    sim = V2Simulator(tmp_path / "tls.sqlite", ssl_context=context)
    await sim.start()
    client = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    client.check_hostname = False
    client.verify_mode = ssl.CERT_NONE
    client.minimum_version = client.maximum_version = ssl.TLSVersion.TLSv1_3
    client.set_psk_client_callback(lambda hint: ("wrong-key", secrets.token_bytes(32)))
    try:
        with pytest.raises((ssl.SSLError, ConnectionError, asyncio.TimeoutError, AssertionError)):
            await Peer(sim).connect(ssl=client, server_hostname="localhost", ssl_handshake_timeout=1)
    finally:
        await sim.close()


async def test_fault_requires_explicit_reset_before_ack_run(device):
    peer = Peer(device)
    await peer.connect()
    run_id, _ = await running(peer)
    alarm = await device.raise_alarm("emergency_stop")
    await device.clear_alarm(alarm)
    await peer.command("ack_alarm", {"alarm_id": alarm, "occurrence_seq": "1"})
    await device.complete_purge()
    await device.complete_cooling()
    _, ack = await peer.command("ack_run", {"run_id": run_id})
    assert ack["payload"]["status"] == "rejected"
    run = (await peer.request("get_status", {}))["payload"]["run"]
    _, reset = await peer.command("reset_fault", {"fault_revision": run["fault_revision"], "reason": "test recovery"})
    assert reset["payload"]["status"] == "applied"
    _, ack = await peer.command("ack_run", {"run_id": run_id})
    assert ack["payload"]["status"] == "applied"
    await peer.close()


async def test_cool_only_recipe_never_raises_index_error(device):
    peer = Peer(device)
    await peer.connect()
    recipe = synthetic_recipe(device.profile["profile_digest"])
    recipe["stages"] = recipe["stages"][-1:]
    await running(peer, recipe=recipe)
    assert (await peer.request("get_status", {}))["payload"]["run"]["state"] == "safe_disposal"
    await peer.close()


async def test_preparing_rechecks_invalid_temperature_before_measuring(device):
    peer = Peer(device)
    await peer.connect()
    await running(peer, tick=False)
    device.set_measurements(furnace_mc={"value": None, "quality": "invalid", "age_ms": 0})
    await device.tick()
    run = (await peer.request("get_status", {}))["payload"]["run"]
    assert run["state"] == "safe_disposal" and run["measurement_start"] is None
    assert run["outcome"] == "invalid"
    await peer.close()


def test_simulator_refuses_hmi_database_without_modifying_it(tmp_path):
    import sqlite3

    path = tmp_path / "hmi.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE test_sessions (id INTEGER PRIMARY KEY)")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="dedicated"):
        V2Simulator(path, test_plaintext=True)
    assert path.read_bytes() == before


async def test_full_uint64_command_waterline_is_persisted_without_json_float_loss(device):
    peer = Peer(device)
    await peer.connect()
    peer.seq = 18446744073709551614
    _, reply = await peer.command("acquire_lease", {"lease_ms": 8000})
    assert reply["payload"]["command_seq"] == "18446744073709551615"
    await device.reboot()
    fresh = Peer(device)
    ack = await fresh.connect()
    assert ack["payload"]["last_command_seq"] == "18446744073709551615"
    await fresh.close()


async def test_safe_completion_requires_valid_safe_gas_feedback(device):
    peer = Peer(device)
    await peer.connect()
    await running(peer)
    await device.finish_measurement()
    await device.complete_purge()
    device.set_measurements(co_measured_ml_min={"value": None, "quality": "invalid", "age_ms": 0})
    await device.complete_cooling()
    run = (await peer.request("get_status", {}))["payload"]["run"]
    assert run["state"] == "cooling" and not run["safe_complete"]
    await peer.close()


async def test_downward_setpoint_ramp_does_not_jump_to_target(device):
    peer = Peer(device)
    await peer.connect()
    recipe = synthetic_recipe(device.profile["profile_digest"])
    device.set_measurements(furnace_setpoint_mc=700000)
    await running(peer, recipe=recipe)
    assert device.state.data["values"]["furnace_setpoint_mc"]["value"] == 700000
    await device.tick(1000)
    assert 699000 < device.state.data["values"]["furnace_setpoint_mc"]["value"] < 700000
    await peer.close()
