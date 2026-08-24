"""add inert v2 per-course component settlement ownership guards.

Revision ID: 20260822_76_lesson_score_components
Revises: 20260822_75_camp_state_contract
Create Date: 2026-08-22

This revision is deliberately schema-only.  It records the one current award
per regional course/component and binds every current award to the frozen
completion teacher, the COMPLETION participation, and semantically matching
append-only score entries.  It also adds nullable v2 ownership to the legacy
lesson aggregate without guessing ownership for existing rows.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260822_76_lesson_score_components"
down_revision: Union[str, None] = "20260822_75_camp_state_contract"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SHADOW_SCHEMA_ONLY = True
RUNTIME_ROUTE_ACTIVATED = False
DEFERRED_COMPLETION_OWNERSHIP_GUARD_IMPLEMENTED = True
SEMANTIC_SCORE_ENTRY_GUARD_IMPLEMENTED = True


def _ensure_revision_capacity() -> None:
    # This mandated revision id is 35 characters while Alembic's historical
    # version table defaults to varchar(32).  Widen inside the same migration
    # transaction before Alembic writes the new head.  Downgrade deliberately
    # keeps the harmless wider metadata column: it cannot be narrowed while
    # the 35-character current revision is still stored.
    op.execute(
        """
        DO $lesson_component_revision_capacity$
        DECLARE
            version_length integer;
        BEGIN
            IF to_regclass('public.alembic_version') IS NULL THEN
                RAISE EXCEPTION
                    'DTS_V2_LESSON_COMPONENT_ALEMBIC_VERSION_MISSING';
            END IF;
            SELECT character_maximum_length
            INTO version_length
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'alembic_version'
              AND column_name = 'version_num';
            IF version_length IS NOT NULL AND version_length < 64 THEN
                ALTER TABLE public.alembic_version
                ALTER COLUMN version_num TYPE varchar(64);
            END IF;
        END
        $lesson_component_revision_capacity$;
        """
    )


def _assert_upgrade_preconditions() -> None:
    op.execute(
        """
        DO $lesson_component_preflight$
        DECLARE
            dependency_name text;
        BEGIN
            FOREACH dependency_name IN ARRAY ARRAY[
                'source_courses',
                'source_course_participations',
                'score_entries',
                'lesson_score_results'
            ] LOOP
                IF to_regclass('public.' || dependency_name) IS NULL THEN
                    RAISE EXCEPTION
                        'DTS_V2_LESSON_COMPONENT_DEPENDENCY_MISSING:%',
                        dependency_name;
                END IF;
            END LOOP;

            IF to_regclass(
                    'public.lesson_score_component_settlements'
                ) IS NOT NULL
               OR to_regprocedure(
                    'public.dts_v2_lesson_score_entry_valid(text,text,text,integer,text,bigint,text,text,numeric,text,text,text)'
                  ) IS NOT NULL
               OR to_regprocedure(
                    'public.dts_v2_assert_lesson_score_course(text,text)'
                  ) IS NOT NULL
               OR EXISTS (
                    SELECT 1
                    FROM pg_catalog.pg_attribute
                    WHERE attrelid =
                            'public.lesson_score_results'::regclass
                      AND attname IN (
                            'v2_source_region',
                            'v2_source_appoint_id',
                            'v2_completion_participation_seq'
                      )
                      AND NOT attisdropped
               ) THEN
                RAISE EXCEPTION
                    'DTS_V2_LESSON_COMPONENT_SCHEMA_ALREADY_PRESENT';
            END IF;
        END
        $lesson_component_preflight$;
        """
    )


def _create_settlement_table() -> None:
    op.create_table(
        "lesson_score_component_settlements",
        sa.Column("source_region", sa.String(length=8), nullable=False),
        sa.Column("source_appoint_id", sa.String(length=512), nullable=False),
        sa.Column(
            "completion_participation_seq", sa.Integer(), nullable=False
        ),
        sa.Column("component_code", sa.String(length=40), nullable=False),
        sa.Column("teacher_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("award_generation", sa.BigInteger(), nullable=False),
        sa.Column(
            "component_score",
            sa.Numeric(precision=10, scale=2),
            nullable=False,
        ),
        sa.Column("score_rule_version", sa.String(length=64), nullable=False),
        sa.Column("evidence_fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "current_award_score_entry_id", sa.String(length=128), nullable=True
        ),
        sa.Column(
            "last_reversal_score_entry_id", sa.String(length=128), nullable=True
        ),
        sa.Column("materialization_origin", sa.String(length=32), nullable=False),
        sa.Column("materialized_by_run_id", sa.String(length=160), nullable=True),
        sa.Column(
            "award_projection_generation", sa.BigInteger(), nullable=False
        ),
        sa.Column(
            "row_version",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("awarded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reversed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.PrimaryKeyConstraint(
            "source_region",
            "source_appoint_id",
            "completion_participation_seq",
            "component_code",
            name="pk_lesson_score_component_settlements",
        ),
        sa.ForeignKeyConstraint(
            [
                "source_region",
                "source_appoint_id",
                "completion_participation_seq",
            ],
            [
                "public.source_course_participations.source_region",
                "public.source_course_participations.source_appoint_id",
                "public.source_course_participations.participation_seq",
            ],
            name="fk_lesson_component_settlement_participation",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["current_award_score_entry_id"],
            ["public.score_entries.score_entry_id"],
            name="fk_lesson_component_current_award_entry",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["last_reversal_score_entry_id"],
            ["public.score_entries.score_entry_id"],
            name="fk_lesson_component_last_reversal_entry",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.CheckConstraint(
            "source_region IN ('dom', 'ovs')",
            name="ck_lesson_component_region",
        ),
        sa.CheckConstraint(
            "component_code IN ("
            "'FEEDBACK_PRAISE', 'PERFECT_COMPLETED', "
            "'PEAK_COMPLETED', 'CLASS_QUALITY_HARDWARE')",
            name="ck_lesson_component_code",
        ),
        sa.CheckConstraint(
            "completion_participation_seq >= 1 "
            "AND award_generation >= 1 "
            "AND component_score > 0 "
            "AND award_projection_generation >= 0 "
            "AND row_version >= 1 "
            "AND btrim(teacher_id) <> '' "
            "AND btrim(score_rule_version) <> '' "
            "AND evidence_fingerprint ~ '^[0-9a-f]{64}$' "
            "AND (current_award_score_entry_id IS NULL "
            "OR last_reversal_score_entry_id IS NULL "
            "OR current_award_score_entry_id <> "
            "last_reversal_score_entry_id)",
            name="ck_lesson_component_values",
        ),
        sa.CheckConstraint(
            "(status = 'AWARDED' "
            "AND current_award_score_entry_id IS NOT NULL "
            "AND reversed_at IS NULL "
            "AND ((award_generation = 1 "
            "AND last_reversal_score_entry_id IS NULL) "
            "OR (award_generation > 1 "
            "AND last_reversal_score_entry_id IS NOT NULL))) "
            "OR (status = 'REVERSED' "
            "AND current_award_score_entry_id IS NULL "
            "AND last_reversal_score_entry_id IS NOT NULL "
            "AND reversed_at IS NOT NULL)",
            name="ck_lesson_component_status_shape",
        ),
        sa.CheckConstraint(
            "materialization_origin IN ("
            "'LEGACY_REUSED', 'CUTOVER_CREATED', 'V2_LIVE') "
            "AND ((materialization_origin = 'CUTOVER_CREATED' "
            "AND materialized_by_run_id IS NOT NULL "
            "AND btrim(materialized_by_run_id) <> '') "
            "OR (materialization_origin <> 'CUTOVER_CREATED' "
            "AND materialized_by_run_id IS NULL))",
            name="ck_lesson_component_origin",
        ),
        schema="public",
    )
    op.create_index(
        "uq_lesson_component_one_current_award",
        "lesson_score_component_settlements",
        ["source_region", "source_appoint_id", "component_code"],
        unique=True,
        schema="public",
        postgresql_where=sa.text("status = 'AWARDED'"),
    )
    op.create_index(
        "ix_lesson_component_teacher",
        "lesson_score_component_settlements",
        ["source_region", "teacher_id", "source_appoint_id"],
        unique=False,
        schema="public",
    )


def _extend_lesson_score_results() -> None:
    op.add_column(
        "lesson_score_results",
        sa.Column("v2_source_region", sa.String(length=8), nullable=True),
        schema="public",
    )
    op.add_column(
        "lesson_score_results",
        sa.Column("v2_source_appoint_id", sa.String(length=512), nullable=True),
        schema="public",
    )
    op.add_column(
        "lesson_score_results",
        sa.Column(
            "v2_completion_participation_seq", sa.Integer(), nullable=True
        ),
        schema="public",
    )
    op.create_check_constraint(
        "ck_lesson_score_result_v2_ownership",
        "lesson_score_results",
        "(v2_source_region IS NULL "
        "AND v2_source_appoint_id IS NULL "
        "AND v2_completion_participation_seq IS NULL) "
        "OR (v2_source_region IN ('dom', 'ovs') "
        "AND v2_source_appoint_id IS NOT NULL "
        "AND v2_completion_participation_seq >= 1)",
        schema="public",
    )
    op.create_foreign_key(
        "fk_lesson_score_result_v2_course",
        "lesson_score_results",
        "source_courses",
        ["v2_source_region", "v2_source_appoint_id"],
        ["source_region", "source_appoint_id"],
        source_schema="public",
        referent_schema="public",
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_foreign_key(
        "fk_lesson_score_result_v2_participation",
        "lesson_score_results",
        "source_course_participations",
        [
            "v2_source_region",
            "v2_source_appoint_id",
            "v2_completion_participation_seq",
        ],
        ["source_region", "source_appoint_id", "participation_seq"],
        source_schema="public",
        referent_schema="public",
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_index(
        "uq_lesson_score_result_v2_course",
        "lesson_score_results",
        ["v2_source_region", "v2_source_appoint_id"],
        unique=True,
        schema="public",
        postgresql_where=sa.text("v2_source_region IS NOT NULL"),
    )


def _create_integrity_guards() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.dts_v2_lesson_score_entry_valid(
            guard_entry_id text,
            guard_region text,
            guard_appoint_id text,
            guard_completion_seq integer,
            guard_component_code text,
            guard_generation bigint,
            guard_entry_kind text,
            guard_teacher_id text,
            expected_score numeric,
            expected_rule_version text,
            expected_evidence_fingerprint text,
            expected_reversal_of text
        )
        RETURNS boolean
        LANGUAGE sql
        STABLE
        SET search_path = pg_catalog, public
        AS $function$
            SELECT EXISTS (
                SELECT 1
                FROM public.score_entries AS entry
                WHERE entry.score_entry_id = guard_entry_id
                  AND entry.teacher_id = guard_teacher_id
                  AND entry.dimension = CASE guard_component_code
                        WHEN 'FEEDBACK_PRAISE' THEN 'USER_FEEDBACK'
                        WHEN 'PERFECT_COMPLETED' THEN 'RELIABILITY'
                        WHEN 'PEAK_COMPLETED' THEN 'RELIABILITY'
                        WHEN 'CLASS_QUALITY_HARDWARE' THEN 'CLASS_QUALITY'
                      END
                  AND entry.reason_code = guard_component_code
                  AND entry.entry_type = CASE guard_entry_kind
                        WHEN 'AWARD' THEN 'LESSON_COMPONENT_AWARD'
                        WHEN 'REVERSAL' THEN 'LESSON_COMPONENT_REVERSAL'
                      END
                  AND entry.idempotency_key = CASE guard_entry_kind
                        WHEN 'AWARD' THEN
                            'lesson:' || guard_region || ':' ||
                            guard_appoint_id || ':' || 'p' ||
                            guard_completion_seq::text || ':' ||
                            guard_component_code || ':' || 'gen' ||
                            guard_generation::text || ':' ||
                            entry.score_rule_version
                        WHEN 'REVERSAL' THEN
                            'lesson-reversal:' || expected_reversal_of
                      END
                  AND (
                        (guard_entry_kind = 'AWARD'
                         AND entry.delta_score > 0
                         AND entry.reversal_of_score_entry_id IS NULL
                         AND entry.evidence_status = 'CONFIRMED')
                        OR
                        (guard_entry_kind = 'REVERSAL'
                         AND entry.delta_score < 0
                         AND entry.reversal_of_score_entry_id =
                                expected_reversal_of
                         AND entry.evidence_status IN (
                                'CONFIRMED', 'SOURCE_MISSING'
                         ))
                  )
                  AND (
                        expected_score IS NULL
                        OR entry.delta_score::numeric = CASE guard_entry_kind
                            WHEN 'AWARD' THEN expected_score
                            WHEN 'REVERSAL' THEN -expected_score
                          END
                  )
                  AND (
                        expected_rule_version IS NULL
                        OR entry.score_rule_version = expected_rule_version
                  )
                  AND jsonb_typeof(entry.payload) = 'object'
                  AND entry.payload ->> 'settlement_contract' =
                        'lesson-score-component-v2'
                  AND entry.payload ->> 'source_region' = guard_region
                  AND entry.payload ->> 'source_appoint_id' = guard_appoint_id
                  AND entry.payload ->> 'completion_participation_seq' =
                        guard_completion_seq::text
                  AND entry.payload ->> 'component_code' =
                        guard_component_code
                  AND entry.payload ->> 'award_generation' =
                        guard_generation::text
                  AND (
                        expected_evidence_fingerprint IS NULL
                        OR entry.payload ->> 'evidence_fingerprint' =
                            expected_evidence_fingerprint
                  )
            )
        $function$;

        CREATE FUNCTION public.dts_v2_assert_lesson_score_course(
            guard_region text,
            guard_appoint_id text
        )
        RETURNS void
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE
            course_row public.source_courses%ROWTYPE;
            settlement_row public.lesson_score_component_settlements%ROWTYPE;
            result_row public.lesson_score_results%ROWTYPE;
            reversed_award_id text;
            previous_generation bigint;
        BEGIN
            SELECT *
            INTO course_row
            FROM public.source_courses
            WHERE source_region = guard_region
              AND source_appoint_id = guard_appoint_id;

            FOR settlement_row IN
                SELECT *
                FROM public.lesson_score_component_settlements
                WHERE source_region = guard_region
                  AND source_appoint_id = guard_appoint_id
            LOOP
                IF settlement_row.status = 'AWARDED' THEN
                    IF course_row.source_appoint_id IS NULL
                       OR course_row.completion_voided_at IS NOT NULL
                       OR course_row.completion_conflict_status = 'PENDING'
                       OR ROW(
                            settlement_row.completion_participation_seq,
                            settlement_row.teacher_id
                       ) IS DISTINCT FROM ROW(
                            course_row.completion_participation_seq,
                            course_row.completion_teacher_id
                       )
                       OR NOT EXISTS (
                            SELECT 1
                            FROM public.source_course_participations AS part
                            WHERE part.source_region =
                                    settlement_row.source_region
                              AND part.source_appoint_id =
                                    settlement_row.source_appoint_id
                              AND part.participation_seq =
                                    settlement_row.completion_participation_seq
                              AND part.teacher_id = settlement_row.teacher_id
                              AND part.participation_role = 'COMPLETION'
                       ) THEN
                        RAISE EXCEPTION
                            'DTS_V2_LESSON_COMPONENT_COMPLETION_MISMATCH';
                    END IF;

                    IF NOT public.dts_v2_lesson_score_entry_valid(
                        settlement_row.current_award_score_entry_id,
                        settlement_row.source_region,
                        settlement_row.source_appoint_id,
                        settlement_row.completion_participation_seq,
                        settlement_row.component_code,
                        settlement_row.award_generation,
                        'AWARD',
                        settlement_row.teacher_id,
                        settlement_row.component_score,
                        settlement_row.score_rule_version,
                        settlement_row.evidence_fingerprint,
                        NULL
                    ) THEN
                        RAISE EXCEPTION
                            'DTS_V2_LESSON_COMPONENT_AWARD_ENTRY_MISMATCH';
                    END IF;
                END IF;

                IF settlement_row.status = 'REVERSED' THEN
                    SELECT reversal_of_score_entry_id
                    INTO reversed_award_id
                    FROM public.score_entries
                    WHERE score_entry_id =
                            settlement_row.last_reversal_score_entry_id;

                    IF reversed_award_id IS NULL
                       OR NOT public.dts_v2_lesson_score_entry_valid(
                            settlement_row.last_reversal_score_entry_id,
                            settlement_row.source_region,
                            settlement_row.source_appoint_id,
                            settlement_row.completion_participation_seq,
                            settlement_row.component_code,
                            settlement_row.award_generation,
                            'REVERSAL',
                            settlement_row.teacher_id,
                            settlement_row.component_score,
                            settlement_row.score_rule_version,
                            settlement_row.evidence_fingerprint,
                            reversed_award_id
                       )
                       OR NOT public.dts_v2_lesson_score_entry_valid(
                            reversed_award_id,
                            settlement_row.source_region,
                            settlement_row.source_appoint_id,
                            settlement_row.completion_participation_seq,
                            settlement_row.component_code,
                            settlement_row.award_generation,
                            'AWARD',
                            settlement_row.teacher_id,
                            settlement_row.component_score,
                            NULL,
                            NULL,
                            NULL
                       ) THEN
                        RAISE EXCEPTION
                            'DTS_V2_LESSON_COMPONENT_REVERSAL_ENTRY_MISMATCH';
                    END IF;
                ELSIF settlement_row.award_generation > 1 THEN
                    previous_generation :=
                        settlement_row.award_generation - 1;
                    SELECT reversal_of_score_entry_id
                    INTO reversed_award_id
                    FROM public.score_entries
                    WHERE score_entry_id =
                            settlement_row.last_reversal_score_entry_id;

                    IF reversed_award_id IS NULL
                       OR NOT public.dts_v2_lesson_score_entry_valid(
                            settlement_row.last_reversal_score_entry_id,
                            settlement_row.source_region,
                            settlement_row.source_appoint_id,
                            settlement_row.completion_participation_seq,
                            settlement_row.component_code,
                            previous_generation,
                            'REVERSAL',
                            settlement_row.teacher_id,
                            NULL,
                            NULL,
                            NULL,
                            reversed_award_id
                       )
                       OR NOT public.dts_v2_lesson_score_entry_valid(
                            reversed_award_id,
                            settlement_row.source_region,
                            settlement_row.source_appoint_id,
                            settlement_row.completion_participation_seq,
                            settlement_row.component_code,
                            previous_generation,
                            'AWARD',
                            settlement_row.teacher_id,
                            NULL,
                            NULL,
                            NULL,
                            NULL
                       ) THEN
                        RAISE EXCEPTION
                            'DTS_V2_LESSON_COMPONENT_HISTORY_ENTRY_MISMATCH';
                    END IF;
                END IF;
            END LOOP;

            FOR result_row IN
                SELECT *
                FROM public.lesson_score_results
                WHERE v2_source_region = guard_region
                  AND v2_source_appoint_id = guard_appoint_id
            LOOP
                IF course_row.source_appoint_id IS NULL
                   OR course_row.completion_voided_at IS NOT NULL
                   OR course_row.completion_conflict_status = 'PENDING'
                   OR result_row.v2_completion_participation_seq
                        IS DISTINCT FROM
                        course_row.completion_participation_seq
                   OR NOT EXISTS (
                        SELECT 1
                        FROM public.source_course_participations AS part
                        WHERE part.source_region = result_row.v2_source_region
                          AND part.source_appoint_id =
                                result_row.v2_source_appoint_id
                          AND part.participation_seq =
                                result_row.v2_completion_participation_seq
                          AND part.teacher_id =
                                course_row.completion_teacher_id
                          AND part.participation_role = 'COMPLETION'
                   ) THEN
                    RAISE EXCEPTION
                        'DTS_V2_LESSON_SCORE_RESULT_COMPLETION_MISMATCH';
                END IF;
            END LOOP;
        END
        $function$;

        CREATE FUNCTION public.dts_v2_lesson_component_lifecycle_guard()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $function$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION
                    'DTS_V2_LESSON_COMPONENT_SETTLEMENT_IMMUTABLE';
            END IF;
            IF ROW(
                NEW.source_region,
                NEW.source_appoint_id,
                NEW.completion_participation_seq,
                NEW.component_code,
                NEW.teacher_id,
                NEW.materialization_origin,
                NEW.materialized_by_run_id,
                NEW.created_at
            ) IS DISTINCT FROM ROW(
                OLD.source_region,
                OLD.source_appoint_id,
                OLD.completion_participation_seq,
                OLD.component_code,
                OLD.teacher_id,
                OLD.materialization_origin,
                OLD.materialized_by_run_id,
                OLD.created_at
            ) THEN
                RAISE EXCEPTION
                    'DTS_V2_LESSON_COMPONENT_IDENTITY_IMMUTABLE';
            END IF;
            IF NEW.row_version <> OLD.row_version + 1
               OR NEW.award_projection_generation <
                    OLD.award_projection_generation THEN
                RAISE EXCEPTION
                    'DTS_V2_LESSON_COMPONENT_REVISION_INVALID';
            END IF;

            IF ROW(
                NEW.status,
                NEW.award_generation,
                NEW.component_score,
                NEW.score_rule_version,
                NEW.evidence_fingerprint,
                NEW.current_award_score_entry_id,
                NEW.last_reversal_score_entry_id,
                NEW.awarded_at,
                NEW.reversed_at
            ) IS NOT DISTINCT FROM ROW(
                OLD.status,
                OLD.award_generation,
                OLD.component_score,
                OLD.score_rule_version,
                OLD.evidence_fingerprint,
                OLD.current_award_score_entry_id,
                OLD.last_reversal_score_entry_id,
                OLD.awarded_at,
                OLD.reversed_at
            ) THEN
                IF NEW.award_projection_generation <=
                        OLD.award_projection_generation THEN
                    RAISE EXCEPTION
                        'DTS_V2_LESSON_COMPONENT_NOOP_UPDATE';
                END IF;
                RETURN NEW;
            END IF;

            IF OLD.status = 'AWARDED' AND NEW.status = 'REVERSED' THEN
                IF NEW.award_generation <> OLD.award_generation
                   OR NEW.component_score IS DISTINCT FROM OLD.component_score
                   OR NEW.current_award_score_entry_id IS NOT NULL THEN
                    RAISE EXCEPTION
                        'DTS_V2_LESSON_COMPONENT_REVERSAL_INVALID';
                END IF;
            ELSIF OLD.status = 'REVERSED' AND NEW.status = 'AWARDED' THEN
                IF NEW.award_generation <> OLD.award_generation + 1
                   OR NEW.last_reversal_score_entry_id IS DISTINCT FROM
                        OLD.last_reversal_score_entry_id THEN
                    RAISE EXCEPTION
                        'DTS_V2_LESSON_COMPONENT_REAWARD_INVALID';
                END IF;
            ELSIF OLD.status = 'AWARDED' AND NEW.status = 'AWARDED' THEN
                IF NEW.award_generation <> OLD.award_generation + 1
                   OR NEW.last_reversal_score_entry_id IS NULL
                   OR NEW.last_reversal_score_entry_id IS NOT DISTINCT FROM
                        OLD.last_reversal_score_entry_id
                   OR NEW.current_award_score_entry_id IS NOT DISTINCT FROM
                        OLD.current_award_score_entry_id THEN
                    RAISE EXCEPTION
                        'DTS_V2_LESSON_COMPONENT_REPLACE_INVALID';
                END IF;
            ELSE
                RAISE EXCEPTION
                    'DTS_V2_LESSON_COMPONENT_TRANSITION_INVALID';
            END IF;
            RETURN NEW;
        END
        $function$;

        CREATE FUNCTION public.dts_v2_lesson_score_result_ownership_guard()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE
            actor_name text := COALESCE(
                NULLIF(current_setting('role', true), 'none'),
                session_user
            );
            old_is_v2 boolean;
            new_is_v2 boolean := NEW.v2_source_region IS NOT NULL;
        BEGIN
            IF actor_name IN (
                    'tit_growth_app',
                    'tit_dts_ingest_runtime',
                    'tit_teacher_crud'
               ) AND new_is_v2 THEN
                RAISE EXCEPTION
                    'DTS_V2_LESSON_SCORE_RESULT_RUNTIME_ROUTE_INACTIVE'
                    USING ERRCODE = '42501';
            END IF;
            IF TG_OP = 'INSERT' THEN
                RETURN NEW;
            END IF;
            old_is_v2 := OLD.v2_source_region IS NOT NULL;
            IF NOT old_is_v2 AND new_is_v2 THEN
                RAISE EXCEPTION
                    'DTS_V2_LESSON_SCORE_RESULT_LEGACY_OWNERSHIP_IMMUTABLE';
            END IF;
            IF old_is_v2 AND NOT new_is_v2 THEN
                RAISE EXCEPTION
                    'DTS_V2_LESSON_SCORE_RESULT_OWNERSHIP_REQUIRED';
            END IF;
            IF old_is_v2 AND ROW(
                    NEW.v2_source_region,
                    NEW.v2_source_appoint_id
               ) IS DISTINCT FROM ROW(
                    OLD.v2_source_region,
                    OLD.v2_source_appoint_id
               ) THEN
                RAISE EXCEPTION
                    'DTS_V2_LESSON_SCORE_RESULT_COURSE_IMMUTABLE';
            END IF;
            IF old_is_v2
               AND NEW.v2_completion_participation_seq IS DISTINCT FROM
                    OLD.v2_completion_participation_seq
               AND NEW.projection_revision <> OLD.projection_revision + 1 THEN
                RAISE EXCEPTION
                    'DTS_V2_LESSON_SCORE_RESULT_TRANSFER_REVISION_INVALID';
            END IF;
            RETURN NEW;
        END
        $function$;

        CREATE FUNCTION public.dts_v2_lesson_score_course_guard()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $function$
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                PERFORM public.dts_v2_assert_lesson_score_course(
                    OLD.source_region,
                    OLD.source_appoint_id
                );
            END IF;
            IF TG_OP <> 'DELETE' THEN
                PERFORM public.dts_v2_assert_lesson_score_course(
                    NEW.source_region,
                    NEW.source_appoint_id
                );
            END IF;
            RETURN NULL;
        END
        $function$;

        CREATE FUNCTION public.dts_v2_lesson_score_result_course_guard()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $function$
        BEGIN
            IF TG_OP <> 'INSERT' AND OLD.v2_source_region IS NOT NULL THEN
                PERFORM public.dts_v2_assert_lesson_score_course(
                    OLD.v2_source_region,
                    OLD.v2_source_appoint_id
                );
            END IF;
            IF TG_OP <> 'DELETE' AND NEW.v2_source_region IS NOT NULL THEN
                PERFORM public.dts_v2_assert_lesson_score_course(
                    NEW.v2_source_region,
                    NEW.v2_source_appoint_id
                );
            END IF;
            RETURN NULL;
        END
        $function$;

        CREATE TRIGGER trg_lesson_component_settlement_lifecycle
        BEFORE UPDATE OR DELETE
        ON public.lesson_score_component_settlements
        FOR EACH ROW
        EXECUTE FUNCTION public.dts_v2_lesson_component_lifecycle_guard();

        CREATE TRIGGER trg_lesson_score_result_v2_ownership
        BEFORE INSERT OR UPDATE ON public.lesson_score_results
        FOR EACH ROW
        EXECUTE FUNCTION public.dts_v2_lesson_score_result_ownership_guard();

        CREATE CONSTRAINT TRIGGER ct_lesson_component_course_guard
        AFTER INSERT OR UPDATE OR DELETE
        ON public.lesson_score_component_settlements
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE FUNCTION public.dts_v2_lesson_score_course_guard();

        CREATE CONSTRAINT TRIGGER ct_source_course_lesson_score_guard
        AFTER INSERT OR UPDATE OR DELETE ON public.source_courses
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE FUNCTION public.dts_v2_lesson_score_course_guard();

        CREATE CONSTRAINT TRIGGER ct_source_participation_lesson_score_guard
        AFTER INSERT OR UPDATE OR DELETE
        ON public.source_course_participations
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE FUNCTION public.dts_v2_lesson_score_course_guard();

        CREATE CONSTRAINT TRIGGER ct_lesson_score_result_v2_course_guard
        AFTER INSERT OR UPDATE OR DELETE ON public.lesson_score_results
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE FUNCTION public.dts_v2_lesson_score_result_course_guard();
        """
    )


