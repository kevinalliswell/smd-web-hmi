import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'
import { mount } from '@vue/test-utils'

import AppSidebar from '@/components/layout/AppSidebar.vue'

describe('AppSidebar 可访问状态', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  afterEach(() => vi.unstubAllGlobals())

  it('移动端关闭时移出焦点树，打开后恢复', async () => {
    vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({
      matches: true,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }))
    const wrapper = mount(AppSidebar, {
      props: { open: false },
      global: {
        plugins: [createPinia()],
        stubs: { RouterLink: { template: '<a><slot /></a>' } },
      },
    })
    await nextTick()

    expect(wrapper.attributes('inert')).toBeDefined()
    expect(wrapper.attributes('aria-hidden')).toBe('true')

    await wrapper.setProps({ open: true })

    expect(wrapper.attributes('inert')).toBeUndefined()
    expect(wrapper.attributes('aria-hidden')).toBeUndefined()
  })

  it('桌面端始终保留导航可访问性', async () => {
    vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }))
    const wrapper = mount(AppSidebar, {
      props: { open: false },
      global: {
        plugins: [createPinia()],
        stubs: { RouterLink: { template: '<a><slot /></a>' } },
      },
    })
    await nextTick()

    expect(wrapper.attributes('inert')).toBeUndefined()
    expect(wrapper.attributes('aria-hidden')).toBeUndefined()
  })
})
