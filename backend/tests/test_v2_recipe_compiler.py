"""A saved human-unit recipe must have one exact, inspectable wire artifact."""

import copy
import json

import pytest

from app.hostcomm.v2_contract.codec import digest
from app.hostcomm.v2_simulator.fixtures import synthetic_profile
from app.services.recipe_definition import RecipeDefinition, standard_template
from app.services.v2_recipe_compiler import compile_recipe


def bundle():
    model = standard_template()
    return dict(recipe_id="a" * 32, version=1, digest=model.digest(), definition=model.model_dump())


def custom(change):
    value = bundle()
    value["definition"]["mode"] = "custom"
    change(value["definition"])
    value["digest"] = RecipeDefinition.model_validate(value["definition"]).digest()
    return value


def test_integer_artifact_bound_to_saved_version_and_profile():
    original = bundle()
    artifact = compile_recipe(original, synthetic_profile(approved=True))
    assert original == bundle()
    assert artifact.recipe["stages"][1]["co_ml_min"] == 1500
    assert artifact.recipe["stages"][-2]["exit"]["value"] == 1800000
    assert artifact.recipe["stages"][-1]["heater_mode"] == "off"
    assert artifact.digest == digest(artifact.recipe)
    assert json.loads(artifact.data) == artifact.recipe
    assert artifact.source_digest == original["digest"]


def test_precision_is_rejected_not_rounded():
    value = custom(lambda d: d["stages"][0].update(n2_l_min=5.0001))
    with pytest.raises(ValueError, match="precision"):
        compile_recipe(value, synthetic_profile(approved=True))


def test_gas_heater_intent_must_be_explicit_and_new_version_is_not_rewritten():
    def change(d):
        stage = copy.deepcopy(d["stages"][0])
        stage.update(kind="gas", furnace_target_c=None, ramp_c_min=None, exit=dict(signal="elapsed_s", value=30))
        d["stages"].insert(0, stage)

    value = custom(change)
    with pytest.raises(ValueError, match="heater_mode"):
        compile_recipe(value, synthetic_profile(approved=True))
    value["definition"]["stages"][0]["heater_mode"] = "off"
    value["digest"] = RecipeDefinition.model_validate(value["definition"]).digest()
    assert compile_recipe(value, synthetic_profile(approved=True)).recipe["stages"][0]["heater_mode"] == "off"


@pytest.mark.parametrize("corruption", ["unapproved", "digest", "safety", "identity"])
def test_unapproved_or_mismatched_evidence_cannot_compile(corruption):
    value, profile = bundle(), synthetic_profile(approved=True)
    if corruption == "unapproved":
        profile = synthetic_profile()
    elif corruption == "digest":
        value["digest"] = "0" * 64
    elif corruption == "safety":
        profile["limits"]["n2_max_ml_min"] = 100
        profile["profile_digest"] = digest({k: v for k, v in profile.items() if k != "profile_digest"})
    else:
        profile["limits"]["n2_max_ml_min"] = 100
    with pytest.raises(ValueError):
        compile_recipe(value, profile)


def test_added_optional_fields_preserve_historical_saved_recipe_digest():
    model = standard_template()
    old = copy.deepcopy(model.model_dump())
    for stage in old["stages"]:
        assert "heater_mode" not in stage
        assert "stable_s" not in stage["exit"]
    assert RecipeDefinition.model_validate(old).digest() == model.digest()
