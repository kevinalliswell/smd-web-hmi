import apiClient from './client'

// 为 CO 相关命令获取一次性二次确认令牌
export function requestConfirmToken(command) {
  return apiClient.post('/api/commands/confirm-intent', { command }).then((r) => r.data.data)
}

// 下发命令（可携带 confirm_token）
export function sendCommand(command, params = {}, confirmToken = null) {
  return apiClient
    .post('/api/commands', { command, params, confirm_token: confirmToken })
    .then((r) => r.data.data)
}

export function ackAlarm(alarmId) {
  return apiClient.post(`/api/alarms/${alarmId}/ack`).then((r) => r.data.data)
}
