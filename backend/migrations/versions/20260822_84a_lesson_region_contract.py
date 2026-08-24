"""contract the v1 lesson compatibility identity to region plus course.

Revision ID: 20260822_84a_lesson_region_contract
Revises: 20260822_84_dts_v2_domain_outbox
Create Date: 2026-08-22

The migration is intentionally a maintenance-window stop line.  It consumes
only the audited region manifest created by the expand revision, requires a
separate compatibility-writer smoke acknowledgement, validates every legacy
foreign-key mapping, and then swaps the primary/foreign keys atomically.  No
course, teacher, ID pattern, or default is used to infer a region.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260822_84a_lesson_region_contract"
down_revision: Union[str, None] = "20260822_84_dts_v2_domain_outbox"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


WRITER_CONTRACT_VERSION = "lesson-region-compat-v1"


def _lock_and_apply_authoritative_manifest() -> None:
    op.execute(
        f"""
        LOCK TABLE public.lesson_source_region_migration_control
            IN ACCESS EXCLUSIVE MODE;
        LOCK TABLE public.lesson_source_region_backfill_manifest
            IN SHARE MODE;
        LOCK TABLE public.lesson_source_wide IN ACCESS EXCLUSIVE MODE;
        LOCK TABLE public.lesson_score_results IN ACCESS EXCLUSIVE MODE;
        LOCK TABLE public.personalized_trigger_matches IN ACCESS EXCLUSIVE MODE;
        LOCK TABLE public.score_entries IN SHARE ROW EXCLUSIVE MODE;

        DO $lesson_region_contract_preflight$
        DECLARE control_record public.lesson_source_region_migration_control%ROWTYPE;
        BEGIN
            SELECT * INTO control_record
            FROM public.lesson_source_region_migration_control
            WHERE control_id='PRIMARY'
            FOR UPDATE;

            -- A brand-new empty schema has no compatibility identity to
            -- backfill and no running legacy writer to smoke-test between
            -- Alembic revisions.  Permit that one objectively provable path
            -- so a fresh ``upgrade head`` remains installable.  Any lesson,
            -- appoint mirror/domain fact, lesson score/match, or lesson
            -- Outbox evidence keeps the production expand/deploy/ack/contract
            -- stop line intact.
            IF control_record.phase='EXPANDED'
               AND NOT EXISTS (SELECT 1 FROM public.lesson_source_wide)
               AND NOT EXISTS (SELECT 1 FROM public.lesson_score_results)
               AND NOT EXISTS (
                    SELECT 1 FROM public.personalized_trigger_matches
                    WHERE lesson_id IS NOT NULL
               )
               AND NOT EXISTS (
                    SELECT 1 FROM public.score_entries
                    WHERE lesson_id IS NOT NULL
               )
               AND NOT EXISTS (
                    SELECT 1 FROM public.dts_source_rows
                    WHERE source_table IN ('dom_appoint','ovs_appoint')
               )
               AND NOT EXISTS (SELECT 1 FROM public.source_courses)
               AND NOT EXISTS (
                    SELECT 1 FROM public.source_course_participations
               )
               AND NOT EXISTS (
                    SELECT 1 FROM public.outbox_events
                    WHERE aggregate_type='LESSON_SOURCE_WIDE'
                       OR (
                            event_type='source_wide.changed.v1'
                            AND payload->>'source_table'='lesson_source_wide'
                       )
               ) THEN
                UPDATE public.lesson_source_region_migration_control
                SET phase='WRITER_READY',
                    writer_contract_version='{WRITER_CONTRACT_VERSION}',
                    writer_smoke_sha256=
                        '31cc3a030fb6e7a58396b75bb3fecb5c0ed6cd8f211310f0e85692cf7d9cf780',
                    approved_by='SYSTEM:FRESH_EMPTY_SCHEMA',
                    writer_ready_at=clock_timestamp(),
                    row_version=row_version+1,
                    updated_at=clock_timestamp()
                WHERE control_id='PRIMARY' AND phase='EXPANDED';
                SELECT * INTO control_record
                FROM public.lesson_source_region_migration_control
                WHERE control_id='PRIMARY'
                FOR UPDATE;
            END IF;
            IF NOT FOUND OR control_record.phase <> 'WRITER_READY'
               OR control_record.writer_contract_version IS DISTINCT FROM
                    '{WRITER_CONTRACT_VERSION}'
               OR control_record.writer_smoke_sha256 IS NULL
               OR control_record.writer_smoke_sha256
                    !~ '^[0-9a-f]{{64}}$' THEN
                RAISE EXCEPTION 'COMPAT_WRITER_NOT_READY';
            END IF;

            IF EXISTS (
                SELECT 1
                FROM public.outbox_events
                WHERE event_type='source_wide.changed.v1'
                  AND aggregate_type='LESSON_SOURCE_WIDE'
                  AND status <> 'PUBLISHED'
                  AND (
                    payload->>'source_region' IS NULL
                    OR payload->>'source_region' NOT IN ('dom','ovs')
                    OR aggregate_id <> concat(
                        payload->>'source_region',':',payload->>'source_id'
                    )
                  )
                LIMIT 1
            ) THEN
                RAISE EXCEPTION 'COMPAT_WRITER_NOT_READY';
            END IF;

            IF EXISTS (
                SELECT 1
                FROM public.lesson_source_region_backfill_manifest manifest
                LEFT JOIN public.lesson_source_wide lesson
                  ON lesson."课程id"=manifest.course_id
                WHERE lesson."课程id" IS NULL
                   OR (lesson.source_region IS NOT NULL
                       AND lesson.source_region<>manifest.source_region)
                LIMIT 1
            ) THEN
                RAISE EXCEPTION 'COMPAT_REGION_EVIDENCE_CONFLICT';
            END IF;

            IF EXISTS (
                SELECT 1
                FROM public.lesson_source_wide lesson
                LEFT JOIN public.lesson_source_region_backfill_manifest manifest
                  ON manifest.course_id=lesson."课程id"
                WHERE lesson.source_region IS NULL
                  AND manifest.course_id IS NULL
                LIMIT 1
            ) THEN
                RAISE EXCEPTION 'COMPAT_REGION_MISSING';
            END IF;
        END
        $lesson_region_contract_preflight$;

        -- Region assignment is a one-time identity contraction, not a lesson
        -- business change.  The expand Outbox trigger deliberately rejects
        -- identity mutation, and the legacy privacy trigger still relies on
        -- source mirrors/GUCs.  Under the exclusive table lock, suspend only
        -- those two legacy triggers while the audited manifest is applied;
        -- the expand region guard remains active and accepts only dom/ovs.
        ALTER TABLE public.lesson_source_wide
            DISABLE TRIGGER trg_lesson_source_wide_outbox_v1;
        ALTER TABLE public.lesson_source_wide
            DISABLE TRIGGER guard_dom_lesson_student_privacy_v1;

        UPDATE public.lesson_source_wide lesson
        SET source_region=manifest.source_region
        FROM public.lesson_source_region_backfill_manifest manifest
        WHERE lesson."课程id"=manifest.course_id
          AND lesson.source_region IS NULL;

        ALTER TABLE public.lesson_source_wide
            ENABLE TRIGGER guard_dom_lesson_student_privacy_v1;
        ALTER TABLE public.lesson_source_wide
            ENABLE TRIGGER trg_lesson_source_wide_outbox_v1;

        DO $lesson_region_contract_wide_verify$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM public.lesson_source_wide
                WHERE source_region IS NULL
                   OR source_region NOT IN ('dom','ovs')
                LIMIT 1
            ) THEN
                RAISE EXCEPTION 'COMPAT_REGION_MISSING';
            END IF;
            IF EXISTS (
                SELECT 1 FROM public.lesson_source_wide
                WHERE (source_region='dom' AND "学员id" IS NOT NULL
                       AND "学员id" !~ '^dom:v1:[0-9a-f]{{64}}$')
                   OR (source_region='ovs' AND "学员id" LIKE 'dom:%')
                LIMIT 1
            ) THEN
                RAISE EXCEPTION 'COMPAT_REGION_STUDENT_PRIVACY_INVALID';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM public.lesson_source_wide
                GROUP BY source_region,"课程id"
                HAVING count(*)<>1
            ) THEN
                RAISE EXCEPTION 'COMPAT_REGION_DUPLICATE';
            END IF;
        END
        $lesson_region_contract_wide_verify$;

        UPDATE public.lesson_score_results result
        SET lesson_source_region=lesson.source_region
        FROM public.lesson_source_wide lesson
        WHERE lesson."课程id"=result.lesson_id
          AND result.lesson_source_region IS NULL;

        UPDATE public.personalized_trigger_matches match
        SET lesson_source_region=lesson.source_region
        FROM public.lesson_source_wide lesson
        WHERE lesson."课程id"=match.lesson_id
          AND match.lesson_id IS NOT NULL
          AND match.lesson_source_region IS NULL;

        DO $lesson_region_contract_fk_verify$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM public.lesson_score_results result
                LEFT JOIN public.lesson_source_wide lesson
                  ON lesson.source_region=result.lesson_source_region
                 AND lesson."课程id"=result.lesson_id
                WHERE result.lesson_source_region NOT IN ('dom','ovs')
                   OR lesson."课程id" IS NULL
                LIMIT 1
            ) THEN
                RAISE EXCEPTION 'COMPAT_REGION_SCORE_MAPPING_INVALID';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM public.personalized_trigger_matches match
                LEFT JOIN public.lesson_source_wide lesson
                  ON lesson.source_region=match.lesson_source_region
                 AND lesson."课程id"=match.lesson_id
                WHERE (match.lesson_id IS NULL)
                        <> (match.lesson_source_region IS NULL)
                   OR (match.lesson_id IS NOT NULL
                       AND lesson."课程id" IS NULL)
                LIMIT 1
            ) THEN
                RAISE EXCEPTION 'COMPAT_REGION_MATCH_MAPPING_INVALID';
            END IF;
        END
        $lesson_region_contract_fk_verify$;
        """
    )


def _replace_keys_and_foreign_keys() -> None:
    op.drop_constraint(
        "fk_lesson_score_result_source_lesson",
        "lesson_score_results",
        schema="public",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_personalized_trigger_match_lesson",
        "personalized_trigger_matches",
        schema="public",
        type_="foreignkey",
    )
    op.drop_constraint(
        "lesson_score_results_pkey",
        "lesson_score_results",
        schema="public",
        type_="primary",
    )
    op.drop_constraint(
        "lesson_source_wide_pkey",
        "lesson_source_wide",
        schema="public",
        type_="primary",
    )
    op.drop_constraint(
        "uq_lesson_source_wide_region_course_candidate",
        "lesson_source_wide",
        schema="public",
        type_="unique",
    )
    op.drop_constraint(
        "ck_lesson_source_wide_region_expand",
        "lesson_source_wide",
        schema="public",
        type_="check",
    )
    op.alter_column(
        "lesson_source_wide",
        "source_region",
        existing_type=sa.String(length=8),
        nullable=False,
        schema="public",
    )
    op.alter_column(
        "lesson_source_wide",
        "老师id",
        existing_type=sa.String(length=64),
        nullable=True,
        schema="public",
    )
    op.create_check_constraint(
        "ck_lesson_source_wide_region",
        "lesson_source_wide",
        "source_region IN ('dom','ovs')",
        schema="public",
    )
    op.create_primary_key(
        "pk_lesson_source_wide",
        "lesson_source_wide",
        ["source_region", "课程id"],
        schema="public",
    )

    op.drop_constraint(
        "ck_lesson_score_result_compat_region_expand",
        "lesson_score_results",
        schema="public",
        type_="check",
    )
    op.alter_column(
        "lesson_score_results",
        "lesson_source_region",
        existing_type=sa.String(length=8),
        nullable=False,
        schema="public",
    )
    op.create_check_constraint(
        "ck_lesson_score_result_compat_region",
        "lesson_score_results",
        "lesson_source_region IN ('dom','ovs')",
        schema="public",
    )
    op.create_primary_key(
        "pk_lesson_score_results_compat",
        "lesson_score_results",
        ["lesson_source_region", "lesson_id"],
        schema="public",
    )
    op.create_foreign_key(
        "fk_lesson_score_result_source_lesson_region",
        "lesson_score_results",
        "lesson_source_wide",
        ["lesson_source_region", "lesson_id"],
        ["source_region", "课程id"],
        source_schema="public",
        referent_schema="public",
        ondelete="CASCADE",
    )

    op.drop_constraint(
        "ck_trigger_match_compat_region_expand",
        "personalized_trigger_matches",
        schema="public",
        type_="check",
    )
    op.create_check_constraint(
        "ck_trigger_match_compat_lesson_pair",
        "personalized_trigger_matches",
        "(lesson_id IS NULL AND lesson_source_region IS NULL) OR "
        "(lesson_id IS NOT NULL AND lesson_source_region IN ('dom','ovs'))",
        schema="public",
    )
    op.create_foreign_key(
        "fk_personalized_trigger_match_lesson_region",
        "personalized_trigger_matches",
        "lesson_source_wide",
        ["lesson_source_region", "lesson_id"],
        ["source_region", "课程id"],
        source_schema="public",
        referent_schema="public",
        ondelete="SET NULL",
    )

    # Regionless indexes make it too easy to accidentally reintroduce an
    # unqualified read plan.  The expand revision already installed equivalent
    # region-leading indexes.
    op.drop_index(
        "ix_lesson_source_wide_teacher_time",
        table_name="lesson_source_wide",
        schema="public",
    )
    op.drop_index(
        "ix_lesson_source_wide_teacher_student_time",
        table_name="lesson_source_wide",
        schema="public",
    )


def _install_contract_guards() -> None:
    op.execute(
        """
        DROP TRIGGER IF EXISTS trg_00_lesson_source_region_expand_v1
        ON public.lesson_source_wide;
        DROP FUNCTION IF EXISTS public.guard_lesson_source_region_expand_v1();
        REVOKE ALL ON FUNCTION public.set_lesson_source_region_context_v1(text)
        FROM tit_dts_ingest_runtime;

        CREATE FUNCTION public.guard_lesson_source_region_contract_v1()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $function$
        BEGIN
            IF NEW.source_region IS NULL
               OR NEW.source_region NOT IN ('dom','ovs') THEN
                RAISE EXCEPTION 'COMPAT_REGION_MISSING'
                    USING ERRCODE='23514';
            END IF;
            IF TG_OP='UPDATE'
               AND ROW(NEW.source_region,NEW."课程id") IS DISTINCT FROM
                   ROW(OLD.source_region,OLD."课程id") THEN
                RAISE EXCEPTION 'COMPAT_LESSON_IDENTITY_IMMUTABLE'
                    USING ERRCODE='23514';
            END IF;
            RETURN NEW;
        END
        $function$;
        REVOKE ALL ON FUNCTION public.guard_lesson_source_region_contract_v1()
        FROM PUBLIC;
        CREATE TRIGGER trg_00_lesson_source_region_contract_v1
        BEFORE INSERT OR UPDATE ON public.lesson_source_wide
        FOR EACH ROW EXECUTE FUNCTION public.guard_lesson_source_region_contract_v1();

        CREATE OR REPLACE FUNCTION public.guard_dom_lesson_student_privacy_v1()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $function$
        BEGIN
            IF NEW.source_region IS NULL
               OR NEW.source_region NOT IN ('dom','ovs') THEN
                RAISE EXCEPTION 'COMPAT_REGION_MISSING'
                    USING ERRCODE='23514';
            END IF;
            IF NEW.source_region='dom'
               AND NEW."学员id" IS NOT NULL
               AND NEW."学员id" !~ '^dom:v1:[0-9a-f]{64}$' THEN
                RAISE EXCEPTION
                    'domestic lesson wide state contains a forbidden student identifier'
                    USING ERRCODE='23514';
            END IF;
            IF NEW.source_region='ovs'
               AND NEW."学员id" LIKE 'dom:%' THEN
                RAISE EXCEPTION
                    'overseas lesson wide state contains a domestic student token'
                    USING ERRCODE='23514';
            END IF;
            RETURN NEW;
        END
        $function$;
        REVOKE ALL ON FUNCTION public.guard_dom_lesson_student_privacy_v1()
        FROM PUBLIC;

        CREATE FUNCTION public.guard_lesson_region_manifest_append_only_v1()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $function$
        BEGIN
            RAISE EXCEPTION 'COMPAT_REGION_MANIFEST_IMMUTABLE'
                USING ERRCODE='55000';
        END
        $function$;
        REVOKE ALL ON FUNCTION
            public.guard_lesson_region_manifest_append_only_v1()
        FROM PUBLIC;
        CREATE TRIGGER trg_guard_lesson_region_manifest_append_only_v1
        BEFORE UPDATE OR DELETE
        ON public.lesson_source_region_backfill_manifest
        FOR EACH ROW EXECUTE FUNCTION
            public.guard_lesson_region_manifest_append_only_v1();

        CREATE FUNCTION public.guard_score_entry_projection_contract_v1()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
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
            public.guard_score_entry_projection_contract_v1()
        FROM PUBLIC;
        CREATE TRIGGER trg_guard_score_entry_projection_contract_v1
        BEFORE INSERT ON public.score_entries
        FOR EACH ROW EXECUTE FUNCTION
            public.guard_score_entry_projection_contract_v1();

        DO $lesson_region_contract_mark_complete$
        BEGIN
            UPDATE public.lesson_source_region_migration_control
            SET phase='CONTRACTED',contracted_at=clock_timestamp(),
                row_version=row_version+1,updated_at=clock_timestamp()
            WHERE control_id='PRIMARY' AND phase='WRITER_READY';
            IF NOT FOUND THEN
                RAISE EXCEPTION 'COMPAT_WRITER_NOT_READY';
            END IF;
        END
        $lesson_region_contract_mark_complete$;
        """
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    _lock_and_apply_authoritative_manifest()
    _replace_keys_and_foreign_keys()
    _install_contract_guards()


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        """
        LOCK TABLE public.lesson_source_wide IN ACCESS EXCLUSIVE MODE;
        LOCK TABLE public.lesson_score_results IN ACCESS EXCLUSIVE MODE;
        LOCK TABLE public.personalized_trigger_matches IN ACCESS EXCLUSIVE MODE;
        DO $lesson_region_contract_downgrade_guard$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM public.lesson_source_wide
                GROUP BY "课程id" HAVING count(*)>1
            ) THEN
                RAISE EXCEPTION 'COMPAT_REGION_DOWNGRADE_ID_COLLISION';
            END IF;
        END
        $lesson_region_contract_downgrade_guard$;
        DROP TRIGGER IF EXISTS trg_guard_score_entry_projection_contract_v1
            ON public.score_entries;
        DROP FUNCTION IF EXISTS public.guard_score_entry_projection_contract_v1();
        DROP TRIGGER IF EXISTS trg_guard_lesson_region_manifest_append_only_v1
            ON public.lesson_source_region_backfill_manifest;
        DROP FUNCTION IF EXISTS
            public.guard_lesson_region_manifest_append_only_v1();
        DROP TRIGGER IF EXISTS trg_00_lesson_source_region_contract_v1
            ON public.lesson_source_wide;
        DROP FUNCTION IF EXISTS public.guard_lesson_source_region_contract_v1();
        """
    )
    op.create_index(
        "ix_lesson_source_wide_teacher_time",
        "lesson_source_wide",
        ["老师id", "上课日期", "上课时间"],
        unique=False,
        schema="public",
    )
    op.create_index(
        "ix_lesson_source_wide_teacher_student_time",
        "lesson_source_wide",
        ["老师id", "学员id", "上课日期", "上课时间"],
        unique=False,
        schema="public",
    )
    op.drop_constraint(
        "fk_personalized_trigger_match_lesson_region",
        "personalized_trigger_matches",
        schema="public",
        type_="foreignkey",
    )
    op.drop_constraint(
        "ck_trigger_match_compat_lesson_pair",
        "personalized_trigger_matches",
        schema="public",
        type_="check",
    )
    op.create_check_constraint(
        "ck_trigger_match_compat_region_expand",
        "personalized_trigger_matches",
        "lesson_source_region IS NULL OR lesson_source_region IN ('dom','ovs')",
        schema="public",
    )
    op.drop_constraint(
        "fk_lesson_score_result_source_lesson_region",
        "lesson_score_results",
        schema="public",
        type_="foreignkey",
    )
    op.drop_constraint(
        "pk_lesson_score_results_compat",
        "lesson_score_results",
        schema="public",
        type_="primary",
    )
    op.drop_constraint(
        "ck_lesson_score_result_compat_region",
        "lesson_score_results",
        schema="public",
        type_="check",
    )
    op.alter_column(
        "lesson_score_results",
        "lesson_source_region",
        existing_type=sa.String(length=8),
        nullable=True,
        schema="public",
    )
    op.create_check_constraint(
        "ck_lesson_score_result_compat_region_expand",
        "lesson_score_results",
        "lesson_source_region IS NULL OR lesson_source_region IN ('dom','ovs')",
        schema="public",
    )
    op.create_primary_key(
        "lesson_score_results_pkey",
        "lesson_score_results",
        ["lesson_id"],
        schema="public",
    )
    op.drop_constraint(
        "pk_lesson_source_wide",
        "lesson_source_wide",
        schema="public",
        type_="primary",
    )
    op.drop_constraint(
        "ck_lesson_source_wide_region",
        "lesson_source_wide",
        schema="public",
        type_="check",
    )
    op.alter_column(
        "lesson_source_wide",
        "老师id",
        existing_type=sa.String(length=64),
        nullable=False,
        schema="public",
    )
    op.alter_column(
        "lesson_source_wide",
        "source_region",
        existing_type=sa.String(length=8),
        nullable=True,
        schema="public",
    )
    op.create_check_constraint(
        "ck_lesson_source_wide_region_expand",
        "lesson_source_wide",
        "source_region IS NULL OR source_region IN ('dom','ovs')",
        schema="public",
    )
    op.create_primary_key(
        "lesson_source_wide_pkey",
        "lesson_source_wide",
        ["课程id"],
        schema="public",
    )
    op.create_unique_constraint(
        "uq_lesson_source_wide_region_course_candidate",
        "lesson_source_wide",
        ["source_region", "课程id"],
        schema="public",
    )
    op.create_foreign_key(
        "fk_lesson_score_result_source_lesson",
        "lesson_score_results",
        "lesson_source_wide",
        ["lesson_id"],
        ["课程id"],
        source_schema="public",
        referent_schema="public",
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_personalized_trigger_match_lesson",
        "personalized_trigger_matches",
        "lesson_source_wide",
        ["lesson_id"],
        ["课程id"],
        source_schema="public",
        referent_schema="public",
        ondelete="SET NULL",
    )
    op.execute(
        """
        UPDATE public.lesson_source_region_migration_control
        SET phase='WRITER_READY',contracted_at=NULL,row_version=row_version+1,
            updated_at=clock_timestamp()
        WHERE control_id='PRIMARY' AND phase='CONTRACTED';

        CREATE FUNCTION public.guard_lesson_source_region_expand_v1()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE context_region text := NULLIF(
            current_setting('tit.dts_source_region',true),''
        );
        BEGIN
            IF TG_OP='UPDATE' AND NEW."课程id" IS DISTINCT FROM OLD."课程id" THEN
                RAISE EXCEPTION 'COMPAT_COURSE_ID_IMMUTABLE';
            END IF;
            IF NEW.source_region IS NULL THEN
                IF context_region IS NULL
                   OR context_region NOT IN ('dom','ovs') THEN
                    RAISE EXCEPTION 'COMPAT_REGION_MISSING';
                END IF;
                NEW.source_region := context_region;
            END IF;
            IF NEW.source_region IS NULL
               OR NEW.source_region NOT IN ('dom','ovs')
               OR (context_region IN ('dom','ovs')
                   AND NEW.source_region<>context_region) THEN
                RAISE EXCEPTION 'COMPAT_REGION_INVALID';
            END IF;
            IF TG_OP='UPDATE' AND OLD.source_region IS NOT NULL
               AND NEW.source_region IS DISTINCT FROM OLD.source_region THEN
                RAISE EXCEPTION 'COMPAT_REGION_IMMUTABLE';
            END IF;
            RETURN NEW;
        END
        $function$;
        CREATE TRIGGER trg_00_lesson_source_region_expand_v1
        BEFORE INSERT OR UPDATE ON public.lesson_source_wide
        FOR EACH ROW EXECUTE FUNCTION public.guard_lesson_source_region_expand_v1();
        GRANT EXECUTE ON FUNCTION
            public.set_lesson_source_region_context_v1(text)
        TO tit_dts_ingest_runtime;
        """
    )
