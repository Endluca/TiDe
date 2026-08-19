#!/usr/bin/env bash
set -euo pipefail

DB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ADMIN_DATABASE_URL="${TIDE_TEST_ADMIN_DATABASE_URL:-postgresql:///postgres}"
TEST_SUFFIX="${$}_${RANDOM}"
FRESH_DB="tide_prod_migration_fresh_${TEST_SUFFIX}"
UPGRADE_DB="tide_prod_migration_upgrade_${TEST_SUFFIX}"
PUBLIC_HEAD_FIRST_DB="tide_prod_public_head_first_${TEST_SUFFIX}"

for database_name in "${FRESH_DB}" "${UPGRADE_DB}" "${PUBLIC_HEAD_FIRST_DB}"; do
  if [[ ! "${database_name}" =~ ^[a-z0-9_]+$ ]]; then
    echo "非法测试数据库名：${database_name}" >&2
    exit 1
  fi
done

ADMIN_PSQL=(psql -X --no-password -v ON_ERROR_STOP=1 "${ADMIN_DATABASE_URL}")

cleanup() {
  "${ADMIN_PSQL[@]}" -c "
    ALTER ROLE tide_sys_admin
      LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE
      NOREPLICATION NOBYPASSRLS
  " >/dev/null 2>&1 || true
  for database_name in "${FRESH_DB}" "${UPGRADE_DB}" "${PUBLIC_HEAD_FIRST_DB}"; do
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
        ('G09:v1', 'G08', 'PUBLISHED', 'Global Communicator Training', 5),
        ('G10:v1', 'G09', 'PUBLISHED', 'SET Teaching Fundamentals', 5)
) AS catalog(row_id, task_code, status, title, score_value);

UPDATE public.task_templates
SET payload = payload || jsonb_build_object(
        'template_id', 'G04',
        'ops_name_zh', '首课备课与设备网络检测',
        'why_template',
            'Complete lesson preparation and confirm that your teaching setup is ready before class.',
        'how_summary',
            'Confirm lesson preparation, check the camera, microphone and network, then take one teaching-environment photo.',
        'completion_standard',
            'Lesson preparation is confirmed, camera, microphone and network pass, and the teaching-environment photo passes AI review.',
        'benefit',
            'Your lesson preparation and pre-class setup are recorded as ready.',
        'content_status', 'READY'
    )
WHERE row_id = 'G02:v1'
  AND template_id = 'G04';
SQL
}

advance_public_g04_to_rev50() {
  local database_name="$1"
  psql -X --no-password -v ON_ERROR_STOP=1 \
    "postgresql:///${database_name}" >/dev/null <<'SQL'
CREATE TABLE IF NOT EXISTS public.alembic_version (
    version_num varchar(64) NOT NULL
);
TRUNCATE public.alembic_version;
INSERT INTO public.alembic_version (version_num)
VALUES ('20260810_50_g04_sections');

UPDATE public.task_templates
SET revision = 50,
    payload = payload || jsonb_build_object(
        'template_id', 'G04',
        'ops_name_zh', '首课备课与设备网络检测',
        'title', 'Lesson Preparation&Device Network Check',
        'content_status', 'READY',
        'why_template',
            'Complete lesson preparation and confirm that your teaching setup is ready before class.',
        'how_summary',
            'Complete three independent sections in any order: review the lesson-preparation guidance; run the camera, microphone and network check; and submit one teaching-environment photo for AI review. Each section keeps its own progress.',
        'completion_standard',
            'G04 is completed only after all three independent sections pass: the lesson-preparation guidance is confirmed; the camera, microphone and network check passes; and all four teaching-environment photo criteria—camera angle, lighting, background and dressing—pass AI review. The sections may be completed in any order.',
        'benefit',
            'Your lesson-preparation knowledge, device and network readiness, and teaching environment are independently verified for your first lesson.'
    ),
    updated_by = 'production_migrator_public_rev50',
    updated_at = now()
WHERE row_id = 'G02:v1'
  AND template_id = 'G04'
  AND status = 'PUBLISHED';
SQL
}

advance_public_g04_to_rev54() {
  local database_name="$1"
  psql -X --no-password -v ON_ERROR_STOP=1 \
    "postgresql:///${database_name}" >/dev/null <<'SQL'
CREATE TABLE IF NOT EXISTS public.alembic_version (
    version_num varchar(64) NOT NULL
);
TRUNCATE public.alembic_version;
INSERT INTO public.alembic_version (version_num)
VALUES ('20260811_54_g04_remove_device_check');

UPDATE public.task_templates
SET payload = payload || jsonb_build_object(
        'why_template',
            'Complete the required TESOL status and learning evidence.',
        'how_summary',
            'Confirm TESOL, pass all 61 questions, complete the Essay and submit the completion proof.',
        'completion_standard',
            'TESOL is complete, the 61-question check reaches 80%, the Essay is complete and the completion proof is submitted.'
    ),
    updated_by = 'production_migrator_public_rev51',
    updated_at = now()
WHERE row_id = 'G01:v1'
  AND template_id = 'G01'
  AND status = 'PUBLISHED';

UPDATE public.task_templates
SET revision = 54,
    payload = payload || jsonb_build_object(
        'template_id', 'G04',
        'ops_name_zh', '首课准备',
        'title', 'Lesson Preparation',
        'content_status', 'READY',
        'why_template',
            'Complete the teaching-environment photo review and prepare the courseware before your first lesson.',
        'how_summary',
            'Complete two sections in any order: submit one teaching-environment photo for AI review and prepare the courseware for your first lesson. Each section keeps its own progress.',
        'completion_standard',
            'G04 is completed only after both sections pass: all four teaching-environment photo criteria—camera angle, lighting, background and dressing—pass AI review, and the courseware preparation is confirmed. The sections may be completed in any order.',
        'benefit',
            'Your teaching environment and courseware are ready for your first lesson.'
    ),
    updated_by = 'production_migrator_public_rev54',
    updated_at = now()
WHERE row_id = 'G02:v1'
  AND template_id = 'G04'
  AND status = 'PUBLISHED';
SQL
}

set_public_head() {
  local database_name="$1"
  local public_head="$2"
  psql -X --no-password -v ON_ERROR_STOP=1 \
    -v public_head="${public_head}" \
    "postgresql:///${database_name}" >/dev/null <<'SQL'
CREATE TABLE IF NOT EXISTS public.alembic_version (
    version_num varchar(64) PRIMARY KEY
);
TRUNCATE public.alembic_version;
INSERT INTO public.alembic_version (version_num) VALUES (:'public_head');
SQL
}

install_public_59_support_guard_and_acl() {
  local database_name="$1"
  # 教师 migrator 测试库不运行根仓库 Alembic；这里镜像 public head 59
  # 提供的只读视图与工单 Trigger/ACL 边界，以验证最终授权契约。
  psql -X --no-password -v ON_ERROR_STOP=1 \
    "postgresql:///${database_name}" >/dev/null <<'SQL'
CREATE OR REPLACE FUNCTION public.guard_simple_support_ticket_write()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $function$
DECLARE
    actor_name text := COALESCE(
        NULLIF(current_setting('role', true), 'none'),
        session_user
    );
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'support tickets cannot be deleted'
            USING ERRCODE = '42501';
    END IF;
    IF current_user = 'tide_support_ticket_owner' THEN
        RETURN NEW;
    END IF;
    IF TG_OP = 'INSERT'
       AND actor_name IN ('tit_teacher_crud', 'tit_growth_app') THEN
        RAISE EXCEPTION
            'runtime roles must create support tickets through the owner function'
            USING ERRCODE = '42501';
    END IF;
    IF TG_OP = 'INSERT' THEN
        RETURN NEW;
    END IF;
    IF actor_name = 'tit_growth_app' THEN
        RAISE EXCEPTION
            'TiDe runtime must update support tickets through the owner function'
            USING ERRCODE = '42501';
    END IF;
    IF actor_name = 'tit_teacher_crud'
       AND (
           NEW.ticket_id IS DISTINCT FROM OLD.ticket_id
           OR NEW.teacher_id IS DISTINCT FROM OLD.teacher_id
           OR NEW.primary_category IS DISTINCT FROM OLD.primary_category
           OR NEW.secondary_category IS DISTINCT FROM OLD.secondary_category
           OR NEW.problem_location IS DISTINCT FROM OLD.problem_location
           OR NEW.problem_context IS DISTINCT FROM OLD.problem_context
           OR NEW.messages IS DISTINCT FROM OLD.messages
           OR NEW.created_at IS DISTINCT FROM OLD.created_at
       ) THEN
        RAISE EXCEPTION
            'teacher runtime may not replace support-ticket identity or messages'
            USING ERRCODE = '42501';
    END IF;
    IF actor_name = 'tit_teacher_crud'
       AND OLD.status = 'CLOSED'
       AND NEW.status IS DISTINCT FROM OLD.status THEN
        RAISE EXCEPTION 'closed support tickets cannot be reopened'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END
$function$;

CREATE OR REPLACE VIEW public.teacher_scorecard_current AS
SELECT teacher_id FROM public.teachers WHERE false;
CREATE OR REPLACE VIEW public.teacher_lesson_score_current AS
SELECT teacher_id FROM public.teachers WHERE false;

REVOKE ALL ON FUNCTION public.guard_simple_support_ticket_write() FROM PUBLIC;
DROP TRIGGER IF EXISTS guard_teacher_support_ticket_update
ON public.teacher_support_tickets;
DROP TRIGGER IF EXISTS guard_simple_support_ticket_write
ON public.teacher_support_tickets;
CREATE TRIGGER guard_simple_support_ticket_write
BEFORE INSERT OR UPDATE OR DELETE ON public.teacher_support_tickets
FOR EACH ROW EXECUTE FUNCTION public.guard_simple_support_ticket_write();

GRANT SELECT, INSERT, UPDATE, DELETE
ON public.teacher_support_tickets
TO tit_growth_app, tit_teacher_crud, tide_support_ticket_owner;
SQL
  # 这套教师 migrator fixture 只镜像教师依赖的 public 结构；最终账本仍须
  # 前进到 public 63；真实 rev60 隐私 Trigger、rev61 文案、rev62 索引和 rev63 direct 隐私迁移
  # 由根仓库迁移测试覆盖。
  set_public_head "${database_name}" "20260819_63_dts_direct_privacy"
}

install_public_personalized_contract() {
  local database_name="$1"
  local integration_mode="$2"
  psql -X --no-password -v ON_ERROR_STOP=1 \
    -v integration_mode="${integration_mode}" \
    "postgresql:///${database_name}" >/dev/null <<'SQL'
INSERT INTO public.task_templates (
    row_id, template_id, template_version, status, revision, output_type,
    execution_owner, external_task_template_code, source_mode, payload,
    created_by, updated_by, integration_mode
) VALUES (
    'P-FB-NEGATIVE:v1',
    'P-FB-NEGATIVE',
    1,
    'PUBLISHED',
    55,
    'TEACHER_TASK',
    'TEACHER_APP',
    'TIT.P.FB.NEGATIVE',
    'REAL',
    '{"template_id":"P-FB-NEGATIVE","output_type":"TEACHER_TASK","audience":"TEACHER","owner":"TIT_GROWTH_OPS","execution_owner":"TEACHER_APP","integration_mode":"OUTBOUND_MANAGED","category":"PERSONALIZED_IMPROVEMENT","dimension":"USER_FEEDBACK","stage":"TRIGGERED","ops_name_zh":"差评改善","content_locale":"en","content_status":"READY","title":"Feedback Improvement","why_template":"The same negative-feedback signal has appeared more than once for this teacher.","how_summary":"Complete the configured improvement activity for the feedback issue shown in the task reason. Depending on the assigned activity, you may need to submit a teaching-environment photo for review or complete another guided action.","completion_standard":"The teacher app marks the task as completed after every requirement for the assigned improvement activity, including any required photo review, is satisfied.","benefit":"This task carries no points. It targets a repeated learner-feedback issue.","priority":"P1","score_type":"ZERO","score_value":0,"source_mode":"REAL"}'::jsonb,
    'production_migrator_test',
    'production_migrator_test',
    :'integration_mode'
);
SQL
}

install_public_g02_contract() {
  local database_name="$1"
  psql -X --no-password -v ON_ERROR_STOP=1 \
    "postgresql:///${database_name}" >/dev/null <<'SQL'
UPDATE public.task_templates
SET payload = jsonb_set(
      jsonb_set(
        payload,
        '{how_summary}',
        to_jsonb('Read the current Overseas NT Policies document in TIDE. Your reading progress is saved automatically.'::text)
      ),
      '{completion_standard}',
      to_jsonb('G02 is completed automatically after you reach the end of the current published document.'::text)
    ),
    revision = revision + 1,
    updated_by = 'production_migrator_test_g02'
WHERE row_id = 'G03:v1'
  AND template_id = 'G02'
  AND status = 'PUBLISHED';
SQL
  set_public_head "${database_name}" "20260811_57_g02_document"
}

install_public_g09_contract() {
  local database_name="$1"
  psql -X --no-password -v ON_ERROR_STOP=1 \
    "postgresql:///${database_name}" >/dev/null <<'SQL'
UPDATE public.task_templates
SET payload = payload || jsonb_build_object(
        'template_id', 'G09',
        'category', 'MANDATORY_GROWTH',
        'score_type', 'FIXED',
        'score_value', 5,
        'content_status', 'READY',
        'title', 'SET Teaching Fundamentals',
        'why_template', 'Learn the fundamentals of SET teaching.',
        'how_summary',
            'Complete the three SET videos and their three paired quizzes in Kuozhi.',
        'completion_standard',
            'All three required videos and all three paired quizzes reach 100% progress in Kuozhi.',
        'benefit', 'You understand the SET teaching foundation.'
    ),
    revision = revision + 1,
    updated_by = 'production_migrator_public_rev65',
    updated_at = now()
WHERE row_id = 'G10:v1'
  AND template_id = 'G09'
  AND template_version = 1
  AND status = 'PUBLISHED';
SQL
  set_public_head "${database_name}" "20260819_65_g09_set_course"
}

create_test_database "${PUBLIC_HEAD_FIRST_DB}"
advance_public_g04_to_rev54 "${PUBLIC_HEAD_FIRST_DB}"
set_public_head "${PUBLIC_HEAD_FIRST_DB}" "20260811_56_p_fb_negative_copy"
install_public_personalized_contract "${PUBLIC_HEAD_FIRST_DB}" "OUTBOUND_MANAGED"
psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${PUBLIC_HEAD_FIRST_DB}" \
  -c "DROP TABLE public.teacher_metric_snapshots" >/dev/null
set +e
public_head_first_output="$(
  TIDE_MIGRATION_DATABASE_URL="postgresql:///${PUBLIC_HEAD_FIRST_DB}" \
  TIDE_MIGRATION_EXPECTED_DATABASE="${PUBLIC_HEAD_FIRST_DB}" \
  TIDE_MIGRATION_TARGET="0038_personalized_environment_photo" \
  TIDE_MIGRATION_TEST_MODE="true" \
    bash "${DB_DIR}/scripts/apply-production.sh" 2>&1
)"
public_head_first_status=$?
set -e
public_head_first_state="$(psql -X --no-password -AtF '|' \
  "postgresql:///${PUBLIC_HEAD_FIRST_DB}" <<'SQL'
SELECT
    to_regclass('public.teacher_metric_snapshots') IS NULL,
    to_regnamespace('tide') IS NULL;
SQL
)"
if [[ "${public_head_first_status}" == "0" \
      || "${public_head_first_output}" != *"public Alembic 46 -> teacher 0028 -> public head 50 -> teacher 0032 -> public head 54 -> teacher 0037 -> public head 55 -> public head 56 -> teacher 0038"* \
      || "${public_head_first_state}" != "t|t" ]]; then
  echo "public head 55 跳过分阶段顺序时 teacher fresh 迁移未失败关闭：${public_head_first_state}" >&2
  echo "${public_head_first_output}" >&2
  exit 1
fi

create_test_database "${FRESH_DB}"
"${ADMIN_PSQL[@]}" -c \
  "ALTER ROLE tide_sys_admin NOINHERIT; ALTER ROLE tit_teacher_crud NOINHERIT" \
  >/dev/null
TIDE_MIGRATION_DATABASE_URL="postgresql:///${FRESH_DB}" \
TIDE_MIGRATION_EXPECTED_DATABASE="${FRESH_DB}" \
TIDE_MIGRATION_TARGET="0028_retire_task_business_change_view" \
TIDE_MIGRATION_TEST_MODE="true" \
  bash "${DB_DIR}/scripts/apply-production.sh" >/dev/null
advance_public_g04_to_rev50 "${FRESH_DB}"
psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${FRESH_DB}" \
  -c "DROP TABLE public.teacher_metric_snapshots" >/dev/null
TIDE_MIGRATION_DATABASE_URL="postgresql:///${FRESH_DB}" \
TIDE_MIGRATION_EXPECTED_DATABASE="${FRESH_DB}" \
TIDE_MIGRATION_TARGET="0032_first_login_onboarding" \
TIDE_MIGRATION_TEST_MODE="true" \
  bash "${DB_DIR}/scripts/apply-production.sh" >/dev/null
advance_public_g04_to_rev54 "${FRESH_DB}"
TIDE_MIGRATION_DATABASE_URL="postgresql:///${FRESH_DB}" \
TIDE_MIGRATION_EXPECTED_DATABASE="${FRESH_DB}" \
TIDE_MIGRATION_TARGET="0037_g04_remove_device_check" \
TIDE_MIGRATION_TEST_MODE="true" \
  bash "${DB_DIR}/scripts/apply-production.sh" >/dev/null

set +e
fresh_missing_template_output="$(
  TIDE_MIGRATION_DATABASE_URL="postgresql:///${FRESH_DB}" \
  TIDE_MIGRATION_EXPECTED_DATABASE="${FRESH_DB}" \
  TIDE_MIGRATION_TARGET="0038_personalized_environment_photo" \
  TIDE_MIGRATION_TEST_MODE="true" \
    bash "${DB_DIR}/scripts/apply-production.sh" 2>&1
)"
fresh_missing_template_status=$?
set -e
fresh_missing_template_state="$(psql -X --no-password -AtF '|' \
  "postgresql:///${FRESH_DB}" <<'SQL'
SELECT
    (
        SELECT migration_id
        FROM tide.schema_migrations
        ORDER BY migration_order DESC
        LIMIT 1
    ) = '0037_g04_remove_device_check',
    NOT EXISTS (
        SELECT 1 FROM tide.schema_migrations
        WHERE migration_id = '0038_personalized_environment_photo'
    ),
    NOT EXISTS (
        SELECT 1 FROM tide.task_execution_versions
        WHERE shared_template_row_id = 'P-FB-NEGATIVE:v1'
           OR task_code = 'P-FB-NEGATIVE'
    );
