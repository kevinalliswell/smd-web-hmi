<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { useAlarmsStore } from '@/stores/alarms'
import { useRole } from '@/composables/useRole'
import NavIcon from '@/components/shared/NavIcon.vue'

const props = defineProps({ open: { type: Boolean, default: false } })
defineEmits(['navigate'])

const isMobile = ref(false)
const navigationHidden = computed(() => isMobile.value && !props.open)
let mediaQuery = null

function updateMobile(event) {
  isMobile.value = event.matches
}

onMounted(() => {
  mediaQuery = window.matchMedia('(max-width: 900px)')
  updateMobile(mediaQuery)
  if (mediaQuery.addEventListener) mediaQuery.addEventListener('change', updateMobile)
  else mediaQuery.addListener?.(updateMobile)
})

onBeforeUnmount(() => {
  if (mediaQuery?.removeEventListener) mediaQuery.removeEventListener('change', updateMobile)
  else mediaQuery?.removeListener?.(updateMobile)
})

const alarms = useAlarmsStore()
const { unackedCount } = storeToRefs(alarms)
const { hasRole } = useRole()

const groups = [
  {
    title: '监控',
    items: [
      { name: 'overview', label: '实时总览' },
      { name: 'trend', label: '趋势曲线' },
      { name: 'test', label: '当前试验' },
      { name: 'alarms', label: '报警事件', badge: true },
      { name: 'history', label: '历史试验' },
    ],
  },
  {
    title: '配置与诊断',
    items: [
      { name: 'recipes', label: '实验配方' },
      { name: 'operations', label: '操作记录' },
      { name: 'parameters', label: '参数配置' },
      { name: 'diagnostics', label: '设备诊断', role: 'maintainer' },
      { name: 'settings', label: '系统设置' },
    ],
  },
  {
    title: '分析与报告',
    items: [
      { name: 'analytics', label: '数据分析' },
      { name: 'reports', label: '报告生成' },
      { name: 'help', label: '帮助' },
    ],
  },
]

function visible(item) {
  return !item.role || hasRole(item.role)
}
</script>

<template>
  <nav
    id="primary-navigation"
    :class="{ open }"
    aria-label="主导航"
    :aria-hidden="navigationHidden ? 'true' : undefined"
    :inert="navigationHidden ? '' : undefined"
  >
    <template v-for="group in groups" :key="group.title">
      <div class="nav-section">{{ group.title }}</div>
      <template v-for="item in group.items" :key="item.name">
        <RouterLink
          v-if="visible(item)"
          :to="{ name: item.name }"
          class="nav-item"
          active-class="active"
          @click="$emit('navigate')"
        >
          <NavIcon :name="item.name" />{{ item.label }}
          <span v-if="item.badge && unackedCount" class="nav-badge">{{ unackedCount }}</span>
        </RouterLink>
      </template>
    </template>
  </nav>
</template>

<style scoped>
#primary-navigation {
  width: var(--nav-w); background: var(--bg-card); border-right: 1px solid var(--border);
  display: flex; flex-direction: column; overflow-y: auto; flex-shrink: 0; padding-bottom: 12px;
}
.nav-section {
  padding: 12px 14px 6px; font-size: var(--fs-xs); letter-spacing: 1px;
  text-transform: uppercase; color: var(--text-muted);
}
.nav-item {
  display: flex; align-items: center; gap: 10px; padding: 9px 14px; cursor: pointer;
  color: var(--text-sec); border-left: 3px solid transparent; transition: all .15s;
}
.nav-item:hover { background: var(--bg-hover); color: var(--text-pri); }
.nav-item.active { background: var(--accent-dim); color: var(--accent); border-left-color: var(--accent); }
.nav-icon { flex: none; }
.nav-badge {
  margin-left: auto; background: var(--red); color: var(--on-red); font-size: var(--fs-xs);
  font-weight: 700; border-radius: var(--radius-pill); padding: 1px 5px;
}

@media (max-width: 900px) {
  #primary-navigation {
    position: fixed;
    z-index: 30;
    top: var(--header-h);
    bottom: var(--footer-h);
    left: 0;
    width: min(82vw, 280px);
    box-shadow: var(--shadow-lg);
    transform: translateX(-105%);
    visibility: hidden;
    pointer-events: none;
    transition: transform .2s ease, visibility 0s linear .2s;
  }
  #primary-navigation.open {
    visibility: visible;
    pointer-events: auto;
    transform: translateX(0);
    transition-delay: 0s;
  }
}
</style>
