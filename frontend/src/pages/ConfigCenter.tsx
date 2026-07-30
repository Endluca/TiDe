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
  isConfigurationFormEditable,
  isLegacyScoreGraduation,
  isScoreGraduationV3,
  isScoreGraduationV4,
  isScoreGraduationV5,
  isScoreGraduationV6,
  isScoreGraduationV7,
  isScoreGraduationV8,
  isScoreGraduationV9,
  isScoreGraduationV10,
  isScoreGraduationV1,
  type AgentPolicyPayload,
  type ConfigKey,
  type ConfigPayload,
  type ConfigVersion,
  type ScoreGraduationPayload,
} from '../configCenter'
import { ApiError, apiErrorMessage, isRequestCancelled, request } from '../api'
import { PageHeader } from '../components/Common'
import { useI18n } from '../i18n'
import { useLatestRequest } from '../useLatestRequest'


const { Paragraph, Text, Title } = Typography
type ConfigFormValues = Record<string, object | string | number | boolean | undefined>


function ScoreForm({ payload, editable }: { payload: unknown; editable: boolean }) {
  const { t } = useI18n()
  const legacy = isLegacyScoreGraduation(payload) || isScoreGraduationV3(payload) || isScoreGraduationV4(payload) || isScoreGraduationV5(payload)
  const isV4 = isScoreGraduationV4(payload)
  const isV5 = isScoreGraduationV5(payload)
  const isV8 = isScoreGraduationV8(payload)
  const isV9 = isScoreGraduationV9(payload)
  const isV10 = isScoreGraduationV10(payload)
  const isV1 = isScoreGraduationV1(payload)
  const isCurrentReliability = isV1 || isV9 || isV10
  const hasHardwareQuality = isV1
  const isCurrent = isScoreGraduationV6(payload) || isScoreGraduationV7(payload) || isV8 || isCurrentReliability
  const classroomQualityPoints = isCurrent && (!isCurrentReliability || hasHardwareQuality)
    ? (payload as ScoreGraduationPayload).scoring_items.classroom_quality?.points_per_unit
    : undefined
  const hasSupplyMilestone = isV4 || isV5 || isCurrent
  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Alert
        type={isCurrent ? 'info' : 'warning'}
        showIcon
        message={isCurrentReliability ? t('当前可靠性、课堂质量和供给积分均由真实指标结算。', 'Reliability, classroom quality, and Peak availability are settled from real metrics.') : isCurrent ? t('当前课堂质量和供给积分均由真实指标结算。', 'Classroom quality and Peak availability are settled from real metrics.') : legacy ? t('该计分版本已停用，仅供读取。', 'This scoring version is retired and read-only.') : t('该草稿的积分结构不完整，暂不可编辑。', 'This draft has an incomplete scoring structure and cannot be edited.')}
        description={isCurrentReliability
          ? hasHardwareQuality
            ? t('可靠性分 = perfect_cnt × 4 + peak_completed_cnt × 2；每节课未开摄像头、CPU 占用过高、网络延迟过高均明确为 0 时，硬件质量加 2 分。任一字段缺失不加分。15 日复约仅保留业务事实，不参与积分。', 'Reliability = perfect_cnt × 4 + peak_completed_cnt × 2. A lesson earns 2 hardware-quality points only when camera-off, high CPU usage, and high network latency are all explicitly 0. Missing evidence awards 0 points. 15-day rebooking remains a business fact and awards no points.')
            : t('可靠性分 = perfect_cnt × 4 + peak_completed_cnt × 2；课堂质量维度保留为 0 分。15 日复约仅保留业务事实，不参与积分。', 'Reliability = perfect_cnt × 4 + peak_completed_cnt × 2. Classroom quality remains 0. 15-day rebooking remains a business fact and awards no points.')
          : isCurrent
            ? t(`课堂质量分 = perfect_cnt × ${classroomQualityPoints}；peak_slot_cnt 达到 40 时供给维度获得 10 分。${isV8 ? '15 日复约仅保留业务事实，不参与积分。' : ''}其余原始分仍无封顶，对外显示分 = min(raw, 200)。`, `Classroom quality = perfect_cnt × ${classroomQualityPoints}; Peak availability awards 10 points when peak_slot_cnt reaches 40. ${isV8 ? '15-day rebooking remains a business fact and awards no points. ' : ''}Other raw scores are uncapped; displayed score = min(raw, 200).`)
            : t('页面不会静默改写已保存规则；如需调整，请从当前已发布版本新建草稿。', 'Saved rules are never silently rewritten. Create a draft from the published version to make changes.')}
      />
      <Row gutter={16}>
        <Form.Item name="policy_version" hidden rules={[{ required: true }]}><Input /></Form.Item>
        <Col xs={24} md={8}>
          <Form.Item name="graduation_effect" label={t('出营生效方式', 'Graduation effect')} rules={[{ required: true }]}>
            <Input disabled />
          </Form.Item>
        </Col>
      </Row>

      {hasSupplyMilestone ? (
        <Card size="small" title={t('供给积分里程碑', 'Peak Availability Milestone')} extra={<Tag color="cyan">{t('由指标系统结算', 'Metric-system settlement')}</Tag>}>
          <Row gutter={16}>
            <Col xs={24} md={8}>
              <Form.Item name={['scoring_items', 'capacity', 'milestone_id']} label={t('里程碑 ID', 'Milestone ID')} rules={[{ required: true }]}>
                <Input disabled />
              </Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Form.Item name={['scoring_items', 'capacity', 'metric']} label={t('指标字段', 'Metric field')} rules={[{ required: true }]}>
                <Input disabled />
              </Form.Item>
            </Col>
            <Col xs={24} md={4}>
              <Form.Item name={['scoring_items', 'capacity', 'operator']} label={t('比较符', 'Operator')} rules={[{ required: true }]}>
                <Select disabled options={[{ value: 'GTE', label: '≥' }]} />
              </Form.Item>
            </Col>
            <Col xs={24} md={4}>
              <Form.Item name={['scoring_items', 'capacity', 'threshold']} label={t('达标阈值', 'Threshold')} rules={[{ required: true }]}>
                <InputNumber disabled={!editable} min={0} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col xs={24} md={4}>
              <Form.Item name={['scoring_items', 'capacity', 'score_value']} label={t('达标得分', 'Points awarded')} rules={[{ required: true }]}>
                <InputNumber disabled={!editable} min={0} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col xs={24} md={4}>
              <Form.Item name={['scoring_items', 'capacity', 'maximum_points']} label={t('维度上限', 'Dimension maximum')} rules={[{ required: true }]}>
                <InputNumber disabled={!editable} min={0} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col span={24}>
              <Form.Item name={['scoring_items', 'capacity', 'settlement_mode']} label={t('结算方式', 'Settlement mode')} rules={[{ required: true }]}>
                <Select disabled options={[{ value: 'FIRST_ACHIEVEMENT_LOCKED', label: t('首次达成即锁定，10 分永久保留', 'Lock at first achievement; retain 10 points permanently') }]} />
              </Form.Item>
            </Col>
          </Row>
          <Text type="secondary">{t('唯一结算公式：peak_slot_cnt 首次达到 40 → 供给分 10/10，并永久保留；达成前为 0/10。任务状态只用于改善闭环，不能代替该指标。', 'Settlement rule: when peak_slot_cnt first reaches 40, Peak availability becomes 10/10 and is retained permanently; before that it is 0/10. Task status supports the improvement loop but cannot replace this metric.')}</Text>
        </Card>
      ) : null}

      <Title level={5}>{t('计分项', 'Scoring Items')}</Title>
      <div className="config-dimension-grid config-dimension-header">
        <Text strong>{t('业务计分项', 'Scoring item')}</Text><Text strong>{t('计分方式', 'Method')}</Text><Text strong>{t('分值', 'Points')}</Text><Text strong>{t('数据说明', 'Data notes')}</Text>
      </div>
      {SCORING_ITEM_META.filter((item) => (
        !(hasSupplyMilestone && item.key === 'capacity')
        && !((isV8 || isCurrentReliability) && item.key === 'feedback_rebook_15d')
        && !(isCurrentReliability && item.key === 'reliability_on_time')
        && !(!isCurrentReliability && item.key === 'reliability_perfect')
        && !(isCurrentReliability && !hasHardwareQuality && item.key === 'classroom_quality')
      )).map((item) => (
        <div className="config-dimension-grid" key={item.key}>
          <div><Text strong>{item.key === 'new_teacher_tasks' ? t('成长任务（必修）', 'Mandatory growth tasks') : item.key === 'feedback_praise' ? t('用户反馈 · 好评', 'User feedback · Praise') : item.key === 'feedback_favorite' ? t('用户反馈 · 收藏', 'User feedback · Favorite') : item.key === 'feedback_rebook_15d' ? t('用户反馈 · 15 日复约', 'User feedback · 15-day rebooking') : item.key === 'reliability_perfect' ? t('可靠性 · 完美完课', 'Reliability · Perfect completion') : item.key === 'reliability_peak' ? t('可靠性 · Peak 完课', 'Reliability · Peak completion') : hasHardwareQuality && item.key === 'classroom_quality' ? t('课堂质量 · 硬件质量', 'Classroom quality · Hardware quality') : isCurrent && item.key === 'classroom_quality' ? t('课堂质量 · 完美完课', 'Classroom quality · Perfect completion') : item.label}</Text><Text type="secondary">{item.key}</Text></div>
          <Text>
            {hasHardwareQuality && item.key === 'classroom_quality'
              ? t('按逐课硬件质量结果累加', 'Accumulate by lesson hardware-quality results')
              : isCurrent && item.key === 'classroom_quality'
                ? t('按 perfect_cnt 累加', 'Accumulate by perfect_cnt')
              : isCurrent && item.key === 'new_teacher_tasks'
                ? t('按必修任务完成状态累加', 'Accumulate completed mandatory-task points')
                : item.field === 'points_per_unit'
                  ? t('按事件数量累加', 'Accumulate by event count')
                  : t('基础项可得上限', 'Maximum base points')}
          </Text>
          <Form.Item name={['scoring_items', item.key, item.field]} rules={[{ required: true }]} noStyle>
            <InputNumber
              disabled={!editable || item.key === 'new_teacher_tasks'}
              min={0.01}
              step={isCurrent && item.key === 'classroom_quality' ? 0.1 : 1}
              style={{ width: '100%' }}
              aria-label={`${item.label}（${item.field === 'maximum_points' ? '分' : `分 / ${item.unit}`}）`}
            />
          </Form.Item>
          <Text type="secondary">
            {item.key === 'classroom_quality'
              ? hasHardwareQuality
                ? t('三个课堂异常字段均明确为 0 时，每节加 2 分', 'Each lesson earns 2 points when all three hardware anomaly fields are explicitly 0')
                : isCurrent ? t(`教师宽表 perfect_cnt × ${classroomQualityPoints}`, `Teacher snapshot perfect_cnt × ${classroomQualityPoints}`) : t('历史版本按替代达成率计算', 'Legacy version uses an alternative achievement rate')
              : isCurrent && item.key === 'new_teacher_tasks'
                ? t('由 G01–G09 已发布任务分值合计，不能在这里单独修改', 'Calculated from published G01–G09 task points and cannot be edited here')
                : item.field === 'maximum_points'
                  ? t('仅限制该基础项；课程分累计不受影响', 'Caps only this base item; lesson score accumulation is unaffected')
                  : t('真实事件为 0 时得分为 0', '0 verified events result in 0 points')}
          </Text>
        </div>
      ))}

      {isCurrent && (!isCurrentReliability || hasHardwareQuality) ? (
        <Row gutter={16}>
          <Col xs={24} md={12}>
            <Form.Item name={['scoring_items', 'classroom_quality', 'metric']} label={t('课堂质量指标字段', 'Classroom-quality metric')} rules={[{ required: true }]}>
              <Input disabled />
            </Form.Item>
          </Col>
          <Col xs={24} md={12}>
            <Form.Item name={['scoring_items', 'classroom_quality', 'source_mode']} label={t('课堂质量来源', 'Classroom-quality source')} rules={[{ required: true }]}>
              <Select disabled options={hasHardwareQuality
                ? [{ value: 'REAL_LESSON_FACTS', label: t('课程事实表', 'Lesson facts') }]
                : [{ value: 'REAL_TEACHER_SNAPSHOT', label: t('教师指标快照', 'Teacher metric snapshot') }]} />
            </Form.Item>
          </Col>
        </Row>
      ) : isCurrentReliability ? (
        <Alert
          type="info"
          showIcon
          message={t('课堂质量当前无加分项', 'Classroom quality currently awards no points')}
          description={t('该维度继续保留并对外返回 0 分；不是源数据缺失。后续补充明确的计分项后再发布新版本。', 'The dimension remains available and returns 0 points. This is not missing source data. Publish a new version after a defined scoring item is added.')}
        />
      ) : (
        <Row gutter={16}>
          <Col xs={24} md={12}>
            <Form.Item name={['scoring_items', 'classroom_quality', 'default_achievement_rate']} label={t('课堂质量默认无问题达成率', 'Default classroom-quality achievement rate')} rules={[{ required: true }]}>
              <InputNumber min={0} max={1} step={0.05} precision={2} style={{ width: '100%' }} />
            </Form.Item>
          </Col>
          <Col xs={24} md={12}>
            <Form.Item name={['scoring_items', 'classroom_quality', 'source_mode']} label={t('课堂质量来源模式', 'Classroom-quality source mode')} rules={[{ required: true }]}>
              <Select disabled options={[{ value: 'MOCK_SIMULATION', label: t('历史替代达成率', 'Legacy proxy achievement rate') }]} />
            </Form.Item>
          </Col>
        </Row>
      )}

      <Title level={5}>{t('分数线与对外显示封顶', 'Score Thresholds & Display Cap')}</Title>
      <Row gutter={16}>
        {!isCurrent ? <Col xs={24} md={12}><Form.Item name={['thresholds', 'graduation_raw_score']} label={t('出营累计分数线', 'Graduation total-score threshold')} rules={[{ required: true }]}><InputNumber min={0.01} style={{ width: '100%' }} /></Form.Item></Col> : null}
        <Col xs={24} md={12}><Form.Item name={['thresholds', 'graduation_external_score']} label={t('出营显示分', 'Graduation display score')} rules={[{ required: true }]}><InputNumber disabled min={0.01} max={200} style={{ width: '100%' }} /></Form.Item></Col>
        <Col xs={24} md={12}><Form.Item name={['thresholds', 'gold_raw_score']} label={t('金牌累计分数线', 'Gold total-score threshold')} rules={[{ required: true }]}><InputNumber disabled={!editable} min={0.01} style={{ width: '100%' }} /></Form.Item></Col>
        <Col xs={24} md={12}><Form.Item name={['thresholds', 'gold_external_score']} label={t('对外显示封顶', 'Display cap')} rules={[{ required: true }]}><InputNumber disabled min={0.01} max={200} style={{ width: '100%' }} /></Form.Item></Col>
      </Row>

      <Title level={5}>{t('最终出营资格硬门槛', 'Graduation Qualification Thresholds')}</Title>
      {isCurrent ? (
        <Card size="small">
          <Row gutter={16}>
            <Col xs={24} md={8}>
              <Form.Item name={['hard_gates', 'graduation', 'required_mandatory_task_count']} label={t('必修成长任务完成数', 'Completed mandatory tasks')} rules={[{ required: true }]}>
                <InputNumber disabled min={0} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Form.Item name={['hard_gates', 'graduation', 'maximum_l0_complaint_count']} label={t('L0 投诉数上限', 'Maximum L0 complaints')} rules={[{ required: true }]}>
                <InputNumber disabled={!editable} min={0} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Form.Item name={['thresholds', 'graduation_raw_score']} label={t('累计总分下限', 'Minimum total score')} rules={[{ required: true }]}>
                <InputNumber disabled={!editable} min={0} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
          </Row>
          <Text type="secondary">{t('三项必须同时满足：当前 9 项必修成长任务全部完成（30 分）、L0 投诉数为 0、累计总分达到 100。', 'All three must be met: all 9 mandatory growth tasks completed (30 points), 0 L0 complaints, and total score at least 100.')}</Text>
        </Card>
      ) : (
        <Row gutter={16}>
          <Col xs={24} md={12}><Form.Item name={['hard_gates', 'graduation', 'minimum_base_score']} label={t('基础分要求', 'Base-score requirement')} rules={[{ required: true }]}><InputNumber min={0} style={{ width: '100%' }} /></Form.Item></Col>
          <Col xs={24} md={12}><Form.Item name={['hard_gates', 'graduation', 'minimum_completed_lessons']} label={t('30 天完课量要求', '30-day completed-lesson requirement')} rules={[{ required: true }]}><InputNumber min={0} style={{ width: '100%' }} /></Form.Item></Col>
          <Col xs={24} md={12}><Form.Item name={['hard_gates', 'graduation', 'minimum_user_feedback_score_exclusive']} label={t('用户反馈分必须大于', 'User-feedback score must exceed')} rules={[{ required: true }]}><InputNumber min={0} style={{ width: '100%' }} /></Form.Item></Col>
          <Col xs={24} md={12}><Form.Item name={['hard_gates', 'graduation', 'minimum_reliability_score_exclusive']} label={t('可靠性分必须大于', 'Reliability score must exceed')} rules={[{ required: true }]}><InputNumber min={0} style={{ width: '100%' }} /></Form.Item></Col>
          <Col xs={24} md={12}><Form.Item name={['hard_gates', 'graduation', 'allow_severe_redline']} label={t('允许存在严重红线记录', 'Allow severe red-line records')} valuePropName="checked"><Switch /></Form.Item></Col>
        </Row>
      )}

      <Title level={5}>{t('最终金牌资格硬门槛', 'Gold Qualification Thresholds')}</Title>
      {isCurrent ? (
        <Card size="small">
          <Row gutter={16}>
            <Col xs={24} md={12}>
              <Form.Item name={['hard_gates', 'gold', 'inherits_graduation']} label={t('必须先满足出营资格', 'Graduation qualification required')} valuePropName="checked">
                <Switch disabled />
              </Form.Item>
            </Col>
            <Col xs={24} md={12}>
              <Form.Item name={['thresholds', 'gold_raw_score']} label={t('累计总分下限', 'Minimum total score')} rules={[{ required: true }]}>
                <InputNumber disabled min={0} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            {isCurrentReliability ? (
              <>
                <Col xs={24} md={8}>
                  <Form.Item name={['hard_gates', 'gold', 'maximum_late_count']} label={t('迟到次数上限', 'Maximum late count')} rules={[{ required: true }]}>
                    <InputNumber disabled={!editable} min={0} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
                <Col xs={24} md={8}>
                  <Form.Item name={['hard_gates', 'gold', 'maximum_early_count']} label={t('早退次数上限', 'Maximum early-leave count')} rules={[{ required: true }]}>
                    <InputNumber disabled={!editable} min={0} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
                <Col xs={24} md={8}>
                  <Form.Item name={['hard_gates', 'gold', 'maximum_absent_count']} label={t('缺席次数上限', 'Maximum absence count')} rules={[{ required: true }]}>
                    <InputNumber disabled={!editable} min={0} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
              </>
            ) : null}
          </Row>
          <Text type="secondary">
            {isCurrentReliability
              ? t('五项必须同时满足：已满足出营资格、累计总分达到 200、迟到不超过 1 次、早退 0 次、缺席 0 次。', 'All five must be met: graduation qualified, total score at least 200, no more than 1 late arrival, 0 early leaves, and 0 absences.')
              : t('两项必须同时满足：已满足出营资格，且累计总分达到 200。', 'Both must be met: graduation qualified and total score at least 200.')}
          </Text>
        </Card>
      ) : (
        <>
          <Row gutter={16}>
            <Col xs={24} md={12}><Form.Item name={['hard_gates', 'gold', 'required_base_score']} label={t('基础分要求', 'Base-score requirement')} rules={[{ required: true }]}><InputNumber min={0} style={{ width: '100%' }} /></Form.Item></Col>
            <Col xs={24} md={12}><Form.Item name={['hard_gates', 'gold', 'minimum_completed_lessons']} label={t('30 天完课量要求', '30-day completed-lesson requirement')} rules={[{ required: true }]}><InputNumber min={0} style={{ width: '100%' }} /></Form.Item></Col>
            <Col xs={24} md={12}><Form.Item name={['hard_gates', 'gold', 'minimum_user_feedback_score']} label={t('用户反馈分要求', 'User-feedback score requirement')} rules={[{ required: true }]}><InputNumber min={0} style={{ width: '100%' }} /></Form.Item></Col>
            <Col xs={24} md={12}><Form.Item name={['hard_gates', 'gold', 'maximum_late_count']} label={t('最多迟到次数', 'Maximum late count')} rules={[{ required: true }]}><InputNumber min={0} style={{ width: '100%' }} /></Form.Item></Col>
            <Col xs={24} md={12}><Form.Item name={['hard_gates', 'gold', 'maximum_early_count']} label={t('最多早退次数', 'Maximum early-leave count')} rules={[{ required: true }]}><InputNumber min={0} style={{ width: '100%' }} /></Form.Item></Col>
            <Col xs={24} md={12}><Form.Item name={['hard_gates', 'gold', 'maximum_real_absent_count']} label={t('最多真实缺席次数', 'Maximum real absence count')} rules={[{ required: true }]}><InputNumber min={0} style={{ width: '100%' }} /></Form.Item></Col>
          </Row>
          <Text type="secondary">{t('历史金牌规则仅供回读，不参与当前资格计算。', 'Legacy Gold rules are read-only and do not participate in current qualification calculations.')}</Text>
        </>
      )}
    </Space>
  )
}


