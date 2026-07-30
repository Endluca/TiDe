import type { ConfigService } from '@nestjs/config';
import type { QueryResult, QueryResultRow } from 'pg';
import type { AppEnvironment } from '../../platform/config/environment';
import type { DatabaseService } from '../../platform/database/database.service';
import { PostgresShiwenReadAdapter } from './postgres-shiwen-read.adapter';
import {
  ShiwenContractError,
  ShiwenViewNotConfiguredError,
} from './shiwen-read.errors';

function createConfig(
  values: Partial<Record<keyof AppEnvironment, unknown>>,
): ConfigService<AppEnvironment, true> {
  return {
    get: jest.fn((key: keyof AppEnvironment) => values[key]),
  } as unknown as ConfigService<AppEnvironment, true>;
}

function queryResult(rows: QueryResultRow[]): QueryResult {
  return {
    command: 'SELECT',
    rowCount: rows.length,
    oid: 0,
    fields: [],
    rows,
  };
}

function createDatabase(rows: QueryResultRow[]) {
  const queryShiwen = jest.fn(
    (_text: string, _values: readonly unknown[] = []) => {
      void _text;
      void _values;
      return Promise.resolve(queryResult(rows));
    },
  );
  return {
    database: {
      queryShiwen,
      queryTide: jest.fn(),
    } as unknown as DatabaseService,
    queryShiwen,
  };
}

function scoreComponent(code: string, scope: 'LESSON' | 'TEACHER' | 'TASK') {
  return {
    code,
    unit_count: 1,
    points_per_unit: 3,
    score: 3,
    source_scope: scope,
    source_metric: 'metric',
    lesson_attributed_count: 1,
    lesson_attributed_score: 3,
    unattributed_score: 0,
    reconciliation_status: 'MATCHED',
  };
}

function scorecardRow(): QueryResultRow {
  return {
    teacherId: 'TEACHER-001',
    campEnrollmentId: 'CAMP-001',
    rawTotalScore: '43',
    publicTotalScore: '43',
    graduationState: 'IN_PROGRESS',
    graduationQualified: false,
    goldQualified: false,
    graduationThreshold: '100',
    goldThreshold: '200',
    mandatoryTaskCompletedCount: 1,
    mandatoryTaskTotalCount: 10,
    scoreRuleVersion: 'score-rule-v3',
    calculatedAt: new Date('2026-07-27T08:00:00Z'),
    dimensions: [
      {
        code: 'USER_FEEDBACK',
        score: 0,
        score_rule_version: 'score-rule-v3',
        projection_revision: 1,
        calculated_at: '2026-07-27T08:00:00.000Z',
        components: [scoreComponent('POSITIVE_FEEDBACK', 'LESSON')],
      },
      {
        code: 'RELIABILITY',
        score: 0,
        score_rule_version: 'score-rule-v3',
        projection_revision: 1,
        calculated_at: '2026-07-27T08:00:00.000Z',
        components: [scoreComponent('ON_TIME_COMPLETED', 'LESSON')],
      },
      {
        code: 'CLASS_QUALITY',
        score: 0,
        score_rule_version: 'score-rule-v3',
        projection_revision: 1,
        calculated_at: '2026-07-27T08:00:00.000Z',
        components: [scoreComponent('CLASS_QUALITY_PERFECT_COUNT', 'LESSON')],
      },
      {
        code: 'CAPACITY',
        score: 10,
        score_rule_version: 'score-rule-v3',
        projection_revision: 1,
        calculated_at: '2026-07-27T08:00:00.000Z',
        components: [scoreComponent('CAPACITY_PEAK_SLOT_40', 'TEACHER')],
      },
      {
        code: 'NEW_TEACHER_TASK',
        score: 4,
        score_rule_version: 'score-rule-v3',
        projection_revision: 1,
        calculated_at: '2026-07-27T08:00:00.000Z',
        components: [scoreComponent('G01', 'TASK')],
      },
    ],
  };
}

function lessonRow(): QueryResultRow {
  return {
    teacherId: 'TEACHER-001',
    lessonId: 'LESSON-001',
    lessonSequence: 1,
    lessonCount: 42,
    sourceAppointId: 'APPOINT-001',
    scheduledStartAt: new Date('2026-07-27T08:00:00Z'),
    lessonLocalDate: '2026-07-27',
    lessonLocalTime: '16:00:00',
    lifecycleStatus: 'END',
    validForScoring: true,
    evidenceStatus: 'READY',
    lessonTotalScore: '5',
    scoreRuleVersion: 'score-rule-v3',
    updatedAt: new Date('2026-07-27T09:00:00Z'),
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
        is_rebooked: true,
        is_blocked: true,
        complaint_detail: 'sensitive',
      },
      classroom_quality: {
        is_camera_off: false,
        is_cpu_usage_high: false,
        is_network_delay_high: false,
      },
      capacity: { is_peak: true },
    },
    dimensions: [
      {
        code: 'USER_FEEDBACK',
        score: 3,
        evidence_status: 'READY',
        evidence_coverage: 'FULL',
        components: [
          {
            code: 'POSITIVE_FEEDBACK',
            score: 3,
            points_per_unit: 3,
            awarded: true,
            evidence_status: 'READY',
            internal_reason: 'must not leak',
          },
        ],
      },
      {
        code: 'RELIABILITY',
        score: 2,
        evidence_status: 'READY',
        evidence_coverage: 'FULL',
        components: [
          {
            code: 'ON_TIME_COMPLETED',
            score: 2,
            points_per_unit: 2,
            awarded: true,
            evidence_status: 'READY',
          },
        ],
      },
      {
        code: 'CLASS_QUALITY',
        score: 0,
        evidence_status: 'SOURCE_MISSING',
        evidence_coverage: null,
        components: [
          {
            code: 'CLASS_QUALITY_PERFECT_COUNT',
            score: 0,
            points_per_unit: null,
            awarded: false,
            evidence_status: 'SOURCE_MISSING',
          },
        ],
      },
    ],
  };
}

