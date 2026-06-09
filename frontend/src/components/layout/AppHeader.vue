<script setup>
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'
import { useAuthStore } from '@/stores/auth'
import { useDeviceStore } from '@/stores/device'
import { useAlarmsStore } from '@/stores/alarms'

const router = useRouter()
const auth = useAuthStore()
const device = useDeviceStore()
const alarms = useAlarmsStore()
const { commQuality, currentState } = storeToRefs(device)
const { unackedCount } = storeToRefs(alarms)

const commDot = computed(
  () => ({ online: 'dot-green', degraded: 'dot-yellow', offline: 'dot-red' })[commQuality.value] || 'dot-gray',
)
const commLabel = computed(
  () => ({ online: '通信正常', degraded: '通信降级', offline: '通信断开' })[commQuality.value] || '未知',
)

function onLogout() {
  auth.logout()
  router.push({ name: 'login' })
}
</script>

<template>
  <header id="header">
    <div class="brand">熔滴炉 <span class="muted">Web 上位机</span></div>
    <div class="hd-sep" />
    <div class="hd-badge"><span class="dot" :class="commDot" />{{ commLabel }}</div>
    <div class="hd-badge">状态：<b style="color: var(--accent)">{{ currentState }}</b></div>
    <div class="spacer" />
    <div class="hd-bell" @click="router.push({ name: 'alarms' })">
      🔔<span v-if="unackedCount" class="alarm-count">{{ unackedCount }}</span>
    </div>
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
.brand { font-weight: 700; font-size: 15px; }
.hd-sep { width: 1px; height: 24px; background: var(--border); }
.hd-badge {
  display: flex; align-items: center; gap: 6px; padding: 3px 10px;
  border-radius: 4px; background: var(--bg-card2); border: 1px solid var(--border);
}
.spacer { flex: 1; }
.hd-bell { position: relative; cursor: pointer; font-size: 16px; padding: 4px; }
.alarm-count {
  position: absolute; top: -2px; right: -2px; background: var(--red); color: #fff;
  font-size: 10px; font-weight: 700; border-radius: 8px; padding: 1px 4px;
}
.hd-user { color: var(--text-sec); }
</style>
