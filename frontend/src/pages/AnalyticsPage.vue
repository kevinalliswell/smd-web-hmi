<script setup>
import { computed, onMounted, ref } from 'vue'
import { fetchTests, fetchTestSamples } from '@/api/tests'
import { compareTests } from '@/api/analytics'
import OverlayChart from '@/components/charts/OverlayChart.vue'

const tests = ref([])
const selected = ref(new Set())
const heightMm = ref('')
const rows = ref([]) // 对比结果（每个试验一项）
const series = ref([]) // 叠加曲线
const channel = ref('furnace_pv')
const loading = ref(false)
const banner = ref('')

const COLORS = ['#38bdf8', '#f59e0b', '#22c55e', '#ef4444', '#a78bfa', '#fb923c', '#60a5fa', '#e879f9']
const CHANNELS = [
  { key: 'furnace_pv', label: '炉温 (℃)' },
  { key: 'burden_temp', label: '料层温度 (℃)' },
  { key: 'delta_p', label: '压差 (Pa)' },
  { key: 'displacement', label: '位移 (mm)' },
  { key: 'drip_weight', label: '滴落重量 (g)' },
]

// 指标行定义
const METRICS = [
  { key: 'furnace_pv_max', label: '炉温峰值 (℃)', d: 1 },
  { key: 'burden_temp_max', label: '料层峰值 (℃)', d: 1 },
  { key: 'delta_p_max', label: 'ΔPmax (Pa)', d: 0 },
  { key: 'delta_p_max_temp', label: 'ΔPmax 温度 (℃)', d: 1 },
  { key: 'td_drip_temp', label: '滴落温度 Td (℃)', d: 1 },
  { key: 'displacement_max', label: '最大位移 (mm)', d: 2 },
  { key: 't10', label: 'T10 (℃)', d: 1 },
  { key: 't40', label: 'T40 (℃)', d: 1 },
]

const channelLabel = computed(() => CHANNELS.find((c) => c.key === channel.value)?.label || '')

function toggle(tid) {
  if (selected.value.has(tid)) selected.value.delete(tid)
  else if (selected.value.size < 8) selected.value.add(tid)
  selected.value = new Set(selected.value)
}

function fmt(v, d) {
  return v === null || v === undefined ? 'N/A' : Number(v).toFixed(d)
}

async function runCompare() {
  const ids = [...selected.value]
  if (!ids.length) return
  loading.value = true
  banner.value = ''
  try {
    rows.value = await compareTests(ids, heightMm.value)
    await loadSeries(ids)
  } catch (e) {
    banner.value = '对比失败：' + (e.response?.data?.message || e.message)
  } finally {
    loading.value = false
  }
}

async function loadSeries(ids) {
  const out = []
  for (let i = 0; i < ids.length; i++) {
    const s = await fetchTestSamples(ids[i], 600)
    out.push({
      label: ids[i],
      color: COLORS[i % COLORS.length],
      values: (s.points || []).map((p) => p[channel.value]),
    })
  }
  series.value = out
}

async function onChannelChange() {
  if (rows.value.length) await loadSeries(rows.value.map((r) => r.test_id))
}

onMounted(async () => {
  try {
    tests.value = (await fetchTests(1, 100)) || []
  } catch { /* ignore */ }
})
</script>

<template>
  <div class="page">
    <h1 class="page-title">数据分析 · 多试验对比</h1>
    <div v-if="banner" class="banner">{{ banner }}</div>

    <div class="layout">
      <!-- 试验选择 -->
      <div class="card picker">
        <div class="card-title">选择试验（最多 8）</div>
        <label v-for="t in tests" :key="t.test_id" class="pick">
          <input type="checkbox" :checked="selected.has(t.test_id)" @change="toggle(t.test_id)" />
          <span class="mono">{{ t.test_id }}</span>
        </label>
        <div v-if="!tests.length" class="muted">暂无试验</div>
        <div class="picker-foot">
          <div class="f">
            <label for="analytics-height">H (mm)</label>
            <input id="analytics-height" v-model="heightMm" class="height-input" placeholder="可选" />
          </div>
          <button class="primary" :disabled="loading || !selected.size" @click="runCompare">对比</button>
        </div>
      </div>

      <!-- 结果 -->
      <div class="results">
        <div class="card">
          <div class="card-head">
            <div class="card-title">叠加曲线</div>
            <div class="spacer" />
            <select v-model="channel" aria-label="对比通道" @change="onChannelChange">
              <option v-for="c in CHANNELS" :key="c.key" :value="c.key">{{ c.label }}</option>
            </select>
          </div>
          <OverlayChart :series="series" :y-label="channelLabel" />
        </div>

        <div class="card">
          <div class="card-title">关键指标对比</div>
          <table class="cmp">
            <thead>
              <tr>
                <th>指标</th>
                <th v-for="r in rows" :key="r.test_id" class="mono">{{ r.test_id }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="!rows.length"><td class="muted" :colspan="1">请选择试验并点击对比</td></tr>
              <tr v-for="m in METRICS" :key="m.key">
                <td class="mlabel">{{ m.label }}</td>
                <td v-for="r in rows" :key="r.test_id" class="mono">{{ fmt(r.metrics[m.key], m.d) }}</td>
              </tr>
              <tr v-if="rows.length" class="meta-row">
                <td class="mlabel">采样点数</td>
                <td v-for="r in rows" :key="r.test_id" class="mono">{{ r.sample_count }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.page { display: flex; flex-direction: column; gap: 16px; }
.page-title { font-size: 18px; font-weight: 700; }
.banner { background: var(--red-dim); border: 1px solid var(--red); color: var(--danger-text); border-radius: 6px; padding: 8px 12px; font-size: 12px; }
.layout { display: grid; grid-template-columns: 260px 1fr; gap: 16px; align-items: start; }
.results { display: flex; flex-direction: column; gap: 16px; }
.card-title { font-weight: 700; margin-bottom: 10px; }
.card-head { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.card-head .card-title { margin-bottom: 0; }
.spacer { flex: 1; }
.picker { display: flex; flex-direction: column; gap: 6px; }
.pick { display: flex; align-items: center; gap: 8px; font-size: 12px; cursor: pointer; }
.picker-foot { display: flex; align-items: center; gap: 10px; margin-top: 12px; }
.f { display: flex; align-items: center; gap: 6px; }
.f label { color: var(--text-sec); font-size: 12px; }
.height-input { width: 90px; }
.cmp { width: 100%; border-collapse: collapse; font-size: 12px; }
.cmp th, .cmp td { text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--border); }
.cmp th { color: var(--text-sec); font-weight: 600; font-size: 11px; }
.mlabel { color: var(--text-sec); }
.meta-row td { border-top: 2px solid var(--border-hi); }
@media (max-width: 1100px) { .layout { grid-template-columns: 1fr; } }
@media (max-width: 560px) { .picker-foot { align-items: stretch; flex-direction: column; } .f { justify-content: space-between; } }
</style>
