"""collapse database access to the final production role contract

Revision ID: 20260812_56_lean_roles
Revises: 20260811_55_source_wide_v12
Create Date: 2026-08-12

The application and SourceWide worker are processes inside the same TiDe
backend trust boundary, so they share ``tit_growth_app``.  DTS remains a
separate writer because it owns the raw source-wide facts.  This revision also
transitions databases that already applied the former group-role design.
"""

from __future__ import annotations

from alembic import op


revision: str = "20260812_56_lean_roles"
down_revision: str | None = "20260811_55_source_wide_v12"
branch_labels: str | None = None
depends_on: str | None = None


OUTBOX_STATUS_COLUMNS: tuple[str, ...] = (
    "status",
    "attempt_count",
    "last_error",
    "available_at",
    "published_at",
)

TASK_ASSIGNMENT_SUPPRESSION_COLUMNS: tuple[str, ...] = (
    "status",
    "status_reason_code",
    "status_changed_at",
    "updated_by",
)


def _guard_runtime_roles() -> None:
    op.execute(
        """
        DO $lean_runtime_role_guard$
        DECLARE
            role_name text;
            runtime_role record;
        BEGIN
            FOREACH role_name IN ARRAY ARRAY[
                'tit_growth_app',
                'tit_dts_ingest_runtime'
            ]::text[] LOOP
                SELECT rolcanlogin, rolinherit, rolsuper, rolcreatedb, rolcreaterole,
                       rolreplication, rolbypassrls
                INTO runtime_role
                FROM pg_roles
                WHERE rolname = role_name;

                IF NOT FOUND THEN
                    RAISE EXCEPTION 'required LOGIN role % does not exist',
                        role_name;
                END IF;
                IF NOT runtime_role.rolcanlogin
                   OR runtime_role.rolinherit
                   OR runtime_role.rolsuper
                   OR runtime_role.rolcreatedb
                   OR runtime_role.rolcreaterole
                   OR runtime_role.rolreplication
                   OR runtime_role.rolbypassrls THEN
                    RAISE EXCEPTION '% must be a restricted LOGIN role',
                        role_name;
                END IF;
                IF EXISTS (
                    SELECT 1
                    FROM pg_auth_members AS memberships
                    JOIN pg_roles AS member_role
                      ON member_role.oid = memberships.member
                    WHERE member_role.rolname = role_name
                ) THEN
                    RAISE EXCEPTION '% must not inherit another role',
                        role_name;
                END IF;
                IF EXISTS (
                    SELECT 1
                    FROM pg_namespace AS namespaces
                    WHERE namespaces.nspname IN ('public', 'tide')
                      AND namespaces.nspowner = (
                          SELECT oid FROM pg_roles WHERE rolname = role_name
                      )
                ) OR EXISTS (
                    SELECT 1
                    FROM pg_class AS relations
                    JOIN pg_namespace AS namespaces
                      ON namespaces.oid = relations.relnamespace
                    WHERE namespaces.nspname IN ('public', 'tide')
                      AND relations.relowner = (
                          SELECT oid FROM pg_roles WHERE rolname = role_name
                      )
                ) THEN
                    RAISE EXCEPTION '% must not own application objects',
                        role_name;
                END IF;
            END LOOP;
        END
        $lean_runtime_role_guard$;
        """
    )


def _retire_legacy_source_acl() -> None:
    op.execute(
        """
        DO $retire_legacy_source_acl$
        DECLARE
            role_name text;
            relation_name text;
        BEGIN
            FOREACH role_name IN ARRAY ARRAY[
                'tit_source_monitor',
                'tit_source_worker',
                'tit_source_worker_runtime'
            ]::text[] LOOP
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
                    FOREACH relation_name IN ARRAY ARRAY[
                        'teacher_source_wide',
                        'lesson_source_wide',
                        'complaint_category_rules',
                        'task_templates',
                        'config_versions',
                        'score_entries',
                        'task_assignments',
                        'teachers',
                        'lesson_score_results',
                        'teacher_qualifications',
                        'score_accounts',
                        'personalized_trigger_matches',
                        'notifications',
                        'ops_cases',
                        'score_component_accounts',
                        'outbox_events'
                    ]::text[] LOOP
                        EXECUTE format(
                            'REVOKE ALL PRIVILEGES ON TABLE public.%I FROM %I',
                            relation_name,
                            role_name
                        );
                    END LOOP;
                    EXECUTE format(
                        'REVOKE ALL PRIVILEGES ON SCHEMA public FROM %I',
                        role_name
                    );
                END IF;
            END LOOP;
        END
        $retire_legacy_source_acl$;
        """
    )


