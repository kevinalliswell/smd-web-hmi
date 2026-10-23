import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import OverviewPage from '@/pages/OverviewPage.vue'
import { useAuthStore } from '@/stores/auth'
import { useDeviceStore } from '@/stores/device'
import { appCompatibility } from '@/utils/appVersion'

vi.mock('@/api/status', () => ({ fetchStatus: vi.fn().mockRejectedValue(new Error('offline')) }))
const wrappers = []
beforeEach(() => { setActivePinia(createPinia()); appCompatibility.mismatch = false })
afterEach(() => { wrappers.splice(0).forEach((w) => w.unmount()); appCompatibility.mismatch = false })
function render(role = 'operator') {
  useAuthStore().role = role
  const wrapper = mount(OverviewPage, { global: { stubs: {
    RouterLink: { template: '<a><slot /></a>' }, RealtimeChart: true, ControlOwnershipPanel: true,
    StartTestModal: true, StopTestModal: true,
  } } })
  wrappers.push(wrapper)
  return wrapper
}
it.each(['observer', 'operator', 'admin', 'maintainer'])('preserves overview role permissions for %s', async (role) => {
  const device = useDeviceStore()
  device.updateSnapshot({ comm_quality: 'online', system: { can_start_test: true, can_stop_test: true, operation_state: 'idle' } })
  const wrapper = render(role)
  await flushPromises()
  expect(wrapper.find('button.primary').exists()).toBe(role !== 'observer')
  if (role === 'observer') expect(wrapper.text()).toContain('观察员 · 只读')
  else {
    expect(wrapper.find('button.primary').element.disabled).toBe(false)
    appCompatibility.mismatch = true
    await flushPromises()
    expect(wrapper.find('button.primary').element.disabled).toBe(true)
    expect(wrapper.find('button.danger').element.disabled).toBe(false)
  }
})
it('distinguishes a live backend from an offline device and never presents stale completion as verified', async () => {
  const device = useDeviceStore()
  device.setBackendConnected(true)
  device.updateSnapshot({ comm_quality: 'offline', state_machine: { measurement_complete: true, safe_complete: false },
    temperature: { furnace_pv_deg_c: 1500 } })
  const wrapper = render()
  await flushPromises()
  expect(wrapper.text()).toContain('设备断连')
  expect(wrapper.text()).toContain('完成状态待核实')
  expect(wrapper.find('button.primary').element.disabled).toBe(true)
  expect(wrapper.find('.kpi-val').text()).not.toContain('1500')
})
