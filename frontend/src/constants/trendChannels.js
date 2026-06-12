// 趋势/曲线通道定义：温度走左轴 yTemp，其余走右轴 yAux。
export const TREND_CHANNELS = [
  { key: 'furnace_pv', label: '炉温 (℃)', color: '#38bdf8', axis: 'yTemp' },
  { key: 'burden_temp', label: '料层温度 (℃)', color: '#a78bfa', axis: 'yTemp' },
  { key: 'delta_p', label: '压差 (Pa)', color: '#f59e0b', axis: 'yAux' },
  { key: 'displacement', label: '位移 (mm)', color: '#22c55e', axis: 'yAux' },
  { key: 'drip_weight', label: '滴落重量 (g)', color: '#ef4444', axis: 'yAux' },
  { key: 'n2_pv', label: 'N₂ (L/min)', color: '#60a5fa', axis: 'yAux' },
  { key: 'co_pv', label: 'CO (L/min)', color: '#fb923c', axis: 'yAux' },
]
