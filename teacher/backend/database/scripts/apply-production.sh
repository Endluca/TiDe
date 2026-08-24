#!/usr/bin/env bash
set -euo pipefail
set +x

DB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -n "${TIDE_MIGRATION_DATABASE_URL:-}" ]]; then
  DATABASE_URL="${TIDE_MIGRATION_DATABASE_URL}"
else
  DATABASE_URL="${DATABASE_URL:-}"
fi
EXPECTED_DATABASE="${TIDE_MIGRATION_EXPECTED_DATABASE:-}"
TARGET_MIGRATION="${TIDE_MIGRATION_TARGET:-0043_p_rel_execution_catalog}"
MIGRATION_TEST_MODE="${TIDE_MIGRATION_TEST_MODE:-false}"
COMPANY_TEST_MIGRATION_MODE="${TIDE_COMPANY_TEST_MIGRATION_MODE:-false}"

PRE_PRIVATE_LINE_DB_HOST="tide-system.rwlb.singapore.rds.aliyuncs.com"
PRE_PRIVATE_LINE_DB_PORT="5432"
PRE_PRIVATE_LINE_DB_NAME="tide_system_test"
PRE_PRIVATE_LINE_DB_OWNER="tide_sys_admin"
PRE_PRIVATE_LINE_PLAINTEXT=false

APPROVED_COMPANY_TEST_DB_HOST="ai-efficiency-postgresql-20260722194941.pods.test.51talk.biz"
APPROVED_COMPANY_TEST_DB_PORT="5432"
APPROVED_COMPANY_TEST_DB_NAME="tit_growth_test_v2"
APPROVED_COMPANY_TEST_DB_OWNER="postgres"
APPROVED_COMPANY_TEST_SSLMODE="disable"
APPROVED_COMPANY_TEST_TARGETS=(
  0037_g04_remove_device_check
  0038_personalized_environment_photo
  0040_g02_document_read_status
  0041_crm_sso_hybrid
  0042_g09_set_kuozhi_course
  0043_p_rel_execution_catalog
)

if [[ "${MIGRATION_TEST_MODE}" != "true" && "${MIGRATION_TEST_MODE}" != "false" ]]; then
  echo "TIDE_MIGRATION_TEST_MODE 只接受 true 或 false。" >&2
  exit 1
fi
if [[ "${COMPANY_TEST_MIGRATION_MODE}" != "true" \
      && "${COMPANY_TEST_MIGRATION_MODE}" != "false" ]]; then
  echo "TIDE_COMPANY_TEST_MIGRATION_MODE 只接受 true 或 false。" >&2
  exit 1
fi
if [[ "${COMPANY_TEST_MIGRATION_MODE}" == "true" \
      && "${MIGRATION_TEST_MODE}" != "false" ]]; then
  echo "公司 TEST 增量迁移禁止使用通用 TIDE_MIGRATION_TEST_MODE 绕过。" >&2
  exit 1
fi

if [[ "${COMPANY_TEST_MIGRATION_MODE}" != "true" && -z "${DATABASE_URL}" ]]; then
  echo "TIDE_MIGRATION_DATABASE_URL 或同一迁移任务中的 DATABASE_URL 不能为空；必须使用独立迁移账号。" >&2
  exit 1
fi
if [[ -z "${EXPECTED_DATABASE}" ]]; then
  echo "TIDE_MIGRATION_EXPECTED_DATABASE 不能为空；禁止从连接串猜测目标库。" >&2
  exit 1
fi

