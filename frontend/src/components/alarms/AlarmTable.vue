<script setup>
// 报警表：可用于活跃报警（带确认按钮）或历史报警。L3 行红色高亮。
import AlarmBadge from './AlarmBadge.vue'
import { formatDateTime } from '@/utils/dateTime'

defineProps({
  alarms: { type: Array, default: () => [] },
  showAck: { type: Boolean, default: false },
  canAck: { type: Boolean, default: false },
  showClear: { type: Boolean, default: false }, // 历史表显示消除时间列
})
const emit = defineEmits(['ack'])
</script>

<template>
  <!-- 空表由调用方渲染 EmptyState：报警页的"空"是安全信息，需要肯定表述 -->
  <table v-if="alarms.length" class="alarm-table">
    <thead>
      <tr>
        <th style="width: 56px">等级</th>
        <th style="width: 140px">报警码</th>
        <th>说明</th>
        <th style="width: 170px">发生时间</th>
        <th v-if="showClear" style="width: 170px">消除时间</th>
        <th v-if="showAck" style="width: 130px">确认</th>
      </tr>
    </thead>
    <tbody>
      <tr v-for="a in alarms" :key="a.alarm_id ?? a.id ?? a.alarm_code" :class="{ critical: (a.level ?? 0) >= 3 }">
        <td><AlarmBadge :level="a.level ?? 0" /></td>
        <td class="mono">{{ a.alarm_code }}</td>
        <td>{{ a.text }}</td>
        <td class="mono small">{{ formatDateTime(a.occur_time) }}</td>
        <td v-if="showClear" class="mono small">{{ formatDateTime(a.clear_time) }}</td>
        <td v-if="showAck">
          <span v-if="a.ack_time" class="acked muted">✓ {{ a.ack_operator }}</span>
          <button v-else-if="canAck" class="ack-btn" @click="emit('ack', a)">确认</button>
          <span v-else class="muted">未确认</span>
        </td>
      </tr>
    </tbody>
  </table>
</template>

<style scoped>
.alarm-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.alarm-table th, .alarm-table td { text-align: left; padding: 7px 8px; border-bottom: 1px solid var(--border); }
.alarm-table th { color: var(--text-sec); font-weight: 600; font-size: 11px; }
.alarm-table tr.critical td { background: rgba(239, 68, 68, 0.08); }
.small { font-size: 11px; color: var(--text-sec); }
.empty { text-align: center; padding: 20px; }
.acked { font-size: 11px; }
.ack-btn { padding: 3px 12px; font-size: 12px; }
</style>
