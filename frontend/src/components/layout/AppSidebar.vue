<script setup>
import { storeToRefs } from 'pinia'
import { useAlarmsStore } from '@/stores/alarms'
import { useRole } from '@/composables/useRole'

const alarms = useAlarmsStore()
const { unackedCount } = storeToRefs(alarms)
const { hasRole } = useRole()

const groups = [
  {
    title: '监控',
    items: [
      { name: 'overview', label: '实时总览', icon: '📊' },
      { name: 'trend', label: '趋势曲线', icon: '📈' },
      { name: 'test', label: '当前试验', icon: '🔬' },
      { name: 'alarms', label: '报警事件', icon: '🚨', badge: true },
      { name: 'history', label: '历史试验', icon: '🗂️' },
    ],
  },
  {
    title: '配置与诊断',
    items: [
      { name: 'parameters', label: '参数配置', icon: '⚙️' },
      { name: 'diagnostics', label: '设备诊断', icon: '🩺', role: 'maintainer' },
      { name: 'settings', label: '系统设置', icon: '🔧' },
    ],
  },
  {
    title: '分析与报告',
    items: [
      { name: 'analytics', label: '数据分析', icon: '🧮' },
      { name: 'reports', label: '报告生成', icon: '📄' },
      { name: 'help', label: '帮助', icon: '❓' },
    ],
  },
]

function visible(item) {
  return !item.role || hasRole(item.role)
}
</script>

<template>
  <nav id="nav">
    <template v-for="group in groups" :key="group.title">
      <div class="nav-section">{{ group.title }}</div>
      <template v-for="item in group.items" :key="item.name">
        <RouterLink
          v-if="visible(item)"
          :to="{ name: item.name }"
          class="nav-item"
          active-class="active"
        >
          <span class="nav-icon">{{ item.icon }}</span>{{ item.label }}
          <span v-if="item.badge && unackedCount" class="nav-badge">{{ unackedCount }}</span>
        </RouterLink>
      </template>
    </template>
  </nav>
</template>

<style scoped>
#nav {
  width: var(--nav-w); background: var(--bg-card); border-right: 1px solid var(--border);
  display: flex; flex-direction: column; overflow-y: auto; flex-shrink: 0; padding-bottom: 12px;
}
.nav-section {
  padding: 12px 14px 6px; font-size: 10px; letter-spacing: 1px;
  text-transform: uppercase; color: var(--text-muted);
}
.nav-item {
  display: flex; align-items: center; gap: 10px; padding: 9px 14px; cursor: pointer;
  color: var(--text-sec); border-left: 3px solid transparent; transition: all .15s;
}
.nav-item:hover { background: var(--bg-hover); color: var(--text-pri); }
.nav-item.active { background: var(--accent-dim); color: var(--accent); border-left-color: var(--accent); }
.nav-icon { width: 16px; text-align: center; }
.nav-badge {
  margin-left: auto; background: var(--red); color: #fff; font-size: 10px;
  font-weight: 700; border-radius: 8px; padding: 1px 5px;
}
</style>
