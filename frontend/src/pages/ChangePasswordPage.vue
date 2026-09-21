<script setup>
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { changePassword } from '@/api/users'
import { useAuthStore } from '@/stores/auth'
import ThemeToggle from '@/components/shared/ThemeToggle.vue'

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
    auth.logout({ revoke: false })
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
    <ThemeToggle class="change-theme" />
    <form class="change-card" @submit.prevent="onSubmit">
      <h1 class="change-brand">首次登录安全设置</h1>
      <div class="change-sub muted">默认口令必须修改后才能进入上位机。</div>

      <label for="current-password">当前密码</label>
      <input id="current-password" v-model="oldPassword" type="password" autocomplete="current-password" autofocus />

      <label for="new-password">新密码</label>
      <input id="new-password" v-model="newPassword" type="password" autocomplete="new-password" />
      <div class="hint muted">至少 8 个字符，且不能与当前密码相同。</div>

      <label for="confirm-password">确认新密码</label>
      <input id="confirm-password" v-model="confirmPassword" type="password" autocomplete="new-password" />
      <div v-if="confirmPassword && newPassword !== confirmPassword" class="field-error">两次输入的密码不一致</div>
      <div v-if="error" class="change-error">{{ error }}</div>

      <button class="primary" type="submit" :disabled="loading || !canSubmit">
        {{ loading ? '正在更新…' : '修改密码并重新登录' }}
      </button>
    </form>
  </div>
</template>

<style scoped>
.change-wrap { position: relative; display: flex; align-items: center; justify-content: center; min-height: 100%; padding: 20px; }
.change-theme { position: absolute; top: 16px; right: 16px; }
.change-card {
  width: min(100%, 400px); background: var(--bg-card); border: 1px solid var(--border);
  border-radius: var(--radius-dialog); padding: 32px; display: flex; flex-direction: column; gap: 8px;
  box-shadow: var(--shadow-lg);
}
.change-brand { font-size: 20px; font-weight: 700; text-align: center; }
.change-sub { text-align: center; margin-bottom: 14px; font-size: var(--fs-base); }
label { font-size: var(--fs-base); color: var(--text-sec); margin-top: 8px; }
.hint { font-size: var(--fs-sm); }
.field-error { color: var(--danger-text); font-size: var(--fs-sm); }
.change-error { color: var(--danger-text); background: var(--red-dim); border: 1px solid var(--red); border-radius: 5px; padding: 8px; font-size: var(--fs-base); margin-top: 8px; }
button { margin-top: 14px; }
@media (max-width: 420px) { .change-card { padding: 22px 18px; } .change-theme { top: 10px; right: 10px; } }
</style>
