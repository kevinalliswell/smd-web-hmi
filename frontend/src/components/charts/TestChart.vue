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
import { liveSample } from '@/utils/measurementQuality'

const props = defineProps({
  maxPoints: { type: Number, default: 300 }, // 约 10 min
})

const device = useDeviceStore()
const { lastUpdate, snapshot, snapshotRevision, dataStale } = storeToRefs(device)

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

// slot 与 TREND_CHANNELS 对齐:同一通道跨页同槽同色
const CHARTS = [
  {
    title: '温度',
    unit: '℃',
    series: [
      { key: 'furnace_pv', label: '炉温', slot: 0 },
      { key: 'burden_temp', label: '料层温度', slot: 1 },
    ],
  },
  { title: '压差', unit: 'Pa', series: [{ key: 'delta_p', label: '压差', slot: 2 }] },
  { title: '位移', unit: 'mm', series: [{ key: 'displacement', label: '位移', slot: 3 }] },
  { title: '滴落重量', unit: 'g', series: [{ key: 'drip_weight', label: '滴落重量', slot: 4 }] },
]

let previousTime = null
function append(time, sample) {
  labels.push(formatTime(time))
  Object.keys(series).forEach((key) => series[key].push(sample[key]))
  if (labels.length > props.maxPoints) {
    labels.shift()
    Object.values(series).forEach((values) => values.shift())
  }
  revision.value += 1
}

watch(snapshotRevision, () => {
  const currentTime = Date.parse(lastUpdate.value)
  if (currentTime === previousTime) return
  if (previousTime !== null && currentTime - previousTime > 5000) {
    append(previousTime + 1, liveSample({}, true))
  }
  append(lastUpdate.value, liveSample(snapshot.value, dataStale.value))
  previousTime = currentTime
})
watch(dataStale, (stale) => {
  if (stale && labels.length) append(Date.now(), liveSample({}, true))
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
