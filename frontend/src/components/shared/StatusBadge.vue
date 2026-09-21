<script setup>
defineProps({
  ok: { type: Boolean, default: null },
  label: { type: String, default: '' },
  okText: { type: String, default: '正常' },
  failText: { type: String, default: '异常' },
})
</script>

<template>
  <div class="status-row">
    <span class="dot" :class="ok === null ? 'dot-gray' : ok ? 'dot-green' : 'dot-red'" />
    <span class="status-label">{{ label }}</span>
    <span class="status-val" :style="{ color: ok === null ? 'var(--text-muted)' : ok ? 'var(--green)' : 'var(--red)' }">
      {{ ok === null ? '未知 / 数据过期' : ok ? okText : failText }}
    </span>
  </div>
</template>

<style scoped>
.status-row {
  display: flex; align-items: center; gap: 9px; padding: 6px 10px;
  border-radius: var(--radius-lg); background: var(--bg-card2);
  border: 1px solid var(--border);
}
.status-label { color: var(--text-sec); }
.status-val { margin-left: auto; font-weight: 600; }
</style>
