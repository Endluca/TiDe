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
import { api } from '../api'
import { canRetryOutput, displayError, isOperationalOutput, isOperationalTaskAssignment, normalizeOutputList, outputDisplayTypeLabels, outputStatusLabels } from '../domain'
import { interventionOutputTypeLabel, interventionStatusLabel, operationDomainLabel, operationalOutputInterventions } from '../operations'
import type { AppSnapshot, OperationsIntervention, OutputRecord, SharedTaskAssignment } from '../types'
import { PageHeader, PriorityTag, TimeText } from '../components/Common'

const { Paragraph, Text } = Typography

function outputStatusBadge(status: OutputRecord['status']) {
  const badge = status === 'FAILED' ? 'error' : ['DELIVERED', 'READ', 'CLICKED'].includes(status) ? 'success' : 'processing'
  return <Badge status={badge} text={outputStatusLabels[status] ?? status} />
}

function interventionStatusBadge(status: string) {
  const badge = status === 'FAILED' || status === 'OUTPUT_MISSING'
    ? 'error'
    : ['RESOLVED', 'CLOSED', 'DELIVERED', 'READ', 'CLICKED', 'COMPLETED'].includes(status)
      ? 'success'
      : status === 'PENDING_DATA' || status === 'ACTION_PENDING'
        ? 'warning'
        : 'processing'
  return <Badge status={badge} text={interventionStatusLabel(status)} />
}

function assignmentStatusBadge(status: string) {
  const labels: Record<string, string> = {
    ASSIGNED: '待查看',
    VIEWED: '已查看',
    IN_PROGRESS: '进行中',
    SUBMITTED: '已提交',
    COMPLETED: '已完成',
    EXPIRED: '已逾期',
    CANCELLED: '已取消',
  }
  const badge = status === 'COMPLETED' ? 'success' : status === 'EXPIRED' ? 'error' : status === 'CANCELLED' ? 'default' : 'processing'
  return <Badge status={badge} text={labels[status] ?? status} />
}

