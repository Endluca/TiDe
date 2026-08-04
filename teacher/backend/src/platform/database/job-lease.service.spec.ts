import type { DatabaseService } from './database.service';
import { JobLeaseLostError, JobLeaseService } from './job-lease.service';

describe('JobLeaseService', () => {
  it('skips work when another instance owns the lease', async () => {
    const database = {
      queryTide: jest.fn().mockResolvedValue({ rowCount: 0, rows: [] }),
    } as unknown as DatabaseService;
    const service = new JobLeaseService(database);
    const work = jest.fn();

    await expect(
      service.runExclusive('growth-stage', 'worker-002', 60_000, work),
    ).resolves.toEqual({ acquired: false });
    expect(work).not.toHaveBeenCalled();
  });

  it('runs once and releases only the matching owner', async () => {
    const queryTide = jest
      .fn()
      .mockResolvedValueOnce({ rowCount: 1, rows: [{ job_key: 'cleanup' }] })
      .mockResolvedValueOnce({ rowCount: 1, rows: [] });
    const database = { queryTide } as unknown as DatabaseService;
    const service = new JobLeaseService(database);

    await expect(
      service.runExclusive('cleanup', 'worker-001', 60_000, () =>
        Promise.resolve(3),
      ),
    ).resolves.toEqual({ acquired: true, result: 3 });

    const calls = queryTide.mock.calls as unknown as Array<[string, unknown[]]>;
    expect(String(calls[0][0])).toContain('ON CONFLICT (job_key) DO UPDATE');
    expect(String(calls[1][0])).toContain(
      'WHERE job_key = $1 AND owner_id = $2',
    );
  });

  it('marks the lease lost when heartbeat renewal no longer owns it', async () => {
    jest.useFakeTimers();
    try {
      const queryTide = jest
        .fn()
        .mockResolvedValueOnce({ rowCount: 1, rows: [{ job_key: 'cleanup' }] })
        .mockResolvedValueOnce({ rowCount: 0, rows: [] })
        .mockResolvedValueOnce({ rowCount: 0, rows: [] });
      const database = { queryTide } as unknown as DatabaseService;
      const service = new JobLeaseService(database);
      let finishWork: (() => void) | undefined;
      let workStarted: (() => void) | undefined;
      const started = new Promise<void>((resolve) => {
        workStarted = resolve;
      });

      const running = service.runExclusive(
        'cleanup',
        'worker-001',
        30_000,
        async (lease) => {
          workStarted?.();
          await new Promise<void>((resolve) => {
            finishWork = resolve;
          });
          lease.assertActive();
          return 3;
        },
      );
      await started;
      await jest.advanceTimersByTimeAsync(10_000);
      finishWork?.();

      await expect(running).rejects.toBeInstanceOf(JobLeaseLostError);
      const calls = queryTide.mock.calls as unknown as Array<
        [string, unknown[]]
      >;
      expect(String(calls[1][0])).toContain('lease_until > now()');
      expect(String(calls[2][0])).toContain(
        'WHERE job_key = $1 AND owner_id = $2',
      );
    } finally {
      jest.useRealTimers();
    }
  });

  it('stops at the local deadline while a renewal query is still pending', async () => {
    jest.useFakeTimers();
    try {
      let resolveRenewal: (() => void) | undefined;
      const pendingRenewal = new Promise<{ rowCount: number; rows: never[] }>(
        (resolve) => {
          resolveRenewal = () => resolve({ rowCount: 1, rows: [] });
        },
      );
      const queryTide = jest
        .fn()
        .mockResolvedValueOnce({ rowCount: 1, rows: [{ job_key: 'cleanup' }] })
        .mockReturnValueOnce(pendingRenewal)
        .mockResolvedValueOnce({ rowCount: 0, rows: [] });
      const database = { queryTide } as unknown as DatabaseService;
      const service = new JobLeaseService(database);
      let assertActive: (() => void) | undefined;
      let finishWork: (() => void) | undefined;
      const workStarted = new Promise<void>((resolve) => {
        finishWork = resolve;
      });

      const running = service.runExclusive(
        'cleanup',
        'worker-001',
        30_000,
        async (lease) => {
          assertActive = () => lease.assertActive();
          await workStarted;
          lease.assertActive();
          return 3;
        },
      );
      await Promise.resolve();
      await jest.advanceTimersByTimeAsync(10_000);
      await jest.advanceTimersByTimeAsync(20_000);

      expect(assertActive).toBeDefined();
      expect(() => assertActive?.()).toThrow(JobLeaseLostError);
      finishWork?.();
      resolveRenewal?.();

      await expect(running).rejects.toBeInstanceOf(JobLeaseLostError);
    } finally {
      jest.useRealTimers();
    }
  });
});
