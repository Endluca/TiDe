BEGIN;

INSERT INTO public.teachers (
    teacher_id, camp_enrollment_id, name, timezone, data_mode,
    source_snapshot_label, payload
) VALUES (
    'MOCK-TEACHER-001',
    'MOCK-CAMP-001',
    '[Mock] Teacher',
    'Asia/Shanghai',
    'MOCK',
    '[Mock] local shared contract',
    '{"mock":true}'::jsonb
)
ON CONFLICT (teacher_id) DO UPDATE SET
    timezone = EXCLUDED.timezone,
    data_mode = EXCLUDED.data_mode,
    source_snapshot_label = EXCLUDED.source_snapshot_label,
    payload = EXCLUDED.payload,
    updated_at = now();

-- Keep the operations-owned row IDs stable while upgrading an existing local
-- pre-renumber catalog. The temporary namespace frees all unique task codes
-- before the semantic permutation is applied.
ALTER TABLE public.task_assignments DISABLE TRIGGER USER;
ALTER TABLE public.task_assignments
    DROP CONSTRAINT IF EXISTS task_assignments_fixed_owner_check,
    DROP CONSTRAINT IF EXISTS task_assignments_fixed_dedupe_check,
    DROP CONSTRAINT IF EXISTS ck_task_assignment_owner_consistency,
    DROP CONSTRAINT IF EXISTS ck_task_assignment_fixed_dedupe;
DROP INDEX IF EXISTS public.task_assignments_fixed_teacher_task_key;

UPDATE public.task_assignments
SET
    task_code = 'TMP-LOCAL-CATALOG-' || template_version_id,
    dedupe_key = 'fixed:' || teacher_id || ':TMP-LOCAL-CATALOG-' || template_version_id
WHERE task_kind = 'FIXED_GROWTH'
  AND template_version_id IN (
      'G01:v1', 'G02:v1', 'G03:v1', 'G04:v1', 'G05:v1',
      'G06:v1', 'G07:v1', 'G08:v1', 'G09:v1', 'G10:v1'
  );

UPDATE public.task_templates
SET template_id = 'TMP-LOCAL-CATALOG-' || row_id
WHERE row_id IN (
    'G01:v1', 'G02:v1', 'G03:v1', 'G04:v1', 'G05:v1',
    'G06:v1', 'G07:v1', 'G08:v1', 'G09:v1', 'G10:v1'
);

INSERT INTO public.task_templates (
    row_id, template_id, template_version, status, revision,
    output_type, execution_owner, external_task_template_code,
    source_mode, payload, created_by, updated_by, integration_mode
)
SELECT
    row_id,
    task_code,
    1,
    template_status,
    2,
    'TEACHER_TASK',
    'TEACHER_APP',
    'TIT.' || task_code,
    'MOCK',
    jsonb_build_object(
        'template_id', task_code,
        'external_task_template_code', 'TIT.' || task_code,
        'title', title,
        'why_template', why_template,
        'how_summary', how_summary,
        'completion_standard', completion_standard,
        'benefit', benefit,
        'priority', priority,
        'score_value', score_value,
        'stage', stage,
        'sequence', sequence,
        'category', category,
        'content_status', content_status,
        'content_locale', 'en',
        'due_rule', NULL,
        'mock', true
    ),
    'local_fixture',
    'local_fixture',
    'INBOUND_STATUS_ONLY'
