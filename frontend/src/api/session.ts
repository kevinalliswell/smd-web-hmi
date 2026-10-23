import type { CurrentUser, LoginSession, UserRole } from './contracts'
import { isRecord } from './realtime'

const role = (v: unknown): v is UserRole => ['observer', 'operator', 'admin', 'maintainer'].some((r) => r === v)
export function requireLoginSession(value: unknown): LoginSession {
  if (!isRecord(value) || typeof value.token !== 'string' || !value.token || !role(value.role)
    || !(value.display_name === null || typeof value.display_name === 'string')
    || typeof value.must_change_password !== 'boolean') throw new Error('登录响应格式无效')
  return { token: value.token, role: value.role, display_name: value.display_name,
    must_change_password: value.must_change_password }
}
export function requireCurrentUser(value: unknown): CurrentUser {
  if (!isRecord(value) || typeof value.username !== 'string' || !role(value.role)
    || typeof value.must_change_password !== 'boolean') throw new Error('账户响应格式无效')
  return { username: value.username, role: value.role, must_change_password: value.must_change_password }
}
