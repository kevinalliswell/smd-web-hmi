import apiClient from './client'

export function login(username, password) {
  return apiClient.post('/api/auth/login', { username, password }).then((r) => r.data.data)
}

export function fetchMe() {
  return apiClient.get('/api/auth/me').then((r) => r.data.data)
}

export function logout() {
  return apiClient.post('/api/auth/logout').then((r) => r.data.data)
}