if [[ "${COMPANY_TEST_MIGRATION_MODE}" == "true" ]]; then
  COMPANY_TEST_CONFIG_FILE="${TIDE_COMPANY_TEST_CONFIG_FILE:-}"
  COMPANY_TEST_DB_HOST="${TIDE_COMPANY_TEST_DB_HOST:-}"
  COMPANY_TEST_DB_PORT="${TIDE_COMPANY_TEST_DB_PORT:-}"
  COMPANY_TEST_DB_USER="${TIDE_COMPANY_TEST_DB_USER:-}"
  COMPANY_TEST_DB_NAME="${TIDE_COMPANY_TEST_DB_NAME:-}"
  COMPANY_TEST_DB_SSLMODE="${TIDE_COMPANY_TEST_DB_SSLMODE:-}"

  if [[ -n "${DATABASE_URL}" ]]; then
    echo "公司 TEST 增量迁移禁止传入连接 URI；只接受受控配置文件。" >&2
    exit 1
  fi
  if [[ "${COMPANY_TEST_CONFIG_FILE}" != /* \
        || ! -f "${COMPANY_TEST_CONFIG_FILE}" \
        || -L "${COMPANY_TEST_CONFIG_FILE}" ]]; then
    echo "公司 TEST 增量迁移只接受绝对路径的普通配置文件（禁止符号链接）。" >&2
    exit 1
  fi
  if stat -f '%Lp' "${COMPANY_TEST_CONFIG_FILE}" >/dev/null 2>&1; then
    company_test_config_mode="$(stat -f '%Lp' "${COMPANY_TEST_CONFIG_FILE}")"
  else
    company_test_config_mode="$(stat -c '%a' "${COMPANY_TEST_CONFIG_FILE}")"
  fi
  if [[ "${company_test_config_mode}" != "600" ]]; then
    echo "公司 TEST 增量迁移配置权限必须精确为 600。" >&2
    exit 1
  fi
  company_test_config_dir="$(cd "$(dirname "${COMPANY_TEST_CONFIG_FILE}")" && pwd -P)"
  company_test_config_path="${company_test_config_dir}/$(basename "${COMPANY_TEST_CONFIG_FILE}")"
  source_root_candidate="$(cd "${DB_DIR}/../../.." && pwd -P)"
  if [[ -e "${source_root_candidate}/.git" ]]; then
    protected_source_root="${source_root_candidate}"
  else
    protected_source_root="$(cd "${DB_DIR}/.." && pwd -P)"
  fi
  case "${company_test_config_path}" in
    "${protected_source_root}"|"${protected_source_root}"/*)
      echo "公司 TEST 增量迁移配置必须放在 Git/镜像工作区之外。" >&2
      exit 1
      ;;
  esac
  approved_company_test_target=false
  for approved_target in "${APPROVED_COMPANY_TEST_TARGETS[@]}"; do
    if [[ "${TARGET_MIGRATION}" == "${approved_target}" ]]; then
      approved_company_test_target=true
      break
    fi
  done
  if [[ "${COMPANY_TEST_DB_HOST}" != "${APPROVED_COMPANY_TEST_DB_HOST}" \
        || "${COMPANY_TEST_DB_PORT}" != "${APPROVED_COMPANY_TEST_DB_PORT}" \
        || "${COMPANY_TEST_DB_NAME}" != "${APPROVED_COMPANY_TEST_DB_NAME}" \
        || "${COMPANY_TEST_DB_USER}" != "${APPROVED_COMPANY_TEST_DB_OWNER}" \
        || "${COMPANY_TEST_DB_SSLMODE}" != "${APPROVED_COMPANY_TEST_SSLMODE}" \
        || "${EXPECTED_DATABASE}" != "${APPROVED_COMPANY_TEST_DB_NAME}" \
        || "${approved_company_test_target}" != "true" ]]; then
    echo "公司 TEST 增量迁移只允许已批准的 tit_growth_test_v2 / postgres，以及 0037、0038、0040、0041、0042、0043 切换点。" >&2
    exit 1
  fi
  if [[ -z "${PGPASSWORD:-}" ]]; then
    echo "公司 TEST 增量迁移缺少受控配置提供的数据库密码。" >&2
    exit 1
  fi
elif [[ "${MIGRATION_TEST_MODE}" != "true" ]]; then
  if [[ ! "${DATABASE_URL}" =~ ^postgres(ql)?:// ]]; then
    echo "生产迁移只接受显式 PostgreSQL URI。" >&2
    exit 1
  fi

  connection_without_scheme="${DATABASE_URL#*://}"
  if [[ "${connection_without_scheme}" != *@*/*\?* \
        || "${connection_without_scheme}" == *#* ]]; then
    echo "生产迁移 URI 必须显式声明账号、主机、端口、数据库和连接参数。" >&2
    exit 1
  fi
  connection_authority="${connection_without_scheme%%/*}"
  connection_userinfo="${connection_authority%@*}"
  connection_server="${connection_authority##*@}"
  connection_user="${connection_userinfo%%:*}"
  connection_path_and_query="${connection_without_scheme#*/}"
  connection_database="${connection_path_and_query%%\?*}"
  connection_query="${DATABASE_URL#*\?}"
  if [[ "${connection_query}" == "${DATABASE_URL}" ]]; then
    echo "生产迁移连接必须声明受支持的 sslmode。" >&2
    exit 1
  fi
  sslmode_count=0
  sslmode_value=""
  connection_identity_override=false
  IFS='&' read -r -a connection_parameters <<<"${connection_query}"
  for connection_parameter in "${connection_parameters[@]}"; do
    connection_parameter_name="${connection_parameter%%=*}"
    if [[ ! "${connection_parameter_name}" =~ ^[a-z_][a-z0-9_]*$ ]]; then
      echo "生产迁移连接参数名必须使用未编码的小写 PostgreSQL 关键字。" >&2
      exit 1
    fi
    if [[ "${connection_parameter_name}" == "sslmode" ]]; then
      sslmode_count=$((sslmode_count + 1))
      sslmode_value="${connection_parameter#*=}"
    fi
    case "${connection_parameter_name}" in
      database|dbname|host|hostaddr|options|port|service|servicefile|ssl|user)
        connection_identity_override=true
        ;;
    esac
  done
  if [[ "${sslmode_count}" != "1" ]]; then
    echo "生产迁移连接必须且只能声明一次 sslmode。" >&2
    exit 1
  fi

  if [[ "${sslmode_value}" == "disable" \
        && "${connection_user}" == "${PRE_PRIVATE_LINE_DB_OWNER}" \
        && "${connection_server}" == "${PRE_PRIVATE_LINE_DB_HOST}:${PRE_PRIVATE_LINE_DB_PORT}" \
        && "${connection_database}" == "${PRE_PRIVATE_LINE_DB_NAME}" \
        && "${EXPECTED_DATABASE}" == "${PRE_PRIVATE_LINE_DB_NAME}" \
        && "${connection_identity_override}" == "false" ]]; then
    PRE_PRIVATE_LINE_PLAINTEXT=true
  elif [[ "${sslmode_value}" != "verify-full" ]]; then
    echo "生产迁移一般要求 sslmode=verify-full；sslmode=disable 仅允许固定 tide_system_test PRE 专线身份。" >&2
    exit 1
  fi
fi

if [[ "${PRE_PRIVATE_LINE_PLAINTEXT}" == "true" ]]; then
  for libpq_identity_name in \
    PGDATABASE PGHOST PGHOSTADDR PGPORT PGSERVICE PGSERVICEFILE PGUSER; do
    if [[ ${!libpq_identity_name+x} == x ]]; then
      echo "固定 PRE 专线迁移禁止 libpq 环境变量改写连接身份：${libpq_identity_name}。" >&2
      exit 1
    fi
  done
fi

export PGCONNECT_TIMEOUT="${PGCONNECT_TIMEOUT:-8}"
if [[ "${COMPANY_TEST_MIGRATION_MODE}" == "true" ]]; then
  export PGSSLMODE="${APPROVED_COMPANY_TEST_SSLMODE}"
  PSQL=(
    psql -X --no-password -v ON_ERROR_STOP=1
    -h "${APPROVED_COMPANY_TEST_DB_HOST}"
    -p "${APPROVED_COMPANY_TEST_DB_PORT}"
    -U "${APPROVED_COMPANY_TEST_DB_OWNER}"
    -d "${APPROVED_COMPANY_TEST_DB_NAME}"
  )
else
  PSQL=(psql -X --no-password -v ON_ERROR_STOP=1 "${DATABASE_URL}")
fi

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
  0027_remove_local_quiz_runtime
  0028_retire_task_business_change_view
  0029_remove_unused_tide_objects
  0030_remove_unused_columns_and_orphan_function
  0031_g04_independent_sections
  0032_first_login_onboarding
  0033_g01_tesol_only
  0037_g04_remove_device_check
  0038_personalized_environment_photo
  0039_g02_policy_document
  0040_g02_document_read_status
  0041_crm_sso_hybrid
  0042_g09_set_kuozhi_course
  0043_p_rel_execution_catalog
)

