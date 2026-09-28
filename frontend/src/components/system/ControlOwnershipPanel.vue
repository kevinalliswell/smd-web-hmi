<script setup>
import { apiErrorMessage } from '@/api/errors'
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import { useAuthStore } from '@/stores/auth'
import { fetchControlOwner, claimControl, releaseControl } from '@/api/control'
import ConfirmDialog from '@/components/shared/ConfirmDialog.vue'

const auth = useAuthStore()
const owner = ref(undefined)
const error = ref('')
const busy = ref(false)
const confirming = ref(false)
const intent = ref('claim')
const reason = ref('')
const isMine = computed(() => owner.value === auth.username)
const takingOver = computed(() => intent.value === 'claim' && Boolean(owner.value) && !isMine.value)
let timer
let disposed = false

async function load() {
  try {
    const data = await fetchControlOwner()
    if (!disposed) owner.value = data.username
  } catch (e) {
    if (!disposed) error.value = apiErrorMessage(e, '无法读取控制权，请刷新重试')
  }
}
function open(action) {
  intent.value = action
  reason.value = ''
  confirming.value = true
  error.value = ''
}
async function confirm() {
  if (busy.value || (takingOver.value && !reason.value.trim())) return
  busy.value = true
  try {
    const data =
      intent.value === 'release'
        ? await releaseControl()
        : await claimControl({ takeover: takingOver.value, reason: reason.value.trim() })
    owner.value = data.username
    confirming.value = false
  } catch (e) {
    error.value = apiErrorMessage(e, '控制权操作失败')
  } finally {
    busy.value = false
  }
}
onMounted(() => {
  load()
  timer = setInterval(() => {
    if (!confirming.value && !busy.value) load()
  }, 5000)
})
onBeforeUnmount(() => {
  disposed = true
  clearInterval(timer)
})
</script>
<template>
  <section
    class="card control-owner"
    aria-labelledby="control-owner-title"
  >
    <h2 id="control-owner-title">设备控制权</h2>
    <p role="status">当前控制者：{{ owner === undefined ? '未知' : owner || '尚未领取' }}</p>
    <p class="muted">
      多个界面共享同一后台。首个写请求自动领取控制权；受控停止仍向其他有操作权限的用户开放。
    </p>
    <p
      v-if="error"
      role="alert"
      class="error"
    >
      {{ error }}
    </p>
    <div class="owner-actions">
      <button
        :disabled="busy"
        @click="load"
      >
        刷新控制者
      </button>
      <button
        v-if="owner === null"
        :disabled="busy"
        @click="open('claim')"
      >
        领取控制权
      </button>
      <button
        v-if="owner && !isMine && auth.canAdmin"
        class="danger"
        :disabled="busy"
        @click="open('claim')"
      >
        接管控制权
      </button>
      <button
        v-if="isMine"
        :disabled="busy"
        @click="open('release')"
      >
        释放控制权
      </button>
    </div>
    <ConfirmDialog
      v-model="confirming"
      :title="intent === 'release' ? '释放控制权' : takingOver ? '接管控制权' : '领取控制权'"
      danger
      :busy="busy"
      :confirm-disabled="takingOver && !reason.trim()"
      :close-on-confirm="false"
      @confirm="confirm"
    >
      <p v-if="intent === 'release'">后台须确认设备新鲜、空闲且没有未闭合实验，才能释放控制权。</p>
      <template v-else-if="takingOver">
        <p>接管 {{ owner }} 的控制权会记录操作者与原因。请确认已完成现场交接。</p>
        <label for="control-takeover-reason">接管原因</label>
        <textarea
          id="control-takeover-reason"
          v-model="reason"
          maxlength="1000"
          rows="3"
        />
      </template>
      <p v-else>领取后由当前账户协调设备控制操作，设备联锁仍有最终裁决权。</p>
      <p
        v-if="error"
        role="alert"
        class="error"
      >
        {{ error }}
      </p>
    </ConfirmDialog>
  </section>
</template>
<style scoped>
.control-owner {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
h2 {
  font-size: var(--fs-lg);
}
p,
label {
  font-size: var(--fs-base);
  line-height: 1.6;
}
.owner-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}
.error {
  color: var(--danger-text);
}
textarea {
  display: block;
  width: 100%;
  margin-top: 8px;
  resize: vertical;
}
</style>
