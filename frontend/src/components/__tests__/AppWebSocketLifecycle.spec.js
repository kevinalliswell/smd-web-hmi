import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'
import { shallowMount } from '@vue/test-utils'
import App from '@/App.vue'
import { useAuthStore } from '@/stores/auth'
import { useAlarmsStore } from '@/stores/alarms'

const wsSpies = vi.hoisted(() => ({
  connect: vi.fn(),
  disconnect: vi.fn(),
}))

vi.mock('@/composables/useWebSocket', () => ({
  useWebSocket: () => wsSpies,
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ name: 'login' }),
}))

describe('App WebSocket 生命周期', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
  })

  it('登录后立即连接，登出后立即断开', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const wrapper = shallowMount(App, {
      global: {
        plugins: [pinia],
        stubs: ['RouterView', 'AppHeader', 'AppSidebar', 'AppFooter'],
      },
    })
    const auth = useAuthStore()
    const alarms = useAlarmsStore()
    alarms.loadActive = vi.fn().mockResolvedValue(undefined)
    wsSpies.connect.mockClear()
    wsSpies.disconnect.mockClear()

    auth.token = 'jwt-token'
    await nextTick()
    expect(wsSpies.connect).toHaveBeenCalledOnce()
    expect(alarms.loadActive).toHaveBeenCalledOnce()

    auth.logout()
    await nextTick()
    expect(wsSpies.disconnect).toHaveBeenCalledOnce()

    wrapper.unmount()
  })
})
