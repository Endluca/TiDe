import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Alert,
  App as AntdApp,
  Button,
  Card,
  Col,
  Descriptions,
  Empty,
  Flex,
  Form,
  Input,
  InputNumber,
  Row,
  Select,
  Space,
  Switch,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography,
} from 'antd'
import {
  CheckCircleOutlined,
  EditOutlined,
  LockOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
} from '@ant-design/icons'
import {
  CONFIG_DOMAIN_META,
  CONFIG_KEYS,
  SCORING_ITEM_META,
  agentEffectivelyEnabled,
  canEditConfiguration,
  canPublishConfiguration,
  canValidateConfiguration,
  configActorLabel,
  configStatusColor,
  currentConfigurations,
  currentConfigOperator,
  currentConfigStatusLabel,
  isLegacyScoreGraduation,
  isScoreGraduationV3,
  isScoreGraduationV4,
  isScoreGraduationV5,
  isScoreGraduationV6,
  type AgentPolicyPayload,
  type ConfigKey,
  type ConfigPayload,
  type ConfigVersion,
  type OperatorIdentity,
} from '../configCenter'
import { PageHeader } from '../components/Common'


const { Paragraph, Text, Title } = Typography
type ConfigFormValues = Record<string, object | string | number | boolean | undefined>


class ConfigApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}


function errorMessage(body: unknown, fallback: string): string {
  if (!body || typeof body !== 'object') return fallback
  const root = body as Record<string, unknown>
  const detail = root.detail && typeof root.detail === 'object' ? root.detail as Record<string, unknown> : root
  return typeof detail.message === 'string' ? detail.message : fallback
}


async function configRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  })
  const body = await response.json().catch(() => ({})) as unknown
  if (!response.ok) throw new ConfigApiError(response.status, errorMessage(body, `HTTP ${response.status}`))
  return body as T
}


