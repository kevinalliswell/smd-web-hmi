import { afterEach, expect, it, vi } from 'vitest'
import apiClient from '@/api/client'
import { appCompatibility } from '@/utils/appVersion'
const originalAdapter = apiClient.defaults.adapter
afterEach(() => { apiClient.defaults.adapter = originalAdapter; appCompatibility.mismatch = false; appCompatibility.backendVersion = '' })
it('observes version responses and prevents transport of new commands while retaining stop', async () => {
  const adapter = vi.fn(async (config) => ({ data: { data: [] }, status: 200, statusText: 'OK', headers: { 'x-smd-version': '0.3.0-rc.9' }, config }))
  apiClient.defaults.adapter = adapter
  await apiClient.get('/api/status')
  await expect(apiClient.post('/api/commands', { command: 'start_test' })).rejects.toThrow('版本不一致')
  expect(adapter).toHaveBeenCalledTimes(1)
  await apiClient.post('/api/commands', { command: 'stop_test' })
  await apiClient.get('/api/tests')
  expect(adapter).toHaveBeenCalledTimes(3)
})
