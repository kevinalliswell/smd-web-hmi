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
