-- Formal DMS migration: teacher 0042 -> 0043.
-- Run this file only when the precheck reports teacher head
-- 0042_g09_set_kuozhi_course. The whole file is one transaction.
-- Canonical business SQL:
-- teacher/backend/database/migrations/0043_p_rel_execution_catalog.up.sql
-- Canonical SHA-256:
-- 0bb25fd49de5aac915dfb9a4e52ad567183a97865b4fc4dfd6d0a35d660492bf

BEGIN;

SET LOCAL lock_timeout = '10s';
SET LOCAL statement_timeout = '10min';

SELECT pg_advisory_xact_lock(
    hashtextextended('tide:production-schema-migrations', 0)
);

DO $$
DECLARE
    public_head text;
    teacher_head text;
    teacher_count integer;
BEGIN
    IF current_setting('transaction_read_only')::boolean THEN
        RAISE EXCEPTION 'teacher 0043 migration requires a writable transaction';
    END IF;

    IF to_regclass('public.alembic_version') IS NULL
       OR to_regclass('tide.schema_migrations') IS NULL THEN
        RAISE EXCEPTION 'teacher 0043 migration requires public and tide ledgers';
    END IF;

    SELECT CASE WHEN count(*) = 1 THEN min(version_num) END
    INTO public_head
    FROM public.alembic_version;

    IF public_head IS DISTINCT FROM '20260823_100_scope_snapshot_diff' THEN
        RAISE EXCEPTION
            'teacher 0043 DMS requires public head 100; current=%',
            coalesce(public_head, '<invalid>');
    END IF;

    SELECT count(*), max(migration_id) FILTER (
        WHERE migration_order = (
            SELECT max(migration_order) FROM tide.schema_migrations
        )
    )
    INTO teacher_count, teacher_head
    FROM tide.schema_migrations;

    IF teacher_count <> 37
       OR teacher_head IS DISTINCT FROM '0042_g09_set_kuozhi_course'
       OR NOT EXISTS (
           SELECT 1
           FROM tide.schema_migrations
           WHERE migration_order = 37
             AND migration_id = '0042_g09_set_kuozhi_course'
       ) THEN
        RAISE EXCEPTION
            'teacher 0043 DMS requires exact 37-row ledger ending at 0042; count=%, head=%',
            teacher_count,
            coalesce(teacher_head, '<invalid>');
    END IF;
END
$$;

LOCK TABLE tide.schema_migrations IN SHARE ROW EXCLUSIVE MODE;

SET LOCAL lock_timeout = '10s';

-- Publish only the two confirmed reliability execution paths. The shared
-- templates remain operations-owned; this migration preserves their stable
-- assignment and progress facts while adding teacher-owned execution data.
DO $$
BEGIN
    IF to_regclass('public.task_templates') IS NULL
       OR to_regclass('public.task_assignments') IS NULL
       OR to_regclass('tide.task_execution_versions') IS NULL
       OR to_regclass('tide.task_step_definitions') IS NULL
       OR to_regclass('tide.task_validation_rules') IS NULL
       OR to_regclass('tide.task_step_progress') IS NULL THEN
        RAISE EXCEPTION
            'shared task tables and Tide execution tables are required before migration 0043';
    END IF;
END
$$;

LOCK TABLE public.task_templates IN SHARE MODE;
LOCK TABLE public.task_assignments IN SHARE MODE;
LOCK TABLE tide.task_execution_versions IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE tide.task_step_definitions IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE tide.task_validation_rules IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE tide.task_step_progress IN SHARE MODE;

DO $$
DECLARE
    template_count integer;
    memo_execution_id uuid;
    attendance_execution_id uuid;
    memo_step_count integer;
    memo_rule_count integer;
