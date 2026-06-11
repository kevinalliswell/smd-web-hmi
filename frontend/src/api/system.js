import apiClient from './client'

export function fetchHostcommStatus() {
  return apiClient.get('/api/system/hostcomm/status').then((r) => r.data.data)
}

// 只读调试：action 限 get_status / get_parameters
export function hostcommDebug(action) {
  return apiClient.post('/api/system/hostcomm/debug', { action }).then((r) => r.data.data)
}

export function fetchSystemInfo() {
  return apiClient.get('/api/system/info').then((r) => r.data.data)
}
