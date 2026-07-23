import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Avatar,
  Badge,
  Button,
  Card,
  Col,
  Descriptions,
  Drawer,
  Empty,
  Flex,
  Input,
  List,
  Pagination,
  Progress,
  Row,
  Select,
  Space,
  Spin,
  Tag,
  Typography,
} from 'antd'
import { CheckCircleOutlined, CloseCircleOutlined, ReloadOutlined, SearchOutlined, UserOutlined } from '@ant-design/icons'
import { api } from '../api'
import type { HardGate, HardGateGroup, MetricProvenance, SourceMode, Teacher, TeacherDataMode, TeacherPage } from '../types'
import { CaseStatus, EmptyPanel, PageHeader, PriorityTag } from '../components/Common'
import { employmentStatusLabel } from '../domain'

const { Text, Title } = Typography
export const TEACHER_PAGE_SIZE = 24

const graduationLabel: Record<string, string> = {
  IN_PROGRESS: '试用期进行中',
  SETTLEMENT_PENDING: '出营待结算',
  GRADUATED: '已出营',
}

const dataModeMeta: Record<string, { label: string; color: string }> = {
  REAL: { label: '证据已确认', color: 'green' },
  MIXED: { label: '多来源数据', color: 'blue' },
  MOCK: { label: '档案待接入', color: 'default' },
  UNKNOWN: { label: '证据待确认', color: 'default' },
}

export const teacherDataCoverageDescription = '当前积分与资格按各指标自身来源判断。国家和时区尚未接入且不参与当前积分与资格；首课预约时间为空时保留为空，不做推断。'
export const mixedTeacherDataDescription = '该教师档案由教师宽表、课程、共享任务和投诉等多来源数据合并；“多来源数据”只表示来源组合，不等于证据缺失。具体以各指标的来源标记为准。'

const sourceModeMeta: Record<string, { label: string; color: string }> = {
  REAL: { label: '来源已确认', color: 'green' },
  DERIVED_REAL: { label: '系统计算', color: 'cyan' },
  MIXED_DERIVED: { label: '部分证据待补', color: 'blue' },
  MOCK: { label: '证据待接入', color: 'default' },
  MOCK_SIMULATION: { label: '替代计算', color: 'default' },
  MOCK_PROXY: { label: '替代指标', color: 'orange' },
  SOURCE_MISSING: { label: '源数据缺失', color: 'default' },
  MISSING_INPUT_ZERO: { label: '缺失按 0 结算', color: 'orange' },
  SYSTEM_TASK_STATUS: { label: '任务状态计算', color: 'cyan' },
  TASK_BASELINE_INCOMPLETE: { label: '必修任务初始化异常', color: 'red' },
  TASK_STATUS_PARTIAL: { label: '必修任务初始化异常', color: 'red' },
  TASK_STATUS_INVALID: { label: '任务模板引用异常', color: 'red' },
  COMPLAINT_LEVEL_MAPPING_INCOMPLETE: { label: '投诉级别映射待补', color: 'gold' },
  LEGACY_DIMENSION: { label: '非当前维度数据', color: 'default' },
  MIXED: { label: '部分来源待补', color: 'blue' },
  UNKNOWN: { label: '证据待确认', color: 'default' },
}

const gateLabels: Record<string, string> = {
  REQUIRES_GRADUATION_CRITERIA: '需先满足最终出营资格',
  ALL_MANDATORY_GROWTH_TASKS_COMPLETED: 'G01–G10 必修成长任务全部完成',
  NO_L0_COMPLAINT: 'L0 投诉数为 0',
  MINIMUM_TOTAL_SCORE: '累计总分达到出营要求',
  MINIMUM_GOLD_TOTAL_SCORE: '累计总分达到金牌要求',
  MINIMUM_BASE_SCORE: '基础分达到出营要求',
  MINIMUM_COMPLETED_LESSONS: '30 天完课量达到要求',
  POSITIVE_USER_FEEDBACK: '用户反馈分为正',
  POSITIVE_RELIABILITY: '可靠性分为正',
  NO_SEVERE_REDLINE: '无严重红线记录',
  REQUIRED_BASE_SCORE: '基础分达到金牌要求',
  MINIMUM_USER_FEEDBACK_SCORE: '用户反馈分达到金牌要求',
  MAXIMUM_LATE_COUNT: '迟到次数不超过要求',
  MAXIMUM_EARLY_COUNT: '早退次数不超过要求',
  MAXIMUM_REAL_ABSENT_COUNT: '真实缺席次数不超过要求',
}

