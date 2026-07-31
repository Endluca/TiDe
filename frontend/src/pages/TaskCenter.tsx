import { useCallback, useMemo, useState } from 'react'
import {
  Badge,
  Button,
  Card,
  Descriptions,
  Drawer,
  Empty,
  Flex,
  Input,
  Result,
  Row,
  Col,
  Collapse,
  Select,
  Space,
  Spin,
  Statistic,
  Table,
  Tag,
  Typography,
} from 'antd'
import type { TableColumnsType } from 'antd'
import { EyeOutlined, ReloadOutlined } from '@ant-design/icons'
import { api, isRequestCancelled } from '../api'
import { displayError } from '../domain'
import type {
  SharedTaskAssignment,
  TaskProgressAssignmentPage,
} from '../types'
import { PageHeader, PriorityTag, TimeText } from '../components/Common'
import {
  filterTaskProgressRows,
  normalizeTaskProgressItems,
} from '../taskProgress'
import type { TaskProgressRow } from '../taskProgress'
import { useI18n } from '../i18n'
import type { AppLocale } from '../i18n'
import { useLatestRequest, useLatestRequestMap } from '../useLatestRequest'

const { Paragraph, Text } = Typography

const terminalStatuses = new Set(['COMPLETED', 'FAILED', 'EXPIRED', 'WAIVED', 'CANCELLED'])
const statusLabels: Record<SharedTaskAssignment['status'], string> = {
  ASSIGNED: '待查看',
  VIEWED: '已查看',
  IN_PROGRESS: '进行中',
  SUBMITTED: '已提交',
  UNDER_REVIEW: '审核中',
  COMPLETED: '已完成',
  FAILED: '未通过',
  EXPIRED: '已逾期',
  WAIVED: '已豁免',
  CANCELLED: '已取消',
}

const statusLabelsEn: Record<SharedTaskAssignment['status'], string> = {
  ASSIGNED: 'Not viewed',
  VIEWED: 'Viewed',
  IN_PROGRESS: 'In progress',
  SUBMITTED: 'Submitted',
  UNDER_REVIEW: 'Under review',
  COMPLETED: 'Completed',
  FAILED: 'Failed',
  EXPIRED: 'Overdue',
  WAIVED: 'Waived',
  CANCELLED: 'Cancelled',
}

function statusBadge(status: SharedTaskAssignment['status'], locale: AppLocale) {
  const badge = status === 'COMPLETED'
    ? 'success'
    : ['FAILED', 'EXPIRED'].includes(status)
      ? 'error'
      : terminalStatuses.has(status)
        ? 'default'
        : 'processing'
  return <Badge status={badge} text={(locale === 'en-US' ? statusLabelsEn : statusLabels)[status]} />
}

type TaskDetailState = TaskProgressAssignmentPage & {
  loading: boolean
  loaded: boolean
  error: string
}

