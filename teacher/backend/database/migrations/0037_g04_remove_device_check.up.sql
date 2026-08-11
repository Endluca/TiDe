BEGIN;

SET LOCAL lock_timeout = '10s';

-- G04 keeps the stable operations identity G02:v1. The device-check
-- definition is removed from the active execution only; historical progress
-- and device evidence remain attached to the existing assignment/attempt IDs.
DO $$
BEGIN
    IF to_regclass('public.task_templates') IS NULL
       OR to_regclass('public.task_assignments') IS NULL
       OR to_regclass('tide.task_execution_versions') IS NULL
       OR to_regclass('tide.task_step_definitions') IS NULL
       OR to_regclass('tide.task_validation_rules') IS NULL
       OR to_regclass('tide.task_step_progress') IS NULL
       OR to_regclass('tide.task_attempts') IS NULL
       OR to_regclass('tide.device_check_runs') IS NULL
       OR to_regclass('tide.device_check_item_results') IS NULL THEN
        RAISE EXCEPTION
            'shared task tables and Tide execution/evidence tables are required before migration 0037';
    END IF;
END
$$;

LOCK TABLE public.task_templates IN SHARE MODE;
LOCK TABLE public.task_assignments IN SHARE MODE;
LOCK TABLE tide.task_execution_versions IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE tide.task_step_definitions IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE tide.task_validation_rules IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE tide.task_step_progress IN SHARE MODE;
LOCK TABLE tide.task_attempts IN SHARE MODE;
LOCK TABLE tide.device_check_runs IN SHARE MODE;
LOCK TABLE tide.device_check_item_results IN SHARE MODE;

DO $$
DECLARE
    fixed_execution_count integer;
    current_execution_count integer;
    retired_g00_execution_count integer;
    g04_execution_count integer;
