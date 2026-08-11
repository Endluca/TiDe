BEGIN;

SET LOCAL lock_timeout = '10s';

-- Keep the stable shared P-FB-NEGATIVE identity and publish one reviewed photo
-- execution. When the teacher execution is absent, use the same deterministic
-- UUID as the explicit current-catalog seed. The catalog stays PENDING by
-- default; the backend exposes READY only for the approved evidence variant.
DO $$
BEGIN
    IF to_regclass('public.task_templates') IS NULL
       OR to_regclass('public.task_assignments') IS NULL
       OR to_regclass('tide.task_execution_versions') IS NULL
       OR to_regclass('tide.task_step_definitions') IS NULL
       OR to_regclass('tide.task_validation_rules') IS NULL
       OR to_regclass('tide.task_step_progress') IS NULL THEN
        RAISE EXCEPTION
            'shared task tables and Tide execution tables are required before migration 0038';
    END IF;
END
$$;

LOCK TABLE public.task_templates IN SHARE MODE;
LOCK TABLE public.task_assignments IN SHARE MODE;
LOCK TABLE tide.task_execution_versions IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE tide.task_step_definitions IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE tide.task_validation_rules IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE tide.task_step_progress IN SHARE MODE;

DO $$
DECLARE
    execution_id uuid;
    step_count integer;
    rule_count integer;
