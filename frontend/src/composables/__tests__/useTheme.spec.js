import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { initTheme, setTheme, toggleTheme, useTheme } from '@/composables/useTheme'

describe('useTheme', () => {
  beforeEach(() => {
    localStorage.clear()
    document.documentElement.removeAttribute('data-theme')
    document.documentElement.style.colorScheme = ''
    vi.stubGlobal(
      'matchMedia',
      vi.fn().mockReturnValue({ matches: true }),
    )
    setTheme('dark', { persist: false, notify: false })
  })

  afterEach(() => vi.unstubAllGlobals())

  it('优先恢复已保存的主题并同步到文档根节点', () => {
    localStorage.setItem('smd_theme', 'light')

    initTheme()

    const { theme, isDark } = useTheme()
    expect(theme.value).toBe('light')
    expect(isDark.value).toBe(false)
    expect(document.documentElement.dataset.theme).toBe('light')
    expect(document.documentElement.style.colorScheme).toBe('light')
  })

  it('没有有效偏好时跟随系统主题', () => {
    localStorage.setItem('smd_theme', 'unsupported')
    matchMedia.mockReturnValue({ matches: false })

    initTheme()

    expect(document.documentElement.dataset.theme).toBe('light')
  })

  it('切换主题后持久化并通知图表等运行时消费者', () => {
    const listener = vi.fn()
    window.addEventListener('smd-theme-change', listener)

    toggleTheme()

    expect(localStorage.getItem('smd_theme')).toBe('light')
    expect(document.documentElement.dataset.theme).toBe('light')
    expect(listener).toHaveBeenCalledOnce()
    expect(listener.mock.calls[0][0].detail).toEqual({ theme: 'light' })
    window.removeEventListener('smd-theme-change', listener)
  })
})
