import { beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import AlarmsPage from '@/pages/AlarmsPage.vue'
import { useDeviceStore } from '@/stores/device'
import { useAuthStore } from '@/stores/auth'
import { ackAlarm, fetchActiveAlarms, fetchAlarmHistory } from '@/api/alarms'
vi.mock('@/api/alarms', () => ({ ackAlarm: vi.fn(), fetchActiveAlarms: vi.fn(), fetchAlarmHistory: vi.fn() }))
beforeEach(() => {
  vi.clearAllMocks(); setActivePinia(createPinia()); useAuthStore().role = 'operator'
  useDeviceStore().updateSnapshot({ comm_quality: 'online', data_fresh: true, control_ready: true, system: { protocol_version: '2.0', can_ack_alarm: true } })
  fetchActiveAlarms.mockResolvedValue([])
  fetchAlarmHistory.mockResolvedValue([{ alarm_id: 77, wire_alarm_id: 'a'.repeat(32), occurrence_seq: '7', alarm_code: 'emergency_stop', text: '已解除的急停', clear_time: '2026-09-06T00:01:00Z', ack_time: null }])
})
it('allows an operator to acknowledge a cleared occurrence by its local ID and updates the pending count', async () => {
  const wrapper = mount(AlarmsPage); await flushPromises()
  await wrapper.findAll('button').find(b => b.text().startsWith('历史报警')).trigger('click'); await flushPromises()
  expect(wrapper.text()).toContain('本页待确认 1 条')
  ackAlarm.mockResolvedValue({ ack_operator: 'operator', ack_time: '2026-09-06T00:02:00Z' })
  await wrapper.findAll('button').find(b => b.text() === '确认').trigger('click'); await flushPromises()
  expect(ackAlarm).toHaveBeenCalledExactlyOnceWith(77)
  expect(wrapper.text()).toContain('本页待确认 0 条')
  expect(wrapper.text()).toContain('✓ operator')
  wrapper.unmount()
})
it('does not offer historical acknowledgment to an observer', async () => {
  useAuthStore().role = 'observer'
  const wrapper = mount(AlarmsPage); await flushPromises()
  expect(wrapper.findAll('button').some(b => b.text() === '确认')).toBe(false)
  expect(ackAlarm).not.toHaveBeenCalled()
  wrapper.unmount()
})
