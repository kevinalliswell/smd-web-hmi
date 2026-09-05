<script setup>
import { computed } from 'vue'
import { useTheme } from '@/composables/useTheme'

const { isDark, toggleTheme } = useTheme()
const label = computed(() => (isDark.value ? '切换到浅色主题' : '切换到深色主题'))
</script>

<template>
  <button
    class="theme-toggle"
    type="button"
    :aria-label="label"
    :title="label"
    :aria-pressed="String(!isDark)"
    @click="toggleTheme"
  >
    <svg v-if="isDark" aria-hidden="true" viewBox="0 0 24 24">
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.93 4.93l1.42 1.42M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.42-1.42M17.66 6.34l1.41-1.41" />
    </svg>
    <svg v-else aria-hidden="true" viewBox="0 0 24 24">
      <path d="M20.6 15.3A8.5 8.5 0 0 1 8.7 3.4 8.5 8.5 0 1 0 20.6 15.3Z" />
    </svg>
    <span class="sr-only">{{ label }}</span>
  </button>
</template>

<style scoped>
.theme-toggle {
  display: grid;
  width: 34px;
  height: 34px;
  place-items: center;
  padding: 0;
  flex: 0 0 auto;
}
.theme-toggle svg {
  width: 17px;
  height: 17px;
  fill: none;
  stroke: currentColor;
  stroke-linecap: round;
  stroke-linejoin: round;
  stroke-width: 1.8;
}
</style>
