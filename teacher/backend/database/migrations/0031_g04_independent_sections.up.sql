BEGIN;

SET LOCAL lock_timeout = '10s';

-- G04 keeps the stable operations identity G02:v1. This migration only
-- upgrades a catalog that already exists; an empty production schema remains
-- empty so catalog rows continue to be created only by an explicit seed/sync.
DO $$
BEGIN
    IF to_regclass('public.task_templates') IS NULL
       OR to_regclass('public.task_assignments') IS NULL
       OR to_regclass('tide.task_execution_versions') IS NULL
       OR to_regclass('tide.task_step_definitions') IS NULL
       OR to_regclass('tide.task_validation_rules') IS NULL
       OR to_regclass('tide.task_step_progress') IS NULL THEN
        RAISE EXCEPTION
            'shared task tables and Tide execution tables are required before migration 0031';
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
          AND payload->>'category' = 'MANDATORY_GROWTH'
          AND payload->>'title' = 'Lesson Preparation&Device Network Check'
          AND (payload->>'score_value')::integer = 3
    ) <> 1 THEN
        RAISE EXCEPTION
            'operations G04 stable template G02:v1 is not the approved published row for migration 0031';
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
            'migration 0031 left the empty execution catalog unchanged; run the explicit current catalog seed before rollout';
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
            'existing fixed execution catalog is incomplete or inconsistent (current mapped %/9, retired G00 rows %, G04 rows %, scoped rows %); migration 0031 will not create or replace execution identity',
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
              OR execution_contract_version NOT IN ('v1', 'task-contract-v3')
              OR config NOT IN (
                  '{"estimatedMinutes":15,"allowRetry":true,"contentStatus":"READY","contentVersion":"2026-07-30","pendingReason":null}'::jsonb,
                  '{"estimatedMinutes":15,"allowRetry":true,"contentStatus":"READY","contentVersion":"2026-08-06","pendingReason":null}'::jsonb,
                  '{"estimatedMinutes":15,"allowRetry":true,"contentStatus":"READY","contentVersion":"2026-08-05-g04-three-part","pendingReason":null,"independentModules":{"stepKeys":["g02-device-check","g02-environment-photo","g02-courseware-confirmation"],"allowOutOfOrderProgress":true,"keepAssignmentInProgressUntilPassed":true}}'::jsonb
              )
          )
    ) THEN
        RAISE EXCEPTION
            'G04 execution identity or config is not an approved pre-0031/current shape';
    END IF;
END
$$;

CREATE TEMP TABLE g04_execution_identity_before ON COMMIT DROP AS
SELECT id
FROM tide.task_execution_versions
WHERE shared_template_row_id = 'G02:v1';

CREATE TEMP TABLE g04_step_identity_before ON COMMIT DROP AS
SELECT definition.step_key, definition.id
FROM tide.task_step_definitions AS definition
JOIN tide.task_execution_versions AS execution
  ON execution.id = definition.execution_version_id
WHERE execution.shared_template_row_id = 'G02:v1';

CREATE TEMP TABLE g04_rule_identity_before ON COMMIT DROP AS
SELECT rule.rule_key, rule.id
FROM tide.task_validation_rules AS rule
JOIN tide.task_execution_versions AS execution
  ON execution.id = rule.execution_version_id
WHERE execution.shared_template_row_id = 'G02:v1';

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

