import type { OutputDisplayType, OutputListResponse, OutputRecord, OutputStatus, OutputSummary, OutputType, SharedTaskAssignment, Task } from './types'

const taskPriorityOrder: Record<string, number> = {
  P0: 0,
  P1: 1,
  P2: 2,
  P3: 3,
}

export function employmentStatusLabel(status?: string | null): string {
  if (!status || status.toUpperCase() === 'UNKNOWN') return '状态待确认'
  if (status.toLowerCase() === 'on') return '在职'
  if (status.toLowerCase() === 'off') return '非在职'
  if (status.toLowerCase() === 'hei') return '已拉黑删除'
  return status
}

/**
 * 运营端统一的任务队列顺序：先按老师分组，再遵循后端冻结的展示排名，
 * 同一排名才用优先级与任务 ID 做稳定兜底。返回新数组，不修改接口快照。
 */
export function sortTasksForDisplay(tasks: Task[]): Task[] {
  return [...tasks].sort((left, right) => {
    const teacherOrder = left.teacher_id.localeCompare(right.teacher_id)
    if (teacherOrder !== 0) return teacherOrder

    const rankOrder = left.display_rank - right.display_rank
    if (rankOrder !== 0) return rankOrder

    const priorityOrder = (taskPriorityOrder[left.priority] ?? Number.MAX_SAFE_INTEGER)
      - (taskPriorityOrder[right.priority] ?? Number.MAX_SAFE_INTEGER)
    if (priorityOrder !== 0) return priorityOrder

    return left.task_id.localeCompare(right.task_id)
  })
}

export function displayError(error: unknown): string {
  if (typeof error === 'object' && error && 'body' in error) {
    const body = (error as { body?: { error_code?: string; field_path?: string | null } }).body
    const errorCode = body?.error_code ?? 'REQUEST_FAILED'
    const errorLabels: Record<string, string> = {
      TASK_SCOPE_MISMATCH: '该教师已出营或不在模板适用范围，不能下发任务',
      TEACHER_TIMEZONE_UNAVAILABLE: '缺少可信教师时区，任务未下发',
      TEACHER_TIMEZONE_INVALID: '教师时区格式无效，任务未下发',
      TEACHER_TIMEZONE_SNAPSHOT_CONFLICT: '任务时区快照与当前资料冲突，任务未下发',
    }
    const message = errorLabels[errorCode] ?? '请求失败'
    return [`${message}（${errorCode}）`, body?.field_path].filter(Boolean).join(' · ')
  }
  return error instanceof Error ? error.message : '请求失败'
}

export const methodLabels: Record<string, string> = {
  QUIZ: '学习小测',
  CHECKLIST: '学习清单',
  UPLOAD_REVIEW: '上传审核',
  DEVICE_CHECK: '设备检测',
  EXTERNAL_SYNC: '外部同步',
  CONFIRMATION_FORM: '结构化确认',
}

export const eventLabels: Record<string, string> = {
  'task.assignment.created.shared': '共享任务已创建',
  'task.assignment.updated.shared': '共享任务已更新',
  'task.assignment_changed.shared': '共享任务状态已变更',
  'task.assignment.created.shared.v1': '共享任务已创建',
  'task.assignment.updated.shared.v1': '共享任务已更新',
  'task.assignment_changed.shared.v1': '共享任务状态已变更',
  'task.issued.v1': '任务已签发',
  'task.dispatch_ack.v1': '教师端已接收',
  'task.runtime_event.v1': '任务运行事件',
  'notification.requested.v1': '站内通知已请求',
  'notification.delivery_event.v1': '站内通知投递事件',
  'agent.plan_committed.v1': '受约束 Agent 规划已提交',
  'ops_case.created.v1': '运营事项已创建',
  'ops_case.decided.v1': '运营已做决定',
}

export const outputTypeLabels: Record<OutputType, string> = {
  TEACHER_TASK: '教师任务',
  OPS_REVIEW_CASE: '运营复核事项',
  SYSTEM_ACTION_REQUEST: '外部动作请求',
  DELIVERY_INTENT: '触达意图',
}

export const outputDisplayTypeLabels: Record<OutputDisplayType, string> = {
  TASK_ASSIGNMENT: '教师任务',
  IN_APP_NOTIFICATION: '站内通知',
  REMINDER: '小提醒 / 催办',
  OPS_CASE: '运营事项',
  EXTERNAL_ACTION_REQUEST: '人工审批请求',
  PROVIDER_REQUEST: 'Agent 调试记录',
}

export const outputStatusLabels: Record<OutputStatus, string> = {
  PLANNED: '已计划',
  REQUESTED: '已请求',
  STORED: '已落盘',
  DELIVERED: '已送达',
  READ: '已读',
  CLICKED: '已点击',
  FAILED: '失败',
  ACTION_PENDING: '待审批 / 未执行',
  CANCELLED: '已取消',
}

export function normalizeOutputList(payload: OutputRecord[] | OutputListResponse): OutputListResponse {
  if (Array.isArray(payload)) return { items: payload, total: payload.length }
  return { items: payload.items ?? [], total: payload.total ?? payload.items?.length ?? 0 }
}

export function isOperationalTaskAssignment(item: SharedTaskAssignment): boolean {
  return !item.source_mode.startsWith('MOCK')
}

export function isOperationalOutput(item: OutputRecord): boolean {
  if (item.non_business || item.display_type === 'PROVIDER_REQUEST') return false
  const text = `${item.title} ${item.body ?? ''} ${item.content ?? ''}`.toLocaleLowerCase()
  return !text.includes('mock') && !text.includes('模拟') && !text.includes('调试')
}

export function summarizeOutputs(outputs: OutputRecord[]): OutputSummary {
  const by_type: OutputSummary['by_type'] = {}
  const by_display_type: NonNullable<OutputSummary['by_display_type']> = {}
  const by_status: OutputSummary['by_status'] = {}
  outputs.forEach((item) => {
    const nonBusiness = item.non_business || item.display_type === 'PROVIDER_REQUEST'
    if (!nonBusiness && item.output_type) by_type[item.output_type] = (by_type[item.output_type] ?? 0) + 1
    by_display_type[item.display_type] = (by_display_type[item.display_type] ?? 0) + 1
    by_status[item.status] = (by_status[item.status] ?? 0) + 1
  })
  return { total: outputs.length, by_type, by_display_type, by_status }
}

export function canRetryOutput(output: Pick<OutputRecord, 'retryable' | 'status' | 'attempt_count' | 'max_attempts'>): boolean {
  return output.retryable && output.status === 'FAILED' && output.attempt_count < output.max_attempts
}
