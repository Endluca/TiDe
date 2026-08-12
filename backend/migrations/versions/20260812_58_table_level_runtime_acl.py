"""replace runtime column grants with table-level ACL plus database guards

Revision ID: 20260812_58_table_acl
Revises: 20260812_57_dts_state
Create Date: 2026-08-12

Operations maintains table-level grants only.  Field ownership is still a
database invariant: triggers reject mutations outside each service boundary,
and the teacher G01 reader uses a two-column view instead of a base-table
column grant.
"""

from __future__ import annotations

from alembic import op


revision: str = "20260812_58_table_acl"
down_revision: str | None = "20260812_57_dts_state"
branch_labels: str | None = None
depends_on: str | None = None


LESSON_RESULT_UPDATE_COLUMNS: tuple[str, ...] = (
    "user_feedback_score",
    "reliability_score",
    "class_quality_score",
    "lesson_total_score",
    "dimensions",
    "score_rule_version",
    "projection_revision",
    "calculated_at",
)

QUALIFICATION_UPDATE_COLUMNS: tuple[str, ...] = (
    "graduation_criteria_met",
    "graduation_qualified",
    "graduation_qualified_at",
    "gold_criteria_met",
    "gold_qualified",
    "gold_qualified_at",
    "score_rule_version",
    "gate_results",
    "revision",
    "calculated_at",
)

SUPPORT_TICKET_UPDATE_COLUMNS: tuple[str, ...] = (
    "status",
    "last_operator_reply_at",
    "teacher_reply_deadline_at",
    "last_read_operator_message_id",
    "teacher_last_read_at",
    "close_reason",
    "closed_at",
    "image_cleanup_status",
    "images_deleted_at",
    "row_version",
    "updated_at",
)


def _drop_explicit_column_acl() -> None:
    """Remove historical attacl entries before applying table grants."""

    op.execute(
        """
        DO $drop_runtime_column_acl$
        DECLARE
            target record;
            column_name text;
        BEGIN
            FOR target IN
                SELECT *
                FROM (VALUES
                    ('tit_growth_app', 'public', 'task_assignments'),
                    ('tit_growth_app', 'public', 'outbox_events'),
                    ('tit_growth_app', 'public', 'operator_accounts'),
                    ('tit_growth_app', 'public', 'lesson_score_results'),
                    ('tit_growth_app', 'public', 'teacher_qualifications'),
                    ('tit_teacher_crud', 'public', 'task_assignments'),
                    ('tit_teacher_crud', 'public', 'teacher_source_wide'),
                    ('tit_teacher_crud', 'public', 'notifications'),
                    ('tit_teacher_crud', 'public', 'teacher_support_tickets'),
                    ('tit_teacher_crud', 'tide', 'system_notifications'),
                    ('tide_support_ticket_owner', 'public', 'teacher_support_tickets')
                ) AS targets(role_name, schema_name, relation_name)
            LOOP
                IF EXISTS (
                    SELECT 1 FROM pg_roles WHERE rolname = target.role_name
                ) AND to_regclass(
                    format('%I.%I', target.schema_name, target.relation_name)
                ) IS NOT NULL THEN
                    FOR column_name IN
                        SELECT attribute.attname::text
                        FROM pg_attribute AS attribute
                        WHERE attribute.attrelid = to_regclass(
                            format(
                                '%I.%I',
                                target.schema_name,
                                target.relation_name
                            )
                        )
                          AND attribute.attnum > 0
                          AND NOT attribute.attisdropped
                    LOOP
                        EXECUTE format(
                            'REVOKE ALL PRIVILEGES (%I) ON TABLE %I.%I FROM %I',
                            column_name,
                            target.schema_name,
                            target.relation_name,
                            target.role_name
                        );
                    END LOOP;
                END IF;
            END LOOP;
        END
        $drop_runtime_column_acl$;
        """
    )