-- Only the two reviewed historical step layouts and the already-current
-- layout may be changed. Unknown steps are never deleted or silently hidden.
DO $$
DECLARE
    execution_id uuid;
    step_count integer;
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
            'G04 contains an unreviewed step; migration 0031 will not delete unknown steps';
    END IF;

    SELECT count(*) INTO step_count
    FROM tide.task_step_definitions
    WHERE execution_version_id = execution_id;

    IF step_count NOT IN (2, 3) THEN
        RAISE EXCEPTION
            'G04 step count % is not an approved two-step or three-step layout',
            step_count;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM tide.task_step_definitions
        WHERE execution_version_id = execution_id
          AND step_key = 'g02-courseware-confirmation'
          AND NOT (
              step_type = 'CHECKLIST'
              AND (
                  (
                      title = 'Confirm courseware preparation'
                      AND config = '{"version":"g02-courseware-2026-07-28","role":"COURSEWARE_CONFIRMATION","items":[{"key":"courseware-prepared","label":"I have reviewed all the slides and finished preparing for this lesson.","labelZh":"我已浏览全部课件，并完成本节课备课。"}]}'::jsonb
                  )
                  OR
                  (
                      title = 'Review and confirm lesson-preparation guidance'
                      AND config IN (
                          '{"version":"g02-courseware-2026-07-28","role":"COURSEWARE_CONFIRMATION","items":[{"key":"courseware-prepared","label":"I have reviewed the lesson-preparation guidance and all slides, and I am ready for this lesson.","labelZh":"我已阅读备课须知并浏览全部课件，已完成本节课备课。"}]}'::jsonb,
                          '{"version":"g02-courseware-2026-08-05-guidance-v1","role":"COURSEWARE_CONFIRMATION","items":[{"key":"courseware-prepared","label":"I have reviewed the lesson-preparation guidance and all slides, and I am ready for this lesson.","labelZh":"我已阅读备课须知并浏览全部课件，已完成本节课备课。"}]}'::jsonb
                      )
                  )
              )
          )
    ) OR NOT EXISTS (
        SELECT 1
        FROM tide.task_step_definitions
        WHERE execution_version_id = execution_id
          AND step_key = 'g02-courseware-confirmation'
    ) THEN
        RAISE EXCEPTION
            'G04 lesson-preparation step is missing or not an approved shape';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM tide.task_step_definitions
        WHERE execution_version_id = execution_id
          AND step_key = 'g02-environment-photo'
          AND NOT (
              step_type = 'UPLOAD'
              AND title = 'Take a teaching-environment photo'
              AND config = '{"version":"g02-photo-2026-07-22","role":"ENVIRONMENT_PHOTO","accept":["image/jpeg"],"captureOnly":true,"maxFiles":1}'::jsonb
          )
    ) OR NOT EXISTS (
        SELECT 1
        FROM tide.task_step_definitions
        WHERE execution_version_id = execution_id
          AND step_key = 'g02-environment-photo'
    ) THEN
        RAISE EXCEPTION
            'G04 teaching-environment photo step is missing or not the approved shape';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM tide.task_step_definitions
        WHERE execution_version_id = execution_id
          AND step_key = 'g02-device-check'
          AND NOT (
              step_type = 'DEVICE_CHECK'
              AND title = 'Check camera, microphone and network'
              AND config IN (
                  '{"version":"g02-device-2026-07-22","role":"DEVICE_CHECK","items":["camera","microphone","network"]}'::jsonb,
                  '{"version":"g02-device-2026-08-05-browser-preflight-v1","role":"DEVICE_CHECK","items":["camera","microphone","network"]}'::jsonb
              )
          )
    ) THEN
        RAISE EXCEPTION
            'G04 device step exists but is not an approved legacy/current shape';
    END IF;

    IF step_count = 2 AND NOT (
        NOT EXISTS (
            SELECT 1 FROM tide.task_step_definitions
            WHERE execution_version_id = execution_id
              AND step_key = 'g02-device-check'
        )
        AND EXISTS (
            SELECT 1 FROM tide.task_step_definitions
            WHERE execution_version_id = execution_id
              AND step_key = 'g02-courseware-confirmation'
              AND position = 1
        )
        AND EXISTS (
            SELECT 1 FROM tide.task_step_definitions
            WHERE execution_version_id = execution_id
              AND step_key = 'g02-environment-photo'
              AND position = 2
        )
    ) THEN
        RAISE EXCEPTION 'G04 two-step layout is not the reviewed courseware/photo shape';
    END IF;

    IF step_count = 3 AND NOT (
        (
            EXISTS (
                SELECT 1 FROM tide.task_step_definitions
                WHERE execution_version_id = execution_id
                  AND step_key = 'g02-courseware-confirmation'
                  AND position = 1
            )
            AND EXISTS (
                SELECT 1 FROM tide.task_step_definitions
                WHERE execution_version_id = execution_id
                  AND step_key = 'g02-device-check'
                  AND position = 2
                  AND config->>'version' = 'g02-device-2026-07-22'
            )
            AND EXISTS (
                SELECT 1 FROM tide.task_step_definitions
                WHERE execution_version_id = execution_id
                  AND step_key = 'g02-environment-photo'
                  AND position = 3
            )
        )
        OR
        (
            EXISTS (
                SELECT 1 FROM tide.task_step_definitions
                WHERE execution_version_id = execution_id
                  AND step_key = 'g02-device-check'
                  AND position = 1
                  AND config->>'version' = 'g02-device-2026-08-05-browser-preflight-v1'
            )
            AND EXISTS (
                SELECT 1 FROM tide.task_step_definitions
                WHERE execution_version_id = execution_id
                  AND step_key = 'g02-courseware-confirmation'
                  AND position = 2
            )
            AND EXISTS (
                SELECT 1 FROM tide.task_step_definitions
                WHERE execution_version_id = execution_id
                  AND step_key = 'g02-environment-photo'
                  AND position = 3
            )
        )
    ) THEN
        RAISE EXCEPTION 'G04 three-step layout is not a reviewed legacy/current shape';
    END IF;
