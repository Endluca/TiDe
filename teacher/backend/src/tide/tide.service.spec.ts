import { ServiceUnavailableException } from '@nestjs/common';
import type { ShiwenTeacherReadAdapter } from '../integrations/shiwen/shiwen-read.adapters';
import type { ShiwenTeacherScorecard } from '../integrations/shiwen/shiwen-read.models';
import type { TideRepository } from './tide.repository';
import { TideService } from './tide.service';

const principal = {
  accountId: 'account-001',
  sessionId: 'session-001',
};

function component(
  code: string,
  score: number,
  pointsPerUnit: number | null,
  sourceScope: 'LESSON' | 'TEACHER' | 'TASK',
) {
  return {
    code,
    unitCount: score > 0 ? 1 : 0,
    pointsPerUnit,
    score,
    sourceScope,
    sourceMetric: null,
    lessonAttributedCount: 0,
    lessonAttributedScore: 0,
    unattributedScore: score,
    reconciliationStatus: 'MATCHED',
  };
}

function scorecard(): ShiwenTeacherScorecard {
  const calculatedAt = '2026-07-27T08:00:00.000Z';
  return {
    teacherId: 'teacher-001',
    campEnrollmentId: 'camp-001',
    onlineStatus: 'NEW',
    rawTotalScore: 47,
    publicTotalScore: 47,
    graduationState: 'IN_CAMP',
    graduationQualified: false,
    graduationQualifiedAt: null,
    graduationScoreLocked: null,
    goldQualified: false,
    goldStatus: 'NOT_GOLD',
    goldQualifiedAt: null,
    graduationThreshold: 100,
    goldThreshold: 200,
    mandatoryTaskCompletedCount: 2,
    mandatoryTaskTotalCount: 9,
    scoreRuleVersion: 'score-rule-v3',
    calculatedAt,
    source: { teacherSourceStatus: 'CONFIRMED' },
    dimensions: [
      {
        code: 'USER_FEEDBACK',
        score: 0,
        scoreRuleVersion: 'score-rule-v3',
        projectionRevision: 2,
        calculatedAt,
        components: [component('POSITIVE_FEEDBACK', 0, 5, 'LESSON')],
      },
      {
        code: 'RELIABILITY',
        score: 0,
        scoreRuleVersion: 'score-rule-v3',
        projectionRevision: 2,
        calculatedAt,
        components: [component('ON_TIME_COMPLETED', 0, 2, 'LESSON')],
      },
      {
        code: 'CLASS_QUALITY',
        score: 0,
        scoreRuleVersion: 'score-rule-v3',
        projectionRevision: 2,
        calculatedAt,
        components: [
          component('CLASS_QUALITY_PERFECT_COUNT', 0, 1.6, 'LESSON'),
        ],
      },
      {
        code: 'CAPACITY',
        score: 10,
        scoreRuleVersion: 'score-rule-v3',
        projectionRevision: 2,
        calculatedAt,
        components: [component('CAPACITY_PEAK_SLOT_40', 10, 10, 'TEACHER')],
      },
      {
        code: 'NEW_TEACHER_TASK',
        score: 6,
        scoreRuleVersion: 'score-rule-v3',
        projectionRevision: 2,
        calculatedAt,
        components: [
          component('G01', 3, 3, 'TASK'),
          component('G02', 0, 2, 'TASK'),
          component('G03', 0, 2, 'TASK'),
          component('G04', 3, 3, 'TASK'),
        ],
      },
    ],
  };
}

