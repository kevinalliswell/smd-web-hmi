<script setup>
// 可配置多通道趋势图：按量纲把选中通道分组，每个量纲一张独立刻度的小图。
//
// 原实现温度走左轴、其余通道全部共用右轴，Pa / mm / g / L·min⁻¹ 四种量纲叠在
// 一个刻度上，量级小的通道被压成贴底直线。现在勾选哪些通道就按量纲分几张图。
import { computed, ref, watch } from 'vue'

import MiniTrendChart from '@/components/charts/MiniTrendChart.vue'
import EmptyState from '@/components/shared/EmptyState.vue'
import { TREND_CHANNELS, groupTitle } from '@/constants/trendChannels'
import { formatMonthDayTime } from '@/utils/dateTime'

const props = defineProps({
  points: { type: Array, default: () => [] },
  visible: { type: Object, default: () => ({}) },
})

// 按量纲分组，保持 TREND_CHANNELS 的声明顺序
const groups = computed(() => {
  const byUnit = new Map()
  for (const ch of TREND_CHANNELS) {
    if (!props.visible[ch.key]) continue
    if (!byUnit.has(ch.unit)) byUnit.set(ch.unit, [])
    byUnit.get(ch.unit).push(ch)
  }
  return [...byUnit.entries()].map(([unit, channels]) => ({
    unit,
    channels,
    title: groupTitle(channels),
    // 组内通道增减时需要重建图表（数据集数量变化），故把通道列入 key
    key: `${unit}:${channels.map((c) => c.key).join(',')}`,
  }))
})

// 查询结果是静态数据，转换一次即可；与 MiniTrendChart 约定用 revision 触发刷新
const labels = ref([])
const values = ref({})
const revision = ref(0)

function rebuild() {
  labels.value = props.points.map((p) => formatMonthDayTime(p.ts))
  const out = {}
  for (const ch of TREND_CHANNELS) out[ch.key] = props.points.map((p) => p[ch.key] ?? null)
  values.value = out
  revision.value += 1
}

// points 只在重新查询时整体替换，按引用比较即可，不做深度遍历
watch(() => props.points, rebuild, { immediate: true })
</script>

<template>
  <div>
    <div v-if="!points.length" class="state-wrap">
      <EmptyState title="当前范围无数据" hint="调整时间范围或试验筛选后重新查询。" />
    </div>
    <div v-else-if="!groups.length" class="state-wrap">
      <EmptyState title="未选择通道" hint="在上方勾选需要查看的通道。" />
    </div>
    <div v-else class="chart-grid">
      <MiniTrendChart
        v-for="g in groups"
        :key="g.key"
        :title="g.title"
        :unit="g.unit"
        :series="g.channels"
        :labels="labels"
        :values="values"
        :revision="revision"
        :height="groups.length > 1 ? 200 : 320"
      />
    </div>
  </div>
</template>

<style scoped>
.chart-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px 20px;
}
/* 只有一组时铺满整行，避免右侧留大片空白 */
.chart-grid:has(> :only-child) { grid-template-columns: minmax(0, 1fr); }
.state-wrap { min-height: 220px; display: grid; place-items: center; }
@media (max-width: 900px) {
  .chart-grid { grid-template-columns: minmax(0, 1fr); }
}
</style>