describe('PostgresShiwenReadAdapter', () => {
  it('reads identity only from the configured safe view', async () => {
    const { database, queryShiwen } = createDatabase([
      {
        teacherId: 'TEACHER-001',
        campEnrollmentId: 'CAMP-001',
        name: 'Teacher',
        timezone: 'Asia/Shanghai',
        campDay: 3,
        graduationState: 'IN_PROGRESS',
        dataMode: 'REAL',
        sourceUpdatedAt: new Date('2026-07-27T08:00:00Z'),
      },
    ]);
    const adapter = new PostgresShiwenReadAdapter(
      createConfig({
        SHIWEN_TEACHER_IDENTITY_VIEW: 'shiwen.teacher_identity_v1',
      }),
      database,
    );

    await expect(adapter.findIdentity('TEACHER-001')).resolves.toMatchObject({
      teacherId: 'TEACHER-001',
      campDay: 3,
    });
    expect(queryShiwen).toHaveBeenCalledWith(
      expect.stringContaining('FROM "shiwen"."teacher_identity_v1"'),
      ['TEACHER-001'],
    );
  });

  it('reads the current teacher scorecard without any local calculation', async () => {
    const { database, queryShiwen } = createDatabase([scorecardRow()]);
    const adapter = new PostgresShiwenReadAdapter(createConfig({}), database);

    await expect(adapter.findScorecard('TEACHER-001')).resolves.toMatchObject({
      publicTotalScore: 43,
      scoreRuleVersion: 'score-rule-v3',
      dimensions: [
        { code: 'USER_FEEDBACK' },
        { code: 'RELIABILITY' },
        { code: 'CLASS_QUALITY' },
        { code: 'CAPACITY' },
        {
          code: 'NEW_TEACHER_TASK',
          components: [{ code: 'G01', pointsPerUnit: 3 }],
        },
      ],
    });
    const sql = String(queryShiwen.mock.calls[0][0]);
    expect(sql).toContain('FROM public.teacher_scorecard_current');
    expect(sql).toContain('WHERE teacher_id = $1');
    expect(sql).not.toContain('score_entries');
  });

  it('reads persisted lesson scores with teacher-scoped pagination and a safe fact whitelist', async () => {
    const { database, queryShiwen } = createDatabase([lessonRow()]);
    const adapter = new PostgresShiwenReadAdapter(createConfig({}), database);

    const result = await adapter.listLessonScores('TEACHER-001', {
      limit: 20,
      offset: 40,
      search: '2026-07',
    });

    expect(queryShiwen).toHaveBeenCalledWith(
      expect.stringContaining('FROM public.teacher_lesson_score_current'),
      ['TEACHER-001', 20, 40, '2026-07'],
    );
    expect(result[0]).toMatchObject({
      lessonId: 'LESSON-001',
      lessonCount: 42,
      facts: {
        positiveFeedback: true,
        rebooked: true,
        peak: true,
      },
    });
    expect(result[0].dimensions[0]).toMatchObject({
      code: 'USER_FEEDBACK',
      components: [{ code: 'POSITIVE_FEEDBACK', awarded: true }],
    });
    expect(result[0].facts).not.toHaveProperty('isBlocked');
    expect(result[0].facts).not.toHaveProperty('complaintDetail');
    expect(result[0].dimensions[0].components[0]).not.toHaveProperty(
      'internal_reason',
    );
    const sql = String(queryShiwen.mock.calls[0][0]);
    expect(sql).toContain('WHERE teacher_id = $1');
    expect(sql).toContain('count(*) OVER() AS "lessonCount"');
    expect(sql).toContain("lesson_id ILIKE '%' || $4 || '%'");
    expect(sql).toContain('LIMIT $2 OFFSET $3');
  });

  it('requires the configured identity view and rejects invalid aggregate rows', async () => {
    const missingViewAdapter = new PostgresShiwenReadAdapter(
      createConfig({}),
      createDatabase([]).database,
    );
    await expect(
      missingViewAdapter.findIdentity('TEACHER-001'),
    ).rejects.toBeInstanceOf(ShiwenViewNotConfiguredError);

    const malformed = scorecardRow();
    malformed.dimensions = [];
    const invalidScorecardAdapter = new PostgresShiwenReadAdapter(
      createConfig({}),
      createDatabase([malformed]).database,
    );
    await expect(
      invalidScorecardAdapter.findScorecard('TEACHER-001'),
    ).rejects.toBeInstanceOf(ShiwenContractError);
  });
});