def _install_shared_guards() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.enforce_task_assignment_write()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE
            template_code text;
            template_status text;
            template_integration_mode text;
            actor_name text := COALESCE(
                NULLIF(current_setting('role', true), 'none'),
                session_user
            );
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'task assignments cannot be deleted'
                    USING ERRCODE = '42501';
            END IF;

            SELECT template_id, status, integration_mode
            INTO template_code, template_status, template_integration_mode
            FROM public.task_templates
            WHERE row_id = NEW.template_version_id;

            IF NOT FOUND THEN
                RAISE EXCEPTION 'task template version % does not exist',
                    NEW.template_version_id
                    USING ERRCODE = '23503';
            END IF;
            IF template_code <> NEW.task_code OR template_status <> 'PUBLISHED' THEN
                RAISE EXCEPTION
                    'task %, template %, and published status are inconsistent',
                    NEW.task_code, NEW.template_version_id
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.task_kind = 'FIXED_GROWTH'
               AND template_integration_mode <> 'INBOUND_STATUS_ONLY' THEN
                RAISE EXCEPTION 'fixed task % must use an inbound-only template',
                    NEW.task_code
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.task_kind = 'PERSONALIZED_IMPROVEMENT'
               AND template_integration_mode <> 'OUTBOUND_MANAGED' THEN
                RAISE EXCEPTION
                    'personalized task % must use a trigger-center template',
                    NEW.task_code
                    USING ERRCODE = '23514';
            END IF;

            IF TG_OP = 'INSERT' THEN
                IF actor_name = 'tit_teacher_crud' THEN
                    RAISE EXCEPTION 'teacher app cannot create assignments'
                        USING ERRCODE = '42501';
                END IF;
                IF NEW.status <> 'ASSIGNED' THEN
                    RAISE EXCEPTION 'new task assignments must start at ASSIGNED'
                        USING ERRCODE = '23514';
                END IF;
                NEW.row_version := 1;
                NEW.updated_at := clock_timestamp();
                RETURN NEW;
            END IF;

            IF OLD.status IN ('COMPLETED', 'EXPIRED', 'WAIVED', 'CANCELLED') THEN
                RAISE EXCEPTION 'terminal task assignment % is immutable',
                    OLD.assignment_id
                    USING ERRCODE = '23514';
            END IF;

            IF NEW.assignment_id IS DISTINCT FROM OLD.assignment_id
               OR NEW.teacher_id IS DISTINCT FROM OLD.teacher_id
               OR NEW.task_code IS DISTINCT FROM OLD.task_code
               OR NEW.template_version_id IS DISTINCT FROM OLD.template_version_id
               OR NEW.task_kind IS DISTINCT FROM OLD.task_kind
               OR NEW.creator_system IS DISTINCT FROM OLD.creator_system
               OR NEW.priority IS DISTINCT FROM OLD.priority
               OR NEW.why IS DISTINCT FROM OLD.why
               OR NEW.display_title IS DISTINCT FROM OLD.display_title
               OR NEW.evidence_snapshot IS DISTINCT FROM OLD.evidence_snapshot
               OR NEW.due_at IS DISTINCT FROM OLD.due_at
               OR NEW.timezone_used IS DISTINCT FROM OLD.timezone_used
               OR NEW.timezone_source IS DISTINCT FROM OLD.timezone_source
               OR NEW.timezone_verified_at IS DISTINCT FROM OLD.timezone_verified_at
               OR NEW.source_mode IS DISTINCT FROM OLD.source_mode
               OR NEW.dedupe_key IS DISTINCT FROM OLD.dedupe_key
               OR NEW.created_by IS DISTINCT FROM OLD.created_by
               OR NEW.assigned_at IS DISTINCT FROM OLD.assigned_at
               OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                RAISE EXCEPTION
                    'immutable task assignment identity/content fields cannot change'
                    USING ERRCODE = '23514';
            END IF;

            IF NEW.row_version <> OLD.row_version THEN
                RAISE EXCEPTION
                    'stale or caller-modified row_version for assignment %',
                    OLD.assignment_id
                    USING ERRCODE = '40001';
            END IF;

            IF actor_name = 'tit_growth_app'
               AND NEW.completed_at IS DISTINCT FROM OLD.completed_at THEN
                RAISE EXCEPTION
                    'TiDe runtime cannot write teacher completion timestamps'
                    USING ERRCODE = '42501';
            END IF;
            IF actor_name = 'tit_teacher_crud' THEN
                IF NEW.status IN ('WAIVED', 'CANCELLED')
                   AND NEW.status IS DISTINCT FROM OLD.status THEN
                    RAISE EXCEPTION 'teacher app cannot waive or cancel assignments'
                        USING ERRCODE = '42501';
                END IF;
                NEW.updated_by := actor_name;
            END IF;

            IF NEW.status IS DISTINCT FROM OLD.status THEN
                IF NEW.status_changed_at IS NULL
                   OR NEW.status_changed_at IS NOT DISTINCT FROM OLD.status_changed_at
                   OR NEW.status_changed_at < OLD.status_changed_at THEN
                    RAISE EXCEPTION
                        'status change requires a monotonic status_changed_at'
                        USING ERRCODE = '23514';
                END IF;

                IF NOT (
                    (OLD.status = 'ASSIGNED' AND NEW.status IN (
                        'VIEWED', 'IN_PROGRESS', 'SUBMITTED', 'UNDER_REVIEW',
                        'COMPLETED', 'FAILED', 'EXPIRED', 'WAIVED', 'CANCELLED'
                    )) OR
                    (OLD.status = 'VIEWED' AND NEW.status IN (
                        'IN_PROGRESS', 'SUBMITTED', 'UNDER_REVIEW',
                        'COMPLETED', 'FAILED', 'EXPIRED', 'WAIVED', 'CANCELLED'
                    )) OR
                    (OLD.status = 'IN_PROGRESS' AND NEW.status IN (
                        'SUBMITTED', 'UNDER_REVIEW', 'COMPLETED', 'FAILED',
                        'EXPIRED', 'WAIVED', 'CANCELLED'
                    )) OR
                    (OLD.status = 'SUBMITTED' AND NEW.status IN (
                        'UNDER_REVIEW', 'COMPLETED', 'FAILED',
                        'EXPIRED', 'WAIVED', 'CANCELLED'
                    )) OR
                    (OLD.status = 'UNDER_REVIEW' AND NEW.status IN (
                        'COMPLETED', 'FAILED', 'EXPIRED', 'WAIVED', 'CANCELLED'
                    )) OR
                    (OLD.status = 'FAILED' AND NEW.status IN (
                        'IN_PROGRESS', 'SUBMITTED', 'UNDER_REVIEW', 'COMPLETED'
                    ))
                ) THEN
                    RAISE EXCEPTION 'invalid task status transition: % -> %',
                        OLD.status, NEW.status
                        USING ERRCODE = '23514';
                END IF;

                IF NEW.status = 'COMPLETED' THEN
                    NEW.completed_at := COALESCE(
                        NEW.completed_at,
                        NEW.status_changed_at,
                        clock_timestamp()
                    );
                END IF;
            ELSIF NEW.status_changed_at IS DISTINCT FROM OLD.status_changed_at THEN
                RAISE EXCEPTION
                    'status_changed_at cannot change without a status transition'
                    USING ERRCODE = '23514';
            END IF;

            IF NEW.status <> 'COMPLETED' AND NEW.completed_at IS NOT NULL THEN
                RAISE EXCEPTION 'completed_at requires COMPLETED status'
                    USING ERRCODE = '23514';
            END IF;
            IF OLD.completed_at IS NOT NULL
               AND NEW.completed_at IS DISTINCT FROM OLD.completed_at THEN
                RAISE EXCEPTION 'completed_at is immutable once set'
                    USING ERRCODE = '23514';
            END IF;

            NEW.row_version := OLD.row_version + 1;
            NEW.updated_at := clock_timestamp();
            RETURN NEW;
        END
        $function$;

        REVOKE ALL ON FUNCTION public.enforce_task_assignment_write()
        FROM PUBLIC;

        CREATE OR REPLACE FUNCTION public.guard_outbox_event_update()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $function$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'outbox events cannot be deleted'
                    USING ERRCODE = '42501';
            END IF;
            IF NEW.outbox_id IS DISTINCT FROM OLD.outbox_id
               OR NEW.event_id IS DISTINCT FROM OLD.event_id
               OR NEW.aggregate_type IS DISTINCT FROM OLD.aggregate_type
               OR NEW.aggregate_id IS DISTINCT FROM OLD.aggregate_id
               OR NEW.event_type IS DISTINCT FROM OLD.event_type
               OR NEW.payload IS DISTINCT FROM OLD.payload
               OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                RAISE EXCEPTION 'outbox event identity and payload are immutable'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END
        $function$;

        DROP TRIGGER IF EXISTS guard_outbox_event_update
        ON public.outbox_events;
        CREATE TRIGGER guard_outbox_event_update
        BEFORE UPDATE OR DELETE ON public.outbox_events
        FOR EACH ROW EXECUTE FUNCTION public.guard_outbox_event_update();
        REVOKE ALL ON FUNCTION public.guard_outbox_event_update() FROM PUBLIC;

        CREATE OR REPLACE FUNCTION public.guard_lesson_score_result_identity()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $function$
        BEGIN
            IF NEW.lesson_id IS DISTINCT FROM OLD.lesson_id THEN
                RAISE EXCEPTION 'lesson score result identity is immutable'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END
        $function$;

        DROP TRIGGER IF EXISTS guard_lesson_score_result_identity
        ON public.lesson_score_results;
        CREATE TRIGGER guard_lesson_score_result_identity
        BEFORE UPDATE ON public.lesson_score_results
        FOR EACH ROW
        EXECUTE FUNCTION public.guard_lesson_score_result_identity();
        REVOKE ALL ON FUNCTION public.guard_lesson_score_result_identity()
        FROM PUBLIC;

        CREATE OR REPLACE FUNCTION public.guard_operator_account_runtime_update()
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
            IF actor_name = 'tit_growth_app'
               AND (
                   NEW.operator_id IS DISTINCT FROM OLD.operator_id
                   OR NEW.username IS DISTINCT FROM OLD.username
                   OR NEW.display_name IS DISTINCT FROM OLD.display_name
                   OR NEW.is_active IS DISTINCT FROM OLD.is_active
                   OR NEW.created_at IS DISTINCT FROM OLD.created_at
               ) THEN
                RAISE EXCEPTION
                    'runtime login may only refresh password hash metadata'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END
        $function$;

        DROP TRIGGER IF EXISTS guard_operator_account_runtime_update
        ON public.operator_accounts;
        CREATE TRIGGER guard_operator_account_runtime_update
        BEFORE UPDATE ON public.operator_accounts
        FOR EACH ROW
        EXECUTE FUNCTION public.guard_operator_account_runtime_update();
        REVOKE ALL ON FUNCTION public.guard_operator_account_runtime_update()
        FROM PUBLIC;

        CREATE OR REPLACE FUNCTION public.guard_teacher_notification_update()
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
            IF actor_name = 'tit_teacher_crud'
               AND (
                   NEW.notification_id IS DISTINCT FROM OLD.notification_id
                   OR NEW.task_id IS DISTINCT FROM OLD.task_id
                   OR NEW.source_ref IS DISTINCT FROM OLD.source_ref
                   OR NEW.teacher_id IS DISTINCT FROM OLD.teacher_id
                   OR NEW.channel IS DISTINCT FROM OLD.channel
                   OR NEW.priority IS DISTINCT FROM OLD.priority
                   OR NEW.requested_at IS DISTINCT FROM OLD.requested_at
                   OR NEW.stored_at IS DISTINCT FROM OLD.stored_at
                   OR NEW.response_due_at IS DISTINCT FROM OLD.response_due_at
                   OR NEW.failure_reason IS DISTINCT FROM OLD.failure_reason
                   OR NEW.payload IS DISTINCT FROM OLD.payload
               ) THEN
                RAISE EXCEPTION
                    'teacher runtime may only update notification interaction state'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END
        $function$;

        DROP TRIGGER IF EXISTS guard_teacher_notification_update
        ON public.notifications;
        CREATE TRIGGER guard_teacher_notification_update
        BEFORE UPDATE ON public.notifications
        FOR EACH ROW
        EXECUTE FUNCTION public.guard_teacher_notification_update();
        REVOKE ALL ON FUNCTION public.guard_teacher_notification_update()
        FROM PUBLIC;

        CREATE OR REPLACE VIEW public.teacher_g01_status_current
        WITH (security_barrier = true)
        AS
        SELECT tchr_id, is_cpl_tesol
        FROM public.teacher_source_wide;

        REVOKE ALL ON TABLE public.teacher_g01_status_current FROM PUBLIC;
        COMMENT ON VIEW public.teacher_g01_status_current IS
            'Teacher backend G01 read boundary; table-level SELECT exposes TESOL only.';
        """
    )


def _install_optional_teacher_guard() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.guard_teacher_support_ticket_update()
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

            -- SECURITY DEFINER ticket methods run as this NOLOGIN owner and
            -- remain the only path allowed to append the messages array.
            IF current_user = 'tide_support_ticket_owner' THEN
                RETURN NEW;
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
            RETURN NEW;
        END
        $function$;

        REVOKE ALL ON FUNCTION public.guard_teacher_support_ticket_update()
        FROM PUBLIC;

        DO $optional_support_ticket_guard$
        BEGIN
            IF to_regclass('public.teacher_support_tickets') IS NOT NULL THEN
                DROP TRIGGER IF EXISTS guard_teacher_support_ticket_update
                ON public.teacher_support_tickets;
                CREATE TRIGGER guard_teacher_support_ticket_update
                BEFORE UPDATE OR DELETE ON public.teacher_support_tickets
                FOR EACH ROW
                EXECUTE FUNCTION public.guard_teacher_support_ticket_update();
            END IF;
        END
        $optional_support_ticket_guard$;
        """
    )


