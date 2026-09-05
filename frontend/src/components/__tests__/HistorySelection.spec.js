import { expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
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
