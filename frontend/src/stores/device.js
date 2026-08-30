import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

export const useDeviceStore = defineStore('device', () => {
  const snapshot = ref({})
  const commQuality = ref('offline') // online / degraded / offline
  const backendConnected = ref(false)
  const lastUpdate = ref(null)

  const currentState = computed(() => snapshot.value?.state_machine?.current_state || snapshot.value?.system?.current_state || '—')
  const furnacePV = computed(() => snapshot.value?.temperature?.furnace_pv_deg_c ?? null)
  const furnaceSV = computed(() => snapshot.value?.temperature?.furnace_sv_deg_c ?? null)
  const operationState = computed(() => snapshot.value?.system?.operation_state || 'unknown')
  const isRunning = computed(() => snapshot.value?.system?.is_running === true)
  const canStartTest = computed(() => snapshot.value?.system?.can_start_test === true)
  const canStopTest = computed(() => snapshot.value?.system?.can_stop_test === true)
  const safetyOk = computed(() => !!snapshot.value?.safety?.safety_relay_allowed)

  function updateSnapshot(data) {
    snapshot.value = data || {}
    if (data?.comm_quality) commQuality.value = data.comm_quality
    lastUpdate.value = data?.last_update || new Date().toISOString()
  }

  function setCommQuality(status) {
    commQuality.value = status
  }

  function setBackendConnected(connected) {
    backendConnected.value = connected
  }

  return {
    snapshot,
    commQuality,
    backendConnected,
    lastUpdate,
    currentState,
    furnacePV,
    furnaceSV,
    operationState,
    isRunning,
    canStartTest,
    canStopTest,
    safetyOk,
    updateSnapshot,
    setCommQuality,
    setBackendConnected,
  }
})
