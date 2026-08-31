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
    expect(d.canStartTest).toBe(false)
    expect(d.canStopTest).toBe(false)
  })

  it('updateSnapshot 后派生炉温/状态/运行标志', () => {
    const d = useDeviceStore()
    d.updateSnapshot({
      comm_quality: 'online',
      system: {
        operation_state: 'running',
        is_running: true,
        can_start_test: false,
        can_stop_test: true,
      },
      state_machine: { current_state: 'GasSwitch' },
      temperature: { furnace_pv_deg_c: 1580.2, furnace_sv_deg_c: 1580 },
      safety: { safety_relay_allowed: true },
    })
    expect(d.commQuality).toBe('online')
    expect(d.currentState).toBe('GasSwitch')
    expect(d.furnacePV).toBe(1580.2)
    expect(d.isRunning).toBe(true)
    expect(d.canStartTest).toBe(false)
    expect(d.canStopTest).toBe(true)
    expect(d.safetyOk).toBe(true)
  })

  it('Standby 非运行态', () => {
    const d = useDeviceStore()
    d.updateSnapshot({
      system: {
        operation_state: 'idle',
        is_running: false,
        can_start_test: true,
        can_stop_test: false,
      },
      state_machine: { current_state: 'Standby' },
    })
    expect(d.isRunning).toBe(false)
    expect(d.canStartTest).toBe(true)
    expect(d.canStopTest).toBe(false)
  })

  it('未知固件状态按保守策略允许停止、禁止启动', () => {
    const d = useDeviceStore()
    d.updateSnapshot({
      system: {
        operation_state: 'unknown',
        is_running: false,
        can_start_test: false,
        can_stop_test: true,
      },
      state_machine: { current_state: 'VendorNewState' },
    })
    expect(d.canStartTest).toBe(false)
    expect(d.canStopTest).toBe(true)
  })
})
