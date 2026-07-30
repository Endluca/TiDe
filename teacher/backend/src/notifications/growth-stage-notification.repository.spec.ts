import type { DatabaseService } from '../platform/database/database.service';
import { GrowthStageNotificationRepository } from './growth-stage-notification.repository';

const now = new Date('2026-07-24T10:00:00.000Z');

describe('GrowthStageNotificationRepository', () => {
  it('updates one bounded batch and creates only true stage-upgrade messages', async () => {
    const queryTide = jest.fn().mockResolvedValue({
      rows: [
        {
          scannedTeachers: 5,
          initializedTeachers: 3,
          createdNotifications: 2,
        },
      ],
    });
    const repository = new GrowthStageNotificationRepository({
      queryTide,
    } as unknown as DatabaseService);

    await expect(repository.scanAndCreate(now, 200)).resolves.toEqual({
      scannedTeachers: 5,
      initializedTeachers: 3,
      createdNotifications: 2,
    });

    expect(queryTide).toHaveBeenCalledTimes(1);
    const [sql, values] = queryTide.mock.calls[0] as [string, unknown[]];
    expect(sql).toContain('stage_one_completed = 4');
    expect(sql).toContain('stage_two_completed = 3');
    expect(sql).toContain('LIMIT $2');
    expect(sql).toContain('INSERT INTO tide.growth_stage_notification_states');
    expect(sql).toContain('INSERT INTO tide.system_notifications');
    expect(sql).toContain('ON CONFLICT (dedupe_key) DO NOTHING');
    expect(sql).not.toContain('UPDATE public.task_assignments');
    expect(values).toEqual([now, 200]);
  });

  it('normalizes database count strings', async () => {
    const repository = new GrowthStageNotificationRepository({
      queryTide: jest.fn().mockResolvedValue({
        rows: [
          {
            scannedTeachers: '1',
            initializedTeachers: '1',
            createdNotifications: '0',
          },
        ],
      }),
    } as unknown as DatabaseService);

    await expect(repository.scanAndCreate(now)).resolves.toEqual({
      scannedTeachers: 1,
      initializedTeachers: 1,
      createdNotifications: 0,
    });
  });
});
