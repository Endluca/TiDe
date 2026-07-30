import { z } from 'zod';

const identifier = z.string().min(1).max(128);
const dateTime = z.preprocess(
  (value) => (value instanceof Date ? value.toISOString() : value),
  z.string().datetime({ offset: true }),
);
const dataMode = z.enum(['MIXED', 'REAL', 'MOCK']);
const numeric = z.preprocess(
  (value) => (typeof value === 'string' ? Number(value) : value),
  z.number().finite(),
);
const nonNegativeNumeric = numeric.pipe(z.number().min(0));
const nonNegativeInteger = numeric.pipe(z.number().int().min(0));

export const teacherIdentitySchema = z
  .object({
    teacherId: identifier,
    campEnrollmentId: identifier,
    name: z.string().min(1).max(200),
    timezone: z.string().max(100).nullable(),
    campDay: nonNegativeInteger.nullable(),
    graduationState: z.string().max(100).nullable(),
    dataMode,
    sourceUpdatedAt: dateTime,
  })
  .strict();

const scoreComponentSchema = z
  .object({
    code: identifier,
    unit_count: nonNegativeNumeric,
    points_per_unit: nonNegativeNumeric.nullable(),
    score: nonNegativeNumeric,
    source_scope: z.enum(['LESSON', 'TEACHER', 'TASK']),
    source_metric: z.string().max(200).nullable(),
    lesson_attributed_count: nonNegativeInteger,
    lesson_attributed_score: numeric,
    unattributed_score: numeric,
    reconciliation_status: identifier,
  })
  .passthrough()
  .transform((component) => ({
    code: component.code,
    unitCount: component.unit_count,
    pointsPerUnit: component.points_per_unit,
    score: component.score,
    sourceScope: component.source_scope,
    sourceMetric: component.source_metric,
    lessonAttributedCount: component.lesson_attributed_count,
    lessonAttributedScore: component.lesson_attributed_score,
    unattributedScore: component.unattributed_score,
    reconciliationStatus: component.reconciliation_status,
  }));

const scoreDimensionSchema = z
  .object({
    code: z.enum([
      'USER_FEEDBACK',
      'RELIABILITY',
      'CLASS_QUALITY',
      'CAPACITY',
      'NEW_TEACHER_TASK',
    ]),
    score: nonNegativeNumeric,
    score_rule_version: identifier,
    projection_revision: nonNegativeInteger,
    calculated_at: dateTime,
    components: z.array(scoreComponentSchema),
  })
  .passthrough()
  .transform((dimension) => ({
    code: dimension.code,
    score: dimension.score,
    scoreRuleVersion: dimension.score_rule_version,
    projectionRevision: dimension.projection_revision,
    calculatedAt: dimension.calculated_at,
    components: dimension.components,
  }));

export const teacherScorecardSchema = z
  .object({
    teacherId: identifier,
    campEnrollmentId: identifier,
    rawTotalScore: nonNegativeNumeric,
    publicTotalScore: nonNegativeNumeric.pipe(z.number().max(200)),
    graduationState: identifier,
    graduationQualified: z.boolean(),
    goldQualified: z.boolean(),
    graduationThreshold: nonNegativeNumeric,
    goldThreshold: nonNegativeNumeric,
    mandatoryTaskCompletedCount: nonNegativeInteger,
    mandatoryTaskTotalCount: nonNegativeInteger,
    scoreRuleVersion: identifier,
    calculatedAt: dateTime,
    dimensions: z.array(scoreDimensionSchema).length(5),
  })
  .strict();

const nullableBoolean = z.boolean().nullable();

const lessonBusinessFactsSchema = z
  .object({
    attendance: z
      .object({
        is_late: nullableBoolean,
        is_early: nullableBoolean,
        is_false_early_leave: nullableBoolean,
      })
      .passthrough(),
    user_feedback: z
      .object({
        has_positive_feedback_tag: nullableBoolean,
        is_favorited: nullableBoolean,
        is_rebooked: nullableBoolean,
      })
      .passthrough(),
    classroom_quality: z
      .object({
        is_camera_off: nullableBoolean,
        is_cpu_usage_high: nullableBoolean,
        is_network_delay_high: nullableBoolean,
      })
      .passthrough(),
    capacity: z.object({ is_peak: nullableBoolean }).passthrough(),
  })
  .passthrough()
  .transform((facts) => ({
    late: facts.attendance.is_late,
    earlyLeave: facts.attendance.is_early,
    falseEarlyLeave: facts.attendance.is_false_early_leave,
    positiveFeedback: facts.user_feedback.has_positive_feedback_tag,
    favorited: facts.user_feedback.is_favorited,
    rebooked: facts.user_feedback.is_rebooked,
    cameraOff: facts.classroom_quality.is_camera_off,
    cpuUsageHigh: facts.classroom_quality.is_cpu_usage_high,
    networkDelayHigh: facts.classroom_quality.is_network_delay_high,
    peak: facts.capacity.is_peak,
  }));

const lessonScoreComponentSchema = z
  .object({
    code: identifier,
    score: nonNegativeNumeric,
    points_per_unit: nonNegativeNumeric.nullable(),
    awarded: z.boolean(),
    evidence_status: identifier,
  })
  .passthrough()
  .transform((component) => ({
    code: component.code,
    score: component.score,
    pointsPerUnit: component.points_per_unit,
    awarded: component.awarded,
    evidenceStatus: component.evidence_status,
  }));

const lessonScoreDimensionSchema = z
  .object({
    code: z.enum(['USER_FEEDBACK', 'RELIABILITY', 'CLASS_QUALITY']),
    score: nonNegativeNumeric,
    evidence_status: identifier,
    evidence_coverage: z.string().max(100).nullable(),
    components: z.array(lessonScoreComponentSchema),
  })
  .passthrough()
  .transform((dimension) => ({
    code: dimension.code,
    score: dimension.score,
    evidenceStatus: dimension.evidence_status,
    evidenceCoverage: dimension.evidence_coverage,
    components: dimension.components,
  }));

const localDate = z
  .string()
  .regex(/^\d{4}-\d{2}-\d{2}$/)
  .nullable();
const localTime = z.string().max(32).nullable();

export const lessonScoreSchema = z
  .object({
    teacherId: identifier,
    lessonId: identifier,
    lessonSequence: nonNegativeInteger.pipe(z.number().min(1)),
    lessonCount: nonNegativeInteger,
    sourceAppointId: identifier,
    scheduledStartAt: dateTime.nullable(),
    lessonLocalDate: localDate,
    lessonLocalTime: localTime,
    lifecycleStatus: identifier,
    validForScoring: z.boolean(),
    evidenceStatus: identifier,
    lessonTotalScore: nonNegativeNumeric,
    scoreRuleVersion: identifier,
    updatedAt: dateTime,
    facts: lessonBusinessFactsSchema,
    dimensions: z.array(lessonScoreDimensionSchema).length(3),
  })
  .strict();
