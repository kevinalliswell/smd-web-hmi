<script setup>
// 当前试验 —— D3 骨架占位页，后续迭代填充。
const title = '当前试验'
const desc = '状态机步骤指示、实时多通道折线图、启动/停止/去皮操作（均含二次确认）。'
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
