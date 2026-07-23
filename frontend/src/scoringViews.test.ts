import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from './api'
import { isOperationalQueueItem, mixedDataAlertDescription, operationalEvidenceSummary, operationalFunnelStages } from './pages/OperationalDashboard'
import {
  TEACHER_PAGE_SIZE,
  completionEvidenceLabel,
  externalScoreFromRaw,
  hardGateLabel,
  hardGateSourceMeta,
  pendingTeacherPage,
  profileFactDisplay,
  scorePolicyLabel,
  sourceModeLabel,
  teacherDataCoverageDescription,
  teacherDataModeLabel,
  teacherDetailPhase,
  teacherScoreProjection,
  teacherSupplyMilestone,
  visibleGraduationGateItems,
  mixedTeacherDataDescription,
} from './pages/Teacher360'
import type { AppSnapshot, Teacher, TeacherOption, TeacherPage } from './types'

function teacher(overrides: Partial<Teacher>): Teacher {
  return {
    teacher_id: 'T-1',
    name: 'Teacher',
    ...overrides,
  }
}

function snapshot(teachers: TeacherOption[] = []): AppSnapshot {
  return {
    dashboard: null,
    teachers,
    templates: [],
    tasks: [],
    cases: [],
    queue: [],
    notifications: [],
    events: [],
  }
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('score projection', () => {
  it('保留 raw 分并把对外显示封顶在 200', () => {
    expect(externalScoreFromRaw(99)).toBe(99)
    expect(externalScoreFromRaw(100)).toBe(100)
    expect(externalScoreFromRaw(199)).toBe(199)
    expect(externalScoreFromRaw(200)).toBe(200)
    expect(externalScoreFromRaw(380)).toBe(200)
    expect(externalScoreFromRaw(660)).toBe(200)
    expect(externalScoreFromRaw(900)).toBe(200)
  })

  it('非当前数据仍按记录的分段映射安全读取', () => {
    expect(externalScoreFromRaw(380, 100, 660, 100, 200, 'v2')).toBe(150)
  })

  it('缺少 raw_total_score 时读取 total_score，但不把分数线命中当成最终资格', () => {
    expect(teacherScoreProjection(teacher({ total_score: 700 }))).toEqual({
      raw: 700,
      external: 200,
      graduationThreshold: 100,
      goldThreshold: 200,
      graduationExternalScore: 100,
      goldExternalScore: 200,
      graduationScoreMet: true,
      graduationCriteriaMet: undefined,
      goldScoreMet: true,
      goldCriteriaMet: undefined,
    })
  })

  it('当前口径展示 peak_slot_cnt/40 与供给分 0/10', () => {
    expect(teacherSupplyMilestone(teacher({
      score_policy_version: 'v6',
      metric_inputs: { peak_slot_cnt: 39 },
      dimensions: [{ code: 'CAPACITY', label: '供给积分', score: 0, source_mode: 'REAL' }],
    }))).toMatchObject({ peakSlotCount: 39, threshold: 40, score: 0, maximumScore: 10, met: false, locked: false })

    expect(teacherSupplyMilestone(teacher({
      score_policy_version: 'v6',
      metric_inputs: { peak_slot_cnt: 12 },
      dimensions: [{
        code: 'CAPACITY',
        label: '供给积分',
        score: 10,
        source_mode: 'REAL',
        components: [{
          code: 'CAPACITY_PEAK_SLOT_40',
          metric: 'peak_slot_cnt',
          value: 12,
          score: 10,
          milestone_achieved: true,
          settlement_mode: 'FIRST_ACHIEVEMENT_LOCKED',
          source_mode: 'REAL',
        }],
      }],
    }))).toMatchObject({ peakSlotCount: 12, score: 10, met: true, locked: true })
  })

  it('v4 历史数据可只读展示供给里程碑，缺少指标时不伪造', () => {
    expect(teacherSupplyMilestone(teacher({ score_policy_version: 'v3', metric_inputs: { peak_slot_cnt: 40 } }))).toBeNull()
    expect(teacherSupplyMilestone(teacher({ score_policy_version: 'v4', metric_inputs: { peak_slot_cnt: 40 } }))).toMatchObject({ met: true })
    expect(teacherSupplyMilestone(teacher({ score_policy_version: 'v6' }))).toBeNull()
  })
})

describe('teacher list and dashboard', () => {
  it('经营总览不把演示队列包装成真实运营待办', () => {
    expect(isOperationalQueueItem({
      queue_id: 'MOCK-QUEUE-1',
      queue_type: 'OPS_REVIEW_CASE',
      priority: 'P2',
      teacher_id: 'T-1',
      title: '演示任务',
      summary: '供运营行动台检查 Case 展示。',
      status: 'OPEN',
      created_at: '2026-07-22T00:00:00Z',
    })).toBe(false)
    expect(isOperationalQueueItem({
      queue_id: 'QUEUE-REAL-1',
      queue_type: 'OPS_REVIEW_CASE',
      priority: 'P0',
      teacher_id: 'T-2',
      title: '严重投诉待处置',
      summary: '需要运营核实投诉责任。',
      status: 'OPEN',
      created_at: '2026-07-22T00:00:00Z',
    })).toBe(true)
  })

  it('当前和非当前数据都使用单一产品口径说明证据范围', () => {
    const currentDescription = mixedDataAlertDescription('v6')
    expect(currentDescription).toContain('Peak slots')
    expect(currentDescription).toContain('perfect_cnt')
    expect(currentDescription).toContain('必修任务基线和完成状态直接读取共享任务表')
    expect(currentDescription).not.toContain('必修任务状态、L0 投诉记录和缺席责任拆分仍待证据补齐')
    expect(currentDescription).not.toContain('Mock')

    const nonCurrentDescription = mixedDataAlertDescription('v3')
    expect(nonCurrentDescription).toContain('非当前计分口径')
    expect(nonCurrentDescription).not.toContain('Peak slots')
    expect(nonCurrentDescription).not.toContain('Mock')
  })

  it('计分口径只向运营展示当前、非当前或待确认', () => {
    expect(scorePolicyLabel('v6')).toBe('当前口径')
    expect(scorePolicyLabel('v5')).toBe('非当前数据（只读）')
    expect(scorePolicyLabel('v4')).toBe('非当前数据（只读）')
    expect(scorePolicyLabel(null)).toBe('待确认')
  })

  it('当前出营只展示必修任务、L0 投诉和总分三条门槛', () => {
    const items = [
      { code: 'ALL_MANDATORY_GROWTH_TASKS_COMPLETED', actual: 10, threshold: 10 },
      { code: 'NO_L0_COMPLAINT', actual: 0, threshold: 0 },
      { code: 'MINIMUM_TOTAL_SCORE', actual: 120, threshold: 100 },
      { code: 'MINIMUM_COMPLETED_LESSONS', actual: 50, threshold: 10 },
      { code: 'NO_SEVERE_REDLINE', actual: false, threshold: false },
    ]
    expect(visibleGraduationGateItems(items, 'v6').map((item) => item.code)).toEqual([
      'ALL_MANDATORY_GROWTH_TASKS_COMPLETED',
      'NO_L0_COMPLAINT',
      'MINIMUM_TOTAL_SCORE',
    ])
    expect(visibleGraduationGateItems(items, 'v4')).toEqual(items)
    expect(hardGateLabel(items[0])).toBe('G01–G10 必修成长任务全部完成')
    expect(hardGateLabel(items[1])).toBe('L0 投诉数为 0')
    expect(hardGateLabel(items[2])).toBe('累计总分达到出营要求')
    expect(hardGateLabel(items[4])).toBe('无严重红线记录')
  })

  it('已满足出营资格的金牌复用门槛不误报证据待补', () => {
    expect(hardGateSourceMeta({
      code: 'REQUIRES_GRADUATION_CRITERIA',
      met: true,
      source_mode: 'MIXED_DERIVED',
    })).toEqual({ label: '资格结果计算', color: 'cyan' })

    expect(hardGateSourceMeta({
      code: 'REQUIRES_GRADUATION_CRITERIA',
      met: false,
      source_mode: 'SOURCE_MISSING',
    })).toEqual({ label: '源数据缺失', color: 'default' })

    expect(hardGateSourceMeta({
      code: 'REQUIRES_GRADUATION_CRITERIA',
      met: false,
      source_mode: 'TASK_BASELINE_INCOMPLETE',
    })).toEqual({ label: '必修任务初始化异常', color: 'red' })
  })

  const teachers = [
    teacher({ teacher_id: 'MOCK-1', data_mode: 'MOCK', employment_status: 'off' }),
    teacher({ teacher_id: 'MIXED-1', data_mode: 'MIXED', employment_status: 'on', raw_total_score: 120, graduation_criteria_met: true }),
    teacher({ teacher_id: 'REAL-1', data_mode: 'REAL', employment_status: 'on', raw_total_score: 700, gold_criteria_met: true }),
  ]

  it('教师 360 固定使用服务端每页 24 条契约', () => {
    expect(TEACHER_PAGE_SIZE).toBe(24)
  })

  it('新查询开始时清空上一次卡片并把界面筛选切到本次请求', () => {
    const previous: TeacherPage = {
      items: teachers,
      total: 3,
      page: 3,
      page_size: 24,
      total_pages: 4,
      filters: {
        keyword: '',
        data_mode: null,
        employment_status: null,
        available_data_modes: ['REAL', 'MIXED', 'MOCK'],
        available_employment_statuses: ['on', 'off'],
      },
    }

    expect(pendingTeacherPage(previous, 1, ' Ana ', 'MIXED', 'on')).toEqual({
      items: [],
      total: 0,
      page: 1,
      page_size: 24,
      total_pages: 0,
      filters: {
        keyword: 'Ana',
        data_mode: 'MIXED',
        employment_status: 'on',
        available_data_modes: ['REAL', 'MIXED', 'MOCK'],
        available_employment_statuses: ['on', 'off'],
      },
    })
  })

  it('缺失档案字段和来源枚举使用运营可读文案', () => {
    expect(profileFactDisplay(null, '时区待确认')).toBe('时区待确认')
    expect(profileFactDisplay('', '时区待确认')).toBe('时区待确认')
    expect(profileFactDisplay('Unknown', '国家待确认')).toBe('国家待确认')
    expect(profileFactDisplay('Asia/Manila', '时区待确认')).toBe('Asia/Manila')
    expect(completionEvidenceLabel(true)).toBe('已完成')
    expect(completionEvidenceLabel(false)).toBe('明确未完成')
    expect(completionEvidenceLabel(null)).toBe('暂无数据')
    expect(sourceModeLabel('SOURCE_MISSING')).toBe('源数据缺失')
    expect(sourceModeLabel('MISSING_INPUT_ZERO')).toBe('缺失按 0 结算')
    expect(sourceModeLabel('SYSTEM_TASK_STATUS')).toBe('任务状态计算')
    expect(sourceModeLabel('TASK_BASELINE_INCOMPLETE')).toBe('必修任务初始化异常')
    expect(sourceModeLabel('TASK_STATUS_PARTIAL')).toBe('必修任务初始化异常')
    expect(sourceModeLabel('TASK_STATUS_INVALID')).toBe('任务模板引用异常')
    expect(sourceModeLabel('COMPLAINT_LEVEL_MAPPING_INCOMPLETE')).toBe('投诉级别映射待补')
    expect(sourceModeLabel('LEGACY_DIMENSION')).toBe('非当前维度数据')
  })

  it('MIXED 表示多来源合并，不误报为证据缺失', () => {
    expect(teacherDataModeLabel('MIXED')).toBe('多来源数据')
    expect(teacherDataCoverageDescription).toContain('当前积分与资格按各指标自身来源判断')
    expect(teacherDataCoverageDescription).toContain('国家和时区尚未接入且不参与当前积分与资格')
    expect(teacherDataCoverageDescription).toContain('首课预约时间为空时保留为空')
    expect(teacherDataCoverageDescription).not.toContain('档案字段待补')
    expect(mixedTeacherDataDescription).toContain('不等于证据缺失')
    expect(mixedTeacherDataDescription).toContain('具体以各指标的来源标记为准')
  })

  it('轻量摘要不能冒充完整详情或详情空态', () => {
    const summary = teacher({ teacher_id: 'MIXED-1', name: 'Summary only' })
    const fullDetail = teacher({ teacher_id: 'MIXED-1', name: 'Full detail', tasks: [], ops_cases: [] })

    expect(teacherDetailPhase(null, summary, true, '')).toBe('LOADING')
    expect(teacherDetailPhase(null, summary, false, 'DETAIL_FAILED')).toBe('ERROR')
    expect(teacherDetailPhase(null, summary, false, '')).toBe('ERROR')
    expect(teacherDetailPhase(fullDetail, summary, false, '')).toBe('READY')
    expect(teacherDetailPhase(null, null, false, '')).toBe('EMPTY')
  })

  it('运营看板只信任 dashboard API，不从选择器列表重算', () => {
    const teacherOptions: TeacherOption[] = teachers.map((item) => ({
      teacher_id: item.teacher_id,
      name: item.name,
      data_mode: item.data_mode ?? 'UNKNOWN',
      employment_status: item.employment_status ?? null,
      graduation_state: item.graduation_state ?? 'IN_PROGRESS',
      task_issuance_blockers: [],
    }))
    const current = snapshot(teacherOptions)
    current.dashboard = {
      as_of: '2026-07-17T00:00:00Z',
      teacher_count: 1069,
      active_teacher_count: 810,
      settlement_pending_count: 0,
      issued_task_count: 0,
      unacknowledged_task_count: 0,
      open_case_count: 0,
      completed_execution_count: 0,
      dimension_averages: [],
      data_mode_counts: { REAL: 1, MIXED: 1, MOCK: 1 },
      employment_status_counts: { on: 2, off: 1 },
      funnel_by_employment_status: {
        on: {
          teacher_count: 800,
          graduation_score_reached_count: 480,
          graduation_criteria_met_count: 400,
          gold_eligible_count: 60,
        },
        off: {
          teacher_count: 200,
          graduation_score_reached_count: 60,
          graduation_criteria_met_count: 50,
          gold_eligible_count: 15,
        },
        hei: {
          teacher_count: 69,
          graduation_score_reached_count: 10,
          graduation_criteria_met_count: 5,
          gold_eligible_count: 5,
        },
      },
      graduation_score_reached_count: 550,
      graduation_criteria_met_count: 500,
      gold_score_reached_count: 390,
      gold_eligible_count: 80,
    }
    expect(operationalEvidenceSummary(current)).toMatchObject({
      dataModes: { REAL: 1, MIXED: 1, MOCK: 1 },
      employmentStatuses: { on: 2, off: 1 },
      graduationScoreReached: 550,
      graduationEligible: 500,
      goldScoreReached: 390,
      goldEligible: 80,
    })
    expect(operationalFunnelStages(current)).toEqual([
      { key: 'cohort', label: '培养名单', value: 1069, rate: 100, tone: 'default' },
      { key: 'graduation-score', label: '达到出营分数线', value: 550, rate: 51.4, tone: 'default' },
      { key: 'graduation-eligible', label: '满足最终出营资格', value: 500, rate: 46.8, tone: 'success' },
      { key: 'gold-eligible', label: '满足金牌资格', value: 80, rate: 7.5, tone: 'gold' },
    ])
    expect(operationalFunnelStages(current, 'on')).toEqual([
      { key: 'cohort', label: '培养名单', value: 800, rate: 100, tone: 'default' },
      { key: 'graduation-score', label: '达到出营分数线', value: 480, rate: 60, tone: 'default' },
      { key: 'graduation-eligible', label: '满足最终出营资格', value: 400, rate: 50, tone: 'success' },
      { key: 'gold-eligible', label: '满足金牌资格', value: 60, rate: 7.5, tone: 'gold' },
    ])
    expect(operationalFunnelStages(current, 'hei')).toEqual([
      { key: 'cohort', label: '培养名单', value: 69, rate: 100, tone: 'default' },
      { key: 'graduation-score', label: '达到出营分数线', value: 10, rate: 14.5, tone: 'default' },
      { key: 'graduation-eligible', label: '满足最终出营资格', value: 5, rate: 7.2, tone: 'success' },
      { key: 'gold-eligible', label: '满足金牌资格', value: 5, rate: 7.2, tone: 'gold' },
    ])
  })

  it('分页查询和教师选择器分别调用轻量接口', async () => {
    const teacherPage = {
      items: [],
      total: 0,
      page: 2,
      page_size: 24,
      total_pages: 0,
      filters: {
        keyword: 'ana',
        data_mode: 'MIXED',
        employment_status: 'on',
        available_data_modes: ['MIXED'],
        available_employment_statuses: ['on'],
      },
    }
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: async () => teacherPage })
      .mockResolvedValueOnce({ ok: true, json: async () => teachers })
    vi.stubGlobal('fetch', fetchMock)

    await api.teachers({ page: 2, page_size: 24, keyword: 'ana', data_mode: 'MIXED', employment_status: 'on' })
    await api.teacherOptions()

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      '/api/teachers?page=2&page_size=24&keyword=ana&data_mode=MIXED&employment_status=on',
      expect.objectContaining({ credentials: 'include' }),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      '/api/teacher-options',
      expect.objectContaining({ credentials: 'include' }),
    )
  })
})