BEGIN
    SELECT count(*) INTO template_count
    FROM public.task_templates
    WHERE row_id IN ('P-REL-MEMO:v1', 'P-REL-ATTENDANCE:v1');

    IF template_count = 0
       AND to_regclass('public.alembic_version') IS NULL THEN
        RAISE NOTICE
            'migration 0043 left the local fixture unchanged because reliability shared templates are absent';
        RETURN;
    END IF;
    IF template_count <> 2 THEN
        RAISE EXCEPTION
            'migration 0043 requires both stable reliability shared templates';
    END IF;

    IF (
        SELECT count(*)
        FROM public.task_templates
        WHERE row_id = 'P-REL-MEMO:v1'
          AND template_id = 'P-REL-MEMO'
          AND template_version = 1
          AND status = 'PUBLISHED'
          AND output_type = 'TEACHER_TASK'
          AND execution_owner = 'TEACHER_APP'
          AND integration_mode = 'OUTBOUND_MANAGED'
          AND source_mode = 'REAL'
          AND payload->>'category' = 'PERSONALIZED_IMPROVEMENT'
          AND payload->>'title' = 'Lesson Memo Improvement'
          AND payload->>'why_template' =
              'A completed lesson was recorded with a blank Lesson Memo.'
          AND payload->>'how_summary' =
              'Complete the Lesson Memo guidance and review how to submit an accurate memo after every lesson.'
          AND payload->>'completion_standard' =
              'The teacher app marks the assigned Lesson Memo learning activity as completed.'
          AND payload->>'content_status' = 'READY'
          AND payload->>'score_type' = 'ZERO'
          AND payload->'score_value' = '0'::jsonb
    ) <> 1 THEN
        RAISE EXCEPTION
            'migration 0043 requires the approved published zero-point P-REL-MEMO:v1 template';
    END IF;

    IF (
        SELECT count(*)
        FROM public.task_templates
        WHERE row_id = 'P-REL-ATTENDANCE:v1'
          AND template_id = 'P-REL-ATTENDANCE'
          AND template_version = 1
          AND status = 'PUBLISHED'
          AND output_type = 'TEACHER_TASK'
          AND execution_owner = 'TEACHER_APP'
          AND integration_mode = 'OUTBOUND_MANAGED'
          AND source_mode = 'REAL'
          AND payload->>'category' = 'PERSONALIZED_IMPROVEMENT'
          AND payload->>'title' = 'Attendance Improvement'
          AND payload->>'why_template' =
              'A lesson record shows a reliability issue, such as an absence, late arrival, or early leave.'
          AND payload->>'how_summary' =
              'Complete the assigned attendance training and pass its quiz.'
          AND payload->>'completion_standard' =
              'The teacher app marks the training and quiz as completed.'
          AND payload->>'content_status' = 'READY'
          AND payload->>'score_type' = 'ZERO'
          AND payload->'score_value' = '0'::jsonb
    ) <> 1 THEN
        RAISE EXCEPTION
            'migration 0043 requires the approved published zero-point P-REL-ATTENDANCE:v1 template';
    END IF;

    SELECT id INTO memo_execution_id
    FROM tide.task_execution_versions
    WHERE shared_template_row_id = 'P-REL-MEMO:v1';

    IF memo_execution_id IS NULL THEN
        IF EXISTS (
            SELECT 1 FROM tide.task_execution_versions
            WHERE task_code = 'P-REL-MEMO'
               OR id = 'b0370e08-0b2a-4b95-8ea1-356c6f618deb'::uuid
        ) THEN
            RAISE EXCEPTION
                'migration 0043 cannot create the deterministic P-REL-MEMO execution';
        END IF;
        INSERT INTO tide.task_execution_versions (
            id, shared_template_row_id, task_code,
            execution_contract_version, config, status
        ) VALUES (
            'b0370e08-0b2a-4b95-8ea1-356c6f618deb'::uuid,
            'P-REL-MEMO:v1',
            'P-REL-MEMO',
            'task-contract-v3',
            '{"estimatedMinutes":6,"allowRetry":true,"contentStatus":"READY","contentVersion":"2026-07-24-lesson-memo-rules-v1","pendingReason":null}'::jsonb,
            'ACTIVE'
        );
        memo_execution_id := 'b0370e08-0b2a-4b95-8ea1-356c6f618deb'::uuid;
    END IF;

    SELECT id INTO attendance_execution_id
    FROM tide.task_execution_versions
    WHERE shared_template_row_id = 'P-REL-ATTENDANCE:v1';

    IF attendance_execution_id IS NULL THEN
        IF EXISTS (
            SELECT 1 FROM tide.task_execution_versions
            WHERE task_code = 'P-REL-ATTENDANCE'
               OR id = 'a2044750-e7e4-4d07-8846-9d0e66539abf'::uuid
        ) THEN
            RAISE EXCEPTION
                'migration 0043 cannot create the deterministic P-REL-ATTENDANCE execution';
        END IF;
        INSERT INTO tide.task_execution_versions (
            id, shared_template_row_id, task_code,
            execution_contract_version, config, status
        ) VALUES (
            'a2044750-e7e4-4d07-8846-9d0e66539abf'::uuid,
            'P-REL-ATTENDANCE:v1',
            'P-REL-ATTENDANCE',
            'task-contract-v3',
            '{"estimatedMinutes":12,"allowRetry":true,"contentStatus":"READY","contentVersion":"2026-08-22-reliability-course-595-v1","pendingReason":null}'::jsonb,
            'ACTIVE'
        );
        attendance_execution_id :=
            'a2044750-e7e4-4d07-8846-9d0e66539abf'::uuid;
    END IF;

    IF EXISTS (
        SELECT 1 FROM tide.task_execution_versions
        WHERE task_code = 'P-REL-MEMO'
          AND shared_template_row_id <> 'P-REL-MEMO:v1'
    ) OR EXISTS (
        SELECT 1 FROM tide.task_execution_versions
        WHERE id = memo_execution_id
          AND (
              task_code <> 'P-REL-MEMO'
              OR status <> 'ACTIVE'
              OR execution_contract_version NOT IN ('v1', 'task-contract-v3')
              OR config NOT IN (
                  '{"estimatedMinutes":8,"allowRetry":true,"contentStatus":"PENDING","contentVersion":"2026-07-30","pendingReason":"JIAHE_PERSONALIZED_CONTENT_PENDING"}'::jsonb,
                  '{"estimatedMinutes":8,"allowRetry":true,"contentStatus":"PENDING","contentVersion":"2026-08-06","pendingReason":"JIAHE_PERSONALIZED_CONTENT_PENDING"}'::jsonb,
                  '{"estimatedMinutes":6,"allowRetry":true,"contentStatus":"READY","contentVersion":"2026-07-24-lesson-memo-rules-v1","pendingReason":null}'::jsonb
              )
          )
    ) THEN
        RAISE EXCEPTION
            'P-REL-MEMO execution identity or config is not an approved pre-0043/current shape';
    END IF;

    IF EXISTS (
        SELECT 1 FROM tide.task_execution_versions
        WHERE task_code = 'P-REL-ATTENDANCE'
          AND shared_template_row_id <> 'P-REL-ATTENDANCE:v1'
    ) OR EXISTS (
        SELECT 1 FROM tide.task_execution_versions
        WHERE id = attendance_execution_id
          AND (
              task_code <> 'P-REL-ATTENDANCE'
              OR status <> 'ACTIVE'
              OR execution_contract_version NOT IN ('v1', 'task-contract-v3')
              OR config NOT IN (
                  '{"estimatedMinutes":8,"allowRetry":true,"contentStatus":"PENDING","contentVersion":"2026-07-30","pendingReason":"JIAHE_PERSONALIZED_CONTENT_PENDING"}'::jsonb,
                  '{"estimatedMinutes":8,"allowRetry":true,"contentStatus":"PENDING","contentVersion":"2026-08-06","pendingReason":"JIAHE_PERSONALIZED_CONTENT_PENDING"}'::jsonb,
                  '{"estimatedMinutes":12,"allowRetry":true,"contentStatus":"READY","contentVersion":"2026-08-22-reliability-course-595-v1","pendingReason":null}'::jsonb
              )
          )
    ) THEN
        RAISE EXCEPTION
            'P-REL-ATTENDANCE execution identity or config is not an approved pre-0043/current shape';
    END IF;

    IF EXISTS (
        SELECT 1 FROM tide.task_step_definitions
        WHERE execution_version_id = attendance_execution_id
    ) OR EXISTS (
        SELECT 1 FROM tide.task_validation_rules
        WHERE execution_version_id = attendance_execution_id
    ) THEN
        RAISE EXCEPTION
            'P-REL-ATTENDANCE contains unreviewed local steps or rules';
    END IF;

    SELECT count(*) INTO memo_step_count
    FROM tide.task_step_definitions
    WHERE execution_version_id = memo_execution_id;
    SELECT count(*) INTO memo_rule_count
    FROM tide.task_validation_rules
    WHERE execution_version_id = memo_execution_id;

    IF memo_step_count NOT IN (0, 1) OR EXISTS (
        SELECT 1 FROM tide.task_step_definitions
        WHERE execution_version_id = memo_execution_id
          AND NOT (
              step_key = 'p-rel-memo-document'
              AND position = 1
              AND step_type = 'DOCUMENT'
              AND title = 'Read Lesson Memo Rules'
              AND config = '{"role":"LESSON_MEMO_RULES_DOCUMENT","documentCode":"lesson-memo-rules","sourceTitle":"Lesson Memo Rules","sourceNodeId":"OG9lyrgJPzkq5xD6fvzmqRonWzN67Mw4","sourceUpdatedAt":"2026-07-24T01:47:08Z","contentVersion":"2026-07-24-lesson-memo-rules-v1","contentHash":"43dde8551988fa167103510da304feac63b853aa03d6c75747086932bf111b51","readingCompletion":"SCROLL_TO_END"}'::jsonb
          )
    ) THEN
        RAISE EXCEPTION 'P-REL-MEMO contains an unreviewed local step';
    END IF;

    IF memo_rule_count NOT IN (0, 1) OR EXISTS (
        SELECT 1 FROM tide.task_validation_rules
        WHERE execution_version_id = memo_execution_id
          AND NOT (
              rule_key = 'all-steps-complete'
              AND rule_type = 'ALL_STEPS_COMPLETE'
              AND rule_version = '2026-07-24-lesson-memo-rules-v1'
              AND position = 1
              AND config = '{"requiredStepKeys":["p-rel-memo-document"]}'::jsonb
              AND teacher_failure_copy =
                  '请将当前版本的 Lesson Memo Rules 阅读到文档末尾。'
          )
    ) THEN
        RAISE EXCEPTION 'P-REL-MEMO contains an unreviewed validation rule';
    END IF;

    IF (memo_step_count = 0) <> (memo_rule_count = 0) THEN
        RAISE EXCEPTION
            'P-REL-MEMO has a partial document execution; migration 0043 will not infer missing definitions';
    END IF;
