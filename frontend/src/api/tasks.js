const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

export async function waitForTask(fetchStatus, taskId, { intervalMs = 500, maxWaitMs = 900_000 } = {}) {
  const deadline = Date.now() + maxWaitMs
  while (Date.now() <= deadline) {
    const task = await fetchStatus(taskId)
    if (task.status === 'completed') return task.result || {}
    if (task.status === 'failed') throw new Error(task.message || '后台任务执行失败')
    await delay(intervalMs)
  }
  throw new Error('后台任务等待超时，请稍后刷新任务状态')
}
