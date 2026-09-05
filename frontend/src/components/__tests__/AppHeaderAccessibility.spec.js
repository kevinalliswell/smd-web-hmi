import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { mount } from '@vue/test-utils'

import AppHeader from '@/components/layout/AppHeader.vue'

const router = { push: vi.fn() }
vi.mock('vue-router', () => ({ useRouter: () => router }))

describe('AppHeader 可访问交互', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('导航与报警入口使用具名按钮并暴露展开状态', async () => {
    const wrapper = mount(AppHeader, {
      props: { navigationOpen: true },
      global: { plugins: [createPinia()] },
    })

    const navigation = wrapper.get('[aria-controls="primary-navigation"]')
    expect(navigation.attributes('aria-label')).toBe('关闭主导航')
    expect(navigation.attributes('aria-expanded')).toBe('true')
    await navigation.trigger('click')
    expect(wrapper.emitted('toggle-navigation')).toHaveLength(1)

    const alarms = wrapper.get('button[aria-label="查看报警事件"]')
    await alarms.trigger('click')
    expect(router.push).toHaveBeenCalledWith({ name: 'alarms' })
  })
})
