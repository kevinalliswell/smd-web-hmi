vi.mock('@/api/reports', () => ({ generateReport: vi.fn().mockResolvedValue({ id: 7, format: 'pdf' }), reportDownloadUrl: id => `/api/reports/${id}/download` }))
vi.mock('@/utils/download', () => ({ downloadFile: vi.fn() }))
import { expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { useAuthStore } from '@/stores/auth'
import { generateReport } from '@/api/reports'
import { downloadFile } from '@/utils/download'
import HistoryPage from '@/pages/HistoryPage.vue'
import { fetchTestDetail } from '@/api/tests'
vi.mock('@/api/tests', () => ({ fetchTests: vi.fn().mockResolvedValue([{ test_id: 'OLD' }, { test_id: 'NEW' }]), fetchTestDetail: vi.fn(), fetchTestSamples: vi.fn().mockResolvedValue({ points: [] }), fetchTestEvents: vi.fn().mockResolvedValue([]), fetchTestAlarms: vi.fn().mockResolvedValue([]) }))
it('keeps the newest selection when an earlier detail request finishes later', async () => {
  setActivePinia(createPinia())
  let finishOld
  fetchTestDetail.mockImplementation((id) => id === 'OLD' ? new Promise(resolve => { finishOld = resolve }) : Promise.resolve({ test_id: 'NEW' }))
  const wrapper = mount(HistoryPage, { global: { stubs: { HistoryChart: true, AlarmTable: true, ExperimentMetadataEditor: true, IncompleteReviewPanel: true } } })
  await flushPromises()
  await wrapper.findAll('button').find(button => button.text() === 'OLD').trigger('click')
  await wrapper.findAll('button').find(button => button.text() === 'NEW').trigger('click')
  await flushPromises(); finishOld({ test_id: 'OLD' }); await flushPromises()
  expect(wrapper.find('.detail .card-title').text()).toBe('NEW')
})

it('names a generated report using the actual server format', async () => {
  setActivePinia(createPinia())
  useAuthStore().role = 'admin'
  fetchTestDetail.mockResolvedValue({ test_id: 'NEW' })
  const wrapper = mount(HistoryPage, { global: { stubs: { HistoryChart: true, AlarmTable: true, ExperimentMetadataEditor: true, IncompleteReviewPanel: true } } })
  await flushPromises()
  await wrapper.findAll('button').find(button => button.text() === 'NEW').trigger('click')
  await flushPromises()
  await wrapper.findAll('button').find(button => button.text() === '生成报告').trigger('click')
  await flushPromises()
  expect(generateReport).toHaveBeenCalledWith('NEW')
  expect(downloadFile).toHaveBeenCalledWith('/api/reports/7/download', 'NEW-report.pdf')
})
