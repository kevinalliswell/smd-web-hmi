import type { DeviceSnapshot, SignalFlag } from '../api/contracts'

export interface Metric { label: string; value: string; unit: string; detail: string; quality: string; warning: boolean; primary?: boolean }
const number = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)
export const formatMetric = (value: unknown, digits = 1): string => number(value) ? value.toFixed(digits) : '—'

export function overviewMetrics(snapshot: DeviceSnapshot, stale: boolean): Metric[] {
  const t = snapshot.temperature || {}, m = snapshot.measurement || {}, g = snapshot.gas || {}
  function metric(label: string, value: unknown, unit: string, valid: SignalFlag | undefined, detail = '', digits = 1, primary = false): Metric {
    const invalid = valid === false || valid === 0
    const quality = stale ? '数据过期' : invalid ? '数据失效' : !number(value) ? '暂无数据' : valid === true || valid === 1 ? '有效' : '质量未知'
    return { label, value: stale || invalid ? '—' : formatMetric(value, digits), unit, detail, quality,
      warning: stale || invalid, primary }
  }
  return [
    metric('炉温 PV', t.furnace_pv_deg_c, '℃', t.furnace_pv_valid, `SV ${stale ? '—' : formatMetric(t.furnace_sv_deg_c)} ℃`, 1, true),
    metric('料层温度', m.burden_temp_deg_c, '℃', m.burden_temp_valid),
    metric('滴落重量', m.drip_weight_g, 'g', m.drip_weight_valid,
      m.drip_weight_valid === true && !stale && typeof m.balance_stable === 'boolean' ? (m.balance_stable ? '天平稳定' : '天平波动') : '', 2, true),
    metric('压差', m.delta_p_pa, 'Pa', m.delta_p_valid),
    metric('位移', m.displacement_mm, 'mm', m.displacement_valid, '', 2),
    metric('N₂ 流量', g.n2_pv_l_min, 'L/min', undefined, `SP ${stale ? '—' : formatMetric(g.n2_sp_l_min, 2)}`, 2),
    metric('CO 流量', g.co_pv_l_min, 'L/min', undefined, `SP ${stale ? '—' : formatMetric(g.co_sp_l_min, 2)}`, 2),
  ]
}

/** A measurement boundary must never stand in for the independent safety boundary. */
export function completionLabel(snapshot: DeviceSnapshot, stale: boolean): string {
  if (stale) return '完成状态待核实'
  const s = snapshot.state_machine
  if (s?.measurement_complete === true && s.safe_complete === true) return '测定与安全均完成'
  if (s?.measurement_complete === true) return '测定完成 · 安全未完成'
  if (s?.safe_complete === true) return '安全完成 · 测定未完成'
  return '尚无完整结束依据'
}
