<script setup>
const props = defineProps({
  stage: { type: Object, required: true },
  index: { type: Number, required: true },
  disabled: Boolean,
  count: Number,
})
const emit = defineEmits(['change', 'remove', 'move'])
function update(field, value) {
  emit('change', { ...props.stage, [field]: value })
}
function updateExit(field, value) {
  update('exit', { ...props.stage.exit, [field]: value })
}
function changeHeater(heater_mode) {
  const stage = { ...props.stage, heater_mode }
  if (heater_mode === 'off') stage.furnace_target_c = null
  if (heater_mode !== 'ramp') stage.ramp_c_min = null
  emit('change', stage)
}
function changeKind(kind) {
  const stage = { ...props.stage, kind }
  if (kind !== 'gas') delete stage.heater_mode
  if (kind !== 'ramp') stage.ramp_c_min = null
  if (kind === 'cool') {
    stage.co_l_min = 0
    stage.furnace_target_c = null
    stage.exit = { signal: 'burden_c', comparison: 'lt', value: 200 }
  }
  if (kind === 'hold') stage.exit = { signal: 'elapsed_s', comparison: 'gte', value: 1800 }
  emit('change', stage)
}
const numeric = (event) => (event.target.value === '' ? null : Number(event.target.value))
</script>
<template>
  <fieldset :disabled="disabled">
    <legend>阶段 {{ index + 1 }} · {{ stage.name }}</legend>
    <div class="fields">
      <label class="name"
        >阶段名称<input
          :value="stage.name"
          maxlength="80"
          required
          @input="update('name', $event.target.value)"
      /></label>
      <label
        >阶段类型<select
          :value="stage.kind"
          @change="changeKind($event.target.value)"
        >
          <option value="ramp">升温</option>
          <option value="hold">保温</option>
          <option value="gas">气氛</option>
          <option value="cool">冷却</option>
        </select></label
      >
      <label v-if="stage.kind === 'gas'">
        加热方式<select aria-label="加热方式" :value="stage.heater_mode || ''" required @change="changeHeater($event.target.value)">
          <option disabled value="">未声明（旧版本，需明确选择）</option>
          <option value="off">关闭加热</option>
          <option value="ramp">按速率升温</option>
          <option value="hold">保持目标温度</option>
        </select>
      </label>
      <label
        >炉温目标 (℃)<input
          :value="stage.furnace_target_c"
          type="number"
          min="0"
          step="any"
          :required="['ramp', 'hold'].includes(stage.kind) || (stage.kind === 'gas' && ['ramp', 'hold'].includes(stage.heater_mode))"
          :disabled="stage.kind === 'cool' || (stage.kind === 'gas' && stage.heater_mode === 'off')"
          @input="update('furnace_target_c', numeric($event))"
      /></label>
      <label
        >升温速率 (℃/min)<input
          :value="stage.ramp_c_min"
          type="number"
          min="0.001"
          step="any"
          :required="stage.kind === 'ramp' || stage.heater_mode === 'ramp'"
          :disabled="stage.kind !== 'ramp' && stage.heater_mode !== 'ramp'"
          @input="update('ramp_c_min', numeric($event))"
      /></label>
      <label
        >N₂ (L/min)<input
          :value="stage.n2_l_min"
          type="number"
          min="0"
          step="any"
          required
          @input="update('n2_l_min', numeric($event))"
      /></label>
      <label
        >CO (L/min)<input
          :value="stage.co_l_min"
          type="number"
          min="0"
          step="any"
          required
          :disabled="stage.kind === 'cool'"
          @input="update('co_l_min', numeric($event))"
      /></label>
      <label
        >转换信号<select
          :value="stage.exit.signal"
          :disabled="stage.kind === 'hold'"
          @change="updateExit('signal', $event.target.value)"
        >
          <option value="furnace_c">炉温 (℃)</option>
          <option value="burden_c">料层温度 (℃)</option>
          <option value="elapsed_s">本阶段时长 (s)</option>
        </select></label
      >
      <label
        >转换比较<select
          :value="stage.exit.comparison"
          @change="updateExit('comparison', $event.target.value)"
        >
          <option value="gte">达到 / 大于等于</option>
          <option value="lt">低于</option>
        </select></label
      >
      <label
        >转换阈值<input
          :value="stage.exit.value"
          type="number"
          min="0"
          step="any"
          required
          @input="updateExit('value', numeric($event))"
      /></label>
      <label>
        条件稳定时长 (s)<input aria-label="条件稳定时长 (s)" :value="stage.exit.stable_s" type="number" min="0" step="0.001" placeholder="未声明（按 0 秒校验）" @input="updateExit('stable_s', numeric($event))" />
      </label>
      <label
        >阶段超时 (s)<input
          :value="stage.timeout_s"
          type="number"
          min="1"
          max="604800"
          step="1"
          required
          @input="update('timeout_s', numeric($event))"
      /></label>
    </div>
    <div class="actions">
      <button
        type="button"
        :disabled="index === 0"
        @click="emit('move', -1)"
      >
        上移</button
      ><button
        type="button"
        :disabled="index === count - 1"
        @click="emit('move', 1)"
      >
        下移</button
      ><button
        type="button"
        :disabled="count <= 1"
        @click="emit('remove')"
      >
        移除阶段
      </button>
    </div>
  </fieldset>
</template>
<style scoped>
fieldset {
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 12px;
  min-width: 0;
}
legend {
  padding: 0 8px;
  font-weight: 600;
  overflow-wrap: anywhere;
}
.fields {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 160px), 1fr));
  gap: 12px;
}
label {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 12px;
  color: var(--text-sec);
  min-width: 0;
}
input,
select {
  width: 100%;
}
.name {
  grid-column: 1 / -1;
}
.actions {
  display: flex;
  gap: 8px;
  margin-top: 12px;
}
</style>