BEGIN
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
            'migration 0038 requires exactly one approved published REAL/OUTBOUND_MANAGED zero-point P-FB-NEGATIVE:v1 shared template';
    END IF;

    SELECT id INTO execution_id
    FROM tide.task_execution_versions
    WHERE shared_template_row_id = 'P-FB-NEGATIVE:v1';

    IF execution_id IS NULL THEN
        IF EXISTS (
            SELECT 1
            FROM tide.task_execution_versions
            WHERE task_code = 'P-FB-NEGATIVE'
               OR id = 'a89b9f31-2a71-43da-846e-60c51e14f162'::uuid
        ) THEN
            RAISE EXCEPTION
                'migration 0038 cannot create the deterministic P-FB-NEGATIVE execution because its task code or ID is already occupied';
        END IF;

        INSERT INTO tide.task_execution_versions (
            id,
            shared_template_row_id,
            task_code,
            execution_contract_version,
            config,
            status
        ) VALUES (
            'a89b9f31-2a71-43da-846e-60c51e14f162'::uuid,
            'P-FB-NEGATIVE:v1',
            'P-FB-NEGATIVE',
            'task-contract-v3',
            '{"estimatedMinutes":8,"allowRetry":true,"contentStatus":"PENDING","contentVersion":"2026-08-06","pendingReason":"JIAHE_PERSONALIZED_CONTENT_PENDING"}'::jsonb,
            'ACTIVE'
        );

        execution_id := 'a89b9f31-2a71-43da-846e-60c51e14f162'::uuid;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM tide.task_execution_versions
        WHERE task_code = 'P-FB-NEGATIVE'
          AND shared_template_row_id <> 'P-FB-NEGATIVE:v1'
    ) OR EXISTS (
        SELECT 1
        FROM tide.task_execution_versions
        WHERE id = execution_id
          AND (
              task_code <> 'P-FB-NEGATIVE'
              OR status <> 'ACTIVE'
              OR execution_contract_version NOT IN ('v1', 'task-contract-v3')
              OR config NOT IN (
                  '{"estimatedMinutes":8,"allowRetry":true,"contentStatus":"PENDING","contentVersion":"2026-07-30","pendingReason":"JIAHE_PERSONALIZED_CONTENT_PENDING"}'::jsonb,
                  '{"estimatedMinutes":8,"allowRetry":true,"contentStatus":"PENDING","contentVersion":"2026-08-06","pendingReason":"JIAHE_PERSONALIZED_CONTENT_PENDING"}'::jsonb,
                  '{"estimatedMinutes":8,"allowRetry":true,"contentStatus":"PENDING","contentVersion":"2026-08-11-personalized-environment-photo-v1","pendingReason":"JIAHE_PERSONALIZED_CONTENT_PENDING","independentModules":{"stepKeys":["p-fb-negative-environment-photo"],"allowOutOfOrderProgress":true,"keepAssignmentInProgressUntilPassed":true}}'::jsonb
              )
          )
    ) THEN
        RAISE EXCEPTION
            'P-FB-NEGATIVE execution identity or config is not an approved pre-0038/current shape';
    END IF;

    SELECT count(*) INTO step_count
    FROM tide.task_step_definitions
    WHERE execution_version_id = execution_id;

    IF step_count NOT IN (0, 1) OR EXISTS (
        SELECT 1
        FROM tide.task_step_definitions
        WHERE execution_version_id = execution_id
          AND NOT (
              step_key = 'p-fb-negative-environment-photo'
              AND position = 1
              AND step_type = 'UPLOAD'
              AND title = 'Take a teaching-environment photo'
              AND config = '{"version":"2026-08-11-personalized-environment-photo-v1","role":"ENVIRONMENT_PHOTO","reviewProfile":"TEACHING_ENVIRONMENT_V1","accept":["image/jpeg"],"captureOnly":true,"maxFiles":1}'::jsonb
          )
    ) THEN
        RAISE EXCEPTION
            'P-FB-NEGATIVE contains an unreviewed step; migration 0038 will not replace unknown content';
    END IF;

    SELECT count(*) INTO rule_count
    FROM tide.task_validation_rules
    WHERE execution_version_id = execution_id;

    IF rule_count NOT IN (0, 2) OR EXISTS (
        SELECT 1
        FROM tide.task_validation_rules
        WHERE execution_version_id = execution_id
          AND NOT (
              (
                  rule_key = 'all-steps-complete'
                  AND rule_type = 'ALL_STEPS_COMPLETE'
                  AND rule_version = '2026-08-11-personalized-environment-photo-v1'
                  AND position = 1
                  AND config = '{"requiredStepKeys":["p-fb-negative-environment-photo"]}'::jsonb
                  AND teacher_failure_copy = '请拍摄并提交一张当前授课环境照片。'
              )
              OR
              (
                  rule_key = 'p-fb-negative-environment-ai-review'
                  AND rule_type = 'AI_IMAGE_REVIEW'
                  AND rule_version = '2026-07-27-strict'
                  AND position = 2
                  AND config = '{"stepKey":"p-fb-negative-environment-photo","criteriaVersion":"personalized-teaching-environment-2026-08-v1","criteriaKeys":["camera_angle","lighting","background","dressing"],"allowedMimeTypes":["image/jpeg","image/png","image/webp"],"reviewProfile":"TEACHING_ENVIRONMENT_V1","systemPrompt":"You strictly review teacher-submitted evidence. Return JSON only with this exact shape: {\"decision\":\"PASS|RETRY|ERROR\",\"teacherReason\":\"teacher-safe concise message\",\"confidenceSummary\":{},\"criteria\":[{\"criterionKey\":\"one configured key\",\"result\":\"PASS|FAIL|UNKNOWN\",\"teacherMessage\":\"teacher-safe message or null\"}]}. Include every configured criterion exactly once. Never infer a pass from the mere presence of a person or object. Use UNKNOWN whenever the visual evidence is unclear. PASS only when every configured criterion is visibly and unambiguously PASS; any FAIL or UNKNOWN requires RETRY. Use ERROR only when the file cannot be assessed. Do not expose internal risk labels or private model reasoning.","userText":"Review this current teaching-environment photo strictly against camera angle, lighting, background and dressing only."}'::jsonb
                  AND teacher_failure_copy = '已保留你完成的内容，请根据提示更新这份材料。'
              )
          )
    ) THEN
        RAISE EXCEPTION
            'P-FB-NEGATIVE contains an unreviewed validation rule; migration 0038 will not replace it';
    END IF;

    IF (step_count = 0) <> (rule_count = 0) THEN
        RAISE EXCEPTION
            'P-FB-NEGATIVE has a partial photo execution; migration 0038 will not infer missing definitions';
    END IF;
END
$$;

CREATE TEMP TABLE p_fb_negative_execution_identity_before ON COMMIT DROP AS
SELECT id
FROM tide.task_execution_versions
WHERE shared_template_row_id = 'P-FB-NEGATIVE:v1';

CREATE TEMP TABLE p_fb_negative_step_identity_before ON COMMIT DROP AS
SELECT definition.step_key, definition.id
FROM tide.task_step_definitions AS definition
JOIN tide.task_execution_versions AS execution
  ON execution.id = definition.execution_version_id
WHERE execution.shared_template_row_id = 'P-FB-NEGATIVE:v1';

