import type { PoolClient } from 'pg';
import type { DatabaseService } from '../platform/database/database.service';
import { PasswordResetRepository } from './password-reset.repository';

function createRepository(query: jest.Mock) {
  const database = {
    withTideTransaction: jest.fn(
      (callback: (client: PoolClient) => Promise<unknown>) =>
        callback({ query } as unknown as PoolClient),
    ),
  } as unknown as DatabaseService;
  return new PasswordResetRepository(database);
}

describe('PasswordResetRepository', () => {
  it('creates one deduplicated account security notification after a valid reset', async () => {
    const query = jest
      .fn()
      .mockResolvedValueOnce({
        rows: [
          {
            tokenId: 'token-001',
            accountId: 'account-001',
            expiresAt: new Date(Date.now() + 60_000),
            usedAt: null,
            revokedAt: null,
            accountStatus: 'ACTIVE',
          },
        ],
      })
      .mockResolvedValue({ rows: [], rowCount: 1 });
    const repository = createRepository(query);

    await expect(
      repository.consumeToken('token-hash', 'next-password-hash'),
    ).resolves.toBe(true);

    const calls = query.mock.calls as unknown as Array<
      [unknown, unknown[] | undefined]
    >;
    const notificationCall = calls.find((call) =>
      String(call[0]).includes('INSERT INTO tide.system_notifications'),
    );
    expect(notificationCall).toBeDefined();
    expect(String(notificationCall?.[0])).toContain(
      "'ACCOUNT_SECURITY_PASSWORD_CHANGED'",
    );
    expect(String(notificationCall?.[0])).toContain(
      "'Your password was changed'",
    );
    expect(String(notificationCall?.[0])).toContain(
      'ON CONFLICT (dedupe_key) DO NOTHING',
    );
    expect(notificationCall?.[1]).toEqual(['account-001', 'token-001']);
  });

  it('does not create a notification when the reset token is invalid', async () => {
    const query = jest.fn().mockResolvedValueOnce({ rows: [] });
    const repository = createRepository(query);

    await expect(
      repository.consumeToken('invalid-token', 'next-password-hash'),
    ).resolves.toBe(false);

    const calls = query.mock.calls as unknown as Array<[unknown]>;
    const allSql = calls.map((call) => String(call[0])).join('\n');
    expect(allSql).not.toContain('INSERT INTO tide.system_notifications');
  });
});
