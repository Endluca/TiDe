-- Local/shared-database owner must run this script. It contains no credentials.
BEGIN;

GRANT USAGE ON SCHEMA public, tide TO tit_teacher_crud;

REVOKE ALL ON public.task_templates FROM tit_teacher_crud;
GRANT SELECT ON public.task_templates TO tit_teacher_crud;

REVOKE ALL ON public.task_assignments FROM tit_teacher_crud;
GRANT SELECT ON public.task_assignments TO tit_teacher_crud;
GRANT UPDATE (
    status, status_reason_code, status_changed_at, completed_at, updated_by
) ON public.task_assignments TO tit_teacher_crud;

REVOKE ALL ON public.config_versions FROM tit_teacher_crud;
REVOKE ALL ON public.score_entries FROM tit_teacher_crud;
DO $legacy_acl$
BEGIN
    IF to_regclass('public.lesson_facts') IS NOT NULL THEN
        EXECUTE 'REVOKE ALL ON public.lesson_facts FROM tit_teacher_crud';
    END IF;
    IF to_regclass('public.lesson_dimension_scores') IS NOT NULL THEN
        EXECUTE
            'REVOKE ALL ON public.lesson_dimension_scores FROM tit_teacher_crud';
    END IF;
    IF to_regclass('public.teacher_metric_snapshots') IS NOT NULL THEN
        EXECUTE
            'REVOKE ALL ON public.teacher_metric_snapshots FROM tit_teacher_crud';
    END IF;
END
$legacy_acl$;

REVOKE ALL ON public.teacher_scorecard_current FROM PUBLIC, tit_teacher_crud;
REVOKE ALL ON public.teacher_lesson_score_current FROM PUBLIC, tit_teacher_crud;
GRANT SELECT ON
    public.teacher_scorecard_current,
    public.teacher_lesson_score_current
TO tit_teacher_crud;

REVOKE ALL ON public.notifications FROM tit_teacher_crud;
GRANT SELECT ON public.notifications TO tit_teacher_crud;
GRANT UPDATE (status, read_at, clicked_at) ON public.notifications TO tit_teacher_crud;

REVOKE ALL ON public.notification_events FROM tit_teacher_crud;
GRANT SELECT, INSERT ON public.notification_events TO tit_teacher_crud;

REVOKE ALL ON public.teachers FROM tit_teacher_crud;
GRANT SELECT ON public.teachers TO tit_teacher_crud;

REVOKE ALL ON public.teacher_source_wide FROM tit_teacher_crud;
GRANT SELECT (
    tchr_id,
    is_cpl_tesol
) ON public.teacher_source_wide TO tit_teacher_crud;

REVOKE ALL ON public.teacher_support_tickets FROM tit_teacher_crud;
GRANT SELECT ON public.teacher_support_tickets TO tit_teacher_crud;
GRANT UPDATE (
    status,
    last_operator_reply_at,
    teacher_reply_deadline_at,
    last_read_operator_message_id,
    teacher_last_read_at,
    close_reason,
    closed_at,
    image_cleanup_status,
    images_deleted_at,
    row_version,
    updated_at
) ON public.teacher_support_tickets TO tit_teacher_crud;
GRANT EXECUTE ON FUNCTION public.create_teacher_support_ticket(
    uuid, varchar, varchar, varchar, jsonb, jsonb
) TO tit_teacher_crud;
GRANT EXECUTE ON FUNCTION public.append_teacher_support_ticket_teacher_message(
    uuid, varchar, bigint, jsonb
) TO tit_teacher_crud;
GRANT EXECUTE ON FUNCTION public.mark_teacher_support_ticket_images_deleted(
    uuid, timestamptz
) TO tit_teacher_crud;
REVOKE ALL ON FUNCTION public.append_teacher_support_ticket_operator_message(
    uuid, bigint, jsonb
) FROM tit_teacher_crud;

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA tide TO tit_teacher_crud;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA tide TO tit_teacher_crud;
ALTER DEFAULT PRIVILEGES IN SCHEMA tide
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO tit_teacher_crud;
ALTER DEFAULT PRIVILEGES IN SCHEMA tide
    GRANT USAGE, SELECT ON SEQUENCES TO tit_teacher_crud;

-- 引导确认是一次性终态事实；应用只能幂等写入和读取，不能改写或删除。
REVOKE ALL ON tide.account_onboarding_states FROM tit_teacher_crud;
GRANT SELECT, INSERT ON tide.account_onboarding_states TO tit_teacher_crud;

-- 迁移账本只存在于正式 migrator 管理的库。存在时只能由独立 migrator
-- 写入，运行账号只允许 readiness 读取。
DO $$
BEGIN
    IF to_regclass('tide.schema_migrations') IS NOT NULL THEN
        EXECUTE
            'REVOKE INSERT, UPDATE, DELETE ON tide.schema_migrations '
            'FROM tit_teacher_crud';
        EXECUTE
            'GRANT SELECT ON tide.schema_migrations TO tit_teacher_crud';
    END IF;
END
$$;


REVOKE UPDATE, DELETE ON tide.kuozhi_course_syncs FROM tit_teacher_crud;
GRANT SELECT, INSERT ON tide.kuozhi_course_syncs TO tit_teacher_crud;

REVOKE DELETE ON tide.system_notifications FROM tit_teacher_crud;
REVOKE UPDATE ON tide.system_notifications FROM tit_teacher_crud;
GRANT UPDATE (
    read_at, clicked_at, cancelled_at, updated_at
) ON tide.system_notifications TO tit_teacher_crud;

REVOKE DELETE ON tide.system_notification_publications FROM tit_teacher_crud;

COMMIT;
