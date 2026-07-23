import { Fragment, useCallback, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { Alert, Badge, Button, Card, Col, Empty, Flex, Progress, Row, Select, Space, Statistic, Table, Tag, Typography } from 'antd'
import type { TableColumnsType } from 'antd'
import {
  ArrowRightOutlined,
  CalendarOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  ExclamationCircleOutlined,
  FileSearchOutlined,
  ReloadOutlined,
  RiseOutlined,
  SafetyCertificateOutlined,
  TeamOutlined,
} from '@ant-design/icons'
import { api } from '../api'
import { displayError, employmentStatusLabel } from '../domain'
import { interventionStatusLabel, operationDomainLabel, sortInterventions, summarizeRiskBreakdown } from '../operations'
import type { AppNavigationContext, AppSnapshot, OperationsIntervention, OperationsOverview, QueueItem } from '../types'
import { PageHeader, PriorityTag, TimeText } from '../components/Common'

const { Text, Title } = Typography

export function isOperationalQueueItem(item: QueueItem): boolean {
  const text = `${item.queue_id} ${item.title} ${item.summary}`.toLocaleLowerCase()
  return !text.includes('mock') && !text.includes('模拟') && !text.includes('供运营行动台检查')
}

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
  const graduationEligible = dashboard?.graduation_criteria_met_count
  const goldScoreReached = dashboard?.gold_score_reached_count
    ?? dashboard?.gold_score_threshold_met_count
    ?? 0
  const goldEligible = dashboard?.gold_eligible_count
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
): OperationalFunnelStage[] {
  const selectedFunnel = employmentStatus === 'ALL'
    ? undefined
    : snapshot.dashboard?.funnel_by_employment_status?.[employmentStatus]
  const summary = operationalEvidenceSummary(snapshot)
  const teacherTotal = selectedFunnel?.teacher_count
    ?? (employmentStatus === 'ALL' ? snapshot.dashboard?.teacher_count ?? 0 : 0)
  const graduationScoreReached = selectedFunnel?.graduation_score_reached_count
    ?? (employmentStatus === 'ALL' ? summary.graduationScoreReached : 0)
  const graduationEligible = selectedFunnel?.graduation_criteria_met_count
    ?? (employmentStatus === 'ALL' ? summary.graduationEligible ?? 0 : 0)
  const goldEligible = selectedFunnel?.gold_eligible_count
    ?? (employmentStatus === 'ALL' ? summary.goldEligible ?? 0 : 0)

  return [
    { key: 'cohort', label: '培养名单', value: teacherTotal, rate: teacherTotal ? 100 : 0, tone: 'default' },
    {
      key: 'graduation-score',
      label: '达到出营分数线',
      value: graduationScoreReached,
      rate: safeRate(graduationScoreReached, teacherTotal),
      tone: 'default',
    },
    {
      key: 'graduation-eligible',
      label: '满足最终出营资格',
      value: graduationEligible,
      rate: safeRate(graduationEligible, teacherTotal),
      tone: 'success',
    },
    {
      key: 'gold-eligible',
      label: '满足金牌资格',
      value: goldEligible,
      rate: safeRate(goldEligible, teacherTotal),
      tone: 'gold',
    },
  ]
}

