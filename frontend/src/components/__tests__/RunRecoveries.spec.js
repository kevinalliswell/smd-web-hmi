import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import RunRecoveriesPage from '@/pages/RunRecoveriesPage.vue'
import { useAuthStore } from '@/stores/auth'
import { fetchRunRecoveries, fetchRunRecovery, bindRunRecovery } from '@/api/runRecoveries'

vi.mock('@/api/runRecoveries', () => ({ fetchRunRecoveries: vi.fn(), fetchRunRecovery: vi.fn(), bindRunRecovery: vi.fn(), replayRunRecovery: vi.fn() }))
const first = { id: 'a', device_id: 'device-a', run_id: 'run-a', review_state: 'unreviewed', review_revision: 3, replay_status: 'not_bound', first_seen_at: '2026-09-08T00:00:00Z', evidence: {}, source_count: 2, reviews: [] }
const second = { ...first, id: 'b', device_id: 'device-b', run_id: 'run-b' }
let wrapper
beforeEach(() => {
  vi.clearAllMocks(); setActivePinia(createPinia()); useAuthStore().role = 'admin'
  fetchRunRecoveries.mockResolvedValue([first, second]); fetchRunRecovery.mockImplementation(id => Promise.resolve(id === 'a' ? first : second))
})
afterEach(() => wrapper?.unmount())
const start = async () => { wrapper = mount(RunRecoveriesPage, { global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } } }); await flushPromises() }

it('requires review reason and reuses the same identity after an uncertain response', async () => {
  await start(); await wrapper.findAll('.record')[0].trigger('click'); await flushPromises()
  const action = () => wrapper.findAll('button').find(b => b.text() === '确认归属并回放')
  expect(action().attributes('disabled')).toBeDefined()
  await wrapper.find('textarea').setValue('核对纸质试验编号及控制板运行')
  bindRunRecovery.mockRejectedValue(new Error('offline'))
  await action().trigger('click'); await flushPromises()
  await action().trigger('click'); await flushPromises()
  expect(bindRunRecovery).toHaveBeenCalledTimes(2)
  expect(bindRunRecovery.mock.calls[0]).toEqual(bindRunRecovery.mock.calls[1])
  expect(bindRunRecovery.mock.calls[0][1]).toMatchObject({ expected_review_revision: 3, target_test_id: null })
  expect(wrapper.text()).toContain('发现时间不是实验开始时间')
})

it('does not let a late old selection replace the current run or its review input', async () => {
  let release
  fetchRunRecovery.mockImplementation(id => id === 'a' ? new Promise(resolve => { release = resolve }) : Promise.resolve(second))
  await start()
  await wrapper.findAll('.record')[0].trigger('click')
  await wrapper.findAll('.record')[1].trigger('click'); await flushPromises()
  await wrapper.find('textarea').setValue('仅核查第二个运行')
  release(first); await flushPromises()
  expect(wrapper.find('.detail').text()).toContain('run-b')
  expect(wrapper.find('textarea').element.value).toBe('仅核查第二个运行')
  fetchRunRecovery.mockResolvedValue(first)
  await wrapper.findAll('.record')[0].trigger('click'); await flushPromises()
  expect(wrapper.find('textarea').element.value).toBe('')
})

it('keeps observers read-only and blocks conflicting evidence for administrators', async () => {
  useAuthStore().role = 'observer'
  await start(); await wrapper.findAll('.record')[0].trigger('click'); await flushPromises()
  expect(wrapper.find('textarea').exists()).toBe(false)
  useAuthStore().role = 'admin'
  fetchRunRecovery.mockResolvedValue({ ...first, review_state: 'conflict' })
  await wrapper.findAll('.record')[0].trigger('click'); await flushPromises()
  await wrapper.find('textarea').setValue('冲突不能覆盖')
  expect(wrapper.findAll('button').find(b => b.text() === '确认归属并回放').attributes('disabled')).toBeDefined()
})