def _lock_schema_from_runtime() -> None:
    op.execute(
        """
        COMMENT ON TABLE public.lesson_score_component_settlements IS
            'DTS v2 shadow: per-course component ledger ownership; no production writer is active';
        COMMENT ON COLUMN public.lesson_score_results.v2_source_region IS
            'Nullable v2 ownership; legacy rows remain NULL and cannot be retrofitted by UPDATE';

        REVOKE ALL PRIVILEGES ON TABLE
            public.lesson_score_component_settlements
        FROM PUBLIC;
        REVOKE ALL PRIVILEGES ON FUNCTION
            public.dts_v2_lesson_score_entry_valid(
                text, text, text, integer, text, bigint,
                text, text, numeric, text, text, text
            ),
            public.dts_v2_assert_lesson_score_course(text, text),
            public.dts_v2_lesson_component_lifecycle_guard(),
            public.dts_v2_lesson_score_result_ownership_guard(),
            public.dts_v2_lesson_score_course_guard(),
            public.dts_v2_lesson_score_result_course_guard()
        FROM PUBLIC;

        DO $lesson_component_acl$
        DECLARE
            role_name text;
        BEGIN
            FOREACH role_name IN ARRAY ARRAY[
                'tit_growth_app',
                'tit_dts_ingest_runtime',
                'tit_teacher_crud'
            ] LOOP
                IF to_regrole(role_name) IS NOT NULL THEN
                    EXECUTE format(
                        'REVOKE ALL PRIVILEGES ON TABLE '
                        'public.lesson_score_component_settlements FROM %I',
                        role_name
                    );
                    EXECUTE format(
                        'REVOKE ALL PRIVILEGES ON FUNCTION '
                        'public.dts_v2_lesson_score_entry_valid('
                        'text,text,text,integer,text,bigint,text,text,numeric,text,text,text), '
                        'public.dts_v2_assert_lesson_score_course(text,text), '
                        'public.dts_v2_lesson_component_lifecycle_guard(), '
                        'public.dts_v2_lesson_score_result_ownership_guard(), '
                        'public.dts_v2_lesson_score_course_guard(), '
                        'public.dts_v2_lesson_score_result_course_guard() FROM %I',
                        role_name
                    );
                END IF;
            END LOOP;
        END
        $lesson_component_acl$;
        """
    )


