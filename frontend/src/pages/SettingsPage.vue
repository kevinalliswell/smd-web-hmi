<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useRole } from '@/composables/useRole'
import { useDeviceStore } from '@/stores/device'
import { useAuthStore } from '@/stores/auth'
import { fetchHealth, fetchSystemInfo, syncTime } from '@/api/system'
import { fetchUsers, createUser, updateUser, changePassword } from '@/api/users'
import PasswordResetDialog from '@/components/users/PasswordResetDialog.vue'
import MaintenancePanel from '@/components/system/MaintenancePanel.vue'

const { canConfigure } = useRole()
const auth = useAuthStore()
const device = useDeviceStore()
const router = useRouter()

const banner = ref(null)
const health = ref(null)
const info = ref(null)
const users = ref([])
const resetTarget = ref(null)
const resetOpen = ref(false)
const resetLoading = ref(false)
const resetError = ref('')

const ROLES = ['observer', 'operator', 'admin', 'maintainer']
const newUser = reactive({ username: '', password: '', role: 'observer', display_name: '' })
const pwd = reactive({ old_password: '', new_password: '' })

function notify(type, text) {
  banner.value = { type, text }
}

async function loadAll() {
  try {
    health.value = await fetchHealth()
  } catch { /* ignore */ }
  if (canConfigure()) {
    try {
      info.value = await fetchSystemInfo()
    } catch { /* ignore */ }
    try {
      users.value = (await fetchUsers()) || []
    } catch { /* ignore */ }
  }
}

async function onCreateUser() {
  try {
    await createUser({ ...newUser })
    notify('ok', `用户 ${newUser.username} 已创建`)
    newUser.username = ''
    newUser.password = ''
    newUser.display_name = ''
    await loadAll()
  } catch (e) {
    notify('err', '创建失败：' + (e.response?.data?.message || e.message))
  }
}

async function onChangeRole(u, role) {
  try {
    await updateUser(u.id, { role })
    notify('ok', `${u.username} 角色已改为 ${role}`)
    await loadAll()
  } catch (e) {
    notify('err', '修改失败：' + (e.response?.data?.message || e.message))
  }
}

async function onToggleActive(u) {
  try {
    await updateUser(u.id, { is_active: !u.is_active })
    notify('ok', `${u.username} 已${u.is_active ? '停用' : '启用'}`)
    await loadAll()
  } catch (e) {
    notify('err', '修改失败：' + (e.response?.data?.message || e.message))
  }
}

function openResetPassword(u) {
  resetTarget.value = u
  resetError.value = ''
  resetOpen.value = true
}

async function onResetPassword(newPassword) {
  if (!resetTarget.value) return
  resetLoading.value = true
  resetError.value = ''
  try {
    await updateUser(resetTarget.value.id, { new_password: newPassword })
    notify('ok', `${resetTarget.value.username} 密码已重置`)
    resetOpen.value = false
    resetTarget.value = null
  } catch (e) {
    resetError.value = e.response?.data?.message || e.message || '重置失败'
  } finally {
    resetLoading.value = false
  }
}

async function onChangeOwnPassword() {
  if (!pwd.old_password || !pwd.new_password) return
  try {
    await changePassword(pwd.old_password, pwd.new_password)
    auth.logout({ revoke: false })
    await router.replace({ name: 'login', query: { passwordChanged: '1' } })
  } catch (e) {
    notify('err', '修改失败：' + (e.response?.data?.message || e.message))
  }
}

async function onSyncTime() {
  if (!device.supportsCommand('sync_time')) return
  try {
    const r = await syncTime()
    notify('ok', `校时已下发（${r.result}），时间 ${r.sent_time}`)
  } catch (e) {
    notify('err', '校时失败：' + (e.response?.data?.message || e.message))
  }
}

onMounted(loadAll)
</script>

