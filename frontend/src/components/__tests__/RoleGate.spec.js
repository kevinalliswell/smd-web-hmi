import { beforeEach, describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import RoleGate from '@/components/shared/RoleGate.vue'
import { useAuthStore } from '@/stores/auth'

function mountGate(role, requireRole) {
  const auth = useAuthStore()
  auth.token = 'x'
  auth.role = role
  return mount(RoleGate, {
    props: { role: requireRole },
    slots: { default: '<span class="secret">机密</span>' },
  })
}

describe('RoleGate', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
  })

  it('角色满足时渲染插槽', () => {
    const w = mountGate('admin', 'operator')
    expect(w.find('.secret').exists()).toBe(true)
  })

  it('角色不足时隐藏插槽', () => {
    const w = mountGate('observer', 'admin')
    expect(w.find('.secret').exists()).toBe(false)
  })

  it('同级角色满足', () => {
    const w = mountGate('maintainer', 'maintainer')
    expect(w.find('.secret').exists()).toBe(true)
  })
})
