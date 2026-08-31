import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { login as apiLogin } from '@/api/auth'
import { useAuthStore } from '@/stores/auth'

vi.mock('@/api/auth', () => ({ login: vi.fn() }))

describe('auth store 角色层级', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
    vi.clearAllMocks()
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

  it('登录后持久化默认口令强制修改标记', async () => {
    apiLogin.mockResolvedValue({
      token: 'token',
      role: 'admin',
      display_name: '系统管理员',
      must_change_password: true,
    })
    const auth = useAuthStore()

    await auth.login('admin', 'admin')

    expect(auth.mustChangePassword).toBe(true)
    expect(localStorage.getItem('smd_must_change_password')).toBe('1')
    auth.logout()
    expect(localStorage.getItem('smd_must_change_password')).toBeNull()
  })
})
