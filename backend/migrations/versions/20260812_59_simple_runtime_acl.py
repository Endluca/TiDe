"""apply the final simple table-level runtime ACL contract

Revision ID: 20260812_59_simple_acl
Revises: 20260811_57_g02_document, 20260812_58_table_acl
Create Date: 2026-08-12

Operations grants only table-level CRUD/SELECT privileges.  Service ownership
is still enforced by database triggers, so a broad table grant does not allow
the teacher or DTS runtimes to rewrite identities, ledgers, or history.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "20260812_59_simple_acl"
down_revision: tuple[str, str] = (
    "20260811_57_g02_document",
    "20260812_58_table_acl",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


GROWTH_READ_RELATIONS: tuple[str, ...] = (
    "public.teacher_source_wide",
    "public.lesson_source_wide",
)

GROWTH_CRUD_RELATIONS: tuple[str, ...] = (
    "public.teachers",
    "public.complaint_category_rules",
    "public.personalized_trigger_matches",
    "public.lesson_score_results",
    "public.teacher_qualifications",
    "public.score_accounts",
    "public.score_component_accounts",
    "public.score_entries",
    "public.task_templates",
    "public.task_assignments",
    "public.notifications",
    "public.ops_cases",
    "public.ops_decisions",
    "public.outbox_events",
    "public.audit_events",
    "public.idempotency_records",
    "public.config_versions",
    "public.config_publication_audits",
    "public.operator_accounts",
    "public.operator_role_grants",
    "public.operator_sessions",
)

OPTIONAL_GROWTH_CRUD_RELATIONS: tuple[str, ...] = (
    "public.teacher_support_tickets",
)

TEACHER_READ_RELATIONS: tuple[str, ...] = (
    "public.alembic_version",
    "public.task_templates",
    "public.teachers",
    "public.teacher_scorecard_current",
    "public.teacher_lesson_score_current",
    "public.teacher_g01_status_current",
)

TEACHER_CRUD_RELATIONS: tuple[str, ...] = (
    "public.task_assignments",
    "public.notifications",
    "public.notification_events",
)

OPTIONAL_TEACHER_CRUD_RELATIONS: tuple[str, ...] = (
    "public.teacher_support_tickets",
)

DTS_CRUD_RELATIONS: tuple[str, ...] = (
    "public.teacher_source_wide",
    "public.lesson_source_wide",
    "public.dts_ingest_checkpoints",
    "public.dts_ingest_events",
    "public.dts_source_rows",
    "public.dts_dirty_keys",
)


def _relation_list(relations: tuple[str, ...]) -> str:
    return ",\n            ".join(relations)


def _text_array(relations: tuple[str, ...]) -> str:
    return ", ".join(repr(relation) for relation in relations)


def _install_public_guards() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.guard_teacher_notification_write()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE
            actor_name text := COALESCE(
                NULLIF(current_setting('role', true), 'none'),
                session_user
            );
        BEGIN
            IF actor_name <> 'tit_teacher_crud' THEN
                IF TG_OP = 'DELETE' THEN
                    RETURN OLD;
                END IF;
                RETURN NEW;
            END IF;

            IF TG_OP = 'INSERT' THEN
                RAISE EXCEPTION 'teacher runtime cannot create notifications'
                    USING ERRCODE = '42501';
            END IF;
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'teacher runtime cannot delete notifications'
                    USING ERRCODE = '42501';
            END IF;

            IF NEW.notification_id IS DISTINCT FROM OLD.notification_id
               OR NEW.task_id IS DISTINCT FROM OLD.task_id
               OR NEW.source_ref IS DISTINCT FROM OLD.source_ref
               OR NEW.teacher_id IS DISTINCT FROM OLD.teacher_id
               OR NEW.channel IS DISTINCT FROM OLD.channel
               OR NEW.priority IS DISTINCT FROM OLD.priority
               OR NEW.requested_at IS DISTINCT FROM OLD.requested_at
               OR NEW.stored_at IS DISTINCT FROM OLD.stored_at
               OR NEW.response_due_at IS DISTINCT FROM OLD.response_due_at
               OR NEW.failure_reason IS DISTINCT FROM OLD.failure_reason
               OR NEW.payload IS DISTINCT FROM OLD.payload THEN
                RAISE EXCEPTION
                    'teacher runtime may only update notification interaction state'
                    USING ERRCODE = '42501';
            END IF;
            IF NEW.status IS DISTINCT FROM OLD.status
               AND NEW.status NOT IN ('READ', 'CLICKED') THEN
                RAISE EXCEPTION 'invalid teacher notification interaction status'
                    USING ERRCODE = '23514';
            END IF;
            IF OLD.read_at IS NOT NULL
               AND NEW.read_at IS DISTINCT FROM OLD.read_at THEN
                RAISE EXCEPTION 'notification read time is immutable once set'
                    USING ERRCODE = '23514';
            END IF;
            IF OLD.clicked_at IS NOT NULL
               AND NEW.clicked_at IS DISTINCT FROM OLD.clicked_at THEN
                RAISE EXCEPTION 'notification click time is immutable once set'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.read_at IS NOT NULL AND NEW.read_at < NEW.requested_at THEN
                RAISE EXCEPTION 'notification read time precedes request time'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.clicked_at IS NOT NULL AND NEW.clicked_at < NEW.requested_at THEN
                RAISE EXCEPTION 'notification click time precedes request time'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END
        $function$;

        DROP TRIGGER IF EXISTS guard_teacher_notification_update
        ON public.notifications;
        DROP TRIGGER IF EXISTS guard_teacher_notification_write
        ON public.notifications;
        CREATE TRIGGER guard_teacher_notification_write
        BEFORE INSERT OR UPDATE OR DELETE ON public.notifications
        FOR EACH ROW
        EXECUTE FUNCTION public.guard_teacher_notification_write();
        REVOKE ALL ON FUNCTION public.guard_teacher_notification_write()
        FROM PUBLIC;
        DROP FUNCTION IF EXISTS public.guard_teacher_notification_update();

        CREATE OR REPLACE FUNCTION public.guard_notification_event_history()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE
            actor_name text := COALESCE(
                NULLIF(current_setting('role', true), 'none'),
                session_user
            );
        BEGIN
            IF TG_OP IN ('UPDATE', 'DELETE')
               AND actor_name IN (
                   'tit_teacher_crud',
                   'tit_growth_app',
                   'tit_dts_ingest_runtime'
               ) THEN
                RAISE EXCEPTION 'notification event history is append-only'
                    USING ERRCODE = '42501';
            END IF;
            IF TG_OP = 'INSERT' AND actor_name = 'tit_teacher_crud' THEN
                IF NEW.delivery_status NOT IN ('READ', 'CLICKED')
                   OR NEW.failure_reason IS NOT NULL
                   OR NEW.payload->>'actor' IS DISTINCT FROM 'TEACHER_APP' THEN
                    RAISE EXCEPTION 'invalid teacher notification event'
                        USING ERRCODE = '23514';
                END IF;
            END IF;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END
        $function$;

        DROP TRIGGER IF EXISTS guard_notification_event_history
        ON public.notification_events;
        CREATE TRIGGER guard_notification_event_history
        BEFORE INSERT OR UPDATE OR DELETE ON public.notification_events
        FOR EACH ROW
        EXECUTE FUNCTION public.guard_notification_event_history();
        REVOKE ALL ON FUNCTION public.guard_notification_event_history()
        FROM PUBLIC;

        CREATE OR REPLACE FUNCTION public.guard_audit_event_history()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE
            actor_name text := COALESCE(
                NULLIF(current_setting('role', true), 'none'),
                session_user
            );
        BEGIN
            IF actor_name IN (
                'tit_growth_app',
                'tit_teacher_crud',
                'tit_dts_ingest_runtime'
            ) THEN
                RAISE EXCEPTION 'audit event history is append-only'
                    USING ERRCODE = '42501';
            END IF;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END
        $function$;

        DROP TRIGGER IF EXISTS guard_audit_event_history
        ON public.audit_events;
        CREATE TRIGGER guard_audit_event_history
        BEFORE UPDATE OR DELETE ON public.audit_events
        FOR EACH ROW
        EXECUTE FUNCTION public.guard_audit_event_history();
        REVOKE ALL ON FUNCTION public.guard_audit_event_history()
        FROM PUBLIC;

        CREATE OR REPLACE FUNCTION public.guard_runtime_append_only_fact()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE
            actor_name text := COALESCE(
                NULLIF(current_setting('role', true), 'none'),
                session_user
            );
        BEGIN
            IF actor_name IN (
                'tit_growth_app',
                'tit_teacher_crud',
                'tit_dts_ingest_runtime'
            ) THEN
                RAISE EXCEPTION '% is append-only for runtime roles',
                    TG_TABLE_SCHEMA || '.' || TG_TABLE_NAME
                    USING ERRCODE = '42501';
            END IF;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END
        $function$;

        DROP TRIGGER IF EXISTS guard_runtime_append_only_fact
        ON public.score_entries;
        CREATE TRIGGER guard_runtime_append_only_fact
        BEFORE UPDATE OR DELETE ON public.score_entries
        FOR EACH ROW
        EXECUTE FUNCTION public.guard_runtime_append_only_fact();

        DROP TRIGGER IF EXISTS guard_runtime_append_only_fact
        ON public.idempotency_records;
        CREATE TRIGGER guard_runtime_append_only_fact
        BEFORE UPDATE OR DELETE ON public.idempotency_records
        FOR EACH ROW
        EXECUTE FUNCTION public.guard_runtime_append_only_fact();

        DROP TRIGGER IF EXISTS guard_runtime_append_only_fact
        ON public.config_publication_audits;
        CREATE TRIGGER guard_runtime_append_only_fact
        BEFORE UPDATE OR DELETE ON public.config_publication_audits
        FOR EACH ROW
        EXECUTE FUNCTION public.guard_runtime_append_only_fact();

        DROP TRIGGER IF EXISTS guard_runtime_append_only_fact
        ON public.ops_decisions;
        CREATE TRIGGER guard_runtime_append_only_fact
        BEFORE UPDATE OR DELETE ON public.ops_decisions
        FOR EACH ROW
        EXECUTE FUNCTION public.guard_runtime_append_only_fact();
        REVOKE ALL ON FUNCTION public.guard_runtime_append_only_fact()
        FROM PUBLIC;

        CREATE OR REPLACE FUNCTION public.guard_simple_support_ticket_write()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE
            actor_name text := COALESCE(
                NULLIF(current_setting('role', true), 'none'),
                session_user
            );
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'support tickets cannot be deleted'
                    USING ERRCODE = '42501';
            END IF;

            -- Message creation and appending continue through the existing
            -- SECURITY DEFINER methods owned by this NOLOGIN role.
            IF current_user = 'tide_support_ticket_owner' THEN
                RETURN NEW;
            END IF;

            IF TG_OP = 'INSERT'
               AND actor_name IN ('tit_teacher_crud', 'tit_growth_app') THEN
                RAISE EXCEPTION
                    'runtime roles must create support tickets through the owner function'
                    USING ERRCODE = '42501';
            END IF;
            IF TG_OP = 'INSERT' THEN
                RETURN NEW;
            END IF;

            IF actor_name = 'tit_growth_app' THEN
                RAISE EXCEPTION
                    'TiDe runtime must update support tickets through the owner function'
                    USING ERRCODE = '42501';
            END IF;
            IF actor_name = 'tit_teacher_crud'
               AND (
                   NEW.ticket_id IS DISTINCT FROM OLD.ticket_id
                   OR NEW.teacher_id IS DISTINCT FROM OLD.teacher_id
                   OR NEW.primary_category IS DISTINCT FROM OLD.primary_category
                   OR NEW.secondary_category IS DISTINCT FROM OLD.secondary_category
                   OR NEW.problem_location IS DISTINCT FROM OLD.problem_location
                   OR NEW.problem_context IS DISTINCT FROM OLD.problem_context
                   OR NEW.messages IS DISTINCT FROM OLD.messages
                   OR NEW.created_at IS DISTINCT FROM OLD.created_at
               ) THEN
                RAISE EXCEPTION
                    'teacher runtime may not replace support-ticket identity or messages'
                    USING ERRCODE = '42501';
            END IF;
            IF actor_name = 'tit_teacher_crud'
               AND OLD.status = 'CLOSED'
               AND NEW.status IS DISTINCT FROM OLD.status THEN
                RAISE EXCEPTION 'closed support tickets cannot be reopened'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END
        $function$;

        REVOKE ALL ON FUNCTION public.guard_simple_support_ticket_write()
        FROM PUBLIC;

        DO $optional_support_ticket_guard$
        BEGIN
            IF to_regclass('public.teacher_support_tickets') IS NOT NULL THEN
                DROP TRIGGER IF EXISTS guard_teacher_support_ticket_update
                ON public.teacher_support_tickets;
                DROP TRIGGER IF EXISTS guard_simple_support_ticket_write
                ON public.teacher_support_tickets;
                CREATE TRIGGER guard_simple_support_ticket_write
                BEFORE INSERT OR UPDATE OR DELETE
                ON public.teacher_support_tickets
                FOR EACH ROW
                EXECUTE FUNCTION public.guard_simple_support_ticket_write();
            END IF;
        END
        $optional_support_ticket_guard$;

        DROP FUNCTION IF EXISTS public.guard_teacher_support_ticket_update();

        CREATE OR REPLACE FUNCTION public.guard_dts_runtime_state_write()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE
            actor_name text := COALESCE(
                NULLIF(current_setting('role', true), 'none'),
                session_user
            );
        BEGIN
            IF actor_name <> 'tit_dts_ingest_runtime' THEN
                IF TG_OP = 'DELETE' THEN
                    RETURN OLD;
                END IF;
                RETURN NEW;
            END IF;

            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'DTS durable state cannot be physically deleted'
                    USING ERRCODE = '42501';
            END IF;

            IF TG_TABLE_NAME = 'dts_ingest_events' THEN
                RAISE EXCEPTION 'DTS event ledger is append-only'
                    USING ERRCODE = '42501';
            ELSIF TG_TABLE_NAME = 'dts_ingest_checkpoints' THEN
                IF NEW.source_region IS DISTINCT FROM OLD.source_region
                   OR NEW.topic IS DISTINCT FROM OLD.topic
                   OR NEW.partition_id IS DISTINCT FROM OLD.partition_id
                   OR NEW.next_offset < OLD.next_offset THEN
                    RAISE EXCEPTION 'DTS checkpoint identity or offset regressed'
                        USING ERRCODE = '23514';
                END IF;
            ELSIF TG_TABLE_NAME = 'dts_source_rows' THEN
                IF NEW.source_region IS DISTINCT FROM OLD.source_region
                   OR NEW.source_table IS DISTINCT FROM OLD.source_table
                   OR NEW.source_key IS DISTINCT FROM OLD.source_key
                   OR NEW.row_version <> OLD.row_version + 1
                   OR (NEW.source_timestamp, NEW.last_record_id, NEW.last_offset)
                      <= (OLD.source_timestamp, OLD.last_record_id, OLD.last_offset)
                THEN
                    RAISE EXCEPTION
                        'DTS source-row identity, version, or source order is invalid'
                        USING ERRCODE = '23514';
                END IF;
            ELSIF TG_TABLE_NAME = 'dts_dirty_keys' THEN
                IF NEW.key_type IS DISTINCT FROM OLD.key_type
                   OR NEW.key_part_1 IS DISTINCT FROM OLD.key_part_1
                   OR NEW.key_part_2 IS DISTINCT FROM OLD.key_part_2
                   OR NEW.row_version <> OLD.row_version + 1 THEN
                    RAISE EXCEPTION
                        'DTS dirty-key identity or version is invalid'
                        USING ERRCODE = '23514';
                END IF;
            END IF;
            RETURN NEW;
        END
        $function$;

        REVOKE ALL ON FUNCTION public.guard_dts_runtime_state_write()
        FROM PUBLIC;

        DO $dts_state_guards$
        DECLARE
            relation_name text;
        BEGIN
            FOREACH relation_name IN ARRAY ARRAY[
                'dts_ingest_checkpoints',
                'dts_ingest_events',
                'dts_source_rows',
                'dts_dirty_keys'
            ]::text[] LOOP
                IF to_regclass('public.' || relation_name) IS NULL THEN
                    RAISE EXCEPTION 'required DTS state table %.% is missing',
                        'public', relation_name;
                END IF;
                EXECUTE format(
                    'DROP TRIGGER IF EXISTS guard_dts_runtime_state_write ON public.%I',
                    relation_name
                );
                EXECUTE format(
                    'CREATE TRIGGER guard_dts_runtime_state_write '
                    'BEFORE UPDATE OR DELETE ON public.%I FOR EACH ROW '
                    'EXECUTE FUNCTION public.guard_dts_runtime_state_write()',
                    relation_name
                );
            END LOOP;
        END
        $dts_state_guards$;
        """
    )


