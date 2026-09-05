import { describe, expect, it } from 'vitest'
import { formatDateTime, formatMonthDayTime, formatTime } from '@/utils/dateTime'

describe('UTC 时间本地化显示', () => {
  it('按指定展示时区转换而不是截取 UTC 字符串', () => {
    const value = '2026-08-30T12:15:16+00:00'
    const options = { timeZone: 'Asia/Shanghai' }

    expect(formatDateTime(value, options)).toBe('2026-08-30 20:15:16')
    expect(formatMonthDayTime(value, options)).toBe('08-30 20:15:16')
    expect(formatTime(value, options)).toBe('20:15:16')
  })

  it('空值与非法值保持可诊断显示', () => {
    expect(formatDateTime(null)).toBe('—')
    expect(formatDateTime('bad-value')).toBe('bad-value')
  })
})
