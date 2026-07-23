import type { TaskTemplate } from './types'

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
    ].join(' ').toLocaleLowerCase()
    return (!needle || searchable.includes(needle))
      && (!filters.stage || template.stage === filters.stage)
  })
}

export function taskScoreSummary(template: Pick<TaskTemplate, 'score_type' | 'score_value'>): string {
  if (template.score_type === 'FIXED') return `${template.score_value} 分`
  if (template.score_type === 'ZERO') return '0 分'
  return '不计分'
}

export function taskOwnerLabel(template: Pick<TaskTemplate, 'execution_owner'>): string {
  return template.execution_owner === 'TEACHER_APP' ? '教师端' : template.execution_owner
}
