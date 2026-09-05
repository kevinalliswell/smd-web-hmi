<script setup>
import { onMounted, ref } from 'vue'
import { fetchRecipes, fetchRecipeVersions } from '@/api/recipes'
const props = defineProps({ disabled: Boolean })
const emit = defineEmits(['select'])
const items = ref([]),
  versions = ref([]),
  selectedId = ref(''),
  version = ref('')
const page = ref(1),
  total = ref(0),
  busy = ref(false),
  error = ref('')
let generation = 0
async function load() {
  busy.value = true
  error.value = ''
  try {
    const data = await fetchRecipes(page.value)
    items.value = data.items
    total.value = data.total
  } catch {
    error.value = '配方列表加载失败，请重试'
  } finally {
    busy.value = false
  }
}
async function selectRecipe() {
  const current = ++generation
  versions.value = []
  version.value = ''
  emit('select', null)
  if (!selectedId.value) return
  busy.value = true
  error.value = ''
  try {
    const rows = await fetchRecipeVersions(selectedId.value)
    if (current !== generation) return
    versions.value = rows
    version.value = rows[0]?.version || ''
    selectVersion()
  } catch {
    error.value = '配方版本加载失败，请重新选择'
  } finally {
    if (current === generation) busy.value = false
  }
}
function selectVersion() {
  emit('select', versions.value.find((row) => row.version === Number(version.value)) || null)
}
async function changePage(delta) {
  page.value += delta
  selectedId.value = ''
  selectRecipe()
  await load()
}
onMounted(load)
defineExpose({ reload: load })
</script>
<template>
  <div
    class="picker"
    :aria-busy="busy"
  >
    <label
      >已保存配方<select
        aria-label="已保存配方"
        v-model="selectedId"
        :disabled="props.disabled || busy"
        @change="selectRecipe"
      >
        <option value="">请选择配方</option>
        <option
          v-for="item in items"
          :key="item.recipe_id"
          :value="item.recipe_id"
        >
          {{ item.definition.name }}
        </option>
      </select></label
    >
    <label v-if="versions.length"
      >不可变版本<select
        aria-label="不可变版本"
        v-model="version"
        :disabled="props.disabled || busy"
        @change="selectVersion"
      >
        <option
          v-for="item in versions"
          :key="item.version"
          :value="item.version"
        >
          v{{ item.version }} · {{ item.definition.mode === 'standard' ? '标准模板' : '非标' }}
        </option>
      </select></label
    >
    <div class="pager">
      <button
        :disabled="props.disabled || busy || page === 1"
        @click="changePage(-1)"
      >
        上一页</button
      ><span>第 {{ page }} 页 · {{ total }} 项</span
      ><button
        :disabled="props.disabled || busy || page * 20 >= total"
        @click="changePage(1)"
      >
        下一页</button
      ><button
        :disabled="props.disabled || busy"
        @click="load"
      >
        刷新
      </button>
    </div>
    <p
      v-if="error"
      role="alert"
    >
      {{ error }}
    </p>
    <p
      v-else-if="!busy && !total"
      class="muted"
    >
      尚无已保存配方，可从标准候选模板新建。
    </p>
  </div>
</template>
<style scoped>
.picker,
label {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}
.picker {
  gap: 12px;
}
select {
  width: 100%;
}
label {
  color: var(--text-sec);
  font-size: 12px;
}
.pager {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
  font-size: 12px;
}
p[role='alert'] {
  color: var(--danger-text);
}
</style>
