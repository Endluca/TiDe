import type { PoolClient } from 'pg';
import type { DatabaseService } from '../platform/database/database.service';
import {
  OnboardingIdempotencyConflictError,
  OnboardingRepository,
} from './onboarding.repository';

const acknowledgedAt = new Date('2026-08-07T08:00:00.000Z');
const row = {
  guideCode: 'FIRST_LOGIN',
  guideVersion: 1,
  status: 'COMPLETED',
  idempotencyKey: 'onboarding-key-001',
  requestHash: 'request-hash-001',
  acknowledgedAt,
};

function transactionFixture(results: Array<{ rows: unknown[] }>) {
  const query = jest.fn();
  results.forEach((result) => query.mockResolvedValueOnce(result));
  const withTideTransaction = jest.fn(
    async (work: (client: PoolClient) => Promise<unknown>) =>
      work({ query } as unknown as PoolClient),
  );
  return {
    repository: new OnboardingRepository({
      withTideTransaction,
    } as unknown as DatabaseService),
    query,
  };
}

const input = {
  accountId: 'account-001',
  guideCode: 'FIRST_LOGIN',
  guideVersion: 1,
  outcome: 'COMPLETED' as const,
  idempotencyKey: 'onboarding-key-001',
  requestHash: 'request-hash-001',
};

describe('OnboardingRepository', () => {
  it('reads only the current account and guide version', async () => {
    const queryTide = jest.fn().mockResolvedValue({ rows: [row] });
    const repository = new OnboardingRepository({
      queryTide,
    } as unknown as DatabaseService);

    await expect(
      repository.findState('account-001', 'FIRST_LOGIN', 1),
    ).resolves.toEqual({
      guideCode: 'FIRST_LOGIN',
      guideVersion: 1,
      status: 'COMPLETED',
      acknowledgedAt,
    });
    expect(queryTide).toHaveBeenCalledWith(
      expect.stringContaining('WHERE account_id = $1'),
      ['account-001', 'FIRST_LOGIN', 1],
    );
  });

  it('reads every stored guide state for only the current account', async () => {
    const scoreRow = {
      ...row,
      guideCode: 'SCORE_DETAILS',
      idempotencyKey: 'onboarding-score-001',
    };
    const queryTide = jest.fn().mockResolvedValue({ rows: [row, scoreRow] });
    const repository = new OnboardingRepository({
      queryTide,
    } as unknown as DatabaseService);

    await expect(repository.findStates('account-001')).resolves.toEqual([
      {
        guideCode: 'FIRST_LOGIN',
        guideVersion: 1,
        status: 'COMPLETED',
        acknowledgedAt,
      },
      {
        guideCode: 'SCORE_DETAILS',
        guideVersion: 1,
        status: 'COMPLETED',
        acknowledgedAt,
      },
    ]);
    expect(queryTide).toHaveBeenCalledWith(
      expect.stringContaining('WHERE account_id = $1'),
      ['account-001'],
    );
  });

  it('serializes acknowledgement per account and inserts one fact', async () => {
    const { repository, query } = transactionFixture([
      { rows: [] },
      { rows: [] },
      { rows: [] },
      { rows: [row] },
    ]);

    await expect(repository.acknowledge(input)).resolves.toMatchObject({
      status: 'COMPLETED',
      acknowledgedAt,
    });
    const calls = query.mock.calls as unknown as Array<[string, unknown[]]>;
    expect(calls[0][0]).toContain('pg_advisory_xact_lock');
    expect(calls[0][1]).toEqual(['onboarding:account-001']);
    expect(calls[3][0]).toContain('INSERT INTO tide.account_onboarding_states');
    expect(calls[3][1]).toEqual([
      'account-001',
      'FIRST_LOGIN',
      1,
      'COMPLETED',
      'onboarding-key-001',
      'request-hash-001',
    ]);
    expect(calls.map((call) => call[0]).join('\n')).not.toContain(
      'UPDATE tide.account_onboarding_states',
    );
  });

  it('replays the same key and payload without another insert', async () => {
    const { repository, query } = transactionFixture([
      { rows: [] },
      { rows: [row] },
    ]);

    await expect(repository.acknowledge(input)).resolves.toMatchObject({
      status: 'COMPLETED',
    });
    expect(query).toHaveBeenCalledTimes(2);
  });

  it('rejects reuse of a stored key for a different request', async () => {
    const { repository, query } = transactionFixture([
      { rows: [] },
      { rows: [{ ...row, requestHash: 'another-request-hash' }] },
    ]);

    await expect(repository.acknowledge(input)).rejects.toBeInstanceOf(
      OnboardingIdempotencyConflictError,
    );
    expect(query).toHaveBeenCalledTimes(2);
  });

  it('rejects reuse of one account key across different guide codes', async () => {
    const { repository, query } = transactionFixture([
      { rows: [] },
      { rows: [row] },
    ]);

    await expect(
      repository.acknowledge({ ...input, guideCode: 'SCORE_DETAILS' }),
    ).rejects.toBeInstanceOf(OnboardingIdempotencyConflictError);
    expect(query).toHaveBeenCalledTimes(2);
  });

  it('keeps the first state immutable when another key arrives', async () => {
    const existing = {
      ...row,
      status: 'SKIPPED',
      idempotencyKey: 'another-onboarding-key',
      requestHash: 'another-request-hash',
    };
    const { repository, query } = transactionFixture([
      { rows: [] },
      { rows: [] },
      { rows: [existing] },
    ]);

    await expect(repository.acknowledge(input)).resolves.toMatchObject({
      status: 'SKIPPED',
    });
    expect(query).toHaveBeenCalledTimes(3);
    const calls = query.mock.calls as unknown as Array<[string, unknown[]]>;
    expect(calls.map((call) => call[0]).join('\n')).not.toContain(
      'INSERT INTO tide.account_onboarding_states',
    );
  });
});
