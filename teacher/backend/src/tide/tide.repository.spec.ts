import type { DatabaseService } from '../platform/database/database.service';
import { TideRepository } from './tide.repository';

describe('TideRepository G01 evidence', () => {
  it('reads the one-row teacher source without inventing freshness metadata', async () => {
    const queryTide = jest.fn().mockResolvedValue({
      rows: [{ selfIntroduced: null, tesolCompleted: false }],
    });
    const repository = new TideRepository({
      queryTide,
    } as unknown as DatabaseService);

    await expect(
      repository.findLatestG01Evidence('teacher-001'),
    ).resolves.toEqual({
      selfIntroduced: null,
      tesolCompleted: false,
      sourceUpdatedAt: null,
    });

    const calls = queryTide.mock.calls as unknown as Array<
      [unknown, unknown[] | undefined]
    >;
    const sql = String(calls[0][0]);
    expect(sql).toContain('FROM public.teacher_source_wide');
    expect(sql).toContain('WHERE tchr_id = $1');
    expect(sql).not.toContain('teacher_metric_snapshots');
    expect(sql).not.toContain('updated_at');
    expect(calls[0][1]).toEqual(['teacher-001']);
  });
});

describe('TideRepository notifications', () => {
  it('scopes the unified message list and task target to the current teacher', async () => {
    const queryTide = jest
      .fn()
      .mockResolvedValueOnce({
        rows: [
          {
            sourceNotificationId: 'system:notification-001',
            source: 'SYSTEM',
            typeCode: 'PERSONALIZED_TASK_ASSIGNED',
            title: 'Prepare for your next class',
            body: 'A new improvement task is ready for you.',
            relatedTaskCode: 'NT-Q03',
            relatedTaskInstanceId: 'assignment-001',
            actionType: 'TASK_DETAIL',
            actionTarget: '/task/assignment-001',
            actionAvailable: true,
            expiresAt: new Date('2026-07-25T08:00:00.000Z'),
            expired: false,
            issuedAt: new Date('2026-07-24T08:00:00.000Z'),
            read: false,
            clicked: false,
          },
        ],
      })
      .mockResolvedValueOnce({
        rows: [{ totalCount: '1', unreadCount: '1' }],
      });
    const repository = new TideRepository({
      queryTide,
    } as unknown as DatabaseService);

    const page = await repository.listNotifications('teacher-001', {
      limit: 20,
      unreadOnly: false,
      beforeIssuedAt: null,
      beforeSourceNotificationId: null,
    });

    const calls = queryTide.mock.calls as unknown as Array<
      [unknown, unknown[] | undefined]
    >;
    const listSql = String(calls[0][0]);
    const listValues = calls[0][1] ?? [];
    expect(listSql).toContain('WHERE notification.teacher_id = $1');
    expect(listSql).toContain(
      'action_assignment.teacher_id = notification.teacher_id',
    );
    expect(listSql).toContain(
      "notification.type_code = 'GROWTH_STAGE_AVAILABLE'",
    );
    expect(listSql).toContain(
      "'/path?stage=' || (notification.payload->>'stageNumber')",
    );
    expect(listValues[0]).toBe('teacher-001');
    expect(page.items[0]).toMatchObject({
      typeCode: 'PERSONALIZED_TASK_ASSIGNED',
      relatedTaskInstanceId: 'assignment-001',
      actionType: 'TASK_DETAIL',
      actionTarget: '/task/assignment-001',
    });
  });
});
