BEGIN;

SET LOCAL lock_timeout = '10s';

-- G01 keeps the stable operations template, execution, assignment and progress
-- identities. Only the existing external-status rule is narrowed to TESOL.
DO $$
BEGIN
    IF to_regclass('public.task_templates') IS NULL
       OR to_regclass('public.task_assignments') IS NULL
       OR to_regclass('tide.task_execution_versions') IS NULL
       OR to_regclass('tide.task_step_definitions') IS NULL
       OR to_regclass('tide.task_validation_rules') IS NULL
       OR to_regclass('tide.task_step_progress') IS NULL THEN
        RAISE EXCEPTION
            'shared task tables and Tide execution tables are required before migration 0033';
    END IF;
END
$$;

LOCK TABLE public.task_templates IN SHARE MODE;
LOCK TABLE public.task_assignments IN SHARE MODE;
LOCK TABLE tide.task_execution_versions IN SHARE MODE;
LOCK TABLE tide.task_step_definitions IN SHARE MODE;
LOCK TABLE tide.task_validation_rules IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE tide.task_step_progress IN SHARE MODE;

DO $$
DECLARE
    execution_id uuid;
BEGIN
    IF (
        SELECT count(*)
        FROM public.task_templates
        WHERE row_id = 'G01:v1'
          AND template_id = 'G01'
          AND status = 'PUBLISHED'
          AND execution_owner = 'TEACHER_APP'
          AND payload->>'category' = 'MANDATORY_GROWTH'
          AND payload->>'title' = 'Profile & Credentials Completion'
          AND (payload->>'score_value')::integer = 3
    ) <> 1 THEN
        RAISE EXCEPTION
            'operations G01 stable template G01:v1 is not the approved published row for migration 0033';
    END IF;

    SELECT id INTO execution_id
    FROM tide.task_execution_versions
    WHERE shared_template_row_id = 'G01:v1';

    IF execution_id IS NULL THEN
        IF EXISTS (SELECT 1 FROM tide.task_execution_versions) THEN
            RAISE EXCEPTION
                'existing execution catalog is missing the stable G01 execution G01:v1';
        END IF;
        RAISE NOTICE
            'migration 0033 left the empty G01 execution unchanged; run the explicit current catalog seed before rollout';
        RETURN;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM tide.task_execution_versions
        WHERE task_code = 'G01'
          AND shared_template_row_id <> 'G01:v1'
    ) OR EXISTS (
        SELECT 1
        FROM tide.task_execution_versions
        WHERE id = execution_id
          AND (
              task_code <> 'G01'
              OR status <> 'ACTIVE'
              OR execution_contract_version NOT IN ('v1', 'task-contract-v3')
          )
    ) THEN
        RAISE EXCEPTION
            'G01 execution identity is not an approved stable shape for migration 0033';
    END IF;

    IF (
        SELECT count(*)
        FROM tide.task_validation_rules
        WHERE execution_version_id = execution_id
          AND (
              rule_key = 'g01-external-status'
              OR rule_type = 'G01_EXTERNAL_STATUS'
          )
    ) <> 1 OR NOT EXISTS (
        SELECT 1
        FROM tide.task_validation_rules
        WHERE execution_version_id = execution_id
          AND rule_key = 'g01-external-status'
          AND rule_type = 'G01_EXTERNAL_STATUS'
          AND position = 3
          AND config = '{}'::jsonb
          AND (
              (
                  rule_version = '2026-07-22'
                  AND teacher_failure_copy =
                      'Self-intro 和 TESOL 真实状态尚未全部通过。'
              )
              OR
              (
                  rule_version = '2026-08-11-tesol-only-v1'
                  AND teacher_failure_copy = 'TESOL 真实状态尚未通过。'
              )
          )
    ) THEN
        RAISE EXCEPTION
            'G01 external-status rule is missing or has an unreviewed shape before migration 0033';
    END IF;
END
$$;

CREATE TEMP TABLE g01_execution_before ON COMMIT DROP AS
SELECT execution.id, to_jsonb(execution) AS row_data
FROM tide.task_execution_versions AS execution
WHERE execution.shared_template_row_id = 'G01:v1';

CREATE TEMP TABLE g01_assignment_before ON COMMIT DROP AS
SELECT assignment.assignment_id, to_jsonb(assignment) AS row_data
FROM public.task_assignments AS assignment
WHERE assignment.template_version_id = 'G01:v1';

