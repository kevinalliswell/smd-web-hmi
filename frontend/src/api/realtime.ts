import type { AlarmNotification, DeviceSnapshot } from './contracts'

type RecordValue = Record<string, unknown>
export const isRecord = (value: unknown): value is RecordValue =>
  value !== null && typeof value === 'object' && !Array.isArray(value)
const optional = (value: unknown, check: (v: unknown) => boolean) => value === undefined || check(value)
const nullableString = (v: unknown): v is string | null => v === null || typeof v === 'string'
const flag = (v: unknown) => v === null || typeof v === 'boolean' || v === 0 || v === 1
const finite = (v: unknown) => v === null || (typeof v === 'number' && Number.isFinite(v))
const quality = (v: unknown): v is 'online' | 'degraded' | 'offline' =>
  v === 'online' || v === 'degraded' || v === 'offline'

/** Validate consumed fields without coercion; allow additive fields from either protocol. */
export function isDeviceSnapshot(value: unknown): value is DeviceSnapshot {
  if (!isRecord(value) || !['system', 'state_machine', 'comm_quality'].some((k) => k in value)) return false
  if (!optional(value.comm_quality, quality) || !optional(value.last_update, nullableString)) return false
  for (const key of ['data_fresh', 'control_ready']) {
    if (!optional(value[key], (v) => typeof v === 'boolean')) return false
  }
  const groups: Record<string, Record<string, (v: unknown) => boolean>> = {
    system: {
      current_state: nullableString, protocol_version: nullableString,
      operation_state: (v) => ['idle', 'running', 'fault', 'terminal', 'unknown'].includes(String(v)),
      ...Object.fromEntries(['can_start_test', 'can_stop_test', 'can_set_parameters', 'is_running',
        'can_activate_recipe', 'can_ack_run', 'can_ack_alarm', 'can_reset_fault',
        'run_recovery_required', 'control_lease_acquire_required'].map((k) => [k, (v: unknown) => typeof v === 'boolean'])),
    },
    state_machine: {
      current_state: nullableString, test_id: nullableString, run_id: nullableString,
      stage_index: finite, measurement_complete: (v) => typeof v === 'boolean', safe_complete: (v) => typeof v === 'boolean', outcome: nullableString,
    },
    temperature: { furnace_pv_deg_c: finite, furnace_sv_deg_c: finite, furnace_pv_valid: flag },
    measurement: Object.fromEntries([
      ...['burden_temp_deg_c', 'drip_weight_g', 'delta_p_pa', 'displacement_mm'].map((k) => [k, finite]),
      ...['burden_temp_valid', 'drip_weight_valid', 'delta_p_valid', 'displacement_valid', 'balance_stable'].map((k) => [k, flag]),
    ]),
    gas: Object.fromEntries(['n2_pv_l_min', 'n2_sp_l_min', 'co_pv_l_min', 'co_sp_l_min'].map((k) => [k, finite])),
    safety: Object.fromEntries(['safety_relay_allowed', 'emergency_stop', 'co_alarm_l1', 'co_alarm_l2', 'exhaust_ok', 'overtemp_alarm'].map((k) => [k, flag])),
    alarm: { ack_required: flag, latched_alarm_count: finite },
    _hostcomm: { protocol_version: nullableString, capabilities: (v) => Array.isArray(v) && v.every((s) => typeof s === 'string') },
    _v2: { channel_quality: isRecord, status: (v) => v === null || isRecord(v), telemetry: (v) => v === null || isRecord(v), profile: (v) => v === null || isRecord(v) },
  }
  return Object.entries(groups).every(([name, fields]) => {
    const group = value[name]
    return group === undefined || (isRecord(group) && Object.entries(fields).every(([key, check]) => optional(group[key], check)))
  })
}

export function requireDeviceSnapshot(value: unknown): DeviceSnapshot {
  if (!isDeviceSnapshot(value)) throw new Error('设备状态格式无效，请检查服务版本')
  return value
}

export type RealtimeMessage =
  | { type: 'auth_ok' | 'pong' }
  | { type: 'status_update'; data: DeviceSnapshot }
  | { type: 'comm_status'; data: { status: 'online' | 'degraded' | 'offline'; reason?: string } }
  | { type: 'alarm_new'; data: AlarmNotification }
  | { type: 'alarm_clear'; data: { alarm_id: number | null } }
  | { type: 'alarm_ack'; data: { alarm_id: number; ack_operator: string | null; ack_time: string } }
  | { type: 'state_change'; data: { to_state: string } }
  | { type: 'event'; data: { kind: string; _v2_persisted?: boolean } }

/** Unknown and malformed frames cannot refresh timestamps, grant control or mark a link online. */
export function parseRealtimeMessage(raw: unknown): RealtimeMessage | null {
  if (typeof raw !== 'string') return null
  let msg: unknown
  try { msg = JSON.parse(raw) } catch { return null }
  if (!isRecord(msg)) return null
  const data = msg.data
  switch (msg.type) {
    case 'auth_ok': case 'pong': return { type: msg.type }
    case 'status_update': return isDeviceSnapshot(data) ? { type: msg.type, data } : null
    case 'comm_status':
      return isRecord(data) && quality(data.status) && optional(data.reason, (v) => typeof v === 'string')
        ? { type: msg.type, data: { status: data.status, reason: data.reason as string | undefined } } : null
    case 'alarm_new':
      return isRecord(data) && Number.isSafeInteger(data.alarm_id) && typeof data.alarm_code === 'string'
        && typeof data.level === 'number' && Number.isFinite(data.level) && typeof data.occur_time === 'string'
        && optional(data.text, nullableString) ? { type: msg.type, data: data as unknown as AlarmNotification } : null
    case 'alarm_clear':
      return isRecord(data) && (data.alarm_id === null || Number.isSafeInteger(data.alarm_id))
        ? { type: msg.type, data: { alarm_id: data.alarm_id as number | null } } : null
    case 'alarm_ack':
      return isRecord(data) && Number.isSafeInteger(data.alarm_id) && nullableString(data.ack_operator) && typeof data.ack_time === 'string'
        ? { type: msg.type, data: { alarm_id: data.alarm_id as number, ack_operator: data.ack_operator, ack_time: data.ack_time } } : null
    case 'state_change':
      return isRecord(data) && typeof data.to_state === 'string' ? { type: msg.type, data: { to_state: data.to_state } } : null
    case 'event':
      return isRecord(data) && typeof data.kind === 'string' && optional(data._v2_persisted, (v) => typeof v === 'boolean')
        ? { type: msg.type, data: { kind: data.kind, _v2_persisted: data._v2_persisted as boolean | undefined } } : null
    default: return null
  }
}