END
$$;

CREATE TEMP TABLE p_rel_execution_identity_before ON COMMIT DROP AS
SELECT shared_template_row_id, id
FROM tide.task_execution_versions
WHERE shared_template_row_id IN (
    'P-REL-MEMO:v1', 'P-REL-ATTENDANCE:v1'
);

CREATE TEMP TABLE p_rel_assignment_before ON COMMIT DROP AS
SELECT assignment.assignment_id, to_jsonb(assignment) AS row_data
FROM public.task_assignments AS assignment
WHERE assignment.template_version_id IN (
    'P-REL-MEMO:v1', 'P-REL-ATTENDANCE:v1'
);

CREATE TEMP TABLE p_rel_progress_before ON COMMIT DROP AS
SELECT progress.id, to_jsonb(progress) AS row_data
FROM tide.task_step_progress AS progress
JOIN public.task_assignments AS assignment
  ON assignment.assignment_id = progress.task_assignment_id
WHERE assignment.template_version_id IN (
    'P-REL-MEMO:v1', 'P-REL-ATTENDANCE:v1'
);

UPDATE tide.task_execution_versions
SET execution_contract_version = 'task-contract-v3',
    config = '{"estimatedMinutes":6,"allowRetry":true,"contentStatus":"READY","contentVersion":"2026-07-24-lesson-memo-rules-v1","pendingReason":null}'::jsonb,
    updated_at = now()
