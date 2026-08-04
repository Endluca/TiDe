import type { ConfigService } from '@nestjs/config';
import type { AppEnvironment } from '../platform/config/environment';
import type {
  ActiveJobLease,
  JobLeaseService,
} from '../platform/database/job-lease.service';
import type { SystemNotificationRepository } from './system-notification.repository';
import { SystemNotificationPublisher } from './system-notification.publisher';

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

describe('SystemNotificationPublisher', () => {
  it('stays off when the shared background-job switch is disabled', async () => {
    const config = {
      get: jest.fn((key: keyof AppEnvironment) => {
        if (key === 'BACKGROUND_JOBS_ENABLED') return false;
        if (key === 'SYSTEM_NOTIFICATION_PUBLISHER_ENABLED') return true;
        return undefined;
      }),
    } as unknown as ConfigService<AppEnvironment, true>;
    const repository = {
      syncPublication: jest.fn(),
      publishDue: jest.fn(),
    };
    const publisher = new SystemNotificationPublisher(
      config,
      repository as unknown as SystemNotificationRepository,
      leaseService().service,
    );

    await publisher.onModuleInit();
    await publisher.runOnce({ throwOnError: true });

    expect(repository.syncPublication).not.toHaveBeenCalled();
    expect(repository.publishDue).not.toHaveBeenCalled();
  });

  it('does not publish when another pod owns the global lease', async () => {
    const config = {
      get: jest.fn((key: keyof AppEnvironment) => {
        if (key === 'BACKGROUND_JOBS_ENABLED') return true;
        if (key === 'BACKGROUND_JOB_LEASE_MS') return 180_000;
        return undefined;
      }),
    } as unknown as ConfigService<AppEnvironment, true>;
    const repository = {
      syncPublication: jest.fn(),
      publishDue: jest.fn(),
    };
    const leases = leaseService(false);
    const publisher = new SystemNotificationPublisher(
      config,
      repository as unknown as SystemNotificationRepository,
      leases.service,
    );

    await publisher.runOnce({ throwOnError: true });

    expect(leases.runExclusive).toHaveBeenCalledWith(
      'system-notification-publisher',
      expect.stringContaining(`:${process.pid}:`),
      180_000,
      expect.any(Function),
    );
    expect(repository.syncPublication).not.toHaveBeenCalled();
    expect(repository.publishDue).not.toHaveBeenCalled();
  });
});
