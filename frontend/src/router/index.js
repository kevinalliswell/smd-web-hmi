import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const routes = [
  { path: '/login', name: 'login', component: () => import('@/pages/LoginPage.vue') },
  { path: '/change-password', name: 'change-password', component: () => import('@/pages/ChangePasswordPage.vue'), meta: { title: '修改初始密码' } },
  { path: '/', redirect: '/overview' },
  { path: '/overview', name: 'overview', component: () => import('@/pages/OverviewPage.vue'), meta: { title: '实时总览' } },
  { path: '/trend', name: 'trend', component: () => import('@/pages/TrendPage.vue'), meta: { title: '趋势曲线' } },
  { path: '/test', name: 'test', component: () => import('@/pages/TestPage.vue'), meta: { title: '当前试验' } },
  { path: '/alarms', name: 'alarms', component: () => import('@/pages/AlarmsPage.vue'), meta: { title: '报警事件' } },
  { path: '/history', name: 'history', component: () => import('@/pages/HistoryPage.vue'), meta: { title: '历史试验' } },
  { path: '/recipes', name: 'recipes', component: () => import('@/pages/RecipesPage.vue'), meta: { title: '实验配方' } },
  { path: '/operations', name: 'operations', component: () => import('@/pages/OperationsPage.vue'), meta: { title: '操作记录' } },
  { path: '/parameters', name: 'parameters', component: () => import('@/pages/ParametersPage.vue'), meta: { title: '参数配置' } },
  { path: '/diagnostics', name: 'diagnostics', component: () => import('@/pages/DiagnosticsPage.vue'), meta: { title: '设备诊断', requireRole: 'maintainer' } },
  { path: '/settings', name: 'settings', component: () => import('@/pages/SettingsPage.vue'), meta: { title: '系统设置' } },
  { path: '/analytics', name: 'analytics', component: () => import('@/pages/AnalyticsPage.vue'), meta: { title: '数据分析' } },
  { path: '/reports', name: 'reports', component: () => import('@/pages/ReportsPage.vue'), meta: { title: '报告生成' } },
  { path: '/help', name: 'help', component: () => import('@/pages/HelpPage.vue'), meta: { title: '帮助' } },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

// 全局前置守卫：未登录跳登录页；无权限跳总览
export function routeGuard(to) {
  const auth = useAuthStore()
  if (!auth.isLoggedIn && to.name !== 'login') {
    return { name: 'login' }
  }
  if (auth.isLoggedIn && auth.mustChangePassword && to.name !== 'change-password') {
    return { name: 'change-password' }
  }
  if (auth.isLoggedIn && !auth.mustChangePassword && to.name === 'change-password') {
    return { name: 'overview' }
  }
  if (auth.isLoggedIn && to.name === 'login') {
    return { name: 'overview' }
  }
  if (to.meta.requireRole && !auth.hasRole(to.meta.requireRole)) {
    return { name: 'overview' }
  }
  return true
}

router.beforeEach(routeGuard)

export default router
