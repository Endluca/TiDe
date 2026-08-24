import type { LessonSignalEvidence, OperationsIntervention, OperationsRiskBreakdown } from './types'
import type { AppLocale } from './i18n'

const domainLabels: Record<string, string> = {
  RELIABILITY: '可靠性',
  USER_FEEDBACK: '用户反馈',
  CLASSROOM_QUALITY: '课堂质量',
  CLASS_QUALITY: '课堂质量',
}

const domainLabelsEn: Record<string, string> = {
  RELIABILITY: 'Reliability',
  USER_FEEDBACK: 'User feedback',
  CLASSROOM_QUALITY: 'Classroom quality',
  CLASS_QUALITY: 'Classroom quality',
}

export function operationDomainLabel(domain: string, providedLabel?: string, locale: AppLocale = 'zh-CN'): string {
  const normalized = domain.trim().toUpperCase()
  if (locale === 'en-US') return domainLabelsEn[normalized] || providedLabel?.trim() || 'Other risk'
  return providedLabel?.trim() || domainLabels[normalized] || '其他风险'
}

export function interventionStatusLabel(status: string, locale: AppLocale = 'zh-CN'): string {
  const labels: Record<string, [string, string]> = {
    PLANNED: ['待发起', 'Planned'],
    REQUESTED: ['待处理', 'Pending'],
    STORED: ['待处理', 'Pending'],
    ACTION_PENDING: ['待审批', 'Pending approval'],
    DELIVERED: ['已送达', 'Delivered'],
    READ: ['已查看', 'Viewed'],
    CLICKED: ['已开始处理', 'Started'],
    COMPLETED: ['已完成', 'Completed'],
    FAILED: ['触达失败', 'Delivery failed'],
    CANCELLED: ['已取消', 'Cancelled'],
    OPEN: ['待处理', 'Pending'],
    ASSIGNED: ['已创建', 'Created'],
    VIEWED: ['已查看', 'Viewed'],
    IN_PROGRESS: ['进行中', 'In progress'],
    IN_REVIEW: ['处理中', 'In review'],
    SUBMITTED: ['已提交', 'Submitted'],
    UNDER_REVIEW: ['审核中', 'Under review'],
    RESOLVED: ['已解决', 'Resolved'],
    CLOSED: ['已关闭', 'Closed'],
    OUTPUT_MISSING: ['结果待补', 'Result missing'],
    PENDING_DATA: ['数据待补', 'Data pending'],
    MATCHED: ['已识别', 'Matched'],
  }
  const pair = labels[status]
  return pair ? pair[locale === 'en-US' ? 1 : 0] : locale === 'en-US' ? 'Follow-up needed' : '待跟进'
}

export function lessonStatusLabel(status: string, locale: AppLocale = 'zh-CN'): string {
  const labels: Record<string, [string, string]> = {
    COMPLETED: ['已完课', 'Completed'],
    FINISHED: ['已完课', 'Completed'],
    CANCELLED: ['已取消', 'Cancelled'],
    TEACHER_ABSENT: ['教师缺席', 'Teacher absent'],
    STUDENT_ABSENT: ['学员缺席', 'Student absent'],
    SCHEDULED: ['待开课', 'Scheduled'],
    END: ['已完课', 'Completed'],
    S_ABSENT: ['学员缺席', 'Student absent'],
    T_ABSENT: ['教师缺席', 'Teacher absent'],
  }
  const pair = labels[status.trim().toUpperCase()]
  return pair ? pair[locale === 'en-US' ? 1 : 0] : status || (locale === 'en-US' ? 'Pending confirmation' : '待确认')
}

export function lessonSignalLabel(signal: string | LessonSignalEvidence, locale: AppLocale = 'zh-CN'): string {
  if (typeof signal === 'string') return signal
  if (locale === 'en-US' && typeof signal.code === 'string' && signal.code.trim()) {
    const englishSignals: Record<string, string> = {
      CAMERA_OFF: 'Camera off',
      HIGH_CPU: 'High CPU usage',
      HIGH_DELAY: 'High network latency',
      UNFILLED_LESSON_MEMO: 'Lesson memo not completed',
      ABSENT: 'Attendance issue',
      LATE: 'Late arrival',
      EARLY: 'Early leave',
      NEGATIVE_FEEDBACK: 'Negative feedback',
      COMPLAINT: 'Complaint',
      BLACKLISTED: 'Student blacklist',
    }
    return englishSignals[signal.code.trim().toUpperCase()] ?? signal.code
  }
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

export function interventionOutputTypeLabel(outputType: string, locale: AppLocale = 'zh-CN'): string {
  const labels: Record<string, [string, string]> = {
    NOTIFICATION: ['教师提醒', 'Teacher reminder'],
    OPS_CASE: ['运营事项', 'Operations case'],
    PENDING_DATA: ['数据待补（内部）', 'Data pending (internal)'],
    TEACHER_TASK: ['教师任务', 'Teacher task'],
  }
  const pair = labels[outputType]
  return pair ? pair[locale === 'en-US' ? 1 : 0] : locale === 'en-US' ? 'Other item' : '其他事项'
}

export function operationalOutputInterventions(items: OperationsIntervention[]): OperationsIntervention[] {
  const unique = new Map<string, OperationsIntervention>()
  items.forEach((item) => {
    if (item.output_type !== 'TEACHER_TASK' && !unique.has(item.output_id)) unique.set(item.output_id, item)
  })
  return sortInterventions([...unique.values()])
}
