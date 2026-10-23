import apiClient from './client'
import { requireLoginSession, requireCurrentUser } from './session'

/** @param {string} username @param {string} password @returns {Promise<import('./contracts').LoginSession>} */
export function login(username, password) {
  /** @type {import('./contracts').LoginRequest} */
  const request = { username, password }
  return apiClient.post('/api/auth/login', request).then((r) => requireLoginSession(r.data.data))
}

export function fetchMe() {
  return apiClient.get('/api/auth/me').then((r) => requireCurrentUser(r.data.data))
}

export function logout() {
  return apiClient.post('/api/auth/logout').then((r) => r.data.data)
}
