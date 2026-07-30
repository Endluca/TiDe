BEGIN;

ALTER TABLE public.task_assignments
    ADD COLUMN IF NOT EXISTS teacher_response_type varchar,
    ADD COLUMN IF NOT EXISTS teacher_response_text text,
    ADD COLUMN IF NOT EXISTS teacher_response_submitted_at timestamptz,
    ADD COLUMN IF NOT EXISTS teacher_response_submitted_by uuid;

-- 兼容曾短暂上线的字段约束，但不创建或读取教师端重复结果表。
ALTER TABLE public.task_assignments
    DROP CONSTRAINT IF EXISTS task_assignments_teacher_response_bundle_check,
    DROP CONSTRAINT IF EXISTS task_assignments_teacher_response_content_check;

UPDATE public.task_assignments
SET teacher_response_type = 'FACTUAL_RESPONSE'
WHERE teacher_response_type = 'BLACKLIST_EXPLANATION';

ALTER TABLE public.task_assignments
    ADD CONSTRAINT task_assignments_teacher_response_bundle_check
        CHECK (
            num_nonnulls(
                teacher_response_type,
                teacher_response_text,
                teacher_response_submitted_at,
                teacher_response_submitted_by
            ) IN (0, 4)
        ),
    ADD CONSTRAINT task_assignments_teacher_response_content_check
        CHECK (
            teacher_response_type IS NULL
            OR (
                task_kind = 'PERSONALIZED_IMPROVEMENT'
                AND task_code = 'P-FB-BLACKLIST'
                AND teacher_response_type = 'FACTUAL_RESPONSE'
                AND char_length(btrim(teacher_response_text)) BETWEEN 1 AND 2000
                AND teacher_response_text = btrim(teacher_response_text)
            )
        );

COMMENT ON COLUMN public.task_assignments.teacher_response_type IS
'教师最终提交内容的稳定类型；当前仅 P-FB-BLACKLIST 使用 FACTUAL_RESPONSE。';
COMMENT ON COLUMN public.task_assignments.teacher_response_text IS
'教师最终提交并进入运营复核的事实说明；草稿仍属于教师端步骤过程数据。';
COMMENT ON COLUMN public.task_assignments.teacher_response_submitted_at IS
'当前事实说明最近一次正式提交时间。';
COMMENT ON COLUMN public.task_assignments.teacher_response_submitted_by IS
'当前事实说明最近一次正式提交的教师端账号 ID。';

COMMIT;
