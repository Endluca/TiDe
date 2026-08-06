#!/usr/bin/env bash
set -euo pipefail

DB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ADMIN_DATABASE_URL="${TIDE_TEST_ADMIN_DATABASE_URL:-postgresql:///postgres}"
TEST_SUFFIX="${$}_${RANDOM}"
FRESH_DB="tide_prod_migration_fresh_${TEST_SUFFIX}"
UPGRADE_DB="tide_prod_migration_upgrade_${TEST_SUFFIX}"

for database_name in "${FRESH_DB}" "${UPGRADE_DB}"; do
  if [[ ! "${database_name}" =~ ^[a-z0-9_]+$ ]]; then
    echo "非法测试数据库名：${database_name}" >&2
    exit 1
  fi
done

ADMIN_PSQL=(psql -X --no-password -v ON_ERROR_STOP=1 "${ADMIN_DATABASE_URL}")

cleanup() {
  "${ADMIN_PSQL[@]}" -c "
    ALTER ROLE tide_migrator
      LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
      NOREPLICATION NOBYPASSRLS
  " >/dev/null 2>&1 || true
  for database_name in "${FRESH_DB}" "${UPGRADE_DB}"; do
    "${ADMIN_PSQL[@]}" -c "DROP DATABASE IF EXISTS \"${database_name}\" WITH (FORCE)" >/dev/null 2>&1 || true
  done
}
trap cleanup EXIT

create_test_database() {
  local database_name="$1"
  "${ADMIN_PSQL[@]}" -c "CREATE DATABASE \"${database_name}\"" >/dev/null
  psql -X --no-password -v ON_ERROR_STOP=1 \
    "postgresql:///${database_name}" \
    -f "${DB_DIR}/fixtures/0001_shared_contract.sql" >/dev/null
  psql -X --no-password -v ON_ERROR_STOP=1 \
    "postgresql:///${database_name}" \
    -f "${DB_DIR}/fixtures/0002_score_entry_contract.sql" >/dev/null
  psql -X --no-password -v ON_ERROR_STOP=1 \
    "postgresql:///${database_name}" \
    -f "${DB_DIR}/fixtures/0003_course_score_snapshot_contract.sql" >/dev/null
  psql -X --no-password -v ON_ERROR_STOP=1 \
    "postgresql:///${database_name}" >/dev/null <<'SQL'
INSERT INTO public.task_templates (
    row_id,
    template_id,
    template_version,
    status,
    revision,
    output_type,
    execution_owner,
    external_task_template_code,
    source_mode,
    payload,
    created_by,
    updated_by,
    integration_mode
)
SELECT
    catalog.row_id,
    catalog.task_code,
    1,
    catalog.status,
    38,
    'TEACHER_TASK',
    'TEACHER_APP',
    catalog.task_code,
    'MOCK',
    jsonb_build_object(
        'category', 'MANDATORY_GROWTH',
        'title', catalog.title,
        'score_value', catalog.score_value,
        'test_fixture', 'production-migrator-rev38'
    ),
    'production_migrator_test',
    'production_migrator_test',
    'INBOUND_STATUS_ONLY'
FROM (
    VALUES
        ('G01:v1', 'G01', 'PUBLISHED', 'Profile & Credentials Completion', 3),
        ('G02:v1', 'G04', 'PUBLISHED', 'Lesson Preparation&Device Network Check', 3),
        ('G03:v1', 'G02', 'PUBLISHED', 'Platform Policies', 2),
        ('G04:v1', 'G03', 'PUBLISHED', 'How to handle different types of students', 2),
        ('G05:v1', 'G00', 'RETIRED', 'Lesson Preparation (retired history)', 0),
        ('G06:v1', 'G05', 'PUBLISHED', 'TTP Orientation', 3),
        ('G07:v1', 'G06', 'PUBLISHED', 'ME Culture & PARSNIP', 4),
        ('G08:v1', 'G07', 'PUBLISHED', 'Reliability Training', 3),
        ('G09:v1', 'G08', 'PUBLISHED', 'Cocos Course Training', 5),
        ('G10:v1', 'G09', 'PUBLISHED', 'SET Teaching Fundamentals', 5)
) AS catalog(row_id, task_code, status, title, score_value);
SQL
}

create_test_database "${FRESH_DB}"
TIDE_MIGRATION_DATABASE_URL="postgresql:///${FRESH_DB}" \
TIDE_MIGRATION_EXPECTED_DATABASE="${FRESH_DB}" \
TIDE_MIGRATION_TEST_MODE="true" \
  bash "${DB_DIR}/scripts/apply-production.sh" >/dev/null

