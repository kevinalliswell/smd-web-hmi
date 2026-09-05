import { describe, expect, it } from 'vitest'
import { measurementQuality, finiteValue, liveSample } from '@/utils/measurementQuality'

describe('measurement quality', () => {
  it('distinguishes absent data, unknown quality, explicit failure and valid zero', () => {
    expect(measurementQuality(undefined, undefined).text).toBe('暂无数据')
    expect(measurementQuality(0, undefined).text).toBe('质量未知')
    expect(measurementQuality(0, false).text).toBe('失效')
    expect(measurementQuality(0, true).text).toBe('有效')
    expect(measurementQuality(1, true, true).text).toBe('数据过期')
  })
  it('does not turn empty strings, NaN or invalid samples into a real number', () => {
    expect(finiteValue('')).toBeNull()
    expect(finiteValue(NaN)).toBeNull()
    expect(finiteValue(0)).toBe(0)
    expect(liveSample({ measurement: { delta_p_pa: 600, delta_p_valid: false } }).delta_p).toBeNull()
  })
  it('emits nulls for every channel during an acquisition gap', () => {
    const sample = liveSample({ temperature: { furnace_pv_deg_c: 900 }, measurement: { displacement_mm: 1 } }, true)
    expect(Object.values(sample).every((value) => value === null)).toBe(true)
  })
})