CREATE TEMP TABLE g01_progress_before ON COMMIT DROP AS
SELECT progress.id, to_jsonb(progress) AS row_data
FROM tide.task_step_progress AS progress
JOIN public.task_assignments AS assignment
  ON assignment.assignment_id = progress.task_assignment_id
WHERE assignment.template_version_id = 'G01:v1';

CREATE TEMP TABLE g01_steps_before ON COMMIT DROP AS
SELECT definition.id, to_jsonb(definition) AS row_data
FROM tide.task_step_definitions AS definition
JOIN tide.task_execution_versions AS execution
  ON execution.id = definition.execution_version_id
WHERE execution.shared_template_row_id = 'G01:v1';

CREATE TEMP TABLE g01_other_rules_before ON COMMIT DROP AS
SELECT rule.id, to_jsonb(rule) AS row_data
FROM tide.task_validation_rules AS rule
JOIN tide.task_execution_versions AS execution
  ON execution.id = rule.execution_version_id
WHERE execution.shared_template_row_id = 'G01:v1'
  AND rule.rule_key <> 'g01-external-status';

CREATE TEMP TABLE g01_external_rule_before ON COMMIT DROP AS
SELECT
    rule.id,
    (to_jsonb(rule) - 'rule_version') - 'teacher_failure_copy' AS immutable_row
FROM tide.task_validation_rules AS rule
JOIN tide.task_execution_versions AS execution
  ON execution.id = rule.execution_version_id
WHERE execution.shared_template_row_id = 'G01:v1'
  AND rule.rule_key = 'g01-external-status';

DO $$
DECLARE
    execution_id uuid;
    updated_count integer;
BEGIN
    SELECT id INTO execution_id
    FROM tide.task_execution_versions
    WHERE shared_template_row_id = 'G01:v1';

    IF execution_id IS NULL THEN
        RETURN;
    END IF;

    UPDATE tide.task_validation_rules
    SET rule_version = '2026-08-11-tesol-only-v1',
        teacher_failure_copy = 'TESOL 真实状态尚未通过。'
    WHERE execution_version_id = execution_id
      AND rule_key = 'g01-external-status'
      AND rule_type = 'G01_EXTERNAL_STATUS'
      AND position = 3
      AND config = '{}'::jsonb
      AND rule_version = '2026-07-22'
      AND teacher_failure_copy =
          'Self-intro 和 TESOL 真实状态尚未全部通过。';

    GET DIAGNOSTICS updated_count = ROW_COUNT;

    IF updated_count = 0 THEN
        IF (
            SELECT count(*)
            FROM tide.task_validation_rules
            WHERE execution_version_id = execution_id
              AND rule_key = 'g01-external-status'
              AND rule_type = 'G01_EXTERNAL_STATUS'
              AND position = 3
              AND config = '{}'::jsonb
              AND rule_version = '2026-08-11-tesol-only-v1'
              AND teacher_failure_copy = 'TESOL 真实状态尚未通过。'
        ) <> 1 THEN
            RAISE EXCEPTION
                'migration 0033 did not update exactly one approved G01 external-status rule';
        END IF;
    ELSIF updated_count <> 1 THEN
        RAISE EXCEPTION
            'migration 0033 updated % G01 external-status rules instead of one',
            updated_count;
    END IF;
END
$$;

DO $$
DECLARE
    execution_id uuid;
