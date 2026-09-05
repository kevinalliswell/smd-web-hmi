import { beforeEach, expect, it } from 'vitest'
import { appCompatibility, observeBackendVersion, blocksVersionedWrite, frontendVersion } from '@/utils/appVersion'
beforeEach(() => { appCompatibility.backendVersion = ''; appCompatibility.mismatch = false })
it('blocks new control and recipe writes on mismatch while preserving stop and read access', () => {
  observeBackendVersion(frontendVersion)
  expect(blocksVersionedWrite({ method: 'post', url: '/api/commands', data: { command: 'start_test' } })).toBe(false)
  observeBackendVersion('0.3.0-rc.9')
  expect(appCompatibility.mismatch).toBe(true)
  expect(blocksVersionedWrite({ method: 'post', url: '/api/commands', data: { command: 'start_test' } })).toBe(true)
  expect(blocksVersionedWrite({ method: 'put', url: '/api/parameters' })).toBe(true)
  expect(blocksVersionedWrite({ method: 'post', url: '/api/recipes/r1/activate' })).toBe(true)
  expect(blocksVersionedWrite({ method: 'post', url: '/api/commands', data: { command: 'stop_test' } })).toBe(false)
  expect(blocksVersionedWrite({ method: 'post', url: '/api/commands/confirm-intent', data: { command: 'stop_test' } })).toBe(false)
  expect(blocksVersionedWrite({ method: 'get', url: '/api/tests' })).toBe(false)
  observeBackendVersion(frontendVersion)
  expect(appCompatibility.mismatch).toBe(true)
})