BEGIN
    IF (
        SELECT count(*)
        FROM public.task_templates
        WHERE row_id = 'G02:v1'
          AND template_id = 'G04'
          AND status = 'PUBLISHED'
          AND execution_owner = 'TEACHER_APP'
          AND payload->>'template_id' = 'G04'
          AND payload->>'category' = 'MANDATORY_GROWTH'
          AND payload->>'ops_name_zh' = '首课准备'
          AND payload->>'title' = 'Lesson Preparation'
          AND payload->>'why_template' =
              'Complete the teaching-environment photo review and prepare the courseware before your first lesson.'
          AND payload->>'how_summary' =
              'Complete two sections in any order: submit one teaching-environment photo for AI review and prepare the courseware for your first lesson. Each section keeps its own progress.'
          AND payload->>'completion_standard' =
              'G04 is completed only after both sections pass: all four teaching-environment photo criteria—camera angle, lighting, background and dressing—pass AI review, and the courseware preparation is confirmed. The sections may be completed in any order.'
          AND payload->>'benefit' =
              'Your teaching environment and courseware are ready for your first lesson.'
          AND payload->>'content_status' = 'READY'
          AND (payload->>'score_value')::integer = 3
    ) <> 1 THEN
        RAISE EXCEPTION
            'operations G04 stable template G02:v1 is not the approved Lesson Preparation row for migration 0037';
    END IF;

    SELECT count(*)
    INTO fixed_execution_count
    FROM tide.task_execution_versions
    WHERE shared_template_row_id IN (
        'G01:v1', 'G02:v1', 'G03:v1', 'G04:v1', 'G05:v1',
        'G06:v1', 'G07:v1', 'G08:v1', 'G09:v1', 'G10:v1'
    );

    SELECT count(*)
    INTO current_execution_count
    FROM (
        VALUES
            ('G01:v1', 'G01'),
            ('G02:v1', 'G04'),
            ('G03:v1', 'G02'),
            ('G04:v1', 'G03'),
            ('G06:v1', 'G05'),
            ('G07:v1', 'G06'),
            ('G08:v1', 'G07'),
            ('G09:v1', 'G08'),
            ('G10:v1', 'G09')
    ) AS expected(row_id, task_code)
    JOIN tide.task_execution_versions AS execution
      ON execution.shared_template_row_id = expected.row_id
     AND execution.task_code = expected.task_code
     AND execution.status = 'ACTIVE';

    SELECT count(*)
    INTO retired_g00_execution_count
    FROM tide.task_execution_versions
    WHERE shared_template_row_id = 'G05:v1'
      AND task_code = 'G00'
      AND status = 'RETIRED';

    SELECT count(*)
    INTO g04_execution_count
    FROM tide.task_execution_versions
    WHERE shared_template_row_id = 'G02:v1';

    IF fixed_execution_count = 0 THEN
        RAISE NOTICE
            'migration 0037 left the empty execution catalog unchanged; run the explicit current catalog seed before rollout';
        RETURN;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM tide.task_execution_versions
        WHERE (
            shared_template_row_id = 'G05:v1'
            OR task_code = 'G00'
        )
          AND NOT (
              shared_template_row_id = 'G05:v1'
              AND task_code = 'G00'
              AND status = 'RETIRED'
          )
    ) THEN
        RAISE EXCEPTION
            'retired G00 execution is attached to an unexpected row or status';
    END IF;

    IF current_execution_count <> 9
       OR retired_g00_execution_count NOT IN (0, 1)
       OR fixed_execution_count <> 9 + retired_g00_execution_count
       OR g04_execution_count <> 1 THEN
        RAISE EXCEPTION
            'existing fixed execution catalog is incomplete or inconsistent (current mapped %/9, retired G00 rows %, G04 rows %, scoped rows %); migration 0037 will not change execution identity',
            current_execution_count,
            retired_g00_execution_count,
            g04_execution_count,
            fixed_execution_count;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM tide.task_execution_versions
        WHERE task_code = 'G04'
          AND shared_template_row_id <> 'G02:v1'
    ) THEN
        RAISE EXCEPTION
            'task code G04 is attached to a row other than stable template G02:v1';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM tide.task_execution_versions
        WHERE shared_template_row_id = 'G02:v1'
          AND (
              task_code <> 'G04'
              OR status <> 'ACTIVE'
              OR execution_contract_version <> 'task-contract-v3'
              OR config NOT IN (
                  '{"estimatedMinutes":15,"allowRetry":true,"contentStatus":"READY","contentVersion":"2026-08-05-g04-three-part","pendingReason":null,"independentModules":{"stepKeys":["g02-device-check","g02-environment-photo","g02-courseware-confirmation"],"allowOutOfOrderProgress":true,"keepAssignmentInProgressUntilPassed":true}}'::jsonb,
                  '{"estimatedMinutes":15,"allowRetry":true,"contentStatus":"READY","contentVersion":"2026-08-11-g04-two-part","pendingReason":null,"independentModules":{"stepKeys":["g02-environment-photo","g02-courseware-confirmation"],"allowOutOfOrderProgress":true,"keepAssignmentInProgressUntilPassed":true}}'::jsonb
              )
          )
    ) THEN
        RAISE EXCEPTION
            'G04 execution identity or config is not an approved pre-0037/current shape';
    END IF;
END
$$;

DO $$
DECLARE
    execution_id uuid;
    step_count integer;
    rule_count integer;
