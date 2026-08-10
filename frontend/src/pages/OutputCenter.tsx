import { useCallback, useState } from 'react'
import {
  Alert,
  Badge,
  Button,
  Card,
  Col,
  Collapse,
  Descriptions,
  Drawer,
  Empty,
  Result,
  Row,
  Space,
  Statistic,
  Table,
  Tag,
  Typography,
} from 'antd'
import type { TableColumnsType } from 'antd'
import {
  BellOutlined,
  EyeOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
  UnorderedListOutlined,
} from '@ant-design/icons'
import { api, isRequestCancelled } from '../api'
import { displayError, isOperationalTaskAssignment } from '../domain'
import {
  interventionOutputTypeLabel,
  interventionStatusLabel,
  operationDomainLabel,
  operationalOutputInterventions,
} from '../operations'
import type { OperationsIntervention, SharedTaskAssignment } from '../types'
import { PageHeader, PriorityTag, TimeText } from '../components/Common'
import { useI18n } from '../i18n'
import { useLatestRequest } from '../useLatestRequest'
import type { AppLocale } from '../i18n'

const { Paragraph, Text } = Typography

function interventionStatusBadge(status: string, locale: AppLocale) {
  const badge = status === 'FAILED' || status === 'OUTPUT_MISSING'
    ? 'error'
    : ['RESOLVED', 'CLOSED', 'DELIVERED', 'READ', 'CLICKED', 'COMPLETED'].includes(status)
      ? 'success'
      : status === 'PENDING_DATA' || status === 'ACTION_PENDING'
        ? 'warning'
        : 'processing'
  return <Badge status={badge} text={interventionStatusLabel(status, locale)} />
}

function assignmentStatusBadge(status: string, locale: AppLocale) {
  const labels: Record<string, string> = {
    ASSIGNED: '待查看',
    VIEWED: '已查看',
    IN_PROGRESS: '进行中',
    SUBMITTED: '已提交',
    COMPLETED: '已完成',
    EXPIRED: '已逾期',
    CANCELLED: '已取消',
  }
  const labelsEn: Record<string, string> = {
    ASSIGNED: 'Not viewed',
    VIEWED: 'Viewed',
    IN_PROGRESS: 'In progress',
    SUBMITTED: 'Submitted',
    COMPLETED: 'Completed',
    EXPIRED: 'Overdue',
    CANCELLED: 'Cancelled',
  }
  const badge = status === 'COMPLETED'
    ? 'success'
    : status === 'EXPIRED'
      ? 'error'
      : status === 'CANCELLED'
        ? 'default'
        : 'processing'
  return <Badge status={badge} text={(locale === 'en-US' ? labelsEn : labels)[status] ?? status} />
}

