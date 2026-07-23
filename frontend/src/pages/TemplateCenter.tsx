import { useCallback, useMemo, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Drawer,
  Empty,
  Flex,
  Input,
  Result,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd'
import { EyeOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons'
import type { TableColumnsType } from 'antd'
import { api } from '../api'
import { displayError } from '../domain'
import type { AppSnapshot, TaskTemplate } from '../types'
import { PageHeader } from '../components/Common'
import {
  filterTaskTemplates,
  normalizeTaskTemplateList,
  taskOwnerLabel,
  taskScoreSummary,
} from '../templateCenter'

const { Paragraph, Text } = Typography

const stageLabels: Record<string, string> = {
  DAY_1_7: '第 1–7 天',
  DAY_8_14: '第 8–14 天',
  DAY_15_30: '第 15–30 天',
  DAY_1_30: '第 1–30 天',
  TRIGGERED: '信号触发后',
}

function stageLabel(stage: string): string {
  return stageLabels[stage] ?? stage
}

export default function TemplateCenter({
  refresh,
}: {
  snapshot: AppSnapshot
  refresh: () => Promise<void>
}) {
  const [templates, setTemplates] = useState<TaskTemplate[]>([])
  const [loading, setLoading] = useState(false)
  const [hasLoaded, setHasLoaded] = useState(false)
  const [loadError, setLoadError] = useState('')
  const [keyword, setKeyword] = useState('')
  const [stage, setStage] = useState('')
  const [selected, setSelected] = useState<TaskTemplate>()

  const load = useCallback(async () => {
    setLoading(true)
    setLoadError('')
    try {
      const response = await api.taskTemplates()
      setTemplates(
        normalizeTaskTemplateList(response)
          .sort((left, right) => left.template_id.localeCompare(right.template_id)),
      )
    } catch (error) {
      setTemplates([])
      setLoadError(displayError(error))
    } finally {
      setHasLoaded(true)
      setLoading(false)
    }
  }, [])

  const filtered = useMemo(
    () => filterTaskTemplates(templates, { keyword, stage }),
    [keyword, stage, templates],
  )

  const stageOptions = useMemo(
    () => [...new Set(templates.map((item) => item.stage))]
      .map((value) => ({ value, label: stageLabel(value) })),
    [templates],
  )

  const totalScore = useMemo(
    () => templates.reduce((sum, item) => sum + (item.score_type === 'FIXED' ? item.score_value : 0), 0),
    [templates],
  )
  const mandatoryCount = useMemo(
    () => templates.filter((item) => item.integration_mode === 'INBOUND_STATUS_ONLY').length,
    [templates],
  )
  const personalizedCount = templates.length - mandatoryCount

  const columns: TableColumnsType<TaskTemplate> = [
    {
      title: '编号',
      dataIndex: 'template_id',
      width: 90,
      render: (value: string) => <Text code>{value}</Text>,
    },
    {
      title: '名称',
      key: 'name',
      width: 260,
      render: (_, item) => <Space direction="vertical" size={2}>
        <Text strong>{item.ops_name_zh}</Text>
        <Text type="secondary">{item.title}</Text>
      </Space>,
    },
    {
      title: '阶段',
      dataIndex: 'stage',
      width: 130,
      render: (value: string) => <Tag>{stageLabel(value)}</Tag>,
    },
    {
      title: '为什么要做',
      dataIndex: 'why_template',
      width: 430,
      render: (value: string) => <Paragraph ellipsis={{ rows: 3, tooltip: value }} style={{ margin: 0 }}>{value}</Paragraph>,
    },
    {
      title: '积分',
      key: 'score',
      width: 100,
      render: (_, item) => <Tag color="purple">{taskScoreSummary(item)}</Tag>,
    },
    {
      title: '责任方',
      key: 'owner',
      width: 110,
      render: (_, item) => taskOwnerLabel(item),
    },
    {
      title: '操作',
      key: 'action',
      width: 90,
      fixed: 'right',
      render: (_, item) => <Button type="link" icon={<EyeOutlined />} onClick={() => setSelected(item)}>详情</Button>,
    },
  ]

  async function refreshAll() {
    await Promise.all([load(), refresh()])
  }

  return <div className="page-shell">
    <PageHeader
      eyebrow="任务与规则"
      title="任务规则"
      description="统一查看必修成长与个性化改善任务的适用阶段、教师动作、完成标准与积分，不在这里修改教师完成状态。"
      actions={<Button icon={<ReloadOutlined />} loading={loading} onClick={() => refreshAll().catch(() => undefined)}>刷新</Button>}
    />

    <Alert
      type="info"
      showIcon
      message={`共 ${templates.length} 项：${mandatoryCount} 项必修成长任务，${personalizedCount} 项个性化改善任务`}
      description={`必修成长任务合计 ${totalScore} 分；个性化改善任务均为 0 分。所有任务的完成状态由教师端维护。`}
    />

    <Card>
      <Flex gap={12} wrap>
        <Input
          allowClear
          prefix={<SearchOutlined />}
          placeholder="搜索编号、名称或任务内容"
          value={keyword}
          onChange={(event) => setKeyword(event.target.value)}
          style={{ width: 340 }}
        />
        <Select
          allowClear
          placeholder="全部阶段"
          value={stage || undefined}
          onChange={(value) => setStage(value || '')}
          options={stageOptions}
          style={{ width: 180 }}
        />
        <Text type="secondary" style={{ marginLeft: 'auto' }}>显示 {filtered.length} / {templates.length}</Text>
      </Flex>
    </Card>

    {loadError
      ? <Result status="warning" title="任务定义加载失败" subTitle={loadError} extra={<Button onClick={() => load()}>重试</Button>} />
      : <Card styles={{ body: { padding: 0 } }}>
        <Table
          rowKey="template_id"
          loading={loading}
          dataSource={filtered}
          columns={columns}
          pagination={false}
          scroll={{ x: 1210 }}
          onRow={(item) => ({ onClick: () => setSelected(item), style: { cursor: 'pointer' } })}
          locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={hasLoaded ? '当前没有任务定义' : '尚未读取任务规则，点击“刷新”'} /> }}
        />
      </Card>}

    <Drawer
      width={760}
      title={selected ? `${selected.template_id} · ${selected.ops_name_zh}` : '任务详情'}
      open={Boolean(selected)}
      onClose={() => setSelected(undefined)}
    >
      {selected ? <Descriptions bordered size="small" column={1}>
        <Descriptions.Item label="编号">{selected.template_id}</Descriptions.Item>
        <Descriptions.Item label="名称"><Space direction="vertical" size={2}><Text strong>{selected.ops_name_zh}</Text><Text type="secondary">{selected.title}</Text></Space></Descriptions.Item>
        <Descriptions.Item label="阶段">{stageLabel(selected.stage)}</Descriptions.Item>
        <Descriptions.Item label="为什么要做">{selected.why_template}</Descriptions.Item>
        <Descriptions.Item label="怎么做">{selected.how_summary}</Descriptions.Item>
        <Descriptions.Item label="完成标准">{selected.completion_standard}</Descriptions.Item>
        <Descriptions.Item label="积分">{taskScoreSummary(selected)}</Descriptions.Item>
        <Descriptions.Item label="责任方">{taskOwnerLabel(selected)}</Descriptions.Item>
      </Descriptions> : null}
    </Drawer>
  </div>
}