BEGIN
    SELECT id INTO execution_id
    FROM tide.task_execution_versions
    WHERE shared_template_row_id = 'G02:v1';

    IF execution_id IS NULL THEN
        RETURN;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM tide.task_step_definitions
        WHERE execution_version_id = execution_id
          AND step_key NOT IN (
              'g02-device-check',
              'g02-environment-photo',
              'g02-courseware-confirmation'
          )
    ) THEN
        RAISE EXCEPTION
            'G04 contains an unreviewed step; migration 0037 will not delete unknown steps';
    END IF;

    SELECT count(*) INTO step_count
    FROM tide.task_step_definitions
    WHERE execution_version_id = execution_id;

    IF step_count = 3 THEN
        IF NOT EXISTS (
            SELECT 1
            FROM tide.task_step_definitions
            WHERE execution_version_id = execution_id
              AND step_key = 'g02-device-check'
              AND position = 1
              AND step_type = 'DEVICE_CHECK'
              AND title = 'Check camera, microphone and network'
              AND config =
                  '{"version":"g02-device-2026-08-05-browser-preflight-v1","role":"DEVICE_CHECK","items":["camera","microphone","network"]}'::jsonb
        ) OR NOT EXISTS (
            SELECT 1
            FROM tide.task_step_definitions
            WHERE execution_version_id = execution_id
              AND step_key = 'g02-courseware-confirmation'
              AND position = 2
              AND step_type = 'CHECKLIST'
              AND title = 'Review and confirm lesson-preparation guidance'
              AND config =
                  '{"version":"g02-courseware-2026-08-05-guidance-v1","role":"COURSEWARE_CONFIRMATION","items":[{"key":"courseware-prepared","label":"I have reviewed the lesson-preparation guidance and all slides, and I am ready for this lesson.","labelZh":"我已阅读备课须知并浏览全部课件，已完成本节课备课。"}]}'::jsonb
        ) OR NOT EXISTS (
            SELECT 1
            FROM tide.task_step_definitions
            WHERE execution_version_id = execution_id
              AND step_key = 'g02-environment-photo'
              AND position = 3
              AND step_type = 'UPLOAD'
              AND title = 'Take a teaching-environment photo'
              AND config =
                  '{"version":"g02-photo-2026-07-22","role":"ENVIRONMENT_PHOTO","accept":["image/jpeg"],"captureOnly":true,"maxFiles":1}'::jsonb
        ) THEN
            RAISE EXCEPTION
                'G04 three-step layout is not the exact migration 0031 shape';
        END IF;
    ELSIF step_count = 2 THEN
        IF NOT EXISTS (
            SELECT 1
            FROM tide.task_step_definitions
            WHERE execution_version_id = execution_id
              AND step_key = 'g02-environment-photo'
              AND position = 1
              AND step_type = 'UPLOAD'
              AND title = 'Take a teaching-environment photo'
              AND config =
                  '{"version":"g02-photo-2026-07-22","role":"ENVIRONMENT_PHOTO","accept":["image/jpeg"],"captureOnly":true,"maxFiles":1}'::jsonb
        ) OR NOT EXISTS (
            SELECT 1
            FROM tide.task_step_definitions
            WHERE execution_version_id = execution_id
              AND step_key = 'g02-courseware-confirmation'
              AND position = 2
              AND step_type = 'CHECKLIST'
              AND title = 'Review and confirm lesson-preparation guidance'
              AND config =
                  '{"version":"g02-courseware-2026-08-05-guidance-v1","role":"COURSEWARE_CONFIRMATION","items":[{"key":"courseware-prepared","label":"I have reviewed the lesson-preparation guidance and all slides, and I am ready for this lesson.","labelZh":"我已阅读备课须知并浏览全部课件，已完成本节课备课。"}]}'::jsonb
        ) THEN
            RAISE EXCEPTION
                'G04 two-step layout is not the exact migration 0037 shape';
        END IF;
    ELSE
        RAISE EXCEPTION
            'G04 step count % is not an approved three-step or two-step layout',
            step_count;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM tide.task_validation_rules
        WHERE execution_version_id = execution_id
          AND rule_key NOT IN (
              'all-steps-complete',
              'g02-environment-ai-review'
          )
    ) THEN
        RAISE EXCEPTION
            'G04 contains an unreviewed validation rule; migration 0037 will not delete it';
    END IF;

    SELECT count(*) INTO rule_count
    FROM tide.task_validation_rules
    WHERE execution_version_id = execution_id;

    IF rule_count <> 2 THEN
        RAISE EXCEPTION
            'G04 must contain exactly the completion rule and photo AI rule before migration 0037 (found %)',
            rule_count;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM tide.task_validation_rules
        WHERE execution_version_id = execution_id
          AND rule_key = 'all-steps-complete'
          AND rule_type = 'ALL_STEPS_COMPLETE'
          AND position = 1
          AND (
              (
                  rule_version = '2026-08-05-g04-three-part-v1'
                  AND config =
                      '{"requiredStepKeys":["g02-device-check","g02-environment-photo","g02-courseware-confirmation"]}'::jsonb
                  AND teacher_failure_copy =
                      '请分别完成备课须知确认、设备网络检测和授课环境照片四项检查，三部分可任意顺序完成。'
              )
              OR
              (
                  rule_version = '2026-08-11-g04-two-part-v1'
                  AND config =
                      '{"requiredStepKeys":["g02-environment-photo","g02-courseware-confirmation"]}'::jsonb
                  AND teacher_failure_copy =
                      '请分别完成授课环境照片检查和课件准备确认，两部分可任意顺序完成。'
              )
          )
    ) THEN
        RAISE EXCEPTION
            'G04 ALL_STEPS_COMPLETE rule is not an approved pre-0037/current shape';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM tide.task_validation_rules
        WHERE execution_version_id = execution_id
          AND rule_key = 'g02-environment-ai-review'
          AND rule_type = 'AI_IMAGE_REVIEW'
          AND rule_version = '2026-07-27-strict'
          AND position = 2
          AND teacher_failure_copy =
              '已保留你完成的内容，请根据提示更新这份材料。'
          AND config->>'stepKey' = 'g02-environment-photo'
          AND config->>'criteriaVersion' =
              'lesson-preparation-camera-view-2026-08-v7-background-veto'
          AND config->'criteriaKeys' =
              '["camera_angle","lighting","background","dressing"]'::jsonb
          AND config->'allowedMimeTypes' =
              '["image/jpeg","image/png","image/webp"]'::jsonb
          AND config->>'systemPrompt' =
              'You strictly review teacher-submitted evidence. Return JSON only with this exact shape: {"decision":"PASS|RETRY|ERROR","teacherReason":"teacher-safe concise message","confidenceSummary":{},"criteria":[{"criterionKey":"one configured key","result":"PASS|FAIL|UNKNOWN","teacherMessage":"teacher-safe message or null"}]}. Include every configured criterion exactly once. Never infer a pass from the mere presence of a person or object. Use UNKNOWN whenever the visual evidence is unclear. PASS only when every configured criterion is visibly and unambiguously PASS; any FAIL or UNKNOWN requires RETRY. Use ERROR only when the file cannot be assessed. Do not expose internal risk labels or private model reasoning.'
          AND config->>'userText' =
              'Review this real teaching-environment photo strictly against camera angle, lighting, background and dressing only.'
          AND (
              SELECT count(*)
              FROM jsonb_object_keys(config)
          ) = 6
    ) THEN
        RAISE EXCEPTION
            'G04 photo AI rule is not the exact migration 0031 shape';
    END IF;

    IF NOT (
        (
            step_count = 3
            AND EXISTS (
                SELECT 1
                FROM tide.task_execution_versions
                WHERE id = execution_id
                  AND config =
                      '{"estimatedMinutes":15,"allowRetry":true,"contentStatus":"READY","contentVersion":"2026-08-05-g04-three-part","pendingReason":null,"independentModules":{"stepKeys":["g02-device-check","g02-environment-photo","g02-courseware-confirmation"],"allowOutOfOrderProgress":true,"keepAssignmentInProgressUntilPassed":true}}'::jsonb
            )
            AND EXISTS (
                SELECT 1
                FROM tide.task_validation_rules
                WHERE execution_version_id = execution_id
                  AND rule_key = 'all-steps-complete'
                  AND rule_version = '2026-08-05-g04-three-part-v1'
                  AND config =
                      '{"requiredStepKeys":["g02-device-check","g02-environment-photo","g02-courseware-confirmation"]}'::jsonb
                  AND teacher_failure_copy =
                      '请分别完成备课须知确认、设备网络检测和授课环境照片四项检查，三部分可任意顺序完成。'
            )
        )
        OR
        (
            step_count = 2
            AND EXISTS (
                SELECT 1
                FROM tide.task_execution_versions
                WHERE id = execution_id
                  AND config =
                      '{"estimatedMinutes":15,"allowRetry":true,"contentStatus":"READY","contentVersion":"2026-08-11-g04-two-part","pendingReason":null,"independentModules":{"stepKeys":["g02-environment-photo","g02-courseware-confirmation"],"allowOutOfOrderProgress":true,"keepAssignmentInProgressUntilPassed":true}}'::jsonb
            )
            AND EXISTS (
                SELECT 1
                FROM tide.task_validation_rules
                WHERE execution_version_id = execution_id
                  AND rule_key = 'all-steps-complete'
                  AND rule_version = '2026-08-11-g04-two-part-v1'
                  AND config =
                      '{"requiredStepKeys":["g02-environment-photo","g02-courseware-confirmation"]}'::jsonb
                  AND teacher_failure_copy =
                      '请分别完成授课环境照片检查和课件准备确认，两部分可任意顺序完成。'
            )
        )
    ) THEN
        RAISE EXCEPTION
            'G04 execution, steps and completion rule form an unreviewed mixed shape';
    END IF;
