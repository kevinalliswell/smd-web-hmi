import apiClient from './client'

/** @returns {import('./contracts').MaintenanceState} */
function publicState(response) {
  const { state, target_version, current_version, upgrade_id } = response.data.data
  return { state, target_version, current_version, upgrade_id }
}

export const fetchMaintenance = () => apiClient.get('/api/system/maintenance').then(publicState)
export const prepareMaintenance = (targetVersion) => apiClient.post('/api/system/maintenance/prepare', {
  target_version: targetVersion,
}).then(publicState)
export const cancelMaintenance = () => apiClient.delete('/api/system/maintenance').then(publicState)