CREATE TEMP TABLE p_fb_negative_rule_identity_before ON COMMIT DROP AS
SELECT rule.rule_key, rule.id
FROM tide.task_validation_rules AS rule
JOIN tide.task_execution_versions AS execution
  ON execution.id = rule.execution_version_id
WHERE execution.shared_template_row_id = 'P-FB-NEGATIVE:v1';

CREATE TEMP TABLE p_fb_negative_assignment_before ON COMMIT DROP AS
SELECT assignment.assignment_id, to_jsonb(assignment) AS row_data
FROM public.task_assignments AS assignment
WHERE assignment.template_version_id = 'P-FB-NEGATIVE:v1';

CREATE TEMP TABLE p_fb_negative_progress_before ON COMMIT DROP AS
SELECT progress.id, to_jsonb(progress) AS row_data
FROM tide.task_step_progress AS progress
JOIN public.task_assignments AS assignment
  ON assignment.assignment_id = progress.task_assignment_id
WHERE assignment.template_version_id = 'P-FB-NEGATIVE:v1';

UPDATE tide.task_execution_versions
SET execution_contract_version = 'task-contract-v3',
    config = '{"estimatedMinutes":8,"allowRetry":true,"contentStatus":"PENDING","contentVersion":"2026-08-11-personalized-environment-photo-v1","pendingReason":"JIAHE_PERSONALIZED_CONTENT_PENDING","independentModules":{"stepKeys":["p-fb-negative-environment-photo"],"allowOutOfOrderProgress":true,"keepAssignmentInProgressUntilPassed":true}}'::jsonb,
    updated_at = now()
WHERE shared_template_row_id = 'P-FB-NEGATIVE:v1';

INSERT INTO tide.task_step_definitions (
    id, execution_version_id, step_key, position, step_type, title, config
)
SELECT
    '8911e60c-012d-4e01-8ecc-1cb10de35c83'::uuid,
    execution.id,
    'p-fb-negative-environment-photo',
    1,
    'UPLOAD',
    'Take a teaching-environment photo',
    '{"version":"2026-08-11-personalized-environment-photo-v1","role":"ENVIRONMENT_PHOTO","reviewProfile":"TEACHING_ENVIRONMENT_V1","accept":["image/jpeg"],"captureOnly":true,"maxFiles":1}'::jsonb
FROM tide.task_execution_versions AS execution
WHERE execution.shared_template_row_id = 'P-FB-NEGATIVE:v1'
ON CONFLICT (execution_version_id, step_key) DO UPDATE
SET position = EXCLUDED.position,
    step_type = EXCLUDED.step_type,
    title = EXCLUDED.title,
    config = EXCLUDED.config;

INSERT INTO tide.task_validation_rules (
    id, execution_version_id, rule_key, rule_type, rule_version,
    position, config, teacher_failure_copy
)
SELECT
    '0ab8cf5d-be42-4582-821c-543812f25a19'::uuid,
    execution.id,
    'all-steps-complete',
    'ALL_STEPS_COMPLETE',
    '2026-08-11-personalized-environment-photo-v1',
    1,
    '{"requiredStepKeys":["p-fb-negative-environment-photo"]}'::jsonb,
    '请拍摄并提交一张当前授课环境照片。'
FROM tide.task_execution_versions AS execution
WHERE execution.shared_template_row_id = 'P-FB-NEGATIVE:v1'
ON CONFLICT (execution_version_id, rule_key) DO UPDATE
SET rule_type = EXCLUDED.rule_type,
    rule_version = EXCLUDED.rule_version,
    position = EXCLUDED.position,
    config = EXCLUDED.config,
    teacher_failure_copy = EXCLUDED.teacher_failure_copy;

