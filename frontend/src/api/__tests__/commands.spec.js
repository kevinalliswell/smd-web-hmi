import { beforeEach, describe, expect, it, vi } from 'vitest'
import { sendCommand } from '@/api/commands'
import apiClient from '@/api/client'
vi.mock('@/api/client', () => ({ default: { post: vi.fn(), get: vi.fn() } }))
beforeEach(() => { vi.clearAllMocks(); sessionStorage.clear() })

describe('command operation identity', () => {
  it('creates and persists identity before sending, then recovers an accepted response after timeout', async () => {
    apiClient.post.mockImplementation(async (_url, body, options) => {
      expect(body.operation_id).toBeTruthy()
      expect(options.headers['Idempotency-Key']).toBe(body.operation_id)
      expect(sessionStorage.getItem('smd_pending_operations')).toContain(body.operation_id)
      throw { code: 'ECONNABORTED' }
    })
    apiClient.get.mockResolvedValue({ data: { data: { operation_status: 'accepted', result: 'accepted' } } })
    expect((await sendCommand('stop_test', {}, 'confirm')).result).toBe('accepted')
    expect(apiClient.post).toHaveBeenCalledOnce()
    expect(apiClient.get.mock.calls[0][0]).toContain('/api/commands/operations/')
  })

  it('does not reissue an unknown operation when the user retries', async () => {
    apiClient.post.mockRejectedValue({ code: 'ECONNABORTED' })
    apiClient.get.mockResolvedValue({ data: { data: { operation_status: 'unknown', result: 'unknown' } } })
    await expect(sendCommand('start_test', { test_id: 'TEST-1', original_height_mm: 20 }, 'confirm')).rejects.toThrow('结果未知')
    const id = apiClient.post.mock.calls[0][1].operation_id
    await expect(sendCommand('start_test', { test_id: 'TEST-1', original_height_mm: 20 }, 'new-confirm')).rejects.toThrow('结果未知')
    expect(apiClient.post).toHaveBeenCalledOnce()
    expect(apiClient.get.mock.calls.at(-1)[0]).toContain(id)
  })

  it('reuses the same ID if the backend confirms no record after a transport failure', async () => {
    apiClient.post.mockRejectedValueOnce({ code: 'ERR_NETWORK' })
    apiClient.get.mockRejectedValue({ response: { status: 404 } })
    await expect(sendCommand('tare_balance')).rejects.toThrow('结果未知')
    apiClient.post.mockResolvedValueOnce({ data: { data: { operation_status: 'accepted', result: 'accepted' } } })
    await sendCommand('tare_balance')
    expect(apiClient.post.mock.calls[1][1].operation_id).toBe(apiClient.post.mock.calls[0][1].operation_id)
  })
  it('does not treat accepted parameters as verified or resend while readback is pending', async () => {
    apiClient.post.mockResolvedValue({ data: { data: { operation_status: 'accepted', result: 'accepted' } } })
    apiClient.get.mockResolvedValue({ data: { data: { operation_status: 'accepted', result: 'accepted' } } })
    await expect(sendCommand('set_parameters', { values: { process: { hold_minutes: 30 } } })).rejects.toThrow('结果未知')
    await expect(sendCommand('set_parameters', { values: { process: { hold_minutes: 30 } } })).rejects.toThrow('结果未知')
    expect(apiClient.post).toHaveBeenCalledOnce()
    expect(sessionStorage.getItem('smd_pending_operations')).toContain('set_parameters')
  })

  it('reports a recovered device rejection as rejected instead of unknown', async () => {
    apiClient.post.mockRejectedValue({ code: 'ECONNABORTED' })
    apiClient.get.mockResolvedValue({ data: { data: { operation_status: 'unknown' } } })
    await expect(sendCommand('tare_balance')).rejects.toThrow('结果未知')
    apiClient.get.mockResolvedValue({ data: { data: { operation_status: 'rejected', reason_code: 'interlock_denied' } } })
    await expect(sendCommand('tare_balance')).rejects.toThrow('interlock_denied')
    expect(apiClient.post).toHaveBeenCalledOnce()
  })

})
