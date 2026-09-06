<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useDeviceStore } from '@/stores/device'
import { useRole } from '@/composables/useRole'
import { requestSourceLogRecovery, fetchSourceLogRecovery, validSourceRecordSequence } from '@/api/maintenance'
import { fetchOperations } from '@/api/commands'
import { apiErrorMessage } from '@/api/errors'
import { formatDateTime } from '@/utils/dateTime'
import OperationResult from '@/components/command/OperationResult.vue'
const rows = ref([]), page = ref(1), busy = ref(false), error = ref('')
async function load(target = page.value) {
  if (busy.value) return
  busy.value = true; error.value = ''
  try { rows.value = await fetchOperations(target, 20); page.value = target }
  catch (e) { error.value = apiErrorMessage(e) }
  finally { busy.value = false }
}
const device = useDeviceStore()
const { hasRole } = useRole()
const firstRecord = ref(''), logJob = ref(null), logBusy = ref(false), logError = ref('')
const logAllowed = computed(() => device.isV2 && hasRole('admin'))
const sequenceValid = computed(() => !firstRecord.value.trim() || validSourceRecordSequence(firstRecord.value.trim()))
const logPending = computed(() => ['pending', 'running'].includes(logJob.value?.status))
const jobLabels = { pending: '等待后台执行', running: '正在补传源日志', completed: '源日志扫描完成；请在报告中核查完整性，已有缺口仍可能存在。', failed: '源日志补传失败' }
let logTimer = null, disposed = false
async function refreshLogs() {
  if (!logJob.value || logBusy.value || disposed) return
  clearTimeout(logTimer)
  logBusy.value = true; logError.value = ''
  try {
    const result = await fetchSourceLogRecovery(logJob.value.task_id)
    if (!disposed) logJob.value = result
  } catch (e) { if (!disposed) logError.value = apiErrorMessage(e, '无法取得日志任务状态，请刷新查询') }
  finally {
    logBusy.value = false
    if (!disposed && logPending.value && !logError.value) logTimer = setTimeout(refreshLogs, 1500)
  }
}
async function recoverLogs() {
  if (!logAllowed.value || logBusy.value || logPending.value || !sequenceValid.value || device.commQuality !== 'online') return
  logBusy.value = true; logError.value = ''
  try { logJob.value = await requestSourceLogRecovery(firstRecord.value.trim() || undefined) }
  catch (e) { logError.value = apiErrorMessage(e, '日志补传请求未完成；请核查后台任务状态') }
  finally { logBusy.value = false }
  if (!logError.value && !disposed) await refreshLogs()
}
onBeforeUnmount(() => { disposed = true; clearTimeout(logTimer) })
onMounted(() => load())
</script>
<template>
  <div class="page">
    <div class="page-head"><h1>操作记录</h1><button :disabled="busy" @click="load()">刷新记录</button></div>
    <p class="muted">每次操作保留固定身份。结果未知时先查询控制板和日志；重复点击不会重新发送原命令。管理员可查询所有用户记录。</p>
    <section v-if="logAllowed" class="card source-logs" aria-labelledby="source-log-title">
      <h2 id="source-log-title">设备源日志补传</h2>
      <p class="muted">默认从上次扫描位置增量补传。需要重查旧缺口时明确填写起始序号，填写 1 会重新扫描保留的全部历史；缺口不会被跳过后判为完整。</p>
      <label>起始源记录序号（可选）<input v-model="firstRecord" aria-label="起始源记录序号" inputmode="numeric" maxlength="20" placeholder="留空：增量补传" :disabled="logBusy || logPending" /></label>
      <p v-if="!sequenceValid" role="alert">请输入 1 至 18446744073709551615 的整数序号。</p>
      <div class="actions">
        <button :disabled="logBusy || logPending || !sequenceValid || device.commQuality !== 'online'" @click="recoverLogs">补传设备日志</button>
        <button v-if="logJob" :disabled="logBusy" @click="refreshLogs">刷新补传任务</button>
      </div>
      <template v-if="logJob">
        <p class="mono">任务编号：{{ logJob.task_id }}</p>
        <p role="status">{{ jobLabels[logJob.status] || '任务状态未知' }}<span v-if="logJob.message">：{{ logJob.message }}</span></p>
        <progress v-if="logPending" :value="logJob.progress ?? 0" max="100" aria-label="日志任务进度" />
      </template>
      <p v-if="logError" role="alert">{{ logError }}</p>
    </section>
    <p v-if="error" role="alert">{{ error }}</p>
    <p v-if="busy" role="status">读取中…</p>
    <p v-else-if="!rows.length" class="card muted">本页暂无操作记录。</p>
    <article v-for="row in rows" :key="row.operation_id" class="card">
      <h2>{{ row.command || '设备操作' }} <span class="muted">{{ formatDateTime(row.created_at) }}</span></h2>
      <OperationResult :operation-id="row.operation_id" :initial-result="row" :require-verified="['set_parameters', 'activate_recipe'].includes(row.command)" @updated="Object.assign(row, $event)" />
    </article>
    <nav class="actions" aria-label="操作记录分页">
      <button :disabled="busy || page === 1" @click="load(page - 1)">上一页</button><span>第 {{ page }} 页</span><button :disabled="busy || rows.length < 20" @click="load(page + 1)">下一页</button>
    </nav>
  </div>
</template>
<style scoped>
.page { display: flex; flex-direction: column; gap: 16px; }
.page-head, .actions { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.source-logs, label { display: flex; flex-direction: column; gap: 10px; }
.source-logs input { width: min(100%, 320px); }
.source-logs label { font-size: 12px; }
h1 { font-size: 18px; } h2 { font-size: 14px; } p { line-height: 1.6; font-size: 12px; }
h2 span { margin-left: 12px; font-size: 12px; font-weight: 400; }
</style>