<template>
  <div class="page">
    <h1 class="page-title">系统设置</h1>
    <div v-if="banner" class="banner" :class="banner.type">{{ banner.text }}</div>

    <MaintenancePanel v-if="canConfigure()" />

    <!-- 系统信息 -->
    <div class="card">
      <div class="card-title">系统信息</div>
      <table class="kv">
        <tbody>
          <tr><th>后端版本</th><td class="mono">{{ health?.version || info?.version || '—' }}</td></tr>
          <tr v-if="info"><th>HostComm</th><td class="mono">{{ info.hostcomm.comm_quality }}（mock: {{ info.hostcomm.mock }}）</td></tr>
          <tr v-if="info"><th>数据库路径</th><td class="mono small">{{ info.db_path }}</td></tr>
          <tr><th>当前用户</th><td>{{ auth.username }} · {{ auth.role }}</td></tr>
        </tbody>
      </table>
      <div v-if="canConfigure()" class="actions">
        <button v-if="device.supportsCommand('sync_time')" @click="onSyncTime">校时（sync_time）</button>
      </div>
    </div>

    <!-- 修改自己的密码 -->
    <div class="card">
      <div class="card-title">修改密码</div>
      <div class="form">
        <input v-model="pwd.old_password" type="password" placeholder="原密码" />
        <input v-model="pwd.new_password" type="password" placeholder="新密码" />
        <button class="primary" :disabled="!pwd.old_password || !pwd.new_password" @click="onChangeOwnPassword">更新密码</button>
      </div>
    </div>

    <!-- 用户管理（Admin）-->
    <div v-if="canConfigure()" class="card">
      <div class="card-title">用户管理</div>
      <table class="u-table">
        <thead>
          <tr><th>用户名</th><th>显示名</th><th>角色</th><th>状态</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="u in users" :key="u.id">
            <td class="mono">{{ u.username }}</td>
            <td>{{ u.display_name || '—' }}</td>
            <td>
              <select :value="u.role" @change="onChangeRole(u, $event.target.value)">
                <option v-for="r in ROLES" :key="r" :value="r">{{ r }}</option>
              </select>
            </td>
            <td>
              <span :class="u.is_active ? 'on' : 'off'">{{ u.is_active ? '启用' : '停用' }}</span>
            </td>
            <td class="ops">
              <button @click="onToggleActive(u)">{{ u.is_active ? '停用' : '启用' }}</button>
              <button @click="openResetPassword(u)">重置密码</button>
            </td>
          </tr>
        </tbody>
      </table>

      <div class="new-user">
        <div class="sub-title">新建用户</div>
        <div class="form">
          <input v-model="newUser.username" placeholder="用户名" />
          <input v-model="newUser.password" type="password" placeholder="密码" />
          <input v-model="newUser.display_name" placeholder="显示名（可选）" />
          <select v-model="newUser.role">
            <option v-for="r in ROLES" :key="r" :value="r">{{ r }}</option>
          </select>
          <button class="primary" :disabled="!newUser.username || !newUser.password" @click="onCreateUser">创建</button>
        </div>
      </div>
    </div>

    <p v-else class="muted">用户管理与校时需要 Admin 及以上角色。</p>

    <PasswordResetDialog
      v-model="resetOpen"
      :username="resetTarget?.username || ''"
      :loading="resetLoading"
      :error="resetError"
      @submit="onResetPassword"
    />
  </div>
</template>

<style scoped>
.sub-title { font-weight: 600; margin: 12px 0 8px; color: var(--text-sec); font-size: var(--fs-base); }
.kv { border-collapse: collapse; font-size: var(--fs-md); }
.kv th, .kv td { text-align: left; padding: 5px 10px; }
.kv th { color: var(--text-sec); font-weight: 600; width: 120px; }
.small { font-size: var(--fs-sm); }
.actions { margin-top: 12px; }
.form { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.u-table { width: 100%; border-collapse: collapse; font-size: var(--fs-base); }
.u-table th, .u-table td { text-align: left; padding: 7px 8px; border-bottom: 1px solid var(--border); }
.u-table th { color: var(--text-sec); font-weight: 600; font-size: var(--fs-sm); }
.ops { display: flex; gap: 6px; }
.ops button { padding: 3px 10px; font-size: var(--fs-base); }
.on { color: var(--green); }
.off { color: var(--text-muted); }
.new-user { margin-top: 8px; }
@media (max-width: 560px) { .form { align-items: stretch; flex-direction: column; } .ops { flex-wrap: wrap; } }
</style>
