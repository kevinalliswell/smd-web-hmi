<script setup>
import { computed, onMounted, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { useDeviceStore } from '@/stores/device'
import { useRole } from '@/composables/useRole'
import { fetchStatus } from '@/api/status'
import { finiteValue, measurementQuality } from '@/utils/measurementQuality'
import StatusBadge from '@/components/shared/StatusBadge.vue'
import RealtimeChart from '@/components/charts/RealtimeChart.vue'
import StartTestModal from '@/components/command/StartTestModal.vue'
import StopTestModal from '@/components/command/StopTestModal.vue'
import ControlOwnershipPanel from '@/components/system/ControlOwnershipPanel.vue'

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
  const quality = (value, valid) => {
    const { text, tone } = measurementQuality(value, valid, device.dataStale)
    return { sub: text, tone }
  }
  const balance = quality(m.drip_weight_g, m.drip_weight_valid)
  if (
    finiteValue(m.drip_weight_g) !== null &&
    !device.dataStale &&
    typeof m.balance_stable === 'boolean'
  ) {
    balance.sub = m.balance_stable ? '稳定' : '波动'
    balance.tone = m.balance_stable ? '' : 'warn'
  }
  // primary：炉温与滴落重量是 GB/T 34211 熔滴试验的核心量（温度制度 + 滴落过程），
  // 其余为辅助量。等权平铺会让操作员在 6 个同样大的数字里自己找重点。
  // sub 的 tone 用于把"失效/波动"这类异常从灰字提升为警示色。
  return [
    {
      label: '炉温 PV',
      value: fmt(t.furnace_pv_deg_c),
      unit: '℃',
      sub: `SV ${fmt(t.furnace_sv_deg_c)}`,
      primary: true,
    },
    { label: '滴落重量', value: fmt(m.drip_weight_g, 2), unit: 'g', primary: true, ...balance },
    {
      label: 'N₂ 流量',
      value: fmt(g.n2_pv_l_min, 2),
      unit: 'L/min',
      sub: `SP ${fmt(g.n2_sp_l_min, 2)}`,
    },
    {
      label: 'CO 流量',
      value: fmt(g.co_pv_l_min, 2),
      unit: 'L/min',
      sub: `SP ${fmt(g.co_sp_l_min, 2)}`,
      co: true,
    },
    {
      label: '压差',
      value: fmt(m.delta_p_pa, 1),
      unit: 'Pa',
      ...quality(m.delta_p_pa, m.delta_p_valid),
    },
    {
      label: '位移',
      value: fmt(m.displacement_mm, 2),
      unit: 'mm',
      ...quality(m.displacement_mm, m.displacement_valid),
    },
  ]
})

const safety = computed(() => snapshot.value?.safety || {})
function safetyValue(key, inverted = false) {
  const value = safety.value[key]
  if (device.dataStale || ![true, false, 0, 1].includes(value)) return null
  return inverted ? !value : Boolean(value)
}

