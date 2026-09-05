<script setup>
import { storeToRefs } from 'pinia'
import { useDeviceStore } from '@/stores/device'
import { formatDateTime } from '@/utils/dateTime'

const device = useDeviceStore()
const { backendConnected, commQuality, lastUpdate } = storeToRefs(device)
</script>

<template>
  <footer id="footer">
    <span>后端：{{ backendConnected ? 'connected' : 'disconnected' }}</span>
    <span class="sep">·</span>
    <span>HostComm：{{ commQuality }}</span>
    <span class="sep">·</span>
    <span class="muted footer-detail">最近更新：{{ formatDateTime(lastUpdate) }}</span>
    <span class="spacer" />
    <span class="muted footer-detail">smd-web-hmi · D3</span>
  </footer>
</template>

<style scoped>
#footer {
  display: flex; align-items: center; gap: 8px; height: var(--footer-h); padding: 0 16px;
  background: var(--bg-card); border-top: 1px solid var(--border);
  font-size: 11px; color: var(--text-sec); flex-shrink: 0;
}
.spacer { flex: 1; }
.sep { color: var(--text-muted); }

@media (max-width: 600px) {
  #footer { padding-inline: 10px; gap: 6px; }
  .footer-detail { display: none; }
}
</style>
