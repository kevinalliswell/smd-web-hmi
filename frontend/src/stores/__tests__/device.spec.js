import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useDeviceStore } from '@/stores/device'

describe('device store 快照派生', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('初始为离线、状态占位', () => {
    const d = useDeviceStore()
    expect(d.commQuality).toBe('offline')
    expect(d.currentState).toBe('—')
    expect(d.isRunning).toBe(false)
  })

  it('updateSnapshot 后派生炉温/状态/运行标志', () => {
    const d = useDeviceStore()
    d.updateSnapshot({
      comm_quality: 'online',
      state_machine: { current_state: 'Reducing' },
      temperature: { furnace_pv_deg_c: 1580.2, furnace_sv_deg_c: 1580 },
      safety: { safety_relay_allowed: true },
    })
    expect(d.commQuality).toBe('online')
    expect(d.currentState).toBe('Reducing')
    expect(d.furnacePV).toBe(1580.2)
    expect(d.isRunning).toBe(true)
    expect(d.safetyOk).toBe(true)
  })

  it('Standby 非运行态', () => {
    const d = useDeviceStore()
    d.updateSnapshot({ state_machine: { current_state: 'Standby' } })
    expect(d.isRunning).toBe(false)
  })
})
