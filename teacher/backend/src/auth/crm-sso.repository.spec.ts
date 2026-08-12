import type { PoolClient } from 'pg';
import type { DatabaseService } from '../platform/database/database.service';
import { CrmSsoBindingError } from './crm-sso.models';
import { CrmSsoRepository } from './crm-sso.repository';

const input = () => ({
  email: 'teacher@51talk.com',
  normalizedEmail: 'teacher@51talk.com',
  teacherId: 'TEACHER-001',
  jtiHash: 'a'.repeat(64),
  issuer: 'crm',
  audience: 'tide',
  exchangeCodeHash: 'b'.repeat(64),
  redirectPath: '/path',
  assertionExpiresAt: new Date(Date.now() + 120_000),
  exchangeExpiresAt: new Date(Date.now() + 60_000),
});

function repository(query: jest.Mock) {
  const client = { query } as unknown as PoolClient;
  const database = {
    withTideTransaction: jest.fn(
      (work: (transaction: PoolClient) => Promise<unknown>) => work(client),
    ),
  } as unknown as DatabaseService;
  return new CrmSsoRepository(database);
}

describe('CrmSsoRepository', () => {
  it('reuses only an exact active email and teacher binding', async () => {
    const query = jest
      .fn()
      .mockResolvedValueOnce({
        rows: [
          {
            accountId: 'account-001',
            normalizedEmail: 'teacher@51talk.com',
            accountStatus: 'ACTIVE',
            teacherId: 'TEACHER-001',
            bindingStatus: 'ACTIVE',
          },
        ],
      })
      .mockResolvedValueOnce({ rowCount: 1, rows: [] });

    await expect(repository(query).createLogin(input())).resolves.toEqual({
      accountId: 'account-001',
      created: false,
    });
    expect(query).toHaveBeenCalledTimes(2);
    expect(String((query.mock.calls[1] as unknown[])[0])).toContain(
      'INSERT INTO tide.crm_sso_logins',
    );
  });

  it('creates only the local account and binding for a real unbound teacher', async () => {
    const query = jest.fn().mockResolvedValue({ rows: [], rowCount: 1 });

    await expect(repository(query).createLogin(input())).resolves.toMatchObject(
      {
        created: true,
      },
    );
    const statements = query.mock.calls.map(([sql]) => String(sql)).join('\n');
    expect(statements).toContain('INSERT INTO tide.user_accounts');
    expect(statements).toContain("NULL, 'ACTIVE', now(), 'CRM_SSO'");
    expect(statements).toContain('INSERT INTO tide.teacher_bindings');
    expect(statements).toContain('CRM_SSO_FIRST_LOGIN');
    expect(statements).toContain('INSERT INTO tide.crm_sso_logins');
  });

  it('blocks cross-account email and teacher bindings', async () => {
    const query = jest.fn().mockResolvedValueOnce({
      rows: [
        {
          accountId: 'email-account',
          normalizedEmail: 'teacher@51talk.com',
          accountStatus: 'ACTIVE',
          teacherId: 'TEACHER-OTHER',
          bindingStatus: 'ACTIVE',
        },
        {
          accountId: 'teacher-account',
          normalizedEmail: 'other@51talk.com',
          accountStatus: 'ACTIVE',
          teacherId: 'TEACHER-001',
          bindingStatus: 'ACTIVE',
        },
      ],
    });

    await expect(repository(query).createLogin(input())).rejects.toMatchObject({
      reason: 'EMAIL_ALREADY_BOUND',
    });
    expect(query).toHaveBeenCalledTimes(1);
  });

  it('maps a duplicate jti to a replay failure', async () => {
    const database = {
      withTideTransaction: jest.fn().mockRejectedValue({
        code: '23505',
        constraint: 'crm_sso_logins_jti_hash_key',
      }),
    } as unknown as DatabaseService;

    await expect(
      new CrmSsoRepository(database).createLogin(input()),
    ).rejects.toEqual(new CrmSsoBindingError('CRM_SSO_REPLAYED'));
  });
});