INSERT INTO tide.task_validation_rules (
    id, execution_version_id, rule_key, rule_type, rule_version,
    position, config, teacher_failure_copy
)
SELECT
    'b823d749-969d-4194-88d7-9ff8de9d2c51'::uuid,
    execution.id,
    'p-fb-negative-environment-ai-review',
    'AI_IMAGE_REVIEW',
    '2026-07-27-strict',
    2,
    jsonb_build_object(
        'stepKey', 'p-fb-negative-environment-photo',
        'criteriaVersion', 'personalized-teaching-environment-2026-08-v1',
        'criteriaKeys', jsonb_build_array('camera_angle', 'lighting', 'background', 'dressing'),
        'allowedMimeTypes', jsonb_build_array('image/jpeg', 'image/png', 'image/webp'),
        'reviewProfile', 'TEACHING_ENVIRONMENT_V1',
        'systemPrompt', 'You strictly review teacher-submitted evidence. Return JSON only with this exact shape: {"decision":"PASS|RETRY|ERROR","teacherReason":"teacher-safe concise message","confidenceSummary":{},"criteria":[{"criterionKey":"one configured key","result":"PASS|FAIL|UNKNOWN","teacherMessage":"teacher-safe message or null"}]}. Include every configured criterion exactly once. Never infer a pass from the mere presence of a person or object. Use UNKNOWN whenever the visual evidence is unclear. PASS only when every configured criterion is visibly and unambiguously PASS; any FAIL or UNKNOWN requires RETRY. Use ERROR only when the file cannot be assessed. Do not expose internal risk labels or private model reasoning.',
        'userText', 'Review this current teaching-environment photo strictly against camera angle, lighting, background and dressing only.'
    ),
    '已保留你完成的内容，请根据提示更新这份材料。'
FROM tide.task_execution_versions AS execution
WHERE execution.shared_template_row_id = 'P-FB-NEGATIVE:v1'
ON CONFLICT (execution_version_id, rule_key) DO UPDATE
SET rule_type = EXCLUDED.rule_type,
    rule_version = EXCLUDED.rule_version,
    position = EXCLUDED.position,
    config = EXCLUDED.config,
    teacher_failure_copy = EXCLUDED.teacher_failure_copy;

DO $$
DECLARE
    execution_id uuid;
