BEGIN;

SET LOCAL lock_timeout = '10s';

DO $$
BEGIN
    IF to_regclass('public.task_templates') IS NULL
       OR to_regclass('tide.task_execution_versions') IS NULL
       OR to_regclass('tide.task_step_definitions') IS NULL
       OR to_regclass('tide.task_validation_rules') IS NULL THEN
        RAISE EXCEPTION
            'shared task catalog and Tide execution tables are required before migration 0042';
    END IF;
END
$$;

LOCK TABLE public.task_templates IN SHARE MODE;
LOCK TABLE tide.task_execution_versions IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE tide.task_step_definitions IN SHARE MODE;
LOCK TABLE tide.task_validation_rules IN SHARE MODE;

DO $$
DECLARE
    execution_id uuid;
BEGIN
    IF (
        SELECT count(*)
        FROM public.task_templates
        WHERE row_id = 'G10:v1'
          AND template_id = 'G09'
          AND template_version = 1
          AND status = 'PUBLISHED'
          AND execution_owner = 'TEACHER_APP'
          AND payload->>'category' = 'MANDATORY_GROWTH'
          AND payload->>'title' = 'SET Teaching Fundamentals'
          AND payload->>'why_template' =
              'Learn the fundamentals of SET teaching.'
          AND payload->>'how_summary' =
              'Complete the three SET videos and their three paired quizzes in Kuozhi.'
          AND payload->>'completion_standard' =
              'All three required videos and all three paired quizzes reach 100% progress in Kuozhi.'
          AND payload->>'content_status' = 'READY'
          AND (payload->>'score_value')::integer = 5
    ) <> 1 THEN
        RAISE EXCEPTION
            'migration 0042 requires the published G09 course-658 shared template from public revision 65';
    END IF;

    SELECT id INTO execution_id
    FROM tide.task_execution_versions
    WHERE shared_template_row_id = 'G10:v1';

    IF execution_id IS NULL THEN
        IF EXISTS (
            SELECT 1
            FROM tide.task_execution_versions AS execution
            JOIN public.task_templates AS template
              ON template.row_id = execution.shared_template_row_id
            WHERE template.payload->>'category' = 'MANDATORY_GROWTH'
        ) THEN
            RAISE EXCEPTION
                'existing execution catalog is missing the stable G09 execution G10:v1';
        END IF;
        RAISE NOTICE
            'migration 0042 left the empty G09 execution unchanged; run the explicit current catalog seed before rollout';
        RETURN;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM tide.task_execution_versions
        WHERE task_code = 'G09'
          AND shared_template_row_id <> 'G10:v1'
    ) OR EXISTS (
        SELECT 1
        FROM tide.task_execution_versions
        WHERE id = execution_id
          AND (
              task_code <> 'G09'
              OR status <> 'ACTIVE'
              OR execution_contract_version NOT IN ('v1', 'task-contract-v3')
              OR config NOT IN (
                  '{"estimatedMinutes":25,"allowRetry":true,"contentStatus":"PENDING","contentVersion":"2026-08-06","pendingReason":"KUOZHI_G09_COURSE_MAPPING_PENDING"}'::jsonb,
                  '{"estimatedMinutes":25,"allowRetry":true,"contentStatus":"READY","contentVersion":"2026-08-19-set-kuozhi-v1","pendingReason":null}'::jsonb
              )
          )
    ) THEN
        RAISE EXCEPTION
            'G09 execution identity or config is not an approved pre-0042/current shape';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM tide.task_step_definitions
        WHERE execution_version_id = execution_id
    ) OR EXISTS (
        SELECT 1
        FROM tide.task_validation_rules
        WHERE execution_version_id = execution_id
    ) THEN
        RAISE EXCEPTION
            'G09 contains unreviewed local steps or rules; migration 0042 will not replace them';
    END IF;
END
$$;

UPDATE tide.task_execution_versions
SET execution_contract_version = 'task-contract-v3',
    config = '{"estimatedMinutes":25,"allowRetry":true,"contentStatus":"READY","contentVersion":"2026-08-19-set-kuozhi-v1","pendingReason":null}'::jsonb,
    updated_at = now()
WHERE shared_template_row_id = 'G10:v1'
  AND config = '{"estimatedMinutes":25,"allowRetry":true,"contentStatus":"PENDING","contentVersion":"2026-08-06","pendingReason":"KUOZHI_G09_COURSE_MAPPING_PENDING"}'::jsonb;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM tide.task_execution_versions
        WHERE shared_template_row_id = 'G10:v1'
          AND (
              task_code <> 'G09'
              OR status <> 'ACTIVE'
              OR execution_contract_version <> 'task-contract-v3'
              OR config <> '{"estimatedMinutes":25,"allowRetry":true,"contentStatus":"READY","contentVersion":"2026-08-19-set-kuozhi-v1","pendingReason":null}'::jsonb
          )
    ) THEN
        RAISE EXCEPTION 'G09 SET course execution publication verification failed';
    END IF;
END
$$;

COMMIT;
