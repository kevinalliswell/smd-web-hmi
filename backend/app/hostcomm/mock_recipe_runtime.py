"""候选扩展契约模拟器；不与真实硬件连接，不作为固件或国标验收证据。"""

from __future__ import annotations

import copy

from app.services.recipe_definition import RecipeDefinition, validate_for_device

MOCK_SAFETY_PROFILE = {
    "version": "mock-safety/1",
    "temperature_max_c": 1650,
    "ramp_max_c_min": 20,
    "n2_max_l_min": 10,
    "co_max_l_min": 3,
    "co_min_furnace_c": 500,
    "safe_end_burden_c": 200,
    "max_stages": 32,
    "rules_reference": "mock-only: 标准争议项未实机确认",
}
EXTENDED_CAPABILITIES = ["recipe_v1", "run_lifecycle_v1", "measurement_events_v1", "telemetry_sequence_v1"]


class MockRecipeRuntime:
    """受限线性执行：连接断开不停止板端模拟时钟；无客户端计时依赖。"""

    def __init__(self):
        self.definition: RecipeDefinition | None = None
        self.recipe: dict | None = None
        self.running = False
        self.stage_index = 0
        self.elapsed = 0.0
        self.furnace = 25.0
        self.burden = 25.0
        self.n2 = 0.0
        self.co = 0.0
        self.state = "Standby"
        self.measurement_complete = False
        self.safe_complete = False
        self.first_drip = False
        self.fault_reason = None
        self._stopping = False

    def start(self, recipe: dict) -> None:
        definition = RecipeDefinition.model_validate(recipe["definition"])
        if (
            definition.digest() != recipe.get("digest")
            or not validate_for_device(definition, MOCK_SAFETY_PROFILE, EXTENDED_CAPABILITIES)["executable"]
        ):
            raise ValueError("配方或安全范围校验失败")
        self.__init__()
        self.recipe = copy.deepcopy(recipe)
        self.definition = definition
        self.running = True
        self.state = "Precheck"

    def stop(self):
        if self.running:
            self._stopping = True
            self.state = "N2Replace"
            self.co = 0.0
            self.n2 = 2.0

    def advance(self, seconds: float):
        # 小步推进，避免倍速跨越条件时跳过气氛转换。
        while self.running and seconds > 0:
            step = min(seconds, 1.0)
            self._tick(step)
            seconds -= step

    def _tick(self, dt: float):
        if self.state == "Precheck":
            self.elapsed += dt
            self.co = 0.0
            if self.elapsed >= 3:
                self.elapsed = 0.0
                self.state = "Heating"
            return
        assert self.definition is not None
        stage = self.definition.stages[self.stage_index]
        self.elapsed += dt
        cooling = self._stopping or stage.kind == "cool"
        if cooling:
            self.state = "N2Replace"
            self.co = 0.0
            self.n2 = 2.0 if self._stopping else stage.n2_l_min
            self.furnace = max(25.0, self.furnace - 15 / 60 * dt)
            self.burden = max(25.0, self.burden - 15 / 60 * dt)
        else:
            self.state = "Hold1580" if stage.kind == "hold" else "Heating"
            self.n2, self.co = stage.n2_l_min, stage.co_l_min
            if self.co > 0 and self.furnace < MOCK_SAFETY_PROFILE["co_min_furnace_c"]:
                self.fault_reason = "co_temperature_interlock"
                self.stop()
                return
            if stage.furnace_target_c is not None:
                rate = (stage.ramp_c_min or 10) / 60 * dt
                distance = stage.furnace_target_c - self.furnace
                self.furnace += max(-rate, min(rate, distance))
            target = max(25, self.furnace - 5)
            self.burden += max(-dt, min(dt, target - self.burden))
            self.first_drip = self.first_drip or self.burden >= 1450
        if self._stopping:
            if self.burden < MOCK_SAFETY_PROFILE["safe_end_burden_c"]:
                self.running = False
                self.safe_complete = True
                self.n2 = 0.0
                self.state = "End"
            return
        value = {"furnace_c": self.furnace, "burden_c": self.burden, "elapsed_s": self.elapsed}[stage.exit.signal]
        satisfied = value >= stage.exit.value if stage.exit.comparison == "gte" else value < stage.exit.value
        if satisfied:
            if self.stage_index == len(self.definition.stages) - 1:
                self.running = False
                self.safe_complete = True
                self.n2 = 0.0
                self.state = "End"
            else:
                self.stage_index += 1
                self.elapsed = 0.0
                if self.definition.stages[self.stage_index].kind == "cool":
                    self.measurement_complete = True
                    self.state = "N2Replace"
                    self.co = 0.0
        elif self.elapsed >= stage.timeout_s:
            self.fault_reason = "stage_timeout"
            self.stop()