SQL
)"
if [[ "${fresh_missing_template_status}" == "0" \
      || "${fresh_missing_template_output}" != *"当前为 20260811_54_g04_remove_device_check，未执行任何 0038 写入或记账"* \
      || "${fresh_missing_template_state}" != "t|t|t" ]]; then
  echo "0038 在 public head 仍为 rev54 时未于任何写入前失败关闭：${fresh_missing_template_state}" >&2
  echo "${fresh_missing_template_output}" >&2
  exit 1
fi

set_public_head "${FRESH_DB}" "20260811_56_p_fb_negative_copy"
set +e
fresh_missing_public_copy_output="$(
  TIDE_MIGRATION_DATABASE_URL="postgresql:///${FRESH_DB}" \
  TIDE_MIGRATION_EXPECTED_DATABASE="${FRESH_DB}" \
  TIDE_MIGRATION_TARGET="0038_personalized_environment_photo" \
  TIDE_MIGRATION_TEST_MODE="true" \
    bash "${DB_DIR}/scripts/apply-production.sh" 2>&1
)"
fresh_missing_public_copy_status=$?
set -e
fresh_missing_public_copy_state="$(psql -X --no-password -AtF '|' \
  "postgresql:///${FRESH_DB}" <<'SQL'
SELECT
    (
        SELECT migration_id
        FROM tide.schema_migrations
        ORDER BY migration_order DESC
        LIMIT 1
    ) = '0037_g04_remove_device_check',
    NOT EXISTS (
        SELECT 1 FROM tide.schema_migrations
        WHERE migration_id = '0038_personalized_environment_photo'
    ),
    NOT EXISTS (
        SELECT 1 FROM tide.task_execution_versions
        WHERE shared_template_row_id = 'P-FB-NEGATIVE:v1'
           OR task_code = 'P-FB-NEGATIVE'
    );
SQL
)"
if [[ "${fresh_missing_public_copy_status}" == "0" \
      || "${fresh_missing_public_copy_output}" != *"public rev56 的 P-FB-NEGATIVE:v1 精确新文案与零分共享契约"* \
      || "${fresh_missing_public_copy_state}" != "t|t|t" ]]; then
  echo "0038 在 public rev55 新文案缺失时未于任何写入前失败关闭：${fresh_missing_public_copy_state}" >&2
  echo "${fresh_missing_public_copy_output}" >&2
  exit 1
fi

psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${FRESH_DB}" >/dev/null <<'SQL'
INSERT INTO public.task_templates (
    row_id, template_id, template_version, status, revision, output_type,
    execution_owner, external_task_template_code, source_mode, payload,
    created_by, updated_by, integration_mode
) VALUES (
    'P-FB-NEGATIVE:v1',
    'P-FB-NEGATIVE',
    1,
    'PUBLISHED',
    55,
    'TEACHER_TASK',
    'TEACHER_APP',
    'TIT.P.FB.NEGATIVE',
    'REAL',
    '{"template_id":"P-FB-NEGATIVE","output_type":"TEACHER_TASK","audience":"TEACHER","owner":"TIT_GROWTH_OPS","execution_owner":"TEACHER_APP","integration_mode":"OUTBOUND_MANAGED","category":"PERSONALIZED_IMPROVEMENT","dimension":"USER_FEEDBACK","stage":"TRIGGERED","ops_name_zh":"差评改善","content_locale":"en","content_status":"READY","title":"Feedback Improvement","why_template":"The same negative-feedback signal has appeared more than once for this teacher.","how_summary":"Complete the configured improvement activity for the feedback issue shown in the task reason. Depending on the assigned activity, you may need to submit a teaching-environment photo for review or complete another guided action.","completion_standard":"The teacher app marks the task as completed after every requirement for the assigned improvement activity, including any required photo review, is satisfied.","benefit":"This task carries no points. It targets a repeated learner-feedback issue.","priority":"P1","score_type":"ZERO","score_value":0,"source_mode":"REAL"}'::jsonb,
    'production_migrator_test',
    'production_migrator_test',
    'INBOUND_STATUS_ONLY'
);
SQL

set +e
fresh_illegal_template_output="$(
  TIDE_MIGRATION_DATABASE_URL="postgresql:///${FRESH_DB}" \
  TIDE_MIGRATION_EXPECTED_DATABASE="${FRESH_DB}" \
  TIDE_MIGRATION_TARGET="0038_personalized_environment_photo" \
  TIDE_MIGRATION_TEST_MODE="true" \
    bash "${DB_DIR}/scripts/apply-production.sh" 2>&1
)"
fresh_illegal_template_status=$?
set -e
fresh_illegal_template_state="$(psql -X --no-password -AtF '|' \
  "postgresql:///${FRESH_DB}" <<'SQL'
SELECT
    NOT EXISTS (
        SELECT 1 FROM tide.schema_migrations
        WHERE migration_id = '0038_personalized_environment_photo'
    ),
    NOT EXISTS (
        SELECT 1 FROM tide.task_execution_versions
        WHERE shared_template_row_id = 'P-FB-NEGATIVE:v1'
           OR task_code = 'P-FB-NEGATIVE'
    );
SQL
)"
if [[ "${fresh_illegal_template_status}" == "0" \
      || "${fresh_illegal_template_output}" != *"public rev56 的 P-FB-NEGATIVE:v1 精确新文案与零分共享契约"* \
      || "${fresh_illegal_template_state}" != "t|t" ]]; then
  echo "0038 在 public rev56 P-FB-NEGATIVE 契约漂移时未于任何写入前失败关闭：${fresh_illegal_template_state}" >&2
  echo "${fresh_illegal_template_output}" >&2
  exit 1
fi

psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${FRESH_DB}" >/dev/null <<'SQL'
UPDATE public.task_templates
SET integration_mode = 'OUTBOUND_MANAGED'
WHERE row_id = 'P-FB-NEGATIVE:v1';

INSERT INTO tide.task_execution_versions (
    id, shared_template_row_id, task_code, execution_contract_version,
    config, status
) VALUES (
    'a89b9f31-2a71-43da-846e-60c51e14f162',
    'G01:v1',
    'P-FB-ID-COLLISION',
    'task-contract-v3',
    '{}'::jsonb,
    'ACTIVE'
);
SQL

set +e
fresh_identity_collision_output="$(
  TIDE_MIGRATION_DATABASE_URL="postgresql:///${FRESH_DB}" \
  TIDE_MIGRATION_EXPECTED_DATABASE="${FRESH_DB}" \
  TIDE_MIGRATION_TARGET="0038_personalized_environment_photo" \
  TIDE_MIGRATION_TEST_MODE="true" \
    bash "${DB_DIR}/scripts/apply-production.sh" 2>&1
)"
fresh_identity_collision_status=$?
set -e
fresh_identity_collision_state="$(psql -X --no-password -AtF '|' \
  "postgresql:///${FRESH_DB}" <<'SQL'
SELECT
    NOT EXISTS (
        SELECT 1 FROM tide.schema_migrations
        WHERE migration_id = '0038_personalized_environment_photo'
    ),
    EXISTS (
        SELECT 1 FROM tide.task_execution_versions
        WHERE id = 'a89b9f31-2a71-43da-846e-60c51e14f162'
          AND shared_template_row_id = 'G01:v1'
          AND task_code = 'P-FB-ID-COLLISION'
    ),
    NOT EXISTS (
        SELECT 1 FROM tide.task_execution_versions
        WHERE shared_template_row_id = 'P-FB-NEGATIVE:v1'
           OR task_code = 'P-FB-NEGATIVE'
    );
SQL
)"
if [[ "${fresh_identity_collision_status}" == "0" \
      || "${fresh_identity_collision_output}" != *"deterministic P-FB-NEGATIVE execution because its task code or ID is already occupied"* \
      || "${fresh_identity_collision_state}" != "t|t|t" ]]; then
  echo "0038 未原子拒绝确定性 execution ID 冲突：${fresh_identity_collision_state}" >&2
  echo "${fresh_identity_collision_output}" >&2
  exit 1
fi

psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${FRESH_DB}" \
  -c "DELETE FROM tide.task_execution_versions WHERE id = 'a89b9f31-2a71-43da-846e-60c51e14f162'" >/dev/null

TIDE_MIGRATION_DATABASE_URL="postgresql:///${FRESH_DB}" \
TIDE_MIGRATION_EXPECTED_DATABASE="${FRESH_DB}" \
TIDE_MIGRATION_TARGET="0038_personalized_environment_photo" \
TIDE_MIGRATION_TEST_MODE="true" \
  bash "${DB_DIR}/scripts/apply-production.sh" >/dev/null
install_public_g02_contract "${FRESH_DB}"
TIDE_MIGRATION_DATABASE_URL="postgresql:///${FRESH_DB}" \
TIDE_MIGRATION_EXPECTED_DATABASE="${FRESH_DB}" \
TIDE_MIGRATION_TARGET="0041_crm_sso_hybrid" \
TIDE_MIGRATION_TEST_MODE="true" \
  bash "${DB_DIR}/scripts/apply-production.sh" >/dev/null
install_public_59_support_guard_and_acl "${FRESH_DB}"
install_public_g09_contract "${FRESH_DB}"
TIDE_MIGRATION_DATABASE_URL="postgresql:///${FRESH_DB}" \
TIDE_MIGRATION_EXPECTED_DATABASE="${FRESH_DB}" \
TIDE_MIGRATION_TARGET="0042_g09_set_kuozhi_course" \
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
    EXISTS (
        SELECT 1
        FROM tide.task_execution_versions execution
        WHERE execution.id = 'a89b9f31-2a71-43da-846e-60c51e14f162'
          AND execution.shared_template_row_id = 'P-FB-NEGATIVE:v1'
          AND execution.task_code = 'P-FB-NEGATIVE'
          AND execution.execution_contract_version = 'task-contract-v3'
          AND execution.status = 'ACTIVE'
          AND execution.config->>'contentVersion' =
              '2026-08-11-personalized-environment-photo-v1'
    ),
    EXISTS (
        SELECT 1
        FROM tide.task_step_definitions definition
        WHERE definition.id = '8911e60c-012d-4e01-8ecc-1cb10de35c83'
          AND definition.execution_version_id =
              'a89b9f31-2a71-43da-846e-60c51e14f162'
          AND definition.step_key = 'p-fb-negative-environment-photo'
    ),
    (
        SELECT count(*) = 2
        FROM tide.task_validation_rules rule
        WHERE rule.execution_version_id =
            'a89b9f31-2a71-43da-846e-60c51e14f162'
    ),
    to_regclass('tide.task_quiz_banks') IS NULL,
    NOT EXISTS (SELECT 1 FROM tide.knowledge_documents),
    to_regclass('tide.kuozhi_course_syncs') IS NOT NULL,
    to_regclass('tide.analytics_task_business_change_v1') IS NULL,
    EXISTS (
        SELECT 1
        FROM tide.schema_migrations
        WHERE migration_id = '0028_retire_task_business_change_view'
    ),
    EXISTS (
        SELECT 1
        FROM tide.schema_migrations
        WHERE migration_id = '0029_remove_unused_tide_objects'
    ),
    EXISTS (
        SELECT 1
        FROM tide.schema_migrations
        WHERE migration_id = '0030_remove_unused_columns_and_orphan_function'
          AND migration_order = 28
          AND filename = '0030_remove_unused_columns_and_orphan_function.up.sql'
    ),
    to_regclass('tide.outcome_projections') IS NULL
        AND to_regclass('tide.camp_enrollment_projections') IS NULL
        AND to_regclass('tide.audit_events') IS NULL
        AND to_regclass('tide.task_template_files') IS NULL
        AND to_regclass('tide.file_migrations') IS NULL
        AND to_regclass('tide.teacher_photo_runs') IS NULL,
    to_regclass('tide.analytics_actor_task_journey_v1') IS NULL
        AND to_regclass('tide.analytics_task_assignment_funnel_v1') IS NULL
        AND to_regclass('tide.analytics_task_funnel_v1') IS NULL
        AND to_regclass('tide.analytics_task_step_funnel_v1') IS NULL
        AND to_regclass('tide.analytics_content_quality_v1') IS NULL,
    NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'tide'
          AND table_name = 'file_objects'
          AND column_name = 'visibility'
    ) AND to_regprocedure('tide.enforce_outbox_target()') IS NULL,
    to_regclass('tide.account_onboarding_states') IS NOT NULL,
    NOT EXISTS (SELECT 1 FROM tide.account_onboarding_states),
    to_regclass('tide.crm_sso_logins') IS NOT NULL,
    NOT EXISTS (SELECT 1 FROM tide.crm_sso_logins),
    (SELECT version_num FROM public.alembic_version) =
        '20260819_65_g09_set_course',
    count(*) FILTER (
        WHERE migration_id = '0041_crm_sso_hybrid'
    ) = 1,
    count(*) FILTER (
        WHERE migration_id = '0042_g09_set_kuozhi_course'
    ) = 1,
    EXISTS (
        SELECT 1
        FROM tide.task_execution_versions
        WHERE shared_template_row_id = 'G10:v1'
          AND task_code = 'G09'
          AND execution_contract_version = 'task-contract-v3'
          AND config =
              '{"estimatedMinutes":25,"allowRetry":true,"contentStatus":"READY","contentVersion":"2026-08-19-set-kuozhi-v1","pendingReason":null}'::jsonb
    ),
    count(*) FILTER (
        WHERE migration_id = '0038_personalized_environment_photo'
    ) = 1,
    count(*) FILTER (
        WHERE migration_id = '0039_g02_policy_document'
    ) = 1,
    count(*) FILTER (
        WHERE migration_id = '0040_g02_document_read_status'
    ) = 1,
    count(*) FILTER (
        WHERE migration_id = '0033_g01_tesol_only'
    ) = 1,
    count(*) FILTER (
        WHERE migration_id = '0037_g04_remove_device_check'
    ) = 1,
    count(*)
FROM tide.schema_migrations;
SQL
)"
if [[ "${fresh_state}" != "t|t|t|f|t|t|t|t|t|t|t|t|t|t|t|t|t|t|t|t|t|t|t|t|t|t|t|t|t|t|t|t|t|37" ]]; then
  echo "生产 fresh 迁移状态异常：${fresh_state}" >&2
  exit 1
fi

fresh_ai_config_before_drift="$(psql -X --no-password -Atqc "
  SELECT config
  FROM tide.task_validation_rules
  WHERE execution_version_id =
      'a89b9f31-2a71-43da-846e-60c51e14f162'
    AND rule_key = 'p-fb-negative-environment-ai-review'
" "postgresql:///${FRESH_DB}")"
psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${FRESH_DB}" >/dev/null <<'SQL'
UPDATE tide.task_validation_rules
SET config = jsonb_set(
        config,
        '{systemPrompt}',
        to_jsonb('migration-test-drifted-system-prompt'::text)
    ) || '{"unexpectedPolicy":true}'::jsonb
WHERE execution_version_id =
    'a89b9f31-2a71-43da-846e-60c51e14f162'
  AND rule_key = 'p-fb-negative-environment-ai-review';
SQL
set +e
fresh_up_drift_output="$(
  psql -X --no-password -v ON_ERROR_STOP=1 \
    "postgresql:///${FRESH_DB}" \
    -f "${DB_DIR}/migrations/0038_personalized_environment_photo.up.sql" 2>&1
)"
fresh_up_drift_status=$?
set -e
fresh_up_drift_state="$(psql -X --no-password -AtF '|' \
  "postgresql:///${FRESH_DB}" <<'SQL'
SELECT
    (
        SELECT config->>'systemPrompt' =
            'migration-test-drifted-system-prompt'
        FROM tide.task_validation_rules
        WHERE execution_version_id =
            'a89b9f31-2a71-43da-846e-60c51e14f162'
          AND rule_key = 'p-fb-negative-environment-ai-review'
    ),
    (
        SELECT config @> '{"unexpectedPolicy":true}'::jsonb
        FROM tide.task_validation_rules
        WHERE execution_version_id =
            'a89b9f31-2a71-43da-846e-60c51e14f162'
          AND rule_key = 'p-fb-negative-environment-ai-review'
    ),
    (SELECT count(*) FROM tide.task_execution_versions) = 1,
    (
        SELECT count(*) = 1
        FROM tide.task_step_definitions
        WHERE execution_version_id =
            'a89b9f31-2a71-43da-846e-60c51e14f162'
    ),
    (
        SELECT count(*) = 2
        FROM tide.task_validation_rules
        WHERE execution_version_id =
            'a89b9f31-2a71-43da-846e-60c51e14f162'
    );
SQL
)"
if [[ "${fresh_up_drift_status}" == "0" \
      || "${fresh_up_drift_output}" != *"unreviewed validation rule"* \
      || "${fresh_up_drift_state}" != "t|t|t|t|t" ]]; then
  echo "0038 up 未拒绝带额外字段和 prompt 漂移的 AI 规则：${fresh_up_drift_state}" >&2
  echo "${fresh_up_drift_output}" >&2
  exit 1
fi
psql -X --no-password -v ON_ERROR_STOP=1 \
  -v original_ai_config="${fresh_ai_config_before_drift}" \
  "postgresql:///${FRESH_DB}" >/dev/null <<'SQL'
UPDATE tide.task_validation_rules
SET config = :'original_ai_config'::jsonb
WHERE execution_version_id =
    'a89b9f31-2a71-43da-846e-60c51e14f162'
  AND rule_key = 'p-fb-negative-environment-ai-review';
SQL

psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${FRESH_DB}" \
  -f "${DB_DIR}/migrations/0038_personalized_environment_photo.up.sql" >/dev/null
fresh_idempotent_state="$(psql -X --no-password -AtF '|' \
  "postgresql:///${FRESH_DB}" <<'SQL'
SELECT
    (SELECT count(*) FROM tide.task_execution_versions) = 1,
    (
        SELECT count(*) = 1
        FROM tide.task_execution_versions
        WHERE id = 'a89b9f31-2a71-43da-846e-60c51e14f162'
          AND shared_template_row_id = 'P-FB-NEGATIVE:v1'
          AND task_code = 'P-FB-NEGATIVE'
    ),
    (
        SELECT count(*) = 1
        FROM tide.task_step_definitions
        WHERE id = '8911e60c-012d-4e01-8ecc-1cb10de35c83'
          AND execution_version_id =
              'a89b9f31-2a71-43da-846e-60c51e14f162'
    ),
    (
        SELECT count(*) = 2
        FROM tide.task_validation_rules
        WHERE execution_version_id =
            'a89b9f31-2a71-43da-846e-60c51e14f162'
    );
SQL
)"
if [[ "${fresh_idempotent_state}" != "t|t|t|t" ]]; then
  echo "0038 fresh execution 重跑不幂等：${fresh_idempotent_state}" >&2
  exit 1
fi