export default function OutputCenter({ snapshot }: { snapshot: AppSnapshot }) {
  const [assignments, setAssignments] = useState<SharedTaskAssignment[]>([])
  const [outputs, setOutputs] = useState<OutputRecord[]>([])
  const [interventions, setInterventions] = useState<OperationsIntervention[]>([])
  const [loading, setLoading] = useState(false)
  const [hasLoaded, setHasLoaded] = useState(false)
  const [loadError, setLoadError] = useState('')
  const [keyword, setKeyword] = useState('')
  const [displayType, setDisplayType] = useState('')
  const [selectedAssignment, setSelectedAssignment] = useState<SharedTaskAssignment>()
  const [selectedOutput, setSelectedOutput] = useState<OutputRecord>()
  const [selectedIntervention, setSelectedIntervention] = useState<OperationsIntervention>()
  const [retryingId, setRetryingId] = useState('')
  const teacherNames = useMemo(
    () => new Map(snapshot.teachers.map((teacher) => [teacher.teacher_id, teacher.name])),
    [snapshot.teachers],
  )

  const load = useCallback(async () => {
    setLoading(true)
    setLoadError('')
    try {
      const [taskRows, outputRows, notificationRows, caseRows, pendingDataRows] = await Promise.all([
        api.taskAssignments(),
        api.outputs(),
        api.operationsInterventions({ type: 'NOTIFICATION', page: 1, page_size: 500 }),
        api.operationsInterventions({ type: 'OPS_CASE', page: 1, page_size: 500 }),
        api.operationsInterventions({ type: 'PENDING_DATA', page: 1, page_size: 500 }),
      ])
      const currentInterventions = operationalOutputInterventions([
        ...notificationRows.items,
        ...caseRows.items,
        ...pendingDataRows.items,
      ])
      const currentOutputIds = new Set(currentInterventions.map((item) => item.output_id))
      setAssignments(taskRows.filter(isOperationalTaskAssignment))
      setInterventions(currentInterventions)
      setOutputs(normalizeOutputList(outputRows).items.filter((item) => item.display_type !== 'TASK_ASSIGNMENT' && isOperationalOutput(item) && !currentOutputIds.has(item.output_id)))
    } catch (error) {
      setAssignments([])
      setOutputs([])
      setInterventions([])
      setLoadError(displayError(error))
    } finally {
      setHasLoaded(true)
      setLoading(false)
    }
  }, [])

  const filteredOutputs = useMemo(() => {
    const needle = keyword.trim().toLocaleLowerCase()
    return outputs.filter((item) => {
      const searchable = [item.output_id, item.title, item.body, item.teacher_id, teacherNames.get(item.teacher_id ?? ''), item.source_type, item.source_id].filter(Boolean).join(' ').toLocaleLowerCase()
      return (!needle || searchable.includes(needle)) && (!displayType || item.display_type === displayType)
    })
  }, [displayType, keyword, outputs, teacherNames])

  const displayTypeOptions = useMemo(
    () => [...new Set(outputs.map((item) => item.display_type))].sort().map((value) => ({ value, label: outputDisplayTypeLabels[value] ?? value })),
    [outputs],
  )

  const counts = useMemo(() => ({
    tasks: assignments.length,
    notices: outputs.filter((item) => ['IN_APP_NOTIFICATION', 'REMINDER'].includes(item.display_type)).length + interventions.filter((item) => item.output_type === 'NOTIFICATION').length,
    cases: outputs.filter((item) => item.display_type === 'OPS_CASE').length + interventions.filter((item) => item.output_type === 'OPS_CASE').length,
    actions: outputs.filter((item) => item.display_type === 'EXTERNAL_ACTION_REQUEST').length,
    dataIssues: interventions.filter((item) => item.output_type === 'PENDING_DATA').length,
  }), [assignments.length, interventions, outputs])

  async function retry(item: OutputRecord) {
    setRetryingId(item.output_id)
    try {
      await api.retryOutput(item.output_id)
      await load()
    } finally {
      setRetryingId('')
    }
  }

  const taskColumns: TableColumnsType<SharedTaskAssignment> = [
    {
      title: '教师任务', key: 'task', width: 280,
      render: (_, item) => <Space direction="vertical" size={3}><Text strong>{item.title || item.task_code}</Text><Text code>{item.task_code}</Text><Space size={5}><Tag color={item.task_kind === 'FIXED_GROWTH' ? 'purple' : 'blue'}>{item.task_kind === 'FIXED_GROWTH' ? '必修成长' : '个性化改善'}</Tag><PriorityTag priority={item.priority} /></Space></Space>,
    },
    {
      title: '接收教师', key: 'teacher', width: 190,
      render: (_, item) => <Space direction="vertical" size={2}><Text>{item.teacher_name || teacherNames.get(item.teacher_id) || item.teacher_id}</Text><Text code>{item.teacher_id}</Text></Space>,
    },
    { title: '为什么产生', dataIndex: 'why', width: 430, render: (value: string) => <Paragraph ellipsis={{ rows: 3, tooltip: value }} style={{ margin: 0 }}>{value}</Paragraph> },
    { title: '状态', key: 'status', width: 180, render: (_, item) => assignmentStatusBadge(item.status) },
    { title: '更新时间', key: 'updated', width: 200, render: (_, item) => <TimeText value={item.updated_at} /> },
    { title: '操作', key: 'action', width: 90, render: (_, item) => <Button type="link" icon={<EyeOutlined />} onClick={() => setSelectedAssignment(item)}>详情</Button> },
  ]

  const outputColumns: TableColumnsType<OutputRecord> = [
    {
      title: '输出', key: 'output', width: 300,
      render: (_, item) => <Space direction="vertical" size={3}><Text strong>{item.title}</Text><Tag>{outputDisplayTypeLabels[item.display_type] ?? item.display_type}</Tag></Space>,
    },
    {
      title: '对象', key: 'recipient', width: 220,
      render: (_, item) => <Text>{item.recipient_name || teacherNames.get(item.teacher_id ?? '') || item.recipient_id || '—'}</Text>,
    },
    { title: '内容', key: 'content', width: 430, render: (_, item) => <Paragraph ellipsis={{ rows: 3, tooltip: item.body || item.content || '' }} style={{ margin: 0 }}>{item.body || item.content || '—'}</Paragraph> },
    { title: '状态', key: 'status', width: 190, render: (_, item) => <Space direction="vertical" size={3}>{outputStatusBadge(item.status)}<Text type="secondary">尝试 {item.attempt_count}/{item.max_attempts}</Text></Space> },
    { title: '创建时间', key: 'created', width: 200, render: (_, item) => <TimeText value={item.created_at} /> },
    {
      title: '操作', key: 'action', width: 150,
      render: (_, item) => <Space><Button type="link" icon={<EyeOutlined />} onClick={() => setSelectedOutput(item)}>详情</Button>{canRetryOutput(item) ? <Button type="link" loading={retryingId === item.output_id} onClick={() => retry(item)}>重试</Button> : null}</Space>,
    },
  ]

  const interventionColumns: TableColumnsType<OperationsIntervention> = [
    {
      title: '类型', dataIndex: 'output_type', width: 150,
      render: (value: string) => <Tag color={value === 'PENDING_DATA' ? 'gold' : value === 'OPS_CASE' ? 'red' : 'blue'}>{interventionOutputTypeLabel(value)}</Tag>,
    },
    { title: '对象', key: 'teacher', width: 180, render: (_, item) => <Space direction="vertical" size={2}><Text strong>{item.teacher_name || item.teacher_id}</Text><Text type="secondary">{item.teacher_id}</Text></Space> },
    {
      title: '内容与原因', key: 'content',
      render: (_, item) => <Space direction="vertical" size={3}><Text strong>{item.title}</Text><Text type="secondary">{operationDomainLabel(item.domain)} · 由 {item.signal_count} 次课程信号触发</Text><Paragraph ellipsis={{ rows: 2, tooltip: item.why }} style={{ margin: 0 }}>{item.why}</Paragraph></Space>,
    },
    { title: '状态', dataIndex: 'status', width: 140, render: (value: string) => interventionStatusBadge(value) },
    { title: '触发时间', dataIndex: 'triggered_at', width: 160, render: (value: string) => <TimeText value={value} /> },
    { title: '操作', key: 'action', width: 90, render: (_, item) => <Button type="link" icon={<EyeOutlined />} onClick={() => setSelectedIntervention(item)}>详情</Button> },
  ]

  return <div className="page-shell">
    <PageHeader eyebrow="触达与处置" title="触达记录" description="查看教师任务、站内通知、运营介入和外部动作的当前进展，及时处理失败与待审批事项。" actions={<Button icon={<ReloadOutlined />} loading={loading} onClick={() => load()}>刷新</Button>} />

    <Alert type="info" showIcon message="按实际状态判断触达结果" description="教师任务创建后即可查看；通知和外部动作只有进入已送达或已完成状态，才代表对方实际收到或执行。" />

    <Row gutter={[12, 12]}>
      <Col xs={12} lg={6}><Card><Statistic title="教师任务" value={hasLoaded ? counts.tasks : '—'} prefix={<UnorderedListOutlined />} /></Card></Col>
      <Col xs={12} lg={6}><Card><Statistic title="通知与提醒" value={hasLoaded ? counts.notices : '—'} prefix={<BellOutlined />} /></Card></Col>
      <Col xs={12} lg={6}><Card><Statistic title="运营事项" value={hasLoaded ? counts.cases : '—'} prefix={<SafetyCertificateOutlined />} /></Card></Col>
      <Col xs={12} lg={6}><Card><Statistic title="外部动作" value={hasLoaded ? counts.actions : '—'} prefix={<SendOutlined />} /></Card></Col>
    </Row>

    {loadError ? <Result status="warning" title="输出加载失败" subTitle={loadError} extra={<Button onClick={() => load()}>重试</Button>} /> : <>
      <Card title={`提醒与运营事项（${interventions.length}）`} extra={counts.dataIssues ? <Tag color="gold">{counts.dataIssues} 项内部数据待补</Tag> : null} styles={{ body: { padding: 0 } }}>
        <Table rowKey="output_id" loading={loading} dataSource={interventions} columns={interventionColumns} pagination={{ pageSize: 10, hideOnSinglePage: true }} scroll={{ x: 1050 }} locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={hasLoaded ? '当前没有提醒、运营事项或数据待补' : '尚未读取触达记录，点击“刷新”'} /> }} />
      </Card>

      <Card title={`教师任务（${assignments.length}）`} styles={{ body: { padding: 0 } }}>
        <Table rowKey="assignment_id" loading={loading} dataSource={assignments} columns={taskColumns} pagination={{ pageSize: 8, hideOnSinglePage: true }} scroll={{ x: 1320 }} locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前没有任务实例" /> }} />
      </Card>

      {outputs.length ? <Card>
        <Flex gap={12} wrap><Input allowClear value={keyword} onChange={(event) => setKeyword(event.target.value)} placeholder="搜索输出、教师或来源" style={{ width: 340 }} /><Select allowClear value={displayType || undefined} onChange={(value) => setDisplayType(value || '')} placeholder="全部输出类型" options={displayTypeOptions} style={{ width: 210 }} /><Text type="secondary" style={{ marginLeft: 'auto' }}>显示 {filteredOutputs.length} / {outputs.length}</Text></Flex>
      </Card> : null}
      {outputs.length ? <Card title={`其他触达记录（${filteredOutputs.length}）`} styles={{ body: { padding: 0 } }}>
        <Table rowKey="output_id" loading={loading} dataSource={filteredOutputs} columns={outputColumns} pagination={{ pageSize: 10, hideOnSinglePage: true }} scroll={{ x: 1520 }} locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前没有通知、运营事项或外部动作" /> }} />
      </Card> : null}
    </>}

    <Drawer width={760} title={selectedAssignment ? `任务 · ${selectedAssignment.assignment_id}` : '任务'} open={Boolean(selectedAssignment)} onClose={() => setSelectedAssignment(undefined)}>
      {selectedAssignment ? <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <Descriptions bordered size="small" column={1}>
          <Descriptions.Item label="任务">{selectedAssignment.title || selectedAssignment.task_code}</Descriptions.Item>
          <Descriptions.Item label="任务编号">{selectedAssignment.task_code}</Descriptions.Item>
          <Descriptions.Item label="教师">{selectedAssignment.teacher_id}</Descriptions.Item>
          <Descriptions.Item label="类型">{selectedAssignment.task_kind}</Descriptions.Item>
          <Descriptions.Item label="为什么">{selectedAssignment.why}</Descriptions.Item>
          <Descriptions.Item label="状态">{assignmentStatusBadge(selectedAssignment.status)}</Descriptions.Item>
          <Descriptions.Item label="更新时间"><TimeText value={selectedAssignment.updated_at} /></Descriptions.Item>
        </Descriptions>
        <Collapse ghost items={[{
          key: 'debug',
          label: '调试信息',
          children: <Descriptions bordered size="small" column={1}>
            <Descriptions.Item label="任务记录 ID">{selectedAssignment.assignment_id}</Descriptions.Item>
            <Descriptions.Item label="更新序号">{selectedAssignment.row_version}</Descriptions.Item>
            <Descriptions.Item label="写入服务">{selectedAssignment.creator_system}</Descriptions.Item>
            <Descriptions.Item label="数据标记">{selectedAssignment.source_mode}</Descriptions.Item>
          </Descriptions>,
        }]} />
      </Space> : null}
    </Drawer>

    <Drawer width={760} title={selectedOutput ? `输出 · ${selectedOutput.output_id}` : '输出'} open={Boolean(selectedOutput)} onClose={() => setSelectedOutput(undefined)}>
      {selectedOutput ? <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <Descriptions bordered size="small" column={1}>
          <Descriptions.Item label="类型">{outputDisplayTypeLabels[selectedOutput.display_type] ?? selectedOutput.display_type}</Descriptions.Item>
          <Descriptions.Item label="对象">{selectedOutput.recipient_name || selectedOutput.recipient_id || '—'}</Descriptions.Item>
          <Descriptions.Item label="内容">{selectedOutput.body || selectedOutput.content || '—'}</Descriptions.Item>
          <Descriptions.Item label="状态">{outputStatusBadge(selectedOutput.status)}</Descriptions.Item>
          <Descriptions.Item label="错误">{selectedOutput.last_error ? JSON.stringify(selectedOutput.last_error) : '—'}</Descriptions.Item>
          <Descriptions.Item label="创建时间"><TimeText value={selectedOutput.created_at} /></Descriptions.Item>
        </Descriptions>
        <Collapse ghost items={[{
          key: 'debug',
          label: '调试信息',
          children: <Descriptions bordered size="small" column={1}>
            <Descriptions.Item label="输出记录 ID">{selectedOutput.output_id}</Descriptions.Item>
            <Descriptions.Item label="来源类型">{selectedOutput.source_type}</Descriptions.Item>
            <Descriptions.Item label="来源记录 ID">{selectedOutput.source_id}</Descriptions.Item>
          </Descriptions>,
        }]} />
      </Space> : null}
    </Drawer>

    <Drawer width={720} title={selectedIntervention?.title ?? '提醒与运营事项'} open={Boolean(selectedIntervention)} onClose={() => setSelectedIntervention(undefined)}>
      {selectedIntervention ? <Descriptions bordered size="small" column={1}>
        <Descriptions.Item label="类型">{interventionOutputTypeLabel(selectedIntervention.output_type)}</Descriptions.Item>
        <Descriptions.Item label="教师">{selectedIntervention.teacher_name || selectedIntervention.teacher_id}</Descriptions.Item>
        <Descriptions.Item label="风险类型">{operationDomainLabel(selectedIntervention.domain)}</Descriptions.Item>
        <Descriptions.Item label="触发范围">由 {selectedIntervention.signal_count} 次课程信号触发</Descriptions.Item>
        <Descriptions.Item label="触发原因">{selectedIntervention.why}</Descriptions.Item>
        <Descriptions.Item label="关键证据">{selectedIntervention.evidence_summary}</Descriptions.Item>
        <Descriptions.Item label="当前状态">{interventionStatusBadge(selectedIntervention.status)}</Descriptions.Item>
        <Descriptions.Item label="触发时间"><TimeText value={selectedIntervention.triggered_at} /></Descriptions.Item>
      </Descriptions> : null}
    </Drawer>
  </div>
}