function lessonScore() {
  return {
    teacherId: 'teacher-001',
    lessonId: 'participation:v1:WyJkb20iLCJhcHBvaW50LTAwMSIsMV0',
    sourceRegion: 'dom' as const,
    sourceAppointId: 'appoint-001',
    participationSeq: 1,
    lessonSequence: 21,
    lessonCount: 42,
    scheduledStartAt: '2026-07-27T08:00:00.000Z',
    lessonLocalDate: '2026-07-27',
    lessonLocalTime: '16:00:00',
    lifecycleStatus: 'END',
    validForScoring: true,
    evidenceStatus: 'READY',
    lessonTotalScore: 7,
    scoreRuleVersion: 'score-rule-v3',
    updatedAt: '2026-07-27T09:00:00.000Z',
    facts: {
      late: false,
      earlyLeave: false,
      positiveFeedback: true,
      favorited: false,
      rebooked: true,
      cameraOff: false,
      cpuUsageHigh: false,
      networkDelayHigh: false,
      peak: true,
    },
    dimensions: [
      {
        code: 'USER_FEEDBACK' as const,
        score: 5,
        evidenceStatus: 'READY',
        evidenceCoverage: 'FULL',
        components: [
          {
            code: 'POSITIVE_FEEDBACK',
            score: 5,
            pointsPerUnit: 5,
            awarded: true,
            evidenceStatus: 'READY',
          },
        ],
      },
      {
        code: 'RELIABILITY' as const,
        score: 2,
        evidenceStatus: 'READY',
        evidenceCoverage: 'FULL',
        components: [
          {
            code: 'ON_TIME_COMPLETED',
            score: 2,
            pointsPerUnit: 2,
            awarded: true,
            evidenceStatus: 'READY',
          },
        ],
      },
      {
        code: 'CLASS_QUALITY' as const,
        score: 0,
        evidenceStatus: 'SOURCE_MISSING',
        evidenceCoverage: null,
        components: [
          {
            code: 'CLASS_QUALITY_PERFECT_COUNT',
            score: 0,
            pointsPerUnit: null,
            awarded: false,
            evidenceStatus: 'SOURCE_MISSING',
          },
        ],
      },
    ],
  };
}

function createFixture() {
  const listFixedGrowthTasks = jest.fn().mockResolvedValue([
    {
      taskCode: 'G01',
      title: 'Profile & Credentials Completion',
      status: 'COMPLETED',
      taskUpdatedAt: '2026-07-27T08:00:00.000Z',
    },
    {
      taskCode: 'G02',
      title: 'Platform Policies',
      status: 'ASSIGNED',
      taskUpdatedAt: '2026-07-27T08:00:00.000Z',
    },
    {
      taskCode: 'G03',
      title: 'How to handle different types of students',
      status: 'UNDER_REVIEW',
      taskUpdatedAt: '2026-07-27T08:00:00.000Z',
    },
    {
      taskCode: 'G04',
      title: 'Lesson Preparation',
      status: 'COMPLETED',
      taskUpdatedAt: '2026-07-27T08:00:00.000Z',
    },
  ]);
  const recordSourceRead = jest.fn().mockResolvedValue(undefined);
  const findLatestG01Evidence = jest.fn().mockResolvedValue({
    tesolCompleted: false,
    sourceUpdatedAt: null,
  });
  const repository = {
    findBinding: jest.fn().mockResolvedValue({
      bindingId: 'binding-001',
      teacherId: 'teacher-001',
      email: 'teacher@example.invalid',
    }),
    findLatestG01Evidence,
    listFixedGrowthTasks,
    recordSourceRead,
    listNotifications: jest.fn().mockResolvedValue({
      items: [],
      totalCount: 0,
      unreadCount: 0,
      hasMore: false,
    }),
  } as unknown as TideRepository;
  const findScorecard = jest.fn().mockResolvedValue(scorecard());
  const listLessonScores = jest.fn().mockResolvedValue([lessonScore()]);
  const teacherReader = {
    findIdentity: jest.fn().mockResolvedValue({
      teacherId: 'teacher-001',
      campEnrollmentId: 'camp-001',
      name: 'Teacher',
      timezone: 'Asia/Shanghai',
      campDay: 1,
      graduationState: 'IN_CAMP',
      dataMode: 'REAL',
      sourceUpdatedAt: '2026-07-27T08:00:00.000Z',
    }),
    findScorecard,
    listLessonScores,
  } as unknown as ShiwenTeacherReadAdapter;

  return {
    repository,
    teacherReader,
    findLatestG01Evidence,
    findScorecard,
    listFixedGrowthTasks,
    listLessonScores,
    recordSourceRead,
    service: new TideService(repository, teacherReader),
  };
}

