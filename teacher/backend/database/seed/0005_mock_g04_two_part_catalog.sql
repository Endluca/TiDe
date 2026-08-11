BEGIN;

-- Local-only follow-up seed. The historical catalog remains unchanged until
-- 0031 has replayed, then this explicit Mock seed advances the shared G04 copy
-- before the teacher-owned 0037 execution migration runs.
DO $$
DECLARE
    fixed_template_count integer;
BEGIN
    SELECT count(*)
    INTO fixed_template_count
    FROM public.task_templates
    WHERE row_id IN (
        'G01:v1', 'G02:v1', 'G03:v1', 'G04:v1', 'G05:v1',
        'G06:v1', 'G07:v1', 'G08:v1', 'G09:v1', 'G10:v1'
    );

    IF fixed_template_count = 0 THEN
        RAISE NOTICE
            'local two-part G04 overlay left the empty shared catalog unchanged';
        RETURN;
    END IF;

    IF (
        SELECT count(*)
        FROM public.task_templates
        WHERE row_id = 'G02:v1'
          AND template_id = 'G04'
          AND status = 'PUBLISHED'
          AND source_mode = 'MOCK'
          AND execution_owner = 'TEACHER_APP'
          AND payload->>'category' = 'MANDATORY_GROWTH'
          AND (payload->>'score_value')::integer = 3
          AND payload->>'title' IN (
              'Lesson Preparation&Device Network Check',
              'Lesson Preparation'
          )
    ) <> 1 THEN
        RAISE EXCEPTION
            'local Mock G04 is missing or not an approved pre/post 0037 shape';
    END IF;
END
$$;

UPDATE public.task_templates
SET payload = payload || jsonb_build_object(
        'ops_name_zh', '首课准备',
        'title', 'Lesson Preparation',
        'why_template',
            'Complete the teaching-environment photo review and prepare the courseware before your first lesson.',
        'how_summary',
            'Complete two sections in any order: submit one teaching-environment photo for AI review and prepare the courseware for your first lesson. Each section keeps its own progress.',
        'completion_standard',
            'G04 is completed only after both sections pass: all four teaching-environment photo criteria—camera angle, lighting, background and dressing—pass AI review, and the courseware preparation is confirmed. The sections may be completed in any order.',
        'benefit',
            'Your teaching environment and courseware are ready for your first lesson.'
    ),
    updated_by = 'local_fixture',
    updated_at = now()
WHERE row_id = 'G02:v1'
  AND template_id = 'G04'
  AND status = 'PUBLISHED'
  AND source_mode = 'MOCK';

COMMIT;