FROM (VALUES
    ('G01:v1', 'G01', 'PUBLISHED', 'Profile & Credentials Completion', 'Complete the required TESOL status and learning evidence.', 'Confirm TESOL, pass all 61 questions, complete the Essay and submit the completion proof.', 'TESOL is complete, the 61-question check reaches 80%, the Essay is complete and the completion proof is submitted.', 'Your profile and required TESOL learning evidence are now complete.', 'P1', 3, 'FOUNDATION', 1, 'MANDATORY_GROWTH', 'READY'),
    ('G02:v1', 'G04', 'PUBLISHED', 'Lesson Preparation&Device Network Check', 'Complete lesson preparation and confirm that your teaching setup is ready before class.', 'Confirm lesson preparation, check the camera, microphone and network, then take one teaching-environment photo.', 'Lesson preparation is confirmed, camera, microphone and network pass, and the teaching-environment photo passes AI review.', 'Your lesson preparation and pre-class setup are recorded as ready.', 'P1', 3, 'FOUNDATION', 4, 'MANDATORY_GROWTH', 'READY'),
    ('G03:v1', 'G02', 'PUBLISHED', 'Platform Policies', 'Learn the essential classroom and account-safety rules.', 'Read the current Overseas NT Policies document in TIDE. Your reading progress is saved automatically.', 'G02 is completed automatically after you reach the end of the current published document.', 'You can apply the core platform policies in class.', 'P1', 2, 'FOUNDATION', 2, 'MANDATORY_GROWTH', 'READY'),
    ('G04:v1', 'G03', 'PUBLISHED', 'How to handle different types of students', 'Build practical responses for different learner needs.', 'Complete the learning content configured by Jiahe.', 'Meet every requirement in the published Student Types configuration.', 'You can adapt your teaching to different learner types.', 'P1', 2, 'FOUNDATION', 3, 'MANDATORY_GROWTH', 'PENDING'),
    ('G05:v1', 'G00', 'RETIRED', 'Lesson Preparation (retired history)', 'Retained only to preserve pre-merge assignment history.', 'No longer assigned or displayed.', 'Historical completion remains immutable.', 'Historical evidence is retained.', 'P1', 0, 'FOUNDATION', 0, 'MANDATORY_GROWTH', 'RETIRED'),
    ('G06:v1', 'G05', 'PUBLISHED', 'TTP Orientation', 'Understand TTP and its key business scenarios.', 'Complete the TTP video and Quiz in Kuozhi.', 'The TTP video reaches 100% progress and the Quiz is completed in Kuozhi.', 'You understand the key TTP workflow and commitments.', 'P2', 3, 'INTEGRATION', 5, 'MANDATORY_GROWTH', 'READY'),
    ('G07:v1', 'G06', 'PUBLISHED', 'ME Culture & PARSNIP', 'Learn cross-cultural classroom guidance.', 'Complete the configured videos and quiz.', 'All configured videos and quiz requirements pass.', 'You can apply the culture guidance appropriately.', 'P2', 4, 'INTEGRATION', 6, 'MANDATORY_GROWTH', 'READY'),
    ('G08:v1', 'G07', 'PUBLISHED', 'Reliability Training', 'Strengthen dependable attendance habits.', 'Complete the configured training and quiz.', 'All configured training and quiz requirements pass.', 'You have a clear reliability routine.', 'P1', 3, 'INTEGRATION', 7, 'MANDATORY_GROWTH', 'READY'),
    ('G09:v1', 'G08', 'PUBLISHED', 'Global Communicator Training', 'Learn the core Global Communicator teaching flow.', 'Complete all six Global Communicator Sample Lessons videos in Kuozhi.', 'All six required videos reach 100% progress in Kuozhi.', 'You can now confidently prepare for a Global Communicator lesson.', 'P2', 5, 'ADVANCE', 8, 'MANDATORY_GROWTH', 'READY'),
    ('G10:v1', 'G09', 'PUBLISHED', 'SET Teaching Fundamentals', 'Learn the fundamentals of SET teaching.', 'Complete the three SET videos and their three paired quizzes in Kuozhi.', 'All three required videos and all three paired quizzes reach 100% progress in Kuozhi.', 'You understand the SET teaching foundation.', 'P2', 5, 'ADVANCE', 9, 'MANDATORY_GROWTH', 'READY')
) AS catalog(
    row_id, task_code, template_status, title, why_template, how_summary,
    completion_standard, benefit, priority, score_value, stage, sequence,
    category, content_status
)
ON CONFLICT (row_id) DO UPDATE SET
    template_id = EXCLUDED.template_id,
    status = EXCLUDED.status,
    revision = EXCLUDED.revision,
    output_type = EXCLUDED.output_type,
    execution_owner = EXCLUDED.execution_owner,
    external_task_template_code = EXCLUDED.external_task_template_code,
    source_mode = EXCLUDED.source_mode,
    payload = EXCLUDED.payload,
    updated_by = EXCLUDED.updated_by,
    integration_mode = EXCLUDED.integration_mode,
    updated_at = now();

