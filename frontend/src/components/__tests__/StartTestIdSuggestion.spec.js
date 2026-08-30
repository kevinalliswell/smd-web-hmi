import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import StartTestModal from '@/components/command/StartTestModal.vue'
import { fetchNextTestId } from '@/api/tests'

vi.mock('@/api/tests', () => ({
  fetchNextTestId: vi.fn(),
}))

describe('StartTestModal 试验编号建议', () => {
  it('挂载后使用后端返回的当日递增编号', async () => {
    fetchNextTestId.mockResolvedValueOnce('TEST-20260610-004')
    const wrapper = mount(StartTestModal)

    await flushPromises()

    expect(fetchNextTestId).toHaveBeenCalledOnce()
    expect(wrapper.find('input').element.value).toBe('TEST-20260610-004')
  })
})
