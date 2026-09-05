import { beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ExperimentMetadataEditor from '@/components/history/ExperimentMetadataEditor.vue'
import IncompleteReviewPanel from '@/components/history/IncompleteReviewPanel.vue'
import ConfirmDialog from '@/components/shared/ConfirmDialog.vue'
import { useDeviceStore } from '@/stores/device'
import { reviseTestMetadata, reviewCloseTest } from '@/api/tests'
vi.mock('@/api/tests', () => ({ reviseTestMetadata: vi.fn(), reviewCloseTest: vi.fn() }))
beforeEach(() => { vi.clearAllMocks(); setActivePinia(createPinia()) })
it('preserves unknown conditions and shows conflicting height rejection without claiming success', async () => {
  reviseTestMetadata.mockRejectedValue({ response: { data: { error_code: 'height_conflict', message: 'H1-H2与已记录原始高度不一致' } } })
  const wrapper = mount(ExperimentMetadataEditor, { props: { test: { test_id: 't1', original_height_mm: 20, measurement_basis: {} } } })
  const labelInput = (text) => wrapper.findAll('label').find((label) => label.text() === text).find('input')
  await labelInput('H1 (mm)').setValue('50'); await labelInput('H2 (mm)').setValue('25')
  await wrapper.find('textarea').setValue('纸质原始记录核对')
  await wrapper.find('form').trigger('submit'); await flushPromises()
  expect(reviseTestMetadata).toHaveBeenCalledWith('t1', { sample_metadata: { h1_mm: 50, h2_mm: 25 }, report_context: {}, reason: '纸质原始记录核对' })
  expect(wrapper.text()).toContain('H1-H2与已记录原始高度不一致')
  expect(wrapper.emitted('updated')).toBeUndefined()
})
it('requires fresh low temperature plus explicit physical confirmation before incomplete archival', async () => {
  const wrapper = mount(IncompleteReviewPanel, { props: { test: { test_id: 't1' } } })
  expect(wrapper.find('button').attributes('disabled')).toBeDefined()
  useDeviceStore().updateSnapshot({ comm_quality: 'online', data_fresh: true, system: { operation_state: 'idle' }, measurement: { burden_temp_valid: true, burden_temp_deg_c: 199 } })
  await flushPromises(); await wrapper.find('button').trigger('click')
  const dialog = wrapper.findComponent(ConfirmDialog)
  expect(dialog.find('button.danger').attributes('disabled')).toBeDefined()
  await dialog.find('input[type=checkbox]').setValue(true); await dialog.find('textarea').setValue('现场核查：冷却与气体处置已完成')
  reviewCloseTest.mockResolvedValue({ phase: 'archived_incomplete', data_integrity: 'incomplete' })
  await dialog.find('button.danger').trigger('click'); await flushPromises()
  expect(reviewCloseTest).toHaveBeenCalledWith('t1', '现场核查：冷却与气体处置已完成')
  expect(wrapper.emitted('updated')).toHaveLength(1)
})