function ScoreForm({ payload }: { payload: unknown }) {
  const legacy = isLegacyScoreGraduation(payload) || isScoreGraduationV3(payload) || isScoreGraduationV4(payload) || isScoreGraduationV5(payload)
  const isV4 = isScoreGraduationV4(payload)
  const isV5 = isScoreGraduationV5(payload)
  const isCurrent = isScoreGraduationV6(payload)
  const hasSupplyMilestone = isV4 || isV5 || isCurrent
  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Alert
        type={isCurrent ? 'info' : 'warning'}
        showIcon
        message={isCurrent ? '当前课堂质量和供给积分均由真实指标结算。' : legacy ? '该计分版本已停用，仅供读取。' : '该草稿的积分结构不完整，暂不可编辑。'}
        description={isCurrent ? '课堂质量分 = perfect_cnt × 1.6；peak_slot_cnt 达到 40 时供给维度获得 10 分。其余原始分仍无封顶，对外显示分 = min(raw, 200)。' : '页面不会静默改写已保存规则；如需调整，请从当前已发布版本新建草稿。'}
      />
      <Row gutter={16}>
        <Form.Item name="policy_version" hidden rules={[{ required: true }]}><Input /></Form.Item>
        <Col xs={24} md={8}>
          <Form.Item name="graduation_effect" label="出营生效方式" rules={[{ required: true }]}>
            <Input disabled />
          </Form.Item>
        </Col>
      </Row>

      {hasSupplyMilestone ? (
        <Card size="small" title="供给积分里程碑" extra={<Tag color="cyan">由指标系统结算</Tag>}>
          <Row gutter={16}>
            <Col xs={24} md={8}>
              <Form.Item name={['scoring_items', 'capacity', 'milestone_id']} label="里程碑 ID" rules={[{ required: true }]}>
                <Input disabled />
              </Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Form.Item name={['scoring_items', 'capacity', 'metric']} label="指标字段" rules={[{ required: true }]}>
                <Input disabled />
              </Form.Item>
            </Col>
            <Col xs={24} md={4}>
              <Form.Item name={['scoring_items', 'capacity', 'operator']} label="比较符" rules={[{ required: true }]}>
                <Select disabled options={[{ value: 'GTE', label: '≥' }]} />
              </Form.Item>
            </Col>
            <Col xs={24} md={4}>
              <Form.Item name={['scoring_items', 'capacity', 'threshold']} label="达标阈值" rules={[{ required: true }]}>
                <InputNumber disabled min={0} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col xs={24} md={4}>
              <Form.Item name={['scoring_items', 'capacity', 'score_value']} label="达标得分" rules={[{ required: true }]}>
                <InputNumber disabled min={0} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col xs={24} md={4}>
              <Form.Item name={['scoring_items', 'capacity', 'maximum_points']} label="维度上限" rules={[{ required: true }]}>
                <InputNumber disabled min={0} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col span={24}>
              <Form.Item name={['scoring_items', 'capacity', 'settlement_mode']} label="结算方式" rules={[{ required: true }]}>
                <Select disabled options={[{ value: 'FIRST_ACHIEVEMENT_LOCKED', label: '首次达成即锁定，10 分永久保留' }]} />
              </Form.Item>
            </Col>
          </Row>
          <Text type="secondary">唯一结算公式：peak_slot_cnt 首次达到 40 → 供给分 10/10，并永久保留；达成前为 0/10。任务状态只用于改善闭环，不能代替该指标。</Text>
        </Card>
      ) : null}

      <Title level={5}>计分项</Title>
      <div className="config-dimension-grid config-dimension-header">
        <Text strong>业务计分项</Text><Text strong>计分方式</Text><Text strong>分值</Text><Text strong>数据说明</Text>
      </div>
      {SCORING_ITEM_META.filter((item) => !(hasSupplyMilestone && item.key === 'capacity')).map((item) => (
        <div className="config-dimension-grid" key={item.key}>
          <div><Text strong>{isCurrent && item.key === 'classroom_quality' ? '课堂质量 · 完美完课' : item.label}</Text><Text type="secondary">{item.key}</Text></div>
          <Text>
            {isCurrent && item.key === 'classroom_quality'
              ? '按 perfect_cnt 累加'
              : isCurrent && item.key === 'new_teacher_tasks'
                ? '按必修任务完成状态累加'
                : item.field === 'points_per_unit'
                  ? '按事件数量累加'
                  : '基础项可得上限'}
          </Text>
          <Form.Item name={['scoring_items', item.key, item.field]} rules={[{ required: true }]} noStyle>
            <InputNumber disabled={isCurrent} min={0.01} step={isCurrent && item.key === 'classroom_quality' ? 0.1 : 1} style={{ width: '100%' }} aria-label={`${item.label}（${item.field === 'maximum_points' ? '分' : `分 / ${item.unit}`}）`} />
          </Form.Item>
          <Text type="secondary">
            {item.key === 'classroom_quality'
              ? isCurrent ? '教师宽表 perfect_cnt × 1.6' : '历史版本按替代达成率计算'
              : isCurrent && item.key === 'new_teacher_tasks'
                ? '读取 task_assignments 中 G01–G10 的 COMPLETED 状态，并累加各任务配置分值'
                : item.field === 'maximum_points'
                  ? '仅限制该基础项；课程分累计不受影响'
                  : '真实事件为 0 时得分为 0'}
          </Text>
        </div>
      ))}

      {isCurrent ? (
        <Row gutter={16}>
          <Col xs={24} md={12}>
            <Form.Item name={['scoring_items', 'classroom_quality', 'metric']} label="课堂质量指标字段" rules={[{ required: true }]}>
              <Input disabled />
            </Form.Item>
          </Col>
          <Col xs={24} md={12}>
            <Form.Item name={['scoring_items', 'classroom_quality', 'source_mode']} label="课堂质量来源" rules={[{ required: true }]}>
              <Select disabled options={[{ value: 'REAL_TEACHER_SNAPSHOT', label: '教师指标快照' }]} />
            </Form.Item>
          </Col>
        </Row>
      ) : (
        <Row gutter={16}>
          <Col xs={24} md={12}>
            <Form.Item name={['scoring_items', 'classroom_quality', 'default_achievement_rate']} label="课堂质量默认无问题达成率" rules={[{ required: true }]}>
              <InputNumber min={0} max={1} step={0.05} precision={2} style={{ width: '100%' }} />
            </Form.Item>
          </Col>
          <Col xs={24} md={12}>
            <Form.Item name={['scoring_items', 'classroom_quality', 'source_mode']} label="课堂质量来源模式" rules={[{ required: true }]}>
              <Select disabled options={[{ value: 'MOCK_SIMULATION', label: '历史替代达成率' }]} />
            </Form.Item>
          </Col>
        </Row>
      )}

      <Title level={5}>分数线与对外显示封顶</Title>
      <Row gutter={16}>
        {!isCurrent ? <Col xs={24} md={12}><Form.Item name={['thresholds', 'graduation_raw_score']} label="出营累计分数线" rules={[{ required: true }]}><InputNumber min={0.01} style={{ width: '100%' }} /></Form.Item></Col> : null}
        <Col xs={24} md={12}><Form.Item name={['thresholds', 'graduation_external_score']} label="出营显示分" rules={[{ required: true }]}><InputNumber disabled={isCurrent} min={0.01} max={200} style={{ width: '100%' }} /></Form.Item></Col>
        <Col xs={24} md={12}><Form.Item name={['thresholds', 'gold_raw_score']} label="金牌累计分数线" rules={[{ required: true }]}><InputNumber disabled={isCurrent} min={0.01} style={{ width: '100%' }} /></Form.Item></Col>
        <Col xs={24} md={12}><Form.Item name={['thresholds', 'gold_external_score']} label="对外显示封顶" rules={[{ required: true }]}><InputNumber disabled={isCurrent} min={0.01} max={200} style={{ width: '100%' }} /></Form.Item></Col>
      </Row>

      <Title level={5}>最终出营资格硬门槛</Title>
      {isCurrent ? (
        <Card size="small">
          <Row gutter={16}>
            <Col xs={24} md={8}>
              <Form.Item name={['hard_gates', 'graduation', 'required_mandatory_task_count']} label="必修成长任务完成数" rules={[{ required: true }]}>
                <InputNumber disabled min={0} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Form.Item name={['hard_gates', 'graduation', 'maximum_l0_complaint_count']} label="L0 投诉数上限" rules={[{ required: true }]}>
                <InputNumber disabled min={0} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Form.Item name={['thresholds', 'graduation_raw_score']} label="累计总分下限" rules={[{ required: true }]}>
                <InputNumber disabled min={0} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
          </Row>
          <Text type="secondary">三项必须同时满足：G01–G10 全部完成（30 分）、L0 投诉数为 0、累计总分达到 100。</Text>
        </Card>
      ) : (
        <Row gutter={16}>
          <Col xs={24} md={12}><Form.Item name={['hard_gates', 'graduation', 'minimum_base_score']} label="基础分要求" rules={[{ required: true }]}><InputNumber min={0} style={{ width: '100%' }} /></Form.Item></Col>
          <Col xs={24} md={12}><Form.Item name={['hard_gates', 'graduation', 'minimum_completed_lessons']} label="30 天完课量要求" rules={[{ required: true }]}><InputNumber min={0} style={{ width: '100%' }} /></Form.Item></Col>
          <Col xs={24} md={12}><Form.Item name={['hard_gates', 'graduation', 'minimum_user_feedback_score_exclusive']} label="用户反馈分必须大于" rules={[{ required: true }]}><InputNumber min={0} style={{ width: '100%' }} /></Form.Item></Col>
          <Col xs={24} md={12}><Form.Item name={['hard_gates', 'graduation', 'minimum_reliability_score_exclusive']} label="可靠性分必须大于" rules={[{ required: true }]}><InputNumber min={0} style={{ width: '100%' }} /></Form.Item></Col>
          <Col xs={24} md={12}><Form.Item name={['hard_gates', 'graduation', 'allow_severe_redline']} label="允许存在严重红线记录" valuePropName="checked"><Switch /></Form.Item></Col>
        </Row>
      )}

      <Title level={5}>最终金牌资格硬门槛</Title>
      {isCurrent ? (
        <Card size="small">
          <Row gutter={16}>
            <Col xs={24} md={12}>
              <Form.Item name={['hard_gates', 'gold', 'inherits_graduation']} label="必须先满足出营资格" valuePropName="checked">
                <Switch disabled />
              </Form.Item>
            </Col>
            <Col xs={24} md={12}>
              <Form.Item name={['thresholds', 'gold_raw_score']} label="累计总分下限" rules={[{ required: true }]}>
                <InputNumber disabled min={0} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
          </Row>
          <Text type="secondary">两项必须同时满足：已满足出营资格，且累计总分达到 200。</Text>
        </Card>
      ) : (
        <>
          <Row gutter={16}>
            <Col xs={24} md={12}><Form.Item name={['hard_gates', 'gold', 'required_base_score']} label="基础分要求" rules={[{ required: true }]}><InputNumber min={0} style={{ width: '100%' }} /></Form.Item></Col>
            <Col xs={24} md={12}><Form.Item name={['hard_gates', 'gold', 'minimum_completed_lessons']} label="30 天完课量要求" rules={[{ required: true }]}><InputNumber min={0} style={{ width: '100%' }} /></Form.Item></Col>
            <Col xs={24} md={12}><Form.Item name={['hard_gates', 'gold', 'minimum_user_feedback_score']} label="用户反馈分要求" rules={[{ required: true }]}><InputNumber min={0} style={{ width: '100%' }} /></Form.Item></Col>
            <Col xs={24} md={12}><Form.Item name={['hard_gates', 'gold', 'maximum_late_count']} label="最多迟到次数" rules={[{ required: true }]}><InputNumber min={0} style={{ width: '100%' }} /></Form.Item></Col>
            <Col xs={24} md={12}><Form.Item name={['hard_gates', 'gold', 'maximum_early_count']} label="最多早退次数" rules={[{ required: true }]}><InputNumber min={0} style={{ width: '100%' }} /></Form.Item></Col>
            <Col xs={24} md={12}><Form.Item name={['hard_gates', 'gold', 'maximum_real_absent_count']} label="最多真实缺席次数" rules={[{ required: true }]}><InputNumber min={0} style={{ width: '100%' }} /></Form.Item></Col>
          </Row>
          <Text type="secondary">历史金牌规则仅供回读，不参与当前资格计算。</Text>
        </>
      )}
    </Space>
  )
}


