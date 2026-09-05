<script setup>
import { apiErrorMessage } from '@/api/errors'
import { ref, watch } from 'vue'
import { evaluateRepeatability } from '@/api/analytics'
const props = defineProps({ testIds: { type: Array, required: true } })
const emit = defineEmits(['reorder'])
const busy = ref(false),
  result = ref(null),
  message = ref('')
let generation = 0
watch(
  () => props.testIds,
  () => {
    generation++
    result.value = null
    message.value = ''
  },
)
function move(index, delta) {
  const ids = [...props.testIds]
  const [id] = ids.splice(index, 1)
  ids.splice(index + delta, 0, id)
  emit('reorder', ids)
}
async function evaluate() {
  if (busy.value || props.testIds.length < 2 || props.testIds.length > 4) return
  const current = ++generation
  busy.value = true
  result.value = null
  message.value = ''
  try {
    const data = await evaluateRepeatability(props.testIds)
    if (current === generation) result.value = data
  } catch (error) {
    if (current === generation) message.value = apiErrorMessage(error, '重复性判定失败')
  } finally {
    busy.value = false
  }
}
const labels = { t10: 'T10', t40: 'T40', ts: 'Ts', td_drip_temp: 'Td' }
</script>
<template>
  <div class="card repeatability">
    <h2>重复试验判定 · 附录 B</h2>
    <p class="muted">
      选择 2–4
      次同样品、同配方的有效测定，按实际测定顺序排列。后台核查身份、完整性与指标后给出结果或补试要求。
    </p>
    <ol>
      <li
        v-for="(id, index) in testIds"
        :key="id"
      >
        <span>{{ id }}</span
        ><button
          :disabled="busy || index === 0"
          :aria-label="`${id} 提前`"
          @click="move(index, -1)"
        >
          ↑</button
        ><button
          :disabled="busy || index === testIds.length - 1"
          :aria-label="`${id} 延后`"
          @click="move(index, 1)"
        >
          ↓
        </button>
      </li>
    </ol>
    <button
      class="primary"
      :disabled="busy || testIds.length < 2 || testIds.length > 4"
      @click="evaluate"
    >
      按此顺序判定重复性
    </button>
    <p
      v-if="message"
      role="alert"
    >
      {{ message }}
    </p>
    <div
      v-if="result"
      role="status"
    >
      <strong>{{ result.eligible ? '输入记录通过资格核查' : '记录不满足判定条件' }}</strong>
      <ul v-if="result.errors?.length">
        <li
          v-for="item in result.errors"
          :key="item"
        >
          {{ item }}
        </li>
      </ul>
      <div class="table-wrap">
        <table v-if="result.results?.length">
          <thead>
            <tr>
              <th>指标</th>
              <th>判定结果 (℃)</th>
              <th>采用值 (℃)</th>
              <th>允许差 A/B/C (℃)</th>
              <th>规则版本</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="row in result.results"
              :key="row.metric"
            >
              <td>{{ labels[row.metric] || row.metric }}</td>
              <td>
                {{
                  row.additional_runs > 0
                    ? `需增加 ${row.additional_runs} 次测定`
                    : (row.result ?? '未知')
                }}
              </td>
              <td>{{ row.used_values?.join(' / ') || '尚未形成结果' }}</td>
              <td>{{ row.tolerances?.A }} / {{ row.tolerances?.B }} / {{ row.tolerances?.C }}</td>
              <td>{{ row.rules_version }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>
<style scoped>
.repeatability {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
}
h2 {
  font-size: 14px;
}
p {
  font-size: 12px;
  line-height: 1.6;
}
ol,
ul {
  padding-left: 24px;
}
ol li {
  margin: 8px 0;
  overflow-wrap: anywhere;
}
ol button {
  margin-left: 8px;
}
button {
  align-self: start;
}
.table-wrap {
  max-width: 100%;
  overflow: auto;
  margin-top: 12px;
}
table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}
th,
td {
  text-align: left;
  padding: 8px;
  border-bottom: 1px solid var(--border);
  min-width: 80px;
}
</style>
