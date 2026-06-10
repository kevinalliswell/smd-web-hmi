<script setup>
import { computed, onMounted, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { useAlarmsStore } from '@/stores/alarms'
import { useRole } from '@/composables/useRole'
import { ackAlarm as apiAckAlarm } from '@/api/alarms'
import AlarmTable from '@/components/alarms/AlarmTable.vue'

const alarms = useAlarmsStore()
const { sortedActive, alarmHistory, criticalCount, hasCritical } = storeToRefs(alarms)
const { canOperate } = useRole()

const tab = ref('active')
const banner = ref('')

const criticalAlarms = computed(() => sortedActive.value.filter((a) => (a.level ?? 0) >= 3))

async function onAck(alarm) {
  banner.value = ''
  try {
    const r = await apiAckAlarm(alarm.alarm_id ?? alarm.id)
    alarms.ackAlarm(alarm.alarm_id ?? alarm.id, r.ack_operator, r.ack_time)
    banner.value = `已确认 ${alarm.alarm_code}（控制板：${r.command || 'ok'}）`
  } catch (e) {
    banner.value = `确认失败：${e.response?.data?.message || e.message}`
  }
}

async function loadHistory() {
  try {
    await alarms.loadHistory()
  } catch {
    /* 忽略 */
  }
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
      <div class="page-title">报警事件</div>
      <div class="spacer" />
      <button @click="alarms.loadActive()">刷新</button>
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
        :can-ack="canOperate()"
        @ack="onAck"
      />
    </div>

    <div v-show="tab === 'history'" class="card">
      <AlarmTable :alarms="alarmHistory" show-ack show-clear :can-ack="false" />
    </div>
  </div>
</template>

<style scoped>
.page { display: flex; flex-direction: column; gap: 16px; }
.page-head { display: flex; align-items: center; gap: 12px; }
.page-title { font-size: 18px; font-weight: 700; }
.spacer { flex: 1; }
.critical-banner {
  background: var(--red-dim); border: 1px solid var(--red); color: #fca5a5;
  border-radius: 6px; padding: 10px 14px; font-weight: 600; animation: pulse 1.5s infinite;
}
.banner { background: var(--accent-dim); border: 1px solid var(--accent); color: var(--accent); border-radius: 6px; padding: 8px 12px; font-size: 12px; }
.tabs { display: flex; gap: 8px; }
.tabs button.active { background: var(--accent-dim); border-color: var(--accent); color: var(--accent); }
.cnt { margin-left: 6px; background: var(--red); color: #fff; font-size: 10px; font-weight: 700; border-radius: 8px; padding: 1px 6px; }
</style>
