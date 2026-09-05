"""线性、可版本化工艺配方；仅描述请求，不提供执行器直控。"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator

Positive = Annotated[FiniteFloat, Field(gt=0, strict=True)]
Nonnegative = Annotated[FiniteFloat, Field(ge=0, strict=True)]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExitCondition(ContractModel):
    signal: Literal["furnace_c", "burden_c", "elapsed_s"]
    comparison: Literal["gte", "lt"] = "gte"
    value: Nonnegative


class Stage(ContractModel):
    name: str = Field(min_length=1, max_length=80)
    kind: Literal["ramp", "hold", "gas", "cool"]
    furnace_target_c: Nonnegative | None = None
    ramp_c_min: Positive | None = None
    n2_l_min: Nonnegative
    co_l_min: Nonnegative
    exit: ExitCondition
    timeout_s: int = Field(gt=0, le=604800)

    @model_validator(mode="after")
    def coherent_stage(self):
        if self.kind == "ramp" and (self.furnace_target_c is None or self.ramp_c_min is None):
            raise ValueError("升温阶段必须明确炉温目标和速率")
        if self.kind == "hold" and (self.furnace_target_c is None or self.exit.signal != "elapsed_s"):
            raise ValueError("保温阶段必须明确炉温目标和持续时间")
        if self.exit.signal == "elapsed_s" and (self.exit.comparison != "gte" or self.exit.value >= self.timeout_s):
            raise ValueError("时间转换条件必须早于阶段超时")
        if self.kind == "cool" and self.co_l_min != 0:
            raise ValueError("冷却阶段必须撤除 CO 请求")
        return self


def _standard_stages() -> list[dict]:
    # 预检/气密/去除上口密封是板端固定启动许可，不能由编辑配方省略。
    stages = []
    for name, target, rate, exit_signal, exit_value, n2, co in [
        ("氮气保护升温至500℃", 500, 10, "furnace_c", 500, 5, 0),
        ("还原气氛升温至900℃", 900, 10, "furnace_c", 900, 3.5, 1.5),
        ("升温至1100℃", 1100, 2, "furnace_c", 1100, 3.5, 1.5),
        ("程序至1600℃，等待料层1580℃", 1600, 5, "burden_c", 1580, 3.5, 1.5),
    ]:
        stages.append(
            dict(
                name=name,
                kind="ramp",
                furnace_target_c=target,
                ramp_c_min=rate,
                n2_l_min=n2,
                co_l_min=co,
                exit=dict(signal=exit_signal, value=exit_value),
                timeout_s=21600,
            )
        )
    stages.extend(
        [
            dict(
                name="料层达到1580℃后保持30分钟",
                kind="hold",
                furnace_target_c=1600,
                n2_l_min=3.5,
                co_l_min=1.5,
                exit=dict(signal="elapsed_s", value=1800),
                timeout_s=3600,
            ),
            dict(
                name="氮气置换并冷却",
                kind="cool",
                n2_l_min=2,
                co_l_min=0,
                exit=dict(signal="burden_c", comparison="lt", value=200),
                timeout_s=86400,
            ),
        ]
    )
    return stages


class RecipeDefinition(ContractModel):
    schema_version: Literal[1] = 1
    name: str = Field(min_length=1, max_length=100)
    mode: Literal["standard", "custom"]
    description: str = Field(default="", max_length=2000)
    stages: list[Stage] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def standard_is_immutable(self):
        if self.mode == "standard" and self.deviations():
            raise ValueError("修改标准流程必须保存为非标版本")
        return self

    def deviations(self) -> list[str]:
        expected = [Stage.model_validate(stage).model_dump() for stage in _standard_stages()]
        actual = [stage.model_dump() for stage in self.stages]
        if actual == expected:
            return []
        differences = []
        for i in range(max(len(actual), len(expected))):
            if i >= len(actual) or i >= len(expected):
                differences.append(f"stage_{i + 1}:added_or_removed")
            elif actual[i] != expected[i]:
                differences.append(f"stage_{i + 1}:modified")
        return differences

    def digest(self) -> str:
        raw = json.dumps(self.model_dump(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()


def standard_template() -> RecipeDefinition:
    return RecipeDefinition(
        name="GB/T 34211-2017 标准候选模板",
        mode="standard",
        description="工艺条款模板；现场条件和原文争议项须完成验收。超时为候选运行保护参数，需设备校验。",
        stages=_standard_stages(),
    )


class SafetyProfile(ContractModel):
    version: str = Field(min_length=1, max_length=100)
    temperature_max_c: Positive
    ramp_max_c_min: Positive
    n2_max_l_min: Positive
    co_max_l_min: Positive
    co_min_furnace_c: Nonnegative
    safe_end_burden_c: Annotated[FiniteFloat, Field(gt=0, le=200)]
    max_stages: int = Field(gt=0, le=64)
    rules_reference: str | None = Field(default=None, max_length=1000)


def validate_for_device(recipe: RecipeDefinition, profile: dict | None, capabilities: list[str]) -> dict:
    errors = []
    if "recipe_v1" not in capabilities:
        errors.append("device_missing_recipe_v1")
    try:
        limits = SafetyProfile.model_validate(profile)
    except ValueError:
        return {
            "executable": False,
            "errors": errors + ["device_safety_profile_missing_or_invalid"],
            "deviations": recipe.deviations(),
        }
    if len(recipe.stages) > limits.max_stages:
        errors.append("device_max_stages")
    if recipe.mode == "standard" and not limits.rules_reference:
        errors.append("standard_rules_not_confirmed")
    minimum_furnace = 0
    for i, stage in enumerate(recipe.stages):
        prefix = f"stage_{i + 1}:"
        for value, maximum, code in [
            (stage.furnace_target_c, limits.temperature_max_c, "temperature_max"),
            (stage.ramp_c_min, limits.ramp_max_c_min, "ramp_max"),
            (stage.n2_l_min, limits.n2_max_l_min, "n2_max"),
            (stage.co_l_min, limits.co_max_l_min, "co_max"),
        ]:
            if value is not None and value > maximum:
                errors.append(prefix + code)
        if stage.co_l_min > 0 and minimum_furnace < limits.co_min_furnace_c:
            errors.append(prefix + "co_before_required_temperature")
        if (
            stage.co_l_min > 0
            and stage.furnace_target_c is not None
            and stage.furnace_target_c < limits.co_min_furnace_c
        ):
            errors.append(prefix + "co_target_below_required_temperature")
        if stage.kind == "cool":
            minimum_furnace = 0
        elif stage.exit.signal == "furnace_c":
            # A measured lower-bound exit can prove temperature only for the
            # following stage. An upper-bound exit cannot carry an old proof.
            minimum_furnace = stage.exit.value if stage.exit.comparison == "gte" else 0
        if stage.furnace_target_c is not None:
            # Any stage kind may lower its target. A higher setpoint alone does
            # not prove reheating, and a lower one limits even a furnace exit.
            minimum_furnace = min(minimum_furnace, stage.furnace_target_c)
    final = recipe.stages[-1]
    if (
        final.kind != "cool"
        or final.co_l_min != 0
        or final.n2_l_min <= 0
        or final.exit.signal != "burden_c"
        or final.exit.comparison != "lt"
        or final.exit.value > limits.safe_end_burden_c
    ):
        errors.append("safe_cooling_end_required")
    return {
        "executable": not errors,
        "errors": errors,
        "deviations": recipe.deviations(),
        "safety_profile_version": limits.version,
        "rules_reference": limits.rules_reference,
    }
