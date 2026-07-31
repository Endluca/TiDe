import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Badge,
  Button,
  Card,
  Col,
  Descriptions,
  Drawer,
  Empty,
  Flex,
  Input,
  Pagination,
  Result,
  Row,
  Segmented,
  Space,
  Statistic,
  Table,
  Tag,
  Typography,
} from 'antd'
import type { TableColumnsType } from 'antd'
import {
  CalendarOutlined,
  EyeOutlined,
  ReloadOutlined,
  SearchOutlined,
  TeamOutlined,
  WarningOutlined,
} from '@ant-design/icons'
import { api, isRequestCancelled } from '../api'
import { displayError } from '../domain'
import { lessonSignalLabel, lessonStatusLabel, operationDomainLabel } from '../operations'
import type { AppNavigationContext, LessonEvidence, LessonEvidencePage } from '../types'
import { PageHeader } from '../components/Common'
import { useI18n } from '../i18n'
import { useLatestRequest } from '../useLatestRequest'

const { Text } = Typography
const LESSON_PAGE_SIZE = 20

const emptyPage: LessonEvidencePage = { items: [], total: 0, page: 1, page_size: LESSON_PAGE_SIZE }

function complaintLevelTag(level?: string | null) {
  if (!level) return <Text type="secondary">—</Text>
  const color = ['P0', 'P1', 'L0', 'L1'].includes(level) ? 'red' : ['P2', 'L2'].includes(level) ? 'orange' : 'default'
  return <Tag color={color}>{level}</Tag>
}

