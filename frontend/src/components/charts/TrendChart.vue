<script setup>
// 可配置多通道趋势图：按 visible 渲染选中通道，温度走左轴，其余走右轴。
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

import { TREND_CHANNELS } from '@/constants/trendChannels'
import { getChartTheme, subscribeChartTheme } from '@/utils/chartTheme'
import { formatMonthDayTime } from '@/utils/dateTime'

Chart.register(LineController, LineElement, PointElement, LinearScale, CategoryScale, Tooltip, Legend)

const props = defineProps({
  points: { type: Array, default: () => [] },
  visible: { type: Object, default: () => ({}) },
})

const canvas = ref(null)
let chart = null
let unsubscribeTheme = null

function datasets(colors) {
  return TREND_CHANNELS.filter((ch) => props.visible[ch.key]).map((ch) => ({
    label: ch.label,
    yAxisID: ch.axis,
    data: props.points.map((p) => p[ch.key]),
    // CO 用语义橙并标记 seriesSemantic(换肤时按 --orange 刷新);其余按槽位取系列色
    borderColor: ch.semantic === 'co' ? colors.co : colors.series[ch.slot % colors.series.length],
    seriesSlot: ch.semantic === 'co' ? undefined : ch.slot,
    seriesSemantic: ch.semantic,
    borderWidth: 1.5,
    pointRadius: 0,
    tension: 0.2,
  }))
}

function render() {
  const colors = getChartTheme()
  const data = { labels: props.points.map((p) => formatMonthDayTime(p.ts)), datasets: datasets(colors) }
  if (chart) {
    chart.data = data
    chart.update('none')
    return
  }
  chart = new Chart(canvas.value, {
    type: 'line',
    data,
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { intersect: false, mode: 'index' },
      scales: {
        x: { grid: { color: colors.grid }, ticks: { color: colors.tick, maxTicksLimit: 12 } },
        yTemp: { position: 'left', grid: { color: colors.grid }, ticks: { color: colors.accent }, title: { display: true, text: '温度 ℃', color: colors.accent } },
        yAux: { position: 'right', grid: { drawOnChartArea: false }, ticks: { color: colors.tick } },
      },
      plugins: { legend: { labels: { color: colors.tick, boxWidth: 12 } } },
    },
  })
}

onMounted(() => {
  render()
  unsubscribeTheme = subscribeChartTheme(() => chart)
})
watch(() => [props.points, props.visible], render, { deep: true })
onBeforeUnmount(() => {
  unsubscribeTheme?.()
  chart?.destroy()
  chart = null
})
</script>

<template>
  <div class="chart-wrap">
    <canvas ref="canvas" />
    <div v-if="!points.length" class="empty muted">无数据，请调整时间范围后查询</div>
  </div>
</template>

<style scoped>
.chart-wrap { position: relative; height: 380px; width: 100%; }
.empty { position: absolute; inset: 0; display: grid; place-items: center; }
@media (max-width: 560px) { .chart-wrap { height: 280px; } }
</style>
