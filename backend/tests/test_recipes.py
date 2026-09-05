"""配方结构与板端安全范围契约。"""

import copy

import pytest
from pydantic import ValidationError

from app.services.recipe_definition import RecipeDefinition, standard_template, validate_for_device


def profile():
    return {
        "version": "mock-profile-1",
        "temperature_max_c": 1600,
        "ramp_max_c_min": 10,
        "n2_max_l_min": 5,
        "co_max_l_min": 2,
        "co_min_furnace_c": 500,
        "safe_end_burden_c": 200,
        "max_stages": 32,
        "rules_reference": "mock-only",
    }


def test_standard_template_matches_gas_and_temperature_objects():
    recipe = standard_template()
    assert recipe.stages[0].n2_l_min == 5
    assert recipe.stages[0].exit.signal == "furnace_c"
    assert recipe.stages[0].exit.value == 500
    assert recipe.stages[1].co_l_min == 1.5
    assert recipe.stages[-2].exit.value == 1800
    assert recipe.stages[-1].exit.signal == "burden_c"
    assert recipe.stages[-1].exit.comparison == "lt"
    assert recipe.stages[-1].exit.value == 200
    assert validate_for_device(recipe, profile(), ["recipe_v1"])["executable"]


def test_editing_standard_process_requires_custom_mode():
    data = standard_template().model_dump()
    data["stages"][1]["co_l_min"] = 1.6
    with pytest.raises(ValidationError):
        RecipeDefinition.model_validate(data)
    data["mode"] = "custom"
    assert RecipeDefinition.model_validate(data).deviations()


def test_missing_capability_or_profile_is_not_executable():
    recipe = standard_template()
    assert not validate_for_device(recipe, None, [])["executable"]
    assert not validate_for_device(recipe, profile(), [])["executable"]
    unapproved = profile()
    unapproved.pop("rules_reference")
    assert not validate_for_device(recipe, unapproved, ["recipe_v1"])["executable"]


def test_custom_stages_remain_inside_device_safety_limits():
    data = standard_template().model_dump()
    data["mode"] = "custom"
    data["stages"][0]["co_l_min"] = 1
    data["stages"][1]["co_l_min"] = 3
    result = validate_for_device(RecipeDefinition.model_validate(data), profile(), ["recipe_v1"])
    assert not result["executable"]
    assert any("co_max" in error for error in result["errors"])
    assert any("co_before" in error for error in result["errors"])


@pytest.mark.parametrize("field,value", [("n2_l_min", float("nan")), ("timeout_s", -1), ("force_do", 1)])
def test_nonfinite_values_and_arbitrary_actuators_are_rejected(field, value):
    data = copy.deepcopy(standard_template().model_dump())
    data["stages"][0][field] = value
    with pytest.raises(ValidationError):
        RecipeDefinition.model_validate(data)


def recipe_with_temperature_transition(transition, *, reheat=None):
    stages = [standard_template().stages[0].model_dump(), transition]
    if reheat is not None:
        stages.append(reheat)
    stages.extend(
        [
            dict(
                name="申请还原气氛",
                kind="gas",
                n2_l_min=3.5,
                co_l_min=1.5,
                exit=dict(signal="elapsed_s", value=30),
                timeout_s=60,
            ),
            standard_template().stages[-1].model_dump(),
        ]
    )
    return RecipeDefinition(name="温度下界回归", mode="custom", stages=stages)


def temperature_transition(kind="hold", target=400, signal="elapsed_s", comparison="gte", value=30):
    return dict(
        name="氮气温度转换",
        kind=kind,
        furnace_target_c=target,
        ramp_c_min=5 if kind == "ramp" else None,
        n2_l_min=5,
        co_l_min=0,
        exit=dict(signal=signal, comparison=comparison, value=value),
        timeout_s=3600,
    )


@pytest.mark.parametrize("kind", ["hold", "ramp", "gas"])
@pytest.mark.parametrize("target", [0, 400, 499.999])
def test_lower_temperature_target_invalidates_prior_co_permission(kind, target):
    recipe = recipe_with_temperature_transition(temperature_transition(kind=kind, target=target))
    result = validate_for_device(recipe, profile(), ["recipe_v1"])
    assert not result["executable"]
    assert "stage_3:co_before_required_temperature" in result["errors"]


@pytest.mark.parametrize("value", [400, 500, 600])
def test_upper_temperature_exit_does_not_prove_a_lower_bound(value):
    recipe = recipe_with_temperature_transition(
        temperature_transition(kind="gas", target=None, signal="furnace_c", comparison="lt", value=value)
    )
    result = validate_for_device(recipe, profile(), ["recipe_v1"])
    assert not result["executable"]
    assert "stage_3:co_before_required_temperature" in result["errors"]


@pytest.mark.parametrize("target", [500, 500.001, 900])
def test_holding_at_or_above_co_boundary_preserves_existing_proof(target):
    recipe = recipe_with_temperature_transition(temperature_transition(target=target))
    assert validate_for_device(recipe, profile(), ["recipe_v1"])["executable"]


@pytest.mark.parametrize("exit_value,allowed", [(499.999, False), (500, True), (500.001, True)])
def test_explicit_furnace_reheat_exit_reestablishes_co_temperature(exit_value, allowed):
    recipe = recipe_with_temperature_transition(
        temperature_transition(),
        reheat=temperature_transition(kind="ramp", target=600, signal="furnace_c", value=exit_value),
    )
    result = validate_for_device(recipe, profile(), ["recipe_v1"])
    assert result["executable"] is allowed
    if not allowed:
        assert "stage_4:co_before_required_temperature" in result["errors"]


@pytest.mark.parametrize("signal,value", [("elapsed_s", 30), ("burden_c", 600)])
def test_high_target_without_furnace_exit_cannot_reestablish_temperature(signal, value):
    recipe = recipe_with_temperature_transition(
        temperature_transition(),
        reheat=temperature_transition(kind="ramp", target=600, signal=signal, value=value),
    )
    result = validate_for_device(recipe, profile(), ["recipe_v1"])
    assert not result["executable"]
    assert "stage_4:co_before_required_temperature" in result["errors"]


def test_reheat_stage_cannot_request_co_before_its_own_exit_proves_temperature():
    reheat = temperature_transition(kind="ramp", target=600, signal="furnace_c", value=500)
    reheat["co_l_min"] = 1.5
    recipe = recipe_with_temperature_transition(temperature_transition(), reheat=reheat)
    result = validate_for_device(recipe, profile(), ["recipe_v1"])
    assert not result["executable"]
    assert "stage_3:co_before_required_temperature" in result["errors"]
    assert "stage_4:co_before_required_temperature" not in result["errors"]


def test_lower_target_cannot_keep_requesting_co_in_the_same_stage():
    transition = temperature_transition(target=499.999)
    transition["co_l_min"] = 1.5
    recipe = recipe_with_temperature_transition(transition)
    result = validate_for_device(recipe, profile(), ["recipe_v1"])
    assert not result["executable"]
    assert "stage_2:co_target_below_required_temperature" in result["errors"]
    assert "stage_3:co_before_required_temperature" in result["errors"]


def test_low_target_is_not_overridden_by_already_satisfied_hot_exit():
    recipe = recipe_with_temperature_transition(
        temperature_transition(kind="ramp", target=400, signal="furnace_c", value=500)
    )
    result = validate_for_device(recipe, profile(), ["recipe_v1"])
    assert not result["executable"]
    assert "stage_3:co_before_required_temperature" in result["errors"]
