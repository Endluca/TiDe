import { Fragment, useCallback, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { Alert, Badge, Button, Card, Col, Empty, Flex, Progress, Row, Select, Space, Statistic, Table, Tag, Typography } from 'antd'
import type { TableColumnsType } from 'antd'
import {
  ArrowRightOutlined,
  CalendarOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  CustomerServiceOutlined,
  ExclamationCircleOutlined,
  FileSearchOutlined,
  ReloadOutlined,
  RiseOutlined,
  SafetyCertificateOutlined,
  TeamOutlined,
} from '@ant-design/icons'
import { api, isRequestCancelled } from '../api'
import { displayError, employmentStatusLabel } from '../domain'
import { interventionStatusLabel, operationDomainLabel, sortInterventions, summarizeRiskBreakdown } from '../operations'
import type { AppNavigationContext, AppSnapshot, OperationsIntervention, OperationsOverview, SupportTicketSummary } from '../types'
import { PageHeader, PriorityTag, TimeText } from '../components/Common'
import { useI18n } from '../i18n'
import { useLatestRequest } from '../useLatestRequest'
import type { AppLocale } from '../i18n'

const { Text, Title } = Typography

function safeRate(value: number | undefined, total: number | undefined): number {
  if (!value || !total || total <= 0) return 0
  return Math.max(0, Math.min(100, Math.round((value / total) * 1000) / 10))
}

export function operationalEvidenceSummary(snapshot: AppSnapshot) {
  const dashboard = snapshot.dashboard
  const dataModes = dashboard?.data_mode_counts
    ?? dashboard?.data_composition
    ?? {}
  const employmentStatuses = dashboard?.employment_status_counts
    ?? {}
  const graduationScoreReached = dashboard?.graduation_score_reached_count
    ?? dashboard?.graduation_score_threshold_met_count
    ?? 0
  const graduationEligible = dashboard?.graduation_qualified_count
    ?? dashboard?.graduation_criteria_met_count
  const goldScoreReached = dashboard?.gold_score_reached_count
    ?? dashboard?.gold_score_threshold_met_count
    ?? 0
  const goldEligible = dashboard?.gold_qualified_count
    ?? dashboard?.gold_eligible_count
    ?? dashboard?.gold_criteria_met_count
  return { dataModes, employmentStatuses, graduationScoreReached, graduationEligible, goldScoreReached, goldEligible }
}

type FunnelStageTone = 'default' | 'success' | 'gold'

interface OperationalFunnelStage {
  key: 'cohort' | 'graduation-score' | 'graduation-eligible' | 'gold-eligible'
  label: string
  value: number
  rate: number
  tone: FunnelStageTone
}

export type FunnelEmploymentStatus = 'ALL' | 'on' | 'off' | 'hei'

export function operationalFunnelStages(
  snapshot: AppSnapshot,
  employmentStatus: FunnelEmploymentStatus = 'ALL',
  locale: AppLocale = 'zh-CN',
): OperationalFunnelStage[] {
  const selectedFunnel = employmentStatus === 'ALL'
    ? undefined
    : snapshot.dashboard?.funnel_by_employment_status?.[employmentStatus]
  const summary = operationalEvidenceSummary(snapshot)
  const teacherTotal = selectedFunnel?.teacher_count
    ?? (employmentStatus === 'ALL' ? snapshot.dashboard?.teacher_count ?? 0 : 0)
  const graduationScoreReached = selectedFunnel?.graduation_score_reached_count
    ?? (employmentStatus === 'ALL' ? summary.graduationScoreReached : 0)
  const graduationEligible = selectedFunnel?.graduation_qualified_count
    ?? selectedFunnel?.graduation_criteria_met_count
    ?? (employmentStatus === 'ALL' ? summary.graduationEligible ?? 0 : 0)
  const goldEligible = selectedFunnel?.gold_qualified_count
    ?? selectedFunnel?.gold_eligible_count
    ?? (employmentStatus === 'ALL' ? summary.goldEligible ?? 0 : 0)

  return [
    { key: 'cohort', label: locale === 'en-US' ? 'Teacher cohort' : '培养名单', value: teacherTotal, rate: teacherTotal ? 100 : 0, tone: 'default' },
    {
      key: 'graduation-score',
      label: locale === 'en-US' ? 'Reached graduation score' : '达到出营分数线',
      value: graduationScoreReached,
      rate: safeRate(graduationScoreReached, teacherTotal),
      tone: 'default',
    },
    {
      key: 'graduation-eligible',
      label: locale === 'en-US' ? 'Graduation qualified' : '满足最终出营资格',
      value: graduationEligible,
      rate: safeRate(graduationEligible, teacherTotal),
      tone: 'success',
    },
    {
      key: 'gold-eligible',
      label: locale === 'en-US' ? 'Gold qualified' : '满足金牌资格',
      value: goldEligible,
      rate: safeRate(goldEligible, teacherTotal),
      tone: 'gold',
    },
  ]
}

export function mixedDataAlertDescription(scorePolicyVersion?: string, locale: AppLocale = 'zh-CN'): string {
  if (locale === 'en-US') {
    const coverage = scorePolicyVersion === 'v1'
      ? 'Teacher profile, completed lessons, Peak slots, perfect completions, late arrivals, early leaves, absences, user feedback, and lesson hardware quality are included'
      : 'Teacher profile, completed lessons, and user feedback are included; this record uses a non-current scoring policy and is read-only'
    return `${coverage}. Mandatory-task baselines and completion states come directly from the shared task table, and L0 complaints are determined from lesson complaint records. Missing fields are never treated as qualification evidence.`
  }
  const currentCoverage = scorePolicyVersion === 'v1'
    ? '教师基础、完课、Peak slots、完美完课、迟到、早退、缺席、用户反馈和逐课硬件质量已纳入当前视图'
    : '教师基础、完课和用户反馈已纳入当前视图；该记录使用非当前计分口径，仅供读取'
  return `${currentCoverage}；必修任务基线和完成状态直接读取共享任务表，L0 投诉按课程投诉记录判断。待补字段不会被默认成已满足资格。`
}

function MetricCard({
  label,
  value,
  note,
  icon,
  tone,
}: {
  label: string
  value: number | string
  note: string
  icon: ReactNode
  tone: 'neutral' | 'success' | 'warning' | 'danger'
}) {
  return (
    <Card className={`executive-metric executive-metric-${tone}`}>
      <Flex justify="space-between" align="flex-start" gap={12}>
        <div><Text type="secondary">{label}</Text><div className="executive-metric-value">{value}</div></div>
        <div className="executive-metric-icon">{icon}</div>
      </Flex>
      <Text type="secondary" className="executive-metric-note">{note}</Text>
    </Card>
  )
}

function FunnelStage({ label, value, rate, tone }: { label: string; value: number; rate: number; tone: FunnelStageTone }) {
  const className = tone === 'default' ? 'funnel-stage' : `funnel-stage funnel-stage-${tone}`
  const strokeColor = tone === 'success' ? '#237a64' : tone === 'gold' ? '#b0873c' : '#84958e'
  return (
    <div className={className}>
      <Text type="secondary">{label}</Text>
      <Flex align="baseline" gap={8}><strong>{value}</strong><span>{rate}%</span></Flex>
      <Progress percent={rate} showInfo={false} strokeColor={strokeColor} trailColor="#e9edea" />
    </div>
  )
}

function interventionBadge(status: string, locale: AppLocale) {
  const badgeStatus = status === 'FAILED'
    ? 'error'
    : status === 'COMPLETED'
      ? 'success'
      : status === 'ACTION_PENDING'
        ? 'warning'
        : 'processing'
  return <Badge status={badgeStatus} text={interventionStatusLabel(status, locale)} />
}

export default function OperationalDashboard({
  active = true,
  snapshot,
  refresh,
  onNavigate,
}: {
  active?: boolean
  snapshot: AppSnapshot
  refresh: () => Promise<void>
  onNavigate: (page: string, context?: AppNavigationContext) => void
}) {
  const { locale, t } = useI18n()
  const operationsRequest = useLatestRequest()
  const ticketSummaryRequest = useLatestRequest()
  const [overview, setOverview] = useState<OperationsOverview | null>(null)
  const [interventions, setInterventions] = useState<OperationsIntervention[]>([])
  const [operationsLoading, setOperationsLoading] = useState(false)
  const [operationsError, setOperationsError] = useState('')
  const [supportTicketSummary, setSupportTicketSummary] = useState<SupportTicketSummary | null>(null)
  const [supportTicketLoading, setSupportTicketLoading] = useState(false)
  const [funnelEmploymentStatus, setFunnelEmploymentStatus] = useState<FunnelEmploymentStatus>('ALL')

  const loadOperations = useCallback(async () => {
    setOperationsLoading(true)
    setOperationsError('')
    let cancelled = false
    try {
      const [overviewResponse, interventionResponse] = await operationsRequest.run((options) =>
        Promise.all([
          api.operationsOverview(options),
          api.operationsInterventions({ type: 'OPS_CASE', open_only: true }, options),
        ]),
      )
      setOverview(overviewResponse)
      setInterventions(sortInterventions(interventionResponse.items))
    } catch (error) {
      if (isRequestCancelled(error)) {
        cancelled = true
        return
      }
      setOverview(null)
      setInterventions([])
      setOperationsError(displayError(error, locale))
    } finally {
      if (!cancelled) setOperationsLoading(false)
    }
  }, [locale, operationsRequest])
  const loadSupportTickets = useCallback(async () => {
    setSupportTicketLoading(true)
    let cancelled = false
    try {
      setSupportTicketSummary(await ticketSummaryRequest.run((options) => api.supportTicketSummary(options)))
    } catch (error) {
      if (isRequestCancelled(error)) {
        cancelled = true
        return
      }
      setSupportTicketSummary(null)
    } finally {
      if (!cancelled) setSupportTicketLoading(false)
    }
  }, [ticketSummaryRequest])

  if (!active) return null

  const dashboard = snapshot.dashboard
  const evidenceSummary = operationalEvidenceSummary(snapshot)
  const cohortTeacherTotal = dashboard?.teacher_count ?? 0
  const observedTeacherTotal = overview?.teacher_total ?? 0
  const lessonTotal = overview?.lesson_total ?? 0
  const affectedTeacherTotal = overview?.affected_teacher_total ?? 0
  const currentOpsTodoCount = overview?.current_ops_todo_count ?? 0
  const openPersonalizedTasks = overview?.open_personalized_tasks ?? 0
  const funnelStages = operationalFunnelStages(snapshot, funnelEmploymentStatus, locale)
  const riskSummary = summarizeRiskBreakdown(overview?.risk_breakdown ?? [])
  const urgentCount = interventions.filter((item) => item.priority === 'P0' || item.priority === 'P1').length
  const employmentStructure = Object.entries(evidenceSummary.employmentStatuses).reduce<Record<string, number>>((summary, [key, count]) => {
    const label = employmentStatusLabel(key, locale)
    summary[label] = (summary[label] ?? 0) + count
    return summary
  }, {})
  const visibleInterventions = interventions.slice(0, 8)

  const columns: TableColumnsType<OperationsIntervention> = [
    { title: t('紧急度', 'Priority'), dataIndex: 'priority', width: 110, render: (value: string) => <PriorityTag priority={value} /> },
    {
      title: t('待办事项', 'Action item'), key: 'summary',
      render: (_, item) => <Space direction="vertical" size={3}><Text strong>{item.title}</Text><Text type="secondary">{t(`由 ${item.signal_count} 次课程信号触发`, `Triggered by ${item.signal_count} lesson signals`)} · {item.why}</Text></Space>,
    },
    { title: t('风险类型', 'Risk type'), dataIndex: 'domain', width: 130, render: (value: string) => <Tag>{operationDomainLabel(value, undefined, locale)}</Tag> },
    { title: t('教师', 'Teacher'), key: 'teacher', width: 150, render: (_, item) => <Button type="link" className="table-link" onClick={() => onNavigate('teachers', { teacherId: item.teacher_id })}>{item.teacher_name || item.teacher_id}</Button> },
    { title: t('进展', 'Progress'), dataIndex: 'status', width: 130, render: (value: string) => interventionBadge(value, locale) },
    { title: t('触发时间', 'Triggered at'), dataIndex: 'triggered_at', width: 145, render: (value: string) => <TimeText value={value} /> },
    {
      title: t('证据', 'Evidence'), key: 'evidence', width: 110,
      render: (_, item) => item.source_lesson_id
        ? <Button type="link" className="table-link" onClick={() => onNavigate('lessons', { teacherId: item.teacher_id, lessonId: item.source_lesson_id ?? undefined })}>{t('查看课程', 'View lesson')}</Button>
        : <Text type="secondary">{t('教师证据', 'Teacher evidence')}</Text>,
    },
  ]

  const executiveCopy = !overview
    ? t(`培养名单共 ${cohortTeacherTotal} 位；最新课程覆盖与风险明细暂未更新。`, `${cohortTeacherTotal} teachers are in the cohort; lesson coverage and risk details have not been refreshed.`)
    : currentOpsTodoCount > 0
      ? t(`当前已覆盖 ${observedTeacherTotal} 位新师、${lessonTotal} 节课程；现有 ${currentOpsTodoCount} 项运营待办，其中 ${overview.severe_complaint_cases} 项为严重投诉。`, `Coverage includes ${observedTeacherTotal} new teachers and ${lessonTotal} lessons. ${currentOpsTodoCount} operations items are pending, including ${overview.severe_complaint_cases} severe complaints.`)
      : t(`当前已覆盖 ${observedTeacherTotal} 位新师、${lessonTotal} 节课程；当前没有待处理的运营事项。`, `Coverage includes ${observedTeacherTotal} new teachers and ${lessonTotal} lessons. No operations items are pending.`)

  async function refreshAll() {
    await Promise.all([refresh(), loadOperations(), loadSupportTickets()])
  }

  return (
    <div className="page-shell operational-home">
      <PageHeader
        eyebrow={t('经营总览', 'Operations Overview')}
        title={t('新师 30 天经营总览', '30-Day New Teacher Overview')}
        description={t('从整体培养与课程表现进入风险、待办，再下钻到单个教师和课程证据。', 'Review cohort and lesson performance, then drill into risks, action items, teachers, and lesson evidence.')}
        actions={<Button icon={<ReloadOutlined />} loading={operationsLoading || supportTicketLoading} onClick={() => refreshAll()}>{t('更新数据', 'Refresh data')}</Button>}
      />

      {!dashboard && !overview && !operationsLoading ? <Alert type="info" showIcon message={t('数据尚未更新', 'Data not refreshed')} description={t('页面切换不会自动请求数据。点击“更新数据”后读取最新经营结果。', 'Changing pages does not request data. Select “Refresh data” to load the latest operations results.')} /> : null}
      {operationsError ? <Alert type="warning" showIcon message={t('最新风险明细暂未更新', 'Risk details were not refreshed')} description={t('培养与出营汇总仍可查看；风险分类、处置事项和课程下钻请稍后刷新。', 'Cohort and qualification summaries remain available. Refresh later for risk categories, action items, and lesson drill-down.')} /> : null}

      <Card className="executive-brief">
        <Row gutter={[24, 18]} align="middle">
          <Col xs={24} lg={17}>
            <div className="executive-brief-label"><RiseOutlined /> {t('今日经营判断', 'Current operations summary')}</div>
            <Title level={3}>{executiveCopy}</Title>
            <Text>{urgentCount > 0
              ? t(`先处理 ${urgentCount} 项高优先级待办，再回看开放任务与出营转化。`, `Handle ${urgentCount} high-priority items first, then review open tasks and graduation conversion.`)
              : t('先看风险结构，再进入教师和课程证据确认具体原因。', 'Review the risk structure, then use teacher and lesson evidence to confirm the cause.')}</Text>
          </Col>
          <Col xs={24} lg={7}>
            <Flex gap={10} justify="flex-end" wrap="wrap" className="executive-actions">
              <Button ghost onClick={() => onNavigate('interventions')}>{t('进入待办处置', 'Open action queue')}</Button>
              <Button ghost onClick={() => onNavigate('support-tickets')}>
                {t(`教师工单 ${supportTicketSummary?.waiting_operator ?? '—'}`, `Teacher tickets ${supportTicketSummary?.waiting_operator ?? '—'}`)}
              </Button>
              <Button type="primary" onClick={() => onNavigate('lessons')}>{t('查看课程证据', 'View lesson evidence')}</Button>
            </Flex>
          </Col>
        </Row>
      </Card>

      <div className="executive-metrics-grid">
        <MetricCard label={t('有课程记录教师', 'Teachers with lessons')} value={overview ? observedTeacherTotal : '—'} note={t(`培养名单共 ${cohortTeacherTotal} 位`, `${cohortTeacherTotal} teachers in cohort`)} icon={<TeamOutlined />} tone="neutral" />
        <MetricCard label={t('30 天课程', '30-day lessons')} value={overview ? lessonTotal : '—'} note={overview ? t('当前课程证据总量', 'Current lesson evidence total') : t('等待课程明细更新', 'Awaiting lesson refresh')} icon={<CalendarOutlined />} tone="neutral" />
        <MetricCard label={t('当前待办', 'Pending actions')} value={overview ? currentOpsTodoCount : '—'} note={overview ? t('仅统计运营处置事项', 'Operations actions only') : t('等待运营待办更新', 'Awaiting action refresh')} icon={<ExclamationCircleOutlined />} tone="danger" />
        <MetricCard label={t('待回复工单', 'Tickets to reply')} value={supportTicketSummary?.waiting_operator ?? '—'} note={supportTicketSummary ? t(`${supportTicketSummary.waiting_teacher} 项待教师回复`, `${supportTicketSummary.waiting_teacher} waiting for teachers`) : t('等待工单更新', 'Awaiting ticket refresh')} icon={<CustomerServiceOutlined />} tone="danger" />
        <MetricCard label={t('开放个性化任务', 'Open personalized tasks')} value={overview ? openPersonalizedTasks : '—'} note={overview ? t(`${riskSummary.openActions} 项处置仍开放`, `${riskSummary.openActions} actions remain open`) : t('等待任务明细更新', 'Awaiting task refresh')} icon={<FileSearchOutlined />} tone="warning" />
      </div>

      <Row gutter={[16, 16]}>
        <Col xs={24} xl={15}>
          <Card
            className="section-card"
            title={t('出营转化', 'Qualification Funnel')}
            extra={(
              <Space wrap>
                <Text type="secondary">{t('在职状态', 'Employment status')}</Text>
                <Select<FunnelEmploymentStatus>
                  aria-label={t('出营转化在职状态', 'Qualification funnel employment status')}
                  value={funnelEmploymentStatus}
                  onChange={setFunnelEmploymentStatus}
                  options={[
                    { value: 'ALL', label: t('全部状态', 'All statuses') },
                    { value: 'on', label: t('在职', 'Active') },
                    { value: 'off', label: t('非在职', 'Inactive') },
                    { value: 'hei', label: t('已拉黑删除', 'Blocked / deleted') },
                  ]}
                  style={{ width: 132 }}
                />
              </Space>
            )}
          >
            <div className="funnel-grid">
              {funnelStages.map((stage, index) => (
                <Fragment key={stage.key}>
                  {index > 0 ? <ArrowRightOutlined className="funnel-arrow" /> : null}
                  <FunnelStage label={stage.label} value={stage.value} rate={stage.rate} tone={stage.tone} />
                </Fragment>
              ))}
            </div>
          </Card>
        </Col>
        <Col xs={24} xl={9}>
          <Card className="section-card" title={t('风险结构', 'Risk Breakdown')} extra={<Text type="secondary">{t(`${affectedTeacherTotal} 位教师受影响`, `${affectedTeacherTotal} teachers affected`)}</Text>}>
            <div className="risk-structure-grid">
              {(overview?.risk_breakdown ?? []).map((item, index) => (
                <button className={`risk-structure-item risk-${index === 0 ? 'danger' : index === 1 ? 'warning' : 'success'}`} key={item.domain} onClick={() => onNavigate('interventions', { domain: item.domain })}>
                  <div><Text strong>{operationDomainLabel(item.domain, item.label, locale)}</Text><Text type="secondary">{t(`${item.signal_count} 条信号 · ${item.teacher_count} 位教师`, `${item.signal_count} signals · ${item.teacher_count} teachers`)}</Text></div>
                  <strong>{item.open_output_count}</strong>
                </button>
              ))}
              {!operationsLoading && !overview?.risk_breakdown.length ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t('暂无已识别风险', 'No identified risks')} /> : null}
            </div>
            {overview ? <Flex gap={8} wrap="wrap" className="risk-footnote"><Tag color="red">{t('严重投诉', 'Severe complaints')} {overview.severe_complaint_cases}</Tag><Tag color="gold">{t('数据待核', 'Data to verify')} {overview.pending_data_issues}</Tag></Flex> : null}
          </Card>
        </Col>
      </Row>

      <Card
        className="section-card action-table-card"
        title={<Space><ClockCircleOutlined /><span>{t('待办处置', 'Action Queue')}</span></Space>}
        extra={<Space><Tag color={urgentCount ? 'error' : 'default'}>{t(`${urgentCount} 项优先处理`, `${urgentCount} priority items`)}</Tag><Button type="link" onClick={() => onNavigate('interventions')}>{t('查看全部待办', 'View all')}</Button></Space>}
      >
        <Table
          rowKey="output_id"
          loading={operationsLoading}
          dataSource={visibleInterventions}
          columns={columns}
          pagination={false}
          scroll={{ x: 980 }}
          locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={operationsError ? t('风险明细尚未更新', 'Risk details not refreshed') : t('当前没有需要运营介入的事项', 'No operations action is required')} /> }}
        />
      </Card>

      <Row gutter={[16, 16]}>
        <Col xs={24} xl={15}>
          <Card className="section-card" title={t('五维平均表现', 'Average Dimension Scores')} extra={<Text type="secondary">{t('用于发现群体性薄弱项', 'Highlights cohort-wide weaknesses')}</Text>}>
            <div className="dimension-overview-grid">
              {(dashboard?.dimension_averages ?? []).map((item) => (
                <div key={item.code} className="dimension-overview-item">
                  <Text type="secondary">{item.label}</Text>
                  <Statistic value={item.average} precision={Number.isInteger(item.average) ? 0 : 1} />
                </div>
              ))}
              {!dashboard?.dimension_averages?.length ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t('暂无维度汇总', 'No dimension summary')} /> : null}
            </div>
          </Card>
        </Col>
        <Col xs={24} xl={9}>
          <Card className="section-card" title={t('教师状态结构', 'Teacher Status Breakdown')}>
            <div className="cohort-structure-list">
              {Object.entries(employmentStructure).map(([label, count]) => (
                <Flex key={label} justify="space-between" align="center"><Text>{label}</Text><Text strong>{count}</Text></Flex>
              ))}
              {!Object.keys(employmentStructure).length ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t('暂无状态汇总', 'No status summary')} /> : null}
            </div>
          </Card>
        </Col>
      </Row>

      <div className="coverage-note">
        <SafetyCertificateOutlined />
        <div><Text strong>{t('数据覆盖说明', 'Data Coverage')}</Text><Text type="secondary">{mixedDataAlertDescription(dashboard?.score_policy_version, locale)}</Text></div>
      </div>
    </div>
  )
}
