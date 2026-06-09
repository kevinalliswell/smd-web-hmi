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

Chart.register(LineController, LineElement, PointElement, LinearScale, CategoryScale, Tooltip, Legend)

const GRID = 'rgba(138,146,170,0.12)'
const TICK = '#8892aa'

export function useChart() {
  let chart = null

  function create(canvas, datasets, options = {}) {
    chart = new Chart(canvas, {
      type: 'line',
      data: { labels: [], datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        interaction: { intersect: false, mode: 'index' },
        scales: {
          x: { grid: { color: GRID }, ticks: { color: TICK, maxTicksLimit: 8 } },
          y: { grid: { color: GRID }, ticks: { color: TICK } },
        },
        plugins: { legend: { labels: { color: TICK } } },
        ...options,
      },
    })
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
    chart?.destroy()
    chart = null
  }

  return { create, push, destroy }
}
