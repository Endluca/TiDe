import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const databaseFile = (relativePath: string) =>
  readFileSync(resolve(__dirname, '../../../database', relativePath), 'utf8');

describe('teacher database migration chain', () => {
  it('keeps every local and production entry point on the 0032 head', () => {
    const ddl = databaseFile('ddl.sql');
    const production = databaseFile('scripts/apply-production.sh');

    expect(ddl.trimEnd()).toMatch(
      /\\ir migrations\/0032_first_login_onboarding\.up\.sql$/,
    );
    expect(production).toContain(
      'TARGET_MIGRATION="${TIDE_MIGRATION_TARGET:-0032_first_login_onboarding}"',
    );
    expect(production).toMatch(
      /0025_fixed_task_semantic_alignment\s+0026_kuozhi_course_syncs\s+0027_remove_local_quiz_runtime\s+0028_retire_task_business_change_view\s+0029_remove_unused_tide_objects\s+0030_remove_unused_columns_and_orphan_function\s+0031_g04_independent_sections\s+0032_first_login_onboarding/,
    );
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
});
