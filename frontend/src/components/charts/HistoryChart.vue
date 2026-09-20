<script setup>
// 相同量纲共享坐标；所有小图使用同一组采样时间，便于对照。
import { onBeforeUnmount, onMounted, watch } from 'vue'
import { formatTime } from '@/utils/dateTime'
import { finiteValue } from '@/utils/measurementQuality'
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

Chart.register(
  LineController,
  LineElement,
  PointElement,
  LinearScale,
  CategoryScale,
  Tooltip,
  Legend,
)

const props = defineProps({ points: { type: Array, default: () => [] } })
// slot 与 TREND_CHANNELS 对齐:同一通道跨页同槽同色,且槽位色与报警状态色分离
const groups = [
  {
    key: 'temperature',
    title: '温度',
    unit: '℃',
    channels: [
      { key: 'furnace_pv', label: '炉温', slot: 0 },
      { key: 'burden_temp', label: '料层温度', slot: 1 },
    ],
  },
  {
    key: 'pressure',
    title: '压差',
    unit: 'Pa',
    channels: [{ key: 'delta_p', label: '压差', slot: 2 }],
  },
  {
    key: 'displacement',
    title: '位移',
    unit: 'mm',
    channels: [{ key: 'displacement', label: '位移', slot: 3 }],
  },
  {
    key: 'weight',
    title: '滴落重量',
    unit: 'g',
    channels: [{ key: 'drip_weight', label: '滴落重量', slot: 4 }],
  },
]
const canvases = {}
const charts = new Map()
const subscriptions = []

function hasData(group) {
  return props.points.some((point) =>
    group.channels.some((channel) => finiteValue(point[channel.key]) !== null),
  )
}

function buildData(group, colors) {
  return {
    labels: props.points.map((point) => formatTime(point.ts)),
    datasets: group.channels.map((channel) => ({
      label: `${channel.label} (${group.unit})`,
      data: props.points.map((point) => finiteValue(point[channel.key])),
      borderColor: colors.series[channel.slot % colors.series.length],
      seriesSlot: channel.slot,
      borderWidth: 1.5,
      pointRadius: 0,
      tension: 0,
      spanGaps: false,
    })),
  }
}

function render() {
  const colors = getChartTheme()
  for (const group of groups) {
    const existing = charts.get(group.key)
    if (existing) {
      existing.data = buildData(group, colors)
      existing.update('none')
      continue
    }
    const chart = new Chart(canvases[group.key], {
      type: 'line',
      data: buildData(group, colors),
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        interaction: { intersect: false, mode: 'index' },
        scales: {
          x: {
            grid: { color: colors.grid },
            ticks: { color: colors.tick, maxTicksLimit: 4, maxRotation: 0 },
          },
          y: {
            grid: { color: colors.grid },
            ticks: { color: colors.tick },
            title: { display: true, text: `${group.title} (${group.unit})`, color: colors.tick },
          },
        },
        plugins: { legend: { labels: { color: colors.tick, boxWidth: 12 } } },
      },
    })
    charts.set(group.key, chart)
    subscriptions.push(subscribeChartTheme(() => chart))
  }
}

onMounted(render)
watch(() => props.points, render)
onBeforeUnmount(() => {
  subscriptions.forEach((unsubscribe) => unsubscribe())
  charts.forEach((chart) => chart.destroy())
  charts.clear()
})
</script>

<template>
  <div class="history-plots">
    <figure
      v-for="group in groups"
      :key="group.key"
    >
      <figcaption>
        {{ group.title }} <span class="muted">{{ group.unit }}</span>
      </figcaption>
      <div class="chart-wrap">
        <canvas
          :ref="
            (element) => {
              canvases[group.key] = element
            }
          "
          role="img"
          :aria-label="`${group.title}历史曲线，单位${group.unit}`"
        />
        <div
          v-if="!hasData(group)"
          class="empty muted"
        >
          无有效{{ group.title }}数据
        </div>
      </div>
    </figure>
  </div>
</template>

<style scoped>
.history-plots {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
}
figure {
  margin: 0;
  min-width: 0;
}
figcaption {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  font-weight: 600;
  margin-bottom: 8px;
}
.chart-wrap {
  position: relative;
  height: 220px;
  width: 100%;
}
.empty {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  font-size: 12px;
}
@media (max-width: 700px) {
  .history-plots {
    grid-template-columns: 1fr;
  }
}
</style>