fresh_ai_failure_copy_before_drift="$(psql -X --no-password -Atqc "
  SELECT teacher_failure_copy
  FROM tide.task_validation_rules
  WHERE execution_version_id =
      'a89b9f31-2a71-43da-846e-60c51e14f162'
    AND rule_key = 'p-fb-negative-environment-ai-review'
" "postgresql:///${FRESH_DB}")"
psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${FRESH_DB}" >/dev/null <<'SQL'
UPDATE tide.task_validation_rules
SET config = config || '{"unexpectedPolicy":true}'::jsonb,
    teacher_failure_copy = 'migration-test-drifted-teacher-copy'
WHERE execution_version_id =
    'a89b9f31-2a71-43da-846e-60c51e14f162'
  AND rule_key = 'p-fb-negative-environment-ai-review';
SQL
set +e
fresh_down_drift_output="$(
  psql -X --no-password -v ON_ERROR_STOP=1 \
    "postgresql:///${FRESH_DB}" \
    -f "${DB_DIR}/migrations/0038_personalized_environment_photo.down.sql" 2>&1
)"
fresh_down_drift_status=$?
set -e
fresh_down_drift_state="$(psql -X --no-password -AtF '|' \
  "postgresql:///${FRESH_DB}" <<'SQL'
SELECT
    EXISTS (
        SELECT 1
        FROM tide.task_execution_versions
        WHERE id = 'a89b9f31-2a71-43da-846e-60c51e14f162'
          AND config->>'contentVersion' =
              '2026-08-11-personalized-environment-photo-v1'
    ),
    (
        SELECT count(*) = 1
        FROM tide.task_step_definitions
        WHERE execution_version_id =
            'a89b9f31-2a71-43da-846e-60c51e14f162'
    ),
    (
        SELECT count(*) = 2
        FROM tide.task_validation_rules
        WHERE execution_version_id =
            'a89b9f31-2a71-43da-846e-60c51e14f162'
    ),
    (
        SELECT config @> '{"unexpectedPolicy":true}'::jsonb
        FROM tide.task_validation_rules
        WHERE execution_version_id =
            'a89b9f31-2a71-43da-846e-60c51e14f162'
          AND rule_key = 'p-fb-negative-environment-ai-review'
    ),
    (
        SELECT teacher_failure_copy =
            'migration-test-drifted-teacher-copy'
        FROM tide.task_validation_rules
        WHERE execution_version_id =
            'a89b9f31-2a71-43da-846e-60c51e14f162'
          AND rule_key = 'p-fb-negative-environment-ai-review'
    );
SQL
)"
if [[ "${fresh_down_drift_status}" == "0" \
      || "${fresh_down_drift_output}" != *"down refused an unknown P-FB-NEGATIVE execution shape"* \
      || "${fresh_down_drift_state}" != "t|t|t|t|t" ]]; then
  echo "0038 down 未拒绝额外字段和教师提示漂移：${fresh_down_drift_state}" >&2
  echo "${fresh_down_drift_output}" >&2
  exit 1
fi
psql -X --no-password -v ON_ERROR_STOP=1 \
  -v original_ai_config="${fresh_ai_config_before_drift}" \
  -v original_failure_copy="${fresh_ai_failure_copy_before_drift}" \
  "postgresql:///${FRESH_DB}" >/dev/null <<'SQL'
UPDATE tide.task_validation_rules
SET config = :'original_ai_config'::jsonb,
    teacher_failure_copy = :'original_failure_copy'
WHERE execution_version_id =
    'a89b9f31-2a71-43da-846e-60c51e14f162'
  AND rule_key = 'p-fb-negative-environment-ai-review';
SQL

psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${FRESH_DB}" \
  -f "${DB_DIR}/migrations/0038_personalized_environment_photo.down.sql" >/dev/null
fresh_down_state="$(psql -X --no-password -AtF '|' \
  "postgresql:///${FRESH_DB}" <<'SQL'
SELECT
    EXISTS (
        SELECT 1
        FROM tide.task_execution_versions
        WHERE id = 'a89b9f31-2a71-43da-846e-60c51e14f162'
          AND shared_template_row_id = 'P-FB-NEGATIVE:v1'
          AND config =
            '{"estimatedMinutes":8,"allowRetry":true,"contentStatus":"PENDING","contentVersion":"2026-08-06","pendingReason":"JIAHE_PERSONALIZED_CONTENT_PENDING"}'::jsonb
    ),
    NOT EXISTS (
        SELECT 1 FROM tide.task_step_definitions
        WHERE execution_version_id =
            'a89b9f31-2a71-43da-846e-60c51e14f162'
    ),
    NOT EXISTS (
        SELECT 1 FROM tide.task_validation_rules
        WHERE execution_version_id =
            'a89b9f31-2a71-43da-846e-60c51e14f162'
    );
SQL
)"
if [[ "${fresh_down_state}" != "t|t|t" ]]; then
  echo "0038 fresh down 未恢复确定性待配置 execution：${fresh_down_state}" >&2
  exit 1
fi

psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${FRESH_DB}" \
  -f "${DB_DIR}/migrations/0038_personalized_environment_photo.up.sql" >/dev/null
fresh_down_up_state="$(psql -X --no-password -AtF '|' \
  "postgresql:///${FRESH_DB}" <<'SQL'
SELECT
    EXISTS (
        SELECT 1
        FROM tide.task_execution_versions
        WHERE id = 'a89b9f31-2a71-43da-846e-60c51e14f162'
          AND shared_template_row_id = 'P-FB-NEGATIVE:v1'
          AND config->>'contentVersion' =
              '2026-08-11-personalized-environment-photo-v1'
    ),
    (
        SELECT count(*) = 1
        FROM tide.task_step_definitions
        WHERE execution_version_id =
            'a89b9f31-2a71-43da-846e-60c51e14f162'
    ),
    (
        SELECT count(*) = 2
        FROM tide.task_validation_rules
        WHERE execution_version_id =
            'a89b9f31-2a71-43da-846e-60c51e14f162'
    );
SQL
)"
if [[ "${fresh_down_up_state}" != "t|t|t" ]]; then
  echo "0038 fresh down/up 未恢复确定性拍照 execution：${fresh_down_up_state}" >&2
  exit 1
fi

psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${FRESH_DB}" \
  -c "DROP TABLE IF EXISTS public.teacher_metric_snapshots" >/dev/null
TIDE_MIGRATION_DATABASE_URL="postgresql:///${FRESH_DB}" \
TIDE_MIGRATION_EXPECTED_DATABASE="${FRESH_DB}" \
TIDE_MIGRATION_TEST_MODE="true" \
  bash "${DB_DIR}/scripts/apply-production.sh" >/dev/null
post_public_drop_state="$(psql -X --no-password -AtF '|' \
  "postgresql:///${FRESH_DB}" <<'SQL'
SELECT
    to_regclass('public.teacher_metric_snapshots') IS NULL,
    to_regclass('tide.analytics_task_business_change_v1') IS NULL,
    (
        SELECT migration_id
        FROM tide.schema_migrations
        ORDER BY migration_order DESC
        LIMIT 1
    ) = '0042_g09_set_kuozhi_course',
    (SELECT count(*) FROM tide.schema_migrations) = 37;
SQL
)"
if [[ "${post_public_drop_state}" != "t|t|t|t" ]]; then
  echo "teacher 0042 后删除旧 snapshot 导致迁移器不可重入：${post_public_drop_state}" >&2
  exit 1
fi
psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${FRESH_DB}" >/dev/null <<'SQL'
ALTER TABLE public.lesson_dimension_scores
  RENAME TO test_retired_lesson_dimension_scores;
ALTER TABLE public.lesson_facts
  RENAME TO test_retired_lesson_facts;
SQL
psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${FRESH_DB}" \
  -f "${DB_DIR}/scripts/grant-tit-teacher-crud.sql" >/dev/null
final_acl_probe_state="$(psql -X --no-password -Atqc "
  select
    not exists (
      select 1
      from unnest(array[
        'public.alembic_version',
        'public.task_templates',
        'public.teachers',
        'public.teacher_scorecard_current',
        'public.teacher_lesson_score_current',
        'public.teacher_g01_status_current'
      ]::text[]) relation(name),
      unnest(array['SELECT']::text[]) privilege(name)
      where not has_table_privilege(
        'tit_teacher_crud', relation.name, privilege.name
      )
    )
    and not exists (
      select 1
      from unnest(array[
        'public.alembic_version',
        'public.task_templates',
        'public.teachers',
        'public.teacher_scorecard_current',
        'public.teacher_lesson_score_current',
        'public.teacher_g01_status_current'
      ]::text[]) relation(name),
      unnest(array['INSERT','UPDATE','DELETE']::text[]) privilege(name)
      where has_table_privilege(
        'tit_teacher_crud', relation.name, privilege.name
      )
    )
    and not exists (
      select 1
      from unnest(array[
        'public.task_assignments',
        'public.notifications',
        'public.notification_events',
        'public.teacher_support_tickets'
      ]::text[]) relation(name),
      unnest(array['SELECT','INSERT','UPDATE','DELETE']::text[]) privilege(name)
      where not has_table_privilege(
        'tit_teacher_crud', relation.name, privilege.name
      )
    )
    and not exists (
      select 1
      from pg_class relation
      join pg_namespace namespace on namespace.oid = relation.relnamespace
      cross join lateral unnest(
        array['SELECT','INSERT','UPDATE','DELETE']::text[]
      ) privilege(name)
      where namespace.nspname = 'tide'
        and relation.relkind in ('r', 'p')
        and not has_table_privilege(
          'tit_teacher_crud', relation.oid, privilege.name
        )
    )
    and not has_table_privilege(
      'tit_teacher_crud', 'public.teacher_source_wide', 'SELECT'
    )
    and not exists (
      select 1
      from unnest(array['SELECT','INSERT','UPDATE','DELETE']::text[])
        privilege(name)
      where not has_table_privilege(
        'tide_support_ticket_owner',
        'public.teacher_support_tickets',
        privilege.name
      )
    )
    and exists (
      select 1 from pg_trigger
      where tgrelid = 'public.teacher_support_tickets'::regclass
        and tgname = 'guard_simple_support_ticket_write'
        and not tgisinternal
    )
    and (select not rolinherit from pg_roles where rolname = 'tide_sys_admin')
    and (select not rolinherit from pg_roles where rolname = 'tit_teacher_crud')
" "postgresql:///${FRESH_DB}")"
if [[ "${final_acl_probe_state}" != "t" ]]; then
  echo "public 63 最终教师/Owner 表级 ACL 验收失败。" >&2
  exit 1
fi
legacy_acl_probe_state="$(psql -X --no-password -AtF '|' \
  "postgresql:///${FRESH_DB}" <<'SQL'
SELECT
    coalesce(
        has_table_privilege(
            'tit_teacher_crud',
            to_regclass('public.teacher_metric_snapshots'),
            'SELECT'
        ),
        false
    ) = false,
    coalesce(
        has_table_privilege(
            'tit_teacher_crud',
            to_regclass('public.lesson_facts'),
            'SELECT'
        ),
        false
    ) = false,
    coalesce(
        has_table_privilege(
            'tit_teacher_crud',
            to_regclass('public.lesson_dimension_scores'),
            'SELECT'
        ),
        false
    ) = false;
SQL
)"
if [[ "${legacy_acl_probe_state}" != "t|t|t" ]]; then
  echo "旧 public 表不存在时权限探测不安全：${legacy_acl_probe_state}" >&2
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

upgrade_business_change_view_definition="$(psql -X --no-password -Atqc \
  "select pg_get_viewdef('tide.analytics_task_business_change_v1'::regclass, true)" \
  "postgresql:///${UPGRADE_DB}")"
if [[ -z "${upgrade_business_change_view_definition}" ]]; then
  echo "0021 基线缺少待退役业务变化视图。" >&2
  exit 1
fi

psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${UPGRADE_DB}" >/dev/null <<'SQL'
INSERT INTO tide.user_accounts (
    id,
    email,
    normalized_email,
    password_hash,
    status,
    email_verified_at
)
VALUES
(
    '27000000-0000-4000-8000-000000000001',
    'migrated-login@example.invalid',
    'migrated-login@example.invalid',
    'migration-test-hash',
    'ACTIVE',
    '2026-07-01 00:00:00+00'
),
(
    '27000000-0000-4000-8000-000000000002',
    'never-logged-in@example.invalid',
    'never-logged-in@example.invalid',
    'migration-test-hash',
    'ACTIVE',
    '2026-07-01 00:00:00+00'
),
(
    '27000000-0000-4000-8000-000000000003',
    'other-success-event@example.invalid',
    'other-success-event@example.invalid',
    'migration-test-hash',
    'ACTIVE',
    '2026-07-01 00:00:00+00'
);

INSERT INTO tide.auth_security_events (
    id,
    account_id,
    event_type,
    outcome,
    created_at
)
VALUES
(
    '27000000-0000-4000-8000-000000000101',
    '27000000-0000-4000-8000-000000000001',
    'LOGIN',
    'SUCCESS',
    '2026-07-10 01:02:03+00'
),
(
    '27000000-0000-4000-8000-000000000102',
    '27000000-0000-4000-8000-000000000001',
    'LOGIN',
    'SUCCESS',
    '2026-07-11 01:02:03+00'
),
(
    '27000000-0000-4000-8000-000000000201',
    '27000000-0000-4000-8000-000000000002',
    'LOGIN',
    'FAILURE',
    '2026-07-12 01:02:03+00'
),
(
    '27000000-0000-4000-8000-000000000301',
    '27000000-0000-4000-8000-000000000003',
    'PASSWORD_RESET',
    'SUCCESS',
    '2026-07-13 01:02:03+00'
);

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
    CASE
        WHEN catalog.row_id IN ('G02:v1', 'G03:v1') THEN
            '{"estimatedMinutes":15,"allowRetry":true,"contentStatus":"READY","contentVersion":"2026-07-30","pendingReason":null}'::jsonb
        ELSE jsonb_build_object('migration_test', true)
    END,
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
VALUES
(
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
),
(
    'MIGRATION-SEMANTIC-G01',
    'MIGRATION-SEMANTIC-TEACHER',
    'G01',
    'G01:v1',
    'FIXED_GROWTH',
    'TRIGGER_CENTER',
    'ASSIGNED',
    'P1',
    '[Verify] Preserve G01 assignment and progress identity.',
    'MOCK',
    'fixed:MIGRATION-SEMANTIC-TEACHER:G01'
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
VALUES
(
    '25abcdef-0000-4000-8000-000000000100',
    '25abcdef-0000-4000-8000-000000000001',
    'g01-essay-confirmation',
    1,
    'CHECKLIST',
    'Confirm the TESOL Essay is complete',
    '{"role":"TESOL_ESSAY","migration_test":true}'::jsonb
),
(
    '25abcdef-0000-4000-8000-000000000101',
    '25abcdef-0000-4000-8000-000000000002',
    'g02-courseware-confirmation',
    1,
    'CHECKLIST',
    'Confirm courseware preparation',
    '{"version":"g02-courseware-2026-07-28","role":"COURSEWARE_CONFIRMATION","items":[{"key":"courseware-prepared","label":"I have reviewed all the slides and finished preparing for this lesson.","labelZh":"我已浏览全部课件，并完成本节课备课。"}]}'::jsonb
),
(
    '25abcdef-0000-4000-8000-000000000102',
    '25abcdef-0000-4000-8000-000000000002',
    'g02-device-check',
    2,
    'DEVICE_CHECK',
    'Check camera, microphone and network',
    '{"version":"g02-device-2026-07-22","role":"DEVICE_CHECK","items":["camera","microphone","network"]}'::jsonb
),
(
    '25abcdef-0000-4000-8000-000000000103',
    '25abcdef-0000-4000-8000-000000000002',
    'g02-environment-photo',
    3,
    'UPLOAD',
    'Take a teaching-environment photo',
    '{"version":"g02-photo-2026-07-22","role":"ENVIRONMENT_PHOTO","accept":["image/jpeg"],"captureOnly":true,"maxFiles":1}'::jsonb
);

INSERT INTO tide.task_validation_rules (
    id,
    execution_version_id,
    rule_key,
    rule_type,
    rule_version,
    position,
    config,
    teacher_failure_copy
)
VALUES
(
    '25abcdef-0000-4000-8000-000000000110',
    '25abcdef-0000-4000-8000-000000000001',
    'all-steps-complete',
    'ALL_STEPS_COMPLETE',
    '2026-07-22',
    1,
    '{}'::jsonb,
    '请确认 Essay，并提交完成证明。'
),
(
    '25abcdef-0000-4000-8000-000000000113',
    '25abcdef-0000-4000-8000-000000000001',
    'g01-external-status',
    'G01_EXTERNAL_STATUS',
    '2026-07-22',
    3,
    '{}'::jsonb,
    'Self-intro 和 TESOL 真实状态尚未全部通过。'
),
(
    '25abcdef-0000-4000-8000-000000000111',
    '25abcdef-0000-4000-8000-000000000002',
    'all-steps-complete',
    'ALL_STEPS_COMPLETE',
    '2026-07-22',
    1,
    '{}'::jsonb,
    '请先完成备课确认、设备网络检查和授课环境照片检查。'
),
(
    '25abcdef-0000-4000-8000-000000000112',
    '25abcdef-0000-4000-8000-000000000002',
    'g02-environment-ai-review',
    'AI_IMAGE_REVIEW',
    '2026-07-27-strict',
    2,
    jsonb_build_object(
        'stepKey', 'g02-environment-photo',
        'criteriaVersion', 'g02-environment-2026-07-v2-strict',
        'criteriaKeys', jsonb_build_array('lighting', 'framing', 'posture', 'headset', 'appearance', 'background', 'clarity'),
        'allowedMimeTypes', jsonb_build_array('image/jpeg', 'image/png', 'image/webp'),
        'systemPrompt', 'Reviewed legacy prompt.',
        'userText', 'Reviewed legacy photo criteria.'
    ),
    '已保留你完成的内容，请根据提示更新这份材料。'
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
VALUES
(
    '25abcdef-0000-4000-8000-000000000202',
    'MIGRATION-SEMANTIC-G04',
    'g02-device-check',
    'IN_PROGRESS',
    50,
    '{"checkpoint":"before-0025"}'::jsonb,
    '2026-07-30 00:00:00+00'
),
(
    '25abcdef-0000-4000-8000-000000000201',
    'MIGRATION-SEMANTIC-G01',
    'g01-essay-confirmation',
    'IN_PROGRESS',
    50,
    '{"checkpoint":"before-0033"}'::jsonb,
    '2026-08-10 00:00:00+00'
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
  TIDE_MIGRATION_TARGET="0028_retire_task_business_change_view" \
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
TIDE_MIGRATION_TARGET="0028_retire_task_business_change_view" \
TIDE_MIGRATION_TEST_MODE="true" \
  bash "${DB_DIR}/scripts/apply-production.sh" >/dev/null

retired_business_change_view_state="$(psql -X --no-password -AtF '|' \
  "postgresql:///${UPGRADE_DB}" <<'SQL'
SELECT
    to_regclass('tide.analytics_task_business_change_v1') IS NULL,
    EXISTS (
        SELECT 1
        FROM tide.schema_migrations
        WHERE migration_id = '0028_retire_task_business_change_view'
    );
SQL
)"
if [[ "${retired_business_change_view_state}" != "t|t" ]]; then
  echo "0028 未退役旧业务变化视图：${retired_business_change_view_state}" >&2
  exit 1
fi

psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${UPGRADE_DB}" \
  -c "DROP TABLE public.teacher_metric_snapshots" >/dev/null
advance_public_g04_to_rev54 "${UPGRADE_DB}"

set +e
skip_public50_output="$(
  TIDE_MIGRATION_DATABASE_URL="postgresql:///${UPGRADE_DB}" \
  TIDE_MIGRATION_EXPECTED_DATABASE="${UPGRADE_DB}" \
  TIDE_MIGRATION_TARGET="0032_first_login_onboarding" \
  TIDE_MIGRATION_TEST_MODE="true" \
    bash "${DB_DIR}/scripts/apply-production.sh" 2>&1
)"
skip_public50_status=$?
skip_teacher32_output="$(
  TIDE_MIGRATION_DATABASE_URL="postgresql:///${UPGRADE_DB}" \
  TIDE_MIGRATION_EXPECTED_DATABASE="${UPGRADE_DB}" \
  TIDE_MIGRATION_TARGET="0037_g04_remove_device_check" \
  TIDE_MIGRATION_TEST_MODE="true" \
    bash "${DB_DIR}/scripts/apply-production.sh" 2>&1
)"
skip_teacher32_status=$?
set -e
managed_skip_state="$(psql -X --no-password -AtF '|' \
  "postgresql:///${UPGRADE_DB}" <<'SQL'
