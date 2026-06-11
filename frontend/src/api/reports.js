import apiClient from './client'

export function fetchReports() {
  return apiClient.get('/api/reports').then((r) => r.data.data)
}

export function generateReport(testId, options = {}) {
  return apiClient
    .post('/api/reports/generate', { test_id: testId, format: 'html', options })
    .then((r) => r.data.data)
}

export const reportDownloadUrl = (id) => `/api/reports/${id}/download`
