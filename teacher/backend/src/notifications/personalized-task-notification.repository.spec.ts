import type { PoolClient } from 'pg';
import type { DatabaseService } from '../platform/database/database.service';
import { PersonalizedTaskNotificationRepository } from './personalized-task-notification.repository';

const now = new Date('2026-07-24T08:00:00.000Z');
const rolloutAt = new Date('2026-07-24T06:00:00.000Z');

const candidate = {
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
};

function createRepository(query: jest.Mock) {
  const database = {
    withTideTransaction: jest.fn(
      (callback: (client: PoolClient) => Promise<unknown>) =>
        callback({ query } as unknown as PoolClient),
    ),
  } as unknown as DatabaseService;
  return new PersonalizedTaskNotificationRepository(database);
}

describe('PersonalizedTaskNotificationRepository', () => {
  it('reads only matching assignment fields and inserts the current teacher notification once', async () => {
    const query = jest
      .fn()
      .mockResolvedValueOnce({ rows: [candidate] })
      .mockResolvedValueOnce({ rowCount: 1 });
    const repository = createRepository(query);

    await expect(repository.scanAndCreate(now, rolloutAt)).resolves.toEqual({
      scannedAssignments: 1,
      createdNotifications: 1,
    });

    const calls = query.mock.calls as unknown as Array<
      [unknown, unknown[] | undefined]
    >;
    const selectSql = String(calls[0][0]);
    expect(selectSql).toContain('FROM public.task_assignments');
    expect(selectSql).toContain(
      "assignment.task_kind = 'PERSONALIZED_IMPROVEMENT'",
    );
    expect(selectSql).toContain("assignment.creator_system = 'TRIGGER_CENTER'");
    expect(selectSql).toContain('assignment.timezone_used');
    expect(selectSql).toContain('assignment.timezone_source');
    expect(selectSql).toContain('assignment.timezone_verified_at');
    expect(selectSql).not.toContain('evidence_snapshot');
    expect(selectSql).not.toMatch(/\bwhy\b/iu);

    const insertSql = String(calls[1][0]);
    const insertValues = calls[1][1] ?? [];
    expect(insertSql).toContain('INSERT INTO tide.system_notifications');
    expect(insertSql).toContain('FROM unnest(');
    expect(insertSql).toContain('ON CONFLICT (dedupe_key) DO NOTHING');
    expect(insertSql).not.toContain('INSERT INTO public.notifications');
    expect(insertValues[1]).toEqual(['teacher-001']);
    expect(insertValues[5]).toEqual(['/task/assignment-001']);
    expect(insertValues[7]).toEqual([
      'personalized-task-assigned:assignment-001',
    ]);
  });

  it('uses the dedupe conflict to avoid creating the same reminder again', async () => {
    const query = jest
      .fn()
      .mockResolvedValueOnce({ rows: [candidate] })
      .mockResolvedValueOnce({ rowCount: 1 })
      .mockResolvedValueOnce({ rows: [candidate] })
      .mockResolvedValueOnce({ rowCount: 0 });
    const repository = createRepository(query);

    await expect(
      repository.scanAndCreate(now, rolloutAt),
    ).resolves.toMatchObject({ createdNotifications: 1 });
    await expect(
      repository.scanAndCreate(now, rolloutAt),
    ).resolves.toMatchObject({ createdNotifications: 0 });

    const calls = query.mock.calls as unknown as Array<[unknown]>;
    const allSql = calls.map((call) => String(call[0])).join('\n');
    expect(allSql).not.toContain('INSERT INTO public.notifications');
    expect(allSql).not.toContain('UPDATE public.task_assignments');
    expect(allSql).not.toContain('score_entries');
  });
});
