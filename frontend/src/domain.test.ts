import { describe, expect, it } from 'vitest'
import { displayError, employmentStatusLabel, eventLabel, isOperationalTaskAssignment, methodLabel, sortTasksForDisplay } from './domain'
import type { SharedTaskAssignment, Task } from './types'

describe('触达中心派生逻辑', () => {
  it('正式运营列表排除演示任务', () => {
    expect(isOperationalTaskAssignment({ source_mode: 'REAL' } as SharedTaskAssignment)).toBe(true)
    expect(isOperationalTaskAssignment({ source_mode: 'MOCK' } as SharedTaskAssignment)).toBe(false)
  })
})

describe('运营错误展示', () => {
  it('把任务安全门错误翻译成运营可读信息，同时保留调试代码', () => {
    expect(displayError({ body: { error_code: 'TEACHER_TIMEZONE_UNAVAILABLE', field_path: '$.teacher_id' } })).toBe(
      '缺少可信教师时区，任务未下发（TEACHER_TIMEZONE_UNAVAILABLE） · $.teacher_id',
    )
  })

  it('英文模式翻译框架文案并保留错误代码', () => {
    expect(displayError({ body: { error_code: 'TEACHER_TIMEZONE_UNAVAILABLE', field_path: '$.teacher_id' } }, 'en-US')).toBe(
      'The task was not assigned because a trusted teacher timezone is unavailable (TEACHER_TIMEZONE_UNAVAILABLE) · $.teacher_id',
    )
    expect(methodLabel('QUIZ', 'en-US')).toBe('Learning quiz')
    expect(eventLabel('ops_case.decided.v1', 'en-US')).toBe('Operations decision recorded')
  })
})

describe('教师在职状态展示', () => {
  it('统一翻译上游状态值，并把缺失值标为待确认', () => {
    expect(employmentStatusLabel('on')).toBe('在职')
    expect(employmentStatusLabel('off')).toBe('非在职')
    expect(employmentStatusLabel('hei')).toBe('已拉黑删除')
    expect(employmentStatusLabel('UNKNOWN')).toBe('状态待确认')
    expect(employmentStatusLabel(null)).toBe('状态待确认')
    expect(employmentStatusLabel('hei', 'en-US')).toBe('Blacklisted / removed')
    expect(employmentStatusLabel(null, 'en-US')).toBe('Status pending')
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
