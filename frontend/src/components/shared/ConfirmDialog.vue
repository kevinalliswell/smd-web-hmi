<script setup>
// 通用二次确认组件。CO/安全相关操作必须走此组件，不得使用 window.confirm()。
defineProps({
  modelValue: { type: Boolean, default: false },
  title: { type: String, default: '确认操作' },
  message: { type: String, default: '' },
  confirmText: { type: String, default: '确认' },
  cancelText: { type: String, default: '取消' },
  danger: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue', 'confirm', 'cancel'])

function onConfirm() {
  emit('confirm')
  emit('update:modelValue', false)
}
function onCancel() {
  emit('cancel')
  emit('update:modelValue', false)
}
</script>

<template>
  <div v-if="modelValue" class="overlay" @click.self="onCancel">
    <div class="dialog" :class="{ danger }">
      <div class="dlg-title">
        <span v-if="danger" class="warn-icon">⚠</span>{{ title }}
      </div>
      <div class="dlg-body">
        <slot>{{ message }}</slot>
      </div>
      <div class="dlg-actions">
        <button @click="onCancel">{{ cancelText }}</button>
        <button :class="danger ? 'danger' : 'primary'" @click="onConfirm">{{ confirmText }}</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.overlay {
  position: fixed; inset: 0; background: rgba(0, 0, 0, 0.6);
  display: flex; align-items: center; justify-content: center; z-index: 100;
}
.dialog {
  background: var(--bg-card); border: 1px solid var(--border-hi); border-radius: 10px;
  width: 440px; max-width: 90vw; padding: 20px;
}
.dialog.danger { border-color: var(--red); }
.dlg-title { font-size: 16px; font-weight: 700; margin-bottom: 12px; display: flex; align-items: center; gap: 8px; }
.warn-icon { color: var(--red); }
.dlg-body { color: var(--text-sec); line-height: 1.6; margin-bottom: 20px; }
.dlg-actions { display: flex; justify-content: flex-end; gap: 10px; }
</style>
