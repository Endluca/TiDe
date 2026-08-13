-- Local/shared-database owner must run this script. It contains no credentials.
BEGIN;

GRANT USAGE ON SCHEMA public, tide TO tit_teacher_crud;

-- public Schema 只保留最终文档列出的 6 张只读表和 4 张 CRUD 表。
-- 先清理历史表级、列级授权，避免旧版最小权限叠加后扩大实际范围。
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM tit_teacher_crud;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM tit_teacher_crud;
DO $public_column_acl$
DECLARE
    target record;
BEGIN
    FOR target IN
        SELECT
            namespace.nspname AS schema_name,
            relation.relname AS relation_name,
            attribute.attname AS column_name
        FROM pg_class AS relation
        JOIN pg_namespace AS namespace
          ON namespace.oid = relation.relnamespace
        JOIN pg_attribute AS attribute
          ON attribute.attrelid = relation.oid
        WHERE namespace.nspname = 'public'
          AND relation.relkind IN ('r', 'p', 'v', 'm', 'f')
          AND attribute.attnum > 0
          AND NOT attribute.attisdropped
    LOOP
        EXECUTE
            format(
                'REVOKE ALL PRIVILEGES (%I) ON TABLE %I.%I FROM tit_teacher_crud',
                target.column_name,
                target.schema_name,
                target.relation_name
            );
    END LOOP;
END
$public_column_acl$;

GRANT SELECT ON
    public.task_templates,
    public.teachers,
    public.teacher_scorecard_current,
    public.teacher_lesson_score_current,
    public.teacher_g01_status_current
TO tit_teacher_crud;

DO $public_alembic_acl$
BEGIN
    IF to_regclass('public.alembic_version') IS NOT NULL THEN
        EXECUTE
            'REVOKE ALL ON public.alembic_version FROM tit_teacher_crud';
        EXECUTE
            'GRANT SELECT ON public.alembic_version TO tit_teacher_crud';
    END IF;
END
$public_alembic_acl$;

GRANT SELECT, INSERT, UPDATE, DELETE ON
    public.task_assignments,
    public.notifications,
    public.notification_events,
    public.teacher_support_tickets
TO tit_teacher_crud;

-- 工单仍通过 SECURITY DEFINER 方法维持原子追加；先收敛全部运行角色，
-- 再恢复教师端三个入口与运营端唯一的回复入口，避免历史授权残留或丢失。
-- 函数由 NOLOGIN/NOINHERIT 的专用 owner 持有；迁移管理员必须显式切换角色，
-- 否则 PostgreSQL 只会警告当前用户不是 owner，授权可能静默保持旧状态。
SET LOCAL ROLE tide_support_ticket_owner;
REVOKE ALL ON FUNCTION public.create_teacher_support_ticket(
    uuid, varchar, varchar, varchar, jsonb, jsonb
) FROM PUBLIC, tit_growth_app, tit_teacher_crud, tit_dts_ingest_runtime;
REVOKE ALL ON FUNCTION public.append_teacher_support_ticket_teacher_message(
    uuid, varchar, bigint, jsonb
) FROM PUBLIC, tit_growth_app, tit_teacher_crud, tit_dts_ingest_runtime;
REVOKE ALL ON FUNCTION public.append_teacher_support_ticket_operator_message(
    uuid, bigint, jsonb
) FROM PUBLIC, tit_growth_app, tit_teacher_crud, tit_dts_ingest_runtime;
REVOKE ALL ON FUNCTION public.mark_teacher_support_ticket_images_deleted(
    uuid, timestamptz
) FROM PUBLIC, tit_growth_app, tit_teacher_crud, tit_dts_ingest_runtime;

GRANT EXECUTE ON FUNCTION public.create_teacher_support_ticket(
    uuid, varchar, varchar, varchar, jsonb, jsonb
) TO tit_teacher_crud;
GRANT EXECUTE ON FUNCTION public.append_teacher_support_ticket_teacher_message(
    uuid, varchar, bigint, jsonb
) TO tit_teacher_crud;
GRANT EXECUTE ON FUNCTION public.mark_teacher_support_ticket_images_deleted(
    uuid, timestamptz
) TO tit_teacher_crud;
GRANT EXECUTE ON FUNCTION public.append_teacher_support_ticket_operator_message(
    uuid, bigint, jsonb
) TO tit_growth_app;
RESET ROLE;

-- tide Schema 当前和以后由 tide_sys_admin 创建的普通表统一 CRUD。
REVOKE ALL ON ALL TABLES IN SCHEMA tide FROM tit_teacher_crud;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA tide TO tit_teacher_crud;
DO $crm_sso_acl$
BEGIN
    IF to_regclass('tide.crm_sso_logins') IS NOT NULL THEN
        REVOKE DELETE ON tide.crm_sso_logins FROM tit_teacher_crud;
        GRANT SELECT, INSERT, UPDATE ON tide.crm_sso_logins TO tit_teacher_crud;
    END IF;
END
$crm_sso_acl$;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA tide FROM tit_teacher_crud;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA tide TO tit_teacher_crud;
ALTER DEFAULT PRIVILEGES FOR ROLE tide_sys_admin IN SCHEMA tide
    REVOKE ALL ON TABLES FROM tit_teacher_crud;
ALTER DEFAULT PRIVILEGES FOR ROLE tide_sys_admin IN SCHEMA tide
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO tit_teacher_crud;
ALTER DEFAULT PRIVILEGES FOR ROLE tide_sys_admin IN SCHEMA tide
    REVOKE ALL ON SEQUENCES FROM tit_teacher_crud;
ALTER DEFAULT PRIVILEGES FOR ROLE tide_sys_admin IN SCHEMA tide
    GRANT USAGE, SELECT ON SEQUENCES TO tit_teacher_crud;

COMMIT;