fresh_state="$(psql -X --no-password -AtF '|' "postgresql:///${FRESH_DB}" <<'SQL'
SELECT
    to_regclass('tide.job_leases') IS NOT NULL,
    to_regprocedure(
        'public.append_teacher_support_ticket_operator_message(uuid,bigint,jsonb)'
    ) IS NOT NULL,
    EXISTS (
        SELECT 1
        FROM tide.schema_migrations
        WHERE migration_id = '0022_performance_job_leases'
    ),
    EXISTS (
        SELECT 1
        FROM tide.schema_migrations
        WHERE migration_id IN (
            '0017_task_assignment_teacher_response',
            '0018_remove_task_assignment_teacher_response'
        )
    ),
    position(
        'row_version IS DISTINCT FROM p_expected_row_version' in
        pg_get_functiondef(
            'public.append_teacher_support_ticket_teacher_message(uuid,character varying,bigint,jsonb)'::regprocedure
        )
    ) > 0,
    position(
        'row_version IS DISTINCT FROM p_expected_row_version' in
        pg_get_functiondef(
            'public.append_teacher_support_ticket_operator_message(uuid,bigint,jsonb)'::regprocedure
        )
    ) > 0,
    NOT EXISTS (
        SELECT 1
        FROM unnest(
            ARRAY[
                'public.create_teacher_support_ticket(uuid,character varying,character varying,character varying,jsonb,jsonb)'::regprocedure,
                'public.append_teacher_support_ticket_teacher_message(uuid,character varying,bigint,jsonb)'::regprocedure,
                'public.append_teacher_support_ticket_operator_message(uuid,bigint,jsonb)'::regprocedure,
                'public.mark_teacher_support_ticket_images_deleted(uuid,timestamp with time zone)'::regprocedure
            ]
        ) secured_function(function_oid)
        JOIN pg_proc procedure
          ON procedure.oid = secured_function.function_oid
        JOIN pg_roles owner_role
          ON owner_role.oid = procedure.proowner
        WHERE owner_role.rolname <> 'tide_support_ticket_owner'
           OR NOT procedure.prosecdef
    ),
    NOT EXISTS (SELECT 1 FROM tide.task_execution_versions),
    to_regclass('tide.task_quiz_banks') IS NULL,
    NOT EXISTS (SELECT 1 FROM tide.knowledge_documents),
    to_regclass('tide.kuozhi_course_syncs') IS NOT NULL,
    count(*)
FROM tide.schema_migrations;
SQL
)"
if [[ "${fresh_state}" != "t|t|t|f|t|t|t|t|t|t|t|25" ]]; then
  echo "生产 fresh 迁移状态异常：${fresh_state}" >&2
  exit 1
fi

create_test_database "${UPGRADE_DB}"
psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${UPGRADE_DB}" \
  -c "CREATE SCHEMA tide; CREATE TABLE tide.unmanaged_marker (id integer PRIMARY KEY)" >/dev/null
if TIDE_MIGRATION_DATABASE_URL="postgresql:///${UPGRADE_DB}" \
  TIDE_MIGRATION_EXPECTED_DATABASE="${UPGRADE_DB}" \
  TIDE_MIGRATION_TEST_MODE="true" \
  bash "${DB_DIR}/scripts/apply-production.sh" >/dev/null 2>&1; then
  echo "无账本既有结构被生产迁移器错误认领。" >&2
  exit 1
fi
psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${UPGRADE_DB}" \
  -c "DROP SCHEMA tide CASCADE" >/dev/null

psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${UPGRADE_DB}" \
  -c "UPDATE public.task_templates SET template_id = 'G10' WHERE row_id = 'G10:v1'" >/dev/null
set +e
catalog_guard_output="$(
  TIDE_MIGRATION_DATABASE_URL="postgresql:///${UPGRADE_DB}" \
  TIDE_MIGRATION_EXPECTED_DATABASE="${UPGRADE_DB}" \
  TIDE_MIGRATION_TARGET="0021_teacher_support_tickets" \
  TIDE_MIGRATION_TEST_MODE="true" \
    bash "${DB_DIR}/scripts/apply-production.sh" 2>&1
)"
catalog_guard_status=$?
set -e
if [[ "${catalog_guard_status}" == "0" \
      || "${catalog_guard_output}" != *"rev38 稳定映射"* ]]; then
  echo "生产迁移未阻断非权威固定任务目录：${catalog_guard_output}" >&2
  exit 1
fi
psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${UPGRADE_DB}" \
  -c "UPDATE public.task_templates SET template_id = 'G09' WHERE row_id = 'G10:v1'" >/dev/null

TIDE_MIGRATION_DATABASE_URL="postgresql:///${UPGRADE_DB}" \
TIDE_MIGRATION_EXPECTED_DATABASE="${UPGRADE_DB}" \
TIDE_MIGRATION_TARGET="0021_teacher_support_tickets" \
TIDE_MIGRATION_TEST_MODE="true" \
  bash "${DB_DIR}/scripts/apply-production.sh" >/dev/null

psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${UPGRADE_DB}" >/dev/null <<'SQL'
INSERT INTO tide.task_execution_versions (
    id,
    shared_template_row_id,
    task_code,
    execution_contract_version,
    config,
    status
)
SELECT
    catalog.execution_id::uuid,
    catalog.row_id,
    catalog.previous_task_code,
    'v1',
    jsonb_build_object('migration_test', true),
    'ACTIVE'
FROM (
    VALUES
        ('25abcdef-0000-4000-8000-000000000001', 'G01:v1', 'G01'),
        ('25abcdef-0000-4000-8000-000000000002', 'G02:v1', 'G02'),
        ('25abcdef-0000-4000-8000-000000000003', 'G03:v1', 'G03'),
        ('25abcdef-0000-4000-8000-000000000004', 'G04:v1', 'G04'),
        ('25abcdef-0000-4000-8000-000000000005', 'G05:v1', 'G05'),
        ('25abcdef-0000-4000-8000-000000000006', 'G06:v1', 'G06'),
        ('25abcdef-0000-4000-8000-000000000007', 'G07:v1', 'G07'),
        ('25abcdef-0000-4000-8000-000000000008', 'G08:v1', 'G08'),
        ('25abcdef-0000-4000-8000-000000000009', 'G09:v1', 'G09'),
        ('25abcdef-0000-4000-8000-000000000010', 'G10:v1', 'G10')
) AS catalog(execution_id, row_id, previous_task_code);