def _install_optional_tide_guards() -> None:
    op.execute(
        """
        DO $optional_tide_guards$
        BEGIN
            IF to_regnamespace('tide') IS NULL THEN
                RETURN;
            END IF;

            IF to_regclass('tide.schema_migrations') IS NOT NULL THEN
                EXECUTE $ddl$
                    CREATE OR REPLACE FUNCTION tide.guard_runtime_schema_migration_write()
                    RETURNS trigger
                    LANGUAGE plpgsql
                    SET search_path = pg_catalog, tide
                    AS $function$
                    DECLARE
                        actor_name text := COALESCE(
                            NULLIF(current_setting('role', true), 'none'),
                            session_user
                        );
                    BEGIN
                        IF actor_name = 'tit_teacher_crud' THEN
                            RAISE EXCEPTION
                                'teacher runtime cannot modify the migration ledger'
                                USING ERRCODE = '42501';
                        END IF;
                        IF TG_OP = 'DELETE' THEN
                            RETURN OLD;
                        END IF;
                        RETURN NEW;
                    END
                    $function$;
                $ddl$;
                EXECUTE
                    'REVOKE ALL ON FUNCTION '
                    'tide.guard_runtime_schema_migration_write() FROM PUBLIC';
                EXECUTE
                    'DROP TRIGGER IF EXISTS guard_runtime_schema_migration_write '
                    'ON tide.schema_migrations';
                EXECUTE
                    'CREATE TRIGGER guard_runtime_schema_migration_write '
                    'BEFORE INSERT OR UPDATE OR DELETE ON tide.schema_migrations '
                    'FOR EACH ROW EXECUTE FUNCTION '
                    'tide.guard_runtime_schema_migration_write()';
            END IF;

            IF to_regclass('tide.crm_sso_logins') IS NOT NULL THEN
                EXECUTE $ddl$
                    CREATE OR REPLACE FUNCTION tide.guard_crm_sso_login_write()
                    RETURNS trigger
                    LANGUAGE plpgsql
                    SET search_path = pg_catalog, tide
                    AS $function$
                    DECLARE
                        actor_name text := COALESCE(
                            NULLIF(current_setting('role', true), 'none'),
                            session_user
                        );
                    BEGIN
                        IF actor_name <> 'tit_teacher_crud' THEN
                            IF TG_OP = 'DELETE' THEN
                                RETURN OLD;
                            END IF;
                            RETURN NEW;
                        END IF;
                        IF TG_OP = 'DELETE' THEN
                            RAISE EXCEPTION
                                'teacher runtime cannot delete CRM SSO login facts'
                                USING ERRCODE = '42501';
                        END IF;
                        IF NEW.id IS DISTINCT FROM OLD.id
                           OR NEW.jti_hash IS DISTINCT FROM OLD.jti_hash
                           OR NEW.issuer IS DISTINCT FROM OLD.issuer
                           OR NEW.audience IS DISTINCT FROM OLD.audience
                           OR NEW.account_id IS DISTINCT FROM OLD.account_id
                           OR NEW.exchange_code_hash IS DISTINCT FROM
                              OLD.exchange_code_hash
                           OR NEW.redirect_path IS DISTINCT FROM OLD.redirect_path
                           OR NEW.assertion_expires_at IS DISTINCT FROM
                              OLD.assertion_expires_at
                           OR NEW.exchange_expires_at IS DISTINCT FROM
                              OLD.exchange_expires_at
                           OR NEW.created_at IS DISTINCT FROM OLD.created_at
                           OR OLD.exchanged_at IS NOT NULL
                           OR NEW.exchanged_at IS NULL THEN
                            RAISE EXCEPTION
                                'CRM SSO login identity is immutable and exchange is one-time'
                                USING ERRCODE = '23514';
                        END IF;
                        RETURN NEW;
                    END
                    $function$;
                $ddl$;
                EXECUTE
                    'REVOKE ALL ON FUNCTION '
                    'tide.guard_crm_sso_login_write() FROM PUBLIC';
                EXECUTE
                    'DROP TRIGGER IF EXISTS guard_crm_sso_login_write '
                    'ON tide.crm_sso_logins';
                EXECUTE
                    'CREATE TRIGGER guard_crm_sso_login_write '
                    'BEFORE UPDATE OR DELETE ON tide.crm_sso_logins '
                    'FOR EACH ROW EXECUTE FUNCTION '
                    'tide.guard_crm_sso_login_write()';
            END IF;

            IF to_regclass('tide.system_notifications') IS NOT NULL THEN
                EXECUTE $ddl$
                    CREATE OR REPLACE FUNCTION tide.protect_system_notification_content()
                    RETURNS trigger
                    LANGUAGE plpgsql
                    SET search_path = pg_catalog, tide
                    AS $function$
                    BEGIN
                        IF TG_OP = 'DELETE' THEN
                            RAISE EXCEPTION
                                'published system notifications cannot be deleted'
                                USING ERRCODE = '42501';
                        END IF;
                        IF NEW.system_notification_id IS DISTINCT FROM
                              OLD.system_notification_id
                           OR NEW.publication_id IS DISTINCT FROM OLD.publication_id
                           OR NEW.teacher_id IS DISTINCT FROM OLD.teacher_id
                           OR NEW.type_code IS DISTINCT FROM OLD.type_code
                           OR NEW.title IS DISTINCT FROM OLD.title
                           OR NEW.body IS DISTINCT FROM OLD.body
                           OR NEW.action_type IS DISTINCT FROM OLD.action_type
                           OR NEW.action_target IS DISTINCT FROM OLD.action_target
                           OR NEW.expires_at IS DISTINCT FROM OLD.expires_at
                           OR NEW.dedupe_key IS DISTINCT FROM OLD.dedupe_key
                           OR NEW.payload IS DISTINCT FROM OLD.payload
                           OR NEW.issued_at IS DISTINCT FROM OLD.issued_at
                           OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                            RAISE EXCEPTION
                                'published system notification content is immutable'
                                USING ERRCODE = '23514';
                        END IF;
                        RETURN NEW;
                    END
                    $function$;
                $ddl$;
                EXECUTE
                    'REVOKE ALL ON FUNCTION '
                    'tide.protect_system_notification_content() FROM PUBLIC';
                EXECUTE
                    'DROP TRIGGER IF EXISTS protect_system_notification_content '
                    'ON tide.system_notifications';
                EXECUTE
                    'CREATE TRIGGER protect_system_notification_content '
                    'BEFORE UPDATE OR DELETE ON tide.system_notifications '
                    'FOR EACH ROW EXECUTE FUNCTION '
                    'tide.protect_system_notification_content()';
            END IF;
        END
        $optional_tide_guards$;
        """
    )


