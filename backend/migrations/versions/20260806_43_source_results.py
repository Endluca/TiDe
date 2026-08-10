"""add source-wide derived results and the restricted worker contract

Revision ID: 20260806_43_source_results
Revises: 20260806_42_effective_acl
Create Date: 2026-08-06
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260806_43_source_results"
down_revision: str | None = "20260806_42_effective_acl"
branch_labels: str | None = None
depends_on: str | None = None


JSON_VALUE = sa.JSON().with_variant(
    postgresql.JSONB(astext_type=sa.Text()),
    "postgresql",
)

SOURCE_READ_TABLES: tuple[str, ...] = (
    "teacher_source_wide",
    "lesson_source_wide",
    "complaint_category_rules",
    "task_templates",
    "config_versions",
)

READ_INSERT_TABLES: tuple[str, ...] = (
    "score_entries",
    "task_assignments",
)

READ_INSERT_UPDATE_TABLES: tuple[str, ...] = (
    "teachers",
    "lesson_score_results",
    "teacher_qualifications",
    "score_accounts",
    "personalized_trigger_matches",
    "notifications",
    "ops_cases",
)

READ_INSERT_UPDATE_DELETE_TABLES: tuple[str, ...] = (
    "score_component_accounts",
)

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


def _qualified(objects: tuple[str, ...]) -> str:
    return ",\n            ".join(f"public.{name}" for name in objects)


def _guard_source_worker_role() -> None:
    op.execute(
        """
        DO $source_worker_role_guard$
        DECLARE
            worker_role record;
        BEGIN
            SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole,
                   rolreplication, rolbypassrls
            INTO worker_role
            FROM pg_roles
            WHERE rolname = 'tit_source_worker';

            IF NOT FOUND THEN
                RAISE EXCEPTION
                    'required NOLOGIN role tit_source_worker does not exist';
            END IF;
            IF worker_role.rolcanlogin THEN
                RAISE EXCEPTION 'tit_source_worker must be NOLOGIN';
            END IF;
            IF worker_role.rolsuper
               OR worker_role.rolcreatedb
               OR worker_role.rolcreaterole
               OR worker_role.rolreplication
               OR worker_role.rolbypassrls THEN
                RAISE EXCEPTION
                    'tit_source_worker must be an unprivileged role';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM pg_auth_members AS memberships
                JOIN pg_roles AS member_role
                  ON member_role.oid = memberships.member
                WHERE member_role.rolname = 'tit_source_worker'
            ) THEN
                RAISE EXCEPTION
                    'tit_source_worker must not inherit another database role';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM pg_namespace AS namespaces
                WHERE namespaces.nspname = 'public'
                  AND namespaces.nspowner = (
                      SELECT oid FROM pg_roles
                      WHERE rolname = 'tit_source_worker'
                  )
            ) OR EXISTS (
                SELECT 1
                FROM pg_class AS relations
                JOIN pg_namespace AS namespaces
                  ON namespaces.oid = relations.relnamespace
                WHERE namespaces.nspname = 'public'
                  AND relations.relowner = (
                      SELECT oid FROM pg_roles
                      WHERE rolname = 'tit_source_worker'
                  )
            ) THEN
                RAISE EXCEPTION
                    'tit_source_worker must not own public schema objects';
            END IF;
        END
        $source_worker_role_guard$;
        """
    )


def _create_derived_tables() -> None:
    op.create_table(
        "lesson_score_results",
        sa.Column("lesson_id", sa.String(length=128), nullable=False),
        sa.Column(
            "user_feedback_score",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "reliability_score",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "class_quality_score",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "lesson_total_score",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "dimensions",
            JSON_VALUE,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("score_rule_version", sa.String(length=64), nullable=False),
        sa.Column(
            "projection_revision",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "calculated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.CheckConstraint(
            "reliability_score >= 0 AND user_feedback_score >= 0 "
            "AND class_quality_score >= 0 AND lesson_total_score >= 0",
            name="ck_lesson_score_result_nonnegative",
        ),
        sa.CheckConstraint(
            "abs(lesson_total_score - (reliability_score + "
            "user_feedback_score + class_quality_score)) <= 0.000001",
            name="ck_lesson_score_result_total",
        ),
        sa.CheckConstraint(
            "projection_revision >= 1",
            name="ck_lesson_score_result_projection_revision",
        ),
        sa.ForeignKeyConstraint(
            ["lesson_id"],
            ["public.lesson_source_wide.课程id"],
            name="fk_lesson_score_result_source_lesson",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "lesson_id",
            name="lesson_score_results_pkey",
        ),
        schema="public",
    )

    op.create_table(
        "teacher_qualifications",
        sa.Column("teacher_id", sa.String(length=64), nullable=False),
        sa.Column(
            "graduation_criteria_met",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "graduation_qualified",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "graduation_qualified_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "gold_criteria_met",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "gold_qualified",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "gold_qualified_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("score_rule_version", sa.String(length=64), nullable=False),
        sa.Column(
            "gate_results",
            JSON_VALUE,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "revision",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "calculated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.CheckConstraint(
            "gold_qualified = FALSE OR graduation_qualified = TRUE",
            name="ck_teacher_qualification_gold_requires_graduation",
        ),
        sa.CheckConstraint(
            "graduation_qualified = TRUE OR graduation_qualified_at IS NULL",
            name="ck_teacher_qualification_graduation_time",
        ),
        sa.CheckConstraint(
            "gold_qualified = TRUE OR gold_qualified_at IS NULL",
            name="ck_teacher_qualification_gold_time",
        ),
        sa.CheckConstraint(
            "revision >= 1",
            name="ck_teacher_qualification_revision",
        ),
        sa.ForeignKeyConstraint(
            ["teacher_id"],
            ["public.teachers.teacher_id"],
            name="fk_teacher_qualification_teacher",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "teacher_id",
            name="teacher_qualifications_pkey",
        ),
        schema="public",
    )


def _rewire_trigger_match_lesson_fk(*, to_source_wide: bool) -> None:
    if to_source_wide:
        op.execute(
            """
            DO $trigger_match_source_lesson_guard$
            DECLARE
                missing_count bigint;
            BEGIN
                SELECT count(*)
                INTO missing_count
                FROM public.personalized_trigger_matches AS match
                LEFT JOIN public.lesson_source_wide AS source
                  ON source."课程id" = match.lesson_id
                WHERE match.lesson_id IS NOT NULL
                  AND source."课程id" IS NULL;

                IF missing_count <> 0 THEN
                    RAISE EXCEPTION
                        'cannot rewire personalized lesson FK: % lesson IDs are absent from lesson_source_wide',
                        missing_count;
                END IF;
            END
            $trigger_match_source_lesson_guard$;
            """
        )
        remote_table = "lesson_source_wide"
        remote_column = "课程id"
        ondelete = "SET NULL"
    else:
        op.execute(
            """
            DO $trigger_match_legacy_lesson_guard$
            DECLARE
                missing_count bigint;
            BEGIN
                SELECT count(*)
                INTO missing_count
                FROM public.personalized_trigger_matches AS match
                LEFT JOIN public.lesson_facts AS fact
                  ON fact.lesson_id = match.lesson_id
                WHERE match.lesson_id IS NOT NULL
                  AND fact.lesson_id IS NULL;

                IF missing_count <> 0 THEN
                    RAISE EXCEPTION
                        'cannot restore legacy personalized lesson FK: % lesson IDs are absent from lesson_facts',
                        missing_count;
                END IF;
            END
            $trigger_match_legacy_lesson_guard$;
            """
        )
        remote_table = "lesson_facts"
        remote_column = "lesson_id"
        ondelete = "RESTRICT"

    op.drop_constraint(
        "fk_personalized_trigger_match_lesson",
        "personalized_trigger_matches",
        schema="public",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_personalized_trigger_match_lesson",
        "personalized_trigger_matches",
        remote_table,
        ["lesson_id"],
        [remote_column],
        source_schema="public",
        referent_schema="public",
        ondelete=ondelete,
    )


def _backfill_earned_qualifications() -> None:
    op.execute(
        """
        INSERT INTO public.teacher_qualifications (
            teacher_id,
            graduation_criteria_met,
            graduation_qualified,
            graduation_qualified_at,
            gold_criteria_met,
            gold_qualified,
            gold_qualified_at,
            score_rule_version,
            gate_results,
            revision,
            calculated_at
        )
        SELECT
            teacher.teacher_id,
            lower(COALESCE(
                teacher.payload ->> 'graduation_criteria_met',
                'false'
            )) = 'true',
            teacher.graduation_state = 'GRADUATED'
                OR teacher.gold_qualified,
            NULL,
            lower(COALESCE(
                teacher.payload ->> 'gold_criteria_met',
                'false'
            )) = 'true',
            teacher.gold_qualified,
            NULL,
            COALESCE(
                NULLIF(teacher.payload ->> 'score_rule_version', ''),
                'LEGACY_UNKNOWN'
            ),
            jsonb_build_object(
                'migration_source', 'teachers_compatibility_projection',
                'qualification_time_status', 'SOURCE_MISSING'
            ),
            1,
            teacher.updated_at
        FROM public.teachers AS teacher
        ON CONFLICT (teacher_id) DO NOTHING;
        """
    )


def _create_qualification_guard() -> None:
    op.execute(
        """
        CREATE FUNCTION public.guard_teacher_qualification_fact()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $function$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                IF OLD.graduation_qualified OR OLD.gold_qualified THEN
                    RAISE EXCEPTION 'EARNED_QUALIFICATION_DELETE_FORBIDDEN';
                END IF;
                RETURN OLD;
            END IF;

            IF NEW.teacher_id IS DISTINCT FROM OLD.teacher_id THEN
                RAISE EXCEPTION 'QUALIFICATION_TEACHER_IMMUTABLE';
            END IF;
            IF OLD.graduation_qualified AND NOT NEW.graduation_qualified THEN
                RAISE EXCEPTION 'GRADUATION_QUALIFICATION_IRREVERSIBLE';
            END IF;
            IF OLD.gold_qualified AND NOT NEW.gold_qualified THEN
                RAISE EXCEPTION 'GOLD_QUALIFICATION_IRREVERSIBLE';
            END IF;
            IF OLD.graduation_qualified_at IS NOT NULL
               AND NEW.graduation_qualified_at
                   IS DISTINCT FROM OLD.graduation_qualified_at THEN
                RAISE EXCEPTION 'GRADUATION_QUALIFIED_AT_IMMUTABLE';
            END IF;
            IF OLD.gold_qualified_at IS NOT NULL
               AND NEW.gold_qualified_at IS DISTINCT FROM OLD.gold_qualified_at THEN
                RAISE EXCEPTION 'GOLD_QUALIFIED_AT_IMMUTABLE';
            END IF;
            IF NEW.revision <> OLD.revision + 1 THEN
                RAISE EXCEPTION 'QUALIFICATION_REVISION_MUST_INCREMENT';
            END IF;
            RETURN NEW;
        END
        $function$;

        CREATE TRIGGER trg_guard_teacher_qualification_fact
        BEFORE UPDATE OR DELETE ON public.teacher_qualifications
        FOR EACH ROW
        EXECUTE FUNCTION public.guard_teacher_qualification_fact();

        REVOKE ALL ON FUNCTION public.guard_teacher_qualification_fact()
        FROM PUBLIC;
        """
    )


def _grant_source_worker_acl() -> None:
    all_relations = (
        SOURCE_READ_TABLES
        + READ_INSERT_TABLES
        + READ_INSERT_UPDATE_TABLES
        + READ_INSERT_UPDATE_DELETE_TABLES
        + ("outbox_events",)
    )
    outbox_columns = ", ".join(OUTBOX_STATUS_COLUMNS)
    task_suppression_columns = ", ".join(
        TASK_ASSIGNMENT_SUPPRESSION_COLUMNS
    )
    op.execute(
        f"""
        REVOKE ALL PRIVILEGES ON TABLE
            {_qualified(all_relations)}
        FROM tit_source_worker;
        REVOKE UPDATE ({outbox_columns})
            ON TABLE public.outbox_events
        FROM tit_source_worker;
        REVOKE UPDATE ({task_suppression_columns})
            ON TABLE public.task_assignments
        FROM tit_source_worker;
        REVOKE ALL PRIVILEGES ON TABLE
            public.lesson_score_results,
            public.teacher_qualifications
        FROM PUBLIC;

        REVOKE CREATE ON SCHEMA public FROM tit_source_worker;
        GRANT USAGE ON SCHEMA public TO tit_source_worker;

        GRANT SELECT ON TABLE
            {_qualified(SOURCE_READ_TABLES)}
        TO tit_source_worker;

        GRANT SELECT, INSERT ON TABLE
            {_qualified(READ_INSERT_TABLES)}
        TO tit_source_worker;

        GRANT UPDATE ({task_suppression_columns})
            ON TABLE public.task_assignments
        TO tit_source_worker;

        GRANT SELECT, INSERT, UPDATE ON TABLE
            {_qualified(READ_INSERT_UPDATE_TABLES)}
        TO tit_source_worker;

        GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE
            {_qualified(READ_INSERT_UPDATE_DELETE_TABLES)}
        TO tit_source_worker;

        GRANT SELECT ON TABLE public.outbox_events TO tit_source_worker;
        GRANT UPDATE ({outbox_columns})
            ON TABLE public.outbox_events
        TO tit_source_worker;
        """
    )
    op.execute(
        """
        DO $source_worker_acl_assertions$
        BEGIN
            IF has_schema_privilege(
                'tit_source_worker', 'public', 'CREATE'
            ) THEN
                RAISE EXCEPTION
                    'tit_source_worker must not create public schema objects';
            END IF;
            IF NOT has_schema_privilege(
                'tit_source_worker', 'public', 'USAGE'
            ) THEN
                RAISE EXCEPTION
                    'tit_source_worker is missing public schema usage';
            END IF;
            IF has_table_privilege(
                'tit_source_worker',
                'public.teacher_source_wide',
                'INSERT'
            ) OR has_table_privilege(
                'tit_source_worker',
                'public.teacher_source_wide',
                'UPDATE'
            ) OR has_table_privilege(
                'tit_source_worker',
                'public.teacher_source_wide',
                'DELETE'
            ) OR has_table_privilege(
                'tit_source_worker',
                'public.lesson_source_wide',
                'INSERT'
            ) OR has_table_privilege(
                'tit_source_worker',
                'public.lesson_source_wide',
                'UPDATE'
            ) OR has_table_privilege(
                'tit_source_worker',
                'public.lesson_source_wide',
                'DELETE'
            ) THEN
                RAISE EXCEPTION
                    'tit_source_worker may only read source-wide tables';
            END IF;
            IF has_table_privilege(
                'tit_source_worker', 'public.outbox_events', 'UPDATE'
            ) OR has_column_privilege(
                'tit_source_worker',
                'public.outbox_events',
                'payload',
                'UPDATE'
            ) THEN
                RAISE EXCEPTION
                    'tit_source_worker received broad outbox update access';
            END IF;
            IF NOT has_column_privilege(
                'tit_source_worker',
                'public.outbox_events',
                'status',
                'UPDATE'
            ) THEN
                RAISE EXCEPTION
                    'tit_source_worker cannot complete source outbox events';
            END IF;
            IF has_table_privilege(
                'tit_source_worker', 'public.task_assignments', 'UPDATE'
            ) OR has_table_privilege(
                'tit_source_worker', 'public.task_assignments', 'DELETE'
            ) THEN
                RAISE EXCEPTION
                    'tit_source_worker must not receive broad task mutation access';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM unnest(ARRAY[
                    'status',
                    'status_reason_code',
                    'status_changed_at',
                    'updated_by'
                ]) AS allowed(column_name)
                WHERE NOT has_column_privilege(
                    'tit_source_worker',
                    'public.task_assignments',
                    allowed.column_name,
                    'UPDATE'
                )
            ) THEN
                RAISE EXCEPTION
                    'tit_source_worker is missing task suppression privileges';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM pg_attribute AS columns
                WHERE columns.attrelid = 'public.task_assignments'::regclass
                  AND columns.attnum > 0
                  AND NOT columns.attisdropped
                  AND columns.attname::text <> ALL (ARRAY[
                      'status',
                      'status_reason_code',
                      'status_changed_at',
                      'updated_by'
                  ])
                  AND has_column_privilege(
                      'tit_source_worker',
                      'public.task_assignments',
                      columns.attname::text,
                      'UPDATE'
                  )
            ) THEN
                RAISE EXCEPTION
                    'tit_source_worker may only update task suppression columns';
            END IF;
        END
        $source_worker_acl_assertions$;
        """
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    _guard_source_worker_role()
    _create_derived_tables()
    _rewire_trigger_match_lesson_fk(to_source_wide=True)
    _backfill_earned_qualifications()
    _create_qualification_guard()
    _grant_source_worker_acl()


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    outbox_columns = ", ".join(OUTBOX_STATUS_COLUMNS)
    task_suppression_columns = ", ".join(
        TASK_ASSIGNMENT_SUPPRESSION_COLUMNS
    )
    all_relations = (
        SOURCE_READ_TABLES
        + READ_INSERT_TABLES
        + READ_INSERT_UPDATE_TABLES
        + READ_INSERT_UPDATE_DELETE_TABLES
        + ("outbox_events",)
    )
    op.execute(
        f"""
        REVOKE UPDATE ({outbox_columns})
            ON TABLE public.outbox_events
        FROM tit_source_worker;
        REVOKE UPDATE ({task_suppression_columns})
            ON TABLE public.task_assignments
        FROM tit_source_worker;
        REVOKE ALL PRIVILEGES ON TABLE
            {_qualified(all_relations)}
        FROM tit_source_worker;
        REVOKE USAGE ON SCHEMA public FROM tit_source_worker;
        """
    )
    op.execute(
        """
        DROP TRIGGER IF EXISTS trg_guard_teacher_qualification_fact
        ON public.teacher_qualifications;
        DROP FUNCTION IF EXISTS public.guard_teacher_qualification_fact();
        """
    )
    _rewire_trigger_match_lesson_fk(to_source_wide=False)
    op.drop_table("teacher_qualifications", schema="public")
    op.drop_table("lesson_score_results", schema="public")