target_found=false
target_includes_product_analytics=false
target_includes_personalized_environment_photo=false
target_includes_g02_policy_document=false
TARGET_MIGRATIONS=()
for migration_id in "${PRODUCTION_MIGRATIONS[@]}"; do
  TARGET_MIGRATIONS+=("${migration_id}")
  if [[ "${migration_id}" == "0020_product_analytics" ]]; then
    target_includes_product_analytics=true
  fi
  if [[ "${migration_id}" == "0038_personalized_environment_photo" ]]; then
    target_includes_personalized_environment_photo=true
  fi
  if [[ "${migration_id}" == "0039_g02_policy_document" ]]; then
    target_includes_g02_policy_document=true
  fi
  if [[ "${migration_id}" == "${TARGET_MIGRATION}" ]]; then
    target_found=true
    break
  fi
done
if [[ "${target_found}" != "true" ]]; then
  echo "未知生产迁移目标：${TARGET_MIGRATION}" >&2
  exit 1
fi
if [[ "${TARGET_MIGRATION}" != "0028_retire_task_business_change_view" \
      && "${TARGET_MIGRATION}" != "0032_first_login_onboarding" \
      && "${TARGET_MIGRATION}" != "0037_g04_remove_device_check" \
      && "${TARGET_MIGRATION}" != "0038_personalized_environment_photo" \
      && "${TARGET_MIGRATION}" != "0040_g02_document_read_status" \
      && "${TARGET_MIGRATION}" != "0041_crm_sso_hybrid" \
      && "${TARGET_MIGRATION}" != "0042_g09_set_kuozhi_course" \
      && "${TARGET_MIGRATION}" != "0043_p_rel_execution_catalog" \
      && "${MIGRATION_TEST_MODE}" != "true" ]]; then
  echo "生产只允许停在跨 Schema 切换点 0028、0032、0037、0038、0040、0041、0042 或最终版本 0043。其他 TIDE_MIGRATION_TARGET 仅供隔离迁移测试。" >&2
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
    current_setting('ssl') = 'on',
    (
        SELECT rolsuper
        FROM pg_roles
        WHERE rolname = current_user
    ),
    (
        SELECT
            rolcanlogin
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
  server_ssl_active \
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
if [[ "${COMPANY_TEST_MIGRATION_MODE}" == "true" ]]; then
  if [[ "${connected_user}" != "${APPROVED_COMPANY_TEST_DB_OWNER}" \
        || "${session_user_name}" != "${APPROVED_COMPANY_TEST_DB_OWNER}" ]]; then
    echo "公司 TEST 增量迁移实际会话必须精确为批准的 postgres owner，禁止 SET ROLE 伪装。" >&2
    exit 1
  fi
  if [[ "${migration_user_superuser}" != "t" ]]; then
    echo "公司 TEST 增量迁移批准 owner 必须保持为 postgres superuser。" >&2
    exit 1
  fi
  if [[ "${ssl_active}" != "f" ]]; then
    echo "sslmode=disable 仅批准用于指定 company TEST，实际会话状态不一致。" >&2
    exit 1
  fi
elif [[ "${MIGRATION_TEST_MODE}" != "true" ]]; then
  if [[ "${connected_user}" != "tide_sys_admin" ]]; then
    echo "生产迁移 current_user 必须精确为 tide_sys_admin。" >&2
    exit 1
  fi
  if [[ "${migration_user_superuser}" != "f" ]]; then
    echo "生产迁移禁止使用 superuser。" >&2
    exit 1
  fi
  if [[ "${migration_user_restricted}" != "t" ]]; then
    echo "tide_sys_admin 必须是禁止复制和绕过 RLS 的 LOGIN 管理角色。" >&2
    exit 1
  fi
  if [[ "${session_user_name}" != "${connected_user}" ]]; then
    echo "生产迁移禁止由高权限 session_user 通过 SET ROLE 伪装。" >&2
    exit 1
  fi
  if [[ "${PRE_PRIVATE_LINE_PLAINTEXT}" == "true" ]]; then
    if [[ "${ssl_active}" != "f" || "${server_ssl_active}" != "f" ]]; then
      echo "固定 PRE 专线明文迁移要求当前会话非 TLS 且 PostgreSQL server ssl=off。" >&2
      exit 1
    fi
  elif [[ "${ssl_active}" != "t" || "${server_ssl_active}" != "t" ]]; then
    echo "数据库会话未实际使用 TLS，生产迁移已停止。" >&2
    exit 1
  fi
fi
if [[ "${migration_owner_membership_ready}" != "t" ]]; then
  echo "tide_sys_admin 必须由 DBA 授予 tide_support_ticket_owner 成员关系。" >&2
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
        AND to_regclass('public.teacher_source_wide') IS NOT NULL
        AND EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'teacher_source_wide'
              AND column_name = 'is_cpl_tesol'
        )
        AND EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'teacher_source_wide'
              AND column_name = 'is_self_introduce'
        )
        AND to_regclass('public.notifications') IS NOT NULL
        AND to_regclass('public.notification_events') IS NOT NULL,
    to_regclass('public.teacher_metric_snapshots') IS NOT NULL,
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
  legacy_teacher_snapshot_exists \
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

