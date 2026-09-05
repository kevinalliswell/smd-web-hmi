import { appCompatibility } from '@/utils/appVersion'
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

export const useDeviceStore = defineStore('device', () => {
  const snapshot = ref({})
  const commQuality = ref('offline') // online / degraded / offline
  const backendConnected = ref(false)
  const lastUpdate = ref(null)
  const snapshotRevision = ref(0)
  const dataStale = ref(true)
  let receivedAt = null
  const isFresh = computed(() => commQuality.value === 'online' && !dataStale.value && snapshot.value?.control_ready !== false)

  const currentState = computed(() => snapshot.value?.state_machine?.current_state || snapshot.value?.system?.current_state || '—')
  const furnacePV = computed(() => snapshot.value?.temperature?.furnace_pv_deg_c ?? null)
  const furnaceSV = computed(() => snapshot.value?.temperature?.furnace_sv_deg_c ?? null)
  const operationState = computed(() => snapshot.value?.system?.operation_state || 'unknown')
  const isRunning = computed(() => snapshot.value?.system?.is_running === true)
  const canStartTest = computed(() => !appCompatibility.mismatch && isFresh.value && snapshot.value?.system?.can_start_test === true)
  const canSetParameters = computed(() => !appCompatibility.mismatch && isFresh.value && snapshot.value?.system?.can_set_parameters === true)
  const canStopTest = computed(() => snapshot.value?.system?.can_stop_test === true)
  const safetyOk = computed(() => !!snapshot.value?.safety?.safety_relay_allowed)

  function updateSnapshot(data) {
    snapshot.value = data || {}
    if (data?.comm_quality) commQuality.value = data.comm_quality
    lastUpdate.value = data?.last_update || new Date().toISOString()
    receivedAt = performance.now()
    dataStale.value = data?.data_fresh === false || data?.comm_quality === 'degraded' || data?.comm_quality === 'offline'
    snapshotRevision.value += 1
  }

  function checkFreshness(now = performance.now()) {
    dataStale.value = receivedAt === null || now - receivedAt > 5000 || commQuality.value !== 'online' || snapshot.value?.data_fresh === false
  }

  function setCommQuality(status) {
    commQuality.value = status
    checkFreshness()
  }

  function setBackendConnected(connected) {
    backendConnected.value = connected
  }

  return {
    snapshot,
    commQuality,
    backendConnected,
    lastUpdate,
    snapshotRevision,
    dataStale,
    isFresh,
    canSetParameters,
    checkFreshness,
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
