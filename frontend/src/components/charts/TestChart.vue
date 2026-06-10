<script setup>
// 当前试验多通道实时图：炉温/料层温度（左轴 ℃）+ 压差(Pa)/位移(mm)/重量(g)（右轴，归一各自量纲）。
// 滚动显示最近约 10 分钟。
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
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
import { useDeviceStore } from '@/stores/device'

Chart.register(LineController, LineElement, PointElement, LinearScale, CategoryScale, Tooltip, Legend)

const props = defineProps({
  maxPoints: { type: Number, default: 300 }, // 约 10 min
})

const device = useDeviceStore()
const { lastUpdate, snapshot } = storeToRefs(device)
const canvas = ref(null)
let chart = null

const GRID = 'rgba(138,146,170,0.12)'
const TICK = '#8892aa'

onMounted(() => {
  chart = new Chart(canvas.value, {
    type: 'line',
    data: {
      labels: [],
      datasets: [
        { label: '炉温 (℃)', yAxisID: 'yTemp', data: [], borderColor: '#38bdf8', borderWidth: 2, pointRadius: 0, tension: 0.25 },
        { label: '料层温度 (℃)', yAxisID: 'yTemp', data: [], borderColor: '#a78bfa', borderWidth: 1.5, pointRadius: 0, tension: 0.25 },
        { label: '压差 (Pa)', yAxisID: 'yAux', data: [], borderColor: '#f59e0b', borderWidth: 1.5, pointRadius: 0, tension: 0.2 },
        { label: '位移 (mm)', yAxisID: 'yAux', data: [], borderColor: '#22c55e', borderWidth: 1.5, pointRadius: 0, tension: 0.2 },
        { label: '滴落重量 (g)', yAxisID: 'yAux', data: [], borderColor: '#ef4444', borderWidth: 1.5, pointRadius: 0, tension: 0.2 },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { intersect: false, mode: 'index' },
      scales: {
        x: { grid: { color: GRID }, ticks: { color: TICK, maxTicksLimit: 8 } },
        yTemp: { position: 'left', grid: { color: GRID }, ticks: { color: '#38bdf8' }, title: { display: true, text: '温度 ℃', color: '#38bdf8' } },
        yAux: { position: 'right', grid: { drawOnChartArea: false }, ticks: { color: TICK }, title: { display: true, text: 'Pa / mm / g', color: TICK } },
      },
      plugins: { legend: { labels: { color: TICK, boxWidth: 12 } } },
    },
  })
})

watch(lastUpdate, () => {
  if (!chart) return
  const s = snapshot.value || {}
  const t = s.temperature || {}
  const m = s.measurement || {}
  const label = new Date().toLocaleTimeString('zh-CN', { hour12: false })
  const values = [
    t.furnace_pv_deg_c ?? null,
    m.burden_temp_deg_c ?? null,
    m.delta_p_pa ?? null,
    m.displacement_mm ?? null,
    m.drip_weight_g ?? null,
  ]
  chart.data.labels.push(label)
  values.forEach((v, i) => chart.data.datasets[i].data.push(v))
  if (chart.data.labels.length > props.maxPoints) {
    chart.data.labels.shift()
    chart.data.datasets.forEach((d) => d.data.shift())
  }
  chart.update('none')
})

onBeforeUnmount(() => {
  chart?.destroy()
  chart = null
})
</script>

<template>
  <div class="chart-wrap">
    <canvas ref="canvas" />
  </div>
</template>

<style scoped>
.chart-wrap { position: relative; height: 320px; width: 100%; }
</style>
