import apiClient from './client'

export function exportLogs(testId, logTypes = null) {
  return apiClient
    .post('/api/logs/export', { test_id: testId, log_types: logTypes })
    .then((r) => r.data.data)
}

export const logDownloadUrl = (taskId) => `/api/logs/export/${taskId}/download`
