import type { ConfigService } from '@nestjs/config';
import type { AppEnvironment } from '../platform/config/environment';
import type { PersonalizedTaskNotificationRepository } from './personalized-task-notification.repository';
import { PersonalizedTaskNotificationScheduler } from './personalized-task-notification.scheduler';

function config(
  values: Partial<AppEnvironment>,
): ConfigService<AppEnvironment, true> {
  return {
    get: jest.fn((key: keyof AppEnvironment) => values[key]),
  } as unknown as ConfigService<AppEnvironment, true>;
}

describe('PersonalizedTaskNotificationScheduler', () => {
  it('is disabled by default', async () => {
    const repository = { scanAndCreate: jest.fn() };
    const scheduler = new PersonalizedTaskNotificationScheduler(
      config({
        PERSONALIZED_TASK_NOTIFICATION_SCHEDULER_ENABLED: false,
      }),
      repository as unknown as PersonalizedTaskNotificationRepository,
    );

    await scheduler.onModuleInit();

    expect(repository.scanAndCreate).not.toHaveBeenCalled();
  });

  it('stays off when the shared background-job switch is disabled', async () => {
    const repository = { scanAndCreate: jest.fn() };
    const scheduler = new PersonalizedTaskNotificationScheduler(
      config({
        BACKGROUND_JOBS_ENABLED: false,
        PERSONALIZED_TASK_NOTIFICATION_SCHEDULER_ENABLED: true,
        PERSONALIZED_TASK_NOTIFICATION_ROLLOUT_AT: '2026-07-24T14:00:00+08:00',
      }),
      repository as unknown as PersonalizedTaskNotificationRepository,
    );

    await scheduler.onModuleInit();
    await scheduler.runOnce({ throwOnError: true });

    expect(repository.scanAndCreate).not.toHaveBeenCalled();
  });

  it('does not overlap concurrent scans', async () => {
    let finishScan: ((value: unknown) => void) | undefined;
    const repository = {
      scanAndCreate: jest.fn(
        () =>
          new Promise((resolve) => {
            finishScan = resolve;
          }),
      ),
    };
    const scheduler = new PersonalizedTaskNotificationScheduler(
      config({
        PERSONALIZED_TASK_NOTIFICATION_ROLLOUT_AT: '2026-07-24T14:00:00+08:00',
      }),
      repository as unknown as PersonalizedTaskNotificationRepository,
    );

    const first = scheduler.runOnce({ throwOnError: true });
    const overlapping = scheduler.runOnce({ throwOnError: true });
    await Promise.resolve();

    expect(repository.scanAndCreate).toHaveBeenCalledTimes(1);
    await expect(overlapping).resolves.toBeUndefined();
    finishScan?.({ scannedAssignments: 1, createdNotifications: 1 });
    await expect(first).resolves.toBeUndefined();
  });
});