function AgentForm({ payload }: { payload: AgentPolicyPayload }) {
  const { t } = useI18n()
  const effective = agentEffectivelyEnabled(payload)
  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Alert
        type={payload.kill_switch ? 'error' : effective ? 'success' : 'warning'}
        showIcon
        message={payload.kill_switch ? t('紧急熔断已打开：Agent 实际不会运行', 'Emergency kill switch is on: the Agent will not run') : effective ? t('Agent 将按受控策略运行', 'The Agent will run under the controlled policy') : t('Agent 已停用', 'The Agent is disabled')}
        description={t('kill switch 优先级高于 enabled。无论选择哪个 provider，候选只能来自已发布任务模板。', 'The kill switch overrides enabled. Regardless of provider, candidates must come from published task templates.')}
      />
      <Row gutter={16}>
        <Col xs={24} md={12}><Form.Item name="enabled" label={t('启用 Agent', 'Enable Agent')} valuePropName="checked"><Switch /></Form.Item></Col>
        <Col xs={24} md={12}><Form.Item name="kill_switch" label={t('紧急熔断', 'Emergency kill switch')} valuePropName="checked"><Switch /></Form.Item></Col>
        <Col xs={24} md={12}><Form.Item name="max_primary_tasks" label={t('主任务上限', 'Primary-task limit')}><InputNumber disabled style={{ width: '100%' }} /></Form.Item></Col>
        <Col xs={24} md={12}><Form.Item name="max_secondary_tasks" label={t('次任务上限', 'Secondary-task limit')} rules={[{ required: true }]}><InputNumber min={0} max={2} style={{ width: '100%' }} /></Form.Item></Col>
        <Col xs={24} md={12}>
          <Form.Item name="provider" label={t('决策提供方', 'Decision provider')} rules={[{ required: true }]}>
            <Select options={[{ value: 'deterministic', label: t('确定性规划器', 'Deterministic planner') }, { value: 'openai', label: 'OpenAI' }]} />
          </Form.Item>
        </Col>
        <Col xs={24} md={12}><Form.Item name="model" label={t('模型', 'Model')} rules={[{ required: true }]}><Input maxLength={128} /></Form.Item></Col>
      </Row>
      <Card size="small" className="config-lock-card">
        <Flex gap={10} align="center"><LockOutlined /><div><Text strong>{t('自由发明任务：永久禁止', 'Task invention: permanently prohibited')}</Text><Text type="secondary">{t('主任务固定最多 1 个，次任务最多 2 个；这两条由服务端强制校验，页面不能放开。', 'At most 1 primary task and 2 secondary tasks. The server enforces both limits and the UI cannot override them.')}</Text></div></Flex>
      </Card>
      <Form.Item name="allow_task_invention" hidden><Input /></Form.Item>
    </Space>
  )
}


