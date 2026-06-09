// 角色权限判断：可查看 / 可操作 / 可配置 / 可维护
import { storeToRefs } from 'pinia'
import { useAuthStore } from '@/stores/auth'

export function useRole() {
  const auth = useAuthStore()
  const { role } = storeToRefs(auth)

  return {
    role,
    canView: () => auth.isLoggedIn, // 任何登录用户可查看
    canOperate: () => auth.hasRole('operator'),
    canConfigure: () => auth.hasRole('admin'),
    canMaintain: () => auth.hasRole('maintainer'),
    hasRole: (minimum) => auth.hasRole(minimum),
  }
}
