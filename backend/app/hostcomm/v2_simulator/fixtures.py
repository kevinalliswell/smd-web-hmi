"""Explicit synthetic limits: never equipment approval or physical I/O configuration."""

from dataclasses import dataclass

from app.hostcomm.v2_contract.codec import digest
from app.hostcomm.v2_contract.messages import Measurements, ProfileSnapshot


@dataclass(frozen=True)
class SimulatorPairing:
    device_id: str = "00000000000000000000000000000015"
    controller_id: str = "00000000000000000000000000000003"
    controller_epoch: str = "00000000000000000000000000000004"
    role: str = "control"

    def __post_init__(self):
        from pydantic import TypeAdapter

        from app.hostcomm.v2_contract.types import Identifier

        for identifier in (self.device_id, self.controller_id, self.controller_epoch):
            TypeAdapter(Identifier).validate_python(identifier)
        if self.role not in {"control", "diagnostic"}:
            raise ValueError("pairing role must be control or diagnostic")


def synthetic_profile(*, approved: bool = False) -> dict:
    """approved=True authorizes only this inert software model, never equipment."""
    result = {
        "profile_id": "SIMULATOR-ONLY/1",
        "engineering_config_digest": "e" * 64,
        "approved": approved,
        "rules_reference": "inert software test fixture" if approved else None,
        "gas_reference": {
            "temperature_mk": 273150,
            "pressure_pa": 101325,
            "gas_model": "dry_ideal",
            "source": "synthetic test data; no equipment approval",
        },
        "limits": {
            "temperature_max_mc": 1600000,
            "ramp_max_mc_per_min": 20000,
            "n2_max_ml_min": 10000,
            "co_max_ml_min": 3000,
            "co_min_furnace_mc": 500000,
            "safe_end_burden_mc": 200000,
            "minimum_n2_ml_min": 2000,
            "purge_duration_ms": 60000,
            "cooling_timeout_ms": 86400000,
        },
        "resources": {
            "max_frame_bytes": 8192,
            "max_recipe_bytes": 65536,
            "max_chunk_bytes": 1536,
            "max_stages": 64,
            "max_sample_rate_millihz": 10000,
            "default_sample_period_ms": 1000,
            "channel_freshness_ms": {name: 3000 for name in Measurements.model_fields},
            "sample_log_capacity_bytes": 16777216,
            "sample_log_durable": True,
            "command_result_slots": 128,
            "command_unresolved_slots": 8,
        },
    }
    result["profile_digest"] = digest(result)
    return ProfileSnapshot.model_validate(result).model_dump(mode="python")


def synthetic_recipe(profile_digest: str) -> dict:
    return {
        "schema_version": 2,
        "recipe_id": "a" * 32,
        "version": 1,
        "name": "SIMULATOR ONLY warm and cool",
        "mode": "custom",
        "rules_version": None,
        "safety_profile_digest": profile_digest,
        "stages": [
            {
                "name": "warm",
                "kind": "ramp",
                "heater_mode": "ramp",
                "target_mc": 500000,
                "rate_mc_per_min": 10000,
                "n2_ml_min": 5000,
                "co_ml_min": 0,
                "exit": {"source": "furnace_mc", "op": "gte", "value": 500000, "stable_ms": 1000},
                "timeout_ms": 21600000,
            },
            {
                "name": "cool",
                "kind": "cool",
                "heater_mode": "off",
                "target_mc": None,
                "rate_mc_per_min": None,
                "n2_ml_min": 2000,
                "co_ml_min": 0,
                "exit": {"source": "burden_mc", "op": "lt", "value": 200000, "stable_ms": 1000},
                "timeout_ms": 86400000,
            },
        ],
    }
