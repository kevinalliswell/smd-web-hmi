<script setup>
import { appCompatibility, frontendVersion } from '@/utils/appVersion'
function reload() {
  window.location.reload()
}
</script>
<template>
  <div
    v-if="appCompatibility.mismatch"
    role="alert"
    class="version-warning"
  >
    <div>
      <strong>页面与后台版本不一致，请刷新页面。</strong>
      <p>
        页面 {{ frontendVersion }} / 后台
        {{
          appCompatibility.backendVersion
        }}。已暂停新的控制、参数和配方修改；历史查询及停止试验仍可使用。刷新不会停止后台采集。
      </p>
    </div>
    <button @click="reload">刷新页面</button>
  </div>
</template>
<style scoped>
.version-warning {
  background: var(--yellow-dim);
  color: var(--warning-text);
  border: 1px solid var(--yellow);
  border-radius: 6px;
  padding: 12px;
  margin-bottom: 16px;
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
p {
  font-size: var(--fs-base);
  margin-top: 6px;
  line-height: 1.6;
}
button {
  flex-shrink: 0;
}
</style>
