import { Inject, Injectable, OnModuleDestroy, Optional } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import type { Pool, PoolClient, QueryResult, QueryResultRow } from 'pg';
import {
  hasApprovedPrePrivateLineDatabaseUrl,
  type AppEnvironment,
} from '../config/environment';
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
  '0026_kuozhi_course_syncs',
  '0027_remove_local_quiz_runtime',
  '0028_retire_task_business_change_view',
  '0029_remove_unused_tide_objects',
  '0030_remove_unused_columns_and_orphan_function',
  '0031_g04_independent_sections',
  '0032_first_login_onboarding',
  '0033_g01_tesol_only',
  '0037_g04_remove_device_check',
  '0038_personalized_environment_photo',
  '0039_g02_policy_document',
  '0040_g02_document_read_status',
  '0041_crm_sso_hybrid',
  '0042_g09_set_kuozhi_course',
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
            FROM tide.account_onboarding_states
            WHERE false
            ) = 0
            AND (
              SELECT count(*)
              FROM tide.crm_sso_logins
              WHERE false
            ) = 0
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
          ${this.productionTransportContract('SHIWEN_READ_DATABASE_URL')}
          AND (SELECT count(*) FROM ${identityRelation} WHERE false) = 0
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
      public_migration_state AS (
        SELECT
          min(version_num) AS version_num,
          count(*)::integer AS migration_count
        FROM public.alembic_version
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
        ${this.productionTransportContract('TIDE_DATABASE_URL')}
        AND migration_state.migration_ids IS NOT DISTINCT FROM
          ARRAY[${migrationIds}]::text[]
        AND migration_state.migration_count =
          ${CURRENT_PRODUCTION_MIGRATIONS.length}
        AND (SELECT count(*) FROM latest_migration) = 1
        AND (
          SELECT migration_id
          FROM latest_migration
          LIMIT 1
        ) = '0042_g09_set_kuozhi_course'
        AND public_migration_state.migration_count = 1
        AND public_migration_state.version_num =
          '20260819_65_g09_set_course'
        AND to_regprocedure(
          'public.dom_student_json_is_safe_v1(jsonb)'
        ) IS NOT NULL
        AND EXISTS (
          SELECT 1
          FROM pg_trigger AS privacy_trigger
          WHERE privacy_trigger.tgrelid =
              'public.lesson_source_wide'::regclass
            AND privacy_trigger.tgname =
              'guard_dom_lesson_student_privacy_v1'
            AND privacy_trigger.tgfoid =
              'public.guard_dom_lesson_student_privacy_v1()'::regprocedure
            AND privacy_trigger.tgenabled IN ('O', 'A')
            AND privacy_trigger.tgtype = 23
            AND NOT privacy_trigger.tgisinternal
        )
        AND EXISTS (
          SELECT 1
          FROM pg_proc AS privacy_function
          JOIN pg_namespace AS privacy_namespace
            ON privacy_namespace.oid = privacy_function.pronamespace
          WHERE privacy_namespace.nspname = 'public'
            AND privacy_function.proname =
              'guard_dom_lesson_student_privacy_v1'
            AND pg_get_function_identity_arguments(privacy_function.oid) = ''
            AND position(
              'tit.dts_source_region' IN privacy_function.prosrc
            ) > 0
            AND position(
              'tit_dts_ingest_runtime' IN privacy_function.prosrc
            ) > 0
        )
        AND NOT EXISTS (
          SELECT 1
          FROM unnest(
            ARRAY[
              'public.alembic_version',
              'public.task_templates',
              'public.teachers',
              'public.teacher_scorecard_current',
              'public.teacher_lesson_score_current',
              'public.teacher_g01_status_current'
            ]::text[]
          ) AS read_relation(relation_name)
          WHERE to_regclass(read_relation.relation_name) IS NULL
             OR NOT has_table_privilege(
               current_user,
               to_regclass(read_relation.relation_name),
               'SELECT'
             )
             OR EXISTS (
               SELECT 1
               FROM unnest(
                 ARRAY[
                   'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE',
                   'REFERENCES', 'TRIGGER'
                 ]::text[]
               ) AS forbidden(privilege_name)
               WHERE has_table_privilege(
                 current_user,
                 to_regclass(read_relation.relation_name),
                 forbidden.privilege_name
               )
             )
             OR EXISTS (
               SELECT 1
               FROM pg_attribute AS attribute
               CROSS JOIN unnest(
                 ARRAY['INSERT', 'UPDATE', 'REFERENCES']::text[]
               ) AS forbidden(privilege_name)
               WHERE attribute.attrelid =
                   to_regclass(read_relation.relation_name)
                 AND attribute.attnum > 0
                 AND NOT attribute.attisdropped
                 AND has_column_privilege(
                   current_user,
                   attribute.attrelid,
                   attribute.attnum,
                   forbidden.privilege_name
                 )
             )
        )
        AND NOT EXISTS (
          SELECT 1
          FROM unnest(
            ARRAY[
              'public.task_assignments',
              'public.notifications',
              'public.notification_events',
              'public.teacher_support_tickets'
            ]::text[]
          ) AS crud_relation(relation_name)
          CROSS JOIN unnest(
            ARRAY['SELECT', 'INSERT', 'UPDATE', 'DELETE']::text[]
          ) AS required(privilege_name)
          WHERE to_regclass(crud_relation.relation_name) IS NULL
             OR NOT has_table_privilege(
               current_user,
               to_regclass(crud_relation.relation_name),
               required.privilege_name
             )
        )
        AND NOT EXISTS (
          SELECT 1
          FROM pg_class AS relation
          JOIN pg_namespace AS namespace
            ON namespace.oid = relation.relnamespace
          WHERE namespace.nspname = 'public'
            AND relation.relkind IN ('r', 'p', 'v', 'm', 'f')
            AND relation.oid <> ALL (
              ARRAY[
                to_regclass('public.alembic_version'),
                to_regclass('public.task_templates'),
                to_regclass('public.teachers'),
                to_regclass('public.teacher_scorecard_current'),
                to_regclass('public.teacher_lesson_score_current'),
                to_regclass('public.teacher_g01_status_current'),
                to_regclass('public.task_assignments'),
                to_regclass('public.notifications'),
                to_regclass('public.notification_events'),
                to_regclass('public.teacher_support_tickets')
              ]::oid[]
            )
            AND (
              EXISTS (
                SELECT 1
                FROM unnest(
                  ARRAY[
                    'SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE',
                    'REFERENCES', 'TRIGGER'
                  ]::text[]
                ) AS forbidden(privilege_name)
                WHERE has_table_privilege(
                  current_user,
                  relation.oid,
                  forbidden.privilege_name
                )
              )
              OR EXISTS (
                SELECT 1
                FROM pg_attribute AS attribute
                CROSS JOIN unnest(
                  ARRAY[
                    'SELECT', 'INSERT', 'UPDATE', 'REFERENCES'
                  ]::text[]
                ) AS forbidden(privilege_name)
                WHERE attribute.attrelid = relation.oid
                  AND attribute.attnum > 0
                  AND NOT attribute.attisdropped
                  AND has_column_privilege(
                    current_user,
                    attribute.attrelid,
                    attribute.attnum,
                    forbidden.privilege_name
                  )
              )
            )
        )
        AND to_regclass('tide.user_accounts') IS NOT NULL
        AND to_regclass('tide.account_onboarding_states') IS NOT NULL
        AND to_regclass('tide.crm_sso_logins') IS NOT NULL
        AND NOT EXISTS (
          SELECT 1
          FROM unnest(
            ARRAY['SELECT', 'INSERT', 'UPDATE']::text[]
          ) AS required(privilege_name)
          WHERE NOT has_table_privilege(
            current_user,
            to_regclass('tide.crm_sso_logins'),
            required.privilege_name
          )
        )
        AND NOT has_table_privilege(
          current_user,
          to_regclass('tide.crm_sso_logins'),
          'DELETE'
        )
        AND to_regclass('tide.task_execution_versions') IS NOT NULL
        AND EXISTS (
          SELECT 1
          FROM information_schema.columns
          WHERE table_schema = 'tide'
            AND table_name = 'task_step_progress'
            AND column_name = 'reached_end'
            AND data_type = 'boolean'
            AND is_nullable = 'NO'
            AND column_default IN ('false', 'false::boolean')
        )
        AND EXISTS (
          SELECT 1
          FROM pg_constraint
          WHERE conrelid = 'tide.task_step_progress'::regclass
            AND conname = 'task_step_progress_g02_read_status_check'
            AND contype = 'c'
            AND convalidated
            AND position('reached_end' IN pg_get_constraintdef(oid)) > 0
            AND position('contentVersion' IN pg_get_constraintdef(oid)) > 0
            AND position('contentHash' IN pg_get_constraintdef(oid)) > 0
            AND position('completed_at' IN pg_get_constraintdef(oid)) > 0
        )
        AND EXISTS (
          SELECT 1
          FROM pg_trigger AS trigger
          JOIN pg_proc AS procedure ON procedure.oid = trigger.tgfoid
          JOIN pg_namespace AS namespace
            ON namespace.oid = procedure.pronamespace
          WHERE trigger.tgrelid = 'tide.task_step_progress'::regclass
            AND trigger.tgname =
              'task_step_progress_g02_assignment_completion_check'
            AND NOT trigger.tgisinternal
            AND trigger.tgdeferrable
            AND trigger.tginitdeferred
            AND namespace.nspname = 'tide'
            AND procedure.proname =
              'enforce_g02_document_assignment_completion'
        )
        AND NOT EXISTS (
          SELECT 1
          FROM tide.task_step_progress AS progress
          JOIN public.task_assignments AS assignment
            ON assignment.assignment_id = progress.task_assignment_id
          WHERE progress.step_key = 'g02-policy-document'
            AND progress.reached_end
            AND assignment.status <> 'COMPLETED'
        )
        AND to_regclass('tide.job_leases') IS NOT NULL
        AND to_regclass('tide.kuozhi_course_syncs') IS NOT NULL
        AND to_regclass(
          'tide.analytics_task_business_change_v1'
        ) IS NULL
        AND to_regclass('public.task_templates') IS NOT NULL
        AND to_regclass('public.teacher_support_tickets') IS NOT NULL
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
        AND EXISTS (
          SELECT 1
          FROM public.task_templates AS template
          WHERE template.row_id = 'G01:v1'
            AND template.template_id = 'G01'
            AND template.status = 'PUBLISHED'
            AND template.payload->>'title' =
              'Profile & Credentials Completion'
            AND template.payload->>'why_template' =
              'Complete the required TESOL status and learning evidence.'
            AND template.payload->>'how_summary' =
              'Confirm TESOL, pass all 61 questions, complete the Essay and submit the completion proof.'
            AND template.payload->>'completion_standard' =
              'TESOL is complete, the 61-question check reaches 80%, the Essay is complete and the completion proof is submitted.'
        )
        AND EXISTS (
          SELECT 1
          FROM public.task_templates AS template
          WHERE template.row_id = 'G02:v1'
            AND template.template_id = 'G04'
            AND template.status = 'PUBLISHED'
            AND template.payload->>'template_id' = 'G04'
            AND template.payload->>'ops_name_zh' = '首课准备'
            AND template.payload->>'title' = 'Lesson Preparation'
            AND template.payload->>'why_template' =
              'Complete the teaching-environment photo review and prepare the courseware before your first lesson.'
            AND template.payload->>'how_summary' =
              'Complete two sections in any order: submit one teaching-environment photo for AI review and prepare the courseware for your first lesson. Each section keeps its own progress.'
            AND template.payload->>'completion_standard' =
              'G04 is completed only after both sections pass: all four teaching-environment photo criteria—camera angle, lighting, background and dressing—pass AI review, and the courseware preparation is confirmed. The sections may be completed in any order.'
            AND template.payload->>'benefit' =
              'Your teaching environment and courseware are ready for your first lesson.'
        )
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
        AND (
          SELECT count(*)
          FROM tide.task_validation_rules AS rule
          JOIN tide.task_execution_versions AS execution
            ON execution.id = rule.execution_version_id
          WHERE execution.shared_template_row_id = 'G01:v1'
            AND execution.task_code = 'G01'
            AND execution.status = 'ACTIVE'
            AND (
              rule.rule_key = 'g01-external-status'
              OR rule.rule_type = 'G01_EXTERNAL_STATUS'
            )
        ) = 1
        AND EXISTS (
          SELECT 1
          FROM tide.task_validation_rules AS rule
          JOIN tide.task_execution_versions AS execution
            ON execution.id = rule.execution_version_id
          WHERE execution.shared_template_row_id = 'G01:v1'
            AND execution.task_code = 'G01'
            AND execution.status = 'ACTIVE'
            AND rule.rule_key = 'g01-external-status'
            AND rule.rule_type = 'G01_EXTERNAL_STATUS'
            AND rule.rule_version = '2026-08-11-tesol-only-v1'
            AND rule.position = 3
            AND rule.config = '{}'::jsonb
            AND rule.teacher_failure_copy = 'TESOL 真实状态尚未通过。'
        )
        AND EXISTS (
          SELECT 1
          FROM tide.task_execution_versions AS execution
          WHERE execution.shared_template_row_id = 'G03:v1'
            AND execution.task_code = 'G02'
            AND execution.status = 'ACTIVE'
            AND execution.execution_contract_version = 'task-contract-v3'
            AND execution.config =
              '{"estimatedMinutes":35,"allowRetry":true,"contentStatus":"READY","contentVersion":"2026-07-24-overseas-nt-policies-v1","pendingReason":null}'::jsonb
        )
        AND (
          SELECT count(*)
          FROM tide.task_step_definitions AS definition
          JOIN tide.task_execution_versions AS execution
            ON execution.id = definition.execution_version_id
          WHERE execution.shared_template_row_id = 'G03:v1'
        ) = 1
        AND EXISTS (
          SELECT 1
          FROM tide.task_step_definitions AS definition
          JOIN tide.task_execution_versions AS execution
            ON execution.id = definition.execution_version_id
          WHERE execution.shared_template_row_id = 'G03:v1'
            AND definition.step_key = 'g02-policy-document'
            AND definition.position = 1
            AND definition.step_type = 'DOCUMENT'
            AND definition.config->>'contentVersion' =
              '2026-07-24-overseas-nt-policies-v1'
            AND definition.config->>'contentHash' =
              '6875233667c6f3d90602a07c84849dbb88f41685929779a7b0dc79ccf859979c'
        )
        AND (
          SELECT count(*)
          FROM tide.task_validation_rules AS rule
          JOIN tide.task_execution_versions AS execution
            ON execution.id = rule.execution_version_id
          WHERE execution.shared_template_row_id = 'G03:v1'
        ) = 1
        AND EXISTS (
          SELECT 1
          FROM tide.task_validation_rules AS rule
          JOIN tide.task_execution_versions AS execution
            ON execution.id = rule.execution_version_id
          WHERE execution.shared_template_row_id = 'G03:v1'
            AND rule.rule_key = 'all-steps-complete'
            AND rule.rule_type = 'ALL_STEPS_COMPLETE'
            AND rule.rule_version = '2026-08-11-g02-policy-document-v1'
            AND rule.position = 1
            AND rule.config =
              '{"requiredStepKeys":["g02-policy-document"]}'::jsonb
        )
        AND EXISTS (
          SELECT 1
          FROM tide.task_execution_versions AS execution
          WHERE execution.shared_template_row_id = 'G02:v1'
            AND execution.task_code = 'G04'
            AND execution.status = 'ACTIVE'
            AND execution.execution_contract_version = 'task-contract-v3'
            AND execution.config =
              '{"estimatedMinutes":15,"allowRetry":true,"contentStatus":"READY","contentVersion":"2026-08-11-g04-two-part","pendingReason":null,"independentModules":{"stepKeys":["g02-environment-photo","g02-courseware-confirmation"],"allowOutOfOrderProgress":true,"keepAssignmentInProgressUntilPassed":true}}'::jsonb
        )
        AND (
          SELECT count(*)
          FROM tide.task_step_definitions AS definition
          JOIN tide.task_execution_versions AS execution
            ON execution.id = definition.execution_version_id
          WHERE execution.shared_template_row_id = 'G02:v1'
        ) = 2
        AND (
          SELECT count(*)
          FROM tide.task_step_definitions AS definition
          JOIN tide.task_execution_versions AS execution
            ON execution.id = definition.execution_version_id
          WHERE execution.shared_template_row_id = 'G02:v1'
            AND (
              (
                definition.step_key = 'g02-environment-photo'
                AND definition.position = 1
                AND definition.step_type = 'UPLOAD'
              )
              OR (
                definition.step_key = 'g02-courseware-confirmation'
                AND definition.position = 2
                AND definition.step_type = 'CHECKLIST'
                AND definition.config->>'version' =
                  'g02-courseware-2026-08-05-guidance-v1'
              )
            )
        ) = 2
        AND EXISTS (
          SELECT 1
          FROM tide.task_validation_rules AS rule
          JOIN tide.task_execution_versions AS execution
            ON execution.id = rule.execution_version_id
          WHERE execution.shared_template_row_id = 'G02:v1'
            AND rule.rule_key = 'all-steps-complete'
            AND rule.rule_type = 'ALL_STEPS_COMPLETE'
            AND rule.rule_version = '2026-08-11-g04-two-part-v1'
            AND rule.position = 1
            AND rule.config =
              '{"requiredStepKeys":["g02-environment-photo","g02-courseware-confirmation"]}'::jsonb
        )
        AND (
          SELECT count(*)
          FROM tide.task_validation_rules AS rule
          JOIN tide.task_execution_versions AS execution
            ON execution.id = rule.execution_version_id
          WHERE execution.shared_template_row_id = 'G02:v1'
        ) = 2
        AND EXISTS (
          SELECT 1
          FROM tide.task_validation_rules AS rule
          JOIN tide.task_execution_versions AS execution
            ON execution.id = rule.execution_version_id
          WHERE execution.shared_template_row_id = 'G02:v1'
            AND rule.rule_key = 'g02-environment-ai-review'
            AND rule.rule_type = 'AI_IMAGE_REVIEW'
            AND rule.position = 2
            AND rule.config->>'criteriaVersion' =
              'lesson-preparation-camera-view-2026-08-v7-background-veto'
            AND rule.config->'criteriaKeys' =
              '["camera_angle","lighting","background","dressing"]'::jsonb
        )
        AND EXISTS (
          SELECT 1
          FROM tide.task_execution_versions AS execution
          JOIN public.task_templates AS template
            ON template.row_id = execution.shared_template_row_id
          WHERE execution.shared_template_row_id = 'P-FB-NEGATIVE:v1'
            AND execution.task_code = 'P-FB-NEGATIVE'
            AND execution.status = 'ACTIVE'
            AND execution.execution_contract_version = 'task-contract-v3'
            AND execution.config =
              '{"estimatedMinutes":8,"allowRetry":true,"contentStatus":"PENDING","contentVersion":"2026-08-11-personalized-environment-photo-v1","pendingReason":"JIAHE_PERSONALIZED_CONTENT_PENDING","independentModules":{"stepKeys":["p-fb-negative-environment-photo"],"allowOutOfOrderProgress":true,"keepAssignmentInProgressUntilPassed":true}}'::jsonb
            AND template.template_id = 'P-FB-NEGATIVE'
            AND template.status = 'PUBLISHED'
            AND template.execution_owner = 'TEACHER_APP'
            AND template.payload->>'category' = 'PERSONALIZED_IMPROVEMENT'
            AND (template.payload->>'score_value')::numeric = 0
        )
        AND (
          SELECT count(*)
          FROM tide.task_step_definitions AS definition
          JOIN tide.task_execution_versions AS execution
            ON execution.id = definition.execution_version_id
          WHERE execution.shared_template_row_id = 'P-FB-NEGATIVE:v1'
        ) = 1
        AND EXISTS (
          SELECT 1
          FROM tide.task_step_definitions AS definition
          JOIN tide.task_execution_versions AS execution
            ON execution.id = definition.execution_version_id
          WHERE execution.shared_template_row_id = 'P-FB-NEGATIVE:v1'
            AND definition.step_key = 'p-fb-negative-environment-photo'
            AND definition.position = 1
            AND definition.step_type = 'UPLOAD'
            AND definition.title = 'Take a teaching-environment photo'
            AND definition.config =
              '{"version":"2026-08-11-personalized-environment-photo-v1","role":"ENVIRONMENT_PHOTO","reviewProfile":"TEACHING_ENVIRONMENT_V1","accept":["image/jpeg"],"captureOnly":true,"maxFiles":1}'::jsonb
        )
        AND (
          SELECT count(*)
          FROM tide.task_validation_rules AS rule
          JOIN tide.task_execution_versions AS execution
            ON execution.id = rule.execution_version_id
          WHERE execution.shared_template_row_id = 'P-FB-NEGATIVE:v1'
        ) = 2
        AND EXISTS (
          SELECT 1
          FROM tide.task_validation_rules AS rule
          JOIN tide.task_execution_versions AS execution
            ON execution.id = rule.execution_version_id
          WHERE execution.shared_template_row_id = 'P-FB-NEGATIVE:v1'
            AND rule.rule_key = 'all-steps-complete'
            AND rule.rule_type = 'ALL_STEPS_COMPLETE'
            AND rule.rule_version =
              '2026-08-11-personalized-environment-photo-v1'
            AND rule.position = 1
            AND rule.config =
              '{"requiredStepKeys":["p-fb-negative-environment-photo"]}'::jsonb
            AND rule.teacher_failure_copy =
              '请拍摄并提交一张当前授课环境照片。'
        )
        AND EXISTS (
          SELECT 1
          FROM tide.task_validation_rules AS rule
          JOIN tide.task_execution_versions AS execution
            ON execution.id = rule.execution_version_id
          WHERE execution.shared_template_row_id = 'P-FB-NEGATIVE:v1'
            AND rule.rule_key = 'p-fb-negative-environment-ai-review'
            AND rule.rule_type = 'AI_IMAGE_REVIEW'
            AND rule.rule_version = '2026-07-27-strict'
            AND rule.position = 2
            AND rule.config =
              '{"stepKey":"p-fb-negative-environment-photo","criteriaVersion":"personalized-teaching-environment-2026-08-v1","criteriaKeys":["camera_angle","lighting","background","dressing"],"allowedMimeTypes":["image/jpeg","image/png","image/webp"],"reviewProfile":"TEACHING_ENVIRONMENT_V1","systemPrompt":"You strictly review teacher-submitted evidence. Return JSON only with this exact shape: {\\"decision\\":\\"PASS|RETRY|ERROR\\",\\"teacherReason\\":\\"teacher-safe concise message\\",\\"confidenceSummary\\":{},\\"criteria\\":[{\\"criterionKey\\":\\"one configured key\\",\\"result\\":\\"PASS|FAIL|UNKNOWN\\",\\"teacherMessage\\":\\"teacher-safe message or null\\"}]}. Include every configured criterion exactly once. Never infer a pass from the mere presence of a person or object. Use UNKNOWN whenever the visual evidence is unclear. PASS only when every configured criterion is visibly and unambiguously PASS; any FAIL or UNKNOWN requires RETRY. Use ERROR only when the file cannot be assessed. Do not expose internal risk labels or private model reasoning.","userText":"Review this current teaching-environment photo strictly against camera angle, lighting, background and dressing only."}'::jsonb
            AND rule.teacher_failure_copy =
              '已保留你完成的内容，请根据提示更新这份材料。'
        )
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
        AND to_regclass('tide.outcome_projections') IS NULL
        AND to_regclass('tide.camp_enrollment_projections') IS NULL
        AND to_regclass('tide.audit_events') IS NULL
        AND to_regclass('tide.task_template_files') IS NULL
        AND to_regclass('tide.file_migrations') IS NULL
        AND to_regclass('tide.teacher_photo_runs') IS NULL
        AND to_regclass('tide.analytics_actor_task_journey_v1') IS NULL
        AND to_regclass(
          'tide.analytics_task_assignment_funnel_v1'
        ) IS NULL
        AND to_regclass('tide.analytics_task_funnel_v1') IS NULL
        AND to_regclass('tide.analytics_task_step_funnel_v1') IS NULL
        AND to_regclass('tide.analytics_content_quality_v1') IS NULL
        AND NOT EXISTS (
          SELECT 1
          FROM information_schema.columns
          WHERE table_schema = 'tide'
            AND table_name = 'file_objects'
            AND column_name = 'visibility'
        )
        AND to_regprocedure('tide.enforce_outbox_target()') IS NULL
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
        AND NOT EXISTS (
          SELECT 1
          FROM pg_class AS relation
          JOIN pg_namespace AS namespace
            ON namespace.oid = relation.relnamespace
          CROSS JOIN unnest(
            ARRAY['SELECT', 'INSERT', 'UPDATE', 'DELETE']::text[]
          ) AS required(privilege_name)
          WHERE namespace.nspname = 'tide'
            AND relation.relkind IN ('r', 'p', 'v', 'm', 'f')
            AND relation.relname <> 'crm_sso_logins'
            AND NOT has_table_privilege(
              current_user,
              relation.oid,
              required.privilege_name
            )
        )
        AND NOT EXISTS (
          SELECT 1
          FROM pg_class AS sequence
          JOIN pg_namespace AS namespace
            ON namespace.oid = sequence.relnamespace
          CROSS JOIN unnest(
            ARRAY['USAGE', 'SELECT']::text[]
          ) AS required(privilege_name)
          WHERE namespace.nspname = 'tide'
            AND sequence.relkind = 'S'
            AND NOT has_sequence_privilege(
              current_user,
              sequence.oid,
              required.privilege_name
            )
        )
        AND NOT EXISTS (
          SELECT 1
          FROM unnest(
            ARRAY['SELECT', 'INSERT', 'UPDATE', 'DELETE']::text[]
          ) AS required(privilege_name)
          WHERE NOT EXISTS (
            SELECT 1
            FROM pg_default_acl AS defaults
            JOIN pg_roles AS owner_role
              ON owner_role.oid = defaults.defaclrole
            JOIN pg_namespace AS namespace
              ON namespace.oid = defaults.defaclnamespace
            CROSS JOIN LATERAL aclexplode(defaults.defaclacl) AS privilege
            WHERE owner_role.rolname = 'tide_sys_admin'
              AND namespace.nspname = 'tide'
              AND defaults.defaclobjtype = 'r'
              AND privilege.privilege_type = required.privilege_name
              AND privilege.grantee = (
                SELECT oid FROM pg_roles WHERE rolname = current_user
              )
          )
        )
        AND NOT EXISTS (
          SELECT 1
          FROM unnest(
            ARRAY['USAGE', 'SELECT']::text[]
          ) AS required(privilege_name)
          WHERE NOT EXISTS (
            SELECT 1
            FROM pg_default_acl AS defaults
            JOIN pg_roles AS owner_role
              ON owner_role.oid = defaults.defaclrole
            JOIN pg_namespace AS namespace
              ON namespace.oid = defaults.defaclnamespace
            CROSS JOIN LATERAL aclexplode(defaults.defaclacl) AS privilege
            WHERE owner_role.rolname = 'tide_sys_admin'
              AND namespace.nspname = 'tide'
              AND defaults.defaclobjtype = 'S'
              AND privilege.privilege_type = required.privilege_name
              AND privilege.grantee = (
                SELECT oid FROM pg_roles WHERE rolname = current_user
              )
          )
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
      CROSS JOIN public_migration_state
    `;
  }

  private productionTransportContract(
    setting: 'TIDE_DATABASE_URL' | 'SHIWEN_READ_DATABASE_URL',
  ): string {
    if (this.config?.get('NODE_ENV', { infer: true }) !== 'production') {
      return 'TRUE';
    }
    const databaseUrl = this.config.get(setting, { infer: true });
    const expectedServerSsl =
      databaseUrl && hasApprovedPrePrivateLineDatabaseUrl(databaseUrl)
        ? 'off'
        : 'on';
    const expectedSessionSsl = expectedServerSsl === 'on' ? 'TRUE' : 'FALSE';
    return `(
      current_setting('ssl') = '${expectedServerSsl}'
      AND COALESCE(
        (
          SELECT ssl
          FROM pg_stat_ssl
          WHERE pid = pg_backend_pid()
        ),
        FALSE
      ) = ${expectedSessionSsl}
    )`;
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
