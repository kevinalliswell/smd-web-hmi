// 报警/事件等级定义（对应 event_log.level / alarm_log.level）。
// 0=Info,1=Notice,2=Warning(L2),3=Critical(L3)。SOP §12 以 L2/L3 分级处置。

export const ALARM_LEVELS = {
  0: { label: 'Info', short: 'I', color: 'var(--text-sec)', dim: 'var(--bg-card2)' },
  1: { label: 'L1', short: 'L1', color: 'var(--accent)', dim: 'var(--accent-dim)' },
  2: { label: 'L2', short: 'L2', color: 'var(--yellow)', dim: 'var(--yellow-dim)' },
  3: { label: 'L3', short: 'L3', color: 'var(--red)', dim: 'var(--red-dim)' },
}

export function levelMeta(level) {
  return ALARM_LEVELS[level] || ALARM_LEVELS[0]
}