export function mixedDataAlertDescription(scorePolicyVersion?: string): string {
  const currentCoverage = scorePolicyVersion === 'v6'
    ? '教师基础、完课、Peak slots、准时完课、perfect_cnt 课堂质量和用户反馈已纳入当前视图'
    : '教师基础、完课、准时完课和用户反馈已纳入当前视图；该记录使用非当前计分口径，仅供读取'
  return `${currentCoverage}；必修任务基线和完成状态直接读取共享任务表。L0 投诉记录和缺席责任拆分仍按各教师的课程证据覆盖度判断。待补字段不直接用于正式资格判断。`
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

function interventionBadge(status: string) {
  const badgeStatus = status === 'FAILED'
    ? 'error'
    : status === 'COMPLETED'
      ? 'success'
      : status === 'ACTION_PENDING'
        ? 'warning'
        : 'processing'
  return <Badge status={badgeStatus} text={interventionStatusLabel(status)} />
}

export default function OperationalDashboard({
  snapshot,
  refresh,
  onNavigate,
}: {
  snapshot: AppSnapshot
  refresh: () => Promise<void>
  onNavigate: (page: string, context?: AppNavigationContext) => void
}) {
  const [overview, setOverview] = useState<OperationsOverview | null>(null)
  const [interventions, setInterventions] = useState<OperationsIntervention[]>([])
  const [operationsLoading, setOperationsLoading] = useState(false)
  const [operationsError, setOperationsError] = useState('')
  const [funnelEmploymentStatus, setFunnelEmploymentStatus] = useState<FunnelEmploymentStatus>('ALL')

  const loadOperations = useCallback(async () => {
    setOperationsLoading(true)
    setOperationsError('')
    try {
      const [overviewResponse, interventionResponse] = await Promise.all([
        api.operationsOverview(),
        api.operationsInterventions({ type: 'OPS_CASE', open_only: true }),
      ])
      setOverview(overviewResponse)
      setInterventions(sortInterventions(interventionResponse.items))
    } catch (error) {
      setOverview(null)
      setInterventions([])
      setOperationsError(displayError(error))
    } finally {
      setOperationsLoading(false)
    }
  }, [])

  const dashboard = snapshot.dashboard
  const evidenceSummary = operationalEvidenceSummary(snapshot)
  const cohortTeacherTotal = dashboard?.teacher_count ?? 0
  const observedTeacherTotal = overview?.teacher_total ?? 0
  const lessonTotal = overview?.lesson_total ?? 0
  const affectedTeacherTotal = overview?.affected_teacher_total ?? 0
  const currentOpsTodoCount = overview?.current_ops_todo_count ?? 0
  const openPersonalizedTasks = overview?.open_personalized_tasks ?? 0
  const funnelStages = operationalFunnelStages(snapshot, funnelEmploymentStatus)
  const riskSummary = summarizeRiskBreakdown(overview?.risk_breakdown ?? [])
  const urgentCount = interventions.filter((item) => item.priority === 'P0' || item.priority === 'P1').length
  const employmentStructure = Object.entries(evidenceSummary.employmentStatuses).reduce<Record<string, number>>((summary, [key, count]) => {
    const label = employmentStatusLabel(key)
    summary[label] = (summary[label] ?? 0) + count
    return summary
  }, {})
  const visibleInterventions = interventions.slice(0, 8)

  const columns: TableColumnsType<OperationsIntervention> = [
    { title: '紧急度', dataIndex: 'priority', width: 110, render: (value: string) => <PriorityTag priority={value} /> },
    {
      title: '待办事项', key: 'summary',
      render: (_, item) => <Space direction="vertical" size={3}><Text strong>{item.title}</Text><Text type="secondary">由 {item.signal_count} 次课程信号触发 · {item.why}</Text></Space>,
    },
    { title: '风险类型', dataIndex: 'domain', width: 130, render: (value: string) => <Tag>{operationDomainLabel(value)}</Tag> },
    { title: '教师', key: 'teacher', width: 150, render: (_, item) => <Button type="link" className="table-link" onClick={() => onNavigate('teachers', { teacherId: item.teacher_id })}>{item.teacher_name || item.teacher_id}</Button> },
    { title: '进展', dataIndex: 'status', width: 130, render: (value: string) => interventionBadge(value) },
    { title: '触发时间', dataIndex: 'triggered_at', width: 145, render: (value: string) => <TimeText value={value} /> },
    {
      title: '证据', key: 'evidence', width: 110,
      render: (_, item) => item.source_lesson_id
        ? <Button type="link" className="table-link" onClick={() => onNavigate('lessons', { teacherId: item.teacher_id, lessonId: item.source_lesson_id ?? undefined })}>查看课程</Button>
        : <Text type="secondary">教师证据</Text>,
    },
  ]

  const executiveCopy = !overview
    ? `培养名单共 ${cohortTeacherTotal} 位；最新课程覆盖与风险明细暂未更新。`
    : currentOpsTodoCount > 0
      ? `当前已覆盖 ${observedTeacherTotal} 位新师、${lessonTotal} 节课程；现有 ${currentOpsTodoCount} 项运营待办，其中 ${overview.severe_complaint_cases} 项为严重投诉。`
      : `当前已覆盖 ${observedTeacherTotal} 位新师、${lessonTotal} 节课程；当前没有待处理的运营事项。`

  async function refreshAll() {
    await Promise.all([refresh(), loadOperations()])
  }

  return (
    <div className="page-shell operational-home">
      <PageHeader
        eyebrow="经营总览"
        title="新师 30 天经营总览"
        description="从整体培养与课程表现进入风险、待办，再下钻到单个教师和课程证据。"
        actions={<Button icon={<ReloadOutlined />} loading={operationsLoading} onClick={() => refreshAll()}>更新数据</Button>}
      />

      {!dashboard && !overview && !operationsLoading ? <Alert type="info" showIcon message="数据尚未更新" description="页面切换不会自动请求数据。点击“更新数据”后读取最新经营结果。" /> : null}
      {operationsError ? <Alert type="warning" showIcon message="最新风险明细暂未更新" description="培养与出营汇总仍可查看；风险分类、处置事项和课程下钻请稍后刷新。" /> : null}

      <Card className="executive-brief">
        <Row gutter={[24, 18]} align="middle">
          <Col xs={24} lg={17}>
            <div className="executive-brief-label"><RiseOutlined /> 今日经营判断</div>
            <Title level={3}>{executiveCopy}</Title>
            <Text>{urgentCount > 0 ? `先处理 ${urgentCount} 项高优先级待办，再回看开放任务与出营转化。` : '先看风险结构，再进入教师和课程证据确认具体原因。'}</Text>
          </Col>
          <Col xs={24} lg={7}>
            <Flex gap={10} justify="flex-end" wrap="wrap" className="executive-actions">
              <Button ghost onClick={() => onNavigate('interventions')}>进入待办处置</Button>
              <Button type="primary" onClick={() => onNavigate('lessons')}>查看课程证据</Button>
            </Flex>
          </Col>
        </Row>
      </Card>

      <Row gutter={[14, 14]}>
        <Col xs={12} xl={6}><MetricCard label="有课程记录教师" value={overview ? observedTeacherTotal : '—'} note={`培养名单共 ${cohortTeacherTotal} 位`} icon={<TeamOutlined />} tone="neutral" /></Col>
        <Col xs={12} xl={6}><MetricCard label="30 天课程" value={overview ? lessonTotal : '—'} note={overview ? '当前课程证据总量' : '等待课程明细更新'} icon={<CalendarOutlined />} tone="neutral" /></Col>
        <Col xs={12} xl={6}><MetricCard label="当前待办" value={overview ? currentOpsTodoCount : '—'} note={overview ? '仅统计运营处置事项' : '等待运营待办更新'} icon={<ExclamationCircleOutlined />} tone="danger" /></Col>
        <Col xs={12} xl={6}><MetricCard label="开放个性化任务" value={overview ? openPersonalizedTasks : '—'} note={overview ? `${riskSummary.openActions} 项处置仍开放` : '等待任务明细更新'} icon={<FileSearchOutlined />} tone="warning" /></Col>
      </Row>

      <Row gutter={[16, 16]}>
        <Col xs={24} xl={15}>
          <Card
            className="section-card"
            title="出营转化"
            extra={(
              <Space wrap>
                <Text type="secondary">在职状态</Text>
                <Select<FunnelEmploymentStatus>
                  aria-label="出营转化在职状态"
                  value={funnelEmploymentStatus}
                  onChange={setFunnelEmploymentStatus}
                  options={[
                    { value: 'ALL', label: '全部状态' },
                    { value: 'on', label: '在职' },
                    { value: 'off', label: '非在职' },
                    { value: 'hei', label: '已拉黑删除' },
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
          <Card className="section-card" title="风险结构" extra={<Text type="secondary">{affectedTeacherTotal} 位教师受影响</Text>}>
            <div className="risk-structure-grid">
              {(overview?.risk_breakdown ?? []).map((item, index) => (
                <button className={`risk-structure-item risk-${index === 0 ? 'danger' : index === 1 ? 'warning' : 'success'}`} key={item.domain} onClick={() => onNavigate('interventions', { domain: item.domain })}>
                  <div><Text strong>{operationDomainLabel(item.domain, item.label)}</Text><Text type="secondary">{item.signal_count} 条信号 · {item.teacher_count} 位教师</Text></div>
                  <strong>{item.open_output_count}</strong>
                </button>
              ))}
              {!operationsLoading && !overview?.risk_breakdown.length ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无已识别风险" /> : null}
            </div>
            {overview ? <Flex gap={8} wrap="wrap" className="risk-footnote"><Tag color="red">严重投诉 {overview.severe_complaint_cases}</Tag><Tag color="gold">数据待核 {overview.pending_data_issues}</Tag></Flex> : null}
          </Card>
        </Col>
      </Row>

      <Card
        className="section-card action-table-card"
        title={<Space><ClockCircleOutlined /><span>待办处置</span></Space>}
        extra={<Space><Tag color={urgentCount ? 'error' : 'default'}>{urgentCount} 项优先处理</Tag><Button type="link" onClick={() => onNavigate('interventions')}>查看全部待办</Button></Space>}
      >
        <Table
          rowKey="output_id"
          loading={operationsLoading}
          dataSource={visibleInterventions}
          columns={columns}
          pagination={false}
          scroll={{ x: 980 }}
          locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={operationsError ? '风险明细尚未更新' : '当前没有需要运营介入的事项'} /> }}
        />
      </Card>

      <Row gutter={[16, 16]}>
        <Col xs={24} xl={15}>
          <Card className="section-card" title="五维平均表现" extra={<Text type="secondary">用于发现群体性薄弱项</Text>}>
            <div className="dimension-overview-grid">
              {(dashboard?.dimension_averages ?? []).map((item) => (
                <div key={item.code} className="dimension-overview-item">
                  <Text type="secondary">{item.label}</Text>
                  <Statistic value={item.average} precision={Number.isInteger(item.average) ? 0 : 1} />
                </div>
              ))}
              {!dashboard?.dimension_averages?.length ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无维度汇总" /> : null}
            </div>
          </Card>
        </Col>
        <Col xs={24} xl={9}>
          <Card className="section-card" title="教师状态结构">
            <div className="cohort-structure-list">
              {Object.entries(employmentStructure).map(([label, count]) => (
                <Flex key={label} justify="space-between" align="center"><Text>{label}</Text><Text strong>{count}</Text></Flex>
              ))}
              {!Object.keys(employmentStructure).length ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无状态汇总" /> : null}
            </div>
          </Card>
        </Col>
      </Row>

      <div className="coverage-note">
        <SafetyCertificateOutlined />
        <div><Text strong>数据覆盖说明</Text><Text type="secondary">{mixedDataAlertDescription(dashboard?.score_policy_version)}</Text></div>
      </div>
    </div>
  )
}
