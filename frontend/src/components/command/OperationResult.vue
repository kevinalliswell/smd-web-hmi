<script setup>
import { computed, ref, watch } from 'vue'
import { fetchOperation, queryDeviceOperation, reconcileOperation, operationResolved, operationReconciled, wireOperationStatus } from '@/api/commands'
import { apiErrorMessage } from '@/api/errors'
import { useDeviceStore } from '@/stores/device'
import { useRole } from '@/composables/useRole'
const props = defineProps({
  operationId: { type: String, required: true },
  requireVerified: { type: Boolean, default: false },
  initialResult: { type: Object, default: null },
})
const emit = defineEmits(['resolved', 'updated'])
const device = useDeviceStore()
const { hasRole } = useRole()
const busy = ref(false), message = ref(''), result = ref(props.initialResult), reason = ref('')
watch(() => props.initialResult, value => { result.value = value })
const wire = computed(() => result.value ? wireOperationStatus(result.value) : null)
const isV2 = computed(() => device.isV2 || Boolean(wire.value))
const reconciled = computed(() => operationReconciled(result.value))
const canReconcile = computed(() => !reconciled.value && hasRole('admin') && ['unknown', 'not_found', 'result_expired'].includes(wire.value || result.value?.operation_status))
const labels = { pending: '等待发送', sent: '已发送，等待设备结果', accepted: '设备已受理', verified: '参数已回读一致', rejected: '设备已拒绝', unknown: '结果仍未知，需完成设备对账' }
const wireLabels = { pending: '持久意图待发送', sent: '已发送，等待板端证据', accepted: '已持久受理，等待执行', applied: '命令已应用（不代表实验完成）', rejected: '板端拒绝', interrupted: '板端执行中断', unknown: '结果仍未知', result_expired: '板端结果已过保留期', not_found: '板端未找到结果（不证明未执行）' }
const backendLabel = computed(() => result.value?.prerequisite_only && result.value.operation_status === 'rejected'
  ? `主命令未发送，${reconciled.value ? '前置操作已核查' : '前置操作失败'}`
  : reconciled.value && ['unknown', 'result_expired', 'not_found'].includes(wire.value || result.value?.operation_status)
    ? '已核查，执行结果仍未知' : labels[result.value?.operation_status] || '结果状态未知')
async function refresh(action = 'local') {
  if (busy.value || (action === 'reconcile' && (!canReconcile.value || !reason.value.trim()))) return
  busy.value = true
  message.value = ''
  try {
    result.value = action === 'board' ? await queryDeviceOperation(props.operationId)
      : action === 'reconcile' ? await reconcileOperation(props.operationId, reason.value.trim())
        : await fetchOperation(props.operationId)
    message.value = backendLabel.value
    emit('updated', result.value)
    if (operationResolved(result.value, props.requireVerified)) emit('resolved', result.value)
  } catch (error) {
    message.value = error.response?.status === 404 ? '后台暂未找到该操作，请保留编号并核查设备状态' : apiErrorMessage(error)
  } finally { busy.value = false }
}
</script>
<template>
  <div class="operation-result">
    <p class="mono">操作编号：{{ operationId }}</p>
    <p v-if="result">后台：{{ backendLabel }}</p>
    <p v-if="wire">{{ result?.prerequisite_only ? '前置控制板操作' : '控制板' }}：{{ wireLabels[wire] || wire }}<span v-if="result?.command_seq || result?.wire_operation?.command_seq"> · 命令序号 {{ result.command_seq || result.wire_operation.command_seq }}</span></p>
    <p v-if="result?.reason_code || result?.wire_operation?.reason">依据：{{ result.reason_code || result.wire_operation.reason }}</p>
    <div class="actions">
      <button :disabled="busy" @click="refresh()">{{ busy ? '查询中…' : '查询执行结果' }}</button>
      <button v-if="isV2" :disabled="busy || device.commQuality !== 'online'" @click="refresh('board')">查询控制板结果</button>
    </div>
    <template v-if="canReconcile && isV2">
      <label>对账依据<textarea v-model="reason" maxlength="1000" rows="2" placeholder="记录现场检查和日志证据，不得据此认定命令已执行" /></label>
      <button :disabled="busy || !reason.trim() || device.commQuality !== 'online'" @click="refresh('reconcile')">记录对账结论</button>
      <p class="muted">对账保留未知事实与审计记录，不重新发送原命令。</p>
    </template>
    <p v-if="message" role="status">{{ message }}</p>
  </div>
</template>
<style scoped>
.operation-result { display: flex; flex-direction: column; gap: 8px; margin-top: 10px; }
p, label { font-size: var(--fs-base); overflow-wrap: anywhere; }
.actions { display: flex; gap: 8px; flex-wrap: wrap; }
label { display: flex; flex-direction: column; gap: 6px; }
textarea { width: 100%; min-height: 64px; resize: vertical; }
</style>