function AgentForm({ payload }: { payload: AgentPolicyPayload }) {
  const effective = agentEffectivelyEnabled(payload)
  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Alert
        type={payload.kill_switch ? 'error' : effective ? 'success' : 'warning'}
        showIcon
        message={payload.kill_switch ? '紧急熔断已打开：Agent 实际不会运行' : effective ? 'Agent 将按受控策略运行' : 'Agent 已停用'}
        description="kill switch 优先级高于 enabled。无论选择哪个 provider，候选只能来自已发布任务模板。"
      />
      <Row gutter={16}>
        <Col xs={24} md={12}><Form.Item name="enabled" label="启用 Agent" valuePropName="checked"><Switch /></Form.Item></Col>
        <Col xs={24} md={12}><Form.Item name="kill_switch" label="紧急熔断" valuePropName="checked"><Switch /></Form.Item></Col>
        <Col xs={24} md={12}><Form.Item name="max_primary_tasks" label="主任务上限"><InputNumber disabled style={{ width: '100%' }} /></Form.Item></Col>
        <Col xs={24} md={12}><Form.Item name="max_secondary_tasks" label="次任务上限" rules={[{ required: true }]}><InputNumber min={0} max={2} style={{ width: '100%' }} /></Form.Item></Col>
        <Col xs={24} md={12}>
          <Form.Item name="provider" label="决策提供方" rules={[{ required: true }]}>
            <Select options={[{ value: 'deterministic', label: '确定性规划器' }, { value: 'openai', label: 'OpenAI' }]} />
          </Form.Item>
        </Col>
        <Col xs={24} md={12}><Form.Item name="model" label="模型" rules={[{ required: true }]}><Input maxLength={128} /></Form.Item></Col>
      </Row>
      <Card size="small" className="config-lock-card">
        <Flex gap={10} align="center"><LockOutlined /><div><Text strong>自由发明任务：永久禁止</Text><Text type="secondary">主任务固定最多 1 个，次任务最多 2 个；这两条由服务端强制校验，页面不能放开。</Text></div></Flex>
      </Card>
      <Form.Item name="allow_task_invention" hidden><Input /></Form.Item>
    </Space>
  )
}


