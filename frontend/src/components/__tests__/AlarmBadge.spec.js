import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import AlarmBadge from '@/components/alarms/AlarmBadge.vue'

describe('AlarmBadge', () => {
  it('按级别显示 L1/L2/L3 文案', () => {
    expect(mount(AlarmBadge, { props: { level: 1 } }).text()).toBe('L1')
    expect(mount(AlarmBadge, { props: { level: 2 } }).text()).toBe('L2')
    expect(mount(AlarmBadge, { props: { level: 3 } }).text()).toBe('L3')
  })

  it('未知级别回退 Info', () => {
    expect(mount(AlarmBadge, { props: { level: 0 } }).text()).toBe('Info')
    expect(mount(AlarmBadge, { props: { level: 9 } }).text()).toBe('Info')
  })
})