SELECT
    count(*) = 26,
    max(migration_order) = 26,
    count(*) FILTER (
        WHERE migration_id IN (
            '0029_remove_unused_tide_objects',
            '0030_remove_unused_columns_and_orphan_function',
            '0031_g04_independent_sections',
            '0032_first_login_onboarding',
            '0033_g01_tesol_only',
            '0037_g04_remove_device_check'
        )
    ) = 0
FROM tide.schema_migrations;
SQL
)"
if [[ "${skip_public50_status}" == "0" \
      || "${skip_public50_output}" != *"teacher 0032 要求 public head 50"* \
      || "${skip_teacher32_status}" == "0" \
      || "${skip_teacher32_output}" != *"teacher 0037 只能从 teacher 0032"* \
      || "${managed_skip_state}" != "t|t|t" ]]; then
  echo "managed 库跳过 public50/teacher0032 时未在 Tide 写入前失败关闭：${managed_skip_state}" >&2
  echo "${skip_public50_output}" >&2
  echo "${skip_teacher32_output}" >&2
  exit 1
fi
advance_public_g04_to_rev50 "${UPGRADE_DB}"

psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${UPGRADE_DB}" >/dev/null <<'SQL'
INSERT INTO tide.task_step_definitions (
    id, execution_version_id, step_key, position, step_type, title, config
)
VALUES (
    '25abcdef-0000-4000-8000-000000000199',
    '25abcdef-0000-4000-8000-000000000002',
    'g02-unreviewed-step',
    4,
    'CUSTOM',
    '[Verify] Unknown step must fail closed',
    '{}'::jsonb
);
SQL

set +e
g04_guard_output="$(
  psql -X --no-password -v ON_ERROR_STOP=1 \
    "postgresql:///${UPGRADE_DB}" \
    -f "${DB_DIR}/migrations/0031_g04_independent_sections.up.sql" 2>&1
)"
g04_guard_status=$?
set -e

g04_guard_state="$(psql -X --no-password -AtF '|' "postgresql:///${UPGRADE_DB}" <<'SQL'
SELECT
    EXISTS (
        SELECT 1
        FROM tide.task_step_definitions
        WHERE step_key = 'g02-unreviewed-step'
    ),
    NOT EXISTS (
        SELECT 1
        FROM tide.schema_migrations
        WHERE migration_id = '0031_g04_independent_sections'
    ),
    EXISTS (
        SELECT 1
        FROM tide.schema_migrations
        WHERE migration_id = '0025_fixed_task_semantic_alignment'
    );
SQL
)"
if [[ "${g04_guard_status}" == "0" \
      || "${g04_guard_output}" != *"unreviewed step"* \
      || "${g04_guard_state}" != "t|t|t" ]]; then
  echo "0031 未原子拒绝未知 G04 step：${g04_guard_state}" >&2
  echo "${g04_guard_output}" >&2
  exit 1
fi

psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${UPGRADE_DB}" \
  -c "DELETE FROM tide.task_step_definitions WHERE step_key = 'g02-unreviewed-step'" >/dev/null

# Rehearse the other reviewed production input (courseware + photo) inside an
# outer transaction. Strip only the migration's own BEGIN/COMMIT so every DML
# and postflight assertion runs, then roll the rehearsal back before the real
# legacy-three-step managed upgrade below.
two_step_rehearsal_state="$({
  printf '%s\n' 'BEGIN;'
  printf '%s\n' \
    "DELETE FROM tide.task_step_definitions WHERE execution_version_id = '25abcdef-0000-4000-8000-000000000002'::uuid AND step_key = 'g02-device-check';" \
    "UPDATE tide.task_step_definitions SET position = 2 WHERE execution_version_id = '25abcdef-0000-4000-8000-000000000002'::uuid AND step_key = 'g02-environment-photo';" \
    "UPDATE tide.task_validation_rules SET teacher_failure_copy = '请完成备课确认和授课环境照片检查。' WHERE execution_version_id = '25abcdef-0000-4000-8000-000000000002'::uuid AND rule_key = 'all-steps-complete';" \
    "UPDATE tide.task_validation_rules SET config = config || jsonb_build_object('criteriaVersion', 'lesson-preparation-camera-view-2026-08-v7-background-veto', 'criteriaKeys', jsonb_build_array('camera_angle', 'lighting', 'background', 'dressing')) WHERE execution_version_id = '25abcdef-0000-4000-8000-000000000002'::uuid AND rule_key = 'g02-environment-ai-review';"
  sed '1d;$d' "${DB_DIR}/migrations/0031_g04_independent_sections.up.sql"
  printf '%s\n' \
    "SELECT count(*) = 3 AND bool_or(step_key = 'g02-device-check' AND id = '16cfbdd4-8486-4f87-8bae-4f4d8e365d18'::uuid) FROM tide.task_step_definitions WHERE execution_version_id = '25abcdef-0000-4000-8000-000000000002'::uuid;" \
    'ROLLBACK;'
} | psql -X --no-password -qAt -v ON_ERROR_STOP=1 "postgresql:///${UPGRADE_DB}")"
if [[ "${two_step_rehearsal_state}" != "t" ]]; then
  echo "0031 未通过已评审的 G04 旧两步结构演练：${two_step_rehearsal_state}" >&2
  exit 1
fi

psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${UPGRADE_DB}" \
  -c "CREATE VIEW tide.test_0028_dependency_guard AS SELECT id FROM tide.audit_events" >/dev/null
set +e
unused_dependency_guard_output="$(
  TIDE_MIGRATION_DATABASE_URL="postgresql:///${UPGRADE_DB}" \
  TIDE_MIGRATION_EXPECTED_DATABASE="${UPGRADE_DB}" \
  TIDE_MIGRATION_TARGET="0029_remove_unused_tide_objects" \
  TIDE_MIGRATION_TEST_MODE="true" \
    bash "${DB_DIR}/scripts/apply-production.sh" 2>&1
)"
unused_dependency_guard_status=$?
set -e
unused_dependency_guard_state="$(psql -X --no-password -AtF '|' \
  "postgresql:///${UPGRADE_DB}" <<'SQL'
SELECT
    to_regclass('tide.test_0028_dependency_guard') IS NOT NULL,
    to_regclass('tide.audit_events') IS NOT NULL,
    NOT EXISTS (
        SELECT 1
        FROM tide.schema_migrations
        WHERE migration_id = '0029_remove_unused_tide_objects'
    );
SQL
)"
if [[ "${unused_dependency_guard_status}" == "0" \
      || "${unused_dependency_guard_output}" != *"dependent views"* \
      || "${unused_dependency_guard_state}" != "t|t|t" ]]; then
  echo "0029 未原子阻断外部依赖：${unused_dependency_guard_state}" >&2
  echo "${unused_dependency_guard_output}" >&2
  exit 1
fi
psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${UPGRADE_DB}" \
  -c "DROP VIEW tide.test_0028_dependency_guard" >/dev/null

psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${UPGRADE_DB}" >/dev/null <<'SQL'
INSERT INTO tide.audit_events (
    id,
    actor_type,
    action,
    resource_type,
    outcome
)
VALUES (
    '28000000-0000-4000-8000-000000000001',
    'SYSTEM',
    'migration-0028-populated-guard',
    'migration_test',
    'SUCCESS'
);
SQL
set +e
unused_populated_guard_output="$(
  TIDE_MIGRATION_DATABASE_URL="postgresql:///${UPGRADE_DB}" \
  TIDE_MIGRATION_EXPECTED_DATABASE="${UPGRADE_DB}" \
  TIDE_MIGRATION_TARGET="0029_remove_unused_tide_objects" \
  TIDE_MIGRATION_TEST_MODE="true" \
    bash "${DB_DIR}/scripts/apply-production.sh" 2>&1
)"
unused_populated_guard_status=$?
set -e
unused_populated_guard_state="$(psql -X --no-password -AtF '|' \
  "postgresql:///${UPGRADE_DB}" <<'SQL'
SELECT
    (SELECT count(*) FROM tide.audit_events) = 1,
    to_regclass('tide.analytics_actor_task_journey_v1') IS NOT NULL,
    NOT EXISTS (
        SELECT 1
        FROM tide.schema_migrations
        WHERE migration_id = '0029_remove_unused_tide_objects'
    );
SQL
)"
if [[ "${unused_populated_guard_status}" == "0" \
      || "${unused_populated_guard_output}" != *"populated unused tide tables: audit_events"* \
      || "${unused_populated_guard_state}" != "t|t|t" ]]; then
  echo "0029 未原子阻断非空废弃表：${unused_populated_guard_state}" >&2
  echo "${unused_populated_guard_output}" >&2
  exit 1
fi
psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${UPGRADE_DB}" \
  -c "DELETE FROM tide.audit_events WHERE id = '28000000-0000-4000-8000-000000000001'" >/dev/null

upgrade_unused_view_definitions="$(psql -X --no-password -Atqc "
  select string_agg(
    view_name || ':' || pg_get_viewdef(format('tide.%I', view_name)::regclass, true),
    E'\\n' order by view_name
  )
  from unnest(array[
    'analytics_actor_task_journey_v1',
    'analytics_task_assignment_funnel_v1',
    'analytics_task_funnel_v1',
    'analytics_task_step_funnel_v1',
    'analytics_content_quality_v1'
  ]::text[]) expected(view_name)
" "postgresql:///${UPGRADE_DB}")"
upgrade_unused_table_signature="$(psql -X --no-password -Atqc "
  with target(table_name) as (
    values
      ('outcome_projections'),
      ('camp_enrollment_projections'),
      ('audit_events'),
      ('task_template_files'),
      ('file_migrations'),
      ('teacher_photo_runs')
  ), signature_parts as (
    select
      'column' as kind,
      columns.table_name,
      columns.column_name || ':' || columns.udt_name || ':' ||
        columns.is_nullable || ':' ||
        coalesce(columns.column_default, '') as definition
    from information_schema.columns columns
    join target using (table_name)
    where columns.table_schema = 'tide'
    union all
    select
      'constraint', target.table_name, constraint_row.conname || ':' ||
        pg_get_constraintdef(constraint_row.oid, true)
    from target
    join pg_class relation
      on relation.oid = format('tide.%I', target.table_name)::regclass
    join pg_constraint constraint_row
      on constraint_row.conrelid = relation.oid
    union all
    select 'index', indexes.tablename, indexes.indexname || ':' || indexes.indexdef
    from pg_indexes indexes
    join target on target.table_name = indexes.tablename
    where indexes.schemaname = 'tide'
  )
  select md5(string_agg(
    kind || ':' || table_name || ':' || definition,
    E'\\n' order by kind, table_name, definition
  ))
  from signature_parts
" "postgresql:///${UPGRADE_DB}")"

TIDE_MIGRATION_DATABASE_URL="postgresql:///${UPGRADE_DB}" \
TIDE_MIGRATION_EXPECTED_DATABASE="${UPGRADE_DB}" \
TIDE_MIGRATION_TARGET="0029_remove_unused_tide_objects" \
TIDE_MIGRATION_TEST_MODE="true" \
  bash "${DB_DIR}/scripts/apply-production.sh" >/dev/null

unused_cleanup_state="$(psql -X --no-password -AtF '|' \
  "postgresql:///${UPGRADE_DB}" <<'SQL'
SELECT
    EXISTS (
        SELECT 1
        FROM tide.schema_migrations
        WHERE migration_id = '0029_remove_unused_tide_objects'
    ),
    to_regclass('tide.outcome_projections') IS NULL
        AND to_regclass('tide.camp_enrollment_projections') IS NULL
        AND to_regclass('tide.audit_events') IS NULL
        AND to_regclass('tide.task_template_files') IS NULL
        AND to_regclass('tide.file_migrations') IS NULL
        AND to_regclass('tide.teacher_photo_runs') IS NULL,
    to_regclass('tide.analytics_actor_task_journey_v1') IS NULL
        AND to_regclass('tide.analytics_task_assignment_funnel_v1') IS NULL
        AND to_regclass('tide.analytics_task_funnel_v1') IS NULL
        AND to_regclass('tide.analytics_task_step_funnel_v1') IS NULL
        AND to_regclass('tide.analytics_content_quality_v1') IS NULL,
    to_regclass('tide.analytics_actor_task_journey_v2') IS NOT NULL,
    to_regclass('tide.analytics_task_assignment_funnel_v2') IS NOT NULL;
SQL
)"
if [[ "${unused_cleanup_state}" != "t|t|t|t|t" ]]; then
  echo "0029 未完整清理无用 tide 对象：${unused_cleanup_state}" >&2
  exit 1
fi

upgrade_file_visibility_signature="$(psql -X --no-password -Atqc "
  select concat_ws('|',
    columns.column_name,
    columns.udt_name,
    columns.is_nullable,
    columns.column_default,
    pg_get_constraintdef(constraint_row.oid, true)
  )
  from information_schema.columns columns
  join pg_constraint constraint_row
    on constraint_row.conrelid = 'tide.file_objects'::regclass
   and constraint_row.conname = 'file_objects_visibility_check'
  where columns.table_schema = 'tide'
    and columns.table_name = 'file_objects'
    and columns.column_name = 'visibility'
" "postgresql:///${UPGRADE_DB}")"
upgrade_orphan_function_definition="$(psql -X --no-password -Atqc \
  "select pg_get_functiondef('tide.enforce_outbox_target()'::regprocedure)" \
  "postgresql:///${UPGRADE_DB}")"
if [[ -z "${upgrade_file_visibility_signature}" \
      || -z "${upgrade_orphan_function_definition}" ]]; then
  echo "0029 升级前置结构缺失。" >&2
  exit 1
fi

psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${UPGRADE_DB}" >/dev/null <<'SQL'
INSERT INTO tide.file_objects (
    id,
    storage_provider,
    object_key,
    original_filename,
    mime_type,
    size_bytes,
    sha256,
    visibility,
    status
)
VALUES (
    '29000000-0000-4000-8000-000000000001',
    'LOCAL',
    'migration-0029-public-asset',
    'migration-0029-public-asset.jpg',
    'image/jpeg',
    1,
    repeat('a', 64),
    'PUBLIC_ASSET',
    'READY'
);
SQL
set +e
non_private_visibility_output="$(
  TIDE_MIGRATION_DATABASE_URL="postgresql:///${UPGRADE_DB}" \
  TIDE_MIGRATION_EXPECTED_DATABASE="${UPGRADE_DB}" \
  TIDE_MIGRATION_TARGET="0030_remove_unused_columns_and_orphan_function" \
  TIDE_MIGRATION_TEST_MODE="true" \
    bash "${DB_DIR}/scripts/apply-production.sh" 2>&1
)"
non_private_visibility_status=$?
set -e
non_private_visibility_state="$(psql -X --no-password -AtF '|' \
  "postgresql:///${UPGRADE_DB}" <<'SQL'
SELECT
    EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'tide'
          AND table_name = 'file_objects'
          AND column_name = 'visibility'
    ),
    to_regprocedure('tide.enforce_outbox_target()') IS NOT NULL,
    NOT EXISTS (
        SELECT 1
        FROM tide.schema_migrations
        WHERE migration_id = '0030_remove_unused_columns_and_orphan_function'
    );
SQL
)"
if [[ "${non_private_visibility_status}" == "0" \
      || "${non_private_visibility_output}" != *"with non-PRIVATE rows"* \
      || "${non_private_visibility_state}" != "t|t|t" ]]; then
  echo "0030 未原子阻断非私有文件：${non_private_visibility_state}" >&2
  echo "${non_private_visibility_output}" >&2
  exit 1
fi
psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${UPGRADE_DB}" \
  -c "DELETE FROM tide.file_objects WHERE id = '29000000-0000-4000-8000-000000000001'" >/dev/null

psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${UPGRADE_DB}" \
  -c "CREATE VIEW tide.test_0030_visibility_dependency AS SELECT visibility FROM tide.file_objects" >/dev/null
set +e
visibility_dependency_output="$(
  TIDE_MIGRATION_DATABASE_URL="postgresql:///${UPGRADE_DB}" \
  TIDE_MIGRATION_EXPECTED_DATABASE="${UPGRADE_DB}" \
  TIDE_MIGRATION_TARGET="0030_remove_unused_columns_and_orphan_function" \
  TIDE_MIGRATION_TEST_MODE="true" \
    bash "${DB_DIR}/scripts/apply-production.sh" 2>&1
)"
visibility_dependency_status=$?
set -e
if [[ "${visibility_dependency_status}" == "0" \
      || "${visibility_dependency_output}" != *"visibility with external dependencies"* ]]; then
  echo "0030 未阻断 visibility 外部依赖。" >&2
  echo "${visibility_dependency_output}" >&2
  exit 1
fi
psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${UPGRADE_DB}" \
  -c "DROP VIEW tide.test_0030_visibility_dependency" >/dev/null

psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${UPGRADE_DB}" >/dev/null <<'SQL'
CREATE TABLE tide.test_0030_outbox_target_dependency (
    integration_event_id uuid,
    target_system text
);
CREATE TRIGGER test_0030_outbox_target_guard
BEFORE INSERT OR UPDATE OF integration_event_id, target_system
ON tide.test_0030_outbox_target_dependency
FOR EACH ROW
EXECUTE FUNCTION tide.enforce_outbox_target();
SQL
set +e
orphan_function_dependency_output="$(
  TIDE_MIGRATION_DATABASE_URL="postgresql:///${UPGRADE_DB}" \
  TIDE_MIGRATION_EXPECTED_DATABASE="${UPGRADE_DB}" \
  TIDE_MIGRATION_TARGET="0030_remove_unused_columns_and_orphan_function" \
  TIDE_MIGRATION_TEST_MODE="true" \
    bash "${DB_DIR}/scripts/apply-production.sh" 2>&1
)"
orphan_function_dependency_status=$?
set -e
if [[ "${orphan_function_dependency_status}" == "0" \
      || "${orphan_function_dependency_output}" != *"enforce_outbox_target() with dependent objects"* ]]; then
  echo "0030 未阻断孤儿函数新增消费者。" >&2
  echo "${orphan_function_dependency_output}" >&2
  exit 1
fi
psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${UPGRADE_DB}" >/dev/null <<'SQL'
DROP TRIGGER test_0030_outbox_target_guard
ON tide.test_0030_outbox_target_dependency;
DROP TABLE tide.test_0030_outbox_target_dependency;
SQL

TIDE_MIGRATION_DATABASE_URL="postgresql:///${UPGRADE_DB}" \
TIDE_MIGRATION_EXPECTED_DATABASE="${UPGRADE_DB}" \
TIDE_MIGRATION_TARGET="0032_first_login_onboarding" \
TIDE_MIGRATION_TEST_MODE="true" \
  bash "${DB_DIR}/scripts/apply-production.sh" >/dev/null

run_personalized_environment_upgrade_tests() {
set_public_head "${UPGRADE_DB}" "20260811_56_p_fb_negative_copy"

psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${UPGRADE_DB}" >/dev/null <<'SQL'
DO $optional_retired_g00$
BEGIN
    IF (
        SELECT count(*)
        FROM tide.task_execution_versions
        WHERE shared_template_row_id = 'G05:v1'
          AND task_code = 'G00'
          AND status = 'RETIRED'
    ) <> 1 THEN
        RAISE EXCEPTION 'optional retired G00 execution fixture is not exact';
    END IF;

    DELETE FROM tide.task_execution_versions
    WHERE shared_template_row_id = 'G05:v1'
      AND task_code = 'G00'
      AND status = 'RETIRED';
END
$optional_retired_g00$;

INSERT INTO public.task_templates (
    row_id, template_id, template_version, status, revision, output_type,
    execution_owner, external_task_template_code, source_mode, payload,
    created_by, updated_by, integration_mode
) VALUES (
    'P-FB-NEGATIVE:v1',
    'P-FB-NEGATIVE',
    1,
    'PUBLISHED',
    55,
    'TEACHER_TASK',
    'TEACHER_APP',
    'TIT.P.FB.NEGATIVE',
    'REAL',
    '{"template_id":"P-FB-NEGATIVE","output_type":"TEACHER_TASK","audience":"TEACHER","owner":"TIT_GROWTH_OPS","execution_owner":"TEACHER_APP","integration_mode":"OUTBOUND_MANAGED","category":"PERSONALIZED_IMPROVEMENT","dimension":"USER_FEEDBACK","stage":"TRIGGERED","ops_name_zh":"差评改善","content_locale":"en","content_status":"READY","title":"Feedback Improvement","why_template":"The same negative-feedback signal has appeared more than once for this teacher.","how_summary":"Complete the configured improvement activity for the feedback issue shown in the task reason. Depending on the assigned activity, you may need to submit a teaching-environment photo for review or complete another guided action.","completion_standard":"The teacher app marks the task as completed after every requirement for the assigned improvement activity, including any required photo review, is satisfied.","benefit":"This task carries no points. It targets a repeated learner-feedback issue.","priority":"P1","score_type":"ZERO","score_value":0,"source_mode":"REAL"}'::jsonb,
    'production_migrator_test',
    'production_migrator_test',
    'OUTBOUND_MANAGED'
);

INSERT INTO tide.task_execution_versions (
    id, shared_template_row_id, task_code, execution_contract_version,
    config, status
) VALUES (
    '25abcdef-0000-4000-8000-000000000011',
    'P-FB-NEGATIVE:v1',
    'P-FB-NEGATIVE',
    'task-contract-v3',
    '{"estimatedMinutes":8,"allowRetry":true,"contentStatus":"PENDING","contentVersion":"2026-08-06","pendingReason":"JIAHE_PERSONALIZED_CONTENT_PENDING"}'::jsonb,
    'ACTIVE'
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
VALUES
(
    'MIGRATION-P-FB-NEGATIVE',
    'MIGRATION-SEMANTIC-TEACHER',
    'P-FB-NEGATIVE',
    'P-FB-NEGATIVE:v1',
    'PERSONALIZED_IMPROVEMENT',
    'TRIGGER_CENTER',
    'ASSIGNED',
    'P1',
    '[Verify] Preserve personalized assignment identity and progress.',
    'MOCK',
    'personalized:MIGRATION-SEMANTIC-TEACHER:P-FB-NEGATIVE'
),
(
    'MIGRATION-P-FB-STARTED',
    'MIGRATION-SEMANTIC-TEACHER',
    'P-FB-NEGATIVE',
    'P-FB-NEGATIVE:v1',
    'PERSONALIZED_IMPROVEMENT',
    'TRIGGER_CENTER',
    'ASSIGNED',
    'P1',
    '[Verify] Started assignment must protect photo definitions on down.',
    'MOCK',
    'personalized:MIGRATION-SEMANTIC-TEACHER:P-FB-STARTED'
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
    '25abcdef-0000-4000-8000-000000000220',
    'MIGRATION-P-FB-NEGATIVE',
    'p-fb-negative-environment-photo',
    'IN_PROGRESS',
    60,
    '{"checkpoint":"before-0038"}'::jsonb,
    '2026-08-10 00:00:00+00'
);
SQL

psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${UPGRADE_DB}" >/dev/null <<'SQL'
INSERT INTO tide.task_step_definitions (
    id, execution_version_id, step_key, position, step_type, title, config
) VALUES (
    '25abcdef-0000-4000-8000-000000000299',
    '25abcdef-0000-4000-8000-000000000011',
    'p-fb-negative-unreviewed-step',
    1,
    'CUSTOM',
    '[Verify] Unknown personalized step must fail closed',
    '{}'::jsonb
);
SQL

set +e
personalized_guard_output="$(
  psql -X --no-password -v ON_ERROR_STOP=1 \
    "postgresql:///${UPGRADE_DB}" \
    -f "${DB_DIR}/migrations/0038_personalized_environment_photo.up.sql" 2>&1
)"
personalized_guard_status=$?
set -e
personalized_guard_state="$(psql -X --no-password -AtF '|' \
  "postgresql:///${UPGRADE_DB}" <<'SQL'
SELECT
    EXISTS (
        SELECT 1 FROM tide.task_step_definitions
        WHERE step_key = 'p-fb-negative-unreviewed-step'
    ),
    EXISTS (
        SELECT 1 FROM tide.task_execution_versions
        WHERE shared_template_row_id = 'P-FB-NEGATIVE:v1'
          AND config->>'contentVersion' = '2026-08-06'
    ),
    NOT EXISTS (
        SELECT 1 FROM tide.task_step_definitions
        WHERE step_key = 'p-fb-negative-environment-photo'
    );
SQL
)"
if [[ "${personalized_guard_status}" == "0" \
      || "${personalized_guard_output}" != *"unreviewed step"* \
      || "${personalized_guard_state}" != "t|t|t" ]]; then
  echo "0038 未原子拒绝未评审 P-FB-NEGATIVE step：${personalized_guard_state}" >&2
  echo "${personalized_guard_output}" >&2
  exit 1
fi
psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${UPGRADE_DB}" \
  -c "DELETE FROM tide.task_step_definitions WHERE step_key = 'p-fb-negative-unreviewed-step'" >/dev/null

TIDE_MIGRATION_DATABASE_URL="postgresql:///${UPGRADE_DB}" \
TIDE_MIGRATION_EXPECTED_DATABASE="${UPGRADE_DB}" \
TIDE_MIGRATION_TARGET="0038_personalized_environment_photo" \
TIDE_MIGRATION_TEST_MODE="true" \
  bash "${DB_DIR}/scripts/apply-production.sh" >/dev/null

personalized_photo_state="$(psql -X --no-password -AtF '|' \
  "postgresql:///${UPGRADE_DB}" <<'SQL'
SELECT
    EXISTS (
        SELECT 1
        FROM tide.task_execution_versions execution
        WHERE execution.id = '25abcdef-0000-4000-8000-000000000011'
          AND execution.shared_template_row_id = 'P-FB-NEGATIVE:v1'
          AND execution.task_code = 'P-FB-NEGATIVE'
          AND execution.execution_contract_version = 'task-contract-v3'
          AND execution.config =
            '{"estimatedMinutes":8,"allowRetry":true,"contentStatus":"PENDING","contentVersion":"2026-08-11-personalized-environment-photo-v1","pendingReason":"JIAHE_PERSONALIZED_CONTENT_PENDING","independentModules":{"stepKeys":["p-fb-negative-environment-photo"],"allowOutOfOrderProgress":true,"keepAssignmentInProgressUntilPassed":true}}'::jsonb
    ),
    EXISTS (
        SELECT 1
        FROM tide.task_step_definitions definition
        WHERE definition.id = '8911e60c-012d-4e01-8ecc-1cb10de35c83'
          AND definition.execution_version_id =
            '25abcdef-0000-4000-8000-000000000011'
          AND definition.step_key = 'p-fb-negative-environment-photo'
          AND definition.step_type = 'UPLOAD'
          AND definition.config->>'reviewProfile' = 'TEACHING_ENVIRONMENT_V1'
    ),
    (
        SELECT count(*) = 2
        FROM tide.task_validation_rules rule
        WHERE rule.execution_version_id =
          '25abcdef-0000-4000-8000-000000000011'
    ),
    EXISTS (
        SELECT 1
        FROM tide.task_validation_rules rule
        WHERE rule.id = 'b823d749-969d-4194-88d7-9ff8de9d2c51'
          AND rule.execution_version_id =
            '25abcdef-0000-4000-8000-000000000011'
          AND rule.rule_key = 'p-fb-negative-environment-ai-review'
          AND rule.config->>'reviewProfile' = 'TEACHING_ENVIRONMENT_V1'
          AND rule.config->'criteriaKeys' =
            '["camera_angle","lighting","background","dressing"]'::jsonb
    ),
    EXISTS (
        SELECT 1
        FROM public.task_assignments assignment
        WHERE assignment.assignment_id = 'MIGRATION-P-FB-NEGATIVE'
          AND assignment.teacher_id = 'MIGRATION-SEMANTIC-TEACHER'
          AND assignment.task_code = 'P-FB-NEGATIVE'
          AND assignment.template_version_id = 'P-FB-NEGATIVE:v1'
          AND assignment.status = 'ASSIGNED'
          AND assignment.row_version = 1
    ),
    EXISTS (
        SELECT 1
        FROM public.task_assignments assignment
        WHERE assignment.assignment_id = 'MIGRATION-P-FB-STARTED'
          AND assignment.teacher_id = 'MIGRATION-SEMANTIC-TEACHER'
          AND assignment.task_code = 'P-FB-NEGATIVE'
          AND assignment.template_version_id = 'P-FB-NEGATIVE:v1'
          AND assignment.status = 'ASSIGNED'
          AND assignment.row_version = 1
    ),
    EXISTS (
        SELECT 1
        FROM tide.task_step_progress progress
        WHERE progress.id = '25abcdef-0000-4000-8000-000000000220'
          AND progress.task_assignment_id = 'MIGRATION-P-FB-NEGATIVE'
          AND progress.step_key = 'p-fb-negative-environment-photo'
          AND progress.status = 'IN_PROGRESS'
          AND progress.percent = 60
          AND progress.progress_summary =
              '{"checkpoint":"before-0038"}'::jsonb
    ),
    EXISTS (
        SELECT 1 FROM tide.schema_migrations
        WHERE migration_id = '0038_personalized_environment_photo'
          AND migration_order = 33
    );
SQL
)"
if [[ "${personalized_photo_state}" != "t|t|t|t|t|t|t|t" ]]; then
  echo "0038 P-FB-NEGATIVE 原位升级异常：${personalized_photo_state}" >&2
  exit 1
fi
}

if command -v sha256sum >/dev/null 2>&1; then
  expected_0030_sha="$(sha256sum "${DB_DIR}/migrations/0030_remove_unused_columns_and_orphan_function.up.sql" | awk '{print $1}')"
else
  expected_0030_sha="$(shasum -a 256 "${DB_DIR}/migrations/0030_remove_unused_columns_and_orphan_function.up.sql" | awk '{print $1}')"
fi
cleanup_0030_state="$(psql -X --no-password -AtF '|' \
  "postgresql:///${UPGRADE_DB}" <<'SQL'
SELECT
    NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'tide'
          AND table_name = 'file_objects'
          AND column_name = 'visibility'
    ),
    to_regprocedure('tide.enforce_outbox_target()') IS NULL,
    migration_order,
    filename,
    sha256
FROM tide.schema_migrations
WHERE migration_id = '0030_remove_unused_columns_and_orphan_function';
SQL
)"
if [[ "${cleanup_0030_state}" != "t|t|28|0030_remove_unused_columns_and_orphan_function.up.sql|${expected_0030_sha}" ]]; then
  echo "0030 清理或生产账本异常：${cleanup_0030_state}" >&2
  exit 1
fi

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
    ) = 9,
    EXISTS (
        SELECT 1
        FROM tide.task_execution_versions
        WHERE shared_template_row_id = 'G05:v1'
          AND task_code = 'G00'
          AND status = 'RETIRED'
    ),
    NOT EXISTS (
        SELECT 1
        FROM tide.task_execution_versions
        WHERE task_code LIKE 'TMP-0025-%'
    ),
    (
        SELECT count(*) = 3
        FROM tide.task_step_definitions definition
        WHERE definition.execution_version_id =
              '25abcdef-0000-4000-8000-000000000002'::uuid
    ) AND EXISTS (
        SELECT 1
        FROM tide.task_step_definitions definition
        WHERE definition.id =
              '25abcdef-0000-4000-8000-000000000102'::uuid
          AND definition.execution_version_id =
              '25abcdef-0000-4000-8000-000000000002'::uuid
          AND definition.step_key = 'g02-device-check'
          AND definition.position = 1
          AND definition.config =
              '{"version":"g02-device-2026-08-05-browser-preflight-v1","role":"DEVICE_CHECK","items":["camera","microphone","network"]}'::jsonb
    ) AND EXISTS (
        SELECT 1
        FROM tide.task_step_definitions definition
        WHERE definition.id =
              '25abcdef-0000-4000-8000-000000000101'::uuid
          AND definition.step_key = 'g02-courseware-confirmation'
          AND definition.position = 2
          AND definition.config->>'version' =
              'g02-courseware-2026-08-05-guidance-v1'
    ) AND EXISTS (
        SELECT 1
        FROM tide.task_step_definitions definition
        WHERE definition.id =
              '25abcdef-0000-4000-8000-000000000103'::uuid
          AND definition.step_key = 'g02-environment-photo'
          AND definition.position = 3
    ),
    EXISTS (
        SELECT 1
        FROM tide.task_execution_versions execution
        WHERE execution.id =
              '25abcdef-0000-4000-8000-000000000002'::uuid
          AND execution.execution_contract_version = 'task-contract-v3'
          AND execution.config =
              '{"estimatedMinutes":15,"allowRetry":true,"contentStatus":"READY","contentVersion":"2026-08-05-g04-three-part","pendingReason":null,"independentModules":{"stepKeys":["g02-device-check","g02-environment-photo","g02-courseware-confirmation"],"allowOutOfOrderProgress":true,"keepAssignmentInProgressUntilPassed":true}}'::jsonb
    ),
    EXISTS (
        SELECT 1
        FROM tide.task_validation_rules rule
        WHERE rule.id =
              '25abcdef-0000-4000-8000-000000000111'::uuid
          AND rule.execution_version_id =
              '25abcdef-0000-4000-8000-000000000002'::uuid
          AND rule.rule_key = 'all-steps-complete'
          AND rule.position = 1
          AND rule.rule_version = '2026-08-05-g04-three-part-v1'
          AND rule.config =
              '{"requiredStepKeys":["g02-device-check","g02-environment-photo","g02-courseware-confirmation"]}'::jsonb
    ),
    EXISTS (
        SELECT 1
        FROM tide.task_execution_versions execution
        WHERE execution.id =
              '25abcdef-0000-4000-8000-000000000001'::uuid
          AND execution.shared_template_row_id = 'G01:v1'
          AND execution.task_code = 'G01'
          AND execution.execution_contract_version = 'v1'
          AND execution.config = '{"migration_test":true}'::jsonb
          AND execution.status = 'ACTIVE'
          AND EXISTS (
              SELECT 1
              FROM tide.task_step_definitions definition
              WHERE definition.id =
                    '25abcdef-0000-4000-8000-000000000100'::uuid
                AND definition.execution_version_id = execution.id
                AND definition.step_key = 'g01-essay-confirmation'
                AND definition.position = 1
                AND definition.step_type = 'CHECKLIST'
                AND definition.config =
                    '{"role":"TESOL_ESSAY","migration_test":true}'::jsonb
          )
          AND EXISTS (
              SELECT 1
              FROM tide.task_validation_rules rule
              WHERE rule.id =
                    '25abcdef-0000-4000-8000-000000000110'::uuid
                AND rule.execution_version_id = execution.id
                AND rule.rule_key = 'all-steps-complete'
                AND rule.rule_version = '2026-07-22'
                AND rule.teacher_failure_copy =
                    '请确认 Essay，并提交完成证明。'
          )
          AND EXISTS (
              SELECT 1
              FROM tide.task_validation_rules rule
              WHERE rule.id =
                    '25abcdef-0000-4000-8000-000000000113'::uuid
                AND rule.execution_version_id = execution.id
                AND rule.rule_key = 'g01-external-status'
                AND rule.rule_type = 'G01_EXTERNAL_STATUS'
                AND rule.rule_version = '2026-07-22'
                AND rule.position = 3
                AND rule.config = '{}'::jsonb
                AND rule.teacher_failure_copy =
                    'Self-intro 和 TESOL 真实状态尚未全部通过。'
          )
          AND EXISTS (
              SELECT 1
              FROM public.task_assignments assignment
              WHERE assignment.assignment_id = 'MIGRATION-SEMANTIC-G01'
                AND assignment.teacher_id = 'MIGRATION-SEMANTIC-TEACHER'
                AND assignment.task_code = 'G01'
                AND assignment.template_version_id = 'G01:v1'
                AND assignment.status = 'ASSIGNED'
                AND assignment.row_version = 1
          )
          AND EXISTS (
              SELECT 1
              FROM tide.task_step_progress progress
              WHERE progress.id =
                    '25abcdef-0000-4000-8000-000000000201'::uuid
                AND progress.task_assignment_id =
                    'MIGRATION-SEMANTIC-G01'
                AND progress.step_key = 'g01-essay-confirmation'
                AND progress.status = 'IN_PROGRESS'
                AND progress.percent = 50
                AND progress.progress_summary =
                    '{"checkpoint":"before-0033"}'::jsonb
          )
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
        WHERE migration_id IN (
            '0025_fixed_task_semantic_alignment',
            '0031_g04_independent_sections',
            '0032_first_login_onboarding'
        )
    ) = 3;