export default function LessonEvidenceCenter({
  active = true,
  initialContext,
  onNavigate,
}: {
  active?: boolean
  initialContext?: AppNavigationContext
  onNavigate: (page: string, context?: AppNavigationContext) => void
}) {
  const { locale, t } = useI18n()
  const lessonRequest = useLatestRequest()
  const [teacherInput, setTeacherInput] = useState(initialContext?.teacherId ?? '')
  const [teacherId, setTeacherId] = useState(initialContext?.teacherId ?? '')
  const [riskOnly, setRiskOnly] = useState(true)
  const [page, setPage] = useState(1)
  const [lessonPage, setLessonPage] = useState<LessonEvidencePage>(emptyPage)
  const [loading, setLoading] = useState(false)
  const [hasLoaded, setHasLoaded] = useState(false)
  const [loadError, setLoadError] = useState('')
  const [selected, setSelected] = useState<LessonEvidence>()

  useEffect(() => {
    const nextTeacher = initialContext?.teacherId ?? ''
    setTeacherInput(nextTeacher)
    setTeacherId(nextTeacher)
    setPage(1)
  }, [initialContext?.teacherId, initialContext?.lessonId])

  const load = useCallback(async (overrides: {
    page?: number
    teacherId?: string
    riskOnly?: boolean
    lessonId?: string
  } = {}) => {
    const requestedPage = overrides.page ?? page
    const requestedTeacherId = overrides.teacherId ?? teacherId
    const requestedRiskOnly = overrides.riskOnly ?? riskOnly
    const requestedLessonId = overrides.lessonId ?? initialContext?.lessonId
    setLoading(true)
    setLoadError('')
    let cancelled = false
    try {
      setLessonPage(await lessonRequest.run((options) =>
        api.lessons({
          page: requestedPage,
          page_size: LESSON_PAGE_SIZE,
          teacher_id: requestedTeacherId || undefined,
          lesson_id: requestedLessonId,
          risk_only: requestedRiskOnly,
        }, options),
      ))
      setHasLoaded(true)
    } catch (error) {
      if (isRequestCancelled(error)) {
        cancelled = true
        return
      }
      setLessonPage({ ...emptyPage, page: requestedPage })
      setLoadError(displayError(error, locale))
    } finally {
      if (!cancelled) setLoading(false)
    }
  }, [initialContext?.lessonId, lessonRequest, locale, page, riskOnly, teacherId])
  const summary = useMemo(() => ({
    riskLessons: lessonPage.items.filter((item) => item.risk_domains.length > 0 || item.signals.length > 0).length,
    teachers: new Set(lessonPage.items.map((item) => item.teacher_id)).size,
    severeComplaints: lessonPage.items.filter((item) => ['P0', 'P1', 'L0', 'L1'].includes(item.complaint_level ?? '')).length,
  }), [lessonPage.items])

  if (!active) return null

  const columns: TableColumnsType<LessonEvidence> = [
    {
      title: t('课程时间', 'Lesson time'), key: 'time', width: 165,
      render: (_, item) => <Space direction="vertical" size={2}><Text strong>{item.lesson_date || t('日期待确认', 'Date pending')}</Text><Text type="secondary">{item.lesson_time || t('时间待确认', 'Time pending')}</Text></Space>,
    },
    {
      title: t('教师', 'Teacher'), key: 'teacher', width: 180,
      render: (_, item) => <Button type="link" className="table-link" onClick={(event) => { event.stopPropagation(); onNavigate('teachers', { teacherId: item.teacher_id }) }}>{item.teacher_name || item.teacher_id}</Button>,
    },
    { title: t('课程状态', 'Lesson status'), dataIndex: 'lesson_status', width: 120, render: (value: string) => <Badge status={['END', 'COMPLETED', 'FINISHED'].includes(value.toUpperCase()) ? 'success' : 'default'} text={lessonStatusLabel(value, locale)} /> },
    {
      title: t('风险类型', 'Risk type'), dataIndex: 'risk_domains', width: 220,
      render: (values: string[]) => values.length ? <Flex gap={5} wrap="wrap">{values.map((value) => <Tag key={value}>{operationDomainLabel(value, undefined, locale)}</Tag>)}</Flex> : <Text type="secondary">{t('无已识别风险', 'No identified risk')}</Text>,
    },
    {
      title: t('关键证据', 'Key evidence'), dataIndex: 'signals',
      render: (signals: LessonEvidence['signals']) => signals.length ? <Flex gap={5} wrap="wrap">{signals.slice(0, 4).map((signal, index) => <Tag key={`${lessonSignalLabel(signal, locale)}-${index}`} color="orange">{lessonSignalLabel(signal, locale)}</Tag>)}</Flex> : <Text type="secondary">{t('暂无异常证据', 'No exception evidence')}</Text>,
    },
    { title: t('投诉级别', 'Complaint level'), dataIndex: 'complaint_level', width: 105, render: (value?: string | null) => complaintLevelTag(value) },
    { title: t('操作', 'Action'), key: 'action', width: 92, render: (_, item) => <Button type="link" icon={<EyeOutlined />} onClick={(event) => { event.stopPropagation(); setSelected(item) }}>{t('详情', 'Details')}</Button> },
  ]

  function applyTeacherFilter() {
    const nextTeacherId = teacherInput.trim()
    setPage(1)
    setTeacherId(nextTeacherId)
    load({ page: 1, teacherId: nextTeacherId }).catch(() => undefined)
  }

  return (
    <div className="page-shell">
      <PageHeader
        eyebrow={t('教师与证据', 'Teachers & Evidence')}
        title={t('课程证据', 'Lesson Evidence')}
        description={t('按课程回看可靠性、用户反馈与课堂质量信号，确认每个处置事项为什么被触发。', 'Review reliability, user feedback, and classroom-quality signals by lesson to confirm why each action was triggered.')}
        actions={<Button icon={<ReloadOutlined />} loading={loading} onClick={() => load()}>{t('更新课程', 'Refresh lessons')}</Button>}
      />

      {initialContext?.lessonId ? <div className="drilldown-note"><SearchOutlined /><Text>{t('正在查看处置事项关联课程：', 'Viewing the lesson linked to this action: ')}<Text strong>{initialContext.lessonId}</Text></Text></div> : null}

      <Row gutter={[12, 12]}>
        <Col xs={12} lg={6}><Card><Statistic title={t('符合筛选的课程', 'Matching lessons')} value={!hasLoaded || loadError ? '—' : lessonPage.total} prefix={<CalendarOutlined />} /></Card></Col>
        <Col xs={12} lg={6}><Card><Statistic title={t('本页风险课程', 'Risk lessons on page')} value={!hasLoaded || loadError ? '—' : summary.riskLessons} prefix={<WarningOutlined />} /></Card></Col>
        <Col xs={12} lg={6}><Card><Statistic title={t('本页涉及教师', 'Teachers on page')} value={!hasLoaded || loadError ? '—' : summary.teachers} prefix={<TeamOutlined />} /></Card></Col>
        <Col xs={12} lg={6}><Card><Statistic title={t('本页严重投诉', 'Severe complaints on page')} value={!hasLoaded || loadError ? '—' : summary.severeComplaints} valueStyle={{ color: '#b24e40' }} /></Card></Col>
      </Row>

      <Card className="filter-card">
        <Flex gap={10} wrap="wrap" align="center">
          <Input allowClear prefix={<SearchOutlined />} placeholder={t('输入教师 ID', 'Enter teacher ID')} value={teacherInput} onChange={(event) => setTeacherInput(event.target.value)} onPressEnter={applyTeacherFilter} style={{ width: 230 }} />
          <Button type="primary" onClick={applyTeacherFilter}>{t('查询教师课程', 'Find teacher lessons')}</Button>
          <Segmented
            value={riskOnly ? 'RISK' : 'ALL'}
            onChange={(value) => {
              const nextRiskOnly = value === 'RISK'
              setRiskOnly(nextRiskOnly)
              setPage(1)
              load({ page: 1, riskOnly: nextRiskOnly }).catch(() => undefined)
            }}
            options={[{ value: 'RISK', label: t('只看风险课程', 'Risk lessons only') }, { value: 'ALL', label: t('全部课程', 'All lessons') }]}
          />
          {teacherId ? <Button onClick={() => {
            setTeacherInput('')
            setTeacherId('')
            setPage(1)
            load({ page: 1, teacherId: '' }).catch(() => undefined)
          }}>{t('清除教师', 'Clear teacher')}</Button> : null}
        </Flex>
      </Card>

      {loadError ? (
        <Result status="warning" title={t('课程证据暂时无法加载', 'Lesson evidence is temporarily unavailable')} subTitle={t('最新课程记录未能更新，请稍后重试。', 'The latest lesson records could not be refreshed. Please try again later.')} extra={<Button onClick={() => load()}>{t('重新加载', 'Try again')}</Button>} />
      ) : (
        <Card styles={{ body: { padding: 0 } }}>
          <Table
            rowKey="lesson_id"
            loading={loading}
            dataSource={lessonPage.items}
            columns={columns}
            pagination={false}
            scroll={{ x: 1120 }}
            rowClassName={(item) => item.lesson_id === initialContext?.lessonId ? 'lesson-focus-row' : ''}
            onRow={(item) => ({ onClick: () => setSelected(item), style: { cursor: 'pointer' } })}
            locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={!hasLoaded ? t('尚未读取课程，点击“更新课程”', 'Lessons have not been loaded. Select “Refresh lessons”.') : riskOnly ? t('当前筛选下没有风险课程', 'No risk lessons match the filters') : t('当前筛选下没有课程记录', 'No lessons match the filters')} /> }}
          />
        </Card>
      )}

      {lessonPage.total > lessonPage.page_size ? (
        <Flex justify="center"><Pagination
          current={lessonPage.page}
          pageSize={lessonPage.page_size}
          total={lessonPage.total}
          showSizeChanger={false}
          onChange={(nextPage) => {
            setPage(nextPage)
            load({ page: nextPage }).catch(() => undefined)
          }}
          showQuickJumper
        /></Flex>
      ) : null}

      <Drawer width={700} title={selected ? `${t('课程证据', 'Lesson evidence')} · ${selected.lesson_id}` : t('课程证据', 'Lesson evidence')} open={Boolean(selected)} onClose={() => setSelected(undefined)}>
        {selected ? <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <Descriptions bordered size="small" column={1}>
            <Descriptions.Item label={t('教师', 'Teacher')}><Button type="link" className="table-link" onClick={() => onNavigate('teachers', { teacherId: selected.teacher_id })}>{selected.teacher_name || selected.teacher_id}</Button></Descriptions.Item>
            <Descriptions.Item label={t('课程日期', 'Lesson date')}>{selected.lesson_date || t('日期待确认', 'Date pending')} {selected.lesson_time || ''}</Descriptions.Item>
            <Descriptions.Item label={t('课程状态', 'Lesson status')}>{lessonStatusLabel(selected.lesson_status, locale)}</Descriptions.Item>
            <Descriptions.Item label={t('风险类型', 'Risk type')}><Flex gap={5} wrap="wrap">{selected.risk_domains.map((value) => <Tag key={value}>{operationDomainLabel(value, undefined, locale)}</Tag>)}</Flex></Descriptions.Item>
            <Descriptions.Item label={t('投诉级别', 'Complaint level')}>{complaintLevelTag(selected.complaint_level)}</Descriptions.Item>
          </Descriptions>
          <Card size="small" title={t('触发证据', 'Trigger evidence')}>
            {selected.signals.length ? <Flex gap={6} wrap="wrap">{selected.signals.map((signal, index) => <Tag color="orange" key={`${lessonSignalLabel(signal, locale)}-${index}`}>{lessonSignalLabel(signal, locale)}</Tag>)}</Flex> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t('暂无异常证据', 'No exception evidence')} />}
          </Card>
        </Space> : null}
      </Drawer>
    </div>
  )
}
