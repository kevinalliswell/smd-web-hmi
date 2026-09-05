import apiClient from './client'

export function compareTests(testIds, originalHeightMm = null) {
  const params = { test_ids: testIds.join(',') }
  if (originalHeightMm != null && originalHeightMm !== '') params.original_height_mm = originalHeightMm
  return apiClient.get('/api/analytics/compare', { params }).then((r) => r.data.data)
}

export function evaluateRepeatability(testIds) {
  return apiClient.post('/api/analytics/repeatability', { test_ids: testIds }).then((r) => r.data.data)
}
