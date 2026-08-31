<script setup>
import { onMounted, ref } from 'vue'
import { useRole } from '@/composables/useRole'
import { fetchTests } from '@/api/tests'
import { fetchReports, generateReport, reportDownloadUrl } from '@/api/reports'
import { exportLogs, logDownloadUrl } from '@/api/logs'
import { downloadFile } from '@/utils/download'
import { formatDateTime } from '@/utils/dateTime'

const { canOperate } = useRole()

const tests = ref([])
const reports = ref([])
const selectedTest = ref('')
const heightMm = ref('')
const banner = ref(null) // {type, text}
const busy = ref(false)

async function loadAll() {
  try {
    tests.value = (await fetchTests(1, 100)) || []
    if (!selectedTest.value && tests.value.length) selectedTest.value = tests.value[0].test_id
  } catch {
    /* 忽略 */
  }
  try {
    reports.value = (await fetchReports()) || []
  } catch {
    /* 忽略 */
  }
}

async function onGenerate() {
  if (!selectedTest.value) return
  busy.value = true
  banner.value = null
  try {
    const options = {}
    const h = Number(heightMm.value)
    if (heightMm.value !== '' && !Number.isNaN(h)) options.original_height_mm = h
    const r = await generateReport(selectedTest.value, options)
    banner.value = { type: 'ok', text: `报告已生成（#${r.id}，${r.file_size_bytes} 字节）` }
    await loadAll()
  } catch (e) {
    banner.value = { type: 'err', text: '生成失败：' + (e.response?.data?.message || e.message) }
  } finally {
    busy.value = false
  }
}

async function onExportLogs() {
  if (!selectedTest.value) return
  busy.value = true
  banner.value = null
  try {
    const r = await exportLogs(selectedTest.value)
    await downloadFile(logDownloadUrl(r.task_id), `${selectedTest.value}-logs.zip`)
    banner.value = { type: 'ok', text: `日志已导出（${r.entries.join(', ')}）` }
  } catch (e) {
    banner.value = { type: 'err', text: '导出失败：' + (e.response?.data?.message || e.message) }
  } finally {
    busy.value = false
  }
}

async function onDownloadReport(rep) {
  try {
    await downloadFile(reportDownloadUrl(rep.id), `${rep.test_id}-report.html`)
  } catch (e) {
    banner.value = { type: 'err', text: '下载失败：' + (e.response?.data?.message || e.message) }
  }
}

onMounted(loadAll)
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div class="page-title">报告生成 / 日志导出</div>
      <div class="spacer" />
      <button @click="loadAll">刷新</button>
    </div>

    <div v-if="banner" class="banner" :class="banner.type">{{ banner.text }}</div>

    <div class="card gen">
      <div class="card-title">生成</div>
      <div class="form">
        <label>试验</label>
        <select v-model="selectedTest">
          <option v-for="t in tests" :key="t.test_id" :value="t.test_id">
            {{ t.test_id }}（{{ t.operator_id }}{{ t.end_time ? '' : ' · 进行中' }}）
          </option>
          <option v-if="!tests.length" value="">无可用试验</option>
        </select>
        <label>原始料层高度 H (mm)</label>
        <input v-model="heightMm" placeholder="可选，用于 T10/T40/ΔH" style="width: 180px" />
      </div>
      <div v-if="canOperate()" class="actions">
        <button class="primary" :disabled="busy || !selectedTest" @click="onGenerate">生成报告</button>
        <button :disabled="busy || !selectedTest" @click="onExportLogs">导出日志 (zip)</button>
      </div>
      <p v-else class="muted">报告生成与日志导出需要 Operator 及以上角色。</p>
    </div>

    <div class="card">
      <div class="card-title">报告列表</div>
      <table class="rep-table">
        <thead>
          <tr><th>#</th><th>试验</th><th>生成时间</th><th>操作员</th><th>格式</th><th>大小</th><th></th></tr>
        </thead>
        <tbody>
          <tr v-if="!reports.length"><td colspan="7" class="empty muted">暂无报告</td></tr>
          <tr v-for="r in reports" :key="r.id">
            <td>{{ r.id }}</td>
            <td class="mono">{{ r.test_id }}</td>
            <td class="small mono">{{ formatDateTime(r.generated_at) }}</td>
            <td>{{ r.operator_id }}</td>
            <td>{{ r.format }}</td>
            <td class="small">{{ r.file_size_bytes }} B</td>
            <td><button class="dl" @click="onDownloadReport(r)">下载</button></td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.page { display: flex; flex-direction: column; gap: 16px; }
.page-head { display: flex; align-items: center; gap: 12px; }
.page-title { font-size: 18px; font-weight: 700; }
.spacer { flex: 1; }
.banner { border-radius: 6px; padding: 8px 12px; font-size: 12px; }
.banner.ok { background: var(--green-dim); border: 1px solid var(--green); color: #86efac; }
.banner.err { background: var(--red-dim); border: 1px solid var(--red); color: #fca5a5; }
.card-title { font-weight: 700; margin-bottom: 10px; }
.form { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 12px; }
.form label { color: var(--text-sec); font-size: 12px; }
.actions { display: flex; gap: 10px; }
.rep-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.rep-table th, .rep-table td { text-align: left; padding: 7px 8px; border-bottom: 1px solid var(--border); }
.rep-table th { color: var(--text-sec); font-weight: 600; font-size: 11px; }
.small { font-size: 11px; color: var(--text-sec); }
.empty { text-align: center; padding: 18px; }
.dl { padding: 3px 12px; font-size: 12px; }
</style>
