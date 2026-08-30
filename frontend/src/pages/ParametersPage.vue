<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRole } from '@/composables/useRole'
import { fetchParameters, putParameters } from '@/api/parameters'
import ConfirmDialog from '@/components/shared/ConfirmDialog.vue'

const { canConfigure } = useRole()
const editable = computed(() => canConfigure())

const original = ref({}) // 设备当前参数（基线）
const form = ref({}) // 编辑副本
const deviceCrc = ref('')
const loading = ref(true)
const offline = ref(false)
const banner = ref(null) // {type:'ok'|'err', text}
const showConfirm = ref(false)
const submitting = ref(false)

// 分组与字段中文标签（未知键回退原名）
const GROUP_LABELS = {
  process: '工艺参数',
  mfc: 'MFC 参数',
  temp_program: '控温程序',
  ai_calib: 'AI 标定',
}
const FIELD_LABELS = {
  gas_switch_temp_deg_c: '切气温度 (℃)',
  end_temp_deg_c: '结束温度 (℃)',
  hold_minutes: '保持时间 (min)',
  low_temp_end_hint_deg_c: '低温结束提示 (℃)',
  total_flow_l_min: '总流量 (L/min)',
  n2_reduce_l_min: 'N₂ 还原流量 (L/min)',
  co_reduce_l_min: 'CO 还原流量 (L/min)',
  n2_purge_l_min: 'N₂ 置换流量 (L/min)',
  leak_check_n2_l_min: '气密 N₂ 流量 (L/min)',
  co_ratio_pct: 'CO 配比 (%)',
  deviation_pct: '偏差阈值 (%)',
  seg1_rate_deg_c_min: '段1速率 (℃/min)',
  seg1_to_deg_c: '段1终点 (℃)',
  seg2_rate_deg_c_min: '段2速率 (℃/min)',
  seg2_to_deg_c: '段2终点 (℃)',
  seg3_rate_deg_c_min: '段3速率 (℃/min)',
  seg3_to_deg_c: '段3终点 (℃)',
}

const groups = computed(() => Object.keys(form.value))
function groupLabel(g) {
  return GROUP_LABELS[g] || g
}
function fieldLabel(f) {
  return FIELD_LABELS[f] || f
}

// 差异：[{group, field, from, to}]
const diffs = computed(() => {
  const out = []
  for (const g of Object.keys(form.value)) {
    for (const f of Object.keys(form.value[g] || {})) {
      const a = original.value?.[g]?.[f]
      const b = form.value[g][f]
      if (Number(a) !== Number(b)) out.push({ group: g, field: f, from: a, to: b })
    }
  }
  return out
})
const dirty = computed(() => diffs.value.length > 0)

function onInput(g, f, ev) {
  const raw = ev.target.value
  const num = Number(raw)
  form.value[g][f] = raw === '' || Number.isNaN(num) ? raw : num
}

async function load() {
  loading.value = true
  banner.value = null
  try {
    const data = await fetchParameters()
    if (!data || data.params == null) {
      offline.value = true
    } else {
      offline.value = false
      original.value = structuredClone(data.params)
      form.value = structuredClone(data.params)
      deviceCrc.value = data.parameter_crc || ''
    }
  } catch (e) {
    banner.value = { type: 'err', text: '读取参数失败：' + (e.response?.data?.message || e.message) }
  } finally {
    loading.value = false
  }
}

function reset() {
  form.value = structuredClone(original.value)
  banner.value = null
}

