// 趋势/曲线通道定义：温度走左轴 yTemp，其余走右轴 yAux。
// 颜色不再内联：slot 指向 --series-(slot+1) 槽位色，同一通道在趋势/历史/当前试验各页同槽同色；
// 槽位色经色觉安全校验，与报警状态色(green/yellow/red/orange)严格分离（见 theme.css）。
// CO 通道标记 semantic:'co'，使用 CO 专属橙且不参与槽位换肤。
export const TREND_CHANNELS = [
  { key: 'furnace_pv', label: '炉温 (℃)', slot: 0, axis: 'yTemp' },
  { key: 'burden_temp', label: '料层温度 (℃)', slot: 1, axis: 'yTemp' },
  { key: 'delta_p', label: '压差 (Pa)', slot: 2, axis: 'yAux' },
  { key: 'displacement', label: '位移 (mm)', slot: 3, axis: 'yAux' },
  { key: 'drip_weight', label: '滴落重量 (g)', slot: 4, axis: 'yAux' },
  { key: 'n2_pv', label: 'N₂ (L/min)', slot: 5, axis: 'yAux' },
  { key: 'co_pv', label: 'CO (L/min)', semantic: 'co', axis: 'yAux' },
]
