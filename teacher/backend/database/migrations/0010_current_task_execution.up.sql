BEGIN;

ALTER TABLE tide.task_execution_versions
    DROP CONSTRAINT IF EXISTS task_execution_versions_code_check;

ALTER TABLE tide.task_execution_versions
    ADD CONSTRAINT task_execution_versions_code_check
    CHECK (task_code ~ '^[A-Z0-9][A-Z0-9_-]{1,127}$');

COMMIT;