END
$$;

CREATE TEMP TABLE g04_execution_identity_before ON COMMIT DROP AS
SELECT id
FROM tide.task_execution_versions
WHERE shared_template_row_id = 'G02:v1';

CREATE TEMP TABLE g04_remaining_step_identity_before ON COMMIT DROP AS
SELECT definition.step_key, definition.id
FROM tide.task_step_definitions AS definition
JOIN tide.task_execution_versions AS execution
  ON execution.id = definition.execution_version_id
WHERE execution.shared_template_row_id = 'G02:v1'
  AND definition.step_key IN (
      'g02-environment-photo',
      'g02-courseware-confirmation'
  );

CREATE TEMP TABLE g04_rule_identity_before ON COMMIT DROP AS
SELECT rule.rule_key, rule.id
FROM tide.task_validation_rules AS rule
JOIN tide.task_execution_versions AS execution
  ON execution.id = rule.execution_version_id
WHERE execution.shared_template_row_id = 'G02:v1';

CREATE TEMP TABLE g04_photo_ai_rule_before ON COMMIT DROP AS
SELECT rule.id, to_jsonb(rule) AS row_data
FROM tide.task_validation_rules AS rule
JOIN tide.task_execution_versions AS execution
  ON execution.id = rule.execution_version_id
WHERE execution.shared_template_row_id = 'G02:v1'
  AND rule.rule_key = 'g02-environment-ai-review';