function DeliveryForm() {
  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Alert type="info" showIcon message="提醒只是 DELIVERY_INTENT 的展示/投递形态，不会成为新的业务输出类型，也不会绕过人工审批。" />
      <Row gutter={16}>
        <Col xs={24} md={12}>
          <Form.Item name="normal_reminder_minutes_before_due" label="普通任务提醒提前量（分钟）" rules={[{ required: true }]}>
            <InputNumber min={1} max={10080} style={{ width: '100%' }} />
          </Form.Item>
        </Col>
        <Col xs={24} md={12}>
          <Form.Item name="urgent_reminder_minutes_before_due" label="紧急任务提醒提前量（分钟）" rules={[{ required: true }]}>
            <InputNumber min={1} max={1440} style={{ width: '100%' }} />
          </Form.Item>
        </Col>
        <Col xs={24} md={12}>
          <Form.Item name="p0_response_window_minutes" label="P0 回复时限（分钟）" rules={[{ required: true }]}>
            <InputNumber min={1} max={1440} style={{ width: '100%' }} />
          </Form.Item>
        </Col>
        <Col xs={24} md={12}>
          <Form.Item name="p0_reminder_minutes_before_response_due" label="P0 到期前提醒（分钟）" rules={[{ required: true }]}>
            <InputNumber min={1} max={1440} style={{ width: '100%' }} />
          </Form.Item>
        </Col>
      </Row>
      <Text type="secondary">紧急任务提醒提前量不得大于普通任务；P0 提醒提前量必须小于 P0 回复时限。</Text>
    </Space>
  )
}


