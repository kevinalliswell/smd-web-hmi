// Isolated browser verification: all APIs/WS use fixtures; no command is sent.
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { createRequire } from 'node:module'
const require = createRequire(import.meta.url)
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright')
const target = new URL(process.env.KEYBOARD_TEST_URL || 'http://127.0.0.1:18106')
assert.equal(target.hostname, '127.0.0.1')
const output = process.env.KEYBOARD_TEST_OUTPUT || '../keyboard-verification'
fs.mkdirSync(output, { recursive: true })
const browser = await chromium.launch({ headless: true })
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
page.setDefaultTimeout(7000)
const errors = [], writes = [], cases = [], sockets = []
let state = 'Heating'
const snapshot = () => ({
  comm_quality: 'online', data_fresh: true, control_ready: true, last_update: new Date().toISOString(),
  system: { can_start_test: state === 'Standby', can_stop_test: state === 'Heating', can_set_parameters: state === 'Standby', operation_state: state === 'Standby' ? 'idle' : 'running', is_running: state === 'Heating' },
  state_machine: { current_state: state }, temperature: {}, measurement: {},
})
page.on('pageerror', (error) => errors.push(error.message))
page.on('console', (message) => { if (['error', 'warning'].includes(message.type())) errors.push(message.text()) })
await page.route('**/*', (route) => {
  const url = new URL(route.request().url())
  if (url.origin !== target.origin) return route.abort()
  if (!url.pathname.startsWith('/api/')) return route.continue()
  const endpoint = url.pathname
  let data = []
  if (endpoint === '/api/auth/login') data = { token: 'keyboard-fixture-only', role: 'admin', display_name: '键盘验收夹具', must_change_password: false }
  else if (route.request().method() !== 'GET') { writes.push(endpoint); return route.abort() }
  else if (endpoint === '/api/status') data = snapshot()
  else if (endpoint === '/api/tests/current') data = null
  else if (endpoint === '/api/tests/next-id') data = { test_id: 'TEST-KEYBOARD-FIXTURE' }
  else if (endpoint === '/api/recipes') data = { items: [], total: 0 }
  else if (endpoint === '/api/system/health') data = { version: '0.3.0-rc.2' }
  else if (endpoint === '/api/system/info') data = { hostcomm: { comm_quality: 'online', mock: true }, db_path: 'fixture-only' }
  else if (endpoint === '/api/system/maintenance') data = { state: 'idle' }
  else if (endpoint === '/api/users') data = [{ id: 1, username: 'keyboard-fixture', role: 'observer', is_active: true }]
  return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ data, ts: new Date().toISOString() }) })
})
await page.routeWebSocket('**/ws/realtime', (socket) => {
  sockets.push(socket)
  socket.onMessage((raw) => {
    const message = JSON.parse(raw)
    if (message.type === 'authenticate') socket.send(JSON.stringify({ type: 'auth_ok' }))
    if (message.type === 'ping') socket.send(JSON.stringify({ type: 'pong' }))
  })
})
const timer = setInterval(() => {
  for (const socket of sockets.slice(-1)) socket.send(JSON.stringify({ type: 'status_update', data: snapshot() }))
}, 250)
async function focused(locator) { assert(await locator.evaluate((element) => element === document.activeElement)) }
async function cycle(dialog, first, last) {
  await focused(first)
  await page.keyboard.press('Shift+Tab'); await focused(last)
  await page.keyboard.press('Tab'); await focused(first)
  for (let i = 0; i < 12; i++) {
    await page.keyboard.press('Tab')
    assert(await dialog.evaluate((element) => element.contains(document.activeElement)), 'Tab escaped modal')
  }
}
async function open(button) { await button.focus(); await page.keyboard.press('Enter') }
async function escape(dialog, opener) {
  await page.keyboard.press('Escape')
  await dialog.waitFor({ state: 'detached' })
  await focused(opener)
}
try {
  await page.goto(target.href)
  await page.getByPlaceholder('用户名', { exact: true }).fill('admin')
  await page.getByPlaceholder('密码', { exact: true }).fill('fixture-only')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await page.getByRole('heading', { name: '实时总览' }).waitFor()
  await page.waitForFunction(() => [...document.querySelectorAll('button')].some((button) => button.textContent.includes('停止试验') && !button.disabled))
  const stop = page.getByRole('button', { name: '■ 停止试验' })
  await open(stop)
  let dialog = page.getByRole('dialog', { name: /停止试验$/ })
  await cycle(dialog, dialog.getByRole('button', { name: '取消', exact: true }), dialog.getByRole('button', { name: '确认停止', exact: true }))
  await page.screenshot({ path: path.join(output, 'keyboard-stop.png') })
  await escape(dialog, stop)
  cases.push('Stop: cancel focus, forward/reverse wrap, Escape and opener restoration')

  state = 'Standby'
  const start = page.getByRole('button', { name: '▶ 启动试验' })
  await page.waitForFunction(() => [...document.querySelectorAll('button')].some((button) => button.textContent.includes('启动试验') && !button.disabled))
  await open(start)
  dialog = page.getByRole('dialog', { name: /启动试验$/ })
  await cycle(dialog, page.getByLabel('试验编号', { exact: true }), dialog.getByRole('button', { name: '取消', exact: true }))
  await page.getByLabel('原始料层高度 H (mm)').fill('25')
  const next = dialog.getByRole('button', { name: '下一步', exact: true })
  await open(next)
  const nested = page.getByRole('dialog').filter({ has: page.getByRole('button', { name: '确认启动', exact: true }) })
  await cycle(nested, nested.getByRole('button', { name: '取消', exact: true }), nested.getByRole('button', { name: '确认启动', exact: true }))
  await escape(nested, next)
  assert.equal(await page.getByRole('dialog').count(), 1)
  await escape(dialog, start)
  cases.push('Start: input focus, hidden/disabled skip, nested wrap, Escape returns Next then page opener')

  await page.getByRole('link', { name: '系统设置' }).click()
  const reset = page.getByRole('button', { name: '重置密码', exact: true })
  await open(reset)
  dialog = page.getByRole('dialog')
  await cycle(dialog, page.getByLabel('新密码', { exact: true }), dialog.getByRole('button', { name: '取消', exact: true }))
  await escape(dialog, reset)
  await open(reset)
  await page.keyboard.press('Shift+Tab'); await page.keyboard.press('Enter')
  await dialog.waitFor({ state: 'detached' }); await focused(reset)
  cases.push('Password: input focus, disabled submit skip, Tab wrap, Escape/cancel restoration')

  await page.getByLabel('目标版本', { exact: true }).fill('0.3.0-rc.3')
  const prepare = page.getByRole('button', { name: '准备升级', exact: true })
  await open(prepare)
  dialog = page.getByRole('dialog', { name: '准备离线升级', exact: true })
  await cycle(dialog, dialog.getByRole('button', { name: '取消', exact: true }), dialog.getByRole('button', { name: '确认准备', exact: true }))
  await escape(dialog, prepare)
  cases.push('Common mounted confirmation: cancel focus, Tab wrap, Escape/opener restoration')
  assert.deepEqual(writes, []); assert.deepEqual(errors, [])
  const result = { browser: browser.version(), fixture: 'Real Vue/Chromium with all API/WS fixtures; no backend, hardware or writes', cases, writes, errors }
  fs.writeFileSync(path.join(output, 'keyboard-after.json'), JSON.stringify(result, null, 2))
  console.log(JSON.stringify(result, null, 2))
} finally { clearInterval(timer); await browser.close() }
