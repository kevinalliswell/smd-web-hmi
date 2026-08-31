import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import PasswordResetDialog from '@/components/users/PasswordResetDialog.vue'

describe('PasswordResetDialog', () => {
  it('关闭时不渲染，打开时使用两个密码输入框', () => {
    const closed = mount(PasswordResetDialog, { props: { modelValue: false, username: 'alice' } })
    expect(closed.find('.dialog').exists()).toBe(false)

    const opened = mount(PasswordResetDialog, { props: { modelValue: true, username: 'alice' } })
    const inputs = opened.findAll('input')
    expect(inputs).toHaveLength(2)
    expect(inputs.every((input) => input.attributes('type') === 'password')).toBe(true)
    expect(opened.text()).toContain('alice')
  })

  it('密码过短或两次不一致时禁止提交', async () => {
    const wrapper = mount(PasswordResetDialog, { props: { modelValue: true, username: 'alice' } })
    const [password, confirmation] = wrapper.findAll('input')

    await password.setValue('short')
    await confirmation.setValue('short')
    expect(wrapper.get('[data-test="submit"]').attributes('disabled')).toBeDefined()

    await password.setValue('secure-password')
    await confirmation.setValue('different-password')
    expect(wrapper.text()).toContain('两次输入不一致')
    expect(wrapper.get('[data-test="submit"]').attributes('disabled')).toBeDefined()
  })

  it('有效确认后只提交密码，由父级在 API 成功后关闭', async () => {
    const wrapper = mount(PasswordResetDialog, { props: { modelValue: true, username: 'alice' } })
    const [password, confirmation] = wrapper.findAll('input')
    await password.setValue('secure-password')
    await confirmation.setValue('secure-password')

    expect(wrapper.get('[data-test="submit"]').attributes('disabled')).toBeUndefined()
    await wrapper.get('form').trigger('submit')

    expect(wrapper.emitted('submit')[0]).toEqual(['secure-password'])
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
  })

  it('显示服务端错误并在提交时锁定按钮', () => {
    const wrapper = mount(PasswordResetDialog, {
      props: { modelValue: true, username: 'alice', loading: true, error: '重置失败' },
    })
    expect(wrapper.text()).toContain('重置失败')
    expect(wrapper.get('[data-test="submit"]').attributes('disabled')).toBeDefined()
  })
})
