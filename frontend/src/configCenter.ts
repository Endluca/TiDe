export type ConfigKey = 'SCORE_GRADUATION' | 'AGENT_POLICY' | 'DELIVERY_POLICY'
export type ConfigStatus = 'DRAFT' | 'VALIDATED' | 'PUBLISHED' | 'RETIRED'

export interface SupplyMilestoneRule {
  maximum_points: number
  milestone_id?: 'CAPACITY_PEAK_SLOT_40'
  metric?: 'peak_slot_cnt'
  operator?: 'GTE'
  threshold?: number
  score_value?: number
  settlement_mode?: 'FIRST_ACHIEVEMENT_LOCKED'
}

export interface LegacyClassroomQualityRule {
  points_per_unit: number
  default_achievement_rate: number
  source_mode: 'MOCK_SIMULATION'
}

export interface PerfectCompletionQualityRule {
  points_per_unit: number
  metric: 'perfect_cnt'
  source_mode: 'REAL_TEACHER_SNAPSHOT'
}

export interface LegacyGraduationHardGates {
  minimum_base_score: number
  minimum_completed_lessons: number
  minimum_user_feedback_score_exclusive: number
  minimum_reliability_score_exclusive: number
  allow_severe_redline: boolean
}

export interface CurrentGraduationHardGates {
  required_mandatory_task_count: number
  maximum_l0_complaint_count: number
}

export interface LegacyGoldHardGates {
  required_base_score: number
  minimum_completed_lessons: number
  minimum_user_feedback_score: number
  maximum_late_count: number
  maximum_early_count: number
  maximum_real_absent_count: number
}

export interface CurrentGoldHardGates {
  inherits_graduation: true
}

export interface ScoreGraduationPayload {
  policy_version: 'v2' | 'v3' | 'v4' | 'v5' | 'v6'
  graduation_effect: 'IMMEDIATE_ON_CRITERIA'
  scoring_items: {
    capacity: SupplyMilestoneRule
    new_teacher_tasks: { maximum_points: number }
    feedback_praise: { points_per_unit: number }
    feedback_favorite: { points_per_unit: number }
    feedback_rebook_15d: { points_per_unit: number }
    reliability_on_time: { points_per_unit: number }
    reliability_peak: { points_per_unit: number }
    classroom_quality: LegacyClassroomQualityRule | PerfectCompletionQualityRule
  }
  thresholds: {
    graduation_raw_score: number
    gold_raw_score: number
    graduation_external_score: number
    gold_external_score: number
  }
  hard_gates: {
    graduation: LegacyGraduationHardGates | CurrentGraduationHardGates
    gold: LegacyGoldHardGates | CurrentGoldHardGates
  }
}

export interface AgentPolicyPayload {
  enabled: boolean
  kill_switch: boolean
  max_primary_tasks: 1
  max_secondary_tasks: number
  provider: 'deterministic' | 'openai'
  model: string
  allow_task_invention: false
}

export interface DeliveryPolicyPayload {
  normal_reminder_minutes_before_due: number
  urgent_reminder_minutes_before_due: number
  p0_response_window_minutes: number
  p0_reminder_minutes_before_response_due: number
}

export type ConfigPayload = ScoreGraduationPayload | AgentPolicyPayload | DeliveryPolicyPayload

export interface ConfigValidationError {
  path: string
  type: string
  message: string
}

export interface ConfigVersion {
  version_id: string
  config_key: ConfigKey
  version_number: number
  status: ConfigStatus
  high_impact: boolean
  payload: ConfigPayload
  validation_errors: ConfigValidationError[]
  source_version_id: string | null
  created_by: string
  updated_by: string
  validated_by: string | null
  published_by: string | null
  retired_by: string | null
  created_at: string
  updated_at: string
  validated_at: string | null
  published_at: string | null
  retired_at: string | null
}

export interface OperatorIdentity {
  operator_id: string
  username: string
  display_name: string | null
  roles: string[]
}

export const CONFIG_KEYS: ConfigKey[] = ['SCORE_GRADUATION', 'AGENT_POLICY', 'DELIVERY_POLICY']

