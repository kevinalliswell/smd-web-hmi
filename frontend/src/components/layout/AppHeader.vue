<script setup>
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'
import { useAuthStore } from '@/stores/auth'
import { useDeviceStore } from '@/stores/device'
import { useAlarmsStore } from '@/stores/alarms'
import ThemeToggle from '@/components/shared/ThemeToggle.vue'

defineProps({ navigationOpen: { type: Boolean, default: false } })
defineEmits(['toggle-navigation'])

const router = useRouter()
const auth = useAuthStore()
const device = useDeviceStore()
const alarms = useAlarmsStore()
const { commQuality, currentState, dataStale } = storeToRefs(device)
const { unackedCount } = storeToRefs(alarms)

const commDot = computed(
  () => dataStale.value && commQuality.value !== 'offline' ? 'dot-yellow' : ({ online: 'dot-green', degraded: 'dot-yellow', offline: 'dot-red' })[commQuality.value] || 'dot-gray',
)
const commLabel = computed(
  () => dataStale.value && commQuality.value !== 'offline' ? '数据过期' : ({ online: '通信正常', degraded: '通信降级', offline: '通信断开' })[commQuality.value] || '未知',
)

function onLogout() {
  auth.logout()
  router.push({ name: 'login' })
}
</script>

<template>
  <header id="header">
    <button
      class="nav-toggle"
      type="button"
      aria-controls="primary-navigation"
      :aria-expanded="String(navigationOpen)"
      :aria-label="navigationOpen ? '关闭主导航' : '打开主导航'"
      @click="$emit('toggle-navigation')"
    >
      <span aria-hidden="true">☰</span>
    </button>
    <div class="brand">熔滴炉 <span class="muted">Web 上位机</span></div>
    <div class="hd-sep" />
    <div class="hd-badge"><span class="dot" :class="commDot" />{{ commLabel }}</div>
    <div class="hd-badge">状态：<b style="color: var(--accent)">{{ currentState }}</b></div>
    <div class="spacer" />
    <button
      class="hd-bell"
      type="button"
      aria-label="查看报警事件"
      title="查看报警事件"
      @click="router.push({ name: 'alarms' })"
    >
      🔔<span v-if="unackedCount" class="alarm-count">{{ unackedCount }}</span>
    </button>
    <ThemeToggle />
    <div class="hd-sep" />
    <div class="hd-user">{{ auth.displayName || auth.username }} · {{ auth.role }}</div>
    <button @click="onLogout">登出</button>
  </header>
</template>

<style scoped>
#header {
  display: flex; align-items: center; height: var(--header-h);
  background: var(--bg-card); border-bottom: 1px solid var(--border);
  padding: 0 16px; gap: 12px; flex-shrink: 0;
}
.nav-toggle { display: none; width: 34px; height: 34px; padding: 0; font-size: var(--fs-title); }
.brand { font-weight: 700; font-size: 15px; }
.hd-sep { width: 1px; height: 24px; background: var(--border); }
.hd-badge {
  display: flex; align-items: center; gap: 6px; padding: 3px 10px;
  border-radius: 4px; background: var(--bg-card2); border: 1px solid var(--border);
}
.spacer { flex: 1; }
.hd-bell { position: relative; width: 34px; height: 34px; padding: 0; font-size: var(--fs-xl); }
.alarm-count {
  position: absolute; top: -2px; right: -2px; background: var(--red); color: var(--on-red);
  font-size: var(--fs-xs); font-weight: 700; border-radius: var(--radius-pill); padding: 1px 4px;
}
.hd-user { color: var(--text-sec); }

@media (max-width: 1100px) {
  .hd-user { display: none; }
}

@media (max-width: 900px) {
  #header { padding: 0 12px; gap: 8px; }
  .nav-toggle { display: grid; place-items: center; }
  .hd-badge { padding-inline: 8px; }
  .hd-sep { display: none; }
}

@media (max-width: 680px) {
  .hd-badge { display: none; }
}

@media (max-width: 460px) {
  #header { padding: 0 8px; }
  .brand .muted { display: none; }
  .brand { font-size: var(--fs-lg); }
}
</style>
