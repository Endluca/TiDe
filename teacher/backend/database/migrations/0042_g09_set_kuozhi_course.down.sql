BEGIN;

SET LOCAL lock_timeout = '10s';

DO $$
DECLARE
    execution_id uuid;
BEGIN
    IF to_regclass('tide.task_execution_versions') IS NULL
       OR to_regclass('tide.task_step_definitions') IS NULL
       OR to_regclass('tide.task_validation_rules') IS NULL THEN
        RAISE EXCEPTION
            'Tide execution tables are required before migration 0042 down';
    END IF;

    SELECT id INTO execution_id
    FROM tide.task_execution_versions
    WHERE shared_template_row_id = 'G10:v1';

    IF execution_id IS NULL THEN
        RAISE NOTICE 'migration 0042 down left the empty G09 execution unchanged';
        RETURN;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM tide.task_execution_versions
        WHERE id = execution_id
          AND (
              task_code <> 'G09'
              OR status <> 'ACTIVE'
              OR execution_contract_version <> 'task-contract-v3'
              OR config NOT IN (
                  '{"estimatedMinutes":25,"allowRetry":true,"contentStatus":"READY","contentVersion":"2026-08-19-set-kuozhi-v1","pendingReason":null}'::jsonb,
                  '{"estimatedMinutes":25,"allowRetry":true,"contentStatus":"PENDING","contentVersion":"2026-08-06","pendingReason":"KUOZHI_G09_COURSE_MAPPING_PENDING"}'::jsonb
              )
          )
    ) OR EXISTS (
        SELECT 1
        FROM tide.task_step_definitions
        WHERE execution_version_id = execution_id
    ) OR EXISTS (
        SELECT 1
        FROM tide.task_validation_rules
        WHERE execution_version_id = execution_id
    ) THEN
        RAISE EXCEPTION
            'migration 0042 down found unreviewed G09 execution drift';
    END IF;
END
$$;

UPDATE tide.task_execution_versions
SET config = '{"estimatedMinutes":25,"allowRetry":true,"contentStatus":"PENDING","contentVersion":"2026-08-06","pendingReason":"KUOZHI_G09_COURSE_MAPPING_PENDING"}'::jsonb,
    updated_at = now()
WHERE shared_template_row_id = 'G10:v1'
  AND config = '{"estimatedMinutes":25,"allowRetry":true,"contentStatus":"READY","contentVersion":"2026-08-19-set-kuozhi-v1","pendingReason":null}'::jsonb;

COMMIT;
