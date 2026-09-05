import { beforeEach, describe, expect, it, vi } from 'vitest'

import { applyChartTheme, getChartTheme } from '@/utils/chartTheme'

describe('chartTheme', () => {
  beforeEach(() => {
    document.documentElement.style.setProperty('--chart-grid', '#d5ddea')
    document.documentElement.style.setProperty('--chart-tick', '#526078')
    document.documentElement.style.setProperty('--accent', '#0369a1')
    document.documentElement.style.setProperty('--series-1', '#2a78d6')
    document.documentElement.style.setProperty('--series-2', '#1baf7a')
  })

  it('从当前 CSS 主题读取颜色', () => {
    expect(getChartTheme()).toEqual({
      grid: '#d5ddea',
      tick: '#526078',
      accent: '#0369a1',
      series1: '#2a78d6',
      series2: '#1baf7a',
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
          // CO 橙等语义色不带 seriesSlot，不参与换肤重着色
          { borderColor: '#f97316' },
        ],
      },
      update: vi.fn(),
    }

    applyChartTheme(chart)

    expect(chart.data.datasets[0].borderColor).toBe('#2a78d6')
    expect(chart.data.datasets[1].borderColor).toBe('#1baf7a')
    expect(chart.data.datasets[2].borderColor).toBe('#f97316')
  })
})
