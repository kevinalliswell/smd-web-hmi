import { describe, expect, it } from 'vitest'
import { completionLabel, formatMetric, overviewMetrics } from '../overview'

describe('overview measurement evidence', () => {
  it.each([null, undefined, NaN, Infinity, '12', false])('does not invent a numeric value for %s', (value) => {
    expect(formatMetric(value)).toBe('—')
  })
  it('hides stale and explicitly invalid measurements, preserving real zero', () => {
    const snapshot = { temperature: { furnace_pv_deg_c: 1100, furnace_pv_valid: false },
      measurement: { drip_weight_g: 0, drip_weight_valid: true } }
    const metrics = overviewMetrics(snapshot, false)
    expect(metrics[0]).toMatchObject({ value: '—', quality: '数据失效' })
    expect(metrics[2]).toMatchObject({ value: '0.00', quality: '有效' })
    expect(overviewMetrics(snapshot, true).every((m) => m.value === '—')).toBe(true)
    expect(overviewMetrics({ gas: { n2_pv_l_min: 1 } }, false)[5].quality).toBe('质量未知')
  })
  it('requires independent measurement and safety completion', () => {
    const snapshot = { state_machine: { measurement_complete: true, safe_complete: false } }
    expect(completionLabel(snapshot, false)).toBe('测定完成 · 安全未完成')
    expect(completionLabel(snapshot, true)).toBe('完成状态待核实')
    snapshot.state_machine.safe_complete = true
    expect(completionLabel(snapshot, false)).toBe('测定与安全均完成')
    expect(completionLabel({}, false)).toBe('尚无完整结束依据')
  })
})
