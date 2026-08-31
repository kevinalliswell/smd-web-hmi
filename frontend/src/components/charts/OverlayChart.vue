<script setup>
// 多试验叠加曲线：每个试验一条线，X 轴为采样序号（对齐起点），Y 为所选通道。
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
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
import { getChartTheme, subscribeChartTheme } from '@/utils/chartTheme'

Chart.register(LineController, LineElement, PointElement, LinearScale, CategoryScale, Tooltip, Legend)

const props = defineProps({
  // series: [{ label, color, values: number[] }]
  series: { type: Array, default: () => [] },
  yLabel: { type: String, default: '' },
})

const canvas = ref(null)
let chart = null
let unsubscribeTheme = null

function build() {
  const maxLen = props.series.reduce((m, s) => Math.max(m, s.values.length), 0)
  const labels = Array.from({ length: maxLen }, (_, i) => i)
  const datasets = props.series.map((s) => ({
    label: s.label,
    data: s.values,
    borderColor: s.color,
    borderWidth: 1.5,
    pointRadius: 0,
    tension: 0.2,
  }))
  return { labels, datasets }
}

function render() {
  const colors = getChartTheme()
  if (chart) {
    chart.data = build()
    chart.options.scales.y.title.text = props.yLabel
    chart.update('none')
    return
  }
  chart = new Chart(canvas.value, {
    type: 'line',
    data: build(),
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { intersect: false, mode: 'index' },
      scales: {
        x: { grid: { color: colors.grid }, ticks: { color: colors.tick, maxTicksLimit: 12 }, title: { display: true, text: '采样序号', color: colors.tick } },
        y: { grid: { color: colors.grid }, ticks: { color: colors.tick }, title: { display: true, text: props.yLabel, color: colors.tick } },
      },
      plugins: { legend: { labels: { color: colors.tick, boxWidth: 12 } } },
    },
  })
}

onMounted(() => {
  render()
  unsubscribeTheme = subscribeChartTheme(() => chart)
})
watch(() => [props.series, props.yLabel], render, { deep: true })
onBeforeUnmount(() => {
  unsubscribeTheme?.()
  chart?.destroy()
  chart = null
})
</script>

<template>
  <div class="chart-wrap">
    <canvas ref="canvas" />
    <div v-if="!series.length" class="empty muted">勾选试验并对比后显示叠加曲线</div>
  </div>
</template>

<style scoped>
.chart-wrap { position: relative; height: 360px; width: 100%; }
.empty { position: absolute; inset: 0; display: grid; place-items: center; }
@media (max-width: 560px) { .chart-wrap { height: 280px; } }
</style>