INSERT INTO public.teachers (
    teacher_id,
    camp_enrollment_id,
    name,
    data_mode
)
VALUES (
    'MIGRATION-SEMANTIC-TEACHER',
    'MIGRATION-SEMANTIC-CAMP',
    '[Verify] Semantic migration teacher',
    'MOCK'
);

INSERT INTO public.task_assignments (
    assignment_id,
    teacher_id,
    task_code,
    template_version_id,
    task_kind,
    creator_system,
    status,
    priority,
    why,
    source_mode,
    dedupe_key
)
VALUES (
    'MIGRATION-SEMANTIC-G04',
    'MIGRATION-SEMANTIC-TEACHER',
    'G04',
    'G02:v1',
    'FIXED_GROWTH',
    'TRIGGER_CENTER',
    'ASSIGNED',
    'P1',
    '[Verify] Preserve assignment identity and progress.',
    'MOCK',
    'fixed:MIGRATION-SEMANTIC-TEACHER:G04'
);

UPDATE public.task_assignments
SET
    status = 'VIEWED',
    status_changed_at = status_changed_at + interval '1 second'
WHERE assignment_id = 'MIGRATION-SEMANTIC-G04';

UPDATE public.task_assignments
SET
    status = 'IN_PROGRESS',
    status_changed_at = status_changed_at + interval '1 second'
WHERE assignment_id = 'MIGRATION-SEMANTIC-G04';

INSERT INTO tide.task_step_definitions (
    id,
    execution_version_id,
    step_key,
    position,
    step_type,
    title,
    config
)
VALUES (
    '25abcdef-0000-4000-8000-000000000102',
    '25abcdef-0000-4000-8000-000000000002',
    'g02-device-check',
    1,
    'DEVICE_CHECK',
    '[Verify] Preserve legacy step key',
    '{"migration_test":true}'::jsonb
);

INSERT INTO tide.task_step_progress (
    id,
    task_assignment_id,
    step_key,
    status,
    percent,
    progress_summary,
    first_started_at
)
VALUES (
    '25abcdef-0000-4000-8000-000000000202',
    'MIGRATION-SEMANTIC-G04',
    'g02-device-check',
    'IN_PROGRESS',
    50,
    '{"checkpoint":"before-0025"}'::jsonb,
    '2026-07-30 00:00:00+00'
);

INSERT INTO tide.app_events (
    id,
    anonymous_teacher_id,
    event_name,
    event_id,
    event_schema_version,
    event_source,
    session_id,
    task_assignment_id,
    properties,
    occurred_at
)
VALUES (
    '25abcdef-0000-4000-8000-000000000302',
    repeat('a', 64),
    'TASK_DETAIL_VIEWED',
    'migration-semantic-before-0025',
    1,
    'CLIENT',
    'migration-semantic-session',
    'MIGRATION-SEMANTIC-G04',
    '{
      "taskCode":"G02",
      "taskType":"FIXED_GROWTH",
      "templateVersion":"1",
      "executionContractVersion":"v1",
      "entrySource":"TASK_LIST",
      "displayPosition":"PRIMARY"
    }'::jsonb,
    '2026-07-30 00:00:00+00'
),
(
    '25abcdef-0000-4000-8000-000000000303',
    repeat('b', 64),
    'PAGE_VIEWED',
    'migration-unresolved-before-0025',
    1,
    'CLIENT',
    'migration-unresolved-session',
    NULL,
    '{"taskCode":"G02","page":"/legacy-task"}'::jsonb,
    '2026-07-29 23:59:00+00'
);
SQL

pre_upgrade_state="$(psql -X --no-password -AtF '|' "postgresql:///${UPGRADE_DB}" <<'SQL'
SELECT
    to_regclass('tide.job_leases') IS NULL,
    count(*) = 19,
    (SELECT count(*) FROM tide.task_execution_versions) = 10,
    (
        SELECT count(*)
        FROM (
            VALUES
                ('25abcdef-0000-4000-8000-000000000001'::uuid, 'G01:v1', 'G01'),
                ('25abcdef-0000-4000-8000-000000000002'::uuid, 'G02:v1', 'G02'),
                ('25abcdef-0000-4000-8000-000000000003'::uuid, 'G03:v1', 'G03'),
                ('25abcdef-0000-4000-8000-000000000004'::uuid, 'G04:v1', 'G04'),
                ('25abcdef-0000-4000-8000-000000000005'::uuid, 'G05:v1', 'G05'),
                ('25abcdef-0000-4000-8000-000000000006'::uuid, 'G06:v1', 'G06'),
                ('25abcdef-0000-4000-8000-000000000007'::uuid, 'G07:v1', 'G07'),
                ('25abcdef-0000-4000-8000-000000000008'::uuid, 'G08:v1', 'G08'),
                ('25abcdef-0000-4000-8000-000000000009'::uuid, 'G09:v1', 'G09'),
                ('25abcdef-0000-4000-8000-000000000010'::uuid, 'G10:v1', 'G10')
        ) expected(execution_id, row_id, previous_task_code)
        JOIN tide.task_execution_versions execution
          ON execution.id = expected.execution_id
         AND execution.shared_template_row_id = expected.row_id
         AND execution.task_code = expected.previous_task_code
         AND execution.status = 'ACTIVE'
    ) = 10
FROM tide.schema_migrations;
SQL
)"
if [[ "${pre_upgrade_state}" != "t|t|t|t" ]]; then
  echo "生产 upgrade 前置状态异常：${pre_upgrade_state}" >&2
  exit 1
fi

psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${UPGRADE_DB}" \
  -c "
    DELETE FROM tide.task_execution_versions
    WHERE id = '25abcdef-0000-4000-8000-000000000010'::uuid
  " >/dev/null

set +e
partial_guard_output="$(
  TIDE_MIGRATION_DATABASE_URL="postgresql:///${UPGRADE_DB}" \
  TIDE_MIGRATION_EXPECTED_DATABASE="${UPGRADE_DB}" \
  TIDE_MIGRATION_TEST_MODE="true" \
    bash "${DB_DIR}/scripts/apply-production.sh" 2>&1
)"
partial_guard_status=$?
set -e

partial_guard_state="$(psql -X --no-password -AtF '|' "postgresql:///${UPGRADE_DB}" <<'SQL'
SELECT
    (SELECT count(*) FROM tide.task_execution_versions) = 9,
    NOT EXISTS (
        SELECT 1
        FROM tide.task_execution_versions
        WHERE task_code LIKE 'TMP-0025-%'
    ),
    NOT EXISTS (
        SELECT 1
        FROM tide.schema_migrations
        WHERE migration_id = '0025_fixed_task_semantic_alignment'
    );
SQL
)"
if [[ "${partial_guard_status}" == "0" \
      || "${partial_guard_output}" != *"partial (9/10)"* \
      || "${partial_guard_state}" != "t|t|t" ]]; then
  echo "0025 未原子拒绝 9/10 固定 execution：${partial_guard_state}" >&2
  echo "${partial_guard_output}" >&2
  exit 1
fi

psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${UPGRADE_DB}" >/dev/null <<'SQL'
INSERT INTO tide.task_execution_versions (
    id,
    shared_template_row_id,
    task_code,
    execution_contract_version,
    config,
    status
)
VALUES (
    '25abcdef-0000-4000-8000-000000000010',
    'G10:v1',
    'G10',
    'v1',
    '{"migration_test":true}'::jsonb,
    'ACTIVE'
);
SQL

TIDE_MIGRATION_DATABASE_URL="postgresql:///${UPGRADE_DB}" \
TIDE_MIGRATION_EXPECTED_DATABASE="${UPGRADE_DB}" \
TIDE_MIGRATION_TEST_MODE="true" \
  bash "${DB_DIR}/scripts/apply-production.sh" >/dev/null

psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${UPGRADE_DB}" >/dev/null <<'SQL'
INSERT INTO tide.app_events (
    id,
    anonymous_teacher_id,
    event_name,
    event_id,
    event_schema_version,
    event_source,
    session_id,
    task_assignment_id,
    properties,
    occurred_at
)
VALUES (
    '25abcdef-0000-4000-8000-000000000304',
    repeat('a', 64),
    'TASK_STARTED',
    'migration-semantic-after-0025',
    1,
    'BACKEND',
    'migration-semantic-session',
    'MIGRATION-SEMANTIC-G04',
    '{
      "taskCode":"G04",
      "taskType":"FIXED_GROWTH",
      "templateVersion":"1",
      "executionContractVersion":"v1",
      "entrySource":"TASK_LIST",
      "displayPosition":"PRIMARY"
    }'::jsonb,
    '2026-07-30 00:01:00+00'
);
SQL

semantic_alignment_state="$(psql -X --no-password -AtF '|' "postgresql:///${UPGRADE_DB}" <<'SQL'
SELECT
    (
        SELECT count(*)
        FROM (
            VALUES
                ('25abcdef-0000-4000-8000-000000000001'::uuid, 'G01:v1', 'G01', 'ACTIVE'),
                ('25abcdef-0000-4000-8000-000000000002'::uuid, 'G02:v1', 'G04', 'ACTIVE'),
                ('25abcdef-0000-4000-8000-000000000003'::uuid, 'G03:v1', 'G02', 'ACTIVE'),
                ('25abcdef-0000-4000-8000-000000000004'::uuid, 'G04:v1', 'G03', 'ACTIVE'),
                ('25abcdef-0000-4000-8000-000000000005'::uuid, 'G05:v1', 'G00', 'RETIRED'),
                ('25abcdef-0000-4000-8000-000000000006'::uuid, 'G06:v1', 'G05', 'ACTIVE'),
                ('25abcdef-0000-4000-8000-000000000007'::uuid, 'G07:v1', 'G06', 'ACTIVE'),
                ('25abcdef-0000-4000-8000-000000000008'::uuid, 'G08:v1', 'G07', 'ACTIVE'),
                ('25abcdef-0000-4000-8000-000000000009'::uuid, 'G09:v1', 'G08', 'ACTIVE'),
                ('25abcdef-0000-4000-8000-000000000010'::uuid, 'G10:v1', 'G09', 'ACTIVE')
        ) expected(execution_id, row_id, task_code, expected_status)
        JOIN tide.task_execution_versions execution
          ON execution.id = expected.execution_id
         AND execution.shared_template_row_id = expected.row_id
         AND execution.task_code = expected.task_code
         AND execution.status = expected.expected_status
    ) = 10,
    NOT EXISTS (
        SELECT 1
        FROM tide.task_execution_versions
        WHERE task_code LIKE 'TMP-0025-%'
    ),
    EXISTS (
        SELECT 1
        FROM tide.task_step_definitions definition
        WHERE definition.id =
              '25abcdef-0000-4000-8000-000000000102'::uuid
          AND definition.execution_version_id =
              '25abcdef-0000-4000-8000-000000000002'::uuid
          AND definition.step_key = 'g02-device-check'
          AND definition.position = 1
          AND definition.config =
              '{"migration_test":true}'::jsonb
    ),
    EXISTS (
        SELECT 1
        FROM public.task_assignments assignment
        WHERE assignment.assignment_id = 'MIGRATION-SEMANTIC-G04'
          AND assignment.teacher_id = 'MIGRATION-SEMANTIC-TEACHER'
          AND assignment.task_code = 'G04'
          AND assignment.template_version_id = 'G02:v1'
          AND assignment.status = 'IN_PROGRESS'
          AND assignment.row_version = 3
    ),
    EXISTS (
        SELECT 1
        FROM tide.task_step_progress progress
        WHERE progress.id =
              '25abcdef-0000-4000-8000-000000000202'::uuid
          AND progress.task_assignment_id = 'MIGRATION-SEMANTIC-G04'
          AND progress.step_key = 'g02-device-check'
          AND progress.status = 'IN_PROGRESS'
          AND progress.percent = 50
          AND progress.progress_summary =
              '{"checkpoint":"before-0025"}'::jsonb
    ),
    (
        SELECT count(*)
        FROM tide.schema_migrations
        WHERE migration_id = '0025_fixed_task_semantic_alignment'
    ) = 1;
