BEGIN;

SET LOCAL lock_timeout = '10s';

DO $$
BEGIN
    IF to_regclass('public.task_templates') IS NULL
       OR to_regclass('public.task_assignments') IS NULL
       OR to_regclass('tide.task_execution_versions') IS NULL
       OR to_regclass('tide.task_step_definitions') IS NULL
       OR to_regclass('tide.task_validation_rules') IS NULL
       OR to_regclass('tide.task_attempts') IS NULL
       OR to_regclass('tide.task_step_progress') IS NULL
       OR to_regclass('tide.task_submissions') IS NULL
       OR to_regclass('tide.task_completions') IS NULL
       OR to_regclass('tide.task_command_receipts') IS NULL
       OR to_regclass('tide.file_upload_intents') IS NULL THEN
        RAISE EXCEPTION
            'shared task tables and Tide execution/evidence tables are required before migration 0038 down';
    END IF;
END
$$;

LOCK TABLE public.task_templates IN SHARE MODE;
LOCK TABLE public.task_assignments IN SHARE MODE;
LOCK TABLE tide.task_execution_versions IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE tide.task_step_definitions IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE tide.task_validation_rules IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE tide.task_attempts IN SHARE MODE;
LOCK TABLE tide.task_step_progress IN SHARE MODE;
LOCK TABLE tide.task_submissions IN SHARE MODE;
LOCK TABLE tide.task_completions IN SHARE MODE;
LOCK TABLE tide.task_command_receipts IN SHARE MODE;
LOCK TABLE tide.file_upload_intents IN SHARE MODE;

DO $$
DECLARE
    execution_id uuid;
    execution_config jsonb;
    step_count integer;
    rule_count integer;