def _grant_table_acl() -> None:
    op.execute(
        """
        REVOKE ALL PRIVILEGES ON TABLE
            public.task_assignments,
            public.outbox_events,
            public.operator_accounts,
            public.lesson_score_results,
            public.teacher_qualifications
        FROM tit_growth_app;

        GRANT SELECT, INSERT, UPDATE ON TABLE
            public.task_assignments,
            public.outbox_events,
            public.lesson_score_results,
            public.teacher_qualifications
        TO tit_growth_app;
        GRANT SELECT, UPDATE ON TABLE public.operator_accounts
        TO tit_growth_app;
        """
    )
    op.execute(
        """
        DO $optional_runtime_table_acl$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_roles WHERE rolname = 'tit_teacher_crud'
            ) THEN
                REVOKE ALL PRIVILEGES ON TABLE
                    public.task_assignments,
                    public.notifications,
                    public.teacher_source_wide,
                    public.teacher_g01_status_current
                FROM tit_teacher_crud;

                GRANT SELECT, UPDATE ON TABLE
                    public.task_assignments,
                    public.notifications
                TO tit_teacher_crud;
                GRANT SELECT ON TABLE public.teacher_g01_status_current
                TO tit_teacher_crud;

                IF to_regclass('public.teacher_support_tickets') IS NOT NULL THEN
                    REVOKE ALL PRIVILEGES ON TABLE
                        public.teacher_support_tickets
                    FROM tit_teacher_crud;
                    GRANT SELECT, UPDATE ON TABLE
                        public.teacher_support_tickets
                    TO tit_teacher_crud;
                END IF;

                IF to_regclass('tide.system_notifications') IS NOT NULL THEN
                    REVOKE ALL PRIVILEGES ON TABLE tide.system_notifications
                    FROM tit_teacher_crud;
                    GRANT SELECT, INSERT, UPDATE ON TABLE
                        tide.system_notifications
                    TO tit_teacher_crud;
                END IF;
            END IF;

            IF EXISTS (
                SELECT 1 FROM pg_roles
                WHERE rolname = 'tide_support_ticket_owner'
            ) AND to_regclass('public.teacher_support_tickets') IS NOT NULL THEN
                REVOKE ALL PRIVILEGES ON TABLE public.teacher_support_tickets
                FROM tide_support_ticket_owner;
                GRANT SELECT, INSERT, UPDATE ON TABLE
                    public.teacher_support_tickets
                TO tide_support_ticket_owner;
            END IF;
        END
        $optional_runtime_table_acl$;
        """
    )


