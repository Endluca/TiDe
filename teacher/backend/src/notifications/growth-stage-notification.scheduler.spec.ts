import type { ConfigService } from '@nestjs/config';
import type { AppEnvironment } from '../platform/config/environment';
import type {
  ActiveJobLease,
  JobLeaseService,
} from '../platform/database/job-lease.service';
import type { GrowthStageNotificationRepository } from './growth-stage-notification.repository';
import { GrowthStageNotificationScheduler } from './growth-stage-notification.scheduler';

function config(
  values: Partial<AppEnvironment>,
): ConfigService<AppEnvironment, true> {
  return {
    get: jest.fn((key: keyof AppEnvironment) => values[key]),
  } as unknown as ConfigService<AppEnvironment, true>;
}

function leaseService(acquired = true): {
  service: JobLeaseService;
  runExclusive: jest.Mock;
} {
  const activeLease: ActiveJobLease = {
    signal: new AbortController().signal,
    assertActive: jest.fn(),
  };
  const runExclusive = jest.fn(
    async (
      _jobKey: string,
      _ownerId: string,
      _leaseMs: number,
      work: (lease: ActiveJobLease) => Promise<unknown>,
    ) =>
      acquired
        ? { acquired: true, result: await work(activeLease) }
        : { acquired: false },
  );
  return {
    service: { runExclusive } as unknown as JobLeaseService,
    runExclusive,
  };
}

describe('GrowthStageNotificationScheduler', () => {
  it('is disabled by default', async () => {
    const repository = { scanAndCreate: jest.fn() };
    const scheduler = new GrowthStageNotificationScheduler(
      config({ GROWTH_STAGE_NOTIFICATION_SCHEDULER_ENABLED: false }),
      repository as unknown as GrowthStageNotificationRepository,
      leaseService().service,
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
      leaseService().service,
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
      leaseService().service,
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

  it('skips the scan when another pod owns the lease', async () => {
    const repository = { scanAndCreate: jest.fn() };
    const leases = leaseService(false);
    const scheduler = new GrowthStageNotificationScheduler(
      config({ BACKGROUND_JOB_LEASE_MS: 180_000 }),
      repository as unknown as GrowthStageNotificationRepository,
      leases.service,
    );

    await scheduler.runOnce({ throwOnError: true });

    expect(leases.runExclusive).toHaveBeenCalledWith(
      'growth-stage-notifications',
      expect.stringContaining(`:${process.pid}:`),
      180_000,
      expect.any(Function),
    );
    expect(repository.scanAndCreate).not.toHaveBeenCalled();
  });
});
