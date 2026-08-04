#!/usr/bin/env bash
set -euo pipefail

DB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATABASE_URL="${TIDE_MIGRATION_DATABASE_URL:-}"
EXPECTED_DATABASE="${TIDE_MIGRATION_EXPECTED_DATABASE:-}"
TARGET_MIGRATION="${TIDE_MIGRATION_TARGET:-0026_kuozhi_course_syncs}"
MIGRATION_TEST_MODE="${TIDE_MIGRATION_TEST_MODE:-false}"

if [[ -z "${DATABASE_URL}" ]]; then
  echo "TIDE_MIGRATION_DATABASE_URL 不能为空；必须使用独立迁移账号。" >&2
  exit 1
fi
if [[ -z "${EXPECTED_DATABASE}" ]]; then
  echo "TIDE_MIGRATION_EXPECTED_DATABASE 不能为空；禁止从连接串猜测目标库。" >&2
  exit 1
fi

if [[ "${MIGRATION_TEST_MODE}" != "true" ]]; then
  if [[ ! "${DATABASE_URL}" =~ ^postgres(ql)?:// ]]; then
    echo "生产迁移只接受显式 PostgreSQL URI。" >&2
    exit 1
  fi
  connection_query="${DATABASE_URL#*\?}"
  if [[ "${connection_query}" == "${DATABASE_URL}" ]]; then
    echo "生产迁移连接必须且只能声明一次 sslmode=verify-full。" >&2
    exit 1
  fi
  sslmode_count=0
  sslmode_value=""
  IFS='&' read -r -a connection_parameters <<<"${connection_query}"
  for connection_parameter in "${connection_parameters[@]}"; do
    if [[ "${connection_parameter%%=*}" == "sslmode" ]]; then
      sslmode_count=$((sslmode_count + 1))
      sslmode_value="${connection_parameter#*=}"
    fi
  done
  if [[ "${sslmode_count}" != "1" || "${sslmode_value}" != "verify-full" ]]; then
    echo "生产迁移连接必须且只能声明一次 sslmode=verify-full。" >&2
    exit 1
  fi
fi

export PGCONNECT_TIMEOUT="${PGCONNECT_TIMEOUT:-8}"
PSQL=(psql -X --no-password -v ON_ERROR_STOP=1 "${DATABASE_URL}")

# 0017/0018 曾修改世文持有的 public.task_assignments，生产链明确永久排除。
PRODUCTION_MIGRATIONS=(
  0001_initial
  0002_shared_database_exchange
  0003_file_upload_intents
  0004_task_command_receipts
  0005_faq_message_commands
  0006_teacher_profile_g01_support
  0007_shared_task_assignment_links
  0008_remove_legacy_task_exchange
  0009_task_view_command
  0010_current_task_execution
  0011_system_notification_delivery
  0012_system_notification_publication_guards
  0013_system_notification_owner_maintenance
  0014_teacher_photo_processing
  0015_teacher_photo_filter_strength
  0016_database_quiz_banks
  0019_growth_stage_notification_state
  0020_product_analytics
  0021_teacher_support_tickets
  0022_performance_job_leases
  0023_teacher_support_operator_atomicity
  0024_support_ticket_cas_and_function_owner
  0025_fixed_task_semantic_alignment
  0026_kuozhi_course_syncs
)

target_found=false
TARGET_MIGRATIONS=()
for migration_id in "${PRODUCTION_MIGRATIONS[@]}"; do
  TARGET_MIGRATIONS+=("${migration_id}")
  if [[ "${migration_id}" == "${TARGET_MIGRATION}" ]]; then
    target_found=true
    break
  fi
done
if [[ "${target_found}" != "true" ]]; then
  echo "未知生产迁移目标：${TARGET_MIGRATION}" >&2
  exit 1
fi
if [[ "${TARGET_MIGRATION}" != "0026_kuozhi_course_syncs" \
      && "${MIGRATION_TEST_MODE}" != "true" ]]; then
  echo "生产运行不允许停在旧版本；TIDE_MIGRATION_TARGET 仅供隔离迁移测试。" >&2
  exit 1
fi

connection_preflight="$("${PSQL[@]}" -AtF '|' <<'SQL'
SELECT
    current_user,
    session_user,
    current_database(),
    current_setting('server_version_num')::integer >= 160000,
    COALESCE(
        (
            SELECT ssl
            FROM pg_stat_ssl
            WHERE pid = pg_backend_pid()
        ),
        false
    ),
    (
        SELECT rolsuper
        FROM pg_roles
        WHERE rolname = current_user
    ),
    (
        SELECT
            rolcanlogin
            AND NOT rolcreatedb
            AND NOT rolcreaterole
            AND NOT rolreplication
            AND NOT rolbypassrls
        FROM pg_roles
        WHERE rolname = current_user
    ),
    EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tit_growth_app'),
    EXISTS (
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
    ),
    COALESCE(
        (
            SELECT pg_has_role(
                (
                    SELECT oid
                    FROM pg_roles
                    WHERE rolname = current_user
                ),
                owner_role.oid,
                'MEMBER'
            )
            FROM pg_roles owner_role
            WHERE owner_role.rolname = 'tide_support_ticket_owner'
        ),
        false
    );
SQL
)"

IFS='|' read -r \
  connected_user \
  session_user_name \
  connected_database \
  version_ready \
  ssl_active \
  migration_user_superuser \
  migration_user_restricted \
  operator_role_ready \
  function_owner_role_ready \
  migration_owner_membership_ready <<<"${connection_preflight}"

if [[ "${version_ready}" != "t" ]]; then
  echo "生产迁移要求 PostgreSQL 16 或更高版本。" >&2
  exit 1
fi
if [[ "${connected_database}" != "${EXPECTED_DATABASE}" ]]; then
  echo "实际目标库 ${connected_database} 与 TIDE_MIGRATION_EXPECTED_DATABASE 不一致。" >&2
  exit 1
fi
if [[ "${function_owner_role_ready}" != "t" ]]; then
  echo "tide_support_ticket_owner 缺失或不是无继承的非登录最小权限角色。" >&2
  exit 1
fi
if [[ "${MIGRATION_TEST_MODE}" != "true" ]]; then
  if [[ "${connected_user}" != "tide_migrator" ]]; then
    echo "生产迁移 current_user 必须精确为 tide_migrator。" >&2
    exit 1
  fi
  if [[ "${migration_user_superuser}" != "f" ]]; then
    echo "生产迁移禁止使用 superuser。" >&2
    exit 1
  fi
  if [[ "${migration_user_restricted}" != "t" ]]; then
    echo "tide_migrator 必须是无建库、建角色、复制或绕过 RLS 权限的 LOGIN 角色。" >&2
    exit 1
  fi
  if [[ "${ssl_active}" != "t" ]]; then
    echo "数据库会话未实际使用 TLS，生产迁移已停止。" >&2
    exit 1
  fi
  if [[ "${session_user_name}" != "${connected_user}" ]]; then
    echo "生产迁移禁止由高权限 session_user 通过 SET ROLE 伪装。" >&2
    exit 1
  fi
fi
if [[ "${migration_owner_membership_ready}" != "t" ]]; then
  echo "迁移账号必须由 DBA 授予 tide_support_ticket_owner 成员关系。" >&2
  exit 1
fi
if [[ "${operator_role_ready}" != "t" ]]; then
  echo "缺少 DBA 预置角色 tit_growth_app；迁移器不会创建角色或管理密码。" >&2
  exit 1
fi

schema_preflight="$("${PSQL[@]}" -AtF '|' <<'SQL'
SELECT
    to_regclass('public.task_templates') IS NOT NULL
        AND to_regclass('public.task_assignments') IS NOT NULL
        AND to_regclass('public.teachers') IS NOT NULL
        AND to_regclass('public.teacher_metric_snapshots') IS NOT NULL
        AND to_regclass('public.notifications') IS NOT NULL
        AND to_regclass('public.notification_events') IS NOT NULL,
    to_regclass('tide.schema_migrations') IS NOT NULL,
    (
        (
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
    ),
    (
        EXISTS (
            SELECT 1
            FROM pg_class relation
            JOIN pg_namespace namespace
              ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname = 'tide'
              AND relation.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')
              AND relation.relname <> 'schema_migrations'
        )
        OR to_regclass('public.teacher_support_tickets') IS NOT NULL
    ),
    (
        SELECT count(*)
        FROM pg_attribute
        WHERE attrelid = to_regclass('public.task_assignments')
          AND attnum > 0
          AND NOT attisdropped
          AND attname IN (
              'teacher_response_type',
              'teacher_response_text',
              'teacher_response_submitted_at',
              'teacher_response_submitted_by'
          )
    );
SQL
)"
IFS='|' read -r \
  shared_ready \
  ledger_exists \
  authoritative_fixed_catalog_ready \
  managed_objects_exist \
  forbidden_column_count <<<"${schema_preflight}"

if [[ "${shared_ready}" != "t" ]]; then
  echo "世文持有的共享 public 表契约不完整，生产迁移已停止。" >&2
  exit 1
fi
if [[ "${authoritative_fixed_catalog_ready}" != "t" ]]; then
  echo "运营端权威固定任务目录不是 rev38 稳定映射（G01–G09 + retired G00），生产迁移已停止。" >&2
  exit 1
fi
if [[ "${ledger_exists}" != "t" && "${managed_objects_exist}" == "t" ]]; then
  echo "检测到无迁移账本的既有 TIDE 结构；为避免伪造历史，生产迁移拒绝自动认领。" >&2
  exit 1
fi
if [[ "${forbidden_column_count}" != "0" ]]; then
  echo "public.task_assignments 含教师端越权字段，生产迁移已停止。" >&2
  exit 1
fi

expected_ids="$(IFS=,; echo "${TARGET_MIGRATIONS[*]}")"
if [[ "${ledger_exists}" == "t" ]]; then
  actual_ids="$("${PSQL[@]}" -Atqc "
    SELECT string_agg(migration_id, ',' ORDER BY migration_order)
    FROM tide.schema_migrations
  ")"
  if [[ "${managed_objects_exist}" == "t" && -z "${actual_ids}" ]]; then
    echo "TIDE 结构存在但迁移账本为空，生产迁移已停止。" >&2
    exit 1
  fi
  if [[ -n "${actual_ids}" \
        && "${expected_ids}" != "${actual_ids}" \
        && "${expected_ids}" != "${actual_ids},"* ]]; then
    echo "迁移账本不是当前生产清单的连续前缀，生产迁移已停止。" >&2
    exit 1
  fi
fi

sha256_file() {
  local file_path="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "${file_path}" | awk '{print $1}'
  else
    shasum -a 256 "${file_path}" | awk '{print $1}'
  fi
}

migration_order=0
for migration_id in "${TARGET_MIGRATIONS[@]}"; do
  migration_order=$((migration_order + 1))
  migration_file="${DB_DIR}/migrations/${migration_id}.up.sql"
  if [[ ! -f "${migration_file}" ]]; then
    echo "缺少生产迁移文件：${migration_file}" >&2
    exit 1
  fi
  if [[ "$(sed -n '1p' "${migration_file}")" != "BEGIN;" \
        || "$(tail -n 1 "${migration_file}")" != "COMMIT;" ]]; then
    echo "生产迁移必须由单一 BEGIN/COMMIT 包裹：${migration_file}" >&2
    exit 1
  fi

  migration_sha256="$(sha256_file "${migration_file}")"
  {
    cat <<'SQL'
BEGIN;
SET LOCAL client_min_messages = warning;
SELECT pg_advisory_xact_lock(
    hashtextextended('tide:production-schema-migrations', 0)
);
CREATE SCHEMA IF NOT EXISTS tide;
CREATE TABLE IF NOT EXISTS tide.schema_migrations (
    migration_id text PRIMARY KEY,
    migration_order integer NOT NULL UNIQUE,
    filename text NOT NULL,
    sha256 char(64) NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now(),
    applied_by text NOT NULL DEFAULT current_user,
    CONSTRAINT schema_migrations_id_check
        CHECK (migration_id ~ '^[0-9]{4}_[a-z0-9_]+$'),
    CONSTRAINT schema_migrations_sha256_check
        CHECK (sha256 ~ '^[0-9a-f]{64}$')
);
REVOKE ALL ON tide.schema_migrations FROM PUBLIC;

SELECT
    EXISTS (
        SELECT 1
        FROM tide.schema_migrations
        WHERE migration_id = :'migration_id'
    ) AS already_applied,
    EXISTS (
        SELECT 1
        FROM tide.schema_migrations
        WHERE migration_id = :'migration_id'
          AND (
              migration_order <> :'migration_order'::integer
              OR filename <> :'migration_filename'
              OR sha256 <> :'migration_sha256'
          )
    ) AS checksum_mismatch
\gset

\if :checksum_mismatch
DO $migration_checksum_guard$
BEGIN
    RAISE EXCEPTION
        'applied migration order, filename or checksum changed';
END
$migration_checksum_guard$;
\endif

\if :already_applied
    \echo '迁移已验证，跳过：' :migration_id
\else
SQL
    sed -e '1{/^BEGIN;$/d;}' -e '${/^COMMIT;$/d;}' "${migration_file}"
    cat <<'SQL'
INSERT INTO tide.schema_migrations (
    migration_id,
    migration_order,
    filename,
    sha256
)
VALUES (
    :'migration_id',
    :'migration_order'::integer,
    :'migration_filename',
    :'migration_sha256'
);
\endif

COMMIT;
SQL
  } | "${PSQL[@]}" \
        -v migration_id="${migration_id}" \
        -v migration_order="${migration_order}" \
        -v migration_filename="${migration_id}.up.sql" \
        -v migration_sha256="${migration_sha256}" \
        --quiet

done

actual_ids="$("${PSQL[@]}" -Atqc "
  SELECT string_agg(migration_id, ',' ORDER BY migration_order)
  FROM tide.schema_migrations
")"
if [[ "${actual_ids}" != "${expected_ids}" ]]; then
  echo "迁移账本不是当前目标的完整连续链，生产迁移已停止。" >&2
  exit 1
fi

echo "生产数据库迁移完成：${TARGET_MIGRATION}。未执行角色密码、Mock 或内容 Seed。"
