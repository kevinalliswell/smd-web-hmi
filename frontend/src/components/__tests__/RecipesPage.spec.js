import { beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import RecipesPage from '@/pages/RecipesPage.vue'
import { useAuthStore } from '@/stores/auth'
import { useDeviceStore } from '@/stores/device'
import { fetchStandardTemplate, saveRecipe, validateRecipe, activateRecipe } from '@/api/recipes'
vi.mock('@/api/recipes', () => ({ fetchStandardTemplate: vi.fn(), saveRecipe: vi.fn(), validateRecipe: vi.fn(), activateRecipe: vi.fn() }))
const stage = { name: '冷却', kind: 'cool', n2_l_min: 2, co_l_min: 0, exit: { signal: 'burden_c', comparison: 'lt', value: 200 }, timeout_s: 86400 }
const definition = { schema_version: 1, name: '标准模板', mode: 'standard', description: '', stages: [stage] }
const row = { recipe_id: 'r1', version: 1, digest: 'abc', definition }
beforeEach(() => { vi.clearAllMocks(); setActivePinia(createPinia()); useAuthStore().role = 'admin'; fetchStandardTemplate.mockResolvedValue({ definition: structuredClone(definition) }) })
const button = (wrapper, text) => wrapper.findAll('button').find((item) => item.text() === text)
it('stage edits change standard to custom, append a version, and invalidate prior execution approval', async () => {
  const wrapper = mount(RecipesPage, { global: { stubs: { RecipePicker: { template: '<div />', methods: { reload() {} } } } } })
  await button(wrapper, '从标准候选模板新建').trigger('click'); await flushPromises()
  const inputs = wrapper.findAll('input'); await inputs[1].setValue('自定义冷却')
  expect(wrapper.text()).toContain('非标实验')
  saveRecipe.mockImplementation(async (draft) => ({ ...row, definition: JSON.parse(JSON.stringify(draft)) }))
  await wrapper.find('form').trigger('submit'); await flushPromises()
  expect(saveRecipe.mock.calls[0][0].mode).toBe('custom')
  expect(wrapper.text()).toContain('已保存 v1，尚未下发设备')
  validateRecipe.mockResolvedValue({ executable: true, errors: [], deviations: ['stage_1:modified'] })
  await button(wrapper, '校验设备能力与安全约束').trigger('click'); await flushPromises()
  expect(button(wrapper, '下发并回读配方').attributes('disabled')).toBeDefined()
  useDeviceStore().updateSnapshot({ comm_quality: 'online', data_fresh: true, system: { can_set_parameters: true } }); await flushPromises()
  expect(button(wrapper, '下发并回读配方').attributes('disabled')).toBeUndefined()
  await wrapper.findAll('input')[1].setValue('再修改')
  expect(button(wrapper, '下发并回读配方').attributes('disabled')).toBeDefined()
  expect(activateRecipe).not.toHaveBeenCalled()
  wrapper.unmount()
})
