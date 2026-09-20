<script setup>
import { storeToRefs } from 'pinia'
import { useDeviceStore } from '@/stores/device'
import { useAlarmsStore } from '@/stores/alarms'
import { formatDateTime } from '@/utils/dateTime'

const device = useDeviceStore()
const alarms = useAlarmsStore()
const { backendConnected, commQuality, lastUpdate, dataStale } = storeToRefs(device)
</script>

<template>
  <footer id="footer">
    <span>后端：{{ backendConnected ? 'connected' : 'disconnected' }}</span>
    <span class="sep">·</span>
    <span>HostComm：{{ commQuality }}</span>
    <span v-if="dataStale" role="status" class="freshness-warning">数据已过期 · 启动/改参已禁用</span>
    <span v-if="alarms.syncError" role="alert" class="freshness-warning">{{ alarms.syncError }}</span>
    <span class="sep">·</span>
    <span class="muted footer-detail">最近更新：{{ formatDateTime(lastUpdate) }}</span>
    <span class="spacer" />
    <span class="muted footer-detail">smd-web-hmi</span>
  </footer>
</template>

<style scoped>
#footer {
  display: flex; align-items: center; gap: 8px; height: var(--footer-h); padding: 0 16px;
  background: var(--bg-card); border-top: 1px solid var(--border);
  font-size: var(--fs-sm); color: var(--text-sec); flex-shrink: 0;
}
.freshness-warning { color: var(--warning-text); }
.spacer { flex: 1; }
.sep { color: var(--text-muted); }

@media (max-width: 600px) {
  #footer { padding-inline: 10px; gap: 6px; }
  .footer-detail { display: none; }
}
</style>