personalized_environment_photo_recorded=false
g02_document_read_status_recorded=false
if [[ "${ledger_exists}" == "t" ]]; then
  personalized_environment_photo_recorded="$("${PSQL[@]}" -Atqc "
    SELECT EXISTS (
      SELECT 1
      FROM tide.schema_migrations
      WHERE migration_id = '0038_personalized_environment_photo'
    )
  ")"
  g02_document_read_status_recorded="$("${PSQL[@]}" -Atqc "
    SELECT EXISTS (
      SELECT 1
      FROM tide.schema_migrations
      WHERE migration_id = '0040_g02_document_read_status'
    )
  ")"
fi

# 0038 consumes the operations-owned P-FB-NEGATIVE copy introduced by public
# rev55. This gate deliberately runs before the migration loop, so neither the
# 0038 business SQL nor its Tide ledger row can be written against an older or
# drifted public contract.
if [[ "${target_includes_personalized_environment_photo}" == "true" \
      && "${personalized_environment_photo_recorded}" != "t" ]]; then
  if [[ "$("${PSQL[@]}" -Atqc "select to_regclass('public.alembic_version') is not null")" != "t" ]]; then
    echo "0038 要求 public Alembic 精确位于 20260811_56_p_fb_negative_copy；当前缺少 public 迁移账本，未执行任何 0038 写入或记账。" >&2
    exit 1
  fi

  public_head="$("${PSQL[@]}" -Atqc "
    select case when count(*) = 1 then min(version_num) else '' end
    from public.alembic_version
  ")"
  if [[ "${public_head}" != "20260811_56_p_fb_negative_copy" ]]; then
    echo "0038 要求 public Alembic 精确位于 20260811_56_p_fb_negative_copy；当前为 ${public_head:-未记账}，未执行任何 0038 写入或记账。" >&2
    exit 1
  fi

  personalized_public_contract_ready="$("${PSQL[@]}" -Atqc "
    select count(*) = 1
    from public.task_templates
    where row_id = 'P-FB-NEGATIVE:v1'
      and template_id = 'P-FB-NEGATIVE'
      and template_version = 1
      and status = 'PUBLISHED'
      and output_type = 'TEACHER_TASK'
      and execution_owner = 'TEACHER_APP'
      and integration_mode = 'OUTBOUND_MANAGED'
      and source_mode = 'REAL'
      and jsonb_typeof(payload) = 'object'
      and payload->>'template_id' = 'P-FB-NEGATIVE'
      and payload->>'category' = 'PERSONALIZED_IMPROVEMENT'
      and payload->>'title' = 'Feedback Improvement'
      and payload->>'score_type' = 'ZERO'
      and payload->'score_value' = '0'::jsonb
      and payload->>'how_summary' =
        'Complete the configured improvement activity for the feedback issue shown in the task reason. Depending on the assigned activity, you may need to submit a teaching-environment photo for review or complete another guided action.'
      and payload->>'completion_standard' =
        'The teacher app marks the task as completed after every requirement for the assigned improvement activity, including any required photo review, is satisfied.'
  ")"
  if [[ "${personalized_public_contract_ready}" != "t" ]]; then
    echo "0038 要求 public rev56 的 P-FB-NEGATIVE:v1 精确新文案与零分共享契约；检测到缺失或漂移，未执行任何 0038 写入或记账。" >&2
    exit 1
  fi
