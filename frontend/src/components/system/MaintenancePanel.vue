<script setup>
import { apiErrorMessage } from '@/api/errors'
import { computed, onMounted, ref } from 'vue'
import { fetchMaintenance } from '@/api/maintenance'

const state = ref(null)
const busy = ref(false)
const error = ref('')
const description = computed(
  () => ({
    idle: '当前无维护操作',
    prepared: '已有维护记录，请运行新版安装器继续或恢复',
    claimed: '安装器正在维护，请在本机完成安装或恢复',
  })[state.value?.state] || '维护状态未知',
)

async function refresh() {
  busy.value = true
  error.value = ''
  try {
    state.value = await fetchMaintenance()
  } catch (e) {
    error.value = apiErrorMessage(e, '读取维护状态失败')
  } finally {
    busy.value = false
  }
}
onMounted(refresh)
</script>

<template>
  <section class="card maintenance" aria-labelledby="maintenance-title">
    <h2 id="maintenance-title">版本与维护状态</h2>
    <p v-if="state?.current_version">当前版本：{{ state.current_version }}</p>
    <p>更新软件时，直接运行新版 Windows 安装器。安装器自动识别版本，保留数据库、配置和账号，无需在此填写版本号或准备升级。</p>
    <p>实验或冷却进行中不能安装。设备离线或状态未知时，由本机管理员在安装器中确认设备已物理停机。</p>
    <p role="status">{{ description }}</p>
    <p v-if="state?.target_version">维护目标版本：{{ state.target_version }}</p>
    <p v-if="error" role="alert" class="error">{{ error }}</p>
    <div><button :disabled="busy" @click="refresh">刷新状态</button></div>
  </section>
</template>

<style scoped>
.maintenance {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
h2 {
  font-size: 14px;
}
p {
  font-size: 12px;
  line-height: 1.6;
  color: var(--text-sec);
}
.error {
  color: var(--danger-text);
}
</style>
