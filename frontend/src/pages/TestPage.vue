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
import FaultResetControl from '@/components/command/FaultResetControl.vue'
import ConfirmDialog from '@/components/shared/ConfirmDialog.vue'
import OperationResult from '@/components/command/OperationResult.vue'
import TareModal from '@/components/command/TareModal.vue'

const device = useDeviceStore()
const test = useTestStore()
const { snapshot, currentState, isRunning, canStartTest, canStopTest } = storeToRefs(device)
const { currentTest } = storeToRefs(test)
const { canOperate } = useRole()

const showStart = ref(false)
const showStop = ref(false)
const showTare = ref(false)
const showAck = ref(false), ackBusy = ref(false), ackOperationId = ref(null)
const phaseLabels = { booting: '设备启动', idle: '待机', preparing: '启动预检', measuring: '测定中', safe_disposal: '安全处置', cooling: '冷却中', completed: '等待结束确认', fault: '故障', maintenance: '维护' }
const run = computed(() => snapshot.value.state_machine || {})
const outcomes = { pending: '待判定', valid_candidate: '有效候选，须核验数据', aborted: '操作终止', invalid: '无效' }
const banner = ref('')
const now = ref(Date.now())
let timer = null

// —— 派生数据 ——
const gas = computed(() => snapshot.value?.gas || {})
const meas = computed(() => snapshot.value?.measurement || {})
const coRatio = computed(() => {
  const co = gas.value.co_pv_l_min
  const n2 = gas.value.n2_pv_l_min
  if (co == null || n2 == null || device.dataStale) return '—'
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
  if (!device.supportsCommand(command)) return
  banner.value = ''
  try {
    const r = await sendCommand(command, params)
    banner.value = `${command} → ${r.result}（${r.reason_code || 'ok'}）`
  } catch (e) {
    banner.value = `${command} 失败：${e.response?.data?.message || e.message}`
  }
}

async function acknowledgeRun() {
  if (!device.canAckRun || ackBusy.value || ackOperationId.value) return
  ackBusy.value = true
  try {
    finishAck(await sendCommand('ack_run'))
  } catch (error) {
    ackOperationId.value = error.outcomeUnknown ? error.operationId : null
    if (ackOperationId.value) showAck.value = false
    banner.value = error.message
  } finally { ackBusy.value = false }
}
function finishAck(result) {
  ackOperationId.value = null
  showAck.value = false
  banner.value = result.wire_reconciled && result.operation_status === 'unknown' ? '已核查，结束确认执行结果仍未知；请核对当前设备状态。' : result.operation_status === 'rejected' ? `结束确认被拒绝：${result.reason_code || '请核查设备'}` : '结束确认已应用，正在刷新设备状态'
  fetchStatus().then(device.updateSnapshot).catch(() => {})
  test.loadCurrentTest().catch(() => {})
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
      <h1 class="page-title">当前试验</h1>
      <div class="state-badge" :class="{ running: isRunning }">{{ currentState }}</div>
      <div v-if="currentTest" class="test-id mono">{{ currentTest.test_id }}</div>
      <div class="spacer" />
      <div class="elapsed mono">⏱ {{ elapsed }}</div>
    </div>

    <div v-if="banner" class="banner">{{ banner }}</div>
    <p v-if="snapshot.system?.run_recovery_required" class="card" role="status">
      本次控制板运行存在归属核查或原始数据回放待办。安全停止仍可使用；结束确认依据板端安全完成与核查状态，最终报告还需回放完成。
      <RouterLink to="/run-recoveries">打开运行恢复</RouterLink>
    </p>

    <!-- 工艺阶段步骤条 -->
    <div class="card">
      <div class="card-title">试验进度</div>
      <template v-if="device.isV2">
        <p class="v2-phase">{{ phaseLabels[currentState] || '未知阶段' }}<span v-if="Number.isInteger(run.stage_index)"> · 配方阶段 {{ run.stage_index + 1 }}</span></p>
        <dl class="v2-evidence">
          <dt>测定自然完成</dt><dd>{{ run.measurement_complete ? '已确认' : '尚未确认' }}</dd>
          <dt>安全处置及冷却完成</dt><dd>{{ run.safe_complete ? '已确认' : '尚未确认' }}</dd>
          <dt>实验结果状态</dt><dd>{{ outcomes[run.outcome] || '未知' }}</dd>
        </dl>
        <p class="ops-hint muted">停止受理后继续记录安全处置和冷却。测定、安全完成与操作确认分别留证。</p>
      </template>
      <StageStepper v-else :current-state="currentState" />
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
            <button class="primary" :disabled="!canStartTest" @click="showStart = true">▶ 启动试验</button>
            <button v-if="device.supportsCommand('pause_hold')" :disabled="!isRunning || isHeld" @click="runCommand('pause_hold')">⏸ 暂停/保持</button>
            <button v-if="device.supportsCommand('resume_test')" :disabled="!isHeld" @click="runCommand('resume_test')">⏵ 继续试验</button>
            <!-- 去皮是日常校准，不与"启动试验"（CO 工艺、需二次确认）共用高亮样式：
                 按钮的视觉权重应当对应操作后果的严重性 -->
            <button v-if="device.supportsCommand('tare_balance')" :disabled="!tareAllowed" @click="showTare = true">⚖ 天平去皮</button>
            <button class="danger" :disabled="!canStopTest" @click="showStop = true">■ 停止试验</button>
            <button v-if="device.isV2" :disabled="!device.canAckRun || Boolean(ackOperationId)" @click="showAck = true">确认本次实验结束</button>
            <p v-if="device.isV2 && snapshot.alarm?.ack_required" class="ops-hint" role="status">请先确认所需报警，再确认实验结束。<RouterLink to="/alarms">前往报警事件（已消除的报警也需确认）</RouterLink></p>
            <p v-else-if="device.isV2 && snapshot._v2?.alarms_reconciled === false" class="ops-hint muted" role="status">报警状态正在对账，完成后再确认实验结束。</p>
            <FaultResetControl v-if="device.isV2" />
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

    <OperationResult v-if="ackOperationId" :operation-id="ackOperationId" @resolved="finishAck" />
    <ConfirmDialog v-model="showAck" title="确认本次实验结束" confirm-text="确认结束" :busy="ackBusy" :confirm-disabled="!device.canAckRun || Boolean(ackOperationId)" :close-on-confirm="false" @confirm="acknowledgeRun">
      <p>控制板已确认安全处置与冷却完成。确认将结束本次设备运行占用；归档质量和标准符合性仍按独立证据判定。</p>
    </ConfirmDialog>
    <StartTestModal v-if="showStart" @close="showStart = false" @done="onCmdDone" />
    <StopTestModal v-if="showStop" @close="showStop = false" @done="onCmdDone" />
    <TareModal v-if="showTare" @close="showTare = false" @done="onCmdDone" />
  </div>
</template>

<style scoped>
.v2-phase { font-weight: 600; margin-bottom: 12px; }
.v2-evidence { display: grid; grid-template-columns: auto 1fr; gap: 8px 16px; font-size: var(--fs-base); }
.v2-evidence dt { color: var(--text-sec); }
.v2-evidence dd { margin: 0; }
.page-head { display: flex; align-items: center; gap: 12px; }
.state-badge { padding: 4px 14px; border-radius: var(--radius-pill); background: var(--bg-card2); border: 1px solid var(--border); font-weight: 600; font-size: var(--fs-base); }
.state-badge.running { color: var(--green); border-color: var(--green); animation: pulse 2s infinite; }
.test-id { color: var(--accent); }
.elapsed { color: var(--text-sec); font-size: var(--fs-lg); }
.row { display: grid; grid-template-columns: 2fr 1fr; gap: 16px; align-items: start; }
.side { display: flex; flex-direction: column; gap: 16px; }
.ops { display: flex; flex-direction: column; gap: 8px; }
.ops button { width: 100%; }
.ops-hint { font-size: var(--fs-sm); line-height: 1.5; margin-top: 4px; }
.proc-row { display: flex; justify-content: space-between; align-items: center; padding: 6px 4px; border-bottom: 1px solid var(--border); }
.proc-row:last-child { border-bottom: none; }
.proc-label { color: var(--text-sec); font-size: var(--fs-base); }
.proc-val { font-size: var(--fs-lg); }
.co-tag { display: inline-block; margin-left: 4px; font-size: var(--fs-2xs); font-weight: 700; background: var(--orange); color: var(--on-orange); border-radius: 3px; padding: 0 3px; }
@media (max-width: 1100px) { .row { grid-template-columns: 1fr; } }
@media (max-width: 560px) { .page-head { align-items: flex-start; flex-wrap: wrap; } .elapsed { width: 100%; } }
</style>