def _assert_contract() -> None:
    op.execute(
        """
        DO $table_acl_assertions$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM unnest(ARRAY[
                    'public.task_assignments',
                    'public.outbox_events',
                    'public.lesson_score_results',
                    'public.teacher_qualifications'
                ]::text[]) AS relation(name)
                WHERE NOT has_table_privilege(
                    'tit_growth_app', relation.name, 'SELECT,INSERT,UPDATE'
                )
            ) OR NOT has_table_privilege(
                'tit_growth_app',
                'public.operator_accounts',
                'SELECT,UPDATE'
            ) THEN
                RAISE EXCEPTION 'TiDe runtime table-level ACL is incomplete';
            END IF;

            IF EXISTS (
                SELECT 1
                FROM pg_attribute AS attribute
                CROSS JOIN LATERAL aclexplode(
                    attribute.attacl
                ) AS privilege
                JOIN pg_roles AS grantee ON grantee.oid = privilege.grantee
                WHERE attribute.attrelid = ANY (ARRAY[
                    'public.task_assignments'::regclass,
                    'public.outbox_events'::regclass,
                    'public.operator_accounts'::regclass,
                    'public.lesson_score_results'::regclass,
                    'public.teacher_qualifications'::regclass
                ])
                  AND attribute.attacl IS NOT NULL
                  AND attribute.attnum > 0
                  AND NOT attribute.attisdropped
                  AND grantee.rolname = 'tit_growth_app'
            ) THEN
                RAISE EXCEPTION 'TiDe runtime still has explicit column ACL';
            END IF;

            IF EXISTS (
                SELECT 1 FROM pg_roles WHERE rolname = 'tit_teacher_crud'
            ) THEN
                IF NOT has_table_privilege(
                    'tit_teacher_crud',
                    'public.task_assignments',
                    'SELECT,UPDATE'
                ) OR has_table_privilege(
                    'tit_teacher_crud',
                    'public.task_assignments',
                    'INSERT,DELETE'
                ) OR has_table_privilege(
                    'tit_teacher_crud',
                    'public.teacher_source_wide',
                    'SELECT'
                ) OR NOT has_table_privilege(
                    'tit_teacher_crud',
                    'public.teacher_g01_status_current',
                    'SELECT'
                ) THEN
                    RAISE EXCEPTION 'teacher runtime table/view ACL is invalid';
                END IF;

                IF EXISTS (
                    SELECT 1
                    FROM pg_attribute AS attribute
                    CROSS JOIN LATERAL aclexplode(
                        attribute.attacl
                    ) AS privilege
                    JOIN pg_roles AS grantee ON grantee.oid = privilege.grantee
                    WHERE attribute.attrelid = ANY (ARRAY[
                        'public.task_assignments'::regclass,
                        'public.notifications'::regclass,
                        'public.teacher_source_wide'::regclass
                    ])
                      AND attribute.attacl IS NOT NULL
                      AND attribute.attnum > 0
                      AND NOT attribute.attisdropped
                      AND grantee.rolname = 'tit_teacher_crud'
                ) THEN
                    RAISE EXCEPTION 'teacher runtime still has explicit column ACL';
                END IF;

                IF to_regclass('public.teacher_support_tickets') IS NOT NULL THEN
                    IF NOT has_table_privilege(
                        'tit_teacher_crud',
                        'public.teacher_support_tickets',
                        'SELECT,UPDATE'
                    ) OR NOT EXISTS (
                        SELECT 1 FROM pg_trigger
                        WHERE tgrelid =
                            'public.teacher_support_tickets'::regclass
                          AND tgname =
                              'guard_teacher_support_ticket_update'
                          AND NOT tgisinternal
                    ) THEN
                        RAISE EXCEPTION 'teacher support-ticket ACL/guard is invalid';
                    END IF;
                END IF;

                IF to_regclass('tide.system_notifications') IS NOT NULL THEN
                    IF NOT has_table_privilege(
                        'tit_teacher_crud',
                        'tide.system_notifications',
                        'SELECT,INSERT,UPDATE'
                    ) THEN
                        RAISE EXCEPTION 'teacher system-notification ACL is invalid';
                    END IF;
                END IF;
            END IF;

            IF to_regclass('public.teacher_support_tickets') IS NOT NULL THEN
                IF EXISTS (
                    SELECT 1
                    FROM pg_attribute AS attribute
                    CROSS JOIN LATERAL aclexplode(attribute.attacl)
                        AS privilege
                    JOIN pg_roles AS grantee
                      ON grantee.oid = privilege.grantee
                    WHERE attribute.attrelid =
                        'public.teacher_support_tickets'::regclass
                      AND attribute.attacl IS NOT NULL
                      AND attribute.attnum > 0
                      AND NOT attribute.attisdropped
                      AND grantee.rolname IN (
                          'tit_teacher_crud',
                          'tide_support_ticket_owner'
                      )
                ) THEN
                    RAISE EXCEPTION 'support-ticket explicit column ACL remains';
                END IF;
            END IF;

            IF to_regclass('tide.system_notifications') IS NOT NULL THEN
                IF EXISTS (
                    SELECT 1
                    FROM pg_attribute AS attribute
                    CROSS JOIN LATERAL aclexplode(attribute.attacl)
                        AS privilege
                    JOIN pg_roles AS grantee
                      ON grantee.oid = privilege.grantee
                    WHERE attribute.attrelid =
                        'tide.system_notifications'::regclass
                      AND attribute.attacl IS NOT NULL
                      AND attribute.attnum > 0
                      AND NOT attribute.attisdropped
                      AND grantee.rolname = 'tit_teacher_crud'
                ) THEN
                    RAISE EXCEPTION 'system-notification explicit column ACL remains';
                END IF;
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM pg_trigger
                WHERE tgrelid = 'public.task_assignments'::regclass
                  AND tgfoid =
                      'public.enforce_task_assignment_write()'::regprocedure
                  AND NOT tgisinternal
            ) OR NOT EXISTS (
                SELECT 1 FROM pg_trigger
                WHERE tgrelid = 'public.outbox_events'::regclass
                  AND tgname = 'guard_outbox_event_update'
                  AND NOT tgisinternal
            ) OR NOT EXISTS (
                SELECT 1 FROM pg_trigger
                WHERE tgrelid = 'public.notifications'::regclass
                  AND tgname = 'guard_teacher_notification_update'
                  AND NOT tgisinternal
            ) THEN
                RAISE EXCEPTION 'table-level ACL database guards are incomplete';
            END IF;
        END
        $table_acl_assertions$;
        """
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    _drop_explicit_column_acl()
    _install_shared_guards()
    _install_optional_teacher_guard()
    _grant_table_acl()
    _assert_contract()


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    lesson_columns = ", ".join(LESSON_RESULT_UPDATE_COLUMNS)
    qualification_columns = ", ".join(QUALIFICATION_UPDATE_COLUMNS)
    support_columns = ", ".join(SUPPORT_TICKET_UPDATE_COLUMNS)

    op.execute(
        f"""
        REVOKE ALL PRIVILEGES ON TABLE
            public.task_assignments,
            public.outbox_events,
            public.operator_accounts,
            public.lesson_score_results,
            public.teacher_qualifications
        FROM tit_growth_app;

        GRANT SELECT, INSERT ON TABLE public.task_assignments
        TO tit_growth_app;
        GRANT UPDATE (
            status, status_reason_code, status_changed_at, updated_by
        ) ON TABLE public.task_assignments TO tit_growth_app;

        GRANT SELECT, INSERT ON TABLE public.outbox_events TO tit_growth_app;
        GRANT UPDATE (
            status, attempt_count, last_error, available_at, published_at
        ) ON TABLE public.outbox_events TO tit_growth_app;

        GRANT SELECT ON TABLE public.operator_accounts TO tit_growth_app;
        GRANT UPDATE (password_hash, updated_at)
        ON TABLE public.operator_accounts TO tit_growth_app;

        GRANT SELECT, INSERT ON TABLE
            public.lesson_score_results,
            public.teacher_qualifications
        TO tit_growth_app;
        GRANT UPDATE ({lesson_columns})
        ON TABLE public.lesson_score_results TO tit_growth_app;
        GRANT UPDATE ({qualification_columns})
        ON TABLE public.teacher_qualifications TO tit_growth_app;

        DROP TRIGGER IF EXISTS guard_outbox_event_update
        ON public.outbox_events;
        DROP FUNCTION IF EXISTS public.guard_outbox_event_update();
        DROP TRIGGER IF EXISTS guard_lesson_score_result_identity
        ON public.lesson_score_results;
        DROP FUNCTION IF EXISTS public.guard_lesson_score_result_identity();
        DROP TRIGGER IF EXISTS guard_operator_account_runtime_update
        ON public.operator_accounts;
        DROP FUNCTION IF EXISTS public.guard_operator_account_runtime_update();
        DROP TRIGGER IF EXISTS guard_teacher_notification_update
        ON public.notifications;
        DROP FUNCTION IF EXISTS public.guard_teacher_notification_update();
        """
    )
    op.execute(
        f"""
        DO $restore_optional_column_acl$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_roles WHERE rolname = 'tit_teacher_crud'
            ) THEN
                REVOKE ALL PRIVILEGES ON TABLE
                    public.task_assignments,
                    public.notifications,
                    public.teacher_g01_status_current
                FROM tit_teacher_crud;
                GRANT SELECT ON TABLE public.task_assignments
                TO tit_teacher_crud;
                GRANT UPDATE (
                    status, status_reason_code, status_changed_at,
                    completed_at, updated_by
                ) ON TABLE public.task_assignments TO tit_teacher_crud;
                GRANT SELECT ON TABLE public.notifications TO tit_teacher_crud;
                GRANT UPDATE (status, read_at, clicked_at)
                ON TABLE public.notifications TO tit_teacher_crud;
                GRANT SELECT (tchr_id, is_cpl_tesol)
                ON TABLE public.teacher_source_wide TO tit_teacher_crud;

                IF to_regclass('public.teacher_support_tickets') IS NOT NULL THEN
                    REVOKE ALL PRIVILEGES ON TABLE
                        public.teacher_support_tickets
                    FROM tit_teacher_crud;
                    GRANT SELECT ON TABLE public.teacher_support_tickets
                    TO tit_teacher_crud;
                    GRANT UPDATE ({support_columns})
                    ON TABLE public.teacher_support_tickets
                    TO tit_teacher_crud;
                    DROP TRIGGER IF EXISTS guard_teacher_support_ticket_update
                    ON public.teacher_support_tickets;
                END IF;

                IF to_regclass('tide.system_notifications') IS NOT NULL THEN
                    REVOKE ALL PRIVILEGES ON TABLE tide.system_notifications
                    FROM tit_teacher_crud;
                    GRANT SELECT, INSERT ON TABLE tide.system_notifications
                    TO tit_teacher_crud;
                    GRANT UPDATE (
                        read_at, clicked_at, cancelled_at, updated_at
                    ) ON TABLE tide.system_notifications
                    TO tit_teacher_crud;
                END IF;
            END IF;

            IF EXISTS (
                SELECT 1 FROM pg_roles
                WHERE rolname = 'tide_support_ticket_owner'
            ) AND to_regclass('public.teacher_support_tickets') IS NOT NULL THEN
                REVOKE ALL PRIVILEGES ON TABLE public.teacher_support_tickets
                FROM tide_support_ticket_owner;
                GRANT SELECT ON TABLE public.teacher_support_tickets
                TO tide_support_ticket_owner;
                GRANT INSERT (
                    ticket_id, teacher_id, primary_category,
                    secondary_category, problem_location, problem_context,
                    messages
                ) ON TABLE public.teacher_support_tickets
                TO tide_support_ticket_owner;
                GRANT UPDATE (
                    messages, status, last_operator_reply_at,
                    teacher_reply_deadline_at, image_cleanup_status,
                    images_deleted_at, row_version, updated_at
                ) ON TABLE public.teacher_support_tickets
                TO tide_support_ticket_owner;
            END IF;
        END
        $restore_optional_column_acl$;

        DROP VIEW IF EXISTS public.teacher_g01_status_current;
        DROP FUNCTION IF EXISTS public.guard_teacher_support_ticket_update();
        """
    )