CREATE TEMP TABLE g04_assignment_before ON COMMIT DROP AS
SELECT assignment.assignment_id, to_jsonb(assignment) AS row_data
FROM public.task_assignments AS assignment
WHERE assignment.template_version_id = 'G02:v1';

CREATE TEMP TABLE g04_progress_before ON COMMIT DROP AS
SELECT progress.id, to_jsonb(progress) AS row_data
FROM tide.task_step_progress AS progress
JOIN public.task_assignments AS assignment
  ON assignment.assignment_id = progress.task_assignment_id
WHERE assignment.template_version_id = 'G02:v1';

CREATE TEMP TABLE g04_device_run_before ON COMMIT DROP AS
SELECT run.id, to_jsonb(run) AS row_data
FROM tide.device_check_runs AS run
JOIN tide.task_attempts AS attempt
  ON attempt.id = run.task_attempt_id
JOIN public.task_assignments AS assignment
  ON assignment.assignment_id = attempt.task_assignment_id
WHERE assignment.template_version_id = 'G02:v1';

CREATE TEMP TABLE g04_device_item_before ON COMMIT DROP AS
SELECT item.id, to_jsonb(item) AS row_data
FROM tide.device_check_item_results AS item
JOIN tide.device_check_runs AS run
  ON run.id = item.device_check_run_id
JOIN tide.task_attempts AS attempt
  ON attempt.id = run.task_attempt_id
