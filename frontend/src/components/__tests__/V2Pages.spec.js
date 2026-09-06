import { beforeEach, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { useDeviceStore } from '@/stores/device'
import { useAuthStore } from '@/stores/auth'
import TestPage from '@/pages/TestPage.vue'
import ParametersPage from '@/pages/ParametersPage.vue'
import { fetchParameters } from '@/api/parameters'
import { fetchStatus } from '@/api/status'
import { sendCommand } from '@/api/commands'
vi.mock('@/api/status', () => ({ fetchStatus: vi.fn() }))
vi.mock('@/api/parameters', () => ({ fetchParameters: vi.fn(), putParameters: vi.fn() }))
vi.mock('@/api/tests', () => ({ fetchCurrentTest: vi.fn().mockResolvedValue(null) }))
vi.mock('@/api/commands', async (importOriginal) => ({ ...await importOriginal(), sendCommand: vi.fn() }))
const snapshot = { comm_quality: 'online', data_fresh: true, control_ready: true, system: { protocol_version: '2.0', can_ack_run: true, can_start_test: false, can_set_parameters: false }, state_machine: { current_state: 'completed', stage_index: 4, outcome: 'aborted', safe_complete: true, measurement_complete: false } }
beforeEach(() => { vi.clearAllMocks(); setActivePinia(createPinia()); useAuthStore().role = 'admin'; useDeviceStore().updateSnapshot(structuredClone(snapshot)); fetchStatus.mockResolvedValue(structuredClone(snapshot)) })
it('shows v2 lifecycle evidence, gates unsupported controls and requires completion confirmation', async () => {
  const wrapper = mount(TestPage, { global: { stubs: { TestChart: true, StageStepper: true } } })
  await flushPromises()
  expect(wrapper.text()).toContain('配方阶段 5'); expect(wrapper.text()).toContain('操作终止')
  expect(wrapper.text()).not.toContain('暂停/保持'); expect(wrapper.text()).not.toContain('天平去皮')
  const button = text => wrapper.findAll('button').find(b => b.text() === text)
  await button('确认本次实验结束').trigger('click')
  expect(sendCommand).not.toHaveBeenCalled()
  sendCommand.mockResolvedValue({ operation_status: 'accepted', wire_status: 'applied' })
  await button('确认结束').trigger('click'); await flushPromises()
  expect(sendCommand).toHaveBeenCalledExactlyOnceWith('ack_run')
  wrapper.unmount()
})
it('shows read-only engineering evidence instead of editing raw v2 parameter objects', async () => {
  fetchParameters.mockResolvedValue({ protocol_version: '2.0', params: { recipe: { version: 1 } }, safety_profile: { profile_id: 'UNAPPROVED-TEST-ONLY', approved: false, profile_digest: 'a'.repeat(64), engineering_config_digest: 'b'.repeat(64) } })
  const wrapper = mount(ParametersPage, { global: { stubs: { RouterLink: true } } })
  await flushPromises()
  expect(wrapper.text()).toContain('尚未审批，禁止实验控制')
  expect(wrapper.text()).toContain('UNAPPROVED-TEST-ONLY')
  expect(wrapper.findAll('input')).toHaveLength(0)
  expect(wrapper.text()).not.toContain('下发参数')
  wrapper.unmount()
})

it('offers an explicit fault reset with operator reason before a separate completion acknowledgment', async () => {
  const status = { ...snapshot, system: { ...snapshot.system, can_reset_fault: true } }
  useDeviceStore().updateSnapshot(status); fetchStatus.mockResolvedValue(status)
  const wrapper = mount(TestPage, { global: { stubs: { TestChart: true, StageStepper: true } } })
  await flushPromises()
  const button = text => wrapper.findAll('button').find(b => b.text() === text)
  expect(button('复位已解除的故障')).toBeDefined()
  await button('复位已解除的故障').trigger('click')
  expect(button('确认复位').attributes('disabled')).toBeDefined()
  await wrapper.find('textarea[aria-label="故障复位依据"]').setValue('现场已排除故障，完成报警确认和安全冷却')
  sendCommand.mockResolvedValue({ operation_status: 'accepted', wire_status: 'applied' })
  await button('确认复位').trigger('click'); await flushPromises()
  expect(sendCommand).toHaveBeenCalledExactlyOnceWith('reset_fault', { reason: '现场已排除故障，完成报警确认和安全冷却' })
  expect(button('确认本次实验结束')).toBeDefined()
  wrapper.unmount()
})