SQL
)"
if [[ "${semantic_alignment_state}" != "t|t|t|t|t|t|t|t|t|t" ]]; then
  echo "0025/0031/0032 未保留 G01/G04 执行、assignment 或 progress 身份：${semantic_alignment_state}" >&2
  exit 1
fi

onboarding_migration_state="$(psql -X --no-password -AtF '|' "postgresql:///${UPGRADE_DB}" <<'SQL'
SELECT
    to_regclass('tide.account_onboarding_states') IS NOT NULL,
    (
        SELECT count(*) = 1
        FROM tide.account_onboarding_states
    ),
    EXISTS (
        SELECT 1
        FROM tide.account_onboarding_states
        WHERE account_id = '27000000-0000-4000-8000-000000000001'
          AND guide_code = 'FIRST_LOGIN'
          AND guide_version = 1
          AND status = 'MIGRATED_EXISTING'
          AND idempotency_key = 'migration:0032:first-login:v1'
          AND request_hash IS NULL
          AND acknowledged_at = '2026-07-10 01:02:03+00'
    ),
    NOT EXISTS (
        SELECT 1
        FROM tide.account_onboarding_states
        WHERE account_id IN (
            '27000000-0000-4000-8000-000000000002',
            '27000000-0000-4000-8000-000000000003'
        )
    ),
    EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'tide.account_onboarding_states'::regclass
          AND conname = 'account_onboarding_states_idempotency_key_check'
          AND contype = 'c'
    );
SQL
)"
if [[ "${onboarding_migration_state}" != "t|t|t|t|t" ]]; then
  echo "0032 存量登录回填或未登录保留异常：${onboarding_migration_state}" >&2
  exit 1
fi

psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${UPGRADE_DB}" >/dev/null <<'SQL'
DO $onboarding_constraints$
DECLARE
    null_hash_rejected boolean := false;
    short_key_rejected boolean := false;
BEGIN
    BEGIN
        INSERT INTO tide.account_onboarding_states (
            account_id,
            guide_code,
            guide_version,
            status,
            idempotency_key,
            request_hash,
            acknowledged_at
        ) VALUES (
            '27000000-0000-4000-8000-000000000002',
            'FIRST_LOGIN',
            1,
            'COMPLETED',
            'complete-null-hash',
            NULL,
            now()
        );
    EXCEPTION WHEN check_violation THEN
        null_hash_rejected := true;
    END;

    BEGIN
        INSERT INTO tide.account_onboarding_states (
            account_id,
            guide_code,
            guide_version,
            status,
            idempotency_key,
            request_hash,
            acknowledged_at
        ) VALUES (
            '27000000-0000-4000-8000-000000000002',
            'FIRST_LOGIN',
            1,
            'SKIPPED',
            '1234567',
            repeat('a', 64),
            now()
        );
    EXCEPTION WHEN check_violation THEN
        short_key_rejected := true;
    END;

    IF NOT null_hash_rejected OR NOT short_key_rejected THEN
        RAISE EXCEPTION
            '0032 onboarding request hash or idempotency key constraint is incomplete';
    END IF;
END
$onboarding_constraints$;
SQL

TIDE_MIGRATION_DATABASE_URL="postgresql:///${UPGRADE_DB}" \
TIDE_MIGRATION_EXPECTED_DATABASE="${UPGRADE_DB}" \
TIDE_MIGRATION_TARGET="0032_first_login_onboarding" \
TIDE_MIGRATION_TEST_MODE="true" \
  bash "${DB_DIR}/scripts/apply-production.sh" >/dev/null

onboarding_idempotent_state="$(psql -X --no-password -AtF '|' "postgresql:///${UPGRADE_DB}" <<'SQL'
SELECT
    count(*) = 30,
    count(*) FILTER (
        WHERE migration_id = '0032_first_login_onboarding'
    ) = 1,
    count(*) FILTER (
        WHERE migration_id = '0033_g01_tesol_only'
    ) = 0,
    (SELECT count(*) FROM tide.account_onboarding_states) = 1
FROM tide.schema_migrations;
SQL
)"
if [[ "${onboarding_idempotent_state}" != "t|t|t|t" ]]; then
  echo "0032 生产迁移链重跑不幂等：${onboarding_idempotent_state}" >&2
  exit 1
fi

advance_public_g04_to_rev54 "${UPGRADE_DB}"

psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${UPGRADE_DB}" >/dev/null <<'SQL'
INSERT INTO tide.task_attempts (
    id, task_assignment_id, attempt_no, status, started_at
) VALUES (
    '37000000-0000-4000-8000-000000000001',
    'MIGRATION-SEMANTIC-G04',
    1,
    'IN_PROGRESS',
    '2026-08-10 00:00:00+00'
);

INSERT INTO tide.device_check_runs (
    id, task_attempt_id, step_key, check_version, status,
    started_at, finished_at
) VALUES (
    '37000000-0000-4000-8000-000000000002',
    '37000000-0000-4000-8000-000000000001',
    'g02-device-check',
    'g02-device-2026-08-05-browser-preflight-v1',
    'PASSED',
    '2026-08-10 00:01:00+00',
    '2026-08-10 00:02:00+00'
);

INSERT INTO tide.device_check_item_results (
    id, device_check_run_id, item_key, status, measured_summary,
    teacher_message
) VALUES (
    '37000000-0000-4000-8000-000000000003',
    '37000000-0000-4000-8000-000000000002',
    'camera',
    'PASSED',
    '{"source":"BROWSER_LOCAL"}'::jsonb,
    NULL
);

UPDATE tide.task_execution_versions
SET config =
    '{"estimatedMinutes":15,"allowRetry":true,"contentStatus":"READY","contentVersion":"2026-08-11-g04-two-part","pendingReason":null,"independentModules":{"stepKeys":["g02-environment-photo","g02-courseware-confirmation"],"allowOutOfOrderProgress":true,"keepAssignmentInProgressUntilPassed":true}}'::jsonb
WHERE shared_template_row_id = 'G02:v1';
SQL

set +e
g04_mixed_shape_output="$(
  psql -X --no-password -v ON_ERROR_STOP=1 \
    "postgresql:///${UPGRADE_DB}" \
    -f "${DB_DIR}/migrations/0037_g04_remove_device_check.up.sql" 2>&1
)"
g04_mixed_shape_status=$?
set -e
g04_mixed_shape_state="$(psql -X --no-password -AtF '|' \
  "postgresql:///${UPGRADE_DB}" <<'SQL'
SELECT
    EXISTS (
        SELECT 1
        FROM tide.task_step_definitions definition
        JOIN tide.task_execution_versions execution
          ON execution.id = definition.execution_version_id
        WHERE execution.shared_template_row_id = 'G02:v1'
          AND definition.step_key = 'g02-device-check'
    ),
    EXISTS (
        SELECT 1
        FROM tide.device_check_runs
        WHERE id = '37000000-0000-4000-8000-000000000002'
    ),
    NOT EXISTS (
        SELECT 1
        FROM tide.schema_migrations
        WHERE migration_id = '0037_g04_remove_device_check'
    );
SQL
)"
if [[ "${g04_mixed_shape_status}" == "0" \
      || "${g04_mixed_shape_output}" != *"unreviewed mixed shape"* \
      || "${g04_mixed_shape_state}" != "t|t|t" ]]; then
  echo "0037 未原子拒绝 G04 execution/step/rule 混合形状：${g04_mixed_shape_state}" >&2
  echo "${g04_mixed_shape_output}" >&2
  exit 1
fi

psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${UPGRADE_DB}" >/dev/null <<'SQL'
UPDATE tide.task_execution_versions
SET config =
    '{"estimatedMinutes":15,"allowRetry":true,"contentStatus":"READY","contentVersion":"2026-08-05-g04-three-part","pendingReason":null,"independentModules":{"stepKeys":["g02-device-check","g02-environment-photo","g02-courseware-confirmation"],"allowOutOfOrderProgress":true,"keepAssignmentInProgressUntilPassed":true}}'::jsonb
WHERE shared_template_row_id = 'G02:v1';
SQL

TIDE_MIGRATION_DATABASE_URL="postgresql:///${UPGRADE_DB}" \
TIDE_MIGRATION_EXPECTED_DATABASE="${UPGRADE_DB}" \
TIDE_MIGRATION_TARGET="0037_g04_remove_device_check" \
TIDE_MIGRATION_TEST_MODE="true" \
  bash "${DB_DIR}/scripts/apply-production.sh" >/dev/null
TIDE_MIGRATION_DATABASE_URL="postgresql:///${UPGRADE_DB}" \
TIDE_MIGRATION_EXPECTED_DATABASE="${UPGRADE_DB}" \
TIDE_MIGRATION_TARGET="0037_g04_remove_device_check" \
TIDE_MIGRATION_TEST_MODE="true" \
  bash "${DB_DIR}/scripts/apply-production.sh" >/dev/null

g04_two_part_state="$(psql -X --no-password -AtF '|' \
  "postgresql:///${UPGRADE_DB}" <<'SQL'
SELECT
    EXISTS (
        SELECT 1
        FROM tide.task_execution_versions execution
        WHERE execution.id = '25abcdef-0000-4000-8000-000000000002'
          AND execution.shared_template_row_id = 'G02:v1'
          AND execution.task_code = 'G04'
          AND execution.config =
              '{"estimatedMinutes":15,"allowRetry":true,"contentStatus":"READY","contentVersion":"2026-08-11-g04-two-part","pendingReason":null,"independentModules":{"stepKeys":["g02-environment-photo","g02-courseware-confirmation"],"allowOutOfOrderProgress":true,"keepAssignmentInProgressUntilPassed":true}}'::jsonb
    ),
    (
        SELECT array_agg(definition.id ORDER BY definition.position) =
            ARRAY[
                '25abcdef-0000-4000-8000-000000000103'::uuid,
                '25abcdef-0000-4000-8000-000000000101'::uuid
            ]
        FROM tide.task_step_definitions definition
        WHERE definition.execution_version_id =
            '25abcdef-0000-4000-8000-000000000002'::uuid
    ),
    NOT EXISTS (
        SELECT 1
        FROM tide.task_step_definitions definition
        WHERE definition.execution_version_id =
              '25abcdef-0000-4000-8000-000000000002'::uuid
          AND definition.step_key = 'g02-device-check'
    ),
    EXISTS (
        SELECT 1
        FROM tide.task_validation_rules rule
        WHERE rule.id = '25abcdef-0000-4000-8000-000000000111'::uuid
          AND rule.rule_version = '2026-08-11-g04-two-part-v1'
          AND rule.config =
              '{"requiredStepKeys":["g02-environment-photo","g02-courseware-confirmation"]}'::jsonb
    ),
    EXISTS (
        SELECT 1
        FROM tide.task_validation_rules rule
        WHERE rule.id = '25abcdef-0000-4000-8000-000000000112'::uuid
          AND rule.rule_key = 'g02-environment-ai-review'
          AND rule.config->>'criteriaVersion' =
              'lesson-preparation-camera-view-2026-08-v7-background-veto'
    ),
    EXISTS (
        SELECT 1
        FROM public.task_assignments assignment
        WHERE assignment.assignment_id = 'MIGRATION-SEMANTIC-G04'
          AND assignment.status = 'IN_PROGRESS'
          AND assignment.row_version = 3
    ),
    EXISTS (
        SELECT 1
        FROM tide.task_step_progress progress
        WHERE progress.id = '25abcdef-0000-4000-8000-000000000202'::uuid
          AND progress.step_key = 'g02-device-check'
          AND progress.progress_summary =
              '{"checkpoint":"before-0025"}'::jsonb
    ),
    EXISTS (
        SELECT 1
        FROM tide.task_attempts attempt
        JOIN tide.device_check_runs run ON run.task_attempt_id = attempt.id
        JOIN tide.device_check_item_results item
          ON item.device_check_run_id = run.id
        WHERE attempt.id = '37000000-0000-4000-8000-000000000001'::uuid
          AND run.id = '37000000-0000-4000-8000-000000000002'::uuid
          AND item.id = '37000000-0000-4000-8000-000000000003'::uuid
          AND run.step_key = 'g02-device-check'
          AND item.item_key = 'camera'
    ),
    EXISTS (
        SELECT 1
        FROM public.task_templates template
        WHERE template.row_id = 'G02:v1'
          AND template.payload->>'ops_name_zh' = '首课准备'
          AND template.payload->>'title' = 'Lesson Preparation'
    ),
    count(*) = 32,
    count(*) FILTER (
        WHERE migration_id = '0033_g01_tesol_only'
    ) = 1,
    count(*) FILTER (
        WHERE migration_id = '0037_g04_remove_device_check'
    ) = 1,
    max(migration_order) FILTER (
        WHERE migration_id = '0037_g04_remove_device_check'
    ) = 32
FROM tide.schema_migrations;
SQL
)"
if [[ "${g04_two_part_state}" != "t|t|t|t|t|t|t|t|t|t|t|t|t" ]]; then
  echo "0037 未精确移除 G04 设备 step 或改动了历史证据：${g04_two_part_state}" >&2
  exit 1
fi

run_personalized_environment_upgrade_tests
install_public_g02_contract "${UPGRADE_DB}"

TIDE_MIGRATION_DATABASE_URL="postgresql:///${UPGRADE_DB}" \
TIDE_MIGRATION_EXPECTED_DATABASE="${UPGRADE_DB}" \
TIDE_MIGRATION_TARGET="0041_crm_sso_hybrid" \
TIDE_MIGRATION_TEST_MODE="true" \
  bash "${DB_DIR}/scripts/apply-production.sh" >/dev/null
crm_sso_migration_state="$(psql -X --no-password -AtF '|' \
  "postgresql:///${UPGRADE_DB}" <<'SQL'
SELECT
    to_regclass('tide.crm_sso_logins') IS NOT NULL,
    EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'tide'
          AND table_name = 'auth_sessions'
          AND column_name = 'auth_method'
    ),
    EXISTS (
        SELECT 1 FROM tide.schema_migrations
        WHERE migration_id = '0041_crm_sso_hybrid'
          AND migration_order = 36
    );
SQL
)"
if [[ "${crm_sso_migration_state}" != "t|t|t" ]]; then
  echo "0041 CRM SSO 结构或迁移账本异常：${crm_sso_migration_state}" >&2
  exit 1
fi
install_public_59_support_guard_and_acl "${UPGRADE_DB}"
install_public_g09_contract "${UPGRADE_DB}"
TIDE_MIGRATION_DATABASE_URL="postgresql:///${UPGRADE_DB}" \
TIDE_MIGRATION_EXPECTED_DATABASE="${UPGRADE_DB}" \
TIDE_MIGRATION_TARGET="0042_g09_set_kuozhi_course" \
TIDE_MIGRATION_TEST_MODE="true" \
  bash "${DB_DIR}/scripts/apply-production.sh" >/dev/null

g09_set_migration_state="$(psql -X --no-password -AtF '|' \
  "postgresql:///${UPGRADE_DB}" <<'SQL'
SELECT
    EXISTS (
        SELECT 1
        FROM tide.task_execution_versions
        WHERE shared_template_row_id = 'G10:v1'
          AND task_code = 'G09'
          AND execution_contract_version = 'task-contract-v3'
          AND config =
              '{"estimatedMinutes":25,"allowRetry":true,"contentStatus":"READY","contentVersion":"2026-08-19-set-kuozhi-v1","pendingReason":null}'::jsonb
    ),
    EXISTS (
        SELECT 1 FROM tide.schema_migrations
        WHERE migration_id = '0042_g09_set_kuozhi_course'
          AND migration_order = 37
    ),
    (SELECT version_num FROM public.alembic_version) =
        '20260819_65_g09_set_course';
SQL
)"
if [[ "${g09_set_migration_state}" != "t|t|t" ]]; then
  echo "0042 G09 SET 课程配置或迁移账本异常：${g09_set_migration_state}" >&2
  exit 1
fi
psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${UPGRADE_DB}" \
  -f "${DB_DIR}/scripts/grant-tit-teacher-crud.sql" >/dev/null

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
        FROM tide.analytics_actor_task_journey_v2
        WHERE task_assignment_id = 'MIGRATION-SEMANTIC-G04'
    ),
    (
        SELECT count(DISTINCT task_assignment_id)
        FROM tide.app_events
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
        FROM tide.analytics_actor_task_journey_v2
    ),
    to_regclass('tide.analytics_actor_task_journey_v1') IS NULL
        AND to_regclass('tide.analytics_task_assignment_funnel_v1') IS NULL
        AND to_regclass('tide.analytics_task_funnel_v1') IS NULL
        AND to_regclass('tide.analytics_task_step_funnel_v1') IS NULL
        AND to_regclass('tide.analytics_content_quality_v1') IS NULL;
SQL
)"
if [[ "${analytics_semantics_state}" != "t|t|t|t|t|t|t|t" ]]; then
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
    direct_update_rejected boolean := false;
    direct_delete_rejected boolean := false;
BEGIN
    BEGIN
        UPDATE public.teacher_support_tickets
        SET messages = '[]'::jsonb
        WHERE ticket_id = '42000000-0000-4000-8000-000000000001';
    EXCEPTION WHEN insufficient_privilege THEN
        direct_update_rejected := true;
    END;
    BEGIN
        DELETE FROM public.teacher_support_tickets
        WHERE ticket_id = '42000000-0000-4000-8000-000000000001';
    EXCEPTION WHEN insufficient_privilege THEN
        direct_delete_rejected := true;
    END;
    BEGIN
        PERFORM public.append_teacher_support_ticket_operator_message(
            '42000000-0000-4000-8000-000000000001',
            1,
            NULL
        );
    EXCEPTION WHEN invalid_parameter_value THEN
        null_message_rejected := true;
    END;

    IF NOT null_message_rejected
       OR NOT direct_update_rejected
       OR NOT direct_delete_rejected THEN
        RAISE EXCEPTION
            'operator validation or table-level support-ticket Trigger was bypassed';
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
    ),
    EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgrelid = 'public.teacher_support_tickets'::regclass
          AND tgname = 'guard_simple_support_ticket_write'
          AND NOT tgisinternal
    )
