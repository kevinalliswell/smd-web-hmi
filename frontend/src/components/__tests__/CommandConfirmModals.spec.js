import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ConfirmDialog from '@/components/shared/ConfirmDialog.vue'
import StartTestModal from '@/components/command/StartTestModal.vue'
import StopTestModal from '@/components/command/StopTestModal.vue'
import TareModal from '@/components/command/TareModal.vue'
import { requestConfirmToken, sendCommand } from '@/api/commands'

vi.mock('@/api/commands', () => ({
  requestConfirmToken: vi.fn(),
  sendCommand: vi.fn(),
}))

vi.mock('@/api/tests', () => ({
  fetchNextTestId: vi.fn().mockResolvedValue('TEST-20260610-001'),
}))

beforeEach(() => {
  vi.clearAllMocks()
  requestConfirmToken.mockResolvedValue({ confirm_token: 'token-1' })
  sendCommand.mockResolvedValue({ result: 'accepted' })
})

describe('command confirmation modals', () => {
  it('启动试验第二步使用 danger ConfirmDialog', async () => {
    const wrapper = mount(StartTestModal)
    expect(wrapper.findComponent(ConfirmDialog).find('.dialog').exists()).toBe(false)

    await wrapper.find('button.primary').trigger('click')

    const confirm = wrapper.findComponent(ConfirmDialog)
    expect(confirm.exists()).toBe(true)
    expect(confirm.props('danger')).toBe(true)
    expect(confirm.text()).toContain('CO 工艺阶段')
  })

  it.each([
    ['停止试验', StopTestModal],
    ['天平去皮', TareModal],
  ])('%s 直接复用 danger ConfirmDialog', (_name, component) => {
    const wrapper = mount(component)
    const confirm = wrapper.findComponent(ConfirmDialog)
    expect(confirm.exists()).toBe(true)
    expect(confirm.props('danger')).toBe(true)
  })

  it('停止命令失败时保留确认框并展示错误', async () => {
    sendCommand.mockRejectedValueOnce({ response: { data: { message: '控制板拒绝停止' } } })
    const wrapper = mount(StopTestModal)

    await wrapper.findComponent(ConfirmDialog).find('button.danger').trigger('click')
    await flushPromises()

    expect(wrapper.emitted('close')).toBeUndefined()
    expect(wrapper.findComponent(ConfirmDialog).exists()).toBe(true)
    expect(wrapper.text()).toContain('控制板拒绝停止')
  })
})