END
$$;

-- Rules are checked independently from steps. Both reviewed historical rule
-- sets are accepted; an unknown rule fails the transaction before any write.
DO $$
DECLARE
    execution_id uuid;
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
        FROM tide.task_validation_rules
        WHERE execution_version_id = execution_id
          AND rule_key NOT IN (
              'all-steps-complete',
              'g02-environment-ai-review'
          )
    ) THEN
        RAISE EXCEPTION
            'G04 contains an unreviewed validation rule; migration 0031 will not delete it';
    END IF;

    SELECT count(*) INTO rule_count
    FROM tide.task_validation_rules
    WHERE execution_version_id = execution_id;

    IF rule_count <> 2 THEN
        RAISE EXCEPTION
            'G04 must contain the reviewed completion rule and photo AI rule before migration 0031 (found %) ',
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
                  rule_version = '2026-07-22'
                  AND config = '{}'::jsonb
                  AND teacher_failure_copy IN (
                      '请完成备课确认和授课环境照片检查。',
                      '请先完成备课确认、设备网络检查和授课环境照片检查。'
                  )
              )
              OR
              (
                  rule_version IN (
                      '2026-07-22',
                      '2026-08-05-g04-three-part-v1'
                  )
                  AND config = '{"requiredStepKeys":["g02-device-check","g02-environment-photo","g02-courseware-confirmation"]}'::jsonb
                  AND teacher_failure_copy = '请分别完成备课须知确认、设备网络检测和授课环境照片四项检查，三部分可任意顺序完成。'
              )
          )
    ) THEN
        RAISE EXCEPTION 'G04 ALL_STEPS_COMPLETE rule is not a reviewed shape';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM tide.task_validation_rules
        WHERE execution_version_id = execution_id
          AND rule_key = 'g02-environment-ai-review'
          AND rule_type = 'AI_IMAGE_REVIEW'
          AND rule_version = '2026-07-27-strict'
          AND position = 2
          AND teacher_failure_copy = '已保留你完成的内容，请根据提示更新这份材料。'
          AND config->>'stepKey' = 'g02-environment-photo'
          AND (
              (
                  config->>'criteriaVersion' = 'g02-environment-2026-07-v2-strict'
                  AND config->'criteriaKeys' = '["lighting","framing","posture","headset","appearance","background","clarity"]'::jsonb
              )
              OR
              (
                  config->>'criteriaVersion' = 'lesson-preparation-camera-view-2026-08-v7-background-veto'
                  AND config->'criteriaKeys' = '["camera_angle","lighting","background","dressing"]'::jsonb
              )
          )
          AND config->'allowedMimeTypes' = '["image/jpeg","image/png","image/webp"]'::jsonb
          AND NULLIF(config->>'systemPrompt', '') IS NOT NULL
          AND NULLIF(config->>'userText', '') IS NOT NULL
    ) THEN
        RAISE EXCEPTION 'G04 photo AI rule is not a reviewed legacy/current shape';
    END IF;
