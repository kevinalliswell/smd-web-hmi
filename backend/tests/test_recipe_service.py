"""配方版本、设备边界及启动前归档回归；Board为契约模拟器。"""

import copy
import json

import pytest
from sqlalchemy import select

from app.db.models import TestSession
from app.db.operation_models import Operation
from app.hostcomm.client import HostCommTimeoutError
from app.services.cache import StatusCache
from app.services.command_service import CommandError, CommandService, compute_param_crc, confirm_tokens
from app.services.parameter_service import ParameterService
from app.services.recipe_definition import standard_template
from app.services.recipe_service import RecipeService
from app.services.test_runtime import active_test

PROFILE = dict(
    version="test-profile-1",
    temperature_max_c=1650,
    ramp_max_c_min=15,
    n2_max_l_min=10,
    co_max_l_min=3,
    co_min_furnace_c=500,
    safe_end_burden_c=200,
    max_stages=64,
    rules_reference="simulation-only-reviewed-rules",
)


class Board:
    is_online = True
    capabilities = ["command", "status_snapshot", "recipe_v1"]
    hello_ack = {"protocol_version": "1.0", "payload": {"fw_version": "test-only"}}

    def __init__(self):
        self.values = {"process": {"end_temp_deg_c": 1580}}
        self.sent = []
        self.profile = copy.deepcopy(PROFILE)
        self.mismatch = False
        self.timeout = False
        self.db = None
        self.start_row = None

    async def get_parameters(self):
        values = copy.deepcopy(self.values)
        if self.mismatch and self.sent:
            values["changed"] = True
        return {"params": values, "safety_profile": self.profile, "fw_version": "test-only"}

    async def send_command(self, command, params, **kwargs):
        if command == "start_test":
            async with self.db.bind.connect() as connection:
                self.start_row = (
                    await connection.execute(
                        select(TestSession.recipe_snapshot_json, TestSession.mode).where(
                            TestSession.test_id == params["test_id"]
                        )
                    )
                ).one()
        self.sent.append((command, params))
        if self.timeout:
            raise HostCommTimeoutError("ack lost")
        if command == "set_parameters":
            self.values = copy.deepcopy(params["values"])
        return {"result": "accepted", "command": command}


async def ready():
    cache = StatusCache()
    await cache.update({"state_machine": {"current_state": "Standby"}})
    return cache


async def saved(db):
    return await RecipeService(db).save(standard_template(), operator_id="admin", role="admin")


async def test_recipe_versions_are_append_only(db_session):
    first = await saved(db_session)
    revised = standard_template().model_dump()
    revised.update(mode="custom", name="Changed")
    revised["stages"][0]["ramp_c_min"] = 8
    service = RecipeService(db_session)
    second = await service.save(revised, recipe_id=first["recipe_id"], operator_id="admin", role="admin")
    assert second["version"] == 2
    original = await service.get(first["recipe_id"], 1)
    assert original["digest"] == first["digest"]
    assert original["definition"]["mode"] == "standard"


async def test_activation_missing_capability_and_tampered_profile_do_not_send(db_session):
    recipe = await saved(db_session)
    board = Board()
    service = RecipeService(db_session, board, await ready())
    board.capabilities = []
    with pytest.raises(CommandError):
        await service.activate(recipe["recipe_id"], 1, operator_id="a", role="admin", operation_id="capless")
    board.capabilities = ["recipe_v1"]
    board.profile["co_min_furnace_c"] = 600
    with pytest.raises(CommandError):
        await service.activate(recipe["recipe_id"], 1, operator_id="a", role="admin", operation_id="too-hot")
    assert not board.sent


@pytest.mark.parametrize("bad", ["unknown_recipe", "digest", "safety_profile"])
async def test_generic_parameters_cannot_bypass_recipe_or_profile_validation(db_session, bad):
    recipe = await saved(db_session)
    values = {"recipe": {k: recipe[k] for k in ("recipe_id", "version", "digest", "definition")}}
    if bad == "unknown_recipe":
        values["recipe"]["recipe_id"] = "not-saved"
    elif bad == "digest":
        values["recipe"]["digest"] = "0" * 64
    else:
        values["safety_profile"] = PROFILE
    board = Board()
    with pytest.raises(CommandError):
        await CommandService(board, await ready()).execute(
            "set_parameters",
            {"values": values, "param_crc": compute_param_crc(values)},
            operator_id="a",
            role="admin",
            db_session=db_session,
        )
    assert not board.sent


