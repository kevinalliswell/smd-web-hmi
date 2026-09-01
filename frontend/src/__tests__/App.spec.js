// 回归测试：登录后必须建立 WebSocket 连接（审查 #15 之 1）
// 登录是 SPA 内导航，App 不会重新挂载——只在 onMounted 判断会导致实时推送永不建立。
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'

const connect = vi.fn()
const disconnect = vi.fn()

vi.mock('@/composables/useWebSocket', () => ({
  useWebSocket: () => ({ connect, disconnect }),
}))
vi.mock('@/api/alarms', () => ({
  fetchActiveAlarms: vi.fn().mockResolvedValue([]),
  fetchAlarmHistory: vi.fn().mockResolvedValue([]),
  ackAlarm: vi.fn().mockResolvedValue({}),
}))
vi.mock('vue-router', () => ({
  useRoute: () => ({ name: 'login' }),
}))

import App from '@/App.vue'
import { useAuthStore } from '@/stores/auth'

const mountApp = () =>
  mount(App, {
    global: {
      stubs: { RouterView: true, AppHeader: true, AppSidebar: true, AppFooter: true },
    },
  })

describe('App WebSocket 生命周期', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
    connect.mockClear()
    disconnect.mockClear()
  })

  it('登录成功后建立连接（不需要刷新页面）', async () => {
    const wrapper = mountApp()
    const auth = useAuthStore()
    expect(connect).not.toHaveBeenCalled()

    // 模拟登录：LoginPage 走的是 SPA 导航，App 不会重新挂载
    auth.token = 'token-after-login'
    auth.role = 'operator'
    await nextTick()

    expect(connect).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('挂载时已有有效登录态则直接连接', async () => {
    localStorage.setItem('smd_token', 'existing-token')
    setActivePinia(createPinia())
    const wrapper = mountApp()
    await nextTick()

    expect(connect).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('登出后断开连接', async () => {
    localStorage.setItem('smd_token', 'existing-token')
    setActivePinia(createPinia())
    const wrapper = mountApp()
    const auth = useAuthStore()
    await nextTick()
    connect.mockClear()

    auth.logout()
    await nextTick()

    expect(disconnect).toHaveBeenCalled()
    expect(connect).not.toHaveBeenCalled()
    wrapper.unmount()
  })
})
