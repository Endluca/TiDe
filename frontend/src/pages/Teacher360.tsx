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
import { api, isRequestCancelled } from '../api'
import type { HardGate, HardGateGroup, MetricProvenance, SourceMode, Teacher, TeacherDataMode, TeacherPage } from '../types'
import { CaseStatus, EmptyPanel, PageHeader, PriorityTag } from '../components/Common'
import { employmentStatusLabel } from '../domain'
import { useI18n } from '../i18n'
import { useLatestRequest } from '../useLatestRequest'
import type { AppLocale } from '../i18n'

const { Text, Title } = Typography
export const TEACHER_PAGE_SIZE = 24

const graduationLabel: Record<string, string> = {
  IN_PROGRESS: '试用期进行中',
  SETTLEMENT_PENDING: '出营待结算',
  GRADUATED: '已出营',
}
const graduationLabelEn: Record<string, string> = {
  IN_PROGRESS: 'Trial in progress',
  SETTLEMENT_PENDING: 'Graduation settlement pending',
  GRADUATED: 'Graduated',
}

function dimensionLabel(code: string, fallback: string, locale: AppLocale): string {
  if (locale !== 'en-US') return fallback
  return {
    RELIABILITY: 'Reliability',
    USER_FEEDBACK: 'User feedback',
    CLASS_QUALITY: 'Classroom quality',
    CAPACITY: 'Peak availability',
    NEW_TEACHER_TASK: 'Mandatory tasks',
    NEW_TEACHER_TASKS: 'Mandatory tasks',
  }[code] ?? fallback
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
  NOT_APPLICABLE: { label: '当前无加分项', color: 'default' },
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
  ALL_MANDATORY_GROWTH_TASKS_COMPLETED: '当前 9 项必修成长任务全部完成',
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
  ZERO_EARLY_COUNT: '早退次数为 0',
  ZERO_ABSENT_COUNT: '缺席次数为 0',
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
  policyVersion: string = 'v1',
): number {
  if (policyVersion === 'v1' || policyVersion === 'v3' || policyVersion === 'v4' || policyVersion === 'v5' || policyVersion === 'v6' || policyVersion === 'v7' || policyVersion === 'v8' || policyVersion === 'v9' || policyVersion === 'v10') return Math.min(rawScore, goldExternalScore)
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
  const policyVersion = ['v1', 'v2', 'v3', 'v4', 'v5', 'v6', 'v7', 'v8', 'v9', 'v10'].includes(teacher.score_policy_version ?? '')
    ? teacher.score_policy_version as 'v1' | 'v2' | 'v3' | 'v4' | 'v5' | 'v6' | 'v7' | 'v8' | 'v9' | 'v10'
    : 'v1'
  const hasEventScorePolicy = ['v1', 'v2', 'v3', 'v4', 'v5', 'v6', 'v7', 'v8', 'v9', 'v10'].includes(teacher.score_policy_version ?? '')
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
    graduationCriteriaMet: teacher.graduation_qualified
      ?? teacher.graduation_criteria_met,
    goldScoreMet: teacher.gold_score_threshold_met ?? raw >= goldThreshold,
    goldCriteriaMet: teacher.gold_qualified ?? teacher.gold_criteria_met,
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
  if (teacher.score_policy_version !== 'v1' && teacher.score_policy_version !== 'v4' && teacher.score_policy_version !== 'v5' && teacher.score_policy_version !== 'v6' && teacher.score_policy_version !== 'v7' && teacher.score_policy_version !== 'v8' && teacher.score_policy_version !== 'v9' && teacher.score_policy_version !== 'v10') return null
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

const dataModeLabelsEn: Record<string, string> = {
  REAL: 'Evidence confirmed',
  MIXED: 'Multiple data sources',
  MOCK: 'Profile integration pending',
  UNKNOWN: 'Evidence pending',
}

const sourceModeLabelsEn: Record<string, string> = {
  REAL: 'Source confirmed',
  DERIVED_REAL: 'System calculated',
  MIXED_DERIVED: 'Partial evidence',
  MOCK: 'Evidence integration pending',
  MOCK_SIMULATION: 'Alternative calculation',
  MOCK_PROXY: 'Proxy metric',
  SOURCE_MISSING: 'Source data missing',
  NOT_APPLICABLE: 'No current scoring item',
  MISSING_INPUT_ZERO: 'Missing input settled as 0',
  SYSTEM_TASK_STATUS: 'Task-status calculation',
  TASK_BASELINE_INCOMPLETE: 'Mandatory-task initialization error',
  TASK_STATUS_PARTIAL: 'Mandatory-task initialization error',
  TASK_STATUS_INVALID: 'Invalid task-template reference',
  COMPLAINT_LEVEL_MAPPING_INCOMPLETE: 'Complaint-level mapping incomplete',
  LEGACY_DIMENSION: 'Non-current dimension data',
  MIXED: 'Partial source data',
  UNKNOWN: 'Evidence pending',
}

function dataModeTag(mode?: TeacherDataMode | string) {
  const { locale } = useI18n()
  const normalized = mode?.toUpperCase() ?? 'UNKNOWN'
  const meta = dataModeMeta[normalized] ?? { label: mode || dataModeMeta.UNKNOWN.label, color: 'default' }
  return <Tag color={meta.color}>{locale === 'en-US' ? dataModeLabelsEn[normalized] ?? mode ?? dataModeLabelsEn.UNKNOWN : meta.label}</Tag>
}

export function teacherDataModeLabel(mode?: TeacherDataMode | string, locale: AppLocale = 'zh-CN'): string {
  const normalized = mode?.toUpperCase() ?? 'UNKNOWN'
  return locale === 'en-US'
    ? dataModeLabelsEn[normalized] ?? mode ?? dataModeLabelsEn.UNKNOWN
    : dataModeMeta[normalized]?.label ?? mode ?? dataModeMeta.UNKNOWN.label
}

export function sourceModeLabel(mode?: SourceMode | string, locale: AppLocale = 'zh-CN'): string {
  return locale === 'en-US'
    ? sourceModeLabelsEn[mode ?? 'UNKNOWN'] ?? mode ?? sourceModeLabelsEn.UNKNOWN
    : sourceModeMeta[mode ?? 'UNKNOWN']?.label ?? mode ?? sourceModeMeta.UNKNOWN.label
}

export function scorePolicyLabel(policyVersion?: string | null, locale: AppLocale = 'zh-CN'): string {
  if (!policyVersion) return locale === 'en-US' ? 'Pending' : '待确认'
  return policyVersion === 'v1'
    ? (locale === 'en-US' ? 'Current policy' : '当前口径')
    : (locale === 'en-US' ? 'Non-current data (read-only)' : '非当前数据（只读）')
}

function sourceModeTag(mode?: SourceMode | string, fieldLabel?: string) {
  const { locale } = useI18n()
  const meta = sourceModeMeta[mode ?? 'UNKNOWN'] ?? { label: mode || sourceModeMeta.UNKNOWN.label, color: 'default' }
  const label = locale === 'en-US' ? sourceModeLabelsEn[mode ?? 'UNKNOWN'] ?? mode ?? sourceModeLabelsEn.UNKNOWN : meta.label
  return <Tag color={meta.color}>{fieldLabel ? `${fieldLabel}: ` : ''}{label}</Tag>
}

export function reconciliationLabel(status?: string, locale: AppLocale = 'zh-CN'): string | null {
  const labels: Record<string, string> = {
    PARTIAL: '部分课程可归因',
    MISMATCH: '课程明细与教师汇总不一致',
    SOURCE_MISSING: '暂无逐课归因依据',
  }
  const labelsEn: Record<string, string> = {
    PARTIAL: 'Partial lesson attribution',
    MISMATCH: 'Lesson details do not match teacher total',
    SOURCE_MISSING: 'No lesson-level attribution evidence',
  }
  return status ? (locale === 'en-US' ? labelsEn : labels)[status] ?? null : null
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
  const { locale } = useI18n()
  const meta = hardGateSourceMeta(item)
  const label = item.code === 'REQUIRES_GRADUATION_CRITERIA' && item.met === true && item.source_mode === 'MIXED_DERIVED'
    ? (locale === 'en-US' ? 'Qualification result calculation' : meta.label)
    : sourceModeLabel(item.source_mode, locale)
  return <Tag color={meta.color}>{label}</Tag>
}

export function profileFactDisplay(value: string | null | undefined, fallback: string): string {
  const normalized = typeof value === 'string' ? value.trim() : ''
  if (!normalized || normalized.toLowerCase() === 'unknown') return fallback
  return normalized
}

export function completionEvidenceLabel(value: boolean | null | undefined, locale: AppLocale = 'zh-CN'): string {
  if (value === true) return locale === 'en-US' ? 'Completed' : '已完成'
  if (value === false) return locale === 'en-US' ? 'Confirmed incomplete' : '明确未完成'
  return locale === 'en-US' ? 'No data' : '暂无数据'
}

function profileProvenance(teacher: Teacher, field: string): MetricProvenance | undefined {
  const value = teacher.profile_provenance?.[field]
  return value && typeof value === 'object' ? value as MetricProvenance : undefined
}

function criteriaTag(label: string, met: boolean | undefined) {
  const { t } = useI18n()
  if (met === undefined) return <Tag>{label}: {t('待计算', 'Pending')}</Tag>
  return <Tag color={met ? 'success' : 'default'} icon={met ? <CheckCircleOutlined /> : <CloseCircleOutlined />}>{label}: {met ? t('是', 'Yes') : t('否', 'No')}</Tag>
}

function isHardGateGroup(value: unknown): value is HardGateGroup {
  if (!value || typeof value !== 'object') return false
  const candidate = value as Partial<HardGateGroup>
  return typeof candidate.met === 'boolean' && Array.isArray(candidate.items)
}

export function visibleGraduationGateItems(items: HardGate[], policyVersion?: string | null): HardGate[] {
  if (policyVersion !== 'v1' && policyVersion !== 'v5' && policyVersion !== 'v6' && policyVersion !== 'v7' && policyVersion !== 'v8' && policyVersion !== 'v9' && policyVersion !== 'v10') return items
  return items.filter((item) => currentGraduationGateCodes.has(item.code))
}

const gateLabelsEn: Record<string, string> = {
  REQUIRES_GRADUATION_CRITERIA: 'Graduation qualification required',
  ALL_MANDATORY_GROWTH_TASKS_COMPLETED: 'All 9 mandatory growth tasks completed',
  NO_L0_COMPLAINT: 'L0 complaint count is 0',
  MINIMUM_TOTAL_SCORE: 'Total score meets graduation requirement',
  MINIMUM_GOLD_TOTAL_SCORE: 'Total score meets Gold requirement',
  MINIMUM_BASE_SCORE: 'Base score meets graduation requirement',
  MINIMUM_COMPLETED_LESSONS: '30-day completed lessons meet requirement',
  POSITIVE_USER_FEEDBACK: 'User feedback score is positive',
  POSITIVE_RELIABILITY: 'Reliability score is positive',
  NO_SEVERE_REDLINE: 'No severe red-line record',
  REQUIRED_BASE_SCORE: 'Base score meets Gold requirement',
  MINIMUM_USER_FEEDBACK_SCORE: 'User feedback score meets Gold requirement',
  MAXIMUM_LATE_COUNT: 'Late count is within limit',
  ZERO_EARLY_COUNT: 'Early-leave count is 0',
  ZERO_ABSENT_COUNT: 'Absence count is 0',
  MAXIMUM_EARLY_COUNT: 'Early-leave count is within limit',
  MAXIMUM_REAL_ABSENT_COUNT: 'Real absence count is within limit',
}

export function hardGateLabel(item: HardGate, locale: AppLocale = 'zh-CN'): string {
  return (locale === 'en-US' ? gateLabelsEn : gateLabels)[item.code] ?? item.metric ?? item.code
}

const scoreComponentLabels: Record<string, string> = {
  PERFECT_COMPLETED: '完美完课',
  perfect_cnt: '完美完课',
  CLASS_QUALITY_HARDWARE: '硬件质量',
  lesson_hardware_quality_passed: '硬件质量',
  PEAK_COMPLETED: 'Peak 时段完课',
  peak_completed_cnt: 'Peak 时段完课',
  FEEDBACK_PRAISE: '学员好评',
  feedback_praise_cnt: '学员好评',
  FEEDBACK_FAVORITE: '学员收藏',
  feedback_favorite_cnt: '学员收藏',
}

const scoreComponentLabelsEn: Record<string, string> = {
  PERFECT_COMPLETED: 'Perfect completion',
  perfect_cnt: 'Perfect completion',
  CLASS_QUALITY_HARDWARE: 'Hardware quality',
  lesson_hardware_quality_passed: 'Hardware quality',
  PEAK_COMPLETED: 'Peak lesson completion',
  peak_completed_cnt: 'Peak lesson completion',
  FEEDBACK_PRAISE: 'Student praise',
  feedback_praise_cnt: 'Student praise',
  FEEDBACK_FAVORITE: 'Student favorite',
  feedback_favorite_cnt: 'Student favorite',
}

export function scoreComponentLabel(component: { code?: string; metric?: string }, locale: AppLocale = 'zh-CN'): string {
  const labels = locale === 'en-US' ? scoreComponentLabelsEn : scoreComponentLabels
  return labels[component.code ?? '']
    ?? labels[component.metric ?? '']
    ?? component.metric
    ?? component.code
    ?? (locale === 'en-US' ? 'Score component' : '积分子项')
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

function formatGateValue(value: unknown, locale: AppLocale = 'zh-CN'): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'boolean') return value ? (locale === 'en-US' ? 'Yes' : '是') : (locale === 'en-US' ? 'No' : '否')
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function GateList({ items }: { items: HardGate[] }) {
  const { locale, t } = useI18n()
  return (
    <List
      size="small"
      dataSource={items}
      locale={{ emptyText: t('暂无门槛明细', 'No threshold details') }}
      renderItem={(item) => (
        <List.Item extra={hardGateSourceTag(item)}>
          <List.Item.Meta
            avatar={item.met ? <CheckCircleOutlined style={{ color: '#178c76' }} /> : <CloseCircleOutlined style={{ color: '#c94945' }} />}
            title={hardGateLabel(item, locale)}
            description={t(`当前 ${formatGateValue(item.actual)} · 规则 ${item.operator ?? '—'} ${formatGateValue(item.threshold)}`, `Current ${formatGateValue(item.actual, locale)} · Rule ${item.operator ?? '—'} ${formatGateValue(item.threshold, locale)}`)}
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
  const { locale } = useI18n()
  const meta = taskStatusMeta[status] ?? { label: status, color: 'default' }
  const labelsEn: Record<string, string> = {
    ASSIGNED: 'Incomplete',
    VIEWED: 'Viewed',
    IN_PROGRESS: 'In progress',
    SUBMITTED: 'Submitted',
    UNDER_REVIEW: 'Under review',
    COMPLETED: 'Completed',
    FAILED: 'Failed',
    EXPIRED: 'Expired',
    WAIVED: 'Waived',
    CANCELLED: 'Cancelled',
  }
  return <Tag color={meta.color}>{locale === 'en-US' ? labelsEn[status] ?? status : meta.label}</Tag>
}

function TeacherIdentity({
  teacher,
  profileState,
}: {
  teacher: Teacher
  profileState: 'LOADING' | 'READY' | 'ERROR'
}) {
  const { locale, t } = useI18n()
  const countryProvenance = profileState === 'READY' ? profileProvenance(teacher, 'country') : undefined
  const timezoneProvenance = profileState === 'READY' ? profileProvenance(teacher, 'timezone') : undefined
  const profileText = profileState === 'LOADING'
    ? `${teacher.teacher_id} · ${t('正在加载档案…', 'Loading profile…')}`
    : profileState === 'ERROR'
      ? `${teacher.teacher_id} · ${t('档案详情不可用', 'Profile unavailable')}`
      : `${teacher.teacher_id} · ${profileFactDisplay(teacher.country, t('国家待确认', 'Country pending'))} · ${profileFactDisplay(teacher.timezone, t('时区待确认', 'Timezone pending'))}`

  return (
    <Flex gap={14} align="center" wrap="wrap">
      <Avatar size={56} className="teacher-avatar">{teacher.avatar || teacher.name?.slice(0, 1) || '?'}</Avatar>
      <div style={{ flex: 1 }}><Title level={4}>{teacher.name}</Title><Text type="secondary">{profileText}</Text></div>
      {dataModeTag(teacher.data_mode)}
      <Tag>{employmentStatusLabel(teacher.employment_status, locale)}</Tag>
      {countryProvenance?.source_mode ? sourceModeTag(countryProvenance.source_mode, t('国家', 'Country')) : null}
      {timezoneProvenance?.source_mode ? sourceModeTag(timezoneProvenance.source_mode, t('时区', 'Timezone')) : null}
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

export default function Teacher360({
  active = true,
  initialTeacherId,
}: {
  active?: boolean
  initialTeacherId?: string
}) {
  const { locale, t } = useI18n()
  const teacherListRequest = useLatestRequest()
  const teacherDetailRequest = useLatestRequest()
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
    return [{ value: 'ALL', label: t('全部数据来源', 'All data sources') }, ...values.map((value) => ({ value, label: teacherDataModeLabel(value, locale) }))]
  }, [locale, t, teacherPage.filters.available_data_modes])
  const employmentOptions = useMemo(() => {
    const values = teacherPage.filters.available_employment_statuses
    return [{ value: 'ALL', label: t('全部在职状态', 'All employment statuses') }, ...values.map((value) => ({ value, label: employmentStatusLabel(value, locale) }))]
  }, [locale, t, teacherPage.filters.available_employment_statuses])

  useEffect(() => {
    setKeyword(initialTeacherId ?? '')
    setPage(1)
  }, [initialTeacherId])

  const loadTeachers = useCallback(async (overrides: { page?: number } = {}) => {
    const requestedPage = overrides.page ?? page
    setListLoading(true)
    setListError('')
    setTeacherPage((previous) => pendingTeacherPage(previous, requestedPage, keyword, dataMode, employmentStatus))
    let cancelled = false
    try {
      const response = await teacherListRequest.run((options) =>
        api.teachers({
          page: requestedPage,
          page_size: TEACHER_PAGE_SIZE,
          keyword: keyword.trim() || undefined,
          data_mode: dataMode === 'ALL' ? undefined : dataMode,
          employment_status: employmentStatus === 'ALL' ? undefined : employmentStatus,
        }, options),
      )
      setTeacherPage(response)
      setHasLoaded(true)
    } catch (error) {
      if (isRequestCancelled(error)) {
        cancelled = true
        return
      }
      setListError(t('教师列表加载失败，请稍后重试。', 'Unable to load the teacher list. Please try again later.'))
    } finally {
      if (!cancelled) setListLoading(false)
    }
  }, [dataMode, employmentStatus, keyword, page, t, teacherListRequest])
  useEffect(() => {
    if (!selectedId) {
      teacherDetailRequest.cancel()
      setLoading(false)
      return
    }
    setDetail(null)
    setDetailError('')
    setLoading(true)
    let cancelled = false
    teacherDetailRequest.run((options) => api.teacher(selectedId, options))
      .then(setDetail)
      .catch((error) => {
        if (isRequestCancelled(error)) {
          cancelled = true
          return
        }
        setDetailError(t('详情接口暂时不可用，当前展示列表快照。', 'Profile details are temporarily unavailable. The list snapshot is shown.'))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
  }, [selectedId, t, teacherDetailRequest])

  if (!active) return null

  return (
    <div className="page-shell">
      <PageHeader
        eyebrow={t('教师与证据', 'Teachers & Evidence')}
        title={t('教师档案', 'Teacher Profiles')}
        description={t('从教师当前进度进入积分、资格门槛、任务和运营介入证据；达到分数线不等于最终资格。', 'Review scores, qualification thresholds, tasks, and operations evidence. Reaching a score line does not by itself grant final qualification.')}
        actions={(
          <Space wrap>
            <Input
              allowClear
              prefix={<SearchOutlined />}
              placeholder={t('姓名 / Teacher ID / 在职状态', 'Name / Teacher ID / Employment status')}
              value={keyword}
              onChange={(event) => { setKeyword(event.target.value); setPage(1) }}
              style={{ width: 260 }}
            />
            <Select value={dataMode} options={dataModeOptions} onChange={(value) => { setDataMode(value); setPage(1) }} style={{ width: 240 }} />
            <Select value={employmentStatus} options={employmentOptions} onChange={(value) => { setEmploymentStatus(value); setPage(1) }} style={{ width: 180 }} />
            <Button type="primary" icon={<ReloadOutlined />} loading={listLoading} onClick={() => loadTeachers().catch(() => undefined)}>{t('更新教师', 'Refresh teachers')}</Button>
          </Space>
        )}
      />
      <Alert
        type="info"
        showIcon
        className="semantic-alert"
        message={t('数据覆盖说明', 'Data Coverage')}
        description={t(teacherDataCoverageDescription, 'Scores and qualifications use each metric’s own evidence source. Country and timezone are not integrated and do not affect current scoring or qualifications. A missing first-booked date remains empty and is never inferred.')}
      />

      <Flex justify="space-between" align="center" wrap="wrap" gap={12}>
        <Text type="secondary">{hasLoaded ? t(`共 ${teacherPage.total} 位教师；服务端仅返回第 ${teacherPage.page} 页（每页最多 ${teacherPage.page_size} 位）`, `${teacherPage.total} teachers; page ${teacherPage.page} is shown (up to ${teacherPage.page_size} per page)`) : t('尚未读取教师档案，点击“更新教师”', 'Teacher profiles have not been loaded. Select “Refresh teachers”.')}</Text>
        <Space>
          {teacherPage.filters.available_data_modes.map((mode) => (
            <span key={mode}>{dataModeTag(mode)}</span>
          ))}
        </Space>
      </Flex>

      {listError ? <Alert type="error" showIcon message={listError} /> : null}
      {listLoading ? <Card><Flex justify="center"><Spin /></Flex></Card> : listError ? null : !visibleTeachers.length ? <Card><Empty description={hasLoaded ? t('没有符合条件的教师', 'No teachers match the filters') : t('尚未读取教师，点击“更新教师”', 'Teachers have not been loaded. Select “Refresh teachers”.')} /></Card> : (
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
                      <Badge status={teacher.graduation_state === 'SETTLEMENT_PENDING' ? 'warning' : 'processing'} text={(locale === 'en-US' ? graduationLabelEn : graduationLabel)[teacher.graduation_state ?? ''] ?? teacher.graduation_state ?? t('状态待确认', 'Status pending')} />
                    </Space>
                  </Flex>

                  <Flex gap={22} align="end" style={{ marginTop: 20 }}>
                    <div className="teacher-score-line" style={{ marginTop: 0 }}><strong>{scoreText(score.raw)}</strong><span>{t('累计总分', 'Total score')}</span></div>
                    <div><Text type="secondary">{t('教师端展示', 'Teacher display')}</Text><div><Text strong style={{ fontSize: 23 }}>{scoreText(score.external)}</Text><Text type="secondary"> / 200</Text></div></div>
                  </Flex>
                  <Progress percent={Math.max(0, Math.min(100, (score.external / 200) * 100))} showInfo={false} strokeColor={score.goldScoreMet ? '#d18b26' : '#176b87'} />

                  <Flex gap={6} wrap="wrap" style={{ marginTop: 10 }}>
                    {criteriaTag(t(`出营分数线 ≥ ${scoreText(score.graduationThreshold)}`, `Graduation score ≥ ${scoreText(score.graduationThreshold)}`), score.graduationScoreMet)}
                    {criteriaTag(t('最终出营资格', 'Graduation qualified'), score.graduationCriteriaMet)}
                    {criteriaTag(t(`金牌分数线 ≥ ${scoreText(score.goldThreshold)}`, `Gold score ≥ ${scoreText(score.goldThreshold)}`), score.goldScoreMet)}
                    {criteriaTag(t('最终金牌资格', 'Gold qualified'), score.goldCriteriaMet)}
                  </Flex>

                  <div className="dimension-mini-grid">
                    {dimensions.map((item) => (
                      <div key={item.code}>
                        <span>{dimensionLabel(item.code, item.label, locale)}</span><b>{scoreText(item.score)}</b><small>{sourceModeLabel(item.source_mode, locale)}</small>
                      </div>
                    ))}
                  </div>
                  <Flex gap={6} wrap="wrap" className="risk-row">
                    {(teacher.risk_tags ?? []).map((tag) => <Tag color={tag.includes('待结算') ? 'gold' : 'volcano'} key={tag}>{tag}</Tag>)}
                    {teacher.employment_status ? <Tag>{employmentStatusLabel(teacher.employment_status, locale)}</Tag> : null}
                  </Flex>
                  <div className="next-action"><UserOutlined /> <span>{teacher.next_best_action || t('查看证据、任务与运营事项', 'Review evidence, tasks, and operations items')}</span></div>
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

      <Drawer title={t('教师证据详情', 'Teacher Evidence Details')} width={860} open={Boolean(selectedId)} onClose={() => { setSelectedId(undefined); setSelectedSummary(null); setDetail(null); setDetailError('') }}>
        {detailPhase === 'EMPTY' ? <Empty description={t('未找到教师详情', 'Teacher details not found')} /> : detailPhase === 'LOADING' ? (
          <Space direction="vertical" size={18} style={{ width: '100%' }}>
            <TeacherIdentity teacher={(detail ?? selectedSummary)!} profileState="LOADING" />
            <Card><Flex justify="center"><Spin tip={t('正在加载完整证据…', 'Loading complete evidence…')}><div /></Spin></Flex></Card>
          </Space>
        ) : detailPhase === 'ERROR' ? (
          <Space direction="vertical" size={18} style={{ width: '100%' }}>
            <TeacherIdentity teacher={selectedSummary!} profileState="ERROR" />
            <Alert
              type="error"
              showIcon
              message={detailError || t('教师详情不可用', 'Teacher details unavailable')}
              description={t('任务、运营事项、硬门槛和来源明细均未加载，因此本页不会把未知事实显示为“暂无”或 0。', 'Tasks, operations items, thresholds, and source details were not loaded. Unknown facts are not shown as “none” or 0.')}
            />
          </Space>
        ) : (
          <Space direction="vertical" size={18} style={{ width: '100%' }}>
            <TeacherIdentity teacher={readyDetail} profileState="READY" />

            {readyDetail.data_mode === 'MIXED' ? <Alert type="info" showIcon message={t(mixedTeacherDataDescription, 'This profile combines the teacher snapshot, lessons, shared tasks, complaints, and other sources. “Multiple data sources” indicates source composition, not missing evidence. Use each metric’s source label.')} /> : null}

            {(() => {
              const score = teacherScoreProjection(readyDetail)
              return (
                <Card size="small" title={t('积分与资格快照', 'Score & Qualification Snapshot')}>
                  <Descriptions size="small" bordered column={{ xs: 1, md: 2 }}>
                    <Descriptions.Item label={t('累计总分（未封顶）', 'Total score (uncapped)')}>{scoreText(score.raw)}</Descriptions.Item>
                    <Descriptions.Item label={t('教师端展示（封顶 200）', 'Teacher display (capped at 200)')}>{scoreText(score.external)}</Descriptions.Item>
                    <Descriptions.Item label={t('基础分', 'Base score')}>{finiteNumber(readyDetail.base_score) ?? '—'}</Descriptions.Item>
                    <Descriptions.Item label={t('计分口径', 'Scoring policy')}>{scorePolicyLabel(readyDetail.score_policy_version, locale)}</Descriptions.Item>
                    <Descriptions.Item label={t('来源快照', 'Source snapshot')}>{readyDetail.source_snapshot_label ?? t('未标注', 'Not labeled')}</Descriptions.Item>
                  </Descriptions>
                  <Flex gap={6} wrap="wrap" style={{ marginTop: 14 }}>
                    {criteriaTag(t(`出营分数线命中 · ≥ ${scoreText(score.graduationThreshold)}`, `Graduation score met · ≥ ${scoreText(score.graduationThreshold)}`), score.graduationScoreMet)}
                    {criteriaTag(t('最终出营资格', 'Graduation qualified'), score.graduationCriteriaMet)}
                    {criteriaTag(t(`金牌分数线命中 · ≥ ${scoreText(score.goldThreshold)}`, `Gold score met · ≥ ${scoreText(score.goldThreshold)}`), score.goldScoreMet)}
                    {criteriaTag(t('最终金牌资格', 'Gold qualified'), score.goldCriteriaMet)}
                  </Flex>
                </Card>
              )
            })()}

            <Card size="small" title={t('教师资料与准备度', 'Teacher Profile & Readiness')}>
              <Descriptions size="small" bordered column={{ xs: 1, md: 3 }}>
                <Descriptions.Item label={t('首次约课日期', 'First booked lesson date')}>{readyDetail.first_booked_date ?? t('暂无数据', 'No data')}</Descriptions.Item>
                <Descriptions.Item label="TESOL">{completionEvidenceLabel(readyDetail.is_cpl_tesol, locale)}</Descriptions.Item>
                <Descriptions.Item label={t('自我介绍', 'Self-introduction')}>{completionEvidenceLabel(readyDetail.is_self_introduce, locale)}</Descriptions.Item>
              </Descriptions>
              <Text type="secondary">{t('TESOL 是 G01 唯一读取的外部状态；自我介绍仅作为教师资料展示，不影响 G01。', 'TESOL is the only external status read by G01. Self-introduction is shown only as teacher profile information and does not affect G01.')}</Text>
            </Card>

            <Card size="small" title={t('五维积分与来源', 'Five-Dimension Scores & Sources')}>
              {detailSupplyMilestone ? (
                <Card
                  size="small"
                  type="inner"
                  title={t('供给积分里程碑', 'Peak Availability Milestone')}
                  extra={sourceModeTag(detailSupplyMilestone.sourceMode)}
                  style={{ marginBottom: 16 }}
                >
                  <Flex justify="space-between" align="center" gap={16} wrap="wrap">
                    <div>
                      <Text type="secondary">Peak slots</Text>
                      <div><Text strong style={{ fontSize: 24 }}>{scoreText(detailSupplyMilestone.peakSlotCount)}</Text><Text type="secondary"> / {detailSupplyMilestone.threshold}</Text></div>
                    </div>
                    <div>
                      <Text type="secondary">{t('供给分', 'Availability score')}</Text>
                      <div><Text strong style={{ fontSize: 24 }}>{detailSupplyMilestone.score}</Text><Text type="secondary"> / {detailSupplyMilestone.maximumScore}</Text></div>
                    </div>
                    <Tag color={detailSupplyMilestone.met ? 'success' : 'default'}>{detailSupplyMilestone.locked ? t('首次达成 · 已永久锁定', 'First achieved · Permanently locked') : detailSupplyMilestone.met ? t('已达成 · 待锁定结算', 'Achieved · Settlement pending') : t('尚未达成', 'Not achieved')}</Tag>
                  </Flex>
                  <Progress percent={Math.max(0, Math.min(100, detailSupplyMilestone.peakSlotCount / detailSupplyMilestone.threshold * 100))} showInfo={false} style={{ marginTop: 10 }} />
                  <Text type="secondary">{t('规则：peak_slot_cnt 首次达到 40 → 供给分 10/10，结算后永久保留；个性化改善任务不在这里结分。', 'Rule: the first time peak_slot_cnt reaches 40, the availability score becomes 10/10 and remains locked after settlement. Personalized improvement tasks do not award points here.')}</Text>
                </Card>
              ) : null}
              {!readyDetail.dimensions?.length ? <EmptyPanel description={t('暂无维度投影', 'No dimension projection')} /> : (
                <div className="dimension-detail-grid">
                  {readyDetail.dimensions.map((item) => (
                    <div key={item.code}>
                      <Flex justify="space-between" align="center"><span>{dimensionLabel(item.code, item.label, locale)}</span>{sourceModeTag(item.source_mode)}</Flex>
                      <strong>{scoreText(item.score)}</strong>
                      {item.components?.length ? (
                        <List
                          size="small"
                          dataSource={item.components}
                          renderItem={(component) => (
                            <List.Item
                              style={{ paddingInline: 0 }}
                              extra={(
                                <Space wrap>
                                  {sourceModeTag(component.source_mode)}
                                  {reconciliationLabel(component.reconciliation_status, locale)
                                    ? <Tag color="gold">{reconciliationLabel(component.reconciliation_status, locale)}</Tag>
                                    : null}
                                </Space>
                              )}
                            >
                              <Text type="secondary">
                                {scoreComponentLabel(component, locale)}: {formatGateValue(component.value)}
                                {['perfect_cnt', 'lesson_hardware_quality_passed'].includes(component.metric ?? '') && finiteNumber(component.points_per_unit) !== undefined
                                  ? ` × ${scoreText(component.points_per_unit!)}`
                                  : ''}
                                {' '}→ {scoreText(component.score)} {t('分', 'pts')}
                              </Text>
                            </List.Item>
                          )}
                        />
                      ) : <small>{item.source_field || item.source_note || t('维度来源明细待接口补充', 'Dimension source details pending integration')}</small>}
                    </div>
                  ))}
                </div>
              )}
            </Card>

            <Card size="small" title={t('硬门槛证据', 'Qualification Threshold Evidence')}>
              {!detailGateGroups.length ? <EmptyPanel description={t('暂无硬门槛明细', 'No threshold details')} /> : detailGateGroups.map(({ key, title, group }) => (
                <Card key={key} size="small" title={key === 'graduation' ? t('最终出营资格硬门槛', 'Graduation Qualification Thresholds') : key === 'gold' ? t('最终金牌资格硬门槛', 'Gold Qualification Thresholds') : title} extra={criteriaTag(t('整组通过', 'All passed'), group.met)} style={{ marginBottom: 12 }}>
                  <GateList items={group.items} />
                </Card>
              ))}
            </Card>

            <Card size="small" title={t(`任务进度（${readyDetail.task_assignments?.length ?? 0}）`, `Task Progress (${readyDetail.task_assignments?.length ?? 0})`)}>
              {!readyDetail.task_assignments?.length ? <EmptyPanel description={t('暂无任务', 'No tasks')} /> : (
                <List dataSource={orderedAssignments} renderItem={(assignment) => (
                  <List.Item extra={taskStatusTag(assignment.status)}>
                    <List.Item.Meta
                      title={<Space wrap><Text code>{assignment.task_code}</Text><span>{assignment.title ?? assignment.task_code}</span><Tag color={assignment.task_kind === 'FIXED_GROWTH' ? 'purple' : 'blue'}>{assignment.task_kind === 'FIXED_GROWTH' ? t('必修成长', 'Mandatory growth') : t('个性化改善', 'Personalized improvement')}</Tag><PriorityTag priority={assignment.priority} /></Space>}
                      description={assignment.why}
                    />
                  </List.Item>
                )} />
              )}
            </Card>
            <Card size="small" title={t(`运营事项（${readyDetail.ops_cases?.length ?? 0}）`, `Operations Items (${readyDetail.ops_cases?.length ?? 0})`)}>
              {!readyDetail.ops_cases?.length ? <EmptyPanel description={t('暂无运营事项', 'No operations items')} /> : readyDetail.ops_cases.map((item) => (
                <Flex className="drawer-case-row" key={item.case_id} justify="space-between" gap={12} wrap="wrap"><div><strong>{item.case_type}</strong><div><Text type="secondary">{item.summary}</Text></div></div><CaseStatus status={item.status} externalStatus={item.external_action_status} /></Flex>
              ))}
            </Card>
          </Space>
        )}
      </Drawer>
    </div>
  )
}
