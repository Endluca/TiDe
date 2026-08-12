BEGIN;

SET LOCAL lock_timeout = '10s';

-- Current G02 keeps the stable operations identity G03:v1. Upgrade only an
-- existing execution in place; a catalog containing only personalized
-- executions still has no fixed-task catalog and therefore remains unchanged.
DO $$
BEGIN
    IF to_regclass('public.task_templates') IS NULL
       OR to_regclass('public.task_assignments') IS NULL
       OR to_regclass('tide.task_execution_versions') IS NULL
       OR to_regclass('tide.task_step_definitions') IS NULL
       OR to_regclass('tide.task_validation_rules') IS NULL
       OR to_regclass('tide.task_step_progress') IS NULL THEN
        RAISE EXCEPTION
            'shared task tables and Tide execution tables are required before migration 0039';
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
    execution_id uuid;
    step_count integer;
    rule_count integer;
BEGIN
    IF (
        SELECT count(*)
        FROM public.task_templates
        WHERE row_id = 'G03:v1'
          AND template_id = 'G02'
          AND status = 'PUBLISHED'
          AND execution_owner = 'TEACHER_APP'
          AND payload->>'category' = 'MANDATORY_GROWTH'
          AND payload->>'title' = 'Platform Policies'
          AND (payload->>'score_value')::integer = 2
    ) <> 1 THEN
        RAISE EXCEPTION
            'operations G02 stable template G03:v1 is not the approved published row for migration 0039';
    END IF;

    SELECT id INTO execution_id
    FROM tide.task_execution_versions
    WHERE shared_template_row_id = 'G03:v1';

    IF execution_id IS NULL THEN
        IF EXISTS (
            SELECT 1
            FROM tide.task_execution_versions AS execution
            JOIN public.task_templates AS template
              ON template.row_id = execution.shared_template_row_id
            WHERE template.payload->>'category' = 'MANDATORY_GROWTH'
        ) THEN
            RAISE EXCEPTION
                'existing execution catalog is missing the stable G02 execution G03:v1';
        END IF;
        RAISE NOTICE
            'migration 0039 left the empty G02 execution unchanged; run the explicit current catalog seed before rollout';
        RETURN;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM tide.task_execution_versions
        WHERE task_code = 'G02'
          AND shared_template_row_id <> 'G03:v1'
    ) OR EXISTS (
        SELECT 1
        FROM tide.task_execution_versions
        WHERE id = execution_id
          AND (
              task_code <> 'G02'
              OR status <> 'ACTIVE'
              OR execution_contract_version NOT IN ('v1', 'task-contract-v3')
              OR config NOT IN (
                  '{"estimatedMinutes":15,"allowRetry":true,"contentStatus":"READY","contentVersion":"2026-07-30","pendingReason":null}'::jsonb,
                  '{"estimatedMinutes":15,"allowRetry":true,"contentStatus":"READY","contentVersion":"2026-08-06","pendingReason":null}'::jsonb,
                  '{"estimatedMinutes":35,"allowRetry":true,"contentStatus":"READY","contentVersion":"2026-07-24-overseas-nt-policies-v1","pendingReason":null}'::jsonb
              )
          )
    ) THEN
        RAISE EXCEPTION
            'G02 execution identity or config is not an approved pre-0039/current shape';
    END IF;

    SELECT count(*) INTO step_count
    FROM tide.task_step_definitions
    WHERE execution_version_id = execution_id;

    IF step_count NOT IN (0, 1) OR EXISTS (
        SELECT 1
        FROM tide.task_step_definitions
        WHERE execution_version_id = execution_id
          AND NOT (
              step_key = 'g02-policy-document'
              AND position = 1
              AND step_type = 'DOCUMENT'
              AND title = 'Read Overseas NT Policies'
              AND config = '{"role":"POLICY_DOCUMENT","documentCode":"overseas-nt-policies","sourceTitle":"Overseas NT Policies","sourceNodeId":"OG9lyrgJPzkq5xD6fvzmqRonWzN67Mw4","sourceUpdatedAt":"2026-07-24T01:47:08Z","contentVersion":"2026-07-24-overseas-nt-policies-v1","contentHash":"6875233667c6f3d90602a07c84849dbb88f41685929779a7b0dc79ccf859979c","readingCompletion":"SCROLL_TO_END"}'::jsonb
          )
    ) THEN
        RAISE EXCEPTION
            'G02 contains an unreviewed step; migration 0039 will not replace unknown content';
    END IF;

    SELECT count(*) INTO rule_count
    FROM tide.task_validation_rules
    WHERE execution_version_id = execution_id;

    IF rule_count NOT IN (0, 1) OR EXISTS (
        SELECT 1
        FROM tide.task_validation_rules
        WHERE execution_version_id = execution_id
          AND NOT (
              rule_key = 'all-steps-complete'
              AND rule_type = 'ALL_STEPS_COMPLETE'
              AND rule_version = '2026-08-11-g02-policy-document-v1'
              AND position = 1
              AND config = '{"requiredStepKeys":["g02-policy-document"]}'::jsonb
              AND teacher_failure_copy = '请将当前版本的 Overseas NT Policies 阅读到文档末尾。'
          )
    ) THEN
        RAISE EXCEPTION
            'G02 contains an unreviewed validation rule; migration 0039 will not replace it';
    END IF;
