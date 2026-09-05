import { expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import RepeatabilityPanel from '@/components/analytics/RepeatabilityPanel.vue'
import { evaluateRepeatability } from '@/api/analytics'
vi.mock('@/api/analytics', () => ({ evaluateRepeatability: vi.fn() }))
it('retains measurement order, requests additional runs and clears stale results after selection changes', async () => {
  evaluateRepeatability.mockResolvedValue({ eligible: true, results: [{ metric: 't10', result: null, additional_runs: 1, used_values: [], tolerances: { A: 10, B: 15, C: 20 }, rules_version: 'test' }] })
  const wrapper = mount(RepeatabilityPanel, { props: { testIds: ['second', 'first'] } })
  await wrapper.find('button.primary').trigger('click'); await flushPromises()
  expect(evaluateRepeatability).toHaveBeenCalledWith(['second', 'first'])
  expect(wrapper.text()).toContain('需增加 1 次测定')
  await wrapper.setProps({ testIds: ['third', 'fourth'] })
  expect(wrapper.find('[role=status]').exists()).toBe(false)
})