SQL
)"
if [[ "${semantic_alignment_state}" != "t|t|t|t|t|t" ]]; then
  echo "0025 固定任务语义迁移未保留执行 ID/稳定模板行：${semantic_alignment_state}" >&2
  exit 1
fi

analytics_semantics_state="$(psql -X --no-password -AtF '|' "postgresql:///${UPGRADE_DB}" <<'SQL'
SELECT
    (
        SELECT
            count(*) = 2
            AND count(*) FILTER (WHERE properties->>'taskCode' = 'G02') = 1
            AND count(*) FILTER (WHERE properties->>'taskCode' = 'G04') = 1
        FROM tide.app_events
        WHERE task_assignment_id = 'MIGRATION-SEMANTIC-G04'
    ),
    (
        SELECT
            count(*) = 2
            AND count(*) FILTER (WHERE task_code = 'G02') = 1
            AND count(*) FILTER (WHERE task_code = 'G04') = 1
        FROM tide.analytics_actor_task_journey_v1
        WHERE task_assignment_id = 'MIGRATION-SEMANTIC-G04'
    ),
    (
        SELECT
            count(*) = 1
            AND bool_and(task_code = 'G02')
            AND bool_and(viewed_at = '2026-07-30 00:00:00+00')
            AND bool_and(started_at = '2026-07-30 00:01:00+00')
            AND bool_and(start_delay_ms = 60000)
        FROM tide.analytics_task_assignment_funnel_v1
        WHERE task_assignment_id = 'MIGRATION-SEMANTIC-G04'
    ),
    (
        SELECT
            count(*) = 2
            AND bool_and(task_code = 'G04')
            AND bool_and(raw_task_code IN ('G02', 'G04'))
            AND count(*) FILTER (WHERE raw_task_code = 'G02') = 1
            AND count(*) FILTER (WHERE raw_task_code = 'G04') = 1
            AND bool_and(stable_template_row_id = 'G02:v1')
            AND bool_and(task_code_resolution = 'STABLE_TEMPLATE')
        FROM tide.analytics_actor_task_journey_v2
        WHERE task_assignment_id = 'MIGRATION-SEMANTIC-G04'
    ),
    (
        SELECT
            count(*) = 1
            AND bool_and(task_code = 'G04')
            AND bool_and(raw_task_codes = ARRAY['G02', 'G04']::text[])
            AND bool_and(stable_template_row_id = 'G02:v1')
            AND bool_and(task_code_resolution = 'STABLE_TEMPLATE')
            AND bool_and(viewed_at = '2026-07-30 00:00:00+00')
            AND bool_and(started_at = '2026-07-30 00:01:00+00')
            AND bool_and(start_delay_ms = 60000)
        FROM tide.analytics_task_assignment_funnel_v2
        WHERE task_assignment_id = 'MIGRATION-SEMANTIC-G04'
    ),
    (
        SELECT count(*)
        FROM tide.app_events
        WHERE task_assignment_id = 'MIGRATION-SEMANTIC-G04'
    ) = (
        SELECT count(*)
        FROM tide.analytics_actor_task_journey_v1
        WHERE task_assignment_id = 'MIGRATION-SEMANTIC-G04'
    ),
    (
        SELECT count(*)
        FROM tide.app_events
        WHERE task_assignment_id = 'MIGRATION-SEMANTIC-G04'
    ) = (
        SELECT count(*)
        FROM tide.analytics_actor_task_journey_v2
        WHERE task_assignment_id = 'MIGRATION-SEMANTIC-G04'
    ),
    (
        SELECT count(DISTINCT task_assignment_id)
        FROM tide.app_events
        WHERE task_assignment_id = 'MIGRATION-SEMANTIC-G04'
    ) = (
        SELECT count(*)
        FROM tide.analytics_task_assignment_funnel_v1
        WHERE task_assignment_id = 'MIGRATION-SEMANTIC-G04'
    ),
    (
        SELECT count(*)
        FROM tide.analytics_task_assignment_funnel_v1
        WHERE task_assignment_id = 'MIGRATION-SEMANTIC-G04'
    ) = (
        SELECT count(*)
        FROM tide.analytics_task_assignment_funnel_v2
        WHERE task_assignment_id = 'MIGRATION-SEMANTIC-G04'
    ),
    EXISTS (
        SELECT 1
        FROM tide.analytics_actor_task_journey_v2
        WHERE event_id = 'migration-unresolved-before-0025'
          AND raw_task_code = 'G02'
          AND task_code IS NULL
          AND stable_template_row_id IS NULL
          AND task_code_resolution = 'UNRESOLVED'
    ),
    (
        SELECT count(*)
        FROM tide.app_events
    ) = (
        SELECT count(*)
        FROM tide.analytics_actor_task_journey_v1
    )
    AND (
        SELECT count(*)
        FROM tide.app_events
    ) = (
        SELECT count(*)
        FROM tide.analytics_actor_task_journey_v2
    );
