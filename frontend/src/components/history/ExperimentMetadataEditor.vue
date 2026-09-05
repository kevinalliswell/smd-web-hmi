<script setup>
import { computed, ref, watch } from 'vue'
import { reviseTestMetadata } from '@/api/tests'
const props = defineProps({ test: { type: Object, required: true }, readOnly: Boolean })
const emit = defineEmits(['updated'])
const specimen = ref({}), report = ref({}), reason = ref(''), busy = ref(false), message = ref('')
const specimenFields = [
  ['batch', '样品批次', 'text'], ['preparation', '制样方法', 'text'],
  ['particle_min_mm', '粒度下限 (mm)', 'number'], ['particle_max_mm', '粒度上限 (mm)', 'number'],
  ['dry_temperature_c', '干燥温度 (℃)', 'number'], ['dry_duration_min', '干燥时长 (min)', 'number'],
  ['sample_mass_g', '样品质量 (g)', 'number'], ['coke_upper_g', '上层焦炭 (g)', 'number'], ['coke_lower_g', '下层焦炭 (g)', 'number'],
  ['h1_mm', 'H1 (mm)', 'number'], ['h2_mm', 'H2 (mm)', 'number'], ['load_kg_cm2', '荷重 (kg/cm²)', 'number'],
  ['sealed_leak_pressure_pa', '封口检漏压差 (Pa)', 'number'], ['loaded_pressure_pa', '装样压差 (Pa)', 'number'], ['leak_n2_l_min', '检漏 N₂ (L/min)', 'number'], ['remarks', '样品条件备注', 'text'],
]
const reportFields = [['laboratory_name', '实验室名称', 'text'], ['laboratory_address', '实验室地址', 'text'], ['test_date', '试验日期', 'date'], ['abnormal_operations', '异常操作说明', 'text'], ['additional_operations', '附加操作说明', 'text']]
watch(() => props.test, (test, previous) => {
  specimen.value = { ...test.measurement_basis?.sample_metadata }
  report.value = { ...test.measurement_basis?.report_context }
  if (previous?.test_id !== test.test_id) { reason.value = ''; message.value = '' }
}, { immediate: true })
const computedHeight = computed(() => {
  const { h1_mm: h1, h2_mm: h2 } = specimen.value
  return [h1, h2].every((value) => value !== '' && value != null && Number.isFinite(Number(value))) ? Number(h1) - Number(h2) : null
})
function fieldsPayload(values, fields) {
  return Object.fromEntries(fields.filter(([key]) => values[key] !== '' && values[key] != null).map(([key, , type]) => [key, type === 'number' ? Number(values[key]) : values[key]]))
}
async function save() {
  if (props.readOnly || busy.value || reason.value.trim().length < 2) return
  busy.value = true; message.value = ''
  try {
    const sample = fieldsPayload(specimen.value, specimenFields)
    if (typeof specimen.value.preparation_confirmed === 'boolean') sample.preparation_confirmed = specimen.value.preparation_confirmed
    await reviseTestMetadata(props.test.test_id, { sample_metadata: sample, report_context: fieldsPayload(report.value, reportFields), reason: reason.value.trim() })
    emit('updated'); message.value = '补录已保存，原值与修订原因已归档。'
  } catch (error) {
    const detail = error.response?.data?.detail
    message.value = Array.isArray(detail) ? detail.map((item) => item.msg).join('；') : detail?.message || error.message || '保存失败'
  } finally { busy.value = false }
}
</script>
<template>
  <details class="card">
    <summary>{{ readOnly ? '查看样品条件与报告信息' : '补录样品条件与报告信息' }}</summary>
    <p class="muted">空白保持未知或保留原值，不补写推测数据。H = H1 − H2；已记录的原始高度不能通过补录冲突改写。</p>
    <form @submit.prevent="save">
      <fieldset :disabled="readOnly || busy"><legend>样品与装样条件</legend><div class="fields"><label v-for="[key, label, type] in specimenFields" :key="key">{{ label }}<input v-model="specimen[key]" :type="type" :step="type === 'number' ? 'any' : undefined" :maxlength="type === 'text' ? 2000 : undefined" /></label><label>制样确认<select v-model="specimen.preparation_confirmed"><option :value="undefined">未知</option><option :value="true">已确认</option><option :value="false">未确认</option></select></label></div></fieldset>
      <p>原始高度 H：{{ test.original_height_mm ?? '未知' }} mm<span v-if="computedHeight !== null">；本次 H1 − H2 = {{ computedHeight.toFixed(3) }} mm</span></p>
      <fieldset :disabled="readOnly || busy"><legend>报告信息</legend><div class="fields"><label v-for="[key, label, type] in reportFields" :key="key">{{ label }}<input v-model="report[key]" :type="type" maxlength="2000" /></label></div></fieldset>
      <label v-if="!readOnly">补录 / 修订原因<textarea v-model="reason" required minlength="2" maxlength="1000" rows="2" :disabled="readOnly || busy" /></label>
      <button v-if="!readOnly" class="primary" :disabled="busy || reason.trim().length < 2">保存补录并记录审计</button>
      <p v-if="message" role="status">{{ message }}</p>
    </form>
  </details>
</template>
<style scoped>
summary { font-weight: 700; cursor: pointer; } form { display: flex; flex-direction: column; gap: 12px; margin-top: 12px; } p { margin-top: 12px; font-size: 12px; line-height: 1.6; overflow-wrap: anywhere; } fieldset { min-width: 0; border: 1px solid var(--border); padding: 12px; border-radius: 6px; } legend { padding: 0 8px; } .fields { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 180px), 1fr)); gap: 12px; } label { display: flex; flex-direction: column; gap: 6px; color: var(--text-sec); font-size: 12px; } input, select { width: 100%; } button { align-self: start; }
</style>
