import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises } from '@vue/test-utils'
import { useWebSocket } from '@/composables/useWebSocket'
import { useAuthStore } from '@/stores/auth'
import { useDeviceStore } from '@/stores/device'
import { fetchStatus } from '@/api/status'

vi.mock('@/api/status', () => ({ fetchStatus: vi.fn() }))

class FakeWebSocket {
  static CONNECTING = 0
  static OPEN = 1
  static CLOSED = 3
  static instances = []

  constructor(url) {
    this.url = url
    this.readyState = FakeWebSocket.CONNECTING
    FakeWebSocket.instances.push(this)
  }

  sent = []

  send(message) {
    this.sent.push(JSON.parse(message))
  }

  close() {
    this.readyState = FakeWebSocket.CLOSED
    this.onclose?.()
  }
}

describe('useWebSocket 链路状态', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    setActivePinia(createPinia())
    localStorage.clear()
    FakeWebSocket.instances = []
    vi.stubGlobal('WebSocket', FakeWebSocket)
    fetchStatus.mockResolvedValue({
      comm_quality: 'degraded',
      system: { current_state: 'Standby', operation_state: 'idle' },
    })
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('通过首帧认证且收到 auth_ok 后才标记后端链路', async () => {
    const auth = useAuthStore()
    const device = useDeviceStore()
    auth.token = 'jwt-token'
    const connection = useWebSocket()

    connection.connect()
    const socket = FakeWebSocket.instances[0]
    expect(socket.url).not.toContain('jwt-token')
    expect(device.commQuality).toBe('offline')

    socket.readyState = FakeWebSocket.OPEN
    socket.onopen()
    expect(socket.sent).toEqual([{ type: 'authenticate', token: 'jwt-token' }])
    expect(device.backendConnected).toBe(false)
    expect(fetchStatus).not.toHaveBeenCalled()

    socket.onmessage({ data: JSON.stringify({ type: 'auth_ok' }) })
    await flushPromises()

    expect(device.backendConnected).toBe(true)
    expect(fetchStatus).toHaveBeenCalledOnce()
    expect(device.commQuality).toBe('degraded')

    connection.disconnect()
    expect(device.backendConnected).toBe(false)
    expect(device.commQuality).toBe('offline')
  })
})
