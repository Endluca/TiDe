BEGIN;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM public.task_assignments
        WHERE teacher_response_type IS NOT NULL
    ) THEN
        RAISE EXCEPTION
            'cannot remove shared teacher response fields while submitted responses exist';
    END IF;
END
$$;

ALTER TABLE public.task_assignments
    DROP CONSTRAINT IF EXISTS task_assignments_teacher_response_content_check,
    DROP CONSTRAINT IF EXISTS task_assignments_teacher_response_bundle_check,
    DROP COLUMN IF EXISTS teacher_response_submitted_by,
    DROP COLUMN IF EXISTS teacher_response_submitted_at,
    DROP COLUMN IF EXISTS teacher_response_text,
    DROP COLUMN IF EXISTS teacher_response_type;

COMMIT;
