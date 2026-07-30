BEGIN;

ALTER TABLE tide.app_events DROP CONSTRAINT IF EXISTS app_events_task_assignment_id_fkey;
ALTER TABLE tide.app_events ALTER COLUMN task_assignment_id TYPE uuid USING NULL;
ALTER TABLE tide.app_events RENAME COLUMN task_assignment_id TO task_instance_id;
ALTER TABLE tide.app_events RENAME TO client_events;
ALTER INDEX tide.app_events_teacher_time_idx RENAME TO client_events_teacher_time_idx;
ALTER TABLE tide.client_events
    ADD CONSTRAINT client_events_task_instance_id_fkey FOREIGN KEY (task_instance_id)
        REFERENCES tide.teacher_tasks(id) ON DELETE SET NULL;

DROP TABLE IF EXISTS tide.system_notifications;

DROP INDEX IF EXISTS tide.file_upload_intents_assignment_step_idx;
DROP INDEX IF EXISTS tide.task_command_receipts_assignment_time_idx;
DROP INDEX IF EXISTS tide.task_completions_assignment_key;
DROP INDEX IF EXISTS tide.task_submissions_assignment_time_idx;
DROP INDEX IF EXISTS tide.video_progress_assignment_step_key;
DROP INDEX IF EXISTS tide.task_step_progress_assignment_step_key;
DROP INDEX IF EXISTS tide.task_attempts_assignment_number_key;

ALTER TABLE tide.file_upload_intents DROP COLUMN IF EXISTS task_assignment_id;
ALTER TABLE tide.task_command_receipts DROP COLUMN IF EXISTS task_assignment_id;
ALTER TABLE tide.task_completions DROP COLUMN IF EXISTS task_assignment_id;
ALTER TABLE tide.task_submissions DROP COLUMN IF EXISTS task_assignment_id;
ALTER TABLE tide.video_progress DROP COLUMN IF EXISTS task_assignment_id;
ALTER TABLE tide.task_step_progress DROP COLUMN IF EXISTS task_assignment_id;
ALTER TABLE tide.task_attempts DROP COLUMN IF EXISTS task_assignment_id;

ALTER TABLE tide.task_template_files DROP COLUMN IF EXISTS execution_version_id;
ALTER TABLE tide.task_validation_rules DROP COLUMN IF EXISTS execution_version_id;
ALTER TABLE tide.task_step_definitions DROP COLUMN IF EXISTS execution_version_id;
DROP TABLE IF EXISTS tide.task_execution_versions;

COMMIT;