fi

if [[ "${target_includes_g02_policy_document}" == "true" \
      && "${g02_document_read_status_recorded}" != "t" ]]; then
  if [[ "$("${PSQL[@]}" -Atqc "select to_regclass('public.alembic_version') is not null")" != "t" ]]; then
    echo "0039/0040 要求 public Alembic 精确位于 20260811_57_g02_document；当前缺少 public 迁移账本，未执行任何 G02 teacher 写入或记账。" >&2
    exit 1
  fi
  public_head="$("${PSQL[@]}" -Atqc "
    select case when count(*) = 1 then min(version_num) else '' end
    from public.alembic_version
  ")"
  if [[ "${public_head}" != "20260811_57_g02_document" ]]; then
    echo "0039/0040 要求 public Alembic 精确位于 20260811_57_g02_document；当前为 ${public_head:-未记账}，未执行任何 G02 teacher 写入或记账。" >&2
    exit 1
  fi
  g02_public_contract_ready="$("${PSQL[@]}" -Atqc "
    select count(*) = 1
    from public.task_templates
    where row_id = 'G03:v1'
      and template_id = 'G02'
      and status = 'PUBLISHED'
      and execution_owner = 'TEACHER_APP'
      and payload->>'category' = 'MANDATORY_GROWTH'
      and payload->>'title' = 'Platform Policies'
      and payload->>'how_summary' =
        'Read the current Overseas NT Policies document in TIDE. Your reading progress is saved automatically.'
      and payload->>'completion_standard' =
        'G02 is completed automatically after you reach the end of the current published document.'
      and (payload->>'score_value')::integer = 2
  ")"
  if [[ "${g02_public_contract_ready}" != "t" ]]; then
    echo "0039/0040 要求 public rev57 的 G02 原生文档精确共享契约；检测到缺失或漂移，未执行任何 G02 teacher 写入或记账。" >&2
    exit 1
  fi
