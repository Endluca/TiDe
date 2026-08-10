import type { PoolClient } from 'pg';
import type { DatabaseService } from '../../platform/database/database.service';
import type { KuozhiProgressCore } from './kuozhi.models';
import { KuozhiProgressRepository } from './kuozhi-progress.repository';

const progress: KuozhiProgressCore = {
  provider: 'KUOZHI',
  dataMode: 'REAL',
  integrationStatus: 'ACTIVE',
  mappingVersion: 2,
  syncStatus: 'AVAILABLE',
  refreshedAt: '2026-08-04T10:00:00.000Z',
  courses: [],
  completion: { enabled: true, completed: true, reasonCode: 'COMPLETED' },
};

describe('KuozhiProgressRepository', () => {
  it('atomically completes the assignment and saves an idempotent snapshot', async () => {
    const query = jest
      .fn()
      .mockResolvedValueOnce({ rows: [] })
      .mockResolvedValueOnce({ rows: [] })
      .mockResolvedValueOnce({ rows: [] })
      .mockResolvedValueOnce({
        rows: [{ status: 'IN_PROGRESS', stateVersion: '2' }],
      })
      .mockResolvedValueOnce({
        rows: [{ status: 'COMPLETED', stateVersion: '3' }],
      })
      .mockResolvedValueOnce({ rows: [] });
    const database = {
      withTideTransaction: (work: (client: PoolClient) => Promise<unknown>) =>
        work({ query } as unknown as PoolClient),
    } as DatabaseService;
    const repository = new KuozhiProgressRepository(database);

    await expect(
      repository.persistRefresh({
        accountId: '00000000-0000-4000-8000-000000000001',
        taskInstanceId: 'assignment-001',
        expectedStateVersion: 2,
        idempotencyKey: '00000000-0000-4000-8000-000000000002',
        commandId: 'kuozhi-command-001',
        requestHash: 'a'.repeat(64),
        autoCompleteAssignment: true,
        progress,
      }),
    ).resolves.toEqual(
      expect.objectContaining({
        assignment: {
          status: 'COMPLETED',
          stateVersion: 3,
          stateUpdated: true,
        },
      }),
    );
    expect(query).toHaveBeenCalledTimes(6);
  });

  it('stores a completed Kuozhi snapshot without completing a composite assignment', async () => {
    const query = jest
      .fn()
      .mockResolvedValueOnce({ rows: [] })
      .mockResolvedValueOnce({ rows: [] })
      .mockResolvedValueOnce({ rows: [] })
      .mockResolvedValueOnce({
        rows: [{ status: 'IN_PROGRESS', stateVersion: '2' }],
      })
      .mockResolvedValueOnce({ rows: [] });
    const database = {
      withTideTransaction: (work: (client: PoolClient) => Promise<unknown>) =>
        work({ query } as unknown as PoolClient),
    } as DatabaseService;
    const repository = new KuozhiProgressRepository(database);

    await expect(
      repository.persistRefresh({
        accountId: '00000000-0000-4000-8000-000000000001',
        taskInstanceId: 'assignment-001',
        expectedStateVersion: 2,
        idempotencyKey: '00000000-0000-4000-8000-000000000002',
        commandId: 'kuozhi-command-001',
        requestHash: 'a'.repeat(64),
        autoCompleteAssignment: false,
        progress,
      }),
    ).resolves.toEqual(
      expect.objectContaining({
        assignment: {
          status: 'IN_PROGRESS',
          stateVersion: 2,
          stateUpdated: false,
        },
      }),
    );
    expect(query).toHaveBeenCalledTimes(5);
    expect(
      query.mock.calls.some(([sql]) =>
        String(sql).includes('UPDATE public.task_assignments'),
      ),
    ).toBe(false);
  });
});
