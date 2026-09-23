import { beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import LoginPage from '@/pages/LoginPage.vue'
import { login } from '@/api/auth'

vi.mock('@/api/auth', () => ({ login: vi.fn(), logout: vi.fn() }))
vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn() }),
  useRoute: () => ({ query: {} }),
}))

beforeEach(() => {
  vi.clearAllMocks()
  setActivePinia(createPinia())
})

async function submit(wrapper, username = 'admin', password = 'whatever-password') {
  await wrapper.get('#login-username').setValue(username)
  await wrapper.get('#login-password').setValue(password)
  await wrapper.get('form').trigger('submit')
  await flushPromises()
}

it('输入校验失败时给出面向操作员的提示，不回显后端校验细节', async () => {
  // 后端 422 的形状：message 是一句话，结构化条目在 detail 里
  login.mockRejectedValue({
    response: {
      status: 422,
      data: {
        error_code: 'validation_error',
        message: '请求参数不符合接口要求，请检查后重试',
        detail: [
          {
            loc: ['body', 'username'],
            msg: "String should match pattern '^[A-Za-z0-9_]+$'",
            type: 'string_pattern_mismatch',
          },
        ],
      },
    },
  })

  const wrapper = mount(LoginPage)
  await submit(wrapper, '面条')

  const text = wrapper.get('.login-error').text()
  expect(text).toBe('用户名或密码格式不正确')
  expect(text).not.toContain('username')
  expect(text).not.toContain('^[A-Za-z0-9_]+$')
  expect(text).not.toContain('{')
  wrapper.unmount()
})

it('认证失败时显示后端给出的业务消息', async () => {
  login.mockRejectedValue({
    response: { status: 401, data: { error_code: 'invalid_credentials', message: '用户名或密码错误' } },
  })

  const wrapper = mount(LoginPage)
  await submit(wrapper)

  expect(wrapper.get('.login-error').text()).toBe('用户名或密码错误')
  wrapper.unmount()
})

it('后端无响应时退回通用中文提示，不暴露传输层英文错误', async () => {
  login.mockRejectedValue(new Error('Network Error'))

  const wrapper = mount(LoginPage)
  await submit(wrapper)

  expect(wrapper.get('.login-error').text()).toBe('登录失败，请检查用户名或密码')
  wrapper.unmount()
})