END
$$;

CREATE TEMP TABLE g02_execution_identity_before ON COMMIT DROP AS
SELECT id
FROM tide.task_execution_versions
WHERE shared_template_row_id = 'G03:v1';

CREATE TEMP TABLE g02_assignment_before ON COMMIT DROP AS
SELECT assignment.assignment_id, to_jsonb(assignment) AS row_data
FROM public.task_assignments AS assignment
WHERE assignment.template_version_id = 'G03:v1';

CREATE TEMP TABLE g02_progress_before ON COMMIT DROP AS
SELECT progress.id, to_jsonb(progress) AS row_data
FROM tide.task_step_progress AS progress
JOIN public.task_assignments AS assignment
  ON assignment.assignment_id = progress.task_assignment_id
WHERE assignment.template_version_id = 'G03:v1';

UPDATE tide.task_execution_versions
SET execution_contract_version = 'task-contract-v3',
    config = '{"estimatedMinutes":35,"allowRetry":true,"contentStatus":"READY","contentVersion":"2026-07-24-overseas-nt-policies-v1","pendingReason":null}'::jsonb,
    updated_at = now()
WHERE shared_template_row_id = 'G03:v1';

INSERT INTO tide.task_step_definitions (
    id, execution_version_id, step_key, position, step_type, title, config
)
SELECT
    '50b64071-750c-4c70-87c3-365dee62fae8'::uuid,
    execution.id,
    'g02-policy-document',
    1,
    'DOCUMENT',
    'Read Overseas NT Policies',
    '{"role":"POLICY_DOCUMENT","documentCode":"overseas-nt-policies","sourceTitle":"Overseas NT Policies","sourceNodeId":"OG9lyrgJPzkq5xD6fvzmqRonWzN67Mw4","sourceUpdatedAt":"2026-07-24T01:47:08Z","contentVersion":"2026-07-24-overseas-nt-policies-v1","contentHash":"6875233667c6f3d90602a07c84849dbb88f41685929779a7b0dc79ccf859979c","readingCompletion":"SCROLL_TO_END"}'::jsonb
FROM tide.task_execution_versions AS execution
WHERE execution.shared_template_row_id = 'G03:v1'
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
    '3bc0afe4-2c32-4749-8fca-bab93b0c068c'::uuid,
    execution.id,
    'all-steps-complete',
    'ALL_STEPS_COMPLETE',
    '2026-08-11-g02-policy-document-v1',
    1,
    '{"requiredStepKeys":["g02-policy-document"]}'::jsonb,
    '请将当前版本的 Overseas NT Policies 阅读到文档末尾。'
FROM tide.task_execution_versions AS execution
WHERE execution.shared_template_row_id = 'G03:v1'
ON CONFLICT (execution_version_id, rule_key) DO UPDATE
SET rule_type = EXCLUDED.rule_type,
    rule_version = EXCLUDED.rule_version,
    position = EXCLUDED.position,
    config = EXCLUDED.config,
    teacher_failure_copy = EXCLUDED.teacher_failure_copy;

DO $$
DECLARE
    execution_id uuid;
