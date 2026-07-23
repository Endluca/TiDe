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
import { EyeOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons'
import { api } from '../api'
import { displayError } from '../domain'
import type {
  AppSnapshot,
  SharedTaskAssignment,
  TaskProgressAssignmentPage,
} from '../types'
import { PageHeader, PriorityTag, TimeText } from '../components/Common'
import {
  filterTaskProgressRows,
  normalizeTaskProgressItems,
} from '../taskProgress'
import type { TaskProgressRow } from '../taskProgress'

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

function statusBadge(status: SharedTaskAssignment['status']) {
  const badge = status === 'COMPLETED'
    ? 'success'
    : ['FAILED', 'EXPIRED'].includes(status)
      ? 'error'
      : terminalStatuses.has(status)
        ? 'default'
        : 'processing'
  return <Badge status={badge} text={statusLabels[status]} />
}

type TaskDetailState = TaskProgressAssignmentPage & {
  loading: boolean
  loaded: boolean
  error: string
}

export default function TaskCenter({ snapshot }: { snapshot: AppSnapshot }) {
  const [progressRows, setProgressRows] = useState<TaskProgressRow[]>([])
  const [detailPages, setDetailPages] = useState<Record<string, TaskDetailState>>({})
  const [loading, setLoading] = useState(false)
  const [hasLoaded, setHasLoaded] = useState(false)
  const [loadError, setLoadError] = useState('')
  const [keyword, setKeyword] = useState('')
  const [lifecycle, setLifecycle] = useState('')
  const [taskKind, setTaskKind] = useState('')
  const [selected, setSelected] = useState<SharedTaskAssignment>()
  const teacherNames = useMemo(
    () => new Map(snapshot.teachers.map((teacher) => [teacher.teacher_id, teacher.name])),
    [snapshot.teachers],
  )

  const load = useCallback(async () => {
    setLoading(true)
    setLoadError('')
    try {
      const response = await api.taskProgress()
      setProgressRows(normalizeTaskProgressItems(response.items))
      setDetailPages({})
      setSelected(undefined)
    } catch (error) {
      setProgressRows([])
      setLoadError(displayError(error))
    } finally {
      setHasLoaded(true)
      setLoading(false)
    }
  }, [])

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
      const response = await api.taskProgressAssignments({
        task_code: item.task_code,
        title: item.title,
        task_kind: item.task_kind,
        page,
        page_size: pageSize,
      })
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
          error: displayError(error),
        },
      }))
    }
  }, [])

  const filteredRows = useMemo(
    () => filterTaskProgressRows(progressRows, { keyword, lifecycle, taskKind }),
    [progressRows, keyword, lifecycle, taskKind],
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

  const detailColumns: TableColumnsType<SharedTaskAssignment> = [
    {
      title: '教师', key: 'teacher', width: 210,
      render: (_, item) => <Space direction="vertical" size={2}><Text strong>{item.teacher_name || teacherNames.get(item.teacher_id) || item.teacher_id}</Text><Text code>{item.teacher_id}</Text></Space>,
    },
    {
      title: '状态', key: 'status', width: 140,
      render: (_, item) => statusBadge(item.status),
    },
    {
      title: '为什么产生', dataIndex: 'why', width: 420,
      render: (value: string) => <Paragraph ellipsis={{ rows: 2, tooltip: value }} style={{ margin: 0 }}>{value}</Paragraph>,
    },
    {
      title: '分配 / 更新', key: 'time', width: 220,
      render: (_, item) => <Space direction="vertical" size={3}><Text type="secondary">分配 <TimeText value={item.assigned_at} /></Text><Text type="secondary">更新 <TimeText value={item.updated_at} /></Text></Space>,
    },
    { title: '操作', key: 'action', width: 90, fixed: 'right', render: (_, item) => <Button type="link" icon={<EyeOutlined />} onClick={() => setSelected(item)}>详情</Button> },
  ]

  const columns: TableColumnsType<TaskProgressRow> = [
    {
      title: '任务名称',
      key: 'task',
      width: 300,
      fixed: 'left',
      render: (_, item) => <Space direction="vertical" size={3}>
        <Text strong>{item.title}</Text>
        <Text code>{item.task_code}</Text>
      </Space>,
    },
    {
      title: '任务类型',
      dataIndex: 'task_kind',
      width: 140,
      render: (value: TaskProgressRow['task_kind']) => (
        <Tag color={value === 'FIXED_GROWTH' ? 'purple' : 'blue'}>
          {value === 'FIXED_GROWTH' ? '必修成长' : '个性化改善'}
        </Tag>
      ),
    },
    { title: '分配人数', dataIndex: 'assigned_teacher_count', width: 110, align: 'right' },
    { title: '任务实例', dataIndex: 'assignment_count', width: 110, align: 'right' },
    { title: '未开始', dataIndex: 'not_started', width: 100, align: 'right' },
    { title: '进行中', dataIndex: 'in_progress', width: 100, align: 'right' },
    {
      title: '已完成',
      dataIndex: 'completed',
      width: 100,
      align: 'right',
      render: (value: number) => <Text strong type={value > 0 ? 'success' : undefined}>{value}</Text>,
    },
    { title: '其他', dataIndex: 'other', width: 90, align: 'right' },
    {
      title: '完成率',
      dataIndex: 'completion_rate',
      width: 110,
      align: 'right',
      fixed: 'right',
      render: (value: number) => <Text strong>{(value * 100).toFixed(1)}%</Text>,
    },
  ]

  return <div className="page-shell">
    <PageHeader
      eyebrow="任务进展"
      title="任务完成进展"
      description="先按任务查看覆盖与完成情况；需要定位教师时，再展开对应任务查看明细。"
      actions={<Button icon={<ReloadOutlined />} loading={loading} onClick={() => load()}>更新任务数据</Button>}
    />

    <Row gutter={[12, 12]}>
      <Col xs={12} lg={6}><Card><Statistic title="任务种类" value={hasLoaded ? summary.taskCount : '—'} /></Card></Col>
      <Col xs={12} lg={6}><Card><Statistic title="教师覆盖人次" value={hasLoaded ? summary.teacherCoverageCount : '—'} /></Card></Col>
      <Col xs={12} lg={6}><Card><Statistic title="任务实例" value={hasLoaded ? summary.assignmentCount : '—'} /></Card></Col>
      <Col xs={12} lg={6}><Card><Statistic title="已完成实例" value={hasLoaded ? summary.completedCount : '—'} valueStyle={{ color: '#287d5b' }} /></Card></Col>
    </Row>

    <Card>
      <Flex gap={12} wrap>
        <Input allowClear prefix={<SearchOutlined />} placeholder="搜索任务名称或编号" value={keyword} onChange={(event) => setKeyword(event.target.value)} style={{ width: 300 }} />
        <Select allowClear placeholder="全部任务类型" value={taskKind || undefined} onChange={(value) => setTaskKind(value || '')} style={{ width: 190 }} options={[{ value: 'FIXED_GROWTH', label: '必修成长' }, { value: 'PERSONALIZED_IMPROVEMENT', label: '个性化改善' }]} />
        <Select
          allowClear
          placeholder="全部生命周期"
          value={lifecycle || undefined}
          onChange={(value) => setLifecycle(value || '')}
          style={{ width: 190 }}
          options={[
            { value: 'NOT_STARTED', label: '有未开始' },
            { value: 'IN_PROGRESS', label: '有进行中' },
            { value: 'COMPLETED', label: '有已完成' },
            { value: 'OTHER', label: '有其他状态' },
          ]}
        />
        <Text type="secondary" style={{ marginLeft: 'auto' }}>显示 {filteredRows.length} / {progressRows.length} 类任务</Text>
      </Flex>
    </Card>

    {loadError
      ? <Result status="warning" title="任务进展加载失败" subTitle={loadError} extra={<Button onClick={() => load()}>重试</Button>} />
      : <Card
        title="按任务查看"
        extra={<Text type="secondary">状态列按任务实例统计；完成率 = 已完成实例 ÷ 任务实例</Text>}
        styles={{ body: { padding: 0 } }}
      >
        <Table
          rowKey="key"
          loading={loading}
          dataSource={filteredRows}
          columns={columns}
          pagination={{ pageSize: 15, hideOnSinglePage: true }}
          scroll={{ x: 1160 }}
          expandable={{
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
                return <Flex justify="center" style={{ padding: 24 }}><Spin tip="正在读取教师明细"><div /></Spin></Flex>
              }
              if (detail.error) {
                return <Result
                  status="warning"
                  title="教师明细加载失败"
                  subTitle={detail.error}
                  extra={<Button onClick={() => loadDetails(item, detail.page, detail.page_size)}>重试</Button>}
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
          locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={hasLoaded ? '当前没有教师任务' : '尚未读取任务，点击“更新任务数据”'} /> }}
        />
      </Card>}

    <Drawer width={800} title={selected ? selected.title || selected.task_code : '任务详情'} open={Boolean(selected)} onClose={() => setSelected(undefined)}>
      {selected ? <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <Flex gap={8} wrap>{statusBadge(selected.status)}<Tag>{selected.task_code}</Tag><Tag color={selected.task_kind === 'FIXED_GROWTH' ? 'purple' : 'blue'}>{selected.task_kind === 'FIXED_GROWTH' ? '必修成长' : '个性化改善'}</Tag><PriorityTag priority={selected.priority} /></Flex>
        <Descriptions bordered size="small" column={1}>
          <Descriptions.Item label="教师">{selected.teacher_name || teacherNames.get(selected.teacher_id) || '—'} · {selected.teacher_id}</Descriptions.Item>
          <Descriptions.Item label="任务编号">{selected.task_code}</Descriptions.Item>
          <Descriptions.Item label="为什么产生">{selected.why}</Descriptions.Item>
          <Descriptions.Item label="怎么做">{selected.what_to_do || '见教师端任务执行页'}</Descriptions.Item>
          <Descriptions.Item label="完成标准">{selected.completion_standard || '见教师端任务执行页'}</Descriptions.Item>
          <Descriptions.Item label="截止时间"><TimeText value={selected.due_at} /></Descriptions.Item>
          <Descriptions.Item label="状态时间"><TimeText value={selected.status_changed_at} /></Descriptions.Item>
          <Descriptions.Item label="完成时间"><TimeText value={selected.completed_at} /></Descriptions.Item>
        </Descriptions>
        <Collapse ghost items={[{
          key: 'debug',
          label: '调试信息',
          children: <Descriptions bordered size="small" column={1}>
            <Descriptions.Item label="任务记录 ID">{selected.assignment_id}</Descriptions.Item>
            <Descriptions.Item label="更新序号">{selected.row_version}</Descriptions.Item>
            <Descriptions.Item label="写入服务">{selected.creator_system}</Descriptions.Item>
            <Descriptions.Item label="数据标记">{selected.source_mode}</Descriptions.Item>
            <Descriptions.Item label="最后更新者">{selected.updated_by}</Descriptions.Item>
          </Descriptions>,
        }]} />
      </Space> : null}
    </Drawer>
  </div>
}
