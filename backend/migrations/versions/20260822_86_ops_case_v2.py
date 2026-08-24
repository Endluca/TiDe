"""add protected DTS v2 completion and technical Ops Case contracts.

Revision ID: 20260822_86_ops_case_v2
Revises: 20260822_85_outbox_three_state
Create Date: 2026-08-22

This revision does not create a completion-correction decision or project a
business result.  It adds the protected Case identities/evidence fields and
the exact command surface already consumed by ``dts_v2_technical_cases.py``.
Outbox DEAD/PUBLISHED state remains the proof of technical failure/recovery.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260822_86_ops_case_v2"
down_revision: Union[str, None] = "20260822_85_outbox_three_state"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


OUTBOX_RUNTIME_ROLE = "tit_growth_app"
TECHNICAL_CASE_TYPES = (
    "DTS_DIRTY_KEY_DEAD",
    "DTS_SOURCE_CONFLICT",
    "FAVORITE_OBSERVATION_DEAD",
    "TASK_MATERIALIZATION_DEAD",
    "DOWNSTREAM_PROJECTION_DEAD",
)
PROTECTED_CASE_TYPES = (
    "COURSE_COMPLETION_CORRECTION",
    *TECHNICAL_CASE_TYPES,
)


def _quoted(values: tuple[str, ...]) -> str:
    return ",".join(f"'{value}'" for value in values)


def _assert_preconditions_and_role() -> None:
    protected = _quoted(PROTECTED_CASE_TYPES)
    op.execute(
        rf"""
        DO $ops_case_v2_preflight$
        BEGIN
            IF to_regclass('public.ops_cases') IS NULL
               OR to_regclass('public.ops_decisions') IS NULL
               OR to_regclass('public.outbox_events') IS NULL
               OR to_regclass('public.audit_events') IS NULL
               OR to_regprocedure(
                    'public.dts_canonical_json_sha256_v1(jsonb)'
                  ) IS NULL
               OR to_regprocedure(
                    'public.outbox_legacy_timestamp_v1(timestamptz)'
                  ) IS NULL
               OR to_regprocedure(
                    'public.dts_v2_source_position_valid(jsonb)'
                  ) IS NULL THEN
                RAISE EXCEPTION 'DTS_V2_OPS_CASE_SCHEMA_NOT_READY';
            END IF;
            IF EXISTS (
                SELECT 1 FROM public.ops_cases
                WHERE case_type IN ({protected})
            ) THEN
                RAISE EXCEPTION 'DTS_V2_PROTECTED_CASE_PREEXISTS';
            END IF;
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema='public' AND table_name='ops_cases'
                  AND column_name='case_revision'
            ) OR to_regprocedure(
                'public.record_dts_v2_technical_case('
                'text,text,text,text,text,text,text,text,text,bigint,text,'
                'integer,bigint)'
            ) IS NOT NULL THEN
                RAISE EXCEPTION 'DTS_V2_OPS_CASE_ALREADY_INSTALLED';
            END IF;
        END
        $ops_case_v2_preflight$;

        DO $ops_case_v2_runtime_role$
        BEGIN
            IF to_regrole('{OUTBOX_RUNTIME_ROLE}') IS NULL THEN
                RAISE EXCEPTION
                    'required application role is missing: {OUTBOX_RUNTIME_ROLE}';
            END IF;
            IF EXISTS (
                SELECT 1 FROM pg_roles
                WHERE rolname='{OUTBOX_RUNTIME_ROLE}'
                  AND (NOT rolcanlogin OR rolinherit OR rolsuper
                       OR rolcreatedb OR rolcreaterole OR rolreplication
                       OR rolbypassrls)
            ) THEN
                RAISE EXCEPTION
                    '{OUTBOX_RUNTIME_ROLE} must be a restricted NOINHERIT LOGIN role';
            END IF;
        END
        $ops_case_v2_runtime_role$;

        LOCK TABLE public.ops_cases,public.ops_decisions,
                   public.outbox_events
        IN ACCESS EXCLUSIVE MODE;
        """
    )


def _expand_schema() -> None:
    technical = _quoted(TECHNICAL_CASE_TYPES)
    protected = _quoted(PROTECTED_CASE_TYPES)
    op.alter_column(
        "ops_cases",
        "case_id",
        type_=sa.String(length=768),
        existing_type=sa.String(length=128),
        existing_nullable=False,
        schema="public",
    )
    op.alter_column(
        "ops_decisions",
        "case_id",
        type_=sa.String(length=768),
        existing_type=sa.String(length=128),
        existing_nullable=False,
        schema="public",
    )
    op.alter_column(
        "audit_events",
        "case_id",
        type_=sa.String(length=768),
        existing_type=sa.String(length=128),
        existing_nullable=True,
        schema="public",
    )
    op.alter_column(
        "ops_cases",
        "teacher_id",
        existing_type=sa.String(length=64),
        nullable=True,
        schema="public",
    )
    op.alter_column(
        "ops_cases",
        "source_reason",
        type_=sa.String(length=128),
        existing_type=sa.String(length=64),
        existing_nullable=True,
        schema="public",
    )
    for column in (
        sa.Column("source_ref", sa.String(length=768), nullable=True),
        sa.Column("source_region", sa.String(length=8), nullable=True),
        sa.Column("source_appoint_id", sa.String(length=512), nullable=True),
        sa.Column(
            "case_revision",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "row_version",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("evidence_fingerprint", sa.String(length=64), nullable=True),
        sa.Column(
            "recovery_evidence_count",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "last_recovery_event_id", sa.String(length=512), nullable=True
        ),
        sa.Column("last_recovery_count", sa.BigInteger(), nullable=True),
        sa.Column(
            "last_recovered_at", sa.DateTime(timezone=True), nullable=True
        ),
    ):
        op.add_column("ops_cases", column, schema="public")

    for column in (
        sa.Column("expected_case_revision", sa.Integer(), nullable=True),
        sa.Column(
            "expected_conflict_fingerprint",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column(
            "expected_source_revision", sa.BigInteger(), nullable=True
        ),
        sa.Column(
            "expected_source_position", postgresql.JSONB(), nullable=True
        ),
        sa.Column(
            "downstream_projection_status",
            sa.String(length=24),
            nullable=True,
        ),
        sa.Column("projection_event_ids", postgresql.JSONB(), nullable=True),
        sa.Column(
            "row_version",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    ):
        op.add_column("ops_decisions", column, schema="public")

    op.create_table(
        "ops_case_recovery_events",
        sa.Column("recovery_event_id", sa.String(length=160), nullable=False),
        sa.Column("case_id", sa.String(length=768), nullable=False),
        sa.Column("source_ref", sa.String(length=768), nullable=False),
        sa.Column("event_id", sa.String(length=512), nullable=False),
        sa.Column("recovery_count", sa.BigInteger(), nullable=False),
        sa.Column("work_status", sa.String(length=24), nullable=False),
        sa.Column("outbox_payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("outbox_row_version", sa.BigInteger(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("case_status_before", sa.String(length=32), nullable=False),
        sa.Column("case_status_after", sa.String(length=32), nullable=False),
        sa.Column("case_revision_after", sa.Integer(), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        sa.Column("evidence_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint(
            "recovery_event_id", name="pk_ops_case_recovery_events"
        ),
        sa.UniqueConstraint(
            "case_id",
            "event_id",
            "recovery_count",
            name="uq_ops_case_recovery_work_generation",
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["public.ops_cases.case_id"],
            name="fk_ops_case_recovery_case",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "recovery_count >= 1 AND outbox_row_version >= 1 "
            "AND case_revision_after >= 1",
            name="ck_ops_case_recovery_versions",
        ),
        sa.CheckConstraint(
            "work_status='PUBLISHED'",
            name="ck_ops_case_recovery_work_status",
        ),
        sa.CheckConstraint(
            "outbox_payload_sha256 ~ '^[0-9a-f]{64}$' "
            "AND evidence_sha256 ~ '^[0-9a-f]{64}$' "
            "AND evidence_sha256="
            "public.dts_canonical_json_sha256_v1(evidence)",
            name="ck_ops_case_recovery_hashes",
        ),
        schema="public",
        comment=(
            "Append-only evidence that the original DTS v2 work reached "
            "PUBLISHED; DEAD to PENDING alone never creates this row."
        ),
    )

    op.execute(
        rf"""
        CREATE UNIQUE INDEX uq_ops_cases_source_ref_v2
        ON public.ops_cases(source_ref)
        WHERE source_ref IS NOT NULL;
        CREATE UNIQUE INDEX uq_ops_cases_completion_course_v2
        ON public.ops_cases(case_type,source_region,source_appoint_id)
        WHERE case_type='COURSE_COMPLETION_CORRECTION';
        CREATE INDEX ix_ops_cases_source_course_v2
        ON public.ops_cases(source_region,source_appoint_id,case_type,status)
        WHERE source_region IS NOT NULL;
        CREATE INDEX ix_ops_case_recovery_case_recorded_v2
        ON public.ops_case_recovery_events(case_id,recorded_at,recovery_event_id);

        ALTER TABLE public.ops_cases
          ADD CONSTRAINT ck_ops_case_v2_versions CHECK (
            case_revision>=1 AND row_version>=1
            AND recovery_evidence_count>=0
            AND ((recovery_evidence_count=0
                  AND last_recovery_event_id IS NULL
                  AND last_recovery_count IS NULL
                  AND last_recovered_at IS NULL)
                 OR (recovery_evidence_count>0
                     AND last_recovery_event_id IS NOT NULL
                     AND last_recovery_count>=1
                     AND last_recovered_at IS NOT NULL))
          ),
          ADD CONSTRAINT ck_ops_case_v2_region CHECK (
            (source_region IS NULL OR source_region IN ('dom','ovs'))
            AND (source_appoint_id IS NULL OR source_region IS NOT NULL)
          ),
          ADD CONSTRAINT ck_ops_case_v2_subject CHECK (
            teacher_id IS NOT NULL OR case_type IN ({protected})
          ),
          ADD CONSTRAINT ck_ops_case_v2_protected_source_ref CHECK (
            case_type NOT IN ({protected}) OR source_ref IS NOT NULL
          ),
          ADD CONSTRAINT ck_ops_case_v2_completion_identity CHECK (
            case_type<>'COURSE_COMPLETION_CORRECTION' OR (
              source_region IN ('dom','ovs')
              AND source_appoint_id IS NOT NULL
              AND btrim(source_appoint_id)<>''
              AND source_ref='course-completion-correction:' ||
                  source_region || ':' || source_appoint_id
              AND case_id=source_ref
              AND evidence_fingerprint ~ '^[0-9a-f]{{64}}$'
            )
          ),
          ADD CONSTRAINT ck_ops_case_v2_technical_shape CHECK (
            case_type NOT IN ({technical}) OR (
              case_id='v2case:' || encode(
                sha256(convert_to(source_ref,'UTF8')),'hex'
              )
              AND priority='P1'
              AND status IN ('OPEN','IN_REVIEW','RESOLVED')
              AND external_action_status='NOT_REQUESTED'
              AND evidence_fingerprint ~ '^[0-9a-f]{{64}}$'
              AND jsonb_typeof(payload)='object'
            )
          );

        ALTER TABLE public.ops_decisions
          ADD CONSTRAINT ck_ops_decision_v2_row_version CHECK (
            row_version>=1
          ),
          ADD CONSTRAINT ck_ops_decision_projection_status_v2 CHECK (
            downstream_projection_status IS NULL OR
            downstream_projection_status IN (
              'PENDING','PUBLISHED','DEAD_LETTER'
            )
          );
        """
    )


def _install_validation_helpers() -> None:
    technical = _quoted(TECHNICAL_CASE_TYPES)
    op.execute(
        rf"""
        CREATE FUNCTION public.dts_ops_case_is_technical_v2(p_case_type text)
        RETURNS boolean
        LANGUAGE sql
        IMMUTABLE
        PARALLEL SAFE
        SET search_path=pg_catalog,public
        AS $function$
          SELECT p_case_type IN ({technical})
        $function$;

        CREATE FUNCTION public.dts_projection_event_ids_valid_v2(p_value jsonb)
        RETURNS boolean
        LANGUAGE sql
        IMMUTABLE
        STRICT
        PARALLEL SAFE
        SET search_path=pg_catalog,public
        AS $function$
          SELECT jsonb_typeof(p_value)='array'
             AND jsonb_array_length(p_value)>0
             AND NOT EXISTS (
               SELECT 1 FROM jsonb_array_elements(p_value) item(value)
               WHERE jsonb_typeof(item.value)<>'string'
                  OR btrim(item.value #>> '{{}}')=''
                  OR length(item.value #>> '{{}}')>512
             )
             AND (
               SELECT count(*)=count(DISTINCT item.value #>> '{{}}')
               FROM jsonb_array_elements(p_value) item(value)
             )
             AND p_value=(
               SELECT jsonb_agg(item.value ORDER BY
                        convert_to(item.value #>> '{{}}','UTF8'))
               FROM jsonb_array_elements(p_value) item(value)
             )
        $function$;

        CREATE FUNCTION public.dts_technical_source_ref_shape_valid_v2(
          p_case_type text,p_source_ref text
        )
        RETURNS boolean
        LANGUAGE plpgsql
        IMMUTABLE
        STRICT
        PARALLEL SAFE
        SET search_path=pg_catalog,public
        AS $function$
        BEGIN
          IF length(p_source_ref)>768 OR btrim(p_source_ref)<>p_source_ref
             OR p_source_ref ~ '[[:space:][:cntrl:]]' THEN
            RETURN false;
          END IF;
          RETURN CASE p_case_type
            WHEN 'DTS_DIRTY_KEY_DEAD' THEN
              p_source_ref ~
                '^tech-case:dts-dirty:[0-9a-f]{{64}}[:]g(0|[1-9][0-9]*)$'
            WHEN 'DTS_SOURCE_CONFLICT' THEN
              p_source_ref LIKE 'tech-case:dts-source-conflict:%'
              AND length(p_source_ref)>
                  length('tech-case:dts-source-conflict:')
            WHEN 'FAVORITE_OBSERVATION_DEAD' THEN
              p_source_ref LIKE 'tech-case:favorite-observation:%'
              AND length(p_source_ref)>
                  length('tech-case:favorite-observation:')
            WHEN 'TASK_MATERIALIZATION_DEAD' THEN
              p_source_ref LIKE 'tech-case:task-plan:%'
              AND length(p_source_ref)>length('tech-case:task-plan:')
            WHEN 'DOWNSTREAM_PROJECTION_DEAD' THEN
              p_source_ref LIKE 'tech-case:projection:%'
              AND length(p_source_ref)>length('tech-case:projection:')
            ELSE false
          END;
        END
        $function$;

        ALTER TABLE public.ops_cases
          ADD CONSTRAINT ck_ops_case_v2_technical_source_ref CHECK (
            NOT public.dts_ops_case_is_technical_v2(case_type)
            OR public.dts_technical_source_ref_shape_valid_v2(
                 case_type,source_ref
               )
          );
        ALTER TABLE public.ops_decisions
          ADD CONSTRAINT ck_ops_decision_projection_events_v2 CHECK (
            projection_event_ids IS NULL OR
            public.dts_projection_event_ids_valid_v2(projection_event_ids)
          );

        REVOKE ALL ON FUNCTION
          public.dts_ops_case_is_technical_v2(text),
          public.dts_projection_event_ids_valid_v2(jsonb),
          public.dts_technical_source_ref_shape_valid_v2(text,text)
        FROM PUBLIC;
        """
    )


def _install_guards() -> None:
    protected = _quoted(PROTECTED_CASE_TYPES)
    op.execute(
        rf"""
        -- rev59 intentionally blocks direct runtime mutation of append-only
        -- facts.  V2 protected commands are SECURITY DEFINER, so current_user
        -- distinguishes command-owned writes from direct tit_growth_app DML
        -- even though both runtimes now share the same login role.
        CREATE OR REPLACE FUNCTION public.guard_runtime_append_only_fact()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE actor_name text:=current_user;
        BEGIN
          IF actor_name IN (
            'tit_growth_app','tit_teacher_crud','tit_dts_ingest_runtime'
          ) THEN
            RAISE EXCEPTION '% is append-only for runtime roles',
              TG_TABLE_SCHEMA || '.' || TG_TABLE_NAME
              USING ERRCODE='42501';
          END IF;
          IF TG_OP='DELETE' THEN RETURN OLD; END IF;
          RETURN NEW;
        END
        $function$;

        CREATE FUNCTION public.guard_ops_cases_v2()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE actor_name text:=current_user;
        DECLARE table_owner text;
        DECLARE has_completed_recovery boolean;
        BEGIN
          SELECT pg_get_userbyid(relowner) INTO table_owner
          FROM pg_class WHERE oid='public.ops_cases'::regclass;
          IF TG_OP='DELETE' THEN
            RAISE EXCEPTION 'OPS_CASE_APPEND_ONLY'
              USING ERRCODE='42501';
          END IF;
          IF NEW.case_type NOT IN ({protected}) THEN
            RETURN NEW;
          END IF;

          IF TG_OP='INSERT' THEN
            IF actor_name<>table_owner
               OR NEW.case_revision<>1 OR NEW.row_version<>1
               OR NEW.recovery_evidence_count<>0
               OR NEW.last_recovery_event_id IS NOT NULL
               OR NEW.last_recovery_count IS NOT NULL
               OR NEW.last_recovered_at IS NOT NULL THEN
              RAISE EXCEPTION 'DTS_V2_PROTECTED_CASE_DIRECT_WRITE_DENIED'
                USING ERRCODE='42501';
            END IF;
            RETURN NEW;
          END IF;

          IF NEW.case_id IS DISTINCT FROM OLD.case_id
             OR NEW.case_type IS DISTINCT FROM OLD.case_type
             OR NEW.source_ref IS DISTINCT FROM OLD.source_ref
             OR NEW.source_region IS DISTINCT FROM OLD.source_region
             OR NEW.source_appoint_id IS DISTINCT FROM OLD.source_appoint_id
             OR NEW.task_id IS DISTINCT FROM OLD.task_id
             OR NEW.priority IS DISTINCT FROM OLD.priority
             OR NEW.external_action_status IS DISTINCT FROM
                  OLD.external_action_status
             OR NEW.created_at IS DISTINCT FROM OLD.created_at
             OR (NEW.teacher_id IS DISTINCT FROM OLD.teacher_id
                 AND NOT (OLD.teacher_id IS NULL
                          AND NEW.teacher_id IS NOT NULL)) THEN
            RAISE EXCEPTION 'DTS_V2_PROTECTED_CASE_IMMUTABLE'
              USING ERRCODE='23514';
          END IF;
          IF NEW.row_version IS DISTINCT FROM OLD.row_version+1
             OR NEW.case_revision NOT IN (
                  OLD.case_revision,OLD.case_revision+1
                )
             OR NEW.recovery_evidence_count NOT IN (
                  OLD.recovery_evidence_count,
                  OLD.recovery_evidence_count+1
                ) THEN
            RAISE EXCEPTION 'DTS_V2_PROTECTED_CASE_VERSION_INVALID'
              USING ERRCODE='23514';
          END IF;

          IF actor_name=table_owner THEN
            RETURN NEW;
          END IF;
          IF actor_name='tit_growth_app' THEN
            IF NEW.case_revision IS DISTINCT FROM OLD.case_revision
               OR NEW.teacher_id IS DISTINCT FROM OLD.teacher_id
               OR NEW.source_reason IS DISTINCT FROM OLD.source_reason
               OR NEW.payload IS DISTINCT FROM OLD.payload
               OR NEW.evidence_fingerprint IS DISTINCT FROM
                    OLD.evidence_fingerprint
               OR NEW.recovery_evidence_count IS DISTINCT FROM
                    OLD.recovery_evidence_count
               OR NEW.last_recovery_event_id IS DISTINCT FROM
                    OLD.last_recovery_event_id
               OR NEW.last_recovery_count IS DISTINCT FROM
                    OLD.last_recovery_count
               OR NEW.last_recovered_at IS DISTINCT FROM
                    OLD.last_recovered_at
               OR NEW.updated_at<=OLD.updated_at THEN
              RAISE EXCEPTION 'DTS_V2_PROTECTED_CASE_DIRECT_WRITE_DENIED'
                USING ERRCODE='42501';
            END IF;
            IF OLD.status='OPEN' AND NEW.status='IN_REVIEW' THEN
              RETURN NEW;
            END IF;
            IF OLD.status='IN_REVIEW' AND NEW.status='RESOLVED' THEN
              SELECT EXISTS (
                SELECT 1 FROM public.ops_case_recovery_events
                WHERE case_id=OLD.case_id AND work_status='PUBLISHED'
              ) INTO has_completed_recovery;
              IF has_completed_recovery THEN
                RETURN NEW;
              END IF;
            END IF;
          END IF;
          RAISE EXCEPTION 'DTS_V2_PROTECTED_CASE_DIRECT_WRITE_DENIED'
            USING ERRCODE='42501';
        END
        $function$;

        CREATE TRIGGER guard_ops_cases_v2
        BEFORE INSERT OR UPDATE OR DELETE ON public.ops_cases
        FOR EACH ROW EXECUTE FUNCTION public.guard_ops_cases_v2();

        CREATE FUNCTION public.guard_ops_case_recovery_append_only_v2()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path=pg_catalog,public
        AS $function$
        BEGIN
          RAISE EXCEPTION 'OPS_CASE_RECOVERY_APPEND_ONLY'
            USING ERRCODE='42501';
        END
        $function$;
        CREATE TRIGGER guard_ops_case_recovery_append_only_v2
        BEFORE UPDATE OR DELETE ON public.ops_case_recovery_events
        FOR EACH ROW EXECUTE FUNCTION
          public.guard_ops_case_recovery_append_only_v2();

        CREATE FUNCTION public.guard_ops_decisions_v2()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path=pg_catalog,public
        AS $function$
        BEGIN
          IF TG_OP='DELETE' THEN
            RAISE EXCEPTION 'OPS_DECISION_APPEND_ONLY'
              USING ERRCODE='42501';
          END IF;
          IF TG_OP='INSERT' THEN
            RETURN NEW;
          END IF;
          IF NEW.decision_id IS DISTINCT FROM OLD.decision_id
             OR NEW.case_id IS DISTINCT FROM OLD.case_id
             OR NEW.decision IS DISTINCT FROM OLD.decision
             OR NEW.expected_case_revision IS DISTINCT FROM
                  OLD.expected_case_revision
             OR NEW.expected_conflict_fingerprint IS DISTINCT FROM
                  OLD.expected_conflict_fingerprint
             OR NEW.expected_source_revision IS DISTINCT FROM
                  OLD.expected_source_revision
             OR NEW.expected_source_position IS DISTINCT FROM
                  OLD.expected_source_position
             OR NEW.projection_event_ids IS DISTINCT FROM
                  OLD.projection_event_ids
             OR NEW.note IS DISTINCT FROM OLD.note
             OR NEW.decided_at IS DISTINCT FROM OLD.decided_at
             OR NEW.actor_type IS DISTINCT FROM OLD.actor_type
             OR NEW.payload IS DISTINCT FROM OLD.payload
             OR NEW.row_version IS DISTINCT FROM OLD.row_version+1 THEN
            RAISE EXCEPTION 'OPS_DECISION_FACT_IMMUTABLE'
              USING ERRCODE='23514';
          END IF;
          IF OLD.downstream_projection_status='PUBLISHED'
             OR NEW.downstream_projection_status IS NULL
             OR NEW.downstream_projection_status IS NOT DISTINCT FROM
                  OLD.downstream_projection_status THEN
            RAISE EXCEPTION 'OPS_DECISION_PROJECTION_TRANSITION_INVALID'
              USING ERRCODE='23514';
          END IF;
          RETURN NEW;
        END
        $function$;
        CREATE TRIGGER guard_ops_decisions_v2
        BEFORE UPDATE OR DELETE ON public.ops_decisions
        FOR EACH ROW EXECUTE FUNCTION public.guard_ops_decisions_v2();

        CREATE FUNCTION public.check_completion_decision_shape_v2()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE parent_case_type text;
        BEGIN
          SELECT case_type INTO parent_case_type
          FROM public.ops_cases WHERE case_id=NEW.case_id;
          IF NOT FOUND THEN
            RETURN NULL;
          END IF;
          IF parent_case_type='COURSE_COMPLETION_CORRECTION' THEN
            IF NEW.expected_case_revision IS NULL
               OR NEW.expected_case_revision<1
               OR NEW.expected_conflict_fingerprint !~ '^[0-9a-f]{{64}}$'
               OR NEW.expected_source_revision IS NULL
               OR NEW.expected_source_revision<1
               OR NOT public.dts_v2_source_position_valid(
                    NEW.expected_source_position
                  )
               OR (TG_OP='INSERT' AND
                   NEW.downstream_projection_status IS DISTINCT FROM
                    'PENDING')
               OR NOT public.dts_projection_event_ids_valid_v2(
                    NEW.projection_event_ids
                  ) THEN
              RAISE EXCEPTION 'COMPLETION_DECISION_PROJECTION_SHAPE_INVALID'
                USING ERRCODE='23514';
            END IF;
          ELSIF NEW.expected_case_revision IS NOT NULL
             OR NEW.expected_conflict_fingerprint IS NOT NULL
             OR NEW.expected_source_revision IS NOT NULL
             OR NEW.expected_source_position IS NOT NULL
             OR NEW.downstream_projection_status IS NOT NULL
             OR NEW.projection_event_ids IS NOT NULL THEN
            RAISE EXCEPTION 'NON_COMPLETION_DECISION_PROJECTION_FIELDS_DENIED'
              USING ERRCODE='23514';
          END IF;
          RETURN NULL;
        END
        $function$;
        CREATE CONSTRAINT TRIGGER check_completion_decision_shape_v2
        AFTER INSERT OR UPDATE ON public.ops_decisions
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION
          public.check_completion_decision_shape_v2();

        CREATE FUNCTION public.audit_manual_ops_case_status_v2()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE actor_name text := coalesce(
          nullif(current_setting('role',true),'none'),session_user
        );
        DECLARE audit_id text;
        DECLARE audit_payload jsonb;
        BEGIN
          IF actor_name<>'tit_growth_app'
             OR NEW.case_type NOT IN ({protected})
             OR NEW.status IS NOT DISTINCT FROM OLD.status THEN
            RETURN NULL;
          END IF;
          audit_id := 'audit:ops-case-status:v2:' ||
            public.dts_canonical_json_sha256_v1(jsonb_build_object(
              'case_id',NEW.case_id,'row_version',NEW.row_version,
              'status',NEW.status
            ));
          audit_payload := jsonb_build_object(
            'protocol_version','ops-case-status-v2',
            'case_id',NEW.case_id,'source_ref',NEW.source_ref,
            'previous_status',OLD.status,'status',NEW.status,
            'row_version',NEW.row_version
          );
          INSERT INTO public.audit_events(
            event_id,event_type,teacher_id,task_id,case_id,occurred_at,
            actor_type,payload_hash,payload
          ) VALUES (
            audit_id,'OPS_CASE_MANUAL_STATUS_CHANGED_V2',NEW.teacher_id,
            NEW.task_id,NEW.case_id,transaction_timestamp(),'OPS_USER',
            public.dts_canonical_json_sha256_v1(audit_payload),audit_payload
          );
          RETURN NULL;
        END
        $function$;
        CREATE TRIGGER audit_manual_ops_case_status_v2
        AFTER UPDATE ON public.ops_cases
        FOR EACH ROW EXECUTE FUNCTION public.audit_manual_ops_case_status_v2();

        REVOKE ALL ON FUNCTION
          public.guard_ops_cases_v2(),
          public.guard_ops_case_recovery_append_only_v2(),
          public.guard_ops_decisions_v2(),
          public.check_completion_decision_shape_v2(),
          public.audit_manual_ops_case_status_v2()
        FROM PUBLIC;
        """
    )


def _install_record_command() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.record_dts_v2_technical_case(
          p_case_id text,p_case_type text,p_source_ref text,
          p_teacher_id text,p_source_region text,p_source_appoint_id text,
          p_error_code text,p_aggregate_type text,p_aggregate_id text,
          p_aggregate_revision bigint,p_event_id text,p_attempt_count integer,
          p_recovery_count bigint
        )
        RETURNS text
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE event_row public.outbox_events%ROWTYPE;
        DECLARE case_row public.ops_cases%ROWTYPE;
        DECLARE aggregate_key jsonb;
        DECLARE event_revision bigint;
        DECLARE event_region text;
        DECLARE event_appoint_id text;
        DECLARE event_teacher_id text;
        DECLARE stored_teacher_id text;
        DECLARE expected_case_type text;
        DECLARE expected_source_ref text;
        DECLARE expected_case_id text;
        DECLARE aggregate_key_hash text;
        DECLARE evidence_document jsonb;
        DECLARE new_evidence_fingerprint text;
        DECLARE failure_timestamp text;
        DECLARE first_failure_timestamp text;
        DECLARE next_case_revision integer;
        DECLARE result_status text;
        DECLARE audit_id text;
        DECLARE audit_payload jsonb;
        BEGIN
          IF p_case_id IS NULL OR length(p_case_id)>768
             OR p_case_type NOT IN (
               'TASK_MATERIALIZATION_DEAD','DOWNSTREAM_PROJECTION_DEAD'
             )
             OR NOT public.dts_technical_source_ref_shape_valid_v2(
                  p_case_type,p_source_ref
                )
             OR p_error_code !~ '^[A-Z][A-Z0-9_]*$'
             OR length(p_error_code)>128
             OR p_aggregate_type IS NULL OR btrim(p_aggregate_type)=''
             OR length(p_aggregate_type)>48
             OR p_aggregate_id IS NULL OR btrim(p_aggregate_id)=''
             OR length(p_aggregate_id)>160
             OR p_aggregate_revision IS NULL OR p_aggregate_revision<1
             OR p_event_id IS NULL OR btrim(p_event_id)=''
             OR length(p_event_id)>512
             OR p_attempt_count IS DISTINCT FROM 8
             OR p_recovery_count IS NULL OR p_recovery_count<0
             OR (p_source_region IS NOT NULL
                 AND p_source_region NOT IN ('dom','ovs'))
             OR (p_source_appoint_id IS NOT NULL
                 AND (btrim(p_source_appoint_id)=''
                      OR btrim(p_source_appoint_id)<>p_source_appoint_id
                      OR length(p_source_appoint_id)>512))
             OR (p_teacher_id IS NOT NULL
                 AND (btrim(p_teacher_id)=''
                      OR btrim(p_teacher_id)<>p_teacher_id
                      OR length(p_teacher_id)>64)) THEN
            RAISE EXCEPTION 'DTS_V2_TECHNICAL_CASE_REQUEST_INVALID'
              USING ERRCODE='22023';
          END IF;
          PERFORM pg_advisory_xact_lock(hashtextextended(
            'dts-v2-tech-case:' || p_source_ref,0
          ));
          SELECT * INTO event_row FROM public.outbox_events
          WHERE event_id=p_event_id FOR UPDATE;
          IF NOT FOUND
             OR event_row.status IS DISTINCT FROM 'DEAD_LETTER'
             OR event_row.attempt_count IS DISTINCT FROM 8
             OR event_row.recovery_count IS DISTINCT FROM p_recovery_count
             OR event_row.aggregate_type IS DISTINCT FROM p_aggregate_type
             OR event_row.aggregate_id IS DISTINCT FROM p_aggregate_id
             OR jsonb_typeof(event_row.payload)<>'object'
             OR jsonb_typeof(event_row.payload->'aggregate_revision')<>
                  'number'
             OR length(event_row.payload->>'aggregate_revision')>18
             OR event_row.payload->>'aggregate_revision' !~
                  '^[1-9][0-9]*$' THEN
            RAISE EXCEPTION 'DTS_V2_TECHNICAL_CASE_WORK_NOT_DEAD'
              USING ERRCODE='23514';
          END IF;
          event_revision :=
            (event_row.payload->>'aggregate_revision')::bigint;
          IF event_revision IS DISTINCT FROM p_aggregate_revision THEN
            RAISE EXCEPTION 'DTS_V2_TECHNICAL_CASE_EVENT_MISMATCH'
              USING ERRCODE='23514';
          END IF;
          IF event_row.aggregate_type='TASK_PLAN' THEN
            expected_case_type := 'TASK_MATERIALIZATION_DEAD';
            IF event_row.event_type IS DISTINCT FROM
                'task.materialization.requested.v2' THEN
              RAISE EXCEPTION 'DTS_V2_TECHNICAL_CASE_EVENT_MISMATCH'
                USING ERRCODE='23514';
            END IF;
            expected_source_ref := 'tech-case:task-plan:' ||
              event_row.aggregate_id || ':' || 'r' || event_revision::text;
          ELSE
            expected_case_type := 'DOWNSTREAM_PROJECTION_DEAD';
            IF event_row.event_type IS DISTINCT FROM
                'source_wide.changed.v2' THEN
              RAISE EXCEPTION 'DTS_V2_TECHNICAL_CASE_EVENT_MISMATCH'
                USING ERRCODE='23514';
            END IF;
            expected_source_ref := 'tech-case:projection:' ||
              event_row.event_id;
          END IF;
          expected_case_id := 'v2case:' || encode(
            sha256(convert_to(expected_source_ref,'UTF8')),'hex'
          );
          IF p_case_type IS DISTINCT FROM expected_case_type
             OR p_source_ref IS DISTINCT FROM expected_source_ref
             OR p_case_id IS DISTINCT FROM expected_case_id THEN
            RAISE EXCEPTION 'DTS_V2_TECHNICAL_CASE_IDENTITY_MISMATCH'
              USING ERRCODE='23514';
          END IF;

          aggregate_key := event_row.payload->'aggregate_key';
          IF jsonb_typeof(aggregate_key)<>'object' THEN
            RAISE EXCEPTION 'DTS_V2_TECHNICAL_CASE_SUBJECT_INVALID'
              USING ERRCODE='23514';
          END IF;
          IF aggregate_key ? 'source_region' THEN
            IF jsonb_typeof(aggregate_key->'source_region')<>'string'
               OR aggregate_key->>'source_region' NOT IN ('dom','ovs') THEN
              RAISE EXCEPTION 'DTS_V2_TECHNICAL_CASE_SUBJECT_INVALID'
                USING ERRCODE='23514';
            END IF;
            event_region := aggregate_key->>'source_region';
          END IF;
          IF aggregate_key ? 'source_appoint_id' THEN
            IF jsonb_typeof(aggregate_key->'source_appoint_id')<>'string'
               OR btrim(aggregate_key->>'source_appoint_id')=''
               OR btrim(aggregate_key->>'source_appoint_id')<>
                    aggregate_key->>'source_appoint_id'
               OR length(aggregate_key->>'source_appoint_id')>512 THEN
              RAISE EXCEPTION 'DTS_V2_TECHNICAL_CASE_SUBJECT_INVALID'
                USING ERRCODE='23514';
            END IF;
            event_appoint_id := aggregate_key->>'source_appoint_id';
          END IF;
          IF aggregate_key ? 'teacher_id' THEN
            IF jsonb_typeof(aggregate_key->'teacher_id')<>'string'
               OR btrim(aggregate_key->>'teacher_id')=''
               OR btrim(aggregate_key->>'teacher_id')<>
                    aggregate_key->>'teacher_id'
               OR length(aggregate_key->>'teacher_id')>64 THEN
              RAISE EXCEPTION 'DTS_V2_TECHNICAL_CASE_SUBJECT_INVALID'
                USING ERRCODE='23514';
            END IF;
            event_teacher_id := aggregate_key->>'teacher_id';
          END IF;
          IF p_source_region IS DISTINCT FROM event_region
             OR p_source_appoint_id IS DISTINCT FROM event_appoint_id
             OR p_teacher_id IS DISTINCT FROM event_teacher_id THEN
            RAISE EXCEPTION 'DTS_V2_TECHNICAL_CASE_SUBJECT_MISMATCH'
              USING ERRCODE='23514';
          END IF;
          stored_teacher_id := CASE WHEN EXISTS (
            SELECT 1 FROM public.teachers WHERE teacher_id=p_teacher_id
          ) THEN p_teacher_id ELSE NULL END;
          aggregate_key_hash := public.dts_canonical_json_sha256_v1(
            aggregate_key
          );
          evidence_document := jsonb_build_object(
            'protocol_version','dts-v2-technical-case-v1',
            'case_type',p_case_type,'source_ref',p_source_ref,
            'error_code',p_error_code,
            'aggregate_type',p_aggregate_type,
            'aggregate_id',p_aggregate_id,
            'aggregate_revision',p_aggregate_revision,
            'aggregate_key_hash',aggregate_key_hash,
            'event_id',p_event_id,'attempt_count',p_attempt_count,
            'recovery_count',p_recovery_count
          );
          new_evidence_fingerprint :=
            public.dts_canonical_json_sha256_v1(evidence_document);
          failure_timestamp := public.outbox_legacy_timestamp_v1(
            transaction_timestamp()
          ) #>> '{}';

          SELECT * INTO case_row FROM public.ops_cases
          WHERE source_ref=p_source_ref FOR UPDATE;
          IF FOUND THEN
            IF case_row.case_id IS DISTINCT FROM p_case_id
               OR case_row.case_type IS DISTINCT FROM p_case_type THEN
              RAISE EXCEPTION 'DTS_V2_TECHNICAL_CASE_CONFLICT'
                USING ERRCODE='23505';
            END IF;
            IF case_row.evidence_fingerprint IS NOT DISTINCT FROM
                new_evidence_fingerprint THEN
              result_status := 'UNCHANGED';
            ELSE
              next_case_revision := case_row.case_revision+1;
              first_failure_timestamp := coalesce(
                case_row.payload->>'first_failed_at',failure_timestamp
              );
              UPDATE public.ops_cases SET
                teacher_id=coalesce(case_row.teacher_id,stored_teacher_id),
                status=CASE WHEN case_row.status='RESOLVED'
                            THEN 'OPEN' ELSE case_row.status END,
                case_revision=next_case_revision,
                row_version=case_row.row_version+1,
                source_reason=p_error_code,
                evidence_fingerprint=new_evidence_fingerprint,
                payload=evidence_document || jsonb_build_object(
                  'case_revision',next_case_revision,
                  'first_failed_at',first_failure_timestamp,
                  'last_failed_at',failure_timestamp
                ),
                updated_at=transaction_timestamp()
              WHERE case_id=case_row.case_id;
              result_status := 'UPDATED';
            END IF;
          ELSE
            next_case_revision := 1;
            INSERT INTO public.ops_cases(
              case_id,case_type,source_ref,teacher_id,source_region,
              source_appoint_id,task_id,priority,status,case_revision,
              row_version,evidence_fingerprint,recovery_evidence_count,
              source_reason,external_action_status,created_at,payload,
              updated_at
            ) VALUES (
              p_case_id,p_case_type,p_source_ref,stored_teacher_id,
              p_source_region,p_source_appoint_id,NULL,'P1','OPEN',1,1,
              new_evidence_fingerprint,0,p_error_code,'NOT_REQUESTED',
              transaction_timestamp(),
              evidence_document || jsonb_build_object(
                'case_revision',1,'first_failed_at',failure_timestamp,
                'last_failed_at',failure_timestamp
              ),transaction_timestamp()
            );
            result_status := 'CREATED';
          END IF;

          IF p_case_type='DOWNSTREAM_PROJECTION_DEAD' THEN
            UPDATE public.ops_decisions SET
              downstream_projection_status='DEAD_LETTER',
              row_version=row_version+1
            WHERE projection_event_ids ? p_event_id
              AND downstream_projection_status<>'DEAD_LETTER';
          END IF;

          IF result_status<>'UNCHANGED' THEN
            SELECT case_revision INTO next_case_revision
            FROM public.ops_cases WHERE case_id=p_case_id;
            audit_id := 'audit:tech-case:v2:' ||
              public.dts_canonical_json_sha256_v1(jsonb_build_object(
                'case_id',p_case_id,'case_revision',next_case_revision
              ));
            audit_payload := jsonb_build_object(
              'protocol_version','dts-v2-technical-case-v1',
              'case_id',p_case_id,'case_type',p_case_type,
              'source_ref',p_source_ref,
              'case_revision',next_case_revision,
              'evidence_fingerprint',new_evidence_fingerprint,
              'result',result_status
            );
            INSERT INTO public.audit_events(
              event_id,event_type,teacher_id,task_id,case_id,occurred_at,
              actor_type,payload_hash,payload
            ) VALUES (
              audit_id,'DTS_V2_TECHNICAL_CASE_RECORDED',stored_teacher_id,
              NULL,p_case_id,transaction_timestamp(),'DTS_V2_OUTBOX_WORKER',
              public.dts_canonical_json_sha256_v1(audit_payload),
              audit_payload
            );
          END IF;
          RETURN result_status;
        END
        $function$;

        REVOKE ALL ON FUNCTION public.record_dts_v2_technical_case(
          text,text,text,text,text,text,text,text,text,bigint,text,
          integer,bigint
        ) FROM PUBLIC;
        """
    )


def _install_recovery_command() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.record_dts_v2_technical_case_recovery(
          p_case_id text,p_source_ref text,p_event_id text,
          p_recovery_count bigint
        )
        RETURNS text
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE case_row public.ops_cases%ROWTYPE;
        DECLARE event_row public.outbox_events%ROWTYPE;
        DECLARE existing_recovery public.ops_case_recovery_events%ROWTYPE;
        DECLARE expected_source_ref text;
        DECLARE event_revision bigint;
        DECLARE generated_recovery_event_id text;
        DECLARE status_after text;
        DECLARE next_case_revision integer;
        DECLARE evidence_document jsonb;
        DECLARE evidence_sha256 text;
        DECLARE audit_id text;
        DECLARE audit_payload jsonb;
        BEGIN
          IF p_case_id IS NULL OR length(p_case_id)>768
             OR p_source_ref IS NULL OR length(p_source_ref)>768
             OR p_event_id IS NULL OR btrim(p_event_id)=''
             OR length(p_event_id)>512
             OR p_recovery_count IS NULL OR p_recovery_count<1 THEN
            RAISE EXCEPTION 'DTS_V2_TECHNICAL_RECOVERY_REQUEST_INVALID'
              USING ERRCODE='22023';
          END IF;
          PERFORM pg_advisory_xact_lock(hashtextextended(
            'dts-v2-tech-case:' || p_source_ref,0
          ));
          SELECT * INTO case_row FROM public.ops_cases
          WHERE source_ref=p_source_ref FOR UPDATE;
          IF NOT FOUND THEN
            RETURN 'NOT_FOUND';
          END IF;
          IF case_row.case_id IS DISTINCT FROM p_case_id
             OR case_row.case_type NOT IN (
               'TASK_MATERIALIZATION_DEAD','DOWNSTREAM_PROJECTION_DEAD'
             ) THEN
            RAISE EXCEPTION 'DTS_V2_TECHNICAL_RECOVERY_IDENTITY_MISMATCH'
              USING ERRCODE='23514';
          END IF;
          SELECT * INTO event_row FROM public.outbox_events
          WHERE event_id=p_event_id FOR UPDATE;
          IF NOT FOUND OR event_row.status IS DISTINCT FROM 'PUBLISHED'
             OR event_row.published_at IS NULL
             OR event_row.recovery_count IS DISTINCT FROM p_recovery_count
             OR jsonb_typeof(event_row.payload->'aggregate_revision')<>
                  'number'
             OR length(event_row.payload->>'aggregate_revision')>18
             OR event_row.payload->>'aggregate_revision' !~
                  '^[1-9][0-9]*$' THEN
            RAISE EXCEPTION 'DTS_V2_TECHNICAL_RECOVERY_NOT_COMPLETE'
              USING ERRCODE='23514';
          END IF;
          event_revision :=
            (event_row.payload->>'aggregate_revision')::bigint;
          IF case_row.case_type='TASK_MATERIALIZATION_DEAD' THEN
            expected_source_ref := 'tech-case:task-plan:' ||
              event_row.aggregate_id || ':' || 'r' || event_revision::text;
            IF event_row.aggregate_type IS DISTINCT FROM 'TASK_PLAN'
               OR event_row.event_type IS DISTINCT FROM
                    'task.materialization.requested.v2' THEN
              RAISE EXCEPTION
                'DTS_V2_TECHNICAL_RECOVERY_IDENTITY_MISMATCH'
                USING ERRCODE='23514';
            END IF;
          ELSE
            expected_source_ref := 'tech-case:projection:' ||
              event_row.event_id;
            IF event_row.aggregate_type='TASK_PLAN'
               OR event_row.event_type IS DISTINCT FROM
                    'source_wide.changed.v2' THEN
              RAISE EXCEPTION
                'DTS_V2_TECHNICAL_RECOVERY_IDENTITY_MISMATCH'
                USING ERRCODE='23514';
            END IF;
          END IF;
          IF p_source_ref IS DISTINCT FROM expected_source_ref THEN
            RAISE EXCEPTION 'DTS_V2_TECHNICAL_RECOVERY_IDENTITY_MISMATCH'
              USING ERRCODE='23514';
          END IF;

          generated_recovery_event_id := 'case-recovery:v2:' ||
            public.dts_canonical_json_sha256_v1(jsonb_build_object(
              'case_id',p_case_id,'event_id',p_event_id,
              'recovery_count',p_recovery_count
            ));
          SELECT * INTO existing_recovery
          FROM public.ops_case_recovery_events AS recovery
          WHERE recovery.recovery_event_id=generated_recovery_event_id;
          IF FOUND THEN
            IF existing_recovery.case_id IS DISTINCT FROM p_case_id
               OR existing_recovery.source_ref IS DISTINCT FROM p_source_ref
               OR existing_recovery.event_id IS DISTINCT FROM p_event_id
               OR existing_recovery.recovery_count IS DISTINCT FROM
                    p_recovery_count
               OR existing_recovery.outbox_payload_sha256 IS DISTINCT FROM
                    event_row.payload_sha256 THEN
              RAISE EXCEPTION 'DTS_V2_TECHNICAL_RECOVERY_CONFLICT'
                USING ERRCODE='23505';
            END IF;
            RETURN 'UNCHANGED';
          END IF;

          status_after := CASE WHEN case_row.status='OPEN'
                               THEN 'RESOLVED' ELSE case_row.status END;
          next_case_revision := case_row.case_revision+1;
          evidence_document := jsonb_build_object(
            'protocol_version','dts-v2-technical-recovery-v1',
            'case_id',p_case_id,'source_ref',p_source_ref,
            'event_id',p_event_id,'recovery_count',p_recovery_count,
            'work_status','PUBLISHED',
            'outbox_payload_sha256',event_row.payload_sha256,
            'outbox_row_version',event_row.row_version,
            'published_at',public.outbox_legacy_timestamp_v1(
              event_row.published_at
            ),
            'case_status_before',case_row.status,
            'case_status_after',status_after,
            'case_revision_after',next_case_revision
          );
          evidence_sha256 :=
            public.dts_canonical_json_sha256_v1(evidence_document);
          INSERT INTO public.ops_case_recovery_events(
            recovery_event_id,case_id,source_ref,event_id,recovery_count,
            work_status,outbox_payload_sha256,outbox_row_version,
            published_at,case_status_before,case_status_after,
            case_revision_after,evidence,evidence_sha256,recorded_at
          ) VALUES (
            generated_recovery_event_id,p_case_id,p_source_ref,p_event_id,
            p_recovery_count,'PUBLISHED',event_row.payload_sha256,
            event_row.row_version,event_row.published_at,case_row.status,
            status_after,next_case_revision,evidence_document,
            evidence_sha256,transaction_timestamp()
          );
          UPDATE public.ops_cases SET
            status=status_after,case_revision=next_case_revision,
            row_version=row_version+1,
            recovery_evidence_count=recovery_evidence_count+1,
            last_recovery_event_id=p_event_id,
            last_recovery_count=p_recovery_count,
            last_recovered_at=transaction_timestamp(),
            payload=jsonb_set(
              payload,'{case_revision}',to_jsonb(next_case_revision),false
            ),
            updated_at=transaction_timestamp()
          WHERE case_id=p_case_id;

          WITH calculated AS (
            SELECT decision.decision_id,CASE
              WHEN EXISTS (
                SELECT 1
                FROM jsonb_array_elements_text(
                  decision.projection_event_ids
                ) AS projection(event_id)
                JOIN public.outbox_events AS linked
                  ON linked.event_id=projection.event_id
                WHERE linked.status='DEAD_LETTER'
              ) THEN 'DEAD_LETTER'
              WHEN NOT EXISTS (
                SELECT 1
                FROM jsonb_array_elements_text(
                  decision.projection_event_ids
                ) AS projection(event_id)
                LEFT JOIN public.outbox_events AS linked
                  ON linked.event_id=projection.event_id
                WHERE linked.event_id IS NULL
                   OR linked.status<>'PUBLISHED'
              ) THEN 'PUBLISHED'
              ELSE 'PENDING'
            END AS status
            FROM public.ops_decisions AS decision
            WHERE decision.projection_event_ids ? p_event_id
            FOR UPDATE
          )
          UPDATE public.ops_decisions AS decision SET
            downstream_projection_status=calculated.status,
            row_version=decision.row_version+1
          FROM calculated
          WHERE decision.decision_id=calculated.decision_id
            AND decision.downstream_projection_status IS DISTINCT FROM
                calculated.status;

          audit_id := 'audit:tech-recovery:v2:' ||
            public.dts_canonical_json_sha256_v1(jsonb_build_object(
              'recovery_event_id',generated_recovery_event_id
            ));
          audit_payload := jsonb_build_object(
            'protocol_version','dts-v2-technical-recovery-v1',
            'case_id',p_case_id,'source_ref',p_source_ref,
            'recovery_event_id',generated_recovery_event_id,
            'event_id',p_event_id,'recovery_count',p_recovery_count,
            'case_status_before',case_row.status,
            'case_status_after',status_after,
            'case_revision',next_case_revision,
            'evidence_sha256',evidence_sha256
          );
          INSERT INTO public.audit_events(
            event_id,event_type,teacher_id,task_id,case_id,occurred_at,
            actor_type,payload_hash,payload
          ) VALUES (
            audit_id,'DTS_V2_TECHNICAL_CASE_RECOVERED',case_row.teacher_id,
            case_row.task_id,p_case_id,transaction_timestamp(),
            'DTS_V2_OUTBOX_WORKER',
            public.dts_canonical_json_sha256_v1(audit_payload),audit_payload
          );
          RETURN CASE WHEN case_row.status='OPEN' THEN 'RESOLVED'
                      ELSE 'EVIDENCE_APPENDED' END;
        END
        $function$;

        REVOKE ALL ON FUNCTION
          public.record_dts_v2_technical_case_recovery(
            text,text,text,bigint
          ) FROM PUBLIC;
        """
    )


def _apply_acl_and_comments() -> None:
    op.execute(
        rf"""
        REVOKE CREATE ON SCHEMA public FROM {OUTBOX_RUNTIME_ROLE};
        GRANT USAGE ON SCHEMA public TO {OUTBOX_RUNTIME_ROLE};
        REVOKE ALL PRIVILEGES ON TABLE
          public.ops_case_recovery_events
        FROM {OUTBOX_RUNTIME_ROLE};
        GRANT SELECT ON TABLE
          public.outbox_events,public.domain_aggregate_revisions,
          public.dts_pipeline_control,public.ops_case_recovery_events
        TO {OUTBOX_RUNTIME_ROLE};
        GRANT UPDATE(
          status,attempt_count,last_error,available_at,published_at,row_version
        ) ON public.outbox_events TO {OUTBOX_RUNTIME_ROLE};
        GRANT EXECUTE ON FUNCTION
          public.dts_canonical_json_v1(jsonb),
          public.dts_canonical_json_sha256_v1(jsonb),
          public.record_dts_v2_technical_case(
            text,text,text,text,text,text,text,text,text,bigint,text,
            integer,bigint
          ),
          public.record_dts_v2_technical_case_recovery(
            text,text,text,bigint
          )
        TO {OUTBOX_RUNTIME_ROLE};

        REVOKE ALL ON FUNCTION
          public.record_dts_v2_technical_case(
            text,text,text,text,text,text,text,text,text,bigint,text,
            integer,bigint
          ),
          public.record_dts_v2_technical_case_recovery(
            text,text,text,bigint
          )
        FROM PUBLIC,tit_dts_ingest_runtime,
             tit_teacher_crud,tide_support_ticket_owner;
        GRANT EXECUTE ON FUNCTION
          public.dts_ops_case_is_technical_v2(text),
          public.dts_projection_event_ids_valid_v2(jsonb),
          public.dts_technical_source_ref_shape_valid_v2(text,text),
          public.dts_v2_source_position_valid(jsonb)
        TO tit_growth_app;

        DO $ops_case_v2_optional_acl$
        DECLARE role_name text;
        BEGIN
          FOREACH role_name IN ARRAY ARRAY[
            'tit_teacher_crud','tide_support_ticket_owner'
          ]::text[] LOOP
            IF to_regrole(role_name) IS NOT NULL THEN
              EXECUTE format(
                'REVOKE ALL PRIVILEGES ON TABLE '
                'public.ops_case_recovery_events FROM %I',role_name
              );
              EXECUTE format(
                'REVOKE ALL ON FUNCTION '
                'public.record_dts_v2_technical_case('
                'text,text,text,text,text,text,text,text,text,bigint,text,'
                'integer,bigint),'
                'public.record_dts_v2_technical_case_recovery('
                'text,text,text,bigint) FROM %I',role_name
              );
            END IF;
          END LOOP;
        END
        $ops_case_v2_optional_acl$;

        COMMENT ON TABLE public.ops_cases IS
          'Current operational Case state. DTS v2 protected Case identities and evidence are command-owned and delete-forbidden.';
        COMMENT ON COLUMN public.ops_cases.source_ref IS
          'Canonical unique business or technical source identity; mandatory for protected v2 Case types.';
        COMMENT ON COLUMN public.ops_cases.case_revision IS
          'Semantic evidence revision used for correction decisions and technical recovery concurrency.';
        COMMENT ON COLUMN public.ops_cases.row_version IS
          'Local optimistic-lock version; increments on every protected Case update.';
        COMMENT ON COLUMN public.ops_cases.recovery_evidence_count IS
          'Count of append-only proofs that the original work truly completed.';
        COMMENT ON TABLE public.ops_decisions IS
          'Immutable operator decision facts plus separately mutable downstream projection status.';
        COMMENT ON COLUMN public.ops_decisions.downstream_projection_status IS
          'PENDING, PUBLISHED, or DEAD_LETTER for the immutable projection_event_ids set; not the decision result.';
        COMMENT ON FUNCTION public.record_dts_v2_technical_case(
          text,text,text,text,text,text,text,text,text,bigint,text,
          integer,bigint
        ) IS
          'Record or revise one typed Outbox DEAD_LETTER Case after verifying the exact locked event.';
        COMMENT ON FUNCTION
          public.record_dts_v2_technical_case_recovery(
            text,text,text,bigint
          ) IS
          'Append recovery evidence only after the original Outbox event is truly PUBLISHED; IN_REVIEW remains open.';
        """
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("DTS v2 Ops Case contract requires PostgreSQL")
    _assert_preconditions_and_role()
    _expand_schema()
    _install_validation_helpers()
    _install_guards()
    _install_record_command()
    _install_recovery_command()
    _apply_acl_and_comments()


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("DTS v2 Ops Case contract requires PostgreSQL")
    protected = _quoted(PROTECTED_CASE_TYPES)
    op.execute(
        rf"""
        LOCK TABLE public.ops_cases,public.ops_decisions,
                   public.ops_case_recovery_events
        IN ACCESS EXCLUSIVE MODE;
        DO $ops_case_v2_downgrade_guard$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM public.ops_cases
            WHERE case_type IN ({protected}) OR source_ref IS NOT NULL
          ) OR EXISTS (
            SELECT 1 FROM public.ops_case_recovery_events
          ) OR EXISTS (
            SELECT 1 FROM public.ops_decisions
            WHERE expected_case_revision IS NOT NULL
               OR expected_conflict_fingerprint IS NOT NULL
               OR expected_source_revision IS NOT NULL
               OR expected_source_position IS NOT NULL
               OR downstream_projection_status IS NOT NULL
               OR projection_event_ids IS NOT NULL
          ) OR EXISTS (
            SELECT 1 FROM public.ops_cases
            WHERE length(case_id)>128
          ) OR EXISTS (
            SELECT 1 FROM public.ops_decisions
            WHERE length(case_id)>128
          ) OR EXISTS (
            SELECT 1 FROM public.audit_events
            WHERE length(case_id)>128
          ) OR EXISTS (
            SELECT 1 FROM public.ops_cases
            WHERE length(source_reason)>64
          ) THEN
            RAISE EXCEPTION
              'refusing DTS v2 Ops Case downgrade: protected history exists';
          END IF;
        END
        $ops_case_v2_downgrade_guard$;

        REVOKE ALL PRIVILEGES ON TABLE
          public.ops_case_recovery_events
        FROM {OUTBOX_RUNTIME_ROLE};
        REVOKE ALL ON FUNCTION
          public.record_dts_v2_technical_case(
            text,text,text,text,text,text,text,text,text,bigint,text,
            integer,bigint
          ),
          public.record_dts_v2_technical_case_recovery(
            text,text,text,bigint
          )
        FROM {OUTBOX_RUNTIME_ROLE};
        REVOKE EXECUTE ON FUNCTION
          public.dts_ops_case_is_technical_v2(text),
          public.dts_projection_event_ids_valid_v2(jsonb),
          public.dts_technical_source_ref_shape_valid_v2(text,text),
          public.dts_v2_source_position_valid(jsonb)
        FROM tit_growth_app;

        DROP TRIGGER IF EXISTS audit_manual_ops_case_status_v2
          ON public.ops_cases;
        DROP TRIGGER IF EXISTS check_completion_decision_shape_v2
          ON public.ops_decisions;
        DROP TRIGGER IF EXISTS guard_ops_decisions_v2
          ON public.ops_decisions;
        DROP TRIGGER IF EXISTS guard_ops_case_recovery_append_only_v2
          ON public.ops_case_recovery_events;
        DROP TRIGGER IF EXISTS guard_ops_cases_v2 ON public.ops_cases;

        DROP FUNCTION IF EXISTS
          public.record_dts_v2_technical_case_recovery(
            text,text,text,bigint
          );
        DROP FUNCTION IF EXISTS public.record_dts_v2_technical_case(
          text,text,text,text,text,text,text,text,text,bigint,text,
          integer,bigint
        );
        DROP FUNCTION IF EXISTS public.audit_manual_ops_case_status_v2();
        DROP FUNCTION IF EXISTS public.check_completion_decision_shape_v2();
        DROP FUNCTION IF EXISTS public.guard_ops_decisions_v2();
        DROP FUNCTION IF EXISTS
          public.guard_ops_case_recovery_append_only_v2();
        DROP FUNCTION IF EXISTS public.guard_ops_cases_v2();

        ALTER TABLE public.ops_decisions
          DROP CONSTRAINT ck_ops_decision_projection_events_v2,
          DROP CONSTRAINT ck_ops_decision_projection_status_v2,
          DROP CONSTRAINT ck_ops_decision_v2_row_version;
        ALTER TABLE public.ops_cases
          DROP CONSTRAINT ck_ops_case_v2_technical_source_ref,
          DROP CONSTRAINT ck_ops_case_v2_technical_shape,
          DROP CONSTRAINT ck_ops_case_v2_completion_identity,
          DROP CONSTRAINT ck_ops_case_v2_protected_source_ref,
          DROP CONSTRAINT ck_ops_case_v2_subject,
          DROP CONSTRAINT ck_ops_case_v2_region,
          DROP CONSTRAINT ck_ops_case_v2_versions;
        DROP FUNCTION IF EXISTS
          public.dts_technical_source_ref_shape_valid_v2(text,text);
        DROP FUNCTION IF EXISTS
          public.dts_projection_event_ids_valid_v2(jsonb);
        DROP FUNCTION IF EXISTS public.dts_ops_case_is_technical_v2(text);
        """
    )
    op.drop_table("ops_case_recovery_events", schema="public")
    for column_name in (
        "row_version",
        "projection_event_ids",
        "downstream_projection_status",
        "expected_source_position",
        "expected_source_revision",
        "expected_conflict_fingerprint",
        "expected_case_revision",
    ):
        op.drop_column("ops_decisions", column_name, schema="public")
    for column_name in (
        "last_recovered_at",
        "last_recovery_count",
        "last_recovery_event_id",
        "recovery_evidence_count",
        "evidence_fingerprint",
        "row_version",
        "case_revision",
        "source_appoint_id",
        "source_region",
        "source_ref",
    ):
        op.drop_column("ops_cases", column_name, schema="public")
    op.alter_column(
        "ops_cases",
        "teacher_id",
        existing_type=sa.String(length=64),
        nullable=False,
        schema="public",
    )
    op.alter_column(
        "ops_cases",
        "source_reason",
        type_=sa.String(length=64),
        existing_type=sa.String(length=128),
        existing_nullable=True,
        schema="public",
    )
    op.alter_column(
        "audit_events",
        "case_id",
        type_=sa.String(length=128),
        existing_type=sa.String(length=768),
        existing_nullable=True,
        schema="public",
    )
    op.alter_column(
        "ops_decisions",
        "case_id",
        type_=sa.String(length=128),
        existing_type=sa.String(length=768),
        existing_nullable=False,
        schema="public",
    )
    op.alter_column(
        "ops_cases",
        "case_id",
        type_=sa.String(length=128),
        existing_type=sa.String(length=768),
        existing_nullable=False,
        schema="public",
    )
    op.execute(
        r"""
        DROP INDEX IF EXISTS public.ix_ops_case_recovery_case_recorded_v2;
        DROP INDEX IF EXISTS public.ix_ops_cases_source_course_v2;
        DROP INDEX IF EXISTS public.uq_ops_cases_completion_course_v2;
        DROP INDEX IF EXISTS public.uq_ops_cases_source_ref_v2;
        COMMENT ON TABLE public.ops_cases IS NULL;
        COMMENT ON TABLE public.ops_decisions IS NULL;
        """
    )
