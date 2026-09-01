<script setup>
// 当前试验多通道实时图：按物理量纲拆成小多图，滚动显示最近约 10 分钟。
//
// 原实现把炉温/料层温度放左轴、压差(Pa)+位移(mm)+重量(g) 共用右轴。三种量纲共用一个
// 刻度时，位移（~1 mm）与滴落重量（~0.5 g）被压差（~40 Pa）的量级压成贴底直线——
// 试验中最需要盯的两个指标反而读不出来。改为同量纲同图，各自独立刻度。
import { ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import MiniTrendChart from '@/components/charts/MiniTrendChart.vue'
import { useDeviceStore } from '@/stores/device'
import { formatTime } from '@/utils/dateTime'

const props = defineProps({
  maxPoints: { type: Number, default: 300 }, // 约 10 min
})

const device = useDeviceStore()
const { lastUpdate, snapshot } = storeToRefs(device)

// 采样缓冲刻意不做成响应式：1 Hz × 300 点的深度遍历既昂贵，又会与 Chart.js
// 内部状态互相触发更新。改用 revision 计数器作为唯一的刷新信号。
const labels = []
const series = {
  furnace_pv: [],
  burden_temp: [],
  delta_p: [],
  displacement: [],
  drip_weight: [],
}
const revision = ref(0)

const CHARTS = [
  {
    title: '温度',
    unit: '℃',
    series: [
      { key: 'furnace_pv', label: '炉温' },
      { key: 'burden_temp', label: '料层温度' },
    ],
  },
  { title: '压差', unit: 'Pa', series: [{ key: 'delta_p', label: '压差' }] },
  { title: '位移', unit: 'mm', series: [{ key: 'displacement', label: '位移' }] },
  { title: '滴落重量', unit: 'g', series: [{ key: 'drip_weight', label: '滴落重量' }] },
]

watch(lastUpdate, () => {
  const s = snapshot.value || {}
  const t = s.temperature || {}
  const m = s.measurement || {}
  labels.push(formatTime(lastUpdate.value))
  series.furnace_pv.push(t.furnace_pv_deg_c ?? null)
  series.burden_temp.push(m.burden_temp_deg_c ?? null)
  series.delta_p.push(m.delta_p_pa ?? null)
  series.displacement.push(m.displacement_mm ?? null)
  series.drip_weight.push(m.drip_weight_g ?? null)

  if (labels.length > props.maxPoints) {
    labels.shift()
    Object.values(series).forEach((arr) => arr.shift())
  }
  revision.value += 1
})
</script>

<template>
  <div class="chart-grid">
    <MiniTrendChart
      v-for="c in CHARTS"
      :key="c.title"
      :title="c.title"
      :unit="c.unit"
      :series="c.series"
      :labels="labels"
      :values="series"
      :revision="revision"
    />
  </div>
</template>

<style scoped>
.chart-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px 20px;
}
@media (max-width: 900px) {
  .chart-grid { grid-template-columns: minmax(0, 1fr); }
}
</style>
