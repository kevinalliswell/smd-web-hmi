import { beforeEach, describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'

import ThemeToggle from '@/components/shared/ThemeToggle.vue'
import { setTheme } from '@/composables/useTheme'

describe('ThemeToggle', () => {
  beforeEach(() => {
    localStorage.clear()
    setTheme('dark', { persist: false, notify: false })
  })

  it('提供可访问名称并切换 light/dark 主题', async () => {
    const wrapper = mount(ThemeToggle)
    const button = wrapper.get('button')

    expect(button.attributes('aria-label')).toBe('切换到浅色主题')
    expect(button.attributes('aria-pressed')).toBe('false')

    await button.trigger('click')

    expect(button.attributes('aria-label')).toBe('切换到深色主题')
    expect(button.attributes('aria-pressed')).toBe('true')
    expect(document.documentElement.dataset.theme).toBe('light')
  })
})
