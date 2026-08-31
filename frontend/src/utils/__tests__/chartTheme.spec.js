import { beforeEach, describe, expect, it, vi } from 'vitest'

import { applyChartTheme, getChartTheme } from '@/utils/chartTheme'

describe('chartTheme', () => {
  beforeEach(() => {
    document.documentElement.style.setProperty('--chart-grid', '#d5ddea')
    document.documentElement.style.setProperty('--chart-tick', '#526078')
    document.documentElement.style.setProperty('--accent', '#0369a1')
  })

  it('从当前 CSS 主题读取颜色', () => {
    expect(getChartTheme()).toEqual({
      grid: '#d5ddea',
      tick: '#526078',
      accent: '#0369a1',
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
})