BEGIN
    SELECT id, config INTO execution_id, execution_config
    FROM tide.task_execution_versions
    WHERE shared_template_row_id = 'P-FB-NEGATIVE:v1';

    IF execution_id IS NULL THEN
        RAISE NOTICE 'migration 0038 down found no P-FB-NEGATIVE execution';
        RETURN;
    END IF;

    IF (
        SELECT count(*)
        FROM public.task_templates
        WHERE row_id = 'P-FB-NEGATIVE:v1'
          AND template_id = 'P-FB-NEGATIVE'
          AND template_version = 1
          AND status = 'PUBLISHED'
          AND output_type = 'TEACHER_TASK'
          AND execution_owner = 'TEACHER_APP'
          AND integration_mode = 'OUTBOUND_MANAGED'
          AND source_mode = 'REAL'
          AND jsonb_typeof(payload) = 'object'
          AND payload->>'template_id' = 'P-FB-NEGATIVE'
          AND payload->>'category' = 'PERSONALIZED_IMPROVEMENT'
          AND payload->>'title' = 'Feedback Improvement'
          AND payload->>'score_type' = 'ZERO'
          AND payload->'score_value' = '0'::jsonb
          AND payload->>'how_summary' =
              'Complete the configured improvement activity for the feedback issue shown in the task reason. Depending on the assigned activity, you may need to submit a teaching-environment photo for review or complete another guided action.'
          AND payload->>'completion_standard' =
              'The teacher app marks the task as completed after every requirement for the assigned improvement activity, including any required photo review, is satisfied.'
    ) <> 1 THEN
        RAISE EXCEPTION
            'migration 0038 down refused an unapproved P-FB-NEGATIVE shared identity';
    END IF;

    SELECT count(*) INTO step_count
    FROM tide.task_step_definitions
    WHERE execution_version_id = execution_id;

    SELECT count(*) INTO rule_count
    FROM tide.task_validation_rules
    WHERE execution_version_id = execution_id;

    IF execution_config = '{"estimatedMinutes":8,"allowRetry":true,"contentStatus":"PENDING","contentVersion":"2026-08-06","pendingReason":"JIAHE_PERSONALIZED_CONTENT_PENDING"}'::jsonb
       AND step_count = 0
       AND rule_count = 0 THEN
        RAISE NOTICE 'migration 0038 down is already applied';
        RETURN;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM tide.task_execution_versions
        WHERE id = execution_id
          AND task_code = 'P-FB-NEGATIVE'
          AND status = 'ACTIVE'
          AND execution_contract_version = 'task-contract-v3'
          AND config = '{"estimatedMinutes":8,"allowRetry":true,"contentStatus":"PENDING","contentVersion":"2026-08-11-personalized-environment-photo-v1","pendingReason":"JIAHE_PERSONALIZED_CONTENT_PENDING","independentModules":{"stepKeys":["p-fb-negative-environment-photo"],"allowOutOfOrderProgress":true,"keepAssignmentInProgressUntilPassed":true}}'::jsonb
    ) OR step_count <> 1 OR rule_count <> 2 OR NOT EXISTS (
        SELECT 1
        FROM tide.task_step_definitions
        WHERE execution_version_id = execution_id
          AND step_key = 'p-fb-negative-environment-photo'
          AND position = 1
          AND step_type = 'UPLOAD'
          AND title = 'Take a teaching-environment photo'
          AND config = '{"version":"2026-08-11-personalized-environment-photo-v1","role":"ENVIRONMENT_PHOTO","reviewProfile":"TEACHING_ENVIRONMENT_V1","accept":["image/jpeg"],"captureOnly":true,"maxFiles":1}'::jsonb
    ) OR NOT EXISTS (
        SELECT 1
        FROM tide.task_validation_rules
        WHERE execution_version_id = execution_id
          AND rule_key = 'all-steps-complete'
          AND rule_type = 'ALL_STEPS_COMPLETE'
          AND rule_version = '2026-08-11-personalized-environment-photo-v1'
          AND position = 1
          AND config = '{"requiredStepKeys":["p-fb-negative-environment-photo"]}'::jsonb
          AND teacher_failure_copy = '请拍摄并提交一张当前授课环境照片。'
    ) OR NOT EXISTS (
        SELECT 1
        FROM tide.task_validation_rules
        WHERE execution_version_id = execution_id
          AND rule_key = 'p-fb-negative-environment-ai-review'
          AND rule_type = 'AI_IMAGE_REVIEW'
          AND rule_version = '2026-07-27-strict'
          AND position = 2
          AND config = '{"stepKey":"p-fb-negative-environment-photo","criteriaVersion":"personalized-teaching-environment-2026-08-v1","criteriaKeys":["camera_angle","lighting","background","dressing"],"allowedMimeTypes":["image/jpeg","image/png","image/webp"],"reviewProfile":"TEACHING_ENVIRONMENT_V1","systemPrompt":"You strictly review teacher-submitted evidence. Return JSON only with this exact shape: {\"decision\":\"PASS|RETRY|ERROR\",\"teacherReason\":\"teacher-safe concise message\",\"confidenceSummary\":{},\"criteria\":[{\"criterionKey\":\"one configured key\",\"result\":\"PASS|FAIL|UNKNOWN\",\"teacherMessage\":\"teacher-safe message or null\"}]}. Include every configured criterion exactly once. Never infer a pass from the mere presence of a person or object. Use UNKNOWN whenever the visual evidence is unclear. PASS only when every configured criterion is visibly and unambiguously PASS; any FAIL or UNKNOWN requires RETRY. Use ERROR only when the file cannot be assessed. Do not expose internal risk labels or private model reasoning.","userText":"Review this current teaching-environment photo strictly against camera angle, lighting, background and dressing only."}'::jsonb
          AND teacher_failure_copy = '已保留你完成的内容，请根据提示更新这份材料。'
    ) THEN
        RAISE EXCEPTION
            'migration 0038 down refused an unknown P-FB-NEGATIVE execution shape';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM public.task_assignments AS assignment
        WHERE assignment.template_version_id = 'P-FB-NEGATIVE:v1'
          AND (
              assignment.status <> 'ASSIGNED'
              OR EXISTS (
                  SELECT 1 FROM tide.task_attempts AS attempt
                  WHERE attempt.task_assignment_id = assignment.assignment_id
              )
              OR EXISTS (
                  SELECT 1 FROM tide.task_step_progress AS progress
                  WHERE progress.task_assignment_id = assignment.assignment_id
                    AND progress.step_key = 'p-fb-negative-environment-photo'
              )
              OR EXISTS (
                  SELECT 1 FROM tide.file_upload_intents AS upload_intent
                  WHERE upload_intent.task_assignment_id = assignment.assignment_id
                    AND upload_intent.step_key = 'p-fb-negative-environment-photo'
              )
              OR EXISTS (
                  SELECT 1 FROM tide.task_submissions AS submission
                  WHERE submission.task_assignment_id = assignment.assignment_id
              )
              OR EXISTS (
                  SELECT 1 FROM tide.task_completions AS completion
                  WHERE completion.task_assignment_id = assignment.assignment_id
              )
              OR EXISTS (
                  SELECT 1 FROM tide.task_command_receipts AS receipt
                  WHERE receipt.task_assignment_id = assignment.assignment_id
              )
          )
    ) THEN
        RAISE EXCEPTION
            'migration 0038 down refused to remove personalized photo definitions with recorded execution evidence';
    END IF;
