// 全局 WebSocket 连接管理：自动重连（指数退避，封顶 30s）、
// 重连后无需重新订阅（后端默认全订阅）、按 type 分发到各 store。
import { useAuthStore } from '@/stores/auth'
import { useDeviceStore } from '@/stores/device'
import { useAlarmsStore } from '@/stores/alarms'
import { useTestStore } from '@/stores/test'

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
    socket = new WebSocket(wsUrl(auth.token))

    socket.onopen = () => {
      reconnectDelay = 1000
      // 不在此处置 commQuality：本事件只说明"浏览器↔后端"已通，
      // 而 commQuality 描述的是"后端↔控制板"链路。设备真实状态由后端
      // 在接入时补推的 comm_status 快照给出（安全红线 6：断链须显示报警）。
      // 应用层心跳
      pingTimer = setInterval(() => {
        if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: 'ping' }))
      }, 15000)
    }

    socket.onmessage = (ev) => {
      try {
        dispatch(JSON.parse(ev.data))
      } catch {
        /* 忽略非 JSON 帧 */
      }
    }

    socket.onclose = () => {
      clearInterval(pingTimer)
      // WS 断开后设备链路状态无从得知，按最保守值显示
      device.setCommQuality('offline')
      if (!manualClose && auth.token) {
        setTimeout(connect, reconnectDelay)
        reconnectDelay = Math.min(reconnectDelay * 2, RECONNECT_MAX)
      }
    }

    socket.onerror = () => socket?.close()
  }

  function disconnect() {
    manualClose = true
    clearInterval(pingTimer)
    if (socket) {
      socket.close()
      socket = null
    }
  }

  return { connect, disconnect }
}