def _grant_dts_acl() -> None:
    op.execute(
        """
        REVOKE CREATE ON SCHEMA public FROM tit_dts_ingest_runtime;
        GRANT USAGE ON SCHEMA public TO tit_dts_ingest_runtime;

        REVOKE ALL PRIVILEGES ON TABLE
            public.teacher_source_wide,
            public.lesson_source_wide
        FROM tit_dts_ingest_runtime;
        GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE
            public.teacher_source_wide,
            public.lesson_source_wide
        TO tit_dts_ingest_runtime;
        """
    )
    op.execute(
        """
        DO $lean_dts_acl_assertions$
        BEGIN
            IF has_schema_privilege(
                'tit_dts_ingest_runtime', 'public', 'CREATE'
            ) OR NOT has_schema_privilege(
                'tit_dts_ingest_runtime', 'public', 'USAGE'
            ) THEN
                RAISE EXCEPTION 'DTS public-schema privileges are invalid';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM unnest(ARRAY[
                    'public.teacher_source_wide',
                    'public.lesson_source_wide'
                ]::text[]) AS relation(name),
                unnest(ARRAY[
                    'SELECT', 'INSERT', 'UPDATE', 'DELETE'
                ]::text[]) AS privilege(name)
                WHERE NOT has_table_privilege(
                    'tit_dts_ingest_runtime', relation.name, privilege.name
                )
            ) THEN
                RAISE EXCEPTION 'DTS is missing source-wide CRUD privileges';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM pg_class AS relations
                JOIN pg_namespace AS namespaces
                  ON namespaces.oid = relations.relnamespace
                WHERE namespaces.nspname IN ('public', 'tide')
                  AND relations.relkind IN ('r', 'p', 'v', 'm', 'f')
                  AND relations.oid NOT IN (
                      'public.teacher_source_wide'::regclass,
                      'public.lesson_source_wide'::regclass
                  )
                  AND EXISTS (
                      SELECT 1
                      FROM unnest(ARRAY[
                          'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE',
                          'REFERENCES', 'TRIGGER'
                      ]::text[]) AS privilege(name)
                      WHERE has_table_privilege(
                          'tit_dts_ingest_runtime',
                          relations.oid,
                          privilege.name
                      )
                  )
            ) THEN
                RAISE EXCEPTION 'DTS can mutate a non-source-wide relation';
            END IF;
        END
        $lean_dts_acl_assertions$;
        """
    )


def _grant_growth_acl() -> None:
    outbox_columns = ", ".join(OUTBOX_STATUS_COLUMNS)
    task_columns = ", ".join(TASK_ASSIGNMENT_SUPPRESSION_COLUMNS)
    op.execute(
        f"""
        REVOKE CREATE ON SCHEMA public FROM tit_growth_app;
        GRANT USAGE ON SCHEMA public TO tit_growth_app;

        GRANT SELECT ON TABLE
            public.teacher_source_wide,
            public.lesson_source_wide,
            public.complaint_category_rules
        TO tit_growth_app;

        REVOKE ALL PRIVILEGES ON TABLE public.task_assignments
        FROM tit_growth_app;
        GRANT SELECT, INSERT ON TABLE public.task_assignments
        TO tit_growth_app;
        GRANT UPDATE ({task_columns})
        ON TABLE public.task_assignments
        TO tit_growth_app;

        GRANT SELECT, INSERT, UPDATE ON TABLE
            public.teachers,
            public.personalized_trigger_matches,
            public.notifications,
            public.ops_cases
        TO tit_growth_app;

        REVOKE ALL PRIVILEGES ON TABLE public.outbox_events
        FROM tit_growth_app;
        GRANT SELECT, INSERT ON TABLE public.outbox_events
        TO tit_growth_app;
        GRANT UPDATE ({outbox_columns})
        ON TABLE public.outbox_events
        TO tit_growth_app;
        """
    )
    op.execute(
        """
        DO $lean_growth_acl_assertions$
        BEGIN
            IF has_schema_privilege(
                'tit_growth_app', 'public', 'CREATE'
            ) THEN
                RAISE EXCEPTION 'tit_growth_app may not create objects';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM unnest(ARRAY[
                    'public.teacher_source_wide',
                    'public.lesson_source_wide'
                ]::text[]) AS relation(name),
                unnest(ARRAY[
                    'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE',
                    'REFERENCES', 'TRIGGER'
                ]::text[]) AS privilege(name)
                WHERE has_table_privilege(
                    'tit_growth_app', relation.name, privilege.name
                )
            ) THEN
                RAISE EXCEPTION 'tit_growth_app source-wide access is not read-only';
            END IF;
            IF has_table_privilege(
                'tit_growth_app', 'public.task_assignments', 'UPDATE'
            ) OR has_column_privilege(
                'tit_growth_app', 'public.task_assignments',
                'assignment_id', 'UPDATE'
            ) THEN
                RAISE EXCEPTION 'task assignment UPDATE is too broad';
            END IF;
            IF NOT has_column_privilege(
                'tit_growth_app', 'public.task_assignments', 'status', 'UPDATE'
            ) THEN
                RAISE EXCEPTION 'task assignment status UPDATE is missing';
            END IF;
            IF has_table_privilege(
                'tit_growth_app', 'public.outbox_events', 'UPDATE'
            ) OR has_column_privilege(
                'tit_growth_app', 'public.outbox_events', 'payload', 'UPDATE'
            ) THEN
                RAISE EXCEPTION 'outbox UPDATE is too broad';
            END IF;
            IF NOT has_column_privilege(
                'tit_growth_app', 'public.outbox_events', 'status', 'UPDATE'
            ) THEN
                RAISE EXCEPTION 'outbox status UPDATE is missing';
            END IF;
        END
        $lean_growth_acl_assertions$;
        """
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    _guard_runtime_roles()
    _retire_legacy_source_acl()
    _grant_dts_acl()
    _grant_growth_acl()


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    # Older databases may or may not still have the retired roles.  Recreating
    # their login credentials or memberships would be unsafe.  Downgrade only
    # removes the DTS writer introduced by this contract; the merged
    # tit_growth_app permissions remain a safe superset for revision 55.
    op.execute(
        """
        DO $lean_roles_downgrade$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_roles
                WHERE rolname = 'tit_dts_ingest_runtime'
            ) THEN
                REVOKE ALL PRIVILEGES ON TABLE
                    public.teacher_source_wide,
                    public.lesson_source_wide
                FROM tit_dts_ingest_runtime;
                REVOKE ALL PRIVILEGES ON SCHEMA public
                FROM tit_dts_ingest_runtime;
            END IF;
        END
        $lean_roles_downgrade$;
        """
    )
