import { expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import StatusBadge from '@/components/shared/StatusBadge.vue'
it('does not show an unspecified safety condition as normal or triggered', () => {
  const wrapper = mount(StatusBadge, { props: { label: 'CO 一级报警' } })
  expect(wrapper.text()).toContain('未知')
  expect(wrapper.find('.dot-gray').exists()).toBe(true)
})
