import { z } from 'zod';
import { encodeCompatibilityLessonId } from './course-participation-identity';

const identifier = z.string().min(1).max(128);
const sourceAppointIdentifier = z.string().min(1).max(512);
const dateTime = z.preprocess(
  (value) => (value instanceof Date ? value.toISOString() : value),
  z.string().datetime({ offset: true }),
);
const dataMode = z.enum(['MIXED', 'REAL', 'MOCK']);
const graduationState = z.enum(['IN_CAMP', 'GRADUATED']);
const teacherOnlineStatus = z.enum(['NEW', 'EXISTING', 'LEFT', 'BLOCKED']);
const goldStatus = z.enum(['NOT_GOLD', 'GOLD']);
const teacherSourceStatus = z.enum(['CONFIRMED', 'SOURCE_MISSING']);
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
    graduationState: graduationState.nullable(),
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
    onlineStatus: teacherOnlineStatus,
    rawTotalScore: nonNegativeNumeric,
    publicTotalScore: nonNegativeNumeric.pipe(z.number().max(200)),
    graduationState,
    graduationQualified: z.boolean(),
    graduationQualifiedAt: dateTime.nullable(),
    graduationScoreLocked: nonNegativeNumeric.nullable(),
    goldQualified: z.boolean(),
    goldStatus,
    goldQualifiedAt: dateTime.nullable(),
    graduationThreshold: nonNegativeNumeric,
    goldThreshold: nonNegativeNumeric,
    mandatoryTaskCompletedCount: nonNegativeInteger,
    mandatoryTaskTotalCount: nonNegativeInteger,
    scoreRuleVersion: identifier,
    calculatedAt: dateTime,
    dimensions: z.array(scoreDimensionSchema).length(5),
    teacherSourceStatus,
  })
  .strict()
  .superRefine((scorecard, context) => {
    if (
      scorecard.goldStatus !== (scorecard.goldQualified ? 'GOLD' : 'NOT_GOLD')
    ) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['goldStatus'],
        message: 'goldStatus must match goldQualified',
      });
    }
    if (
      scorecard.graduationQualified !==
      (scorecard.graduationQualifiedAt !== null &&
        scorecard.graduationScoreLocked !== null)
    ) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['graduationQualifiedAt'],
        message: 'graduation qualification facts are inconsistent',
      });
    }
    if (scorecard.goldQualified !== (scorecard.goldQualifiedAt !== null)) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['goldQualifiedAt'],
        message: 'gold qualification facts are inconsistent',
      });
    }
  })
  .transform(({ teacherSourceStatus: sourceStatus, ...scorecard }) => ({
    ...scorecard,
    source: { teacherSourceStatus: sourceStatus },
  }));

const nullableBoolean = z.boolean().nullable();

const lessonBusinessFactsSchema = z
  .object({
    attendance: z
      .object({
        is_late: nullableBoolean,
        is_early: nullableBoolean,
      })
      .passthrough(),
    user_feedback: z
      .object({
        has_positive_feedback_tag: nullableBoolean.optional(),
        is_favorited: nullableBoolean.optional(),
        is_rebooked: nullableBoolean.optional(),
        grading_classification: z
          .enum(['POSITIVE', 'NEGATIVE', 'SOURCE_MISSING'])
          .nullable()
          .optional(),
        favorite_attribution_status: z.string().nullable().optional(),
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
  .transform((facts) => {
    const grading = facts.user_feedback.grading_classification;
    const positiveFeedback =
      facts.user_feedback.has_positive_feedback_tag !== undefined
        ? facts.user_feedback.has_positive_feedback_tag
        : grading === 'POSITIVE'
          ? true
          : grading === 'NEGATIVE'
            ? false
            : null;
    const favoriteStatus = facts.user_feedback.favorite_attribution_status;
    const favorited =
      facts.user_feedback.is_favorited !== undefined
        ? facts.user_feedback.is_favorited
        : favoriteStatus === 'AWARDED' ||
            favoriteStatus === 'AWARDED_PENDING_EVIDENCE'
          ? true
          : null;
    return {
      late: facts.attendance.is_late,
      earlyLeave: facts.attendance.is_early,
      positiveFeedback,
      favorited,
      rebooked: facts.user_feedback.is_rebooked ?? null,
      cameraOff: facts.classroom_quality.is_camera_off,
      cpuUsageHigh: facts.classroom_quality.is_cpu_usage_high,
      networkDelayHigh: facts.classroom_quality.is_network_delay_high,
      peak: facts.capacity.is_peak,
    };
  });

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
    evidence_status: identifier.optional().default('NOT_APPLICABLE'),
    evidence_coverage: z.string().max(100).nullable().optional(),
    components: z.array(lessonScoreComponentSchema),
  })
  .passthrough()
  .transform((dimension) => ({
    code: dimension.code,
    score: dimension.score,
    evidenceStatus: dimension.evidence_status,
    evidenceCoverage: dimension.evidence_coverage ?? null,
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
    sourceRegion: z.enum(['dom', 'ovs']),
    sourceAppointId: sourceAppointIdentifier,
    participationSeq: nonNegativeInteger.pipe(z.number().min(1)),
    lessonSequence: nonNegativeInteger.pipe(z.number().min(1)),
    lessonCount: nonNegativeInteger,
    scheduledStartAt: dateTime.nullable(),
    lessonLocalDate: localDate,
    lessonLocalTime: localTime,
    lifecycleStatus: identifier,
    validForScoring: z.boolean(),
    evidenceStatus: identifier,
    lessonTotalScore: nonNegativeNumeric,
    scoreRuleVersion: identifier.nullable(),
    updatedAt: dateTime,
    facts: lessonBusinessFactsSchema,
    dimensions: z.array(lessonScoreDimensionSchema).length(3),
  })
  .strict()
  .transform((lesson) => ({
    ...lesson,
    lessonId: encodeCompatibilityLessonId(lesson),
  }));
