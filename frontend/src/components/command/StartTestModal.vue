<script setup>
// 启动试验：输入试验编号 → 二次确认（含 CO 安全提示）→ 经后端获取 confirm_token 后下发。
import { computed, onMounted, ref } from 'vue'
import { useModalFocus } from '@/composables/useModalFocus'
const { dialog, onDialogKeydown } = useModalFocus()
import { requestConfirmToken, sendCommand } from '@/api/commands'
import { useDeviceStore } from '@/stores/device'
import { fetchNextTestId } from '@/api/tests'
import RecipePicker from '@/components/recipes/RecipePicker.vue'
import ConfirmDialog from '@/components/shared/ConfirmDialog.vue'
import OperationResult from '@/components/command/OperationResult.vue'

const emit = defineEmits(['close', 'done'])
const device = useDeviceStore()
const initialSuggestion = suggestTestId()
const testId = ref(initialSuggestion)
const originalHeightMm = ref('')
const sampleLabel = ref('')
const notes = ref('')
const recipe = ref(null)
const error = ref('')
const unknownOperationId = ref(null)
function onOperationResolved(result) {
  if (result.wire_reconciled && !['accepted', 'verified', 'rejected'].includes(result.operation_status)) {
    error.value = '已核查，执行结果仍未知；新的操作须根据当前设备状态重新确认'
    unknownOperationId.value = null
    return
  }
  if (result.operation_status === 'rejected') {
    error.value = result.reason_code || '设备已拒绝，请核查后重新确认'
    unknownOperationId.value = null
    return
  }
  emit('done', result)
  emit('close')
}
const submitting = ref(false)
const confirming = ref(false)
const canContinue = computed(() => {
  const height = Number(originalHeightMm.value)
  return testId.value.trim() && Number.isFinite(height) && height > 0 && height <= 10000 && (!device.isV2 || (device.canStartTest && Boolean(recipe.value)))
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
  if (unknownOperationId.value || submitting.value || !canContinue.value) return
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
        ...(recipe.value
          ? { recipe_id: recipe.value.recipe_id, recipe_version: recipe.value.version }
          : {}),
      },
      confirm_token,
    )
    emit('done', result)
    confirming.value = false
    emit('close')
  } catch (e) {
    unknownOperationId.value = e.outcomeUnknown ? e.operationId : null
    error.value =
      e.response?.data?.detail?.message || e.response?.data?.message || e.message || '启动失败'
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div
    class="overlay"
    @click.self="emit('close')"
    @keydown.esc="emit('close')"
  >
    <div
      ref="dialog"
      tabindex="-1"
      @keydown="onDialogKeydown"
      class="dialog"
      role="dialog"
      aria-modal="true"
      aria-labelledby="start-test-title"
    >
      <div
        id="start-test-title"
        class="dlg-title"
      >
        启动试验
      </div>
      <label for="start-test-id">试验编号</label>
      <input
        id="start-test-id"
        v-model="testId"
        maxlength="64"
      />
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
      <input
        id="start-test-label"
        v-model="sampleLabel"
        maxlength="128"
      />
      <details :open="device.isV2">
        <summary>选择已下发配方</summary>
        <RecipePicker @select="recipe = $event" />
        <p class="muted">支持配方的设备必须选择已回读一致的版本；旧设备留空使用固定流程。</p>
      </details>
      <p v-if="recipe">
        {{ recipe.definition.name }} · v{{ recipe.version }} ·
        {{ recipe.definition.mode === 'standard' ? '标准候选模板' : '非标' }}
      </p>
      <label for="start-test-notes">备注</label>
      <textarea
        id="start-test-notes"
        v-model="notes"
        maxlength="1000"
        rows="3"
      />
      <div
        v-if="error && !confirming"
        class="err"
      >
        {{ error }}
      </div>

      <div class="actions">
        <button @click="emit('close')">取消</button>
        <button
          class="primary"
          :disabled="!canContinue"
          @click="onStart"
        >
          下一步
        </button>
      </div>
    </div>

    <ConfirmDialog
      v-model="confirming"
      title="启动试验"
      confirm-text="确认启动"
      busy-text="下发中…"
      danger
      :busy="submitting"
      :confirm-disabled="Boolean(unknownOperationId) || !canContinue"
      :close-on-confirm="false"
      @confirm="onConfirm"
    >
      <p v-if="recipe">
        {{ recipe.definition.name }} · v{{ recipe.version }}。后台将核对设备执行版本。
      </p>
      <div class="co-warn">
        本试验涉及 CO 工艺阶段。请确认现场排风、CO 监测与安全继电器均正常。
        启动请求将发送至控制板，最终由 STM32 状态机与硬接线联锁裁决。
      </div>
      <div
        v-if="error"
        class="err"
      >
        {{ error }}
      </div>
      <OperationResult
        v-if="unknownOperationId"
        :operation-id="unknownOperationId"
        @resolved="onOperationResolved"
      />
    </ConfirmDialog>
  </div>
</template>

<style scoped>
.overlay {
  position: fixed;
  inset: 0;
  background: var(--overlay);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: var(--z-modal);
}
.dialog {
  background: var(--bg-card);
  border: 1px solid var(--border-hi);
  border-radius: var(--radius-dialog);
  width: min(520px, calc(100vw - 24px));
  max-height: calc(100dvh - 24px);
  overflow-y: auto;
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.dlg-title {
  font-size: 16px;
  font-weight: 700;
}
label {
  font-size: 12px;
  color: var(--text-sec);
}
.co-warn {
  background: var(--yellow-dim);
  border: 1px solid var(--yellow);
  color: var(--warning-text);
  border-radius: var(--radius-md);
  padding: 10px;
  font-size: 12px;
  line-height: 1.6;
}
.err {
  color: var(--danger-text);
  font-size: 12px;
}
.actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 6px;
}
textarea {
  resize: vertical;
  min-height: 58px;
}
</style>
