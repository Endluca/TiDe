import { Inject, Injectable, OnModuleDestroy, Optional } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import type { Pool, PoolClient, QueryResult, QueryResultRow } from 'pg';
import type { AppEnvironment } from '../config/environment';
import { quoteQualifiedViewName } from '../../integrations/shiwen/qualified-view-name';
import {
  SHIWEN_READ_DATABASE_POOL,
  TIDE_DATABASE_POOL,
} from './database.constants';
import { DatabaseNotConfiguredError } from './database.errors';

export interface DatabaseReadiness {
  tide: 'ok' | 'not_configured' | 'unavailable';
  shiwenRead: 'ok' | 'not_configured' | 'unavailable';
}

export interface DatabasePoolSnapshot {
  configured: boolean;
  totalConnections: number;
  idleConnections: number;
  waitingRequests: number;
}

export interface DatabasePoolStats {
  tide: DatabasePoolSnapshot;
  shiwenRead: DatabasePoolSnapshot;
}

const CURRENT_PRODUCTION_MIGRATIONS = [
  '0001_initial',
  '0002_shared_database_exchange',
  '0003_file_upload_intents',
  '0004_task_command_receipts',
  '0005_faq_message_commands',
  '0006_teacher_profile_g01_support',
  '0007_shared_task_assignment_links',
  '0008_remove_legacy_task_exchange',
  '0009_task_view_command',
  '0010_current_task_execution',
  '0011_system_notification_delivery',
  '0012_system_notification_publication_guards',
  '0013_system_notification_owner_maintenance',
  '0014_teacher_photo_processing',
  '0015_teacher_photo_filter_strength',
  '0016_database_quiz_banks',
  '0019_growth_stage_notification_state',
  '0020_product_analytics',
  '0021_teacher_support_tickets',
  '0022_performance_job_leases',
  '0023_teacher_support_operator_atomicity',
  '0024_support_ticket_cas_and_function_owner',
  '0025_fixed_task_semantic_alignment',
] as const;

@Injectable()
export class DatabaseService implements OnModuleDestroy {
  constructor(
    @Inject(TIDE_DATABASE_POOL) private readonly tidePool: Pool | null,
    @Inject(SHIWEN_READ_DATABASE_POOL)
    private readonly shiwenReadPool: Pool | null,
    @Optional()
    private readonly config?: ConfigService<AppEnvironment, true>,
  ) {}

  isTideConfigured(): boolean {
    return this.tidePool !== null;
  }

  isShiwenReadConfigured(): boolean {
    return this.shiwenReadPool !== null;
  }

  queryTide<Row extends QueryResultRow = QueryResultRow>(
    text: string,
    values: readonly unknown[] = [],
  ): Promise<QueryResult<Row>> {
    if (!this.tidePool) {
      throw new DatabaseNotConfiguredError('tide');
    }

    return this.tidePool.query<Row>(text, [...values]);
  }

  queryShiwen<Row extends QueryResultRow = QueryResultRow>(
    text: string,
    values: readonly unknown[] = [],
  ): Promise<QueryResult<Row>> {
    if (!this.shiwenReadPool) {
      throw new DatabaseNotConfiguredError('shiwen-read');
    }
    return this.shiwenReadPool.query<Row>(text, [...values]);
  }

  async withTideTransaction<Result>(
    work: (client: PoolClient) => Promise<Result>,
  ): Promise<Result> {
    if (!this.tidePool) {
      throw new DatabaseNotConfiguredError('tide');
    }

    const client = await this.tidePool.connect();

    try {
      await client.query('BEGIN');
      const result = await work(client);
      await client.query('COMMIT');
      return result;
    } catch (error) {
      await client.query('ROLLBACK');
      throw error;
    } finally {
      client.release();
    }
  }

  async withShiwenReadTransaction<Result>(
    work: (client: PoolClient) => Promise<Result>,
  ): Promise<Result> {
    if (!this.shiwenReadPool) {
      throw new DatabaseNotConfiguredError('shiwen-read');
    }

    const client = await this.shiwenReadPool.connect();

    try {
      await client.query('BEGIN READ ONLY');
      const result = await work(client);
      await client.query('COMMIT');
      return result;
    } catch (error) {
      await client.query('ROLLBACK');
      throw error;
    } finally {
      client.release();
    }
  }

  async checkReadiness(): Promise<DatabaseReadiness> {
    const [tide, shiwenRead] = await Promise.all([
      this.checkTideContract(),
      this.checkShiwenReadContract(),
    ]);

    return { tide, shiwenRead };
  }

