export function finiteValue(value) {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

export function measurementQuality(value, valid, stale = false) {
  if (finiteValue(value) === null) return { text: '暂无数据', tone: '' }
  if (stale) return { text: '数据过期', tone: 'warn' }
  if (valid === true || valid === 1) return { text: '有效', tone: '' }
  if (valid === false || valid === 0) return { text: '失效', tone: 'warn' }
  return { text: '质量未知', tone: '' }
}

export function liveSample(snapshot, stale = false) {
  const t = snapshot?.temperature || {}
  const m = snapshot?.measurement || {}
  const read = (value, valid) => stale || valid === false || valid === 0 ? null : finiteValue(value)
  return {
    furnace_pv: read(t.furnace_pv_deg_c, t.furnace_pv_valid),
    burden_temp: read(m.burden_temp_deg_c, m.burden_temp_valid),
    delta_p: read(m.delta_p_pa, m.delta_p_valid),
    displacement: read(m.displacement_mm, m.displacement_valid),
    drip_weight: read(m.drip_weight_g, m.drip_weight_valid),
  }
}
