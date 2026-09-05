import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { fetchActiveAlarms, fetchAlarmHistory } from '@/api/alarms'

export const useAlarmsStore = defineStore('alarms', () => {
  const activeAlarms = ref([])
  const alarmHistory = ref([])
  const syncError = ref('')
  const syncing = ref(false)
  let generation = 0
  let pendingEvents = []

  // 按级别降序（L3 在前），同级按发生时间倒序
  const sortedActive = computed(() =>
    [...activeAlarms.value].sort(
      (a, b) => (b.level ?? 0) - (a.level ?? 0) || String(b.occur_time).localeCompare(String(a.occur_time)),
    ),
  )
  const unackedCount = computed(() => activeAlarms.value.filter((a) => !a.ack_time).length)
  const criticalCount = computed(() => activeAlarms.value.filter((a) => (a.level ?? 0) >= 3).length)
  const hasCritical = computed(() => criticalCount.value > 0)

  function _key(a) {
    return a.alarm_id ?? a.id ?? a.alarm_code
  }

  function setActive(list) {
    activeAlarms.value = (list || []).map((a) => ({ ...a, alarm_id: a.alarm_id ?? a.id }))
  }

  function addAlarm(alarm) {
    if (syncing.value) pendingEvents.push(() => addAlarm(alarm))
    const k = _key(alarm)
    if (!activeAlarms.value.some((a) => _key(a) === k)) {
      activeAlarms.value.unshift({ ...alarm, alarm_id: alarm.alarm_id ?? alarm.id })
    }
  }

  function clearAlarm(alarmId) {
    if (syncing.value) pendingEvents.push(() => clearAlarm(alarmId))
    // 报警消除：从活跃列表移出（原始记录仍在后端 alarm_log，不删除）
    activeAlarms.value = activeAlarms.value.filter((a) => _key(a) !== alarmId && a.alarm_code !== alarmId)
  }

  function ackAlarm(alarmId, operator, ackTime) {
    if (syncing.value) pendingEvents.push(() => ackAlarm(alarmId, operator, ackTime))
    const a = activeAlarms.value.find((x) => _key(x) === alarmId)
    if (a) {
      a.ack_time = ackTime
      a.ack_operator = operator
    }
  }

  async function loadActive(isCurrent = () => true) {
    const activeGeneration = ++generation
    pendingEvents = []
    syncing.value = true
    syncError.value = ''
    try {
      const list = await fetchActiveAlarms()
      if (activeGeneration !== generation || !isCurrent()) return
      // Pushes received after the request started must not be replaced by an older REST snapshot.
      const events = pendingEvents
      syncing.value = false
      setActive(list)
      for (const replay of events) replay()
    } catch (error) {
      if (activeGeneration === generation && isCurrent()) syncError.value = '报警状态同步失败，请刷新重试'
      throw error
    } finally {
      if (activeGeneration === generation) { syncing.value = false; pendingEvents = [] }
    }
  }

  async function loadHistory(page = 1, size = 50) {
    alarmHistory.value = await fetchAlarmHistory(page, size)
    return alarmHistory.value
  }

  return {
    activeAlarms,
    syncError,
    syncing,
    alarmHistory,
    sortedActive,
    unackedCount,
    criticalCount,
    hasCritical,
    setActive,
    addAlarm,
    clearAlarm,
    ackAlarm,
    loadActive,
    loadHistory,
  }
})
