<script setup>
import { apiErrorMessage } from '@/api/errors'
import { appCompatibility } from '@/utils/appVersion'
import { computed, ref } from 'vue'
import { fetchStandardTemplate, saveRecipe, validateRecipe, activateRecipe } from '@/api/recipes'
import { useDeviceStore } from '@/stores/device'
import { useRole } from '@/composables/useRole'
import RecipePicker from '@/components/recipes/RecipePicker.vue'
import RecipeStageEditor from '@/components/recipes/RecipeStageEditor.vue'
import ConfirmDialog from '@/components/shared/ConfirmDialog.vue'
import OperationResult from '@/components/command/OperationResult.vue'
const { hasRole } = useRole()
const device = useDeviceStore()
const admin = computed(() => hasRole('admin'))
const picker = ref(null),
  saved = ref(null),
  draft = ref(null),
  original = ref('')
const busy = ref(false),
  message = ref(''),
  verdict = ref(null),
  confirming = ref(false),
  operationId = ref(null)
const dirty = computed(() => draft.value && JSON.stringify(draft.value) !== original.value)
const locked = computed(() => appCompatibility.mismatch || busy.value || Boolean(operationId.value))
const canActivate = computed(
  () =>
    admin.value &&
    saved.value &&
    !dirty.value &&
    verdict.value?.executable &&
    device.canActivateRecipe &&
    !locked.value,
)
function describe(error) {
  return apiErrorMessage(error)
}
function select(row) {
  saved.value = row
  draft.value = row ? JSON.parse(JSON.stringify(row.definition)) : null
  original.value = JSON.stringify(draft.value)
  verdict.value = null
  message.value = ''
}
async function newTemplate() {
  busy.value = true
  try {
    const { definition } = await fetchStandardTemplate()
    select(null)
    draft.value = definition
    original.value = ''
  } catch (error) {
    message.value = describe(error)
  } finally {
    busy.value = false
  }
}
function discard() {
  select(saved.value)
}
function changeStage(index, stage) {
  draft.value.mode = 'custom'
  draft.value.stages[index] = stage
  verdict.value = null
}
function removeStage(index) {
  draft.value.mode = 'custom'
  draft.value.stages.splice(index, 1)
  verdict.value = null
}
function moveStage(index, delta) {
  const stages = draft.value.stages
  const [stage] = stages.splice(index, 1)
  stages.splice(index + delta, 0, stage)
  draft.value.mode = 'custom'
  verdict.value = null
}
function addStage() {
  draft.value.mode = 'custom'
  verdict.value = null
  draft.value.stages.push({
    name: '新阶段',
    kind: 'hold',
    furnace_target_c: 500,
    ramp_c_min: null,
    n2_l_min: 5,
    co_l_min: 0,
    exit: { signal: 'elapsed_s', comparison: 'gte', value: 1800 },
    timeout_s: 3600,
  })
}
async function save() {
  if (locked.value || !admin.value) return
  busy.value = true
  message.value = ''
  try {
    const row = await saveRecipe(draft.value, saved.value?.recipe_id)
    select(row)
    message.value = `已保存 v${row.version}，尚未下发设备`
    await picker.value?.reload()
  } catch (error) {
    message.value = describe(error)
  } finally {
    busy.value = false
  }
}
async function validate() {
  busy.value = true
  verdict.value = null
  message.value = ''
  try {
    verdict.value = await validateRecipe(saved.value.recipe_id, saved.value.version)
  } catch (error) {
    message.value = describe(error)
  } finally {
    busy.value = false
  }
}
function resolved(result) {
  operationId.value = null
  confirming.value = false
  message.value =
    result.operation_status === 'verified' && result.readback_ok !== false
      ? '设备已回读一致；启动时仍会核对版本与现场许可。'
      : result.wire_reconciled && result.operation_status === 'unknown' ? '已核查，配方激活执行结果仍未知；请重新校验设备执行版本。' : `配方激活未成功：${result.reason_code || '设备拒绝'}`
}
async function activate() {
  if (!canActivate.value) return
  busy.value = true
  message.value = ''
  try {
    resolved(await activateRecipe(saved.value.recipe_id, saved.value.version))
  } catch (error) {
    operationId.value = error.outcomeUnknown ? error.operationId : null
    message.value = describe(error)
  } finally {
    busy.value = false
    if (!operationId.value) confirming.value = false
  }
}
</script>
<template>
  <div class="page">
    <h1>实验配方</h1>
    <p class="muted">
      配方按顺序执行阶段。保存生成新版本；设备的安全上限和联锁独立生效。标准候选模板须完成条款争议确认及现场验收。
    </p>
    <div class="card selection">
      <RecipePicker
        ref="picker"
        :disabled="locked || Boolean(dirty)"
        @select="select"
      /><button
        v-if="admin"
        :disabled="locked || Boolean(dirty)"
        @click="newTemplate"
      >
        从标准候选模板新建
      </button>
    </div>
    <p
      v-if="message"
      class="message"
      role="status"
    >
      {{ message }}
    </p>
    <form
      v-if="draft"
      class="card editor"
      @submit.prevent="save"
    >
      <div class="identity">
        <strong>{{ saved ? `v${saved.version} 的工作副本` : '新配方' }}</strong
        ><span>{{ draft.mode === 'standard' ? '标准候选模板' : '非标实验' }}</span
        ><span v-if="dirty">有未保存修改</span>
      </div>
      <p
        v-if="saved"
        class="digest mono"
      >
        配方 {{ saved.recipe_id }} · 摘要 {{ saved.digest }}
      </p>
      <fieldset
        :disabled="!admin || locked"
        class="metadata"
      >
        <label
          >配方名称<input
            v-model="draft.name"
            maxlength="100"
            required
        /></label>
        <label
          >说明<textarea
            v-model="draft.description"
            maxlength="2000"
            rows="2"
          />
        </label>
      </fieldset>
      <p class="muted">修改标准模板的阶段会生成非标版本；偏离项由后台计算，并随实验快照归档。</p>
      <RecipeStageEditor
        v-for="(stage, index) in draft.stages"
        :key="index"
        :stage="stage"
        :index="index"
        :count="draft.stages.length"
        :disabled="!admin || locked"
        @change="changeStage(index, $event)"
        @remove="removeStage(index)"
        @move="moveStage(index, $event)"
      />
      <div class="actions">
        <button
          v-if="admin"
          type="button"
          :disabled="locked || draft.stages.length >= 64"
          @click="addStage"
        >
          添加阶段
        </button>
        <button
          v-if="dirty"
          type="button"
          :disabled="locked"
          @click="discard"
        >
          放弃工作副本修改
        </button>
        <button
          v-if="admin"
          class="primary"
          type="submit"
          :disabled="locked || !dirty"
        >
          {{ saved ? '保存为新版本' : '保存配方' }}
        </button>
        <button
          type="button"
          :disabled="locked || !saved || Boolean(dirty)"
          @click="validate"
        >
          校验设备能力与安全约束
        </button>
        <button
          v-if="admin"
          type="button"
          :disabled="!canActivate"
          @click="confirming = true"
        >
          下发并回读配方
        </button>
      </div>
      <div
        v-if="verdict && !dirty"
        class="validation"
        role="status"
      >
        <strong>{{
          verdict.executable ? '设备校验通过，尚需下发并回读' : '设备校验未通过'
        }}</strong>
        <ul v-if="verdict.errors?.length">
          <li
            v-for="item in verdict.errors"
            :key="item"
          >
            {{ item }}
          </li>
        </ul>
        <p>
          安全配置版本：{{ verdict.safety_profile_version || '未知' }} · 判定依据：{{
            verdict.rules_reference || '未确认'
          }}
        </p>
        <p>标准偏离：{{ verdict.deviations?.join('；') || '无阶段偏离' }}</p>
      </div>
      <p
        v-if="!device.canActivateRecipe"
        class="muted"
      >
        设备当前不允许激活配方；需连接正常、数据新鲜并满足后台状态许可后才能下发。
      </p>
    </form>
    <ConfirmDialog
      v-model="confirming"
      title="下发配方版本"
      confirm-text="确认下发"
      :busy="busy"
      :confirm-disabled="!canActivate"
      :close-on-confirm="false"
      danger
      @confirm="activate"
    >
      <p>{{ saved?.definition.name }} · v{{ saved?.version }}</p>
      <p>
        请确认炉温、气氛及转换条件适用于本次实验。设备将原子保存该版本，并回读校验；运行期间使用不可变快照。
      </p>
      <p
        v-if="message"
        role="status"
      >
        {{ message }}
      </p>
      <OperationResult
        v-if="operationId"
        :operation-id="operationId"
        require-verified
        @resolved="resolved"
      />
    </ConfirmDialog>
    <OperationResult
      v-if="operationId && !confirming"
      :operation-id="operationId"
      require-verified
      @resolved="resolved"
    />
  </div>
</template>
<style scoped>
.editor {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
h1 {
  font-size: var(--fs-title);
}
.selection {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 16px;
  align-items: start;
}
.identity,
.actions {
  display: flex;
  gap: 12px;
  align-items: center;
  flex-wrap: wrap;
}
.identity span {
  color: var(--text-sec);
}
.digest {
  overflow-wrap: anywhere;
  font-size: var(--fs-base);
}
.metadata {
  display: grid;
  gap: 12px;
  border: 0;
  padding: 0;
  min-width: 0;
}
label {
  display: flex;
  flex-direction: column;
  gap: 6px;
  color: var(--text-sec);
  font-size: var(--fs-base);
}
.message,
.validation {
  background: var(--accent-dim);
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: 6px;
  overflow-wrap: anywhere;
}
.validation p {
  margin-top: 8px;
}
ul {
  padding-left: 20px;
  margin-top: 8px;
}
p {
  line-height: 1.6;
}
@media (max-width: 700px) {
  .selection {
    grid-template-columns: 1fr;
  }
}
</style>
