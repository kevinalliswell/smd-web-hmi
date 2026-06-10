<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { useDeviceStore } from '@/stores/device'
import { useTestStore } from '@/stores/test'
import { useRole } from '@/composables/useRole'
import { resolveStage } from '@/constants/processStages'
import { sendCommand } from '@/api/commands'
import { fetchStatus } from '@/api/status'
import StageStepper from '@/components/test/StageStepper.vue'
import TestChart from '@/components/charts/TestChart.vue'
import StartTestModal from '@/components/command/StartTestModal.vue'
import StopTestModal from '@/components/command/StopTestModal.vue'
import TareModal from '@/components/command/TareModal.vue'

const device = useDeviceStore()
const test = useTestStore()
const { snapshot, currentState, isRunning } = storeToRefs(device)
const { currentTest } = storeToRefs(test)
const { canOperate } = useRole()

const showStart = ref(false)
const showStop = ref(false)
const showTare = ref(false)
const banner = ref('')
const now = ref(Date.now())
let timer = null

// —— 派生数据 ——
const gas = computed(() => snapshot.value?.gas || {})
const meas = computed(() => snapshot.value?.measurement || {})
const coRatio = computed(() => {
  const co = gas.value.co_pv_l_min ?? 0
  const n2 = gas.value.n2_pv_l_min ?? 0
  const total = co + n2
  return total > 0 ? ((co / total) * 100).toFixed(1) : '—'
})
const tareAllowed = computed(() => !!meas.value.tare_allowed)
const isHeld = computed(() => resolveStage(currentState.value).special?.level === 'warn')

const elapsed = computed(() => {
  if (!currentTest.value?.start_time) return '—'
  const start = new Date(currentTest.value.start_time).getTime()
  if (Number.isNaN(start)) return '—'
  const sec = Math.max(0, Math.floor((now.value - start) / 1000))
  const h = String(Math.floor(sec / 3600)).padStart(2, '0')
  const m = String(Math.floor((sec % 3600) / 60)).padStart(2, '0')
  const s = String(sec % 60).padStart(2, '0')
  return `${h}:${m}:${s}`
})

const procReadouts = computed(() => [
  { label: 'N₂ SP/PV', value: `${fmt(gas.value.n2_sp_l_min, 2)} / ${fmt(gas.value.n2_pv_l_min, 2)}`, unit: 'L/min' },
  { label: 'CO SP/PV', value: `${fmt(gas.value.co_sp_l_min, 2)} / ${fmt(gas.value.co_pv_l_min, 2)}`, unit: 'L/min', co: true },
  { label: 'CO 配比', value: coRatio.value, unit: '%', co: true },
  { label: '滴落重量', value: fmt(meas.value.drip_weight_g, 2), unit: 'g' },
  { label: '压差', value: fmt(meas.value.delta_p_pa, 0), unit: 'Pa' },
  { label: '位移', value: fmt(meas.value.displacement_mm, 2), unit: 'mm' },
])

function fmt(v, d = 1) {
  return v === null || v === undefined ? '—' : Number(v).toFixed(d)
}

// —— 命令 ——
async function runCommand(command, params = {}) {
  banner.value = ''
  try {
    const r = await sendCommand(command, params)
    banner.value = `${command} → ${r.result}（${r.reason_code || 'ok'}）`
  } catch (e) {
    banner.value = `${command} 失败：${e.response?.data?.message || e.message}`
  }
}

function onCmdDone(r) {
  banner.value = `命令已受理：${r?.result || ''}`
  test.loadCurrentTest().catch(() => {})
}

