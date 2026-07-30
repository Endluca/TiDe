import type { TaskProgressItem } from './types'

export type TaskProgressRow = TaskProgressItem & {
  key: string
}

export interface TaskProgressFilters {
  keyword: string
  taskKind: string
  lifecycle: string
}

export function taskProgressKey(
  item: Pick<TaskProgressItem, 'task_code' | 'title' | 'task_kind'>,
): string {
  return JSON.stringify([item.task_kind, item.task_code, item.title])
}

export function normalizeTaskProgressItems(
  items: TaskProgressItem[],
): TaskProgressRow[] {
  return items
    .map((item) => ({ ...item, key: taskProgressKey(item) }))
    .sort((left, right) => (
      Number(left.task_kind !== 'FIXED_GROWTH')
      - Number(right.task_kind !== 'FIXED_GROWTH')
      || left.task_code.localeCompare(right.task_code, undefined, { numeric: true })
      || left.title.localeCompare(right.title)
    ))
}

export function filterTaskProgressRows(
  rows: TaskProgressRow[],
  filters: TaskProgressFilters,
): TaskProgressRow[] {
  const needle = filters.keyword.trim().toLocaleLowerCase()
  return rows.filter((row) => {
    const searchable = `${row.task_code} ${row.title}`.toLocaleLowerCase()
    const matchesLifecycle = !filters.lifecycle
      || (filters.lifecycle === 'NOT_STARTED' && row.not_started > 0)
      || (filters.lifecycle === 'IN_PROGRESS' && row.in_progress > 0)
      || (filters.lifecycle === 'COMPLETED' && row.completed > 0)
      || (filters.lifecycle === 'OTHER' && row.other > 0)

    return (!needle || searchable.includes(needle))
      && (!filters.taskKind || row.task_kind === filters.taskKind)
      && matchesLifecycle
  })
}
