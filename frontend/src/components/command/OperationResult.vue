<script setup>
import { ref } from 'vue'
import { fetchOperation } from '@/api/commands'
const props = defineProps({ operationId: { type: String, required: true }, requireVerified: { type: Boolean, default: false } })
const emit = defineEmits(['resolved'])
const busy = ref(false)
const message = ref('')
async function refresh() {
  busy.value = true
  try {
    const result = await fetchOperation(props.operationId)
    const labels = { pending: '等待发送', sent: '已发送，等待设备结果', accepted: '设备已受理', verified: '参数已回读一致', rejected: '设备已拒绝', unknown: '结果仍未知，需完成设备对账' }
    message.value = labels[result.operation_status] || '结果状态未知'
    if (['verified', 'rejected'].includes(result.operation_status) || (result.operation_status === 'accepted' && !props.requireVerified)) emit('resolved', result)
  } catch (error) {
    message.value = error.response?.status === 404 ? '后台暂未找到该操作，请保留编号并核查设备状态' : '暂时无法查询，请稍后重试'
  } finally { busy.value = false }
}
</script>
<template>
  <div class="operation-result">
    <p class="mono">操作编号：{{ operationId }}</p>
    <button :disabled="busy" @click="refresh">{{ busy ? '查询中…' : '查询执行结果' }}</button>
    <p v-if="message" role="status">{{ message }}</p>
  </div>
</template>
<style scoped>
.operation-result { display: flex; flex-direction: column; gap: 8px; margin-top: 10px; }
p { font-size: 12px; overflow-wrap: anywhere; }
</style>
