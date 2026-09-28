<script setup>
// 实时炉温趋势：订阅 device store 快照更新，按时间滚动追加 PV/SV。
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useDeviceStore } from '@/stores/device'
import { useChart } from '@/composables/useChart'
import { getChartTheme, withAlpha } from '@/utils/chartTheme'
import { formatTime } from '@/utils/dateTime'

const props = defineProps({
  maxPoints: { type: Number, default: 180 }, // 滚动窗口（约 30 min @ 10s 粒度）
})

const device = useDeviceStore()
const { lastUpdate, furnacePV, furnaceSV, dataStale, snapshotRevision } = storeToRefs(device)
const canvas = ref(null)
const chart = useChart()

onMounted(() => {
  // PV/SV 均按系列槽位取色(炉温=槽0,与趋势/历史页一致;SV 用槽 3 并以虚线区分)——
  // 此前 SV 用的报警黄违反"曲线不得与报警同色"的约束
  const colors = getChartTheme()
  chart.create(canvas.value, [
    {
      label: '炉温 PV (℃)',
      data: [],
      borderColor: colors.series[0],
      backgroundColor: withAlpha(colors.series[0], 0.08),
      seriesSlot: 0,
      borderWidth: 2,
      pointRadius: 0,
      tension: 0.25,
      fill: true,
    },
    {
      label: '炉温 SV (℃)',
      data: [],
      borderColor: colors.series[3],
      seriesSlot: 3,
      borderWidth: 1.5,
      borderDash: [5, 4],
      pointRadius: 0,
      tension: 0,
    },
  ])
})

// 每次快照更新（lastUpdate 变化）追加一个时间点
let previousTime = null
watch(snapshotRevision, () => {
  const time = Date.parse(lastUpdate.value)
  if (time === previousTime) return
  if (previousTime !== null && time - previousTime > 5000) chart.push(formatTime(previousTime + 1), [null, null], props.maxPoints)
  previousTime = time
  const t = formatTime(lastUpdate.value)
  chart.push(t, dataStale.value ? [null, null] : [furnacePV.value, furnaceSV.value], props.maxPoints)
})

watch(dataStale, (stale) => {
  if (stale) chart.push(formatTime(Date.now()), [null, null], props.maxPoints)
})

onBeforeUnmount(() => chart.destroy())
</script>

<template>
  <div class="chart-wrap">
    <canvas ref="canvas" />
  </div>
</template>

<style scoped>
.chart-wrap { position: relative; height: 260px; width: 100%; }
@media (max-width: 560px) { .chart-wrap { height: 220px; } }
</style>
