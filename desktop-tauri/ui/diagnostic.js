const title = document.querySelector('#title')
const message = document.querySelector('#message')
const retry = document.querySelector('#retry')
const labels = {
  configuration_missing: '尚未配置本机地址',
  configuration_invalid: '本机配置需要检查',
  service_unreachable: '暂时无法连接后台',
  certificate_error: '安全连接校验失败',
  service_response_invalid: '后台响应不匹配',
  runtime_missing: '桌面运行时缺失',
  page_load_failed: '页面未能加载',
}
function show(error) {
  title.textContent = labels[error?.kind] || '连接诊断'
  message.textContent = error?.message || '点击重试，重新检查本机配置与后台连接。'
  retry.disabled = false
  retry.textContent = '重试连接'
}
async function connect() {
  retry.disabled = true
  retry.textContent = '检查中…'
  title.textContent = '正在连接本机后台'
  message.textContent = '检查配置与服务响应，请稍候。'
  try {
    await window.__TAURI__.core.invoke('connect_service')
  } catch (error) {
    show(error)
  }
}
retry.addEventListener('click', connect)
async function initialize() {
  if (!window.__TAURI__?.core) {
    show({ message: '此诊断页由 SMD 桌面程序提供。请从桌面入口打开。' })
    retry.disabled = true
    return
  }
  try {
    const state = await window.__TAURI__.core.invoke('diagnostic_status')
    if (state.self_check) {
      // Round-trip proves the bundled page script and native renderer actually loaded.
      await window.__TAURI__.core.invoke('self_check_rendered')
    } else if (state.auto_connect) {
      await connect()
    } else {
      show(state.diagnostic)
    }
  } catch {
    show({ message: '无法读取桌面诊断状态，请关闭并重新打开窗口。' })
  }
}
initialize()