SQL
)"
if [[ "${analytics_semantics_state}" != "t|t|t|t|t|t|t|t|t|t|t" ]]; then
  echo "0025 analytics v2 未保留 raw 证据或稳定 assignment 语义：${analytics_semantics_state}" >&2
  exit 1
fi

analytics_index_state="$(psql -X --no-password -AtF '|' "postgresql:///${UPGRADE_DB}" <<'SQL'
SELECT
    to_regclass('tide.app_events_task_name_time_idx') IS NOT NULL,
    to_regclass('public.task_assignments_pkey') IS NOT NULL,
    to_regclass('public.task_templates_pkey') IS NOT NULL;
SQL
)"
if [[ "${analytics_index_state}" != "t|t|t" ]]; then
  echo "analytics v2 稳定身份解析缺少关联索引：${analytics_index_state}" >&2
  exit 1
fi

analytics_resolution_plan="$(psql -X --no-password -At "postgresql:///${UPGRADE_DB}" <<'SQL'
EXPLAIN (COSTS OFF, FORMAT TEXT)
SELECT
    event_id,
    task_code,
    raw_task_code,
    stable_template_row_id,
    task_code_resolution
FROM tide.analytics_task_event_semantics_v2
WHERE task_assignment_id = 'MIGRATION-SEMANTIC-G04'
ORDER BY occurred_at;
SQL
)"
if [[ "${analytics_resolution_plan}" != *"app_events"* \
      || "${analytics_resolution_plan}" != *"task_assignments"* \
      || "${analytics_resolution_plan}" != *"task_templates"* ]]; then
  echo "analytics v2 基础解析查询计划异常：${analytics_resolution_plan}" >&2
  exit 1
fi
if [[ "${analytics_resolution_plan}" == *"app_events_task_name_time_idx"* \
      && "${analytics_resolution_plan}" == *"task_assignments_pkey"* \
      && "${analytics_resolution_plan}" == *"task_templates_pkey"* ]]; then
  echo "analytics v2 小样本 EXPLAIN 使用事件索引及 assignment/template 主键；本结果不作为生产性能证据。"
elif [[ "${analytics_resolution_plan}" == *"Seq Scan"* ]]; then
  echo "analytics v2 小样本 EXPLAIN 采用 Seq Scan；三张关联表索引存在，本结果不作为生产性能证据。"
else
  echo "analytics v2 小样本 EXPLAIN 已生成；三张关联表索引存在，本结果不作为生产性能证据。"
fi

psql -X --no-password -v ON_ERROR_STOP=1 \
  -v from_time="2026-07-29" \
  -v to_time="2026-08-01" \
  -v task_code="G04" \
  -v task_assignment_id="MIGRATION-SEMANTIC-G04" \
  "postgresql:///${UPGRADE_DB}" \
  -f "${DB_DIR}/analytics/product_analytics_queries.sql" >/dev/null

psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${UPGRADE_DB}" >/dev/null <<'SQL'
INSERT INTO public.teachers (
    teacher_id,
    camp_enrollment_id,
    name,
    data_mode
)
VALUES (
    'MIGRATION-VERIFY-TEACHER',
    'MIGRATION-VERIFY-CAMP',
    '[Verify] Teacher',
    'MOCK'
);

SELECT public.create_teacher_support_ticket(
    '42000000-0000-4000-8000-000000000001',
    'MIGRATION-VERIFY-TEACHER',
    'PRODUCT_FUNCTION',
    'HELP',
    '{}'::jsonb,
    '{
      "message_id":"42000000-0000-4000-8000-000000000011",
      "sender":"TEACHER",
      "content":"[Verify] Initial message.",
      "images":[]
    }'::jsonb
);

-- 生产由 DBA 独立执行教师应用授权；这里仅补齐角色级函数回归所需权限。
GRANT EXECUTE ON FUNCTION public.append_teacher_support_ticket_teacher_message(
    uuid, varchar, bigint, jsonb
) TO tit_teacher_crud;

DO $verify$
DECLARE
    teacher_null_rejected boolean := false;
    operator_null_rejected boolean := false;
BEGIN
    BEGIN
        PERFORM public.append_teacher_support_ticket_teacher_message(
            '42000000-0000-4000-8000-000000000001',
            'MIGRATION-VERIFY-TEACHER',
            NULL,
            '{
              "message_id":"42000000-0000-4000-8000-000000000012",
              "sender":"TEACHER",
              "content":"[Verify] NULL CAS must fail.",
              "images":[]
            }'::jsonb
        );
    EXCEPTION WHEN serialization_failure THEN
        teacher_null_rejected := true;
    END;

    BEGIN
        PERFORM public.append_teacher_support_ticket_operator_message(
            '42000000-0000-4000-8000-000000000001',
            NULL,
            '{
              "message_id":"42000000-0000-4000-8000-000000000022",
              "sender":"OPERATOR",
              "content":"[Verify] NULL CAS must fail.",
              "images":[]
            }'::jsonb
        );
    EXCEPTION WHEN serialization_failure THEN
        operator_null_rejected := true;
    END;

    IF NOT teacher_null_rejected OR NOT operator_null_rejected THEN
        RAISE EXCEPTION 'NULL expected row version bypassed CAS';
    END IF;

    IF (
        SELECT row_version = 1 AND jsonb_array_length(messages) = 1
        FROM public.teacher_support_tickets
        WHERE ticket_id = '42000000-0000-4000-8000-000000000001'
    ) IS DISTINCT FROM true THEN
        RAISE EXCEPTION 'NULL CAS attempt mutated support ticket';
    END IF;
