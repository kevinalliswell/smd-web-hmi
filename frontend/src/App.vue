<script setup>
import { computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import AppHeader from '@/components/layout/AppHeader.vue'
import AppSidebar from '@/components/layout/AppSidebar.vue'
import AppFooter from '@/components/layout/AppFooter.vue'
import { useAuthStore } from '@/stores/auth'
import { useAlarmsStore } from '@/stores/alarms'
import { useWebSocket } from '@/composables/useWebSocket'

const route = useRoute()
const auth = useAuthStore()
const alarms = useAlarmsStore()

// 登录页不显示主框架（顶栏/侧栏/底栏）
const isChrome = computed(() => route.name !== 'login' && auth.isLoggedIn)

// 全局 WebSocket：按登录态连接/断开，按消息分发到各 store。
// 必须用 watch 而非仅 onMounted——登录是 SPA 内导航，App 不会重新挂载，
// 只在挂载时判断会导致登录后实时推送永不建立（需手动刷新页面）。
const ws = useWebSocket()
onMounted(() => auth.checkToken())
watch(
  () => auth.isLoggedIn,
  (loggedIn) => {
    if (loggedIn) {
      ws.connect()
      // 拉取活跃报警，使顶栏/侧栏徽章即时显示（后续由 WS 实时更新）
      alarms.loadActive().catch(() => {})
    } else {
      ws.disconnect()
    }
  },
  { immediate: true },
)
onUnmounted(() => ws.disconnect())
</script>

<template>
  <div v-if="isChrome" class="layout">
    <AppHeader />
    <div class="body">
      <AppSidebar />
      <main class="content">
        <RouterView />
      </main>
    </div>
    <AppFooter />
  </div>
  <RouterView v-else />
</template>

<style scoped>
.layout { display: flex; flex-direction: column; height: 100%; }
.body { display: flex; flex: 1; min-height: 0; }
.content { flex: 1; overflow-y: auto; padding: 16px; }
</style>