BEGIN
    SELECT id INTO execution_id
    FROM tide.task_execution_versions
    WHERE shared_template_row_id = 'G01:v1';

    IF execution_id IS NULL THEN
        RETURN;
    END IF;

    IF EXISTS (
        SELECT id, row_data FROM g01_execution_before
        EXCEPT
        SELECT execution.id, to_jsonb(execution)
        FROM tide.task_execution_versions AS execution
        WHERE execution.shared_template_row_id = 'G01:v1'
    ) OR EXISTS (
        SELECT execution.id, to_jsonb(execution)
        FROM tide.task_execution_versions AS execution
        WHERE execution.shared_template_row_id = 'G01:v1'
        EXCEPT
        SELECT id, row_data FROM g01_execution_before
    ) THEN
        RAISE EXCEPTION 'migration 0033 changed the G01 execution row';
    END IF;

    IF EXISTS (
        SELECT assignment_id, row_data FROM g01_assignment_before
        EXCEPT
        SELECT assignment.assignment_id, to_jsonb(assignment)
        FROM public.task_assignments AS assignment
        WHERE assignment.template_version_id = 'G01:v1'
    ) OR EXISTS (
        SELECT assignment.assignment_id, to_jsonb(assignment)
        FROM public.task_assignments AS assignment
        WHERE assignment.template_version_id = 'G01:v1'
        EXCEPT
        SELECT assignment_id, row_data FROM g01_assignment_before
    ) THEN
        RAISE EXCEPTION 'migration 0033 changed G01 assignments';
    END IF;

    IF EXISTS (
        SELECT id, row_data FROM g01_progress_before
        EXCEPT
        SELECT progress.id, to_jsonb(progress)
        FROM tide.task_step_progress AS progress
        JOIN public.task_assignments AS assignment
          ON assignment.assignment_id = progress.task_assignment_id
        WHERE assignment.template_version_id = 'G01:v1'
    ) OR EXISTS (
        SELECT progress.id, to_jsonb(progress)
        FROM tide.task_step_progress AS progress
        JOIN public.task_assignments AS assignment
          ON assignment.assignment_id = progress.task_assignment_id
        WHERE assignment.template_version_id = 'G01:v1'
        EXCEPT
        SELECT id, row_data FROM g01_progress_before
    ) THEN
        RAISE EXCEPTION 'migration 0033 changed G01 progress';
    END IF;

    IF EXISTS (
        SELECT id, row_data FROM g01_steps_before
        EXCEPT
        SELECT definition.id, to_jsonb(definition)
        FROM tide.task_step_definitions AS definition
        WHERE definition.execution_version_id = execution_id
    ) OR EXISTS (
        SELECT definition.id, to_jsonb(definition)
        FROM tide.task_step_definitions AS definition
        WHERE definition.execution_version_id = execution_id
        EXCEPT
        SELECT id, row_data FROM g01_steps_before
    ) THEN
        RAISE EXCEPTION 'migration 0033 changed G01 step definitions';
    END IF;

    IF EXISTS (
        SELECT id, row_data FROM g01_other_rules_before
        EXCEPT
        SELECT rule.id, to_jsonb(rule)
        FROM tide.task_validation_rules AS rule
        WHERE rule.execution_version_id = execution_id
          AND rule.rule_key <> 'g01-external-status'
    ) OR EXISTS (
        SELECT rule.id, to_jsonb(rule)
        FROM tide.task_validation_rules AS rule
        WHERE rule.execution_version_id = execution_id
          AND rule.rule_key <> 'g01-external-status'
        EXCEPT
        SELECT id, row_data FROM g01_other_rules_before
    ) THEN
        RAISE EXCEPTION 'migration 0033 changed another G01 validation rule';
    END IF;

    IF EXISTS (
        SELECT id, immutable_row FROM g01_external_rule_before
        EXCEPT
        SELECT
            rule.id,
            (to_jsonb(rule) - 'rule_version') - 'teacher_failure_copy'
        FROM tide.task_validation_rules AS rule
        WHERE rule.execution_version_id = execution_id
          AND rule.rule_key = 'g01-external-status'
    ) OR EXISTS (
        SELECT
            rule.id,
            (to_jsonb(rule) - 'rule_version') - 'teacher_failure_copy'
        FROM tide.task_validation_rules AS rule
        WHERE rule.execution_version_id = execution_id
          AND rule.rule_key = 'g01-external-status'
        EXCEPT
        SELECT id, immutable_row FROM g01_external_rule_before
    ) THEN
        RAISE EXCEPTION
            'migration 0033 changed immutable G01 external-status rule fields';
    END IF;

    IF (
        SELECT count(*)
        FROM tide.task_validation_rules
        WHERE execution_version_id = execution_id
          AND (
              rule_key = 'g01-external-status'
              OR rule_type = 'G01_EXTERNAL_STATUS'
          )
    ) <> 1 OR NOT EXISTS (
        SELECT 1
        FROM tide.task_validation_rules
        WHERE execution_version_id = execution_id
          AND rule_key = 'g01-external-status'
          AND rule_type = 'G01_EXTERNAL_STATUS'
          AND rule_version = '2026-08-11-tesol-only-v1'
          AND position = 3
          AND config = '{}'::jsonb
          AND teacher_failure_copy = 'TESOL 真实状态尚未通过。'
    ) THEN
        RAISE EXCEPTION
            'migration 0033 did not leave exactly one TESOL-only G01 rule';
    END IF;
END
$$;

COMMIT;
