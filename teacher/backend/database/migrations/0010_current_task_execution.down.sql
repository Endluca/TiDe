BEGIN;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM tide.task_execution_versions
        WHERE task_code !~ '^G(0[1-9]|10)$'
    ) THEN
        RAISE EXCEPTION 'personalized execution versions must be removed before rolling back 0010';
    END IF;
END
$$;

ALTER TABLE tide.task_execution_versions
    DROP CONSTRAINT IF EXISTS task_execution_versions_code_check;

ALTER TABLE tide.task_execution_versions
    ADD CONSTRAINT task_execution_versions_code_check
    CHECK (task_code ~ '^G(0[1-9]|10)$');

COMMIT;
