<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { useDeviceStore } from '@/stores/device'
import { fetchHostcommStatus, hostcommDebug } from '@/api/system'

const device = useDeviceStore()
const { snapshot } = storeToRefs(device)

const hc = ref(null)
const debugOut = ref('')
const debugBusy = ref(false)
const banner = ref('')
let timer = null

async function loadHostcomm() {
  try {
    hc.value = await fetchHostcommStatus()
  } catch (e) {
    banner.value = '读取 HostComm 状态失败：' + (e.response?.data?.message || e.message)
  }
}

async function runDebug(action) {
  debugBusy.value = true
  banner.value = ''
  try {
    const r = await hostcommDebug(action)
    debugOut.value = JSON.stringify(r.response, null, 2)
  } catch (e) {
    debugOut.value = ''
    banner.value = `调试 ${action} 失败：` + (e.response?.data?.message || e.message)
  } finally {
    debugBusy.value = false
  }
}

// 子设备通信状态（取自实时状态快照）
const subDevices = computed(() => {
  const s = snapshot.value || {}
  const t = s.temperature || {}
  const g = s.gas || {}
  const m = s.measurement || {}
  const norm = (status, alarm) => ({
    status: status || '—',
    alarm: alarm || null,
    ok: (status ? status === 'ok' : true) && !alarm,
  })
  return [
    { name: '控温仪', ...norm(t.temp_ctrl_run_state, t.temp_ctrl_alarm_code) },
    { name: 'N₂ MFC', ...norm(g.n2_status, g.n2_alarm_code) },
    { name: 'CO MFC', ...norm(g.co_status, g.co_alarm_code) },
    {
      name: '电子天平',
      ...norm(m.balance_status || (m.balance_stable ? 'ok' : 'unstable'), m.balance_alarm_code),
    },
  ]
})

const hcRows = computed(() => {
  if (!hc.value) return []
  const h = hc.value
  return [
    ['连接状态', h.connected ? '已连接' : '未连接'],
    ['通信质量', h.comm_quality],
    ['地址', `${h.host || '—'}:${h.port ?? '—'}`],
    ['固件版本', h.fw_version || '—'],
    ['能力集', (h.capabilities || []).join(', ') || '—'],
    ['已解析帧', h.frames_parsed],
    ['丢弃帧', h.frames_dropped],
    ['JSON 错误', h.json_errors],
    ['心跳延迟 (s)', h.heartbeat_age_s ?? '—'],
    ['心跳丢失次数', h.missed_heartbeats],
  ]
})

onMounted(() => {
  loadHostcomm()
  timer = setInterval(loadHostcomm, 3000)
})
onBeforeUnmount(() => clearInterval(timer))
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div class="page-title">设备诊断</div>
      <div class="spacer" />
      <button @click="loadHostcomm">刷新</button>
    </div>

    <div v-if="banner" class="banner err">{{ banner }}</div>

    <div class="grid">
      <!-- HostComm 状态 -->
      <div class="card">
        <div class="card-title">HostComm 连接</div>
        <table class="kv">
          <tbody>
            <tr v-for="row in hcRows" :key="row[0]">
              <th>{{ row[0] }}</th>
              <td class="mono">{{ row[1] }}</td>
            </tr>
            <tr v-if="!hc"><td colspan="2" class="muted">读取中…</td></tr>
          </tbody>
        </table>
      </div>

      <!-- 子设备通信状态 -->
      <div class="card">
        <div class="card-title">子设备通信状态</div>
        <div class="dev-list">
          <div v-for="d in subDevices" :key="d.name" class="dev-row">
            <span class="dot" :class="d.ok ? 'dot-green' : 'dot-red'" />
            <span class="dev-name">{{ d.name }}</span>
            <span class="dev-status muted">{{ d.status }}</span>
            <span v-if="d.alarm" class="dev-alarm">{{ d.alarm }}</span>
          </div>
        </div>
        <p class="muted note">子设备状态取自实时状态快照；DI/DO 位图解析待数据字典冻结后接入。</p>
      </div>
    </div>

    <!-- 只读调试工具（Maintainer）-->
    <div class="card">
      <div class="card-title">HostComm 调试工具（只读）</div>
      <div class="dbg-actions">
        <button :disabled="debugBusy" @click="runDebug('get_status')">读取状态 get_status</button>
        <button :disabled="debugBusy" @click="runDebug('get_parameters')">读取参数 get_parameters</button>
        <span class="muted note">仅允许只读请求；不提供任何控制/强制/绕过操作。</span>
      </div>
      <pre v-if="debugOut" class="dbg-out">{{ debugOut }}</pre>
    </div>
  </div>
</template>

<style scoped>
.page { display: flex; flex-direction: column; gap: 16px; }
.page-head { display: flex; align-items: center; gap: 12px; }
.page-title { font-size: 18px; font-weight: 700; }
.spacer { flex: 1; }
.banner.err { background: var(--red-dim); border: 1px solid var(--red); color: #fca5a5; border-radius: 6px; padding: 8px 12px; font-size: 12px; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; align-items: start; }
.card-title { font-weight: 700; margin-bottom: 10px; }
.kv { width: 100%; border-collapse: collapse; font-size: 12px; }
.kv th, .kv td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--border); }
.kv th { color: var(--text-sec); font-weight: 600; width: 130px; }
.dev-list { display: flex; flex-direction: column; gap: 6px; }
.dev-row { display: flex; align-items: center; gap: 8px; padding: 6px 8px; border-radius: 5px; background: var(--bg-card2); }
.dev-name { font-weight: 600; }
.dev-status { margin-left: auto; font-size: 12px; }
.dev-alarm { color: var(--red); font-size: 11px; font-family: monospace; }
.note { font-size: 11px; margin-top: 8px; }
.dbg-actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.dbg-out { margin-top: 12px; background: var(--bg-base); border: 1px solid var(--border); border-radius: 6px; padding: 10px; font-size: 11px; max-height: 360px; overflow: auto; }
@media (max-width: 1000px) { .grid { grid-template-columns: 1fr; } }
</style>
