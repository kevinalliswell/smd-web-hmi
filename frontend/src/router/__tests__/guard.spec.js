import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { routeGuard } from '@/router'
import { useAuthStore } from '@/stores/auth'

describe('默认口令路由守卫', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
  })

  it('强制改密用户不能进入业务页面', () => {
    const auth = useAuthStore()
    auth.token = 'token'
    auth.mustChangePassword = true

    expect(routeGuard({ name: 'overview', meta: {} })).toEqual({ name: 'change-password' })
    expect(routeGuard({ name: 'change-password', meta: {} })).toBe(true)
  })

  it('完成改密后不能再次进入强制改密页', () => {
    const auth = useAuthStore()
    auth.token = 'token'
    auth.mustChangePassword = false

    expect(routeGuard({ name: 'change-password', meta: {} })).toEqual({ name: 'overview' })
  })
})
