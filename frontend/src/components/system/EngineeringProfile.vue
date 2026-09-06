<script setup>
import { computed } from 'vue'
const props = defineProps({ profile: { type: Object, default: null }, current: { type: Boolean, default: false } })
const rows = computed(() => {
  const p = props.profile, l = p?.limits, r = p?.resources
  if (!p || !l || !r) return []
  return [
    ['最高温度', l.temperature_max_mc / 1000, '℃'], ['最大升温速率', l.ramp_max_mc_per_min / 1000, '℃/min'],
    ['N₂ 最大流量', l.n2_max_ml_min / 1000, 'L/min'], ['CO 最大流量', l.co_max_ml_min / 1000, 'L/min'],
    ['允许 CO 的最低炉温', l.co_min_furnace_mc / 1000, '℃'], ['安全结束料层温度上限', l.safe_end_burden_mc / 1000, '℃'],
    ['最小 N₂ 流量', l.minimum_n2_ml_min / 1000, 'L/min'], ['置换时长', l.purge_duration_ms / 1000, 's'],
    ['冷却超时', l.cooling_timeout_ms / 1000, 's'], ['最高采样频率', r.max_sample_rate_millihz / 1000, 'Hz'],
    ['默认采样周期', r.default_sample_period_ms, 'ms'], ['源日志持久化', r.sample_log_durable ? '支持' : '不支持', ''],
  ]
})
</script>
<template>
  <section class="card profile" aria-labelledby="engineering-profile-title">
    <h2 id="engineering-profile-title">设备工程配置（只读）</h2>
    <p>工程约束由设备审批配置提供，配方编辑不能解除安全上限。</p>
    <p v-if="!profile" role="status">尚未取得工程配置；连接配对设备后刷新。</p>
    <template v-else>
      <p role="status">{{ profile.approved ? '设备声明已审批' : '尚未审批，禁止实验控制' }} · {{ current ? '当前连接快照' : '缓存证据，需重新连接核对' }}</p>
      <dl><dt>配置标识</dt><dd>{{ profile.profile_id }}</dd><dt>配置摘要</dt><dd class="mono">{{ profile.profile_digest }}</dd><dt>工程原件摘要</dt><dd class="mono">{{ profile.engineering_config_digest }}</dd><dt>判定规则</dt><dd>{{ profile.rules_reference || '未确认' }}</dd></dl>
      <table v-if="rows.length"><tbody><tr v-for="row in rows" :key="row[0]"><th scope="row">{{ row[0] }}</th><td>{{ row[1] }} {{ row[2] }}</td></tr></tbody></table>
      <p v-if="profile.gas_reference">气体流量基准：{{ profile.gas_reference.temperature_mk / 1000 }} K，{{ profile.gas_reference.pressure_pa }} Pa · {{ profile.gas_reference.source }}</p>
      <details><summary>查看完整工程配置及通道新鲜度约束</summary><pre>{{ JSON.stringify(profile, null, 2) }}</pre></details>
    </template>
  </section>
</template>
<style scoped>
.profile { display: flex; flex-direction: column; gap: 12px; }
h2 { font-size: 15px; }
p, dl, table, summary { font-size: 12px; line-height: 1.6; }
dl { display: grid; grid-template-columns: auto 1fr; gap: 8px 16px; }
dt, th { color: var(--text-sec); text-align: left; }
dd { margin: 0; overflow-wrap: anywhere; }
th, td { padding: 5px 8px; border-bottom: 1px solid var(--border); }
pre { white-space: pre-wrap; overflow-wrap: anywhere; font-size: 11px; padding-top: 12px; }
</style>
