<script setup>
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import ThemeToggle from '@/components/shared/ThemeToggle.vue'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()

const username = ref('')
const password = ref('')
const error = ref('')
const loading = ref(false)

async function onSubmit() {
  error.value = ''
  loading.value = true
  try {
    const result = await auth.login(username.value, password.value)
    router.push({ name: result.must_change_password ? 'change-password' : 'overview' })
  } catch (e) {
    error.value = e.response?.data?.message || '登录失败，请检查用户名或密码'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-wrap">
    <ThemeToggle class="login-theme" />
    <form class="login-card" @submit.prevent="onSubmit">
      <h1 class="login-brand">熔滴炉 Web 上位机</h1>
      <div class="login-sub muted">GB/T 34211 · 本地工业上位机</div>

      <div v-if="route.query.passwordChanged" class="login-success">密码已更新，请使用新密码登录。</div>

      <label for="login-username">用户名</label>
      <input id="login-username" v-model="username" autocomplete="username" placeholder="用户名" autofocus />

      <label for="login-password">密码</label>
      <input id="login-password" v-model="password" type="password" autocomplete="current-password" placeholder="密码" />

      <div v-if="error" class="login-error">{{ error }}</div>

      <button class="primary login-btn" type="submit" :disabled="loading">
        {{ loading ? '登录中…' : '登录' }}
      </button>
      <div class="login-hint muted">首次部署账户为 admin；一次性口令由安装器生成，首次登录必须修改。</div>
    </form>
  </div>
</template>

<style scoped>
.login-wrap { position: relative; display: flex; align-items: center; justify-content: center; min-height: 100%; padding: 20px; }
.login-theme { position: absolute; top: 16px; right: 16px; }
.login-card {
  width: min(100%, 360px); background: var(--bg-card); border: 1px solid var(--border);
  border-radius: 12px; padding: 28px; display: flex; flex-direction: column; gap: 8px;
}
.login-brand { font-size: 20px; font-weight: 700; text-align: center; }
.login-sub { text-align: center; margin-bottom: 16px; font-size: 12px; }
label { font-size: 12px; color: var(--text-sec); margin-top: 8px; }
.login-error { color: var(--danger-text); background: var(--red-dim); border: 1px solid var(--red); border-radius: 5px; padding: 8px; font-size: 12px; margin-top: 8px; }
.login-success { color: var(--success-text); background: var(--green-dim); border: 1px solid var(--green); border-radius: 5px; padding: 8px; font-size: 12px; }
.login-btn { margin-top: 16px; }
.login-hint { margin-top: 14px; font-size: 11px; text-align: center; line-height: 1.5; }
@media (max-width: 420px) { .login-card { padding: 22px 18px; } .login-theme { top: 10px; right: 10px; } }
</style>