END
$$;

UPDATE tide.task_execution_versions
SET execution_contract_version = 'task-contract-v3',
    config = '{"estimatedMinutes":15,"allowRetry":true,"contentStatus":"READY","contentVersion":"2026-08-05-g04-three-part","pendingReason":null,"independentModules":{"stepKeys":["g02-device-check","g02-environment-photo","g02-courseware-confirmation"],"allowOutOfOrderProgress":true,"keepAssignmentInProgressUntilPassed":true}}'::jsonb,
    updated_at = now()
WHERE shared_template_row_id = 'G02:v1';

UPDATE tide.task_step_definitions AS definition
SET position = position + 1000000
FROM tide.task_execution_versions AS execution
WHERE execution.id = definition.execution_version_id
  AND execution.shared_template_row_id = 'G02:v1';

INSERT INTO tide.task_step_definitions (
    id, execution_version_id, step_key, position, step_type, title, config
)
SELECT
    '16cfbdd4-8486-4f87-8bae-4f4d8e365d18'::uuid,
    execution.id,
    'g02-device-check',
    1,
    'DEVICE_CHECK',
    'Check camera, microphone and network',
    '{"version":"g02-device-2026-08-05-browser-preflight-v1","role":"DEVICE_CHECK","items":["camera","microphone","network"]}'::jsonb
FROM tide.task_execution_versions AS execution
WHERE execution.shared_template_row_id = 'G02:v1'
ON CONFLICT (execution_version_id, step_key) DO UPDATE
SET position = EXCLUDED.position,
    step_type = EXCLUDED.step_type,
    title = EXCLUDED.title,
    config = EXCLUDED.config;

INSERT INTO tide.task_step_definitions (
    id, execution_version_id, step_key, position, step_type, title, config
)
SELECT
    'ddc9a338-4b94-446e-8560-0fb057340307'::uuid,
    execution.id,
    'g02-courseware-confirmation',
    2,
    'CHECKLIST',
    'Review and confirm lesson-preparation guidance',
    '{"version":"g02-courseware-2026-08-05-guidance-v1","role":"COURSEWARE_CONFIRMATION","items":[{"key":"courseware-prepared","label":"I have reviewed the lesson-preparation guidance and all slides, and I am ready for this lesson.","labelZh":"我已阅读备课须知并浏览全部课件，已完成本节课备课。"}]}'::jsonb
FROM tide.task_execution_versions AS execution
WHERE execution.shared_template_row_id = 'G02:v1'
ON CONFLICT (execution_version_id, step_key) DO UPDATE
SET position = EXCLUDED.position,
    step_type = EXCLUDED.step_type,
    title = EXCLUDED.title,
    config = EXCLUDED.config;

INSERT INTO tide.task_step_definitions (
    id, execution_version_id, step_key, position, step_type, title, config
)
SELECT
    'c6b3a705-e6c4-4734-8001-6ed9ce25a5bc'::uuid,
    execution.id,
    'g02-environment-photo',
    3,
    'UPLOAD',
    'Take a teaching-environment photo',
    '{"version":"g02-photo-2026-07-22","role":"ENVIRONMENT_PHOTO","accept":["image/jpeg"],"captureOnly":true,"maxFiles":1}'::jsonb
FROM tide.task_execution_versions AS execution
WHERE execution.shared_template_row_id = 'G02:v1'
ON CONFLICT (execution_version_id, step_key) DO UPDATE
SET position = EXCLUDED.position,
    step_type = EXCLUDED.step_type,
    title = EXCLUDED.title,
    config = EXCLUDED.config;

