<script setup>
// 停止试验：二次确认（CO 安全警告）→ 取令牌 → 下发 stop_test。
import { ref } from 'vue'
import { requestConfirmToken, sendCommand } from '@/api/commands'
import ConfirmDialog from '@/components/shared/ConfirmDialog.vue'
import OperationResult from '@/components/command/OperationResult.vue'

const emit = defineEmits(['close', 'done'])
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

async function onConfirm() {
  if (unknownOperationId.value || submitting.value) return
  error.value = ''
  submitting.value = true
  try {
    const { confirm_token } = await requestConfirmToken('stop_test')
    const result = await sendCommand('stop_test', {}, confirm_token)
    emit('done', result)
    emit('close')
  } catch (e) {
    unknownOperationId.value = e.outcomeUnknown ? e.operationId : null
    error.value =
      e.response?.data?.detail?.message || e.response?.data?.message || e.message || '停止失败'
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <ConfirmDialog
    :model-value="true"
    title="停止试验"
    confirm-text="确认停止"
    busy-text="下发中…"
    danger
    :busy="submitting"
    :confirm-disabled="Boolean(unknownOperationId)"
    :close-on-confirm="false"
    @confirm="onConfirm"
    @cancel="emit('close')"
  >
    <div class="co-warn">
      受控停止将请求控制板进入安全处置/吹扫流程，不会绕过任何安全处置步骤。 请确认现场具备停止条件。
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
</template>

<style scoped>
.co-warn {
  background: var(--red-dim);
  border: 1px solid var(--red);
  color: var(--danger-text);
  border-radius: 6px;
  padding: 10px;
  font-size: var(--fs-base);
  line-height: 1.6;
}
.err {
  color: var(--danger-text);
  font-size: var(--fs-base);
}
</style>
