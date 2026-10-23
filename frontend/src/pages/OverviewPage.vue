<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { useDeviceStore } from '@/stores/device'
import { useAlarmsStore } from '@/stores/alarms'
import { useRole } from '@/composables/useRole'
import { fetchStatus } from '@/api/status'
import { overviewMetrics, completionLabel } from '@/utils/overview'
import StatusBadge from '@/components/shared/StatusBadge.vue'
import NavIcon from '@/components/shared/NavIcon.vue'
import RealtimeChart from '@/components/charts/RealtimeChart.vue'
import StartTestModal from '@/components/command/StartTestModal.vue'
import StopTestModal from '@/components/command/StopTestModal.vue'
import ControlOwnershipPanel from '@/components/system/ControlOwnershipPanel.vue'

const device = useDeviceStore()
const alarms = useAlarmsStore()
const { snapshot, currentState, isRunning, canStartTest, canStopTest } = storeToRefs(device)
const { canOperate } = useRole()
const showStart = ref(false), showStop = ref(false)
const kpis = computed(() => overviewMetrics(snapshot.value, device.dataStale))
const completion = computed(() => completionLabel(snapshot.value, device.dataStale))
const linkLabel = computed(() => {
  if (!device.backendConnected) return '后台连接中断'
  if (device.commReason === 'v2_pairing_or_runtime_unavailable') return '配对或协议运行时不可用'
  if (device.commQuality === 'offline') return '设备断连'
  if (device.dataStale) return '设备数据过期'
  return device.commQuality === 'online' ? '设备在线' : '通信降级'
})
const safetyItems = [
  { key: 'safety_relay_allowed', label: '安全继电器许可', inverted: false },
  { key: 'exhaust_ok', label: '排风状态', inverted: false },
  { key: 'emergency_stop', label: '急停', inverted: true },
  { key: 'co_alarm_l1', label: 'CO 一级报警', inverted: true },
  { key: 'co_alarm_l2', label: 'CO 二级报警', inverted: true },
  { key: 'overtemp_alarm', label: '超温报警', inverted: true },
] as const
function safetyValue(key: typeof safetyItems[number]['key'], inverted: boolean): boolean | null {
  const value = snapshot.value.safety?.[key]
  if (device.dataStale || value === undefined || value === null) return null
  return inverted ? !value : Boolean(value)
}
onMounted(async () => {
  const revision = device.snapshotRevision
  try {
    const status = await fetchStatus()
    // A late REST response must not overwrite a newer WS snapshot.
    if (device.snapshotRevision === revision) device.updateSnapshot(status)
  } catch { /* Existing link and freshness indicators remain authoritative. */ }
})
</script>

<template>
  <div class="page overview">
    <div class="page-head">
      <div><p class="eyebrow">运行监控 / OVERVIEW</p><h1 class="page-title">实时总览</h1><p class="page-subtitle">设备实时采集与安全状态</p></div>
      <div class="overview-links"><RouterLink :to="{ name: 'trend' }"><NavIcon name="trend" />趋势曲线</RouterLink><RouterLink :to="{ name: 'test' }"><NavIcon name="test" />当前试验</RouterLink></div>
    </div>

    <section class="run-strip card" aria-label="当前运行状态">
      <div class="run-state"><span class="dot" :class="device.dataStale ? 'dot-gray' : isRunning ? 'dot-green' : 'dot-gray'" /><div><span class="strip-label">设备状态</span><strong>{{ device.dataStale ? '待核实' : currentState }}</strong></div></div>
      <div><span class="strip-label">运行标识</span><strong class="mono run-id">{{ snapshot.state_machine?.test_id || snapshot.state_machine?.run_id || '暂无运行' }}</strong></div>
      <div><span class="strip-label">完成依据</span><strong>{{ completion }}</strong></div>
      <div class="link-state" :class="{ warning: device.dataStale }"><span class="dot" :class="device.dataStale ? 'dot-yellow' : 'dot-green'" />{{ linkLabel }}</div>
    </section>

    <section class="kpi-grid" aria-label="实时测量值">
      <article v-for="kpi in kpis" :key="kpi.label" class="card kpi" :class="{ 'kpi-primary': kpi.primary }">
        <div class="kpi-label">{{ kpi.label }}<span v-if="kpi.label === 'CO 流量'" class="co-tag">CO</span></div>
        <div class="kpi-val">{{ kpi.value }}<span class="kpi-unit">{{ kpi.unit }}</span></div>
        <div class="kpi-quality" :class="{ warning: kpi.warning }">{{ kpi.quality }}</div>
        <div class="kpi-sub">{{ kpi.detail || '实时采集' }}</div>
      </article>
    </section>

    <div class="dashboard-grid">
      <section class="card trend-card" aria-labelledby="overview-trend-title">
        <div class="section-head"><div><h2 id="overview-trend-title">炉温趋势</h2><p class="muted">PV / SV · 本次页面采集窗口</p></div><RouterLink :to="{ name: 'trend' }">查看全部通道 →</RouterLink></div>
        <RealtimeChart />
        <p class="chart-note">过期与断连区间保留断点，不补写测量值。</p>
      </section>
      <section class="card safety" aria-labelledby="overview-safety-title">
        <div class="section-head"><h2 id="overview-safety-title">安全状态</h2><span class="section-caption">板端反馈</span></div>
        <StatusBadge v-for="item in safetyItems" :key="item.key" :ok="safetyValue(item.key, item.inverted)" :label="item.label" :ok-text="item.key === 'emergency_stop' ? '未触发' : '正常'" :fail-text="item.inverted ? '已触发' : '异常'" />
      </section>

      <section class="card operations" aria-labelledby="overview-operations-title">
        <div class="section-head"><h2 id="overview-operations-title">运行操作</h2><span class="section-caption">{{ canOperate() ? '操作员及以上' : '观察员 · 只读' }}</span></div>
        <div v-if="canOperate()" class="operation-buttons">
          <button class="primary" :disabled="!canStartTest" @click="showStart = true">启动试验</button>
          <button class="danger" :disabled="!canStopTest" @click="showStop = true">受控停止</button>
          <RouterLink :to="{ name: 'test' }">试验详情与恢复 →</RouterLink>
        </div>
        <p v-else class="muted">当前账户可查看数据，不能提交设备控制请求。</p>
        <p class="operation-note">CO 相关操作需二次确认；控制许可和设备联锁以后台与控制板裁决为准。</p>
      </section>
      <section class="card alarm-summary" aria-labelledby="overview-alarms-title">
        <div class="section-head"><h2 id="overview-alarms-title">报警与确认</h2><RouterLink :to="{ name: 'alarms' }">查看事件 →</RouterLink></div>
        <p v-if="alarms.syncError" class="warning">报警同步失败，请到事件页核查。</p>
        <p v-else class="alarm-number"><strong>{{ alarms.unackedCount }}</strong> 条活跃报警未确认</p>
        <p v-if="snapshot.alarm?.ack_required === true" class="warning">设备仍有待确认报警，须核查消除与确认记录。</p>
        <p v-else class="muted">报警历史与消除后的确认状态请查看事件记录。</p>
      </section>
    </div>
    <ControlOwnershipPanel v-if="canOperate()" />
    <StartTestModal v-if="showStart" @close="showStart = false" />
    <StopTestModal v-if="showStop" @close="showStop = false" />
  </div>
