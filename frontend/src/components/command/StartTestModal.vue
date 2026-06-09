<script setup>
// 启动试验：输入试验编号 → 二次确认（含 CO 安全提示）→ 经后端获取 confirm_token 后下发。
import { ref } from 'vue'
import { requestConfirmToken, sendCommand } from '@/api/commands'

const emit = defineEmits(['close', 'done'])
const testId = ref(suggestTestId())
const error = ref('')
const submitting = ref(false)
const confirming = ref(false)

function suggestTestId() {
  const d = new Date()
  const ymd = `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, '0')}${String(d.getDate()).padStart(2, '0')}`
  return `TEST-${ymd}-001`
}

async function onStart() {
  // 第一步：弹出二次确认
  confirming.value = true
}

async function onConfirm() {
  error.value = ''
  submitting.value = true
  try {
    // CO 相关命令：先取一次性确认令牌，再携带令牌下发
    const { confirm_token } = await requestConfirmToken('start_test')
    const result = await sendCommand('start_test', { test_id: testId.value }, confirm_token)
    emit('done', result)
    emit('close')
  } catch (e) {
    error.value = e.response?.data?.message || '启动失败'
  } finally {
    submitting.value = false
    confirming.value = false
  }
}
</script>

<template>
  <div class="overlay" @click.self="emit('close')">
    <div class="dialog">
      <div class="dlg-title">启动试验</div>
      <label>试验编号</label>
      <input v-model="testId" />
      <div v-if="error" class="err">{{ error }}</div>

      <div v-if="confirming" class="co-warn">
        ⚠ 本试验涉及 CO 工艺阶段。请确认现场排风、CO 监测与安全继电器均正常。
        启动请求将发送至控制板，最终由 STM32 状态机与硬接线联锁裁决。
      </div>

      <div class="actions">
        <button @click="emit('close')">取消</button>
        <button v-if="!confirming" class="primary" @click="onStart">下一步</button>
        <button v-else class="danger" :disabled="submitting" @click="onConfirm">
          {{ submitting ? '下发中…' : '确认启动' }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.overlay { position: fixed; inset: 0; background: rgba(0,0,0,.6); display: flex; align-items: center; justify-content: center; z-index: 100; }
.dialog { background: var(--bg-card); border: 1px solid var(--border-hi); border-radius: 10px; width: 420px; padding: 20px; display: flex; flex-direction: column; gap: 10px; }
.dlg-title { font-size: 16px; font-weight: 700; }
label { font-size: 12px; color: var(--text-sec); }
.co-warn { background: var(--yellow-dim); border: 1px solid var(--yellow); color: #fde68a; border-radius: 6px; padding: 10px; font-size: 12px; line-height: 1.6; }
.err { color: #fca5a5; font-size: 12px; }
.actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 6px; }
</style>