BEGIN
    SELECT id INTO execution_id
    FROM tide.task_execution_versions
    WHERE shared_template_row_id = 'P-FB-NEGATIVE:v1';

    IF execution_id IS NULL THEN
        IF EXISTS (SELECT 1 FROM p_fb_negative_execution_identity_before) THEN
            RAISE EXCEPTION 'migration 0038 removed the existing P-FB-NEGATIVE execution';
        END IF;
        RETURN;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM p_fb_negative_execution_identity_before
        WHERE id = execution_id
    ) THEN
        RAISE EXCEPTION 'migration 0038 replaced the P-FB-NEGATIVE execution ID';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM tide.task_execution_versions
        WHERE id = execution_id
          AND task_code = 'P-FB-NEGATIVE'
          AND status = 'ACTIVE'
          AND execution_contract_version = 'task-contract-v3'
          AND config = '{"estimatedMinutes":8,"allowRetry":true,"contentStatus":"PENDING","contentVersion":"2026-08-11-personalized-environment-photo-v1","pendingReason":"JIAHE_PERSONALIZED_CONTENT_PENDING","independentModules":{"stepKeys":["p-fb-negative-environment-photo"],"allowOutOfOrderProgress":true,"keepAssignmentInProgressUntilPassed":true}}'::jsonb
    ) THEN
        RAISE EXCEPTION 'migration 0038 did not publish the exact P-FB-NEGATIVE execution config';
    END IF;

    IF (
        SELECT count(*) FROM tide.task_step_definitions
        WHERE execution_version_id = execution_id
    ) <> 1 OR NOT EXISTS (
        SELECT 1 FROM tide.task_step_definitions
        WHERE execution_version_id = execution_id
          AND step_key = 'p-fb-negative-environment-photo'
          AND position = 1
          AND step_type = 'UPLOAD'
          AND title = 'Take a teaching-environment photo'
          AND config = '{"version":"2026-08-11-personalized-environment-photo-v1","role":"ENVIRONMENT_PHOTO","reviewProfile":"TEACHING_ENVIRONMENT_V1","accept":["image/jpeg"],"captureOnly":true,"maxFiles":1}'::jsonb
    ) THEN
        RAISE EXCEPTION 'migration 0038 did not produce the exact personalized photo step';
    END IF;

    IF (
        SELECT count(*) FROM tide.task_validation_rules
        WHERE execution_version_id = execution_id
    ) <> 2 OR NOT EXISTS (
        SELECT 1 FROM tide.task_validation_rules
        WHERE execution_version_id = execution_id
          AND rule_key = 'all-steps-complete'
          AND rule_type = 'ALL_STEPS_COMPLETE'
          AND rule_version = '2026-08-11-personalized-environment-photo-v1'
          AND position = 1
          AND config = '{"requiredStepKeys":["p-fb-negative-environment-photo"]}'::jsonb
          AND teacher_failure_copy = '请拍摄并提交一张当前授课环境照片。'
    ) OR NOT EXISTS (
        SELECT 1 FROM tide.task_validation_rules
        WHERE execution_version_id = execution_id
          AND rule_key = 'p-fb-negative-environment-ai-review'
          AND rule_type = 'AI_IMAGE_REVIEW'
          AND rule_version = '2026-07-27-strict'
          AND position = 2
          AND config = '{"stepKey":"p-fb-negative-environment-photo","criteriaVersion":"personalized-teaching-environment-2026-08-v1","criteriaKeys":["camera_angle","lighting","background","dressing"],"allowedMimeTypes":["image/jpeg","image/png","image/webp"],"reviewProfile":"TEACHING_ENVIRONMENT_V1","systemPrompt":"You strictly review teacher-submitted evidence. Return JSON only with this exact shape: {\"decision\":\"PASS|RETRY|ERROR\",\"teacherReason\":\"teacher-safe concise message\",\"confidenceSummary\":{},\"criteria\":[{\"criterionKey\":\"one configured key\",\"result\":\"PASS|FAIL|UNKNOWN\",\"teacherMessage\":\"teacher-safe message or null\"}]}. Include every configured criterion exactly once. Never infer a pass from the mere presence of a person or object. Use UNKNOWN whenever the visual evidence is unclear. PASS only when every configured criterion is visibly and unambiguously PASS; any FAIL or UNKNOWN requires RETRY. Use ERROR only when the file cannot be assessed. Do not expose internal risk labels or private model reasoning.","userText":"Review this current teaching-environment photo strictly against camera angle, lighting, background and dressing only."}'::jsonb
          AND teacher_failure_copy = '已保留你完成的内容，请根据提示更新这份材料。'
    ) THEN
        RAISE EXCEPTION 'migration 0038 did not produce the exact personalized photo rules';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM p_fb_negative_step_identity_before AS before
        LEFT JOIN tide.task_step_definitions AS after
          ON after.execution_version_id = execution_id
         AND after.step_key = before.step_key
         AND after.id = before.id
        WHERE after.id IS NULL
    ) OR EXISTS (
        SELECT 1
        FROM p_fb_negative_rule_identity_before AS before
        LEFT JOIN tide.task_validation_rules AS after
          ON after.execution_version_id = execution_id
         AND after.rule_key = before.rule_key
         AND after.id = before.id
        WHERE after.id IS NULL
    ) THEN
        RAISE EXCEPTION 'migration 0038 replaced an existing step or rule ID';
    END IF;

    IF EXISTS (
        (SELECT assignment_id, row_data FROM p_fb_negative_assignment_before
         EXCEPT
         SELECT assignment.assignment_id, to_jsonb(assignment)
         FROM public.task_assignments AS assignment
         WHERE assignment.template_version_id = 'P-FB-NEGATIVE:v1')
        UNION ALL
        (SELECT assignment.assignment_id, to_jsonb(assignment)
         FROM public.task_assignments AS assignment
         WHERE assignment.template_version_id = 'P-FB-NEGATIVE:v1'
         EXCEPT
         SELECT assignment_id, row_data FROM p_fb_negative_assignment_before)
    ) THEN
        RAISE EXCEPTION 'migration 0038 modified a P-FB-NEGATIVE assignment fact';
    END IF;

    IF EXISTS (
        (SELECT id, row_data FROM p_fb_negative_progress_before
         EXCEPT
         SELECT progress.id, to_jsonb(progress)
         FROM tide.task_step_progress AS progress
         JOIN public.task_assignments AS assignment
           ON assignment.assignment_id = progress.task_assignment_id
         WHERE assignment.template_version_id = 'P-FB-NEGATIVE:v1')
        UNION ALL
        (SELECT progress.id, to_jsonb(progress)
         FROM tide.task_step_progress AS progress
         JOIN public.task_assignments AS assignment
           ON assignment.assignment_id = progress.task_assignment_id
         WHERE assignment.template_version_id = 'P-FB-NEGATIVE:v1'
         EXCEPT
         SELECT id, row_data FROM p_fb_negative_progress_before)
    ) THEN
        RAISE EXCEPTION 'migration 0038 modified existing P-FB-NEGATIVE progress';
    END IF;
END
$$;

COMMIT;
