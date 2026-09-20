<script setup>
// 单量纲小图：一张图只画同一物理量纲的 1–2 条曲线。
//
// 为什么不用双 Y 轴：把 Pa / mm / g 画在同一刻度上时，量级小的通道会被压成贴底直线，
// 试验中最关键的滴落重量与位移实际上不可读。同量纲分图后每条曲线都有自己的刻度。
// 系列色取 --series-* 槽位，与状态色（报警红/正常绿）严格分离。
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  CategoryScale,
  Chart,
  Legend,
  LinearScale,
  LineController,
  LineElement,
  PointElement,
  Tooltip,
} from 'chart.js'
import { getChartTheme, subscribeChartTheme } from '@/utils/chartTheme'

Chart.register(LineController, LineElement, PointElement, LinearScale, CategoryScale, Tooltip, Legend)

const props = defineProps({
  // 标题与单位分开：单位只在轴上出现一次，不重复进图例
  title: { type: String, required: true },
  unit: { type: String, default: '' },
  // [{ label, key, dashed?, color?, slot? }]；color 用于保留既有语义色（如 CO 橙）,
  // slot 指定全局系列槽位以保证跨页同通道同色,缺省按图内顺序取槽
  series: { type: Array, required: true },
  // labels/values 是普通（非响应式）缓冲，由 revision 递增来通知刷新：
  // 若把响应式数组直接交给 Chart.js 持有，二者会互相触发更新直至爆栈。
  labels: { type: Array, default: () => [] },
  // { key: number[] }
  values: { type: Object, default: () => ({}) },
  revision: { type: Number, default: 0 },
  height: { type: Number, default: 190 },
})

const canvas = ref(null)
let chart = null
let unsubscribeTheme = null

// 单系列不需要图例——标题已经指明了它是什么
const showLegend = computed(() => props.series.length > 1)

function buildDatasets(colors) {
  const slots = colors.series
  return props.series.map((s, i) => ({
    label: s.label,
    data: [],
    borderColor: s.color || slots[(s.slot ?? i) % slots.length],
    // 语义色显式指定时不参与主题换肤重着色
    seriesSlot: s.color ? undefined : (s.slot ?? i),
    borderWidth: 2,
    borderDash: s.dashed ? [5, 4] : undefined,
    pointRadius: 0,
    tension: 0.25,
    spanGaps: false,
  }))
}

function syncData() {
  if (!chart) return
  // 复制一份再交给 Chart.js：图表内部会改写这些数组，不能与上游缓冲共享引用
  chart.data.labels = props.labels.slice()
  props.series.forEach((s, i) => {
    chart.data.datasets[i].data = (props.values[s.key] || []).slice()
  })
  chart.update('none')
}

onMounted(() => {
  const colors = getChartTheme()
  chart = new Chart(canvas.value, {
    type: 'line',
    data: { labels: [], datasets: buildDatasets(colors) },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { intersect: false, mode: 'index' },
      scales: {
        x: { grid: { color: colors.grid }, ticks: { color: colors.tick, maxTicksLimit: 5 } },
        y: {
          grid: { color: colors.grid },
          ticks: { color: colors.tick, maxTicksLimit: 6 },
          title: { display: !!props.unit, text: props.unit, color: colors.tick },
        },
      },
      plugins: {
        legend: { display: showLegend.value, labels: { color: colors.tick, boxWidth: 12, boxHeight: 2 } },
        tooltip: { intersect: false, mode: 'index' },
      },
    },
  })
  syncData()
  unsubscribeTheme = subscribeChartTheme(() => chart)
})

watch(() => props.revision, syncData)

onBeforeUnmount(() => {
  unsubscribeTheme?.()
  chart?.destroy()
  chart = null
})
</script>

<template>
  <figure class="mini">
    <figcaption class="mini-title">
      {{ title }}
      <span v-if="unit" class="mini-unit">{{ unit }}</span>
    </figcaption>
    <div class="mini-canvas" :style="{ height: `${height}px` }">
      <canvas ref="canvas" role="img" :aria-label="`${title}趋势，单位${unit}；缺失数据以断点显示`" />
    </div>
  </figure>
</template>

<style scoped>
.mini { min-width: 0; }
.mini-title {
  display: flex;
  align-items: baseline;
  gap: 6px;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-sec);
  margin-bottom: 6px;
}
.mini-unit { font-weight: 400; color: var(--text-muted); }
.mini-canvas { position: relative; width: 100%; }
</style>
