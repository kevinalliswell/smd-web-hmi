import { beforeEach, describe, expect, it, vi } from 'vitest'

import { applyChartTheme, getChartTheme, withAlpha } from '@/utils/chartTheme'

const LIGHT_SERIES = [
  '#2a78d6',
  '#1baf7a',
  '#374991',
  '#1e8db1',
  '#85284d',
  '#9c72de',
  '#6b46a0',
  '#b761b1',
]

describe('chartTheme', () => {
  beforeEach(() => {
    document.documentElement.style.setProperty('--chart-grid', '#d5ddea')
    document.documentElement.style.setProperty('--chart-tick', '#526078')
    document.documentElement.style.setProperty('--accent', '#0369a1')
    document.documentElement.style.setProperty('--orange', '#c2410c')
    document.documentElement.style.setProperty('--accent-soft', 'rgba(3, 105, 161, 0.08)')
    LIGHT_SERIES.forEach((color, i) =>
      document.documentElement.style.setProperty(`--series-${i + 1}`, color),
    )
  })

  it('从当前 CSS 主题读取颜色', () => {
    expect(getChartTheme()).toEqual({
      grid: '#d5ddea',
      tick: '#526078',
      accent: '#0369a1',
      series1: '#2a78d6',
      series2: '#1baf7a',
      series: LIGHT_SERIES,
      co: '#c2410c',
      accentSoft: 'rgba(3, 105, 161, 0.08)',
    })
  })

  it('切换主题后更新现有图表坐标轴与图例', () => {
    const chart = {
      options: {
        scales: {
          x: { grid: { color: 'old' }, ticks: { color: 'old' } },
          yTemp: {
            grid: { color: 'old' },
            ticks: { color: 'old' },
            title: { color: 'old' },
          },
        },
        plugins: { legend: { labels: { color: 'old' } } },
      },
      update: vi.fn(),
    }

    applyChartTheme(chart)

    expect(chart.options.scales.x.grid.color).toBe('#d5ddea')
    expect(chart.options.scales.x.ticks.color).toBe('#526078')
    expect(chart.options.scales.yTemp.ticks.color).toBe('#0369a1')
    expect(chart.options.plugins.legend.labels.color).toBe('#526078')
    expect(chart.update).toHaveBeenCalledWith('none')
  })

  it('换肤时按槽位重着色曲线，显式语义色保持不变', () => {
    const chart = {
      options: { scales: {}, plugins: { legend: { labels: { color: 'old' } } } },
      data: {
        datasets: [
          { seriesSlot: 0, borderColor: 'old' },
          { seriesSlot: 1, borderColor: 'old' },
          // 高槽位与带填充的数据集同样按槽换色,填充同步为同色淡化
          { seriesSlot: 3, borderColor: 'old', fill: true, backgroundColor: 'old' },
          // CO 语义数据集换肤时按 --orange 刷新(曲线与图例/通道标签保持同色)
          { seriesSemantic: 'co', borderColor: '#f97316' },
          // 无任何标记的裸显式颜色保持不变
          { borderColor: '#123456' },
        ],
      },
      update: vi.fn(),
    }

    applyChartTheme(chart)

    expect(chart.data.datasets[0].borderColor).toBe('#2a78d6')
    expect(chart.data.datasets[1].borderColor).toBe('#1baf7a')
    expect(chart.data.datasets[2].borderColor).toBe('#1e8db1')
    expect(chart.data.datasets[2].backgroundColor).toBe('rgba(30, 141, 177, 0.08)')
    expect(chart.data.datasets[3].borderColor).toBe('#c2410c')
    expect(chart.data.datasets[4].borderColor).toBe('#123456')
  })

  it('withAlpha 只转换 6 位 hex,其余原样返回', () => {
    expect(withAlpha('#3987e5', 0.08)).toBe('rgba(57, 135, 229, 0.08)')
    expect(withAlpha('rgba(1, 2, 3, 0.5)', 0.08)).toBe('rgba(1, 2, 3, 0.5)')
  })
})
