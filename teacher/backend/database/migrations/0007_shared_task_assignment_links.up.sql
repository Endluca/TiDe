BEGIN;

DO $$
BEGIN
    IF to_regclass('public.task_templates') IS NULL
       OR to_regclass('public.task_assignments') IS NULL THEN
        RAISE EXCEPTION 'shared task_templates/task_assignments must exist before migration 0007';
    END IF;
END
$$;

CREATE TABLE tide.task_execution_versions (
    id uuid PRIMARY KEY,
    shared_template_row_id varchar NOT NULL REFERENCES public.task_templates(row_id) ON DELETE RESTRICT,
    task_code varchar NOT NULL,
    execution_contract_version text NOT NULL,
    config jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL DEFAULT 'ACTIVE',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT task_execution_versions_template_key UNIQUE (shared_template_row_id),
    CONSTRAINT task_execution_versions_task_key UNIQUE (task_code),
    CONSTRAINT task_execution_versions_status_check CHECK (status IN ('ACTIVE', 'RETIRED')),
    CONSTRAINT task_execution_versions_config_check CHECK (jsonb_typeof(config) = 'object'),
    CONSTRAINT task_execution_versions_code_check CHECK (task_code ~ '^G(0[1-9]|10)$')
);

INSERT INTO tide.task_execution_versions (
    id, shared_template_row_id, task_code, execution_contract_version, config
)
SELECT
    gen_random_uuid(),
    shared.row_id,
    shared.template_id,
    legacy.execution_contract_version,
    legacy.content_config
FROM public.task_templates shared
JOIN tide.task_templates legacy_template
  ON legacy_template.task_code = shared.template_id
JOIN LATERAL (
    SELECT version.execution_contract_version, version.content_config
    FROM tide.task_template_versions version
    WHERE version.template_id = legacy_template.id
      AND version.status = 'PUBLISHED'
    ORDER BY version.version DESC
    LIMIT 1
) legacy ON true
WHERE shared.status = 'PUBLISHED'
  AND shared.template_id ~ '^G(0[1-9]|10)$'
ON CONFLICT (shared_template_row_id) DO NOTHING;

ALTER TABLE tide.task_step_definitions
    ADD COLUMN execution_version_id uuid REFERENCES tide.task_execution_versions(id) ON DELETE CASCADE;
ALTER TABLE tide.task_validation_rules
    ADD COLUMN execution_version_id uuid REFERENCES tide.task_execution_versions(id) ON DELETE CASCADE;
ALTER TABLE tide.task_template_files
    ADD COLUMN execution_version_id uuid REFERENCES tide.task_execution_versions(id) ON DELETE CASCADE;

UPDATE tide.task_step_definitions definition
SET execution_version_id = execution.id
FROM tide.task_template_versions version
JOIN tide.task_templates template ON template.id = version.template_id
JOIN tide.task_execution_versions execution ON execution.task_code = template.task_code
WHERE definition.template_version_id = version.id;

UPDATE tide.task_validation_rules rule
SET execution_version_id = execution.id
FROM tide.task_template_versions version
JOIN tide.task_templates template ON template.id = version.template_id
JOIN tide.task_execution_versions execution ON execution.task_code = template.task_code
WHERE rule.template_version_id = version.id;

UPDATE tide.task_template_files file_link
SET execution_version_id = execution.id
FROM tide.task_template_versions version
JOIN tide.task_templates template ON template.id = version.template_id
JOIN tide.task_execution_versions execution ON execution.task_code = template.task_code
WHERE file_link.template_version_id = version.id;

ALTER TABLE tide.task_attempts ADD COLUMN task_assignment_id varchar REFERENCES public.task_assignments(assignment_id) ON DELETE RESTRICT;
ALTER TABLE tide.task_step_progress ADD COLUMN task_assignment_id varchar REFERENCES public.task_assignments(assignment_id) ON DELETE CASCADE;
ALTER TABLE tide.video_progress ADD COLUMN task_assignment_id varchar REFERENCES public.task_assignments(assignment_id) ON DELETE CASCADE;
ALTER TABLE tide.task_submissions ADD COLUMN task_assignment_id varchar REFERENCES public.task_assignments(assignment_id) ON DELETE RESTRICT;
ALTER TABLE tide.task_completions ADD COLUMN task_assignment_id varchar REFERENCES public.task_assignments(assignment_id) ON DELETE RESTRICT;
ALTER TABLE tide.task_command_receipts ADD COLUMN task_assignment_id varchar REFERENCES public.task_assignments(assignment_id) ON DELETE CASCADE;
ALTER TABLE tide.file_upload_intents ADD COLUMN task_assignment_id varchar REFERENCES public.task_assignments(assignment_id) ON DELETE CASCADE;

CREATE TEMP TABLE legacy_fixed_assignment_map ON COMMIT DROP AS
SELECT
    legacy.id AS teacher_task_id,
    assignment.assignment_id
FROM tide.teacher_tasks legacy
JOIN tide.teacher_bindings binding ON binding.id = legacy.teacher_binding_id
JOIN public.task_assignments assignment
  ON assignment.teacher_id = binding.teacher_id
 AND assignment.task_code = legacy.task_code
WHERE legacy.source_type = 'FIXED'
  AND legacy.task_code ~ '^G(0[1-9]|10)$';

DO $$
DECLARE
    unmapped_count integer;
