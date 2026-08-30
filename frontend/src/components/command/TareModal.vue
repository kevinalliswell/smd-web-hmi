<script setup>
// 天平去皮：非 CO 命令，无需二次确认令牌；仅做一次轻量确认（确认承滴坩埚空载）。
import { ref } from 'vue'
import { sendCommand } from '@/api/commands'
import ConfirmDialog from '@/components/shared/ConfirmDialog.vue'

const emit = defineEmits(['close', 'done'])
const error = ref('')
const submitting = ref(false)

async function onConfirm() {
  error.value = ''
  submitting.value = true
  try {
    const result = await sendCommand('tare_balance', {})
    emit('done', result)
    emit('close')
  } catch (e) {
    error.value = e.response?.data?.message || '去皮失败'
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
    :close-on-confirm="false"
    @confirm="onConfirm"
    @cancel="emit('close')"
  >
    <p class="muted">请确认承滴坩埚处于空载状态后再去皮。去皮请求将发送至控制板，由其在允许阶段执行。</p>
    <div v-if="error" class="err">{{ error }}</div>
  </ConfirmDialog>
</template>

<style scoped>
.err { color: #fca5a5; font-size: 12px; }
</style>
