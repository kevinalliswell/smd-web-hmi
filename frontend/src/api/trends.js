import apiClient from './client'

// 历史趋势查询（时间窗 + 可选 test_id + 降采样）
/** @param {{ fromTs?: string, toTs?: string, testId?: string, maxPoints?: number }} options */
export function fetchTrends({ fromTs, toTs, testId, maxPoints = 2000 } = {}) {
  const params = {}
  if (fromTs) params.from_ts = fromTs
  if (toTs) params.to_ts = toTs
  if (testId) params.test_id = testId
  params.max_points = maxPoints
  return apiClient.get('/api/trends', { params }).then((r) => r.data.data)
}
