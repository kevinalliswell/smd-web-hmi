// FastAPI统一错误是顶层error_code/message；同时兼容原生detail响应。
export function apiErrorDetails(error) {
  const body = error?.response?.data
  return body?.detail ?? body
}
export function apiErrorMessage(error, fallback = '请求失败') {
  const detail = apiErrorDetails(error)
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail))
    return detail
      .map(
        (item) =>
          `${item.location || item.loc?.slice(1).join('.') || '输入'}：${item.message || item.msg || '输入无效'}`,
      )
      .join('；')
  return detail?.message || error?.message || fallback
}
