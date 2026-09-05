import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import MaintenancePanel from '@/components/system/MaintenancePanel.vue'
import ConfirmDialog from '@/components/shared/ConfirmDialog.vue'
import { fetchMaintenance, prepareMaintenance, cancelMaintenance } from '@/api/maintenance'
vi.mock('@/api/maintenance', () => ({ fetchMaintenance: vi.fn(), prepareMaintenance: vi.fn(), cancelMaintenance: vi.fn() }))
beforeEach(() => {
  vi.clearAllMocks()
  fetchMaintenance.mockResolvedValue({ state: 'idle' })
})
it('requires confirmation to prepare and supports cancelling without exposing a ticket', async () => {
  prepareMaintenance.mockResolvedValue({ state: 'prepared', target_version: '0.3.0-rc.2' })
  cancelMaintenance.mockResolvedValue({ state: 'idle' })
  const wrapper = mount(MaintenancePanel)
  await flushPromises()
  await wrapper.find('input').setValue('0.3.0-rc.2')
  await wrapper.find('button.primary').trigger('click')
  expect(prepareMaintenance).not.toHaveBeenCalled()
  await wrapper.findComponent(ConfirmDialog).find('button.primary').trigger('click')
  await flushPromises()
  expect(prepareMaintenance).toHaveBeenCalledWith('0.3.0-rc.2')
  expect(wrapper.text()).toContain('等待本机安装器')
  await wrapper.findAll('button').find((button) => button.text() === '取消准备').trigger('click')
  await flushPromises()
  expect(cancelMaintenance).toHaveBeenCalledOnce()
})
describe('rejected preparation', () => {
  it('shows the backend reason and keeps the service out of a claimed-success state', async () => {
    prepareMaintenance.mockRejectedValue({ response: { data: { detail: { message: '存在待核查实验，禁止升级' } } } })
    const wrapper = mount(MaintenancePanel)
    await flushPromises()
    await wrapper.find('input').setValue('0.3.0-rc.2')
    await wrapper.find('button.primary').trigger('click')
    await wrapper.findComponent(ConfirmDialog).find('button.primary').trigger('click')
    await flushPromises()
    expect(wrapper.find('[role="alert"]').text()).toContain('存在待核查实验')
    expect(wrapper.find('[role="status"]').text()).toContain('未进入维护')
  })
})