BEGIN
    IF EXISTS (
        SELECT id FROM g02_execution_identity_before
        EXCEPT
        SELECT id FROM tide.task_execution_versions
        WHERE shared_template_row_id = 'G03:v1'
    ) OR EXISTS (
        SELECT id FROM tide.task_execution_versions
        WHERE shared_template_row_id = 'G03:v1'
        EXCEPT
        SELECT id FROM g02_execution_identity_before
    ) THEN
        RAISE EXCEPTION 'migration 0039 changed G02 execution identity';
    END IF;

    IF EXISTS (
        SELECT assignment_id, row_data FROM g02_assignment_before
        EXCEPT
        SELECT assignment_id, to_jsonb(assignment)
        FROM public.task_assignments AS assignment
        WHERE assignment.template_version_id = 'G03:v1'
    ) OR EXISTS (
        SELECT assignment_id, to_jsonb(assignment)
        FROM public.task_assignments AS assignment
        WHERE assignment.template_version_id = 'G03:v1'
        EXCEPT
        SELECT assignment_id, row_data FROM g02_assignment_before
    ) THEN
        RAISE EXCEPTION 'migration 0039 changed a G02 assignment fact';
    END IF;

    IF EXISTS (
        SELECT id, row_data FROM g02_progress_before
        EXCEPT
        SELECT progress.id, to_jsonb(progress)
        FROM tide.task_step_progress AS progress
        JOIN public.task_assignments AS assignment
          ON assignment.assignment_id = progress.task_assignment_id
        WHERE assignment.template_version_id = 'G03:v1'
    ) OR EXISTS (
        SELECT progress.id, to_jsonb(progress)
        FROM tide.task_step_progress AS progress
        JOIN public.task_assignments AS assignment
          ON assignment.assignment_id = progress.task_assignment_id
        WHERE assignment.template_version_id = 'G03:v1'
        EXCEPT
        SELECT id, row_data FROM g02_progress_before
    ) THEN
        RAISE EXCEPTION 'migration 0039 changed existing G02 progress';
    END IF;

    SELECT id INTO execution_id
    FROM tide.task_execution_versions
    WHERE shared_template_row_id = 'G03:v1';

    IF execution_id IS NULL THEN
        IF EXISTS (
            SELECT 1
            FROM tide.task_execution_versions AS execution
            JOIN public.task_templates AS template
              ON template.row_id = execution.shared_template_row_id
            WHERE template.payload->>'category' = 'MANDATORY_GROWTH'
        ) THEN
            RAISE EXCEPTION
                'migration 0039 left a non-empty execution catalog without G02';
        END IF;
        RETURN;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM tide.task_execution_versions AS execution
        WHERE execution.id = execution_id
          AND execution.shared_template_row_id = 'G03:v1'
          AND execution.task_code = 'G02'
          AND execution.status = 'ACTIVE'
          AND execution.execution_contract_version = 'task-contract-v3'
          AND execution.config = '{"estimatedMinutes":35,"allowRetry":true,"contentStatus":"READY","contentVersion":"2026-07-24-overseas-nt-policies-v1","pendingReason":null}'::jsonb
    ) OR (
        SELECT count(*)
        FROM tide.task_step_definitions
        WHERE execution_version_id = execution_id
    ) <> 1 OR NOT EXISTS (
        SELECT 1
        FROM tide.task_step_definitions AS definition
        WHERE definition.execution_version_id = execution_id
          AND definition.step_key = 'g02-policy-document'
          AND definition.step_type = 'DOCUMENT'
          AND definition.position = 1
          AND definition.title = 'Read Overseas NT Policies'
          AND definition.config = '{"role":"POLICY_DOCUMENT","documentCode":"overseas-nt-policies","sourceTitle":"Overseas NT Policies","sourceNodeId":"OG9lyrgJPzkq5xD6fvzmqRonWzN67Mw4","sourceUpdatedAt":"2026-07-24T01:47:08Z","contentVersion":"2026-07-24-overseas-nt-policies-v1","contentHash":"6875233667c6f3d90602a07c84849dbb88f41685929779a7b0dc79ccf859979c","readingCompletion":"SCROLL_TO_END"}'::jsonb
    ) OR (
        SELECT count(*)
        FROM tide.task_validation_rules
        WHERE execution_version_id = execution_id
    ) <> 1 OR NOT EXISTS (
        SELECT 1
        FROM tide.task_validation_rules AS rule
        WHERE rule.execution_version_id = execution_id
          AND rule.rule_key = 'all-steps-complete'
          AND rule.rule_type = 'ALL_STEPS_COMPLETE'
          AND rule.rule_version = '2026-08-11-g02-policy-document-v1'
          AND rule.position = 1
          AND rule.config = '{"requiredStepKeys":["g02-policy-document"]}'::jsonb
          AND rule.teacher_failure_copy = '请将当前版本的 Overseas NT Policies 阅读到文档末尾。'
    ) THEN
        RAISE EXCEPTION 'migration 0039 G02 document verification failed';
    END IF;
END
$$;

COMMIT;