export default function OutputCenter({ active = true }: { active?: boolean }) {
  const { locale, t } = useI18n()
  const allOutputsRequest = useLatestRequest()
  const assignmentRequest = useLatestRequest()
  const interventionRequest = useLatestRequest()
  const [assignments, setAssignments] = useState<SharedTaskAssignment[]>([])
  const [assignmentTotal, setAssignmentTotal] = useState(0)
  const [assignmentPage, setAssignmentPage] = useState(1)
  const [assignmentPageSize, setAssignmentPageSize] = useState(8)
  const [assignmentLoading, setAssignmentLoading] = useState(false)
  const [interventions, setInterventions] = useState<OperationsIntervention[]>([])
  const [interventionTotal, setInterventionTotal] = useState(0)
  const [interventionPage, setInterventionPage] = useState(1)
  const [interventionPageSize, setInterventionPageSize] = useState(10)
  const [interventionCounts, setInterventionCounts] = useState<Record<string, number>>({})
  const [interventionLoading, setInterventionLoading] = useState(false)
  const [loading, setLoading] = useState(false)
  const [hasLoaded, setHasLoaded] = useState(false)
  const [loadError, setLoadError] = useState('')
  const [selectedAssignment, setSelectedAssignment] = useState<SharedTaskAssignment>()
  const [selectedIntervention, setSelectedIntervention] = useState<OperationsIntervention>()

  const load = useCallback(async () => {
    assignmentRequest.cancel()
    interventionRequest.cancel()
    setLoading(true)
    setAssignmentLoading(true)
    setInterventionLoading(true)
    setLoadError('')
    let cancelled = false
    try {
      const [taskRows, interventionRows] = await allOutputsRequest.run((options) =>
        Promise.all([
          api.taskAssignments({ include_mock: false, page: 1, page_size: 8 }, options),
          api.operationsInterventions({
            type: 'NOTIFICATION,OPS_CASE,PENDING_DATA',
            page: 1,
            page_size: 10,
          }, options),
        ]),
      )
      setAssignments(taskRows.items.filter(isOperationalTaskAssignment))
      setAssignmentTotal(taskRows.total)
      setAssignmentPage(taskRows.page)
      setAssignmentPageSize(taskRows.page_size)
      setInterventions(operationalOutputInterventions(interventionRows.items))
      setInterventionTotal(interventionRows.total)
      setInterventionPage(interventionRows.page ?? 1)
      setInterventionPageSize(interventionRows.page_size ?? 10)
      setInterventionCounts(interventionRows.counts_by_type ?? {})
    } catch (error) {
      if (isRequestCancelled(error)) {
        cancelled = true
        return
      }
      setAssignments([])
      setAssignmentTotal(0)
      setInterventions([])
      setInterventionTotal(0)
      setInterventionCounts({})
      setLoadError(displayError(error, locale))
    } finally {
      if (!cancelled) {
        setHasLoaded(true)
        setAssignmentLoading(false)
        setInterventionLoading(false)
        setLoading(false)
      }
    }
  }, [allOutputsRequest, assignmentRequest, interventionRequest, locale])

  async function loadAssignmentPage(page: number, pageSize: number) {
    allOutputsRequest.cancel()
    setLoading(false)
    setInterventionLoading(false)
    setAssignmentLoading(true)
    setLoadError('')
    let cancelled = false
    try {
      const taskRows = await assignmentRequest.run((options) =>
        api.taskAssignments({
          include_mock: false,
          page,
          page_size: pageSize,
        }, options),
      )
      setAssignments(taskRows.items.filter(isOperationalTaskAssignment))
      setAssignmentTotal(taskRows.total)
      setAssignmentPage(taskRows.page)
      setAssignmentPageSize(taskRows.page_size)
    } catch (error) {
      if (isRequestCancelled(error)) {
        cancelled = true
        return
      }
      setLoadError(displayError(error, locale))
    } finally {
      if (!cancelled) setAssignmentLoading(false)
    }
  }

  async function loadInterventionPage(page: number, pageSize: number) {
    allOutputsRequest.cancel()
    setLoading(false)
    setAssignmentLoading(false)
    setInterventionLoading(true)
    setLoadError('')
    let cancelled = false
    try {
      const result = await interventionRequest.run((options) =>
        api.operationsInterventions({
          type: 'NOTIFICATION,OPS_CASE,PENDING_DATA',
          page,
          page_size: pageSize,
        }, options),
      )
      setInterventions(operationalOutputInterventions(result.items))
      setInterventionTotal(result.total)
      setInterventionPage(result.page ?? page)
      setInterventionPageSize(result.page_size ?? pageSize)
      setInterventionCounts(result.counts_by_type ?? {})
    } catch (error) {
      if (isRequestCancelled(error)) {
        cancelled = true
        return
      }
      setLoadError(displayError(error, locale))
    } finally {
      if (!cancelled) setInterventionLoading(false)
    }
  }

  if (!active) return null

  const taskColumns: TableColumnsType<SharedTaskAssignment> = [
    {
      title: t('教师任务', 'Teacher task'), key: 'task', width: 280,
      render: (_, item) => <Space direction="vertical" size={3}><Text strong>{item.title || item.task_code}</Text><Text code>{item.task_code}</Text><Space size={5}><Tag color={item.task_kind === 'FIXED_GROWTH' ? 'purple' : 'blue'}>{item.task_kind === 'FIXED_GROWTH' ? t('必修成长', 'Mandatory growth') : t('个性化改善', 'Personalized improvement')}</Tag><PriorityTag priority={item.priority} /></Space></Space>,
    },
    {
      title: t('接收教师', 'Teacher'), key: 'teacher', width: 190,
      render: (_, item) => <Space direction="vertical" size={2}><Text>{item.teacher_name || item.teacher_id}</Text><Text code>{item.teacher_id}</Text></Space>,
    },
    { title: t('为什么产生', 'Why it was assigned'), dataIndex: 'why', width: 430, render: (value: string) => <Paragraph ellipsis={{ rows: 3, tooltip: value }} style={{ margin: 0 }}>{value}</Paragraph> },
    { title: t('状态', 'Status'), key: 'status', width: 180, render: (_, item) => assignmentStatusBadge(item.status, locale) },
    { title: t('更新时间', 'Updated at'), key: 'updated', width: 200, render: (_, item) => <TimeText value={item.updated_at} /> },
    { title: t('操作', 'Action'), key: 'action', width: 90, render: (_, item) => <Button type="link" icon={<EyeOutlined />} onClick={() => setSelectedAssignment(item)}>{t('详情', 'Details')}</Button> },
  ]

  const interventionColumns: TableColumnsType<OperationsIntervention> = [
    {
      title: t('类型', 'Type'), dataIndex: 'output_type', width: 150,
      render: (value: string) => <Tag color={value === 'PENDING_DATA' ? 'gold' : value === 'OPS_CASE' ? 'red' : 'blue'}>{interventionOutputTypeLabel(value, locale)}</Tag>,
    },
    { title: t('对象', 'Recipient'), key: 'teacher', width: 180, render: (_, item) => <Space direction="vertical" size={2}><Text strong>{item.teacher_name || item.teacher_id}</Text><Text type="secondary">{item.teacher_id}</Text></Space> },
    {
      title: t('内容与原因', 'Content and reason'), key: 'content',
      render: (_, item) => <Space direction="vertical" size={3}><Text strong>{item.title}</Text><Text type="secondary">{operationDomainLabel(item.domain, undefined, locale)} · {t(`由 ${item.signal_count} 次课程信号触发`, `Triggered by ${item.signal_count} lesson signals`)}</Text><Paragraph ellipsis={{ rows: 2, tooltip: item.why }} style={{ margin: 0 }}>{item.why}</Paragraph></Space>,
    },
    { title: t('状态', 'Status'), dataIndex: 'status', width: 140, render: (value: string) => interventionStatusBadge(value, locale) },
    { title: t('触发时间', 'Triggered at'), dataIndex: 'triggered_at', width: 160, render: (value: string) => <TimeText value={value} /> },
    { title: t('操作', 'Action'), key: 'action', width: 90, render: (_, item) => <Button type="link" icon={<EyeOutlined />} onClick={() => setSelectedIntervention(item)}>{t('详情', 'Details')}</Button> },
  ]

  const notificationCount = interventionCounts.NOTIFICATION ?? 0
  const caseCount = interventionCounts.OPS_CASE ?? 0
  const dataIssueCount = interventionCounts.PENDING_DATA ?? 0

  return <div className="page-shell">
    <PageHeader eyebrow={t('触达与处置', 'Delivery & Actions')} title={t('触达记录', 'Delivery Records')} description={t('查看教师任务、站内通知和运营介入的当前进展，及时处理失败和待补数据。', 'Track teacher tasks, in-app notifications, and operations interventions; address failures and missing data.')} actions={<Button icon={<ReloadOutlined />} loading={loading} onClick={() => load()}>{t('刷新', 'Refresh')}</Button>} />

    <Alert type="info" showIcon message={t('按实际状态判断触达结果', 'Use actual status to judge delivery')} description={t('教师任务创建后即可查看；通知只有进入已送达状态，才代表教师实际收到。', 'Teacher tasks appear after creation. A notification counts as delivered only after it reaches the delivered state.')} />

    <Row gutter={[12, 12]}>
      <Col xs={24} lg={8}><Card><Statistic title={t('教师任务', 'Teacher tasks')} value={hasLoaded ? assignmentTotal : '—'} prefix={<UnorderedListOutlined />} /></Card></Col>
      <Col xs={24} lg={8}><Card><Statistic title={t('通知与提醒', 'Notifications & reminders')} value={hasLoaded ? notificationCount : '—'} prefix={<BellOutlined />} /></Card></Col>
      <Col xs={24} lg={8}><Card><Statistic title={t('运营事项', 'Operations cases')} value={hasLoaded ? caseCount : '—'} prefix={<SafetyCertificateOutlined />} /></Card></Col>
    </Row>

    {loadError ? <Result status="warning" title={t('触达记录加载失败', 'Unable to load delivery records')} subTitle={loadError} extra={<Button onClick={() => load()}>{t('重试', 'Try again')}</Button>} /> : <>
      <Card title={t(`提醒与运营事项（${interventionTotal}）`, `Notifications & operations cases (${interventionTotal})`)} extra={dataIssueCount ? <Tag color="gold">{t(`${dataIssueCount} 项内部数据待补`, `${dataIssueCount} internal data issues`)}</Tag> : null} styles={{ body: { padding: 0 } }}>
        <Table
          rowKey="output_id"
          loading={interventionLoading}
          dataSource={interventions}
          columns={interventionColumns}
          pagination={{
            current: interventionPage,
            pageSize: interventionPageSize,
            total: interventionTotal,
            showSizeChanger: true,
            pageSizeOptions: [10, 20, 50, 100],
            hideOnSinglePage: true,
          }}
          onChange={(pagination) => {
            void loadInterventionPage(
              pagination.current ?? 1,
              pagination.pageSize ?? interventionPageSize,
            )
          }}
          scroll={{ x: 1050 }}
          locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={hasLoaded ? t('当前没有提醒、运营事项或数据待补', 'No notifications, operations cases, or data issues') : t('尚未读取触达记录，点击“刷新”', 'Delivery records have not been loaded. Select “Refresh”.')} /> }}
        />
      </Card>

      <Card title={t(`教师任务（${assignmentTotal}）`, `Teacher tasks (${assignmentTotal})`)} styles={{ body: { padding: 0 } }}>
        <Table
          rowKey="assignment_id"
          loading={assignmentLoading}
          dataSource={assignments}
          columns={taskColumns}
          pagination={{
            current: assignmentPage,
            pageSize: assignmentPageSize,
            total: assignmentTotal,
            showSizeChanger: true,
            pageSizeOptions: [8, 20, 50, 100],
            hideOnSinglePage: true,
          }}
          onChange={(pagination) => {
            void loadAssignmentPage(
              pagination.current ?? 1,
              pagination.pageSize ?? assignmentPageSize,
            )
          }}
          scroll={{ x: 1320 }}
          locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t('当前没有任务实例', 'No task assignments')} /> }}
        />
      </Card>
    </>}

    <Drawer width={760} title={selectedAssignment ? `${t('任务', 'Task')} · ${selectedAssignment.assignment_id}` : t('任务', 'Task')} open={Boolean(selectedAssignment)} onClose={() => setSelectedAssignment(undefined)}>
      {selectedAssignment ? <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <Descriptions bordered size="small" column={1}>
          <Descriptions.Item label={t('任务', 'Task')}>{selectedAssignment.title || selectedAssignment.task_code}</Descriptions.Item>
          <Descriptions.Item label={t('任务编号', 'Task ID')}>{selectedAssignment.task_code}</Descriptions.Item>
          <Descriptions.Item label={t('教师', 'Teacher')}>{selectedAssignment.teacher_id}</Descriptions.Item>
          <Descriptions.Item label={t('类型', 'Type')}>{selectedAssignment.task_kind}</Descriptions.Item>
          <Descriptions.Item label={t('为什么', 'Why')}>{selectedAssignment.why}</Descriptions.Item>
          <Descriptions.Item label={t('状态', 'Status')}>{assignmentStatusBadge(selectedAssignment.status, locale)}</Descriptions.Item>
          <Descriptions.Item label={t('更新时间', 'Updated at')}><TimeText value={selectedAssignment.updated_at} /></Descriptions.Item>
        </Descriptions>
        <Collapse ghost items={[{
          key: 'debug',
          label: t('调试信息', 'Debug information'),
          children: <Descriptions bordered size="small" column={1}>
            <Descriptions.Item label={t('任务记录 ID', 'Assignment ID')}>{selectedAssignment.assignment_id}</Descriptions.Item>
            <Descriptions.Item label={t('更新序号', 'Row version')}>{selectedAssignment.row_version}</Descriptions.Item>
            <Descriptions.Item label={t('写入服务', 'Creator system')}>{selectedAssignment.creator_system}</Descriptions.Item>
            <Descriptions.Item label={t('数据标记', 'Source mode')}>{selectedAssignment.source_mode}</Descriptions.Item>
          </Descriptions>,
        }]} />
      </Space> : null}
    </Drawer>

    <Drawer width={720} title={selectedIntervention?.title ?? t('提醒与运营事项', 'Notification or operations case')} open={Boolean(selectedIntervention)} onClose={() => setSelectedIntervention(undefined)}>
      {selectedIntervention ? <Descriptions bordered size="small" column={1}>
        <Descriptions.Item label={t('类型', 'Type')}>{interventionOutputTypeLabel(selectedIntervention.output_type, locale)}</Descriptions.Item>
        <Descriptions.Item label={t('教师', 'Teacher')}>{selectedIntervention.teacher_name || selectedIntervention.teacher_id}</Descriptions.Item>
        <Descriptions.Item label={t('风险类型', 'Risk type')}>{operationDomainLabel(selectedIntervention.domain, undefined, locale)}</Descriptions.Item>
        <Descriptions.Item label={t('触发范围', 'Trigger scope')}>{t(`由 ${selectedIntervention.signal_count} 次课程信号触发`, `Triggered by ${selectedIntervention.signal_count} lesson signals`)}</Descriptions.Item>
        <Descriptions.Item label={t('触发原因', 'Trigger reason')}>{selectedIntervention.why}</Descriptions.Item>
        <Descriptions.Item label={t('关键证据', 'Key evidence')}>{selectedIntervention.evidence_summary}</Descriptions.Item>
        <Descriptions.Item label={t('当前状态', 'Current status')}>{interventionStatusBadge(selectedIntervention.status, locale)}</Descriptions.Item>
        <Descriptions.Item label={t('触发时间', 'Triggered at')}><TimeText value={selectedIntervention.triggered_at} /></Descriptions.Item>
      </Descriptions> : null}
    </Drawer>
  </div>
}
