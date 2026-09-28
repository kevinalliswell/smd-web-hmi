<script setup>
// 工艺阶段步骤指示器：根据当前状态高亮阶段，CO 阶段标记安全色。
import { computed } from 'vue'
import { PROCESS_STAGES, resolveStage } from '@/constants/processStages'

const props = defineProps({
  currentState: { type: String, default: '' },
})

const resolved = computed(() => resolveStage(props.currentState))
const activeIndex = computed(() => resolved.value.index)

function statusOf(i) {
  if (activeIndex.value < 0) return 'pending'
  if (i < activeIndex.value) return 'done'
  if (i === activeIndex.value) return 'active'
  return 'pending'
}
</script>

<template>
  <div class="stepper">
    <div v-if="resolved.special" class="special" :class="resolved.special.level">
      ⚠ 当前状态：{{ resolved.special.label }}（{{ currentState }}）
    </div>
    <ol class="steps">
      <li
        v-for="(stage, i) in PROCESS_STAGES"
        :key="stage.key"
        class="step"
        :class="[statusOf(i), { co: stage.co }]"
        :title="stage.desc"
      >
        <span class="node">{{ i + 1 }}</span>
        <span class="label">
          {{ stage.label }}
          <span v-if="stage.co" class="co-tag">CO</span>
        </span>
      </li>
    </ol>
    <p v-if="activeIndex >= 0" class="stage-desc muted">
      {{ PROCESS_STAGES[activeIndex].desc }}
    </p>
  </div>
</template>

<style scoped>
.stepper { display: flex; flex-direction: column; gap: 10px; }
.special { padding: 8px 12px; border-radius: 6px; font-weight: 600; }
.special.danger { background: var(--red-dim); border: 1px solid var(--red); color: var(--danger-text); }
.special.warn { background: var(--yellow-dim); border: 1px solid var(--yellow); color: var(--warning-text); }
.steps {
  display: flex; list-style: none; gap: 4px; padding: 0; margin: 0;
  overflow-x: auto; counter-reset: step;
}
.step {
  flex: 1 1 0; min-width: 92px; display: flex; flex-direction: column; align-items: center;
  gap: 6px; padding: 8px 4px; border-radius: 6px; position: relative; text-align: center;
}
.step:not(:last-child)::after {
  content: ''; position: absolute; top: 22px; right: -2px; width: calc(100% - 32px);
  height: 2px; background: var(--border); transform: translateX(50%);
}
.node {
  width: 28px; height: 28px; border-radius: 50%; display: grid; place-items: center;
  border: 2px solid var(--border); background: var(--bg-card2); color: var(--text-sec);
  font-weight: 700; z-index: 1;
}
.label { font-size: var(--fs-sm); color: var(--text-sec); line-height: 1.3; }
.co-tag {
  display: inline-block; margin-left: 3px; font-size: var(--fs-2xs); font-weight: 700;
  background: var(--orange); color: var(--on-orange); border-radius: 3px; padding: 0 3px;
}
.step.done .node { border-color: var(--green); color: var(--green); }
.step.active .node { border-color: var(--accent); background: var(--accent-dim); color: var(--accent); box-shadow: 0 0 8px var(--accent); }
.step.active .label { color: var(--text-pri); font-weight: 600; }
.step.active.co .node { border-color: var(--orange); color: var(--orange); box-shadow: 0 0 8px var(--orange); }
.stage-desc { font-size: var(--fs-base); line-height: 1.6; }
</style>
