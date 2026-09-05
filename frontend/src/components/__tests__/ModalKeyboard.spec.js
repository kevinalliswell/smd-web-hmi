import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent, nextTick, ref } from 'vue'
import ConfirmDialog from '@/components/shared/ConfirmDialog.vue'
import StartTestModal from '@/components/command/StartTestModal.vue'
import StopTestModal from '@/components/command/StopTestModal.vue'
import { requestConfirmToken, sendCommand } from '@/api/commands'
import PasswordResetDialog from '@/components/users/PasswordResetDialog.vue'

vi.mock('@/api/tests', () => ({ fetchNextTestId: vi.fn().mockResolvedValue('TEST-KEYBOARD') }))
vi.mock('@/api/recipes', () => ({ fetchRecipes: vi.fn().mockResolvedValue({ items: [], total: 0 }) }))
vi.mock('@/api/commands', () => ({ requestConfirmToken: vi.fn(), sendCommand: vi.fn() }))

const wrappers = []
function open(component, props = {}) {
  const harness = defineComponent({
    components: { Modal: component },
    setup: () => ({ shown: ref(false), props }),
    template: '<button id="opener" @click="shown = true">Open</button><Modal v-if="shown" v-bind="props" :model-value="true" @update:model-value="shown = $event" @close="shown = false" />',
  })
  const wrapper = mount(harness, { attachTo: document.body })
  wrappers.push(wrapper)
  return wrapper
}
async function show(wrapper) {
  wrapper.get('#opener').element.focus()
  await wrapper.get('#opener').trigger('click')
  await flushPromises()
}
afterEach(() => {
  wrappers.splice(0).forEach((wrapper) => wrapper.unmount())
  document.body.innerHTML = ''
})

describe('modal keyboard boundary', () => {
  it.each([ConfirmDialog, StopTestModal])('focuses cancel and restores its opener after Escape', async (component) => {
    const wrapper = open(component)
    await show(wrapper)
    const cancel = wrapper.get('[role="dialog"] button')
    expect(document.activeElement).toBe(cancel.element)
    await cancel.trigger('keydown', { key: 'Escape' })
    await nextTick()
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    expect(document.activeElement).toBe(wrapper.get('#opener').element)
  })

  it('cycles Tab/Shift+Tab and skips disabled controls', async () => {
    const wrapper = open(ConfirmDialog, { confirmDisabled: true })
    await show(wrapper)
    const cancel = wrapper.get('[role="dialog"] button')
    await cancel.trigger('keydown', { key: 'Tab' })
    expect(document.activeElement).toBe(cancel.element)
    await cancel.trigger('keydown', { key: 'Tab', shiftKey: true })
    expect(document.activeElement).toBe(cancel.element)
  })

  it('keeps busy confirmation focus inside the dialog without cancelling', async () => {
    const wrapper = open(ConfirmDialog, { busy: true })
    await show(wrapper)
    const dialog = wrapper.get('[role="dialog"]')
    expect(document.activeElement).toBe(dialog.element)
    await dialog.trigger('keydown', { key: 'Tab' })
    await dialog.trigger('keydown', { key: 'Escape' })
    expect(wrapper.find('[role="dialog"]').exists()).toBe(true)
    expect(document.activeElement).toBe(dialog.element)
  })

  it('returns from nested start confirmation to Next, then to the page opener', async () => {
    const wrapper = open(StartTestModal)
    await show(wrapper)
    expect(document.activeElement).toBe(wrapper.get('#start-test-id').element)
    await wrapper.get('#start-test-height').setValue('25')
    const next = wrapper.get('button.primary')
    next.element.focus()
    await next.trigger('click')
    const confirmation = wrapper.findComponent(ConfirmDialog)
    expect(document.activeElement).toBe(confirmation.get('button').element)
    await confirmation.get('button').trigger('keydown', { key: 'Escape' })
    await nextTick()
    expect(confirmation.find('[role="dialog"]').exists()).toBe(false)
    expect(document.activeElement).toBe(next.element)
    await next.trigger('keydown', { key: 'Escape' })
    await nextTick()
    expect(document.activeElement).toBe(wrapper.get('#opener').element)
  })

  it('keeps focus when submission disables the focused button and restores after acceptance', async () => {
    let resolveToken
    requestConfirmToken.mockImplementationOnce(() => new Promise((resolve) => { resolveToken = resolve }))
    sendCommand.mockResolvedValueOnce({ result: 'accepted' })
    const wrapper = open(StopTestModal)
    await show(wrapper)
    const confirm = wrapper.get('button.danger')
    confirm.element.focus()
    await confirm.trigger('click')
    const dialog = wrapper.get('[role="dialog"]')
    expect(document.activeElement).toBe(dialog.element)
    await dialog.trigger('keydown', { key: 'Escape' })
    expect(wrapper.find('[role="dialog"]').exists()).toBe(true)
    resolveToken({ confirm_token: 'fixture-only' })
    await flushPromises()
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    expect(document.activeElement).toBe(wrapper.get('#opener').element)
  })

  it('restores the page opener when both nested start dialogs unmount together', async () => {
    requestConfirmToken.mockResolvedValueOnce({ confirm_token: 'fixture-only' })
    sendCommand.mockResolvedValueOnce({ result: 'accepted' })
    const wrapper = open(StartTestModal)
    await show(wrapper)
    await wrapper.get('#start-test-height').setValue('25')
    const next = wrapper.get('button.primary')
    next.element.focus()
    await next.trigger('click')
    const confirm = wrapper.findComponent(ConfirmDialog).get('button.danger')
    confirm.element.focus()
    await confirm.trigger('click')
    await flushPromises()
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    expect(document.activeElement).toBe(wrapper.get('#opener').element)
  })

  it('focuses password input and returns through cancel without submitting', async () => {
    const wrapper = open(PasswordResetDialog)
    await show(wrapper)
    expect(document.activeElement).toBe(wrapper.get('#reset-password').element)
    await wrapper.get('#reset-password').trigger('keydown', { key: 'Tab', shiftKey: true })
    expect(document.activeElement).toBe(wrapper.get('button[type="button"]').element)
    await wrapper.get('button[type="button"]').trigger('click')
    await nextTick()
    expect(document.activeElement).toBe(wrapper.get('#opener').element)
  })
})
