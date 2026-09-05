import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { login as apiLogin, logout as apiLogout } from '@/api/auth'

const ROLE_LEVEL = { observer: 0, operator: 1, admin: 2, maintainer: 3 }

export const useAuthStore = defineStore('auth', () => {
  const token = ref(sessionStorage.getItem('smd_token') || '')
  const username = ref(sessionStorage.getItem('smd_username') || '')
  const role = ref(sessionStorage.getItem('smd_role') || '')
  const displayName = ref(sessionStorage.getItem('smd_display') || '')
  const mustChangePassword = ref(sessionStorage.getItem('smd_must_change_password') === '1')

  const isLoggedIn = computed(() => !!token.value)
  const canOperate = computed(() => hasRole('operator'))
  const canAdmin = computed(() => hasRole('admin'))
  const canMaintain = computed(() => hasRole('maintainer'))

  function hasRole(minimum) {
    return (ROLE_LEVEL[role.value] ?? -1) >= (ROLE_LEVEL[minimum] ?? 99)
  }

  async function login(usernameInput, password) {
    const data = await apiLogin(usernameInput, password)
    token.value = data.token
    username.value = usernameInput
    role.value = data.role
    displayName.value = data.display_name || usernameInput
    mustChangePassword.value = Boolean(data.must_change_password)
    sessionStorage.setItem('smd_token', token.value)
    sessionStorage.setItem('smd_username', username.value)
    sessionStorage.setItem('smd_role', role.value)
    sessionStorage.setItem('smd_display', displayName.value)
    sessionStorage.setItem('smd_must_change_password', mustChangePassword.value ? '1' : '0')
    return data
  }

  function logout({ revoke = true } = {}) {
    const revokeRequest = revoke && token.value ? apiLogout().catch(() => undefined) : Promise.resolve()
    token.value = ''
    username.value = ''
    role.value = ''
    displayName.value = ''
    mustChangePassword.value = false
    ;['smd_token', 'smd_username', 'smd_role', 'smd_display', 'smd_must_change_password'].forEach((k) =>
      sessionStorage.removeItem(k),
    )
    return revokeRequest
  }

  function checkToken() {
    // 简易：仅校验本地是否存在 token（过期由后端 401 拦截处理）
    token.value = sessionStorage.getItem('smd_token') || ''
  }

  return {
    token,
    username,
    role,
    displayName,
    mustChangePassword,
    isLoggedIn,
    canOperate,
    canAdmin,
    canMaintain,
    hasRole,
    login,
    logout,
    checkToken,
  }
})
