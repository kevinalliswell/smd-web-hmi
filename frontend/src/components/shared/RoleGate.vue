<script setup>
// 按角色显示/隐藏插槽内容。用法：<RoleGate role="operator">...</RoleGate>
import { useRole } from '@/composables/useRole'

const props = defineProps({
  role: { type: String, required: true },
})
const { hasRole } = useRole()
</script>

<template>
  <slot v-if="hasRole(props.role)" />
</template>