UPDATE public.task_assignments
SET
    task_code = CASE template_version_id
        WHEN 'G01:v1' THEN 'G01'
        WHEN 'G02:v1' THEN 'G04'
        WHEN 'G03:v1' THEN 'G02'
        WHEN 'G04:v1' THEN 'G03'
        WHEN 'G05:v1' THEN 'G00'
        WHEN 'G06:v1' THEN 'G05'
        WHEN 'G07:v1' THEN 'G06'
        WHEN 'G08:v1' THEN 'G07'
        WHEN 'G09:v1' THEN 'G08'
        WHEN 'G10:v1' THEN 'G09'
    END,
    creator_system = 'TRIGGER_CENTER',
    dedupe_key = 'fixed:' || teacher_id || ':' ||
        CASE template_version_id
            WHEN 'G01:v1' THEN 'G01'
            WHEN 'G02:v1' THEN 'G04'
            WHEN 'G03:v1' THEN 'G02'
            WHEN 'G04:v1' THEN 'G03'
            WHEN 'G05:v1' THEN 'G00'
            WHEN 'G06:v1' THEN 'G05'
            WHEN 'G07:v1' THEN 'G06'
            WHEN 'G08:v1' THEN 'G07'
            WHEN 'G09:v1' THEN 'G08'
            WHEN 'G10:v1' THEN 'G09'
        END
WHERE task_kind = 'FIXED_GROWTH'
  AND template_version_id IN (
      'G01:v1', 'G02:v1', 'G03:v1', 'G04:v1', 'G05:v1',
      'G06:v1', 'G07:v1', 'G08:v1', 'G09:v1', 'G10:v1'
  );

ALTER TABLE public.task_assignments
    ADD CONSTRAINT task_assignments_fixed_owner_check CHECK (
        (
            task_code ~ '^G0[0-9]$'
            AND task_kind = 'FIXED_GROWTH'
            AND creator_system = 'TRIGGER_CENTER'
        )
        OR
        (
            task_code !~ '^G0[0-9]$'
            AND task_kind = 'PERSONALIZED_IMPROVEMENT'
            AND creator_system = 'TRIGGER_CENTER'
        )
    ),
    ADD CONSTRAINT task_assignments_fixed_dedupe_check CHECK (
        task_kind <> 'FIXED_GROWTH'
        OR dedupe_key = 'fixed:' || teacher_id || ':' || task_code
    );

CREATE UNIQUE INDEX task_assignments_fixed_teacher_task_key
    ON public.task_assignments (teacher_id, task_code)
    WHERE task_kind = 'FIXED_GROWTH';
ALTER TABLE public.task_assignments ENABLE TRIGGER USER;

INSERT INTO public.task_assignments (
    teacher_id, task_code, template_version_id, task_kind,
    creator_system, priority, why, source_mode, dedupe_key
)
SELECT
    'MOCK-TEACHER-001',
    template.template_id,
    template.row_id,
    'FIXED_GROWTH',
    'TRIGGER_CENTER',
    template.payload->>'priority',
    template.payload->>'why_template',
    'MOCK',
    'fixed:MOCK-TEACHER-001:' || template.template_id
FROM public.task_templates template
WHERE template.status = 'PUBLISHED'
  AND template.template_id ~ '^G0[1-9]$'
ON CONFLICT (dedupe_key) DO NOTHING;

INSERT INTO public.teacher_source_wide (
    tchr_id, real_name, is_cpl_tesol, is_self_introduce
) VALUES (
    'MOCK-TEACHER-001',
    '[Mock] Local Teacher',
    false,
    false
)
ON CONFLICT (tchr_id) DO UPDATE SET
    is_cpl_tesol = EXCLUDED.is_cpl_tesol,
    is_self_introduce = EXCLUDED.is_self_introduce;

INSERT INTO public.teacher_metric_snapshots (
    snapshot_id, batch_id, teacher_id, snapshot_label, source_row_number,
    data_mode, is_cpl_tesol, is_self_introduce, raw_payload
) VALUES (
    'MOCK-SNAPSHOT-001',
    'MOCK-BATCH-001',
    'MOCK-TEACHER-001',
    '[Mock] local shared contract',
    1,
    'MIXED',
    false,
    false,
    '{"mock":true}'::jsonb
)
ON CONFLICT (snapshot_id) DO UPDATE SET
    is_cpl_tesol = EXCLUDED.is_cpl_tesol,
    is_self_introduce = EXCLUDED.is_self_introduce,
    updated_at = now();

INSERT INTO public.notifications (
    notification_id, task_id, teacher_id, channel, priority, status, payload
)
SELECT
    'MOCK-NOTIFICATION-001',
    assignment.assignment_id,
    assignment.teacher_id,
    'IN_APP',
    'P1',
    'STORED',
    '{"mock":true,"title":"[Mock] Training reminder","body":"[Mock] Continue your current task."}'::jsonb
FROM public.task_assignments assignment
WHERE assignment.teacher_id = 'MOCK-TEACHER-001'
  AND assignment.task_code = 'G02'
ON CONFLICT (notification_id) DO NOTHING;

COMMIT;