UPDATE tide.task_validation_rules AS rule
SET position = position + 1000000
FROM tide.task_execution_versions AS execution
WHERE execution.id = rule.execution_version_id
  AND execution.shared_template_row_id = 'G02:v1';

INSERT INTO tide.task_validation_rules (
    id, execution_version_id, rule_key, rule_type, rule_version,
    position, config, teacher_failure_copy
)
SELECT
    '3b43f903-de81-4a46-8278-0e29ad409e82'::uuid,
    execution.id,
    'all-steps-complete',
    'ALL_STEPS_COMPLETE',
    '2026-08-05-g04-three-part-v1',
    1,
    '{"requiredStepKeys":["g02-device-check","g02-environment-photo","g02-courseware-confirmation"]}'::jsonb,
    '请分别完成备课须知确认、设备网络检测和授课环境照片四项检查，三部分可任意顺序完成。'
FROM tide.task_execution_versions AS execution
WHERE execution.shared_template_row_id = 'G02:v1'
ON CONFLICT (execution_version_id, rule_key) DO UPDATE
SET rule_type = EXCLUDED.rule_type,
    rule_version = EXCLUDED.rule_version,
    position = EXCLUDED.position,
    config = EXCLUDED.config,
    teacher_failure_copy = EXCLUDED.teacher_failure_copy;

-- Keep the existing photo-check identity while aligning the reviewed legacy
-- criteria to the current four-criterion rule.
INSERT INTO tide.task_validation_rules (
    id, execution_version_id, rule_key, rule_type, rule_version,
    position, config, teacher_failure_copy
)
SELECT
    '418baa9c-fdae-4d80-82de-ad7e15b613de'::uuid,
    execution.id,
    'g02-environment-ai-review',
    'AI_IMAGE_REVIEW',
    '2026-07-27-strict',
    2,
    jsonb_build_object(
        'stepKey', 'g02-environment-photo',
        'criteriaVersion', 'lesson-preparation-camera-view-2026-08-v7-background-veto',
        'criteriaKeys', jsonb_build_array('camera_angle', 'lighting', 'background', 'dressing'),
        'allowedMimeTypes', jsonb_build_array('image/jpeg', 'image/png', 'image/webp'),
        'systemPrompt', 'You strictly review teacher-submitted evidence. Return JSON only with this exact shape: {"decision":"PASS|RETRY|ERROR","teacherReason":"teacher-safe concise message","confidenceSummary":{},"criteria":[{"criterionKey":"one configured key","result":"PASS|FAIL|UNKNOWN","teacherMessage":"teacher-safe message or null"}]}. Include every configured criterion exactly once. Never infer a pass from the mere presence of a person or object. Use UNKNOWN whenever the visual evidence is unclear. PASS only when every configured criterion is visibly and unambiguously PASS; any FAIL or UNKNOWN requires RETRY. Use ERROR only when the file cannot be assessed. Do not expose internal risk labels or private model reasoning.',
        'userText', 'Review this real teaching-environment photo strictly against camera angle, lighting, background and dressing only.'
    ),
    '已保留你完成的内容，请根据提示更新这份材料。'
