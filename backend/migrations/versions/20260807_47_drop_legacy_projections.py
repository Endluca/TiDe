"""drop the empty legacy teacher and lesson projections

Revision ID: 20260807_47_legacy_drop
Revises: 20260807_46_teacher_g01_source
Create Date: 2026-08-07

The two source-wide tables and their current derived results have replaced
these projections.  This revision owns only public-schema objects.  The
historical ``tide.analytics_task_business_change_v1`` view must be retired by
its owner before this revision can run.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260807_47_legacy_drop"
down_revision: str | None = "20260807_46_teacher_g01_source"
branch_labels: str | None = None
depends_on: str | None = None


JSON_VALUE = sa.JSON().with_variant(
    postgresql.JSONB(astext_type=sa.Text()),
    "postgresql",
)

LEGACY_TABLES: tuple[str, ...] = (
    "lesson_dimension_scores",
    "lesson_facts",
    "teacher_metric_snapshots",
)


def _guard_upgrade() -> None:
    # The explicit lock closes the check/drop race with any stale writer.
    # The cross-schema view is checked first so this migration never appears
    # to take ownership of a tide object.
    op.execute(
        """
        DO $legacy_projection_drop_guard$
        DECLARE
            missing_tables text;
        BEGIN
            IF to_regclass(
                'tide.analytics_task_business_change_v1'
            ) IS NOT NULL THEN
                RAISE EXCEPTION
                    'tide.analytics_task_business_change_v1 must be retired by the tide schema owner before legacy public projections can be dropped';
            END IF;

            SELECT string_agg(table_name, ', ' ORDER BY table_name)
            INTO missing_tables
            FROM unnest(ARRAY[
                'lesson_dimension_scores',
                'lesson_facts',
                'teacher_metric_snapshots'
            ]::text[]) AS expected(table_name)
            WHERE to_regclass('public.' || expected.table_name) IS NULL;

            IF missing_tables IS NOT NULL THEN
                RAISE EXCEPTION
                    'legacy projection cleanup expected missing public tables: %',
                    missing_tables;
            END IF;
        END
        $legacy_projection_drop_guard$;

        LOCK TABLE
            public.lesson_dimension_scores,
            public.lesson_facts,
            public.teacher_metric_snapshots
        IN ACCESS EXCLUSIVE MODE;

        DO $legacy_projection_locked_guard$
        DECLARE
            populated_tables text;
            dependent_views text;
            dependent_foreign_keys text;
            inherited_relations text;
            publication_memberships text;
        BEGIN
            SELECT string_agg(table_name, ', ' ORDER BY table_name)
            INTO populated_tables
            FROM (
                SELECT 'lesson_dimension_scores' AS table_name
                WHERE EXISTS (
                    SELECT 1
                    FROM public.lesson_dimension_scores
                    LIMIT 1
                )
                UNION ALL
                SELECT 'lesson_facts'
                WHERE EXISTS (
                    SELECT 1 FROM public.lesson_facts LIMIT 1
                )
                UNION ALL
                SELECT 'teacher_metric_snapshots'
                WHERE EXISTS (
                    SELECT 1
                    FROM public.teacher_metric_snapshots
                    LIMIT 1
                )
            ) AS populated;

            IF populated_tables IS NOT NULL THEN
                RAISE EXCEPTION
                    'refusing to drop populated legacy projections: %',
                    populated_tables;
            END IF;

            SELECT string_agg(
                DISTINCT format('%I.%I', dependent_ns.nspname, dependent.relname),
                ', '
                ORDER BY format('%I.%I', dependent_ns.nspname, dependent.relname)
            )
            INTO dependent_views
            FROM pg_depend AS dependency
            JOIN pg_rewrite AS rewrite
              ON rewrite.oid = dependency.objid
            JOIN pg_class AS dependent
              ON dependent.oid = rewrite.ev_class
            JOIN pg_namespace AS dependent_ns
              ON dependent_ns.oid = dependent.relnamespace
            WHERE dependency.refobjid = ANY (ARRAY[
                'public.lesson_dimension_scores'::regclass,
                'public.lesson_facts'::regclass,
                'public.teacher_metric_snapshots'::regclass
            ]::oid[])
              AND dependent.oid <> dependency.refobjid;

            IF dependent_views IS NOT NULL THEN
                RAISE EXCEPTION
                    'refusing to drop legacy projections with dependent views: %',
                    dependent_views;
            END IF;

            SELECT string_agg(
                format(
                    '%I.%I (%I)',
                    dependent_ns.nspname,
                    dependent.relname,
                    constraint_row.conname
                ),
                ', '
                ORDER BY dependent_ns.nspname,
                         dependent.relname,
                         constraint_row.conname
            )
            INTO dependent_foreign_keys
            FROM pg_constraint AS constraint_row
            JOIN pg_class AS dependent
              ON dependent.oid = constraint_row.conrelid
            JOIN pg_namespace AS dependent_ns
              ON dependent_ns.oid = dependent.relnamespace
            WHERE constraint_row.contype = 'f'
              AND constraint_row.confrelid = ANY (ARRAY[
                    'public.lesson_dimension_scores'::regclass,
                    'public.lesson_facts'::regclass,
                    'public.teacher_metric_snapshots'::regclass
              ]::oid[])
              AND NOT constraint_row.conrelid = ANY (ARRAY[
                    'public.lesson_dimension_scores'::regclass,
                    'public.lesson_facts'::regclass,
                    'public.teacher_metric_snapshots'::regclass
              ]::oid[]);

            IF dependent_foreign_keys IS NOT NULL THEN
                RAISE EXCEPTION
                    'refusing to drop legacy projections with external foreign keys: %',
                    dependent_foreign_keys;
            END IF;

            SELECT string_agg(
                DISTINCT format(
                    '%I.%I',
                    external_ns.nspname,
                    external_relation.relname
                ),
                ', '
                ORDER BY format(
                    '%I.%I',
                    external_ns.nspname,
                    external_relation.relname
                )
            )
            INTO inherited_relations
            FROM pg_inherits AS inheritance
            JOIN pg_class AS external_relation
              ON external_relation.oid = CASE
                    WHEN inheritance.inhparent = ANY (ARRAY[
                        'public.lesson_dimension_scores'::regclass,
                        'public.lesson_facts'::regclass,
                        'public.teacher_metric_snapshots'::regclass
                    ]::oid[])
                    THEN inheritance.inhrelid
                    ELSE inheritance.inhparent
                 END
            JOIN pg_namespace AS external_ns
              ON external_ns.oid = external_relation.relnamespace
            WHERE inheritance.inhparent = ANY (ARRAY[
                    'public.lesson_dimension_scores'::regclass,
                    'public.lesson_facts'::regclass,
                    'public.teacher_metric_snapshots'::regclass
                  ]::oid[])
               OR inheritance.inhrelid = ANY (ARRAY[
                    'public.lesson_dimension_scores'::regclass,
                    'public.lesson_facts'::regclass,
                    'public.teacher_metric_snapshots'::regclass
                  ]::oid[]);

            IF inherited_relations IS NOT NULL THEN
                RAISE EXCEPTION
                    'refusing to drop inherited or partitioned legacy projections: %',
                    inherited_relations;
            END IF;

            SELECT string_agg(
                DISTINCT publication.pubname,
                ', ' ORDER BY publication.pubname
            )
            INTO publication_memberships
            FROM pg_publication_rel AS membership
            JOIN pg_publication AS publication
              ON publication.oid = membership.prpubid
            WHERE membership.prrelid = ANY (ARRAY[
                'public.lesson_dimension_scores'::regclass,
                'public.lesson_facts'::regclass,
                'public.teacher_metric_snapshots'::regclass
            ]::oid[]);

            IF publication_memberships IS NOT NULL THEN
                RAISE EXCEPTION
                    'refusing to drop legacy projections still published by: %',
                    publication_memberships;
            END IF;
        END
        $legacy_projection_locked_guard$;
        """
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    _guard_upgrade()
    # Dependency order is deliberate: the score table owns the only accepted
    # target-to-target foreign key.
    for table_name in LEGACY_TABLES:
        op.drop_table(table_name, schema="public")


def _restore_teacher_metric_snapshots() -> None:
    op.create_table(
        "teacher_metric_snapshots",
        sa.Column("snapshot_id", sa.String(length=192), nullable=False),
        sa.Column("batch_id", sa.String(length=96), nullable=False),
        sa.Column("teacher_id", sa.String(length=64), nullable=False),
        sa.Column("snapshot_label", sa.String(length=128), nullable=False),
        sa.Column("source_row_number", sa.Integer(), nullable=False),
        sa.Column("data_mode", sa.String(length=16), nullable=False),
        sa.Column("real_name", sa.String(length=255), nullable=False),
        sa.Column("employment_status", sa.String(length=32), nullable=True),
        sa.Column("bu", sa.String(length=64), nullable=True),
        sa.Column("based_type", sa.String(length=64), nullable=True),
        sa.Column("teach_area_type", sa.String(length=64), nullable=True),
        sa.Column("onboard_date", sa.Date(), nullable=True),
        sa.Column("onboard_30d_end_date", sa.Date(), nullable=True),
        sa.Column("lessons_completed", sa.Integer(), nullable=False),
        sa.Column("total_completed_cnt", sa.Integer(), nullable=False),
        sa.Column("peak_completed_cnt", sa.Integer(), nullable=False),
        sa.Column("perfect_cnt", sa.Integer(), nullable=False),
        sa.Column("on_time_completed_cnt", sa.Integer(), nullable=False),
        sa.Column("feedback_praise_cnt", sa.Integer(), nullable=False),
        sa.Column("feedback_favorite_cnt", sa.Integer(), nullable=False),
        sa.Column(
            "completed_again_student_15d_cnt",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column("late_cnt", sa.Integer(), nullable=False),
        sa.Column("early_cnt", sa.Integer(), nullable=False),
        sa.Column("real_absent_cnt", sa.Integer(), nullable=False),
        sa.Column("severe_redline_event", sa.Boolean(), nullable=False),
        sa.Column("capacity_score", sa.Float(), nullable=False),
        sa.Column("new_teacher_task_score", sa.Float(), nullable=False),
        sa.Column(
            "class_quality_no_issue_rate",
            sa.Float(),
            nullable=False,
        ),
        sa.Column("reliability_score", sa.Float(), nullable=False),
        sa.Column("user_feedback_score", sa.Float(), nullable=False),
        sa.Column("class_quality_score", sa.Float(), nullable=False),
        sa.Column("raw_total_score", sa.Float(), nullable=False),
        sa.Column("public_total_score", sa.Float(), nullable=False),
        sa.Column("metric_inputs", JSON_VALUE, nullable=False),
        sa.Column("metric_provenance", JSON_VALUE, nullable=False),
        sa.Column("raw_payload", JSON_VALUE, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("score_rule_version", sa.String(length=64), nullable=False),
        sa.Column("score_policy_snapshot", JSON_VALUE, nullable=False),
        sa.Column(
            "score_policy_sha256",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "peak_slot_cnt",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("first_booked_date", sa.Date(), nullable=True),
        sa.Column("is_cpl_tesol", sa.Boolean(), nullable=True),
        sa.Column("is_self_introduce", sa.Boolean(), nullable=True),
        sa.Column("absent_cnt", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "data_mode = 'MIXED'",
            name="ck_teacher_metric_snapshot_mixed",
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["public.data_import_batches.batch_id"],
            name="fk_teacher_metric_snapshot_batch",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "snapshot_id",
            name="teacher_metric_snapshots_pkey",
        ),
        sa.UniqueConstraint(
            "batch_id",
            "teacher_id",
            name="uq_teacher_metric_snapshot_batch_teacher",
        ),
        schema="public",
    )
    for column_name in (
        "batch_id",
        "teacher_id",
        "snapshot_label",
        "employment_status",
        "bu",
        "based_type",
        "teach_area_type",
    ):
        op.create_index(
            f"ix_teacher_metric_snapshots_{column_name}",
            "teacher_metric_snapshots",
            [column_name],
            schema="public",
        )
    op.create_index(
        "ix_teacher_metric_snapshot_ops_filter",
        "teacher_metric_snapshots",
        ["snapshot_label", "employment_status", "bu", "based_type"],
        schema="public",
    )
    op.create_index(
        "ix_teacher_metric_snapshots_score_policy_sha256",
        "teacher_metric_snapshots",
        ["score_policy_sha256"],
        schema="public",
    )
    op.create_index(
        "ix_teacher_metric_snapshots_first_booked_date",
        "teacher_metric_snapshots",
        ["first_booked_date"],
        schema="public",
    )


def _restore_lesson_facts() -> None:
    op.create_table(
        "lesson_facts",
        sa.Column("lesson_id", sa.String(length=128), nullable=False),
        sa.Column("source_appoint_id", sa.String(length=128), nullable=False),
        sa.Column(
            "camp_enrollment_id",
            sa.String(length=96),
            nullable=False,
        ),
        sa.Column("teacher_id", sa.String(length=64), nullable=False),
        sa.Column(
            "scheduled_start_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "scheduled_end_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "lesson_lifecycle_status",
            sa.String(length=48),
            nullable=False,
        ),
        sa.Column("valid_for_scoring", sa.Boolean(), nullable=False),
        sa.Column("evidence_status", sa.String(length=32), nullable=False),
        sa.Column("data_mode", sa.String(length=16), nullable=False),
        sa.Column("payload", JSON_VALUE, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lesson_local_date", sa.Date(), nullable=True),
        sa.Column("lesson_local_time", sa.Time(), nullable=True),
        sa.Column("student_id_hash", sa.String(length=64), nullable=True),
        sa.Column("is_late", sa.Boolean(), nullable=True),
        sa.Column("is_early", sa.Boolean(), nullable=True),
        sa.Column("is_false_early_leave", sa.Boolean(), nullable=True),
        sa.Column("negative_score", sa.Float(), nullable=True),
        sa.Column("has_negative_feedback_tag", sa.Boolean(), nullable=True),
        # Revision 19 dropped this column after adding the remaining feedback
        # fields.  Recreate and immediately drop it below so PostgreSQL keeps
        # the same physical attribute-number gap as revision 46.
        sa.Column(
            "negative_tag_value",
            sa.String(length=255),
            nullable=True,
        ),
        sa.Column(
            "absence_reason_detail",
            sa.String(length=512),
            nullable=True,
        ),
        sa.Column(
            "complaint_category_l1",
            sa.String(length=255),
            nullable=True,
        ),
        sa.Column(
            "complaint_category_l2",
            sa.String(length=255),
            nullable=True,
        ),
        sa.Column(
            "complaint_category_l3",
            sa.String(length=500),
            nullable=True,
        ),
        sa.Column(
            "complaint_source_level",
            sa.String(length=32),
            nullable=True,
        ),
        sa.Column("complaint_level_rank", sa.Integer(), nullable=True),
        sa.Column(
            "complaint_route",
            sa.String(length=32),
            nullable=True,
        ),
        sa.Column(
            "complaint_rule_id",
            sa.String(length=160),
            nullable=True,
        ),
        sa.Column("is_blocked", sa.Boolean(), nullable=True),
        sa.Column("is_favorited", sa.Boolean(), nullable=True),
        sa.Column("has_positive_feedback_tag", sa.Boolean(), nullable=True),
        sa.Column(
            "positive_tag_value",
            sa.String(length=255),
            nullable=True,
        ),
        sa.Column("is_rebooked", sa.Boolean(), nullable=True),
        sa.Column("is_camera_off", sa.Boolean(), nullable=True),
        sa.Column("is_cpu_usage_high", sa.Boolean(), nullable=True),
        sa.Column("is_network_delay_high", sa.Boolean(), nullable=True),
        sa.Column("source_batch_id", sa.String(length=96), nullable=True),
        sa.Column("source_record_id", sa.String(length=160), nullable=True),
        sa.Column("is_peak", sa.Boolean(), nullable=True),
        sa.Column("feedback_detail", sa.Text(), nullable=True),
        sa.Column(
            "negative_tag_values",
            JSON_VALUE,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.CheckConstraint(
            "complaint_level_rank IS NULL OR "
            "complaint_level_rank BETWEEN 0 AND 4",
            name="ck_lesson_fact_complaint_rank",
        ),
        sa.ForeignKeyConstraint(
            ["teacher_id"],
            ["public.teachers.teacher_id"],
            name="lesson_facts_teacher_id_fkey",
        ),
        sa.ForeignKeyConstraint(
            ["complaint_rule_id"],
            ["public.complaint_category_rules.rule_id"],
            name="fk_lesson_fact_complaint_rule",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_batch_id"],
            ["public.data_import_batches.batch_id"],
            name="fk_lesson_fact_source_batch",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_record_id"],
            ["public.source_records.source_record_id"],
            name="fk_lesson_fact_source_record",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("lesson_id", name="lesson_facts_pkey"),
        sa.UniqueConstraint(
            "source_record_id",
            name="uq_lesson_fact_source_record",
        ),
        schema="public",
    )
    op.drop_column(
        "lesson_facts",
        "negative_tag_value",
        schema="public",
    )
    for index_name, columns in (
        ("ix_lesson_facts_camp_enrollment_id", ["camp_enrollment_id"]),
        ("ix_lesson_facts_teacher_id", ["teacher_id"]),
        ("ix_lesson_facts_student_id_hash", ["student_id_hash"]),
        ("ix_lesson_facts_complaint_rule_id", ["complaint_rule_id"]),
        ("ix_lesson_facts_source_batch_id", ["source_batch_id"]),
        (
            "ix_lesson_fact_teacher_local_date",
            ["teacher_id", "lesson_local_date"],
        ),
        ("ix_lesson_fact_complaint_l3", ["complaint_category_l3"]),
    ):
        op.create_index(
            index_name,
            "lesson_facts",
            columns,
            schema="public",
        )


def _restore_lesson_dimension_scores() -> None:
    op.create_table(
        "lesson_dimension_scores",
        sa.Column("score_state_id", sa.String(length=256), nullable=False),
        sa.Column(
            "camp_enrollment_id",
            sa.String(length=96),
            nullable=False,
        ),
        sa.Column("lesson_id", sa.String(length=128), nullable=False),
        sa.Column("teacher_id", sa.String(length=64), nullable=False),
        sa.Column("dimension", sa.String(length=32), nullable=False),
        sa.Column("current_score", sa.Float(), nullable=False),
        sa.Column("evidence_status", sa.String(length=32), nullable=False),
        sa.Column(
            "evidence_coverage",
            sa.String(length=32),
            nullable=True,
        ),
        sa.Column("score_rule_version", sa.String(length=64), nullable=False),
        sa.Column("current_revision", sa.Integer(), nullable=False),
        sa.Column("score_as_of", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_score_entry_id",
            sa.String(length=128),
            nullable=True,
        ),
        sa.Column("payload", JSON_VALUE, nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["lesson_id"],
            ["public.lesson_facts.lesson_id"],
            name="lesson_dimension_scores_lesson_id_fkey",
        ),
        sa.ForeignKeyConstraint(
            ["teacher_id"],
            ["public.teachers.teacher_id"],
            name="lesson_dimension_scores_teacher_id_fkey",
        ),
        sa.PrimaryKeyConstraint(
            "score_state_id",
            name="lesson_dimension_scores_pkey",
        ),
        sa.UniqueConstraint(
            "camp_enrollment_id",
            "lesson_id",
            "dimension",
            name="uq_lesson_dimension_state",
        ),
        schema="public",
    )
    for index_name, columns in (
        (
            "ix_lesson_dimension_scores_camp_enrollment_id",
            ["camp_enrollment_id"],
        ),
        ("ix_lesson_dimension_scores_lesson_id", ["lesson_id"]),
        ("ix_lesson_dimension_scores_teacher_id", ["teacher_id"]),
        (
            "ix_lesson_dimension_score_teacher_lesson",
            ["teacher_id", "lesson_id"],
        ),
    ):
        op.create_index(
            index_name,
            "lesson_dimension_scores",
            columns,
            schema="public",
        )


def _restore_acl() -> None:
    # Remove any grants introduced by cluster default privileges before
    # recreating the exact revision-46 application contract.
    op.execute(
        """
        REVOKE ALL PRIVILEGES ON TABLE
            public.teacher_metric_snapshots,
            public.lesson_facts,
            public.lesson_dimension_scores
        FROM PUBLIC, tit_growth_app;

        DO $legacy_projection_optional_role_revoke$
        DECLARE
            role_name text;
        BEGIN
            FOREACH role_name IN ARRAY ARRAY[
                'tit_teacher_crud',
                'tit_teacher_crud'
            ]::text[] LOOP
                IF EXISTS (
                    SELECT 1 FROM pg_roles WHERE rolname = role_name
                ) THEN
                    EXECUTE format(
                        'REVOKE ALL PRIVILEGES ON TABLE '
                        'public.teacher_metric_snapshots, '
                        'public.lesson_facts, '
                        'public.lesson_dimension_scores FROM %I',
                        role_name
                    );
                END IF;
            END LOOP;
        END
        $legacy_projection_optional_role_revoke$;

        GRANT SELECT, UPDATE ON TABLE
            public.teacher_metric_snapshots
        TO tit_growth_app;

        GRANT SELECT ON TABLE
            public.lesson_facts
        TO tit_growth_app;

        GRANT SELECT, INSERT, DELETE ON TABLE
            public.lesson_dimension_scores
        TO tit_growth_app;
        """
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    _restore_teacher_metric_snapshots()
    _restore_lesson_facts()
    _restore_lesson_dimension_scores()
    _restore_acl()
