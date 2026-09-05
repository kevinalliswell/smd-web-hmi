import apiClient from './client'
import { waitForTask } from './tasks'

export function fetchReports() {
  return apiClient.get('/api/reports').then((r) => r.data.data)
}

export async function generateReport(testId, format = 'pdf', options = {}) {
  const task = await apiClient
    .post('/api/reports/generate', { test_id: testId, format, options })
    .then((r) => r.data.data)
  return waitForTask(
    (taskId) => apiClient.get(`/api/reports/tasks/${taskId}`).then((r) => r.data.data),
    task.task_id,
  )
}

export const reportDownloadUrl = (id) => `/api/reports/${id}/download`
