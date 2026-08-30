<script setup>
// 帮助：安全须知、操作流程、角色权限、异常分级、常见操作指引。
import { PROCESS_STAGES } from '@/constants/processStages'
import { version as appVersion } from '../../package.json'

const SAFETY = [
  'CO 热态全程不得无人值守，现场配便携 CO 报警仪。',
  '室内 CO 不得超过 50 ppm（一级 25 ppm 报警，二级 50 ppm 联锁）。',
  '排风运行且风量正常、尾气通畅并保持负压后，方允许通 CO。',
  '急停、CO 二级报警、排风/超温/SCR/密封箱过压由安全继电器硬切，上位机不得绕过。',
  '异常即处置：先“关 CO、切 N₂ 置换、保持排风、停/降加热”，再排查。',
]

const ROLES = [
  ['Observer', '只读：实时数据、历史、报警'],
  ['Operator', 'Observer + 启动/停止/暂停/复位/确认报警/天平去皮'],
  ['Admin', 'Operator + 参数下发、校时、用户管理'],
  ['Maintainer', 'Admin + 设备诊断、HostComm 只读调试'],
]

const ALARM_LEVELS = [
  ['L2', 'CO 二级 / 排风故障 / 料层压差高 / 气路堵塞 → 关 CO、N₂ 置换，必要时停加热'],
  ['L3', '急停 / 独立超温 / SCR 故障 / 密封箱过压 → 切加热许可、关 CO 双切断阀、N₂ 应急置换、保持排风；复位后不得自动恢复'],
]

const TIPS = [
  ['启动试验', '当前试验页 → 输入试验编号 → 二次确认（CO 安全提示）→ 下发 start_test'],
  ['停止试验', '当前试验页 → 二次确认（CO 安全警告）→ 下发 stop_test（受控停止/吹扫）'],
  ['天平去皮', '承滴坩埚空载时，在 tare_allowed 状态下点击“天平去皮”'],
  ['确认报警', '报警事件页 → L3 置顶红色高亮 → Operator+ 点击“确认”（发 ack_alarm）'],
  ['参数下发', '参数配置页（Admin）→ 编辑 → 差异对比二次确认 → 下发（非运行态，CRC + 回读确认）'],
  ['报告/导出', '报告页或历史页 → 生成 HTML 报告 / 导出日志 zip（含曲线/事件/报警/参数 CSV）'],
]
</script>

<template>
  <div class="page">
    <h1 class="page-title">帮助</h1>

    <div class="grid">
      <div class="card danger">
        <div class="card-title">⚠ 安全须知（强制）</div>
        <ul>
          <li v-for="(s, i) in SAFETY" :key="i">{{ s }}</li>
        </ul>
      </div>

      <div class="card">
        <div class="card-title">角色权限</div>
        <table class="t">
          <tbody>
            <tr v-for="r in ROLES" :key="r[0]">
              <th>{{ r[0] }}</th>
              <td>{{ r[1] }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <div class="card">
      <div class="card-title">试验工艺流程</div>
      <ol class="flow">
        <li v-for="(s, i) in PROCESS_STAGES" :key="s.key">
          <span class="seq">{{ i + 1 }}</span>
          <span class="name">{{ s.label }}<span v-if="s.co" class="co">CO</span></span>
          <span class="desc muted">{{ s.desc }}</span>
        </li>
      </ol>
    </div>

    <div class="grid">
      <div class="card">
        <div class="card-title">异常分级处置（SOP §12）</div>
        <table class="t">
          <tbody>
            <tr v-for="a in ALARM_LEVELS" :key="a[0]">
              <th :class="a[0] === 'L3' ? 'l3' : 'l2'">{{ a[0] }}</th>
              <td>{{ a[1] }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="card">
        <div class="card-title">常见操作</div>
        <table class="t">
          <tbody>
            <tr v-for="t in TIPS" :key="t[0]">
              <th>{{ t[0] }}</th>
              <td>{{ t[1] }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <div class="card about muted">
      smd-web-hmi 熔滴炉 Web 上位机 · 前端 v{{ appVersion }} · GB/T 34211 · D3 接口联调（v0.3-d3）。
      详细规格见 docs/；接口对齐待办见 docs/待确认事项与接口对齐清单。
    </div>
  </div>
</template>

<style scoped>
.page { display: flex; flex-direction: column; gap: 16px; }
.page-title { font-size: 18px; font-weight: 700; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; align-items: start; }
.card-title { font-weight: 700; margin-bottom: 10px; }
.card.danger { border-color: var(--red); }
.card.danger .card-title { color: var(--danger-text); }
ul { margin: 0; padding-left: 18px; line-height: 1.9; font-size: 13px; }
.t { width: 100%; border-collapse: collapse; font-size: 13px; }
.t th, .t td { text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--border); vertical-align: top; }
.t th { color: var(--text-sec); font-weight: 600; width: 110px; white-space: nowrap; }
.t th.l2 { color: var(--yellow); }
.t th.l3 { color: var(--red); }
.flow { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }
.flow li { display: grid; grid-template-columns: 28px 150px 1fr; align-items: center; gap: 10px; }
.seq { width: 24px; height: 24px; border-radius: 50%; display: grid; place-items: center; background: var(--accent-dim); color: var(--accent); font-weight: 700; font-size: 12px; }
.name { font-weight: 600; }
.co { display: inline-block; margin-left: 6px; font-size: 9px; font-weight: 700; background: var(--orange); color: var(--on-orange); border-radius: 3px; padding: 0 4px; }
.desc { font-size: 12px; line-height: 1.5; }
.about { font-size: 12px; line-height: 1.6; }
@media (max-width: 1000px) { .grid { grid-template-columns: 1fr; } .flow li { grid-template-columns: 28px 1fr; } .flow .desc { grid-column: 2; } }
</style>
