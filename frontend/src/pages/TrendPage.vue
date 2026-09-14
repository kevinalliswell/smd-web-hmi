<script setup>
import { onMounted, reactive, ref } from 'vue'
import { fetchTrends } from '@/api/trends'
import { fetchTests } from '@/api/tests'
import TrendChart from '@/components/charts/TrendChart.vue'
import { TREND_CHANNELS } from '@/constants/trendChannels'

const tests = ref([])
const fromTs = ref('')
const toTs = ref('')
const testId = ref('')
const points = ref([])
const total = ref(0)
const stride = ref(1)
const loading = ref(false)
const banner = ref('')

// 默认显示温度两通道
const visible = reactive(
  TREND_CHANNELS.reduce((acc, ch) => {
    acc[ch.key] = ch.key === 'furnace_pv' || ch.key === 'burden_temp'
    return acc
  }, {}),
)

// datetime-local（无秒/时区）→ 后端 ISO 字符串前缀比较（同一部署时区）
function toIso(local) {
  return local ? local.replace('T', 'T') + ':00' : ''
}

async function query() {
  loading.value = true
  banner.value = ''
  try {
    const data = await fetchTrends({
      fromTs: toIso(fromTs.value),
      toTs: toIso(toTs.value),
      testId: testId.value || undefined,
      maxPoints: 2000,
    })
    points.value = data.points || []
    total.value = data.total
    stride.value = data.stride
    if (!points.value.length) banner.value = '该范围内无采样数据'
  } catch (e) {
    banner.value = '查询失败：' + (e.response?.data?.message || e.message)
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  try {
    tests.value = (await fetchTests(1, 100)) || []
  } catch { /* ignore */ }
  query()
})
</script>

<template>
  <div class="page">
    <h1 class="page-title">趋势曲线</h1>

    <div class="card filters">
      <div class="f">
        <label for="trend-from">起</label>
        <input id="trend-from" v-model="fromTs" type="datetime-local" />
      </div>
      <div class="f">
        <label for="trend-to">止</label>
        <input id="trend-to" v-model="toTs" type="datetime-local" />
      </div>
      <div class="f">
        <label for="trend-test">试验</label>
        <select id="trend-test" v-model="testId">
          <option value="">（全部）</option>
          <option v-for="t in tests" :key="t.test_id" :value="t.test_id">{{ t.test_id }}</option>
        </select>
      </div>
      <button class="primary" :disabled="loading" @click="query">查询</button>
      <span v-if="total" class="muted">{{ total }} 点 · 降采样 1/{{ stride }}</span>
    </div>

    <div class="card channels">
      <!-- 不在这里放固定色标：曲线颜色按通道在其量纲分组内的位置分配，
           固定色标会与图上实际颜色不符。颜色对应关系看各图的图例/标题。 -->
      <label v-for="ch in TREND_CHANNELS" :key="ch.key" class="chk">
        <input v-model="visible[ch.key]" type="checkbox" />
        <span>{{ ch.label }}</span>
        <span class="chk-unit">{{ ch.unit }}</span>
      </label>
    </div>

    <div v-if="banner" class="banner">{{ banner }}</div>

    <div class="card">
      <TrendChart :points="points" :visible="visible" />
    </div>
  </div>
</template>

<style scoped>
.page { display: flex; flex-direction: column; gap: 16px; }
.page-title { font-size: 18px; font-weight: 700; }
.filters { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
.f { display: flex; align-items: center; gap: 6px; }
.f label { color: var(--text-sec); font-size: 12px; }
.channels { display: flex; gap: 16px; flex-wrap: wrap; }
.chk-unit { color: var(--text-muted); font-size: 11px; }
.chk { display: flex; align-items: center; gap: 6px; font-size: 12px; cursor: pointer; }
.banner { background: var(--accent-dim); border: 1px solid var(--accent); color: var(--accent); border-radius: 6px; padding: 8px 12px; font-size: 12px; }
@media (max-width: 560px) { .filters, .f { align-items: stretch; flex-direction: column; } .channels { gap: 10px 14px; } }
</style>
