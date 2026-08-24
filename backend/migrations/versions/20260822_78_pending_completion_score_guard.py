"""freeze existing v2 lesson scores while completion correction is pending.

Revision ID: 20260822_78_pending_score_guard
Revises: 20260822_77_retire_cpu_network
Create Date: 2026-08-22

Revision 76 incorrectly rejected an otherwise valid, already-materialized v2
lesson result or component award as soon as a post-completion correction moved
the course to PENDING.  This revision keeps those rows only while they still
match the frozen completion.  Immediate write guards prohibit result writes
and new, replacement, or re-award component writes until KEEP, TRANSFER, or
VOID resolves the correction.  Award reversal remains available so correction
transactions can atomically unwind the frozen owner.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from alembic import op


revision: str = "20260822_78_pending_score_guard"
down_revision: Union[str, None] = "20260822_77_retire_cpu_network"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SETTLEMENT_PENDING_TRIGGER = (
    "trg_lesson_component_settlement_pending_freeze"
)
RESULT_PENDING_TRIGGER = "trg_lesson_score_result_v2_pending_freeze"


def _assert_course_function_sql(*, reject_pending: bool) -> str:
    settlement_pending_rejection = ""
    result_pending_rejection = ""
    if reject_pending:
        settlement_pending_rejection = """
                       OR course_row.completion_conflict_status = 'PENDING'"""
        result_pending_rejection = """
                   OR course_row.completion_conflict_status = 'PENDING'"""

    return f"""
        CREATE OR REPLACE FUNCTION public.dts_v2_assert_lesson_score_course(
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
                       OR course_row.completion_voided_at IS NOT NULL{settlement_pending_rejection}
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
                   OR course_row.completion_voided_at IS NOT NULL{result_pending_rejection}
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
    """


def _lock_and_assert_upgrade_preconditions() -> None:
    op.execute(
        f"""
        DO $pending_score_dependency_guard$
        DECLARE
            dependency_name text;
        BEGIN
            FOREACH dependency_name IN ARRAY ARRAY[
                'source_courses',
                'source_course_participations',
                'score_entries',
                'lesson_score_results',
                'lesson_score_component_settlements'
            ] LOOP
                IF to_regclass('public.' || dependency_name) IS NULL THEN
                    RAISE EXCEPTION
                        'DTS_V2_PENDING_SCORE_DEPENDENCY_MISSING:%',
                        dependency_name;
                END IF;
            END LOOP;
        END
        $pending_score_dependency_guard$;

        LOCK TABLE
            public.source_courses,
            public.source_course_participations,
            public.score_entries,
            public.lesson_score_results,
            public.lesson_score_component_settlements
        IN ACCESS EXCLUSIVE MODE;

        DO $pending_score_contract_guard$
        DECLARE
            assert_definition text;
            pending_clause text :=
                'course_row.completion_conflict_status = ''PENDING''';
            pending_clause_count integer;
        BEGIN
            IF to_regprocedure(
                    'public.dts_v2_assert_lesson_score_course(text,text)'
               ) IS NULL
               OR to_regprocedure(
                    'public.dts_v2_lesson_component_lifecycle_guard()'
                  ) IS NULL
               OR to_regprocedure(
                    'public.dts_v2_lesson_score_result_ownership_guard()'
                  ) IS NULL THEN
                RAISE EXCEPTION
                    'DTS_V2_PENDING_SCORE_REV76_GUARD_MISSING';
            END IF;

            SELECT pg_get_functiondef(
                'public.dts_v2_assert_lesson_score_course(text,text)'::regprocedure
            ) INTO assert_definition;
            pending_clause_count :=
                (length(assert_definition) - length(replace(
                    assert_definition,
                    pending_clause,
                    ''
                ))) / length(pending_clause);
            IF pending_clause_count <> 2 THEN
                RAISE EXCEPTION
                    'DTS_V2_PENDING_SCORE_REV76_GUARD_DRIFT:%',
                    pending_clause_count;
            END IF;

            IF to_regprocedure(
                    'public.dts_v2_lesson_component_pending_freeze_guard()'
               ) IS NOT NULL
               OR to_regprocedure(
                    'public.dts_v2_lesson_score_result_pending_freeze_guard()'
                  ) IS NOT NULL
               OR EXISTS (
                    SELECT 1
                    FROM pg_catalog.pg_trigger
                    WHERE tgname IN (
                        '{SETTLEMENT_PENDING_TRIGGER}',
                        '{RESULT_PENDING_TRIGGER}'
                    )
                      AND NOT tgisinternal
               ) THEN
                RAISE EXCEPTION
                    'DTS_V2_PENDING_SCORE_GUARD_ALREADY_PRESENT';
            END IF;
        END
        $pending_score_contract_guard$;
        """
    )


def _create_pending_write_guards() -> None:
    op.execute(
        f"""
        CREATE FUNCTION
            public.dts_v2_lesson_component_pending_freeze_guard()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE
            conflict_status text;
        BEGIN
            SELECT course.completion_conflict_status
            INTO conflict_status
            FROM public.source_courses AS course
            WHERE course.source_region = NEW.source_region
              AND course.source_appoint_id = NEW.source_appoint_id
            FOR UPDATE;

            IF conflict_status = 'PENDING'
               AND (TG_OP = 'INSERT' OR NEW.status = 'AWARDED') THEN
                RAISE EXCEPTION
                    'DTS_V2_LESSON_COMPONENT_PENDING_AWARD_WRITE_FORBIDDEN'
                    USING ERRCODE = '55000';
            END IF;
            RETURN NEW;
        END
        $function$;

        CREATE FUNCTION
            public.dts_v2_lesson_score_result_pending_freeze_guard()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE
            guard_region text;
            guard_appoint_id text;
            conflict_status text;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                guard_region := OLD.v2_source_region;
                guard_appoint_id := OLD.v2_source_appoint_id;
            ELSE
                guard_region := NEW.v2_source_region;
                guard_appoint_id := NEW.v2_source_appoint_id;
            END IF;

            IF guard_region IS NOT NULL THEN
                SELECT course.completion_conflict_status
                INTO conflict_status
                FROM public.source_courses AS course
                WHERE course.source_region = guard_region
                  AND course.source_appoint_id = guard_appoint_id
                FOR UPDATE;
            END IF;

            IF conflict_status = 'PENDING' THEN
                RAISE EXCEPTION
                    'DTS_V2_LESSON_SCORE_RESULT_PENDING_WRITE_FORBIDDEN'
                    USING ERRCODE = '55000';
            END IF;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END
        $function$;

        CREATE TRIGGER {SETTLEMENT_PENDING_TRIGGER}
        BEFORE INSERT OR UPDATE
        ON public.lesson_score_component_settlements
        FOR EACH ROW
        EXECUTE FUNCTION
            public.dts_v2_lesson_component_pending_freeze_guard();

        CREATE TRIGGER {RESULT_PENDING_TRIGGER}
        BEFORE INSERT OR UPDATE OR DELETE
        ON public.lesson_score_results
        FOR EACH ROW
        EXECUTE FUNCTION
            public.dts_v2_lesson_score_result_pending_freeze_guard();
        """
    )


def _lock_runtime_acl_and_comments() -> None:
    op.execute(
        """
        COMMENT ON TABLE public.lesson_score_component_settlements IS
            'DTS v2 shadow: PENDING retains frozen awards but blocks new/replacement/re-award writes; no production writer is active';
        COMMENT ON COLUMN
            public.lesson_score_results.v2_completion_participation_seq IS
            'Frozen v2 completion ownership; PENDING retains existing matching results but forbids result writes';

        REVOKE ALL PRIVILEGES ON FUNCTION
            public.dts_v2_lesson_component_pending_freeze_guard(),
            public.dts_v2_lesson_score_result_pending_freeze_guard()
        FROM PUBLIC;

        DO $pending_score_acl_guard$
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
                        'REVOKE ALL PRIVILEGES ON FUNCTION '
                        'public.dts_v2_lesson_component_pending_freeze_guard(), '
                        'public.dts_v2_lesson_score_result_pending_freeze_guard() '
                        'FROM %I',
                        role_name
                    );
                END IF;
            END LOOP;
        END
        $pending_score_acl_guard$;
        """
    )


def _assert_installed_contract() -> None:
    op.execute(
        f"""
        DO $pending_score_installed_guard$
        DECLARE
            assert_definition text;
            expected_trigger text;
            pending_clause text :=
                'course_row.completion_conflict_status = ''PENDING''';
        BEGIN
            SELECT pg_get_functiondef(
                'public.dts_v2_assert_lesson_score_course(text,text)'::regprocedure
            ) INTO assert_definition;
            IF position(pending_clause IN assert_definition) <> 0 THEN
                RAISE EXCEPTION
                    'DTS_V2_PENDING_SCORE_ASSERTION_NOT_RELAXED';
            END IF;

            FOREACH expected_trigger IN ARRAY ARRAY[
                '{SETTLEMENT_PENDING_TRIGGER}',
                '{RESULT_PENDING_TRIGGER}'
            ] LOOP
                IF NOT EXISTS (
                    SELECT 1
                    FROM pg_catalog.pg_trigger
                    WHERE tgname = expected_trigger
                      AND NOT tgisinternal
                      AND tgenabled <> 'D'
                      AND NOT tgdeferrable
                ) THEN
                    RAISE EXCEPTION
                        'DTS_V2_PENDING_SCORE_TRIGGER_MISSING:%',
                        expected_trigger;
                END IF;
            END LOOP;

            IF EXISTS (
                SELECT 1
                FROM pg_catalog.pg_proc AS procedure
                CROSS JOIN LATERAL aclexplode(COALESCE(
                    procedure.proacl,
                    acldefault('f', procedure.proowner)
                )) AS privilege
                WHERE procedure.oid IN (
                    'public.dts_v2_lesson_component_pending_freeze_guard()'::regprocedure,
                    'public.dts_v2_lesson_score_result_pending_freeze_guard()'::regprocedure
                )
                  AND privilege.grantee = 0
                  AND privilege.privilege_type = 'EXECUTE'
            ) THEN
                RAISE EXCEPTION
                    'DTS_V2_PENDING_SCORE_PUBLIC_EXECUTE_REMAINS';
            END IF;

            PERFORM public.dts_v2_assert_lesson_score_course(
                course.source_region,
                course.source_appoint_id
            )
            FROM public.source_courses AS course
            WHERE course.completion_conflict_status = 'PENDING';
        END
        $pending_score_installed_guard$;
        """
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    _lock_and_assert_upgrade_preconditions()
    op.execute(_assert_course_function_sql(reject_pending=False))
    _create_pending_write_guards()
    _lock_runtime_acl_and_comments()
    _assert_installed_contract()


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    op.execute(
        f"""
        LOCK TABLE
            public.source_courses,
            public.source_course_participations,
            public.score_entries,
            public.lesson_score_results,
            public.lesson_score_component_settlements
        IN ACCESS EXCLUSIVE MODE;

        DO $pending_score_downgrade_guard$
        BEGIN
            IF to_regprocedure(
                    'public.dts_v2_lesson_component_pending_freeze_guard()'
               ) IS NULL
               OR to_regprocedure(
                    'public.dts_v2_lesson_score_result_pending_freeze_guard()'
                  ) IS NULL
               OR NOT EXISTS (
                    SELECT 1
                    FROM pg_catalog.pg_trigger
                    WHERE tgname = '{SETTLEMENT_PENDING_TRIGGER}'
                      AND NOT tgisinternal
               )
               OR NOT EXISTS (
                    SELECT 1
                    FROM pg_catalog.pg_trigger
                    WHERE tgname = '{RESULT_PENDING_TRIGGER}'
                      AND NOT tgisinternal
               ) THEN
                RAISE EXCEPTION
                    'DTS_V2_PENDING_SCORE_DOWNGRADE_GUARD_MISSING';
            END IF;

            IF EXISTS (
                SELECT 1
                FROM public.source_courses AS course
                WHERE course.completion_conflict_status = 'PENDING'
                  AND (
                    EXISTS (
                        SELECT 1
                        FROM public.lesson_score_component_settlements AS settlement
                        WHERE settlement.source_region = course.source_region
                          AND settlement.source_appoint_id =
                                course.source_appoint_id
                          AND settlement.status = 'AWARDED'
                    )
                    OR EXISTS (
                        SELECT 1
                        FROM public.lesson_score_results AS result
                        WHERE result.v2_source_region = course.source_region
                          AND result.v2_source_appoint_id =
                                course.source_appoint_id
                    )
                  )
            ) THEN
                RAISE EXCEPTION
                    'DTS_V2_PENDING_SCORE_DOWNGRADE_DATA_PRESENT';
            END IF;
        END
        $pending_score_downgrade_guard$;

        DROP TRIGGER {RESULT_PENDING_TRIGGER}
            ON public.lesson_score_results;
        DROP TRIGGER {SETTLEMENT_PENDING_TRIGGER}
            ON public.lesson_score_component_settlements;
        DROP FUNCTION
            public.dts_v2_lesson_score_result_pending_freeze_guard();
        DROP FUNCTION
            public.dts_v2_lesson_component_pending_freeze_guard();
        """
    )
    op.execute(_assert_course_function_sql(reject_pending=True))
    op.execute(
        """
        COMMENT ON TABLE public.lesson_score_component_settlements IS
            'DTS v2 shadow: per-course component ledger ownership; no production writer is active';
        COMMENT ON COLUMN
            public.lesson_score_results.v2_completion_participation_seq IS NULL;

        DO $pending_score_downgrade_installed_guard$
        DECLARE
            assert_definition text;
            pending_clause text :=
                'course_row.completion_conflict_status = ''PENDING''';
            pending_clause_count integer;
        BEGIN
            SELECT pg_get_functiondef(
                'public.dts_v2_assert_lesson_score_course(text,text)'::regprocedure
            ) INTO assert_definition;
            pending_clause_count :=
                (length(assert_definition) - length(replace(
                    assert_definition,
                    pending_clause,
                    ''
                ))) / length(pending_clause);
            IF pending_clause_count <> 2 THEN
                RAISE EXCEPTION
                    'DTS_V2_PENDING_SCORE_DOWNGRADE_ASSERTION_DRIFT:%',
                    pending_clause_count;
            END IF;
        END
        $pending_score_downgrade_installed_guard$;
        """
    )
