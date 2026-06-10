<script setup>
import { computed } from 'vue'
import { levelMeta } from '@/constants/alarmLevels'

const props = defineProps({
  level: { type: Number, default: 0 },
})
const meta = computed(() => levelMeta(props.level))
</script>

<template>
  <span class="lvl-badge" :style="{ color: meta.color, background: meta.dim, borderColor: meta.color }">
    {{ meta.label }}
  </span>
</template>

<style scoped>
.lvl-badge {
  display: inline-block; min-width: 28px; text-align: center; font-size: 11px;
  font-weight: 700; border: 1px solid; border-radius: 4px; padding: 1px 6px;
}
</style>
