import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from './api'
import { currentOpsCaseStatusOptions } from './pages/InterventionCenter'
import {
  interventionStatusLabel,
  interventionOutputTypeLabel,
  lessonSignalLabel,
  lessonStatusLabel,
  operationDomainLabel,
  operationalOutputInterventions,
  sortInterventions,
  summarizeRiskBreakdown,
} from './operations'
import type { OperationsIntervention } from './types'

afterEach(() => vi.unstubAllGlobals())

describe('运营视图文案与排序', () => {
  it('当前运营处置待办只提供未结束状态筛选', () => {
    expect(currentOpsCaseStatusOptions.map((item) => item.value)).toEqual([
      'OPEN',
      'ACTION_PENDING',
      'IN_REVIEW',
    ])
    expect(currentOpsCaseStatusOptions.map((item) => item.value)).not.toContain('RESOLVED')
    expect(currentOpsCaseStatusOptions.map((item) => item.value)).not.toContain('CANCELLED')
  })

  it('把风险、处置和课程状态转换成运营可读文案', () => {
    expect(operationDomainLabel('RELIABILITY')).toBe('可靠性')
    expect(operationDomainLabel('CLASS_QUALITY')).toBe('课堂质量')
    expect(operationDomainLabel('OTHER', '专项风险')).toBe('专项风险')
    expect(interventionStatusLabel('ACTION_PENDING')).toBe('待审批')
    expect(lessonStatusLabel('TEACHER_ABSENT')).toBe('教师缺席')
    expect(lessonStatusLabel('end')).toBe('已完课')
    expect(lessonStatusLabel('s_absent')).toBe('学员缺席')
    expect(interventionStatusLabel('IN_REVIEW')).toBe('处理中')
    expect(lessonSignalLabel({ code: 'CAMERA_OFF', label: '未开摄像头' })).toBe('未开摄像头')
  })

  it('处置事项先按优先级、再按触发时间排序', () => {
    const base = {
      output_type: 'OPS_CASE',
      title: '风险待处置',
      teacher_id: 'T-1',
      teacher_name: 'Teacher',
      domain: 'RELIABILITY',
      status: 'REQUESTED',
      why: '存在可靠性风险',
      evidence_summary: '课程证据',
      source_lesson_id: 'L-1',
      action_label: '参加培训',
    }
    const items = [
      { ...base, output_id: 'P2', priority: 'P2', triggered_at: '2026-07-22T10:00:00Z' },
      { ...base, output_id: 'P0-OLD', priority: 'P0', triggered_at: '2026-07-22T09:00:00Z' },
      { ...base, output_id: 'P0-NEW', priority: 'P0', triggered_at: '2026-07-22T11:00:00Z' },
    ] as OperationsIntervention[]
    expect(sortInterventions(items).map((item) => item.output_id)).toEqual(['P0-NEW', 'P0-OLD', 'P2'])
    expect(items.map((item) => item.output_id)).toEqual(['P2', 'P0-OLD', 'P0-NEW'])
  })

  it('风险汇总不重复猜测业务指标', () => {
    expect(summarizeRiskBreakdown([
      { domain: 'RELIABILITY', label: '可靠性', signal_count: 8, teacher_count: 5, open_output_count: 4 },
      { domain: 'USER_FEEDBACK', label: '用户反馈', signal_count: 3, teacher_count: 2, open_output_count: 1 },
    ])).toEqual({ signals: 11, teachers: 7, openActions: 5 })
  })

  it('输出视图排除教师任务并按 output_id 去重', () => {
    const item = {
      output_id: 'NOTICE-1',
      output_type: 'NOTIFICATION',
      title: '课中质量提醒',
      teacher_id: 'T-1',
      teacher_name: 'Teacher',
      domain: 'CLASS_QUALITY',
      priority: 'P2',
      status: 'STORED',
      triggered_at: '2026-07-22T00:00:00Z',
      why: '检测到课堂质量异常',
      evidence_summary: '未开摄像头',
      source_lesson_id: 'L-1',
      source_lesson_ids: ['L-1'],
      signal_count: 1,
      action_label: '查看提醒',
    } as OperationsIntervention
    expect(operationalOutputInterventions([
      item,
      { ...item },
      { ...item, output_id: 'TASK-1', output_type: 'TEACHER_TASK' },
      { ...item, output_id: 'DATA-1', output_type: 'PENDING_DATA' },
    ]).map((current) => current.output_id)).toEqual(['NOTICE-1', 'DATA-1'])
    expect(interventionOutputTypeLabel('PENDING_DATA')).toBe('数据待补（内部）')
  })
})

describe('运营与课程接口', () => {
  it('按筛选条件请求处置列表和课程证据', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: async () => ({ items: [], total: 0 }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ items: [], total: 0, page: 2, page_size: 20 }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ case_id: 'CASE-1', status: 'IN_REVIEW', decision_id: 'D-1', updated_at: '2026-07-22T00:00:00Z' }) })
    vi.stubGlobal('fetch', fetchMock)

    await api.operationsInterventions({ type: 'OPS_CASE', open_only: true, status: 'REQUESTED', domain: 'RELIABILITY', teacher_id: 'T-1' })
    await api.lessons({ page: 2, page_size: 20, teacher_id: 'T-1', lesson_id: 'L-1', risk_only: true })
    await api.decideOperationsCase('CASE-1', 'START_PROCESSING', '')

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      '/api/operations/interventions?type=OPS_CASE&open_only=true&status=REQUESTED&domain=RELIABILITY&teacher_id=T-1',
      expect.objectContaining({ credentials: 'include' }),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      '/api/lessons?page=2&page_size=20&teacher_id=T-1&lesson_id=L-1&risk_only=true',
      expect.objectContaining({ credentials: 'include' }),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      '/api/operations/cases/CASE-1/decision',
      expect.objectContaining({
        method: 'POST',
        credentials: 'include',
        body: JSON.stringify({ decision: 'START_PROCESSING', note: '' }),
      }),
    )
  })
})
