import { describe, expect, it } from 'vitest'
import {
  agentEffectivelyEnabled,
  canEditConfiguration,
  canPublishConfiguration,
  configActorLabel,
  currentConfigurations,
  currentConfigOperator,
  currentConfigStatusLabel,
  isLegacyScoreGraduation,
  isScoreGraduationV3,
  isScoreGraduationV4,
  isScoreGraduationV5,
  isScoreGraduationV6,
  type AgentPolicyPayload,
  type ConfigVersion,
  type ScoreGraduationPayload,
} from './configCenter'


function version(status: ConfigVersion['status'], createdBy = 'creator'): ConfigVersion {
  return {
    version_id: 'CFG-1',
    config_key: 'AGENT_POLICY',
    version_number: 1,
    status,
    high_impact: true,
    payload: {
      enabled: true,
      kill_switch: false,
      max_primary_tasks: 1,
      max_secondary_tasks: 2,
      provider: 'openai',
      model: 'gpt-5.6-terra',
      allow_task_invention: false,
    },
    validation_errors: [],
    source_version_id: null,
    created_by: createdBy,
    updated_by: createdBy,
    validated_by: null,
    published_by: null,
    retired_by: null,
    created_at: '2026-07-17T00:00:00Z',
    updated_at: '2026-07-17T00:00:00Z',
    validated_at: null,
    published_at: null,
    retired_at: null,
  }
}


