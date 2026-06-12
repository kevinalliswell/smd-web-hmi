import apiClient from './client'

export function compareTests(testIds, originalHeightMm = null) {
  const params = { test_ids: testIds.join(',') }
  if (originalHeightMm != null && originalHeightMm !== '') params.original_height_mm = originalHeightMm
  return apiClient.get('/api/analytics/compare', { params }).then((r) => r.data.data)
}
