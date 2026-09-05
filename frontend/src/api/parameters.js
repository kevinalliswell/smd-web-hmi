import apiClient from './client'
import { sendCommand } from './commands'
import { paramCrc } from '@/utils/crc32'

// 读取当前参数快照
export function fetchParameters() {
  return apiClient.get('/api/parameters').then((r) => r.data.data)
}

// 下发参数：自动计算与后端一致的 param_crc
export function putParameters(values) {
  return sendCommand('set_parameters', { values, param_crc: paramCrc(values) })
}

export function fetchParameterHistory() {
  return apiClient.get('/api/parameters/history').then((r) => r.data.data)
}
