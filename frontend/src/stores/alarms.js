import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

export const useAlarmsStore = defineStore('alarms', () => {
  const activeAlarms = ref([])
  const alarmHistory = ref([])

  const unackedCount = computed(() => activeAlarms.value.filter((a) => !a.ack_time).length)
  const criticalCount = computed(() => activeAlarms.value.filter((a) => a.level >= 3).length)
  const hasCritical = computed(() => criticalCount.value > 0)

  function setActive(list) {
    activeAlarms.value = list || []
  }

  function addAlarm(alarm) {
    // 去重后插入（按 alarm_id 或 alarm_code）
    const key = alarm.alarm_id ?? alarm.alarm_code
    if (!activeAlarms.value.some((a) => (a.alarm_id ?? a.alarm_code) === key)) {
      activeAlarms.value.unshift(alarm)
    }
  }

  function clearAlarm(alarmId) {
    activeAlarms.value = activeAlarms.value.filter((a) => a.alarm_id !== alarmId)
  }

  function ackAlarm(alarmId, operator, ackTime) {
    const a = activeAlarms.value.find((x) => x.alarm_id === alarmId || x.id === alarmId)
    if (a) {
      a.ack_time = ackTime
      a.ack_operator = operator
    }
  }

  return {
    activeAlarms,
    alarmHistory,
    unackedCount,
    criticalCount,
    hasCritical,
    setActive,
    addAlarm,
    clearAlarm,
    ackAlarm,
  }
})
