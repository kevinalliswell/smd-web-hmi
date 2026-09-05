import apiClient from './client'
/** @returns {Promise<{username: string | null, changed_at?: string}>} */
export const fetchControlOwner = () => apiClient.get('/api/control').then((r) => r.data.data)
/** @param {{takeover: boolean, reason: string}} intent */
export const claimControl = (intent) => apiClient.post('/api/control/claim', intent).then((r) => r.data.data)
export const releaseControl = () => apiClient.delete('/api/control').then((r) => r.data.data)
