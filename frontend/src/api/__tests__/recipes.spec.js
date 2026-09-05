import { beforeEach, expect, it, vi } from 'vitest'
import { activateRecipe } from '@/api/recipes'
import apiClient from '@/api/client'
vi.mock('@/api/client', () => ({ default: { post: vi.fn(), get: vi.fn() } }))
beforeEach(() => { vi.clearAllMocks(); sessionStorage.clear() })
it('retains recipe activation identity while readback is unknown', async () => {
  apiClient.post.mockResolvedValue({ data: { data: { operation_status: 'accepted' } } })
  apiClient.get.mockResolvedValue({ data: { data: { operation_status: 'unknown' } } })
  await expect(activateRecipe('recipe A', 2)).rejects.toThrow('结果未知')
  const [url, body, config] = apiClient.post.mock.calls[0]
  expect(url).toBe('/api/recipes/recipe%20A/activate')
  expect(body.version).toBe(2)
  expect(body.operation_id).toBe(config.headers['Idempotency-Key'])
  await expect(activateRecipe('recipe A', 2)).rejects.toThrow('结果未知')
  expect(apiClient.post).toHaveBeenCalledOnce()
  apiClient.get.mockResolvedValue({ data: { data: { operation_status: 'verified', readback_ok: true } } })
  expect((await activateRecipe('recipe A', 2)).readback_ok).toBe(true)
})
