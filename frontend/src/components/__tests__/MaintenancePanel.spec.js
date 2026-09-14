import { beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import MaintenancePanel from '@/components/system/MaintenancePanel.vue'
import { fetchMaintenance } from '@/api/maintenance'
vi.mock('@/api/maintenance', () => ({ fetchMaintenance: vi.fn() }))
beforeEach(() => {
  vi.clearAllMocks()
  fetchMaintenance.mockResolvedValue({ state: 'idle', current_version: '0.3.0' })
})
it('shows installed version and direct installer instructions without manual preparation', async () => {
  const wrapper = mount(MaintenancePanel)
  await flushPromises()
  expect(wrapper.text()).toContain('版本与维护状态')
  expect(wrapper.text()).toContain('当前版本：0.3.0')
  expect(wrapper.text()).toContain('保留数据库、配置和账号')
  expect(wrapper.findAll('input')).toHaveLength(0)
  expect(wrapper.findAll('button').map(button => button.text())).toEqual(['刷新状态'])
  expect(fetchMaintenance).toHaveBeenCalledOnce()
})
it('keeps unfinished maintenance visible and refreshes after installer recovery', async () => {
  fetchMaintenance.mockResolvedValueOnce({ state: 'claimed', current_version: '0.3.0-rc.4', target_version: '0.3.0' })
  const wrapper = mount(MaintenancePanel)
  await flushPromises()
  expect(wrapper.find('[role="status"]').text()).toContain('本机完成安装或恢复')
  await wrapper.find('button').trigger('click')
  await flushPromises()
  expect(wrapper.find('[role="status"]').text()).toContain('当前无维护操作')
})
it('shows failure without reporting maintenance completion', async () => {
  fetchMaintenance.mockRejectedValue({ response: { data: { message: '维护记录损坏' } } })
  const wrapper = mount(MaintenancePanel)
  await flushPromises()
  expect(wrapper.find('[role="alert"]').text()).toContain('维护记录损坏')
  expect(wrapper.find('[role="status"]').text()).toContain('维护状态未知')
})
