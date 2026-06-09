<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const auth = useAuthStore()

const username = ref('')
const password = ref('')
const error = ref('')
const loading = ref(false)

async function onSubmit() {
  error.value = ''
  loading.value = true
  try {
    await auth.login(username.value, password.value)
    router.push({ name: 'overview' })
  } catch (e) {
    error.value = e.response?.data?.message || '登录失败，请检查用户名或密码'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-wrap">
    <form class="login-card" @submit.prevent="onSubmit">
      <div class="login-brand">熔滴炉 Web 上位机</div>
      <div class="login-sub muted">GB/T 34211 · 本地工业上位机</div>

      <label>用户名</label>
      <input v-model="username" autocomplete="username" placeholder="用户名" autofocus />

      <label>密码</label>
      <input v-model="password" type="password" autocomplete="current-password" placeholder="密码" />

      <div v-if="error" class="login-error">{{ error }}</div>

      <button class="primary login-btn" type="submit" :disabled="loading">
        {{ loading ? '登录中…' : '登录' }}
      </button>
      <div class="login-hint muted">首次部署默认账户 admin / admin，登录后请尽快修改密码。</div>
    </form>
  </div>
</template>

<style scoped>
.login-wrap { display: flex; align-items: center; justify-content: center; height: 100%; }
.login-card {
  width: 340px; background: var(--bg-card); border: 1px solid var(--border);
  border-radius: 12px; padding: 28px; display: flex; flex-direction: column; gap: 8px;
}
.login-brand { font-size: 20px; font-weight: 700; text-align: center; }
.login-sub { text-align: center; margin-bottom: 16px; font-size: 12px; }
label { font-size: 12px; color: var(--text-sec); margin-top: 8px; }
.login-error { color: #fca5a5; background: var(--red-dim); border: 1px solid var(--red); border-radius: 5px; padding: 8px; font-size: 12px; margin-top: 8px; }
.login-btn { margin-top: 16px; }
.login-hint { margin-top: 14px; font-size: 11px; text-align: center; line-height: 1.5; }
</style>