JOIN public.task_assignments AS assignment
  ON assignment.assignment_id = attempt.task_assignment_id
WHERE assignment.template_version_id = 'G02:v1';

UPDATE tide.task_execution_versions
SET config =
        '{"estimatedMinutes":15,"allowRetry":true,"contentStatus":"READY","contentVersion":"2026-08-11-g04-two-part","pendingReason":null,"independentModules":{"stepKeys":["g02-environment-photo","g02-courseware-confirmation"],"allowOutOfOrderProgress":true,"keepAssignmentInProgressUntilPassed":true}}'::jsonb,
    updated_at = now()
WHERE shared_template_row_id = 'G02:v1'
  AND config IS DISTINCT FROM
        '{"estimatedMinutes":15,"allowRetry":true,"contentStatus":"READY","contentVersion":"2026-08-11-g04-two-part","pendingReason":null,"independentModules":{"stepKeys":["g02-environment-photo","g02-courseware-confirmation"],"allowOutOfOrderProgress":true,"keepAssignmentInProgressUntilPassed":true}}'::jsonb;

DELETE FROM tide.task_step_definitions AS definition
USING tide.task_execution_versions AS execution
WHERE execution.id = definition.execution_version_id
  AND execution.shared_template_row_id = 'G02:v1'
  AND definition.step_key = 'g02-device-check';

UPDATE tide.task_step_definitions AS definition
SET position = CASE definition.step_key
        WHEN 'g02-environment-photo' THEN 1
        WHEN 'g02-courseware-confirmation' THEN 2
        ELSE definition.position
    END
FROM tide.task_execution_versions AS execution
WHERE execution.id = definition.execution_version_id
  AND execution.shared_template_row_id = 'G02:v1'
  AND definition.step_key IN (
      'g02-environment-photo',
      'g02-courseware-confirmation'
  );

UPDATE tide.task_validation_rules AS rule
SET rule_version = '2026-08-11-g04-two-part-v1',
    config =
        '{"requiredStepKeys":["g02-environment-photo","g02-courseware-confirmation"]}'::jsonb,
    teacher_failure_copy =
        '请分别完成授课环境照片检查和课件准备确认，两部分可任意顺序完成。'
FROM tide.task_execution_versions AS execution
WHERE execution.id = rule.execution_version_id
  AND execution.shared_template_row_id = 'G02:v1'
  AND rule.rule_key = 'all-steps-complete';

DO $$
DECLARE
    execution_id uuid;
