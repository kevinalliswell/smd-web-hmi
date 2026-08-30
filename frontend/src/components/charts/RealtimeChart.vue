<script setup>
// 实时炉温趋势：订阅 device store 快照更新，按时间滚动追加 PV/SV。
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useDeviceStore } from '@/stores/device'
import { useChart } from '@/composables/useChart'

const props = defineProps({
  maxPoints: { type: Number, default: 180 }, // 滚动窗口（约 30 min @ 10s 粒度）
})

const device = useDeviceStore()
const { lastUpdate, furnacePV, furnaceSV } = storeToRefs(device)
const canvas = ref(null)
const chart = useChart()

onMounted(() => {
  chart.create(canvas.value, [
    {
      label: '炉温 PV (℃)',
      data: [],
      borderColor: '#38bdf8',
      backgroundColor: 'rgba(56,189,248,0.08)',
      borderWidth: 2,
      pointRadius: 0,
      tension: 0.25,
      fill: true,
    },
    {
      label: '炉温 SV (℃)',
      data: [],
      borderColor: '#f59e0b',
      borderWidth: 1.5,
      borderDash: [5, 4],
      pointRadius: 0,
      tension: 0,
    },
  ])
})

// 每次快照更新（lastUpdate 变化）追加一个时间点
watch(lastUpdate, () => {
  if (furnacePV.value === null && furnaceSV.value === null) return
  const t = new Date().toLocaleTimeString('zh-CN', { hour12: false })
  chart.push(t, [furnacePV.value, furnaceSV.value], props.maxPoints)
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