fi

product_analytics_recorded=false
if [[ "${ledger_exists}" == "t" ]]; then
  product_analytics_recorded="$("${PSQL[@]}" -Atqc "
    SELECT EXISTS (
      SELECT 1
      FROM tide.schema_migrations
      WHERE migration_id = '0020_product_analytics'
    )
  ")"
fi
if [[ "${target_includes_product_analytics}" == "true" \
      && "${product_analytics_recorded}" != "t" \
      && "${legacy_teacher_snapshot_exists}" != "t" ]]; then
  echo "历史 0020 尚未记录且 public.teacher_metric_snapshots 已不存在。必须按 public Alembic 46 -> teacher 0028 -> public head 50 -> teacher 0032 -> public head 54 -> teacher 0037 -> public head 55 -> public head 56 -> teacher 0038 分阶段迁移；禁止跳过中间契约回放历史 teacher 链。" >&2
  exit 1
fi

expected_ids="$(IFS=,; echo "${TARGET_MIGRATIONS[*]}")"
actual_ids=""
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

current_tide_head="${actual_ids##*,}"
if [[ "${TARGET_MIGRATION}" == "0032_first_login_onboarding" ]]; then
  if [[ "${current_tide_head}" != "0028_retire_task_business_change_view" \
        && "${current_tide_head}" != "0029_remove_unused_tide_objects" \
        && "${current_tide_head}" != "0030_remove_unused_columns_and_orphan_function" \
        && "${current_tide_head}" != "0031_g04_independent_sections" \
        && "${current_tide_head}" != "0032_first_login_onboarding" ]]; then
    echo "teacher 0032 只能从 teacher 0028–0031 的连续中间状态继续；必须按 public Alembic 46 -> teacher 0028 -> public head 50 -> teacher 0032 分阶段执行。本次未写入任何 Tide 迁移。" >&2
    exit 1
  fi
  public_g04_stage_ready="$("${PSQL[@]}" -Atqc "
    select
      to_regclass('public.teacher_metric_snapshots') is null
      and to_regclass('public.alembic_version') is not null
      and (
        select count(*) = 1
          and min(version_num) = '20260810_50_g04_sections'
        from public.alembic_version
      )
      and (
        select count(*)
        from public.task_templates template
        where template.row_id = 'G02:v1'
          and template.template_id = 'G04'
          and template.status = 'PUBLISHED'
          and template.execution_owner = 'TEACHER_APP'
          and template.payload->>'template_id' = 'G04'
          and template.payload->>'category' = 'MANDATORY_GROWTH'
          and template.payload->>'ops_name_zh' = '首课备课与设备网络检测'
          and template.payload->>'title' =
            'Lesson Preparation&Device Network Check'
          and template.payload->>'why_template' =
            'Complete lesson preparation and confirm that your teaching setup is ready before class.'
          and template.payload->>'how_summary' =
            'Complete three independent sections in any order: review the lesson-preparation guidance; run the camera, microphone and network check; and submit one teaching-environment photo for AI review. Each section keeps its own progress.'
          and template.payload->>'completion_standard' =
            'G04 is completed only after all three independent sections pass: the lesson-preparation guidance is confirmed; the camera, microphone and network check passes; and all four teaching-environment photo criteria—camera angle, lighting, background and dressing—pass AI review. The sections may be completed in any order.'
          and template.payload->>'benefit' =
            'Your lesson-preparation knowledge, device and network readiness, and teaching environment are independently verified for your first lesson.'
          and template.payload->>'content_status' = 'READY'
          and (template.payload->>'score_value')::integer = 3
      ) = 1
  ")"
  if [[ "${public_g04_stage_ready}" != "t" ]]; then
    echo "teacher 0032 要求 public head 50 的精确 G04 三段副本且旧 teacher_metric_snapshots 已退役。本次未写入任何 Tide 迁移。" >&2
    exit 1
  fi
