BEGIN;

DO $$
DECLARE
    unmapped_count integer;
    unsafe_definition_count integer;
BEGIN
    SELECT
        (SELECT count(*) FROM tide.task_attempts WHERE task_assignment_id IS NULL) +
        (SELECT count(*) FROM tide.task_step_progress WHERE task_assignment_id IS NULL) +
        (SELECT count(*) FROM tide.video_progress WHERE task_assignment_id IS NULL) +
        (SELECT count(*) FROM tide.task_submissions WHERE task_assignment_id IS NULL) +
        (SELECT count(*) FROM tide.task_completions WHERE task_assignment_id IS NULL) +
        (SELECT count(*) FROM tide.task_command_receipts WHERE task_assignment_id IS NULL) +
        (SELECT count(*) FROM tide.file_upload_intents WHERE task_assignment_id IS NULL)
    INTO unmapped_count;
    IF unmapped_count > 0 THEN
        RAISE EXCEPTION '% local process rows are not mapped to shared assignments', unmapped_count;
    END IF;

    SELECT count(*) INTO unsafe_definition_count
    FROM tide.task_step_definitions definition
    JOIN tide.task_template_versions version ON version.id = definition.template_version_id
    JOIN tide.task_templates template ON template.id = version.template_id
    WHERE definition.execution_version_id IS NULL
      AND (template.data_origin <> 'MOCK' OR template.task_kind <> 'PERSONALIZED');
    IF unsafe_definition_count > 0 THEN
        RAISE EXCEPTION '% non-Mock execution definitions require manual migration', unsafe_definition_count;
    END IF;

    SELECT count(*) INTO unsafe_definition_count
    FROM tide.task_validation_rules rule
    JOIN tide.task_template_versions version ON version.id = rule.template_version_id
    JOIN tide.task_templates template ON template.id = version.template_id
    WHERE rule.execution_version_id IS NULL
      AND (template.data_origin <> 'MOCK' OR template.task_kind <> 'PERSONALIZED');
    IF unsafe_definition_count > 0 THEN
        RAISE EXCEPTION '% non-Mock validation rules require manual migration', unsafe_definition_count;
    END IF;
END
$$;

DELETE FROM tide.task_validation_rules WHERE execution_version_id IS NULL;
DELETE FROM tide.task_step_definitions WHERE execution_version_id IS NULL;
DELETE FROM tide.task_template_files WHERE execution_version_id IS NULL;

ALTER TABLE tide.task_step_definitions
    DROP CONSTRAINT task_step_definitions_key,
    DROP CONSTRAINT task_step_definitions_position_key,
    DROP CONSTRAINT task_step_definitions_template_version_id_fkey,
    DROP COLUMN template_version_id,
    ALTER COLUMN execution_version_id SET NOT NULL,
    ADD CONSTRAINT task_step_definitions_execution_key UNIQUE (execution_version_id, step_key),
    ADD CONSTRAINT task_step_definitions_execution_position_key UNIQUE (execution_version_id, position);

ALTER TABLE tide.task_validation_rules
    DROP CONSTRAINT task_validation_rules_key,
    DROP CONSTRAINT task_validation_rules_position_key,
    DROP CONSTRAINT task_validation_rules_template_version_id_fkey,
    DROP COLUMN template_version_id,
    ALTER COLUMN execution_version_id SET NOT NULL,
    ADD CONSTRAINT task_validation_rules_execution_key UNIQUE (execution_version_id, rule_key),
    ADD CONSTRAINT task_validation_rules_execution_position_key UNIQUE (execution_version_id, position);

ALTER TABLE tide.task_template_files
    DROP CONSTRAINT task_template_files_pkey,
    DROP CONSTRAINT task_template_files_template_version_id_fkey,
    DROP COLUMN template_version_id,
    ALTER COLUMN execution_version_id SET NOT NULL,
    ADD CONSTRAINT task_template_files_pkey PRIMARY KEY (execution_version_id, file_id, purpose);

ALTER TABLE tide.task_attempts
    DROP CONSTRAINT task_attempts_number_key,
    DROP CONSTRAINT task_attempts_teacher_task_id_fkey,
    DROP COLUMN teacher_task_id,
    ALTER COLUMN task_assignment_id SET NOT NULL;

ALTER TABLE tide.task_step_progress
    DROP CONSTRAINT task_step_progress_key,
    DROP CONSTRAINT task_step_progress_teacher_task_id_fkey,
    DROP COLUMN teacher_task_id,
    ALTER COLUMN task_assignment_id SET NOT NULL;

ALTER TABLE tide.video_progress
    DROP CONSTRAINT video_progress_key,
    DROP CONSTRAINT video_progress_teacher_task_id_fkey,
    DROP COLUMN teacher_task_id,
    ALTER COLUMN task_assignment_id SET NOT NULL;

DROP INDEX IF EXISTS tide.task_submissions_task_time_idx;
ALTER TABLE tide.task_submissions
    DROP CONSTRAINT task_submissions_teacher_task_id_fkey,
    DROP COLUMN teacher_task_id,
    ALTER COLUMN task_assignment_id SET NOT NULL;

ALTER TABLE tide.task_completions
    DROP CONSTRAINT task_completions_task_key,
    DROP CONSTRAINT task_completions_teacher_task_id_fkey,
    DROP COLUMN teacher_task_id,
    ALTER COLUMN task_assignment_id SET NOT NULL;

DROP INDEX IF EXISTS tide.task_command_receipts_task_time_idx;
ALTER TABLE tide.task_command_receipts
    DROP CONSTRAINT task_command_receipts_teacher_task_id_fkey,
    DROP COLUMN teacher_task_id,
    ALTER COLUMN task_assignment_id SET NOT NULL;

DROP INDEX IF EXISTS tide.file_upload_intents_task_idx;
ALTER TABLE tide.file_upload_intents
    DROP CONSTRAINT file_upload_intents_teacher_task_id_fkey,
    DROP COLUMN teacher_task_id,
    ALTER COLUMN task_assignment_id SET NOT NULL;

DROP VIEW IF EXISTS tide.shiwen_fixed_task_completion_events_v1;
DROP VIEW IF EXISTS tide.shiwen_personalized_status_events_v1;

DROP TABLE IF EXISTS tide.dead_letters CASCADE;
DROP TABLE IF EXISTS tide.external_receipts CASCADE;
DROP TABLE IF EXISTS tide.outbox_deliveries CASCADE;
DROP TABLE IF EXISTS tide.integration_events CASCADE;
DROP TABLE IF EXISTS tide.integration_inbox_receipts CASCADE;
DROP TABLE IF EXISTS tide.task_status_events CASCADE;
DROP TABLE IF EXISTS tide.external_assignments CASCADE;
DROP TABLE IF EXISTS tide.support_requests CASCADE;

DROP TABLE IF EXISTS tide.message_reads CASCADE;
DROP TABLE IF EXISTS tide.message_projections CASCADE;
DROP TABLE IF EXISTS tide.g01_review_projections CASCADE;
DROP TABLE IF EXISTS tide.course_attribution_projections CASCADE;
DROP TABLE IF EXISTS tide.metric_projections CASCADE;
DROP TABLE IF EXISTS tide.teacher_identity_projections CASCADE;

DROP TABLE tide.teacher_tasks CASCADE;
DROP TABLE tide.task_template_versions CASCADE;
DROP TABLE tide.task_templates CASCADE;

DROP FUNCTION IF EXISTS tide.enforce_integration_event_origin();
DROP FUNCTION IF EXISTS tide.reject_integration_event_mutation();
DROP FUNCTION IF EXISTS tide.validate_integration_event_task_reference();

COMMIT;
