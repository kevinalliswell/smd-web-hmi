<script setup>
// 通用空状态。
//
// 工控界面里"没有内容"有两种含义，必须区分：
//   tone="ok"      —— 空即正常（无活跃报警）。这是安全信息，要肯定地说出来，
//                     否则操作员会怀疑是不是没加载出来。
//   tone="neutral" —— 尚无数据（还没做过试验、还没生成报告），中性陈述。
defineProps({
  tone: { type: String, default: 'neutral' }, // ok | neutral
  title: { type: String, required: true },
  hint: { type: String, default: '' },
})
</script>

<template>
  <div class="empty-state" :class="tone">
    <span class="mark" aria-hidden="true">
      <svg v-if="tone === 'ok'" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M20 6 9 17l-5-5" stroke-linecap="round" stroke-linejoin="round" />
      </svg>
      <svg v-else viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2">
        <rect x="3" y="5" width="18" height="14" rx="2" />
        <path d="M3 10h18" stroke-linecap="round" />
      </svg>
    </span>
    <p class="title">{{ title }}</p>
    <p v-if="hint" class="hint">{{ hint }}</p>
  </div>
</template>

<style scoped>
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 28px 16px;
  text-align: center;
}
.mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border-radius: 50%;
  margin-bottom: 2px;
}
.ok .mark { color: var(--green); background: var(--green-dim); }
.neutral .mark { color: var(--text-muted); background: var(--bg-card2); }
.title { font-size: 13px; font-weight: 600; color: var(--text-pri); }
.ok .title { color: var(--success-text); }
.hint { font-size: 12px; color: var(--text-muted); max-width: 42ch; }
</style>
