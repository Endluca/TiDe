import type { LessonSignalEvidence, OperationsIntervention, OperationsRiskBreakdown } from './types'

const domainLabels: Record<string, string> = {
  RELIABILITY: '可靠性',
  USER_FEEDBACK: '用户反馈',
  CLASSROOM_QUALITY: '课堂质量',
  CLASS_QUALITY: '课堂质量',
}

export function operationDomainLabel(domain: string, providedLabel?: string): string {
  const normalized = domain.trim().toUpperCase()
  return providedLabel?.trim() || domainLabels[normalized] || '其他风险'
}

export function interventionStatusLabel(status: string): string {
  const labels: Record<string, string> = {
    PLANNED: '待发起',
    REQUESTED: '待处理',
    STORED: '待处理',
    ACTION_PENDING: '待审批',
    DELIVERED: '已送达',
    READ: '已查看',
    CLICKED: '已开始处理',
    COMPLETED: '已完成',
    FAILED: '触达失败',
    CANCELLED: '已取消',
    OPEN: '待处理',
    ASSIGNED: '已创建',
    VIEWED: '已查看',
    IN_PROGRESS: '进行中',
    IN_REVIEW: '处理中',
    SUBMITTED: '已提交',
    UNDER_REVIEW: '审核中',
    RESOLVED: '已解决',
    CLOSED: '已关闭',
    OUTPUT_MISSING: '结果待补',
    PENDING_DATA: '数据待补',
    MATCHED: '已识别',
  }
  return labels[status] ?? '待跟进'
}

export function lessonStatusLabel(status: string): string {
  const labels: Record<string, string> = {
    COMPLETED: '已完课',
    FINISHED: '已完课',
    CANCELLED: '已取消',
    TEACHER_ABSENT: '教师缺席',
    STUDENT_ABSENT: '学员缺席',
    SCHEDULED: '待开课',
    END: '已完课',
    S_ABSENT: '学员缺席',
    T_ABSENT: '教师缺席',
  }
  return labels[status.trim().toUpperCase()] ?? (status || '待确认')
}

export function lessonSignalLabel(signal: string | LessonSignalEvidence): string {
  if (typeof signal === 'string') return signal
  if (typeof signal.label === 'string' && signal.label.trim()) return signal.label
  if (typeof signal.code === 'string' && signal.code.trim()) return signal.code
  return '异常证据'
}

export function sortInterventions(items: OperationsIntervention[]): OperationsIntervention[] {
  const priorityOrder: Record<string, number> = { P0: 0, P1: 1, P2: 2, P3: 3 }
  return [...items].sort((left, right) => (
    (priorityOrder[left.priority] ?? 9) - (priorityOrder[right.priority] ?? 9)
    || right.triggered_at.localeCompare(left.triggered_at)
  ))
}

export function summarizeRiskBreakdown(items: OperationsRiskBreakdown[]) {
  return items.reduce((summary, item) => ({
    signals: summary.signals + item.signal_count,
    teachers: summary.teachers + item.teacher_count,
    openActions: summary.openActions + item.open_output_count,
  }), { signals: 0, teachers: 0, openActions: 0 })
}

export function interventionOutputTypeLabel(outputType: string): string {
  const labels: Record<string, string> = {
    NOTIFICATION: '教师提醒',
    OPS_CASE: '运营事项',
    PENDING_DATA: '数据待补（内部）',
    TEACHER_TASK: '教师任务',
  }
  return labels[outputType] ?? '其他事项'
}

export function operationalOutputInterventions(items: OperationsIntervention[]): OperationsIntervention[] {
  const unique = new Map<string, OperationsIntervention>()
  items.forEach((item) => {
    if (item.output_type !== 'TEACHER_TASK' && !unique.has(item.output_id)) unique.set(item.output_id, item)
  })
  return sortInterventions([...unique.values()])
}
