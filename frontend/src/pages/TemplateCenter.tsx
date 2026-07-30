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
import { api, isRequestCancelled } from '../api'
import { displayError } from '../domain'
import type { TaskTemplate } from '../types'
import { PageHeader } from '../components/Common'
import { useI18n } from '../i18n'
import {
  filterTaskTemplates,
  normalizeTaskTemplateList,
  taskOwnerLabel,
  taskScoreSummary,
} from '../templateCenter'
import { useLatestRequest } from '../useLatestRequest'

const { Paragraph, Text } = Typography

function stageLabel(stage: string, locale: 'zh-CN' | 'en-US'): string {
  const labels: Record<string, [string, string]> = {
    DAY_1_7: ['第 1–7 天', 'Days 1–7'],
    DAY_8_14: ['第 8–14 天', 'Days 8–14'],
    DAY_15_30: ['第 15–30 天', 'Days 15–30'],
    DAY_1_30: ['第 1–30 天', 'Days 1–30'],
    TRIGGERED: ['信号触发后', 'After signal'],
  }
  const value = labels[stage]
  return value ? value[locale === 'en-US' ? 1 : 0] : stage
}

export default function TemplateCenter({ active = true }: { active?: boolean }) {
  const { locale, isEnglish, t } = useI18n()
  const templateRequest = useLatestRequest()
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
    let cancelled = false
    try {
      const response = await templateRequest.run((options) => api.taskTemplates(options))
      setTemplates(
        normalizeTaskTemplateList(response)
          .sort((left, right) => left.template_id.localeCompare(right.template_id)),
      )
    } catch (error) {
      if (isRequestCancelled(error)) {
        cancelled = true
        return
      }
      setTemplates([])
      setLoadError(displayError(error, locale))
    } finally {
      if (!cancelled) {
        setHasLoaded(true)
        setLoading(false)
      }
    }
  }, [locale, templateRequest])
  const filtered = useMemo(
    () => filterTaskTemplates(templates, { keyword, stage }),
    [keyword, stage, templates],
  )

  const stageOptions = useMemo(
    () => [...new Set(templates.map((item) => item.stage))]
      .map((value) => ({ value, label: stageLabel(value, locale) })),
    [locale, templates],
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

  if (!active) return null

  const columns: TableColumnsType<TaskTemplate> = [
    {
      title: t('编号', 'ID'),
      dataIndex: 'template_id',
      width: 90,
      render: (value: string) => <Text code>{value}</Text>,
    },
    {
      title: t('名称', 'Name'),
      key: 'name',
      width: 260,
      render: (_, item) => <Space direction="vertical" size={2}>
        <Text strong>{isEnglish ? item.title : item.ops_name_zh}</Text>
        <Text type="secondary">{isEnglish ? item.ops_name_zh : item.title}</Text>
      </Space>,
    },
    {
      title: t('阶段', 'Stage'),
      dataIndex: 'stage',
      width: 130,
      render: (value: string) => <Tag>{stageLabel(value, locale)}</Tag>,
    },
    {
      title: t('为什么要做', 'Why it is assigned'),
      dataIndex: 'why_template',
      width: 430,
      render: (value: string) => <Paragraph ellipsis={{ rows: 3, tooltip: value }} style={{ margin: 0 }}>{value}</Paragraph>,
    },
    {
      title: t('积分', 'Points'),
      key: 'score',
      width: 100,
      render: (_, item) => <Tag color="purple">{taskScoreSummary(item, locale)}</Tag>,
    },
    {
      title: t('责任方', 'Owner'),
      key: 'owner',
      width: 110,
      render: (_, item) => taskOwnerLabel(item, locale),
    },
    {
      title: t('操作', 'Action'),
      key: 'action',
      width: 90,
      fixed: 'right',
      render: (_, item) => <Button type="link" icon={<EyeOutlined />} onClick={() => setSelected(item)}>{t('详情', 'Details')}</Button>,
    },
  ]

  return <div className="page-shell">
    <PageHeader
      eyebrow={t('任务与规则', 'Tasks & Rules')}
      title={t('任务规则', 'Task Rules')}
      description={t('统一查看必修成长与个性化改善任务的适用阶段、教师动作、完成标准与积分，不在这里修改教师完成状态。', 'Review stages, teacher actions, completion criteria, and points for mandatory and personalized tasks. Teacher completion states are not edited here.')}
      actions={<Button icon={<ReloadOutlined />} loading={loading} onClick={() => load().catch(() => undefined)}>{t('刷新', 'Refresh')}</Button>}
    />

    <Alert
      type="info"
      showIcon
      message={t(`共 ${templates.length} 项：${mandatoryCount} 项必修成长任务，${personalizedCount} 项个性化改善任务`, `${templates.length} tasks: ${mandatoryCount} mandatory growth tasks and ${personalizedCount} personalized improvement tasks`)}
      description={t(`必修成长任务合计 ${totalScore} 分；个性化改善任务均为 0 分。所有任务的完成状态由教师端维护。`, `Mandatory growth tasks total ${totalScore} points; personalized improvement tasks award 0 points. All completion states are maintained by the teacher app.`)}
    />

    <Card>
      <Flex gap={12} wrap>
        <Input
          allowClear
          prefix={<SearchOutlined />}
          placeholder={t('搜索编号、名称或任务内容', 'Search ID, name, or task content')}
          value={keyword}
          onChange={(event) => setKeyword(event.target.value)}
          style={{ width: 340 }}
        />
        <Select
          allowClear
          placeholder={t('全部阶段', 'All stages')}
          value={stage || undefined}
          onChange={(value) => setStage(value || '')}
          options={stageOptions}
          style={{ width: 180 }}
        />
        <Text type="secondary" style={{ marginLeft: 'auto' }}>{t('显示', 'Showing')} {filtered.length} / {templates.length}</Text>
      </Flex>
    </Card>

    {loadError
      ? <Result status="warning" title={t('任务定义加载失败', 'Unable to load task rules')} subTitle={loadError} extra={<Button onClick={() => load()}>{t('重试', 'Try again')}</Button>} />
      : <Card styles={{ body: { padding: 0 } }}>
        <Table
          rowKey="template_id"
          loading={loading}
          dataSource={filtered}
          columns={columns}
          pagination={false}
          scroll={{ x: 1210 }}
          onRow={(item) => ({ onClick: () => setSelected(item), style: { cursor: 'pointer' } })}
          locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={hasLoaded ? t('当前没有任务定义', 'No task rules found') : t('尚未读取任务规则，点击“刷新”', 'Task rules have not been loaded. Select “Refresh”.')} /> }}
        />
      </Card>}

    <Drawer
      width={760}
      title={selected ? `${selected.template_id} · ${isEnglish ? selected.title : selected.ops_name_zh}` : t('任务详情', 'Task details')}
      open={Boolean(selected)}
      onClose={() => setSelected(undefined)}
    >
      {selected ? <Descriptions bordered size="small" column={1}>
        <Descriptions.Item label={t('编号', 'ID')}>{selected.template_id}</Descriptions.Item>
        <Descriptions.Item label={t('名称', 'Name')}><Space direction="vertical" size={2}><Text strong>{isEnglish ? selected.title : selected.ops_name_zh}</Text><Text type="secondary">{isEnglish ? selected.ops_name_zh : selected.title}</Text></Space></Descriptions.Item>
        <Descriptions.Item label={t('阶段', 'Stage')}>{stageLabel(selected.stage, locale)}</Descriptions.Item>
        <Descriptions.Item label={t('为什么要做', 'Why it is assigned')}>{selected.why_template}</Descriptions.Item>
        <Descriptions.Item label={t('怎么做', 'What to do')}>{selected.how_summary}</Descriptions.Item>
        <Descriptions.Item label={t('完成标准', 'Completion criteria')}>{selected.completion_standard}</Descriptions.Item>
        <Descriptions.Item label={t('完成后获得', 'Outcome')}>{selected.benefit}</Descriptions.Item>
        <Descriptions.Item label={t('内容状态', 'Content status')}>
          {selected.content_status === 'PENDING_JIAHE'
            ? <Tag color="orange">{t('待嘉荷补充', 'Content pending')}</Tag>
            : <Tag color="green">{t('内容已确认', 'Content confirmed')}</Tag>}
        </Descriptions.Item>
        <Descriptions.Item label={t('积分', 'Points')}>{taskScoreSummary(selected, locale)}</Descriptions.Item>
        <Descriptions.Item label={t('责任方', 'Owner')}>{taskOwnerLabel(selected, locale)}</Descriptions.Item>
      </Descriptions> : null}
    </Drawer>
  </div>
}
