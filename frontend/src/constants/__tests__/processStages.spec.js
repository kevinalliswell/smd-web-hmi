import { describe, expect, it } from 'vitest'
import { PROCESS_STAGES, resolveStage } from '@/constants/processStages'

describe('processStages 阶段解析', () => {
  it('阶段按工艺顺序排列，CO 阶段标记正确', () => {
    const keys = PROCESS_STAGES.map((s) => s.key)
    expect(keys[0]).toBe('Standby')
    expect(keys).toContain('GasSwitch')
    // CO 在 500℃ 切气后介入，置换前结束
    const co = PROCESS_STAGES.filter((s) => s.co).map((s) => s.key)
    expect(co).toEqual(['GasSwitch', 'Heating', 'Hold1580'])
  })

  it('精确状态名解析到正确阶段', () => {
    expect(resolveStage('Standby').index).toBe(0)
    expect(resolveStage('GasSwitch').stage.key).toBe('GasSwitch')
  })

  it('别名与大小写/分隔符容错', () => {
    expect(resolveStage('reducing').stage.key).toBe('GasSwitch')
    expect(resolveStage('HOLDING').stage.key).toBe('Hold1580')
    expect(resolveStage('ramp_up').stage.key).toBe('Heating')
    expect(resolveStage('leak-check').stage.key).toBe('LeakCheck')
  })

  it('故障/暂停识别为特殊状态', () => {
    expect(resolveStage('Fault').special.level).toBe('danger')
    expect(resolveStage('Hold').special.level).toBe('warn')
    expect(resolveStage('Pause').special.level).toBe('warn')
  })

  it('未知状态返回 -1', () => {
    expect(resolveStage('Whatever').index).toBe(-1)
    expect(resolveStage('').index).toBe(-1)
  })
})