describe('TideService', () => {
  it('returns the live profile and G01 evidence', async () => {
    const fixture = createFixture();

    await expect(fixture.service.getProfile(principal)).resolves.toMatchObject({
      teacherId: 'teacher-001',
      name: 'Teacher',
      freshness: { source: 'LIVE', stale: false },
    });
    const review = await fixture.service.getG01Review(principal);
    expect(review).toMatchObject({
      tesolStatus: 'WAITING',
      externalStatusesComplete: false,
      freshness: { sourceUpdatedAt: null },
    });
    expect(review).not.toHaveProperty('selfIntroStatus');
  });

  it('returns an unavailable status when the TESOL source field is still null', async () => {
    const fixture = createFixture();
    fixture.findLatestG01Evidence.mockResolvedValue({
      tesolCompleted: null,
      sourceUpdatedAt: null,
    });

    await expect(
      fixture.service.getG01Review(principal),
    ).resolves.toMatchObject({
      tesolStatus: 'UNAVAILABLE',
      externalStatusesComplete: false,
      freshness: { sourceUpdatedAt: null },
    });
  });

  it('passes through the scorecard and derives only available G-task score', async () => {
    const fixture = createFixture();

    const result = await fixture.service.getSummary(principal);

    expect(result).toMatchObject({
      rawTotalScore: 47,
      publicTotalScore: 47,
      onlineStatus: 'NEW',
      graduationState: 'IN_CAMP',
      mandatoryTaskCompletedCount: 2,
      mandatoryTaskTotalCount: 9,
      source: { teacherSourceStatus: 'CONFIRMED' },
      availableScore: {
        score: 4,
        items: [
          {
            taskCode: 'G02',
            score: 2,
            taskStatus: 'ASSIGNED',
          },
          {
            taskCode: 'G03',
            score: 2,
            taskStatus: 'UNDER_REVIEW',
          },
        ],
      },
    });
    expect(result).not.toHaveProperty('baseScore');
    expect(result).not.toHaveProperty('scoreOverview');
    expect(result).not.toHaveProperty('pending');
  });

  it('returns an empty state when the teacher has no growth data', async () => {
    const fixture = createFixture();
    fixture.findScorecard.mockResolvedValue(null);

    await expect(fixture.service.getSummary(principal)).resolves.toMatchObject({
      available: false,
      reason: 'NO_GROWTH_DATA',
      freshness: {
        source: 'LIVE',
        sourceUpdatedAt: null,
        stale: false,
      },
    });
    expect(fixture.listFixedGrowthTasks).not.toHaveBeenCalled();
    expect(fixture.recordSourceRead).toHaveBeenCalledWith(
      'binding-001',
      'METRICS',
      true,
    );
  });

  it('hides the available-score block when every G task is terminal', async () => {
    const fixture = createFixture();
    fixture.listFixedGrowthTasks.mockResolvedValue([
      {
        taskCode: 'G01',
        title: null,
        status: 'COMPLETED',
        taskUpdatedAt: '2026-07-27T08:00:00.000Z',
      },
      {
        taskCode: 'G03',
        title: null,
        status: 'CANCELLED',
        taskUpdatedAt: '2026-07-27T08:00:00.000Z',
      },
    ]);

    await expect(fixture.service.getSummary(principal)).resolves.toMatchObject({
      availableScore: null,
    });
  });

  it('does not present retired G00 history as available score', async () => {
    const fixture = createFixture();
    fixture.listFixedGrowthTasks.mockResolvedValue([
      {
        taskCode: 'G00',
        title: 'Retired lesson-preparation history',
        status: 'ASSIGNED',
        taskUpdatedAt: '2026-07-27T08:00:00.000Z',
      },
    ]);

    await expect(fixture.service.getSummary(principal)).resolves.toMatchObject({
      availableScore: null,
    });
  });

  it('returns persisted lesson scores with backend pagination', async () => {
    const fixture = createFixture();

    const result = await fixture.service.getCourses(principal, {
      page: 2,
      pageSize: 20,
      search: 'LESSON-001',
    });

    expect(fixture.listLessonScores).toHaveBeenCalledWith('teacher-001', {
      limit: 20,
      offset: 20,
      search: 'LESSON-001',
    });
    expect(result).toMatchObject({
      page: 2,
      pageSize: 20,
      totalCount: 42,
      items: [
        {
          lessonId: 'participation:v1:WyJkb20iLCJhcHBvaW50LTAwMSIsMV0',
          sourceRegion: 'dom',
          sourceAppointId: 'appoint-001',
          participationSeq: 1,
          lessonTotalScore: 7,
        },
      ],
    });
    expect(result.items[0].dimensions[0]).toMatchObject({
      code: 'USER_FEEDBACK',
      components: [{ code: 'POSITIVE_FEEDBACK', awarded: true }],
    });
    expect(result.items[0]).not.toHaveProperty('scoreExplanation');
  });

  it('records a failed source read and exposes a retryable 503', async () => {
    const fixture = createFixture();
    fixture.findScorecard.mockRejectedValue(new Error('offline'));

    await expect(fixture.service.getSummary(principal)).rejects.toBeInstanceOf(
      ServiceUnavailableException,
    );
    expect(fixture.recordSourceRead).toHaveBeenCalledWith(
      'binding-001',
      'METRICS',
      false,
      'Error',
    );
  });
});
