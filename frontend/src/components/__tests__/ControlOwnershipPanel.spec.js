import { beforeEach, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { useAuthStore } from '@/stores/auth'
import ControlOwnershipPanel from '@/components/system/ControlOwnershipPanel.vue'
import ConfirmDialog from '@/components/shared/ConfirmDialog.vue'
import { fetchControlOwner, claimControl, releaseControl } from '@/api/control'
vi.mock('@/api/control', () => ({ fetchControlOwner: vi.fn(), claimControl: vi.fn(), releaseControl: vi.fn() }))
beforeEach(() => {
  setActivePinia(createPinia()); vi.clearAllMocks()
  const auth = useAuthStore(); auth.username = 'alice'; auth.role = 'admin'
})
it('requires a takeover reason before an admin can replace another owner', async () => {
  fetchControlOwner.mockResolvedValue({ username: 'bob' })
  claimControl.mockResolvedValue({ username: 'alice' })
  const wrapper = mount(ControlOwnershipPanel); await flushPromises()
  await wrapper.findAll('button').find((b) => b.text() === '接管控制权').trigger('click')
  const confirm = wrapper.findComponent(ConfirmDialog)
  expect(confirm.find('button.danger').attributes('disabled')).toBeDefined()
  await wrapper.find('textarea').setValue('现场交接已完成')
  await confirm.find('button.danger').trigger('click'); await flushPromises()
  expect(claimControl).toHaveBeenCalledWith({ takeover: true, reason: '现场交接已完成' })
  expect(wrapper.text()).toContain('当前控制者：alice')
  wrapper.unmount()
})
it('retains ownership and displays the server refusal when release is unsafe', async () => {
  fetchControlOwner.mockResolvedValue({ username: 'alice' })
  releaseControl.mockRejectedValue({ response: { data: { detail: { message: '存在未闭合实验' } } } })
  const wrapper = mount(ControlOwnershipPanel); await flushPromises()
  await wrapper.findAll('button').find((b) => b.text() === '释放控制权').trigger('click')
  await wrapper.findComponent(ConfirmDialog).find('button.danger').trigger('click'); await flushPromises()
  expect(wrapper.text()).toContain('存在未闭合实验')
  expect(wrapper.text()).toContain('当前控制者：alice')
  wrapper.unmount()
})
