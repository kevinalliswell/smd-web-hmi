<script setup>
import { computed, onMounted, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { useAlarmsStore } from '@/stores/alarms'
import { useDeviceStore } from '@/stores/device'
import { useRole } from '@/composables/useRole'
import { ackAlarm as apiAckAlarm } from '@/api/alarms'
import AlarmTable from '@/components/alarms/AlarmTable.vue'
import EmptyState from '@/components/shared/EmptyState.vue'

const alarms = useAlarmsStore()
const device = useDeviceStore()
const { sortedActive, alarmHistory, criticalCount, hasCritical } = storeToRefs(alarms)
const { canOperate } = useRole()

const tab = ref('active')
const banner = ref('')
const historyPage = ref(1), historyBusy = ref(false), acking = ref(false)
const historyPending = computed(() => alarmHistory.value.filter(alarm => !alarm.ack_time).length)
const alarmSnapshotKnown = computed(() => !alarms.syncing && !alarms.syncError && (!device.isV2 || (device.commQuality === 'online' && !device.dataStale)))

async function onAck(alarm) {
  if (!canOperate() || !device.canAckAlarm || acking.value) return
  acking.value = true
  banner.value = ''
  try {
    const r = await apiAckAlarm(alarm.alarm_id ?? alarm.id)
    alarms.ackAlarm(alarm.alarm_id ?? alarm.id, r.ack_operator, r.ack_time)
    banner.value = `已确认 ${alarm.alarm_code}（控制板：${r.command || 'ok'}）`
  } catch (e) {
    banner.value = `确认失败：${e.response?.data?.message || e.message}`
  } finally { acking.value = false }
}

async function loadHistory(page = historyPage.value) {
  if (historyBusy.value) return
  historyBusy.value = true
  try {
    await alarms.loadHistory(page, 50)
    historyPage.value = page
  } catch (error) {
    banner.value = `历史报警读取失败：${error.response?.data?.message || error.message}`
  } finally { historyBusy.value = false }
}
async function refresh() {
  try { await alarms.loadActive() }
  catch { banner.value = '活动报警刷新失败，请核对连接后重试' }
  await loadHistory()
}

onMounted(async () => {
  try {
    await alarms.loadActive()
  } catch {
    /* 离线忽略 */
  }
  loadHistory()
})
</script>

<template>
  <div class="page">
    <div class="page-head">
      <h1 class="page-title">报警事件</h1>
      <div class="spacer" />
      <button :disabled="historyBusy" @click="refresh">刷新</button>
    </div>

    <!-- L3 危险报警顶部横幅 -->
    <div v-if="hasCritical" class="critical-banner">
      ⚠ 存在 {{ criticalCount }} 条 L3 危险报警，请立即按 SOP §12 处置（关 CO / N₂ 置换 / 保持排风）。
    </div>

    <div v-if="banner" class="banner">{{ banner }}</div>

    <div class="tabs">
      <button :class="{ active: tab === 'active' }" @click="tab = 'active'">
        活跃报警<span v-if="sortedActive.length" class="cnt">{{ sortedActive.length }}</span>
      </button>
      <button :class="{ active: tab === 'history' }" @click="tab = 'history'; loadHistory()">
        历史报警
      </button>
    </div>

    <div v-show="tab === 'active'" class="card">
      <AlarmTable
        :alarms="sortedActive"
        show-ack
        :can-ack="canOperate() && device.canAckAlarm && !acking"
        @ack="onAck"
      />
      <EmptyState
        v-if="!sortedActive.length"
        :tone="alarmSnapshotKnown ? 'ok' : 'neutral'"
        :title="alarmSnapshotKnown ? '当前无活跃报警' : '报警状态待同步'"
        :hint="alarmSnapshotKnown ? '设备未上报未消除的报警。报警由控制板判定并推送，此处仅作显示与确认。' : '设备连接或报警对账尚未完成，不能据此认定现场无报警。'"
      />
    </div>

    <div v-show="tab === 'history'" class="card">
      <p class="history-hint" role="status">本页待确认 {{ historyPending }} 条。报警消除后仍须操作确认；请核查对应发生序号，所需报警未确认会阻止实验结束确认。</p>
      <AlarmTable :alarms="alarmHistory" show-ack show-clear :can-ack="canOperate() && device.canAckAlarm && !acking" @ack="onAck" />
      <nav class="history-pages" aria-label="历史报警分页">
        <button :disabled="historyBusy || historyPage === 1" @click="loadHistory(historyPage - 1)">上一页</button>
        <span>第 {{ historyPage }} 页</span>
        <button :disabled="historyBusy || alarmHistory.length < 50" @click="loadHistory(historyPage + 1)">下一页</button>
      </nav>
      <EmptyState v-if="!alarmHistory.length" title="暂无历史报警" hint="报警发生并消除后会归档到这里。" />
    </div>
  </div>
</template>

<style scoped>
.history-hint { color: var(--text-sec); font-size: var(--fs-base); line-height: 1.6; margin-bottom: 12px; }
.history-pages { display: flex; justify-content: space-between; align-items: center; margin-top: 12px; gap: 12px; font-size: var(--fs-base); }
.page-head { display: flex; align-items: center; gap: 12px; }
.critical-banner {
  background: var(--red-dim); border: 1px solid var(--red); color: var(--danger-text);
  border-radius: 6px; padding: 10px 14px; font-weight: 600; animation: pulse 1.5s infinite;
}
.tabs { display: flex; gap: 8px; }
.tabs button.active { background: var(--accent-dim); border-color: var(--accent); color: var(--accent); }
.cnt { margin-left: 6px; background: var(--red); color: var(--on-red); font-size: var(--fs-xs); font-weight: 700; border-radius: var(--radius-pill); padding: 1px 6px; }
@media (max-width: 560px) { .page-head { align-items: stretch; flex-direction: column; } .tabs { overflow-x: auto; } .tabs button { white-space: nowrap; } }
</style>