export const CONFIG_DOMAIN_META: Record<ConfigKey, { title: string; description: string }> = {
  SCORE_GRADUATION: {
    title: '积分与出营 / 金牌',
    description: '原始分无封顶、供给积分里程碑、两级分数线与资格硬门槛',
  },
  AGENT_POLICY: {
    title: 'Agent 策略',
    description: '启停、紧急熔断、主次任务上限与受控模型；禁止自由发明任务',
  },
  DELIVERY_POLICY: {
    title: '提醒与交付',
    description: '普通/紧急任务提醒，以及 P0 确认任务的回复和提醒时限',
  },
}

export const SCORING_ITEM_META: Array<{
  key: keyof ScoreGraduationPayload['scoring_items']
  label: string
  field: 'maximum_points' | 'points_per_unit'
  unit: string
}> = [
  { key: 'capacity', label: '供给积分里程碑', field: 'maximum_points', unit: '满分' },
  { key: 'new_teacher_tasks', label: '成长任务（必修）', field: 'maximum_points', unit: '当前分值' },
  { key: 'feedback_praise', label: '用户反馈 · 好评', field: 'points_per_unit', unit: '每次' },
  { key: 'feedback_favorite', label: '用户反馈 · 收藏', field: 'points_per_unit', unit: '每人' },
  { key: 'feedback_rebook_15d', label: '用户反馈 · 15 日复约', field: 'points_per_unit', unit: '每人' },
  { key: 'reliability_on_time', label: '可靠性 · 准时完课', field: 'points_per_unit', unit: '每节' },
  { key: 'reliability_peak', label: '可靠性 · Peak 完课', field: 'points_per_unit', unit: '每节' },
  { key: 'classroom_quality', label: '课堂质量 · 无问题课程', field: 'points_per_unit', unit: '每节' },
]

function isScoreGraduationVersion(
  payload: unknown,
  policyVersion: ScoreGraduationPayload['policy_version'],
): payload is ScoreGraduationPayload {
  if (!payload || typeof payload !== 'object') return false
  const value = payload as Partial<ScoreGraduationPayload>
  const scoringItems = value.scoring_items as Record<string, unknown> | undefined
  const thresholds = value.thresholds as Record<string, unknown> | undefined
  const hardGates = value.hard_gates as Record<string, unknown> | undefined
  return value.policy_version === policyVersion
    && value.graduation_effect === 'IMMEDIATE_ON_CRITERIA'
    && Boolean(scoringItems)
    && ['capacity', 'new_teacher_tasks', 'feedback_praise', 'feedback_favorite', 'feedback_rebook_15d', 'reliability_on_time', 'reliability_peak', 'classroom_quality'].every((key) => key in (scoringItems ?? {}))
    && ['graduation_raw_score', 'gold_raw_score', 'graduation_external_score', 'gold_external_score'].every((key) => key in (thresholds ?? {}))
    && ['graduation', 'gold'].every((key) => key in (hardGates ?? {}))
}

export function isLegacyScoreGraduation(payload: unknown): payload is ScoreGraduationPayload {
  return isScoreGraduationVersion(payload, 'v2')
}

export function isScoreGraduationV3(payload: unknown): payload is ScoreGraduationPayload {
  return isScoreGraduationVersion(payload, 'v3')
}

export function isScoreGraduationV4(payload: unknown): payload is ScoreGraduationPayload {
  if (!isScoreGraduationVersion(payload, 'v4')) return false
  const capacity = payload.scoring_items.capacity
  const classroomQuality = payload.scoring_items.classroom_quality
  const graduationGates = payload.hard_gates.graduation
  return capacity.milestone_id === 'CAPACITY_PEAK_SLOT_40'
    && capacity.metric === 'peak_slot_cnt'
    && capacity.operator === 'GTE'
    && typeof capacity.threshold === 'number'
    && typeof capacity.score_value === 'number'
    && typeof capacity.maximum_points === 'number'
    && capacity.settlement_mode === 'FIRST_ACHIEVEMENT_LOCKED'
    && 'default_achievement_rate' in classroomQuality
    && classroomQuality.source_mode === 'MOCK_SIMULATION'
    && 'minimum_completed_lessons' in graduationGates
}