FROM tide.task_execution_versions AS execution
WHERE execution.shared_template_row_id = 'G02:v1'
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
    WHERE shared_template_row_id = 'G02:v1';

    IF execution_id IS NULL THEN
        IF EXISTS (SELECT 1 FROM g04_execution_identity_before) THEN
            RAISE EXCEPTION 'migration 0031 removed the existing G04 execution';
        END IF;
        RETURN;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM g04_execution_identity_before AS before
        WHERE before.id = execution_id
    ) THEN
        RAISE EXCEPTION 'migration 0031 replaced the existing G04 execution ID';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM tide.task_execution_versions
        WHERE id = execution_id
          AND shared_template_row_id = 'G02:v1'
          AND task_code = 'G04'
          AND execution_contract_version = 'task-contract-v3'
          AND status = 'ACTIVE'
          AND config = '{"estimatedMinutes":15,"allowRetry":true,"contentStatus":"READY","contentVersion":"2026-08-05-g04-three-part","pendingReason":null,"independentModules":{"stepKeys":["g02-device-check","g02-environment-photo","g02-courseware-confirmation"],"allowOutOfOrderProgress":true,"keepAssignmentInProgressUntilPassed":true}}'::jsonb
    ) THEN
        RAISE EXCEPTION 'migration 0031 did not publish the exact G04 execution config';
    END IF;

    IF (
        SELECT count(*)
        FROM tide.task_step_definitions
        WHERE execution_version_id = execution_id
    ) <> 3 OR NOT EXISTS (
        SELECT 1 FROM tide.task_step_definitions
        WHERE execution_version_id = execution_id
          AND step_key = 'g02-device-check'
          AND position = 1
          AND step_type = 'DEVICE_CHECK'
          AND config = '{"version":"g02-device-2026-08-05-browser-preflight-v1","role":"DEVICE_CHECK","items":["camera","microphone","network"]}'::jsonb
    ) OR NOT EXISTS (
        SELECT 1 FROM tide.task_step_definitions
        WHERE execution_version_id = execution_id
          AND step_key = 'g02-courseware-confirmation'
          AND position = 2
          AND step_type = 'CHECKLIST'
          AND config = '{"version":"g02-courseware-2026-08-05-guidance-v1","role":"COURSEWARE_CONFIRMATION","items":[{"key":"courseware-prepared","label":"I have reviewed the lesson-preparation guidance and all slides, and I am ready for this lesson.","labelZh":"我已阅读备课须知并浏览全部课件，已完成本节课备课。"}]}'::jsonb
    ) OR NOT EXISTS (
        SELECT 1 FROM tide.task_step_definitions
        WHERE execution_version_id = execution_id
          AND step_key = 'g02-environment-photo'
          AND position = 3
          AND step_type = 'UPLOAD'
    ) THEN
        RAISE EXCEPTION 'migration 0031 did not produce the exact three G04 sections';
    END IF;

    IF (
        SELECT count(*)
        FROM tide.task_validation_rules
        WHERE execution_version_id = execution_id
    ) <> 2 OR NOT EXISTS (
        SELECT 1 FROM tide.task_validation_rules
        WHERE execution_version_id = execution_id
          AND rule_key = 'all-steps-complete'
          AND position = 1
          AND rule_type = 'ALL_STEPS_COMPLETE'
          AND rule_version = '2026-08-05-g04-three-part-v1'
          AND config = '{"requiredStepKeys":["g02-device-check","g02-environment-photo","g02-courseware-confirmation"]}'::jsonb
    ) OR NOT EXISTS (
        SELECT 1 FROM tide.task_validation_rules
        WHERE execution_version_id = execution_id
          AND rule_key = 'g02-environment-ai-review'
          AND position = 2
          AND rule_type = 'AI_IMAGE_REVIEW'
          AND config->>'criteriaVersion' = 'lesson-preparation-camera-view-2026-08-v7-background-veto'
    ) THEN
        RAISE EXCEPTION 'migration 0031 did not produce the exact G04 validation rules';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM g04_step_identity_before AS before
        LEFT JOIN tide.task_step_definitions AS after
          ON after.execution_version_id = execution_id
         AND after.step_key = before.step_key
         AND after.id = before.id
        WHERE after.id IS NULL
    ) THEN
        RAISE EXCEPTION 'migration 0031 replaced an existing G04 step ID';
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
        RAISE EXCEPTION 'migration 0031 replaced an existing G04 rule ID';
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
        RAISE EXCEPTION 'migration 0031 modified a G04 assignment row';
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
        RAISE EXCEPTION 'migration 0031 modified a G04 step-progress row';
    END IF;
END
$$;

COMMIT;
