import type { DatabaseService } from './database.service';
import { JobLeaseService } from './job-lease.service';

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
});
