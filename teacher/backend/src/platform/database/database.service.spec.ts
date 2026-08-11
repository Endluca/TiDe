import type { Pool, PoolClient, QueryResult, QueryResultRow } from 'pg';
import type { ConfigService } from '@nestjs/config';
import type { AppEnvironment } from '../config/environment';
import { DatabaseService } from './database.service';

function createQueryResult(): QueryResult<QueryResultRow> {
  return {
    command: 'SELECT',
    rowCount: 1,
    oid: 0,
    fields: [],
    rows: [{ value: 1 }],
  };
}

function createReadinessResult(
  ready: boolean,
): QueryResult<{ ready: boolean }> {
  return {
    command: 'SELECT',
    rowCount: 1,
    oid: 0,
    fields: [],
    rows: [{ ready }],
  };
}

function config(
  values: Partial<AppEnvironment>,
): ConfigService<AppEnvironment, true> {
  return {
    get: jest.fn((key: keyof AppEnvironment) => values[key]),
  } as unknown as ConfigService<AppEnvironment, true>;
}

describe('DatabaseService', () => {
  it('reports unconfigured optional connections', async () => {
    const service = new DatabaseService(null, null);

    await expect(service.checkReadiness()).resolves.toEqual({
      tide: 'not_configured',
      shiwenRead: 'not_configured',
    });
    expect(service.getPoolStats()).toEqual({
      tide: {
        configured: false,
        totalConnections: 0,
        idleConnections: 0,
        waitingRequests: 0,
      },
      shiwenRead: {
        configured: false,
        totalConnections: 0,
        idleConnections: 0,
        waitingRequests: 0,
      },
    });
  });

  it('exposes pool pressure without querying the database', () => {
    const pool = {
      totalCount: 8,
      idleCount: 2,
      waitingCount: 3,
    } as Pool;
    const service = new DatabaseService(pool, null);

    expect(service.getPoolStats().tide).toEqual({
      configured: true,
      totalConnections: 8,
      idleConnections: 2,
      waitingRequests: 3,
    });
  });

  it('commits a successful TIDE transaction', async () => {
    const query = jest.fn().mockResolvedValue(createQueryResult());
    const release = jest.fn();
    const client = { query, release } as unknown as PoolClient;
    const pool = {
      connect: jest.fn().mockResolvedValue(client),
    } as unknown as Pool;
    const service = new DatabaseService(pool, null);

    await expect(
      service.withTideTransaction(async (transaction) => {
        await transaction.query('SELECT 1');
        return 'done';
      }),
    ).resolves.toBe('done');

    expect(query).toHaveBeenNthCalledWith(1, 'BEGIN');
    expect(query).toHaveBeenNthCalledWith(2, 'SELECT 1');
    expect(query).toHaveBeenNthCalledWith(3, 'COMMIT');
    expect(release).toHaveBeenCalledTimes(1);
  });

  it('rolls back and releases a failed TIDE transaction', async () => {
    const query = jest.fn().mockResolvedValue(createQueryResult());
    const release = jest.fn();
    const client = { query, release } as unknown as PoolClient;
    const pool = {
      connect: jest.fn().mockResolvedValue(client),
    } as unknown as Pool;
    const service = new DatabaseService(pool, null);

    await expect(
      service.withTideTransaction(() => Promise.reject(new Error('failed'))),
    ).rejects.toThrow('failed');

    expect(query).toHaveBeenNthCalledWith(1, 'BEGIN');
    expect(query).toHaveBeenNthCalledWith(2, 'ROLLBACK');
    expect(release).toHaveBeenCalledTimes(1);
  });

  it('opens the Shiwen connection as a read-only transaction', async () => {
    const query = jest.fn().mockResolvedValue(createQueryResult());
    const release = jest.fn();
    const client = { query, release } as unknown as PoolClient;
    const pool = {
      connect: jest.fn().mockResolvedValue(client),
    } as unknown as Pool;
    const service = new DatabaseService(null, pool);

    await service.withShiwenReadTransaction(async (transaction) => {
      await transaction.query('SELECT 1');
    });

    expect(query).toHaveBeenNthCalledWith(1, 'BEGIN READ ONLY');
    expect(query).toHaveBeenNthCalledWith(2, 'SELECT 1');
    expect(query).toHaveBeenNthCalledWith(3, 'COMMIT');
    expect(release).toHaveBeenCalledTimes(1);
  });

  it('uses the pool directly for a single Shiwen SELECT', async () => {
    const query = jest.fn().mockResolvedValue(createQueryResult());
    const connect = jest.fn();
    const pool = { query, connect } as unknown as Pool;
    const service = new DatabaseService(null, pool);

    await service.queryShiwen('SELECT value FROM source WHERE id = $1', [
      'teacher-001',
    ]);

    expect(query).toHaveBeenCalledWith(
      'SELECT value FROM source WHERE id = $1',
      ['teacher-001'],
    );
    expect(connect).not.toHaveBeenCalled();
  });

  it('checks the relations required by direct Shiwen reads', async () => {
    const query = jest.fn().mockResolvedValue(createReadinessResult(true));
    const pool = { query } as unknown as Pool;
    const service = new DatabaseService(
      null,
      pool,
      config({ SHIWEN_READ_MODE: 'DIRECT_TABLES' }),
    );

    await expect(service.checkReadiness()).resolves.toEqual({
      tide: 'not_configured',
      shiwenRead: 'ok',
    });
    expect(query).toHaveBeenCalledWith(
      expect.stringContaining('public.teacher_scorecard_current'),
    );
    expect(query).toHaveBeenCalledWith(
      expect.stringContaining('public.teacher_lesson_score_current'),
    );
    expect(query).toHaveBeenCalledWith(
      expect.stringContaining('public.teachers'),
    );
  });

  it('fails readiness when VIEWS mode has no identity view', async () => {
    const query = jest.fn().mockResolvedValue(createReadinessResult(true));
    const pool = { query } as unknown as Pool;
    const service = new DatabaseService(
      null,
      pool,
      config({ SHIWEN_READ_MODE: 'VIEWS' }),
    );

    await expect(service.checkReadiness()).resolves.toEqual({
      tide: 'not_configured',
      shiwenRead: 'unavailable',
    });
    expect(query).not.toHaveBeenCalled();
  });

  it('checks the configured identity view in VIEWS mode', async () => {
    const query = jest.fn().mockResolvedValue(createReadinessResult(true));
    const pool = { query } as unknown as Pool;
    const service = new DatabaseService(
      null,
      pool,
      config({
        SHIWEN_READ_MODE: 'VIEWS',
        SHIWEN_TEACHER_IDENTITY_VIEW: 'shiwen.teacher_identity_v1',
      }),
    );

    await expect(service.checkReadiness()).resolves.toEqual({
      tide: 'not_configured',
      shiwenRead: 'ok',
    });
    expect(query).toHaveBeenCalledWith(
      expect.stringContaining('"shiwen"."teacher_identity_v1"'),
    );
  });

  it('requires the exact production migration head and current core objects', async () => {
    const query = jest.fn().mockResolvedValue(createReadinessResult(true));
    const pool = { query } as unknown as Pool;
    const service = new DatabaseService(
      pool,
      null,
      config({ NODE_ENV: 'production' }),
    );

    await expect(service.checkReadiness()).resolves.toEqual({
      tide: 'ok',
      shiwenRead: 'not_configured',
    });
    expect(query).toHaveBeenCalledWith(
      expect.stringContaining('0037_g04_remove_device_check'),
    );
    expect(query).toHaveBeenCalledWith(
      expect.stringContaining('0032_first_login_onboarding'),
    );
    expect(query).toHaveBeenCalledWith(
      expect.stringContaining('0033_g01_tesol_only'),
    );
    expect(query).toHaveBeenCalledWith(
      expect.stringContaining('2026-08-11-tesol-only-v1'),
    );
    expect(query).toHaveBeenCalledWith(
      expect.stringContaining("OR rule.rule_type = 'G01_EXTERNAL_STATUS'"),
    );
    expect(query).toHaveBeenCalledWith(
      expect.stringContaining('0031_g04_independent_sections'),
    );
    expect(query).toHaveBeenCalledWith(
      expect.stringContaining('0030_remove_unused_columns_and_orphan_function'),
    );
    expect(query).toHaveBeenCalledWith(
      expect.stringContaining('tide.schema_migrations'),
    );
    expect(query).toHaveBeenCalledWith(
      expect.stringContaining(
        "to_regclass('tide.schema_migrations'),\n          'DELETE'",
      ),
    );
    expect(query).toHaveBeenCalledWith(
      expect.stringContaining('tide.job_leases'),
    );
    expect(query).toHaveBeenCalledWith(
      expect.stringContaining('tide.kuozhi_course_syncs'),
    );
    expect(query).toHaveBeenCalledWith(
      expect.stringContaining(
        "'tide.analytics_task_business_change_v1'\n        ) IS NULL",
      ),
    );
    expect(query).toHaveBeenCalledWith(
      expect.stringContaining('tide.account_onboarding_states'),
    );
    expect(query).toHaveBeenCalledWith(
      expect.stringContaining('2026-08-11-g04-two-part'),
    );
    expect(query).toHaveBeenCalledWith(
      expect.stringContaining(
        'requiredStepKeys":["g02-environment-photo","g02-courseware-confirmation',
      ),
    );
    expect(query).toHaveBeenCalledWith(
      expect.stringContaining("payload->>'title' = 'Lesson Preparation'"),
    );
    expect(query).toHaveBeenCalledWith(
      expect.stringContaining("payload->>'ops_name_zh' = '首课准备'"),
    );
    expect(query).not.toHaveBeenCalledWith(
      expect.stringContaining('g02-device-2026-08-05-browser-preflight-v1'),
    );
    for (const privilege of ['SELECT', 'INSERT', 'UPDATE', 'DELETE']) {
      expect(query).toHaveBeenCalledWith(
        expect.stringContaining(
          `to_regclass('tide.job_leases'),\n          '${privilege}'`,
        ),
      );
    }
    expect(query).toHaveBeenCalledWith(
      expect.stringContaining("to_regclass('tide.teacher_photo_runs') IS NULL"),
    );
    expect(query).toHaveBeenCalledWith(
      expect.stringContaining("column_name = 'visibility'"),
    );
    expect(query).toHaveBeenCalledWith(
      expect.stringContaining(
        "to_regprocedure('tide.enforce_outbox_target()') IS NULL",
      ),
    );
    for (const privilege of ['SELECT', 'INSERT']) {
      expect(query).toHaveBeenCalledWith(
        expect.stringContaining(
          `to_regclass('tide.account_onboarding_states'),\n          '${privilege}'`,
        ),
      );
    }
    expect(query).not.toHaveBeenCalledWith(
      expect.stringContaining("'SELECT,INSERT,UPDATE"),
    );
    expect(query).toHaveBeenCalledWith(
      expect.stringContaining('public.teacher_support_tickets'),
    );
    expect(query).toHaveBeenCalledWith(
      expect.stringContaining("('G05:v1', 'G00', 'RETIRED')"),
    );
    expect(query).toHaveBeenCalledWith(
      expect.stringContaining('tide_support_ticket_owner'),
    );
  });

  it('fails production readiness when a contract query returns false', async () => {
    const query = jest.fn().mockResolvedValue(createReadinessResult(false));
    const pool = { query } as unknown as Pool;
    const service = new DatabaseService(
      pool,
      null,
      config({ NODE_ENV: 'production' }),
    );

    await expect(service.checkReadiness()).resolves.toEqual({
      tide: 'unavailable',
      shiwenRead: 'not_configured',
    });
  });
});
