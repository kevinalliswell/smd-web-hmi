<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRole } from '@/composables/useRole'
import { fetchRunRecoveries, fetchRunRecovery, bindRunRecovery, replayRunRecovery } from '@/api/runRecoveries'
import { apiErrorMessage } from '@/api/errors'
import { formatDateTime } from '@/utils/dateTime'

const { hasRole } = useRole()
const rows = ref([]), selected = ref(null), reason = ref(''), target = ref(''), page = ref(0)
const busy = ref(false), error = ref('')
const mayReview = computed(() => hasRole('admin'))
const pending = computed(() => ['pending', 'running'].includes(selected.value?.replay_status))
const reviewable = computed(() => mayReview.value && !busy.value && reason.value.trim().length >= 2 && selected.value?.review_state !== 'conflict')
const states = { unreviewed: '待核查归属', bound: '已核查绑定', conflict: '原始证据冲突' }
const replays = { not_bound: '尚未绑定', pending: '等待回放', running: '正在回放', complete: '现有原始记录回放完成', failed: '回放失败，可核查后继续' }
const attempts = new Map()
let timer = null, disposed = false, detailGeneration = 0, listGeneration = 0, selectedId = null
async function load() {
  busy.value = true; error.value = ''
  const generation = ++listGeneration
  try { const result = await fetchRunRecoveries(50, page.value * 50); if (!disposed && generation === listGeneration) rows.value = result }
  catch (e) { if (!disposed && generation === listGeneration) error.value = apiErrorMessage(e) }
  finally { if (!disposed && generation === listGeneration) busy.value = false }
}
async function open(id) {
  clearTimeout(timer)
  const generation = ++detailGeneration
  if (selectedId !== id) { reason.value = ''; target.value = ''; selected.value = null; selectedId = id }
  try {
    const result = await fetchRunRecovery(id)
    if (disposed || generation !== detailGeneration) return
    selected.value = result
    rows.value = rows.value.map(row => row.id === id ? { ...row, ...result } : row)
    if (pending.value) timer = setTimeout(() => open(id), 1500)
  } catch (e) { if (!disposed && generation === detailGeneration) error.value = apiErrorMessage(e) }
}
async function act(action) {
  if (!reviewable.value || pending.value) return
  busy.value = true; error.value = ''
  const row = selected.value
  const body = { reason: reason.value.trim(), expected_review_revision: row.review_revision }
  if (action === 'binding') body.target_test_id = target.value.trim() || null
  const signature = JSON.stringify([row.id, action, body])
  if (!attempts.has(signature)) attempts.set(signature, crypto.randomUUID())
  body.idempotency_key = attempts.get(signature)
  try {
    await (action === 'binding' ? bindRunRecovery : replayRunRecovery)(row.id, body)
    await open(row.id)
    await load()
  } catch (e) { error.value = apiErrorMessage(e, '请求结果未确认，请刷新核查后重试；相同内容复用请求标识') }
  finally { busy.value = false }
}
async function changePage(delta) { page.value += delta; await load() }
onMounted(load)
onBeforeUnmount(() => { disposed = true; clearTimeout(timer) })
</script>

