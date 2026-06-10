import apiClient from './client'
import { paramCrc } from '@/utils/crc32'

// 读取当前参数快照
export function fetchParameters() {
  return apiClient.get('/api/parameters').then((r) => r.data.data)
}

// 下发参数：自动计算与后端一致的 param_crc
export function putParameters(values) {
  return apiClient
    .put('/api/parameters', { values, param_crc: paramCrc(values) })
    .then((r) => r.data.data)
}

export function fetchParameterHistory() {
  return apiClient.get('/api/parameters/history').then((r) => r.data.data)
}
