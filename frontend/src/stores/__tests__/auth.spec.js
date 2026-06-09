import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useAuthStore } from '@/stores/auth'

describe('auth store 角色层级', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
  })

  it('未登录时无任何权限', () => {
    const auth = useAuthStore()
    expect(auth.isLoggedIn).toBe(false)
    expect(auth.canOperate).toBe(false)
    expect(auth.canAdmin).toBe(false)
  })

  it('operator 可操作但不可配置', () => {
    const auth = useAuthStore()
    auth.role = 'operator'
    auth.token = 'x'
    expect(auth.hasRole('observer')).toBe(true)
    expect(auth.hasRole('operator')).toBe(true)
    expect(auth.hasRole('admin')).toBe(false)
    expect(auth.canOperate).toBe(true)
    expect(auth.canAdmin).toBe(false)
  })

  it('maintainer 拥有最高权限', () => {
    const auth = useAuthStore()
    auth.role = 'maintainer'
    expect(auth.hasRole('admin')).toBe(true)
    expect(auth.hasRole('maintainer')).toBe(true)
  })
})