function DeliveryForm() {
  const { t } = useI18n()
  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Alert type="info" showIcon message={t('提醒只是 DELIVERY_INTENT 的展示/投递形态，不会成为新的业务输出类型，也不会绕过人工审批。', 'A reminder is only a presentation/delivery form of DELIVERY_INTENT. It does not create a new business output type or bypass manual approval.')} />
      <Row gutter={16}>
        <Col xs={24} md={12}>
          <Form.Item name="normal_reminder_minutes_before_due" label={t('普通任务提醒提前量（分钟）', 'Normal reminder lead time (minutes)')} rules={[{ required: true }]}>
            <InputNumber min={1} max={10080} style={{ width: '100%' }} />
          </Form.Item>
        </Col>
        <Col xs={24} md={12}>
          <Form.Item name="urgent_reminder_minutes_before_due" label={t('紧急任务提醒提前量（分钟）', 'Urgent reminder lead time (minutes)')} rules={[{ required: true }]}>
            <InputNumber min={1} max={1440} style={{ width: '100%' }} />
          </Form.Item>
        </Col>
        <Col xs={24} md={12}>
          <Form.Item name="p0_response_window_minutes" label={t('P0 回复时限（分钟）', 'P0 response window (minutes)')} rules={[{ required: true }]}>
            <InputNumber min={1} max={1440} style={{ width: '100%' }} />
          </Form.Item>
        </Col>
        <Col xs={24} md={12}>
          <Form.Item name="p0_reminder_minutes_before_response_due" label={t('P0 到期前提醒（分钟）', 'P0 reminder before response due (minutes)')} rules={[{ required: true }]}>
            <InputNumber min={1} max={1440} style={{ width: '100%' }} />
          </Form.Item>
        </Col>
      </Row>
      <Text type="secondary">{t('紧急任务提醒提前量不得大于普通任务；P0 提醒提前量必须小于 P0 回复时限。', 'Urgent reminder lead time cannot exceed the normal reminder lead time. P0 reminder lead time must be shorter than the P0 response window.')}</Text>
    </Space>
  )
}