BEGIN
    SELECT id INTO execution_id
    FROM tide.task_execution_versions
    WHERE shared_template_row_id = 'G02:v1';

    IF execution_id IS NULL THEN
        IF EXISTS (SELECT 1 FROM g04_execution_identity_before) THEN
            RAISE EXCEPTION 'migration 0037 removed the existing G04 execution';
        END IF;
        RETURN;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM g04_execution_identity_before AS before
        WHERE before.id = execution_id
    ) THEN
        RAISE EXCEPTION 'migration 0037 replaced the existing G04 execution ID';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM tide.task_execution_versions
        WHERE id = execution_id
          AND shared_template_row_id = 'G02:v1'
          AND task_code = 'G04'
          AND execution_contract_version = 'task-contract-v3'
          AND status = 'ACTIVE'
          AND config =
              '{"estimatedMinutes":15,"allowRetry":true,"contentStatus":"READY","contentVersion":"2026-08-11-g04-two-part","pendingReason":null,"independentModules":{"stepKeys":["g02-environment-photo","g02-courseware-confirmation"],"allowOutOfOrderProgress":true,"keepAssignmentInProgressUntilPassed":true}}'::jsonb
    ) THEN
        RAISE EXCEPTION
            'migration 0037 did not publish the exact two-part G04 execution config';
    END IF;

    IF (
        SELECT count(*)
        FROM tide.task_step_definitions
        WHERE execution_version_id = execution_id
    ) <> 2 OR NOT EXISTS (
        SELECT 1
        FROM tide.task_step_definitions
        WHERE execution_version_id = execution_id
          AND step_key = 'g02-environment-photo'
          AND position = 1
          AND step_type = 'UPLOAD'
          AND title = 'Take a teaching-environment photo'
          AND config =
              '{"version":"g02-photo-2026-07-22","role":"ENVIRONMENT_PHOTO","accept":["image/jpeg"],"captureOnly":true,"maxFiles":1}'::jsonb
    ) OR NOT EXISTS (
        SELECT 1
        FROM tide.task_step_definitions
        WHERE execution_version_id = execution_id
          AND step_key = 'g02-courseware-confirmation'
          AND position = 2
          AND step_type = 'CHECKLIST'
          AND title = 'Review and confirm lesson-preparation guidance'
          AND config =
              '{"version":"g02-courseware-2026-08-05-guidance-v1","role":"COURSEWARE_CONFIRMATION","items":[{"key":"courseware-prepared","label":"I have reviewed the lesson-preparation guidance and all slides, and I am ready for this lesson.","labelZh":"我已阅读备课须知并浏览全部课件，已完成本节课备课。"}]}'::jsonb
    ) OR EXISTS (
        SELECT 1
        FROM tide.task_step_definitions
        WHERE execution_version_id = execution_id
          AND step_key = 'g02-device-check'
    ) THEN
        RAISE EXCEPTION
            'migration 0037 did not produce the exact two G04 sections';
    END IF;

    IF (
        SELECT count(*)
        FROM tide.task_validation_rules
        WHERE execution_version_id = execution_id
    ) <> 2 OR NOT EXISTS (
        SELECT 1
        FROM tide.task_validation_rules
        WHERE execution_version_id = execution_id
          AND rule_key = 'all-steps-complete'
          AND position = 1
          AND rule_type = 'ALL_STEPS_COMPLETE'
          AND rule_version = '2026-08-11-g04-two-part-v1'
          AND config =
              '{"requiredStepKeys":["g02-environment-photo","g02-courseware-confirmation"]}'::jsonb
          AND teacher_failure_copy =
              '请分别完成授课环境照片检查和课件准备确认，两部分可任意顺序完成。'
    ) OR NOT EXISTS (
        SELECT 1
        FROM tide.task_validation_rules
        WHERE execution_version_id = execution_id
          AND rule_key = 'g02-environment-ai-review'
          AND position = 2
          AND rule_type = 'AI_IMAGE_REVIEW'
          AND config->>'criteriaVersion' =
              'lesson-preparation-camera-view-2026-08-v7-background-veto'
    ) THEN
        RAISE EXCEPTION
            'migration 0037 did not produce the exact G04 validation rules';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM g04_remaining_step_identity_before AS before
        LEFT JOIN tide.task_step_definitions AS after
          ON after.execution_version_id = execution_id
         AND after.step_key = before.step_key
         AND after.id = before.id
        WHERE after.id IS NULL
    ) THEN
        RAISE EXCEPTION
            'migration 0037 replaced a retained G04 step ID';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM g04_rule_identity_before AS before
        LEFT JOIN tide.task_validation_rules AS after
          ON after.execution_version_id = execution_id
         AND after.rule_key = before.rule_key
         AND after.id = before.id
        WHERE after.id IS NULL
    ) THEN
        RAISE EXCEPTION 'migration 0037 replaced an existing G04 rule ID';
    END IF;

    IF EXISTS (
        (SELECT id, row_data FROM g04_photo_ai_rule_before
         EXCEPT
         SELECT rule.id, to_jsonb(rule)
         FROM tide.task_validation_rules AS rule
         WHERE rule.execution_version_id = execution_id
           AND rule.rule_key = 'g02-environment-ai-review')
        UNION ALL
        (SELECT rule.id, to_jsonb(rule)
         FROM tide.task_validation_rules AS rule
         WHERE rule.execution_version_id = execution_id
           AND rule.rule_key = 'g02-environment-ai-review'
         EXCEPT
         SELECT id, row_data FROM g04_photo_ai_rule_before)
    ) THEN
        RAISE EXCEPTION 'migration 0037 modified the G04 photo AI rule';
    END IF;

    IF EXISTS (
        (SELECT assignment_id, row_data FROM g04_assignment_before
         EXCEPT
         SELECT assignment.assignment_id, to_jsonb(assignment)
         FROM public.task_assignments AS assignment
         WHERE assignment.template_version_id = 'G02:v1')
        UNION ALL
        (SELECT assignment.assignment_id, to_jsonb(assignment)
         FROM public.task_assignments AS assignment
         WHERE assignment.template_version_id = 'G02:v1'
         EXCEPT
         SELECT assignment_id, row_data FROM g04_assignment_before)
    ) THEN
        RAISE EXCEPTION
            'migration 0037 modified a G04 assignment or assignment status';
    END IF;

    IF EXISTS (
        (SELECT id, row_data FROM g04_progress_before
         EXCEPT
         SELECT progress.id, to_jsonb(progress)
         FROM tide.task_step_progress AS progress
         JOIN public.task_assignments AS assignment
           ON assignment.assignment_id = progress.task_assignment_id
         WHERE assignment.template_version_id = 'G02:v1')
        UNION ALL
        (SELECT progress.id, to_jsonb(progress)
         FROM tide.task_step_progress AS progress
         JOIN public.task_assignments AS assignment
           ON assignment.assignment_id = progress.task_assignment_id
         WHERE assignment.template_version_id = 'G02:v1'
         EXCEPT
         SELECT id, row_data FROM g04_progress_before)
    ) THEN
        RAISE EXCEPTION
            'migration 0037 modified historical G04 step progress';
    END IF;

    IF EXISTS (
        (SELECT id, row_data FROM g04_device_run_before
         EXCEPT
         SELECT run.id, to_jsonb(run)
         FROM tide.device_check_runs AS run
         JOIN tide.task_attempts AS attempt
           ON attempt.id = run.task_attempt_id
         JOIN public.task_assignments AS assignment
           ON assignment.assignment_id = attempt.task_assignment_id
         WHERE assignment.template_version_id = 'G02:v1')
        UNION ALL
        (SELECT run.id, to_jsonb(run)
         FROM tide.device_check_runs AS run
         JOIN tide.task_attempts AS attempt
           ON attempt.id = run.task_attempt_id
         JOIN public.task_assignments AS assignment
           ON assignment.assignment_id = attempt.task_assignment_id
         WHERE assignment.template_version_id = 'G02:v1'
         EXCEPT
         SELECT id, row_data FROM g04_device_run_before)
    ) THEN
        RAISE EXCEPTION
            'migration 0037 modified historical G04 device-check runs';
    END IF;

    IF EXISTS (
        (SELECT id, row_data FROM g04_device_item_before
         EXCEPT
         SELECT item.id, to_jsonb(item)
         FROM tide.device_check_item_results AS item
         JOIN tide.device_check_runs AS run
           ON run.id = item.device_check_run_id
         JOIN tide.task_attempts AS attempt
           ON attempt.id = run.task_attempt_id
         JOIN public.task_assignments AS assignment
           ON assignment.assignment_id = attempt.task_assignment_id
         WHERE assignment.template_version_id = 'G02:v1')
        UNION ALL
        (SELECT item.id, to_jsonb(item)
         FROM tide.device_check_item_results AS item
         JOIN tide.device_check_runs AS run
           ON run.id = item.device_check_run_id
         JOIN tide.task_attempts AS attempt
           ON attempt.id = run.task_attempt_id
         JOIN public.task_assignments AS assignment
           ON assignment.assignment_id = attempt.task_assignment_id
         WHERE assignment.template_version_id = 'G02:v1'
         EXCEPT
         SELECT id, row_data FROM g04_device_item_before)
    ) THEN
        RAISE EXCEPTION
            'migration 0037 modified historical G04 device-check item evidence';
    END IF;
END
$$;

COMMIT;
