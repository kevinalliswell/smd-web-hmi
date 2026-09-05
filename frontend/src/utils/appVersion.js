import { reactive } from 'vue'
import { version } from '../../package.json'
export const frontendVersion = version
export const appCompatibility = reactive({ backendVersion: '', mismatch: false })
export function observeBackendVersion(value) {
  if (typeof value !== 'string' || !value) return
  appCompatibility.backendVersion = value
  // Once mixed versions were observed, require an explicit reload to reset the boundary.
  if (value !== frontendVersion) appCompatibility.mismatch = true
}
export function blocksVersionedWrite(config) {
  if (
    !appCompatibility.mismatch ||
    ['get', 'head', 'options'].includes((config.method || 'get').toLowerCase())
  )
    return false
  const path = (config.url || '').split('?')[0]
  if (path === '/api/commands' || path === '/api/commands/confirm-intent')
    return config.data?.command !== 'stop_test'
  return (
    path.startsWith('/api/parameters') ||
    path.startsWith('/api/control') ||
    (path.startsWith('/api/recipes') && !path.endsWith('/validate'))
  )
}
