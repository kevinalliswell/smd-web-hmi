import { beforeEach, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { useDeviceStore } from '@/stores/device'
import { useAuthStore } from '@/stores/auth'
import RecipeStageEditor from '@/components/recipes/RecipeStageEditor.vue'
import OperationResult from '@/components/command/OperationResult.vue'
import { queryDeviceOperation, reconcileOperation } from '@/api/commands'
vi.mock('@/api/commands', async (importOriginal) => ({ ...await importOriginal(), fetchOperation: vi.fn(), queryDeviceOperation: vi.fn(), reconcileOperation: vi.fn() }))
beforeEach(() => { setActivePinia(createPinia()); vi.clearAllMocks() })
const snapshot = (system = {}) => ({ comm_quality: 'online', data_fresh: true, control_ready: true, _hostcomm: { protocol_version: '2.0', capabilities: ['atomic_recipe'] }, system: { protocol_version: '2.0', can_activate_recipe: true, can_set_parameters: false, ...system } })
it('uses v2 atomic recipe and authoritative activation independently from legacy parameter permission', () => {
  const d = useDeviceStore(); d.updateSnapshot(snapshot())
  expect(d.isV2).toBe(true); expect(d.supportsRecipes).toBe(true); expect(d.canActivateRecipe).toBe(true)
  expect(d.canSetParameters).toBe(false); expect(d.supportsCommand('pause_hold')).toBe(false)
  d.updateSnapshot(snapshot({ can_activate_recipe: false })); expect(d.canActivateRecipe).toBe(false)
  d.updateSnapshot(snapshot()); d.checkFreshness(performance.now() + 6000); expect(d.canActivateRecipe).toBe(false)
})
it('requires v2 completion permission, never infers it from completed phase', () => {
  const d = useDeviceStore(); d.updateSnapshot(snapshot({ current_state: 'completed' })); expect(d.canAckRun).toBe(false)
  d.updateSnapshot(snapshot({ can_ack_run: true })); expect(d.canAckRun).toBe(true)
})
it('preserves an old gas stage until the operator explicitly chooses its heater mode', async () => {
  const stage = { name: '气氛', kind: 'gas', furnace_target_c: null, ramp_c_min: null, n2_l_min: 5, co_l_min: 0, exit: { signal: 'elapsed_s', comparison: 'gte', value: 10 }, timeout_s: 20 }
  const wrapper = mount(RecipeStageEditor, { props: { stage, index: 0, count: 2 } })
  expect(wrapper.emitted('change')).toBeUndefined()
  const mode = wrapper.find('select[aria-label="加热方式"]'); expect(mode.exists()).toBe(true)
  await mode.setValue('off')
  expect(wrapper.emitted('change')[0][0].heater_mode).toBe('off')
  expect(stage).not.toHaveProperty('heater_mode')
  expect(wrapper.find('input[aria-label="条件稳定时长 (s)"]').exists()).toBe(true)
})
it('retains a v2 durable accepted intent as unresolved and queries evidence without replaying it', async () => {
  useAuthStore().role = 'operator'; useDeviceStore().updateSnapshot(snapshot())
  queryDeviceOperation.mockResolvedValue({ operation_status: 'accepted', wire_operation: { status: 'accepted', command_seq: '12' } })
  const wrapper = mount(OperationResult, { props: { operationId: 'test' } })
  await wrapper.findAll('button').find(b => b.text() === '查询控制板结果').trigger('click'); await flushPromises()
  expect(wrapper.text()).toContain('已持久受理，等待执行')
  expect(wrapper.emitted('resolved')).toBeUndefined()
  expect(queryDeviceOperation).toHaveBeenCalledWith('test')
  expect(reconcileOperation).not.toHaveBeenCalled()
})
it('requires a reason and explicit maintainer action for unknown reconciliation', async () => {
  useAuthStore().role = 'admin'; useDeviceStore().updateSnapshot(snapshot())
  const wrapper = mount(OperationResult, { props: { operationId: 'test', initialResult: { operation_status: 'unknown', wire_operation: { status: 'unknown' } } } })
  const action = wrapper.findAll('button').find(b => b.text() === '记录对账结论')
  expect(action.attributes('disabled')).toBeDefined()
  await wrapper.find('textarea').setValue('现场核对和日志检查完成')
  reconcileOperation.mockResolvedValue({ operation_status: 'unknown', wire_operation: { status: 'unknown', locally_reconciled: true } })
  await action.trigger('click'); await flushPromises()
  expect(reconcileOperation).toHaveBeenCalledWith('test', '现场核对和日志检查完成')
  expect(wrapper.emitted('resolved')).toBeUndefined()
})

it('shows audited unknown and prerequisite-only rejection honestly without repeated reconciliation controls', async () => {
  useAuthStore().role = 'admin'; useDeviceStore().updateSnapshot(snapshot())
  const wrapper = mount(OperationResult, { props: { operationId: 'reviewed', initialResult: { operation_status: 'unknown', wire_status: 'result_expired', wire_reconciled: true, wire_operation: { status: 'result_expired', reconciled: 1 } } } })
  expect(wrapper.text()).toContain('已核查，执行结果仍未知')
  expect(wrapper.text()).not.toContain('记录对账结论')
  await wrapper.setProps({ initialResult: { operation_status: 'rejected', prerequisite_only: true, wire_status: 'result_expired', wire_reconciled: true } })
  expect(wrapper.text()).toContain('主命令未发送')
  expect(wrapper.text()).toContain('前置操作已核查')
  expect(wrapper.text()).not.toContain('命令已应用')
  wrapper.unmount()
})
