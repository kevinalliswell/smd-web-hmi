// @ts-check
import apiClient from './client'
/** @typedef {import('./contracts').OperationResult} OperationResult */
/** @typedef {import('./contracts').CommandRequest} CommandRequest */
/** @typedef {import('./contracts').CommandFailure} CommandFailure */

const STORAGE_KEY = 'smd_pending_operations'

function pendingOperations() {
  try { return JSON.parse(sessionStorage.getItem(STORAGE_KEY) || '{}') } catch { return {} }
}

function savePending(entries) {
  // Persist before transmission; if storage is unavailable, do not send an untraceable action.
  sessionStorage.setItem(STORAGE_KEY, JSON.stringify(entries))
}

function stable(value) {
  if (Array.isArray(value)) return value.map(stable)
  if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort().map((key) => [key, stable(value[key])]))
  return value
}

function operationKey(command, params) {
  return JSON.stringify([sessionStorage.getItem('smd_username'), command, stable(params)])
}

function unknownOperation(operationId) {
  /** @type {CommandFailure} */
  const error = new Error(`操作结果未知，请查询结果；不要重新创建相同操作。操作编号：${operationId}`)
  error.operationId = operationId
  error.outcomeUnknown = true
  return error
}

/** @param {OperationResult} result */
function finishOperation(result, key, operationId, command) {
  const status = result.operation_status || result.result
  if (command === 'set_parameters' && status === 'accepted') throw unknownOperation(operationId)
  if (!['accepted', 'verified', 'rejected'].includes(status)) throw unknownOperation(operationId)
  const entries = pendingOperations()
  delete entries[key]
  savePending(entries)
  if (status === 'rejected') {
    /** @type {CommandFailure} */
    const error = new Error(result.reason_code || '控制板拒绝操作')
    error.operationId = operationId
    throw error
  }
  return { ...result, operation_id: result.operation_id || operationId }
}

// 为 CO 相关命令获取一次性二次确认令牌。
export function requestConfirmToken(command) {
  return apiClient.post('/api/commands/confirm-intent', { command }).then((r) => r.data.data)
}

/** @param {string} operationId @returns {Promise<OperationResult>} */
export async function fetchOperation(operationId) {
  const result = (await apiClient.get(`/api/commands/operations/${encodeURIComponent(operationId)}`)).data.data
  if (['accepted', 'verified', 'rejected'].includes(result.operation_status)) {
    const entries = pendingOperations()
    for (const [key, entry] of Object.entries(entries)) {
      if (entry.operation_id === operationId && !(entry.command === 'set_parameters' && result.operation_status === 'accepted')) delete entries[key]
    }
    savePending(entries)
  }
  return result
}

// Each logical intent retains its identity across transport failure and user retries.
// There is no automatic POST retry. A repeated user action queries the previous operation first.
/**
 * @param {string} command
 * @param {Record<string, unknown>} params
 * @param {string | null} confirmToken
 * @returns {Promise<OperationResult>}
 */
export async function sendCommand(command, params = {}, confirmToken = null) {
  const key = operationKey(command, params)
  const entries = pendingOperations()
  const existing = entries[key]
  const operationId = existing?.operation_id || crypto.randomUUID()
  if (existing) {
    try {
      return finishOperation(await fetchOperation(operationId), key, operationId, command)
    } catch (error) {
      if (error.response?.status !== 404) throw error.outcomeUnknown ? error : unknownOperation(operationId)
    }
  }
  entries[key] = { operation_id: operationId, command, created_at: new Date().toISOString() }
  savePending(entries)
  let response
  try {
    /** @type {CommandRequest} */
    const body = { command, params, confirm_token: confirmToken, operation_id: operationId }
    response = await apiClient.post('/api/commands', body, { headers: { 'Idempotency-Key': operationId } })
  } catch (error) {
    const status = error.response?.status
    const detail = error.response?.data?.detail
    if (status >= 400 && status < 500 && !detail?.operation_id) {
      const current = pendingOperations()
      delete current[key]
      savePending(current)
      throw error
    }
    try {
      response = { data: { data: await fetchOperation(operationId) } }
    } catch { throw unknownOperation(operationId) }
  }
  return finishOperation(response.data.data, key, operationId, command)
}

export function ackAlarm(alarmId) {
  return apiClient.post(`/api/alarms/${alarmId}/ack`).then((r) => r.data.data)
}