</template>

<style scoped>
.page-head, .section-head, .overview-links, .run-state, .link-state { display: flex; align-items: center; gap: 12px; }
.page-head, .section-head { justify-content: space-between; }
.eyebrow { color: var(--text-muted); font-size: var(--fs-sm); letter-spacing: .1em; margin-bottom: 6px; }
.page-subtitle { color: var(--text-sec); margin-top: 6px; }
.overview-links a { display: flex; align-items: center; gap: 8px; border: 1px solid var(--border-hi); border-radius: var(--radius-ctl); padding: 10px 14px; background: var(--bg-card); }
.run-strip { display: grid; grid-template-columns: minmax(130px, .8fr) minmax(130px, 1fr) minmax(190px, 1.3fr) auto; gap: 20px; align-items: center; padding-block: 15px; }
.strip-label { display: block; color: var(--text-muted); font-size: var(--fs-sm); margin-bottom: 5px; }
.run-strip strong { font-weight: var(--fw-semi); }
.run-id { overflow-wrap: anywhere; }
.link-state { color: var(--success-text); font-size: var(--fs-base); }
.warning { color: var(--warning-text); }
.kpi-grid { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap: 12px; }
.kpi { position: relative; display: flex; flex-direction: column; gap: 10px; padding: 16px 14px; overflow: hidden; }
.kpi-primary { border-color: var(--border-hi); background: var(--bg-card2); }
.kpi-primary::before { content: ''; position: absolute; top: 0; left: 14px; right: 14px; height: 2px; background: var(--accent); }
.kpi-label { display: flex; align-items: center; gap: 6px; color: var(--text-sec); font-size: var(--fs-base); }
.kpi-val { font: var(--fw-semi) 28px/1.2 var(--font-mono); letter-spacing: -.04em; white-space: nowrap; }
.kpi-unit { margin-left: 5px; font: normal var(--fs-sm) var(--font-ui); color: var(--text-sec); letter-spacing: 0; }
.kpi-quality, .kpi-sub { font-size: var(--fs-sm); color: var(--text-sec); }
.kpi-quality.warning { color: var(--warning-text); }
.co-tag { font-size: var(--fs-xs); padding: 1px 4px; border-radius: var(--radius-xs); color: var(--on-orange); background: var(--orange); }
.dashboard-grid { display: grid; grid-template-columns: minmax(0, 1.8fr) minmax(280px, 1fr); gap: 16px; }
.section-head { margin-bottom: 16px; flex-wrap: wrap; }
h2 { font-size: var(--fs-lg); font-weight: var(--fw-semi); }
.section-head p { font-size: var(--fs-sm); margin-top: 5px; }
.section-head a, .section-caption { font-size: var(--fs-base); }
.section-caption { color: var(--text-muted); }
.safety { display: flex; flex-direction: column; gap: 8px; }
.safety .section-head { margin-bottom: 3px; }
.chart-note, .operation-note { font-size: var(--fs-sm); color: var(--text-sec); line-height: 1.7; margin-top: 12px; }
.operation-buttons { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
.operation-buttons button { min-width: 120px; min-height: 44px; }
.alarm-number { color: var(--text-sec); margin-bottom: 10px; }
.alarm-number strong { font: var(--fw-semi) 26px var(--font-mono); color: var(--text-pri); margin-right: 6px; }
.alarm-summary p { line-height: 1.6; }
@media (min-width: 1500px) { .kpi-val { font-size: 32px; } }
@media (max-width: 1250px) { .kpi-grid { grid-template-columns: repeat(4, minmax(0, 1fr)); } .run-strip { grid-template-columns: 1fr 1fr; } }
@media (max-width: 1050px) { .dashboard-grid { grid-template-columns: 1fr; } }
@media (max-width: 650px) { .page-head { align-items: flex-start; flex-direction: column; } .kpi-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; } .run-strip { gap: 16px; } .kpi-val { font-size: 26px; } .run-strip strong { font-size: var(--fs-base); } }
@media (max-width: 380px) { .run-strip { grid-template-columns: 1fr; } }
</style>
