import type { ConfigService } from '@nestjs/config';
import type { AppEnvironment } from '../platform/config/environment';
import type { GrowthStageNotificationRepository } from './growth-stage-notification.repository';
import { GrowthStageNotificationScheduler } from './growth-stage-notification.scheduler';

function config(
  values: Partial<AppEnvironment>,
): ConfigService<AppEnvironment, true> {
  return {
    get: jest.fn((key: keyof AppEnvironment) => values[key]),
  } as unknown as ConfigService<AppEnvironment, true>;
}

describe('GrowthStageNotificationScheduler', () => {
  it('is disabled by default', async () => {
    const repository = { scanAndCreate: jest.fn() };
    const scheduler = new GrowthStageNotificationScheduler(
      config({ GROWTH_STAGE_NOTIFICATION_SCHEDULER_ENABLED: false }),
      repository as unknown as GrowthStageNotificationRepository,
    );

    await scheduler.onModuleInit();

    expect(repository.scanAndCreate).not.toHaveBeenCalled();
  });

  it('stays off when the shared background-job switch is disabled', async () => {
    const repository = { scanAndCreate: jest.fn() };
    const scheduler = new GrowthStageNotificationScheduler(
      config({
        BACKGROUND_JOBS_ENABLED: false,
        GROWTH_STAGE_NOTIFICATION_SCHEDULER_ENABLED: true,
      }),
      repository as unknown as GrowthStageNotificationRepository,
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
    const scheduler = new GrowthStageNotificationScheduler(
      config({}),
      repository as unknown as GrowthStageNotificationRepository,
    );

    const first = scheduler.runOnce({ throwOnError: true });
    const overlapping = scheduler.runOnce({ throwOnError: true });
    await Promise.resolve();

    expect(repository.scanAndCreate).toHaveBeenCalledTimes(1);
    await expect(overlapping).resolves.toBeUndefined();
    finishScan?.({
      scannedTeachers: 1,
      initializedTeachers: 0,
      createdNotifications: 1,
    });
    await expect(first).resolves.toBeUndefined();
  });
});
