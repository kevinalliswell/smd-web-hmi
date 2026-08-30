// Chart.js 轻封装：创建滚动折线图，提供 push/destroy。
import {
  Chart,
  LineController,
  LineElement,
  PointElement,
  LinearScale,
  CategoryScale,
  Tooltip,
  Legend,
} from 'chart.js'
import { getChartTheme, subscribeChartTheme } from '@/utils/chartTheme'

Chart.register(LineController, LineElement, PointElement, LinearScale, CategoryScale, Tooltip, Legend)

export function useChart() {
  let chart = null
  let unsubscribeTheme = null

  function create(canvas, datasets, options = {}) {
    const colors = getChartTheme()
    chart = new Chart(canvas, {
      type: 'line',
      data: { labels: [], datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        interaction: { intersect: false, mode: 'index' },
        scales: {
          x: { grid: { color: colors.grid }, ticks: { color: colors.tick, maxTicksLimit: 8 } },
          y: { grid: { color: colors.grid }, ticks: { color: colors.tick } },
        },
        plugins: { legend: { labels: { color: colors.tick } } },
        ...options,
      },
    })
    unsubscribeTheme?.()
    unsubscribeTheme = subscribeChartTheme(() => chart)
    return chart
  }

  // 追加一个时间点数据，保留最近 maxPoints 个
  function push(label, values, maxPoints = 120) {
    if (!chart) return
    chart.data.labels.push(label)
    values.forEach((v, i) => chart.data.datasets[i]?.data.push(v))
    if (chart.data.labels.length > maxPoints) {
      chart.data.labels.shift()
      chart.data.datasets.forEach((d) => d.data.shift())
    }
    chart.update('none')
  }

  function destroy() {
    unsubscribeTheme?.()
    unsubscribeTheme = null
    chart?.destroy()
    chart = null
  }

  return { create, push, destroy }
}
