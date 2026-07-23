import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Badge,
  Button,
  Alert,
  Card,
  Col,
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
  App as AntdApp,
} from 'antd'
import type { TableColumnsType } from 'antd'
import {
  EyeOutlined,
  FileSearchOutlined,
  ReloadOutlined,
  SearchOutlined,
  TeamOutlined,
  WarningOutlined,
} from '@ant-design/icons'
import { api } from '../api'
import { displayError } from '../domain'
import { interventionStatusLabel, operationDomainLabel, sortInterventions } from '../operations'
import type { AppNavigationContext, OperationsIntervention } from '../types'
import { PageHeader, PriorityTag, TimeText } from '../components/Common'

const { Paragraph, Text } = Typography

const domainOptions = [
  { value: 'RELIABILITY', label: '可靠性' },
  { value: 'USER_FEEDBACK', label: '用户反馈' },
  { value: 'CLASS_QUALITY', label: '课堂质量' },
]

export const currentOpsCaseStatusOptions = ['OPEN', 'ACTION_PENDING', 'IN_REVIEW']
  .map((value) => ({ value, label: interventionStatusLabel(value) }))

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

export default function InterventionCenter({
  initialContext,
  onNavigate,
  canDecideCase,
}: {
  initialContext?: AppNavigationContext
  onNavigate: (page: string, context?: AppNavigationContext) => void
  canDecideCase: boolean
}) {
  const { message } = AntdApp.useApp()
  const [domain, setDomain] = useState(initialContext?.domain ?? '')
  const [status, setStatus] = useState('')
  const [teacherInput, setTeacherInput] = useState(initialContext?.teacherId ?? '')
  const [teacherId, setTeacherId] = useState(initialContext?.teacherId ?? '')
  const [items, setItems] = useState<OperationsIntervention[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const pageSize = 50
  const [loading, setLoading] = useState(false)
  const [hasLoaded, setHasLoaded] = useState(false)
  const [loadError, setLoadError] = useState('')
  const [selected, setSelected] = useState<OperationsIntervention>()
  const [caseNote, setCaseNote] = useState('')
  const [decisionLoading, setDecisionLoading] = useState<'START_PROCESSING' | 'RESOLVE' | ''>('')

  useEffect(() => {
    setDomain(initialContext?.domain ?? '')
    const nextTeacher = initialContext?.teacherId ?? ''
    setTeacherInput(nextTeacher)
    setTeacherId(nextTeacher)
    setPage(1)
  }, [initialContext?.domain, initialContext?.teacherId])

  const load = useCallback(async (overrides: {
    page?: number
    domain?: string
    status?: string
    teacherId?: string
  } = {}) => {
    const requestedPage = overrides.page ?? page
    const requestedDomain = overrides.domain ?? domain
    const requestedStatus = overrides.status ?? status
    const requestedTeacherId = overrides.teacherId ?? teacherId
    setLoading(true)
    setLoadError('')
    try {
      const response = await api.operationsInterventions({
        page: requestedPage,
        page_size: pageSize,
        type: 'OPS_CASE',
        open_only: true,
        domain: requestedDomain || undefined,
        status: requestedStatus || undefined,
        teacher_id: requestedTeacherId.trim() || undefined,
      })
      setItems(sortInterventions(response.items))
      setTotal(response.total)
    } catch (error) {
      setItems([])
      setTotal(0)
      setLoadError(displayError(error))
    } finally {
      setHasLoaded(true)
      setLoading(false)
    }
  }, [domain, page, status, teacherId])

  const counts = useMemo(() => ({
    urgent: items.filter((item) => item.priority === 'P0' || item.priority === 'P1').length,
    teachers: new Set(items.map((item) => item.teacher_id)).size,
    severe: items.filter((item) => item.title.includes('严重投诉') || item.priority === 'P0').length,
  }), [items])

  const columns: TableColumnsType<OperationsIntervention> = [
    { title: '紧急度', dataIndex: 'priority', width: 110, render: (value: string) => <PriorityTag priority={value} /> },
    {
      title: '教师', key: 'teacher', width: 180,
      render: (_, item) => <Button type="link" className="table-link" onClick={(event) => { event.stopPropagation(); onNavigate('teachers', { teacherId: item.teacher_id }) }}>{item.teacher_name || item.teacher_id}</Button>,
    },
    { title: '风险类型', dataIndex: 'domain', width: 130, render: (value: string) => <Tag>{operationDomainLabel(value)}</Tag> },
    {
      title: '为什么需要介入', key: 'reason', width: 420,
      render: (_, item) => <Space direction="vertical" size={3}><Text strong>{item.title}</Text><Text type="secondary">由 {item.signal_count} 次课程信号触发</Text><Paragraph ellipsis={{ rows: 2, tooltip: item.why }} style={{ margin: 0 }}>{item.why}</Paragraph><Text type="secondary">{item.evidence_summary}</Text></Space>,
    },
    { title: '当前进展', dataIndex: 'status', width: 135, render: (value: string) => interventionBadge(value) },
    { title: '触发时间', dataIndex: 'triggered_at', width: 145, render: (value: string) => <TimeText value={value} /> },
    {
      title: '下一步', key: 'action', width: 160,
      render: (_, item) => <Space direction="vertical" size={2}><Text>{item.action_label}</Text>{item.source_lesson_id ? <Button type="link" className="table-link" onClick={(event) => { event.stopPropagation(); onNavigate('lessons', { teacherId: item.teacher_id, lessonId: item.source_lesson_id ?? undefined }) }}>查看课程证据</Button> : null}</Space>,
    },
  ]

  async function decideCase(decision: 'START_PROCESSING' | 'RESOLVE') {
    if (!selected || selected.output_type !== 'OPS_CASE') return
    if (decision === 'RESOLVE' && !caseNote.trim()) {
      message.warning('完成处理前请填写处理结论')
      return
    }
    setDecisionLoading(decision)
    try {
      await api.decideOperationsCase(selected.output_id, decision, caseNote.trim())
      message.success(decision === 'START_PROCESSING' ? '已开始处理该事项' : '该事项已完成处理')
      setSelected(undefined)
      setCaseNote('')
      await load()
    } catch {
      message.error('本次操作未完成，请刷新后重试')
    } finally {
      setDecisionLoading('')
    }
  }

  return (
    <div className="page-shell">
      <PageHeader
        eyebrow="经营与处置"
        title="当前运营处置待办"
        description="仅展示尚未结束的运营处置事项；按严重程度查看触发原因、证据和下一步动作。"
        actions={<Button icon={<ReloadOutlined />} loading={loading} onClick={() => load()}>更新待办</Button>}
      />

      <Row gutter={[12, 12]}>
        <Col xs={12} lg={6}><Card><Statistic title="当前待办" value={loadError || !hasLoaded ? '—' : total} prefix={<WarningOutlined />} /></Card></Col>
        <Col xs={12} lg={6}><Card><Statistic title="本页高优先级" value={loadError || !hasLoaded ? '—' : counts.urgent} valueStyle={{ color: '#b24e40' }} /></Card></Col>
        <Col xs={12} lg={6}><Card><Statistic title="本页涉及教师" value={loadError || !hasLoaded ? '—' : counts.teachers} prefix={<TeamOutlined />} /></Card></Col>
        <Col xs={12} lg={6}><Card><Statistic title="本页严重投诉" value={loadError || !hasLoaded ? '—' : counts.severe} valueStyle={{ color: '#b24e40' }} /></Card></Col>
      </Row>

      <Card className="filter-card">
        <Flex gap={10} wrap="wrap">
          <Select allowClear placeholder="全部风险类型" value={domain || undefined} onChange={(value) => { setDomain(value || ''); setPage(1) }} options={domainOptions} style={{ width: 170 }} />
          <Select allowClear placeholder="全部进展" value={status || undefined} onChange={(value) => { setStatus(value || ''); setPage(1) }} options={currentOpsCaseStatusOptions} style={{ width: 160 }} />
          <Input allowClear prefix={<SearchOutlined />} placeholder="输入教师 ID" value={teacherInput} onChange={(event) => setTeacherInput(event.target.value)} onPressEnter={() => setTeacherId(teacherInput.trim())} style={{ width: 210 }} />
          <Button type="primary" onClick={() => { const nextTeacher = teacherInput.trim(); setTeacherId(nextTeacher); setPage(1); load({ page: 1, teacherId: nextTeacher }).catch(() => undefined) }}>查询教师</Button>
          <Button onClick={() => { setDomain(''); setStatus(''); setTeacherInput(''); setTeacherId(''); setPage(1); load({ page: 1, domain: '', status: '', teacherId: '' }).catch(() => undefined) }}>清除筛选</Button>
          <Text type="secondary" style={{ marginLeft: 'auto' }}>{hasLoaded ? `本页 ${items.length} 项 · 共 ${total} 项` : '等待主动更新'}</Text>
        </Flex>
      </Card>

      {loadError ? (
        <Result status="warning" title="待办暂时无法加载" subTitle="最新处置事项未能更新，请稍后重试。" extra={<Button onClick={() => load()}>重新加载</Button>} />
      ) : (
        <Card styles={{ body: { padding: 0 } }}>
          <Table
            rowKey="output_id"
            loading={loading}
            dataSource={items}
            columns={columns}
            pagination={{ current: page, pageSize, total, showSizeChanger: false, hideOnSinglePage: true, showTotal: (value) => `共 ${value} 项` }}
            onChange={(pagination) => { const nextPage = pagination.current ?? 1; setPage(nextPage); load({ page: nextPage }).catch(() => undefined) }}
            scroll={{ x: 1280 }}
            onRow={(item) => ({ onClick: () => { setSelected(item); setCaseNote('') }, style: { cursor: 'pointer' } })}
            locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={hasLoaded ? '当前筛选下没有待办事项' : '尚未读取待办，点击“更新待办”'} /> }}
          />
        </Card>
      )}

      <Drawer width={720} title={selected?.title ?? '处置详情'} open={Boolean(selected)} onClose={() => { setSelected(undefined); setCaseNote('') }}>
        {selected ? <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <Flex gap={8} wrap="wrap"><PriorityTag priority={selected.priority} />{interventionBadge(selected.status)}<Tag>{operationDomainLabel(selected.domain)}</Tag></Flex>
          <Descriptions bordered size="small" column={1}>
            <Descriptions.Item label="教师"><Button type="link" className="table-link" onClick={() => onNavigate('teachers', { teacherId: selected.teacher_id })}>{selected.teacher_name || selected.teacher_id}</Button></Descriptions.Item>
            <Descriptions.Item label="触发范围">由 {selected.signal_count} 次课程信号触发</Descriptions.Item>
            <Descriptions.Item label="触发原因">{selected.why}</Descriptions.Item>
            <Descriptions.Item label="关键证据">{selected.evidence_summary}</Descriptions.Item>
            <Descriptions.Item label="建议动作">{selected.action_label}</Descriptions.Item>
            <Descriptions.Item label="触发时间"><TimeText value={selected.triggered_at} /></Descriptions.Item>
          </Descriptions>
          {selected.source_lesson_id ? <Button type="primary" icon={<FileSearchOutlined />} onClick={() => onNavigate('lessons', { teacherId: selected.teacher_id, lessonId: selected.source_lesson_id ?? undefined })}>查看对应课程证据</Button> : null}
          {selected.output_type === 'OPS_CASE' && canDecideCase ? (
            <Card size="small" title="运营处理" className="case-decision-card">
              <Space direction="vertical" size={12} style={{ width: '100%' }}>
                <div><Text strong>处理记录</Text><Text type="secondary" style={{ display: 'block', marginTop: 3 }}>开始处理时可不填；完成处理时必须填写结论和后续动作。</Text></div>
                <Input.TextArea value={caseNote} onChange={(event) => setCaseNote(event.target.value)} maxLength={2000} showCount autoSize={{ minRows: 3, maxRows: 7 }} placeholder="填写核查结果、责任判断或后续动作" />
                <Flex justify="flex-end" gap={8} wrap="wrap">
                  <Button disabled={['IN_REVIEW', 'RESOLVED', 'CLOSED', 'CANCELLED'].includes(selected.status)} loading={decisionLoading === 'START_PROCESSING'} onClick={() => decideCase('START_PROCESSING')}>开始处理</Button>
                  <Button type="primary" danger disabled={['RESOLVED', 'CLOSED', 'CANCELLED'].includes(selected.status)} loading={decisionLoading === 'RESOLVE'} onClick={() => decideCase('RESOLVE')}>完成处理</Button>
                </Flex>
              </Space>
            </Card>
          ) : selected.output_type === 'OPS_CASE' ? <Alert type="info" showIcon message="当前账号可查看该事项，处理操作需要运营处置权限。" /> : null}
        </Space> : null}
      </Drawer>
    </div>
  )
}
