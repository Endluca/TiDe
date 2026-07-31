import type { OutputDisplayType, OutputListResponse, OutputRecord, OutputStatus, OutputSummary, OutputType, SharedTaskAssignment, Task } from './types'
import type { AppLocale } from './i18n'

const taskPriorityOrder: Record<string, number> = {
  P0: 0,
  P1: 1,
  P2: 2,
  P3: 3,
}

export function employmentStatusLabel(status?: string | null, locale: AppLocale = 'zh-CN'): string {
  const english = locale === 'en-US'
  if (!status || status.toUpperCase() === 'UNKNOWN') return english ? 'Status pending' : '状态待确认'
  if (status.toLowerCase() === 'on') return english ? 'Active' : '在职'
  if (status.toLowerCase() === 'off') return english ? 'Inactive' : '非在职'
  if (status.toLowerCase() === 'hei') return english ? 'Blacklisted / removed' : '已拉黑删除'
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

export function displayError(error: unknown, locale: AppLocale = 'zh-CN'): string {
  if (typeof error === 'object' && error && 'body' in error) {
    const body = (error as { body?: { error_code?: string; field_path?: string | null } }).body
    const errorCode = body?.error_code ?? 'REQUEST_FAILED'
    const errorLabels: Record<string, [string, string]> = {
      TASK_SCOPE_MISMATCH: ['该教师已出营或不在模板适用范围，不能下发任务', 'The teacher has graduated or is outside the task scope'],
      TEACHER_TIMEZONE_UNAVAILABLE: ['缺少可信教师时区，任务未下发', 'The task was not assigned because a trusted teacher timezone is unavailable'],
      TEACHER_TIMEZONE_INVALID: ['教师时区格式无效，任务未下发', 'The task was not assigned because the teacher timezone is invalid'],
      TEACHER_TIMEZONE_SNAPSHOT_CONFLICT: ['任务时区快照与当前资料冲突，任务未下发', 'The task was not assigned because the timezone snapshot conflicts with the current profile'],
      SUPPORT_TICKET_VERSION_CONFLICT: ['该工单已被更新，请查看最新内容后重试', 'This ticket changed. Review the latest content and try again'],
      SUPPORT_TICKET_MESSAGE_CONFLICT: ['消息编号已被其他内容使用，请重新发送', 'This message ID is already used by different content'],
      SUPPORT_TICKET_CLOSED: ['该工单已关闭，不能继续回复', 'This ticket is closed and cannot receive replies'],
      SUPPORT_TICKET_MESSAGE_INVALID: ['回复内容不符合共享工单要求', 'The reply does not meet the shared-ticket requirements'],
      SUPPORT_TICKET_NOT_FOUND: ['未找到该工单', 'Ticket not found'],
      SUPPORT_TICKET_SOURCE_UNAVAILABLE: ['共享工单数据暂不可用', 'The shared-ticket data is unavailable'],
      SUPPORT_TICKET_REQUEST_FAILED: ['工单请求失败，请稍后重试', 'The ticket request failed. Try again later'],
      SUPPORT_TICKET_STATE_INVALID: ['工单状态筛选值无效', 'The ticket status filter is invalid'],
      SUPPORT_TICKET_CATEGORY_INVALID: ['工单问题类型筛选值无效', 'The ticket category filter is invalid'],
    }
    const pair = errorLabels[errorCode]
    const message = pair ? pair[locale === 'en-US' ? 1 : 0] : locale === 'en-US' ? 'Request failed' : '请求失败'
    const messageWithCode = locale === 'en-US' ? `${message} (${errorCode})` : `${message}（${errorCode}）`
    return [messageWithCode, body?.field_path].filter(Boolean).join(' · ')
  }
  return error instanceof Error ? error.message : locale === 'en-US' ? 'Request failed' : '请求失败'
}

export const methodLabels: Record<string, string> = {
  QUIZ: '学习小测',
  CHECKLIST: '学习清单',
  UPLOAD_REVIEW: '上传审核',
  DEVICE_CHECK: '设备检测',
  EXTERNAL_SYNC: '外部同步',
  CONFIRMATION_FORM: '结构化确认',
}

const methodLabelsEn: Record<string, string> = {
  QUIZ: 'Learning quiz',
  CHECKLIST: 'Learning checklist',
  UPLOAD_REVIEW: 'Upload and review',
  DEVICE_CHECK: 'Device check',
  EXTERNAL_SYNC: 'External sync',
  CONFIRMATION_FORM: 'Structured confirmation',
}

export function methodLabel(method: string, locale: AppLocale = 'zh-CN'): string {
  return (locale === 'en-US' ? methodLabelsEn : methodLabels)[method] ?? method
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
  'support_ticket.operator_replied.v1': '运营已回复教师工单',
}

const eventLabelsEn: Record<string, string> = {
  'task.assignment.created.shared': 'Shared task created',
  'task.assignment.updated.shared': 'Shared task updated',
  'task.assignment_changed.shared': 'Shared task status changed',
  'task.assignment.created.shared.v1': 'Shared task created',
  'task.assignment.updated.shared.v1': 'Shared task updated',
  'task.assignment_changed.shared.v1': 'Shared task status changed',
  'task.issued.v1': 'Task issued',
  'task.dispatch_ack.v1': 'Received by teacher app',
  'task.runtime_event.v1': 'Task runtime event',
  'notification.requested.v1': 'In-app notification requested',
  'notification.delivery_event.v1': 'Notification delivery event',
  'agent.plan_committed.v1': 'Constrained agent plan committed',
  'ops_case.created.v1': 'Operations case created',
  'ops_case.decided.v1': 'Operations decision recorded',
  'support_ticket.operator_replied.v1': 'Operations replied to teacher ticket',
}

export function eventLabel(eventType: string, locale: AppLocale = 'zh-CN'): string {
  return (locale === 'en-US' ? eventLabelsEn : eventLabels)[eventType] ?? eventType
}

export const outputTypeLabels: Record<OutputType, string> = {
  TEACHER_TASK: '教师任务',
  OPS_REVIEW_CASE: '运营复核事项',
  SYSTEM_ACTION_REQUEST: '外部动作请求',
  DELIVERY_INTENT: '触达意图',
}

const outputTypeLabelsEn: Record<OutputType, string> = {
  TEACHER_TASK: 'Teacher task',
  OPS_REVIEW_CASE: 'Operations review',
  SYSTEM_ACTION_REQUEST: 'External action request',
  DELIVERY_INTENT: 'Delivery intent',
}

export function outputTypeLabel(type: OutputType, locale: AppLocale = 'zh-CN'): string {
  return (locale === 'en-US' ? outputTypeLabelsEn : outputTypeLabels)[type] ?? type
}

export const outputDisplayTypeLabels: Record<OutputDisplayType, string> = {
  TASK_ASSIGNMENT: '教师任务',
  IN_APP_NOTIFICATION: '站内通知',
  REMINDER: '小提醒 / 催办',
  OPS_CASE: '运营事项',
  EXTERNAL_ACTION_REQUEST: '人工审批请求',
  PROVIDER_REQUEST: 'Agent 调试记录',
}

const outputDisplayTypeLabelsEn: Record<OutputDisplayType, string> = {
  TASK_ASSIGNMENT: 'Teacher task',
  IN_APP_NOTIFICATION: 'In-app notification',
  REMINDER: 'Reminder',
  OPS_CASE: 'Operations case',
  EXTERNAL_ACTION_REQUEST: 'Manual approval request',
  PROVIDER_REQUEST: 'Agent diagnostic record',
}

export function outputDisplayTypeLabel(type: OutputDisplayType, locale: AppLocale = 'zh-CN'): string {
  return (locale === 'en-US' ? outputDisplayTypeLabelsEn : outputDisplayTypeLabels)[type] ?? type
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

const outputStatusLabelsEn: Record<OutputStatus, string> = {
  PLANNED: 'Planned',
  REQUESTED: 'Requested',
  STORED: 'Stored',
  DELIVERED: 'Delivered',
  READ: 'Read',
  CLICKED: 'Clicked',
  FAILED: 'Failed',
  ACTION_PENDING: 'Pending approval / not executed',
  CANCELLED: 'Cancelled',
}

export function outputStatusLabel(status: OutputStatus, locale: AppLocale = 'zh-CN'): string {
  return (locale === 'en-US' ? outputStatusLabelsEn : outputStatusLabels)[status] ?? status
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