onMounted(async () => {
  timer = setInterval(() => (now.value = Date.now()), 1000)
  try {
    device.updateSnapshot(await fetchStatus())
  } catch {
    /* 离线忽略 */
  }
  test.loadCurrentTest().catch(() => {})
})
onBeforeUnmount(() => clearInterval(timer))
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div class="page-title">当前试验</div>
      <div class="state-badge" :class="{ running: isRunning }">{{ currentState }}</div>
      <div v-if="currentTest" class="test-id mono">{{ currentTest.test_id }}</div>
      <div class="spacer" />
      <div class="elapsed mono">⏱ {{ elapsed }}</div>
    </div>

    <div v-if="banner" class="banner">{{ banner }}</div>

    <!-- 工艺阶段步骤条 -->
    <div class="card">
      <div class="card-title">试验进度</div>
      <StageStepper :current-state="currentState" />
    </div>

    <div class="row">
      <!-- 多通道实时图 -->
      <div class="card">
        <div class="card-title">实时曲线（炉温/料层温度/压差/位移/重量，最近 ~10 min）</div>
        <TestChart />
      </div>

      <!-- 操作区 + 过程量 -->
      <div class="side">
        <div class="card ops">
          <div class="card-title">操作</div>
          <template v-if="canOperate()">
            <button class="primary" :disabled="isRunning" @click="showStart = true">▶ 启动试验</button>
            <button :disabled="!isRunning || isHeld" @click="runCommand('pause_hold')">⏸ 暂停/保持</button>
            <button :disabled="!isHeld" @click="runCommand('resume_test')">⏵ 继续试验</button>
            <button :disabled="!tareAllowed" :class="{ primary: tareAllowed }" @click="showTare = true">⚖ 天平去皮</button>
            <button class="danger" :disabled="!isRunning" @click="showStop = true">■ 停止试验</button>
            <p class="ops-hint muted">CO 相关操作（启动/停止）需二次确认；命令仅为请求，最终由控制板与硬接线联锁裁决。</p>
          </template>
          <p v-else class="ops-hint muted">当前角色（仅查看）无操作权限。</p>
        </div>

        <div class="card proc">
          <div class="card-title">过程量</div>
          <div v-for="r in procReadouts" :key="r.label" class="proc-row">
            <span class="proc-label">{{ r.label }}<span v-if="r.co" class="co-tag">CO</span></span>
            <span class="proc-val mono">{{ r.value }} <span class="muted">{{ r.unit }}</span></span>
          </div>
        </div>
      </div>
    </div>

    <StartTestModal v-if="showStart" @close="showStart = false" @done="onCmdDone" />
    <StopTestModal v-if="showStop" @close="showStop = false" @done="onCmdDone" />
    <TareModal v-if="showTare" @close="showTare = false" @done="onCmdDone" />
  </div>
</template>

<style scoped>
.page { display: flex; flex-direction: column; gap: 16px; }
.page-head { display: flex; align-items: center; gap: 12px; }
.page-title { font-size: 18px; font-weight: 700; }
.state-badge { padding: 4px 12px; border-radius: 6px; background: var(--bg-card2); border: 1px solid var(--border); font-weight: 600; }
.state-badge.running { color: var(--green); border-color: var(--green); animation: pulse 2s infinite; }
.test-id { color: var(--accent); }
.spacer { flex: 1; }
.elapsed { color: var(--text-sec); font-size: 14px; }
.banner { background: var(--accent-dim); border: 1px solid var(--accent); color: var(--accent); border-radius: 6px; padding: 8px 12px; font-size: 12px; }
.card-title { font-weight: 700; margin-bottom: 10px; }
.row { display: grid; grid-template-columns: 2fr 1fr; gap: 16px; align-items: start; }
.side { display: flex; flex-direction: column; gap: 16px; }
.ops { display: flex; flex-direction: column; gap: 8px; }
.ops button { width: 100%; }
.ops-hint { font-size: 11px; line-height: 1.5; margin-top: 4px; }
.proc-row { display: flex; justify-content: space-between; align-items: center; padding: 6px 4px; border-bottom: 1px solid var(--border); }
.proc-row:last-child { border-bottom: none; }
.proc-label { color: var(--text-sec); font-size: 12px; }
.proc-val { font-size: 14px; }
.co-tag { display: inline-block; margin-left: 4px; font-size: 9px; font-weight: 700; background: var(--orange); color: #1a1208; border-radius: 3px; padding: 0 3px; }
@media (max-width: 1100px) { .row { grid-template-columns: 1fr; } }
</style>
