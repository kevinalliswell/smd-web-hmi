// 全局 WebSocket 连接管理：自动重连（指数退避，封顶 30s）、
// 重连后无需重新订阅（后端默认全订阅）、按 type 分发到各 store。
import { useAuthStore } from '@/stores/auth'
import { useDeviceStore } from '@/stores/device'
import { useAlarmsStore } from '@/stores/alarms'
import { useTestStore } from '@/stores/test'
import { fetchStatus } from '@/api/status'

let socket = null
let reconnectDelay = 1000
const RECONNECT_MAX = 30000
let manualClose = false
let pingTimer = null

function wsUrl(token) {
  const base =
    import.meta.env.VITE_WS_URL ||
    `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/realtime`
  return `${base}?token=${encodeURIComponent(token)}`
}

export function useWebSocket() {
  const auth = useAuthStore()
  const device = useDeviceStore()
  const alarms = useAlarmsStore()
  const test = useTestStore()

  function dispatch(msg) {
    switch (msg.type) {
      case 'status_update':
        device.updateSnapshot(msg.data)
        break
      case 'alarm_new':
        alarms.addAlarm(msg.data)
        break
      case 'alarm_clear':
        alarms.clearAlarm(msg.data.alarm_id)
        break
      case 'alarm_ack':
        alarms.ackAlarm(msg.data.alarm_id, msg.data.ack_operator, msg.data.ack_time)
        break
      case 'comm_status':
        device.setCommQuality(msg.data.status)
        break
      case 'state_change':
        if (test.currentTest) test.currentTest.state = msg.data.to_state
        break
      case 'pong':
        break
      default:
        // command_result / *_progress 等由各页面按需处理
        break
    }
  }

  function connect() {
    if (!auth.token || (socket && socket.readyState <= 1)) return
    manualClose = false
    device.setBackendConnected(false)
    device.setCommQuality('offline')
    const activeSocket = new WebSocket(wsUrl(auth.token))
    socket = activeSocket

    activeSocket.onopen = () => {
      if (socket !== activeSocket) return
      reconnectDelay = 1000
      device.setBackendConnected(true)
      // 浏览器↔后端 WS 与后端↔控制板 HostComm 是两条链路。
      // 建连后通过 REST 读取 HostComm 当前真值，不能把 WS onopen 当作设备在线。
      fetchStatus()
        .then((snapshot) => {
          if (socket === activeSocket && activeSocket.readyState === WebSocket.OPEN) {
            device.updateSnapshot(snapshot)
          }
        })
        .catch(() => {})
      // 应用层心跳
      pingTimer = setInterval(() => {
        if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: 'ping' }))
      }, 15000)
    }

    activeSocket.onmessage = (ev) => {
      if (socket !== activeSocket) return
      try {
        dispatch(JSON.parse(ev.data))
      } catch {
        /* 忽略非 JSON 帧 */
      }
    }

    activeSocket.onclose = () => {
      if (socket !== activeSocket) return
      clearInterval(pingTimer)
      device.setBackendConnected(false)
      device.setCommQuality('offline')
      if (socket === activeSocket) socket = null
      if (!manualClose && auth.token) {
        setTimeout(connect, reconnectDelay)
        reconnectDelay = Math.min(reconnectDelay * 2, RECONNECT_MAX)
      }
    }

    activeSocket.onerror = () => activeSocket.close()
  }

  function disconnect() {
    manualClose = true
    clearInterval(pingTimer)
    if (socket) {
      const activeSocket = socket
      activeSocket.close()
      socket = null
    }
    device.setBackendConnected(false)
    device.setCommQuality('offline')
  }

  return { connect, disconnect }
}
