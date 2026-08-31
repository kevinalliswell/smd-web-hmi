import apiClient from './client'

// 当前进行中的试验（不存在返回 null）
export function fetchCurrentTest() {
  return apiClient.get('/api/tests/current').then((r) => r.data.data)
}

export function fetchTests(page = 1, size = 20) {
  return apiClient.get('/api/tests', { params: { page, size } }).then((r) => r.data.data)
}

export function fetchNextTestId() {
  return apiClient.get('/api/tests/next-id').then((r) => r.data.data.test_id)
}

export function fetchTestDetail(testId) {
  return apiClient.get(`/api/tests/${testId}`).then((r) => r.data.data)
}

export function fetchTestSamples(testId, maxPoints = 1000) {
  return apiClient
    .get(`/api/tests/${testId}/samples`, { params: { max_points: maxPoints } })
    .then((r) => r.data.data)
}

export function fetchTestEvents(testId) {
  return apiClient.get(`/api/tests/${testId}/events`).then((r) => r.data.data)
}

export function fetchTestAlarms(testId) {
  return apiClient.get(`/api/tests/${testId}/alarms`).then((r) => r.data.data)
}