FROM public.teacher_support_tickets
WHERE ticket_id = '42000000-0000-4000-8000-000000000001';
SQL
)"
if [[ "${atomic_state}" != "WAITING_TEACHER|t|t|t|2|2|t|t|t|f|t|t|t|f|t" ]]; then
  echo "运营回复原子性、最终表级权限或 Trigger 异常：${atomic_state}" >&2
  exit 1
fi

psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${UPGRADE_DB}" \
  -f "${DB_DIR}/migrations/0041_crm_sso_hybrid.down.sql" >/dev/null
crm_sso_down_state="$(psql -X --no-password -Atqc "
  SELECT
    to_regclass('tide.crm_sso_logins') IS NULL
    AND NOT EXISTS (
      SELECT 1
      FROM information_schema.columns
      WHERE table_schema = 'tide'
        AND table_name = 'auth_sessions'
        AND column_name = 'auth_method'
    )
" "postgresql:///${UPGRADE_DB}")"
if [[ "${crm_sso_down_state}" != "t" ]]; then
  echo "0041 down 未恢复本地认证结构。" >&2
  exit 1
fi
psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${UPGRADE_DB}" \
  -f "${DB_DIR}/migrations/0041_crm_sso_hybrid.up.sql" >/dev/null
crm_sso_down_up_state="$(psql -X --no-password -Atqc "
  SELECT to_regclass('tide.crm_sso_logins') IS NOT NULL
" "postgresql:///${UPGRADE_DB}")"
if [[ "${crm_sso_down_up_state}" != "t" ]]; then
  echo "0041 down-up 未恢复 CRM SSO 结构。" >&2
  exit 1
fi
psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${UPGRADE_DB}" \
  -f "${DB_DIR}/migrations/0041_crm_sso_hybrid.down.sql" >/dev/null

set +e
personalized_evidence_down_output="$(
  psql -X --no-password -v ON_ERROR_STOP=1 \
    "postgresql:///${UPGRADE_DB}" \
    -f "${DB_DIR}/migrations/0038_personalized_environment_photo.down.sql" 2>&1
)"
personalized_evidence_down_status=$?
set -e
personalized_evidence_down_state="$(psql -X --no-password -AtF '|' \
  "postgresql:///${UPGRADE_DB}" <<'SQL'
SELECT
    EXISTS (
        SELECT 1
        FROM tide.task_execution_versions
        WHERE id = '25abcdef-0000-4000-8000-000000000011'
          AND shared_template_row_id = 'P-FB-NEGATIVE:v1'
          AND config->>'contentVersion' =
              '2026-08-11-personalized-environment-photo-v1'
    ),
    EXISTS (
        SELECT 1
        FROM tide.task_step_progress
        WHERE id = '25abcdef-0000-4000-8000-000000000220'
          AND task_assignment_id = 'MIGRATION-P-FB-NEGATIVE'
          AND step_key = 'p-fb-negative-environment-photo'
          AND status = 'IN_PROGRESS'
          AND percent = 60
          AND progress_summary = '{"checkpoint":"before-0038"}'::jsonb
    ),
    (
        SELECT count(*) = 1
        FROM tide.task_step_definitions
        WHERE execution_version_id =
            '25abcdef-0000-4000-8000-000000000011'
    ),
    (
        SELECT count(*) = 2
        FROM tide.task_validation_rules
        WHERE execution_version_id =
            '25abcdef-0000-4000-8000-000000000011'
    );
SQL
)"
if [[ "${personalized_evidence_down_status}" == "0" \
      || "${personalized_evidence_down_output}" != *"refused to remove personalized photo definitions with recorded execution evidence"* \
      || "${personalized_evidence_down_state}" != "t|t|t|t" ]]; then
  echo "0038 down 未原子保护已有个性化拍照进度：${personalized_evidence_down_state}" >&2
  echo "${personalized_evidence_down_output}" >&2
  exit 1
fi

psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${UPGRADE_DB}" \
  -c "DELETE FROM tide.task_step_progress WHERE id = '25abcdef-0000-4000-8000-000000000220'" >/dev/null

psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${UPGRADE_DB}" >/dev/null <<'SQL'
INSERT INTO tide.task_command_receipts (
    id,
    account_id,
    idempotency_key,
    command_id,
    command_type,
    request_hash,
    response_body,
    task_assignment_id
) VALUES (
    '25abcdef-0000-4000-8000-000000000221',
    '27000000-0000-4000-8000-000000000001',
    'migration-0038-photo-start',
    'migration-0038-photo-start-command',
    'START',
    repeat('b', 64),
    '{"accepted":true}'::jsonb,
    'MIGRATION-P-FB-NEGATIVE'
);
SQL

set +e
personalized_receipt_down_output="$(
  psql -X --no-password -v ON_ERROR_STOP=1 \
    "postgresql:///${UPGRADE_DB}" \
    -f "${DB_DIR}/migrations/0038_personalized_environment_photo.down.sql" 2>&1
)"
personalized_receipt_down_status=$?
set -e
personalized_receipt_down_state="$(psql -X --no-password -AtF '|' \
  "postgresql:///${UPGRADE_DB}" <<'SQL'
SELECT
    EXISTS (
        SELECT 1
        FROM tide.task_command_receipts
        WHERE id = '25abcdef-0000-4000-8000-000000000221'
          AND task_assignment_id = 'MIGRATION-P-FB-NEGATIVE'
          AND command_type = 'START'
    ),
    (
        SELECT count(*) = 1
        FROM tide.task_step_definitions
        WHERE execution_version_id =
            '25abcdef-0000-4000-8000-000000000011'
    ),
    (
        SELECT count(*) = 2
        FROM tide.task_validation_rules
        WHERE execution_version_id =
            '25abcdef-0000-4000-8000-000000000011'
    );
SQL
)"
if [[ "${personalized_receipt_down_status}" == "0" \
      || "${personalized_receipt_down_output}" != *"refused to remove personalized photo definitions with recorded execution evidence"* \
      || "${personalized_receipt_down_state}" != "t|t|t" ]]; then
  echo "0038 down 未原子保护个性化任务命令回执：${personalized_receipt_down_state}" >&2
  echo "${personalized_receipt_down_output}" >&2
  exit 1
fi

psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${UPGRADE_DB}" \
  -c "DELETE FROM tide.task_command_receipts WHERE id = '25abcdef-0000-4000-8000-000000000221'" >/dev/null

psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${UPGRADE_DB}" \
  -f "${DB_DIR}/migrations/0038_personalized_environment_photo.down.sql" >/dev/null

personalized_down_state="$(psql -X --no-password -AtF '|' \
  "postgresql:///${UPGRADE_DB}" <<'SQL'
SELECT
    EXISTS (
        SELECT 1 FROM tide.task_execution_versions
        WHERE shared_template_row_id = 'P-FB-NEGATIVE:v1'
          AND config =
            '{"estimatedMinutes":8,"allowRetry":true,"contentStatus":"PENDING","contentVersion":"2026-08-06","pendingReason":"JIAHE_PERSONALIZED_CONTENT_PENDING"}'::jsonb
    ),
    NOT EXISTS (
        SELECT 1 FROM tide.task_step_definitions definition
        JOIN tide.task_execution_versions execution
          ON execution.id = definition.execution_version_id
        WHERE execution.shared_template_row_id = 'P-FB-NEGATIVE:v1'
    ),
    NOT EXISTS (
        SELECT 1 FROM tide.task_validation_rules rule
        JOIN tide.task_execution_versions execution
          ON execution.id = rule.execution_version_id
        WHERE execution.shared_template_row_id = 'P-FB-NEGATIVE:v1'
    ),
    EXISTS (
        SELECT 1
        FROM tide.task_execution_versions
        WHERE id = '25abcdef-0000-4000-8000-000000000011'
          AND shared_template_row_id = 'P-FB-NEGATIVE:v1'
    ),
    EXISTS (
        SELECT 1
        FROM public.task_assignments
        WHERE assignment_id = 'MIGRATION-P-FB-NEGATIVE'
          AND template_version_id = 'P-FB-NEGATIVE:v1'
          AND status = 'ASSIGNED'
          AND row_version = 1
    );
SQL
)"
if [[ "${personalized_down_state}" != "t|t|t|t|t" ]]; then
  echo "0038 down 未恢复精确待配置执行形状：${personalized_down_state}" >&2
  exit 1
fi

psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${UPGRADE_DB}" \
  -f "${DB_DIR}/migrations/0038_personalized_environment_photo.up.sql" >/dev/null
personalized_down_up_state="$(psql -X --no-password -AtF '|' \
  "postgresql:///${UPGRADE_DB}" <<'SQL'
SELECT
    EXISTS (
        SELECT 1
        FROM tide.task_execution_versions
        WHERE id = '25abcdef-0000-4000-8000-000000000011'
          AND shared_template_row_id = 'P-FB-NEGATIVE:v1'
          AND config->>'contentVersion' =
              '2026-08-11-personalized-environment-photo-v1'
    ),
    (
        SELECT count(*) = 1
        FROM tide.task_step_definitions
        WHERE execution_version_id =
            '25abcdef-0000-4000-8000-000000000011'
    ),
    (
        SELECT count(*) = 2
        FROM tide.task_validation_rules
        WHERE execution_version_id =
            '25abcdef-0000-4000-8000-000000000011'
    ),
    EXISTS (
        SELECT 1
        FROM public.task_assignments
        WHERE assignment_id = 'MIGRATION-P-FB-NEGATIVE'
          AND status = 'ASSIGNED'
          AND row_version = 1
    );
SQL
)"
if [[ "${personalized_down_up_state}" != "t|t|t|t" ]]; then
  echo "0038 existing execution down/up 未保留身份和 assignment：${personalized_down_up_state}" >&2
  exit 1
fi

psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${UPGRADE_DB}" >/dev/null <<'SQL'
UPDATE public.task_assignments
SET
    status = 'VIEWED',
    status_changed_at = status_changed_at + interval '1 second'
WHERE assignment_id = 'MIGRATION-P-FB-STARTED';

UPDATE public.task_assignments
SET
    status = 'IN_PROGRESS',
    status_changed_at = status_changed_at + interval '1 second'
WHERE assignment_id = 'MIGRATION-P-FB-STARTED';
SQL

set +e
personalized_started_assignment_down_output="$(
  psql -X --no-password -v ON_ERROR_STOP=1 \
    "postgresql:///${UPGRADE_DB}" \
    -f "${DB_DIR}/migrations/0038_personalized_environment_photo.down.sql" 2>&1
)"
personalized_started_assignment_down_status=$?
set -e
personalized_started_assignment_down_state="$(psql -X --no-password -AtF '|' \
  "postgresql:///${UPGRADE_DB}" <<'SQL'
SELECT
    EXISTS (
        SELECT 1
        FROM public.task_assignments
        WHERE assignment_id = 'MIGRATION-P-FB-STARTED'
          AND template_version_id = 'P-FB-NEGATIVE:v1'
          AND status = 'IN_PROGRESS'
          AND row_version = 3
    ),
    (
        SELECT count(*) = 1
        FROM tide.task_step_definitions
        WHERE execution_version_id =
            '25abcdef-0000-4000-8000-000000000011'
    ),
    (
        SELECT count(*) = 2
        FROM tide.task_validation_rules
        WHERE execution_version_id =
            '25abcdef-0000-4000-8000-000000000011'
    );
SQL
)"
if [[ "${personalized_started_assignment_down_status}" == "0" \
      || "${personalized_started_assignment_down_output}" != *"refused to remove personalized photo definitions with recorded execution evidence"* \
      || "${personalized_started_assignment_down_state}" != "t|t|t" ]]; then
  echo "0038 down 未原子保护 IN_PROGRESS 个性化 assignment：${personalized_started_assignment_down_state}" >&2
  echo "${personalized_started_assignment_down_output}" >&2
  exit 1
fi

psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${UPGRADE_DB}" \
  -f "${DB_DIR}/migrations/0041_crm_sso_hybrid.down.sql" >/dev/null
crm_sso_down_state="$(psql -X --no-password -Atqc "
  SELECT
    to_regclass('tide.crm_sso_logins') IS NULL
    AND NOT EXISTS (
      SELECT 1
      FROM information_schema.columns
      WHERE table_schema = 'tide'
        AND table_name = 'auth_sessions'
        AND column_name = 'auth_method'
    )
" "postgresql:///${UPGRADE_DB}")"
if [[ "${crm_sso_down_state}" != "t" ]]; then
  echo "0041 down 未恢复本地认证结构。" >&2
  exit 1
fi
psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${UPGRADE_DB}" \
  -f "${DB_DIR}/migrations/0041_crm_sso_hybrid.up.sql" >/dev/null
crm_sso_down_up_state="$(psql -X --no-password -Atqc "
  SELECT to_regclass('tide.crm_sso_logins') IS NOT NULL
" "postgresql:///${UPGRADE_DB}")"
if [[ "${crm_sso_down_up_state}" != "t" ]]; then
  echo "0041 down-up 未恢复 CRM SSO 结构。" >&2
  exit 1
fi
psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${UPGRADE_DB}" \
  -f "${DB_DIR}/migrations/0041_crm_sso_hybrid.down.sql" >/dev/null

set +e
personalized_evidence_down_output="$(
  psql -X --no-password -v ON_ERROR_STOP=1 \
    "postgresql:///${UPGRADE_DB}" \
    -f "${DB_DIR}/migrations/0038_personalized_environment_photo.down.sql" 2>&1
)"
personalized_evidence_down_status=$?
set -e
personalized_evidence_down_state="$(psql -X --no-password -AtF '|' \
  "postgresql:///${UPGRADE_DB}" <<'SQL'
SELECT
    EXISTS (
        SELECT 1
        FROM tide.task_execution_versions
        WHERE id = '25abcdef-0000-4000-8000-000000000011'
          AND shared_template_row_id = 'P-FB-NEGATIVE:v1'
          AND config->>'contentVersion' =
              '2026-08-11-personalized-environment-photo-v1'
    ),
    EXISTS (
        SELECT 1
        FROM tide.task_step_progress
        WHERE id = '25abcdef-0000-4000-8000-000000000220'
          AND task_assignment_id = 'MIGRATION-P-FB-NEGATIVE'
          AND step_key = 'p-fb-negative-environment-photo'
          AND status = 'IN_PROGRESS'
          AND percent = 60
          AND progress_summary = '{"checkpoint":"before-0038"}'::jsonb
    ),
    (
        SELECT count(*) = 1
        FROM tide.task_step_definitions
        WHERE execution_version_id =
            '25abcdef-0000-4000-8000-000000000011'
    ),
    (
        SELECT count(*) = 2
        FROM tide.task_validation_rules
        WHERE execution_version_id =
            '25abcdef-0000-4000-8000-000000000011'
    );
SQL
)"
if [[ "${personalized_evidence_down_status}" == "0" \
      || "${personalized_evidence_down_output}" != *"refused to remove personalized photo definitions with recorded execution evidence"* \
      || "${personalized_evidence_down_state}" != "t|t|t|t" ]]; then
  echo "0038 down 未原子保护已有个性化拍照进度：${personalized_evidence_down_state}" >&2
  echo "${personalized_evidence_down_output}" >&2
  exit 1
fi

psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${UPGRADE_DB}" \
  -c "DELETE FROM tide.task_step_progress WHERE id = '25abcdef-0000-4000-8000-000000000220'" >/dev/null

psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${UPGRADE_DB}" >/dev/null <<'SQL'
INSERT INTO tide.task_command_receipts (
    id,
    account_id,
    idempotency_key,
    command_id,
    command_type,
    request_hash,
    response_body,
    task_assignment_id
) VALUES (
    '25abcdef-0000-4000-8000-000000000221',
    '27000000-0000-4000-8000-000000000001',
    'migration-0038-photo-start',
    'migration-0038-photo-start-command',
    'START',
    repeat('b', 64),
    '{"accepted":true}'::jsonb,
    'MIGRATION-P-FB-NEGATIVE'
);
SQL

set +e
personalized_receipt_down_output="$(
  psql -X --no-password -v ON_ERROR_STOP=1 \
    "postgresql:///${UPGRADE_DB}" \
    -f "${DB_DIR}/migrations/0038_personalized_environment_photo.down.sql" 2>&1
)"
personalized_receipt_down_status=$?
set -e
personalized_receipt_down_state="$(psql -X --no-password -AtF '|' \
  "postgresql:///${UPGRADE_DB}" <<'SQL'
SELECT
    EXISTS (
        SELECT 1
        FROM tide.task_command_receipts
        WHERE id = '25abcdef-0000-4000-8000-000000000221'
          AND task_assignment_id = 'MIGRATION-P-FB-NEGATIVE'
          AND command_type = 'START'
    ),
    (
        SELECT count(*) = 1
        FROM tide.task_step_definitions
        WHERE execution_version_id =
            '25abcdef-0000-4000-8000-000000000011'
    ),
    (
        SELECT count(*) = 2
        FROM tide.task_validation_rules
        WHERE execution_version_id =
            '25abcdef-0000-4000-8000-000000000011'
    );
SQL
)"
if [[ "${personalized_receipt_down_status}" == "0" \
      || "${personalized_receipt_down_output}" != *"refused to remove personalized photo definitions with recorded execution evidence"* \
      || "${personalized_receipt_down_state}" != "t|t|t" ]]; then
  echo "0038 down 未原子保护个性化任务命令回执：${personalized_receipt_down_state}" >&2
  echo "${personalized_receipt_down_output}" >&2
  exit 1
fi

psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${UPGRADE_DB}" \
  -c "DELETE FROM tide.task_command_receipts WHERE id = '25abcdef-0000-4000-8000-000000000221'" >/dev/null

psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${UPGRADE_DB}" \
  -f "${DB_DIR}/migrations/0038_personalized_environment_photo.down.sql" >/dev/null

personalized_down_state="$(psql -X --no-password -AtF '|' \
  "postgresql:///${UPGRADE_DB}" <<'SQL'
