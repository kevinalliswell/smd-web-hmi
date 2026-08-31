<script setup>
// 历史曲线回放：静态多通道折线图（炉温/料层温度 左轴 ℃；压差/位移/重量 右轴）。
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { formatTime } from '@/utils/dateTime'
import {
  Chart,
  LineController,
  LineElement,
  PointElement,
  LinearScale,
  CategoryScale,
  Tooltip,
  Legend,
} from 'chart.js'

Chart.register(LineController, LineElement, PointElement, LinearScale, CategoryScale, Tooltip, Legend)

const props = defineProps({
  points: { type: Array, default: () => [] },
})

const canvas = ref(null)
let chart = null
const GRID = 'rgba(138,146,170,0.12)'
const TICK = '#8892aa'

function buildData() {
  const labels = props.points.map((p) => formatTime(p.ts))
  const col = (key) => props.points.map((p) => p[key])
  return {
    labels,
    datasets: [
      { label: '炉温 (℃)', yAxisID: 'yTemp', data: col('furnace_pv'), borderColor: '#38bdf8', borderWidth: 1.5, pointRadius: 0, tension: 0.2 },
      { label: '料层温度 (℃)', yAxisID: 'yTemp', data: col('burden_temp'), borderColor: '#a78bfa', borderWidth: 1.5, pointRadius: 0, tension: 0.2 },
      { label: '压差 (Pa)', yAxisID: 'yAux', data: col('delta_p'), borderColor: '#f59e0b', borderWidth: 1.5, pointRadius: 0, tension: 0.2 },
      { label: '位移 (mm)', yAxisID: 'yAux', data: col('displacement'), borderColor: '#22c55e', borderWidth: 1.5, pointRadius: 0, tension: 0.2 },
      { label: '滴落重量 (g)', yAxisID: 'yAux', data: col('drip_weight'), borderColor: '#ef4444', borderWidth: 1.5, pointRadius: 0, tension: 0.2 },
    ],
  }
}

function render() {
  if (chart) {
    chart.data = buildData()
    chart.update('none')
    return
  }
  chart = new Chart(canvas.value, {
    type: 'line',
    data: buildData(),
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { intersect: false, mode: 'index' },
      scales: {
        x: { grid: { color: GRID }, ticks: { color: TICK, maxTicksLimit: 10 } },
        yTemp: { position: 'left', grid: { color: GRID }, ticks: { color: '#38bdf8' }, title: { display: true, text: '温度 ℃', color: '#38bdf8' } },
        yAux: { position: 'right', grid: { drawOnChartArea: false }, ticks: { color: TICK }, title: { display: true, text: 'Pa / mm / g', color: TICK } },
      },
      plugins: { legend: { labels: { color: TICK, boxWidth: 12 } } },
    },
  })
}

onMounted(render)
watch(() => props.points, render, { deep: false })
onBeforeUnmount(() => {
  chart?.destroy()
  chart = null
})
</script>

<template>
  <div class="chart-wrap">
    <canvas ref="canvas" />
    <div v-if="!points.length" class="empty muted">无曲线数据</div>
  </div>
</template>

<style scoped>
.chart-wrap { position: relative; height: 320px; width: 100%; }
.empty { position: absolute; inset: 0; display: grid; place-items: center; }
</style>
