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
    let productionQuery = '';
    const query = jest.fn((sql: string) => {
      productionQuery = sql;
      return Promise.resolve(createReadinessResult(true));
    });
    const pool = { query } as unknown as Pool;
    const service = new DatabaseService(
      pool,
      null,
      config({
        NODE_ENV: 'production',
        TIDE_DATABASE_URL:
          'postgresql://tit_teacher_crud:secret@db.example/tide?sslmode=verify-full',
      }),
    );

    await expect(service.checkReadiness()).resolves.toEqual({
      tide: 'ok',
      shiwenRead: 'not_configured',
    });
    expect(query).toHaveBeenCalledTimes(1);
    expect(query).toHaveBeenCalledWith(
      expect.stringContaining('0037_g04_remove_device_check'),
    );
    expect(query).toHaveBeenCalledWith(
      expect.stringContaining('0038_personalized_environment_photo'),
    );
    expect(query).toHaveBeenCalledWith(
      expect.stringContaining('0041_crm_sso_hybrid'),
    );
    expect(query).toHaveBeenCalledWith(
      expect.stringContaining('0042_g09_set_kuozhi_course'),
    );
    expect(query).toHaveBeenCalledWith(
      expect.stringContaining('0032_first_login_onboarding'),
    );
    expect(query).toHaveBeenCalledWith(
      expect.stringContaining('0033_g01_tesol_only'),
    );
    expect(query).toHaveBeenCalledWith(
      expect.stringContaining('FROM public.alembic_version'),
    );
    expect(query).toHaveBeenCalledWith(
      expect.stringContaining('20260819_65_g09_set_course'),
    );
    expect(productionQuery).toContain(
      'public.dom_student_json_is_safe_v1(jsonb)',
    );
    expect(productionQuery).toContain('guard_dom_lesson_student_privacy_v1');
    expect(productionQuery).toContain('tit.dts_source_region');
    expect(productionQuery).toContain('tit_dts_ingest_runtime');
    expect(productionQuery).toContain("tgenabled IN ('O', 'A')");
    expect(productionQuery).toContain('tgtype = 23');
    expect(productionQuery).toContain('AS read_relation(relation_name)');
    for (const relation of [
      'public.alembic_version',
      'public.task_templates',
      'public.teachers',
      'public.teacher_scorecard_current',
      'public.teacher_lesson_score_current',
      'public.teacher_g01_status_current',
    ]) {
      expect(productionQuery).toContain(`'${relation}'`);
    }
    expect(productionQuery).toContain(
      "'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE',",
    );
    expect(productionQuery).toContain("'REFERENCES', 'TRIGGER'");
    expect(productionQuery).toContain(
      "ARRAY['INSERT', 'UPDATE', 'REFERENCES']::text[]",
    );
    expect(productionQuery).toContain('AS crud_relation(relation_name)');
    for (const relation of [
      'public.task_assignments',
      'public.notifications',
      'public.notification_events',
      'public.teacher_support_tickets',
    ]) {
      expect(productionQuery).toContain(`'${relation}'`);
    }
    expect(productionQuery).not.toContain('public.teacher_source_wide');
    expect(productionQuery).toContain('relation.oid <> ALL (');
    expect(productionQuery).toContain(
      "'SELECT', 'INSERT', 'UPDATE', 'REFERENCES'",
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
      expect.stringContaining('tide.crm_sso_logins'),
    );
    expect(query).toHaveBeenCalledWith(
      expect.stringContaining('2026-08-11-g04-two-part'),
    );
    expect(query).toHaveBeenCalledWith(
      expect.stringContaining('task_step_progress_g02_read_status_check'),
    );
    expect(query).toHaveBeenCalledWith(
      expect.stringContaining(
        'task_step_progress_g02_assignment_completion_check',
      ),
    );
    expect(query).toHaveBeenCalledWith(
      expect.stringContaining('enforce_g02_document_assignment_completion'),
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
    expect(query).toHaveBeenCalledWith(
      expect.stringContaining("shared_template_row_id = 'P-FB-NEGATIVE:v1'"),
    );
    expect(query).toHaveBeenCalledWith(
      expect.stringContaining('2026-08-11-personalized-environment-photo-v1'),
    );
    const personalizedContractStart = productionQuery.indexOf(
      "execution.shared_template_row_id = 'P-FB-NEGATIVE:v1'",
    );
    const personalizedContractEnd = productionQuery.indexOf(
      'AND NOT EXISTS',
      personalizedContractStart,
    );
    expect(personalizedContractStart).toBeGreaterThanOrEqual(0);
    expect(personalizedContractEnd).toBeGreaterThan(personalizedContractStart);
    const personalizedContractSql = productionQuery.slice(
      personalizedContractStart,
      personalizedContractEnd,
    );
    expect(personalizedContractSql).toContain(
      "definition.title = 'Take a teaching-environment photo'",
    );
    expect(personalizedContractSql).toContain(
      'definition.config =\n' +
        '              \'{"version":"2026-08-11-personalized-environment-photo-v1","role":"ENVIRONMENT_PHOTO","reviewProfile":"TEACHING_ENVIRONMENT_V1","accept":["image/jpeg"],"captureOnly":true,"maxFiles":1}\'::jsonb',
    );
    expect(personalizedContractSql).toContain(
      "rule.rule_key = 'all-steps-complete'",
    );
    expect(personalizedContractSql).toContain(
      'rule.config =\n' +
        '              \'{"requiredStepKeys":["p-fb-negative-environment-photo"]}\'::jsonb',
    );
    expect(personalizedContractSql).toContain(
      "rule.rule_key = 'p-fb-negative-environment-ai-review'",
    );
    expect(personalizedContractSql).toContain(
      '"criteriaVersion":"personalized-teaching-environment-2026-08-v1"',
    );
    expect(personalizedContractSql).toContain(
      '"allowedMimeTypes":["image/jpeg","image/png","image/webp"]',
    );
    expect(personalizedContractSql).toContain(
      '"systemPrompt":"You strictly review teacher-submitted evidence.',
    );
    expect(personalizedContractSql).toContain(
      '"userText":"Review this current teaching-environment photo strictly',
    );
    expect(personalizedContractSql).toContain(
      "'请拍摄并提交一张当前授课环境照片。'",
    );
    expect(personalizedContractSql).toContain(
      "'已保留你完成的内容，请根据提示更新这份材料。'",
    );
    expect(personalizedContractSql).not.toContain('definition.config->');
    expect(personalizedContractSql).not.toContain('rule.config->');
    const exactConfigJson = Array.from(
      personalizedContractSql.matchAll(
        /(?:definition|rule)\.config\s*=\s*'([^']+)'::jsonb/g,
      ),
      (match) => match[1],
    );
    expect(exactConfigJson).toHaveLength(3);
    for (const configJson of exactConfigJson) {
      expect(() => JSON.parse(configJson) as unknown).not.toThrow();
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
    expect(productionQuery).toContain("namespace.nspname = 'tide'");
    expect(productionQuery).toContain(
      "relation.relkind IN ('r', 'p', 'v', 'm', 'f')",
    );
    expect(productionQuery).toContain("relation.relname <> 'crm_sso_logins'");
    expect(productionQuery).toContain(
      "ARRAY['SELECT', 'INSERT', 'UPDATE', 'DELETE']::text[]",
    );
    expect(productionQuery).toContain(
      "ARRAY['SELECT', 'INSERT', 'UPDATE']::text[]",
    );
    expect(productionQuery).toContain(
      "to_regclass('tide.crm_sso_logins'),\n          'DELETE'",
    );
    expect(productionQuery).toContain('has_sequence_privilege(');
    expect(productionQuery).toContain("sequence.relkind = 'S'");
    expect(productionQuery).toContain("ARRAY['USAGE', 'SELECT']::text[]");
    expect(productionQuery).toContain('FROM pg_default_acl AS defaults');
    expect(productionQuery).toContain("owner_role.rolname = 'tide_sys_admin'");
    expect(productionQuery).toContain("defaults.defaclobjtype = 'r'");
    expect(productionQuery).toContain("defaults.defaclobjtype = 'S'");
    expect(productionQuery).toContain(
      'SELECT oid FROM pg_roles WHERE rolname = current_user',
    );
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
    expect(productionQuery).toContain("current_setting('ssl') = 'on'");
    expect(productionQuery).toContain('FROM pg_stat_ssl');
  });

  it('requires actual plaintext for the fixed PRE private-line readiness query', async () => {
    let productionQuery = '';
    const query = jest.fn((sql: string) => {
      productionQuery = sql;
      return Promise.resolve(createReadinessResult(true));
    });
    const pool = { query } as unknown as Pool;
    const privateLineUrl =
      'postgresql://tit_teacher_crud:secret@tide-system.rwlb.singapore.rds.aliyuncs.com:5432/tide_system_test?sslmode=disable';
    const service = new DatabaseService(
      pool,
      null,
      config({
        NODE_ENV: 'production',
        TIDE_DATABASE_URL: privateLineUrl,
      }),
    );

    await expect(service.checkReadiness()).resolves.toEqual({
      tide: 'ok',
      shiwenRead: 'not_configured',
    });
    expect(productionQuery).toContain("current_setting('ssl') = 'off'");
    expect(productionQuery).toContain(') = FALSE');
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