def _drop_runtime_column_acl() -> None:
    op.execute(
        """
        DO $drop_runtime_column_acl$
        DECLARE
            role_name text;
            target record;
        BEGIN
            FOREACH role_name IN ARRAY ARRAY[
                'tit_growth_app',
                'tit_teacher_crud',
                'tit_dts_ingest_runtime',
                'tide_support_ticket_owner'
            ]::text[] LOOP
                IF to_regrole(role_name) IS NULL THEN
                    CONTINUE;
                END IF;
                FOR target IN
                    SELECT namespace.nspname, relation.relname, attribute.attname
                    FROM pg_attribute AS attribute
                    JOIN pg_class AS relation
                      ON relation.oid = attribute.attrelid
                    JOIN pg_namespace AS namespace
                      ON namespace.oid = relation.relnamespace
                    WHERE namespace.nspname IN ('public', 'tide')
                      AND relation.relkind IN ('r', 'p', 'v', 'f')
                      AND attribute.attnum > 0
                      AND NOT attribute.attisdropped
                      AND attribute.attacl IS NOT NULL
                LOOP
                    EXECUTE format(
                        'REVOKE ALL PRIVILEGES (%I) ON TABLE %I.%I FROM %I',
                        target.attname,
                        target.nspname,
                        target.relname,
                        role_name
                    );
                END LOOP;
            END LOOP;
        END
        $drop_runtime_column_acl$;
        """
    )


