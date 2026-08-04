import type { ConfigService } from '@nestjs/config';
import type { AppEnvironment } from '../platform/config/environment';
import type {
  ActiveJobLease,
  JobLeaseService,
} from '../platform/database/job-lease.service';
import { SupportTicketScheduler } from './support-ticket.scheduler';
import type { SupportTicketService } from './support-ticket.service';

function config(
  values: Partial<AppEnvironment>,
): ConfigService<AppEnvironment, true> {
  return {
    get: jest.fn((key: keyof AppEnvironment) => values[key]),
  } as unknown as ConfigService<AppEnvironment, true>;
}

function leaseService(acquired: boolean): {
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

describe('SupportTicketScheduler', () => {
  it('skips cleanup when another pod owns the lease', async () => {
    const tickets = { closeExpiredAndCleanup: jest.fn() };
    const leases = leaseService(false);
    const scheduler = new SupportTicketScheduler(
      config({
        BACKGROUND_JOBS_ENABLED: true,
        BACKGROUND_JOB_LEASE_MS: 180_000,
        SUPPORT_TICKET_CLEANUP_BATCH_SIZE: 20,
      }),
      tickets as unknown as SupportTicketService,
      leases.service,
    );

    await scheduler.runOnce({ throwOnError: true });

    expect(leases.runExclusive).toHaveBeenCalledWith(
      'support-ticket-cleanup',
      expect.stringContaining(`:${process.pid}:`),
      180_000,
      expect.any(Function),
    );
    expect(tickets.closeExpiredAndCleanup).not.toHaveBeenCalled();
  });

  it('passes an active-lease assertion into cleanup', async () => {
    const tickets = {
      closeExpiredAndCleanup: jest.fn().mockResolvedValue(undefined),
    };
    const leases = leaseService(true);
    const scheduler = new SupportTicketScheduler(
      config({
        BACKGROUND_JOBS_ENABLED: true,
        BACKGROUND_JOB_LEASE_MS: 180_000,
        SUPPORT_TICKET_CLEANUP_BATCH_SIZE: 20,
      }),
      tickets as unknown as SupportTicketService,
      leases.service,
    );

    await scheduler.runOnce({ throwOnError: true });

    expect(tickets.closeExpiredAndCleanup).toHaveBeenCalledWith(
      20,
      expect.any(Function),
    );
  });
});