END
$verify$;

SET ROLE tit_teacher_crud;

DO $verify$
DECLARE
    null_message_rejected boolean := false;
BEGIN
    BEGIN
        PERFORM public.append_teacher_support_ticket_teacher_message(
            '42000000-0000-4000-8000-000000000001',
            'MIGRATION-VERIFY-TEACHER',
            1,
            NULL
        );
    EXCEPTION WHEN invalid_parameter_value THEN
        null_message_rejected := true;
    END;

    IF NOT null_message_rejected THEN
        RAISE EXCEPTION 'teacher NULL message bypassed validation';
    END IF;
END
$verify$;

RESET ROLE;
SET ROLE tit_growth_app;

DO $verify$
DECLARE
    null_message_rejected boolean := false;
BEGIN
    BEGIN
        PERFORM public.append_teacher_support_ticket_operator_message(
            '42000000-0000-4000-8000-000000000001',
            1,
            NULL
        );
    EXCEPTION WHEN invalid_parameter_value THEN
        null_message_rejected := true;
    END;

    IF NOT null_message_rejected THEN
        RAISE EXCEPTION 'operator NULL message bypassed validation';
    END IF;
END
$verify$;

RESET ROLE;

DO $verify$
BEGIN
    IF (
        SELECT row_version = 1 AND jsonb_array_length(messages) = 1
        FROM public.teacher_support_tickets
        WHERE ticket_id = '42000000-0000-4000-8000-000000000001'
    ) IS DISTINCT FROM true THEN
        RAISE EXCEPTION 'NULL message attempt mutated support ticket';
    END IF;
END
$verify$;

SET ROLE tit_growth_app;

SELECT public.append_teacher_support_ticket_operator_message(
    '42000000-0000-4000-8000-000000000001',
    1,
    '{
      "message_id":"42000000-0000-4000-8000-000000000021",
      "sender":"OPERATOR",
      "content":"[Verify] Operator reply.",
      "images":[]
    }'::jsonb
);

RESET ROLE;
SQL

atomic_state="$(psql -X --no-password -AtF '|' "postgresql:///${UPGRADE_DB}" <<'SQL'
SELECT
    status,
    last_operator_reply_at IS NOT NULL,
    (messages->1->>'created_at')::timestamptz = last_operator_reply_at,
    teacher_reply_deadline_at - last_operator_reply_at = interval '48 hours',
    row_version,
    jsonb_array_length(messages),
    has_table_privilege(
        'tit_growth_app',
        'public.teacher_support_tickets',
        'SELECT'
    ),
    has_table_privilege(
        'tit_growth_app',
        'public.teacher_support_tickets',
        'UPDATE'
    ),
    has_function_privilege(
        'tit_growth_app',
        'public.append_teacher_support_ticket_operator_message(uuid,bigint,jsonb)',
        'EXECUTE'
    ),
    has_function_privilege(
        'tit_growth_app',
        'public.append_teacher_support_ticket_teacher_message(uuid,character varying,bigint,jsonb)',
        'EXECUTE'
    ),
    (
        SELECT NOT rolcanlogin AND NOT rolsuper
        FROM pg_roles
        WHERE rolname = 'tide_support_ticket_owner'
    ),
    has_table_privilege(
        'tide_support_ticket_owner',
        'public.teacher_support_tickets',
        'SELECT'
    ),
    has_table_privilege(
        'tide_support_ticket_owner',
        'public.teacher_support_tickets',
        'DELETE'
    ),
    has_schema_privilege(
        'tide_support_ticket_owner',
        'public',
        'CREATE'
    )
FROM public.teacher_support_tickets
WHERE ticket_id = '42000000-0000-4000-8000-000000000001';
SQL
)"
if [[ "${atomic_state}" != "WAITING_TEACHER|t|t|t|2|2|t|f|t|f|t|t|f|f" ]]; then
  echo "运营回复原子性或最小权限异常：${atomic_state}" >&2
  exit 1
fi

psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${UPGRADE_DB}" \
  -f "${DB_DIR}/migrations/0025_fixed_task_semantic_alignment.down.sql" >/dev/null
semantic_down_state="$(psql -X --no-password -Atqc "
  SELECT count(*) = 10
  FROM (
    VALUES
      ('G01:v1', 'G01'), ('G02:v1', 'G04'),
      ('G03:v1', 'G02'), ('G04:v1', 'G03'),
      ('G05:v1', 'G00'), ('G06:v1', 'G05'),
      ('G07:v1', 'G06'), ('G08:v1', 'G07'),
      ('G09:v1', 'G08'), ('G10:v1', 'G09')
  ) expected(row_id, task_code)
  JOIN tide.task_execution_versions execution
    ON execution.shared_template_row_id = expected.row_id
   AND execution.task_code = expected.task_code
" "postgresql:///${UPGRADE_DB}")"
if [[ "${semantic_down_state}" != "t" ]]; then
  echo "0025 forward-only down 错误恢复了旧任务语义。" >&2
  exit 1