export default function ConfigCenter({
  active = true,
  operator,
}: {
  active?: boolean
  operator: {
    operator_id: string
    username: string
    display_name?: string | null
    roles: string[]
  }
}) {
  const { locale, t } = useI18n()
  const { message } = AntdApp.useApp()
  const versionRequest = useLatestRequest()
  const [form] = Form.useForm<ConfigFormValues>()
  const [activeKey, setActiveKey] = useState<ConfigKey>('SCORE_GRADUATION')
  const [versionsByKey, setVersionsByKey] = useState<Partial<Record<ConfigKey, ConfigVersion[]>>>({})
  const [selectedIdByKey, setSelectedIdByKey] = useState<Partial<Record<ConfigKey, string>>>({})
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
    && !isScoreGraduationV1(selected?.payload)
    && !isScoreGraduationV6(selected?.payload)
    && !isScoreGraduationV7(selected?.payload)
    && !isScoreGraduationV8(selected?.payload)
  const formEditable = isConfigurationFormEditable(
    selected,
    canManage,
    selectedUsesReadOnlyScorePolicy,
  )

  const loadVersions = useCallback(async (key: ConfigKey, preferredId?: string) => {
    setLoading(true)
    setLoadError(undefined)
    let cancelled = false
    try {
      const items = await versionRequest.run((options) =>
        request<ConfigVersion[]>(`/api/configs?key=${key}`, {}, options),
      )
      const currentItems = currentConfigurations(items)
      setVersionsByKey((current) => ({ ...current, [key]: currentItems }))
      const nextId = preferredId && currentItems.some((item) => item.version_id === preferredId)
        ? preferredId
        : currentItems.find((item) => item.status === 'DRAFT')?.version_id
          ?? currentItems.find((item) => item.status === 'VALIDATED')?.version_id
          ?? currentItems.find((item) => item.status === 'PUBLISHED')?.version_id
          ?? currentItems[0]?.version_id
      setSelectedIdByKey((current) => ({ ...current, [key]: nextId }))
    } catch (error) {
      if (isRequestCancelled(error)) {
        cancelled = true
        return
      }
      setVersionsByKey((current) => ({ ...current, [key]: [] }))
      setSelectedIdByKey((current) => ({ ...current, [key]: undefined }))
      setLoadError(error instanceof Error ? error.message : t('配置加载失败', 'Unable to load configuration'))
    } finally {
      if (!cancelled) setLoading(false)
    }
  }, [t, versionRequest])
  useEffect(() => {
    form.resetFields()
    if (selected) form.setFieldsValue(selected.payload as unknown as ConfigFormValues)
  }, [activeKey, form, selected])

  const watchedPayload = Form.useWatch([], form) as unknown as ConfigPayload | undefined
  const agentPayload = activeKey === 'AGENT_POLICY' && watchedPayload
    ? watchedPayload as AgentPolicyPayload
    : selected?.payload as AgentPolicyPayload | undefined

  const selectedDescription = useMemo(() => ({
    SCORE_GRADUATION: {
      title: t('积分与出营 / 金牌', 'Scores & Graduation / Gold'),
      description: t('原始分无封顶、供给积分里程碑、两级分数线与资格硬门槛', 'Uncapped raw score, Peak availability milestone, two score thresholds, and qualification gates'),
    },
    AGENT_POLICY: {
      title: t('Agent 策略', 'Agent Policy'),
      description: t('启停、紧急熔断、主次任务上限与受控模型；禁止自由发明任务', 'Enablement, emergency kill switch, task limits, and controlled model; task invention is prohibited'),
    },
    DELIVERY_POLICY: {
      title: t('提醒与交付', 'Reminders & Delivery'),
      description: t('普通/紧急任务提醒，以及 P0 确认任务的回复和提醒时限', 'Normal and urgent reminders plus P0 response and reminder windows'),
    },
  }[activeKey]), [activeKey, t])
  const configStatusLabel = (status: ConfigVersion['status']) => locale === 'en-US'
    ? ({ DRAFT: 'Draft', VALIDATED: 'Ready to publish', PUBLISHED: 'Active', RETIRED: 'Archived' }[status])
    : currentConfigStatusLabel(status)
  const actorLabel = (actorId?: string | null) => locale === 'en-US' && actorId?.startsWith('system:') ? 'System initialization' : configActorLabel(actorId)

  if (!active) return null

  async function createDraft() {
    setSaving(true)
    try {
      const created = await request<ConfigVersion>('/api/configs/drafts', {
        method: 'POST',
        body: JSON.stringify({ config_key: activeKey, ...(published ? { from_version_id: published.version_id } : {}) }),
      })
      message.success(t('已创建新草稿', 'Draft created'))
      await loadVersions(activeKey, created.version_id)
    } catch (error) {
      message.error(apiErrorMessage(error, t('新建草稿失败', 'Unable to create draft')))
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
      const updated = await request<ConfigVersion>(`/api/configs/${selected.version_id}`, {
        method: 'PATCH',
        body: JSON.stringify({ payload }),
      })
      message.success(t('草稿已保存', 'Draft saved'))
      await loadVersions(activeKey, updated.version_id)
    } catch (error) {
      if (error instanceof ApiError) message.error(apiErrorMessage(error, t('草稿保存失败', 'Unable to save draft')))
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
      await request<ConfigVersion>(`/api/configs/${selected.version_id}`, {
        method: 'PATCH',
        body: JSON.stringify({ payload }),
      })
      const result = await request<{ valid: boolean; errors: Array<{ message: string }>; version: ConfigVersion }>(
        `/api/configs/${selected.version_id}/validate`,
        { method: 'POST' },
      )
      if (!result.valid) {
        message.error(result.errors.map((item) => item.message).join('；'))
      } else {
        message.success(t('服务端校验通过，等待另一位配置发布人发布', 'Server validation passed. Waiting for another configuration publisher.'))
      }
      await loadVersions(activeKey, selected.version_id)
    } catch (error) {
      if (error instanceof ApiError) message.error(apiErrorMessage(error, t('服务端校验失败', 'Server validation failed')))
    } finally {
      setSaving(false)
    }
  }

  async function publishVersion() {
    if (!selected) return
    setSaving(true)
    try {
      const result = await request<ConfigVersion>(`/api/configs/${selected.version_id}/publish`, { method: 'POST' })
      message.success(t('配置已发布，已成为当前规则', 'Configuration published and now active'))
      await loadVersions(activeKey, result.version_id)
    } catch (error) {
      message.error(apiErrorMessage(error, t('发布失败', 'Publish failed')))
    } finally {
      setSaving(false)
    }
  }

  const publishDisabledReason = selected?.status === 'VALIDATED' && selected.high_impact && operator?.operator_id === selected.created_by
    ? t('你是该草稿创建人，必须由另一位配置发布人操作', 'You created this draft. Another configuration publisher must publish it.')
    : undefined

  return (
    <div className="page-shell">
      <PageHeader
        eyebrow={t('任务与规则', 'Tasks & Rules')}
        title={t('积分与门槛', 'Scores & Thresholds')}
        description={t('管理会直接影响积分、出营资格与任务提醒的当前规则。所有调整都会留痕，并在发布后生效。', 'Manage active rules that directly affect scores, qualifications, and task reminders. All changes are audited and take effect after publication.')}
        actions={<Button icon={<ReloadOutlined />} loading={loading} onClick={() => loadVersions(activeKey)}>{t('更新配置', 'Refresh configuration')}</Button>}
      />
      <Alert
        type="warning"
        showIcon
        icon={<SafetyCertificateOutlined />}
        message={t('高影响配置：创建人与发布人必须不同', 'High-impact configuration: creator and publisher must differ')}
        description={t('页面不会提交 actor_id；操作者来自服务端已登录会话。修改会留审计，需新建编辑草稿，发布后成为当前规则。', 'The UI does not submit actor_id; the operator comes from the authenticated server session. Changes are audited, edited in a new draft, and become active only after publication.')}
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
            label: key === 'SCORE_GRADUATION' ? t('积分与出营 / 金牌', 'Scores & Graduation / Gold') : key === 'AGENT_POLICY' ? t('Agent 策略', 'Agent Policy') : t('提醒与交付', 'Reminders & Delivery'),
            children: <Text type="secondary">{key === activeKey ? selectedDescription.description : ''}</Text>,
          }))}
        />
      </Card>

      {loadError ? <Alert type="error" showIcon message={t('配置中心暂不可用', 'Configuration center is temporarily unavailable')} description={loadError} /> : null}

      <Row gutter={[18, 18]} align="stretch">
        <Col xs={24} xl={8}>
          <Card
            className="config-current-card"
            title={t('当前配置', 'Current Configuration')}
            extra={canManage ? <Button type="primary" onClick={createDraft} loading={saving} disabled={!published}>{t('新建草稿', 'New draft')}</Button> : null}
          >
            {!hasLoaded ? (
              <Alert type="info" showIcon message={t('尚未读取配置', 'Configuration not loaded')} description={t('点击“更新配置”读取当前类别；切换类别不会自动请求数据。', 'Select “Refresh configuration” to load this category. Switching categories does not request data.')} />
            ) : !published && !loading && !loadError ? (
              <Alert type="warning" showIcon message={t('尚无默认配置', 'No default configuration')} description={t('请先显式运行配置 seed；空库读取不会自动创建配置。', 'Run the configuration seed explicitly. Reading an empty database never creates configuration automatically.')} />
            ) : null}
            <Table
              size="small"
              loading={loading}
              rowKey="version_id"
              dataSource={versions}
              pagination={false}
              rowClassName={(record) => record.version_id === selectedId ? 'config-current-selected' : ''}
              onRow={(record) => ({ onClick: () => setSelectedIdByKey((current) => ({ ...current, [activeKey]: record.version_id })) })}
              locale={{ emptyText: hasLoaded ? t('当前类别暂无配置', 'No configuration in this category') : t('等待主动更新', 'Awaiting refresh') }}
              columns={[
                { title: t('状态', 'Status'), dataIndex: 'status', render: (value) => <Tag color={configStatusColor(value)}>{configStatusLabel(value)}</Tag> },
                { title: t('操作人', 'Operator'), key: 'operator', ellipsis: true, render: (_, record) => locale === 'en-US' && currentConfigOperator(record) === '系统初始化' ? 'System initialization' : currentConfigOperator(record) },
              ]}
            />
          </Card>
        </Col>

        <Col xs={24} xl={16}>
          <Card
            className="config-editor-card"
            title={selectedDescription.title}
            extra={selected ? <Tag color={configStatusColor(selected.status)}>{configStatusLabel(selected.status)}</Tag> : null}
          >
            {!selected ? <Empty description={hasLoaded ? t('选择当前配置或编辑草稿', 'Select the active configuration or a draft') : t('点击“更新配置”读取当前类别', 'Select “Refresh configuration” to load this category')} /> : (
              <>
                <Descriptions size="small" bordered column={{ xs: 1, md: 2 }} className="config-version-meta">
                  <Descriptions.Item label={t('创建人', 'Created by')}>{actorLabel(selected.created_by)}</Descriptions.Item>
                  <Descriptions.Item label={t('校验人', 'Validated by')}>{actorLabel(selected.validated_by)}</Descriptions.Item>
                  <Descriptions.Item label={t('发布人', 'Published by')}>{actorLabel(selected.published_by)}</Descriptions.Item>
                  <Descriptions.Item label={t('最后操作人', 'Last updated by')}>{actorLabel(selected.updated_by)}</Descriptions.Item>
                </Descriptions>

                {selected.validation_errors.length ? (
                  <Alert
                    type="error"
                    showIcon
                    message={t('服务端校验未通过', 'Server validation failed')}
                    description={selected.validation_errors.map((item) => `${item.path || t('配置', 'Configuration')}: ${item.message}`).join('; ')}
                  />
                ) : null}

                <Form
                  form={form}
                  layout="vertical"
                  disabled={!formEditable}
                  className="config-form"
                >
                  {activeKey === 'SCORE_GRADUATION' ? <ScoreForm payload={selected.payload} editable={formEditable} /> : null}
                  {activeKey === 'AGENT_POLICY' && agentPayload ? <AgentForm payload={agentPayload} /> : null}
                  {activeKey === 'DELIVERY_POLICY' ? <DeliveryForm /> : null}
                </Form>

                {canManage ? (
                  <Flex gap={10} justify="flex-end" wrap="wrap" className="config-actions">
                    <Button icon={<EditOutlined />} disabled={!canEditConfiguration(selected) || selectedUsesReadOnlyScorePolicy} loading={saving} onClick={saveDraft}>{t('保存草稿', 'Save draft')}</Button>
                    <Button icon={<CheckCircleOutlined />} disabled={!canValidateConfiguration(selected) || selectedUsesReadOnlyScorePolicy} loading={saving} onClick={validateDraft}>{t('保存并校验', 'Save & validate')}</Button>
                    <Tooltip title={publishDisabledReason}>
                      <span><Button type="primary" icon={<SafetyCertificateOutlined />} disabled={!canPublishConfiguration(selected, operator?.operator_id)} loading={saving} onClick={publishVersion}>{t('双人发布', 'Dual-control publish')}</Button></span>
                    </Tooltip>
                  </Flex>
                ) : <Alert type="info" showIcon message={t('当前账号为只读权限', 'This account is read-only')} description={t('只有 CONFIG_PUBLISHER 可以创建、校验和发布配置。', 'Only CONFIG_PUBLISHER can create, validate, and publish configuration.')} />}
              </>
            )}
          </Card>
        </Col>
      </Row>

      <Card size="small">
        <Title level={5}>{t('当前登录身份', 'Current Identity')}</Title>
        <Paragraph type="secondary">
          {operator ? `${operator.display_name ?? operator.username} · ${operator.operator_id} · ${operator.roles.join(' / ')}` : t('未读取到登录身份', 'Identity not loaded')}
        </Paragraph>
      </Card>
    </div>
  )
}
