import apiClient from './client'
import type { components } from './generated/schema'

export type DurableJob = components['schemas']['JobView']
export const fetchJob = (taskId: string): Promise<DurableJob> =>
  apiClient.get(`/api/jobs/${taskId}`).then((r) => r.data.data)
export const fetchJobs = (page = 1, pageSize = 20): Promise<components['schemas']['JobPage']> =>
  apiClient.get('/api/jobs', { params: { page, page_size: pageSize } }).then((r) => r.data.data)
export const retryJob = (taskId: string): Promise<components['schemas']['JobAdmission']> =>
  apiClient.post(`/api/jobs/${taskId}/retry`).then((r) => r.data.data)