async function submit() {
  submitting.value = true
  banner.value = null
  try {
    const result = await putParameters(form.value)
    if (result.readback_ok) {
      original.value = structuredClone(result.params || form.value)
      form.value = structuredClone(original.value)
      deviceCrc.value = result.parameter_crc || ''
      banner.value = { type: 'ok', text: `下发成功并回读一致，参数 CRC = ${result.parameter_crc}` }
    } else {
      banner.value = { type: 'err', text: '回读未确认一致' }
    }
  } catch (e) {
    const d = e.response?.data
    banner.value = { type: 'err', text: `下发失败 [${d?.error_code || ''}]：${d?.message || e.message}` }
  } finally {
    submitting.value = false
    showConfirm.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page-head">
      <h1 class="page-title">参数配置</h1>
      <div v-if="deviceCrc" class="crc mono">设备参数 CRC：{{ deviceCrc }}</div>
      <div class="spacer" />
      <button @click="load">刷新</button>
    </div>

    <div v-if="banner" class="banner" :class="banner.type">{{ banner.text }}</div>

    <div v-if="loading" class="card muted">读取参数中…</div>
    <div v-else-if="offline" class="card muted">HostComm 离线，暂无法读取参数。</div>

    <template v-else>
      <p v-if="!editable" class="muted">当前角色为只读视图；参数下发需要 Admin 及以上角色。</p>

      <div class="groups">
        <div v-for="g in groups" :key="g" class="card group">
          <div class="card-title">{{ groupLabel(g) }}</div>
          <div class="fields">
            <div v-for="f in Object.keys(form[g])" :key="f" class="field">
              <label>{{ fieldLabel(f) }}</label>
              <input
                :value="form[g][f]"
                :disabled="!editable"
                :class="{ changed: Number(original?.[g]?.[f]) !== Number(form[g][f]) }"
                @input="onInput(g, f, $event)"
              />
            </div>
          </div>
        </div>
      </div>

      <div v-if="editable" class="actions">
        <span class="muted">{{ dirty ? `${diffs.length} 项待下发` : '无改动' }}</span>
        <div class="spacer" />
        <button :disabled="!dirty" @click="reset">还原</button>
        <button class="primary" :disabled="!dirty || submitting" @click="showConfirm = true">
          下发参数
        </button>
      </div>
    </template>

    <!-- 二次确认：差异对比 -->
    <ConfirmDialog
      v-model="showConfirm"
      title="确认下发参数"
      confirm-text="确认下发"
      @confirm="submit"
    >
      <p class="muted" style="margin-bottom: 10px">
        以下 {{ diffs.length }} 项将下发至控制板（非运行态生效，下发后自动回读确认）：
      </p>
      <table class="diff">
        <thead>
          <tr><th>参数</th><th>当前</th><th>修改为</th></tr>
        </thead>
        <tbody>
          <tr v-for="d in diffs" :key="d.group + '.' + d.field">
            <td>{{ groupLabel(d.group) }} · {{ fieldLabel(d.field) }}</td>
            <td class="mono">{{ d.from }}</td>
            <td class="mono changed">{{ d.to }}</td>
          </tr>
        </tbody>
      </table>
    </ConfirmDialog>
  </div>
</template>

<style scoped>
.page { display: flex; flex-direction: column; gap: 16px; }
.page-head { display: flex; align-items: center; gap: 12px; }
.page-title { font-size: 18px; font-weight: 700; }
.crc { color: var(--text-sec); font-size: 12px; }
.spacer { flex: 1; }
.banner { border-radius: 6px; padding: 8px 12px; font-size: 12px; }
.banner.ok { background: var(--green-dim); border: 1px solid var(--green); color: var(--success-text); }
.banner.err { background: var(--red-dim); border: 1px solid var(--red); color: var(--danger-text); }
.groups { display: grid; grid-template-columns: repeat(2, 1fr); gap: 16px; }
.card-title { font-weight: 700; margin-bottom: 10px; }
.fields { display: flex; flex-direction: column; gap: 8px; }
.field { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.field label { color: var(--text-sec); font-size: 12px; }
.field input { width: 130px; text-align: right; }
.field input.changed { border-color: var(--yellow); color: var(--yellow); }
.actions { display: flex; align-items: center; gap: 10px; }
.diff { width: 100%; border-collapse: collapse; font-size: 12px; }
.diff th, .diff td { text-align: left; padding: 5px 8px; border-bottom: 1px solid var(--border); }
.diff th { color: var(--text-sec); font-weight: 600; }
.diff .changed { color: var(--yellow); }
@media (max-width: 1000px) { .groups { grid-template-columns: 1fr; } }
@media (max-width: 560px) { .page-head, .actions { align-items: stretch; flex-direction: column; } .field input { width: 112px; } }
</style>
