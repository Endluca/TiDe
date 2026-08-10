"""grant the runtime role explicit minimum public-schema privileges

Revision ID: 20260806_40_runtime_acl
Revises: 20260806_39_source_wide
Create Date: 2026-08-06
"""

from __future__ import annotations

from alembic import op


revision: str = "20260806_40_runtime_acl"
down_revision: str | None = "20260806_39_source_wide"
branch_labels: str | None = None
depends_on: str | None = None


# These groups are intentionally explicit.  A future table must choose a group
# in its own migration; runtime privileges must never grow through
# blanket relation grants or broad default privileges.
NO_ACCESS_TABLES: tuple[str, ...] = (
    # Retired import/Agent stores remain only until their FK and code
    # dependencies are removed.  Neither the API nor the score worker owns
    # these facts.
    "data_import_batches",
    "source_records",
    "agent_decisions",
    "provider_calls",
    # The root API/worker does not own teacher-side delivery history.
    "notification_events",
)

READ_ONLY_TABLES: tuple[str, ...] = (
    "teacher_source_wide",
    "lesson_source_wide",
    # Transitional consumers still query these two old projections.  New
    # source writes must not flow back into them.
    "lesson_facts",
    "complaint_category_rules",
    "personalized_trigger_matches",
    "task_assignments",
    "operator_role_grants",
)

READ_INSERT_TABLES: tuple[str, ...] = (
    "score_entries",
    "audit_events",
    "idempotency_records",
    "config_publication_audits",
)

INSERT_ONLY_TABLES: tuple[str, ...] = (
    "ops_decisions",
)

READ_UPDATE_TABLES: tuple[str, ...] = (
    "teachers",
    "teacher_metric_snapshots",
    "notifications",
    "ops_cases",
    "outbound_outputs",
)

READ_INSERT_UPDATE_TABLES: tuple[str, ...] = (
    "score_accounts",
    "task_templates",
    "outbox_events",
    "config_versions",
    "operator_sessions",
)

READ_INSERT_DELETE_TABLES: tuple[str, ...] = (
    "lesson_dimension_scores",
)

READ_INSERT_UPDATE_DELETE_TABLES: tuple[str, ...] = (
    "score_component_accounts",
)

# The root runtime does not query these teacher-facing projections.  The
# separately managed tit_teacher_crud role keeps its own SELECT grants.
NO_ACCESS_VIEWS: tuple[str, ...] = (
    "teacher_scorecard_current",
    "teacher_lesson_score_current",
)


