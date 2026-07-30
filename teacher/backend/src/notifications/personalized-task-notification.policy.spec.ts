import {
  buildPersonalizedTaskReminders,
  personalizedTaskReminderTitle,
  type PersonalizedTaskReminderAssignment,
} from './personalized-task-notification.policy';

const now = new Date('2026-07-24T08:00:00.000Z');
const rolloutAt = new Date('2026-07-24T06:00:00.000Z');

function assignment(
  overrides: Partial<PersonalizedTaskReminderAssignment> = {},
): PersonalizedTaskReminderAssignment {
  return {
    assignmentId: 'assignment-001',
    teacherId: 'teacher-001',
    taskCode: 'NT-Q03',
    displayTitle: 'Prepare for your next class',
    status: 'ASSIGNED',
    assignedAt: new Date('2026-07-24T07:00:00.000Z'),
    dueAt: new Date('2026-07-26T08:00:00.000Z'),
    timezoneUsed: 'Asia/Shanghai',
    timezoneSource: 'TEACHER_PROFILE',
    timezoneVerifiedAt: new Date('2026-07-24T06:30:00.000Z'),
    ...overrides,
  };
}

describe('personalized task notification policy', () => {
  it('creates one new-task reminder after rollout with the exact task target', () => {
    const reminders = buildPersonalizedTaskReminders(
      assignment(),
      now,
      rolloutAt,
    );

    expect(reminders).toEqual([
      expect.objectContaining({
        teacherId: 'teacher-001',
        typeCode: 'PERSONALIZED_TASK_ASSIGNED',
        title: 'Prepare for your next class is ready for you',
        body: 'This improvement task is ready in My TIDE. It is designed to help you strengthen one part of your teaching. Start whenever you’re ready.',
        actionType: 'TASK_DETAIL',
        actionTarget: '/task/assignment-001',
        dedupeKey: 'personalized-task-assigned:assignment-001',
        expiresAt: new Date('2026-07-26T08:00:00.000Z'),
      }),
    ]);
  });

  it('does not backfill a new-task reminder for assignments before rollout', () => {
    const reminders = buildPersonalizedTaskReminders(
      assignment({
        assignedAt: new Date('2026-07-24T05:59:59.999Z'),
      }),
      now,
      rolloutAt,
    );

    expect(
      reminders.find((reminder) => reminder.reminderType === 'ASSIGNED'),
    ).toBeUndefined();
  });

  it.each(['ASSIGNED', 'VIEWED', 'IN_PROGRESS', 'FAILED'])(
    'creates one due reminder within 24 hours for %s',
    (status) => {
      const reminders = buildPersonalizedTaskReminders(
        assignment({
          assignedAt: new Date('2026-07-23T05:00:00.000Z'),
          dueAt: new Date('2026-07-25T07:59:59.999Z'),
          status,
        }),
        now,
        rolloutAt,
      );

      expect(reminders).toHaveLength(1);
      expect(reminders[0]).toMatchObject({
        typeCode: 'PERSONALIZED_TASK_DUE_24H',
        title: 'Prepare for your next class is due soon',
        dedupeKey: 'personalized-task-due-24h:assignment-001',
        actionTarget: '/task/assignment-001',
      });
      expect(reminders[0].body).toContain(
        'This improvement task is due Jul 25, 2026, 3:59 PM GMT+8.',
      );
    },
  );

  it.each([
    'SUBMITTED',
    'UNDER_REVIEW',
    'COMPLETED',
    'EXPIRED',
    'WAIVED',
    'CANCELLED',
  ])('does not create a due reminder for %s', (status) => {
    const reminders = buildPersonalizedTaskReminders(
      assignment({
        assignedAt: new Date('2026-07-23T05:00:00.000Z'),
        dueAt: new Date('2026-07-24T09:00:00.000Z'),
        status,
      }),
      now,
      rolloutAt,
    );

    expect(reminders).toHaveLength(0);
  });

  it('does not create a due reminder after the deadline', () => {
    const reminders = buildPersonalizedTaskReminders(
      assignment({
        assignedAt: new Date('2026-07-23T05:00:00.000Z'),
        dueAt: new Date('2026-07-24T07:59:59.999Z'),
      }),
      now,
      rolloutAt,
    );

    expect(reminders).toHaveLength(0);
  });

  it('falls back to task code when a display title contains internal wording', () => {
    expect(
      personalizedTaskReminderTitle(
        'Internal high-risk complaint evidence',
        'NT-Q03',
      ),
    ).toBe('NT-Q03');
    expect(
      personalizedTaskReminderTitle('Prepare for your next class', 'NT-Q03'),
    ).toBe('Prepare for your next class');
  });
});