export function isScoreGraduationV5(payload: unknown): payload is ScoreGraduationPayload {
  if (!isScoreGraduationVersion(payload, 'v5')) return false
  const capacity = payload.scoring_items.capacity
  const classroomQuality = payload.scoring_items.classroom_quality
  const graduationGates = payload.hard_gates.graduation
  return capacity.milestone_id === 'CAPACITY_PEAK_SLOT_40'
    && capacity.metric === 'peak_slot_cnt'
    && capacity.operator === 'GTE'
    && typeof capacity.threshold === 'number'
    && typeof capacity.score_value === 'number'
    && typeof capacity.maximum_points === 'number'
    && capacity.settlement_mode === 'FIRST_ACHIEVEMENT_LOCKED'
    && 'metric' in classroomQuality
    && classroomQuality.metric === 'perfect_cnt'
    && classroomQuality.source_mode === 'REAL_TEACHER_SNAPSHOT'
    && typeof classroomQuality.points_per_unit === 'number'
    && 'required_mandatory_task_count' in graduationGates
    && typeof graduationGates.required_mandatory_task_count === 'number'
    && 'maximum_l0_complaint_count' in graduationGates
    && typeof graduationGates.maximum_l0_complaint_count === 'number'
}

export function isScoreGraduationV6(payload: unknown): payload is ScoreGraduationPayload {
  if (!isScoreGraduationVersion(payload, 'v6')) return false
  const capacity = payload.scoring_items.capacity
  const classroomQuality = payload.scoring_items.classroom_quality
  const graduationGates = payload.hard_gates.graduation
  const goldGates = payload.hard_gates.gold
  return capacity.milestone_id === 'CAPACITY_PEAK_SLOT_40'
    && capacity.metric === 'peak_slot_cnt'
    && capacity.operator === 'GTE'
    && typeof capacity.threshold === 'number'
    && typeof capacity.score_value === 'number'
    && typeof capacity.maximum_points === 'number'
    && capacity.settlement_mode === 'FIRST_ACHIEVEMENT_LOCKED'
    && 'metric' in classroomQuality
    && classroomQuality.metric === 'perfect_cnt'
    && classroomQuality.source_mode === 'REAL_TEACHER_SNAPSHOT'
    && typeof classroomQuality.points_per_unit === 'number'
    && 'required_mandatory_task_count' in graduationGates
    && typeof graduationGates.required_mandatory_task_count === 'number'
    && 'maximum_l0_complaint_count' in graduationGates
    && typeof graduationGates.maximum_l0_complaint_count === 'number'
    && 'inherits_graduation' in goldGates
    && goldGates.inherits_graduation === true
}

export function canEditConfiguration(version?: ConfigVersion): boolean {
  return version?.status === 'DRAFT'
}

export function canValidateConfiguration(version?: ConfigVersion): boolean {
  return version?.status === 'DRAFT'
}

export function canPublishConfiguration(version: ConfigVersion | undefined, operatorId?: string): boolean {
  if (!version || version.status !== 'VALIDATED') return false
  if (!operatorId) return true
  return !version.high_impact || version.created_by !== operatorId
}

export function agentEffectivelyEnabled(payload: AgentPolicyPayload): boolean {
  return payload.enabled && !payload.kill_switch
}

export function configStatusColor(status: ConfigStatus): string {
  return {
    DRAFT: 'default',
    VALIDATED: 'processing',
    PUBLISHED: 'success',
    RETIRED: 'warning',
  }[status]
}

export function currentConfigurations(versions: ConfigVersion[]): ConfigVersion[] {
  return versions.filter((version) => version.status !== 'RETIRED')
}

export function currentConfigStatusLabel(status: ConfigStatus): string {
  return {
    DRAFT: '编辑草稿',
    VALIDATED: '待发布',
    PUBLISHED: '当前生效',
    RETIRED: '已归档',
  }[status]
}

export function currentConfigOperator(version: ConfigVersion): string {
  if (version.status === 'PUBLISHED') return configActorLabel(version.published_by ?? version.updated_by)
  if (version.status === 'VALIDATED') return configActorLabel(version.validated_by ?? version.updated_by)
  return configActorLabel(version.updated_by)
}

export function configActorLabel(actorId?: string | null): string {
  if (!actorId) return '—'
  return actorId.startsWith('system:') ? '系统初始化' : actorId
}
