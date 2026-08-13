import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const databaseFile = (relativePath: string) =>
  readFileSync(resolve(__dirname, '../../../database', relativePath), 'utf8');

describe('teacher database migration chain', () => {
  it('keeps every local and production entry point on the 0041 head', () => {
    const ddl = databaseFile('ddl.sql');
    const production = databaseFile('scripts/apply-production.sh');

    expect(ddl).toMatch(
      /\\ir fixtures\/0004_p_fb_negative_contract\.sql[\s\S]*seed\/0000_mock_shared_catalog\.sql[\s\S]*0033_g01_tesol_only\.up\.sql[\s\S]*seed\/0005_mock_g04_two_part_catalog\.sql[\s\S]*0037_g04_remove_device_check\.up\.sql[\s\S]*0038_personalized_environment_photo\.up\.sql[\s\S]*0039_g02_policy_document\.up\.sql[\s\S]*0040_g02_document_read_status\.up\.sql[\s\S]*0041_crm_sso_hybrid\.up\.sql/,
    );
    expect(ddl.trimEnd()).toMatch(
      /\\ir migrations\/0041_crm_sso_hybrid\.up\.sql$/,
    );
    expect(ddl).toContain('\\ir seed/0005_mock_g04_two_part_catalog.sql');
    expect(production).toContain(
      'TARGET_MIGRATION="${TIDE_MIGRATION_TARGET:-0041_crm_sso_hybrid}"',
    );
    expect(production).toMatch(
      /0025_fixed_task_semantic_alignment\s+0026_kuozhi_course_syncs\s+0027_remove_local_quiz_runtime\s+0028_retire_task_business_change_view\s+0029_remove_unused_tide_objects\s+0030_remove_unused_columns_and_orphan_function\s+0031_g04_independent_sections\s+0032_first_login_onboarding\s+0033_g01_tesol_only\s+0037_g04_remove_device_check\s+0038_personalized_environment_photo\s+0039_g02_policy_document\s+0040_g02_document_read_status\s+0041_crm_sso_hybrid/,
    );
    expect(production).toContain('g02_document_read_status_recorded=false');
    expect(production).toContain(
      '&& "${g02_document_read_status_recorded}" != "t"',
    );
  });

  it('limits PRE private-line plaintext migration to the fixed database identity', () => {
    const production = databaseFile('scripts/apply-production.sh');

    expect(production).toContain(
      'if [[ -n "${TIDE_MIGRATION_DATABASE_URL:-}" ]]; then',
    );
    expect(production).toContain('DATABASE_URL="${DATABASE_URL:-}"');
    expect(production).toContain(
      'PRE_PRIVATE_LINE_DB_HOST="tide-system.rwlb.singapore.rds.aliyuncs.com"',
    );
    expect(production).toContain('PRE_PRIVATE_LINE_DB_NAME="tide_system_test"');
    expect(production).toContain('PRE_PRIVATE_LINE_DB_OWNER="tide_sys_admin"');
    expect(production).toContain(
      'database|dbname|host|hostaddr|options|port|service|servicefile|ssl|user)',
    );
    expect(production).toContain(
      '&& "${EXPECTED_DATABASE}" == "${PRE_PRIVATE_LINE_DB_NAME}"',
    );
    expect(production).toContain("current_setting('ssl') = 'on'");
    expect(production).toContain(
      'if [[ "${ssl_active}" != "f" || "${server_ssl_active}" != "f" ]]; then',
    );
    expect(production).toContain(
      'elif [[ "${ssl_active}" != "t" || "${server_ssl_active}" != "t" ]]; then',
    );
    expect(production).toContain(
      'PGDATABASE PGHOST PGHOSTADDR PGPORT PGSERVICE PGSERVICEFILE PGUSER',
    );
    expect(production).toContain(
      'if [[ ${!libpq_identity_name+x} == x ]]; then',
    );
    expect(production).not.toContain(
      'TIDE_MIGRATION_PRIVATE_LINE_PLAINTEXT_APPROVED',
    );
  });

  it('updates only the stable G01 external-status rule in 0033', () => {
    const up = databaseFile('migrations/0033_g01_tesol_only.up.sql').trim();
    const down = databaseFile('migrations/0033_g01_tesol_only.down.sql').trim();

    expect(up.startsWith('BEGIN;')).toBe(true);
    expect(up.endsWith('COMMIT;')).toBe(true);
    expect(down.startsWith('BEGIN;')).toBe(true);
    expect(down.endsWith('COMMIT;')).toBe(true);
    for (const sql of [up, down]) {
      expect(sql).toContain("shared_template_row_id = 'G01:v1'");
      expect(sql).toContain("rule_key = 'g01-external-status'");
      expect(sql).toContain("rule_type = 'G01_EXTERNAL_STATUS'");
      expect(sql).toMatch(
        /rule_key = 'g01-external-status'\s+OR rule_type = 'G01_EXTERNAL_STATUS'/,
      );
      expect(sql).toContain('OR NOT EXISTS (');
      expect(sql).toContain('g01_execution_before');
      expect(sql).toContain('g01_assignment_before');
      expect(sql).toContain('g01_progress_before');
      expect(sql).toContain('g01_steps_before');
      expect(sql).toContain('g01_other_rules_before');
      expect(sql).toContain('g01_external_rule_before');
    }
    expect(up).toContain("rule_version = '2026-08-11-tesol-only-v1'");
    expect(up).toContain("teacher_failure_copy = 'TESOL 真实状态尚未通过。'");
    expect(up).toContain('has an unreviewed shape before migration 0033');
    expect(down).toContain("SET rule_version = '2026-07-22'");
    expect(down).toContain('Self-intro 和 TESOL 真实状态尚未全部通过。');
  });

  it('keeps 0038 transactional and refuses to remove recorded photo evidence', () => {
    const up = databaseFile(
      'migrations/0038_personalized_environment_photo.up.sql',
    ).trim();
    const down = databaseFile(
      'migrations/0038_personalized_environment_photo.down.sql',
    ).trim();

    expect(up.startsWith('BEGIN;')).toBe(true);
    expect(up.endsWith('COMMIT;')).toBe(true);
    expect(down.startsWith('BEGIN;')).toBe(true);
    expect(down.endsWith('COMMIT;')).toBe(true);
    expect(up).toContain('p-fb-negative-environment-photo');
    expect(up).toContain("'TEACHING_ENVIRONMENT_V1'");
    expect(up).toContain('"contentStatus":"PENDING"');
    expect(up).toContain("'a89b9f31-2a71-43da-846e-60c51e14f162'::uuid");
    expect(up).toContain("integration_mode = 'OUTBOUND_MANAGED'");
    expect(up).toContain("source_mode = 'REAL'");
    expect(up).toContain("payload->>'score_type' = 'ZERO'");
    expect(up).toContain(
      'Complete the configured improvement activity for the feedback issue shown in the task reason.',
    );
    expect(up).toContain(
      'The teacher app marks the task as completed after every requirement for the assigned improvement activity',
    );
    expect(up).toContain(
      'requires exactly one approved published REAL/OUTBOUND_MANAGED zero-point P-FB-NEGATIVE:v1 shared template',
    );
    expect(up).toContain(
      'cannot create the deterministic P-FB-NEGATIVE execution because its task code or ID is already occupied',
    );
    expect(up).not.toContain(
      'left the empty P-FB-NEGATIVE execution unchanged',
    );
    expect(up).toContain('p_fb_negative_assignment_before');
    expect(up).toContain('p_fb_negative_progress_before');
    expect(up).toContain(
      'AND config = \'{"stepKey":"p-fb-negative-environment-photo","criteriaVersion":"personalized-teaching-environment-2026-08-v1"',
    );
    expect(up).toContain(
      "AND teacher_failure_copy = '已保留你完成的内容，请根据提示更新这份材料。'",
    );
    expect(up).not.toContain('config->');
    expect(down).toContain("integration_mode = 'OUTBOUND_MANAGED'");
    expect(down).toContain("source_mode = 'REAL'");
    expect(down).toContain("payload->>'score_type' = 'ZERO'");
    expect(down).toContain(
      'Complete the configured improvement activity for the feedback issue shown in the task reason.',
    );
    expect(down).toContain(
      'The teacher app marks the task as completed after every requirement for the assigned improvement activity',
    );
    expect(down).toContain("AND title = 'Take a teaching-environment photo'");
    expect(down).toContain(
      'AND config = \'{"stepKey":"p-fb-negative-environment-photo","criteriaVersion":"personalized-teaching-environment-2026-08-v1"',
    );
    expect(down).toContain(
      "AND teacher_failure_copy = '已保留你完成的内容，请根据提示更新这份材料。'",
    );
    expect(down).not.toContain('config->');
    expect(down).toContain(
      'refused to remove personalized photo definitions with recorded execution evidence',
    );
    expect(down).toContain('tide.file_upload_intents');
    expect(down).toContain('tide.task_submissions');
  });

  it('creates and removes 0041 CRM SSO inside explicit transactions', () => {
    const up = databaseFile('migrations/0041_crm_sso_hybrid.up.sql').trim();
    const down = databaseFile('migrations/0041_crm_sso_hybrid.down.sql').trim();

    expect(up.startsWith('BEGIN;')).toBe(true);
    expect(up.endsWith('COMMIT;')).toBe(true);
    expect(down.startsWith('BEGIN;')).toBe(true);
    expect(down.endsWith('COMMIT;')).toBe(true);
    expect(up).toContain('CREATE TABLE tide.crm_sso_logins');
    expect(up).toContain('ALTER COLUMN password_hash DROP NOT NULL');
    expect(up).toContain("created_via IN ('LOCAL', 'CRM_SSO')");
    expect(up).toContain("auth_method IN ('PASSWORD', 'CRM_SSO')");
  });

  it('keeps both directions of 0031 inside one explicit transaction', () => {
    for (const filename of [
      'migrations/0031_g04_independent_sections.up.sql',
      'migrations/0031_g04_independent_sections.down.sql',
    ]) {
      const sql = databaseFile(filename).trim();
      expect(sql.startsWith('BEGIN;')).toBe(true);
      expect(sql.endsWith('COMMIT;')).toBe(true);
    }
  });

  it('allows canonical current executions when retired G00 has no execution row', () => {
    const up = databaseFile('migrations/0031_g04_independent_sections.up.sql');

    expect(up).toContain('current_execution_count <> 9');
    expect(up).toContain('retired_g00_execution_count NOT IN (0, 1)');
    expect(up).toContain(
      'fixed_execution_count <> 9 + retired_g00_execution_count',
    );
    expect(up).toContain(
      "shared_template_row_id = 'G05:v1'\n              AND task_code = 'G00'\n              AND status = 'RETIRED'",
    );
    expect(up).toContain(
      '{"estimatedMinutes":15,"allowRetry":true,"contentStatus":"READY","contentVersion":"2026-08-06","pendingReason":null}',
    );
  });

  it('creates and removes 0032 inside explicit transactions', () => {
    const up = databaseFile(
      'migrations/0032_first_login_onboarding.up.sql',
    ).trim();
    const down = databaseFile(
      'migrations/0032_first_login_onboarding.down.sql',
    ).trim();

    expect(up.startsWith('BEGIN;')).toBe(true);
    expect(up.endsWith('COMMIT;')).toBe(true);
    expect(down.startsWith('BEGIN;')).toBe(true);
    expect(down.endsWith('COMMIT;')).toBe(true);
    expect(up).toContain('CREATE TABLE tide.account_onboarding_states');
    expect(up).toContain("security_event.event_type = 'LOGIN'");
    expect(up).toContain("security_event.outcome = 'SUCCESS'");
    expect(up).toContain("'MIGRATED_EXISTING'");
    expect(up).toContain('char_length(idempotency_key) BETWEEN 8 AND 128');
    expect(up).toContain('AND request_hash IS NOT NULL');
    for (const guideCode of [
      'FIRST_LOGIN',
      'MY_TIDE_OVERVIEW',
      'SCORE_DETAILS',
      'TASK_PATH',
      'TASK_RESULT',
      'MESSAGES_TICKETS',
      'HELP_ROUTES',
      'PERSONALIZED_TASK_FIRST',
    ]) {
      expect(up).toContain(`'${guideCode}'`);
    }
    expect(up).toContain(
      "status <> 'MIGRATED_EXISTING'\n            OR guide_code = 'FIRST_LOGIN'",
    );
    expect(down).toContain(
      'DROP TABLE IF EXISTS tide.account_onboarding_states',
    );
  });

  it('removes only the active G04 device definition in forward-only 0037', () => {
    const up = databaseFile(
      'migrations/0037_g04_remove_device_check.up.sql',
    ).trim();
    const down = databaseFile(
      'migrations/0037_g04_remove_device_check.down.sql',
    ).trim();

    expect(up.startsWith('BEGIN;')).toBe(true);
    expect(up.endsWith('COMMIT;')).toBe(true);
    expect(down.startsWith('BEGIN;')).toBe(true);
    expect(down.endsWith('COMMIT;')).toBe(true);
    expect(up).toContain("definition.step_key = 'g02-device-check'");
    expect(up).toContain('DELETE FROM tide.task_step_definitions');
    expect(up).toContain(
      '{"requiredStepKeys":["g02-environment-photo","g02-courseware-confirmation"]}',
    );
    expect(up).toContain('g04_remaining_step_identity_before');
    expect(up).toContain('g04_rule_identity_before');
    expect(up).toContain('g04_assignment_before');
    expect(up).toContain('g04_progress_before');
    expect(up).toContain('g04_device_run_before');
    expect(up).toContain('g04_device_item_before');
    expect(up).toContain(
      'G04 execution, steps and completion rule form an unreviewed mixed shape',
    );
    expect(down).toContain('is forward-only');
    expect(down).not.toContain('INSERT INTO tide.task_step_definitions');
  });

  it('skips historical 0031 only when local G04 is already exact 0037', () => {
    const apply = databaseFile('scripts/apply.sh');

    expect(apply).toContain('g04_two_part_execution_ready');
    expect(apply).toContain('exact 0037 two-part execution');
    expect(apply).toMatch(
      /if \[\[ "\$\{g04_two_part_execution_ready\}" == "t" \]\];[\s\S]*else[\s\S]*0031_g04_independent_sections\.up\.sql[\s\S]*fi/,
    );
    expect(apply).toMatch(
      /0033_g01_tesol_only\.up\.sql[\s\S]*seed\/0005_mock_g04_two_part_catalog\.sql[\s\S]*0037_g04_remove_device_check\.up\.sql/,
    );
  });

  it('checks every cross-schema stage before production migration writes', () => {
    const production = databaseFile('scripts/apply-production.sh');

    expect(production).toContain(
      'teacher 0032 要求 public head 50 的精确 G04 三段副本',
    );
    expect(production).toContain(
      "min(version_num) = '20260810_50_g04_sections'",
    );
    expect(production).toContain(
      'teacher 0037 只能从 teacher 0032、0033 的连续状态继续',
    );
    expect(production).toContain(
      'teacher 0037 要求 public head 54 中同时存在 rev51 G01 TESOL-only 精确副本与 G04 两段精确副本',
    );
    expect(production).toContain(
      "min(version_num) = '20260811_54_g04_remove_device_check'",
    );
    expect(production.indexOf('current_tide_head=')).toBeLessThan(
      production.indexOf('for migration_id in "${TARGET_MIGRATIONS[@]}"'),
    );
    expect(production).toContain("payload->>'ops_name_zh' = '首课准备'");
  });
});
