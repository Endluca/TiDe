import { useCallback, useMemo, useState } from 'react'
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
  Flex,
  Input,
  Result,
  Row,
  Select,
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
  SendOutlined,
  UnorderedListOutlined,
} from '@ant-design/icons'
import { api, isRequestCancelled } from '../api'
import { canRetryOutput, displayError, isOperationalTaskAssignment, normalizeOutputList, outputDisplayTypeLabel, outputStatusLabel } from '../domain'
import { interventionOutputTypeLabel, interventionStatusLabel, operationDomainLabel, operationalOutputInterventions } from '../operations'
import type { OperationsIntervention, OutputDisplayType, OutputRecord, SharedTaskAssignment } from '../types'
import { PageHeader, PriorityTag, TimeText } from '../components/Common'
import { useI18n } from '../i18n'
import { useLatestRequest } from '../useLatestRequest'
import type { AppLocale } from '../i18n'

const { Paragraph, Text } = Typography

function outputStatusBadge(status: OutputRecord['status'], locale: AppLocale) {
  const badge = status === 'FAILED' ? 'error' : ['DELIVERED', 'READ', 'CLICKED'].includes(status) ? 'success' : 'processing'
  return <Badge status={badge} text={outputStatusLabel(status, locale)} />
}

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
  const badge = status === 'COMPLETED' ? 'success' : status === 'EXPIRED' ? 'error' : status === 'CANCELLED' ? 'default' : 'processing'
  return <Badge status={badge} text={(locale === 'en-US' ? labelsEn : labels)[status] ?? status} />
}