describe('configuration center governance helpers', () => {
  it('只向页面提供当前配置和编辑中的草稿', () => {
    const items = [version('PUBLISHED'), version('DRAFT'), version('VALIDATED'), version('RETIRED')]
    expect(currentConfigurations(items).map((item) => item.status)).toEqual(['PUBLISHED', 'DRAFT', 'VALIDATED'])
    expect(currentConfigStatusLabel('PUBLISHED')).toBe('当前生效')
    expect(currentConfigStatusLabel('DRAFT')).toBe('编辑草稿')
    expect(currentConfigStatusLabel('VALIDATED')).toBe('待发布')
    expect(currentConfigOperator({ ...version('PUBLISHED'), published_by: 'publisher' })).toBe('publisher')
    expect(configActorLabel('system:score-upgrade')).toBe('系统初始化')
    expect(configActorLabel(null)).toBe('—')
  })

  it('only edits draft versions', () => {
    expect(canEditConfiguration(version('DRAFT'))).toBe(true)
    expect(canEditConfiguration(version('VALIDATED'))).toBe(false)
    expect(canEditConfiguration(version('PUBLISHED'))).toBe(false)
  })

  it('requires a different publisher for high impact config', () => {
    const validated = version('VALIDATED', 'operator-a')
    expect(canPublishConfiguration(validated, 'operator-a')).toBe(false)
    expect(canPublishConfiguration(validated, 'operator-b')).toBe(true)
  })

  it('kill switch overrides enabled', () => {
    const payload = version('PUBLISHED').payload as AgentPolicyPayload
    expect(agentEffectivelyEnabled(payload)).toBe(true)
    expect(agentEffectivelyEnabled({ ...payload, kill_switch: true })).toBe(false)
  })

  it('识别两项金牌门槛的 v6 当前结构，并兼容读取 v2-v5', () => {
    const payload: ScoreGraduationPayload = {
      policy_version: 'v5',
      graduation_effect: 'IMMEDIATE_ON_CRITERIA',
      scoring_items: {
        capacity: {
          milestone_id: 'CAPACITY_PEAK_SLOT_40',
          metric: 'peak_slot_cnt',
          operator: 'GTE',
          threshold: 40,
          score_value: 10,
          maximum_points: 10,
          settlement_mode: 'FIRST_ACHIEVEMENT_LOCKED',
        },
        new_teacher_tasks: { maximum_points: 30 },
        feedback_praise: { points_per_unit: 5 },
        feedback_favorite: { points_per_unit: 5 },
        feedback_rebook_15d: { points_per_unit: 8 },
        reliability_on_time: { points_per_unit: 2 },
        reliability_peak: { points_per_unit: 1 },
        classroom_quality: { points_per_unit: 1.6, metric: 'perfect_cnt', source_mode: 'REAL_TEACHER_SNAPSHOT' },
      },
      thresholds: {
        graduation_raw_score: 100,
        gold_raw_score: 200,
        graduation_external_score: 100,
        gold_external_score: 200,
      },
      hard_gates: {
        graduation: {
          required_mandatory_task_count: 10,
          maximum_l0_complaint_count: 0,
        },
        gold: {
          required_base_score: 40,
          minimum_completed_lessons: 10,
          minimum_user_feedback_score: 20,
          maximum_late_count: 1,
          maximum_early_count: 0,
          maximum_real_absent_count: 0,
        },
      },
    }

    expect(isScoreGraduationV5(payload)).toBe(true)
    expect(isScoreGraduationV6(payload)).toBe(false)
    expect(isScoreGraduationV4(payload)).toBe(false)
    expect(isScoreGraduationV3(payload)).toBe(false)
    expect(isLegacyScoreGraduation(payload)).toBe(false)
    const currentPayload: ScoreGraduationPayload = {
      ...payload,
      policy_version: 'v6',
      hard_gates: {
        ...payload.hard_gates,
        gold: { inherits_graduation: true },
      },
    }
    expect(isScoreGraduationV6(currentPayload)).toBe(true)
    expect(isScoreGraduationV5(currentPayload)).toBe(false)
    const legacyClassroomQuality = {
      points_per_unit: 2,
      default_achievement_rate: 0.8,
      source_mode: 'MOCK_SIMULATION' as const,
    }
    const legacyGraduationGates = {
      minimum_base_score: 30,
      minimum_completed_lessons: 10,
      minimum_user_feedback_score_exclusive: 0,
      minimum_reliability_score_exclusive: 0,
      allow_severe_redline: false,
    }
    expect(isScoreGraduationV4({
      ...payload,
      policy_version: 'v4',
      scoring_items: {
        ...payload.scoring_items,
        classroom_quality: legacyClassroomQuality,
      },
      hard_gates: {
        ...payload.hard_gates,
        graduation: legacyGraduationGates,
      },
    })).toBe(true)
    expect(isScoreGraduationV3({
      ...payload,
      policy_version: 'v3',
      scoring_items: {
        ...payload.scoring_items,
        classroom_quality: legacyClassroomQuality,
      },
      hard_gates: {
        ...payload.hard_gates,
        graduation: legacyGraduationGates,
      },
    })).toBe(true)
    expect(isLegacyScoreGraduation({
      ...payload,
      policy_version: 'v2',
      scoring_items: {
        ...payload.scoring_items,
        capacity: { maximum_points: 10 },
        classroom_quality: legacyClassroomQuality,
      },
      thresholds: { ...payload.thresholds, gold_raw_score: 660 },
      hard_gates: {
        ...payload.hard_gates,
        graduation: legacyGraduationGates,
      },
    })).toBe(true)
    expect(isScoreGraduationV5({
      ...payload,
      scoring_items: {
        ...payload.scoring_items,
        capacity: { ...payload.scoring_items.capacity, threshold: 39 },
      },
    })).toBe(true)
    expect(isScoreGraduationV5({
      ...payload,
      scoring_items: {
        ...payload.scoring_items,
        capacity: { maximum_points: 10 },
      },
    })).toBe(false)
    expect(isScoreGraduationV5({
      ...payload,
      scoring_items: {
        ...payload.scoring_items,
        classroom_quality: legacyClassroomQuality,
      },
    })).toBe(false)
    expect(isScoreGraduationV5({ dimensions: {} })).toBe(false)
    expect(isScoreGraduationV6({
      ...currentPayload,
      hard_gates: {
        ...currentPayload.hard_gates,
        gold: { inherits_graduation: false },
      },
    })).toBe(false)
    expect(isScoreGraduationV6({ dimensions: {} })).toBe(false)
    expect(isScoreGraduationV4({ dimensions: {} })).toBe(false)
    expect(isScoreGraduationV3({ dimensions: {} })).toBe(false)
    expect(isLegacyScoreGraduation({ dimensions: {} })).toBe(false)
  })
})
