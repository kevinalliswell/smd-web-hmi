import apiClient from './client'

// 当前进行中的试验（不存在返回 null）
export function fetchCurrentTest() {
  return apiClient.get('/api/tests/current').then((r) => r.data.data)
}

export function fetchTests(page = 1, size = 20) {
  return apiClient.get('/api/tests', { params: { page, size } }).then((r) => r.data.data)
}

export function fetchTestDetail(testId) {
  return apiClient.get(`/api/tests/${testId}`).then((r) => r.data.data)
}
