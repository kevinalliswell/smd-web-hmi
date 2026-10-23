import { describe, expect, it } from 'vitest'
import { parseRealtimeMessage, requireDeviceSnapshot } from '../realtime'
import { requireLoginSession, requireCurrentUser } from '../session'

describe('realtime input boundary', () => {
  it.each([
    'not JSON', 'null', '[]', '{"type":"future_online"}',
    '{"type":"status_update","data":null}',
    '{"type":"status_update","data":{"system":{"can_start_test":"true"}}}',
    '{"type":"status_update","data":{"comm_quality":"future"}}',
    '{"type":"status_update","data":{"system":{},"temperature":{"furnace_pv_deg_c":"1200"}}}',
    '{"type":"comm_status","data":{"status":"connected"}}',
    '{"type":"alarm_ack","data":{"alarm_id":"1","ack_time":"now"}}',
  ])('discards malformed or unknown frames: %s', (frame) => {
    expect(parseRealtimeMessage(frame)).toBeNull()
  })

  it('retains explicit unknown/offline states and additive protocol evidence', () => {
    const data = { comm_quality: 'offline', system: { operation_state: 'unknown', can_start_test: false },
      _v2: { status: { evidence: 'preserved' } }, future_evidence: 1 }
    expect(parseRealtimeMessage(JSON.stringify({ type: 'status_update', data }))?.data).toEqual(data)
    expect(() => requireDeviceSnapshot({ comm_quality: 'online', temperature: { furnace_pv_deg_c: Infinity } })).toThrow()
  })

  it('rejects malformed authentication and unknown roles', () => {
    expect(() => requireLoginSession({ token: 'x', role: 'root', display_name: null, must_change_password: false })).toThrow()
    expect(() => requireCurrentUser({ username: 'x', role: 'admin', must_change_password: 'false' })).toThrow()
    expect(requireCurrentUser({ username: 'x', role: 'observer', must_change_password: true }).role).toBe('observer')
  })
})
