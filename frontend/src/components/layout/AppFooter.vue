<script setup>
import { storeToRefs } from 'pinia'
import { useDeviceStore } from '@/stores/device'

const device = useDeviceStore()
const { backendConnected, commQuality, lastUpdate } = storeToRefs(device)
</script>

<template>
  <footer id="footer">
    <span>后端实时：{{ backendConnected ? 'connected' : 'disconnected' }}</span>
    <span class="sep">·</span>
    <span>HostComm：{{ commQuality }}</span>
    <span class="sep">·</span>
    <span class="muted">最近更新：{{ lastUpdate || '—' }}</span>
    <span class="spacer" />
    <span class="muted">smd-web-hmi · D3</span>
  </footer>
</template>

<style scoped>
#footer {
  display: flex; align-items: center; gap: 8px; height: 26px; padding: 0 16px;
  background: var(--bg-card); border-top: 1px solid var(--border);
  font-size: 11px; color: var(--text-sec); flex-shrink: 0;
}
.spacer { flex: 1; }
.sep { color: var(--text-muted); }
</style>
