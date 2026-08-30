import apiClient from './client'
import { waitForTask } from './tasks'

export async function exportLogs(testId, logTypes = null) {
  const task = await apiClient
    .post('/api/logs/export', { test_id: testId, log_types: logTypes })
    .then((r) => r.data.data)
  return waitForTask(
    (taskId) => apiClient.get(`/api/logs/export/${taskId}`).then((r) => r.data.data),
    task.task_id,
  )
}

export const logDownloadUrl = (taskId) => `/api/logs/export/${taskId}/download`