  getPoolStats(): DatabasePoolStats {
    return {
      tide: this.poolSnapshot(this.tidePool),
      shiwenRead: this.poolSnapshot(this.shiwenReadPool),
    };
  }

  async onModuleDestroy(): Promise<void> {
    await Promise.all(
      [this.tidePool, this.shiwenReadPool]
        .filter((pool): pool is Pool => pool !== null)
        .map((pool) => pool.end()),
    );
  }

  private async queryReadiness(
    pool: Pool | null,
    query: string,
  ): Promise<'ok' | 'not_configured' | 'unavailable'> {
    if (!pool) {
      return 'not_configured';
    }

    try {
      const result = await pool.query<{ ready: boolean }>(query);
      return result.rows.length === 1 && result.rows[0]?.ready === true
        ? 'ok'
        : 'unavailable';
    } catch {
      return 'unavailable';
    }
  }

  private checkTideContract(): Promise<
    'ok' | 'not_configured' | 'unavailable'
  > {
    const production =
      this.config?.get('NODE_ENV', { infer: true }) === 'production';
    return this.queryReadiness(
      this.tidePool,
      production
        ? this.productionTideContractQuery()
        : `
          SELECT (
            (SELECT count(*) FROM tide.user_accounts WHERE false) = 0
            AND (
              SELECT count(*)
              FROM tide.task_execution_versions
              WHERE false
            ) = 0
          ) AS ready
        `,
    );
  }

  private checkShiwenReadContract(): Promise<
    'ok' | 'not_configured' | 'unavailable'
  > {
    if (!this.shiwenReadPool) {
      return Promise.resolve('not_configured');
    }

    const mode =
      this.config?.get('SHIWEN_READ_MODE', { infer: true }) ?? 'DIRECT_TABLES';
    const identityRelation =
      mode === 'DIRECT_TABLES'
        ? 'public.teachers'
        : this.qualifiedIdentityView();
    if (!identityRelation) {
      return Promise.resolve('unavailable');
    }

    return this.queryReadiness(
      this.shiwenReadPool,
      `
        SELECT (
          (SELECT count(*) FROM ${identityRelation} WHERE false) = 0
          AND (
            SELECT count(*)
            FROM public.teacher_scorecard_current
            WHERE false
          ) = 0
          AND (
            SELECT count(*)
            FROM public.teacher_lesson_score_current
            WHERE false
          ) = 0
        ) AS ready
      `,
    );
  }