fi
psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${UPGRADE_DB}" \
  -f "${DB_DIR}/migrations/0024_support_ticket_cas_and_function_owner.down.sql" >/dev/null
down_state="$(psql -X --no-password -AtF '|' "postgresql:///${UPGRADE_DB}" <<'SQL'
SELECT
    position(
        'row_version IS DISTINCT FROM p_expected_row_version' in
        pg_get_functiondef(
            'public.append_teacher_support_ticket_teacher_message(uuid,character varying,bigint,jsonb)'::regprocedure
        )
    ) > 0,
    position(
        'row_version IS DISTINCT FROM p_expected_row_version' in
        pg_get_functiondef(
            'public.append_teacher_support_ticket_operator_message(uuid,bigint,jsonb)'::regprocedure
        )
    ) > 0,
    (
        SELECT owner_role.rolname
        FROM pg_proc procedure
        JOIN pg_roles owner_role
          ON owner_role.oid = procedure.proowner
        WHERE procedure.oid =
            'public.append_teacher_support_ticket_operator_message(uuid,bigint,jsonb)'::regprocedure
    ) = 'tide_support_ticket_owner';
SQL
)"
if [[ "${down_state}" != "t|t|t" ]]; then
  echo "0024 forward-only 回滚错误恢复了安全漏洞：${down_state}" >&2
  exit 1
fi

set +e
guard_output="$(
  TIDE_MIGRATION_DATABASE_URL="postgresql:///${UPGRADE_DB}" \
  TIDE_MIGRATION_EXPECTED_DATABASE="${UPGRADE_DB}" \
    bash "${DB_DIR}/scripts/apply-production.sh" 2>&1
)"
guard_status=$?
set -e
if [[ "${guard_status}" == "0" || "${guard_output}" != *"sslmode=verify-full"* ]]; then
  echo "生产迁移未阻断缺失 verify-full 的连接。" >&2
  exit 1
fi

set +e
guard_output="$(
  TIDE_MIGRATION_DATABASE_URL="postgresql:///${UPGRADE_DB}" \
  TIDE_MIGRATION_EXPECTED_DATABASE="wrong_${UPGRADE_DB}" \
  TIDE_MIGRATION_TEST_MODE="true" \
    bash "${DB_DIR}/scripts/apply-production.sh" 2>&1
)"
guard_status=$?
set -e
if [[ "${guard_status}" == "0" || "${guard_output}" != *"实际目标库"* ]]; then
  echo "生产迁移未阻断错误目标库。" >&2
  exit 1
fi

set +e
guard_output="$(
  PGOPTIONS="-c role=tit_growth_app" \
  TIDE_MIGRATION_DATABASE_URL="postgresql:///${UPGRADE_DB}?sslmode=verify-full" \
  TIDE_MIGRATION_EXPECTED_DATABASE="${UPGRADE_DB}" \
    bash "${DB_DIR}/scripts/apply-production.sh" 2>&1
)"
guard_status=$?
set -e
if [[ "${guard_status}" == "0" || "${guard_output}" != *"current_user 必须精确为 tide_migrator"* ]]; then
  echo "生产迁移未按账号守卫阻断错误数据库账号：${guard_output}" >&2
  exit 1
fi

"${ADMIN_PSQL[@]}" -c "ALTER ROLE tide_migrator SUPERUSER" >/dev/null
set +e
guard_output="$(
  PGOPTIONS="-c role=tide_migrator" \
  TIDE_MIGRATION_DATABASE_URL="postgresql:///${UPGRADE_DB}?sslmode=verify-full" \
  TIDE_MIGRATION_EXPECTED_DATABASE="${UPGRADE_DB}" \
    bash "${DB_DIR}/scripts/apply-production.sh" 2>&1
)"
guard_status=$?
set -e
"${ADMIN_PSQL[@]}" -c "
  ALTER ROLE tide_migrator
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
" >/dev/null
if [[ "${guard_status}" == "0" || "${guard_output}" != *"禁止使用 superuser"* ]]; then
  echo "生产迁移未按 superuser 守卫阻断：${guard_output}" >&2
  exit 1
fi

set +e
guard_output="$(
  PGOPTIONS="-c role=tide_migrator" \
  TIDE_MIGRATION_DATABASE_URL="postgresql:///${UPGRADE_DB}?sslmode=verify-full" \
  TIDE_MIGRATION_EXPECTED_DATABASE="${UPGRADE_DB}" \
    bash "${DB_DIR}/scripts/apply-production.sh" 2>&1
)"
guard_status=$?
set -e
if [[ "${guard_status}" == "0" || "${guard_output}" != *"未实际使用 TLS"* ]]; then
  echo "生产迁移未按 TLS 守卫阻断会话：${guard_output}" >&2
  exit 1
fi

psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${UPGRADE_DB}" \
  -c "UPDATE tide.schema_migrations SET sha256 = repeat('0', 64) WHERE migration_id = '0001_initial'" >/dev/null

if TIDE_MIGRATION_DATABASE_URL="postgresql:///${UPGRADE_DB}" \
  TIDE_MIGRATION_EXPECTED_DATABASE="${UPGRADE_DB}" \
  TIDE_MIGRATION_TEST_MODE="true" \
  bash "${DB_DIR}/scripts/apply-production.sh" >/dev/null 2>&1; then
  echo "迁移 checksum 被篡改后仍然通过。" >&2
  exit 1
fi

echo "生产 migrator fresh/upgrade、0022–0027、0/10 门禁、analytics v2、NULL CAS/message、固定 owner、连接守卫与 checksum 验证通过。"
