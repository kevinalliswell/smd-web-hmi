<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import VersionMismatchBanner from '@/components/layout/VersionMismatchBanner.vue'
import AppHeader from '@/components/layout/AppHeader.vue'
import AppSidebar from '@/components/layout/AppSidebar.vue'
import AppFooter from '@/components/layout/AppFooter.vue'
import { useAuthStore } from '@/stores/auth'
import { useAlarmsStore } from '@/stores/alarms'
import { useWebSocket } from '@/composables/useWebSocket'

const route = useRoute()
const auth = useAuthStore()
const alarms = useAlarmsStore()
const navigationOpen = ref(false)

// 登录页不显示主框架（顶栏/侧栏/底栏）
const isChrome = computed(() => !['login', 'change-password'].includes(route.name) && auth.isLoggedIn)

// 全局 WebSocket：登录后连接，按消息分发到各 store
const ws = useWebSocket()
function closeNavigation() {
  navigationOpen.value = false
}

function onKeydown(event) {
  if (event.key === 'Escape') closeNavigation()
}

onMounted(() => {
  auth.checkToken()
  window.addEventListener('keydown', onKeydown)
})

watch(() => route.name, closeNavigation)

watch(
  () => [auth.isLoggedIn, auth.mustChangePassword],
  ([loggedIn, mustChangePassword]) => {
    if (loggedIn && !mustChangePassword) {
      ws.connect()
      // 拉取活跃报警，使顶栏/侧栏徽章即时显示（后续由 WS 实时更新）
      alarms.loadActive().catch(() => {})
    } else {
      ws.disconnect()
    }
  },
  { immediate: true },
)
onUnmounted(() => {
  window.removeEventListener('keydown', onKeydown)
  ws.disconnect()
})
</script>

<template>
  <div v-if="isChrome" class="layout">
    <AppHeader
      :navigation-open="navigationOpen"
      @toggle-navigation="navigationOpen = !navigationOpen"
    />
    <div class="body">
      <div
        v-if="navigationOpen"
        class="nav-backdrop"
        aria-hidden="true"
        @click="closeNavigation"
      />
      <AppSidebar :open="navigationOpen" @navigate="closeNavigation" />
      <main class="content">
        <div class="content-inner"><VersionMismatchBanner /><RouterView /></div>
      </main>
    </div>
    <AppFooter />
  </div>
  <RouterView v-else />
</template>

<style scoped>
.layout { display: flex; flex-direction: column; height: 100%; min-width: 0; }
.body { position: relative; display: flex; flex: 1; min-width: 0; min-height: 0; }
.content { flex: 1; min-width: 0; overflow: auto; padding: 18px; }
.content-inner { width: min(100%, 1600px); min-width: 0; margin: 0 auto; }
.nav-backdrop { display: none; }

@media (max-width: 900px) {
  .content { padding: 12px; }
  .nav-backdrop {
    position: fixed;
    z-index: 20;
    inset: var(--header-h) 0 var(--footer-h) 0;
    display: block;
    width: auto;
    height: auto;
    padding: 0;
    border: 0;
    border-radius: 0;
    background: var(--overlay);
  }
}

@media (max-width: 480px) {
  .content { padding: 8px; }
}
</style>
