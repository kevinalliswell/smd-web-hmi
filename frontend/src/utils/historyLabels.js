// 只翻译历史页的显示语义；保留 API 原始字段供报告与审计追溯。
const phases = {
  awaiting_device: '等待设备受理',
  measuring: '测定中',
  safe_disposal: '置换与冷却中',
  stopping: '停止处理中（继续采集）',
  needs_review: '待核查',
  start_rejected: '启动未受理',
  completed: '全流程已归档',
  archived_incomplete: '已核查归档（记录不完整）',
}
const reasons = {
  completed: '自动流程结束',
  operator_stop: '操作员停止',
  safe_end_incomplete: '安全处置结束（测定未确认）',
  manual_review_incomplete: '人工核查关闭（记录不完整）',
}
const integrity = {
  complete: '数据记录完整',
  incomplete: '数据记录不完整',
  unknown: '数据完整性未确认',
}

function label(table, code, fallback) {
  return typeof code === 'string' && Object.hasOwn(table, code) ? table[code] : fallback
}
export const historyPhaseLabel = (code) => label(phases, code, '阶段未确认')
export const historyIntegrityLabel = (code) => label(integrity, code, '数据完整性未确认')
export function historyEndReasonLabel(code) {
  if (typeof code === 'string' && code.startsWith('start_rejected:')) return '启动未受理'
  return label(reasons, code, code ? '其他结束原因（待核查）' : '结束原因未记录')
}