elif [[ "${TARGET_MIGRATION}" == "0037_g04_remove_device_check" ]]; then
  if [[ "${current_tide_head}" != "0032_first_login_onboarding" \
        && "${current_tide_head}" != "0033_g01_tesol_only" \
        && "${current_tide_head}" != "0037_g04_remove_device_check" ]]; then
    echo "teacher 0037 只能从 teacher 0032、0033 的连续状态继续；必须按 public Alembic 46 -> teacher 0028 -> public head 50 -> teacher 0032 -> public head 54 -> teacher 0037 分阶段执行。本次未写入任何 Tide 迁移。" >&2
    exit 1
  fi
  public_final_stage_ready="$("${PSQL[@]}" -Atqc "
    select
      to_regclass('public.teacher_metric_snapshots') is null
      and to_regclass('public.alembic_version') is not null
      and (
        select count(*) = 1
          and min(version_num) = '20260811_54_g04_remove_device_check'
        from public.alembic_version
      )
      and (
        select count(*)
        from public.task_templates template
        where template.row_id = 'G01:v1'
          and template.template_id = 'G01'
          and template.status = 'PUBLISHED'
          and template.execution_owner = 'TEACHER_APP'
          and template.payload->>'category' = 'MANDATORY_GROWTH'
          and template.payload->>'title' =
            'Profile & Credentials Completion'
          and template.payload->>'why_template' =
            'Complete the required TESOL status and learning evidence.'
          and template.payload->>'how_summary' =
            'Confirm TESOL, pass all 61 questions, complete the Essay and submit the completion proof.'
          and template.payload->>'completion_standard' =
            'TESOL is complete, the 61-question check reaches 80%, the Essay is complete and the completion proof is submitted.'
          and (template.payload->>'score_value')::integer = 3
      ) = 1
      and (
        select count(*)
        from public.task_templates template
        where template.row_id = 'G02:v1'
          and template.template_id = 'G04'
          and template.status = 'PUBLISHED'
          and template.execution_owner = 'TEACHER_APP'
          and template.payload->>'template_id' = 'G04'
          and template.payload->>'category' = 'MANDATORY_GROWTH'
          and template.payload->>'ops_name_zh' = '首课准备'
          and template.payload->>'title' = 'Lesson Preparation'
          and template.payload->>'why_template' =
            'Complete the teaching-environment photo review and prepare the courseware before your first lesson.'
          and template.payload->>'how_summary' =
            'Complete two sections in any order: submit one teaching-environment photo for AI review and prepare the courseware for your first lesson. Each section keeps its own progress.'
          and template.payload->>'completion_standard' =
            'G04 is completed only after both sections pass: all four teaching-environment photo criteria—camera angle, lighting, background and dressing—pass AI review, and the courseware preparation is confirmed. The sections may be completed in any order.'
          and template.payload->>'benefit' =
            'Your teaching environment and courseware are ready for your first lesson.'
          and template.payload->>'content_status' = 'READY'
          and (template.payload->>'score_value')::integer = 3
      ) = 1
  ")"
  if [[ "${public_final_stage_ready}" != "t" ]]; then
    echo "teacher 0037 要求 public head 54 中同时存在 rev51 G01 TESOL-only 精确副本与 G04 两段精确副本。本次未写入任何 Tide 迁移。" >&2
    exit 1
  fi
elif [[ "${TARGET_MIGRATION}" == "0040_g02_document_read_status" \
        || "${TARGET_MIGRATION}" == "0041_crm_sso_hybrid" ]]; then
  if [[ "${current_tide_head}" != "0038_personalized_environment_photo" \
        && "${current_tide_head}" != "0039_g02_policy_document" \
        && "${current_tide_head}" != "0040_g02_document_read_status" \
        && "${current_tide_head}" != "0041_crm_sso_hybrid" ]]; then
    echo "teacher 0040/0041 只能从 teacher 0038、0039 或 0040 的连续状态继续；必须先完成 public head 56 -> teacher 0038，再执行 public head 57 -> teacher 0040 -> teacher 0041。本次未写入任何 Tide 迁移。" >&2
    exit 1
  fi