def _qualified(objects: tuple[str, ...]) -> str:
    return ",\n            ".join(f"public.{name}" for name in objects)


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    op.execute(
        """
        DO $runtime_role_guard$
        DECLARE
            runtime_role record;
        BEGIN
            SELECT rolsuper, rolcreatedb, rolcreaterole,
                   rolreplication, rolbypassrls
            INTO runtime_role
            FROM pg_roles
            WHERE rolname = 'tit_growth_app';

            IF NOT FOUND THEN
                RAISE EXCEPTION
                    'required database role tit_growth_app does not exist';
            END IF;
            IF runtime_role.rolsuper
               OR runtime_role.rolcreatedb
               OR runtime_role.rolcreaterole
               OR runtime_role.rolreplication
               OR runtime_role.rolbypassrls THEN
                RAISE EXCEPTION
                    'tit_growth_app must be an unprivileged runtime role';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM pg_auth_members AS memberships
                JOIN pg_roles AS member_role
                  ON member_role.oid = memberships.member
                WHERE member_role.rolname = 'tit_growth_app'
            ) THEN
                RAISE EXCEPTION
                    'tit_growth_app must not inherit or SET ROLE into another role';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM pg_namespace AS namespaces
                WHERE namespaces.nspname = 'public'
                  AND namespaces.nspowner = (
                      SELECT oid FROM pg_roles WHERE rolname = 'tit_growth_app'
                  )
            ) OR EXISTS (
                SELECT 1
                FROM pg_class AS relations
                JOIN pg_namespace AS namespaces
                  ON namespaces.oid = relations.relnamespace
                WHERE namespaces.nspname = 'public'
                  AND relations.relowner = (
                      SELECT oid FROM pg_roles WHERE rolname = 'tit_growth_app'
                  )
            ) OR EXISTS (
                SELECT 1
                FROM pg_proc AS routines
                JOIN pg_namespace AS namespaces
                  ON namespaces.oid = routines.pronamespace
                WHERE namespaces.nspname = 'public'
                  AND routines.proowner = (
                      SELECT oid FROM pg_roles WHERE rolname = 'tit_growth_app'
                  )
            ) THEN
                RAISE EXCEPTION
                    'tit_growth_app must not own objects in schema public';
            END IF;
        END
        $runtime_role_guard$;
        """
    )

    all_relations = (
        NO_ACCESS_TABLES
        + READ_ONLY_TABLES
        + READ_INSERT_TABLES
        + INSERT_ONLY_TABLES
        + READ_UPDATE_TABLES
        + READ_INSERT_UPDATE_TABLES
        + READ_INSERT_DELETE_TABLES
        + READ_INSERT_UPDATE_DELETE_TABLES
        + ("operator_accounts",)
        + NO_ACCESS_VIEWS
    )
    op.execute(
        f"""
        REVOKE ALL PRIVILEGES ON TABLE
            {_qualified(all_relations)}
        FROM tit_growth_app;

        REVOKE CREATE ON SCHEMA public FROM PUBLIC, tit_growth_app;
        GRANT USAGE ON SCHEMA public TO tit_growth_app;

        GRANT SELECT ON TABLE
            {_qualified(READ_ONLY_TABLES)}
        TO tit_growth_app;

        GRANT SELECT, INSERT ON TABLE
            {_qualified(READ_INSERT_TABLES)}
        TO tit_growth_app;

        GRANT INSERT ON TABLE
            {_qualified(INSERT_ONLY_TABLES)}
        TO tit_growth_app;

        GRANT SELECT, UPDATE ON TABLE
            {_qualified(READ_UPDATE_TABLES)}
        TO tit_growth_app;

        GRANT SELECT, INSERT, UPDATE ON TABLE
            {_qualified(READ_INSERT_UPDATE_TABLES)}
        TO tit_growth_app;

        GRANT SELECT, INSERT, DELETE ON TABLE
            {_qualified(READ_INSERT_DELETE_TABLES)}
        TO tit_growth_app;

        GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE
            {_qualified(READ_INSERT_UPDATE_DELETE_TABLES)}
        TO tit_growth_app;

        GRANT SELECT ON TABLE public.operator_accounts TO tit_growth_app;
        GRANT UPDATE (password_hash)
            ON TABLE public.operator_accounts TO tit_growth_app;

        REVOKE ALL PRIVILEGES ON SEQUENCE
            public.audit_events_sequence_seq
        FROM tit_growth_app;
        GRANT USAGE ON SEQUENCE
            public.audit_events_sequence_seq
        TO tit_growth_app;
        """
    )

    op.execute(
        """
        DO $runtime_acl_assertions$
        BEGIN
            IF NOT has_schema_privilege('tit_growth_app', 'public', 'USAGE') THEN
                RAISE EXCEPTION
                    'tit_growth_app is missing USAGE on schema public';
            END IF;
            IF has_schema_privilege('tit_growth_app', 'public', 'CREATE') THEN
                RAISE EXCEPTION
                    'tit_growth_app still has CREATE on schema public';
            END IF;
            IF has_table_privilege(
                'tit_growth_app', 'public.operator_accounts', 'UPDATE'
            ) THEN
                RAISE EXCEPTION
                    'tit_growth_app received table-wide operator account UPDATE';
            END IF;
            IF NOT has_column_privilege(
                'tit_growth_app',
                'public.operator_accounts',
                'password_hash',
                'UPDATE'
            ) THEN
                RAISE EXCEPTION
                    'tit_growth_app is missing password_hash UPDATE';
            END IF;
        END
        $runtime_acl_assertions$;
        """
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    all_relations = (
        NO_ACCESS_TABLES
        + READ_ONLY_TABLES
        + READ_INSERT_TABLES
        + INSERT_ONLY_TABLES
        + READ_UPDATE_TABLES
        + READ_INSERT_UPDATE_TABLES
        + READ_INSERT_DELETE_TABLES
        + READ_INSERT_UPDATE_DELETE_TABLES
        + ("operator_accounts",)
        + NO_ACCESS_VIEWS
    )
    op.execute(
        f"""
        REVOKE ALL PRIVILEGES ON TABLE
            {_qualified(all_relations)}
        FROM tit_growth_app;
        REVOKE ALL PRIVILEGES ON SEQUENCE
            public.audit_events_sequence_seq
        FROM tit_growth_app;

        GRANT SELECT ON TABLE
            public.teacher_source_wide,
            public.lesson_source_wide,
            public.teacher_scorecard_current,
            public.teacher_lesson_score_current
        TO tit_growth_app;

        GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE
            public.score_component_accounts
        TO tit_growth_app;
        """
    )
