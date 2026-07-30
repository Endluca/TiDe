import {
  lessonScoreSchema,
  teacherIdentitySchema,
  teacherScorecardSchema,
} from './shiwen-read.schemas';

const calculatedAt = '2026-07-27T08:00:00.000Z';

function teacherComponent(code: string) {
  return {
    code,
    unit_count: 1,
    points_per_unit: 2,
    score: 2,
    source_scope: 'LESSON',
    source_metric: null,
    lesson_attributed_count: 1,
    lesson_attributed_score: 2,
    unattributed_score: 0,
    reconciliation_status: 'MATCHED',
  };
}

describe('Shiwen read schemas', () => {
  it('normalizes dates from the identity view', () => {
    const value = teacherIdentitySchema.parse({
      teacherId: 'teacher-001',
      campEnrollmentId: 'camp-001',
      name: 'Teacher',
      timezone: 'Asia/Shanghai',
      campDay: 2,
      graduationState: 'IN_PROGRESS',
      dataMode: 'REAL',
      sourceUpdatedAt: new Date(calculatedAt),
    });

    expect(value.sourceUpdatedAt).toBe(calculatedAt);
  });

  it('accepts exactly five persisted teacher dimensions', () => {
    const dimensions = [
      'USER_FEEDBACK',
      'RELIABILITY',
      'CLASS_QUALITY',
      'CAPACITY',
      'NEW_TEACHER_TASK',
    ].map((code) => ({
      code,
      score: 2,
      score_rule_version: 'rule-v3',
      projection_revision: 1,
      calculated_at: calculatedAt,
      components: [
        teacherComponent(code === 'NEW_TEACHER_TASK' ? 'G01' : code),
      ],
    }));

    const value = teacherScorecardSchema.parse({
      teacherId: 'teacher-001',
      campEnrollmentId: 'camp-001',
      rawTotalScore: '42',
      publicTotalScore: '42',
      graduationState: 'IN_PROGRESS',
      graduationQualified: false,
      goldQualified: false,
      graduationThreshold: '100',
      goldThreshold: '200',
      mandatoryTaskCompletedCount: 1,
      mandatoryTaskTotalCount: 10,
      scoreRuleVersion: 'rule-v3',
      calculatedAt: new Date(calculatedAt),
      dimensions,
    });

    expect(value.publicTotalScore).toBe(42);
    expect(value.dimensions[4].components[0]).toMatchObject({
      code: 'G01',
      pointsPerUnit: 2,
    });
    expect(() =>
      teacherScorecardSchema.parse({
        ...value,
        dimensions: [],
      }),
    ).toThrow();
  });

  it('drops sensitive lesson facts and component internals', () => {
    const value = lessonScoreSchema.parse({
      teacherId: 'teacher-001',
      lessonId: 'lesson-001',
      lessonSequence: 1,
      lessonCount: 1,
      sourceAppointId: 'appoint-001',
      scheduledStartAt: new Date(calculatedAt),
      lessonLocalDate: '2026-07-27',
      lessonLocalTime: '16:00:00',
      lifecycleStatus: 'END',
      validForScoring: true,
      evidenceStatus: 'READY',
      lessonTotalScore: 2,
      scoreRuleVersion: 'rule-v3',
      updatedAt: new Date(calculatedAt),
      facts: {
        attendance: {
          is_late: false,
          is_early: false,
          is_false_early_leave: false,
          is_absence: true,
        },
        user_feedback: {
          has_positive_feedback_tag: true,
          is_favorited: false,
          is_rebooked: false,
          is_blocked: true,
          complaint_detail: 'sensitive',
        },
        classroom_quality: {
          is_camera_off: false,
          is_cpu_usage_high: false,
          is_network_delay_high: false,
        },
        capacity: { is_peak: false },
      },
      dimensions: ['USER_FEEDBACK', 'RELIABILITY', 'CLASS_QUALITY'].map(
        (code) => ({
          code,
          score: code === 'USER_FEEDBACK' ? 2 : 0,
          evidence_status: 'READY',
          evidence_coverage: 'FULL',
          components: [
            {
              code,
              score: code === 'USER_FEEDBACK' ? 2 : 0,
              points_per_unit: 2,
              awarded: code === 'USER_FEEDBACK',
              evidence_status: 'READY',
              internal_reason: 'must not leak',
            },
          ],
        }),
      ),
    });

    expect(value.facts).toEqual({
      late: false,
      earlyLeave: false,
      falseEarlyLeave: false,
      positiveFeedback: true,
      favorited: false,
      rebooked: false,
      cameraOff: false,
      cpuUsageHigh: false,
      networkDelayHigh: false,
      peak: false,
    });
    expect(value.dimensions[0].components[0]).not.toHaveProperty(
      'internal_reason',
    );
  });
});