<template>
  <div class="page">
    <div class="page-head"><h1>运行恢复</h1><button :disabled="busy" @click="load">刷新列表</button></div>
    <p class="muted">这些控制板运行尚未归到本机实验。先核查设备和运行标识，再绑定并回放已保存的原始记录。绑定不会恢复加热或气体输出；当前运行的安全停止仍在当前试验页。</p>
    <RouterLink to="/test">查看当前试验与安全停止</RouterLink>
    <p v-if="error" role="alert">{{ error }}</p>
    <p v-if="!rows.length && !busy" class="card muted">暂无待恢复的陌生运行。</p>
    <div class="records">
      <button v-for="row in rows" :key="row.id" class="card record" :disabled="busy" :aria-pressed="selected?.id === row.id" @click="open(row.id)">
        <strong>{{ states[row.review_state] || row.review_state }}</strong>
        <span class="mono">设备 {{ row.device_id }} · 运行 {{ row.run_id }}</span>
        <span>发现于 {{ formatDateTime(row.first_seen_at) }} · {{ replays[row.replay_status] }}</span>
      </button>
    </div>
    <nav class="actions" aria-label="运行恢复分页"><button :disabled="busy || page === 0" @click="changePage(-1)">上一页</button><span>第 {{ page + 1 }} 页</span><button :disabled="busy || rows.length < 50" @click="changePage(1)">下一页</button></nav>
    <section v-if="selected" class="card detail" aria-labelledby="recovery-detail">
      <div class="page-head"><h2 id="recovery-detail">核查原始运行</h2><button :disabled="busy" @click="open(selected.id)">刷新证据</button></div>
      <dl><dt>设备标识</dt><dd class="mono">{{ selected.device_id }}</dd><dt>运行标识</dt><dd class="mono">{{ selected.run_id }}</dd><dt>本机实验</dt><dd>{{ selected.test_id || '尚未绑定' }}</dd><dt>当前证据版本</dt><dd>{{ selected.review_revision }}</dd><dt>原始记录数</dt><dd>{{ selected.source_count }}</dd><dt>回放进度</dt><dd role="status">{{ replays[selected.replay_status] }}（本地记录位置 {{ selected.replay_through_id }}）</dd></dl>
      <p>发现时间不是实验开始时间。缺少原始开始样本、样品信息、配方或工程配置时保持未知；可在历史试验中有审计地补录样品条件。回放完成也不代表数据完整或符合国标。</p>
      <p>真实开始时间：{{ selected.start_time ? formatDateTime(selected.start_time) : '未知' }}；发现时间：{{ formatDateTime(selected.discovered_at || selected.first_seen_at) }}。</p>
      <p v-if="selected.log_gaps?.length" role="alert">关联源日志包含 {{ selected.log_gaps.length }} 个已记录缺口，报告仍须按测定边界判断影响。</p>
      <p v-if="selected.review_state === 'conflict'" role="alert">原始边界或配置摘要冲突，已禁止绑定、结束确认和最终报告。请保留原始记录交维护者核查。</p>
      <details><summary>查看来源、边界和缺口证据</summary><pre>{{ JSON.stringify({ evidence: selected.evidence, log_gaps: selected.log_gaps }, null, 2) }}</pre></details>
      <template v-if="mayReview">
        <label v-if="!selected.test_id">已有实验编号（可选）<input v-model="target" maxlength="64" :disabled="busy" placeholder="留空创建恢复记录；已有记录必须具备原始启动请求证据" /></label>
        <label>核查原因<textarea v-model="reason" :disabled="busy || pending" rows="3" maxlength="2000" placeholder="记录核对的设备、运行及归属依据" /></label>
        <div class="actions"><button v-if="!selected.test_id" :disabled="!reviewable" @click="act('binding')">确认归属并回放</button><button v-else :disabled="!reviewable || pending" @click="act('replay')">继续回放原始记录</button><RouterLink v-if="selected.test_id" to="/history">查看历史试验与补录条件</RouterLink></div>
      </template>
      <p v-else class="muted">管理员或维护员可核查绑定及恢复回放。</p>
      <h3>核查审计</h3><p class="muted">显示最近最多 100 条核查及关联日志缺口；全部原始证据仍保存在数据库中。</p><p v-if="!selected.reviews?.length" class="muted">尚无人工核查记录。</p>
      <p v-for="(review, index) in selected.reviews" :key="index">{{ formatDateTime(review.created_at) }} · {{ review.actor }} · {{ review.reason }}</p>
    </section>
  </div>
</template>

<style scoped>
.page,.detail,.records { display:flex; flex-direction:column; gap:16px; }
.page-head,.actions { display:flex; align-items:center; gap:12px; flex-wrap:wrap; }
.page-head { justify-content:space-between; }.record { display:flex; flex-direction:column; align-items:flex-start; gap:8px; text-align:left; }
.record[aria-pressed="true"] { outline:2px solid var(--accent); }
h1 { font-size:20px; } h2 { font-size:17px; } h3 { font-size:15px; }
p { line-height:1.7; } label { display:flex; flex-direction:column; gap:8px; }
dl { display:grid; grid-template-columns:150px minmax(0,1fr); gap:10px; } dd { margin:0; overflow-wrap:anywhere; }
pre { white-space:pre-wrap; overflow-wrap:anywhere; font-size:12px; }.mono { overflow-wrap:anywhere; }
@media(max-width:560px) { dl { grid-template-columns:1fr; } }
</style>