def _grant_final_acl() -> None:
    growth_crud = _relation_list(GROWTH_CRUD_RELATIONS)
    growth_read = _relation_list(GROWTH_READ_RELATIONS)
    dts_crud = _relation_list(DTS_CRUD_RELATIONS)

    op.execute(
        f"""
        REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public
        FROM tit_growth_app, tit_dts_ingest_runtime;
        GRANT USAGE ON SCHEMA public
        TO tit_growth_app, tit_dts_ingest_runtime;

        GRANT SELECT ON TABLE
            {growth_read}
        TO tit_growth_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE
            {growth_crud}
        TO tit_growth_app;

        GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE
            {dts_crud}
        TO tit_dts_ingest_runtime;
        """
    )

    op.execute(
        f"""
        DO $optional_public_acl$
        BEGIN
            IF to_regclass('public.teacher_support_tickets') IS NOT NULL THEN
                EXECUTE
                    'GRANT SELECT, INSERT, UPDATE, DELETE ON '
                    'public.teacher_support_tickets TO tit_growth_app';
            END IF;

            IF to_regrole('tit_teacher_crud') IS NOT NULL THEN
                EXECUTE
                    'REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public '
                    'FROM tit_teacher_crud';
                EXECUTE
                    'GRANT USAGE ON SCHEMA public TO tit_teacher_crud';
                EXECUTE
                    'GRANT SELECT ON TABLE '
                    '{", ".join(TEACHER_READ_RELATIONS)} '
                    'TO tit_teacher_crud';
                EXECUTE
                    'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE '
                    '{", ".join(TEACHER_CRUD_RELATIONS)} '
                    'TO tit_teacher_crud';
                IF to_regclass('public.teacher_support_tickets') IS NOT NULL THEN
                    EXECUTE
                        'GRANT SELECT, INSERT, UPDATE, DELETE ON '
                        'public.teacher_support_tickets TO tit_teacher_crud';
                END IF;
            END IF;

            IF to_regrole('tide_support_ticket_owner') IS NOT NULL THEN
                EXECUTE
                    'REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public '
                    'FROM tide_support_ticket_owner';
                IF to_regclass('public.teacher_support_tickets') IS NULL THEN
                    RAISE EXCEPTION
                        'support-ticket owner exists without its shared table';
                END IF;
                EXECUTE
                    'GRANT USAGE ON SCHEMA public '
                    'TO tide_support_ticket_owner';
                EXECUTE
                    'GRANT SELECT, INSERT, UPDATE, DELETE ON '
                    'public.teacher_support_tickets '
                    'TO tide_support_ticket_owner';
            END IF;
        END
        $optional_public_acl$;
        """
    )

    op.execute(
        """
        DO $tide_schema_acl$
        BEGIN
            IF to_regnamespace('tide') IS NULL THEN
                RETURN;
            END IF;

            IF to_regrole('tit_growth_app') IS NOT NULL THEN
                EXECUTE
                    'REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA tide '
                    'FROM tit_growth_app';
            END IF;
            IF to_regrole('tit_dts_ingest_runtime') IS NOT NULL THEN
                EXECUTE
                    'REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA tide '
                    'FROM tit_dts_ingest_runtime';
            END IF;
            IF to_regrole('tide_support_ticket_owner') IS NOT NULL THEN
                EXECUTE
                    'REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA tide '
                    'FROM tide_support_ticket_owner';
            END IF;

            IF to_regrole('tit_teacher_crud') IS NOT NULL THEN
                EXECUTE
                    'REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA tide '
                    'FROM tit_teacher_crud';
                EXECUTE
                    'GRANT USAGE ON SCHEMA tide TO tit_teacher_crud';
                EXECUTE
                    'GRANT SELECT, INSERT, UPDATE, DELETE '
                    'ON ALL TABLES IN SCHEMA tide TO tit_teacher_crud';
                EXECUTE
                    'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA tide '
                    'TO tit_teacher_crud';
            END IF;

            IF to_regrole('tide_sys_admin') IS NOT NULL THEN
                EXECUTE
                    'GRANT SELECT, INSERT, UPDATE, DELETE '
                    'ON ALL TABLES IN SCHEMA public, tide TO tide_sys_admin';
                IF to_regrole('tit_teacher_crud') IS NOT NULL THEN
                    EXECUTE
                        'ALTER DEFAULT PRIVILEGES FOR ROLE tide_sys_admin '
                        'IN SCHEMA tide GRANT SELECT, INSERT, UPDATE, DELETE '
                        'ON TABLES TO tit_teacher_crud';
                    EXECUTE
                        'ALTER DEFAULT PRIVILEGES FOR ROLE tide_sys_admin '
                        'IN SCHEMA tide GRANT USAGE, SELECT '
                        'ON SEQUENCES TO tit_teacher_crud';
                END IF;
            END IF;
        END
        $tide_schema_acl$;
        """
    )