WHERE shared_template_row_id = 'P-REL-MEMO:v1';

UPDATE tide.task_execution_versions
SET execution_contract_version = 'task-contract-v3',
    config = '{"estimatedMinutes":12,"allowRetry":true,"contentStatus":"READY","contentVersion":"2026-08-22-reliability-course-595-v1","pendingReason":null}'::jsonb,
    updated_at = now()
WHERE shared_template_row_id = 'P-REL-ATTENDANCE:v1';

INSERT INTO tide.task_step_definitions (
    id, execution_version_id, step_key, position, step_type, title, config
)
SELECT
    'e21ab6c9-f7c9-45c6-8447-cf219f589a48'::uuid,
    execution.id,
    'p-rel-memo-document',
    1,
    'DOCUMENT',
    'Read Lesson Memo Rules',
    '{"role":"LESSON_MEMO_RULES_DOCUMENT","documentCode":"lesson-memo-rules","sourceTitle":"Lesson Memo Rules","sourceNodeId":"OG9lyrgJPzkq5xD6fvzmqRonWzN67Mw4","sourceUpdatedAt":"2026-07-24T01:47:08Z","contentVersion":"2026-07-24-lesson-memo-rules-v1","contentHash":"43dde8551988fa167103510da304feac63b853aa03d6c75747086932bf111b51","readingCompletion":"SCROLL_TO_END"}'::jsonb
