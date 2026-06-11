import apiClient from './client'

export function fetchUsers() {
  return apiClient.get('/api/users').then((r) => r.data.data)
}

export function createUser(payload) {
  return apiClient.post('/api/users', payload).then((r) => r.data.data)
}

export function updateUser(id, payload) {
  return apiClient.put(`/api/users/${id}`, payload).then((r) => r.data.data)
}

export function changePassword(oldPassword, newPassword) {
  return apiClient
    .post('/api/users/change-password', { old_password: oldPassword, new_password: newPassword })
    .then((r) => r.data.data)
}
