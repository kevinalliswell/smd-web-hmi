function dateParts(value, { timeZone } = {}) {
  if (!value) return null
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return null
  const formatter = new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hourCycle: 'h23',
    ...(timeZone ? { timeZone } : {}),
  })
  return Object.fromEntries(
    formatter
      .formatToParts(date)
      .filter((part) => part.type !== 'literal')
      .map((part) => [part.type, part.value]),
  )
}

export function formatDateTime(value, options) {
  const parts = dateParts(value, options)
  if (!parts) return value || '—'
  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second}`
}

export function formatMonthDayTime(value, options) {
  const parts = dateParts(value, options)
  if (!parts) return value || '—'
  return `${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second}`
}

export function formatTime(value, options) {
  const parts = dateParts(value, options)
  if (!parts) return value || '—'
  return `${parts.hour}:${parts.minute}:${parts.second}`
}
