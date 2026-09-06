"""Compile saved human units to an immutable, exact HostComm 2 artifact.

The saved source digest is never replaced by the device artifact digest. Profiles
are authenticated device evidence; callers must not accept one from an HTTP body.
"""

from dataclasses import dataclass
from decimal import Decimal

from app.hostcomm.v2_contract.codec import canonical_bytes, digest
from app.hostcomm.v2_contract.messages import ProfileSnapshot
from app.hostcomm.v2_contract.recipe import Recipe
from app.services.recipe_definition import RecipeDefinition, validate_for_device


@dataclass(frozen=True)
class CompiledRecipe:
    recipe: dict
    digest: str
    data: bytes
    source_digest: str
    validation: dict


def milli(value, field: str) -> int | None:
    if value is None:
        return None
    scaled = Decimal(str(value)) * 1000
    if scaled != scaled.to_integral_value():
        raise ValueError(f"{field}: precision exceeds the integer wire unit")
    return int(scaled)


def validated_profile(profile: dict) -> dict:
    model = ProfileSnapshot.model_validate(profile)
    parsed = model.model_dump()
    if digest({k: v for k, v in parsed.items() if k != "profile_digest"}) != model.profile_digest:
        raise ValueError("profile_digest mismatch")
    if not model.approved:
        raise ValueError("engineering profile is not approved")
    return parsed


def compile_recipe(bundle: dict, profile: dict) -> CompiledRecipe:
    model = RecipeDefinition.model_validate(bundle["definition"])
    if model.digest() != bundle["digest"]:
        raise ValueError("saved recipe digest mismatch")
    profile = validated_profile(profile)
    limits = profile["limits"]
    # Reuse the source recipe's conservative temperature/gas proof. The
    # capability argument selects this pure validator, not advertised firmware.
    verdict = validate_for_device(
        model,
        {
            "version": profile["profile_digest"],
            "temperature_max_c": limits["temperature_max_mc"] / 1000,
            "ramp_max_c_min": limits["ramp_max_mc_per_min"] / 1000,
            "n2_max_l_min": limits["n2_max_ml_min"] / 1000,
            "co_max_l_min": limits["co_max_ml_min"] / 1000,
            "co_min_furnace_c": limits["co_min_furnace_mc"] / 1000,
            "safe_end_burden_c": limits["safe_end_burden_mc"] / 1000,
            "max_stages": profile["resources"]["max_stages"],
            "rules_reference": profile["rules_reference"],
        },
        ["recipe_v1"],
    )
    if not verdict["executable"]:
        raise ValueError("; ".join(verdict["errors"]))
    stages = []
    for index, stage in enumerate(model.stages):
        mode = stage.heater_mode or {"ramp": "ramp", "hold": "hold", "cool": "off"}.get(stage.kind)
        if mode is None:
            raise ValueError(f"stage_{index + 1}: gas stage requires explicit heater_mode in a new saved version")
        # Do not silently discard contradictory legacy heater fields.
        if mode == "off" and (stage.furnace_target_c is not None or stage.ramp_c_min is not None):
            raise ValueError(f"stage_{index + 1}: heater off conflicts with a target or rate")
        if mode == "hold" and stage.ramp_c_min is not None:
            raise ValueError(f"stage_{index + 1}: hold conflicts with a ramp rate")
        stages.append(
            {
                "name": stage.name,
                "kind": stage.kind,
                "heater_mode": mode,
                "target_mc": milli(stage.furnace_target_c, "target"),
                "rate_mc_per_min": milli(stage.ramp_c_min, "rate"),
                "n2_ml_min": milli(stage.n2_l_min, "n2"),
                "co_ml_min": milli(stage.co_l_min, "co"),
                "exit": {
                    "source": {"furnace_c": "furnace_mc", "burden_c": "burden_mc", "elapsed_s": "elapsed_ms"}[
                        stage.exit.signal
                    ],
                    "op": stage.exit.comparison,
                    "value": milli(stage.exit.value, "exit"),
                    "stable_ms": milli(stage.exit.stable_s or 0, "stable"),
                },
                "timeout_ms": stage.timeout_s * 1000,
            }
        )
    final = stages[-1]
    if final["n2_ml_min"] < limits["minimum_n2_ml_min"]:
        raise ValueError("final nitrogen request below engineering minimum")
    if final["timeout_ms"] > limits["cooling_timeout_ms"]:
        raise ValueError("cooling timeout exceeds engineering limit")
    wire = Recipe.model_validate(
        {
            "schema_version": 2,
            "recipe_id": bundle["recipe_id"],
            "version": bundle["version"],
            "name": model.name,
            "mode": model.mode,
            "rules_version": profile["rules_reference"] if model.mode == "standard" else None,
            "safety_profile_digest": profile["profile_digest"],
            "stages": stages,
        }
    ).model_dump()
    data = canonical_bytes(wire)
    if len(data) > profile["resources"]["max_recipe_bytes"]:
        raise ValueError("recipe exceeds device artifact capacity")
    return CompiledRecipe(wire, digest(wire), data, model.digest(), verdict)
