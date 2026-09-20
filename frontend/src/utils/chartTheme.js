// 图表取色管道：所有图表颜色在渲染时从 CSS 令牌解析,换肤经 smd-theme-change 全自动刷新。
// 兜底值与 theme.css 深色令牌同步维护。
const FALLBACK = {
  grid: '#232c40',
  tick: '#9fabc4',
  accent: '#38bdf8',
  series: ['#3987e5', '#0f9b89', '#465ec4', '#32a0c5', '#9a5169', '#a57be8', '#73599e', '#ca6dc3'],
  co: '#f97316',
  accentSoft: 'rgba(56, 189, 248, 0.08)',
}

export function getChartTheme() {
  const styles = getComputedStyle(document.documentElement)
  const token = (name, fallback) => styles.getPropertyValue(name).trim() || fallback
  const series = FALLBACK.series.map((fallback, i) => token(`--series-${i + 1}`, fallback))
  return {
    grid: token('--chart-grid', FALLBACK.grid),
    tick: token('--chart-tick', FALLBACK.tick),
    accent: token('--accent', FALLBACK.accent),
    // 兼容既有调用:series1/2 与 series[0]/[1] 同值
    series1: series[0],
    series2: series[1],
    series,
    co: token('--orange', FALLBACK.co),
    accentSoft: token('--accent-soft', FALLBACK.accentSoft),
  }
}

// #rrggbb → rgba(r, g, b, alpha);非 6 位 hex 原样返回(令牌本身已带透明度时)
export function withAlpha(color, alpha) {
  if (!/^#[0-9a-fA-F]{6}$/.test(color)) return color
  const value = parseInt(color.slice(1), 16)
  return `rgba(${(value >> 16) & 255}, ${(value >> 8) & 255}, ${value & 255}, ${alpha})`
}

export function applyChartTheme(chart) {
  if (!chart?.options) return
  const colors = getChartTheme()

  Object.entries(chart.options.scales || {}).forEach(([name, scale]) => {
    if (scale.grid && scale.grid.drawOnChartArea !== false) scale.grid.color = colors.grid
    if (scale.ticks) scale.ticks.color = name === 'yTemp' ? colors.accent : colors.tick
    if (scale.title) scale.title.color = name === 'yTemp' ? colors.accent : colors.tick
  })
  const seriesColors = colors.series
  ;(chart.data?.datasets || []).forEach((ds) => {
    // 仅刷新按系列槽着色的数据集；显式指定颜色的（如 CO 橙）保持不变
    if (ds.seriesSlot === undefined) return
    const color = seriesColors[ds.seriesSlot % seriesColors.length]
    ds.borderColor = color
    // 带填充的槽位数据集(如实时炉温)同步刷新同色淡填充
    if (ds.fill) ds.backgroundColor = withAlpha(color, 0.08)
  })
  const legendLabels = chart.options.plugins?.legend?.labels
  if (legendLabels) legendLabels.color = colors.tick
  chart.update('none')
}

export function subscribeChartTheme(getChart) {
  const handler = () => applyChartTheme(getChart())
  window.addEventListener('smd-theme-change', handler)
  return () => window.removeEventListener('smd-theme-change', handler)
}
