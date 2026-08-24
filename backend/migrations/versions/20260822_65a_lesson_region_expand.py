"""expand the v1 lesson compatibility identity with an explicit region.

Revision ID: 20260822_65a_lesson_region_exp
Revises: 20260819_65_g09_set_course
Create Date: 2026-08-22

This is the first half of the frozen expand/contract sequence.  It is safe to
install before the compatibility binary because the original single-column
primary key and foreign keys remain in force.  New writers must nevertheless
send ``source_region`` explicitly (or use the transaction-local compatibility
context); no database default is installed and no existing row is guessed.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260822_65a_lesson_region_exp"
down_revision: Union[str, None] = "20260819_65_g09_set_course"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


WRITER_CONTRACT_VERSION = "lesson-region-compat-v1"


def _assert_preconditions() -> None:
    op.execute(
        """
        DO $lesson_region_expand_preflight$
        BEGIN
            IF to_regclass('public.lesson_source_wide') IS NULL
               OR to_regclass('public.lesson_score_results') IS NULL
               OR to_regclass('public.personalized_trigger_matches') IS NULL THEN
                RAISE EXCEPTION 'COMPAT_REGION_SCHEMA_MISSING';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'lesson_source_wide'
                  AND column_name = 'source_region'
            ) THEN
                RAISE EXCEPTION 'COMPAT_REGION_EXPAND_ALREADY_PRESENT';
            END IF;
            IF NOT EXISTS (
                SELECT 1
                FROM pg_roles
                WHERE rolname = 'tit_dts_ingest_runtime'
                  AND rolcanlogin
                  AND NOT rolsuper
                  AND NOT rolcreatedb
                  AND NOT rolcreaterole
                  AND NOT rolreplication
                  AND NOT rolbypassrls
            ) THEN
                RAISE EXCEPTION 'COMPAT_REGION_INGEST_ROLE_INVALID';
            END IF;
        END
        $lesson_region_expand_preflight$;
        """
    )


def _expand_columns_and_indexes() -> None:
    op.add_column(
        "lesson_source_wide",
        sa.Column("source_region", sa.String(length=8), nullable=True),
        schema="public",
    )
    op.create_check_constraint(
        "ck_lesson_source_wide_region_expand",
        "lesson_source_wide",
        "source_region IS NULL OR source_region IN ('dom','ovs')",
        schema="public",
    )
    # PostgreSQL can use this non-partial unique constraint as the target of
    # nullable composite FKs during the expand window.  NULL remains available
    # only for rows that pre-date the authoritative backfill.
    op.create_unique_constraint(
        "uq_lesson_source_wide_region_course_candidate",
        "lesson_source_wide",
        ["source_region", "课程id"],
        schema="public",
    )
    op.create_index(
        "ix_lesson_source_wide_region_teacher_time",
        "lesson_source_wide",
        ["source_region", "老师id", "上课日期", "上课时间", "课程id"],
        unique=False,
        schema="public",
    )
    op.create_index(
        "ix_lesson_source_wide_region_teacher_student_time",
        "lesson_source_wide",
        [
            "source_region",
            "老师id",
            "学员id",
            "上课日期",
            "上课时间",
            "课程id",
        ],
        unique=False,
        schema="public",
    )

    op.add_column(
        "lesson_score_results",
        sa.Column("lesson_source_region", sa.String(length=8), nullable=True),
        schema="public",
    )
    op.create_check_constraint(
        "ck_lesson_score_result_compat_region_expand",
        "lesson_score_results",
        "lesson_source_region IS NULL OR lesson_source_region IN ('dom','ovs')",
        schema="public",
    )
    op.create_index(
        "ix_lesson_score_result_compat_region_lesson",
        "lesson_score_results",
        ["lesson_source_region", "lesson_id"],
        unique=False,
        schema="public",
    )

    op.add_column(
        "personalized_trigger_matches",
        sa.Column("lesson_source_region", sa.String(length=8), nullable=True),
        schema="public",
    )
    op.create_check_constraint(
        "ck_trigger_match_compat_region_expand",
        "personalized_trigger_matches",
        "lesson_source_region IS NULL OR lesson_source_region IN ('dom','ovs')",
        schema="public",
    )
    op.create_index(
        "ix_trigger_match_compat_region_lesson",
        "personalized_trigger_matches",
        ["lesson_source_region", "lesson_id"],
        unique=False,
        schema="public",
    )

    # Add the frozen typed score provenance surface before any compatibility
    # writer may claim readiness.  Existing rows remain explicitly unresolved;
    # the contract/cutover migrations must not infer their origin.
    op.add_column(
        "score_entries",
        sa.Column("source_region", sa.String(length=8), nullable=True),
        schema="public",
    )
    op.add_column(
        "score_entries",
        sa.Column("source_appoint_id", sa.String(length=512), nullable=True),
        schema="public",
    )
    op.add_column(
        "score_entries",
        sa.Column("participation_seq", sa.Integer(), nullable=True),
        schema="public",
    )
    op.add_column(
        "score_entries",
        sa.Column("projection_origin", sa.String(length=32), nullable=True),
        schema="public",
    )
    op.add_column(
        "score_entries",
        sa.Column("materialized_by_run_id", sa.String(length=160), nullable=True),
        schema="public",
    )
    op.add_column(
        "score_entries",
        sa.Column("projection_generation", sa.BigInteger(), nullable=True),
        schema="public",
    )
    op.create_check_constraint(
        "ck_score_entry_source_identity_expand",
        "score_entries",
        "(source_region IS NULL AND source_appoint_id IS NULL) OR "
        "(source_region IN ('dom','ovs') AND source_appoint_id IS NOT NULL "
        "AND btrim(source_appoint_id) <> '')",
        schema="public",
    )
    op.create_check_constraint(
        "ck_score_entry_projection_origin_expand",
        "score_entries",
        "projection_origin IS NULL OR projection_origin IN ("
        "'LEGACY_EXISTING','V1_COMPAT_LIVE','FIXED_TASK_LIVE',"
        "'CUTOVER_CREATED','V2_LIVE','ROLLBACK_CREATED')",
        schema="public",
    )
    op.create_index(
        "ix_score_entry_source_course_expand",
        "score_entries",
        ["source_region", "source_appoint_id", "participation_seq"],
        unique=False,
        schema="public",
    )


def _create_evidence_and_control_tables() -> None:
    op.create_table(
        "lesson_source_region_backfill_manifest",
        sa.Column("course_id", sa.String(length=128), nullable=False),
        sa.Column("source_region", sa.String(length=8), nullable=False),
        sa.Column("evidence_kind", sa.String(length=40), nullable=False),
        sa.Column("evidence_ref", sa.Text(), nullable=False),
        sa.Column("evidence_sha256", sa.String(length=64), nullable=False),
        sa.Column("approved_by", sa.String(length=128), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint(
            "course_id", name="pk_lesson_source_region_backfill_manifest"
        ),
        sa.CheckConstraint(
            "source_region IN ('dom','ovs')",
            name="ck_lesson_region_manifest_region",
        ),
        sa.CheckConstraint(
            "evidence_kind IN ("
            "'SOURCE_APPOINT_SNAPSHOT','DTS_LEDGER','MANUAL_VERIFIED')",
            name="ck_lesson_region_manifest_evidence_kind",
        ),
        sa.CheckConstraint(
            "btrim(evidence_ref) <> '' "
            "AND evidence_sha256 ~ '^[0-9a-f]{64}$' "
            "AND btrim(approved_by) <> ''",
            name="ck_lesson_region_manifest_evidence",
        ),
        schema="public",
    )
    op.create_table(
        "lesson_source_region_migration_control",
        sa.Column("control_id", sa.String(length=16), nullable=False),
        sa.Column("phase", sa.String(length=24), nullable=False),
        sa.Column("writer_contract_version", sa.String(length=64), nullable=True),
        sa.Column("writer_smoke_sha256", sa.String(length=64), nullable=True),
        sa.Column("approved_by", sa.String(length=128), nullable=True),
        sa.Column("writer_ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("contracted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "row_version",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint(
            "control_id", name="pk_lesson_source_region_migration_control"
        ),
        sa.CheckConstraint(
            "control_id = 'PRIMARY'",
            name="ck_lesson_region_control_singleton",
        ),
        sa.CheckConstraint(
            "phase IN ('EXPANDED','WRITER_READY','CONTRACTED')",
            name="ck_lesson_region_control_phase",
        ),
        sa.CheckConstraint(
            "(phase = 'EXPANDED' AND writer_contract_version IS NULL "
            "AND writer_smoke_sha256 IS NULL AND approved_by IS NULL "
            "AND writer_ready_at IS NULL AND contracted_at IS NULL) OR "
            "(phase = 'WRITER_READY' AND writer_contract_version IS NOT NULL "
            "AND writer_smoke_sha256 ~ '^[0-9a-f]{64}$' "
            "AND approved_by IS NOT NULL AND btrim(approved_by) <> '' "
            "AND writer_ready_at IS NOT NULL AND contracted_at IS NULL) OR "
            "(phase = 'CONTRACTED' AND writer_contract_version IS NOT NULL "
            "AND writer_smoke_sha256 ~ '^[0-9a-f]{64}$' "
            "AND approved_by IS NOT NULL AND writer_ready_at IS NOT NULL "
            "AND contracted_at IS NOT NULL)",
            name="ck_lesson_region_control_shape",
        ),
        schema="public",
    )
    op.execute(
        """
        INSERT INTO public.lesson_source_region_migration_control (
            control_id, phase
        ) VALUES ('PRIMARY','EXPANDED');

        REVOKE ALL PRIVILEGES ON TABLE
            public.lesson_source_region_backfill_manifest,
            public.lesson_source_region_migration_control
        FROM PUBLIC, tit_dts_ingest_runtime;

        DO $lesson_region_expand_optional_acl$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='tit_growth_app') THEN
                REVOKE ALL PRIVILEGES ON TABLE
                    public.lesson_source_region_backfill_manifest,
                    public.lesson_source_region_migration_control
                FROM tit_growth_app;
                GRANT SELECT ON TABLE
                    public.lesson_source_region_migration_control
                TO tit_growth_app;
            END IF;
        END
        $lesson_region_expand_optional_acl$;
        """
    )


def _create_context_and_evidence_functions() -> None:
    op.execute(
        f"""
        CREATE FUNCTION public.set_lesson_source_region_context_v1(
            p_source_region text
        ) RETURNS text
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        BEGIN
            IF p_source_region IS NULL
               OR p_source_region NOT IN ('dom','ovs') THEN
                RAISE EXCEPTION 'COMPAT_REGION_INVALID'
                    USING ERRCODE='23514';
            END IF;
            PERFORM set_config('tit.dts_source_region',p_source_region,true);
            RETURN p_source_region;
        END
        $function$;

        CREATE FUNCTION public.stage_lesson_source_region_backfill_v1(
            p_course_id text,
            p_source_region text,
            p_evidence_kind text,
            p_evidence_ref text,
            p_evidence_sha256 text,
            p_approved_by text
        ) RETURNS void
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE existing_region text;
        BEGIN
            IF p_course_id IS NULL OR btrim(p_course_id) = ''
               OR p_source_region IS NULL
               OR p_source_region NOT IN ('dom','ovs')
               OR p_evidence_kind IS NULL
               OR p_evidence_kind NOT IN (
                    'SOURCE_APPOINT_SNAPSHOT','DTS_LEDGER','MANUAL_VERIFIED'
               )
               OR p_evidence_ref IS NULL OR btrim(p_evidence_ref) = ''
               OR p_evidence_sha256 IS NULL
               OR p_evidence_sha256 !~ '^[0-9a-f]{{64}}$'
               OR p_approved_by IS NULL OR btrim(p_approved_by) = '' THEN
                RAISE EXCEPTION 'COMPAT_REGION_EVIDENCE_INVALID'
                    USING ERRCODE='23514';
            END IF;
            PERFORM pg_advisory_xact_lock(
                hashtextextended('lesson-region-manifest:' || p_course_id,0)
            );
            SELECT source_region INTO existing_region
            FROM public.lesson_source_wide
            WHERE "课程id" = p_course_id
            FOR SHARE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'COMPAT_REGION_LESSON_NOT_FOUND:%',p_course_id;
            END IF;
            IF existing_region IS NOT NULL
               AND existing_region <> p_source_region THEN
                RAISE EXCEPTION 'COMPAT_REGION_EVIDENCE_CONFLICT:%',p_course_id;
            END IF;
            INSERT INTO public.lesson_source_region_backfill_manifest (
                course_id,source_region,evidence_kind,evidence_ref,
                evidence_sha256,approved_by,approved_at
            ) VALUES (
                p_course_id,p_source_region,p_evidence_kind,p_evidence_ref,
                p_evidence_sha256,p_approved_by,clock_timestamp()
            )
            ON CONFLICT (course_id) DO UPDATE SET
                source_region=EXCLUDED.source_region,
                evidence_kind=EXCLUDED.evidence_kind,
                evidence_ref=EXCLUDED.evidence_ref,
                evidence_sha256=EXCLUDED.evidence_sha256,
                approved_by=EXCLUDED.approved_by,
                approved_at=EXCLUDED.approved_at
            WHERE public.lesson_source_region_backfill_manifest.source_region
                      = EXCLUDED.source_region;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'COMPAT_REGION_EVIDENCE_CONFLICT:%',p_course_id;
            END IF;
        END
        $function$;

        CREATE FUNCTION public.confirm_lesson_region_compat_writer_v1(
            p_writer_contract_version text,
            p_writer_smoke_sha256 text,
            p_approved_by text
        ) RETURNS void
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE control_record public.lesson_source_region_migration_control%ROWTYPE;
        BEGIN
            IF p_writer_contract_version IS DISTINCT FROM
                   '{WRITER_CONTRACT_VERSION}'
               OR p_writer_smoke_sha256 IS NULL
               OR p_writer_smoke_sha256 !~ '^[0-9a-f]{{64}}$'
               OR p_approved_by IS NULL OR btrim(p_approved_by) = '' THEN
                RAISE EXCEPTION 'COMPAT_WRITER_EVIDENCE_INVALID'
                    USING ERRCODE='23514';
            END IF;
            SELECT * INTO control_record
            FROM public.lesson_source_region_migration_control
            WHERE control_id='PRIMARY'
            FOR UPDATE;
            IF NOT FOUND OR control_record.phase NOT IN (
                'EXPANDED','WRITER_READY'
            ) THEN
                RAISE EXCEPTION 'COMPAT_WRITER_NOT_READY';
            END IF;
            IF control_record.phase='WRITER_READY' THEN
                IF control_record.writer_contract_version
                       <> p_writer_contract_version
                   OR control_record.writer_smoke_sha256
                       <> p_writer_smoke_sha256 THEN
                    RAISE EXCEPTION 'COMPAT_WRITER_EVIDENCE_CONFLICT';
                END IF;
                RETURN;
            END IF;
            UPDATE public.lesson_source_region_migration_control
            SET phase='WRITER_READY',
                writer_contract_version=p_writer_contract_version,
                writer_smoke_sha256=p_writer_smoke_sha256,
                approved_by=p_approved_by,
                writer_ready_at=clock_timestamp(),
                row_version=row_version+1,
                updated_at=clock_timestamp()
            WHERE control_id='PRIMARY';
        END
        $function$;

        REVOKE ALL ON FUNCTION
            public.set_lesson_source_region_context_v1(text),
            public.stage_lesson_source_region_backfill_v1(
                text,text,text,text,text,text
            ),
            public.confirm_lesson_region_compat_writer_v1(text,text,text)
        FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION
            public.set_lesson_source_region_context_v1(text)
        TO tit_dts_ingest_runtime;
        """
    )


def _install_expand_writer_guard_and_outbox() -> None:
    op.execute(
        """
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
                RAISE EXCEPTION 'COMPAT_COURSE_ID_IMMUTABLE'
                    USING ERRCODE='23514';
            END IF;
            IF NEW.source_region IS NULL THEN
                IF context_region IS NULL
                   OR context_region NOT IN ('dom','ovs') THEN
                    RAISE EXCEPTION 'COMPAT_REGION_MISSING'
                        USING ERRCODE='23514';
                END IF;
                NEW.source_region := context_region;
            ELSIF NEW.source_region NOT IN ('dom','ovs') THEN
                RAISE EXCEPTION 'COMPAT_REGION_INVALID'
                    USING ERRCODE='23514';
            END IF;
            IF context_region IS NOT NULL
               AND context_region NOT IN ('dom','ovs') THEN
                RAISE EXCEPTION 'COMPAT_REGION_INVALID'
                    USING ERRCODE='23514';
            END IF;
            IF context_region IN ('dom','ovs')
               AND NEW.source_region <> context_region THEN
                RAISE EXCEPTION 'COMPAT_REGION_CONTEXT_MISMATCH'
                    USING ERRCODE='23514';
            END IF;
            IF TG_OP='UPDATE' AND OLD.source_region IS NOT NULL
               AND NEW.source_region IS DISTINCT FROM OLD.source_region THEN
                RAISE EXCEPTION 'COMPAT_REGION_IMMUTABLE'
                    USING ERRCODE='23514';
            END IF;
            RETURN NEW;
        END
        $function$;

        REVOKE ALL ON FUNCTION public.guard_lesson_source_region_expand_v1()
        FROM PUBLIC;
        CREATE TRIGGER trg_00_lesson_source_region_expand_v1
        BEFORE INSERT OR UPDATE ON public.lesson_source_wide
        FOR EACH ROW EXECUTE FUNCTION public.guard_lesson_source_region_expand_v1();

        CREATE OR REPLACE FUNCTION public.emit_source_wide_change_v1()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE
            old_row jsonb := '{}'::jsonb;
            new_row jsonb := '{}'::jsonb;
            changed_fields text[] := ARRAY[]::text[];
            source_table_name text;
            source_id_value text;
            source_region_value text;
            old_teacher_id_value text;
            new_teacher_id_value text;
            aggregate_type_value text;
            aggregate_id_value text;
            event_token text;
            event_time timestamptz := clock_timestamp();
        BEGIN
            IF TG_OP <> 'INSERT' THEN old_row := to_jsonb(OLD); END IF;
            IF TG_OP <> 'DELETE' THEN new_row := to_jsonb(NEW); END IF;
            IF TG_OP='UPDATE' THEN
                IF TG_TABLE_NAME='teacher_source_wide'
                   AND old_row->>'tchr_id' IS DISTINCT FROM new_row->>'tchr_id' THEN
                    RAISE EXCEPTION 'teacher_source_wide.tchr_id is immutable';
                ELSIF TG_TABLE_NAME='lesson_source_wide'
                   AND (old_row->>'课程id' IS DISTINCT FROM new_row->>'课程id'
                        OR old_row->>'source_region'
                           IS DISTINCT FROM new_row->>'source_region') THEN
                    RAISE EXCEPTION 'lesson_source_wide identity is immutable';
                END IF;
                SELECT COALESCE(array_agg(field_name ORDER BY field_name),ARRAY[]::text[])
                INTO changed_fields
                FROM (
                    SELECT key_name AS field_name
                    FROM jsonb_object_keys(old_row||new_row) keys(key_name)
                    WHERE old_row->key_name IS DISTINCT FROM new_row->key_name
                ) changed;
                IF cardinality(changed_fields)=0 THEN RETURN NEW; END IF;
            ELSIF TG_OP='INSERT' THEN
                SELECT COALESCE(array_agg(key_name ORDER BY key_name),ARRAY[]::text[])
                INTO changed_fields FROM jsonb_object_keys(new_row) keys(key_name);
            ELSE
                SELECT COALESCE(array_agg(key_name ORDER BY key_name),ARRAY[]::text[])
                INTO changed_fields FROM jsonb_object_keys(old_row) keys(key_name);
            END IF;

            source_table_name := TG_TABLE_NAME;
            IF TG_TABLE_NAME='teacher_source_wide' THEN
                source_id_value := COALESCE(new_row->>'tchr_id',old_row->>'tchr_id');
                source_region_value := NULL;
                old_teacher_id_value := old_row->>'tchr_id';
                new_teacher_id_value := new_row->>'tchr_id';
                aggregate_type_value := 'TEACHER_SOURCE_WIDE';
                aggregate_id_value := source_id_value;
            ELSIF TG_TABLE_NAME='lesson_source_wide' THEN
                source_id_value := COALESCE(new_row->>'课程id',old_row->>'课程id');
                source_region_value := COALESCE(
                    new_row->>'source_region',old_row->>'source_region'
                );
                IF source_region_value IS NULL
                   OR source_region_value NOT IN ('dom','ovs') THEN
                    RAISE EXCEPTION 'COMPAT_REGION_MISSING';
                END IF;
                old_teacher_id_value := old_row->>'老师id';
                new_teacher_id_value := new_row->>'老师id';
                aggregate_type_value := 'LESSON_SOURCE_WIDE';
                aggregate_id_value := source_region_value || ':' || source_id_value;
            ELSE
                RAISE EXCEPTION 'unsupported source-wide table: %',TG_TABLE_NAME;
            END IF;

            event_token := md5(concat_ws('|',source_table_name,TG_OP,
                COALESCE(source_region_value,''),source_id_value,
                txid_current()::text,event_time::text,pg_backend_pid()::text,
                random()::text));
            INSERT INTO public.outbox_events (
                outbox_id,event_id,aggregate_type,aggregate_id,event_type,
                payload,status,available_at,attempt_count,last_error,
                created_at,published_at
            ) VALUES (
                'OUT-SOURCE-'||event_token,'EVT-SOURCE-'||event_token,
                aggregate_type_value,aggregate_id_value,'source_wide.changed.v1',
                jsonb_build_object(
                    'source_table',source_table_name,
                    'source_region',source_region_value,
                    'source_id',source_id_value,'operation',TG_OP,
                    'changed_fields',changed_fields,
                    'old_teacher_id',old_teacher_id_value,
                    'new_teacher_id',new_teacher_id_value
                ),'PENDING',event_time,0,NULL,event_time,NULL
            );
            IF TG_OP='DELETE' THEN RETURN OLD; END IF;
            RETURN NEW;
        END
        $function$;
        """
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    _assert_preconditions()
    _expand_columns_and_indexes()
    _create_evidence_and_control_tables()
    _create_context_and_evidence_functions()
    _install_expand_writer_guard_and_outbox()


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        """
        DO $lesson_region_expand_downgrade_guard$
        BEGIN
            IF EXISTS (SELECT 1 FROM public.lesson_source_wide LIMIT 1)
               OR EXISTS (SELECT 1 FROM public.lesson_score_results LIMIT 1)
               OR EXISTS (
                    SELECT 1 FROM public.personalized_trigger_matches
                    WHERE lesson_id IS NOT NULL LIMIT 1
               )
               OR EXISTS (
                    SELECT 1 FROM public.score_entries
                    WHERE source_region IS NOT NULL
                       OR source_appoint_id IS NOT NULL
                       OR projection_origin IS NOT NULL
                    LIMIT 1
               ) THEN
                RAISE EXCEPTION 'COMPAT_REGION_DOWNGRADE_DATA_PRESENT';
            END IF;
        END
        $lesson_region_expand_downgrade_guard$;

        DROP TRIGGER IF EXISTS trg_00_lesson_source_region_expand_v1
        ON public.lesson_source_wide;
        DROP FUNCTION IF EXISTS public.guard_lesson_source_region_expand_v1();
        DROP FUNCTION IF EXISTS public.set_lesson_source_region_context_v1(text);
        DROP FUNCTION IF EXISTS public.stage_lesson_source_region_backfill_v1(
            text,text,text,text,text,text
        );
        DROP FUNCTION IF EXISTS public.confirm_lesson_region_compat_writer_v1(
            text,text,text
        );
        """
    )

    for name in (
        "ix_score_entry_source_course_expand",
    ):
        op.drop_index(name, table_name="score_entries", schema="public")
    op.drop_constraint(
        "ck_score_entry_projection_origin_expand",
        "score_entries",
        schema="public",
        type_="check",
    )
    op.drop_constraint(
        "ck_score_entry_source_identity_expand",
        "score_entries",
        schema="public",
        type_="check",
    )
    for column in (
        "projection_generation",
        "materialized_by_run_id",
        "projection_origin",
        "participation_seq",
        "source_appoint_id",
        "source_region",
    ):
        op.drop_column("score_entries", column, schema="public")

    op.drop_index(
        "ix_trigger_match_compat_region_lesson",
        table_name="personalized_trigger_matches",
        schema="public",
    )
    op.drop_constraint(
        "ck_trigger_match_compat_region_expand",
        "personalized_trigger_matches",
        schema="public",
        type_="check",
    )
    op.drop_column(
        "personalized_trigger_matches", "lesson_source_region", schema="public"
    )
    op.drop_index(
        "ix_lesson_score_result_compat_region_lesson",
        table_name="lesson_score_results",
        schema="public",
    )
    op.drop_constraint(
        "ck_lesson_score_result_compat_region_expand",
        "lesson_score_results",
        schema="public",
        type_="check",
    )
    op.drop_column("lesson_score_results", "lesson_source_region", schema="public")

    op.drop_table("lesson_source_region_migration_control", schema="public")
    op.drop_table("lesson_source_region_backfill_manifest", schema="public")
    op.drop_index(
        "ix_lesson_source_wide_region_teacher_student_time",
        table_name="lesson_source_wide",
        schema="public",
    )
    op.drop_index(
        "ix_lesson_source_wide_region_teacher_time",
        table_name="lesson_source_wide",
        schema="public",
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
    op.drop_column("lesson_source_wide", "source_region", schema="public")
