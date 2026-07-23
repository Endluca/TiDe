import { describe, expect, it } from 'vitest'
import {
  filterTaskTemplates,
  normalizeTaskTemplateList,
  taskOwnerLabel,
  taskScoreSummary,
} from './templateCenter'
import type { TaskTemplate } from './types'

function template(overrides: Partial<TaskTemplate> = {}): TaskTemplate {
  return {
    template_id: 'G01',
    revision: 1,
    status: 'PUBLISHED',
    output_type: 'TEACHER_TASK',
    audience: 'TEACHER',
    owner: 'TIT_GROWTH_OPS',
    execution_owner: 'TEACHER_APP',
    integration_mode: 'INBOUND_STATUS_ONLY',
    ops_name_zh: '资料与资质完善',
    content_locale: 'en',
    category: 'MANDATORY_GROWTH',
    dimension: 'NEW_TEACHER_TASK',
    stage: 'DAY_1_7',
    title: 'Profile & Credentials Completion',
    why_template: 'Required for readiness.',
    how_summary: 'Complete the required profile items.',
    completion_standard: 'All required items pass review.',
    benefit: 'Earn 4 points once.',
    help_ref: null,
    priority: 'P1',
    due_rule: { type: 'CAMP_DAY_OR_EVENT_DEADLINE', camp_day: 7 },
    appeal_mode: 'HUMAN_REVIEW',
    external_task_template_code: 'TIT.G01',
    action_url: null,
    score_type: 'FIXED',
    score_value: 4,
    source_mode: 'MOCK',
    source_refs: ['current-contract'],
    created_at: '2026-07-22T00:00:00Z',
    updated_at: '2026-07-22T00:00:00Z',
    ...overrides,
  }
}

describe('当前任务定义', () => {
  it('按编号、内容和阶段筛选', () => {
    const items = [template(), template({ template_id: 'G09', stage: 'DAY_15_30', ops_name_zh: 'Cocos 课程培训' })]
    expect(filterTaskTemplates(items, { keyword: 'cocos', stage: 'DAY_15_30' })).toEqual([items[1]])
    expect(filterTaskTemplates(items, { keyword: 'G01', stage: '' })).toEqual([items[0]])
  })

  it('兼容数组和 items 包装的列表响应', () => {
    const item = template()
    expect(normalizeTaskTemplateList([item])).toEqual([item])
    expect(normalizeTaskTemplateList({ items: [item] })).toEqual([item])
  })

  it('只展示必要的积分与责任方含义', () => {
    expect(taskScoreSummary(template())).toBe('4 分')
    expect(taskScoreSummary(template({ score_type: 'ZERO', score_value: 0 }))).toBe('0 分')
    expect(taskScoreSummary(template({ score_type: 'NOT_APPLICABLE', score_value: 0 }))).toBe('不计分')
    expect(taskOwnerLabel(template())).toBe('教师端')
  })
})