export default function TaskCenter({ active = true }: { active?: boolean }) {
  const { locale, t } = useI18n()
  const progressRequest = useLatestRequest()
  const detailRequestFor = useLatestRequestMap()
  const [progressRows, setProgressRows] = useState<TaskProgressRow[]>([])
  const [detailPages, setDetailPages] = useState<Record<string, TaskDetailState>>({})
  const [loading, setLoading] = useState(false)
  const [hasLoaded, setHasLoaded] = useState(false)
  const [loadError, setLoadError] = useState('')
  const [keyword, setKeyword] = useState('')
  const [appliedKeyword, setAppliedKeyword] = useState('')
  const [lifecycle, setLifecycle] = useState('')
  const [taskKind, setTaskKind] = useState('')
  const [selected, setSelected] = useState<SharedTaskAssignment>()
  const [tablePage, setTablePage] = useState(1)
  const [expandedRowKeys, setExpandedRowKeys] = useState<string[]>([])
  const load = useCallback(async (searchKeyword = '') => {
    detailRequestFor.cancelAll()
    setExpandedRowKeys([])
    setDetailPages({})
    setSelected(undefined)
    setLoading(true)
    setLoadError('')
    let cancelled = false
    try {
      const response = await progressRequest.run((options) =>
        api.taskProgress({
          keyword: searchKeyword.trim() || undefined,
        }, options),
      )
      setProgressRows(normalizeTaskProgressItems(response.items))
    } catch (error) {
      if (isRequestCancelled(error)) {
        cancelled = true
        return
      }
      setProgressRows([])
      setLoadError(displayError(error, locale))
    } finally {
      if (!cancelled) {
        setHasLoaded(true)
        setLoading(false)
      }
    }
  }, [detailRequestFor, locale, progressRequest])
  const loadDetails = useCallback(async (
    item: TaskProgressRow,
    page: number,
    pageSize: number,
  ) => {
    setDetailPages((current) => ({
      ...current,
      [item.key]: {
        items: current[item.key]?.items ?? [],
        total: current[item.key]?.total ?? 0,
        page,
        page_size: pageSize,
        total_pages: current[item.key]?.total_pages ?? 0,
        loading: true,
        loaded: current[item.key]?.loaded ?? false,
        error: '',
      },
    }))
    try {
      const response = await detailRequestFor(item.key).run((options) =>
        api.taskProgressAssignments({
          task_code: item.task_code,
          title: item.title,
          task_kind: item.task_kind,
          keyword: appliedKeyword || undefined,
          page,
          page_size: pageSize,
        }, options),
      )
      setDetailPages((current) => ({
        ...current,
        [item.key]: {
          ...response,
          loading: false,
          loaded: true,
          error: '',
        },
      }))
    } catch (error) {
      if (isRequestCancelled(error)) return
      setDetailPages((current) => ({
        ...current,
        [item.key]: {
          items: current[item.key]?.items ?? [],
          total: current[item.key]?.total ?? 0,
          page,
          page_size: pageSize,
          total_pages: current[item.key]?.total_pages ?? 0,
          loading: false,
          loaded: true,
          error: displayError(error, locale),
        },
      }))
    }
  }, [appliedKeyword, detailRequestFor, locale])

  const filteredRows = useMemo(
    () => filterTaskProgressRows(progressRows, { keyword: '', lifecycle, taskKind }),
    [progressRows, lifecycle, taskKind],
  )

  const summary = useMemo(() => ({
    taskCount: progressRows.length,
    teacherCoverageCount: progressRows.reduce(
      (total, item) => total + item.assigned_teacher_count,
      0,
    ),
    assignmentCount: progressRows.reduce(
      (total, item) => total + item.assignment_count,
      0,
    ),
    completedCount: progressRows.reduce(
      (total, item) => total + item.completed,
      0,
    ),
  }), [progressRows])

  if (!active) return null

  const detailColumns: TableColumnsType<SharedTaskAssignment> = [
    {
      title: t('教师', 'Teacher'), key: 'teacher', width: 210,
      render: (_, item) => <Space direction="vertical" size={2}><Text strong>{item.teacher_name || item.teacher_id}</Text><Text code>{item.teacher_id}</Text></Space>,
    },
    {
      title: t('状态', 'Status'), key: 'status', width: 140,
      render: (_, item) => statusBadge(item.status, locale),
    },
    {
      title: t('为什么产生', 'Why it was assigned'), dataIndex: 'why', width: 420,
      render: (value: string) => <Paragraph ellipsis={{ rows: 2, tooltip: value }} style={{ margin: 0 }}>{value}</Paragraph>,
    },
    {
      title: t('分配 / 更新', 'Assigned / Updated'), key: 'time', width: 220,
      render: (_, item) => <Space direction="vertical" size={3}><Text type="secondary">{t('分配', 'Assigned')} <TimeText value={item.assigned_at} /></Text><Text type="secondary">{t('更新', 'Updated')} <TimeText value={item.updated_at} /></Text></Space>,
    },
    { title: t('操作', 'Action'), key: 'action', width: 90, fixed: 'right', render: (_, item) => <Button type="link" icon={<EyeOutlined />} onClick={() => setSelected(item)}>{t('详情', 'Details')}</Button> },
  ]

  const columns: TableColumnsType<TaskProgressRow> = [
    {
      title: t('任务名称', 'Task name'),
      key: 'task',
      width: 300,
      fixed: 'left',
      render: (_, item) => <Space direction="vertical" size={3}>
        <Text strong>{item.title}</Text>
        <Text code>{item.task_code}</Text>
      </Space>,
    },
    {
      title: t('任务类型', 'Task type'),
      dataIndex: 'task_kind',
      width: 140,
      render: (value: TaskProgressRow['task_kind']) => (
        <Tag color={value === 'FIXED_GROWTH' ? 'purple' : 'blue'}>
          {value === 'FIXED_GROWTH' ? t('必修成长', 'Mandatory growth') : t('个性化改善', 'Personalized improvement')}
        </Tag>
      ),
    },
    { title: t('分配人数', 'Assigned teachers'), dataIndex: 'assigned_teacher_count', width: 110, align: 'right' },
    { title: t('任务实例', 'Assignments'), dataIndex: 'assignment_count', width: 110, align: 'right' },
    { title: t('未开始', 'Not started'), dataIndex: 'not_started', width: 100, align: 'right' },
    { title: t('进行中', 'In progress'), dataIndex: 'in_progress', width: 100, align: 'right' },
    {
      title: t('已完成', 'Completed'),
      dataIndex: 'completed',
      width: 100,
      align: 'right',
      render: (value: number) => <Text strong type={value > 0 ? 'success' : undefined}>{value}</Text>,
    },
    { title: t('其他', 'Other'), dataIndex: 'other', width: 90, align: 'right' },
    {
      title: t('完成率', 'Completion rate'),
      dataIndex: 'completion_rate',
      width: 110,
      align: 'right',
      fixed: 'right',
      render: (value: number) => <Text strong>{(value * 100).toFixed(1)}%</Text>,
    },
  ]

  return <div className="page-shell">
    <PageHeader
      eyebrow={t('任务进展', 'Task Progress')}
      title={t('任务完成进展', 'Task Completion Progress')}
      description={t('先按任务查看覆盖与完成情况；需要定位教师时，再展开对应任务查看明细。', 'Review coverage and completion by task, then expand a task to locate individual teachers.')}
      actions={<Button icon={<ReloadOutlined />} loading={loading} onClick={() => load(appliedKeyword)}>{t('更新任务数据', 'Refresh task data')}</Button>}
    />

    <Row gutter={[12, 12]}>
      <Col xs={12} lg={6}><Card><Statistic title={t('任务种类', 'Task types')} value={hasLoaded ? summary.taskCount : '—'} /></Card></Col>
      <Col xs={12} lg={6}><Card><Statistic title={t('教师覆盖人次', 'Teacher coverage')} value={hasLoaded ? summary.teacherCoverageCount : '—'} /></Card></Col>
      <Col xs={12} lg={6}><Card><Statistic title={t('任务实例', 'Assignments')} value={hasLoaded ? summary.assignmentCount : '—'} /></Card></Col>
      <Col xs={12} lg={6}><Card><Statistic title={t('已完成实例', 'Completed assignments')} value={hasLoaded ? summary.completedCount : '—'} valueStyle={{ color: '#287d5b' }} /></Card></Col>
    </Row>

    <Card>
      <Flex gap={12} wrap>
        <Input.Search
          allowClear
          enterButton={t('搜索', 'Search')}
          loading={loading}
          placeholder={t('搜索任务名称、编号或教师 ID', 'Search task name, ID, or teacher ID')}
          value={keyword}
          onChange={(event) => {
            const value = event.target.value
            setKeyword(value)
            if (!value && appliedKeyword) {
              setAppliedKeyword('')
              setTablePage(1)
              void load('')
            }
          }}
          onSearch={(value) => {
            const normalized = value.trim()
            setKeyword(normalized)
            setAppliedKeyword(normalized)
            setTablePage(1)
            void load(normalized)
          }}
          style={{ width: 360 }}
        />
        <Select allowClear placeholder={t('全部任务类型', 'All task types')} value={taskKind || undefined} onChange={(value) => { setTaskKind(value || ''); setTablePage(1) }} style={{ width: 190 }} options={[{ value: 'FIXED_GROWTH', label: t('必修成长', 'Mandatory growth') }, { value: 'PERSONALIZED_IMPROVEMENT', label: t('个性化改善', 'Personalized improvement') }]} />
        <Select
          allowClear
          placeholder={t('全部生命周期', 'All lifecycle states')}
          value={lifecycle || undefined}
          onChange={(value) => { setLifecycle(value || ''); setTablePage(1) }}
          style={{ width: 190 }}
          options={[
            { value: 'NOT_STARTED', label: t('有未开始', 'Has not started') },
            { value: 'IN_PROGRESS', label: t('有进行中', 'Has in progress') },
            { value: 'COMPLETED', label: t('有已完成', 'Has completed') },
            { value: 'OTHER', label: t('有其他状态', 'Has other status') },
          ]}
        />
        <Text type="secondary" style={{ marginLeft: 'auto' }}>{t(`显示 ${filteredRows.length} / ${progressRows.length} 类任务`, `Showing ${filteredRows.length} / ${progressRows.length} task types`)}</Text>
      </Flex>
    </Card>

    {loadError
      ? <Result status="warning" title={t('任务进展加载失败', 'Unable to load task progress')} subTitle={loadError} extra={<Button onClick={() => load(appliedKeyword)}>{t('重试', 'Try again')}</Button>} />
      : <Card
        title={t('按任务查看', 'View by task')}
        extra={<Text type="secondary">{t('状态列按任务实例统计；完成率 = 已完成实例 ÷ 任务实例', 'Status columns count assignments; completion rate = completed assignments ÷ assignments')}</Text>}
        styles={{ body: { padding: 0 } }}
      >
        <Table
          rowKey="key"
          loading={loading}
          dataSource={filteredRows}
          columns={columns}
          pagination={{ current: tablePage, pageSize: 15, hideOnSinglePage: true, onChange: setTablePage }}
          scroll={{ x: 1160 }}
          expandable={{
            expandedRowKeys,
            onExpandedRowsChange: (keys) => setExpandedRowKeys(keys.map(String)),
            rowExpandable: (item) => item.assignment_count > 0,
            onExpand: (expanded, item) => {
              const detail = detailPages[item.key]
              if (expanded && !detail?.loading && !detail?.loaded) {
                void loadDetails(item, 1, 10)
              }
            },
            expandedRowRender: (item) => {
              const detail = detailPages[item.key]
              if (!detail || (detail.loading && !detail.loaded)) {
                return <Flex justify="center" style={{ padding: 24 }}><Spin tip={t('正在读取教师明细', 'Loading teacher details')}><div /></Spin></Flex>
              }
              if (detail.error) {
                return <Result
                  status="warning"
                  title={t('教师明细加载失败', 'Unable to load teacher details')}
                  subTitle={detail.error}
                  extra={<Button onClick={() => loadDetails(item, detail.page, detail.page_size)}>{t('重试', 'Try again')}</Button>}
                />
              }
              return <Table
                rowKey="assignment_id"
                size="small"
                loading={detail.loading}
                dataSource={detail.items}
                columns={detailColumns}
                pagination={{
                  current: detail.page,
                  pageSize: detail.page_size,
                  total: detail.total,
                  showSizeChanger: true,
                  pageSizeOptions: [10, 20, 50],
                  onChange: (page, pageSize) => {
                    void loadDetails(item, page, pageSize)
                  },
                }}
                scroll={{ x: 1080 }}
              />
            },
          }}
          locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={hasLoaded ? t('当前没有教师任务', 'No teacher tasks found') : t('尚未读取任务，点击“更新任务数据”', 'Tasks have not been loaded. Select “Refresh task data”.')} /> }}
        />
      </Card>}

    <Drawer width={800} title={selected ? selected.title || selected.task_code : t('任务详情', 'Task details')} open={Boolean(selected)} onClose={() => setSelected(undefined)}>
      {selected ? <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <Flex gap={8} wrap>{statusBadge(selected.status, locale)}<Tag>{selected.task_code}</Tag><Tag color={selected.task_kind === 'FIXED_GROWTH' ? 'purple' : 'blue'}>{selected.task_kind === 'FIXED_GROWTH' ? t('必修成长', 'Mandatory growth') : t('个性化改善', 'Personalized improvement')}</Tag><PriorityTag priority={selected.priority} /></Flex>
        <Descriptions bordered size="small" column={1}>
          <Descriptions.Item label={t('教师', 'Teacher')}>{selected.teacher_name || '—'} · {selected.teacher_id}</Descriptions.Item>
          <Descriptions.Item label={t('任务编号', 'Task ID')}>{selected.task_code}</Descriptions.Item>
          <Descriptions.Item label={t('为什么产生', 'Why it was assigned')}>{selected.why}</Descriptions.Item>
          <Descriptions.Item label={t('怎么做', 'What to do')}>{selected.what_to_do || t('见教师端任务执行页', 'See the teacher task page')}</Descriptions.Item>
          <Descriptions.Item label={t('完成标准', 'Completion criteria')}>{selected.completion_standard || t('见教师端任务执行页', 'See the teacher task page')}</Descriptions.Item>
          <Descriptions.Item label={t('完成后获得', 'Outcome')}>{selected.outcome || '—'}</Descriptions.Item>
          <Descriptions.Item label={t('截止时间', 'Due at')}><TimeText value={selected.due_at} /></Descriptions.Item>
          <Descriptions.Item label={t('状态时间', 'Status changed at')}><TimeText value={selected.status_changed_at} /></Descriptions.Item>
          <Descriptions.Item label={t('完成时间', 'Completed at')}><TimeText value={selected.completed_at} /></Descriptions.Item>
        </Descriptions>
        <Collapse ghost items={[{
          key: 'debug',
          label: t('调试信息', 'Debug information'),
          children: <Descriptions bordered size="small" column={1}>
            <Descriptions.Item label={t('任务记录 ID', 'Assignment ID')}>{selected.assignment_id}</Descriptions.Item>
            <Descriptions.Item label={t('更新序号', 'Row version')}>{selected.row_version}</Descriptions.Item>
            <Descriptions.Item label={t('写入服务', 'Creator system')}>{selected.creator_system}</Descriptions.Item>
            <Descriptions.Item label={t('数据标记', 'Source mode')}>{selected.source_mode}</Descriptions.Item>
            <Descriptions.Item label={t('最后更新者', 'Last updated by')}>{selected.updated_by}</Descriptions.Item>
          </Descriptions>,
        }]} />
      </Space> : null}
    </Drawer>
  </div>
}
