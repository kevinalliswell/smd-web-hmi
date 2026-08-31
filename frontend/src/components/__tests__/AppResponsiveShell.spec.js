import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'
import { shallowMount } from '@vue/test-utils'

import App from '@/App.vue'
import AppHeader from '@/components/layout/AppHeader.vue'
import AppSidebar from '@/components/layout/AppSidebar.vue'
import { useAlarmsStore } from '@/stores/alarms'

vi.mock('@/composables/useWebSocket', () => ({
  useWebSocket: () => ({ connect: vi.fn(), disconnect: vi.fn() }),
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ name: 'overview' }),
}))

describe('App 响应式外壳', () => {
  beforeEach(() => {
    localStorage.clear()
    localStorage.setItem('smd_token', 'test-token')
    setActivePinia(createPinia())
  })

  it('可由页头开关移动导航，并支持 Escape 关闭', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    useAlarmsStore().loadActive = vi.fn().mockResolvedValue(undefined)
    const wrapper = shallowMount(App, {
      global: {
        plugins: [pinia],
        stubs: ['RouterView', 'AppFooter'],
      },
    })

    const header = wrapper.getComponent(AppHeader)
    const sidebar = wrapper.getComponent(AppSidebar)
    expect(header.props('navigationOpen')).toBe(false)
    expect(sidebar.props('open')).toBe(false)

    header.vm.$emit('toggle-navigation')
    await nextTick()

    expect(header.props('navigationOpen')).toBe(true)
    expect(sidebar.props('open')).toBe(true)

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await nextTick()

    expect(sidebar.props('open')).toBe(false)
    wrapper.unmount()
  })
})
