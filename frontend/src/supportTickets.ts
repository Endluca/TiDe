import type { AppLocale } from './i18n'
import type { SupportTicketWorkflowState } from './types'

const workflowLabels: Record<SupportTicketWorkflowState, [string, string]> = {
  WAITING_OPERATOR: ['待运营回复', 'Waiting for operations'],
  WAITING_TEACHER: ['待教师回复', 'Waiting for teacher'],
  CLOSED: ['已关闭', 'Closed'],
}

const categoryLabels: Record<string, [string, string]> = {
  TASK_RULES: ['任务与规则', 'Tasks and rules'],
  LESSON_INFO: ['课程信息', 'Lesson information'],
  SCORE_OR_REVIEW: ['积分与评价', 'Scores and reviews'],
  PRODUCT_FUNCTION: ['产品功能', 'Product functionality'],
  ACCOUNT_LOGIN: ['账号与登录', 'Account and login'],
  MEDIA_UPLOAD_CAMERA: ['图片上传与摄像头', 'Media upload and camera'],
  OTHER: ['其他', 'Other'],
}

const locationLabels: Record<string, [string, string]> = {
  MY_TIDE: ['我的 TiDe', 'My TiDe'],
  TASK: ['任务', 'Task'],
  LESSON: ['课程', 'Lesson'],
  MESSAGES: ['消息', 'Messages'],
  ACCOUNT: ['账号', 'Account'],
  HELP: ['帮助', 'Help'],
  OTHER: ['其他位置', 'Other location'],
}

const contextLabels: Record<string, [string, string]> = {
  lesson_id: ['课程 ID', 'Lesson ID'],
  task_id: ['任务 ID', 'Task ID'],
  task_code: ['任务编号', 'Task code'],
  page_path: ['页面路径', 'Page path'],
  client_version: ['客户端版本', 'Client version'],
  device: ['设备', 'Device'],
}

function localized(pair: [string, string], locale: AppLocale): string {
  return pair[locale === 'en-US' ? 1 : 0]
}

export function supportTicketWorkflowLabel(
  state: SupportTicketWorkflowState,
  locale: AppLocale = 'zh-CN',
): string {
  return localized(workflowLabels[state], locale)
}

export function supportTicketCategoryLabel(
  code?: string | null,
  locale: AppLocale = 'zh-CN',
): string {
  if (!code) return locale === 'en-US' ? 'Uncategorized' : '未分类'
  const pair = categoryLabels[code]
  return pair ? localized(pair, locale) : code
}

export function supportTicketLocationLabel(
  code?: string | null,
  locale: AppLocale = 'zh-CN',
): string {
  if (!code) return locale === 'en-US' ? 'Not provided' : '未提供'
  const pair = locationLabels[code]
  return pair ? localized(pair, locale) : code
}

export function supportTicketContextLabel(
  code: string,
  locale: AppLocale = 'zh-CN',
): string {
  const pair = contextLabels[code]
  return pair ? localized(pair, locale) : code
}
