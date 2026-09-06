import apiClient from './client'

/** @returns {import('./contracts').MaintenanceState} */
function publicState(response) {
  const { state, target_version, current_version, upgrade_id } = response.data.data
  return { state, target_version, current_version, upgrade_id }
}

export const fetchMaintenance = () => apiClient.get('/api/system/maintenance').then(publicState)
export const prepareMaintenance = (targetVersion) =>
  apiClient
    .post('/api/system/maintenance/prepare', {
      target_version: targetVersion,
    })
    .then(publicState)
export const cancelMaintenance = () => apiClient.delete('/api/system/maintenance').then(publicState)

/** @param {string} value */
export function validSourceRecordSequence(value) {
  return /^[1-9][0-9]{0,19}$/.test(value) && BigInt(value) <= 18446744073709551615n
}

/** @param {string | undefined} firstRecordSeq @returns {Promise<import('./contracts').BackgroundJob>} */
export function requestSourceLogRecovery(firstRecordSeq) {
  if (firstRecordSeq !== undefined && !validSourceRecordSequence(firstRecordSeq))
    return Promise.reject(new Error('起始序号必须是 1 至 18446744073709551615 的整数'))
  const body = firstRecordSeq === undefined ? {} : { first_record_seq: firstRecordSeq }
  return apiClient.post('/api/system/maintenance/source-logs', body).then(r => r.data.data)
}

/** @param {string} taskId @returns {Promise<import('./contracts').BackgroundJob>} */
export function fetchSourceLogRecovery(taskId) {
  return apiClient.get(`/api/system/maintenance/source-logs/${encodeURIComponent(taskId)}`).then(r => r.data.data)
}