async def test_activation_readback_mismatch_keeps_unknown_operation(db_session):
    recipe, board = await saved(db_session), Board()
    board.mismatch = True
    with pytest.raises(CommandError) as error:
        await RecipeService(db_session, board, await ready()).activate(
            recipe["recipe_id"], 1, operator_id="a", role="admin", operation_id="mismatch"
        )
    assert error.value.error_code == "parameter_readback_mismatch"
    assert (await db_session.get(Operation, "mismatch")).status == "unknown"


async def test_start_binds_recipe_before_wire_and_timeout_keeps_session(db_session):
    recipe, board, cache = await saved(db_session), Board(), await ready()
    await RecipeService(db_session, board, cache).activate(
        recipe["recipe_id"], 1, operator_id="a", role="admin", operation_id="activate"
    )
    board.db, board.timeout = db_session, True
    params = {"test_id": "BOUND", "original_height_mm": 40, "recipe_id": recipe["recipe_id"], "recipe_version": 1}
    service = CommandService(board, cache)
    try:
        with pytest.raises(HostCommTimeoutError):
            await service.execute(
                "start_test",
                params,
                operator_id="a",
                role="operator",
                confirm_token=confirm_tokens.issue(),
                db_session=db_session,
                operation_id="start",
            )
        assert board.start_row.mode == "standard"
        assert json.loads(board.start_row.recipe_snapshot_json)["digest"] == recipe["digest"]
        row = await db_session.scalar(select(TestSession).where(TestSession.test_id == "BOUND"))
        assert row.end_time is None
        assert row.phase in {"awaiting_device", "needs_review"}
        retry = await service.execute(
            "start_test", params, operator_id="a", role="operator", db_session=db_session, operation_id="start"
        )
        assert retry["operation_status"] == "unknown"
        assert [command for command, _ in board.sent].count("start_test") == 1
    finally:
        active_test.stop()


async def test_start_refuses_different_selected_version_and_preserves_metadata_locally(db_session):
    recipe, board, cache = await saved(db_session), Board(), await ready()
    await RecipeService(db_session, board, cache).activate(
        recipe["recipe_id"], 1, operator_id="a", role="admin", operation_id="activate"
    )
    board.db = db_session
    params = {"test_id": "LOCAL", "original_height_mm": 40, "recipe_id": recipe["recipe_id"], "recipe_version": 2}
    service = CommandService(board, cache)
    with pytest.raises(CommandError) as error:
        await service.execute(
            "start_test",
            params,
            operator_id="a",
            role="operator",
            confirm_token=confirm_tokens.issue(),
            db_session=db_session,
        )
    assert error.value.error_code == "active_recipe_mismatch"
    assert len(board.sent) == 1
    params.update(recipe_version=1, sample_metadata={"batch": "lab-1"}, report_context={"laboratory_name": "test"})
    try:
        await service.execute(
            "start_test",
            params,
            operator_id="a",
            role="operator",
            confirm_token=confirm_tokens.issue(),
            db_session=db_session,
        )
        wire = board.sent[-1][1]
        assert set(wire) == {"test_id", "expected_recipe"}
        assert wire["expected_recipe"] == {k: recipe[k] for k in ("recipe_id", "version", "digest")}
        row = await db_session.scalar(select(TestSession).where(TestSession.test_id == "LOCAL"))
        basis = json.loads(row.measurement_basis_json)
        assert basis["sample_metadata"] == {"batch": "lab-1"}
        assert basis["report_context"] == {"laboratory_name": "test"}
    finally:
        active_test.stop()


def test_recipe_cannot_co_hold_below_device_temperature_or_unsafe_end():
    from app.services.recipe_definition import RecipeDefinition, validate_for_device

    recipe = standard_template().model_dump()
    recipe["mode"] = "custom"
    recipe["stages"][4]["furnace_target_c"] = 400
    verdict = validate_for_device(RecipeDefinition.model_validate(recipe), PROFILE, ["recipe_v1"])
    assert "stage_5:co_target_below_required_temperature" in verdict["errors"]
    profile = {**PROFILE, "safe_end_burden_c": 600}
    assert not validate_for_device(standard_template(), profile, ["recipe_v1"])["executable"]
