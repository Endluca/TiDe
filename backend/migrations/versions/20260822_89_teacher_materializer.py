"""add global DTS v2 TEACHER materialization contract.

Revision ID: 20260822_89_teacher_materializer
Revises: 20260822_88a_runtime_primary_guard
Create Date: 2026-08-22

This revision is additive and does not switch a current read route.  It adds
the proof columns and the single protected command used by the TEACHER Outbox
consumer after both regional aggregate snapshots have been locked.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260822_89_teacher_materializer"
down_revision: Union[str, None] = "20260822_88a_runtime_primary_guard"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


OUTBOX_RUNTIME_ROLE = "tit_growth_app"
_EVIDENCE_VALUES = (
    "CONFIRMED",
    "CONFIRMED_EMPTY",
    "LEGACY_FROZEN",
    "SOURCE_MISSING",
)
_BUSINESS_COLUMNS = (
    "tchr_id",
    "real_name",
    "center_type_id",
    "center_type_desc",
    "bu",
    "status",
    "status_on_date",
    "status_off_date",
    "last_on_date",
    "job_days",
    "job_month",
    "teach_area_type",
    "onboard_date",
    "onboard_30d_end_date",
    "first_open_slot_dt",
    "first_booked_dt",
    "first_completed_dt",
    "total_booked_cnt",
    "peak_booked_cnt",
    "total_completed_cnt",
    "peak_completed_cnt",
    "absent_cnt",
    "late_cnt",
    "early_cnt",
    "anomaly_cnt",
    "perfect_cnt",
    "no_notice_cnt",
    "first_completed_student_cnt",
    "feedback_total_eval_cnt",
    "feedback_praise_cnt",
    "feedback_negative_cnt",
    "feedback_complaint_cnt",
    "feedback_valid_complaint_cnt",
    "feedback_favorite_cnt",
    "feedback_block_cnt",
    "total_slot_cnt",
    "reg_slot_cnt",
    "peak_slot_cnt",
    "slot_days",
    "peak_slot_days",
    "reliability_absent_rate",
    "reliability_late_rate",
    "reliability_early_leave_rate",
    "reliability_late_early_rate",
    "feedback_praise_rate",
    "feedback_negative_rate",
    "feedback_complaint_rate",
    "feedback_favorite_rate",
    "feedback_block_rate",
    "feedback_eval_rate",
    "capacity_avg_completed_per_day",
    "capacity_peak_slot_rate",
    "capacity_key_slot_day_rate",
    "is_cpl_tesol",
    "is_self_introduce",
)
_PROOF_COLUMNS = (
    "first_open_slot_evidence_status",
    "first_booked_evidence_status",
    "first_completed_evidence_status",
    "v2_dom_aggregate_revision",
    "v2_ovs_aggregate_revision",
    "v2_projection_generation",
    "v2_materialized_event_id",
    "v2_materialized_at",
    "v2_row_version",
)


def _preflight() -> None:
    op.execute(
        r"""
        DO $teacher_materializer_preflight$
        DECLARE required_relation text;
        BEGIN
          FOREACH required_relation IN ARRAY ARRAY[
            'teacher_source_wide','teachers','task_assignments','score_entries',
            'domain_aggregate_revisions','dts_pipeline_control',
            'dts_source_row_versions','source_courses',
            'source_course_participations',
            'audit_events'
          ] LOOP
            IF to_regclass('public.' || required_relation) IS NULL THEN
              RAISE EXCEPTION
                'DTS_V2_TEACHER_MATERIALIZER_PREREQUISITE_MISSING:%',
                required_relation;
            END IF;
          END LOOP;
          IF to_regprocedure(
               'public.score_projection_config_snapshot_v2()'
             ) IS NULL
             OR to_regprocedure(
               'public.dts_v2_runtime_primary_guard_v1(text)'
             ) IS NULL
             OR to_regprocedure(
               'public.guard_score_entry_projection_contract_v1()'
             ) IS NULL
             OR to_regrole('tit_growth_app') IS NULL THEN
            RAISE EXCEPTION
              'DTS_V2_TEACHER_MATERIALIZER_PREREQUISITE_MISSING';
          END IF;
          IF to_regprocedure(
               'public.materialize_teacher_source_wide_v2(jsonb,bigint,bigint,bigint,text)'
             ) IS NOT NULL
             OR to_regclass(
               'public.score_entry_idempotency_aliases'
             ) IS NOT NULL THEN
            RAISE EXCEPTION 'DTS_V2_TEACHER_MATERIALIZER_ALREADY_INSTALLED';
          END IF;
        END
        $teacher_materializer_preflight$;

        LOCK TABLE public.teacher_source_wide,public.teachers,
          public.score_entries,public.domain_aggregate_revisions,
          public.source_courses,public.source_course_participations
        IN SHARE ROW EXCLUSIVE MODE;
        """
    )


def _extend_teacher_source_wide() -> None:
    for name in (
        "first_open_slot_evidence_status",
        "first_booked_evidence_status",
        "first_completed_evidence_status",
    ):
        op.add_column(
            "teacher_source_wide",
            sa.Column(name, sa.String(length=32), nullable=True),
            schema="public",
        )
    for name in (
        "v2_dom_aggregate_revision",
        "v2_ovs_aggregate_revision",
        "v2_projection_generation",
        "v2_row_version",
    ):
        op.add_column(
            "teacher_source_wide",
            sa.Column(name, sa.BigInteger(), nullable=True),
            schema="public",
        )
    op.add_column(
        "teacher_source_wide",
        sa.Column("v2_materialized_event_id", sa.String(length=512), nullable=True),
        schema="public",
    )
    op.add_column(
        "teacher_source_wide",
        sa.Column(
            "v2_materialized_at", sa.DateTime(timezone=True), nullable=True
        ),
        schema="public",
    )
    values = ",".join(f"'{value}'" for value in _EVIDENCE_VALUES)
    op.create_check_constraint(
        "ck_teacher_source_wide_v2_materialization_shape",
        "teacher_source_wide",
        "(v2_dom_aggregate_revision IS NULL "
        "AND v2_ovs_aggregate_revision IS NULL "
        "AND v2_projection_generation IS NULL "
        "AND v2_materialized_event_id IS NULL "
        "AND v2_materialized_at IS NULL AND v2_row_version IS NULL "
        "AND first_open_slot_evidence_status IS NULL "
        "AND first_booked_evidence_status IS NULL "
        "AND first_completed_evidence_status IS NULL) OR "
        "(v2_dom_aggregate_revision >= 1 "
        "AND v2_ovs_aggregate_revision >= 1 "
        "AND v2_projection_generation >= 1 "
        "AND v2_materialized_event_id IS NOT NULL "
        "AND btrim(v2_materialized_event_id) <> '' "
        "AND v2_materialized_at IS NOT NULL AND v2_row_version >= 1 "
        f"AND first_open_slot_evidence_status IN ({values}) "
        f"AND first_booked_evidence_status IN ({values}) "
        f"AND first_completed_evidence_status IN ({values}))",
        schema="public",
    )
    op.create_index(
        "ix_teacher_source_wide_v2_generation",
        "teacher_source_wide",
        ["v2_projection_generation", "tchr_id"],
        schema="public",
    )
    op.execute(
        r"""
        COMMENT ON COLUMN
          public.teacher_source_wide.first_open_slot_evidence_status IS
          'Lifetime first-open evidence: confirmed, proven empty, frozen legacy, or missing';
        COMMENT ON COLUMN
          public.teacher_source_wide.first_booked_evidence_status IS
          'Lifetime first-booked evidence: confirmed, proven empty, frozen legacy, or missing';
        COMMENT ON COLUMN
          public.teacher_source_wide.first_completed_evidence_status IS
          'Lifetime first-completion evidence: confirmed, proven empty, frozen legacy, or missing';
        COMMENT ON COLUMN
          public.teacher_source_wide.v2_projection_generation IS
          'Global serving generation; independent of both regional aggregate revisions';

        CREATE FUNCTION public.guard_teacher_source_wide_v2_history()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path=pg_catalog,public
        AS $function$
        BEGIN
          IF OLD.first_open_slot_dt IS NOT NULL AND (
               NEW.first_open_slot_dt IS NULL
               OR NEW.first_open_slot_dt>OLD.first_open_slot_dt
             ) THEN
            RAISE EXCEPTION 'DTS_V2_TEACHER_FIRST_OPEN_REGRESSION'
              USING ERRCODE='23514';
          END IF;
          IF OLD.first_booked_dt IS NOT NULL AND (
               NEW.first_booked_dt IS NULL
               OR NEW.first_booked_dt>OLD.first_booked_dt
             ) THEN
            RAISE EXCEPTION 'DTS_V2_TEACHER_FIRST_BOOKED_REGRESSION'
              USING ERRCODE='23514';
          END IF;
          IF OLD.first_completed_dt IS NOT NULL AND (
               NEW.first_completed_dt IS NULL
               OR NEW.first_completed_dt>OLD.first_completed_dt
             ) THEN
            RAISE EXCEPTION 'DTS_V2_TEACHER_FIRST_COMPLETED_REGRESSION'
              USING ERRCODE='23514';
          END IF;
          IF OLD.v2_row_version IS NOT NULL THEN
            IF NEW.v2_dom_aggregate_revision<OLD.v2_dom_aggregate_revision
               OR NEW.v2_ovs_aggregate_revision<OLD.v2_ovs_aggregate_revision
               OR NEW.v2_projection_generation<OLD.v2_projection_generation THEN
              RAISE EXCEPTION 'DTS_V2_TEACHER_PROJECTION_REGRESSION'
                USING ERRCODE='23514';
            END IF;
            IF NEW.v2_row_version<>OLD.v2_row_version+1 THEN
              RAISE EXCEPTION 'DTS_V2_TEACHER_ROW_VERSION_INVALID'
                USING ERRCODE='23514';
            END IF;
          ELSIF NEW.v2_row_version IS NOT NULL
                AND NEW.v2_row_version<>1 THEN
            RAISE EXCEPTION 'DTS_V2_TEACHER_ROW_VERSION_INVALID'
              USING ERRCODE='23514';
          END IF;
          RETURN NEW;
        END
        $function$;
        REVOKE ALL ON FUNCTION
          public.guard_teacher_source_wide_v2_history() FROM PUBLIC;
        CREATE TRIGGER trg_guard_teacher_source_wide_v2_history
        BEFORE UPDATE ON public.teacher_source_wide
        FOR EACH ROW EXECUTE FUNCTION
          public.guard_teacher_source_wide_v2_history();

        DROP TRIGGER IF EXISTS trg_teacher_source_wide_outbox_v1
          ON public.teacher_source_wide;
        CREATE TRIGGER trg_teacher_source_wide_outbox_v1
        AFTER INSERT OR UPDATE OR DELETE ON public.teacher_source_wide
        FOR EACH ROW
        WHEN (current_setting('tit.dts_v2_materializer',true)
              IS DISTINCT FROM 'teacher')
        EXECUTE FUNCTION public.emit_source_wide_change_v1();
        """
    )


def _add_history_indexes() -> None:
    op.execute(
        r"""
        CREATE INDEX ix_dts_source_row_versions_appoint_teacher_after_v2
          ON public.dts_source_row_versions(
            source_region,source_table,(after_row->>'t_id'),
            source_row_revision
          )
          WHERE source_table IN ('dom_appoint','ovs_appoint')
            AND after_row ? 't_id';
        CREATE INDEX ix_dts_source_row_versions_appoint_teacher_before_v2
          ON public.dts_source_row_versions(
            source_region,source_table,(before_row->>'t_id'),
            source_row_revision
          )
          WHERE source_table IN ('dom_appoint','ovs_appoint')
            AND before_row ? 't_id';
        CREATE INDEX ix_dts_source_row_versions_schedule_teacher_after_v2
          ON public.dts_source_row_versions(
            (after_row->>'teacher_id'),source_row_revision
          )
          WHERE source_region='dom'
            AND source_table='dom_teacher_class_schedule'
            AND after_row ? 'teacher_id';
        CREATE INDEX ix_dts_source_row_versions_schedule_teacher_before_v2
          ON public.dts_source_row_versions(
            (before_row->>'teacher_id'),source_row_revision
          )
          WHERE source_region='dom'
            AND source_table='dom_teacher_class_schedule'
            AND before_row ? 'teacher_id';
        """
    )


def _extend_absence_provenance() -> None:
    for name, column_type in (
        ("absence_source_id", sa.String(length=512)),
        ("absence_source_id_type", sa.String(length=16)),
        ("absence_source_row_revision", sa.BigInteger()),
        ("absence_selected_reason_type", sa.Text()),
    ):
        op.add_column(
            "source_course_participations",
            sa.Column(name, column_type, nullable=True),
            schema="public",
        )
    op.execute(
        r"""
        DO $absence_provenance_preflight$
        BEGIN
          IF EXISTS (
               SELECT 1
               FROM public.source_course_participations
               WHERE absence_reason_detail IS NOT NULL
               LIMIT 1
             ) THEN
            -- Existing normalized absence text cannot prove which canonical
            -- dom_teacher_absent_reason row won the selector.  Stop the
            -- contract install instead of fabricating a source id/revision.
            RAISE EXCEPTION
              'DTS_V2_ABSENCE_PROVENANCE_BACKFILL_REQUIRED';
          END IF;
        END
        $absence_provenance_preflight$;
        """
    )
    op.create_check_constraint(
        "ck_source_course_participation_absence_provenance_v2",
        "source_course_participations",
        "(absence_source_id IS NULL "
        "AND absence_source_id_type IS NULL "
        "AND absence_source_row_revision IS NULL "
        "AND absence_selected_reason_type IS NULL "
        "AND absence_reason_detail IS NULL) OR "
        "(source_region='dom' "
        "AND absence_source_id IS NOT NULL "
        "AND btrim(absence_source_id)<>'' "
        "AND absence_source_id_type IN ('NUMERIC','TEXT') "
        "AND absence_source_row_revision>=1 "
        "AND absence_reason_detail IS NOT DISTINCT FROM "
        "absence_selected_reason_type)",
        schema="public",
    )
    op.create_index(
        "ix_source_course_participation_absence_source_v2",
        "source_course_participations",
        [
            "absence_source_id_type",
            "absence_source_id",
            "absence_source_row_revision",
        ],
        schema="public",
        postgresql_where=sa.text("absence_source_id IS NOT NULL"),
    )


def _extend_course_teacher_region_evidence() -> None:
    """Separate appoint evidence from teacher-profile regional evidence.

    ``source_courses.evidence_status`` remains the single downstream gate,
    while the two inputs are retained independently so a later profile repair
    can clear only the regional conflict without erasing an appoint conflict.
    """

    op.add_column(
        "source_courses",
        sa.Column(
            "appoint_evidence_status",
            sa.String(length=32),
            nullable=True,
        ),
        schema="public",
    )
    op.add_column(
        "source_courses",
        sa.Column(
            "teacher_region_evidence_status",
            sa.String(length=32),
            nullable=True,
        ),
        schema="public",
    )
    op.execute(
        """
        UPDATE public.source_courses
        SET appoint_evidence_status=evidence_status,
            teacher_region_evidence_status='SOURCE_MISSING',
            evidence_status=CASE
              WHEN evidence_status='SOURCE_CONFLICT'
                THEN 'SOURCE_CONFLICT'
              ELSE 'SOURCE_MISSING'
            END
        """
    )
    op.alter_column(
        "source_courses",
        "appoint_evidence_status",
        existing_type=sa.String(length=32),
        nullable=False,
        server_default=sa.text("'SOURCE_MISSING'"),
        schema="public",
    )
    op.alter_column(
        "source_courses",
        "teacher_region_evidence_status",
        existing_type=sa.String(length=32),
        nullable=False,
        server_default=sa.text("'SOURCE_MISSING'"),
        schema="public",
    )
    op.create_check_constraint(
        "ck_source_course_region_evidence_status_v2",
        "source_courses",
        "appoint_evidence_status IN ("
        "'CONFIRMED','SOURCE_MISSING','SOURCE_CONFLICT') "
        "AND teacher_region_evidence_status IN ("
        "'CONFIRMED','SOURCE_MISSING','SOURCE_CONFLICT')",
        schema="public",
    )
    op.create_check_constraint(
        "ck_source_course_combined_evidence_v2",
        "source_courses",
        "evidence_status = CASE "
        "WHEN appoint_evidence_status='SOURCE_CONFLICT' "
        "OR teacher_region_evidence_status='SOURCE_CONFLICT' "
        "THEN 'SOURCE_CONFLICT' "
        "WHEN appoint_evidence_status='SOURCE_MISSING' "
        "OR teacher_region_evidence_status='SOURCE_MISSING' "
        "THEN 'SOURCE_MISSING' ELSE 'CONFIRMED' END",
        schema="public",
    )

    for name, column_type in (
        ("teacher_expected_source_region", sa.String(length=8)),
        ("teacher_region_evidence_status", sa.String(length=32)),
        ("teacher_profile_source_row_revision", sa.BigInteger()),
        ("teacher_profile_source_payload_hash", sa.String(length=64)),
    ):
        op.add_column(
            "source_course_participations",
            sa.Column(
                name,
                column_type,
                nullable=(name != "teacher_region_evidence_status"),
                server_default=(
                    sa.text("'SOURCE_MISSING'")
                    if name == "teacher_region_evidence_status"
                    else None
                ),
            ),
            schema="public",
        )
    op.create_check_constraint(
        "ck_source_course_participation_teacher_region_v2",
        "source_course_participations",
        "teacher_region_evidence_status IN ("
        "'CONFIRMED','SOURCE_MISSING','SOURCE_CONFLICT') AND ("
        "(teacher_region_evidence_status='SOURCE_MISSING' "
        "AND teacher_expected_source_region IS NULL "
        "AND ((teacher_profile_source_row_revision IS NULL "
        "AND teacher_profile_source_payload_hash IS NULL) OR "
        "(teacher_profile_source_row_revision>=1 "
        "AND teacher_profile_source_payload_hash ~ '^[0-9a-f]{64}$'))) OR "
        "(teacher_region_evidence_status IN ("
        "'CONFIRMED','SOURCE_CONFLICT') "
        "AND teacher_expected_source_region IN ('dom','ovs') "
        "AND teacher_profile_source_row_revision>=1 "
        "AND teacher_profile_source_payload_hash ~ '^[0-9a-f]{64}$'))",
        schema="public",
    )


def _create_alias_table() -> None:
    op.create_table(
        "score_entry_idempotency_aliases",
        sa.Column("alias_key", sa.String(length=1024), nullable=False),
        sa.Column("score_entry_id", sa.String(length=128), nullable=False),
        sa.Column("alias_type", sa.String(length=64), nullable=False),
        sa.Column("migration_run_id", sa.String(length=160), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("audit_event_id", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint(
            "alias_key", name="pk_score_entry_idempotency_aliases"
        ),
        sa.UniqueConstraint(
            "score_entry_id", name="uq_score_entry_idempotency_alias_entry"
        ),
        sa.ForeignKeyConstraint(
            ["score_entry_id"],
            ["public.score_entries.score_entry_id"],
            name="fk_score_entry_idempotency_alias_entry",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["audit_event_id"],
            ["public.audit_events.event_id"],
            name="fk_score_entry_idempotency_alias_audit",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "alias_type='LEGACY_CAPACITY_MILESTONE'",
            name="ck_score_entry_idempotency_alias_type",
        ),
        sa.CheckConstraint(
            "btrim(alias_key)<>'' AND btrim(migration_run_id)<>'' "
            "AND btrim(reason)<>''",
            name="ck_score_entry_idempotency_alias_text",
        ),
        schema="public",
        comment=(
            "Cutover-only canonical aliases for immutable legacy capacity "
            "milestone score entries"
        ),
    )
    op.execute(
        r"""
        CREATE FUNCTION public.guard_score_entry_idempotency_alias_immutable_v1()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path=pg_catalog,public
        AS $function$
        BEGIN
          RAISE EXCEPTION 'SCORE_ENTRY_IDEMPOTENCY_ALIAS_IMMUTABLE'
            USING ERRCODE='55000';
        END
        $function$;
        REVOKE ALL ON FUNCTION
          public.guard_score_entry_idempotency_alias_immutable_v1()
        FROM PUBLIC;
        CREATE TRIGGER trg_guard_score_entry_idempotency_alias_immutable_v1
        BEFORE UPDATE OR DELETE
        ON public.score_entry_idempotency_aliases
        FOR EACH ROW EXECUTE FUNCTION
          public.guard_score_entry_idempotency_alias_immutable_v1();

        REVOKE ALL ON TABLE public.score_entry_idempotency_aliases FROM PUBLIC;
        REVOKE ALL ON TABLE public.score_entry_idempotency_aliases
          FROM tit_growth_app,tit_teacher_crud,tit_dts_ingest_runtime,
               tit_growth_app;
        GRANT SELECT ON TABLE public.score_entry_idempotency_aliases
          TO tit_growth_app,tit_teacher_crud,
             tit_growth_app;
        """
    )


def _replace_score_guard() -> None:
    op.execute(
        r"""
        CREATE OR REPLACE FUNCTION public.guard_score_entry_projection_contract_v1()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE capacity_key text;
        DECLARE capacity_id text;
        DECLARE capacity_candidate boolean;
        BEGIN
          IF NEW.projection_origin IS NULL
             OR NEW.projection_origin='LEGACY_EXISTING' THEN
            RAISE EXCEPTION 'SCORE_PROJECTION_ORIGIN_REQUIRED'
              USING ERRCODE='23514';
          END IF;
          IF NEW.projection_origin='V1_COMPAT_LIVE' THEN
            IF (NEW.lesson_id IS NULL AND (
                  NEW.source_region IS NOT NULL
                  OR NEW.source_appoint_id IS NOT NULL
                  OR NEW.participation_seq IS NOT NULL
                )) OR (NEW.lesson_id IS NOT NULL AND (
                  NEW.source_region IS NULL
                  OR NEW.source_region NOT IN ('dom','ovs')
                  OR NEW.source_appoint_id IS DISTINCT FROM NEW.lesson_id
                  OR NOT EXISTS (
                    SELECT 1 FROM public.lesson_source_wide lesson
                    WHERE lesson.source_region=NEW.source_region
                      AND lesson."课程id"=NEW.source_appoint_id
                  )
                )) THEN
              RAISE EXCEPTION 'SCORE_COMPAT_SOURCE_IDENTITY_INVALID'
                USING ERRCODE='23514';
            END IF;
          ELSIF NEW.projection_origin='FIXED_TASK_LIVE' THEN
            IF NEW.source_region IS NOT NULL
               OR NEW.source_appoint_id IS NOT NULL
               OR NEW.participation_seq IS NOT NULL THEN
              RAISE EXCEPTION 'SCORE_FIXED_TASK_SOURCE_IDENTITY_INVALID'
                USING ERRCODE='23514';
            END IF;
          ELSIF NEW.projection_origin='V2_LIVE' THEN
            capacity_candidate :=
              NEW.entry_type='MILESTONE_ACHIEVEMENT'
              OR NEW.dimension='CAPACITY'
              OR NEW.reason_code='CAPACITY_PEAK_SLOT_40_ACHIEVED'
              OR NEW.idempotency_key LIKE
                   'CAPACITY_MILESTONE:CAPACITY_PEAK_SLOT_40:%';
            IF capacity_candidate THEN
              capacity_key := 'CAPACITY_MILESTONE:'
                || 'CAPACITY_PEAK_SLOT_40:' || NEW.teacher_id;
              capacity_id := 'CAPM-' || substr(
                encode(sha256(convert_to(capacity_key,'UTF8')),'hex'),
                1,32
              );
              IF NEW.source_region IS NOT NULL
                 OR NEW.source_appoint_id IS NOT NULL
                 OR NEW.participation_seq IS NOT NULL
                 OR NEW.lesson_id IS NOT NULL
                 OR NEW.materialized_by_run_id IS NOT NULL
                 OR NEW.reversal_of_score_entry_id IS NOT NULL
                 OR NEW.task_assignment_id IS NOT NULL
                 OR NEW.score_entry_id IS DISTINCT FROM capacity_id
                 OR NEW.idempotency_key IS DISTINCT FROM capacity_key
                 OR NEW.dimension<>'CAPACITY'
                 OR NEW.entry_type<>'MILESTONE_ACHIEVEMENT'
                 OR NEW.reason_code<>'CAPACITY_PEAK_SLOT_40_ACHIEVED'
                 OR NEW.delta_score IS DISTINCT FROM 10.0
                 OR NEW.evidence_status<>'CONFIRMED'
                 OR NEW.projection_generation IS NULL
                 OR NEW.projection_generation<1
                 OR NOT EXISTS (
                   SELECT 1 FROM public.teachers teacher
                   WHERE teacher.teacher_id=NEW.teacher_id
                     AND teacher.camp_enrollment_id=NEW.camp_enrollment_id
                 ) THEN
                RAISE EXCEPTION 'SCORE_V2_CAPACITY_IDENTITY_INVALID'
                  USING ERRCODE='23514';
              END IF;
            ELSIF NEW.source_region IS NULL
               OR NEW.source_region NOT IN ('dom','ovs')
               OR NEW.source_appoint_id IS NULL
               OR btrim(NEW.source_appoint_id)=''
               OR NEW.projection_generation IS NULL
               OR NEW.projection_generation<1
               OR NEW.materialized_by_run_id IS NOT NULL THEN
              RAISE EXCEPTION 'SCORE_V2_SOURCE_IDENTITY_INVALID'
                USING ERRCODE='23514';
            END IF;
          ELSIF NEW.projection_origin IN (
            'CUTOVER_CREATED','ROLLBACK_CREATED'
          ) THEN
            IF NEW.source_region IS NULL
               OR NEW.source_region NOT IN ('dom','ovs')
               OR NEW.source_appoint_id IS NULL
               OR btrim(NEW.source_appoint_id)=''
               OR NEW.projection_generation IS NULL
               OR NEW.projection_generation<1
               OR NEW.materialized_by_run_id IS NULL THEN
              RAISE EXCEPTION 'SCORE_V2_SOURCE_IDENTITY_INVALID'
                USING ERRCODE='23514';
            END IF;
          END IF;
          RETURN NEW;
        END
        $function$;
        REVOKE ALL ON FUNCTION
          public.guard_score_entry_projection_contract_v1() FROM PUBLIC;
        """
    )


def _install_materializer() -> None:
    expected_value_keys = sorted((*_BUSINESS_COLUMNS, "online_status", "online_status_evidence_status"))
    expected_sql = ",".join(f"'{value}'" for value in expected_value_keys)
    insert_columns = (*_BUSINESS_COLUMNS, *_PROOF_COLUMNS)
    insert_sql = ",".join(insert_columns)
    incoming_sql = ",".join(f"incoming.{name}" for name in insert_columns)
    mutable = tuple(name for name in insert_columns if name != "tchr_id")
    update_left = ",".join(mutable)
    update_right = ",".join(f"excluded.{name}" for name in mutable)
    distinct_old = ",".join(f"public.teacher_source_wide.{name}" for name in mutable)
    distinct_new = ",".join(f"excluded.{name}" for name in mutable)
    op.execute(
        f"""
        CREATE FUNCTION public.materialize_teacher_source_wide_v2(
          p_payload jsonb,p_dom_revision bigint,p_ovs_revision bigint,
          p_projection_generation bigint,p_event_id text
        )
        RETURNS jsonb
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE incoming public.teacher_source_wide%ROWTYPE;
        DECLARE existing public.teacher_source_wide%ROWTYPE;
        DECLARE control_generation bigint;
        DECLARE aggregate_count integer;
        DECLARE wide_changes integer := 0;
        DECLARE teacher_changes integer := 0;
        DECLARE capacity_changes integer := 0;
        DECLARE direct_entry_id text;
        DECLARE alias_entry_id text;
        DECLARE existing_entry public.score_entries%ROWTYPE;
        DECLARE capacity_key text;
        DECLARE capacity_id text;
        DECLARE policy_version text;
        DECLARE online_status text;
        DECLARE online_evidence text;
        DECLARE value_keys text[];
        DECLARE actor_name text := COALESCE(
          NULLIF(current_setting('role',true),'none'),session_user
        );
        BEGIN
          IF actor_name<>'{OUTBOX_RUNTIME_ROLE}'
             OR jsonb_typeof(p_payload)<>'object'
             OR (SELECT array_agg(key ORDER BY key)
                 FROM jsonb_object_keys(p_payload) AS keys(key))
                  IS DISTINCT FROM ARRAY[
                    'first_date_evidence','metric_evidence',
                    'regional_state_sha256','teacher_id','teacher_id_type',
                    'v','values'
                  ]::text[]
             OR p_payload->>'v'<>'1'
             OR p_payload->>'teacher_id' IS NULL
             OR btrim(p_payload->>'teacher_id')=''
             OR p_payload->>'teacher_id'<>btrim(p_payload->>'teacher_id')
             OR p_payload->>'teacher_id_type' NOT IN ('NUMERIC','TEXT')
             OR jsonb_typeof(p_payload->'values')<>'object'
             OR jsonb_typeof(p_payload->'first_date_evidence')<>'object'
             OR jsonb_typeof(p_payload->'metric_evidence')<>'object'
             OR jsonb_typeof(p_payload->'regional_state_sha256')<>'object'
             OR (SELECT array_agg(key ORDER BY key)
                 FROM jsonb_object_keys(
                   p_payload->'regional_state_sha256'
                 ) AS keys(key))
                  IS DISTINCT FROM ARRAY['dom','ovs']::text[]
             OR p_payload#>>'{{regional_state_sha256,dom}}' !~ '^[0-9a-f]{{64}}$'
             OR p_payload#>>'{{regional_state_sha256,ovs}}' !~ '^[0-9a-f]{{64}}$'
             OR p_dom_revision<1 OR p_ovs_revision<1
             OR p_projection_generation<1
             OR p_event_id IS NULL OR btrim(p_event_id)=''
             OR length(p_event_id)>512 THEN
            RAISE EXCEPTION 'DTS_V2_TEACHER_MATERIALIZE_REQUEST_INVALID'
              USING ERRCODE='22023';
          END IF;
          SELECT array_agg(key ORDER BY key) INTO value_keys
          FROM jsonb_object_keys(p_payload->'values') AS keys(key);
          IF value_keys IS DISTINCT FROM ARRAY[{expected_sql}]::text[] THEN
            RAISE EXCEPTION 'DTS_V2_TEACHER_VALUES_SHAPE_INVALID'
              USING ERRCODE='22023';
          END IF;
          IF p_payload#>>'{{values,tchr_id}}'
               IS DISTINCT FROM p_payload->>'teacher_id'
             OR p_payload#>>'{{first_date_evidence,first_open_slot_dt_evidence_status}}'
                  NOT IN {tuple(_EVIDENCE_VALUES)!r}
             OR p_payload#>>'{{first_date_evidence,first_booked_dt_evidence_status}}'
                  NOT IN {tuple(_EVIDENCE_VALUES)!r}
             OR p_payload#>>'{{first_date_evidence,first_completed_dt_evidence_status}}'
                  NOT IN {tuple(_EVIDENCE_VALUES)!r} THEN
            RAISE EXCEPTION 'DTS_V2_TEACHER_EVIDENCE_SHAPE_INVALID'
              USING ERRCODE='22023';
          END IF;

          control_generation :=
            public.dts_v2_runtime_primary_guard_v1('TEACHER');
          IF control_generation IS NULL
             OR control_generation IS DISTINCT FROM p_projection_generation THEN
            RAISE EXCEPTION 'DTS_V2_TEACHER_PRIMARY_GENERATION_MISMATCH'
              USING ERRCODE='55000';
          END IF;
          PERFORM 1
          FROM public.domain_aggregate_revisions aggregate
          WHERE aggregate.aggregate_type='TEACHER'
            AND aggregate.canonical_key IN (
              jsonb_build_object('source_region','dom','teacher_id',
                                 p_payload->>'teacher_id'),
              jsonb_build_object('source_region','ovs','teacher_id',
                                 p_payload->>'teacher_id')
            )
            AND ((aggregate.canonical_key->>'source_region'='dom'
                  AND aggregate.revision=p_dom_revision
                  AND aggregate.aggregate_state_sha256=
                      p_payload#>>'{{regional_state_sha256,dom}}')
                 OR (aggregate.canonical_key->>'source_region'='ovs'
                     AND aggregate.revision=p_ovs_revision
                     AND aggregate.aggregate_state_sha256=
                         p_payload#>>'{{regional_state_sha256,ovs}}'))
          ORDER BY aggregate.aggregate_id
          FOR SHARE;
          SELECT count(*) INTO aggregate_count
          FROM public.domain_aggregate_revisions aggregate
          WHERE aggregate.aggregate_type='TEACHER'
            AND aggregate.canonical_key IN (
              jsonb_build_object('source_region','dom','teacher_id',
                                 p_payload->>'teacher_id'),
              jsonb_build_object('source_region','ovs','teacher_id',
                                 p_payload->>'teacher_id')
            )
            AND ((aggregate.canonical_key->>'source_region'='dom'
                  AND aggregate.revision=p_dom_revision
                  AND aggregate.aggregate_state_sha256=
                      p_payload#>>'{{regional_state_sha256,dom}}')
                 OR (aggregate.canonical_key->>'source_region'='ovs'
                     AND aggregate.revision=p_ovs_revision
                     AND aggregate.aggregate_state_sha256=
                         p_payload#>>'{{regional_state_sha256,ovs}}'));
          IF aggregate_count<>2 THEN
            RAISE EXCEPTION 'DTS_V2_TEACHER_REGIONAL_VECTOR_STALE'
              USING ERRCODE='40001';
          END IF;

          SELECT * INTO incoming
          FROM jsonb_populate_record(
            NULL::public.teacher_source_wide,p_payload->'values'
          );
          incoming.first_open_slot_evidence_status :=
            p_payload#>>'{{first_date_evidence,first_open_slot_dt_evidence_status}}';
          incoming.first_booked_evidence_status :=
            p_payload#>>'{{first_date_evidence,first_booked_dt_evidence_status}}';
          incoming.first_completed_evidence_status :=
            p_payload#>>'{{first_date_evidence,first_completed_dt_evidence_status}}';
          incoming.v2_dom_aggregate_revision := p_dom_revision;
          incoming.v2_ovs_aggregate_revision := p_ovs_revision;
          incoming.v2_projection_generation := p_projection_generation;
          incoming.v2_materialized_event_id := p_event_id;
          incoming.v2_materialized_at := transaction_timestamp();
          SELECT * INTO existing FROM public.teacher_source_wide
          WHERE tchr_id=incoming.tchr_id FOR UPDATE;
          incoming.v2_row_version := CASE WHEN FOUND
            THEN coalesce(existing.v2_row_version,0)+1 ELSE 1 END;
          IF existing.v2_row_version IS NOT NULL AND (
               p_dom_revision<existing.v2_dom_aggregate_revision
               OR p_ovs_revision<existing.v2_ovs_aggregate_revision
               OR p_projection_generation<existing.v2_projection_generation
             ) THEN
            RAISE EXCEPTION 'DTS_V2_TEACHER_PROJECTION_REGRESSION'
              USING ERRCODE='40001';
          END IF;
          IF existing.v2_row_version IS NOT NULL
             AND p_dom_revision=existing.v2_dom_aggregate_revision
             AND p_ovs_revision=existing.v2_ovs_aggregate_revision
             AND p_projection_generation=existing.v2_projection_generation THEN
            IF (to_jsonb(incoming)
                  -'v2_materialized_event_id'
                  -'v2_materialized_at'-'v2_row_version')
                 IS DISTINCT FROM
               (to_jsonb(existing)
                  -'v2_materialized_event_id'
                  -'v2_materialized_at'-'v2_row_version') THEN
              RAISE EXCEPTION
                'DTS_V2_TEACHER_REGIONAL_VECTOR_NONDETERMINISTIC'
                USING ERRCODE='23514';
            END IF;
            -- A replay/superseded regional event for an already materialized
            -- combined vector is a true no-op.  It must not churn the serving
            -- event marker or row version.
            incoming.v2_materialized_event_id :=
              existing.v2_materialized_event_id;
            incoming.v2_materialized_at := existing.v2_materialized_at;
            incoming.v2_row_version := existing.v2_row_version;
          END IF;

          PERFORM set_config('tit.dts_v2_materializer','teacher',true);
          INSERT INTO public.teacher_source_wide({insert_sql})
          SELECT {incoming_sql}
          ON CONFLICT (tchr_id) DO UPDATE SET
            ({update_left})=ROW({update_right})
          WHERE ROW({distinct_old}) IS DISTINCT FROM ROW({distinct_new});
          GET DIAGNOSTICS wide_changes=ROW_COUNT;

          online_status := p_payload#>>'{{values,online_status}}';
          online_evidence :=
            p_payload#>>'{{values,online_status_evidence_status}}';
          IF online_status IS NOT NULL
             AND online_status NOT IN ('NEW','EXISTING','LEFT','BLOCKED') THEN
            RAISE EXCEPTION 'DTS_V2_TEACHER_ONLINE_STATUS_INVALID'
              USING ERRCODE='23514';
          END IF;
          INSERT INTO public.teachers(
            teacher_id,camp_enrollment_id,name,country,timezone,camp_day,
            online_status,graduation_state,gold_qualified,total_score,
            graduation_threshold,data_mode,source_snapshot_label,payload,
            created_at,updated_at
          ) VALUES (
            incoming.tchr_id,'CAMP:'||incoming.tchr_id,
            coalesce(nullif(incoming.real_name,''),incoming.tchr_id),NULL,'UTC',
            least(greatest(coalesce(incoming.job_days,0),0),30),
            online_status,'IN_CAMP',false,0,0,'REAL','SOURCE_WIDE_V2',
            jsonb_build_object(
              'graduation_state','IN_CAMP','source_wide_v2',
              jsonb_build_object(
                'online_status_evidence_status',online_evidence,
                'dom_aggregate_revision',p_dom_revision,
                'ovs_aggregate_revision',p_ovs_revision,
                'projection_generation',p_projection_generation
              )
            ),transaction_timestamp(),transaction_timestamp()
          ) ON CONFLICT (teacher_id) DO UPDATE SET
            name=excluded.name,camp_day=excluded.camp_day,
            online_status=excluded.online_status,data_mode='REAL',
            source_snapshot_label='SOURCE_WIDE_V2',
            payload=public.teachers.payload || jsonb_build_object(
              'source_wide_v2',excluded.payload->'source_wide_v2'
            ),updated_at=transaction_timestamp()
          WHERE ROW(
            public.teachers.name,public.teachers.camp_day,
            public.teachers.online_status,public.teachers.data_mode,
            public.teachers.source_snapshot_label,
            public.teachers.payload->'source_wide_v2'
          ) IS DISTINCT FROM ROW(
            excluded.name,excluded.camp_day,excluded.online_status,'REAL',
            'SOURCE_WIDE_V2',excluded.payload->'source_wide_v2'
          );
          GET DIAGNOSTICS teacher_changes=ROW_COUNT;

          capacity_key := 'CAPACITY_MILESTONE:'
            || 'CAPACITY_PEAK_SLOT_40:' || incoming.tchr_id;
          capacity_id := 'CAPM-' || substr(
            encode(sha256(convert_to(capacity_key,'UTF8')),'hex'),1,32
          );
          SELECT score_entry_id INTO direct_entry_id
          FROM public.score_entries
          WHERE idempotency_key=capacity_key FOR SHARE;
          SELECT score_entry_id INTO alias_entry_id
          FROM public.score_entry_idempotency_aliases
          WHERE alias_key=capacity_key FOR SHARE;
          IF direct_entry_id IS NOT NULL AND alias_entry_id IS NOT NULL
             AND direct_entry_id<>alias_entry_id THEN
            RAISE EXCEPTION 'CAPACITY_MILESTONE_DEDUPE_CONFLICT'
              USING ERRCODE='23505';
          END IF;
          IF coalesce(direct_entry_id,alias_entry_id) IS NOT NULL THEN
            SELECT * INTO existing_entry FROM public.score_entries
            WHERE score_entry_id=coalesce(direct_entry_id,alias_entry_id)
            FOR SHARE;
            IF NOT FOUND
               OR existing_entry.teacher_id<>incoming.tchr_id
               OR existing_entry.camp_enrollment_id<>'CAMP:'||incoming.tchr_id
               OR existing_entry.dimension<>'CAPACITY'
               OR existing_entry.reason_code<>'CAPACITY_PEAK_SLOT_40_ACHIEVED'
               OR existing_entry.delta_score IS DISTINCT FROM 10.0
               OR EXISTS (
                 SELECT 1 FROM public.score_entries reversal
                 WHERE reversal.reversal_of_score_entry_id=
                       existing_entry.score_entry_id
               ) THEN
              RAISE EXCEPTION 'CAPACITY_MILESTONE_DEDUPE_CONFLICT'
                USING ERRCODE='23505';
            END IF;
          ELSIF p_payload#>>'{{metric_evidence,peak_slot_cnt}}'='CONFIRMED'
                AND incoming.peak_slot_cnt>=40 THEN
            policy_version :=
              public.score_projection_config_snapshot_v2()->>'policy_version';
            INSERT INTO public.score_entries(
              score_entry_id,camp_enrollment_id,lesson_id,source_region,
              source_appoint_id,participation_seq,teacher_id,dimension,
              entry_type,delta_score,reason_code,evidence_status,
              score_rule_version,occurred_at,recorded_at,
              reversal_of_score_entry_id,task_assignment_id,
              projection_origin,materialized_by_run_id,
              projection_generation,idempotency_key,payload
            ) VALUES (
              capacity_id,'CAMP:'||incoming.tchr_id,NULL,NULL,NULL,NULL,
              incoming.tchr_id,'CAPACITY','MILESTONE_ACHIEVEMENT',10,
              'CAPACITY_PEAK_SLOT_40_ACHIEVED','CONFIRMED',policy_version,
              transaction_timestamp(),transaction_timestamp(),NULL,NULL,
              'V2_LIVE',NULL,p_projection_generation,capacity_key,
              jsonb_build_object(
                'component_code','CAPACITY_PEAK_SLOT_40',
                'milestone_id','CAPACITY_PEAK_SLOT_40',
                'metric','teacher_source_wide.peak_slot_cnt',
                'operator','GTE','threshold',40,
                'observed_value',incoming.peak_slot_cnt,
                'source_mode','DERIVED_REAL',
                'settlement_mode','FIRST_ACHIEVEMENT_LOCKED'
              )
            );
            GET DIAGNOSTICS capacity_changes=ROW_COUNT;
          END IF;
          RETURN jsonb_build_object(
            'teacher_source_changes',wide_changes,
            'teacher_identity_changes',teacher_changes,
            'capacity_score_entries',capacity_changes
          );
        EXCEPTION
          WHEN invalid_text_representation OR numeric_value_out_of_range
            OR datetime_field_overflow THEN
            RAISE EXCEPTION 'DTS_V2_TEACHER_VALUE_TYPE_INVALID'
              USING ERRCODE='22023';
        END
        $function$;
        REVOKE ALL ON FUNCTION
          public.materialize_teacher_source_wide_v2(
            jsonb,bigint,bigint,bigint,text
          ) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION
          public.materialize_teacher_source_wide_v2(
            jsonb,bigint,bigint,bigint,text
          ) TO {OUTBOX_RUNTIME_ROLE};
        """
    )


def _install_teacher_dirty_closure() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.enqueue_peer_teacher_dirty_from_source_revision_v2(
          p_source_region text,p_source_table text,p_source_key text,
          p_source_row_revision bigint,p_key_type text,
          p_key_part_1 text,p_key_part_2 text
        )
        RETURNS jsonb
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE version public.dts_source_row_versions%ROWTYPE;
        DECLARE current_row public.dts_source_rows%ROWTYPE;
        DECLARE input_identity jsonb;
        DECLARE input_fingerprint text;
        BEGIN
          SELECT * INTO version
          FROM public.dts_source_row_versions
          WHERE source_region=p_source_region
            AND source_table=p_source_table
            AND source_key=p_source_key
            AND source_row_revision=p_source_row_revision
          FOR KEY SHARE;
          SELECT * INTO current_row
          FROM public.dts_source_rows
          WHERE source_region=p_source_region
            AND source_table=p_source_table
            AND source_key=p_source_key
          FOR KEY SHARE;
          IF version.source_region IS NULL OR current_row.source_region IS NULL
             OR p_source_region<>'dom' OR p_source_table<>'dom_teacher'
             OR p_key_type<>'TEACHER' OR p_key_part_1<>p_source_key
             OR p_key_part_2<>''
             OR current_row.provenance_state<>'V2_CONFIRMED'
             OR current_row.source_row_revision<>p_source_row_revision
             OR current_row.source_payload_hash<>
                  version.protected_source_row_hash THEN
            RAISE EXCEPTION 'DIRTY_PEER_TEACHER_AUTHORITY_DENIED'
              USING ERRCODE='42501';
          END IF;
          input_identity := jsonb_build_object(
            'source_region',p_source_region,
            'source_table',p_source_table,'source_key',p_source_key
          );
          input_fingerprint := public.dts_canonical_json_sha256_v1(
            jsonb_build_object(
              'protocol','dirty-source-v1','identity',input_identity,
              'revision',p_source_row_revision,
              'version_kind',version.version_kind,
              'operation',version.operation,
              'is_deleted',current_row.is_deleted,
              'protected_source_row_hash',version.protected_source_row_hash
            )
          );
          RETURN public._upsert_dts_dirty_key_input_v2(
            'ovs','TEACHER',p_key_part_1,'','SOURCE_REVISION',
            input_identity,p_source_row_revision,input_fingerprint
          );
        END
        $function$;
        REVOKE ALL ON FUNCTION
          public.enqueue_peer_teacher_dirty_from_source_revision_v2(
            text,text,text,bigint,text,text,text
          ) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION
          public.enqueue_peer_teacher_dirty_from_source_revision_v2(
            text,text,text,bigint,text,text,text
          ) TO tit_dts_ingest_runtime;

        CREATE FUNCTION public.fanout_dom_teacher_scope_peer_v2()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE affected_teacher text;
        DECLARE input_identity jsonb;
        DECLARE input_fingerprint text;
        DECLARE scope_revision bigint;
        BEGIN
          IF NEW.aggregate_type<>'SOURCE_SCOPE'
             OR NEW.canonical_key->>'source_region'<>'dom'
             OR NEW.canonical_key->>'source_table'<>'dom_teacher' THEN
            RETURN NEW;
          END IF;
          input_identity := NEW.canonical_key;
          scope_revision := (NEW.aggregate_state->>'scope_row_version')::bigint;
          input_fingerprint := public.dts_canonical_json_sha256_v1(
            jsonb_build_object(
              'protocol','dirty-scope-v1','identity',input_identity,
              'scope_row_version',scope_revision,
              'state',NEW.aggregate_state->>'state',
              'active_snapshot_id',
                NEW.aggregate_state->>'active_snapshot_id',
              'active_epoch_id',NEW.aggregate_state->>'active_epoch_id',
              'active_fence_hash',
                NEW.aggregate_state->>'active_fence_hash'
            )
          );
          FOR affected_teacher IN
            WITH dependency_documents AS (
              SELECT dependency_keys
              FROM public.dts_source_snapshot_rows
              WHERE source_region='dom' AND source_table='dom_teacher'
                AND scope_kind=NEW.canonical_key->>'scope_kind'
                AND scope_level=NEW.canonical_key->>'scope_level'
                AND scope_key=NEW.canonical_key->>'scope_key'
              UNION ALL
              SELECT dependency_keys
              FROM public.dts_source_scope_memberships
              WHERE source_region='dom' AND source_table='dom_teacher'
                AND scope_kind=NEW.canonical_key->>'scope_kind'
                AND scope_level=NEW.canonical_key->>'scope_level'
                AND scope_key=NEW.canonical_key->>'scope_key'
            ), teacher_ids AS (
              SELECT value #>> '{}' AS teacher_id
              FROM dependency_documents,
                   jsonb_array_elements(
                     coalesce(dependency_keys->'teacher_ids','[]'::jsonb)
                   ) item(value)
              UNION
              SELECT NEW.canonical_key->>'scope_key'
              WHERE NEW.canonical_key->>'scope_level'='TEACHER'
            )
            SELECT DISTINCT teacher_id FROM teacher_ids
            WHERE teacher_id IS NOT NULL AND btrim(teacher_id)<>''
            ORDER BY teacher_id
          LOOP
            PERFORM public._upsert_dts_dirty_key_input_v2(
              'ovs','TEACHER',affected_teacher,'','SCOPE_REVISION',
              input_identity,scope_revision,input_fingerprint
            );
          END LOOP;
          RETURN NEW;
        EXCEPTION
          WHEN invalid_text_representation OR numeric_value_out_of_range THEN
            RAISE EXCEPTION 'DIRTY_PEER_TEACHER_SCOPE_INVALID'
              USING ERRCODE='23514';
        END
        $function$;
        REVOKE ALL ON FUNCTION
          public.fanout_dom_teacher_scope_peer_v2() FROM PUBLIC;
        CREATE TRIGGER trg_fanout_dom_teacher_scope_peer_v2
        AFTER INSERT OR UPDATE ON public.domain_aggregate_revisions
        FOR EACH ROW
        WHEN (NEW.aggregate_type='SOURCE_SCOPE')
        EXECUTE FUNCTION public.fanout_dom_teacher_scope_peer_v2();
        """
    )


def _verify_acl() -> None:
    op.execute(
        f"""
        REVOKE ALL PRIVILEGES ON TABLE public.teacher_source_wide
          FROM {OUTBOX_RUNTIME_ROLE};
        GRANT SELECT ON TABLE public.teacher_source_wide
          TO {OUTBOX_RUNTIME_ROLE};
        DO $teacher_materializer_acl$
        BEGIN
          IF NOT has_table_privilege(
               '{OUTBOX_RUNTIME_ROLE}',
               'public.teacher_source_wide','SELECT'
             ) OR has_table_privilege(
               '{OUTBOX_RUNTIME_ROLE}',
               'public.teacher_source_wide','INSERT'
             ) OR has_table_privilege(
               '{OUTBOX_RUNTIME_ROLE}',
               'public.teacher_source_wide','UPDATE'
             ) OR NOT has_function_privilege(
               '{OUTBOX_RUNTIME_ROLE}',
               'public.materialize_teacher_source_wide_v2(jsonb,bigint,bigint,bigint,text)',
               'EXECUTE'
             ) THEN
            RAISE EXCEPTION 'DTS_V2_TEACHER_MATERIALIZER_ACL_INVALID';
          END IF;
        END
        $teacher_materializer_acl$;
        """
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    _preflight()
    _extend_teacher_source_wide()
    _add_history_indexes()
    _extend_absence_provenance()
    _extend_course_teacher_region_evidence()
    _create_alias_table()
    _replace_score_guard()
    _install_teacher_dirty_closure()
    _install_materializer()
    _verify_acl()


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        r"""
        DO $teacher_materializer_downgrade_guard$
        BEGIN
          IF EXISTS (
               SELECT 1 FROM public.teacher_source_wide
               WHERE v2_row_version IS NOT NULL LIMIT 1
             ) OR EXISTS (
               SELECT 1 FROM public.score_entry_idempotency_aliases LIMIT 1
             ) OR EXISTS (
               SELECT 1 FROM public.score_entries
               WHERE projection_origin='V2_LIVE'
                 AND reason_code='CAPACITY_PEAK_SLOT_40_ACHIEVED'
               LIMIT 1
             ) OR EXISTS (
               SELECT 1 FROM public.source_course_participations
               WHERE absence_source_id IS NOT NULL
                  OR teacher_profile_source_row_revision IS NOT NULL
                  OR teacher_region_evidence_status<>'SOURCE_MISSING'
               LIMIT 1
             ) THEN
            RAISE EXCEPTION 'DTS_V2_TEACHER_MATERIALIZER_FACTS_PRESENT';
          END IF;
        END
        $teacher_materializer_downgrade_guard$;

        REVOKE ALL ON FUNCTION
          public.materialize_teacher_source_wide_v2(
            jsonb,bigint,bigint,bigint,text
          ) FROM PUBLIC,tit_growth_app;
        DROP FUNCTION public.materialize_teacher_source_wide_v2(
          jsonb,bigint,bigint,bigint,text
        );

        DROP TRIGGER IF EXISTS trg_fanout_dom_teacher_scope_peer_v2
          ON public.domain_aggregate_revisions;
        DROP FUNCTION IF EXISTS public.fanout_dom_teacher_scope_peer_v2();
        REVOKE ALL ON FUNCTION
          public.enqueue_peer_teacher_dirty_from_source_revision_v2(
            text,text,text,bigint,text,text,text
          ) FROM PUBLIC,tit_dts_ingest_runtime;
        DROP FUNCTION
          public.enqueue_peer_teacher_dirty_from_source_revision_v2(
            text,text,text,bigint,text,text,text
          );

        DROP TRIGGER IF EXISTS trg_teacher_source_wide_outbox_v1
          ON public.teacher_source_wide;
        CREATE TRIGGER trg_teacher_source_wide_outbox_v1
        AFTER INSERT OR UPDATE OR DELETE ON public.teacher_source_wide
        FOR EACH ROW EXECUTE FUNCTION public.emit_source_wide_change_v1();

        DROP TRIGGER IF EXISTS trg_guard_teacher_source_wide_v2_history
          ON public.teacher_source_wide;
        DROP FUNCTION public.guard_teacher_source_wide_v2_history();
        REVOKE SELECT ON TABLE public.teacher_source_wide
          FROM tit_growth_app;

        CREATE OR REPLACE FUNCTION public.guard_score_entry_projection_contract_v1()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path=pg_catalog,public
        AS $function$
        BEGIN
          IF NEW.projection_origin IS NULL
             OR NEW.projection_origin='LEGACY_EXISTING' THEN
            RAISE EXCEPTION 'SCORE_PROJECTION_ORIGIN_REQUIRED'
              USING ERRCODE='23514';
          END IF;
          IF NEW.projection_origin='V1_COMPAT_LIVE' THEN
            IF (NEW.lesson_id IS NULL AND (
                  NEW.source_region IS NOT NULL
                  OR NEW.source_appoint_id IS NOT NULL
                  OR NEW.participation_seq IS NOT NULL
                )) OR (NEW.lesson_id IS NOT NULL AND (
                  NEW.source_region IS NULL
                  OR NEW.source_region NOT IN ('dom','ovs')
                  OR NEW.source_appoint_id IS DISTINCT FROM NEW.lesson_id
                  OR NOT EXISTS (
                    SELECT 1 FROM public.lesson_source_wide lesson
                    WHERE lesson.source_region=NEW.source_region
                      AND lesson."课程id"=NEW.source_appoint_id
                  )
                )) THEN
              RAISE EXCEPTION 'SCORE_COMPAT_SOURCE_IDENTITY_INVALID'
                USING ERRCODE='23514';
            END IF;
          ELSIF NEW.projection_origin='FIXED_TASK_LIVE' THEN
            IF NEW.source_region IS NOT NULL
               OR NEW.source_appoint_id IS NOT NULL
               OR NEW.participation_seq IS NOT NULL THEN
              RAISE EXCEPTION 'SCORE_FIXED_TASK_SOURCE_IDENTITY_INVALID'
                USING ERRCODE='23514';
            END IF;
          ELSIF NEW.projection_origin IN (
            'CUTOVER_CREATED','V2_LIVE','ROLLBACK_CREATED'
          ) THEN
            IF NEW.source_region IS NULL
               OR NEW.source_region NOT IN ('dom','ovs')
               OR NEW.source_appoint_id IS NULL
               OR btrim(NEW.source_appoint_id)=''
               OR NEW.projection_generation IS NULL
               OR NEW.projection_generation<1
               OR ((NEW.projection_origin IN (
                    'CUTOVER_CREATED','ROLLBACK_CREATED'
                   )) <> (NEW.materialized_by_run_id IS NOT NULL)) THEN
              RAISE EXCEPTION 'SCORE_V2_SOURCE_IDENTITY_INVALID'
                USING ERRCODE='23514';
            END IF;
          END IF;
          RETURN NEW;
        END
        $function$;
        REVOKE ALL ON FUNCTION
          public.guard_score_entry_projection_contract_v1() FROM PUBLIC;

        DROP TRIGGER IF EXISTS
          trg_guard_score_entry_idempotency_alias_immutable_v1
          ON public.score_entry_idempotency_aliases;
        DROP FUNCTION
          public.guard_score_entry_idempotency_alias_immutable_v1();
        """
    )
    op.drop_table("score_entry_idempotency_aliases", schema="public")
    op.drop_constraint(
        "ck_source_course_participation_teacher_region_v2",
        "source_course_participations",
        type_="check",
        schema="public",
    )
    for name in (
        "teacher_profile_source_payload_hash",
        "teacher_profile_source_row_revision",
        "teacher_region_evidence_status",
        "teacher_expected_source_region",
    ):
        op.drop_column(
            "source_course_participations", name, schema="public"
        )
    op.drop_constraint(
        "ck_source_course_combined_evidence_v2",
        "source_courses",
        type_="check",
        schema="public",
    )
    op.drop_constraint(
        "ck_source_course_region_evidence_status_v2",
        "source_courses",
        type_="check",
        schema="public",
    )
    op.execute(
        """
        UPDATE public.source_courses
        SET evidence_status=appoint_evidence_status
        """
    )
    op.drop_column(
        "source_courses", "teacher_region_evidence_status", schema="public"
    )
    op.drop_column(
        "source_courses", "appoint_evidence_status", schema="public"
    )
    op.drop_index(
        "ix_source_course_participation_absence_source_v2",
        table_name="source_course_participations",
        schema="public",
    )
    op.drop_constraint(
        "ck_source_course_participation_absence_provenance_v2",
        "source_course_participations",
        type_="check",
        schema="public",
    )
    for name in (
        "absence_selected_reason_type",
        "absence_source_row_revision",
        "absence_source_id_type",
        "absence_source_id",
    ):
        op.drop_column(
            "source_course_participations", name, schema="public"
        )
    for name in (
        "ix_dts_source_row_versions_schedule_teacher_before_v2",
        "ix_dts_source_row_versions_schedule_teacher_after_v2",
        "ix_dts_source_row_versions_appoint_teacher_before_v2",
        "ix_dts_source_row_versions_appoint_teacher_after_v2",
    ):
        op.drop_index(name, table_name="dts_source_row_versions", schema="public")
    op.drop_index(
        "ix_teacher_source_wide_v2_generation",
        table_name="teacher_source_wide",
        schema="public",
    )
    op.drop_constraint(
        "ck_teacher_source_wide_v2_materialization_shape",
        "teacher_source_wide",
        type_="check",
        schema="public",
    )
    for name in reversed(_PROOF_COLUMNS):
        op.drop_column("teacher_source_wide", name, schema="public")
