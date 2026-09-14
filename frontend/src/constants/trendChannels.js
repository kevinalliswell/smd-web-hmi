// 趋势/曲线通道定义。
//
// unit 决定通道画在哪张图上：同量纲的通道共用一张图与一套刻度，不同量纲各自成图。
// 原先除温度外的通道全部塞进同一个右轴（Pa / mm / g / L·min⁻¹ 共用一个刻度），
// 量级小的通道会被压成贴底直线而读不出变化。
//
// colorToken 只在有既有语义时指定（CO 橙与全局 CO 徽章一致）；其余留空，由
// MiniTrendChart 按 --series-* 槽位着色——那组颜色与状态色（报警红/正常绿）分离，
// 避免把数据线误读成报警状态。canvas 不解析 CSS 变量，故存 token 名由组件取值，
// 这样明暗主题切换时颜色也能跟着变。
export const TREND_CHANNELS = [
  { key: 'furnace_pv', label: '炉温', unit: '℃' },
  { key: 'burden_temp', label: '料层温度', unit: '℃' },
  { key: 'delta_p', label: '压差', unit: 'Pa' },
  { key: 'displacement', label: '位移', unit: 'mm' },
  { key: 'drip_weight', label: '滴落重量', unit: 'g' },
  { key: 'n2_pv', label: 'N₂ 流量', unit: 'L/min' },
  { key: 'co_pv', label: 'CO 流量', unit: 'L/min', colorToken: '--orange' },
]

// 图表标题：同量纲组内只有一个通道时用通道名，多个时合并
export function groupTitle(channels) {
  return channels.map((ch) => ch.label).join(' / ')
}
