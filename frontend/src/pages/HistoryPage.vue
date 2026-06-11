<script setup>
import { onMounted, ref } from 'vue'
import { useRole } from '@/composables/useRole'
import {
  fetchTests,
  fetchTestDetail,
  fetchTestSamples,
  fetchTestEvents,
  fetchTestAlarms,
} from '@/api/tests'
import { generateReport, reportDownloadUrl } from '@/api/reports'
import { exportLogs, logDownloadUrl } from '@/api/logs'
import { downloadFile } from '@/utils/download'
import HistoryChart from '@/components/charts/HistoryChart.vue'
import AlarmTable from '@/components/alarms/AlarmTable.vue'

const { canOperate } = useRole()

const tests = ref([])
const page = ref(1)
const selected = ref(null) // 详情
const samples = ref([])
const events = ref([])
const alarms = ref([])
const loading = ref(false)
const banner = ref(null)

async function loadTests() {
  tests.value = (await fetchTests(page.value, 20)) || []
}

async function openTest(t) {
  loading.value = true
  banner.value = null
  try {
    selected.value = await fetchTestDetail(t.test_id)
    const s = await fetchTestSamples(t.test_id, 1000)
    samples.value = s?.points || []
    events.value = (await fetchTestEvents(t.test_id)) || []
    alarms.value = (await fetchTestAlarms(t.test_id)) || []
  } catch (e) {
    banner.value = { type: 'err', text: '加载失败：' + (e.response?.data?.message || e.message) }
  } finally {
    loading.value = false
  }
}

async function onGenerateReport() {
  if (!selected.value) return
  try {
    const r = await generateReport(selected.value.test_id)
    await downloadFile(reportDownloadUrl(r.id), `${selected.value.test_id}-report.html`)
    banner.value = { type: 'ok', text: `报告已生成并下载（#${r.id}）` }
  } catch (e) {
    banner.value = { type: 'err', text: '生成失败：' + (e.response?.data?.message || e.message) }
  }
}

async function onExportLogs() {
  if (!selected.value) return
  try {
    const r = await exportLogs(selected.value.test_id)
    await downloadFile(logDownloadUrl(r.task_id), `${selected.value.test_id}-logs.zip`)
    banner.value = { type: 'ok', text: '日志已导出' }
  } catch (e) {
    banner.value = { type: 'err', text: '导出失败：' + (e.response?.data?.message || e.message) }
  }
}

function prevPage() {
  if (page.value > 1) {
    page.value--
    loadTests()
  }
}
function nextPage() {
  if (tests.value.length === 20) {
    page.value++
    loadTests()
  }
}

onMounted(loadTests)
</script>

<template>
  <div class="page">
    <div class="page-title">历史试验</div>
    <div v-if="banner" class="banner" :class="banner.type">{{ banner.text }}</div>

    <div class="layout">
      <!-- 试验列表 -->
      <div class="card list">
        <div class="card-title">试验列表</div>
        <table class="t-table">
          <thead>
            <tr><th>试验编号</th><th>操作员</th><th>开始</th><th>状态</th></tr>
          </thead>
          <tbody>
            <tr v-if="!tests.length"><td colspan="4" class="empty muted">暂无试验</td></tr>
            <tr
              v-for="t in tests"
              :key="t.test_id"
              :class="{ sel: selected && selected.test_id === t.test_id }"
              @click="openTest(t)"
            >
              <td class="mono">{{ t.test_id }}</td>
              <td>{{ t.operator_id }}</td>
              <td class="small mono">{{ (t.start_time || '').slice(0, 19) }}</td>
              <td>
                <span v-if="!t.end_time" class="running">进行中</span>
                <span v-else class="muted">{{ t.end_reason || '已结束' }}</span>
              </td>
            </tr>
          </tbody>
        </table>
        <div class="pager">
          <button :disabled="page === 1" @click="prevPage">上一页</button>
          <span class="muted">第 {{ page }} 页</span>
          <button :disabled="tests.length < 20" @click="nextPage">下一页</button>
        </div>
      </div>

      <!-- 详情 -->
      <div class="detail">
        <div v-if="!selected" class="card muted">从左侧选择一个试验查看详情与曲线回放。</div>
        <template v-else>
          <div class="card">
            <div class="card-head">
              <div class="card-title">{{ selected.test_id }}</div>
              <div class="spacer" />
              <template v-if="canOperate()">
                <button @click="onExportLogs">导出日志</button>
                <button class="primary" @click="onGenerateReport">生成报告</button>
              </template>
            </div>
            <div class="meta">
              <div><span class="k">操作员</span>{{ selected.operator_id }}</div>
              <div><span class="k">开始</span>{{ selected.start_time }}</div>
              <div><span class="k">结束</span>{{ selected.end_time || '进行中' }}</div>
              <div><span class="k">结束原因</span>{{ selected.end_reason || '—' }}</div>
              <div><span class="k">采样点</span>{{ selected.sample_count }}</div>
              <div><span class="k">报警数</span>{{ selected.alarm_count }}</div>
            </div>
          </div>

          <div class="card">
            <div class="card-title">曲线回放</div>
            <HistoryChart :points="samples" />
          </div>

          <div class="card">
            <div class="card-title">报警记录</div>
            <AlarmTable :alarms="alarms" show-clear />
          </div>

          <div class="card">
            <div class="card-title">事件日志</div>
            <table class="t-table">
              <thead><tr><th>时间</th><th>来源</th><th>事件码</th><th>级别</th><th>说明</th></tr></thead>
              <tbody>
                <tr v-if="!events.length"><td colspan="5" class="empty muted">无事件</td></tr>
                <tr v-for="(e, i) in events" :key="i">
                  <td class="small mono">{{ e.ts }}</td>
                  <td>{{ e.source }}</td>
                  <td class="mono">{{ e.event_code }}</td>
                  <td>L{{ e.level }}</td>
                  <td>{{ e.text }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<style scoped>
.page { display: flex; flex-direction: column; gap: 16px; }
.page-title { font-size: 18px; font-weight: 700; }
.banner { border-radius: 6px; padding: 8px 12px; font-size: 12px; }
.banner.ok { background: var(--green-dim); border: 1px solid var(--green); color: #86efac; }
.banner.err { background: var(--red-dim); border: 1px solid var(--red); color: #fca5a5; }
.layout { display: grid; grid-template-columns: 360px 1fr; gap: 16px; align-items: start; }
.detail { display: flex; flex-direction: column; gap: 16px; }
.card-title { font-weight: 700; margin-bottom: 10px; }
.card-head { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.card-head .card-title { margin-bottom: 0; }
.spacer { flex: 1; }
.t-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.t-table th, .t-table td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--border); }
.t-table th { color: var(--text-sec); font-weight: 600; font-size: 11px; }
.t-table tbody tr { cursor: pointer; }
.t-table tbody tr:hover { background: var(--bg-hover); }
.t-table tr.sel { background: var(--accent-dim); }
.small { font-size: 11px; color: var(--text-sec); }
.running { color: var(--green); }
.empty { text-align: center; padding: 16px; }
.pager { display: flex; align-items: center; gap: 10px; margin-top: 10px; justify-content: center; }
.meta { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; font-size: 13px; }
.meta .k { display: inline-block; min-width: 64px; color: var(--text-sec); font-size: 12px; }
@media (max-width: 1100px) { .layout { grid-template-columns: 1fr; } }
</style>
