import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ConfirmDialog from '@/components/shared/ConfirmDialog.vue'

describe('ConfirmDialog', () => {
  it('modelValue=false 时不渲染', () => {
    const w = mount(ConfirmDialog, { props: { modelValue: false } })
    expect(w.find('.dialog').exists()).toBe(false)
  })

  it('modelValue=true 渲染标题与默认插槽内容', () => {
    const w = mount(ConfirmDialog, {
      props: { modelValue: true, title: '确认下发' },
      slots: { default: '将下发 3 项参数' },
    })
    expect(w.find('.dialog').exists()).toBe(true)
    expect(w.text()).toContain('确认下发')
    expect(w.text()).toContain('将下发 3 项参数')
  })

  it('点击确认按钮 emit confirm 并关闭', async () => {
    const w = mount(ConfirmDialog, { props: { modelValue: true, confirmText: '确认' } })
    const buttons = w.findAll('button')
    await buttons[buttons.length - 1].trigger('click') // 最后一个为确认
    expect(w.emitted('confirm')).toHaveLength(1)
    expect(w.emitted('update:modelValue')[0]).toEqual([false])
  })

  it('点击取消按钮 emit cancel', async () => {
    const w = mount(ConfirmDialog, { props: { modelValue: true } })
    await w.findAll('button')[0].trigger('click') // 第一个为取消
    expect(w.emitted('cancel')).toHaveLength(1)
    expect(w.emitted('update:modelValue')[0]).toEqual([false])
  })

  it('danger 模式应用危险样式类', () => {
    const w = mount(ConfirmDialog, { props: { modelValue: true, danger: true } })
    expect(w.find('.dialog').classes()).toContain('danger')
  })

  it('busy 时禁止确认、取消与遮罩关闭', async () => {
    const w = mount(ConfirmDialog, { props: { modelValue: true, busy: true } })
    const buttons = w.findAll('button')
    expect(buttons.every((button) => button.attributes('disabled') !== undefined)).toBe(true)
    await buttons[0].trigger('click')
    await buttons[1].trigger('click')
    await w.find('.overlay').trigger('click')
    expect(w.emitted('confirm')).toBeUndefined()
    expect(w.emitted('cancel')).toBeUndefined()
  })

  it('closeOnConfirm=false 时由调用方控制关闭时机', async () => {
    const w = mount(ConfirmDialog, {
      props: { modelValue: true, closeOnConfirm: false },
    })
    await w.findAll('button')[1].trigger('click')
    expect(w.emitted('confirm')).toHaveLength(1)
    expect(w.emitted('update:modelValue')).toBeUndefined()
  })
})
