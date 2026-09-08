from uuid import uuid4

import pytest
from smd_bench.contracts import DriverAction
from smd_bench.driver import BenchSimulator, DeviceActions

from app.hostcomm.v2_simulator import synthetic_profile


@pytest.mark.asyncio
async def test_driver_returns_source_receipts_and_rejects_invalid_measurements(tmp_path):
    board = BenchSimulator(tmp_path / "device.sqlite", test_plaintext=True, profile=synthetic_profile(approved=True))
    actions = DeviceActions(board)
    try:
        await board.start()
        reply = await actions.execute(DriverAction(id="point", action="sample", values={"burden_mc": 600000}))
        assert reply["sample"]["boot_id"] == board.boot_id
        assert int(reply["sample"]["sample_seq"]) > 0
        assert board.state.data["values"]["burden_mc"]["value"] == 600000
        with pytest.raises(ValueError):
            await actions.execute(DriverAction(id="bad", action="sample", values={"unknown_output": 1}))
        assert (await actions.execute(DriverAction(id="state", action="snapshot")))["run"]["state"] == "idle"
    finally:
        await board.close()


@pytest.mark.asyncio
async def test_reply_loss_matches_start_not_an_unrelated_lease_receipt(tmp_path):
    board = BenchSimulator(tmp_path / "device.sqlite", test_plaintext=True, profile=synthetic_profile(approved=True))
    try:
        board.arm_reply_loss("start_run")
        assert not board.consume_reply_loss(
            "command_result", {"type": "command", "payload": {"command": "acquire_lease"}}
        )
        assert board.consume_reply_loss(
            "command_result", {"type": "command", "payload": {"command": "start_run", "operation_id": uuid4().hex}}
        )
        assert not board.consume_reply_loss("command_result", {"type": "command", "payload": {"command": "start_run"}})
        assert len(board.fault_receipts) == 1
        assert board.fault_receipts[0]["command"] == "start_run"
    finally:
        await board.close()


@pytest.mark.asyncio
async def test_unknown_run_is_board_evidence_without_a_host_command(tmp_path):
    from app.hostcomm.v2_contract.codec import canonical_bytes, digest
    from app.hostcomm.v2_simulator import synthetic_recipe

    board = BenchSimulator(tmp_path / "device.sqlite", test_plaintext=True, profile=synthetic_profile(approved=True))
    try:
        await board.start()
        actions = DeviceActions(board)
        with pytest.raises(ValueError):
            await actions.execute(DriverAction(id="unknown", action="seed_unknown_run"))
        recipe = synthetic_recipe(board.profile["profile_digest"])
        raw = canonical_bytes(recipe)
        board.state.data["active_recipe"] = {"raw": raw.decode(), "digest": digest(recipe)}
        seeded = await actions.execute(DriverAction(id="unknown", action="seed_unknown_run"))
        assert seeded["run"]["state"] == "measuring"
        assert seeded["run"]["measurement_start"]["boot_id"] == board.boot_id
        assert seeded["wire_commands"] == []
        assert seeded["fault_receipts"][-1]["fault"] == "seed_unknown_run"
    finally:
        await board.close()
