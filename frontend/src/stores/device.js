import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

const RUNNING_STATES = ['Precheck', 'Heating', 'Holding', 'Reducing', 'Cooling', 'Purge']

export const useDeviceStore = defineStore('device', () => {
  const snapshot = ref({})
  const commQuality = ref('offline') // online / degraded / offline
  const lastUpdate = ref(null)

  const currentState = computed(() => snapshot.value?.state_machine?.current_state || snapshot.value?.system?.current_state || '—')
  const furnacePV = computed(() => snapshot.value?.temperature?.furnace_pv_deg_c ?? null)
  const furnaceSV = computed(() => snapshot.value?.temperature?.furnace_sv_deg_c ?? null)
  const isRunning = computed(() => RUNNING_STATES.includes(currentState.value))
  const safetyOk = computed(() => !!snapshot.value?.safety?.safety_relay_allowed)

  function updateSnapshot(data) {
    snapshot.value = data || {}
    if (data?.comm_quality) commQuality.value = data.comm_quality
    lastUpdate.value = data?.last_update || new Date().toISOString()
  }

  function setCommQuality(status) {
    commQuality.value = status
  }

  return {
    snapshot,
    commQuality,
    lastUpdate,
    currentState,
    furnacePV,
    furnaceSV,
    isRunning,
    safetyOk,
    updateSnapshot,
    setCommQuality,
  }
})
