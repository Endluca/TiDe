import { describe, expect, it } from 'vitest'
import { canRetryOutput, displayError, employmentStatusLabel, isOperationalOutput, isOperationalTaskAssignment, normalizeOutputList, outputDisplayTypeLabels, outputTypeLabels, sortTasksForDisplay, summarizeOutputs } from './domain'
import type { OutputRecord, SharedTaskAssignment, Task } from './types'

const output = {
  output_id: 'OUT-1',
  output_type: 'DELIVERY_INTENT',
  display_type: 'IN_APP_NOTIFICATION',
  audience_type: 'TEACHER',
  recipient_id: 'T-1001',
  recipient_name: 'Maria Santos',
  channel: 'WEBAPP_INBOX',
  source_type: 'TASK',
  source_id: 'TASK-1',
  status: 'FAILED',
  title: '任务提醒',
  created_at: '2026-07-17T00:00:00Z',
  attempt_count: 1,
  max_attempts: 3,
  retryable: true,
  requires_human_approval: false,
  payload: {},
} as OutputRecord

describe('输出中心派生逻辑', () => {
  it('正式运营列表排除调试输出和演示任务', () => {
    expect(isOperationalOutput(output)).toBe(true)
    expect(isOperationalOutput({ ...output, output_id: 'OUT-MOCK', title: '模拟通知' })).toBe(false)
    expect(isOperationalOutput({ ...output, output_id: 'OUT-PROVIDER', display_type: 'PROVIDER_REQUEST', non_business: true })).toBe(false)
    expect(isOperationalTaskAssignment({ source_mode: 'REAL' } as SharedTaskAssignment)).toBe(true)
    expect(isOperationalTaskAssignment({ source_mode: 'MOCK' } as SharedTaskAssignment)).toBe(false)
  })

  it('兼容数组列表并按类型、状态汇总', () => {
    expect(normalizeOutputList([output])).toEqual({ items: [output], total: 1 })
    expect(summarizeOutputs([output])).toMatchObject({
      total: 1,
      by_type: { DELIVERY_INTENT: 1 },
      by_display_type: { IN_APP_NOTIFICATION: 1 },
      by_status: { FAILED: 1 },
    })
    expect(outputTypeLabels.DELIVERY_INTENT).toBe('触达意图')
    expect(outputDisplayTypeLabels.IN_APP_NOTIFICATION).toBe('站内通知')
  })

  it('将 Provider 调用放在展示汇总，但不计入四类业务输出', () => {
    const provider = {
      ...output,
      output_id: 'OUT-DEBUG-1',
      output_type: 'SYSTEM_ACTION_REQUEST',
      display_type: 'PROVIDER_REQUEST',
      non_business: true,
    } as OutputRecord
    expect(summarizeOutputs([output, provider])).toMatchObject({
      total: 2,
      by_type: { DELIVERY_INTENT: 1 },
      by_display_type: { IN_APP_NOTIFICATION: 1, PROVIDER_REQUEST: 1 },
    })
  })

  it('只允许对尚未耗尽尝试次数的可重试失败输出重试', () => {
    expect(canRetryOutput(output)).toBe(true)
    expect(canRetryOutput({ ...output, status: 'REQUESTED' })).toBe(false)
    expect(canRetryOutput({ ...output, attempt_count: 3 })).toBe(false)
  })
})

describe('运营错误展示', () => {
  it('把任务安全门错误翻译成运营可读信息，同时保留调试代码', () => {
    expect(displayError({ body: { error_code: 'TEACHER_TIMEZONE_UNAVAILABLE', field_path: '$.teacher_id' } })).toBe(
      '缺少可信教师时区，任务未下发（TEACHER_TIMEZONE_UNAVAILABLE） · $.teacher_id',
    )
  })

})

describe('教师在职状态展示', () => {
  it('统一翻译上游状态值，并把缺失值标为待确认', () => {
    expect(employmentStatusLabel('on')).toBe('在职')
    expect(employmentStatusLabel('off')).toBe('非在职')
    expect(employmentStatusLabel('hei')).toBe('已拉黑删除')
    expect(employmentStatusLabel('UNKNOWN')).toBe('状态待确认')
    expect(employmentStatusLabel(null)).toBe('状态待确认')
  })
})

describe('任务展示排序', () => {
  it('按 teacher_id、display_rank、priority 排序且不修改原数组', () => {
    const tasks = [
      { task_id: 'TASK-T2', teacher_id: 'T-1002', display_rank: 1, priority: 'P0' },
      { task_id: 'TASK-R2', teacher_id: 'T-1001', display_rank: 2, priority: 'P0' },
      { task_id: 'TASK-P2', teacher_id: 'T-1001', display_rank: 1, priority: 'P2' },
      { task_id: 'TASK-P0', teacher_id: 'T-1001', display_rank: 1, priority: 'P0' },
      { task_id: 'TASK-P3', teacher_id: 'T-1001', display_rank: 1, priority: 'P3' },
    ] as Task[]
    const originalOrder = tasks.map((item) => item.task_id)

    expect(sortTasksForDisplay(tasks).map((item) => item.task_id)).toEqual([
      'TASK-P0',
      'TASK-P2',
      'TASK-P3',
      'TASK-R2',
      'TASK-T2',
    ])
    expect(tasks.map((item) => item.task_id)).toEqual(originalOrder)
  })
})
