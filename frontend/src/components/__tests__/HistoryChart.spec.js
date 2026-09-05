import { afterEach, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import HistoryChart from '@/components/charts/HistoryChart.vue'

const plots = vi.hoisted(() => [])
vi.mock('chart.js', () => {
  class Chart {
    static register() {}
    constructor(_canvas, config) {
      this.data = config.data
      this.options = config.options
      this.update = vi.fn()
      this.destroy = vi.fn()
      plots.push(this)
    }
  }
  return {
    Chart,
    LineController: {},
    LineElement: {},
    PointElement: {},
    LinearScale: {},
    CategoryScale: {},
    Tooltip: {},
    Legend: {},
  }
})
afterEach(() => {
  plots.length = 0
})

it('keeps pressure, displacement and weight on separate unit-specific plots with aligned times', async () => {
  const points = [
    {
      ts: '2026-09-06T00:00:00Z',
      furnace_pv: 1200,
      burden_temp: 1190,
      delta_p: 25000,
      displacement: 0.02,
      drip_weight: 0.1,
    },
    {
      ts: '2026-09-06T00:00:01Z',
      furnace_pv: 1201,
      burden_temp: 1191,
      delta_p: 26000,
      displacement: null,
      drip_weight: 0.2,
    },
  ]
  const wrapper = mount(HistoryChart, { props: { points } })
  expect(plots).toHaveLength(4)
  expect(plots.map((plot) => plot.options.scales.y.title.text)).toEqual([
    '温度 (℃)',
    '压差 (Pa)',
    '位移 (mm)',
    '滴落重量 (g)',
  ])
  expect(plots.map((plot) => plot.data.datasets.length)).toEqual([2, 1, 1, 1])
  expect(plots[1].data.datasets[0].data).toEqual([25000, 26000])
  expect(plots[2].data.datasets[0].data).toEqual([0.02, null])
  expect(plots[3].data.datasets[0].data).toEqual([0.1, 0.2])
  expect(
    plots.every(
      (plot) => JSON.stringify(plot.data.labels) === JSON.stringify(plots[0].data.labels),
    ),
  ).toBe(true)
  expect(
    plots.every((plot) => plot.data.datasets.every((series) => series.spanGaps === false)),
  ).toBe(true)
  await wrapper.setProps({ points: [points[1]] })
  expect(plots).toHaveLength(4)
  expect(plots.every((plot) => plot.data.labels.length === 1)).toBe(true)
  wrapper.unmount()
  expect(plots.every((plot) => plot.destroy.mock.calls.length === 1)).toBe(true)
})
