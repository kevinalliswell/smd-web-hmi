import apiClient from './client'
import { requireDeviceSnapshot } from './realtime'

/** @returns {Promise<import('./contracts').DeviceSnapshot>} */
export function fetchStatus() {
  return apiClient.get('/api/status').then((r) => requireDeviceSnapshot(r.data.data))
}

export function fetchActiveAlarms() {
  return apiClient.get('/api/alarms/active').then((r) => r.data.data)
}
