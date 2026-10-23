const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

export async function waitForTask(fetchStatus, taskId, { intervalMs = 500, maxWaitMs = 900_000 } = {}) {
  const deadline = Date.now() + maxWaitMs
  while (Date.now() <= deadline) {
    const task = await fetchStatus(taskId)
    if (task.status === 'completed') return task.result || {}
    if (task.status === 'failed' || task.status === 'interrupted') {
      const error = new Error(task.message || (task.status === 'interrupted' ? '后台任务已中断，请核查后重试' : '后台任务执行失败'))
      throw Object.assign(error, { taskId, status: task.status })
    }
    await delay(intervalMs)
  }
  throw Object.assign(new Error('后台任务等待超时，请稍后刷新任务状态'), { taskId })
}
