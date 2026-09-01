<script setup>
// 启动试验：输入试验编号 → 二次确认（含 CO 安全提示）→ 经后端获取 confirm_token 后下发。
import { computed, onMounted, ref } from 'vue'
import { requestConfirmToken, sendCommand } from '@/api/commands'
import { fetchNextTestId } from '@/api/tests'
import ConfirmDialog from '@/components/shared/ConfirmDialog.vue'

const emit = defineEmits(['close', 'done'])
const initialSuggestion = suggestTestId()
const testId = ref(initialSuggestion)
const originalHeightMm = ref('')
const sampleLabel = ref('')
const notes = ref('')
const error = ref('')
const submitting = ref(false)
const confirming = ref(false)
const canContinue = computed(() => {
  const height = Number(originalHeightMm.value)
  return testId.value.trim() && Number.isFinite(height) && height > 0 && height <= 10000
})

function suggestTestId() {
  const d = new Date()
  const ymd = `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, '0')}${String(d.getDate()).padStart(2, '0')}`
  return `TEST-${ymd}-001`
}

onMounted(async () => {
  try {
    const suggested = await fetchNextTestId()
    if (suggested && testId.value === initialSuggestion) testId.value = suggested
  } catch {
    // 后端不可用时保留本地兜底编号，提交时仍会执行唯一性校验。
  }
})

function onStart() {
  // 第一步：弹出二次确认
  error.value = ''
  if (!canContinue.value) {
    error.value = '请填写有效的试验编号和原始料层高度 H'
    return
  }
  confirming.value = true
}

async function onConfirm() {
  error.value = ''
  submitting.value = true
  try {
    // CO 相关命令：先取一次性确认令牌，再携带令牌下发
    const { confirm_token } = await requestConfirmToken('start_test')
    const result = await sendCommand(
      'start_test',
      {
        test_id: testId.value,
        original_height_mm: Number(originalHeightMm.value),
        sample_label: sampleLabel.value.trim() || undefined,
        notes: notes.value.trim() || undefined,
      },
      confirm_token,
    )
    emit('done', result)
    confirming.value = false
    emit('close')
  } catch (e) {
    error.value = e.response?.data?.message || '启动失败'
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="overlay" @click.self="emit('close')" @keydown.esc="emit('close')">
    <div class="dialog" role="dialog" aria-modal="true" aria-labelledby="start-test-title">
      <div id="start-test-title" class="dlg-title">启动试验</div>
      <label for="start-test-id">试验编号</label>
      <input id="start-test-id" v-model="testId" maxlength="64" />
      <label for="start-test-height">原始料层高度 H (mm) <b aria-hidden="true">*</b></label>
      <input
        id="start-test-height"
        v-model="originalHeightMm"
        type="number"
        min="0.01"
        max="10000"
        step="0.01"
        required
      />
      <label for="start-test-label">样品标识</label>
      <input id="start-test-label" v-model="sampleLabel" maxlength="128" />
      <label for="start-test-notes">备注</label>
      <textarea id="start-test-notes" v-model="notes" maxlength="1000" rows="3" />
      <div v-if="error && !confirming" class="err">{{ error }}</div>

      <div class="actions">
        <button @click="emit('close')">取消</button>
        <button class="primary" :disabled="!canContinue" @click="onStart">下一步</button>
      </div>
    </div>

    <ConfirmDialog
      v-model="confirming"
      title="启动试验"
      confirm-text="确认启动"
      busy-text="下发中…"
      danger
      :busy="submitting"
      :close-on-confirm="false"
      @confirm="onConfirm"
    >
      <div class="co-warn">
        本试验涉及 CO 工艺阶段。请确认现场排风、CO 监测与安全继电器均正常。
        启动请求将发送至控制板，最终由 STM32 状态机与硬接线联锁裁决。
      </div>
      <div v-if="error" class="err">{{ error }}</div>
    </ConfirmDialog>
  </div>
</template>

<style scoped>
.overlay { position: fixed; inset: 0; background: rgba(0,0,0,.6); display: flex; align-items: center; justify-content: center; z-index: 100; }
.dialog { background: var(--bg-card); border: 1px solid var(--border-hi); border-radius: 10px; width: 420px; padding: 20px; display: flex; flex-direction: column; gap: 10px; }
.dlg-title { font-size: 16px; font-weight: 700; }
label { font-size: 12px; color: var(--text-sec); }
.co-warn { background: var(--yellow-dim); border: 1px solid var(--yellow); color: var(--warning-text); border-radius: 6px; padding: 10px; font-size: 12px; line-height: 1.6; }
.err { color: var(--danger-text); font-size: 12px; }
.actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 6px; }
textarea { resize: vertical; min-height: 58px; }
</style>
