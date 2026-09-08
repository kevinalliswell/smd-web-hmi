import apiClient from './client'
/** @typedef {import('./generated/schema').components['schemas']['RecoverySummary']} RecoverySummary */
/** @typedef {import('./generated/schema').components['schemas']['RecoveryDetail']} RecoveryDetail */
/** @typedef {import('./generated/schema').components['schemas']['RecoveryBindingRequest']} RecoveryBindingRequest */
/** @typedef {import('./generated/schema').components['schemas']['RecoveryReviewRequest']} RecoveryReviewRequest */

/** @returns {Promise<RecoverySummary[]>} */
export function fetchRunRecoveries(limit = 50, offset = 0) {
  return apiClient.get('/api/run-recoveries', { params: { limit, offset } }).then(r => r.data.data)
}
/** @param {string} id @returns {Promise<RecoveryDetail>} */
export function fetchRunRecovery(id) {
  return apiClient.get(`/api/run-recoveries/${encodeURIComponent(id)}`).then(r => r.data.data)
}
/** @param {string} id @param {RecoveryBindingRequest} body @returns {Promise<RecoverySummary>} */
export function bindRunRecovery(id, body) {
  return apiClient.post(`/api/run-recoveries/${encodeURIComponent(id)}/binding`, body).then(r => r.data.data)
}
/** @param {string} id @param {RecoveryReviewRequest} body @returns {Promise<RecoverySummary>} */
export function replayRunRecovery(id, body) {
  return apiClient.post(`/api/run-recoveries/${encodeURIComponent(id)}/replays`, body).then(r => r.data.data)
}
