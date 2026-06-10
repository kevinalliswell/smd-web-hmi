import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useAlarmsStore } from '@/stores/alarms'

describe('alarms store 分级与确认', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('sortedActive 按级别降序（L3 在前）', () => {
    const s = useAlarmsStore()
    s.setActive([
      { id: 1, alarm_code: 'A1', level: 1, occur_time: '2026-06-10T01:00:00' },
      { id: 2, alarm_code: 'A3', level: 3, occur_time: '2026-06-10T01:01:00' },
      { id: 3, alarm_code: 'A2', level: 2, occur_time: '2026-06-10T01:02:00' },
    ])
    expect(s.sortedActive.map((a) => a.level)).toEqual([3, 2, 1])
    expect(s.criticalCount).toBe(1)
    expect(s.hasCritical).toBe(true)
  })

  it('未确认计数与确认更新', () => {
    const s = useAlarmsStore()
    s.setActive([{ id: 5, alarm_code: 'A', level: 2, occur_time: 't' }])
    expect(s.unackedCount).toBe(1)
    s.ackAlarm(5, 'op1', '2026-06-10T02:00:00')
    expect(s.unackedCount).toBe(0)
    expect(s.activeAlarms[0].ack_operator).toBe('op1')
  })

  it('addAlarm 去重，clearAlarm 移出活跃', () => {
    const s = useAlarmsStore()
    s.addAlarm({ alarm_id: 9, alarm_code: 'X', level: 3, occur_time: 't' })
    s.addAlarm({ alarm_id: 9, alarm_code: 'X', level: 3, occur_time: 't' })
    expect(s.activeAlarms.length).toBe(1)
    s.clearAlarm(9)
    expect(s.activeAlarms.length).toBe(0)
  })
})