const currentGraduationGateCodes = new Set([
  'ALL_MANDATORY_GROWTH_TASKS_COMPLETED',
  'NO_L0_COMPLAINT',
  'MINIMUM_TOTAL_SCORE',
])

function finiteNumber(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined
}

export function externalScoreFromRaw(
  rawScore: number,
  graduationRawScore = 100,
  goldRawScore = 200,
  graduationExternalScore = 100,
  goldExternalScore = 200,
  policyVersion: string = 'v6',
): number {
  if (policyVersion === 'v3' || policyVersion === 'v4' || policyVersion === 'v5' || policyVersion === 'v6') return Math.min(rawScore, goldExternalScore)
  if (rawScore < graduationRawScore) return rawScore * graduationExternalScore / graduationRawScore
  if (rawScore < goldRawScore) {
    return graduationExternalScore
      + ((rawScore - graduationRawScore) * (goldExternalScore - graduationExternalScore)) / (goldRawScore - graduationRawScore)
  }
  return goldExternalScore
}

export function teacherScoreProjection(teacher: Teacher) {
  const raw = finiteNumber(teacher.raw_total_score)
    ?? finiteNumber(teacher.total_score)
    ?? (teacher.dimensions ?? []).reduce((total, item) => total + (finiteNumber(item.score) ?? 0), 0)
  const policyVersion = ['v2', 'v3', 'v4', 'v5', 'v6'].includes(teacher.score_policy_version ?? '')
    ? teacher.score_policy_version as 'v2' | 'v3' | 'v4' | 'v5' | 'v6'
    : 'v6'
  const hasEventScorePolicy = ['v2', 'v3', 'v4', 'v5', 'v6'].includes(teacher.score_policy_version ?? '')
  const graduationThreshold = hasEventScorePolicy ? finiteNumber(teacher.graduation_threshold) ?? 100 : 100
  const goldThreshold = hasEventScorePolicy
    ? finiteNumber(teacher.gold_threshold) ?? (policyVersion === 'v2' ? 660 : 200)
    : 200
  const graduationExternalScore = hasEventScorePolicy ? finiteNumber(teacher.graduation_external_score) ?? 100 : 100
  const goldExternalScore = hasEventScorePolicy ? finiteNumber(teacher.gold_external_score) ?? 200 : 200
  const external = finiteNumber(teacher.external_display_score)
    ?? externalScoreFromRaw(raw, graduationThreshold, goldThreshold, graduationExternalScore, goldExternalScore, policyVersion)
  return {
    raw,
    external,
    graduationThreshold,
    goldThreshold,
    graduationExternalScore,
    goldExternalScore,
    graduationScoreMet: teacher.graduation_score_threshold_met ?? raw >= graduationThreshold,
    graduationCriteriaMet: teacher.graduation_criteria_met,
    goldScoreMet: teacher.gold_score_threshold_met ?? raw >= goldThreshold,
    goldCriteriaMet: teacher.gold_criteria_met,
  }
}

function scoreText(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(1)
}

export interface SupplyMilestoneProjection {
  peakSlotCount: number
  threshold: 40
  score: 0 | 10
  maximumScore: 10
  met: boolean
  locked: boolean
  sourceMode?: SourceMode | string
}

export function teacherSupplyMilestone(teacher: Teacher): SupplyMilestoneProjection | null {
  if (teacher.score_policy_version !== 'v4' && teacher.score_policy_version !== 'v5' && teacher.score_policy_version !== 'v6') return null
  const capacityDimension = teacher.dimensions?.find((item) => item.code === 'CAPACITY')
  const milestoneComponent = capacityDimension?.components?.find((item) => item.code === 'CAPACITY_PEAK_SLOT_40')
  const peakSlotCount = finiteNumber(teacher.metric_inputs?.peak_slot_cnt)
    ?? finiteNumber(milestoneComponent?.value)
  if (peakSlotCount === undefined) return null
  const settledScore = finiteNumber(capacityDimension?.score)
  const locked = milestoneComponent?.milestone_achieved === true
    || (settledScore !== undefined && settledScore >= 10)
  const met = locked || peakSlotCount >= 40
  const provenance = teacher.metric_provenance?.peak_slot_cnt
  const sourceMode = provenance && typeof provenance === 'object'
    ? (provenance as MetricProvenance).source_mode
    : capacityDimension?.source_mode
  return {
    peakSlotCount,
    threshold: 40,
    score: met ? 10 : 0,
    maximumScore: 10,
    met,
    locked,
    sourceMode,
  }
}