def _assert_installed_contract() -> None:
    op.execute(
        """
        DO $lesson_component_installed_guard$
        DECLARE
            expected_trigger text;
        BEGIN
            IF to_regclass(
                    'public.lesson_score_component_settlements'
                ) IS NULL THEN
                RAISE EXCEPTION
                    'DTS_V2_LESSON_COMPONENT_TABLE_MISSING';
            END IF;
            FOREACH expected_trigger IN ARRAY ARRAY[
                'ct_lesson_component_course_guard',
                'ct_source_course_lesson_score_guard',
                'ct_source_participation_lesson_score_guard',
                'ct_lesson_score_result_v2_course_guard'
            ] LOOP
                IF NOT EXISTS (
                    SELECT 1
                    FROM pg_catalog.pg_trigger
                    WHERE tgname = expected_trigger
                      AND NOT tgisinternal
                      AND tgenabled <> 'D'
                      AND tgdeferrable
                      AND tginitdeferred
                ) THEN
                    RAISE EXCEPTION
                        'DTS_V2_LESSON_COMPONENT_DEFERRED_GUARD_MISSING:%',
                        expected_trigger;
                END IF;
            END LOOP;
            IF EXISTS (
                SELECT 1
                FROM public.lesson_score_results
                WHERE v2_source_region IS NOT NULL
                   OR v2_source_appoint_id IS NOT NULL
                   OR v2_completion_participation_seq IS NOT NULL
            ) THEN
                RAISE EXCEPTION
                    'DTS_V2_LESSON_SCORE_RESULT_UNEXPECTED_BACKFILL';
            END IF;
        END
        $lesson_component_installed_guard$;
        """
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    _ensure_revision_capacity()
    _assert_upgrade_preconditions()
    _create_settlement_table()
    _extend_lesson_score_results()
    _create_integrity_guards()
    _lock_schema_from_runtime()
    _assert_installed_contract()


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    op.execute(
        """
        LOCK TABLE
            public.lesson_score_component_settlements,
            public.lesson_score_results,
            public.source_courses,
            public.source_course_participations,
            public.score_entries
        IN ACCESS EXCLUSIVE MODE;

        DO $lesson_component_downgrade_guard$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM public.lesson_score_component_settlements
                LIMIT 1
            ) THEN
                RAISE EXCEPTION
                    'DTS_V2_LESSON_COMPONENT_DOWNGRADE_DATA_PRESENT';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM public.lesson_score_results
                WHERE v2_source_region IS NOT NULL
                   OR v2_source_appoint_id IS NOT NULL
                   OR v2_completion_participation_seq IS NOT NULL
                LIMIT 1
            ) THEN
                RAISE EXCEPTION
                    'DTS_V2_LESSON_SCORE_RESULT_DOWNGRADE_OWNERSHIP_PRESENT';
            END IF;
        END
        $lesson_component_downgrade_guard$;

        DROP TRIGGER ct_lesson_score_result_v2_course_guard
            ON public.lesson_score_results;
        DROP TRIGGER ct_source_participation_lesson_score_guard
            ON public.source_course_participations;
        DROP TRIGGER ct_source_course_lesson_score_guard
            ON public.source_courses;
        DROP TRIGGER ct_lesson_component_course_guard
            ON public.lesson_score_component_settlements;
        DROP TRIGGER trg_lesson_score_result_v2_ownership
            ON public.lesson_score_results;
        DROP TRIGGER trg_lesson_component_settlement_lifecycle
            ON public.lesson_score_component_settlements;

        DROP FUNCTION public.dts_v2_lesson_score_result_course_guard();
        DROP FUNCTION public.dts_v2_lesson_score_course_guard();
        DROP FUNCTION public.dts_v2_lesson_score_result_ownership_guard();
        DROP FUNCTION public.dts_v2_lesson_component_lifecycle_guard();
        DROP FUNCTION public.dts_v2_assert_lesson_score_course(text, text);
        DROP FUNCTION public.dts_v2_lesson_score_entry_valid(
            text, text, text, integer, text, bigint,
            text, text, numeric, text, text, text
        );
        """
    )

    op.drop_index(
        "uq_lesson_score_result_v2_course",
        table_name="lesson_score_results",
        schema="public",
    )
    op.drop_constraint(
        "fk_lesson_score_result_v2_participation",
        "lesson_score_results",
        schema="public",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_lesson_score_result_v2_course",
        "lesson_score_results",
        schema="public",
        type_="foreignkey",
    )
    op.drop_constraint(
        "ck_lesson_score_result_v2_ownership",
        "lesson_score_results",
        schema="public",
        type_="check",
    )
    op.drop_column(
        "lesson_score_results",
        "v2_completion_participation_seq",
        schema="public",
    )
    op.drop_column(
        "lesson_score_results", "v2_source_appoint_id", schema="public"
    )
    op.drop_column(
        "lesson_score_results", "v2_source_region", schema="public"
    )
    op.drop_table("lesson_score_component_settlements", schema="public")
