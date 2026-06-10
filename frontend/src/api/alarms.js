import apiClient from './client'

export function fetchActiveAlarms() {
  return apiClient.get('/api/alarms/active').then((r) => r.data.data)
}

export function fetchAlarmHistory(page = 1, size = 50) {
  return apiClient.get('/api/alarms/history', { params: { page, size } }).then((r) => r.data.data)
}

export function ackAlarm(alarmId) {
  return apiClient.post(`/api/alarms/${alarmId}/ack`).then((r) => r.data.data)
}