function fmt(v, digits = 1) {
  return finiteValue(v) === null ? '—' : v.toFixed(digits)
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
      <h1 class="page-title">实时总览</h1>
      <div
        class="state-badge"
        :class="{ running: isRunning }"
      >
        {{ currentState }}
      </div>
    </div>

    <!-- KPI 卡片 -->
    <div class="kpi-grid">
      <div
        v-for="kpi in kpis"
        :key="kpi.label"
        class="card kpi"
        :class="{ 'kpi-primary': kpi.primary }"
      >
        <div class="kpi-label">
          {{ kpi.label }}
          <span
            v-if="kpi.co"
            class="co-tag"
            >CO</span
          >
        </div>
        <div class="kpi-val">
          {{ kpi.value }}<span class="kpi-unit">{{ kpi.unit }}</span>
        </div>
        <div
          class="kpi-sub"
          :class="kpi.tone === 'warn' ? 'warn' : 'muted'"
        >
          {{ kpi.sub }}
        </div>
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
        <StatusBadge
          :ok="safetyValue('safety_relay_allowed')"
          label="安全继电器许可"
        />
        <StatusBadge
          :ok="safetyValue('co_alarm_l1', true)"
          label="CO 一级报警"
          ok-text="正常"
          fail-text="触发"
        />
        <StatusBadge
          :ok="safetyValue('co_alarm_l2', true)"
          label="CO 二级报警"
          ok-text="正常"
          fail-text="触发"
        />
        <StatusBadge
          :ok="safetyValue('exhaust_ok')"
          label="排风状态"
        />
        <StatusBadge
          :ok="safetyValue('emergency_stop', true)"
          label="急停"
          ok-text="未触发"
          fail-text="已触发"
        />
        <StatusBadge
          :ok="safetyValue('overtemp_alarm', true)"
          label="超温报警"
          ok-text="正常"
          fail-text="触发"
        />
      </div>

      <!-- 操作区（仅 Operator+ 可见）-->
      <div class="card ops">
        <div class="card-title">操作</div>
        <template v-if="canOperate()">
          <button
            class="primary"
            :disabled="!canStartTest"
            @click="showStart = true"
          >
            ▶ 启动试验
          </button>
          <button
            class="danger"
            :disabled="!canStopTest"
            @click="showStop = true"
          >
            ■ 停止试验
          </button>
          <p class="ops-hint muted">CO 相关操作均需二次确认，最终由控制板裁决。</p>
        </template>
        <p
          v-else
          class="ops-hint muted"
        >
          当前角色（仅查看）无操作权限。
        </p>
      </div>
    </div>

    <ControlOwnershipPanel v-if="canOperate()" />

    <StartTestModal
      v-if="showStart"
      @close="showStart = false"
    />
    <StopTestModal
      v-if="showStop"
      @close="showStop = false"
    />
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.page-head {
  display: flex;
  align-items: center;
  gap: 12px;
}
.page-title {
  font-size: 18px;
  font-weight: 700;
}
.state-badge {
  padding: 4px 12px;
  border-radius: 6px;
  background: var(--bg-card2);
  border: 1px solid var(--border);
  font-weight: 600;
}
.state-badge.running {
  color: var(--green);
  border-color: var(--green);
  animation: pulse 2s infinite;
}
/* 主指标占更宽的列并用更大字号，一眼分出主次 */
.kpi-grid {
  display: grid;
  grid-template-columns: 1.35fr 1.35fr repeat(4, 1fr);
  gap: 12px;
}
.kpi {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.kpi-label {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--text-sec);
  font-size: 12px;
}
.kpi-val {
  font-size: 24px;
  font-weight: 700;
  font-family: 'Courier New', monospace;
  line-height: 1;
}
.kpi-primary {
  border-color: var(--border-hi);
}
.kpi-primary .kpi-val {
  font-size: 34px;
}
.kpi-unit {
  font-size: 13px;
  color: var(--text-sec);
  margin-left: 4px;
}
.kpi-sub {
  font-size: 11px;
}
.kpi-sub.warn {
  color: var(--warning-text);
}
.co-tag {
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.04em;
  padding: 1px 5px;
  border-radius: 3px;
  background: var(--orange);
  color: var(--on-orange);
}
.row {
  display: grid;
  grid-template-columns: 2fr 1fr;
  gap: 16px;
}
.card-title {
  font-weight: 700;
  margin-bottom: 10px;
}
.safety {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.ops {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.ops button {
  width: 100%;
}
.ops-hint {
  font-size: 11px;
  line-height: 1.5;
}
@media (max-width: 1200px) {
  .kpi-grid {
    grid-template-columns: repeat(3, 1fr);
  }
  .kpi-primary .kpi-val {
    font-size: 28px;
  }
  .row {
    grid-template-columns: 1fr;
  }
}
@media (max-width: 700px) {
  .kpi-grid {
    grid-template-columns: repeat(2, 1fr);
  }
}
@media (max-width: 420px) {
  .kpi-grid {
    grid-template-columns: 1fr;
  }
  .kpi-val {
    font-size: 24px;
  }
}
</style>
