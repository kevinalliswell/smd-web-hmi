<script setup>
// 停止试验：二次确认（CO 安全警告）→ 取令牌 → 下发 stop_test。
import { ref } from 'vue'
import { requestConfirmToken, sendCommand } from '@/api/commands'
import ConfirmDialog from '@/components/shared/ConfirmDialog.vue'

const emit = defineEmits(['close', 'done'])
const error = ref('')
const submitting = ref(false)

async function onConfirm() {
  error.value = ''
  submitting.value = true
  try {
    const { confirm_token } = await requestConfirmToken('stop_test')
    const result = await sendCommand('stop_test', {}, confirm_token)
    emit('done', result)
    emit('close')
  } catch (e) {
    error.value = e.response?.data?.message || '停止失败'
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
    :close-on-confirm="false"
    @confirm="onConfirm"
    @cancel="emit('close')"
  >
    <div class="co-warn">
      受控停止将请求控制板进入安全处置/吹扫流程，不会绕过任何安全处置步骤。
      请确认现场具备停止条件。
    </div>
    <div v-if="error" class="err">{{ error }}</div>
  </ConfirmDialog>
</template>

<style scoped>
.co-warn { background: var(--red-dim); border: 1px solid var(--red); color: #fca5a5; border-radius: 6px; padding: 10px; font-size: 12px; line-height: 1.6; }
.err { color: #fca5a5; font-size: 12px; }
</style>
