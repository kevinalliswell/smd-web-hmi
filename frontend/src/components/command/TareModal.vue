<script setup>
// 天平去皮：非 CO 命令，无需二次确认令牌；仅做一次轻量确认（确认承滴坩埚空载）。
import { ref } from 'vue'
import { sendCommand } from '@/api/commands'
import ConfirmDialog from '@/components/shared/ConfirmDialog.vue'
import OperationResult from '@/components/command/OperationResult.vue'

const emit = defineEmits(['close', 'done'])
const error = ref('')
const unknownOperationId = ref(null)
function onOperationResolved(result) {
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
    const result = await sendCommand('tare_balance', {})
    emit('done', result)
    emit('close')
  } catch (e) {
    unknownOperationId.value = e.outcomeUnknown ? e.operationId : null
    error.value = e.response?.data?.detail?.message || e.response?.data?.message || e.message || '去皮失败'
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <ConfirmDialog
    :model-value="true"
    title="天平去皮"
    confirm-text="确认去皮"
    busy-text="下发中…"
    danger
    :busy="submitting"
      :confirm-disabled="Boolean(unknownOperationId)"
    :close-on-confirm="false"
    @confirm="onConfirm"
    @cancel="emit('close')"
  >
    <p class="muted">请确认承滴坩埚处于空载状态后再去皮。去皮请求将发送至控制板，由其在允许阶段执行。</p>
    <div v-if="error" class="err">{{ error }}</div>
      <OperationResult v-if="unknownOperationId" :operation-id="unknownOperationId" @resolved="onOperationResolved" />
  </ConfirmDialog>
</template>

<style scoped>
.err { color: var(--danger-text); font-size: 12px; }
</style>
