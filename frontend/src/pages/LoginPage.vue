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
    // 422 不逐字段回显：登录页没必要把输入校验细节（字段名、正则）摊给操作员。
    // 其余情况沿用后端的业务消息；没有响应体时回到通用中文提示，不暴露传输层英文错误。
    error.value =
      e.response?.status === 422
        ? '用户名或密码格式不正确'
        : e.response?.data?.message || '登录失败，请检查用户名或密码'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-wrap">
    <ThemeToggle class="login-theme" />
    <form class="login-card" @submit.prevent="onSubmit">
      <span class="login-glyph" aria-hidden="true">
        <svg width="28" height="28" viewBox="0 0 24 24"><path d="M12 3.5c3 3.6 4.9 6.3 4.9 8.9a4.9 4.9 0 0 1-9.8 0c0-2.6 1.9-5.3 4.9-8.9Z" fill="var(--bg-base)" /></svg>
      </span>
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
  width: min(100%, 400px); background: var(--bg-card); border: 1px solid var(--border);
  border-radius: var(--radius-dialog); padding: 32px; display: flex; flex-direction: column; gap: 8px;
  box-shadow: var(--shadow-lg);
}
.login-glyph {
  width: 52px; height: 52px; border-radius: var(--radius-dialog); background: var(--accent);
  display: grid; place-items: center; margin: 0 auto 4px;
  box-shadow: 0 0 0 6px var(--accent-soft);
}
.login-brand { font-size: 21px; font-weight: 700; text-align: center; }
.login-sub { text-align: center; margin-bottom: 16px; font-size: var(--fs-base); }
label { font-size: var(--fs-base); color: var(--text-sec); margin-top: 8px; }
.login-error { color: var(--danger-text); background: var(--red-dim); border: 1px solid var(--red); border-radius: 5px; padding: 8px; font-size: var(--fs-base); margin-top: 8px; }
.login-success { color: var(--success-text); background: var(--green-dim); border: 1px solid var(--green); border-radius: 5px; padding: 8px; font-size: var(--fs-base); }
.login-btn { margin-top: 16px; }
.login-hint { margin-top: 14px; font-size: var(--fs-sm); text-align: center; line-height: 1.5; }
@media (max-width: 420px) { .login-card { padding: 22px 18px; } .login-theme { top: 10px; right: 10px; } }
</style>
