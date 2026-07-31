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
import { api, isRequestCancelled } from '../api'
import { displayError } from '../domain'
import { interventionStatusLabel, operationDomainLabel, sortInterventions } from '../operations'
import type { AppNavigationContext, OperationsIntervention } from '../types'
import { PageHeader, PriorityTag, TimeText } from '../components/Common'
import { useI18n } from '../i18n'
import type { AppLocale } from '../i18n'
import { useLatestRequest } from '../useLatestRequest'

const { Paragraph, Text } = Typography

const domainOptions = [
  { value: 'RELIABILITY', label: '可靠性' },
  { value: 'USER_FEEDBACK', label: '用户反馈' },
  { value: 'CLASS_QUALITY', label: '课堂质量' },
]

export const currentOpsCaseStatusOptions = ['OPEN', 'ACTION_PENDING', 'IN_REVIEW']
  .map((value) => ({ value, label: interventionStatusLabel(value) }))

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

export default function InterventionCenter({
  active = true,
  initialContext,
  onNavigate,
  canDecideCase,
}: {
  active?: boolean
  initialContext?: AppNavigationContext
  onNavigate: (page: string, context?: AppNavigationContext) => void
  canDecideCase: boolean
}) {
  const { locale, t } = useI18n()
  const { message } = AntdApp.useApp()
  const interventionRequest = useLatestRequest()
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
    let cancelled = false
    try {
      const response = await interventionRequest.run((options) =>
        api.operationsInterventions({
          page: requestedPage,
          page_size: pageSize,
          type: 'OPS_CASE',
          open_only: true,
          domain: requestedDomain || undefined,
          status: requestedStatus || undefined,
          teacher_id: requestedTeacherId.trim() || undefined,
        }, options),
      )
      setItems(sortInterventions(response.items))
      setTotal(response.total)
    } catch (error) {
      if (isRequestCancelled(error)) {
        cancelled = true
        return
      }
      setItems([])
      setTotal(0)
      setLoadError(displayError(error, locale))
    } finally {
      if (!cancelled) {
        setHasLoaded(true)
        setLoading(false)
      }
    }
  }, [domain, interventionRequest, locale, page, status, teacherId])
  const localizedDomainOptions = domainOptions.map((item) => ({
    ...item,
    label: operationDomainLabel(item.value, undefined, locale),
  }))
  const localizedStatusOptions = ['OPEN', 'ACTION_PENDING', 'IN_REVIEW']
    .map((value) => ({ value, label: interventionStatusLabel(value, locale) }))
  const counts = useMemo(() => ({
    urgent: items.filter((item) => item.priority === 'P0' || item.priority === 'P1').length,
    teachers: new Set(items.map((item) => item.teacher_id)).size,
    severe: items.filter((item) => item.title.includes('严重投诉') || item.priority === 'P0').length,
  }), [items])

  if (!active) return null

  const columns: TableColumnsType<OperationsIntervention> = [
    { title: t('紧急度', 'Priority'), dataIndex: 'priority', width: 110, render: (value: string) => <PriorityTag priority={value} /> },
    {
      title: t('教师', 'Teacher'), key: 'teacher', width: 180,
      render: (_, item) => <Button type="link" className="table-link" onClick={(event) => { event.stopPropagation(); onNavigate('teachers', { teacherId: item.teacher_id }) }}>{item.teacher_name || item.teacher_id}</Button>,
    },
    { title: t('风险类型', 'Risk type'), dataIndex: 'domain', width: 130, render: (value: string) => <Tag>{operationDomainLabel(value, undefined, locale)}</Tag> },
    {
      title: t('为什么需要介入', 'Why action is needed'), key: 'reason', width: 420,
      render: (_, item) => <Space direction="vertical" size={3}><Text strong>{item.title}</Text><Text type="secondary">{t(`由 ${item.signal_count} 次课程信号触发`, `Triggered by ${item.signal_count} lesson signals`)}</Text><Paragraph ellipsis={{ rows: 2, tooltip: item.why }} style={{ margin: 0 }}>{item.why}</Paragraph><Text type="secondary">{item.evidence_summary}</Text></Space>,
    },
    { title: t('当前进展', 'Current status'), dataIndex: 'status', width: 135, render: (value: string) => interventionBadge(value, locale) },
    { title: t('触发时间', 'Triggered at'), dataIndex: 'triggered_at', width: 145, render: (value: string) => <TimeText value={value} /> },
    {
      title: t('下一步', 'Next step'), key: 'action', width: 160,
      render: (_, item) => <Space direction="vertical" size={2}><Text>{item.action_label}</Text>{item.source_lesson_id ? <Button type="link" className="table-link" onClick={(event) => { event.stopPropagation(); onNavigate('lessons', { teacherId: item.teacher_id, lessonId: item.source_lesson_id ?? undefined }) }}>{t('查看课程证据', 'View lesson evidence')}</Button> : null}</Space>,
    },
  ]

  async function decideCase(decision: 'START_PROCESSING' | 'RESOLVE') {
    if (!selected || selected.output_type !== 'OPS_CASE') return
    if (decision === 'RESOLVE' && !caseNote.trim()) {
      message.warning(t('完成处理前请填写处理结论', 'Add a resolution note before completing this item'))
      return
    }
    setDecisionLoading(decision)
    try {
      await api.decideOperationsCase(selected.output_id, decision, caseNote.trim())
      message.success(decision === 'START_PROCESSING' ? t('已开始处理该事项', 'Processing started') : t('该事项已完成处理', 'Item resolved'))
      setSelected(undefined)
      setCaseNote('')
      await load()
    } catch {
      message.error(t('本次操作未完成，请刷新后重试', 'The action could not be completed. Refresh and try again.'))
    } finally {
      setDecisionLoading('')
    }
  }

  return (
    <div className="page-shell">
      <PageHeader
        eyebrow={t('经营与处置', 'Operations')}
        title={t('当前运营处置待办', 'Current Operations Actions')}
        description={t('仅展示尚未结束的运营处置事项；按严重程度查看触发原因、证据和下一步动作。', 'Shows only open operations actions, ordered by severity with trigger reason, evidence, and next step.')}
        actions={<Button icon={<ReloadOutlined />} loading={loading} onClick={() => load()}>{t('更新待办', 'Refresh actions')}</Button>}
      />

      <Row gutter={[12, 12]}>
        <Col xs={12} lg={6}><Card><Statistic title={t('当前待办', 'Pending actions')} value={loadError || !hasLoaded ? '—' : total} prefix={<WarningOutlined />} /></Card></Col>
        <Col xs={12} lg={6}><Card><Statistic title={t('本页高优先级', 'High priority on page')} value={loadError || !hasLoaded ? '—' : counts.urgent} valueStyle={{ color: '#b24e40' }} /></Card></Col>
        <Col xs={12} lg={6}><Card><Statistic title={t('本页涉及教师', 'Teachers on page')} value={loadError || !hasLoaded ? '—' : counts.teachers} prefix={<TeamOutlined />} /></Card></Col>
        <Col xs={12} lg={6}><Card><Statistic title={t('本页严重投诉', 'Severe complaints on page')} value={loadError || !hasLoaded ? '—' : counts.severe} valueStyle={{ color: '#b24e40' }} /></Card></Col>
      </Row>

      <Card className="filter-card">
        <Flex gap={10} wrap="wrap">
          <Select allowClear placeholder={t('全部风险类型', 'All risk types')} value={domain || undefined} onChange={(value) => { setDomain(value || ''); setPage(1) }} options={localizedDomainOptions} style={{ width: 170 }} />
          <Select allowClear placeholder={t('全部进展', 'All statuses')} value={status || undefined} onChange={(value) => { setStatus(value || ''); setPage(1) }} options={localizedStatusOptions} style={{ width: 160 }} />
          <Input allowClear prefix={<SearchOutlined />} placeholder={t('输入教师 ID', 'Enter teacher ID')} value={teacherInput} onChange={(event) => setTeacherInput(event.target.value)} onPressEnter={() => setTeacherId(teacherInput.trim())} style={{ width: 210 }} />
          <Button type="primary" onClick={() => { const nextTeacher = teacherInput.trim(); setTeacherId(nextTeacher); setPage(1); load({ page: 1, teacherId: nextTeacher }).catch(() => undefined) }}>{t('查询教师', 'Find teacher')}</Button>
          <Button onClick={() => { setDomain(''); setStatus(''); setTeacherInput(''); setTeacherId(''); setPage(1); load({ page: 1, domain: '', status: '', teacherId: '' }).catch(() => undefined) }}>{t('清除筛选', 'Clear filters')}</Button>
          <Text type="secondary" style={{ marginLeft: 'auto' }}>{hasLoaded ? t(`本页 ${items.length} 项 · 共 ${total} 项`, `${items.length} on page · ${total} total`) : t('等待主动更新', 'Awaiting refresh')}</Text>
        </Flex>
      </Card>

      {loadError ? (
        <Result status="warning" title={t('待办暂时无法加载', 'Actions are temporarily unavailable')} subTitle={t('最新处置事项未能更新，请稍后重试。', 'The latest actions could not be refreshed. Please try again later.')} extra={<Button onClick={() => load()}>{t('重新加载', 'Try again')}</Button>} />
      ) : (
        <Card styles={{ body: { padding: 0 } }}>
          <Table
            rowKey="output_id"
            loading={loading}
            dataSource={items}
            columns={columns}
            pagination={{ current: page, pageSize, total, showSizeChanger: false, hideOnSinglePage: true, showTotal: (value) => t(`共 ${value} 项`, `${value} total`) }}
            onChange={(pagination) => { const nextPage = pagination.current ?? 1; setPage(nextPage); load({ page: nextPage }).catch(() => undefined) }}
            scroll={{ x: 1280 }}
            onRow={(item) => ({ onClick: () => { setSelected(item); setCaseNote('') }, style: { cursor: 'pointer' } })}
            locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={hasLoaded ? t('当前筛选下没有待办事项', 'No actions match the filters') : t('尚未读取待办，点击“更新待办”', 'Actions have not been loaded. Select “Refresh actions”.')} /> }}
          />
        </Card>
      )}

      <Drawer width={720} title={selected?.title ?? t('处置详情', 'Action details')} open={Boolean(selected)} onClose={() => { setSelected(undefined); setCaseNote('') }}>
        {selected ? <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <Flex gap={8} wrap="wrap"><PriorityTag priority={selected.priority} />{interventionBadge(selected.status, locale)}<Tag>{operationDomainLabel(selected.domain, undefined, locale)}</Tag></Flex>
          <Descriptions bordered size="small" column={1}>
            <Descriptions.Item label={t('教师', 'Teacher')}><Button type="link" className="table-link" onClick={() => onNavigate('teachers', { teacherId: selected.teacher_id })}>{selected.teacher_name || selected.teacher_id}</Button></Descriptions.Item>
            <Descriptions.Item label={t('触发范围', 'Trigger scope')}>{t(`由 ${selected.signal_count} 次课程信号触发`, `Triggered by ${selected.signal_count} lesson signals`)}</Descriptions.Item>
            <Descriptions.Item label={t('触发原因', 'Trigger reason')}>{selected.why}</Descriptions.Item>
            <Descriptions.Item label={t('关键证据', 'Key evidence')}>{selected.evidence_summary}</Descriptions.Item>
            <Descriptions.Item label={t('建议动作', 'Recommended action')}>{selected.action_label}</Descriptions.Item>
            <Descriptions.Item label={t('触发时间', 'Triggered at')}><TimeText value={selected.triggered_at} /></Descriptions.Item>
          </Descriptions>
          {selected.source_lesson_id ? <Button type="primary" icon={<FileSearchOutlined />} onClick={() => onNavigate('lessons', { teacherId: selected.teacher_id, lessonId: selected.source_lesson_id ?? undefined })}>{t('查看对应课程证据', 'View linked lesson evidence')}</Button> : null}
          {selected.output_type === 'OPS_CASE' && canDecideCase ? (
            <Card size="small" title={t('运营处理', 'Operations action')} className="case-decision-card">
              <Space direction="vertical" size={12} style={{ width: '100%' }}>
                <div><Text strong>{t('处理记录', 'Action notes')}</Text><Text type="secondary" style={{ display: 'block', marginTop: 3 }}>{t('开始处理时可不填；完成处理时必须填写结论和后续动作。', 'Notes are optional when starting, but a conclusion and follow-up action are required to resolve the item.')}</Text></div>
                <Input.TextArea value={caseNote} onChange={(event) => setCaseNote(event.target.value)} maxLength={2000} showCount autoSize={{ minRows: 3, maxRows: 7 }} placeholder={t('填写核查结果、责任判断或后续动作', 'Enter findings, responsibility, or follow-up actions')} />
                <Flex justify="flex-end" gap={8} wrap="wrap">
                  <Button disabled={['IN_REVIEW', 'RESOLVED', 'CLOSED', 'CANCELLED'].includes(selected.status)} loading={decisionLoading === 'START_PROCESSING'} onClick={() => decideCase('START_PROCESSING')}>{t('开始处理', 'Start processing')}</Button>
                  <Button type="primary" danger disabled={['RESOLVED', 'CLOSED', 'CANCELLED'].includes(selected.status)} loading={decisionLoading === 'RESOLVE'} onClick={() => decideCase('RESOLVE')}>{t('完成处理', 'Resolve')}</Button>
                </Flex>
              </Space>
            </Card>
          ) : selected.output_type === 'OPS_CASE' ? <Alert type="info" showIcon message={t('当前账号可查看该事项，处理操作需要运营处置权限。', 'This account can view the item, but operations permission is required to update it.')} /> : null}
        </Space> : null}
      </Drawer>
    </div>
  )
}
