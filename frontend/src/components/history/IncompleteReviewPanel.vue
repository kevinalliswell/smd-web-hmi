<script setup>
import { apiErrorMessage } from '@/api/errors'
import { computed, ref } from 'vue'
import { useDeviceStore } from '@/stores/device'
import { reviewCloseTest } from '@/api/tests'
import ConfirmDialog from '@/components/shared/ConfirmDialog.vue'
const props = defineProps({ test: { type: Object, required: true } })
const emit = defineEmits(['updated'])
const device = useDeviceStore()
const open = ref(false),
  physicalConfirmed = ref(false),
  reason = ref(''),
  busy = ref(false),
  message = ref('')
const safe = computed(
  () =>
    !props.test.measurement_basis?.v2 && !props.test.measurement_basis?.recovery &&
    device.isFresh &&
    device.operationState === 'idle' &&
    device.snapshot.measurement?.burden_temp_valid === true &&
    typeof device.snapshot.measurement?.burden_temp_deg_c === 'number' &&
    device.snapshot.measurement.burden_temp_deg_c < 200,
)
function openReview() {
  open.value = true
  physicalConfirmed.value = false
  reason.value = ''
  message.value = ''
}

async function close() {
  if (!safe.value || !physicalConfirmed.value || reason.value.trim().length < 5 || busy.value)
    return
  busy.value = true
  message.value = ''
  try {
    await reviewCloseTest(props.test.test_id, reason.value.trim())
    open.value = false
    emit('updated')
  } catch (error) {
    message.value = apiErrorMessage(error, '审核关闭失败')
  } finally {
    busy.value = false
  }
}
</script>
<template>
  <div
    v-if="!test.end_time"
    class="card review"
  >
    <strong>未闭合实验核查</strong>
    <p>
      仅在现场处置完成后，将无法正常对账的会话归档为“不完整”。此操作不会补造测定完成或有效实验结果。
    </p>
    <p v-if="test.measurement_basis?.v2 || test.measurement_basis?.recovery" class="muted">
      HostComm v2 必须恢复本次运行的板端安全完成边界，现场确认或其他运行的待机状态不能替代。
      <RouterLink to="/run-recoveries">打开运行恢复</RouterLink>
    </p>
    <p
      v-else-if="!safe"
      class="muted"
    >
      须设备在线、数据新鲜、处于待机，且有效料层温度低于 200℃。
    </p>
    <button
      :disabled="!safe"
      @click="openReview"
    >
      审核关闭为不完整记录
    </button>
    <ConfirmDialog
      v-model="open"
      title="审核关闭未闭合实验"
      confirm-text="归档为不完整"
      danger
      :busy="busy"
      :confirm-disabled="!safe || !physicalConfirmed || reason.trim().length < 5"
      :close-on-confirm="false"
      @confirm="close"
    >
      <p>
        试验 {{ test.test_id }} 将归档为不完整。测定完成时间与原始数据不因此补齐，审核原因会留痕。
      </p>
      <label class="confirmation"
        ><input
          v-model="physicalConfirmed"
          type="checkbox"
        />我已现场确认设备和气体处置安全</label
      >
      <label
        >核查过程与关闭原因<textarea
          v-model="reason"
          minlength="5"
          maxlength="2000"
          rows="3"
        />
      </label>
      <p
        v-if="message"
        role="alert"
      >
        {{ message }}
      </p>
    </ConfirmDialog>
  </div>
</template>
<style scoped>
.review {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
p {
  font-size: var(--fs-base);
  line-height: 1.6;
}
button {
  align-self: start;
}
label {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 12px;
}
.confirmation {
  flex-direction: row;
  align-items: center;
}
.confirmation input {
  width: auto;
}
</style>
