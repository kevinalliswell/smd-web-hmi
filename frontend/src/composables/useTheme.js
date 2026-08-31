import { computed, readonly, ref } from 'vue'

const STORAGE_KEY = 'smd_theme'
const VALID_THEMES = new Set(['light', 'dark'])
const theme = ref('dark')

function systemTheme() {
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

function savedTheme() {
  try {
    return localStorage.getItem(STORAGE_KEY)
  } catch {
    return null
  }
}

export function setTheme(nextTheme, { persist = true, notify = true } = {}) {
  const next = VALID_THEMES.has(nextTheme) ? nextTheme : systemTheme()
  theme.value = next
  document.documentElement.dataset.theme = next
  document.documentElement.style.colorScheme = next

  if (persist) {
    try {
      localStorage.setItem(STORAGE_KEY, next)
    } catch {
      // 禁用或不可用的 Web Storage 不应阻止主题切换与应用启动。
    }
  }
  if (notify) {
    window.dispatchEvent(new CustomEvent('smd-theme-change', { detail: { theme: next } }))
  }
}

export function initTheme() {
  const saved = savedTheme()
  setTheme(VALID_THEMES.has(saved) ? saved : systemTheme(), { persist: false, notify: false })
}

export function toggleTheme() {
  setTheme(theme.value === 'dark' ? 'light' : 'dark')
}

export function useTheme() {
  return {
    theme: readonly(theme),
    isDark: computed(() => theme.value === 'dark'),
    setTheme,
    toggleTheme,
  }
}
