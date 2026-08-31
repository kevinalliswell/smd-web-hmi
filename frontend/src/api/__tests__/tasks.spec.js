import { describe, expect, it } from 'vitest'
import { waitForTask } from '@/api/tasks'

describe('后台任务轮询', () => {
  it('等待 pending/running 并返回 completed 结果', async () => {
    const states = [
      { status: 'pending' },
      { status: 'running', progress: 10 },
      { status: 'completed', progress: 100, result: { id: 42 } },
    ]
    const result = await waitForTask(async () => states.shift(), 'task-id', { intervalMs: 0, maxWaitMs: 1000 })
    expect(result).toEqual({ id: 42 })
  })

  it('将 failed 状态转换为可展示错误', async () => {
    await expect(
      waitForTask(async () => ({ status: 'failed', message: '任务失败' }), 'task-id', {
        intervalMs: 0,
        maxWaitMs: 1000,
      }),
    ).rejects.toThrow('任务失败')
  })
})
