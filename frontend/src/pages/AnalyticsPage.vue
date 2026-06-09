<script setup>
// 数据分析 —— D3 骨架占位页，后续迭代填充。
const title = '数据分析'
const desc = '多试验对比、关键指标统计与导出。'
</script>

<template>
  <div class="page">
    <div class="page-title">{{ title }}</div>
    <div class="card placeholder">
      <p>{{ desc }}</p>
      <p class="muted">D3 阶段骨架占位，功能将在后续迭代实现。</p>
    </div>
  </div>
</template>

<style scoped>
.page { display: flex; flex-direction: column; gap: 16px; }
.page-title { font-size: 18px; font-weight: 700; }
.placeholder { line-height: 1.8; }
.placeholder p { margin-bottom: 6px; }
</style>
