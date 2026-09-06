"""Immutable recipe artifact, with explicit heater requests and integer units."""

from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from .types import Digest, Identifier, PositiveInt, ShortText, StrictModel, UInt


class ExitCondition(StrictModel):
    source: Literal["furnace_mc", "burden_mc", "elapsed_ms"]
    op: Literal["gte", "lt"]
    value: UInt
    stable_ms: UInt


class Stage(StrictModel):
    name: Annotated[str, StringConstraints(min_length=1, max_length=80)]
    kind: Literal["ramp", "hold", "gas", "cool"]
    heater_mode: Literal["off", "ramp", "hold"]
    target_mc: UInt | None
    rate_mc_per_min: PositiveInt | None
    n2_ml_min: UInt
    co_ml_min: UInt
    exit: ExitCondition
    timeout_ms: Annotated[int, Field(gt=0, le=604800000)]

    @model_validator(mode="after")
    def coherent(self):
        if (self.heater_mode == "off") != (self.target_mc is None):
            raise ValueError("target_mc is null exactly when heater_mode=off")
        if (self.heater_mode == "ramp") != (self.rate_mc_per_min is not None):
            raise ValueError("rate_mc_per_min is required only for ramp")
        if self.kind == "ramp" and self.heater_mode != "ramp":
            raise ValueError("ramp stage requires explicit ramp heater mode")
        if self.kind == "ramp" and self.exit.source == "elapsed_ms":
            raise ValueError("ramp stage exit must observe furnace or burden temperature")
        if self.kind == "hold" and (self.heater_mode != "hold" or self.exit.source != "elapsed_ms"):
            raise ValueError("hold stage requires hold heater and elapsed exit")
        if self.kind == "cool" and (self.heater_mode != "off" or self.co_ml_min != 0):
            raise ValueError("cool stage requires heater off and zero CO request")
        if self.exit.stable_ms >= self.timeout_ms:
            raise ValueError("stable interval must be shorter than stage timeout")
        if self.exit.source == "elapsed_ms" and (
            self.exit.op != "gte" or self.exit.stable_ms != 0 or self.exit.value >= self.timeout_ms
        ):
            raise ValueError("elapsed exit requires gte, zero stable_ms, and value before timeout")
        return self


class Recipe(StrictModel):
    schema_version: Literal[2]
    recipe_id: Identifier
    version: PositiveInt
    name: Annotated[str, StringConstraints(min_length=1, max_length=100)]
    mode: Literal["standard", "custom"]
    rules_version: ShortText | None
    safety_profile_digest: Digest
    stages: Annotated[list[Stage], Field(min_length=1, max_length=64)]

    @model_validator(mode="after")
    def coherent(self):
        if self.mode == "standard" and self.rules_version is None:
            raise ValueError("standard candidate requires an identified rules version")
        if any(stage.kind == "cool" for stage in self.stages[:-1]):
            raise ValueError("cool stage is terminal; no reheating or subsequent stages")
        final = self.stages[-1]
        if not (
            final.kind == "cool"
            and final.n2_ml_min > 0
            and final.exit.source == "burden_mc"
            and final.exit.op == "lt"
            and 0 < final.exit.value <= 200000
        ):
            raise ValueError("final stage must request N2 cooling below a burden limit <=200000 m°C")
        return self
