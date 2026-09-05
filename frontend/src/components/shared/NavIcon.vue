<script setup>
// 导航图标：统一的线性 SVG。
//
// 原先用 emoji（📊📈🔬🚨…）。emoji 由系统字体渲染，不同 Windows 版本字形与配色
// 都不一致（工控机常见 Win10 LTSC / Win11 混装），且无法跟随主题着色。改为内联
// SVG：描边用 currentColor，选中态与主题切换自动生效。
defineProps({
  name: { type: String, required: true },
  size: { type: Number, default: 16 },
})
</script>

<template>
  <svg
    class="nav-icon"
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.8"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
    focusable="false"
  >
    <!-- 实时总览：仪表盘 -->
    <template v-if="name === 'overview'">
      <path d="M4 19a8 8 0 1 1 16 0" />
      <path d="M12 15l4-4" />
    </template>
    <!-- 趋势曲线 -->
    <template v-else-if="name === 'trend'">
      <path d="M4 19V5" />
      <path d="M4 19h16" />
      <path d="M7 15l4-5 3 3 4-6" />
    </template>
    <!-- 当前试验：坩埚/滴落 -->
    <template v-else-if="name === 'test'">
      <path d="M8 3h8" />
      <path d="M9 3v6l-4 8a2 2 0 0 0 1.8 3h10.4a2 2 0 0 0 1.8-3l-4-8V3" />
      <path d="M7 15h10" />
    </template>
    <!-- 报警事件 -->
    <template v-else-if="name === 'alarms'">
      <path d="M12 3a6 6 0 0 0-6 6c0 5-2 6-2 6h16s-2-1-2-6a6 6 0 0 0-6-6z" />
      <path d="M10.5 20a2 2 0 0 0 3 0" />
    </template>
    <!-- 历史试验：归档 -->
    <template v-else-if="name === 'history'">
      <rect x="3" y="4" width="18" height="5" rx="1" />
      <path d="M5 9v10a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V9" />
      <path d="M10 13h4" />
    </template>
    <template v-else-if="name === 'recipes'"><path d="M8 5h12M8 12h12M8 19h12" /><circle cx="3" cy="5" r="1" /><circle cx="3" cy="12" r="1" /><circle cx="3" cy="19" r="1" /></template>
    <!-- 参数配置 -->
    <template v-else-if="name === 'parameters'">
      <path d="M4 7h10" /><path d="M18 7h2" /><circle cx="16" cy="7" r="2" />
      <path d="M4 17h4" /><path d="M12 17h8" /><circle cx="10" cy="17" r="2" />
    </template>
    <!-- 设备诊断 -->
    <template v-else-if="name === 'diagnostics'">
      <path d="M3 12h4l2 5 4-12 2 7h6" />
    </template>
    <!-- 系统设置 -->
    <template v-else-if="name === 'settings'">
      <circle cx="12" cy="12" r="3" />
      <path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6l1.4 1.4M17 17l1.4 1.4M18.4 5.6L17 7M7 17l-1.4 1.4" />
    </template>
    <!-- 数据分析 -->
    <template v-else-if="name === 'analytics'">
      <path d="M4 20V10" /><path d="M10 20V4" /><path d="M16 20v-7" /><path d="M22 20H2" />
    </template>
    <!-- 报告生成 -->
    <template v-else-if="name === 'reports'">
      <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
      <path d="M14 3v5h5" />
      <path d="M9 13h6M9 17h4" />
    </template>
    <!-- 帮助 -->
    <template v-else-if="name === 'help'">
      <circle cx="12" cy="12" r="9" />
      <path d="M9.5 9.5a2.5 2.5 0 1 1 3.2 2.4c-.6.2-.9.7-.9 1.3v.3" />
      <path d="M12 17h.01" />
    </template>
  </svg>
</template>

<style scoped>
.nav-icon { flex: none; }
</style>
