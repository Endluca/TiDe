BEGIN;

SET LOCAL lock_timeout = '10s';

DO $$
BEGIN
    IF to_regclass('tide.task_step_progress') IS NULL THEN
        RAISE EXCEPTION
            'tide.task_step_progress is required before rolling back migration 0040';
    END IF;
END
$$;

ALTER TABLE tide.task_step_progress
    DROP CONSTRAINT IF EXISTS task_step_progress_g02_read_status_check;

DROP TRIGGER IF EXISTS task_step_progress_g02_assignment_completion_check
    ON tide.task_step_progress;

DROP FUNCTION IF EXISTS tide.enforce_g02_document_assignment_completion();

ALTER TABLE tide.task_step_progress
    DROP COLUMN IF EXISTS reached_end;

COMMIT;