function dataModeTag(mode?: TeacherDataMode | string) {
  const normalized = mode?.toUpperCase() ?? 'UNKNOWN'
  const meta = dataModeMeta[normalized] ?? { label: mode || dataModeMeta.UNKNOWN.label, color: 'default' }
  return <Tag color={meta.color}>{meta.label}</Tag>
}

export function teacherDataModeLabel(mode?: TeacherDataMode | string): string {
  const normalized = mode?.toUpperCase() ?? 'UNKNOWN'
  return dataModeMeta[normalized]?.label ?? mode ?? dataModeMeta.UNKNOWN.label
}

export function sourceModeLabel(mode?: SourceMode | string): string {
  return sourceModeMeta[mode ?? 'UNKNOWN']?.label ?? mode ?? sourceModeMeta.UNKNOWN.label
}

export function scorePolicyLabel(policyVersion?: string | null): string {
  if (!policyVersion) return '待确认'
  return policyVersion === 'v6' ? '当前口径' : '非当前数据（只读）'
}

function sourceModeTag(mode?: SourceMode | string, fieldLabel?: string) {
  const meta = sourceModeMeta[mode ?? 'UNKNOWN'] ?? { label: mode || sourceModeMeta.UNKNOWN.label, color: 'default' }
  return <Tag color={meta.color}>{fieldLabel ? `${fieldLabel}：` : ''}{meta.label}</Tag>
}

export function hardGateSourceMeta(item: HardGate): { label: string; color: string } {
  if (
    item.code === 'REQUIRES_GRADUATION_CRITERIA'
    && item.met === true
    && item.source_mode === 'MIXED_DERIVED'
  ) {
    return { label: '资格结果计算', color: 'cyan' }
  }
  return sourceModeMeta[item.source_mode ?? 'UNKNOWN']
    ?? { label: item.source_mode || sourceModeMeta.UNKNOWN.label, color: 'default' }
}

function hardGateSourceTag(item: HardGate) {
  const meta = hardGateSourceMeta(item)
  return <Tag color={meta.color}>{meta.label}</Tag>
}

export function profileFactDisplay(value: string | null | undefined, fallback: string): string {
  const normalized = typeof value === 'string' ? value.trim() : ''
  if (!normalized || normalized.toLowerCase() === 'unknown') return fallback
  return normalized
}

export function completionEvidenceLabel(value: boolean | null | undefined): string {
  if (value === true) return '已完成'
  if (value === false) return '明确未完成'
  return '暂无数据'
}

function profileProvenance(teacher: Teacher, field: string): MetricProvenance | undefined {
  const value = teacher.profile_provenance?.[field]
  return value && typeof value === 'object' ? value as MetricProvenance : undefined
}

function criteriaTag(label: string, met: boolean | undefined) {
  if (met === undefined) return <Tag>{label}：待计算</Tag>
  return <Tag color={met ? 'success' : 'default'} icon={met ? <CheckCircleOutlined /> : <CloseCircleOutlined />}>{label}：{met ? '是' : '否'}</Tag>
}

function isHardGateGroup(value: unknown): value is HardGateGroup {
  if (!value || typeof value !== 'object') return false
  const candidate = value as Partial<HardGateGroup>
  return typeof candidate.met === 'boolean' && Array.isArray(candidate.items)
}

export function visibleGraduationGateItems(items: HardGate[], policyVersion?: string | null): HardGate[] {
  if (policyVersion !== 'v5' && policyVersion !== 'v6') return items
  return items.filter((item) => currentGraduationGateCodes.has(item.code))
}

export function hardGateLabel(item: HardGate): string {
  return gateLabels[item.code] ?? item.metric ?? item.code
}

function hardGateGroups(teacher: Teacher): Array<{ key: string; title: string; group: HardGateGroup }> {
  const gates = teacher.hard_gates
  if (!gates || Array.isArray(gates) || typeof gates !== 'object') return []
  const record = gates as Record<string, unknown>
  const groups: Array<{ key: string; title: string; group: HardGateGroup }> = []
  if (isHardGateGroup(record.graduation)) {
    groups.push({
      key: 'graduation',
      title: '最终出营资格硬门槛',
      group: {
        ...record.graduation,
        items: visibleGraduationGateItems(record.graduation.items, teacher.score_policy_version),
      },
    })
  }
  if (isHardGateGroup(record.gold)) groups.push({ key: 'gold', title: '最终金牌资格硬门槛', group: record.gold })
  return groups
}