FROM tide.task_execution_versions AS execution
WHERE execution.shared_template_row_id = 'P-REL-MEMO:v1'
ON CONFLICT (execution_version_id, step_key) DO UPDATE
SET position = EXCLUDED.position,
    step_type = EXCLUDED.step_type,
    title = EXCLUDED.title,
    config = EXCLUDED.config;

INSERT INTO tide.task_validation_rules (
    id, execution_version_id, rule_key, rule_type, rule_version,
    position, config, teacher_failure_copy
)
SELECT
    '480f8006-d834-4d4c-8112-bbc544dcb827'::uuid,
    execution.id,
    'all-steps-complete',
    'ALL_STEPS_COMPLETE',
    '2026-07-24-lesson-memo-rules-v1',
    1,
    '{"requiredStepKeys":["p-rel-memo-document"]}'::jsonb,
    '请将当前版本的 Lesson Memo Rules 阅读到文档末尾。'
FROM tide.task_execution_versions AS execution
WHERE execution.shared_template_row_id = 'P-REL-MEMO:v1'
ON CONFLICT (execution_version_id, rule_key) DO UPDATE
SET rule_type = EXCLUDED.rule_type,
    rule_version = EXCLUDED.rule_version,
    position = EXCLUDED.position,
    config = EXCLUDED.config,
    teacher_failure_copy = EXCLUDED.teacher_failure_copy;

DO $$
DECLARE
    template_count integer;
    memo_execution_id uuid;
    attendance_execution_id uuid;