def _assert_final_contract() -> None:
    growth_read = _text_array(GROWTH_READ_RELATIONS)
    growth_crud = _text_array(
        GROWTH_CRUD_RELATIONS + OPTIONAL_GROWTH_CRUD_RELATIONS
    )
    teacher_read = _text_array(TEACHER_READ_RELATIONS)
    teacher_crud = _text_array(
        TEACHER_CRUD_RELATIONS + OPTIONAL_TEACHER_CRUD_RELATIONS
    )
    dts_crud = _text_array(DTS_CRUD_RELATIONS)

    op.execute(
        f"""
        DO $simple_acl_assertions$
        DECLARE
            relation_name text;
            privilege_name text;
        BEGIN
            FOREACH relation_name IN ARRAY ARRAY[{growth_read}]::text[] LOOP
                IF to_regclass(relation_name) IS NULL
                   OR NOT has_table_privilege(
                       'tit_growth_app', relation_name, 'SELECT'
                   )
                   OR has_table_privilege(
                       'tit_growth_app', relation_name, 'INSERT,UPDATE,DELETE'
                   ) THEN
                    RAISE EXCEPTION
                        'invalid read-only TiDe runtime ACL on %', relation_name;
                END IF;
            END LOOP;
            FOREACH relation_name IN ARRAY ARRAY[{growth_crud}]::text[] LOOP
                IF to_regclass(relation_name) IS NOT NULL THEN
                    FOREACH privilege_name IN ARRAY
                        ARRAY['SELECT','INSERT','UPDATE','DELETE']::text[]
                    LOOP
                        IF NOT has_table_privilege(
                            'tit_growth_app', relation_name, privilege_name
                        ) THEN
                            RAISE EXCEPTION
                                'incomplete TiDe runtime CRUD ACL on %: missing %',
                                relation_name,
                                privilege_name;
                        END IF;
                    END LOOP;
                END IF;
            END LOOP;
            FOREACH relation_name IN ARRAY ARRAY[{dts_crud}]::text[] LOOP
                IF to_regclass(relation_name) IS NULL THEN
                    RAISE EXCEPTION
                        'incomplete DTS runtime CRUD ACL on %', relation_name;
                END IF;
                FOREACH privilege_name IN ARRAY
                    ARRAY['SELECT','INSERT','UPDATE','DELETE']::text[]
                LOOP
                    IF NOT has_table_privilege(
                        'tit_dts_ingest_runtime', relation_name, privilege_name
                    ) THEN
                        RAISE EXCEPTION
                            'incomplete DTS runtime CRUD ACL on %: missing %',
                            relation_name,
                            privilege_name;
                    END IF;
                END LOOP;
            END LOOP;

            IF to_regrole('tit_teacher_crud') IS NOT NULL THEN
                FOREACH relation_name IN ARRAY ARRAY[{teacher_read}]::text[] LOOP
                    IF to_regclass(relation_name) IS NULL
                       OR NOT has_table_privilege(
                           'tit_teacher_crud', relation_name, 'SELECT'
                       )
                       OR has_table_privilege(
                           'tit_teacher_crud', relation_name,
                           'INSERT,UPDATE,DELETE'
                       ) THEN
                        RAISE EXCEPTION
                            'invalid teacher read-only ACL on %', relation_name;
                    END IF;
                END LOOP;
                FOREACH relation_name IN ARRAY ARRAY[{teacher_crud}]::text[] LOOP
                    IF to_regclass(relation_name) IS NOT NULL THEN
                        FOREACH privilege_name IN ARRAY
                            ARRAY['SELECT','INSERT','UPDATE','DELETE']::text[]
                        LOOP
                            IF NOT has_table_privilege(
                                'tit_teacher_crud', relation_name, privilege_name
                            ) THEN
                                RAISE EXCEPTION
                                    'incomplete teacher CRUD ACL on %: missing %',
                                    relation_name,
                                    privilege_name;
                            END IF;
                        END LOOP;
                    END IF;
                END LOOP;
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM pg_trigger
                WHERE tgrelid = 'public.task_assignments'::regclass
                  AND tgname = 'trg_task_assignment_reject_delete'
                  AND NOT tgisinternal
            ) OR NOT EXISTS (
                SELECT 1 FROM pg_trigger
                WHERE tgrelid = 'public.notifications'::regclass
                  AND tgname = 'guard_teacher_notification_write'
                  AND NOT tgisinternal
            ) OR NOT EXISTS (
                SELECT 1 FROM pg_trigger
                WHERE tgrelid = 'public.notification_events'::regclass
                  AND tgname = 'guard_notification_event_history'
                  AND NOT tgisinternal
            ) OR NOT EXISTS (
                SELECT 1 FROM pg_trigger
                WHERE tgrelid = 'public.audit_events'::regclass
                  AND tgname = 'guard_audit_event_history'
                  AND NOT tgisinternal
            ) OR (
                SELECT count(*)
                FROM pg_trigger
                WHERE tgrelid = ANY (ARRAY[
                    'public.score_entries'::regclass,
                    'public.idempotency_records'::regclass,
                    'public.config_publication_audits'::regclass,
                    'public.ops_decisions'::regclass
                ])
                  AND tgname = 'guard_runtime_append_only_fact'
                  AND NOT tgisinternal
            ) <> 4 THEN
                RAISE EXCEPTION 'shared runtime write guards are incomplete';
            END IF;

            IF to_regclass('public.teacher_support_tickets') IS NOT NULL
               AND NOT EXISTS (
                   SELECT 1 FROM pg_trigger
                   WHERE tgrelid =
                       to_regclass('public.teacher_support_tickets')
                     AND tgname = 'guard_simple_support_ticket_write'
                     AND NOT tgisinternal
               ) THEN
                RAISE EXCEPTION 'support-ticket runtime guard is missing';
            END IF;

            IF (
                SELECT count(*)
                FROM pg_trigger
                WHERE tgrelid = ANY (ARRAY[
                    'public.dts_ingest_checkpoints'::regclass,
                    'public.dts_ingest_events'::regclass,
                    'public.dts_source_rows'::regclass,
                    'public.dts_dirty_keys'::regclass
                ])
                  AND tgname = 'guard_dts_runtime_state_write'
                  AND NOT tgisinternal
            ) <> 4 THEN
                RAISE EXCEPTION 'DTS durable-state guards are incomplete';
            END IF;

            IF to_regclass('tide.schema_migrations') IS NOT NULL
               AND NOT EXISTS (
                   SELECT 1 FROM pg_trigger
                   WHERE tgrelid = to_regclass('tide.schema_migrations')
                     AND tgname = 'guard_runtime_schema_migration_write'
                     AND NOT tgisinternal
               ) THEN
                RAISE EXCEPTION 'teacher migration-ledger guard is missing';
            END IF;
            IF to_regclass('tide.crm_sso_logins') IS NOT NULL
               AND NOT EXISTS (
                   SELECT 1 FROM pg_trigger
                   WHERE tgrelid = to_regclass('tide.crm_sso_logins')
                     AND tgname = 'guard_crm_sso_login_write'
                     AND NOT tgisinternal
               ) THEN
                RAISE EXCEPTION 'CRM SSO one-time-exchange guard is missing';
            END IF;
            IF to_regclass('tide.system_notifications') IS NOT NULL
               AND NOT EXISTS (
                   SELECT 1 FROM pg_trigger
                   WHERE tgrelid = to_regclass('tide.system_notifications')
                     AND tgname = 'protect_system_notification_content'
                     AND NOT tgisinternal
               ) THEN
                RAISE EXCEPTION 'system-notification content guard is missing';
            END IF;

            IF EXISTS (
                SELECT 1
                FROM pg_attribute AS attribute
                CROSS JOIN LATERAL aclexplode(attribute.attacl) AS privilege
                JOIN pg_roles AS grantee ON grantee.oid = privilege.grantee
                JOIN pg_class AS relation ON relation.oid = attribute.attrelid
                JOIN pg_namespace AS namespace
                  ON namespace.oid = relation.relnamespace
                WHERE namespace.nspname IN ('public', 'tide')
                  AND attribute.attacl IS NOT NULL
                  AND attribute.attnum > 0
                  AND NOT attribute.attisdropped
                  AND grantee.rolname IN (
                      'tit_growth_app',
                      'tit_teacher_crud',
                      'tit_dts_ingest_runtime',
                      'tide_support_ticket_owner'
                  )
            ) THEN
                RAISE EXCEPTION 'runtime column ACL remains after simplification';
            END IF;
        END
        $simple_acl_assertions$;
        """
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    _install_public_guards()
    _install_optional_tide_guards()
    _drop_runtime_column_acl()
    _grant_final_acl()
    _assert_final_contract()


def downgrade() -> None:
    raise RuntimeError(
        "20260812_59_simple_acl is forward-only: restoring historical mixed "
        "column/table ACLs would be ambiguous and unsafe"
    )
