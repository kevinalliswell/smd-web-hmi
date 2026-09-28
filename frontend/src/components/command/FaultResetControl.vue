<script setup>
import { computed, ref } from 'vue'
import { useDeviceStore } from '@/stores/device'
import { useRole } from '@/composables/useRole'
import { sendCommand } from '@/api/commands'
import { fetchStatus } from '@/api/status'
import { apiErrorMessage } from '@/api/errors'
import ConfirmDialog from '@/components/shared/ConfirmDialog.vue'
import OperationResult from '@/components/command/OperationResult.vue'
const device = useDeviceStore()
const { canOperate } = useRole()
const opened = ref(false), busy = ref(false), reason = ref(''), message = ref(''), operationId = ref(null)
const allowed = computed(() => canOperate() && device.canResetFault && !busy.value && !operationId.value)
function resolved(result) {
  operationId.value = null
  opened.value = false
  message.value = result.wire_reconciled && result.operation_status === 'unknown' ? '已核查，故障复位执行结果仍未知；请核对当前设备状态。' : result.operation_status === 'rejected' ? `复位被拒绝：${result.reason_code || '请核查现场和报警证据'}` : '故障复位已应用；原实验不会继续，实验结束仍须单独确认。'
  fetchStatus().then(device.updateSnapshot).catch(() => {})
}
async function reset() {
  if (!allowed.value || !reason.value.trim()) return
  busy.value = true; message.value = ''
  try { resolved(await sendCommand('reset_fault', { reason: reason.value.trim() })) }
  catch (error) {
    operationId.value = error.outcomeUnknown ? error.operationId : null
    if (operationId.value) opened.value = false
    message.value = apiErrorMessage(error)
  } finally { busy.value = false }
}
</script>
<template>
  <div class="fault-reset">
    <button :disabled="!allowed" @click="opened = true">复位已解除的故障</button>
    <p v-if="message" role="status">{{ message }}</p>
    <OperationResult v-if="operationId" :operation-id="operationId" @resolved="resolved" />
    <ConfirmDialog v-model="opened" title="复位已解除的故障" confirm-text="确认复位" :busy="busy" :confirm-disabled="!allowed || !reason.trim()" :close-on-confirm="false" @confirm="reset">
      <p>请核对故障原因已排除、报警已处理，并记录现场恢复依据。故障态复位仅允许继续安全处置；复位不会恢复原实验、加热或 CO，也不会代替冷却完成和实验结束确认。</p>
      <label>故障复位依据<textarea v-model="reason" aria-label="故障复位依据" maxlength="512" rows="3" required /></label>
      <p v-if="message" role="status">{{ message }}</p>
    </ConfirmDialog>
  </div>
</template>
<style scoped>
.fault-reset, label { display: flex; flex-direction: column; gap: 8px; }
p, label { font-size: var(--fs-base); line-height: 1.6; }
textarea { width: 100%; resize: vertical; }
</style>