BEGIN
    SELECT count(*) INTO unmapped_count
    FROM tide.teacher_tasks task
    WHERE task.source_type = 'FIXED'
      AND task.task_code ~ '^G(0[1-9]|10)$'
      AND NOT EXISTS (
          SELECT 1 FROM legacy_fixed_assignment_map mapping
          WHERE mapping.teacher_task_id = task.id
      );
    IF unmapped_count > 0 THEN
        RAISE EXCEPTION '% legacy fixed tasks cannot be mapped to shared assignments', unmapped_count;
    END IF;

    SELECT count(*) INTO unmapped_count
    FROM tide.teacher_tasks task
    WHERE task.source_type = 'PERSONALIZED'
      AND task.data_origin <> 'MOCK';
    IF unmapped_count > 0 THEN
        RAISE EXCEPTION '% non-Mock personalized tasks require manual migration', unmapped_count;
    END IF;
END
$$;

UPDATE tide.task_attempts local
SET task_assignment_id = mapping.assignment_id
FROM legacy_fixed_assignment_map mapping
WHERE local.teacher_task_id = mapping.teacher_task_id;

UPDATE tide.task_step_progress local
SET task_assignment_id = mapping.assignment_id
FROM legacy_fixed_assignment_map mapping
WHERE local.teacher_task_id = mapping.teacher_task_id;

UPDATE tide.video_progress local
SET task_assignment_id = mapping.assignment_id
FROM legacy_fixed_assignment_map mapping
WHERE local.teacher_task_id = mapping.teacher_task_id;

UPDATE tide.task_submissions local
SET task_assignment_id = mapping.assignment_id
FROM legacy_fixed_assignment_map mapping
WHERE local.teacher_task_id = mapping.teacher_task_id;

UPDATE tide.task_completions local
SET task_assignment_id = mapping.assignment_id
FROM legacy_fixed_assignment_map mapping
WHERE local.teacher_task_id = mapping.teacher_task_id;

UPDATE tide.task_command_receipts local
SET task_assignment_id = mapping.assignment_id
FROM legacy_fixed_assignment_map mapping
WHERE local.teacher_task_id = mapping.teacher_task_id;

UPDATE tide.file_upload_intents local
SET task_assignment_id = mapping.assignment_id
FROM legacy_fixed_assignment_map mapping
WHERE local.teacher_task_id = mapping.teacher_task_id;

CREATE UNIQUE INDEX task_attempts_assignment_number_key
    ON tide.task_attempts (task_assignment_id, attempt_no)
    WHERE task_assignment_id IS NOT NULL;
CREATE UNIQUE INDEX task_step_progress_assignment_step_key
    ON tide.task_step_progress (task_assignment_id, step_key)
    WHERE task_assignment_id IS NOT NULL;
CREATE UNIQUE INDEX video_progress_assignment_step_key
    ON tide.video_progress (task_assignment_id, step_key)
    WHERE task_assignment_id IS NOT NULL;
CREATE INDEX task_submissions_assignment_time_idx
    ON tide.task_submissions (task_assignment_id, submitted_at DESC)
    WHERE task_assignment_id IS NOT NULL;
CREATE UNIQUE INDEX task_completions_assignment_key
    ON tide.task_completions (task_assignment_id)
    WHERE task_assignment_id IS NOT NULL;
CREATE INDEX task_command_receipts_assignment_time_idx
    ON tide.task_command_receipts (task_assignment_id, created_at DESC)
    WHERE task_assignment_id IS NOT NULL;
CREATE INDEX file_upload_intents_assignment_step_idx
    ON tide.file_upload_intents (task_assignment_id, step_key, created_at DESC)
    WHERE task_assignment_id IS NOT NULL;

CREATE TABLE tide.system_notifications (
    system_notification_id uuid PRIMARY KEY,
    teacher_id varchar NOT NULL REFERENCES public.teachers(teacher_id) ON DELETE RESTRICT,
    type_code varchar NOT NULL,
    title varchar NOT NULL,
    body text NOT NULL,
    action_type varchar,
    action_target text,
    read_at timestamptz,
    clicked_at timestamptz,
    expires_at timestamptz,
    cancelled_at timestamptz,
    dedupe_key varchar NOT NULL UNIQUE,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT system_notifications_action_check CHECK ((action_type IS NULL) = (action_target IS NULL)),
    CONSTRAINT system_notifications_payload_check CHECK (jsonb_typeof(payload) = 'object')
);

CREATE INDEX system_notifications_teacher_time_idx
    ON tide.system_notifications (teacher_id, created_at DESC);
CREATE INDEX system_notifications_teacher_read_idx
    ON tide.system_notifications (teacher_id, read_at);

ALTER TABLE tide.client_events RENAME TO app_events;
ALTER INDEX tide.client_events_teacher_time_idx RENAME TO app_events_teacher_time_idx;
ALTER TABLE tide.app_events RENAME COLUMN task_instance_id TO task_assignment_id;
ALTER TABLE tide.app_events DROP CONSTRAINT client_events_event_key;
ALTER TABLE tide.app_events DROP CONSTRAINT client_events_name_check;
ALTER TABLE tide.app_events DROP CONSTRAINT client_events_properties_check;
ALTER TABLE tide.app_events DROP CONSTRAINT client_events_task_instance_id_fkey;
ALTER TABLE tide.app_events ALTER COLUMN task_assignment_id TYPE varchar USING NULL;
ALTER TABLE tide.app_events
    ADD CONSTRAINT app_events_event_key UNIQUE (teacher_binding_id, event_id),
    ADD CONSTRAINT app_events_name_check CHECK (char_length(event_name) BETWEEN 1 AND 128),
    ADD CONSTRAINT app_events_properties_check CHECK (jsonb_typeof(properties) = 'object'),
    ADD CONSTRAINT app_events_task_assignment_id_fkey FOREIGN KEY (task_assignment_id)
        REFERENCES public.task_assignments(assignment_id) ON DELETE SET NULL;

COMMIT;
