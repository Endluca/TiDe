import type { TaskTemplate } from './types'
import type { AppLocale } from './i18n'

export interface TaskTemplateFilters {
  keyword: string
  stage: string
}

export function normalizeTaskTemplateList(
  response: TaskTemplate[] | { items: TaskTemplate[] },
): TaskTemplate[] {
  return Array.isArray(response) ? response : response.items
}

export function filterTaskTemplates(
  templates: TaskTemplate[],
  filters: TaskTemplateFilters,
): TaskTemplate[] {
  const needle = filters.keyword.trim().toLocaleLowerCase()
  return templates.filter((template) => {
    const searchable = [
      template.template_id,
      template.ops_name_zh,
      template.title,
      template.stage,
      template.why_template,
      template.how_summary,
      template.completion_standard,
      template.benefit,
      template.content_status,
    ].join(' ').toLocaleLowerCase()
    return (!needle || searchable.includes(needle))
      && (!filters.stage || template.stage === filters.stage)
  })
}

export function taskScoreSummary(template: Pick<TaskTemplate, 'score_type' | 'score_value'>, locale: AppLocale = 'zh-CN'): string {
  if (template.score_type === 'FIXED') return locale === 'en-US' ? `${template.score_value} pts` : `${template.score_value} 分`
  if (template.score_type === 'ZERO') return locale === 'en-US' ? '0 pts' : '0 分'
  return locale === 'en-US' ? 'No points' : '不计分'
}

export function taskOwnerLabel(template: Pick<TaskTemplate, 'execution_owner'>, locale: AppLocale = 'zh-CN'): string {
  return template.execution_owner === 'TEACHER_APP'
    ? (locale === 'en-US' ? 'Teacher app' : '教师端')
    : template.execution_owner
}
