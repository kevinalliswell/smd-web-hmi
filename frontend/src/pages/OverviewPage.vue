<script setup>
import { computed, onMounted, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { useDeviceStore } from '@/stores/device'
import { useRole } from '@/composables/useRole'
import { fetchStatus } from '@/api/status'
import StatusBadge from '@/components/shared/StatusBadge.vue'
import RealtimeChart from '@/components/charts/RealtimeChart.vue'
import StartTestModal from '@/components/command/StartTestModal.vue'
import StopTestModal from '@/components/command/StopTestModal.vue'

const device = useDeviceStore()
const { snapshot, currentState, isRunning, canStartTest, canStopTest } = storeToRefs(device)
const { canOperate } = useRole()

const showStart = ref(false)
const showStop = ref(false)

// KPI 卡片定义（数据来自 WebSocket status_update 驱动的 device store）
const kpis = computed(() => {
  const t = snapshot.value?.temperature || {}
  const g = snapshot.value?.gas || {}
  const m = snapshot.value?.measurement || {}
  return [
    { label: '炉温 PV', value: fmt(t.furnace_pv_deg_c), unit: '℃', sub: `SV ${fmt(t.furnace_sv_deg_c)}` },
    { label: 'N₂ 流量', value: fmt(g.n2_pv_l_min, 2), unit: 'L/min', sub: `SP ${fmt(g.n2_sp_l_min, 2)}` },
    { label: 'CO 流量', value: fmt(g.co_pv_l_min, 2), unit: 'L/min', sub: `SP ${fmt(g.co_sp_l_min, 2)}` },
    { label: '滴落重量', value: fmt(m.drip_weight_g, 2), unit: 'g', sub: m.balance_stable ? '稳定' : '波动' },
    { label: '压差', value: fmt(m.delta_p_pa, 1), unit: 'Pa', sub: m.delta_p_valid ? '有效' : '失效' },
    { label: '位移', value: fmt(m.displacement_mm, 2), unit: 'mm', sub: m.displacement_valid ? '有效' : '失效' },
  ]
})

const safety = computed(() => snapshot.value?.safety || {})

function fmt(v, digits = 1) {
  return v === null || v === undefined ? '—' : Number(v).toFixed(digits)
}

onMounted(async () => {
  // 首次进入用 REST 兜底拉一次（后续由 WebSocket 实时刷新）
  try {
    device.updateSnapshot(await fetchStatus())
  } catch {
    /* 离线时忽略 */
  }
})
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div class="page-title">实时总览</div>
      <div class="state-badge" :class="{ running: isRunning }">{{ currentState }}</div>
    </div>

    <!-- KPI 卡片 -->
    <div class="kpi-grid">
      <div v-for="kpi in kpis" :key="kpi.label" class="card kpi">
        <div class="kpi-label">{{ kpi.label }}</div>
        <div class="kpi-val">{{ kpi.value }}<span class="kpi-unit">{{ kpi.unit }}</span></div>
        <div class="kpi-sub muted">{{ kpi.sub }}</div>
      </div>
    </div>

    <!-- 实时炉温趋势 -->
    <div class="card">
      <div class="card-title">炉温趋势（实时）</div>
      <RealtimeChart />
    </div>

    <div class="row">
      <!-- 安全状态 -->
      <div class="card safety">
        <div class="card-title">安全状态</div>
        <StatusBadge :ok="!!safety.safety_relay_allowed" label="安全继电器许可" />
        <StatusBadge :ok="!safety.co_alarm_l1" label="CO 一级报警" ok-text="正常" fail-text="触发" />
        <StatusBadge :ok="!safety.co_alarm_l2" label="CO 二级报警" ok-text="正常" fail-text="触发" />
        <StatusBadge :ok="!!safety.exhaust_ok" label="排风状态" />
        <StatusBadge :ok="!safety.emergency_stop" label="急停" ok-text="未触发" fail-text="已触发" />
        <StatusBadge :ok="!safety.overtemp_alarm" label="超温报警" ok-text="正常" fail-text="触发" />
      </div>

      <!-- 操作区（仅 Operator+ 可见）-->
      <div class="card ops">
        <div class="card-title">操作</div>
        <template v-if="canOperate()">
          <button class="primary" :disabled="!canStartTest" @click="showStart = true">▶ 启动试验</button>
          <button class="danger" :disabled="!canStopTest" @click="showStop = true">■ 停止试验</button>
          <p class="ops-hint muted">CO 相关操作均需二次确认，最终由控制板裁决。</p>
        </template>
        <p v-else class="ops-hint muted">当前角色（仅查看）无操作权限。</p>
      </div>
    </div>

    <StartTestModal v-if="showStart" @close="showStart = false" />
    <StopTestModal v-if="showStop" @close="showStop = false" />
  </div>
</template>

<style scoped>
.page { display: flex; flex-direction: column; gap: 16px; }
.page-head { display: flex; align-items: center; gap: 12px; }
.page-title { font-size: 18px; font-weight: 700; }
.state-badge { padding: 4px 12px; border-radius: 6px; background: var(--bg-card2); border: 1px solid var(--border); font-weight: 600; }
.state-badge.running { color: var(--green); border-color: var(--green); animation: pulse 2s infinite; }
.kpi-grid { display: grid; grid-template-columns: repeat(6, 1fr); gap: 12px; }
.kpi { display: flex; flex-direction: column; gap: 6px; }
.kpi-label { color: var(--text-sec); font-size: 12px; }
.kpi-val { font-size: 28px; font-weight: 700; font-family: 'Courier New', monospace; line-height: 1; }
.kpi-unit { font-size: 13px; color: var(--text-sec); margin-left: 4px; }
.kpi-sub { font-size: 11px; }
.row { display: grid; grid-template-columns: 2fr 1fr; gap: 16px; }
.card-title { font-weight: 700; margin-bottom: 10px; }
.safety { display: flex; flex-direction: column; gap: 6px; }
.ops { display: flex; flex-direction: column; gap: 10px; }
.ops button { width: 100%; }
.ops-hint { font-size: 11px; line-height: 1.5; }
@media (max-width: 1200px) { .kpi-grid { grid-template-columns: repeat(3, 1fr); } .row { grid-template-columns: 1fr; } }
</style>