elif [[ "${TARGET_MIGRATION}" == "0042_g09_set_kuozhi_course" ]]; then
  g09_public_ready="$("${PSQL[@]}" -Atqc "
    select
      to_regclass('public.alembic_version') is not null
      and (
        select count(*) = 1
          and min(version_num) = '20260819_65_g09_set_course'
        from public.alembic_version
      )
      and (
        select count(*)
        from public.task_templates
        where row_id = 'G10:v1'
          and template_id = 'G09'
          and status = 'PUBLISHED'
          and payload->>'how_summary' =
            'Complete the three SET videos and their three paired quizzes in Kuozhi.'
          and payload->>'completion_standard' =
            'All three required videos and all three paired quizzes reach 100% progress in Kuozhi.'
          and payload->>'content_status' = 'READY'
          and (payload->>'score_value')::integer = 5
      ) = 1
  ")"
  if [[ "${current_tide_head}" != "0041_crm_sso_hybrid" \
        && "${current_tide_head}" != "0042_g09_set_kuozhi_course" ]]; then
    echo "teacher 0042 只能从 teacher 0041 连续执行；当前 Tide head 为 ${current_tide_head:-空}。本次未写入任何迁移。" >&2
    exit 1
  fi
  if [[ "${g09_public_ready}" != "t" ]]; then
    echo "teacher 0042 要求 public head 65 已发布稳定 G10:v1 / G09 的课程 658 文案。本次未写入任何 Tide 迁移。" >&2
    exit 1
  fi
elif [[ "${TARGET_MIGRATION}" == "0043_p_rel_execution_catalog" ]]; then
  reliability_public_ready="$("${PSQL[@]}" -Atqc "
    select
      to_regclass('public.alembic_version') is not null
      and (
        select count(*) = 1
          and min(version_num) in (
            '20260822_99_blacklist_three_state',
            '20260823_100_scope_snapshot_diff',
            '20260824_101_dts_single_pipeline_reset'
          )
        from public.alembic_version
      )
      and (
        select count(*)
        from public.task_templates
        where row_id in ('P-REL-MEMO:v1', 'P-REL-ATTENDANCE:v1')
          and template_version = 1
          and status = 'PUBLISHED'
          and output_type = 'TEACHER_TASK'
          and execution_owner = 'TEACHER_APP'
          and integration_mode = 'OUTBOUND_MANAGED'
          and source_mode = 'REAL'
          and payload->>'category' = 'PERSONALIZED_IMPROVEMENT'
          and payload->>'content_status' = 'READY'
          and payload->>'score_type' = 'ZERO'
          and payload->'score_value' = '0'::jsonb
      ) = 2
      and (
        select count(*)
        from public.task_templates
        where row_id = 'P-REL-MEMO:v1'
          and template_id = 'P-REL-MEMO'
          and payload->>'title' = 'Lesson Memo Improvement'
          and payload->>'how_summary' =
            'Complete the Lesson Memo guidance and review how to submit an accurate memo after every lesson.'
      ) = 1
      and (
        select count(*)
        from public.task_templates
        where row_id = 'P-REL-ATTENDANCE:v1'
          and template_id = 'P-REL-ATTENDANCE'
          and payload->>'title' = 'Attendance Improvement'
          and payload->>'how_summary' =
            'Complete the assigned attendance training and pass its quiz.'
      ) = 1
  ")"
  if [[ "${current_tide_head}" != "0042_g09_set_kuozhi_course" \
        && "${current_tide_head}" != "0043_p_rel_execution_catalog" ]]; then
    echo "teacher 0043 只能从 teacher 0042 连续执行；当前 Tide head 为 ${current_tide_head:-空}。本次未写入任何迁移。" >&2
    exit 1
  fi
  if [[ "${reliability_public_ready}" != "t" ]]; then
    echo "teacher 0043 要求 public head 99 或 100 已发布两条稳定、零分、REAL 的可靠性任务模板。本次未写入任何 Tide 迁移。" >&2
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

if [[ "${COMPANY_TEST_MIGRATION_MODE}" == "true" ]]; then
  echo "公司 TEST 数据库增量迁移完成：${TARGET_MIGRATION}。未执行角色密码、Mock 或内容 Seed。"
else
  echo "生产数据库迁移完成：${TARGET_MIGRATION}。未执行角色密码、Mock 或内容 Seed。"
fi
