<script setup>
import { computed, onMounted, ref } from 'vue'
import { fetchMaintenance, prepareMaintenance, cancelMaintenance } from '@/api/maintenance'
import ConfirmDialog from '@/components/shared/ConfirmDialog.vue'

const targetVersion = ref('')
const state = ref(null)
const busy = ref(false)
const error = ref('')
const confirming = ref(false)
const versionValid = computed(() => /^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$/.test(targetVersion.value.trim()))
const description = computed(() => ({
  idle: '未进入维护', prepared: '已准备升级，等待本机安装器', claimed: '本机安装器已领取，须在本机完成升级或恢复',
})[state.value?.state] || '维护状态未知')

async function run(action) {
  busy.value = true
  error.value = ''
  try { state.value = await action() }
  catch (e) {
    const detail = e.response?.data?.detail
    error.value = (typeof detail === 'string' ? detail : detail?.message) || e.message || '维护操作失败'
  } finally { busy.value = false; confirming.value = false }
}

onMounted(() => run(fetchMaintenance))
</script>

<template>
  <section class="card maintenance" aria-labelledby="maintenance-title">
    <h2 id="maintenance-title">离线升级</h2>
    <p>准备升级会阻止新的控制操作。实验进行中、设备状态不明或存在待核查操作时，后台会拒绝准备。</p>
    <p>升级文件由现场管理员在本机安装器中选择和校验；此页面只准备或取消维护，不上传安装包。</p>
    <p role="status">{{ description }}</p>
    <p v-if="state?.target_version">目标版本：{{ state.target_version }}</p>
    <p v-if="error" role="alert" class="error">{{ error }}</p>
    <div class="maintenance-actions">
      <label for="upgrade-target-version">目标版本</label>
      <input id="upgrade-target-version" v-model="targetVersion" placeholder="0.3.0-rc.2" :disabled="busy || state?.state !== 'idle'" />
      <button :disabled="busy" @click="run(fetchMaintenance)">刷新状态</button>
      <button class="primary" :disabled="busy || !versionValid || state?.state !== 'idle'" @click="confirming = true">准备升级</button>
      <button :disabled="busy || state?.state !== 'prepared'" @click="run(cancelMaintenance)">取消准备</button>
    </div>
    <ConfirmDialog v-model="confirming" title="准备离线升级" confirm-text="确认准备" :busy="busy" :close-on-confirm="false" @confirm="run(() => prepareMaintenance(targetVersion.trim()))">
      准备升级到 {{ targetVersion }}。后台将再次检查设备空闲、数据新鲜及实验/操作已完成对账；通过后暂停接收新的控制请求。
    </ConfirmDialog>
  </section>
</template>

<style scoped>
.maintenance { display: flex; flex-direction: column; gap: 12px; }
h2 { font-size: 14px; }
p { font-size: 12px; line-height: 1.6; color: var(--text-sec); }
.error { color: var(--danger-text); }
.maintenance-actions { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
.maintenance-actions input { width: 150px; }
</style>