  private productionTideContractQuery(): string {
    const migrationIds = CURRENT_PRODUCTION_MIGRATIONS.map(
      (migrationId) => `'${migrationId}'`,
    ).join(', ');

    return `
      WITH migration_state AS (
        SELECT
          array_agg(migration_id ORDER BY migration_order) AS migration_ids,
          count(*)::integer AS migration_count
        FROM tide.schema_migrations
      ),
      latest_migration AS (
        SELECT migration_id
        FROM tide.schema_migrations
        WHERE migration_order = (
          SELECT max(migration_order)
          FROM tide.schema_migrations
        )
      )
      SELECT (
        migration_state.migration_ids IS NOT DISTINCT FROM
          ARRAY[${migrationIds}]::text[]
        AND migration_state.migration_count =
          ${CURRENT_PRODUCTION_MIGRATIONS.length}
        AND (SELECT count(*) FROM latest_migration) = 1
        AND (
          SELECT migration_id
          FROM latest_migration
          LIMIT 1
        ) = '0025_fixed_task_semantic_alignment'
        AND to_regclass('tide.user_accounts') IS NOT NULL
        AND to_regclass('tide.task_execution_versions') IS NOT NULL
        AND to_regclass('tide.teacher_photo_runs') IS NOT NULL
        AND to_regclass('tide.job_leases') IS NOT NULL
        AND to_regclass('public.task_templates') IS NOT NULL
        AND to_regclass('public.teacher_support_tickets') IS NOT NULL
        AND to_regclass(
          'tide.teacher_photo_runs_pending_claim_idx'
        ) IS NOT NULL
        AND to_regclass('tide.job_leases_expiry_idx') IS NOT NULL
        AND to_regclass(
          'public.teacher_support_tickets_status_deadline_idx'
        ) IS NOT NULL
        AND (
          SELECT count(*)
          FROM (
            VALUES
              ('G01:v1', 'G01', 'PUBLISHED'),
              ('G02:v1', 'G04', 'PUBLISHED'),
              ('G03:v1', 'G02', 'PUBLISHED'),
              ('G04:v1', 'G03', 'PUBLISHED'),
              ('G05:v1', 'G00', 'RETIRED'),
              ('G06:v1', 'G05', 'PUBLISHED'),
              ('G07:v1', 'G06', 'PUBLISHED'),
              ('G08:v1', 'G07', 'PUBLISHED'),
              ('G09:v1', 'G08', 'PUBLISHED'),
              ('G10:v1', 'G09', 'PUBLISHED')
          ) AS expected(row_id, task_code, expected_status)
          JOIN public.task_templates AS template
            ON template.row_id = expected.row_id
           AND template.template_id = expected.task_code
           AND template.status = expected.expected_status
           AND template.execution_owner = 'TEACHER_APP'
           AND template.payload->>'category' = 'MANDATORY_GROWTH'
        ) = 10
        AND (
          SELECT array_agg(template_id::text ORDER BY template_id)
          FROM public.task_templates
          WHERE status = 'PUBLISHED'
            AND payload->>'category' = 'MANDATORY_GROWTH'
        ) IS NOT DISTINCT FROM
          ARRAY[
            'G01', 'G02', 'G03', 'G04', 'G05',
            'G06', 'G07', 'G08', 'G09'
          ]::text[]
        AND (
          SELECT count(*)
          FROM tide.task_execution_versions AS execution
          JOIN public.task_templates AS template
            ON template.row_id = execution.shared_template_row_id
          WHERE template.status = 'PUBLISHED'
            AND template.payload->>'category' = 'MANDATORY_GROWTH'
            AND execution.task_code = template.template_id
            AND execution.status = 'ACTIVE'
        ) = 9
        AND NOT EXISTS (
          SELECT 1
          FROM tide.task_execution_versions AS execution
          JOIN public.task_templates AS template
            ON template.row_id = execution.shared_template_row_id
          WHERE template.row_id IN (
            'G01:v1', 'G02:v1', 'G03:v1', 'G04:v1', 'G05:v1',
            'G06:v1', 'G07:v1', 'G08:v1', 'G09:v1', 'G10:v1'
          )
            AND (
              execution.task_code IS DISTINCT FROM template.template_id
              OR execution.task_code LIKE 'TMP-0025-%'
              OR (
                execution.task_code = 'G00'
                AND execution.status <> 'RETIRED'
              )
              OR (
                execution.task_code ~ '^G0[1-9]$'
                AND execution.status <> 'ACTIVE'
              )
            )
        )
        AND (
          SELECT count(*)
          FROM pg_attribute
          WHERE attrelid = to_regclass('tide.teacher_photo_runs')
            AND attnum > 0
            AND NOT attisdropped
            AND attname IN (
              'processing_owner',
              'lease_expires_at',
              'attempt_count',
              'next_attempt_at'
            )
        ) = 4
        AND (
          SELECT count(*)
          FROM pg_attribute
          WHERE attrelid = to_regclass('tide.job_leases')
            AND attnum > 0
            AND NOT attisdropped
            AND attname IN (
              'job_key',
              'owner_id',
              'lease_until',
              'updated_at'
            )
        ) = 4
        AND (
          SELECT count(*)
          FROM pg_attribute
          WHERE attrelid = to_regclass('public.teacher_support_tickets')
            AND attnum > 0
            AND NOT attisdropped
            AND attname IN (
              'messages',
              'status',
              'last_operator_reply_at',
              'teacher_reply_deadline_at',
              'row_version',
              'image_cleanup_status'
            )
        ) = 6
        AND to_regprocedure(
          'public.create_teacher_support_ticket(uuid,character varying,character varying,character varying,jsonb,jsonb)'
        ) IS NOT NULL
        AND to_regprocedure(
          'public.append_teacher_support_ticket_teacher_message(uuid,character varying,bigint,jsonb)'
        ) IS NOT NULL
        AND to_regprocedure(
          'public.append_teacher_support_ticket_operator_message(uuid,bigint,jsonb)'
        ) IS NOT NULL
        AND to_regprocedure(
          'public.mark_teacher_support_ticket_images_deleted(uuid,timestamp with time zone)'
        ) IS NOT NULL
        AND EXISTS (
          SELECT 1
          FROM pg_roles owner_role
          WHERE owner_role.rolname = 'tide_support_ticket_owner'
            AND NOT owner_role.rolcanlogin
            AND NOT owner_role.rolsuper
            AND NOT owner_role.rolcreatedb
            AND NOT owner_role.rolcreaterole
            AND NOT owner_role.rolreplication
            AND NOT owner_role.rolbypassrls
            AND NOT EXISTS (
              SELECT 1
              FROM pg_auth_members membership
              WHERE membership.member = owner_role.oid
            )
        )
        AND NOT has_schema_privilege(
          'tide_support_ticket_owner',
          'public',
          'CREATE'
        )
        AND has_table_privilege(
          current_user,
          to_regclass('tide.schema_migrations'),
          'SELECT'
        )
        AND NOT has_table_privilege(
          current_user,
          to_regclass('tide.schema_migrations'),
          'INSERT'
        )
        AND NOT has_table_privilege(
          current_user,
          to_regclass('tide.schema_migrations'),
          'UPDATE'
        )
        AND NOT has_table_privilege(
          current_user,
          to_regclass('tide.schema_migrations'),
          'DELETE'
        )
        AND has_table_privilege(
          current_user,
          to_regclass('tide.job_leases'),
          'SELECT'
        )
        AND has_table_privilege(
          current_user,
          to_regclass('tide.job_leases'),
          'INSERT'
        )
        AND has_table_privilege(
          current_user,
          to_regclass('tide.job_leases'),
          'UPDATE'
        )
        AND has_table_privilege(
          current_user,
          to_regclass('tide.job_leases'),
          'DELETE'
        )
        AND has_table_privilege(
          current_user,
          to_regclass('tide.teacher_photo_runs'),
          'SELECT'
        )
        AND has_table_privilege(
          current_user,
          to_regclass('tide.teacher_photo_runs'),
          'INSERT'
        )
        AND has_table_privilege(
          current_user,
          to_regclass('tide.teacher_photo_runs'),
          'UPDATE'
        )
        AND has_table_privilege(
          current_user,
          to_regclass('public.teacher_support_tickets'),
          'SELECT'
        )
        AND has_function_privilege(
          current_user,
          to_regprocedure(
            'public.create_teacher_support_ticket(uuid,character varying,character varying,character varying,jsonb,jsonb)'
          ),
          'EXECUTE'
        )
        AND has_function_privilege(
          current_user,
          to_regprocedure(
            'public.append_teacher_support_ticket_teacher_message(uuid,character varying,bigint,jsonb)'
          ),
          'EXECUTE'
        )
        AND has_function_privilege(
          current_user,
          to_regprocedure(
            'public.mark_teacher_support_ticket_images_deleted(uuid,timestamp with time zone)'
          ),
          'EXECUTE'
        )
        AND NOT has_function_privilege(
          current_user,
          to_regprocedure(
            'public.append_teacher_support_ticket_operator_message(uuid,bigint,jsonb)'
          ),
          'EXECUTE'
        )
        AND NOT EXISTS (
          SELECT 1
          FROM unnest(
            ARRAY[
              to_regprocedure(
                'public.create_teacher_support_ticket(uuid,character varying,character varying,character varying,jsonb,jsonb)'
              ),
              to_regprocedure(
                'public.append_teacher_support_ticket_teacher_message(uuid,character varying,bigint,jsonb)'
              ),
              to_regprocedure(
                'public.append_teacher_support_ticket_operator_message(uuid,bigint,jsonb)'
              ),
              to_regprocedure(
                'public.mark_teacher_support_ticket_images_deleted(uuid,timestamp with time zone)'
              )
            ]
          ) AS secured_function(function_oid)
          JOIN pg_proc procedure
            ON procedure.oid = secured_function.function_oid
          JOIN pg_roles owner_role
            ON owner_role.oid = procedure.proowner
          WHERE owner_role.rolname <> 'tide_support_ticket_owner'
             OR NOT procedure.prosecdef
        )
      ) AS ready
      FROM migration_state
    `;
  }

  private qualifiedIdentityView(): string | null {
    const viewName = this.config?.get('SHIWEN_TEACHER_IDENTITY_VIEW', {
      infer: true,
    });
    return viewName ? quoteQualifiedViewName(viewName) : null;
  }

  private poolSnapshot(pool: Pool | null): DatabasePoolSnapshot {
    return {
      configured: pool !== null,
      totalConnections: pool?.totalCount ?? 0,
      idleConnections: pool?.idleCount ?? 0,
      waitingRequests: pool?.waitingCount ?? 0,
    };
  }
}
