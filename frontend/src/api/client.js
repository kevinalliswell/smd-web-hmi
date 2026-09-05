// axios 实例：统一 baseURL、JWT 注入、错误解包
import axios from 'axios'
import { observeBackendVersion, blocksVersionedWrite } from '@/utils/appVersion'

const apiClient = axios.create({
  baseURL: import.meta.env.VITE_API_BASE || '',
  timeout: 10000,
})

// 请求拦截：注入 Bearer token
apiClient.interceptors.request.use((config) => {
  if (blocksVersionedWrite(config)) {
    const error = new Error('页面与后台版本不一致，请刷新后再操作')
    Object.assign(error, { code: 'CLIENT_VERSION_MISMATCH' })
    return Promise.reject(error)
  }
  const token = sessionStorage.getItem('smd_token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

// 响应拦截：401 时清除登录态并跳转
apiClient.interceptors.response.use(
  (resp) => { observeBackendVersion(resp.headers?.['x-smd-version']); return resp },
  (error) => {
    observeBackendVersion(error.response?.headers?.['x-smd-version'])
    if (error.response?.status === 401) {
      ;['smd_token', 'smd_username', 'smd_role', 'smd_display', 'smd_must_change_password'].forEach((key) =>
        sessionStorage.removeItem(key),
      )
      if (window.location.pathname !== '/login') window.location.href = '/login'
    }
    return Promise.reject(error)
  },
)

export default apiClient
