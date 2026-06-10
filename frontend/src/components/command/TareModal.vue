<script setup>
// 天平去皮：非 CO 命令，无需二次确认令牌；仅做一次轻量确认（确认承滴坩埚空载）。
import { ref } from 'vue'
import { sendCommand } from '@/api/commands'

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
  <div class="overlay" @click.self="emit('close')">
    <div class="dialog">
      <div class="dlg-title">天平去皮</div>
      <p class="muted">请确认承滴坩埚处于空载状态后再去皮。去皮请求将发送至控制板，由其在允许阶段执行。</p>
      <div v-if="error" class="err">{{ error }}</div>
      <div class="actions">
        <button @click="emit('close')">取消</button>
        <button class="primary" :disabled="submitting" @click="onConfirm">
          {{ submitting ? '下发中…' : '确认去皮' }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.overlay { position: fixed; inset: 0; background: rgba(0,0,0,.6); display: flex; align-items: center; justify-content: center; z-index: 100; }
.dialog { background: var(--bg-card); border: 1px solid var(--border-hi); border-radius: 10px; width: 400px; padding: 20px; display: flex; flex-direction: column; gap: 12px; }
.dlg-title { font-size: 16px; font-weight: 700; }
.err { color: #fca5a5; font-size: 12px; }
.actions { display: flex; justify-content: flex-end; gap: 10px; }
</style>
