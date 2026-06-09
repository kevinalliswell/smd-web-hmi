// axios 实例：统一 baseURL、JWT 注入、错误解包
import axios from 'axios'

const apiClient = axios.create({
  baseURL: import.meta.env.VITE_API_BASE || '',
  timeout: 10000,
})

// 请求拦截：注入 Bearer token
apiClient.interceptors.request.use((config) => {
  const token = localStorage.getItem('smd_token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

// 响应拦截：401 时清除登录态并跳转
apiClient.interceptors.response.use(
  (resp) => resp,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem('smd_token')
      if (window.location.pathname !== '/login') window.location.href = '/login'
    }
    return Promise.reject(error)
  },
)

export default apiClient