BEGIN
    SELECT count(*) INTO template_count
    FROM public.task_templates
    WHERE row_id IN ('P-REL-MEMO:v1', 'P-REL-ATTENDANCE:v1');
    IF template_count = 0 THEN
        RETURN;
    END IF;

    SELECT id INTO memo_execution_id
    FROM tide.task_execution_versions
    WHERE shared_template_row_id = 'P-REL-MEMO:v1';
    SELECT id INTO attendance_execution_id
    FROM tide.task_execution_versions
    WHERE shared_template_row_id = 'P-REL-ATTENDANCE:v1';

    IF EXISTS (
        SELECT shared_template_row_id, id FROM p_rel_execution_identity_before
        EXCEPT
        SELECT shared_template_row_id, id
        FROM tide.task_execution_versions
        WHERE shared_template_row_id IN (
            'P-REL-MEMO:v1', 'P-REL-ATTENDANCE:v1'
        )
    ) OR EXISTS (
        SELECT shared_template_row_id, id
        FROM tide.task_execution_versions
        WHERE shared_template_row_id IN (
            'P-REL-MEMO:v1', 'P-REL-ATTENDANCE:v1'
        )
        EXCEPT
        SELECT shared_template_row_id, id FROM p_rel_execution_identity_before
    ) THEN
        RAISE EXCEPTION 'migration 0043 changed a reliability execution identity';
    END IF;

    IF EXISTS (
        (SELECT assignment_id, row_data FROM p_rel_assignment_before
         EXCEPT
         SELECT assignment.assignment_id, to_jsonb(assignment)
         FROM public.task_assignments AS assignment
         WHERE assignment.template_version_id IN (
             'P-REL-MEMO:v1', 'P-REL-ATTENDANCE:v1'
         ))
        UNION ALL
        (SELECT assignment.assignment_id, to_jsonb(assignment)
         FROM public.task_assignments AS assignment
         WHERE assignment.template_version_id IN (
             'P-REL-MEMO:v1', 'P-REL-ATTENDANCE:v1'
         )
         EXCEPT
         SELECT assignment_id, row_data FROM p_rel_assignment_before)
    ) THEN
        RAISE EXCEPTION 'migration 0043 changed a reliability assignment fact';
    END IF;

    IF EXISTS (
        (SELECT id, row_data FROM p_rel_progress_before
         EXCEPT
         SELECT progress.id, to_jsonb(progress)
         FROM tide.task_step_progress AS progress
         JOIN public.task_assignments AS assignment
           ON assignment.assignment_id = progress.task_assignment_id
         WHERE assignment.template_version_id IN (
             'P-REL-MEMO:v1', 'P-REL-ATTENDANCE:v1'
         ))
        UNION ALL
        (SELECT progress.id, to_jsonb(progress)
         FROM tide.task_step_progress AS progress
         JOIN public.task_assignments AS assignment
           ON assignment.assignment_id = progress.task_assignment_id
         WHERE assignment.template_version_id IN (
             'P-REL-MEMO:v1', 'P-REL-ATTENDANCE:v1'
         )
         EXCEPT
         SELECT id, row_data FROM p_rel_progress_before)
    ) THEN
        RAISE EXCEPTION 'migration 0043 changed reliability progress';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM tide.task_execution_versions
        WHERE id = memo_execution_id
          AND task_code = 'P-REL-MEMO'
          AND status = 'ACTIVE'
          AND execution_contract_version = 'task-contract-v3'
          AND config = '{"estimatedMinutes":6,"allowRetry":true,"contentStatus":"READY","contentVersion":"2026-07-24-lesson-memo-rules-v1","pendingReason":null}'::jsonb
    ) OR NOT EXISTS (
        SELECT 1 FROM tide.task_execution_versions
        WHERE id = attendance_execution_id
          AND task_code = 'P-REL-ATTENDANCE'
          AND status = 'ACTIVE'
          AND execution_contract_version = 'task-contract-v3'
          AND config = '{"estimatedMinutes":12,"allowRetry":true,"contentStatus":"READY","contentVersion":"2026-08-22-reliability-course-595-v1","pendingReason":null}'::jsonb
    ) OR EXISTS (
        SELECT 1 FROM tide.task_step_definitions
        WHERE execution_version_id = attendance_execution_id
    ) OR EXISTS (
        SELECT 1 FROM tide.task_validation_rules
        WHERE execution_version_id = attendance_execution_id
    ) OR (
        SELECT count(*) FROM tide.task_step_definitions
        WHERE execution_version_id = memo_execution_id
    ) <> 1 OR NOT EXISTS (
        SELECT 1 FROM tide.task_step_definitions
        WHERE execution_version_id = memo_execution_id
          AND step_key = 'p-rel-memo-document'
          AND position = 1
          AND step_type = 'DOCUMENT'
          AND title = 'Read Lesson Memo Rules'
          AND config = '{"role":"LESSON_MEMO_RULES_DOCUMENT","documentCode":"lesson-memo-rules","sourceTitle":"Lesson Memo Rules","sourceNodeId":"OG9lyrgJPzkq5xD6fvzmqRonWzN67Mw4","sourceUpdatedAt":"2026-07-24T01:47:08Z","contentVersion":"2026-07-24-lesson-memo-rules-v1","contentHash":"43dde8551988fa167103510da304feac63b853aa03d6c75747086932bf111b51","readingCompletion":"SCROLL_TO_END"}'::jsonb
    ) OR (
        SELECT count(*) FROM tide.task_validation_rules
        WHERE execution_version_id = memo_execution_id
    ) <> 1 OR NOT EXISTS (
        SELECT 1 FROM tide.task_validation_rules
        WHERE execution_version_id = memo_execution_id
          AND rule_key = 'all-steps-complete'
          AND rule_type = 'ALL_STEPS_COMPLETE'
          AND rule_version = '2026-07-24-lesson-memo-rules-v1'
          AND position = 1
          AND config = '{"requiredStepKeys":["p-rel-memo-document"]}'::jsonb
          AND teacher_failure_copy =
              '请将当前版本的 Lesson Memo Rules 阅读到文档末尾。'
    ) THEN
        RAISE EXCEPTION
            'migration 0043 reliability execution publication verification failed';
    END IF;
END
$$;

INSERT INTO tide.schema_migrations (
    migration_id,
    migration_order,
    filename,
    sha256
) VALUES (
    '0043_p_rel_execution_catalog',
    38,
    '0043_p_rel_execution_catalog.up.sql',
    '0bb25fd49de5aac915dfb9a4e52ad567183a97865b4fc4dfd6d0a35d660492bf'
);

DO $$
BEGIN
    IF (SELECT count(*) FROM tide.schema_migrations) <> 38
       OR NOT EXISTS (
           SELECT 1
           FROM tide.schema_migrations
           WHERE migration_id = '0043_p_rel_execution_catalog'
             AND migration_order = 38
             AND filename = '0043_p_rel_execution_catalog.up.sql'
             AND sha256 =
                 '0bb25fd49de5aac915dfb9a4e52ad567183a97865b4fc4dfd6d0a35d660492bf'
       ) THEN
        RAISE EXCEPTION 'teacher 0043 migration ledger verification failed';
    END IF;
END
$$;

COMMIT;

SELECT migration_id, migration_order, filename, sha256, applied_at
FROM tide.schema_migrations
ORDER BY migration_order;