function formatGateValue(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function GateList({ items }: { items: HardGate[] }) {
  return (
    <List
      size="small"
      dataSource={items}
      locale={{ emptyText: '暂无门槛明细' }}
      renderItem={(item) => (
        <List.Item extra={hardGateSourceTag(item)}>
          <List.Item.Meta
            avatar={item.met ? <CheckCircleOutlined style={{ color: '#178c76' }} /> : <CloseCircleOutlined style={{ color: '#c94945' }} />}
            title={hardGateLabel(item)}
            description={`当前 ${formatGateValue(item.actual)} · 规则 ${item.operator ?? '—'} ${formatGateValue(item.threshold)}`}
          />
        </List.Item>
      )}
    />
  )
}

const taskStatusMeta: Record<string, { label: string; color: string }> = {
  ASSIGNED: { label: '未完成', color: 'default' },
  VIEWED: { label: '已查看', color: 'blue' },
  IN_PROGRESS: { label: '进行中', color: 'processing' },
  SUBMITTED: { label: '已提交', color: 'cyan' },
  UNDER_REVIEW: { label: '审核中', color: 'gold' },
  COMPLETED: { label: '已完成', color: 'success' },
  FAILED: { label: '未通过', color: 'error' },
  EXPIRED: { label: '已过期', color: 'default' },
  WAIVED: { label: '已豁免', color: 'purple' },
  CANCELLED: { label: '已取消', color: 'default' },
}

function taskStatusTag(status: string) {
  const meta = taskStatusMeta[status] ?? { label: status, color: 'default' }
  return <Tag color={meta.color}>{meta.label}</Tag>
}

function TeacherIdentity({
  teacher,
  profileState,
}: {
  teacher: Teacher
  profileState: 'LOADING' | 'READY' | 'ERROR'
}) {
  const countryProvenance = profileState === 'READY' ? profileProvenance(teacher, 'country') : undefined
  const timezoneProvenance = profileState === 'READY' ? profileProvenance(teacher, 'timezone') : undefined
  const profileText = profileState === 'LOADING'
    ? `${teacher.teacher_id} · 正在加载档案…`
    : profileState === 'ERROR'
      ? `${teacher.teacher_id} · 档案详情不可用`
      : `${teacher.teacher_id} · ${profileFactDisplay(teacher.country, '国家待确认')} · ${profileFactDisplay(teacher.timezone, '时区待确认')}`

  return (
    <Flex gap={14} align="center" wrap="wrap">
      <Avatar size={56} className="teacher-avatar">{teacher.avatar || teacher.name?.slice(0, 1) || '?'}</Avatar>
      <div style={{ flex: 1 }}><Title level={4}>{teacher.name}</Title><Text type="secondary">{profileText}</Text></div>
      {dataModeTag(teacher.data_mode)}
      <Tag>{employmentStatusLabel(teacher.employment_status)}</Tag>
      {countryProvenance?.source_mode ? sourceModeTag(countryProvenance.source_mode, '国家') : null}
      {timezoneProvenance?.source_mode ? sourceModeTag(timezoneProvenance.source_mode, '时区') : null}
    </Flex>
  )
}

const emptyTeacherPage: TeacherPage = {
  items: [],
  total: 0,
  page: 1,
  page_size: TEACHER_PAGE_SIZE,
  total_pages: 0,
  filters: {
    keyword: '',
    data_mode: null,
    employment_status: null,
    available_data_modes: [],
    available_employment_statuses: [],
  },
}

export function pendingTeacherPage(
  previous: TeacherPage,
  page: number,
  keyword: string,
  dataMode: string,
  employmentStatus: string,
): TeacherPage {
  return {
    items: [],
    total: 0,
    page,
    page_size: TEACHER_PAGE_SIZE,
    total_pages: 0,
    filters: {
      ...previous.filters,
      keyword: keyword.trim(),
      data_mode: dataMode === 'ALL' ? null : dataMode,
      employment_status: employmentStatus === 'ALL' ? null : employmentStatus,
    },
  }
}

export type TeacherDetailPhase = 'EMPTY' | 'LOADING' | 'ERROR' | 'READY'

export function teacherDetailPhase(
  detail: Teacher | null,
  summary: Teacher | null,
  loading: boolean,
  error: string,
): TeacherDetailPhase {
  if (!detail && !summary) return 'EMPTY'
  if (loading) return 'LOADING'
  if (error || !detail) return 'ERROR'
  return 'READY'
}

export default function Teacher360({ initialTeacherId }: { initialTeacherId?: string }) {
  const [keyword, setKeyword] = useState(initialTeacherId ?? '')
  const [dataMode, setDataMode] = useState('ALL')
  const [employmentStatus, setEmploymentStatus] = useState('ALL')
  const [page, setPage] = useState(1)
  const [teacherPage, setTeacherPage] = useState<TeacherPage>(emptyTeacherPage)
  const [listLoading, setListLoading] = useState(false)
  const [hasLoaded, setHasLoaded] = useState(false)
  const [listError, setListError] = useState('')
  const [selectedId, setSelectedId] = useState<string>()
  const [selectedSummary, setSelectedSummary] = useState<Teacher | null>(null)
  const [detail, setDetail] = useState<Teacher | null>(null)
  const [detailError, setDetailError] = useState('')
  const [loading, setLoading] = useState(false)

  const visibleTeachers = teacherPage.items
  const detailPhase = teacherDetailPhase(detail, selectedSummary, loading, detailError)
  const readyDetail = detail as Teacher
  const orderedAssignments = useMemo(
    () => [...(detail?.task_assignments ?? [])].sort((left, right) => left.task_code.localeCompare(right.task_code)),
    [detail?.task_assignments],
  )
  const detailGateGroups = useMemo(() => detail ? hardGateGroups(detail) : [], [detail])
  const detailSupplyMilestone = useMemo(() => detail ? teacherSupplyMilestone(detail) : null, [detail])
  const dataModeOptions = useMemo(() => {
    const values = teacherPage.filters.available_data_modes
    return [{ value: 'ALL', label: '全部数据来源' }, ...values.map((value) => ({ value, label: dataModeMeta[value]?.label ?? value }))]
  }, [teacherPage.filters.available_data_modes])
  const employmentOptions = useMemo(() => {
    const values = teacherPage.filters.available_employment_statuses
    return [{ value: 'ALL', label: '全部在职状态' }, ...values.map((value) => ({ value, label: employmentStatusLabel(value) }))]
  }, [teacherPage.filters.available_employment_statuses])

  useEffect(() => {
    setKeyword(initialTeacherId ?? '')
    setPage(1)
  }, [initialTeacherId])

  const loadTeachers = useCallback(async (overrides: { page?: number } = {}) => {
    const requestedPage = overrides.page ?? page
    setListLoading(true)
    setListError('')
    setTeacherPage((previous) => pendingTeacherPage(previous, requestedPage, keyword, dataMode, employmentStatus))
    try {
      const response = await api.teachers({
        page: requestedPage,
        page_size: TEACHER_PAGE_SIZE,
        keyword: keyword.trim() || undefined,
        data_mode: dataMode === 'ALL' ? undefined : dataMode,
        employment_status: employmentStatus === 'ALL' ? undefined : employmentStatus,
      })
      setTeacherPage(response)
      setHasLoaded(true)
    } catch {
      setListError('教师列表加载失败，请稍后重试。')
    } finally {
      setListLoading(false)
    }
  }, [dataMode, employmentStatus, keyword, page])

  useEffect(() => {
    if (!selectedId) return
    let active = true
    setDetail(null)
    setDetailError('')
    setLoading(true)
    api.teacher(selectedId)
      .then((response) => { if (active) setDetail(response) })
      .catch(() => { if (active) setDetailError('详情接口暂时不可用，当前展示列表快照。') })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [selectedId])

  return (
    <div className="page-shell">
      <PageHeader
        eyebrow="教师与证据"
        title="教师档案"
        description="从教师当前进度进入积分、资格门槛、任务和运营介入证据；达到分数线不等于最终资格。"
        actions={(
          <Space wrap>
            <Input
              allowClear
              prefix={<SearchOutlined />}
              placeholder="姓名 / Teacher ID / 在职状态"
              value={keyword}
              onChange={(event) => { setKeyword(event.target.value); setPage(1) }}
              style={{ width: 260 }}
            />
            <Select value={dataMode} options={dataModeOptions} onChange={(value) => { setDataMode(value); setPage(1) }} style={{ width: 240 }} />
            <Select value={employmentStatus} options={employmentOptions} onChange={(value) => { setEmploymentStatus(value); setPage(1) }} style={{ width: 180 }} />
            <Button type="primary" icon={<ReloadOutlined />} loading={listLoading} onClick={() => loadTeachers().catch(() => undefined)}>更新教师</Button>
          </Space>
        )}
      />
      <Alert
        type="info"
        showIcon
        className="semantic-alert"
        message="数据覆盖说明"
        description={teacherDataCoverageDescription}
      />

      <Flex justify="space-between" align="center" wrap="wrap" gap={12}>
        <Text type="secondary">{hasLoaded ? `共 ${teacherPage.total} 位教师；服务端仅返回第 ${teacherPage.page} 页（每页最多 ${teacherPage.page_size} 位）` : '尚未读取教师档案，点击“更新教师”'}</Text>
        <Space>{dataModeTag('REAL')}{dataModeTag('MIXED')}{dataModeTag('MOCK')}</Space>
      </Flex>

      {listError ? <Alert type="error" showIcon message={listError} /> : null}
      {listLoading ? <Card><Flex justify="center"><Spin /></Flex></Card> : listError ? null : !visibleTeachers.length ? <Card><Empty description={hasLoaded ? '没有符合条件的教师' : '尚未读取教师，点击“更新教师”'} /></Card> : (
        <Row gutter={[16, 16]}>
          {visibleTeachers.map((teacher) => {
            const score = teacherScoreProjection(teacher)
            const dimensions = teacher.dimensions ?? []
            return (
              <Col xs={24} md={12} xl={8} key={teacher.teacher_id}>
                <Card hoverable className="teacher-card" onClick={() => { setSelectedSummary(teacher); setSelectedId(teacher.teacher_id) }}>
                  <Flex justify="space-between" align="flex-start" gap={12}>
                    <Flex gap={12} align="center">
                      <Avatar size={48} className="teacher-avatar">{teacher.avatar || teacher.name?.slice(0, 1) || '?'}</Avatar>
                      <div><Title level={5}>{teacher.name}</Title><Text type="secondary">{teacher.teacher_id}{teacher.camp_day !== undefined ? ` · Day ${teacher.camp_day}` : ''}</Text></div>
                    </Flex>
                    <Space direction="vertical" size={3} align="end">
                      {dataModeTag(teacher.data_mode)}
                      <Badge status={teacher.graduation_state === 'SETTLEMENT_PENDING' ? 'warning' : 'processing'} text={graduationLabel[teacher.graduation_state ?? ''] ?? teacher.graduation_state ?? '状态待确认'} />
                    </Space>
                  </Flex>

                  <Flex gap={22} align="end" style={{ marginTop: 20 }}>
                    <div className="teacher-score-line" style={{ marginTop: 0 }}><strong>{scoreText(score.raw)}</strong><span>累计总分</span></div>
                    <div><Text type="secondary">教师端展示</Text><div><Text strong style={{ fontSize: 23 }}>{scoreText(score.external)}</Text><Text type="secondary"> / 200</Text></div></div>
                  </Flex>
                  <Progress percent={Math.max(0, Math.min(100, (score.external / 200) * 100))} showInfo={false} strokeColor={score.goldScoreMet ? '#d18b26' : '#176b87'} />

                  <Flex gap={6} wrap="wrap" style={{ marginTop: 10 }}>
                    {criteriaTag(`出营分数线 ≥ ${scoreText(score.graduationThreshold)}`, score.graduationScoreMet)}
                    {criteriaTag('最终出营资格', score.graduationCriteriaMet)}
                    {criteriaTag(`金牌分数线 ≥ ${scoreText(score.goldThreshold)}`, score.goldScoreMet)}
                    {criteriaTag('最终金牌资格', score.goldCriteriaMet)}
                  </Flex>

                  <div className="dimension-mini-grid">
                    {dimensions.map((item) => (
                      <div key={item.code}>
                        <span>{item.label}</span><b>{scoreText(item.score)}</b><small>{sourceModeLabel(item.source_mode)}</small>
                      </div>
                    ))}
                  </div>
                  <Flex gap={6} wrap="wrap" className="risk-row">
                    {(teacher.risk_tags ?? []).map((tag) => <Tag color={tag.includes('待结算') ? 'gold' : 'volcano'} key={tag}>{tag}</Tag>)}
                    {teacher.employment_status ? <Tag>{employmentStatusLabel(teacher.employment_status)}</Tag> : null}
                  </Flex>
                  <div className="next-action"><UserOutlined /> <span>{teacher.next_best_action || '查看证据、任务与运营事项'}</span></div>
                </Card>
              </Col>
            )
          })}
        </Row>
      )}

      {teacherPage.total > TEACHER_PAGE_SIZE ? (
        <Flex justify="center"><Pagination
          current={page}
          pageSize={TEACHER_PAGE_SIZE}
          total={teacherPage.total}
          showSizeChanger={false}
          onChange={(nextPage) => {
            setPage(nextPage)
            loadTeachers({ page: nextPage }).catch(() => undefined)
          }}
          showQuickJumper
        /></Flex>
      ) : null}

      <Drawer title="教师证据详情" width={860} open={Boolean(selectedId)} onClose={() => { setSelectedId(undefined); setSelectedSummary(null); setDetail(null); setDetailError('') }}>
        {detailPhase === 'EMPTY' ? <Empty description="未找到教师详情" /> : detailPhase === 'LOADING' ? (
          <Space direction="vertical" size={18} style={{ width: '100%' }}>
            <TeacherIdentity teacher={(detail ?? selectedSummary)!} profileState="LOADING" />
            <Card><Flex justify="center"><Spin tip="正在加载完整证据…"><div /></Spin></Flex></Card>
          </Space>
        ) : detailPhase === 'ERROR' ? (
          <Space direction="vertical" size={18} style={{ width: '100%' }}>
            <TeacherIdentity teacher={selectedSummary!} profileState="ERROR" />
            <Alert
              type="error"
              showIcon
              message={detailError || '教师详情不可用'}
              description="任务、运营事项、硬门槛和来源明细均未加载，因此本页不会把未知事实显示为“暂无”或 0。"
            />
          </Space>
        ) : (
          <Space direction="vertical" size={18} style={{ width: '100%' }}>
            <TeacherIdentity teacher={readyDetail} profileState="READY" />

            {readyDetail.data_mode === 'MIXED' ? <Alert type="info" showIcon message={mixedTeacherDataDescription} /> : null}

            {(() => {
              const score = teacherScoreProjection(readyDetail)
              return (
                <Card size="small" title="积分与资格快照">
                  <Descriptions size="small" bordered column={{ xs: 1, md: 2 }}>
                    <Descriptions.Item label="累计总分（未封顶）">{scoreText(score.raw)}</Descriptions.Item>
                    <Descriptions.Item label="教师端展示（封顶 200）">{scoreText(score.external)}</Descriptions.Item>
                    <Descriptions.Item label="基础分">{finiteNumber(readyDetail.base_score) ?? '—'}</Descriptions.Item>
                    <Descriptions.Item label="计分口径">{scorePolicyLabel(readyDetail.score_policy_version)}</Descriptions.Item>
                    <Descriptions.Item label="来源批次">{readyDetail.source_batch_id ?? '暂未绑定批次'}</Descriptions.Item>
                    <Descriptions.Item label="来源快照">{readyDetail.source_snapshot_label ?? '未标注'}</Descriptions.Item>
                  </Descriptions>
                  <Flex gap={6} wrap="wrap" style={{ marginTop: 14 }}>
                    {criteriaTag(`出营分数线命中 · ≥ ${scoreText(score.graduationThreshold)}`, score.graduationScoreMet)}
                    {criteriaTag('最终出营资格', score.graduationCriteriaMet)}
                    {criteriaTag(`金牌分数线命中 · ≥ ${scoreText(score.goldThreshold)}`, score.goldScoreMet)}
                    {criteriaTag('最终金牌资格', score.goldCriteriaMet)}
                  </Flex>
                </Card>
              )
            })()}

            <Card size="small" title="教师资料与准备度">
              <Descriptions size="small" bordered column={{ xs: 1, md: 3 }}>
                <Descriptions.Item label="首次约课日期">{readyDetail.first_booked_date ?? '暂无数据'}</Descriptions.Item>
                <Descriptions.Item label="TESOL">{completionEvidenceLabel(readyDetail.is_cpl_tesol)}</Descriptions.Item>
                <Descriptions.Item label="自我介绍">{completionEvidenceLabel(readyDetail.is_self_introduce)}</Descriptions.Item>
              </Descriptions>
              <Text type="secondary">TESOL 和自我介绍只是 G01 的组成证据，任一单项完成都不等于 G01 已完成。</Text>
            </Card>

            <Card size="small" title="五维积分与来源">
              {detailSupplyMilestone ? (
                <Card
                  size="small"
                  type="inner"
                  title="供给积分里程碑"
                  extra={sourceModeTag(detailSupplyMilestone.sourceMode)}
                  style={{ marginBottom: 16 }}
                >
                  <Flex justify="space-between" align="center" gap={16} wrap="wrap">
                    <div>
                      <Text type="secondary">Peak slots</Text>
                      <div><Text strong style={{ fontSize: 24 }}>{scoreText(detailSupplyMilestone.peakSlotCount)}</Text><Text type="secondary"> / {detailSupplyMilestone.threshold}</Text></div>
                    </div>
                    <div>
                      <Text type="secondary">供给分</Text>
                      <div><Text strong style={{ fontSize: 24 }}>{detailSupplyMilestone.score}</Text><Text type="secondary"> / {detailSupplyMilestone.maximumScore}</Text></div>
                    </div>
                    <Tag color={detailSupplyMilestone.met ? 'success' : 'default'}>{detailSupplyMilestone.locked ? '首次达成 · 已永久锁定' : detailSupplyMilestone.met ? '已达成 · 待锁定结算' : '尚未达成'}</Tag>
                  </Flex>
                  <Progress percent={Math.max(0, Math.min(100, detailSupplyMilestone.peakSlotCount / detailSupplyMilestone.threshold * 100))} showInfo={false} style={{ marginTop: 10 }} />
                  <Text type="secondary">规则：peak_slot_cnt 首次达到 40 → 供给分 10/10，结算后永久保留；个性化改善任务不在这里结分。</Text>
                </Card>
              ) : null}
              {!readyDetail.dimensions?.length ? <EmptyPanel description="暂无维度投影" /> : (
                <div className="dimension-detail-grid">
                  {readyDetail.dimensions.map((item) => (
                    <div key={item.code}>
                      <Flex justify="space-between" align="center"><span>{item.label}</span>{sourceModeTag(item.source_mode)}</Flex>
                      <strong>{scoreText(item.score)}</strong>
                      {item.components?.length ? (
                        <List
                          size="small"
                          dataSource={item.components}
                          renderItem={(component) => (
                            <List.Item style={{ paddingInline: 0 }} extra={sourceModeTag(component.source_mode)}>
                              <Text type="secondary">
                                {component.metric || component.code}：{formatGateValue(component.value)}
                                {component.metric === 'perfect_cnt' && finiteNumber(component.points_per_unit) !== undefined
                                  ? ` × ${scoreText(component.points_per_unit!)}`
                                  : ''}
                                {' '}→ {scoreText(component.score)} 分
                              </Text>
                            </List.Item>
                          )}
                        />
                      ) : <small>{item.source_field || item.source_note || '维度来源明细待接口补充'}</small>}
                    </div>
                  ))}
                </div>
              )}
            </Card>

            <Card size="small" title="硬门槛证据">
              {!detailGateGroups.length ? <EmptyPanel description="暂无硬门槛明细" /> : detailGateGroups.map(({ key, title, group }) => (
                <Card key={key} size="small" title={title} extra={criteriaTag('整组通过', group.met)} style={{ marginBottom: 12 }}>
                  <GateList items={group.items} />
                </Card>
              ))}
            </Card>

            <Card size="small" title={`任务进度（${readyDetail.task_assignments?.length ?? 0}）`}>
              {!readyDetail.task_assignments?.length ? <EmptyPanel description="暂无任务" /> : (
                <List dataSource={orderedAssignments} renderItem={(assignment) => (
                  <List.Item extra={taskStatusTag(assignment.status)}>
                    <List.Item.Meta
                      title={<Space wrap><Text code>{assignment.task_code}</Text><span>{assignment.title ?? assignment.task_code}</span><Tag color={assignment.task_kind === 'FIXED_GROWTH' ? 'purple' : 'blue'}>{assignment.task_kind === 'FIXED_GROWTH' ? '必修成长' : '个性化改善'}</Tag><PriorityTag priority={assignment.priority} /></Space>}
                      description={assignment.why}
                    />
                  </List.Item>
                )} />
              )}
            </Card>
            <Card size="small" title={`运营事项（${readyDetail.ops_cases?.length ?? 0}）`}>
              {!readyDetail.ops_cases?.length ? <EmptyPanel description="暂无运营事项" /> : readyDetail.ops_cases.map((item) => (
                <Flex className="drawer-case-row" key={item.case_id} justify="space-between" gap={12} wrap="wrap"><div><strong>{item.case_type}</strong><div><Text type="secondary">{item.summary}</Text></div></div><CaseStatus status={item.status} externalStatus={item.external_action_status} /></Flex>
              ))}
            </Card>
          </Space>
        )}
      </Drawer>
    </div>
  )
}
