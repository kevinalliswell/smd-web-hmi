import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import OperationResult from '@/components/command/OperationResult.vue'
import { fetchOperation } from '@/api/commands'
vi.mock('@/api/commands', async (importOriginal) => ({ ...await importOriginal(), fetchOperation: vi.fn() }))
describe('operation result query', () => {
  beforeEach(() => setActivePinia(createPinia()))
  it('keeps unknown visible and only resolves after the backend reports acceptance', async () => {
    fetchOperation.mockResolvedValueOnce({ operation_status: 'unknown' }).mockResolvedValueOnce({ operation_status: 'accepted' })
    const wrapper = mount(OperationResult, { props: { operationId: 'a-stable-id' } })
    await wrapper.find('button').trigger('click'); await flushPromises()
    expect(wrapper.text()).toContain('结果仍未知')
    expect(wrapper.emitted('resolved')).toBeUndefined()
    await wrapper.find('button').trigger('click'); await flushPromises()
    expect(wrapper.emitted('resolved')[0][0].operation_status).toBe('accepted')
    expect(fetchOperation).toHaveBeenCalledWith('a-stable-id')
  })
})
