<script setup>
import { computed, ref, watch } from 'vue'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  username: { type: String, default: '' },
  loading: { type: Boolean, default: false },
  error: { type: String, default: '' },
})
const emit = defineEmits(['update:modelValue', 'submit'])

const password = ref('')
const confirmation = ref('')

const categoryCount = computed(() => [
  /[a-z]/.test(password.value),
  /[A-Z]/.test(password.value),
  /\d/.test(password.value),
  /[^A-Za-z0-9]/.test(password.value),
].filter(Boolean).length)
const strength = computed(() => {
  if (password.value.length < 8) return '不足 8 个字符'
  if (password.value.length >= 12 && categoryCount.value >= 3) return '强'
  if (categoryCount.value >= 2) return '中'
  return '弱，建议混合字母、数字或符号'
})
const matches = computed(() => confirmation.value === '' || password.value === confirmation.value)
const canSubmit = computed(() => password.value.length >= 8 && password.value === confirmation.value)

function resetFields() {
  password.value = ''
  confirmation.value = ''
}

function cancel() {
  if (props.loading) return
  resetFields()
  emit('update:modelValue', false)
}

function submit() {
  if (!canSubmit.value || props.loading) return
  emit('submit', password.value)
}

watch(
  () => props.modelValue,
  () => resetFields(),
)
</script>

<template>
  <div v-if="modelValue" class="overlay" @click.self="cancel">
    <form class="dialog" @submit.prevent="submit">
      <div class="dlg-title">重置 {{ username }} 的密码</div>
      <p class="security-note">密码只会通过加密连接提交；请勿通过聊天或纸条传递。</p>

      <label>新密码</label>
      <input v-model="password" type="password" autocomplete="new-password" autofocus />
      <div class="strength" :class="{ ok: password.length >= 8 }">强度：{{ strength }}</div>

      <label>再次输入新密码</label>
      <input v-model="confirmation" type="password" autocomplete="new-password" />
      <div v-if="!matches" class="field-error">两次输入不一致</div>
      <div v-if="error" class="server-error">{{ error }}</div>

      <div class="dlg-actions">
        <button type="button" :disabled="loading" @click="cancel">取消</button>
        <button data-test="submit" class="primary" type="submit" :disabled="loading || !canSubmit">
          {{ loading ? '正在重置…' : '确认重置' }}
        </button>
      </div>
    </form>
  </div>
</template>

<style scoped>
.overlay {
  position: fixed; inset: 0; background: rgba(0, 0, 0, 0.65);
  display: flex; align-items: center; justify-content: center; z-index: 110;
}
.dialog {
  width: 420px; max-width: 90vw; padding: 22px; border-radius: 10px;
  border: 1px solid var(--border-hi); background: var(--bg-card);
  display: flex; flex-direction: column; gap: 8px;
}
.dlg-title { font-size: 16px; font-weight: 700; }
.security-note { color: var(--text-sec); font-size: 12px; line-height: 1.5; margin-bottom: 6px; }
label { color: var(--text-sec); font-size: 12px; margin-top: 6px; }
.strength { color: var(--yellow); font-size: 11px; }
.strength.ok { color: var(--text-sec); }
.field-error { color: #fca5a5; font-size: 11px; }
.server-error { color: #fca5a5; background: var(--red-dim); border: 1px solid var(--red); border-radius: 5px; padding: 8px; font-size: 12px; }
.dlg-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 14px; }
</style>