END
$$;

DELETE FROM tide.task_validation_rules AS rule
USING tide.task_execution_versions AS execution
WHERE execution.id = rule.execution_version_id
  AND execution.shared_template_row_id = 'P-FB-NEGATIVE:v1'
  AND rule.rule_key IN (
      'all-steps-complete',
      'p-fb-negative-environment-ai-review'
  );

DELETE FROM tide.task_step_definitions AS definition
USING tide.task_execution_versions AS execution
WHERE execution.id = definition.execution_version_id
  AND execution.shared_template_row_id = 'P-FB-NEGATIVE:v1'
  AND definition.step_key = 'p-fb-negative-environment-photo';

UPDATE tide.task_execution_versions
SET config = '{"estimatedMinutes":8,"allowRetry":true,"contentStatus":"PENDING","contentVersion":"2026-08-06","pendingReason":"JIAHE_PERSONALIZED_CONTENT_PENDING"}'::jsonb,
    updated_at = now()
WHERE shared_template_row_id = 'P-FB-NEGATIVE:v1'
  AND config = '{"estimatedMinutes":8,"allowRetry":true,"contentStatus":"PENDING","contentVersion":"2026-08-11-personalized-environment-photo-v1","pendingReason":"JIAHE_PERSONALIZED_CONTENT_PENDING","independentModules":{"stepKeys":["p-fb-negative-environment-photo"],"allowOutOfOrderProgress":true,"keepAssignmentInProgressUntilPassed":true}}'::jsonb;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM tide.task_execution_versions AS execution
        WHERE execution.shared_template_row_id = 'P-FB-NEGATIVE:v1'
          AND (
              execution.task_code <> 'P-FB-NEGATIVE'
              OR execution.status <> 'ACTIVE'
              OR execution.execution_contract_version <> 'task-contract-v3'
              OR execution.config <> '{"estimatedMinutes":8,"allowRetry":true,"contentStatus":"PENDING","contentVersion":"2026-08-06","pendingReason":"JIAHE_PERSONALIZED_CONTENT_PENDING"}'::jsonb
              OR EXISTS (
                  SELECT 1 FROM tide.task_step_definitions AS definition
                  WHERE definition.execution_version_id = execution.id
              )
              OR EXISTS (
                  SELECT 1 FROM tide.task_validation_rules AS rule
                  WHERE rule.execution_version_id = execution.id
              )
          )
    ) THEN
        RAISE EXCEPTION 'migration 0038 down did not restore the exact pending execution shape';
    END IF;
END
$$;

COMMIT;
