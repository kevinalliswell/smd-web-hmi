// 回归测试：WS 连接事件不得伪造设备链路状态（审查 #15 之 2）
import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useWebSocket } from '@/composables/useWebSocket'
import { useAuthStore } from '@/stores/auth'
import { useDeviceStore } from '@/stores/device'

class FakeWebSocket {
  static OPEN = 1
  static instances = []

  constructor(url) {
    this.url = url
    this.readyState = 0
    FakeWebSocket.instances.push(this)
  }

  send() {}

  close() {
    this.readyState = 3
    this.onclose?.()
  }

  // 测试驱动：模拟握手完成与服务端下发
  fireOpen() {
    this.readyState = 1
    this.onopen?.()
  }

  fireMessage(payload) {
    this.onmessage?.({ data: JSON.stringify(payload) })
  }
}

describe('useWebSocket 链路状态语义', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    FakeWebSocket.instances = []
    globalThis.WebSocket = FakeWebSocket
    localStorage.clear()
  })

  function connectWithToken() {
    const auth = useAuthStore()
    auth.token = 'test-token'
    const ws = useWebSocket()
    ws.connect()
    return { ws, socket: FakeWebSocket.instances.at(-1) }
  }

  it('握手完成不把 commQuality 置为 online（那只说明浏览器连上了后端）', () => {
    const device = useDeviceStore()
    const { ws, socket } = connectWithToken()
    socket.fireOpen()
    // 设备链路状态只能由后端下发的 comm_status 决定
    expect(device.commQuality).toBe('offline')
    ws.disconnect()
  })

  it('按后端补推的 comm_status 更新链路状态', () => {
    const device = useDeviceStore()
    const { ws, socket } = connectWithToken()
    socket.fireOpen()
    socket.fireMessage({ type: 'comm_status', data: { status: 'degraded' } })
    expect(device.commQuality).toBe('degraded')
    socket.fireMessage({ type: 'comm_status', data: { status: 'online' } })
    expect(device.commQuality).toBe('online')
    ws.disconnect()
  })

  it('连接断开后按最保守值显示离线', () => {
    const device = useDeviceStore()
    const { ws, socket } = connectWithToken()
    socket.fireOpen()
    socket.fireMessage({ type: 'comm_status', data: { status: 'online' } })
    socket.close()
    expect(device.commQuality).toBe('offline')
    ws.disconnect()
  })

  it('未登录（无 token）时不建立连接', () => {
    const ws = useWebSocket()
    ws.connect()
    expect(FakeWebSocket.instances).toHaveLength(0)
    ws.disconnect()
  })
})
