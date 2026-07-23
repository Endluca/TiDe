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
import { api } from '../api'
import { displayError } from '../domain'
import { lessonSignalLabel, lessonStatusLabel, operationDomainLabel } from '../operations'
import type { AppNavigationContext, LessonEvidence, LessonEvidencePage } from '../types'
import { PageHeader } from '../components/Common'

const { Text } = Typography
const LESSON_PAGE_SIZE = 20

const emptyPage: LessonEvidencePage = { items: [], total: 0, page: 1, page_size: LESSON_PAGE_SIZE }

function complaintLevelTag(level?: string | null) {
  if (!level) return <Text type="secondary">—</Text>
  const color = ['P0', 'P1', 'L0', 'L1'].includes(level) ? 'red' : ['P2', 'L2'].includes(level) ? 'orange' : 'default'
  return <Tag color={color}>{level}</Tag>
}

export default function LessonEvidenceCenter({
  initialContext,
  onNavigate,
}: {
  initialContext?: AppNavigationContext
  onNavigate: (page: string, context?: AppNavigationContext) => void
}) {
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
    try {
      setLessonPage(await api.lessons({
        page: requestedPage,
        page_size: LESSON_PAGE_SIZE,
        teacher_id: requestedTeacherId || undefined,
        lesson_id: requestedLessonId,
        risk_only: requestedRiskOnly,
      }))
      setHasLoaded(true)
    } catch (error) {
      setLessonPage({ ...emptyPage, page: requestedPage })
      setLoadError(displayError(error))
    } finally {
      setLoading(false)
    }
  }, [initialContext?.lessonId, page, riskOnly, teacherId])

  const summary = useMemo(() => ({
    riskLessons: lessonPage.items.filter((item) => item.risk_domains.length > 0 || item.signals.length > 0).length,
    teachers: new Set(lessonPage.items.map((item) => item.teacher_id)).size,
    severeComplaints: lessonPage.items.filter((item) => ['P0', 'P1', 'L0', 'L1'].includes(item.complaint_level ?? '')).length,
  }), [lessonPage.items])

  const columns: TableColumnsType<LessonEvidence> = [
    {
      title: '课程时间', key: 'time', width: 165,
      render: (_, item) => <Space direction="vertical" size={2}><Text strong>{item.lesson_date || '日期待确认'}</Text><Text type="secondary">{item.lesson_time || '时间待确认'}</Text></Space>,
    },
    {
      title: '教师', key: 'teacher', width: 180,
      render: (_, item) => <Button type="link" className="table-link" onClick={(event) => { event.stopPropagation(); onNavigate('teachers', { teacherId: item.teacher_id }) }}>{item.teacher_name || item.teacher_id}</Button>,
    },
    { title: '课程状态', dataIndex: 'lesson_status', width: 120, render: (value: string) => <Badge status={['END', 'COMPLETED', 'FINISHED'].includes(value.toUpperCase()) ? 'success' : 'default'} text={lessonStatusLabel(value)} /> },
    {
      title: '风险类型', dataIndex: 'risk_domains', width: 220,
      render: (values: string[]) => values.length ? <Flex gap={5} wrap="wrap">{values.map((value) => <Tag key={value}>{operationDomainLabel(value)}</Tag>)}</Flex> : <Text type="secondary">无已识别风险</Text>,
    },
    {
      title: '关键证据', dataIndex: 'signals',
      render: (signals: LessonEvidence['signals']) => signals.length ? <Flex gap={5} wrap="wrap">{signals.slice(0, 4).map((signal, index) => <Tag key={`${lessonSignalLabel(signal)}-${index}`} color="orange">{lessonSignalLabel(signal)}</Tag>)}</Flex> : <Text type="secondary">暂无异常证据</Text>,
    },
    { title: '投诉级别', dataIndex: 'complaint_level', width: 105, render: (value?: string | null) => complaintLevelTag(value) },
    { title: '操作', key: 'action', width: 92, render: (_, item) => <Button type="link" icon={<EyeOutlined />} onClick={(event) => { event.stopPropagation(); setSelected(item) }}>详情</Button> },
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
        eyebrow="教师与证据"
        title="课程证据"
        description="按课程回看可靠性、用户反馈与课堂质量信号，确认每个处置事项为什么被触发。"
        actions={<Button icon={<ReloadOutlined />} loading={loading} onClick={() => load()}>更新课程</Button>}
      />

      {initialContext?.lessonId ? <div className="drilldown-note"><SearchOutlined /><Text>正在查看处置事项关联课程：<Text strong>{initialContext.lessonId}</Text></Text></div> : null}

      <Row gutter={[12, 12]}>
        <Col xs={12} lg={6}><Card><Statistic title="符合筛选的课程" value={!hasLoaded || loadError ? '—' : lessonPage.total} prefix={<CalendarOutlined />} /></Card></Col>
        <Col xs={12} lg={6}><Card><Statistic title="本页风险课程" value={!hasLoaded || loadError ? '—' : summary.riskLessons} prefix={<WarningOutlined />} /></Card></Col>
        <Col xs={12} lg={6}><Card><Statistic title="本页涉及教师" value={!hasLoaded || loadError ? '—' : summary.teachers} prefix={<TeamOutlined />} /></Card></Col>
        <Col xs={12} lg={6}><Card><Statistic title="本页严重投诉" value={!hasLoaded || loadError ? '—' : summary.severeComplaints} valueStyle={{ color: '#b24e40' }} /></Card></Col>
      </Row>

      <Card className="filter-card">
        <Flex gap={10} wrap="wrap" align="center">
          <Input allowClear prefix={<SearchOutlined />} placeholder="输入教师 ID" value={teacherInput} onChange={(event) => setTeacherInput(event.target.value)} onPressEnter={applyTeacherFilter} style={{ width: 230 }} />
          <Button type="primary" onClick={applyTeacherFilter}>查询教师课程</Button>
          <Segmented
            value={riskOnly ? 'RISK' : 'ALL'}
            onChange={(value) => {
              const nextRiskOnly = value === 'RISK'
              setRiskOnly(nextRiskOnly)
              setPage(1)
              load({ page: 1, riskOnly: nextRiskOnly }).catch(() => undefined)
            }}
            options={[{ value: 'RISK', label: '只看风险课程' }, { value: 'ALL', label: '全部课程' }]}
          />
          {teacherId ? <Button onClick={() => {
            setTeacherInput('')
            setTeacherId('')
            setPage(1)
            load({ page: 1, teacherId: '' }).catch(() => undefined)
          }}>清除教师</Button> : null}
        </Flex>
      </Card>

      {loadError ? (
        <Result status="warning" title="课程证据暂时无法加载" subTitle="最新课程记录未能更新，请稍后重试。" extra={<Button onClick={() => load()}>重新加载</Button>} />
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
            locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={!hasLoaded ? '尚未读取课程，点击“更新课程”' : riskOnly ? '当前筛选下没有风险课程' : '当前筛选下没有课程记录'} /> }}
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

      <Drawer width={700} title={selected ? `课程证据 · ${selected.lesson_id}` : '课程证据'} open={Boolean(selected)} onClose={() => setSelected(undefined)}>
        {selected ? <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <Descriptions bordered size="small" column={1}>
            <Descriptions.Item label="教师"><Button type="link" className="table-link" onClick={() => onNavigate('teachers', { teacherId: selected.teacher_id })}>{selected.teacher_name || selected.teacher_id}</Button></Descriptions.Item>
            <Descriptions.Item label="课程日期">{selected.lesson_date || '日期待确认'} {selected.lesson_time || ''}</Descriptions.Item>
            <Descriptions.Item label="课程状态">{lessonStatusLabel(selected.lesson_status)}</Descriptions.Item>
            <Descriptions.Item label="风险类型"><Flex gap={5} wrap="wrap">{selected.risk_domains.map((value) => <Tag key={value}>{operationDomainLabel(value)}</Tag>)}</Flex></Descriptions.Item>
            <Descriptions.Item label="投诉级别">{complaintLevelTag(selected.complaint_level)}</Descriptions.Item>
          </Descriptions>
          <Card size="small" title="触发证据">
            {selected.signals.length ? <Flex gap={6} wrap="wrap">{selected.signals.map((signal, index) => <Tag color="orange" key={`${lessonSignalLabel(signal)}-${index}`}>{lessonSignalLabel(signal)}</Tag>)}</Flex> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无异常证据" />}
          </Card>
        </Space> : null}
      </Drawer>
    </div>
  )
}
