import { beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import OperationsPage from '@/pages/OperationsPage.vue'
import { useDeviceStore } from '@/stores/device'
import { useAuthStore } from '@/stores/auth'
import { requestSourceLogRecovery, fetchSourceLogRecovery } from '@/api/maintenance'
vi.mock('@/api/commands', () => ({ fetchOperations: vi.fn().mockResolvedValue([]) }))
vi.mock('@/api/maintenance', async (importOriginal) => ({ ...await importOriginal(), requestSourceLogRecovery: vi.fn(), fetchSourceLogRecovery: vi.fn() }))
beforeEach(() => { vi.clearAllMocks(); setActivePinia(createPinia()); useAuthStore().role = 'admin'; useDeviceStore().updateSnapshot({ comm_quality: 'online', system: { protocol_version: '2.0' } }) })
it('only starts an explicit scan, defaults to incremental and preserves an operator uint64 sequence as text', async () => {
  const wrapper = mount(OperationsPage)
  await flushPromises()
  const button = text => wrapper.findAll('button').find(b => b.text() === text)
  expect(requestSourceLogRecovery).not.toHaveBeenCalled()
  requestSourceLogRecovery.mockResolvedValue({ task_id: 'scan1', status: 'pending' })
  fetchSourceLogRecovery.mockResolvedValue({ task_id: 'scan1', status: 'completed', progress: 100 })
  await button('补传设备日志').trigger('click'); await flushPromises()
  expect(requestSourceLogRecovery).toHaveBeenCalledWith(undefined)
  expect(wrapper.text()).toContain('请在报告中核查完整性')
  await wrapper.find('input[aria-label="起始源记录序号"]').setValue('18446744073709551616')
  expect(button('补传设备日志').attributes('disabled')).toBeDefined()
  await wrapper.find('input[aria-label="起始源记录序号"]').setValue('18446744073709551615')
  fetchSourceLogRecovery.mockResolvedValue({ task_id: 'scan2', status: 'failed', progress: 10, message: '设备日志读取失败' })
  await button('补传设备日志').trigger('click'); await flushPromises()
  expect(requestSourceLogRecovery).toHaveBeenLastCalledWith('18446744073709551615')
  expect(wrapper.text()).toContain('设备日志读取失败')
  expect(wrapper.text()).not.toContain('数据已完整')
  wrapper.unmount()
})
