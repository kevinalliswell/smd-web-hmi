<script setup>
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { changePassword } from '@/api/users'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const auth = useAuthStore()
const oldPassword = ref('')
const newPassword = ref('')
const confirmPassword = ref('')
const error = ref('')
const loading = ref(false)

const canSubmit = computed(() =>
  oldPassword.value &&
  newPassword.value.length >= 8 &&
  newPassword.value === confirmPassword.value,
)

async function onSubmit() {
  if (!canSubmit.value) return
  error.value = ''
  loading.value = true
  try {
    await changePassword(oldPassword.value, newPassword.value)
    auth.logout()
    await router.replace({ name: 'login', query: { passwordChanged: '1' } })
  } catch (e) {
    error.value = e.response?.data?.message || '密码修改失败'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="change-wrap">
    <form class="change-card" @submit.prevent="onSubmit">
      <div class="change-brand">首次登录安全设置</div>
      <div class="change-sub muted">默认口令必须修改后才能进入上位机。</div>

      <label>当前密码</label>
      <input v-model="oldPassword" type="password" autocomplete="current-password" autofocus />

      <label>新密码</label>
      <input v-model="newPassword" type="password" autocomplete="new-password" />
      <div class="hint muted">至少 8 个字符，且不能与当前密码相同。</div>

      <label>确认新密码</label>
      <input v-model="confirmPassword" type="password" autocomplete="new-password" />
      <div v-if="confirmPassword && newPassword !== confirmPassword" class="field-error">两次输入的密码不一致</div>
      <div v-if="error" class="change-error">{{ error }}</div>

      <button class="primary" type="submit" :disabled="loading || !canSubmit">
        {{ loading ? '正在更新…' : '修改密码并重新登录' }}
      </button>
    </form>
  </div>
</template>

<style scoped>
.change-wrap { display: flex; align-items: center; justify-content: center; height: 100%; }
.change-card {
  width: 380px; background: var(--bg-card); border: 1px solid var(--border);
  border-radius: 12px; padding: 28px; display: flex; flex-direction: column; gap: 8px;
}
.change-brand { font-size: 20px; font-weight: 700; text-align: center; }
.change-sub { text-align: center; margin-bottom: 14px; font-size: 12px; }
label { font-size: 12px; color: var(--text-sec); margin-top: 8px; }
.hint { font-size: 11px; }
.field-error { color: #fca5a5; font-size: 11px; }
.change-error { color: #fca5a5; background: var(--red-dim); border: 1px solid var(--red); border-radius: 5px; padding: 8px; font-size: 12px; margin-top: 8px; }
button { margin-top: 14px; }
</style>
