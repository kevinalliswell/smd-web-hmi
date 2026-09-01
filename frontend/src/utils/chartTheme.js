const FALLBACK = {
  grid: '#2a3150',
  tick: '#a0aac0',
  accent: '#38bdf8',
  series1: '#3987e5',
  series2: '#199e70',
}

export function getChartTheme() {
  const styles = getComputedStyle(document.documentElement)
  const token = (name, fallback) => styles.getPropertyValue(name).trim() || fallback
  return {
    grid: token('--chart-grid', FALLBACK.grid),
    tick: token('--chart-tick', FALLBACK.tick),
    accent: token('--accent', FALLBACK.accent),
    series1: token('--series-1', FALLBACK.series1),
    series2: token('--series-2', FALLBACK.series2),
  }
}

export function applyChartTheme(chart) {
  if (!chart?.options) return
  const colors = getChartTheme()

  Object.entries(chart.options.scales || {}).forEach(([name, scale]) => {
    if (scale.grid && scale.grid.drawOnChartArea !== false) scale.grid.color = colors.grid
    if (scale.ticks) scale.ticks.color = name === 'yTemp' ? colors.accent : colors.tick
    if (scale.title) scale.title.color = name === 'yTemp' ? colors.accent : colors.tick
  })
  const seriesColors = [colors.series1, colors.series2]
  ;(chart.data?.datasets || []).forEach((ds, i) => {
    // 仅刷新按系列槽着色的数据集；显式指定颜色的（如 CO 橙）保持不变
    if (ds.seriesSlot === undefined) return
    ds.borderColor = seriesColors[ds.seriesSlot % seriesColors.length]
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
