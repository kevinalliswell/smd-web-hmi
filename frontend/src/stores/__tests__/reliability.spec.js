import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useDeviceStore } from '@/stores/device'
import { useAlarmsStore } from '@/stores/alarms'
import { fetchActiveAlarms } from '@/api/alarms'
vi.mock('@/api/alarms', () => ({ fetchActiveAlarms: vi.fn(), fetchAlarmHistory: vi.fn() }))

beforeEach(() => { setActivePinia(createPinia()); vi.clearAllMocks() })

describe('stale device data', () => {
  it('blocks start/parameters after a gap while retaining controlled stop', () => {
    const store = useDeviceStore()
    store.updateSnapshot({ comm_quality: 'online', system: {
      can_start_test: true, can_stop_test: true, can_set_parameters: true, operation_state: 'idle',
    } })
    expect(store.canStartTest).toBe(true)
    store.checkFreshness(performance.now() + 6000)
    expect(store.dataStale).toBe(true)
    expect(store.canStartTest).toBe(false)
    expect(store.canSetParameters).toBe(false)
    expect(store.canStopTest).toBe(true)
  })

  it('never interprets a state permission without live communication as ready', () => {
    const store = useDeviceStore()
    store.updateSnapshot({ comm_quality: 'degraded', system: { can_start_test: true } })
    expect(store.canStartTest).toBe(false)
  })
})

describe('alarm resynchronization', () => {
  it('replays a pushed alarm and clear that arrive while the REST snapshot is loading', async () => {
    let resolve
    fetchActiveAlarms.mockReturnValue(new Promise((done) => { resolve = done }))
    const store = useAlarmsStore()
    const loading = store.loadActive()
    store.addAlarm({ id: 2, alarm_code: 'NEW', level: 3 })
    store.clearAlarm(1)
    resolve([{ id: 1, alarm_code: 'OLD', level: 1 }])
    await loading
    expect(store.activeAlarms.map((alarm) => alarm.alarm_id)).toEqual([2])
  })

  it('keeps previous alarms and marks failed resynchronization', async () => {
    const store = useAlarmsStore()
    store.setActive([{ id: 1, level: 3 }])
    fetchActiveAlarms.mockRejectedValue(new Error('offline'))
    await expect(store.loadActive()).rejects.toThrow('offline')
    expect(store.activeAlarms).toHaveLength(1)
    expect(store.syncError).toBeTruthy()
  })
})
