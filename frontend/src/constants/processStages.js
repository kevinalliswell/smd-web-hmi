// 熔滴炉试验工艺阶段 —— 单一事实源（依据工艺流程图 + 试验SOP）。
//
// 来源：
//   docs 工艺流程图 子图 E「自动试验运行程序」
//   试验SOP §5 启动许可 / §7 气密性 / §8 N2保护 / §9 切气 / §10 升温记录 / §11 结束置换
//
// 说明：STM32 经 status_snapshot.state_machine.current_state 上报的状态字符串
// 命名未在协议中冻结，故每个阶段提供 aliases 做容错匹配；如与真实固件枚举不一致，
// 仅需在此处校正本表即可，UI 自动跟随。

export const PROCESS_STAGES = [
  {
    key: 'Standby',
    label: '待机/装样',
    desc: '装样：下焦 80 g + 铁矿石 500 g + 上焦 40 g；荷重 2 kg/cm²；测原始料层高度 H。',
    co: false,
    aliases: ['standby', 'idle', 'setup', 'load', 'ready'],
  },
  {
    key: 'Precheck',
    label: '启动许可检查',
    desc: '急停/排风/CO 报警/N2 压力/控温仪/MFC/安全继电器自检，全部满足方可升温。',
    co: false,
    aliases: ['precheck', 'prestart', 'permitcheck', 'permit', 'check'],
  },
  {
    key: 'LeakCheck',
    label: '气密性检查',
    desc: 'N2 5 L/min 下密封系统压差应 ≥ 20000 Pa，不合格禁止正式试验。',
    co: false,
    aliases: ['leakcheck', 'leak', 'airtight', 'tightness'],
  },
  {
    key: 'N2Purge',
    label: '升温前 N2 保护',
    desc: '炉温 < 500 ℃：N2 通气保护，CO MFC = 0，CO 工艺请求保持关闭，确认惰性气氛。',
    co: false,
    aliases: ['n2purge', 'purge', 'inert', 'protect', 'preheatpurge'],
  },
  {
    key: 'GasSwitch',
    label: '500 ℃ 切换还原气',
    desc: '炉温 500 ℃ 且安全许可满足：N2 3.5 + CO 1.5 L/min（CO 30% + N2 70%，总 5 L/min），开 CO 阀组。',
    co: true,
    aliases: ['gasswitch', 'switchco', 'reduce', 'reducing', 'cogas'],
  },
  {
    key: 'Heating',
    label: '程序升温',
    desc: '室温–900 ℃: 10 ℃/min；900–1100 ℃: 2 ℃/min；1100–1600 ℃: 5 ℃/min；自 600 ℃ 起连续记录。',
    co: true,
    aliases: ['heating', 'rampup', 'ramp', 'program', 'heat'],
  },
  {
    key: 'Hold1580',
    label: '1580 ℃ 保持',
    desc: '料层温度达 1580 ℃ 后继续保持 30 min 作为结束判据。',
    co: true,
    // 注意：不收录裸 'hold'，以免与"手动暂停/保持"特殊状态冲突
    aliases: ['hold1580', 'holding', 'soak', 'endhold', 'hightemphold'],
  },
  {
    key: 'N2Replace',
    label: '撤 CO · N2 置换',
    desc: '结束动作：CO MFC = 0，N2 MFC = 2 L/min，撤销 CO 请求并 N2 置换。',
    co: false,
    aliases: ['n2replace', 'replace', 'cooling', 'cool', 'endpurge', 'purge2'],
  },
  {
    key: 'End',
    label: '结束/冷却',
    desc: '料层温度 < 200 ℃ 提示可结束；保持 N2 与排风至炉体安全冷却。',
    co: false,
    aliases: ['end', 'done', 'finished', 'cooldown', 'complete'],
  },
]

// 非线性状态（不参与步骤条），用于叠加提示
export const SPECIAL_STATES = {
  fault: { label: '故障', level: 'danger' },
  purgefault: { label: '故障吹扫', level: 'danger' },
  hold: { label: '暂停/保持', level: 'warn' },
  pause: { label: '暂停/保持', level: 'warn' },
}

function normalize(state) {
  return String(state || '').toLowerCase().replace(/[\s_-]/g, '')
}

/**
 * 将 STM32 上报的状态字符串解析为工艺阶段。
 * @returns {{ index: number, stage: object|null, special: object|null }}
 */
export function resolveStage(currentState) {
  const n = normalize(currentState)
  if (!n) return { index: -1, stage: null, special: null }

  // 先精确匹配工艺阶段（避免 'Holding' 被特殊状态 'hold' 误吞）
  const index = PROCESS_STAGES.findIndex(
    (s) => normalize(s.key) === n || s.aliases.some((a) => normalize(a) === n),
  )
  if (index >= 0) return { index, stage: PROCESS_STAGES[index], special: null }

  // 再识别特殊状态（故障/手动暂停），按子串包含
  for (const [k, v] of Object.entries(SPECIAL_STATES)) {
    if (n.includes(k)) return { index: -1, stage: null, special: v }
  }

  return { index: -1, stage: null, special: null }
}