export default function ConfigCenter() {
  const { message } = AntdApp.useApp()
  const [form] = Form.useForm<ConfigFormValues>()
  const [activeKey, setActiveKey] = useState<ConfigKey>('SCORE_GRADUATION')
  const [versionsByKey, setVersionsByKey] = useState<Partial<Record<ConfigKey, ConfigVersion[]>>>({})
  const [selectedIdByKey, setSelectedIdByKey] = useState<Partial<Record<ConfigKey, string>>>({})
  const [operator, setOperator] = useState<OperatorIdentity>()
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [loadError, setLoadError] = useState<string>()

  const versions = versionsByKey[activeKey] ?? []
  const selectedId = selectedIdByKey[activeKey]
  const hasLoaded = versionsByKey[activeKey] !== undefined
  const selected = versions.find((item) => item.version_id === selectedId)
  const published = versions.find((item) => item.status === 'PUBLISHED')
  const canManage = operator?.roles.includes('CONFIG_PUBLISHER') ?? false
  const selectedUsesReadOnlyScorePolicy = activeKey === 'SCORE_GRADUATION'
    && Boolean(selected)
    && !isScoreGraduationV6(selected?.payload)

  const loadVersions = useCallback(async (key: ConfigKey, preferredId?: string) => {
    setLoading(true)
    setLoadError(undefined)
    try {
      const [items, identity] = await Promise.all([
        configRequest<ConfigVersion[]>(`/api/configs?key=${key}`),
        configRequest<OperatorIdentity>('/api/auth/me'),
      ])
      const currentItems = currentConfigurations(items)
      setVersionsByKey((current) => ({ ...current, [key]: currentItems }))
      setOperator(identity)
      const nextId = preferredId && currentItems.some((item) => item.version_id === preferredId)
        ? preferredId
        : currentItems.find((item) => item.status === 'DRAFT')?.version_id
          ?? currentItems.find((item) => item.status === 'VALIDATED')?.version_id
          ?? currentItems.find((item) => item.status === 'PUBLISHED')?.version_id
          ?? currentItems[0]?.version_id
      setSelectedIdByKey((current) => ({ ...current, [key]: nextId }))
    } catch (error) {
      setVersionsByKey((current) => ({ ...current, [key]: [] }))
      setSelectedIdByKey((current) => ({ ...current, [key]: undefined }))
      setLoadError(error instanceof Error ? error.message : '配置加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    form.resetFields()
    if (selected) form.setFieldsValue(selected.payload as unknown as ConfigFormValues)
  }, [activeKey, form, selected])

  const watchedPayload = Form.useWatch([], form) as unknown as ConfigPayload | undefined
  const agentPayload = activeKey === 'AGENT_POLICY' && watchedPayload
    ? watchedPayload as AgentPolicyPayload
    : selected?.payload as AgentPolicyPayload | undefined

  const selectedDescription = useMemo(() => CONFIG_DOMAIN_META[activeKey], [activeKey])

  async function createDraft() {
    setSaving(true)
    try {
      const created = await configRequest<ConfigVersion>('/api/configs/drafts', {
        method: 'POST',
        body: JSON.stringify({ config_key: activeKey, ...(published ? { from_version_id: published.version_id } : {}) }),
      })
      message.success('已创建新草稿')
      await loadVersions(activeKey, created.version_id)
    } catch (error) {
      message.error(error instanceof Error ? error.message : '新建草稿失败')
    } finally {
      setSaving(false)
    }
  }

  async function saveDraft() {
    if (!selected) return
    setSaving(true)
    try {
      await form.validateFields()
      const payload = form.getFieldsValue() as unknown as ConfigPayload
      const updated = await configRequest<ConfigVersion>(`/api/configs/${selected.version_id}`, {
        method: 'PATCH',
        body: JSON.stringify({ payload }),
      })
      message.success('草稿已保存')
      await loadVersions(activeKey, updated.version_id)
    } catch (error) {
      if (error instanceof ConfigApiError) message.error(error.message)
    } finally {
      setSaving(false)
    }
  }

  async function validateDraft() {
    if (!selected) return
    setSaving(true)
    try {
      await form.validateFields()
      const payload = form.getFieldsValue() as unknown as ConfigPayload
      await configRequest<ConfigVersion>(`/api/configs/${selected.version_id}`, {
        method: 'PATCH',
        body: JSON.stringify({ payload }),
      })
      const result = await configRequest<{ valid: boolean; errors: Array<{ message: string }>; version: ConfigVersion }>(
        `/api/configs/${selected.version_id}/validate`,
        { method: 'POST' },
      )
      if (!result.valid) {
        message.error(result.errors.map((item) => item.message).join('；'))
      } else {
        message.success('服务端校验通过，等待另一位配置发布人发布')
      }
      await loadVersions(activeKey, selected.version_id)
    } catch (error) {
      if (error instanceof ConfigApiError) message.error(error.message)
    } finally {
      setSaving(false)
    }
  }

  async function publishVersion() {
    if (!selected) return
    setSaving(true)
    try {
      const result = await configRequest<ConfigVersion>(`/api/configs/${selected.version_id}/publish`, { method: 'POST' })
      message.success('配置已发布，已成为当前规则')
      await loadVersions(activeKey, result.version_id)
    } catch (error) {
      message.error(error instanceof Error ? error.message : '发布失败')
    } finally {
      setSaving(false)
    }
  }

  const publishDisabledReason = selected?.status === 'VALIDATED' && selected.high_impact && operator?.operator_id === selected.created_by
    ? '你是该草稿创建人，必须由另一位配置发布人操作'
    : undefined

  return (
    <div className="page-shell">
      <PageHeader
        eyebrow="任务与规则"
        title="积分与门槛"
        description="管理会直接影响积分、出营资格与任务提醒的当前规则。所有调整都会留痕，并在发布后生效。"
        actions={<Button icon={<ReloadOutlined />} loading={loading} onClick={() => loadVersions(activeKey)}>更新配置</Button>}
      />
      <Alert
        type="warning"
        showIcon
        icon={<SafetyCertificateOutlined />}
        message="高影响配置：创建人与发布人必须不同"
        description="页面不会提交 actor_id；操作者来自服务端已登录会话。修改会留审计，需新建编辑草稿，发布后成为当前规则。"
      />

      <Card className="config-domain-card">
        <Tabs
          activeKey={activeKey}
          onChange={(key) => {
            setActiveKey(key as ConfigKey)
            setLoadError(undefined)
          }}
          items={CONFIG_KEYS.map((key) => ({
            key,
            label: CONFIG_DOMAIN_META[key].title,
            children: <Text type="secondary">{CONFIG_DOMAIN_META[key].description}</Text>,
          }))}
        />
      </Card>

      {loadError ? <Alert type="error" showIcon message="配置中心暂不可用" description={loadError} /> : null}

      <Row gutter={[18, 18]} align="stretch">
        <Col xs={24} xl={8}>
          <Card
            className="config-current-card"
            title="当前配置"
            extra={canManage ? <Button type="primary" onClick={createDraft} loading={saving} disabled={!published}>新建草稿</Button> : null}
          >
            {!hasLoaded ? (
              <Alert type="info" showIcon message="尚未读取配置" description="点击“更新配置”读取当前类别；切换类别不会自动请求数据。" />
            ) : !published && !loading && !loadError ? (
              <Alert type="warning" showIcon message="尚无默认配置" description="请先显式运行配置 seed；空库读取不会自动创建配置。" />
            ) : null}
            <Table
              size="small"
              loading={loading}
              rowKey="version_id"
              dataSource={versions}
              pagination={false}
              rowClassName={(record) => record.version_id === selectedId ? 'config-current-selected' : ''}
              onRow={(record) => ({ onClick: () => setSelectedIdByKey((current) => ({ ...current, [activeKey]: record.version_id })) })}
              locale={{ emptyText: hasLoaded ? '当前类别暂无配置' : '等待主动更新' }}
              columns={[
                { title: '状态', dataIndex: 'status', render: (value) => <Tag color={configStatusColor(value)}>{currentConfigStatusLabel(value)}</Tag> },
                { title: '操作人', key: 'operator', ellipsis: true, render: (_, record) => currentConfigOperator(record) },
              ]}
            />
          </Card>
        </Col>

        <Col xs={24} xl={16}>
          <Card
            className="config-editor-card"
            title={selectedDescription.title}
            extra={selected ? <Tag color={configStatusColor(selected.status)}>{currentConfigStatusLabel(selected.status)}</Tag> : null}
          >
            {!selected ? <Empty description={hasLoaded ? '选择当前配置或编辑草稿' : '点击“更新配置”读取当前类别'} /> : (
              <>
                <Descriptions size="small" bordered column={{ xs: 1, md: 2 }} className="config-version-meta">
                  <Descriptions.Item label="创建人">{configActorLabel(selected.created_by)}</Descriptions.Item>
                  <Descriptions.Item label="校验人">{configActorLabel(selected.validated_by)}</Descriptions.Item>
                  <Descriptions.Item label="发布人">{configActorLabel(selected.published_by)}</Descriptions.Item>
                  <Descriptions.Item label="最后操作人">{configActorLabel(selected.updated_by)}</Descriptions.Item>
                </Descriptions>

                {selected.validation_errors.length ? (
                  <Alert
                    type="error"
                    showIcon
                    message="服务端校验未通过"
                    description={selected.validation_errors.map((item) => `${item.path || '配置'}：${item.message}`).join('；')}
                  />
                ) : null}

                <Form
                  form={form}
                  layout="vertical"
                  disabled={!canManage || !canEditConfiguration(selected) || (activeKey === 'SCORE_GRADUATION' && !isScoreGraduationV6(selected.payload))}
                  className="config-form"
                >
                  {activeKey === 'SCORE_GRADUATION' ? <ScoreForm payload={selected.payload} /> : null}
                  {activeKey === 'AGENT_POLICY' && agentPayload ? <AgentForm payload={agentPayload} /> : null}
                  {activeKey === 'DELIVERY_POLICY' ? <DeliveryForm /> : null}
                </Form>

                {canManage ? (
                  <Flex gap={10} justify="flex-end" wrap="wrap" className="config-actions">
                    <Button icon={<EditOutlined />} disabled={!canEditConfiguration(selected) || selectedUsesReadOnlyScorePolicy} loading={saving} onClick={saveDraft}>保存草稿</Button>
                    <Button icon={<CheckCircleOutlined />} disabled={!canValidateConfiguration(selected) || selectedUsesReadOnlyScorePolicy} loading={saving} onClick={validateDraft}>保存并校验</Button>
                    <Tooltip title={publishDisabledReason}>
                      <span><Button type="primary" icon={<SafetyCertificateOutlined />} disabled={!canPublishConfiguration(selected, operator?.operator_id)} loading={saving} onClick={publishVersion}>双人发布</Button></span>
                    </Tooltip>
                  </Flex>
                ) : <Alert type="info" showIcon message="当前账号为只读权限" description="只有 CONFIG_PUBLISHER 可以创建、校验和发布配置。" />}
              </>
            )}
          </Card>
        </Col>
      </Row>

      <Card size="small">
        <Title level={5}>当前登录身份</Title>
        <Paragraph type="secondary">
          {operator ? `${operator.display_name ?? operator.username} · ${operator.operator_id} · ${operator.roles.join(' / ')}` : '未读取到登录身份'}
        </Paragraph>
      </Card>
    </div>
  )
}
