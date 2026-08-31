import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { login as apiLogin } from '@/api/auth'

const ROLE_LEVEL = { observer: 0, operator: 1, admin: 2, maintainer: 3 }

export const useAuthStore = defineStore('auth', () => {
  const token = ref(localStorage.getItem('smd_token') || '')
  const username = ref(localStorage.getItem('smd_username') || '')
  const role = ref(localStorage.getItem('smd_role') || '')
  const displayName = ref(localStorage.getItem('smd_display') || '')
  const mustChangePassword = ref(localStorage.getItem('smd_must_change_password') === '1')

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
    localStorage.setItem('smd_token', token.value)
    localStorage.setItem('smd_username', username.value)
    localStorage.setItem('smd_role', role.value)
    localStorage.setItem('smd_display', displayName.value)
    localStorage.setItem('smd_must_change_password', mustChangePassword.value ? '1' : '0')
    return data
  }

  function logout() {
    token.value = ''
    username.value = ''
    role.value = ''
    displayName.value = ''
    mustChangePassword.value = false
    ;['smd_token', 'smd_username', 'smd_role', 'smd_display', 'smd_must_change_password'].forEach((k) =>
      localStorage.removeItem(k),
    )
  }

  function checkToken() {
    // 简易：仅校验本地是否存在 token（过期由后端 401 拦截处理）
    token.value = localStorage.getItem('smd_token') || ''
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
