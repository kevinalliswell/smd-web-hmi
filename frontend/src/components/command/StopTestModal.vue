<script setup>
// 停止试验：二次确认（CO 安全警告）→ 取令牌 → 下发 stop_test。
import { ref } from 'vue'
import { requestConfirmToken, sendCommand } from '@/api/commands'

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
  <div class="overlay" @click.self="emit('close')">
    <div class="dialog danger">
      <div class="dlg-title">⚠ 停止试验</div>
      <div class="co-warn">
        受控停止将请求控制板进入安全处置/吹扫流程，不会绕过任何安全处置步骤。
        请确认现场具备停止条件。
      </div>
      <div v-if="error" class="err">{{ error }}</div>
      <div class="actions">
        <button @click="emit('close')">取消</button>
        <button class="danger" :disabled="submitting" @click="onConfirm">
          {{ submitting ? '下发中…' : '确认停止' }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.overlay { position: fixed; inset: 0; background: rgba(0,0,0,.6); display: flex; align-items: center; justify-content: center; z-index: 100; }
.dialog { background: var(--bg-card); border: 1px solid var(--red); border-radius: 10px; width: 420px; padding: 20px; display: flex; flex-direction: column; gap: 10px; }
.dlg-title { font-size: 16px; font-weight: 700; color: #fca5a5; }
.co-warn { background: var(--red-dim); border: 1px solid var(--red); color: #fca5a5; border-radius: 6px; padding: 10px; font-size: 12px; line-height: 1.6; }
.err { color: #fca5a5; font-size: 12px; }
.actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 6px; }
</style>
