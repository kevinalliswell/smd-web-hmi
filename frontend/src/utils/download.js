import apiClient from '@/api/client'

// 通过带 JWT 的 axios 实例以 blob 下载文件并触发浏览器保存。
export async function downloadFile(url, filename) {
  const resp = await apiClient.get(url, { responseType: 'blob' })
  const blob = new Blob([resp.data])
  const href = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = href
  a.download = filename || 'download'
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(href)
}