export default function OutputCenter({ active = true }: { active?: boolean }) {
  const { locale, t } = useI18n()
  const allOutputsRequest = useLatestRequest()
  const assignmentRequest = useLatestRequest()
  const interventionRequest = useLatestRequest()
  const outputRequest = useLatestRequest()
  const [assignments, setAssignments] = useState<SharedTaskAssignment[]>([])
  const [assignmentTotal, setAssignmentTotal] = useState(0)
  const [assignmentPage, setAssignmentPage] = useState(1)
  const [assignmentPageSize, setAssignmentPageSize] = useState(8)
  const [assignmentLoading, setAssignmentLoading] = useState(false)
  const [outputs, setOutputs] = useState<OutputRecord[]>([])
  const [outputTotal, setOutputTotal] = useState(0)
  const [outputPage, setOutputPage] = useState(1)
  const [outputPageSize, setOutputPageSize] = useState(10)
  const [outputCounts, setOutputCounts] = useState<Record<string, number>>({})
  const [outputLoading, setOutputLoading] = useState(false)
  const [interventions, setInterventions] = useState<OperationsIntervention[]>([])
  const [interventionTotal, setInterventionTotal] = useState(0)
  const [interventionPage, setInterventionPage] = useState(1)
  const [interventionPageSize, setInterventionPageSize] = useState(10)
  const [interventionCounts, setInterventionCounts] = useState<Record<string, number>>({})
  const [interventionLoading, setInterventionLoading] = useState(false)
  const [loading, setLoading] = useState(false)
  const [hasLoaded, setHasLoaded] = useState(false)
  const [loadError, setLoadError] = useState('')
  const [keyword, setKeyword] = useState('')
  const [displayType, setDisplayType] = useState('')
  const [selectedAssignment, setSelectedAssignment] = useState<SharedTaskAssignment>()
  const [selectedOutput, setSelectedOutput] = useState<OutputRecord>()
  const [selectedIntervention, setSelectedIntervention] = useState<OperationsIntervention>()
  const [retryingId, setRetryingId] = useState('')
  const load = useCallback(async () => {
    assignmentRequest.cancel()
    interventionRequest.cancel()
    outputRequest.cancel()
    setLoading(true)
    setAssignmentLoading(true)
    setInterventionLoading(true)
    setOutputLoading(true)
    setLoadError('')
    let cancelled = false
    try {
      const [taskRows, outputRows, interventionRows] = await allOutputsRequest.run((options) =>
        Promise.all([
          api.taskAssignments({ include_mock: false, page: 1, page_size: 8 }, options),
          api.outputs({ page: 1, page_size: 10 }, options),
          api.operationsInterventions({
            type: 'NOTIFICATION,OPS_CASE,PENDING_DATA',
            page: 1,
            page_size: 10,
          }, options),
        ]),
      )
      const currentInterventions = operationalOutputInterventions(interventionRows.items)
      const currentOutputs = normalizeOutputList(outputRows)
      setAssignments(taskRows.items.filter(isOperationalTaskAssignment))
      setAssignmentTotal(taskRows.total)
      setAssignmentPage(taskRows.page)
      setAssignmentPageSize(taskRows.page_size)
      setInterventions(currentInterventions)
      setInterventionTotal(interventionRows.total)
      setInterventionPage(interventionRows.page ?? 1)
      setInterventionPageSize(interventionRows.page_size ?? 10)
      setInterventionCounts(interventionRows.counts_by_type ?? {})
      setOutputs(currentOutputs.items)
      setOutputTotal(currentOutputs.total)
      setOutputPage(currentOutputs.page ?? 1)
      setOutputPageSize(currentOutputs.page_size ?? 10)
      setOutputCounts(currentOutputs.counts_by_type ?? {})
    } catch (error) {
      if (isRequestCancelled(error)) {
        cancelled = true
        return
      }
      setAssignments([])
      setAssignmentTotal(0)
      setOutputs([])
      setOutputTotal(0)
      setOutputCounts({})
      setInterventions([])
      setInterventionTotal(0)
      setInterventionCounts({})
      setLoadError(displayError(error, locale))
    } finally {
      if (!cancelled) {
        setHasLoaded(true)
        setAssignmentLoading(false)
        setInterventionLoading(false)
        setOutputLoading(false)
        setLoading(false)
      }
    }
  }, [
    allOutputsRequest,
    assignmentRequest,
    interventionRequest,
    locale,
    outputRequest,
  ])

  async function loadAssignmentPage(page: number, pageSize: number) {
    allOutputsRequest.cancel()
    setLoading(false)
    setInterventionLoading(false)
    setOutputLoading(false)
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
    setOutputLoading(false)
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

  async function loadOutputPage(page: number, pageSize: number) {
    allOutputsRequest.cancel()
    setLoading(false)
    setAssignmentLoading(false)
    setInterventionLoading(false)
    setOutputLoading(true)
    setLoadError('')
    let cancelled = false
    try {
      const result = await outputRequest.run((options) =>
        api.outputs({
          type: displayType || undefined,
          keyword: keyword.trim() || undefined,
          page,
          page_size: pageSize,
        }, options),
      )
      setOutputs(result.items)
      setOutputTotal(result.total)
      setOutputPage(result.page ?? page)
      setOutputPageSize(result.page_size ?? pageSize)
    } catch (error) {
      if (isRequestCancelled(error)) {
        cancelled = true
        return
      }
      setLoadError(displayError(error, locale))
    } finally {
      if (!cancelled) setOutputLoading(false)
    }
  }

  const displayTypeOptions = useMemo(
    () => Object.keys(outputCounts).sort().map((value) => ({
      value,
      label: outputDisplayTypeLabel(value as OutputDisplayType, locale),
    })),
    [locale, outputCounts],
  )

  const counts = useMemo(() => ({
    tasks: assignmentTotal,
    notices: (outputCounts.IN_APP_NOTIFICATION ?? 0) + (outputCounts.REMINDER ?? 0) + (interventionCounts.NOTIFICATION ?? 0),
    cases: (outputCounts.OPS_CASE ?? 0) + (interventionCounts.OPS_CASE ?? 0),
    actions: outputCounts.EXTERNAL_ACTION_REQUEST ?? 0,
    dataIssues: interventionCounts.PENDING_DATA ?? 0,
  }), [assignmentTotal, interventionCounts, outputCounts])

  if (!active) return null

  async function retry(item: OutputRecord) {
    setRetryingId(item.output_id)
    try {
      await api.retryOutput(item.output_id)
      await loadOutputPage(outputPage, outputPageSize)
    } finally {
      setRetryingId('')
    }
  }

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

  const outputColumns: TableColumnsType<OutputRecord> = [
    {
      title: t('输出', 'Delivery'), key: 'output', width: 300,
      render: (_, item) => <Space direction="vertical" size={3}><Text strong>{item.title}</Text><Tag>{outputDisplayTypeLabel(item.display_type, locale)}</Tag></Space>,
    },
    {
      title: t('对象', 'Recipient'), key: 'recipient', width: 220,
      render: (_, item) => <Text>{item.recipient_name || item.recipient_id || '—'}</Text>,
    },
    { title: t('内容', 'Content'), key: 'content', width: 430, render: (_, item) => <Paragraph ellipsis={{ rows: 3, tooltip: item.body || item.content || '' }} style={{ margin: 0 }}>{item.body || item.content || '—'}</Paragraph> },
    { title: t('状态', 'Status'), key: 'status', width: 190, render: (_, item) => <Space direction="vertical" size={3}>{outputStatusBadge(item.status, locale)}<Text type="secondary">{t('尝试', 'Attempts')} {item.attempt_count}/{item.max_attempts}</Text></Space> },
    { title: t('创建时间', 'Created at'), key: 'created', width: 200, render: (_, item) => <TimeText value={item.created_at} /> },
    {
      title: t('操作', 'Action'), key: 'action', width: 150,
      render: (_, item) => <Space><Button type="link" icon={<EyeOutlined />} onClick={() => setSelectedOutput(item)}>{t('详情', 'Details')}</Button>{canRetryOutput(item) ? <Button type="link" loading={retryingId === item.output_id} onClick={() => retry(item)}>{t('重试', 'Retry')}</Button> : null}</Space>,
    },
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

  return <div className="page-shell">
    <PageHeader eyebrow={t('触达与处置', 'Delivery & Actions')} title={t('触达记录', 'Delivery Records')} description={t('查看教师任务、站内通知、运营介入和外部动作的当前进展，及时处理失败与待审批事项。', 'Track teacher tasks, in-app notifications, operations actions, and external actions; address failures and pending approvals.')} actions={<Button icon={<ReloadOutlined />} loading={loading} onClick={() => load()}>{t('刷新', 'Refresh')}</Button>} />

    <Alert type="info" showIcon message={t('按实际状态判断触达结果', 'Use actual status to judge delivery')} description={t('教师任务创建后即可查看；通知和外部动作只有进入已送达或已完成状态，才代表对方实际收到或执行。', 'Teacher tasks appear after creation. Notifications and external actions count as delivered only after they reach a delivered or completed state.')} />

    <Row gutter={[12, 12]}>
      <Col xs={12} lg={6}><Card><Statistic title={t('教师任务', 'Teacher tasks')} value={hasLoaded ? counts.tasks : '—'} prefix={<UnorderedListOutlined />} /></Card></Col>
      <Col xs={12} lg={6}><Card><Statistic title={t('通知与提醒', 'Notifications & reminders')} value={hasLoaded ? counts.notices : '—'} prefix={<BellOutlined />} /></Card></Col>
      <Col xs={12} lg={6}><Card><Statistic title={t('运营事项', 'Operations cases')} value={hasLoaded ? counts.cases : '—'} prefix={<SafetyCertificateOutlined />} /></Card></Col>
      <Col xs={12} lg={6}><Card><Statistic title={t('外部动作', 'External actions')} value={hasLoaded ? counts.actions : '—'} prefix={<SendOutlined />} /></Card></Col>
    </Row>

    {loadError ? <Result status="warning" title={t('输出加载失败', 'Unable to load delivery records')} subTitle={loadError} extra={<Button onClick={() => load()}>{t('重试', 'Try again')}</Button>} /> : <>
      <Card title={t(`提醒与运营事项（${interventionTotal}）`, `Notifications & operations cases (${interventionTotal})`)} extra={counts.dataIssues ? <Tag color="gold">{t(`${counts.dataIssues} 项内部数据待补`, `${counts.dataIssues} internal data issues`)}</Tag> : null} styles={{ body: { padding: 0 } }}>
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

      {hasLoaded && (outputTotal > 0 || keyword || displayType) ? <Card>
        <Flex gap={12} wrap><Input allowClear value={keyword} onChange={(event) => setKeyword(event.target.value)} onPressEnter={() => void loadOutputPage(1, outputPageSize)} placeholder={t('搜索输出、教师或来源', 'Search delivery, teacher, or source')} style={{ width: 340 }} /><Select allowClear value={displayType || undefined} onChange={(value) => setDisplayType(value || '')} placeholder={t('全部输出类型', 'All delivery types')} options={displayTypeOptions} style={{ width: 210 }} /><Button onClick={() => void loadOutputPage(1, outputPageSize)}>{t('查询', 'Search')}</Button><Text type="secondary" style={{ marginLeft: 'auto' }}>{t(`共 ${outputTotal} 条`, `${outputTotal} total`)}</Text></Flex>
      </Card> : null}
      {hasLoaded && (outputTotal > 0 || keyword || displayType) ? <Card title={t(`其他触达记录（${outputTotal}）`, `Other delivery records (${outputTotal})`)} styles={{ body: { padding: 0 } }}>
        <Table
          rowKey="output_id"
          loading={outputLoading}
          dataSource={outputs}
          columns={outputColumns}
          pagination={{
            current: outputPage,
            pageSize: outputPageSize,
            total: outputTotal,
            showSizeChanger: true,
            pageSizeOptions: [10, 20, 50, 100],
            hideOnSinglePage: true,
          }}
          onChange={(pagination) => {
            void loadOutputPage(
              pagination.current ?? 1,
              pagination.pageSize ?? outputPageSize,
            )
          }}
          scroll={{ x: 1520 }}
          locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t('当前没有通知、运营事项或外部动作', 'No notifications, operations cases, or external actions')} /> }}
        />
      </Card> : null}
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

    <Drawer width={760} title={selectedOutput ? `${t('输出', 'Delivery')} · ${selectedOutput.output_id}` : t('输出', 'Delivery')} open={Boolean(selectedOutput)} onClose={() => setSelectedOutput(undefined)}>
      {selectedOutput ? <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <Descriptions bordered size="small" column={1}>
          <Descriptions.Item label={t('类型', 'Type')}>{outputDisplayTypeLabel(selectedOutput.display_type, locale)}</Descriptions.Item>
          <Descriptions.Item label={t('对象', 'Recipient')}>{selectedOutput.recipient_name || selectedOutput.recipient_id || '—'}</Descriptions.Item>
          <Descriptions.Item label={t('内容', 'Content')}>{selectedOutput.body || selectedOutput.content || '—'}</Descriptions.Item>
          <Descriptions.Item label={t('状态', 'Status')}>{outputStatusBadge(selectedOutput.status, locale)}</Descriptions.Item>
          <Descriptions.Item label={t('错误', 'Error')}>{selectedOutput.last_error ? JSON.stringify(selectedOutput.last_error) : '—'}</Descriptions.Item>
          <Descriptions.Item label={t('创建时间', 'Created at')}><TimeText value={selectedOutput.created_at} /></Descriptions.Item>
        </Descriptions>
        <Collapse ghost items={[{
          key: 'debug',
          label: t('调试信息', 'Debug information'),
          children: <Descriptions bordered size="small" column={1}>
            <Descriptions.Item label={t('输出记录 ID', 'Delivery ID')}>{selectedOutput.output_id}</Descriptions.Item>
            <Descriptions.Item label={t('来源类型', 'Source type')}>{selectedOutput.source_type}</Descriptions.Item>
            <Descriptions.Item label={t('来源记录 ID', 'Source ID')}>{selectedOutput.source_id}</Descriptions.Item>
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