SELECT
    EXISTS (
        SELECT 1 FROM tide.task_execution_versions
        WHERE shared_template_row_id = 'P-FB-NEGATIVE:v1'
          AND config =
            '{"estimatedMinutes":8,"allowRetry":true,"contentStatus":"PENDING","contentVersion":"2026-08-06","pendingReason":"JIAHE_PERSONALIZED_CONTENT_PENDING"}'::jsonb
    ),
    NOT EXISTS (
        SELECT 1 FROM tide.task_step_definitions definition
        JOIN tide.task_execution_versions execution
          ON execution.id = definition.execution_version_id
        WHERE execution.shared_template_row_id = 'P-FB-NEGATIVE:v1'
    ),
    NOT EXISTS (
        SELECT 1 FROM tide.task_validation_rules rule
        JOIN tide.task_execution_versions execution
          ON execution.id = rule.execution_version_id
        WHERE execution.shared_template_row_id = 'P-FB-NEGATIVE:v1'
    ),
    EXISTS (
        SELECT 1
        FROM tide.task_execution_versions
        WHERE id = '25abcdef-0000-4000-8000-000000000011'
          AND shared_template_row_id = 'P-FB-NEGATIVE:v1'
    ),
    EXISTS (
        SELECT 1
        FROM public.task_assignments
        WHERE assignment_id = 'MIGRATION-P-FB-NEGATIVE'
          AND template_version_id = 'P-FB-NEGATIVE:v1'
          AND status = 'ASSIGNED'
          AND row_version = 1
    );
SQL
)"
if [[ "${personalized_down_state}" != "t|t|t|t|t" ]]; then
  echo "0038 down 未恢复精确待配置执行形状：${personalized_down_state}" >&2
  exit 1
fi

psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${UPGRADE_DB}" \
  -f "${DB_DIR}/migrations/0038_personalized_environment_photo.up.sql" >/dev/null
personalized_down_up_state="$(psql -X --no-password -AtF '|' \
  "postgresql:///${UPGRADE_DB}" <<'SQL'
SELECT
    EXISTS (
        SELECT 1
        FROM tide.task_execution_versions
        WHERE id = '25abcdef-0000-4000-8000-000000000011'
          AND shared_template_row_id = 'P-FB-NEGATIVE:v1'
          AND config->>'contentVersion' =
              '2026-08-11-personalized-environment-photo-v1'
    ),
    (
        SELECT count(*) = 1
        FROM tide.task_step_definitions
        WHERE execution_version_id =
            '25abcdef-0000-4000-8000-000000000011'
    ),
    (
        SELECT count(*) = 2
        FROM tide.task_validation_rules
        WHERE execution_version_id =
            '25abcdef-0000-4000-8000-000000000011'
    ),
    EXISTS (
        SELECT 1
        FROM public.task_assignments
        WHERE assignment_id = 'MIGRATION-P-FB-NEGATIVE'
          AND status = 'ASSIGNED'
          AND row_version = 1
    );
SQL
)"
if [[ "${personalized_down_up_state}" != "t|t|t|t" ]]; then
  echo "0038 existing execution down/up 未保留身份和 assignment：${personalized_down_up_state}" >&2
  exit 1
fi

psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${UPGRADE_DB}" >/dev/null <<'SQL'
UPDATE public.task_assignments
SET
    status = 'VIEWED',
    status_changed_at = status_changed_at + interval '1 second'
WHERE assignment_id = 'MIGRATION-P-FB-STARTED';

UPDATE public.task_assignments
SET
    status = 'IN_PROGRESS',
    status_changed_at = status_changed_at + interval '1 second'
WHERE assignment_id = 'MIGRATION-P-FB-STARTED';
SQL

set +e
personalized_started_assignment_down_output="$(
  psql -X --no-password -v ON_ERROR_STOP=1 \
    "postgresql:///${UPGRADE_DB}" \
    -f "${DB_DIR}/migrations/0038_personalized_environment_photo.down.sql" 2>&1
)"
personalized_started_assignment_down_status=$?
set -e
personalized_started_assignment_down_state="$(psql -X --no-password -AtF '|' \
  "postgresql:///${UPGRADE_DB}" <<'SQL'
SELECT
    EXISTS (
        SELECT 1
        FROM public.task_assignments
        WHERE assignment_id = 'MIGRATION-P-FB-STARTED'
          AND template_version_id = 'P-FB-NEGATIVE:v1'
          AND status = 'IN_PROGRESS'
          AND row_version = 3
    ),
    (
        SELECT count(*) = 1
        FROM tide.task_step_definitions
        WHERE execution_version_id =
            '25abcdef-0000-4000-8000-000000000011'
    ),
    (
        SELECT count(*) = 2
        FROM tide.task_validation_rules
        WHERE execution_version_id =
            '25abcdef-0000-4000-8000-000000000011'
    );
SQL
)"
if [[ "${personalized_started_assignment_down_status}" == "0" \
      || "${personalized_started_assignment_down_output}" != *"refused to remove personalized photo definitions with recorded execution evidence"* \
      || "${personalized_started_assignment_down_state}" != "t|t|t" ]]; then
  echo "0038 down 未原子保护 IN_PROGRESS 个性化 assignment：${personalized_started_assignment_down_state}" >&2
  echo "${personalized_started_assignment_down_output}" >&2
  exit 1
fi

psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${UPGRADE_DB}" \
  -f "${DB_DIR}/migrations/0037_g04_remove_device_check.down.sql" >/dev/null
g04_0037_down_state="$(psql -X --no-password -Atqc "
  SELECT
    execution.config->>'contentVersion' = '2026-08-11-g04-two-part'
    AND (
      SELECT array_agg(definition.step_key ORDER BY definition.position) =
        ARRAY['g02-environment-photo', 'g02-courseware-confirmation']::text[]
      FROM tide.task_step_definitions definition
      WHERE definition.execution_version_id = execution.id
    )
    AND NOT EXISTS (
      SELECT 1
      FROM tide.task_step_definitions definition
      WHERE definition.execution_version_id = execution.id
        AND definition.step_key = 'g02-device-check'
    )
  FROM tide.task_execution_versions execution
  WHERE execution.shared_template_row_id = 'G02:v1'
" "postgresql:///${UPGRADE_DB}")"
if [[ "${g04_0037_down_state}" != "t" ]]; then
  echo "0037 forward-only down 错误恢复了 G04 设备检测。" >&2
  exit 1
fi

psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${UPGRADE_DB}" \
  -f "${DB_DIR}/migrations/0033_g01_tesol_only.down.sql" >/dev/null

g01_0033_down_state="$(psql -X --no-password -AtF '|' \
  "postgresql:///${UPGRADE_DB}" <<'SQL'
SELECT
    EXISTS (
        SELECT 1
        FROM tide.task_validation_rules rule
        WHERE rule.id = '25abcdef-0000-4000-8000-000000000113'::uuid
          AND rule.execution_version_id =
              '25abcdef-0000-4000-8000-000000000001'::uuid
          AND rule.rule_key = 'g01-external-status'
          AND rule.rule_type = 'G01_EXTERNAL_STATUS'
          AND rule.rule_version = '2026-07-22'
          AND rule.position = 3
          AND rule.config = '{}'::jsonb
          AND rule.teacher_failure_copy =
              'Self-intro 和 TESOL 真实状态尚未全部通过。'
    ),
    EXISTS (
        SELECT 1
        FROM tide.task_execution_versions execution
        WHERE execution.id = '25abcdef-0000-4000-8000-000000000001'::uuid
          AND execution.shared_template_row_id = 'G01:v1'
          AND execution.task_code = 'G01'
    ),
    EXISTS (
        SELECT 1
        FROM public.task_assignments assignment
        WHERE assignment.assignment_id = 'MIGRATION-SEMANTIC-G01'
          AND assignment.status = 'ASSIGNED'
          AND assignment.row_version = 1
    ),
    EXISTS (
        SELECT 1
        FROM tide.task_step_progress progress
        WHERE progress.id = '25abcdef-0000-4000-8000-000000000201'::uuid
          AND progress.task_assignment_id = 'MIGRATION-SEMANTIC-G01'
          AND progress.progress_summary =
              '{"checkpoint":"before-0033"}'::jsonb
    );
SQL
)"
if [[ "${g01_0033_down_state}" != "t|t|t|t" ]]; then
  echo "0033 down 未精确恢复 G01 rule 或改写稳定身份：${g01_0033_down_state}" >&2
  exit 1
fi

psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${UPGRADE_DB}" \
  -f "${DB_DIR}/migrations/0032_first_login_onboarding.down.sql" >/dev/null
onboarding_down_state="$(psql -X --no-password -Atqc "
  SELECT to_regclass('tide.account_onboarding_states') IS NULL
" "postgresql:///${UPGRADE_DB}")"
if [[ "${onboarding_down_state}" != "t" ]]; then
  echo "0032 down 未删除引导状态表。" >&2
  exit 1
fi

psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${UPGRADE_DB}" \
  -f "${DB_DIR}/migrations/0032_first_login_onboarding.up.sql" >/dev/null
onboarding_down_up_state="$(psql -X --no-password -AtF '|' "postgresql:///${UPGRADE_DB}" <<'SQL'
SELECT
    to_regclass('tide.account_onboarding_states') IS NOT NULL,
    (
        SELECT count(*) = 1
        FROM tide.account_onboarding_states
        WHERE status = 'MIGRATED_EXISTING'
          AND request_hash IS NULL
    ),
    NOT EXISTS (
        SELECT 1
        FROM tide.account_onboarding_states
        WHERE account_id = '27000000-0000-4000-8000-000000000002'
    );
SQL
)"
if [[ "${onboarding_down_up_state}" != "t|t|t" ]]; then
  echo "0032 down-up 未准确重建引导状态：${onboarding_down_up_state}" >&2
  exit 1
fi
psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${UPGRADE_DB}" \
  -f "${DB_DIR}/migrations/0032_first_login_onboarding.down.sql" >/dev/null

psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${UPGRADE_DB}" \
  -f "${DB_DIR}/migrations/0031_g04_independent_sections.down.sql" >/dev/null
g04_down_state="$(psql -X --no-password -Atqc "
  SELECT
    execution.config->>'contentVersion' = '2026-08-11-g04-two-part'
    AND (
      SELECT array_agg(definition.step_key ORDER BY definition.position) =
        ARRAY['g02-environment-photo', 'g02-courseware-confirmation']::text[]
      FROM tide.task_step_definitions definition
      WHERE definition.execution_version_id = execution.id
    )
    AND NOT EXISTS (
      SELECT 1
      FROM tide.task_step_definitions definition
      WHERE definition.execution_version_id = execution.id
        AND definition.step_key = 'g02-device-check'
    )
  FROM tide.task_execution_versions execution
  WHERE execution.shared_template_row_id = 'G02:v1'
" "postgresql:///${UPGRADE_DB}")"
if [[ "${g04_down_state}" != "t" ]]; then
  echo "0031 forward-only down 错误改动了 0037 的 G04 两段结构。" >&2
  exit 1
fi

psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${UPGRADE_DB}" \
  -f "${DB_DIR}/migrations/0030_remove_unused_columns_and_orphan_function.down.sql" >/dev/null
restored_upgrade_file_visibility_signature="$(psql -X --no-password -Atqc "
  select concat_ws('|',
    columns.column_name,
    columns.udt_name,
    columns.is_nullable,
    columns.column_default,
    pg_get_constraintdef(constraint_row.oid, true)
  )
  from information_schema.columns columns
  join pg_constraint constraint_row
    on constraint_row.conrelid = 'tide.file_objects'::regclass
   and constraint_row.conname = 'file_objects_visibility_check'
  where columns.table_schema = 'tide'
    and columns.table_name = 'file_objects'
    and columns.column_name = 'visibility'
" "postgresql:///${UPGRADE_DB}")"
restored_upgrade_orphan_function_definition="$(psql -X --no-password -Atqc \
  "select pg_get_functiondef('tide.enforce_outbox_target()'::regprocedure)" \
  "postgresql:///${UPGRADE_DB}")"
if [[ "${restored_upgrade_file_visibility_signature}" != "${upgrade_file_visibility_signature}" ]]; then
  echo "0030 down 未精确恢复 file_objects.visibility。" >&2
  exit 1
fi
if [[ "${restored_upgrade_orphan_function_definition}" != "${upgrade_orphan_function_definition}" ]]; then
  echo "0030 down 未精确恢复 enforce_outbox_target()。" >&2
  exit 1
fi

psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${UPGRADE_DB}" \
  -f "${DB_DIR}/migrations/0029_remove_unused_tide_objects.down.sql" >/dev/null
restored_upgrade_unused_view_definitions="$(psql -X --no-password -Atqc "
  select string_agg(
    view_name || ':' || pg_get_viewdef(format('tide.%I', view_name)::regclass, true),
    E'\\n' order by view_name
  )
  from unnest(array[
    'analytics_actor_task_journey_v1',
    'analytics_task_assignment_funnel_v1',
    'analytics_task_funnel_v1',
    'analytics_task_step_funnel_v1',
    'analytics_content_quality_v1'
  ]::text[]) expected(view_name)
" "postgresql:///${UPGRADE_DB}")"
restored_upgrade_unused_table_signature="$(psql -X --no-password -Atqc "
  with target(table_name) as (
    values
      ('outcome_projections'),
      ('camp_enrollment_projections'),
      ('audit_events'),
      ('task_template_files'),
      ('file_migrations'),
      ('teacher_photo_runs')
  ), signature_parts as (
    select
      'column' as kind,
      columns.table_name,
      columns.column_name || ':' || columns.udt_name || ':' ||
        columns.is_nullable || ':' ||
        coalesce(columns.column_default, '') as definition
    from information_schema.columns columns
    join target using (table_name)
    where columns.table_schema = 'tide'
    union all
    select
      'constraint', target.table_name, constraint_row.conname || ':' ||
        pg_get_constraintdef(constraint_row.oid, true)
    from target
    join pg_class relation
      on relation.oid = format('tide.%I', target.table_name)::regclass
    join pg_constraint constraint_row
      on constraint_row.conrelid = relation.oid
    union all
    select 'index', indexes.tablename, indexes.indexname || ':' || indexes.indexdef
    from pg_indexes indexes
    join target on target.table_name = indexes.tablename
    where indexes.schemaname = 'tide'
  )
  select md5(string_agg(
    kind || ':' || table_name || ':' || definition,
    E'\\n' order by kind, table_name, definition
  ))
  from signature_parts
" "postgresql:///${UPGRADE_DB}")"
if [[ "${restored_upgrade_unused_view_definitions}" != "${upgrade_unused_view_definitions}" ]]; then
  echo "0029 down 未精确恢复 v1 分析视图。" >&2
  exit 1
fi
if [[ "${restored_upgrade_unused_table_signature}" != "${upgrade_unused_table_signature}" ]]; then
  echo "0029 down 未精确恢复已清理表结构。" >&2
  exit 1
fi

set +e
retired_view_down_output="$(
  psql -X --no-password -v ON_ERROR_STOP=1 \
    "postgresql:///${UPGRADE_DB}" \
    -f "${DB_DIR}/migrations/0028_retire_task_business_change_view.down.sql" 2>&1
)"
retired_view_down_status=$?
set -e
retired_view_down_state="$(psql -X --no-password -Atqc \
  "select to_regclass('tide.analytics_task_business_change_v1') is null" \
  "postgresql:///${UPGRADE_DB}")"
if [[ "${retired_view_down_status}" == "0" \
      || "${retired_view_down_output}" != *"public.teacher_metric_snapshots"* \
      || "${retired_view_down_state}" != "t" ]]; then
  echo "0028 down 未在 public 47+ 缺少历史快照表时失败关闭：${retired_view_down_state}" >&2
  echo "${retired_view_down_output}" >&2
  exit 1
fi
psql -X --no-password -v ON_ERROR_STOP=1 \
  "postgresql:///${UPGRADE_DB}" \
  -f "${DB_DIR}/migrations/0025_fixed_task_semantic_alignment.down.sql" >/dev/null
semantic_down_state="$(psql -X --no-password -Atqc "
  SELECT
    (
      SELECT count(*) = 9
      FROM (
        VALUES
          ('G01:v1', 'G01'), ('G02:v1', 'G04'),
          ('G03:v1', 'G02'), ('G04:v1', 'G03'),
          ('G06:v1', 'G05'), ('G07:v1', 'G06'),
          ('G08:v1', 'G07'), ('G09:v1', 'G08'),
          ('G10:v1', 'G09')
      ) expected(row_id, task_code)
      JOIN tide.task_execution_versions execution
        ON execution.shared_template_row_id = expected.row_id
       AND execution.task_code = expected.task_code
    )
    AND NOT EXISTS (
      SELECT 1
      FROM tide.task_execution_versions execution
      WHERE execution.shared_template_row_id = 'G05:v1'
        AND execution.task_code <> 'G00'
    )
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
if [[ "${guard_status}" == "0" || "${guard_output}" != *"current_user 必须精确为 tide_sys_admin"* ]]; then
  echo "生产迁移未按账号守卫阻断错误数据库账号：${guard_output}" >&2
  exit 1
fi

"${ADMIN_PSQL[@]}" -c "ALTER ROLE tide_sys_admin SUPERUSER" >/dev/null
set +e
guard_output="$(
  PGOPTIONS="-c role=tide_sys_admin" \
  TIDE_MIGRATION_DATABASE_URL="postgresql:///${UPGRADE_DB}?sslmode=verify-full" \
  TIDE_MIGRATION_EXPECTED_DATABASE="${UPGRADE_DB}" \
    bash "${DB_DIR}/scripts/apply-production.sh" 2>&1
)"
guard_status=$?
set -e
"${ADMIN_PSQL[@]}" -c "
  ALTER ROLE tide_sys_admin
    NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
" >/dev/null
if [[ "${guard_status}" == "0" || "${guard_output}" != *"禁止使用 superuser"* ]]; then
  echo "生产迁移未按 superuser 守卫阻断：${guard_output}" >&2
  exit 1
fi

set +e
guard_output="$(
  PGOPTIONS="-c role=tide_sys_admin" \
  TIDE_MIGRATION_DATABASE_URL="postgresql:///${UPGRADE_DB}?sslmode=verify-full" \
  TIDE_MIGRATION_EXPECTED_DATABASE="${UPGRADE_DB}" \
    bash "${DB_DIR}/scripts/apply-production.sh" 2>&1
)"
guard_status=$?
set -e
if [[ "${guard_status}" == "0" || ( "${guard_output}" != *"未实际使用 TLS"* && "${guard_output}" != *"server does not support SSL"* && "${guard_output}" != *"禁止由高权限 session_user"* ) ]]; then
  echo "生产迁移未按 TLS/会话身份守卫阻断会话：${guard_output}" >&2
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

echo "生产 migrator fresh/upgrade、teacher canonical 0042 与最终 public 65 账本契约、跨 Schema 顺序门禁、0022–0042、G01 TESOL-only 受限视图、G02 原生文档、G04 两模块、G05/G08/G09 阔知课程、P-FB-NEGATIVE 环境拍照、CRM SSO、教师英文文案、最终表级 ACL、运行时 Trigger、固定 owner、连接守卫与 checksum 验证通过；真实 rev60 隐私 Trigger、rev61 文案、rev62 索引、rev63 direct 隐私以及 rev64/rev65 课程文案迁移由根仓库迁移测试验收。"
