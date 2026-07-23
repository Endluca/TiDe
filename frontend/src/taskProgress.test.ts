import { describe, expect, it } from 'vitest'
import {
  filterTaskProgressRows,
  normalizeTaskProgressItems,
  taskProgressKey,
} from './taskProgress'
import type { TaskProgressItem } from './types'

function progress(overrides: Partial<TaskProgressItem> = {}): TaskProgressItem {
  return {
    task_code: 'G01',
    title: '资料与资质完善',
    task_kind: 'FIXED_GROWTH',
    assigned_teacher_count: 2,
    assignment_count: 2,
    not_started: 1,
    in_progress: 0,
    completed: 1,
    other: 0,
    completion_rate: 0.5,
    ...overrides,
  }
}

describe('task progress rows', () => {
  it('uses code, title and kind as the row identity', () => {
    expect(taskProgressKey(progress())).not.toBe(taskProgressKey(progress({
      title: '资料与资质完善-另一标题',
    })))
  })

  it('preserves the backend assignment-based completion rate', () => {
    const [row] = normalizeTaskProgressItems([
      progress({
        assigned_teacher_count: 1,
        assignment_count: 2,
        completed: 1,
        completion_rate: 0.5,
      }),
    ])

    expect(row.completion_rate).toBe(0.5)
  })

  it('filters summary rows by lifecycle bucket without requiring raw assignments', () => {
    const rows = normalizeTaskProgressItems([
      progress(),
      progress({
        task_code: 'P-REL-MEMO',
        title: 'Lesson Memo 教学',
        task_kind: 'PERSONALIZED_IMPROVEMENT',
        not_started: 0,
        in_progress: 2,
        completed: 0,
        completion_rate: 0,
      }),
    ])

    expect(filterTaskProgressRows(rows, {
      keyword: 'memo',
      taskKind: 'PERSONALIZED_IMPROVEMENT',
      lifecycle: 'IN_PROGRESS',
    }).map((item) => item.task_code)).toEqual(['P-REL-MEMO'])
  })
})
