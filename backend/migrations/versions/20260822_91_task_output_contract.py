"""materialize DTS v2 trigger matches into the shared task fact.

Revision ID: 20260822_91_task_output_contract
Revises: 20260822_90_completion_correction
Create Date: 2026-08-22

This revision owns the database boundary from one complete current match set
to TASK_PLAN and, later, one frozen ``task_assignments`` row.  Notifications
and Ops Cases deliberately remain typed, unmatched outputs for rev93.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260822_91_task_output_contract"
down_revision: Union[str, None] = "20260822_90_completion_correction"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


OUTBOX_ROLE = "tit_dts_outbox_worker_runtime"
APP_ROLE = "tit_growth_app"
TEACHER_ROLE = "tit_teacher_crud"
EMPTY_OBJECT_HASH = (
    "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"
)


def _preflight() -> None:
    op.execute(
        r"""
        DO $task_output_preflight$
        DECLARE relation_name text;
        BEGIN
          FOREACH relation_name IN ARRAY ARRAY[
            'config_versions','task_templates','task_assignments','teachers',
            'personalized_trigger_matches','domain_aggregate_revisions',
            'outbox_events','source_courses','source_course_participations',
            'dts_source_rows','teacher_student_relationship_current',
            'lesson_source_wide','ops_cases','notifications'
          ] LOOP
            IF to_regclass('public.' || relation_name) IS NULL THEN
              RAISE EXCEPTION 'DTS_V2_TASK_OUTPUT_PREREQUISITE_MISSING:%',
                relation_name;
            END IF;
          END LOOP;
          IF to_regprocedure(
               'public.dts_canonical_json_sha256_v1(jsonb)'
             ) IS NULL
             OR to_regprocedure(
               'public.dts_v2_runtime_primary_guard_v1(text)'
             ) IS NULL
             OR to_regrole('tit_dts_outbox_worker_runtime') IS NULL THEN
            RAISE EXCEPTION 'DTS_V2_TASK_OUTPUT_PREREQUISITE_MISSING';
          END IF;
          IF to_regprocedure(
               'public.reconcile_course_trigger_matches_v2(text,text,bigint,bigint,text,jsonb,text)'
             ) IS NOT NULL
             OR to_regprocedure(
               'public.materialize_task_plan_v2(text,text,bigint,text)'
             ) IS NOT NULL THEN
            RAISE EXCEPTION 'DTS_V2_TASK_OUTPUT_ALREADY_INSTALLED';
          END IF;
        END
        $task_output_preflight$;

        LOCK TABLE public.config_versions,public.task_templates,
          public.task_assignments,public.personalized_trigger_matches,
          public.domain_aggregate_revisions,public.outbox_events
        IN SHARE ROW EXCLUSIVE MODE;
        """
    )


def _expand_config_contract() -> None:
    op.add_column(
        "config_versions",
        sa.Column(
            "schema_version",
            sa.Integer(),
            nullable=True,
            server_default=sa.text("1"),
        ),
        schema="public",
    )
    op.add_column(
        "config_versions",
        sa.Column("payload_hash", sa.String(length=64), nullable=True),
        schema="public",
    )
    op.execute(
        """
        UPDATE public.config_versions
        SET schema_version=1,
            payload_hash=public.dts_canonical_json_sha256_v1(payload)
        """
    )
    op.alter_column(
        "config_versions",
        "schema_version",
        nullable=False,
        existing_type=sa.Integer(),
        server_default=sa.text("1"),
        schema="public",
    )
    op.alter_column(
        "config_versions",
        "payload_hash",
        nullable=False,
        existing_type=sa.String(length=64),
        schema="public",
    )
    op.drop_constraint(
        "ck_config_versions_key",
        "config_versions",
        type_="check",
        schema="public",
    )
    op.create_check_constraint(
        "ck_config_versions_key",
        "config_versions",
        "config_key IN ('SCORE_GRADUATION','AGENT_POLICY',"
        "'DELIVERY_POLICY','teacher_personalized_copy')",
        schema="public",
    )
    op.create_check_constraint(
        "ck_config_versions_schema_payload_v2",
        "config_versions",
        "schema_version>=1 AND payload_hash ~ '^[0-9a-f]{64}$' "
        "AND payload_hash=public.dts_canonical_json_sha256_v1(payload)",
        schema="public",
    )
    op.create_unique_constraint(
        "uq_config_version_identity_key",
        "config_versions",
        ["version_id", "config_key"],
        schema="public",
    )
    op.execute(
        r"""
        CREATE OR REPLACE FUNCTION public.guard_config_version_contract_v2()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        BEGIN
          IF TG_OP='DELETE' AND OLD.status IN ('PUBLISHED','RETIRED') THEN
            RAISE EXCEPTION 'CONFIG_VERSION_IMMUTABLE'
              USING ERRCODE='23514';
          ELSIF TG_OP='DELETE' THEN
            RETURN OLD;
          END IF;
          NEW.payload_hash := public.dts_canonical_json_sha256_v1(NEW.payload);
          IF NEW.schema_version<>1 THEN
            RAISE EXCEPTION 'CONFIG_SCHEMA_VERSION_UNSUPPORTED'
              USING ERRCODE='23514';
          END IF;
          IF TG_OP='UPDATE' AND OLD.status IN ('PUBLISHED','RETIRED') THEN
            IF NEW.version_id IS DISTINCT FROM OLD.version_id
               OR NEW.config_key IS DISTINCT FROM OLD.config_key
               OR NEW.version_number IS DISTINCT FROM OLD.version_number
               OR NEW.schema_version IS DISTINCT FROM OLD.schema_version
               OR NEW.payload IS DISTINCT FROM OLD.payload
               OR NEW.payload_hash IS DISTINCT FROM OLD.payload_hash THEN
              RAISE EXCEPTION 'CONFIG_VERSION_IMMUTABLE'
                USING ERRCODE='23514';
            END IF;
            IF OLD.status='RETIRED' AND NEW.status IS DISTINCT FROM OLD.status THEN
              RAISE EXCEPTION 'CONFIG_RETIRED_TERMINAL'
                USING ERRCODE='23514';
            END IF;
            IF OLD.status='PUBLISHED' AND NEW.status='RETIRED'
               AND current_setting('tit.catalog_publication_v2',true)<>'on' THEN
              RAISE EXCEPTION 'CONFIG_PUBLICATION_COMMAND_REQUIRED'
                USING ERRCODE='42501';
            END IF;
          END IF;
          IF TG_OP='UPDATE' AND NEW.status='PUBLISHED'
             AND OLD.status IS DISTINCT FROM 'PUBLISHED'
             AND current_setting('tit.catalog_publication_v2',true)<>'on' THEN
            RAISE EXCEPTION 'CONFIG_PUBLICATION_COMMAND_REQUIRED'
              USING ERRCODE='42501';
          END IF;
          RETURN NEW;
        END
        $function$;
        REVOKE ALL ON FUNCTION public.guard_config_version_contract_v2()
          FROM PUBLIC;
        DROP TRIGGER IF EXISTS trg_config_version_contract_v2
          ON public.config_versions;
        CREATE TRIGGER trg_config_version_contract_v2
        BEFORE INSERT OR UPDATE OR DELETE ON public.config_versions
        FOR EACH ROW EXECUTE FUNCTION public.guard_config_version_contract_v2();
        """
    )


def _expand_assignment_contract() -> None:
    columns = (
        sa.Column("materialization_seed_kind", sa.String(length=32)),
        sa.Column("materialization_seed_key", sa.String(length=768)),
        sa.Column("materialization_seed_revision", sa.BigInteger()),
        sa.Column("materialization_seed_payload_hash", sa.String(length=64)),
        sa.Column("materialization_template_revision", sa.Integer()),
        sa.Column("materialization_plan_revision", sa.BigInteger()),
        sa.Column("eligibility_generation", sa.BigInteger()),
        sa.Column("eligible_since_at", sa.DateTime(timezone=True)),
        sa.Column("teacher_copy_version_id", sa.String(length=128)),
        sa.Column("teacher_copy_config_key", sa.String(length=64)),
        sa.Column("teacher_execution_variant", sa.String(length=48)),
        sa.Column("plan_evidence_hash", sa.String(length=64)),
    )
    for column in columns:
        op.add_column("task_assignments", column, schema="public")
    op.create_unique_constraint(
        "uq_task_template_row_identity",
        "task_templates",
        ["row_id", "template_id"],
        schema="public",
    )
    op.create_foreign_key(
        "fk_task_assignment_template_code_v2",
        "task_assignments",
        "task_templates",
        ["template_version_id", "task_code"],
        ["row_id", "template_id"],
        source_schema="public",
        referent_schema="public",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_task_assignment_copy_version_v2",
        "task_assignments",
        "config_versions",
        ["teacher_copy_version_id", "teacher_copy_config_key"],
        ["version_id", "config_key"],
        source_schema="public",
        referent_schema="public",
        ondelete="RESTRICT",
    )
    op.execute(
        r"""
        DROP TRIGGER IF EXISTS trg_task_assignment_write
          ON public.task_assignments;

        UPDATE public.task_assignments assignment
        SET materialization_seed_kind='LEGACY_FROZEN',
            materialization_seed_key=
              'legacy-assignment:' || assignment.assignment_id,
            materialization_seed_revision=0,
            materialization_template_revision=template.revision,
            materialization_plan_revision=0,
            eligibility_generation=0,
            eligible_since_at=assignment.assigned_at,
            teacher_copy_version_id=NULL,
            teacher_copy_config_key=NULL,
            teacher_execution_variant='LEGACY_FROZEN',
            plan_evidence_hash=public.dts_canonical_json_sha256_v1(
              jsonb_build_object(
                'protocol_version','legacy-assignment-evidence-v1',
                'assignment_id',assignment.assignment_id,
                'evidence_snapshot',assignment.evidence_snapshot,
                'eligibility_time_source','LEGACY_ASSIGNED_AT'
              )
            ),
            materialization_seed_payload_hash=
              public.dts_canonical_json_sha256_v1(
                jsonb_build_object(
                  'why',assignment.why,
                  'display_title',assignment.display_title,
                  'evidence_snapshot',assignment.evidence_snapshot,
                  'template_version_id',assignment.template_version_id,
                  'template_revision',template.revision,
                  'teacher_execution_variant','LEGACY_FROZEN',
                  'eligibility_generation',0,
                  'eligible_since_at',to_char(
                    assignment.assigned_at AT TIME ZONE 'UTC',
                    'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
                  ),
                  'due_at',CASE WHEN assignment.due_at IS NULL THEN NULL ELSE
                    to_char(assignment.due_at AT TIME ZONE 'UTC',
                      'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') END,
                  'timezone_used',assignment.timezone_used,
                  'timezone_source',assignment.timezone_source,
                  'timezone_verified_at',CASE
                    WHEN assignment.timezone_verified_at IS NULL THEN NULL ELSE
                      to_char(assignment.timezone_verified_at AT TIME ZONE 'UTC',
                        'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') END,
                  'teacher_copy_version_id',NULL,
                  'teacher_copy_config_key',NULL
                )
              )
        FROM public.task_templates template
        WHERE assignment.task_kind='PERSONALIZED_IMPROVEMENT'
          AND template.row_id=assignment.template_version_id;
        """
    )
    op.create_check_constraint(
        "ck_task_assignment_materialization_contract_v2",
        "task_assignments",
        "(task_kind='FIXED_GROWTH' AND materialization_seed_kind IS NULL "
        "AND materialization_seed_key IS NULL "
        "AND materialization_seed_revision IS NULL "
        "AND materialization_seed_payload_hash IS NULL "
        "AND materialization_template_revision IS NULL "
        "AND materialization_plan_revision IS NULL "
        "AND eligibility_generation IS NULL AND eligible_since_at IS NULL "
        "AND teacher_copy_version_id IS NULL "
        "AND teacher_copy_config_key IS NULL "
        "AND teacher_execution_variant IS NULL "
        "AND plan_evidence_hash IS NULL) OR "
        "(task_kind='PERSONALIZED_IMPROVEMENT' "
        "AND materialization_seed_kind IN ('LEGACY_FROZEN','LEGACY_COMPAT','MATCH') "
        "AND btrim(materialization_seed_key)<>'' "
        "AND materialization_seed_revision>=0 "
        "AND materialization_seed_payload_hash ~ '^[0-9a-f]{64}$' "
        "AND materialization_template_revision>=1 "
        "AND materialization_plan_revision>=0 "
        "AND eligibility_generation>=0 AND eligible_since_at IS NOT NULL "
        "AND teacher_execution_variant IN "
        "('LEGACY_FROZEN','GENERAL','TEACHING_ENVIRONMENT_PHOTO') "
        "AND plan_evidence_hash ~ '^[0-9a-f]{64}$' "
        "AND ((materialization_seed_kind='LEGACY_FROZEN' "
        "AND materialization_seed_revision=0 "
        "AND materialization_plan_revision=0 "
        "AND eligibility_generation=0 "
        "AND teacher_execution_variant='LEGACY_FROZEN' "
        "AND teacher_copy_version_id IS NULL "
        "AND teacher_copy_config_key IS NULL) OR "
        "(materialization_seed_kind IN ('LEGACY_COMPAT','MATCH') "
        "AND teacher_copy_version_id IS NOT NULL "
        "AND teacher_copy_config_key='teacher_personalized_copy'))) ",
        schema="public",
    )
    op.create_index(
        "ix_task_assignment_materialization_seed_v2",
        "task_assignments",
        ["materialization_seed_kind", "materialization_seed_key"],
        schema="public",
    )


def _expand_match_contract() -> None:
    json_value = sa.JSON().with_variant(
        postgresql.JSONB(astext_type=sa.Text()), "postgresql"
    )
    columns = (
        sa.Column("source_region", sa.String(length=8)),
        sa.Column("source_appoint_id", sa.String(length=512)),
        sa.Column("participation_seq", sa.Integer()),
        sa.Column("match_kind", sa.String(length=64)),
        sa.Column("match_revision", sa.BigInteger()),
        sa.Column("last_transition_at", sa.DateTime(timezone=True)),
        sa.Column("target_task_code", sa.String(length=64)),
        sa.Column("assignment_dedupe_key", sa.String(length=256)),
        sa.Column("seed_rule_rank", sa.Integer()),
        sa.Column("threshold_required", sa.Integer()),
        sa.Column("source_candidate_active", sa.Boolean()),
        sa.Column("plan_evidence", json_value),
        sa.Column("plan_evidence_hash", sa.String(length=64)),
        sa.Column("teacher_execution_variant", sa.String(length=48)),
        sa.Column("output_key", sa.String(length=768)),
        sa.Column("source_ref", sa.String(length=768)),
        sa.Column("assignment_dedupe_key_sort_bytes", sa.LargeBinary()),
        sa.Column("source_region_rank", sa.Integer()),
        sa.Column("source_appoint_id_type", sa.String(length=16)),
        sa.Column("source_appoint_id_type_rank", sa.Integer()),
        sa.Column("source_appoint_id_numeric", sa.Numeric()),
        sa.Column("source_appoint_id_sort_bytes", sa.LargeBinary()),
        sa.Column("evidence_discriminator", sa.String(length=512)),
        sa.Column("evidence_discriminator_type", sa.String(length=16)),
        sa.Column("evidence_discriminator_type_rank", sa.Integer()),
        sa.Column("evidence_discriminator_numeric", sa.Numeric()),
        sa.Column("evidence_discriminator_sort_bytes", sa.LargeBinary()),
        sa.Column("dedupe_key_sort_bytes", sa.LargeBinary()),
        sa.Column("materialization_origin", sa.String(length=32)),
        sa.Column("created_projection_generation", sa.BigInteger()),
        sa.Column("serving_projection_generation", sa.BigInteger()),
        sa.Column("is_serving", sa.Boolean()),
        sa.Column("task_assignment_id", sa.String(length=128)),
        sa.Column("ops_case_id", sa.String(length=768)),
        sa.Column("notification_id", sa.String(length=128)),
        sa.Column("source_aggregate_revision", sa.BigInteger()),
        sa.Column("projection_generation", sa.BigInteger()),
        sa.Column("triggering_event_id", sa.String(length=512)),
    )
    for column in columns:
        op.add_column(
            "personalized_trigger_matches", column, schema="public"
        )
    op.execute(
        rf"""
        UPDATE public.personalized_trigger_matches match
        SET source_region=match.lesson_source_region,
            source_appoint_id=match.lesson_id,
            participation_seq=NULL,
            match_kind='LEGACY_REUSED',match_revision=1,
            last_transition_at=coalesce(match.updated_at,match.matched_at),
            target_task_code=NULL,assignment_dedupe_key=NULL,
            seed_rule_rank=NULL,threshold_required=NULL,
            source_candidate_active=false,
            plan_evidence=match.evidence_snapshot,
            plan_evidence_hash=public.dts_canonical_json_sha256_v1(
              match.evidence_snapshot
            ),
            teacher_execution_variant=NULL,
            output_key=match.output_id,
            source_ref='trigger-match:' || match.dedupe_key,
            assignment_dedupe_key_sort_bytes=NULL,
            source_region_rank=CASE match.lesson_source_region
              WHEN 'dom' THEN 0 WHEN 'ovs' THEN 1 ELSE 2 END,
            source_appoint_id_type=CASE WHEN match.lesson_id IS NULL
              THEN 'NONE' ELSE 'TEXT' END,
            source_appoint_id_type_rank=CASE WHEN match.lesson_id IS NULL
              THEN 2 ELSE 1 END,
            source_appoint_id_numeric=NULL,
            source_appoint_id_sort_bytes=CASE WHEN match.lesson_id IS NULL
              THEN NULL ELSE convert_to(match.lesson_id,'UTF8') END,
            evidence_discriminator=NULL,
            evidence_discriminator_type='NONE',
            evidence_discriminator_type_rank=2,
            evidence_discriminator_numeric=NULL,
            evidence_discriminator_sort_bytes=NULL,
            dedupe_key_sort_bytes=convert_to(match.dedupe_key,'UTF8'),
            materialization_origin='LEGACY_REUSED',
            created_projection_generation=0,
            serving_projection_generation=NULL,is_serving=false,
            task_assignment_id=CASE
              WHEN match.output_type='TEACHER_TASK' AND EXISTS(
                SELECT 1 FROM public.task_assignments assignment
                WHERE assignment.assignment_id=match.output_id
              ) THEN match.output_id ELSE NULL END,
            ops_case_id=CASE
              WHEN match.output_type='OPS_CASE' AND EXISTS(
                SELECT 1 FROM public.ops_cases case_row
                WHERE case_row.case_id=match.output_id
              ) THEN match.output_id ELSE NULL END,
            notification_id=CASE
              WHEN match.output_type='NOTIFICATION' AND EXISTS(
                SELECT 1 FROM public.notifications notification
                WHERE notification.notification_id=match.output_id
              ) THEN match.output_id ELSE NULL END,
            source_aggregate_revision=NULL,projection_generation=NULL,
            triggering_event_id=NULL;
        """
    )
    for name, type_ in (
        ("match_kind", sa.String(length=64)),
        ("match_revision", sa.BigInteger()),
        ("last_transition_at", sa.DateTime(timezone=True)),
        ("source_candidate_active", sa.Boolean()),
        ("plan_evidence", json_value),
        ("plan_evidence_hash", sa.String(length=64)),
        ("source_ref", sa.String(length=768)),
        ("source_region_rank", sa.Integer()),
        ("source_appoint_id_type", sa.String(length=16)),
        ("source_appoint_id_type_rank", sa.Integer()),
        ("evidence_discriminator_type", sa.String(length=16)),
        ("evidence_discriminator_type_rank", sa.Integer()),
        ("dedupe_key_sort_bytes", sa.LargeBinary()),
        ("materialization_origin", sa.String(length=32)),
        ("created_projection_generation", sa.BigInteger()),
        ("is_serving", sa.Boolean()),
    ):
        op.alter_column(
            "personalized_trigger_matches",
            name,
            nullable=False,
            existing_type=type_,
            schema="public",
        )
    op.create_foreign_key(
        "fk_trigger_match_task_assignment_v2",
        "personalized_trigger_matches",
        "task_assignments",
        ["task_assignment_id"],
        ["assignment_id"],
        source_schema="public",
        referent_schema="public",
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_foreign_key(
        "fk_trigger_match_ops_case_v2",
        "personalized_trigger_matches",
        "ops_cases",
        ["ops_case_id"],
        ["case_id"],
        source_schema="public",
        referent_schema="public",
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_foreign_key(
        "fk_trigger_match_notification_v2",
        "personalized_trigger_matches",
        "notifications",
        ["notification_id"],
        ["notification_id"],
        source_schema="public",
        referent_schema="public",
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_check_constraint(
        "ck_trigger_match_v2_identity",
        "personalized_trigger_matches",
        "match_revision>=1 AND btrim(match_kind)<>'' "
        "AND btrim(source_ref)<>'' "
        "AND plan_evidence_hash ~ '^[0-9a-f]{64}$' "
        "AND plan_evidence_hash=public.dts_canonical_json_sha256_v1(plan_evidence) "
        "AND materialization_origin IN "
        "('LEGACY_REUSED','CUTOVER_CREATED','V2_LIVE') "
        "AND created_projection_generation>=0 "
        "AND ((is_serving AND serving_projection_generation>=1) "
        "OR (NOT is_serving AND serving_projection_generation IS NULL))",
        schema="public",
    )
    op.create_check_constraint(
        "ck_trigger_match_v2_typed_seed",
        "personalized_trigger_matches",
        "source_region_rank=CASE source_region WHEN 'dom' THEN 0 "
        "WHEN 'ovs' THEN 1 ELSE 2 END "
        "AND source_appoint_id_type_rank=CASE source_appoint_id_type "
        "WHEN 'NUMERIC' THEN 0 WHEN 'TEXT' THEN 1 ELSE 2 END "
        "AND evidence_discriminator_type_rank=CASE evidence_discriminator_type "
        "WHEN 'NUMERIC' THEN 0 WHEN 'TEXT' THEN 1 ELSE 2 END "
        "AND ((source_appoint_id_type='NONE' "
        "AND source_appoint_id IS NULL AND source_appoint_id_numeric IS NULL "
        "AND source_appoint_id_sort_bytes IS NULL) OR "
        "(source_appoint_id_type='NUMERIC' AND source_appoint_id IS NOT NULL "
        "AND source_appoint_id_numeric IS NOT NULL "
        "AND source_appoint_id_sort_bytes IS NULL) OR "
        "(source_appoint_id_type='TEXT' AND source_appoint_id IS NOT NULL "
        "AND source_appoint_id_numeric IS NULL "
        "AND source_appoint_id_sort_bytes=convert_to(source_appoint_id,'UTF8'))) "
        "AND ((evidence_discriminator_type='NONE' "
        "AND evidence_discriminator IS NULL "
        "AND evidence_discriminator_numeric IS NULL "
        "AND evidence_discriminator_sort_bytes IS NULL) OR "
        "(evidence_discriminator_type='NUMERIC' "
        "AND evidence_discriminator IS NOT NULL "
        "AND evidence_discriminator_numeric IS NOT NULL "
        "AND evidence_discriminator_sort_bytes IS NULL) OR "
        "(evidence_discriminator_type='TEXT' "
        "AND evidence_discriminator IS NOT NULL "
        "AND evidence_discriminator_numeric IS NULL "
        "AND evidence_discriminator_sort_bytes="
        "convert_to(evidence_discriminator,'UTF8'))) "
        "AND dedupe_key_sort_bytes=convert_to(dedupe_key,'UTF8') "
        "AND (assignment_dedupe_key IS NULL "
        "OR assignment_dedupe_key_sort_bytes="
        "convert_to(assignment_dedupe_key,'UTF8'))",
        schema="public",
    )
    op.create_check_constraint(
        "ck_trigger_match_v2_output_link",
        "personalized_trigger_matches",
        "materialization_origin='LEGACY_REUSED' OR "
        "((match_status IN ('MATCHED','PENDING_DATA','FAILED') "
        "AND task_assignment_id IS NULL AND ops_case_id IS NULL "
        "AND notification_id IS NULL AND materialized_at IS NULL "
        "AND output_id IS NULL) OR "
        "(match_status='MATERIALIZED' AND materialized_at IS NOT NULL "
        "AND ((output_type='TEACHER_TASK' AND task_assignment_id IS NOT NULL "
        "AND ops_case_id IS NULL AND notification_id IS NULL "
        "AND output_id=task_assignment_id) OR "
        "(output_type='OPS_CASE' AND ops_case_id IS NOT NULL "
        "AND task_assignment_id IS NULL AND notification_id IS NULL "
        "AND output_id=ops_case_id) OR "
        "(output_type='NOTIFICATION' AND notification_id IS NOT NULL "
        "AND task_assignment_id IS NULL AND ops_case_id IS NULL "
        "AND output_id=notification_id))) OR "
        "(match_status='SUPPRESSED' AND ((task_assignment_id IS NULL "
        "AND ops_case_id IS NULL AND notification_id IS NULL "
        "AND materialized_at IS NULL AND output_id IS NULL) OR "
        "(materialized_at IS NOT NULL AND ((output_type='TEACHER_TASK' "
        "AND output_id=task_assignment_id AND ops_case_id IS NULL "
        "AND notification_id IS NULL) OR (output_type='OPS_CASE' "
        "AND output_id=ops_case_id AND task_assignment_id IS NULL "
        "AND notification_id IS NULL) OR (output_type='NOTIFICATION' "
        "AND output_id=notification_id AND task_assignment_id IS NULL "
        "AND ops_case_id IS NULL))))))",
        schema="public",
    )
    op.create_index(
        "ix_trigger_match_v2_assignment_active_seed",
        "personalized_trigger_matches",
        [
            "assignment_dedupe_key_sort_bytes",
            "is_serving",
            "match_status",
            "seed_rule_rank",
            "source_region_rank",
            "source_appoint_id_type_rank",
            "source_appoint_id_numeric",
            "source_appoint_id_sort_bytes",
            "participation_seq",
            "evidence_discriminator_type_rank",
            "evidence_discriminator_numeric",
            "evidence_discriminator_sort_bytes",
            "dedupe_key_sort_bytes",
        ],
        schema="public",
    )
    op.create_index(
        "ix_trigger_match_v2_course_current",
        "personalized_trigger_matches",
        ["source_region", "source_appoint_id", "source_candidate_active"],
        schema="public",
    )


def _install_row_guards() -> None:
    op.execute(
        r"""
        CREATE OR REPLACE FUNCTION public.normalize_trigger_match_v2()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        BEGIN
          IF TG_OP='DELETE' THEN
            RAISE EXCEPTION 'TRIGGER_MATCH_DELETE_FORBIDDEN'
              USING ERRCODE='42501';
          END IF;
          IF TG_OP='UPDATE' THEN
            IF NEW.trigger_match_id IS DISTINCT FROM OLD.trigger_match_id
               OR NEW.dedupe_key IS DISTINCT FROM OLD.dedupe_key
               OR NEW.teacher_id IS DISTINCT FROM OLD.teacher_id
               OR NEW.materialization_origin IS DISTINCT FROM
                    OLD.materialization_origin
               OR NEW.created_projection_generation IS DISTINCT FROM
                    OLD.created_projection_generation
               OR NEW.task_assignment_id IS DISTINCT FROM
                    OLD.task_assignment_id
                    AND OLD.task_assignment_id IS NOT NULL
               OR NEW.ops_case_id IS DISTINCT FROM OLD.ops_case_id
                    AND OLD.ops_case_id IS NOT NULL
               OR NEW.notification_id IS DISTINCT FROM OLD.notification_id
                    AND OLD.notification_id IS NOT NULL THEN
              RAISE EXCEPTION 'TRIGGER_MATCH_IMMUTABLE_IDENTITY'
                USING ERRCODE='23514';
            END IF;
            IF NEW.match_revision<OLD.match_revision THEN
              RAISE EXCEPTION 'TRIGGER_MATCH_REVISION_REGRESSION'
                USING ERRCODE='23514';
            END IF;
          END IF;
          NEW.source_region_rank := CASE NEW.source_region
            WHEN 'dom' THEN 0 WHEN 'ovs' THEN 1 ELSE 2 END;
          NEW.source_appoint_id_type_rank :=
            CASE NEW.source_appoint_id_type WHEN 'NUMERIC' THEN 0
              WHEN 'TEXT' THEN 1 ELSE 2 END;
          NEW.evidence_discriminator_type_rank :=
            CASE NEW.evidence_discriminator_type WHEN 'NUMERIC' THEN 0
              WHEN 'TEXT' THEN 1 ELSE 2 END;
          NEW.assignment_dedupe_key_sort_bytes := CASE
            WHEN NEW.assignment_dedupe_key IS NULL THEN NULL
            ELSE convert_to(NEW.assignment_dedupe_key,'UTF8') END;
          NEW.dedupe_key_sort_bytes := convert_to(NEW.dedupe_key,'UTF8');
          IF NEW.source_appoint_id_type='NUMERIC' THEN
            NEW.source_appoint_id_numeric := NEW.source_appoint_id::numeric;
            NEW.source_appoint_id_sort_bytes := NULL;
          ELSIF NEW.source_appoint_id_type='TEXT' THEN
            NEW.source_appoint_id_numeric := NULL;
            NEW.source_appoint_id_sort_bytes :=
              convert_to(NEW.source_appoint_id,'UTF8');
          ELSE
            NEW.source_appoint_id := NULL;
            NEW.source_appoint_id_numeric := NULL;
            NEW.source_appoint_id_sort_bytes := NULL;
          END IF;
          IF NEW.evidence_discriminator_type='NUMERIC' THEN
            NEW.evidence_discriminator_numeric :=
              NEW.evidence_discriminator::numeric;
            NEW.evidence_discriminator_sort_bytes := NULL;
          ELSIF NEW.evidence_discriminator_type='TEXT' THEN
            NEW.evidence_discriminator_numeric := NULL;
            NEW.evidence_discriminator_sort_bytes :=
              convert_to(NEW.evidence_discriminator,'UTF8');
          ELSE
            NEW.evidence_discriminator := NULL;
            NEW.evidence_discriminator_numeric := NULL;
            NEW.evidence_discriminator_sort_bytes := NULL;
          END IF;
          NEW.plan_evidence_hash :=
            public.dts_canonical_json_sha256_v1(NEW.plan_evidence);
          NEW.source_ref := coalesce(
            nullif(NEW.source_ref,''),'trigger-match:' || NEW.dedupe_key
          );
          NEW.output_id := coalesce(
            NEW.task_assignment_id,NEW.ops_case_id,NEW.notification_id
          );
          RETURN NEW;
        END
        $function$;
        REVOKE ALL ON FUNCTION public.normalize_trigger_match_v2()
          FROM PUBLIC;
        CREATE TRIGGER trg_normalize_trigger_match_v2
        BEFORE INSERT OR UPDATE OR DELETE
        ON public.personalized_trigger_matches
        FOR EACH ROW EXECUTE FUNCTION public.normalize_trigger_match_v2();

        CREATE OR REPLACE FUNCTION public.enforce_task_assignment_write()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE template_code text;
        DECLARE template_status text;
        DECLARE template_mode text;
        DECLARE actor_name text := coalesce(
          nullif(current_setting('role',true),'none'),session_user
        );
        BEGIN
          IF TG_OP='DELETE' THEN
            RAISE EXCEPTION 'task assignments cannot be deleted'
              USING ERRCODE='42501';
          END IF;
          SELECT template_id,status,integration_mode
          INTO template_code,template_status,template_mode
          FROM public.task_templates WHERE row_id=NEW.template_version_id;
          IF NOT FOUND OR template_code<>NEW.task_code THEN
            RAISE EXCEPTION 'TASK_ASSIGNMENT_TEMPLATE_MISMATCH'
              USING ERRCODE='23514';
          END IF;
          IF NEW.task_kind='FIXED_GROWTH'
             AND template_mode<>'INBOUND_STATUS_ONLY' THEN
            RAISE EXCEPTION 'TASK_ASSIGNMENT_TEMPLATE_MISMATCH'
              USING ERRCODE='23514';
          END IF;
          IF NEW.task_kind='PERSONALIZED_IMPROVEMENT'
             AND template_mode<>'OUTBOUND_MANAGED' THEN
            RAISE EXCEPTION 'TASK_ASSIGNMENT_TEMPLATE_MISMATCH'
              USING ERRCODE='23514';
          END IF;
          IF TG_OP='INSERT' THEN
            IF template_status<>'PUBLISHED' THEN
              RAISE EXCEPTION 'TASK_ASSIGNMENT_TEMPLATE_NOT_PUBLISHED'
                USING ERRCODE='23514';
            END IF;
            IF actor_name='tit_teacher_crud' OR NEW.status<>'ASSIGNED' THEN
              RAISE EXCEPTION 'TASK_ASSIGNMENT_INSERT_DENIED'
                USING ERRCODE='42501';
            END IF;
            IF NEW.task_kind='PERSONALIZED_IMPROVEMENT' THEN
              IF NEW.materialization_seed_kind<>'MATCH'
                 OR current_setting('tit.task_materialization_v2',true)<>'on' THEN
                RAISE EXCEPTION 'TASK_PLAN_MATERIALIZATION_COMMAND_REQUIRED'
                  USING ERRCODE='42501';
              END IF;
            END IF;
            NEW.row_version:=1;
            NEW.updated_at:=clock_timestamp();
            RETURN NEW;
          END IF;
          IF OLD.status IN ('COMPLETED','EXPIRED','WAIVED','CANCELLED') THEN
            RAISE EXCEPTION 'terminal task assignment % is immutable',
              OLD.assignment_id USING ERRCODE='23514';
          END IF;
          IF NEW.assignment_id IS DISTINCT FROM OLD.assignment_id
             OR NEW.teacher_id IS DISTINCT FROM OLD.teacher_id
             OR NEW.task_code IS DISTINCT FROM OLD.task_code
             OR NEW.template_version_id IS DISTINCT FROM OLD.template_version_id
             OR NEW.task_kind IS DISTINCT FROM OLD.task_kind
             OR NEW.creator_system IS DISTINCT FROM OLD.creator_system
             OR NEW.priority IS DISTINCT FROM OLD.priority
             OR NEW.why IS DISTINCT FROM OLD.why
             OR NEW.display_title IS DISTINCT FROM OLD.display_title
             OR NEW.evidence_snapshot IS DISTINCT FROM OLD.evidence_snapshot
             OR NEW.due_at IS DISTINCT FROM OLD.due_at
             OR NEW.timezone_used IS DISTINCT FROM OLD.timezone_used
             OR NEW.timezone_source IS DISTINCT FROM OLD.timezone_source
             OR NEW.timezone_verified_at IS DISTINCT FROM OLD.timezone_verified_at
             OR NEW.source_mode IS DISTINCT FROM OLD.source_mode
             OR NEW.dedupe_key IS DISTINCT FROM OLD.dedupe_key
             OR NEW.created_by IS DISTINCT FROM OLD.created_by
             OR NEW.assigned_at IS DISTINCT FROM OLD.assigned_at
             OR NEW.created_at IS DISTINCT FROM OLD.created_at
             OR NEW.materialization_seed_kind IS DISTINCT FROM OLD.materialization_seed_kind
             OR NEW.materialization_seed_key IS DISTINCT FROM OLD.materialization_seed_key
             OR NEW.materialization_seed_revision IS DISTINCT FROM OLD.materialization_seed_revision
             OR NEW.materialization_seed_payload_hash IS DISTINCT FROM OLD.materialization_seed_payload_hash
             OR NEW.materialization_template_revision IS DISTINCT FROM OLD.materialization_template_revision
             OR NEW.materialization_plan_revision IS DISTINCT FROM OLD.materialization_plan_revision
             OR NEW.eligibility_generation IS DISTINCT FROM OLD.eligibility_generation
             OR NEW.eligible_since_at IS DISTINCT FROM OLD.eligible_since_at
             OR NEW.teacher_copy_version_id IS DISTINCT FROM OLD.teacher_copy_version_id
             OR NEW.teacher_copy_config_key IS DISTINCT FROM OLD.teacher_copy_config_key
             OR NEW.teacher_execution_variant IS DISTINCT FROM OLD.teacher_execution_variant
             OR NEW.plan_evidence_hash IS DISTINCT FROM OLD.plan_evidence_hash THEN
            RAISE EXCEPTION 'immutable task assignment identity/content fields cannot change'
              USING ERRCODE='23514';
          END IF;
          IF NEW.row_version<>OLD.row_version THEN
            RAISE EXCEPTION 'stale or caller-modified row_version for assignment %',
              OLD.assignment_id USING ERRCODE='40001';
          END IF;
          IF actor_name='tit_growth_app'
             AND NEW.completed_at IS DISTINCT FROM OLD.completed_at THEN
            RAISE EXCEPTION 'TiDe runtime cannot write teacher completion timestamps'
              USING ERRCODE='42501';
          END IF;
          IF actor_name='tit_teacher_crud' THEN
            IF NEW.status IN ('WAIVED','CANCELLED')
               AND NEW.status IS DISTINCT FROM OLD.status THEN
              RAISE EXCEPTION 'teacher app cannot waive or cancel assignments'
                USING ERRCODE='42501';
            END IF;
            NEW.updated_by:=actor_name;
          END IF;
          IF NEW.status IS DISTINCT FROM OLD.status THEN
            IF NEW.status_changed_at IS NULL
               OR NEW.status_changed_at IS NOT DISTINCT FROM OLD.status_changed_at
               OR NEW.status_changed_at<OLD.status_changed_at THEN
              RAISE EXCEPTION 'status change requires a monotonic status_changed_at'
                USING ERRCODE='23514';
            END IF;
            IF NOT (
              (OLD.status='ASSIGNED' AND NEW.status IN
                ('VIEWED','IN_PROGRESS','SUBMITTED','UNDER_REVIEW','COMPLETED',
                 'FAILED','EXPIRED','WAIVED','CANCELLED')) OR
              (OLD.status='VIEWED' AND NEW.status IN
                ('IN_PROGRESS','SUBMITTED','UNDER_REVIEW','COMPLETED','FAILED',
                 'EXPIRED','WAIVED','CANCELLED')) OR
              (OLD.status='IN_PROGRESS' AND NEW.status IN
                ('SUBMITTED','UNDER_REVIEW','COMPLETED','FAILED','EXPIRED',
                 'WAIVED','CANCELLED')) OR
              (OLD.status='SUBMITTED' AND NEW.status IN
                ('UNDER_REVIEW','COMPLETED','FAILED','EXPIRED','WAIVED',
                 'CANCELLED')) OR
              (OLD.status='UNDER_REVIEW' AND NEW.status IN
                ('COMPLETED','FAILED','EXPIRED','WAIVED','CANCELLED')) OR
              (OLD.status='FAILED' AND NEW.status IN
                ('IN_PROGRESS','SUBMITTED','UNDER_REVIEW','COMPLETED'))
            ) THEN
              RAISE EXCEPTION 'invalid task status transition: % -> %',
                OLD.status,NEW.status USING ERRCODE='23514';
            END IF;
            IF NEW.status='COMPLETED' THEN
              NEW.completed_at:=coalesce(
                NEW.completed_at,NEW.status_changed_at,clock_timestamp()
              );
            END IF;
          ELSIF NEW.status_changed_at IS DISTINCT FROM OLD.status_changed_at THEN
            RAISE EXCEPTION 'status_changed_at cannot change without a status transition'
              USING ERRCODE='23514';
          END IF;
          IF NEW.status<>'COMPLETED' AND NEW.completed_at IS NOT NULL THEN
            RAISE EXCEPTION 'completed_at requires COMPLETED status'
              USING ERRCODE='23514';
          END IF;
          IF OLD.completed_at IS NOT NULL
             AND NEW.completed_at IS DISTINCT FROM OLD.completed_at THEN
            RAISE EXCEPTION 'completed_at is immutable once set'
              USING ERRCODE='23514';
          END IF;
          NEW.row_version:=OLD.row_version+1;
          NEW.updated_at:=clock_timestamp();
          RETURN NEW;
        END
        $function$;
        REVOKE ALL ON FUNCTION public.enforce_task_assignment_write()
          FROM PUBLIC;
        CREATE TRIGGER trg_task_assignment_write
        BEFORE INSERT OR UPDATE OR DELETE ON public.task_assignments
        FOR EACH ROW EXECUTE FUNCTION public.enforce_task_assignment_write();
        """
    )


def _install_task_plan_reducer() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.task_plan_active_match_set_hash_v2(
          p_assignment_dedupe_key text
        ) RETURNS text
        LANGUAGE sql
        STABLE
        SET search_path=pg_catalog,public
        AS $function$
          SELECT public.dts_canonical_json_sha256_v1(
            coalesce(jsonb_agg(element ORDER BY
              seed_rule_rank,source_region_rank,
              source_appoint_id_type_rank,source_appoint_id_numeric,
              source_appoint_id_sort_bytes,participation_seq,
              evidence_discriminator_type_rank,
              evidence_discriminator_numeric,
              evidence_discriminator_sort_bytes,dedupe_key_sort_bytes
            ),'[]'::jsonb)
          )
          FROM (
            SELECT seed_rule_rank,source_region_rank,
              source_appoint_id_type_rank,source_appoint_id_numeric,
              source_appoint_id_sort_bytes,participation_seq,
              evidence_discriminator_type_rank,
              evidence_discriminator_numeric,
              evidence_discriminator_sort_bytes,dedupe_key_sort_bytes,
              jsonb_build_object(
                'dedupe_key',dedupe_key,
                'match_revision',match_revision,
                'seed_rule_rank',seed_rule_rank,
                'source_region',source_region,
                'source_appoint_id_type',source_appoint_id_type,
                'source_appoint_id_numeric',
                  source_appoint_id_numeric::text,
                'source_appoint_id_sort_hex',
                  encode(source_appoint_id_sort_bytes,'hex'),
                'participation_seq',participation_seq,
                'evidence_discriminator_type',evidence_discriminator_type,
                'evidence_discriminator_numeric',
                  evidence_discriminator_numeric::text,
                'evidence_discriminator_sort_hex',
                  encode(evidence_discriminator_sort_bytes,'hex'),
                'plan_evidence_hash',plan_evidence_hash,
                'teacher_execution_variant',teacher_execution_variant,
                'normalized_active',true
              ) AS element
            FROM public.personalized_trigger_matches
            WHERE assignment_dedupe_key=p_assignment_dedupe_key
              AND output_type='TEACHER_TASK' AND is_serving
              AND match_status IN ('MATCHED','MATERIALIZED')
          ) active
        $function$;

        CREATE FUNCTION public.rebuild_task_plan_v2(
          p_assignment_dedupe_key text,
          p_changed_match_keys jsonb
        ) RETURNS jsonb
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE now_at timestamptz := transaction_timestamp();
        DECLARE teacher_value text;
        DECLARE task_code_value text;
        DECLARE identity_count integer;
        DECLARE active_count integer;
        DECLARE negative_name_count integer;
        DECLARE negative_variant_count integer;
        DECLARE copy_count integer;
        DECLARE template_count integer;
        DECLARE copy_row public.config_versions%ROWTYPE;
        DECLARE template_row public.task_templates%ROWTYPE;
        DECLARE assignment_row public.task_assignments%ROWTYPE;
        DECLARE assignment_found boolean := false;
        DECLARE old_plan public.domain_aggregate_revisions%ROWTYPE;
        DECLARE old_found boolean := false;
        DECLARE blocker_elements jsonb := '[]'::jsonb;
        DECLARE blocker_hash text;
        DECLARE blocker_code_value text;
        DECLARE active_hash text;
        DECLARE materializable_value boolean;
        DECLARE generation_value bigint;
        DECLARE eligible_value timestamptz;
        DECLARE timezone_value text;
        DECLARE timezone_source_value text;
        DECLARE timezone_verified_value timestamptz;
        DECLARE teacher_revision_value bigint;
        DECLARE template_id_value text;
        DECLARE template_revision_value integer;
        DECLARE copy_id_value text;
        DECLARE copy_number_value integer;
        DECLARE plan_key jsonb;
        DECLARE key_hash text;
        DECLARE aggregate_identity text;
        DECLARE plan_without_hash jsonb;
        DECLARE plan_hash text;
        DECLARE plan_state jsonb;
        DECLARE next_revision bigint;
        DECLARE event_identity text;
        DECLARE outbox_identity text;
        DECLARE event_payload jsonb;
        DECLARE inserted_count integer;
        BEGIN
          IF p_assignment_dedupe_key IS NULL
             OR btrim(p_assignment_dedupe_key)=''
             OR jsonb_typeof(p_changed_match_keys)<>'array' THEN
            RAISE EXCEPTION 'TASK_PLAN_REDUCER_INPUT_INVALID'
              USING ERRCODE='22023';
          END IF;
          PERFORM pg_advisory_xact_lock_shared(
            hashtextextended('tit:dts-v2-cutover',0)
          );
          -- UTF-8 order is CONFIG then TEMPLATE for every task plan.
          SELECT count(DISTINCT teacher_id),min(teacher_id),
                 count(DISTINCT target_task_code),min(target_task_code)
          INTO identity_count,teacher_value,template_count,task_code_value
          FROM public.personalized_trigger_matches
          WHERE assignment_dedupe_key=p_assignment_dedupe_key;
          IF identity_count<>1 OR template_count<>1
             OR teacher_value IS NULL OR task_code_value IS NULL THEN
            RAISE EXCEPTION 'TASK_PLAN_MATCH_IDENTITY_CONFLICT'
              USING ERRCODE='23514';
          END IF;
          PERFORM pg_advisory_xact_lock_shared(hashtextextended(
            'CONFIG:teacher_personalized_copy',0
          ));
          PERFORM pg_advisory_xact_lock_shared(hashtextextended(
            'TEMPLATE:' || task_code_value,0
          ));
          PERFORM pg_advisory_xact_lock(hashtextextended(
            'TASK_ASSIGNMENT:' || p_assignment_dedupe_key,0
          ));
          SELECT * INTO assignment_row
          FROM public.task_assignments
          WHERE dedupe_key=p_assignment_dedupe_key FOR UPDATE;
          assignment_found:=FOUND;

          SELECT count(*) INTO active_count
          FROM public.personalized_trigger_matches
          WHERE assignment_dedupe_key=p_assignment_dedupe_key
            AND output_type='TEACHER_TASK' AND is_serving
            AND match_status IN ('MATCHED','MATERIALIZED');
          SELECT count(*) FILTER(
                   WHERE match_kind='NEGATIVE_LABEL_NAME_MISSING'
                 ),count(*) FILTER(
                   WHERE match_kind='NEGATIVE_LABEL_VARIANT_CONFLICT'
                 )
          INTO negative_name_count,negative_variant_count
          FROM public.personalized_trigger_matches
          WHERE assignment_dedupe_key=p_assignment_dedupe_key
            AND is_serving AND match_status='PENDING_DATA';

          IF NOT assignment_found AND (active_count>0
             OR negative_name_count+negative_variant_count>0) THEN
            SELECT count(*),min(version_id),min(version_number)
            INTO copy_count,copy_id_value,copy_number_value
            FROM public.config_versions
            WHERE config_key='teacher_personalized_copy'
              AND status='PUBLISHED';
          ELSE
            copy_count:=1;
          END IF;

          SELECT coalesce(jsonb_agg(element ORDER BY sort_key),'[]'::jsonb)
          INTO blocker_elements
          FROM (
            SELECT convert_to(match_kind || ':' || dedupe_key,'UTF8') sort_key,
              jsonb_build_object(
                'kind',CASE match_kind
                  WHEN 'NEGATIVE_LABEL_NAME_MISSING'
                    THEN 'NEGATIVE_LABEL_NAME_MISSING'
                  ELSE 'NEGATIVE_LABEL_VARIANT_CONFLICT' END,
                'dedupe_key',dedupe_key,
                'match_revision',match_revision,
                'plan_evidence_hash',plan_evidence_hash
              ) element
            FROM public.personalized_trigger_matches
            WHERE assignment_dedupe_key=p_assignment_dedupe_key
              AND is_serving AND match_status='PENDING_DATA'
              AND match_kind IN (
                'NEGATIVE_LABEL_NAME_MISSING',
                'NEGATIVE_LABEL_VARIANT_CONFLICT'
              )
            UNION ALL
            SELECT convert_to('TASK_COPY_CONFIG_MISSING','UTF8'),
              jsonb_build_object(
                'kind','TASK_COPY_CONFIG_MISSING',
                'config_key','teacher_personalized_copy'
              )
            WHERE NOT assignment_found AND copy_count=0
            UNION ALL
            SELECT convert_to('TASK_COPY_CONFIG_CONFLICT','UTF8'),
              jsonb_build_object(
                'kind','TASK_COPY_CONFIG_CONFLICT',
                'config_key','teacher_personalized_copy',
                'candidate_version_ids',(
                  SELECT coalesce(jsonb_agg(version_id ORDER BY
                    convert_to(version_id,'UTF8')),'[]'::jsonb)
                  FROM public.config_versions
                  WHERE config_key='teacher_personalized_copy'
                    AND status='PUBLISHED'
                )
              )
            WHERE NOT assignment_found AND copy_count>1
          ) blockers;
          blocker_hash:=public.dts_canonical_json_sha256_v1(blocker_elements);
          blocker_code_value:=CASE
            WHEN copy_count>1 AND NOT assignment_found
              THEN 'TASK_COPY_CONFIG_CONFLICT'
            WHEN copy_count=0 AND NOT assignment_found
              THEN 'TASK_COPY_CONFIG_MISSING'
            WHEN negative_name_count>0 AND negative_variant_count>0
              THEN 'NEGATIVE_LABEL_MULTIPLE'
            WHEN negative_variant_count>0
              THEN 'NEGATIVE_LABEL_VARIANT_CONFLICT'
            WHEN negative_name_count>0
              THEN 'NEGATIVE_LABEL_NAME_MISSING'
            WHEN active_count=0 THEN 'NO_ACTIVE_MATCH'
            ELSE 'NONE' END;
          materializable_value := active_count>0
            AND blocker_elements='[]'::jsonb;
          active_hash:=public.task_plan_active_match_set_hash_v2(
            p_assignment_dedupe_key
          );

          plan_key:=jsonb_build_object(
            'assignment_dedupe_key',p_assignment_dedupe_key
          );
          key_hash:=public.dts_canonical_json_sha256_v1(plan_key);
          aggregate_identity:='v2:TASK_PLAN:' || key_hash;
          SELECT * INTO old_plan
          FROM public.domain_aggregate_revisions
          WHERE aggregate_type='TASK_PLAN'
            AND aggregate_id=aggregate_identity FOR UPDATE;
          old_found:=FOUND;

          IF assignment_found THEN
            generation_value:=assignment_row.eligibility_generation;
            eligible_value:=assignment_row.eligible_since_at;
            timezone_value:=assignment_row.timezone_used;
            timezone_source_value:=assignment_row.timezone_source;
            timezone_verified_value:=assignment_row.timezone_verified_at;
            teacher_revision_value:=CASE WHEN old_found THEN
              (old_plan.aggregate_state->>'teacher_row_version')::bigint
            ELSE coalesce((
              SELECT v2_row_version FROM public.teacher_source_wide
              WHERE tchr_id=teacher_value
            ),1) END;
            template_id_value:=assignment_row.template_version_id;
            template_revision_value:=
              assignment_row.materialization_template_revision;
            copy_id_value:=assignment_row.teacher_copy_version_id;
            copy_number_value:=CASE WHEN copy_id_value IS NULL THEN NULL ELSE (
              SELECT version_number FROM public.config_versions
              WHERE version_id=copy_id_value
                AND config_key=assignment_row.teacher_copy_config_key
            ) END;
          ELSIF materializable_value THEN
            SELECT count(*) INTO template_count
            FROM public.task_templates
            WHERE template_id=task_code_value AND status='PUBLISHED';
            IF template_count<>1 OR copy_count<>1 THEN
              RAISE EXCEPTION 'TASK_PLAN_CATALOG_CONFLICT'
                USING ERRCODE='23514';
            END IF;
            SELECT * INTO template_row FROM public.task_templates
            WHERE template_id=task_code_value AND status='PUBLISHED';
            SELECT * INTO copy_row FROM public.config_versions
            WHERE config_key='teacher_personalized_copy'
              AND status='PUBLISHED';
            IF old_found AND (old_plan.aggregate_state->>'materializable')::boolean
               AND old_plan.aggregate_state->>'template_version_id'
                    =template_row.row_id
               AND (old_plan.aggregate_state->>'template_revision')::integer
                    =template_row.revision
               AND old_plan.aggregate_state->>'teacher_copy_version_id'
                    =copy_row.version_id
               AND (old_plan.aggregate_state->>'teacher_copy_version_number')::integer
                    =copy_row.version_number THEN
              generation_value:=(old_plan.aggregate_state->>'eligibility_generation')::bigint;
              eligible_value:=(old_plan.aggregate_state->>'eligible_since_at')::timestamptz;
              timezone_value:=old_plan.aggregate_state->>'timezone_used';
              timezone_source_value:=old_plan.aggregate_state->>'timezone_source';
              timezone_verified_value:=(old_plan.aggregate_state->>'timezone_verified_at')::timestamptz;
              teacher_revision_value:=(old_plan.aggregate_state->>'teacher_row_version')::bigint;
            ELSE
              generation_value:=coalesce(
                (old_plan.aggregate_state->>'eligibility_generation')::bigint,0
              )+1;
              eligible_value:=now_at;
              SELECT CASE WHEN EXISTS(
                SELECT 1 FROM pg_timezone_names
                WHERE name=teacher.timezone
              ) THEN teacher.timezone ELSE 'UTC' END,
              CASE WHEN EXISTS(
                SELECT 1 FROM pg_timezone_names
                WHERE name=teacher.timezone
              ) THEN 'TEACHER_PROFILE' ELSE 'SYSTEM_DEFAULT' END
              INTO timezone_value,timezone_source_value
              FROM public.teachers teacher
              WHERE teacher.teacher_id=teacher_value FOR SHARE;
              IF NOT FOUND THEN
                RAISE EXCEPTION 'TASK_PLAN_TEACHER_MISSING'
                  USING ERRCODE='23503';
              END IF;
              timezone_verified_value:=now_at;
              teacher_revision_value:=coalesce((
                SELECT v2_row_version FROM public.teacher_source_wide
                WHERE tchr_id=teacher_value
              ),1);
            END IF;
            template_id_value:=template_row.row_id;
            template_revision_value:=template_row.revision;
            copy_id_value:=copy_row.version_id;
            copy_number_value:=copy_row.version_number;
          ELSIF old_found THEN
            generation_value:=(old_plan.aggregate_state->>'eligibility_generation')::bigint;
            eligible_value:=(old_plan.aggregate_state->>'eligible_since_at')::timestamptz;
            timezone_value:=old_plan.aggregate_state->>'timezone_used';
            timezone_source_value:=old_plan.aggregate_state->>'timezone_source';
            timezone_verified_value:=(old_plan.aggregate_state->>'timezone_verified_at')::timestamptz;
            teacher_revision_value:=(old_plan.aggregate_state->>'teacher_row_version')::bigint;
            template_id_value:=old_plan.aggregate_state->>'template_version_id';
            template_revision_value:=(old_plan.aggregate_state->>'template_revision')::integer;
            copy_id_value:=old_plan.aggregate_state->>'teacher_copy_version_id';
            copy_number_value:=(old_plan.aggregate_state->>'teacher_copy_version_number')::integer;
          ELSE
            generation_value:=NULL; eligible_value:=NULL;
            timezone_value:=NULL; timezone_source_value:=NULL;
            timezone_verified_value:=NULL; teacher_revision_value:=NULL;
            template_id_value:=NULL; template_revision_value:=NULL;
            copy_id_value:=NULL; copy_number_value:=NULL;
          END IF;

          plan_without_hash:=jsonb_build_object(
            'protocol_version','task-plan-state-v1',
            'assignment_dedupe_key',p_assignment_dedupe_key,
            'teacher_id',teacher_value,
            'target_task_code',task_code_value,
            'materializable',materializable_value,
            'eligibility_generation',generation_value,
            'eligible_since_at',CASE WHEN eligible_value IS NULL THEN NULL ELSE
              to_char(eligible_value AT TIME ZONE 'UTC',
                'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') END,
            'timezone_used',timezone_value,
            'timezone_source',timezone_source_value,
            'timezone_verified_at',CASE
              WHEN timezone_verified_value IS NULL THEN NULL ELSE
              to_char(timezone_verified_value AT TIME ZONE 'UTC',
                'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') END,
            'teacher_row_version',teacher_revision_value,
            'template_version_id',template_id_value,
            'template_revision',template_revision_value,
            'teacher_copy_version_id',copy_id_value,
            'teacher_copy_config_key',CASE WHEN copy_id_value IS NULL
              THEN NULL ELSE 'teacher_personalized_copy' END,
            'teacher_copy_version_number',copy_number_value,
            'active_match_set_hash',active_hash,
            'blocker_code',blocker_code_value,
            'blocker_set_hash',blocker_hash
          );
          plan_hash:=public.dts_canonical_json_sha256_v1(plan_without_hash);
          plan_state:=plan_without_hash ||
            jsonb_build_object('plan_state_hash',plan_hash);
          IF old_found AND old_plan.aggregate_state_sha256=
               public.dts_canonical_json_sha256_v1(plan_state) THEN
            RETURN jsonb_build_object(
              'status','UNCHANGED','aggregate_id',aggregate_identity,
              'aggregate_revision',old_plan.revision,
              'plan_state_hash',plan_hash
            );
          END IF;
          next_revision:=CASE WHEN old_found THEN old_plan.revision+1 ELSE 1 END;
          IF old_found THEN
            UPDATE public.domain_aggregate_revisions
            SET revision=next_revision,aggregate_state=plan_state,
                aggregate_state_sha256=
                  public.dts_canonical_json_sha256_v1(plan_state),
                last_source_row_revision=NULL,last_source_position=NULL,
                updated_at=now_at
            WHERE aggregate_type='TASK_PLAN'
              AND aggregate_id=aggregate_identity;
          ELSE
            INSERT INTO public.domain_aggregate_revisions(
              aggregate_type,aggregate_id,canonical_key,
              canonical_key_sha256,revision,last_source_row_revision,
              last_source_position,aggregate_state,aggregate_state_sha256,
              updated_at
            ) VALUES (
              'TASK_PLAN',aggregate_identity,plan_key,key_hash,next_revision,
              NULL,NULL,plan_state,
              public.dts_canonical_json_sha256_v1(plan_state),now_at
            );
          END IF;
          event_identity:='task.materialization.requested.v2:' ||
            aggregate_identity || ':' || next_revision::text;
          outbox_identity:='outbox:v2:' || encode(
            sha256(convert_to(event_identity,'UTF8')),'hex'
          );
          event_payload:=jsonb_build_object(
            'protocol_version','task-materialization-request-v1',
            'aggregate_key',plan_key,
            'aggregate_revision',next_revision,
            'assignment_dedupe_key',p_assignment_dedupe_key,
            'eligibility_generation',generation_value,
            'eligible_since_at',plan_state->'eligible_since_at',
            'timezone_used',timezone_value,
            'timezone_source',timezone_source_value,
            'timezone_verified_at',plan_state->'timezone_verified_at',
            'template_version_id',template_id_value,
            'template_revision',template_revision_value,
            'teacher_copy_version_id',copy_id_value,
            'teacher_copy_config_key',plan_state->'teacher_copy_config_key',
            'teacher_copy_version_number',copy_number_value,
            'plan_state_hash',plan_hash,
            'changed_match_keys',p_changed_match_keys
          );
          INSERT INTO public.outbox_events(
            outbox_id,event_id,aggregate_type,aggregate_id,event_type,
            payload,payload_sha256,status,available_at,attempt_count,
            recovery_count,recovered_at,row_version,last_error,
            settled_by_run_id,created_at,published_at
          ) VALUES (
            outbox_identity,event_identity,'TASK_PLAN',aggregate_identity,
            'task.materialization.requested.v2',event_payload,
            public.dts_canonical_json_sha256_v1(event_payload),'PENDING',
            now_at,0,0,NULL,1,NULL,NULL,now_at,NULL
          ) ON CONFLICT DO NOTHING;
          GET DIAGNOSTICS inserted_count=ROW_COUNT;
          IF inserted_count=0 AND NOT EXISTS(
            SELECT 1 FROM public.outbox_events
            WHERE event_id=event_identity AND outbox_id=outbox_identity
              AND aggregate_type='TASK_PLAN'
              AND aggregate_id=aggregate_identity
              AND event_type='task.materialization.requested.v2'
              AND payload=event_payload
          ) THEN
            RAISE EXCEPTION 'TASK_PLAN_OUTBOX_ID_CONFLICT'
              USING ERRCODE='23514';
          END IF;
          RETURN jsonb_build_object(
            'status','CHANGED','aggregate_id',aggregate_identity,
            'aggregate_revision',next_revision,
            'plan_state_hash',plan_hash
          );
        END
        $function$;
        REVOKE ALL ON FUNCTION
          public.task_plan_active_match_set_hash_v2(text),
          public.rebuild_task_plan_v2(text,jsonb)
        FROM PUBLIC;
        """
    )


def _install_negative_group_reducer() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.reconcile_negative_label_group_v2(
          p_teacher_id text,p_label_id text,p_projection_generation bigint
        ) RETURNS jsonb
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE current_count integer;
        DECLARE missing_count integer;
        DECLARE variant_count integer;
        DECLARE label_type text;
        DECLARE assignment_key text;
        DECLARE blocker_kind text;
        DECLARE chosen_blocker_kind text;
        DECLARE blocker_key text;
        DECLARE courses_hash text;
        DECLARE blocker_evidence jsonb;
        DECLARE blocker_id text;
        DECLARE desired_status text;
        DECLARE changed_count integer := 0;
        BEGIN
          assignment_key:='personalized:P-FB-NEGATIVE:' ||
            p_teacher_id || ':' || p_label_id;
          PERFORM 1 FROM public.personalized_trigger_matches
          WHERE teacher_id=p_teacher_id
            AND evidence_discriminator=p_label_id
            AND match_kind='NEGATIVE_LABEL_COURSE'
          ORDER BY dedupe_key_sort_bytes FOR UPDATE;
          SELECT count(DISTINCT (source_region,source_appoint_id)),
                 count(*) FILTER (
                   WHERE nullif(plan_evidence->>'label_name','') IS NULL
                 ),count(DISTINCT CASE
                   WHEN nullif(plan_evidence->>'label_name','') IN
                     ('灯光过暗/亮','环境乱/灯光差')
                     THEN 'TEACHING_ENVIRONMENT_PHOTO'
                   ELSE 'GENERAL' END),
                 min(evidence_discriminator_type)
          INTO current_count,missing_count,variant_count,label_type
          FROM public.personalized_trigger_matches
          WHERE teacher_id=p_teacher_id
            AND evidence_discriminator=p_label_id
            AND match_kind='NEGATIVE_LABEL_COURSE'
            AND source_candidate_active AND is_serving;
          SELECT public.dts_canonical_json_sha256_v1(
            coalesce(jsonb_agg(course_key ORDER BY
              convert_to(course_key,'UTF8')),'[]'::jsonb)
          ) INTO courses_hash
          FROM (
            SELECT DISTINCT source_region || ':' || source_appoint_id
              AS course_key
            FROM public.personalized_trigger_matches
            WHERE teacher_id=p_teacher_id
              AND evidence_discriminator=p_label_id
              AND match_kind='NEGATIVE_LABEL_COURSE'
              AND source_candidate_active AND is_serving
          ) courses;
          chosen_blocker_kind:=CASE
            WHEN current_count<2 THEN NULL
            WHEN missing_count>0 THEN 'NEGATIVE_LABEL_NAME_MISSING'
            WHEN variant_count>1 THEN 'NEGATIVE_LABEL_VARIANT_CONFLICT'
            ELSE NULL END;
          UPDATE public.personalized_trigger_matches match
          SET teacher_execution_variant=CASE
                WHEN nullif(match.plan_evidence->>'label_name','') IS NULL
                  THEN 'NONE'
                WHEN match.plan_evidence->>'label_name' IN
                  ('灯光过暗/亮','环境乱/灯光差')
                  THEN 'TEACHING_ENVIRONMENT_PHOTO'
                ELSE 'GENERAL' END,
              plan_evidence=match.plan_evidence || jsonb_build_object(
                'copy_lookup_key',match.plan_evidence->>'label_name',
                'variant',CASE
                  WHEN nullif(match.plan_evidence->>'label_name','') IS NULL
                    THEN 'NONE'
                  WHEN match.plan_evidence->>'label_name' IN
                    ('灯光过暗/亮','环境乱/灯光差')
                    THEN 'TEACHING_ENVIRONMENT_PHOTO'
                  ELSE 'GENERAL' END
              ),
              evidence_snapshot=match.plan_evidence || jsonb_build_object(
                'copy_lookup_key',match.plan_evidence->>'label_name',
                'variant',CASE
                  WHEN nullif(match.plan_evidence->>'label_name','') IS NULL
                    THEN 'NONE'
                  WHEN match.plan_evidence->>'label_name' IN
                    ('灯光过暗/亮','环境乱/灯光差')
                    THEN 'TEACHING_ENVIRONMENT_PHOTO'
                  ELSE 'GENERAL' END
              ),
              match_status=CASE
                WHEN current_count>=2 AND chosen_blocker_kind IS NULL
                  THEN CASE WHEN match.task_assignment_id IS NULL
                    THEN 'MATCHED' ELSE 'MATERIALIZED' END
                ELSE 'SUPPRESSED' END,
              output_type='TEACHER_TASK',output_key=assignment_key,
              match_revision=match.match_revision+CASE WHEN
                match.teacher_execution_variant IS DISTINCT FROM CASE
                  WHEN nullif(match.plan_evidence->>'label_name','') IS NULL
                    THEN 'NONE'
                  WHEN match.plan_evidence->>'label_name' IN
                    ('灯光过暗/亮','环境乱/灯光差')
                    THEN 'TEACHING_ENVIRONMENT_PHOTO'
                  ELSE 'GENERAL' END
                OR match.match_status IS DISTINCT FROM CASE
                  WHEN current_count>=2 AND chosen_blocker_kind IS NULL
                    THEN CASE WHEN match.task_assignment_id IS NULL
                      THEN 'MATCHED' ELSE 'MATERIALIZED' END
                  ELSE 'SUPPRESSED' END
                OR match.output_type IS DISTINCT FROM 'TEACHER_TASK'
                OR match.output_key IS DISTINCT FROM assignment_key
                OR match.plan_evidence IS DISTINCT FROM
                  match.plan_evidence || jsonb_build_object(
                    'copy_lookup_key',match.plan_evidence->>'label_name',
                    'variant',CASE
                      WHEN nullif(match.plan_evidence->>'label_name','') IS NULL
                        THEN 'NONE'
                      WHEN match.plan_evidence->>'label_name' IN
                        ('灯光过暗/亮','环境乱/灯光差')
                        THEN 'TEACHING_ENVIRONMENT_PHOTO'
                      ELSE 'GENERAL' END
                  )
                THEN 1 ELSE 0 END,
              last_transition_at=CASE WHEN
                match.teacher_execution_variant IS DISTINCT FROM CASE
                  WHEN nullif(match.plan_evidence->>'label_name','') IS NULL
                    THEN 'NONE'
                  WHEN match.plan_evidence->>'label_name' IN
                    ('灯光过暗/亮','环境乱/灯光差')
                    THEN 'TEACHING_ENVIRONMENT_PHOTO'
                  ELSE 'GENERAL' END
                OR match.match_status IS DISTINCT FROM CASE
                  WHEN current_count>=2 AND chosen_blocker_kind IS NULL
                    THEN CASE WHEN match.task_assignment_id IS NULL
                      THEN 'MATCHED' ELSE 'MATERIALIZED' END
                  ELSE 'SUPPRESSED' END
                OR match.output_type IS DISTINCT FROM 'TEACHER_TASK'
                OR match.output_key IS DISTINCT FROM assignment_key
                OR match.plan_evidence IS DISTINCT FROM
                  match.plan_evidence || jsonb_build_object(
                    'copy_lookup_key',match.plan_evidence->>'label_name',
                    'variant',CASE
                      WHEN nullif(match.plan_evidence->>'label_name','') IS NULL
                        THEN 'NONE'
                      WHEN match.plan_evidence->>'label_name' IN
                        ('灯光过暗/亮','环境乱/灯光差')
                        THEN 'TEACHING_ENVIRONMENT_PHOTO'
                      ELSE 'GENERAL' END
                  )
                THEN transaction_timestamp()
                ELSE match.last_transition_at END,
              updated_at=transaction_timestamp()
          WHERE match.teacher_id=p_teacher_id
            AND match.evidence_discriminator=p_label_id
            AND match.match_kind='NEGATIVE_LABEL_COURSE'
            AND match.is_serving;
          GET DIAGNOSTICS changed_count=ROW_COUNT;

          FOREACH blocker_kind IN ARRAY ARRAY[
            'NEGATIVE_LABEL_NAME_MISSING',
            'NEGATIVE_LABEL_VARIANT_CONFLICT'
          ] LOOP
            blocker_key:=CASE blocker_kind
              WHEN 'NEGATIVE_LABEL_NAME_MISSING'
                THEN 'negative-label-name-missing:'
              ELSE 'negative-label-variant-conflict:' END ||
              p_teacher_id || ':' || p_label_id;
            desired_status:=CASE WHEN current_count>=2 AND
              ((blocker_kind='NEGATIVE_LABEL_NAME_MISSING'
                  AND missing_count>0) OR
               (blocker_kind='NEGATIVE_LABEL_VARIANT_CONFLICT'
                  AND missing_count=0 AND variant_count>1))
              THEN 'PENDING_DATA' ELSE 'SUPPRESSED' END;
            blocker_evidence:=jsonb_build_object(
              'protocol_version','negative-label-pending-v1',
              'teacher_id',p_teacher_id,'label_id',p_label_id,
              'contributing_course_key_hash',courses_hash,
              'pending_reason',blocker_kind
            );
            blocker_id:='TRM-V2-' || substring(encode(sha256(
              convert_to(blocker_key,'UTF8')),'hex') for 40);
            INSERT INTO public.personalized_trigger_matches(
              trigger_match_id,trigger_code,rule_version,teacher_id,
              lesson_source_region,lesson_id,complaint_rule_id,dedupe_key,
              output_type,output_title,output_id,match_status,
              evidence_snapshot,matched_at,materialized_at,updated_at,
              source_region,source_appoint_id,participation_seq,match_kind,
              match_revision,last_transition_at,target_task_code,
              assignment_dedupe_key,seed_rule_rank,threshold_required,
              source_candidate_active,plan_evidence,plan_evidence_hash,
              teacher_execution_variant,output_key,source_ref,
              assignment_dedupe_key_sort_bytes,source_region_rank,
              source_appoint_id_type,source_appoint_id_type_rank,
              source_appoint_id_numeric,source_appoint_id_sort_bytes,
              evidence_discriminator,evidence_discriminator_type,
              evidence_discriminator_type_rank,
              evidence_discriminator_numeric,
              evidence_discriminator_sort_bytes,dedupe_key_sort_bytes,
              materialization_origin,created_projection_generation,
              serving_projection_generation,is_serving,
              task_assignment_id,ops_case_id,notification_id,
              source_aggregate_revision,projection_generation,
              triggering_event_id
            ) VALUES (
              blocker_id,'TR-FB-NEGATIVE-REPEAT','dts-direct-v2',
              p_teacher_id,NULL,NULL,NULL,blocker_key,'PENDING_DATA',
              'Negative feedback evidence is pending',NULL,desired_status,
              blocker_evidence,transaction_timestamp(),NULL,
              transaction_timestamp(),'dom',NULL,NULL,blocker_kind,1,
              transaction_timestamp(),'P-FB-NEGATIVE',assignment_key,10,2,
              desired_status='PENDING_DATA',blocker_evidence,
              public.dts_canonical_json_sha256_v1(blocker_evidence),'NONE',
              NULL,'trigger-match:' || blocker_key,NULL,0,'NONE',2,
              NULL,NULL,p_label_id,coalesce(label_type,'TEXT'),
              CASE coalesce(label_type,'TEXT') WHEN 'NUMERIC' THEN 0 ELSE 1 END,
              CASE WHEN label_type='NUMERIC' THEN p_label_id::numeric END,
              CASE WHEN coalesce(label_type,'TEXT')='TEXT'
                THEN convert_to(p_label_id,'UTF8') END,
              convert_to(blocker_key,'UTF8'),'V2_LIVE',
              p_projection_generation,p_projection_generation,true,
              NULL,NULL,NULL,NULL,p_projection_generation,NULL
            ) ON CONFLICT (dedupe_key) DO UPDATE SET
              match_status=excluded.match_status,
              source_candidate_active=excluded.source_candidate_active,
              plan_evidence=excluded.plan_evidence,
              evidence_snapshot=excluded.evidence_snapshot,
              projection_generation=excluded.projection_generation,
              serving_projection_generation=
                excluded.serving_projection_generation,
              match_revision=personalized_trigger_matches.match_revision+
                CASE WHEN personalized_trigger_matches.match_status
                           IS DISTINCT FROM excluded.match_status
                       OR personalized_trigger_matches.plan_evidence
                           IS DISTINCT FROM excluded.plan_evidence
                     THEN 1 ELSE 0 END,
              last_transition_at=CASE WHEN
                personalized_trigger_matches.match_status
                    IS DISTINCT FROM excluded.match_status
                OR personalized_trigger_matches.plan_evidence
                    IS DISTINCT FROM excluded.plan_evidence
                THEN transaction_timestamp()
                ELSE personalized_trigger_matches.last_transition_at END,
              updated_at=transaction_timestamp();
          END LOOP;
          RETURN jsonb_build_object(
            'current_course_count',current_count,
            'blocker_kind',chosen_blocker_kind,
            'rows_reconciled',changed_count
          );
        END
        $function$;
        REVOKE ALL ON FUNCTION
          public.reconcile_negative_label_group_v2(text,text,bigint)
        FROM PUBLIC;
        """
    )


def _install_course_reconciler() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.reconcile_course_trigger_matches_v2(
          p_source_region text,p_source_appoint_id text,
          p_expected_course_aggregate_revision bigint,
          p_projection_generation bigint,p_triggering_event_id text,
          p_match_plan jsonb,p_expected_plan_sha256 text
        ) RETURNS jsonb
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE guarded_generation bigint;
        DECLARE plan_hash text;
        DECLARE appoint_type text;
        DECLARE incoming_keys text[];
        DECLARE sorted_incoming_keys text[];
        DECLARE declared_affected_keys text[];
        DECLARE derived_affected_keys text[];
        DECLARE affected_keys text[];
        DECLARE item jsonb;
        DECLARE evidence jsonb;
        DECLARE raw_kind text;
        DECLARE kind_value text;
        DECLARE status_value text;
        DECLARE output_type_value text;
        DECLARE output_key_value text;
        DECLARE task_code_value text;
        DECLARE assignment_key_value text;
        DECLARE teacher_value text;
        DECLARE discriminator_value text;
        DECLARE discriminator_type_value text;
        DECLARE variant_value text;
        DECLARE dedupe_value text;
        DECLARE match_id_value text;
        DECLARE trigger_code_value text;
        DECLARE complaint_rule_value text;
        DECLARE inserted_count integer := 0;
        DECLARE updated_count integer := 0;
        DECLARE suppressed_count integer := 0;
        DECLARE existed_before boolean;
        DECLARE group_row record;
        DECLARE assignment_item text;
        BEGIN
          guarded_generation:=public.dts_v2_runtime_primary_guard_v1('COURSE');
          IF p_projection_generation IS NULL
             OR guarded_generation IS NULL
             OR guarded_generation<>p_projection_generation THEN
            RAISE EXCEPTION 'DTS_V2_TRIGGER_MATCH_GENERATION_CHANGED'
              USING ERRCODE='40001';
          END IF;
          IF p_source_region IS NULL
             OR p_source_region NOT IN ('dom','ovs')
             OR p_source_appoint_id IS NULL
             OR btrim(p_source_appoint_id)=''
             OR p_expected_course_aggregate_revision IS NULL
             OR p_expected_course_aggregate_revision<1
             OR p_projection_generation<1
             OR p_triggering_event_id IS NULL
             OR btrim(p_triggering_event_id)=''
             OR p_match_plan IS NULL
             OR jsonb_typeof(p_match_plan) IS DISTINCT FROM 'object'
             OR p_expected_plan_sha256 IS NULL
             OR p_expected_plan_sha256 !~ '^[0-9a-f]{64}$' THEN
            RAISE EXCEPTION 'DTS_V2_TRIGGER_MATCH_REQUEST_INVALID'
              USING ERRCODE='22023';
          END IF;
          plan_hash:=public.dts_canonical_json_sha256_v1(p_match_plan);
          IF plan_hash IS DISTINCT FROM p_expected_plan_sha256
             OR p_match_plan->>'protocol_version'
                  IS DISTINCT FROM 'course-trigger-plan-v1'
             OR p_match_plan->>'source_region'
                  IS DISTINCT FROM p_source_region
             OR p_match_plan->>'source_appoint_id'
                  IS DISTINCT FROM p_source_appoint_id
             OR jsonb_typeof(p_match_plan->'matches')
                  IS DISTINCT FROM 'array'
             OR jsonb_typeof(
                  p_match_plan->'affected_assignment_dedupe_keys'
                ) IS DISTINCT FROM 'array'
             OR p_match_plan - ARRAY[
                  'protocol_version','source_region','source_appoint_id',
                  'affected_assignment_dedupe_keys','matches'
                ]::text[] <> '{}'::jsonb THEN
            RAISE EXCEPTION 'DTS_V2_TRIGGER_MATCH_PLAN_HASH_MISMATCH'
              USING ERRCODE='23514';
          END IF;
          PERFORM 1 FROM public.source_courses
          WHERE source_region=p_source_region
            AND source_appoint_id=p_source_appoint_id FOR UPDATE;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'DTS_V2_TRIGGER_MATCH_COURSE_MISSING'
              USING ERRCODE='23503';
          END IF;
          IF NOT EXISTS(
            SELECT 1 FROM public.domain_aggregate_revisions
            WHERE aggregate_type='COURSE'
              AND canonical_key=jsonb_build_object(
                'source_region',p_source_region,
                'source_appoint_id',p_source_appoint_id
              )
              AND revision=p_expected_course_aggregate_revision
          ) THEN
            RAISE EXCEPTION 'DTS_V2_TRIGGER_MATCH_COURSE_REVISION_CHANGED'
              USING ERRCODE='40001';
          END IF;
          SELECT source_key_type INTO appoint_type
          FROM public.dts_source_rows
          WHERE source_region=p_source_region
            AND source_table=p_source_region || '_appoint'
            AND source_key=p_source_appoint_id FOR SHARE;
          IF appoint_type IS NULL
             OR appoint_type NOT IN ('NUMERIC','TEXT') THEN
            RAISE EXCEPTION 'DTS_V2_TRIGGER_MATCH_APPOINT_TYPE_MISSING'
              USING ERRCODE='23503';
          END IF;
          SELECT coalesce(array_agg(value->>'dedupe_key'),ARRAY[]::text[])
          INTO incoming_keys
          FROM jsonb_array_elements(p_match_plan->'matches');
          SELECT coalesce(array_agg(key ORDER BY convert_to(key,'UTF8')),
                   ARRAY[]::text[])
          INTO sorted_incoming_keys
          FROM (
            SELECT value->>'dedupe_key' AS key
            FROM jsonb_array_elements(p_match_plan->'matches') value
          ) keys;
          SELECT coalesce(array_agg(key ORDER BY convert_to(key,'UTF8')),
                   ARRAY[]::text[])
          INTO declared_affected_keys
          FROM (
            SELECT value #>> '{}' AS key
            FROM jsonb_array_elements(
              p_match_plan->'affected_assignment_dedupe_keys'
            ) value
          ) keys;
          SELECT coalesce(array_agg(key ORDER BY convert_to(key,'UTF8')),
                   ARRAY[]::text[])
          INTO derived_affected_keys
          FROM (
            SELECT DISTINCT value->>'assignment_dedupe_key' AS key
            FROM jsonb_array_elements(p_match_plan->'matches') value
            WHERE nullif(value->>'assignment_dedupe_key','') IS NOT NULL
          ) keys;
          IF EXISTS(
            SELECT 1 FROM jsonb_array_elements(p_match_plan->'matches') value
            WHERE jsonb_typeof(value) IS DISTINCT FROM 'object'
              OR nullif(value->>'dedupe_key','') IS NULL
              OR value->>'source_region' IS DISTINCT FROM p_source_region
              OR value->>'source_appoint_id'
                   IS DISTINCT FROM p_source_appoint_id
              OR nullif(value->>'teacher_id','') IS NULL
              OR value->>'teacher_id_type' IS NULL
              OR value->>'teacher_id_type' NOT IN ('NUMERIC','TEXT')
              OR jsonb_typeof(value->'participation_seq')
                   IS DISTINCT FROM 'number'
              OR (value->>'participation_seq') !~ '^[1-9][0-9]*$'
              OR jsonb_typeof(value->'seed_rule_rank')
                   IS DISTINCT FROM 'number'
              OR (value->>'seed_rule_rank') !~ '^[1-9][0-9]*$'
              OR jsonb_typeof(value->'threshold_required')
                   IS DISTINCT FROM 'number'
              OR (value->>'threshold_required') !~ '^[1-9][0-9]*$'
              OR jsonb_typeof(value->'plan_evidence')
                   IS DISTINCT FROM 'object'
              OR value->'plan_evidence'->>'source_region'
                   IS DISTINCT FROM p_source_region
              OR value->'plan_evidence'->>'source_appoint_id'
                   IS DISTINCT FROM p_source_appoint_id
              OR value->'plan_evidence'->>'teacher_id'
                   IS DISTINCT FROM value->>'teacher_id'
              OR value->'plan_evidence'->>'teacher_id_type'
                   IS DISTINCT FROM value->>'teacher_id_type'
              OR value->'plan_evidence'->>'participation_seq'
                   IS DISTINCT FROM value->>'participation_seq'
              OR value->>'evidence_discriminator_type' IS NULL
              OR value->>'evidence_discriminator_type'
                    NOT IN ('NUMERIC','TEXT')
              OR nullif(value->>'evidence_discriminator','') IS NULL
              OR value->>'match_status' IS NULL
              OR value->>'match_status' NOT IN ('MATCHED','PENDING_DATA')
              OR value->>'output_type' IS NULL
              OR value->>'output_type' NOT IN
                   ('TEACHER_TASK','OPS_CASE','NOTIFICATION','PENDING_DATA')
              OR NOT (value ? 'output_key')
              OR (value->>'match_kind' NOT IN (
                    'NEGATIVE_LABEL_COURSE','NEGATIVE_LABEL_NAME_MISSING'
                  ) AND nullif(value->>'output_key','') IS NULL)
              OR jsonb_typeof(value->'teacher_execution_variant')
                   IS DISTINCT FROM 'null'
              OR value - ARRAY[
                   'dedupe_key','match_kind','match_status','output_type',
                   'output_key','teacher_id','teacher_id_type',
                   'source_region','source_appoint_id','participation_seq',
                   'target_task_code','assignment_dedupe_key',
                   'evidence_discriminator','evidence_discriminator_type',
                   'seed_rule_rank','threshold_required',
                   'teacher_execution_variant','plan_evidence',
                   'plan_evidence_hash'
                 ]::text[] <> '{}'::jsonb
              OR public.dts_canonical_json_sha256_v1(
                   value->'plan_evidence'
                 ) IS DISTINCT FROM value->>'plan_evidence_hash'
              OR NOT EXISTS(
                   SELECT 1
                   FROM public.source_course_participations participation
                   WHERE participation.source_region=p_source_region
                     AND participation.source_appoint_id=
                           p_source_appoint_id
                     AND participation.participation_seq=
                           (value->>'participation_seq')::integer
                     AND participation.teacher_id=value->>'teacher_id'
                     AND participation.teacher_id_type=
                           value->>'teacher_id_type'
                 )
          ) OR EXISTS(
            SELECT 1 FROM jsonb_array_elements(
              p_match_plan->'affected_assignment_dedupe_keys'
            ) value
            WHERE jsonb_typeof(value) IS DISTINCT FROM 'string'
              OR nullif(value #>> '{}','') IS NULL
          ) OR cardinality(incoming_keys)<>(
            SELECT count(DISTINCT key) FROM unnest(incoming_keys) key
          ) OR incoming_keys IS DISTINCT FROM sorted_incoming_keys
             OR declared_affected_keys IS DISTINCT FROM derived_affected_keys
             OR cardinality(declared_affected_keys)<>(
               SELECT count(DISTINCT key)
               FROM unnest(declared_affected_keys) key
             ) THEN
            RAISE EXCEPTION 'DTS_V2_TRIGGER_MATCH_PLAN_INVALID'
              USING ERRCODE='22023';
          END IF;
          SELECT coalesce(array_agg(DISTINCT assignment_dedupe_key)
                   FILTER (WHERE assignment_dedupe_key IS NOT NULL),
                   ARRAY[]::text[])
          INTO affected_keys
          FROM public.personalized_trigger_matches
          WHERE source_region=p_source_region
            AND source_appoint_id=p_source_appoint_id;

          UPDATE public.personalized_trigger_matches match
          SET source_candidate_active=false,match_status='SUPPRESSED',
              match_revision=match_revision+1,
              last_transition_at=transaction_timestamp(),
              source_aggregate_revision=
                p_expected_course_aggregate_revision,
              projection_generation=p_projection_generation,
              triggering_event_id=p_triggering_event_id,
              updated_at=transaction_timestamp()
          WHERE source_region=p_source_region
            AND source_appoint_id=p_source_appoint_id
            AND NOT (dedupe_key=ANY(incoming_keys))
            AND (source_candidate_active OR match_status<>'SUPPRESSED');
          GET DIAGNOSTICS suppressed_count=ROW_COUNT;

          FOR item IN SELECT value
            FROM jsonb_array_elements(p_match_plan->'matches') LOOP
            dedupe_value:=item->>'dedupe_key';
            raw_kind:=item->>'match_kind';
            kind_value:=CASE WHEN raw_kind IN (
              'NEGATIVE_LABEL_COURSE','NEGATIVE_LABEL_NAME_MISSING'
            ) THEN 'NEGATIVE_LABEL_COURSE' ELSE raw_kind END;
            teacher_value:=item->>'teacher_id';
            task_code_value:=nullif(item->>'target_task_code','');
            assignment_key_value:=
              nullif(item->>'assignment_dedupe_key','');
            discriminator_value:=item->>'evidence_discriminator';
            discriminator_type_value:=
              item->>'evidence_discriminator_type';
            output_key_value:=nullif(item->>'output_key','');
            evidence:=item->'plan_evidence';
            trigger_code_value:=coalesce(
              nullif(evidence->>'rule_code',''),
              CASE kind_value WHEN 'NEGATIVE_LABEL_COURSE'
                THEN 'TR-FB-NEGATIVE-REPEAT' ELSE kind_value END
            );
            complaint_rule_value:=nullif(
              evidence->>'complaint_rule_id',''
            );
            SELECT EXISTS(
              SELECT 1 FROM public.personalized_trigger_matches
              WHERE dedupe_key=dedupe_value
            ) INTO existed_before;
            IF EXISTS(
              SELECT 1 FROM public.personalized_trigger_matches
              WHERE dedupe_key=dedupe_value
                AND materialization_origin<>'V2_LIVE'
            ) THEN
              RAISE EXCEPTION 'DTS_V2_TRIGGER_MATCH_LEGACY_IDENTITY_CONFLICT'
                USING ERRCODE='23514';
            END IF;
            IF kind_value='NEGATIVE_LABEL_COURSE' THEN
              status_value:='SUPPRESSED';
              output_type_value:='TEACHER_TASK';
              variant_value:=CASE
                WHEN nullif(evidence->>'label_name','') IS NULL THEN 'NONE'
                WHEN evidence->>'label_name' IN
                  ('灯光过暗/亮','环境乱/灯光差')
                  THEN 'TEACHING_ENVIRONMENT_PHOTO'
                ELSE 'GENERAL' END;
              evidence:=evidence || jsonb_build_object(
                'copy_lookup_key',evidence->>'label_name',
                'variant',variant_value
              );
              output_key_value:=assignment_key_value;
            ELSE
              status_value:=item->>'match_status';
              output_type_value:=item->>'output_type';
              variant_value:=CASE WHEN output_type_value='TEACHER_TASK'
                THEN 'GENERAL' ELSE 'NONE' END;
            END IF;
            IF kind_value NOT IN (
              'ABSENCE_P_REL_MEMO','ABSENCE_P_REL_ATTENDANCE',
              'COMPLAINT_ATTENDANCE','NEGATIVE_LABEL_COURSE',
              'GENERAL_COMPLAINT','COMPLAINT_NETWORK_NOTIFICATION',
              'SEVERE_COMPLAINT','CAMERA_OFF_NOTIFICATION'
            ) OR status_value NOT IN ('MATCHED','SUPPRESSED')
               OR output_type_value NOT IN
                 ('TEACHER_TASK','OPS_CASE','NOTIFICATION')
               OR (output_type_value='TEACHER_TASK' AND (
                    task_code_value NOT IN (
                      'P-REL-MEMO','P-REL-ATTENDANCE','P-FB-NEGATIVE',
                      'P-FB-COMPLAINT','P-FB-BLACKLIST'
                    ) OR assignment_key_value IS NULL
                  ))
               OR (output_type_value<>'TEACHER_TASK' AND (
                    task_code_value IS NOT NULL
                    OR assignment_key_value IS NOT NULL
                  )) THEN
              RAISE EXCEPTION 'DTS_V2_TRIGGER_MATCH_KIND_INVALID'
                USING ERRCODE='22023';
            END IF;
            match_id_value:='TRM-V2-' || substring(encode(sha256(
              convert_to(dedupe_value,'UTF8')),'hex') for 40);
            IF EXISTS(
              SELECT 1 FROM public.personalized_trigger_matches existing
              WHERE existing.dedupe_key=dedupe_value
                AND (
                  existing.trigger_match_id IS DISTINCT FROM match_id_value
                  OR existing.teacher_id IS DISTINCT FROM teacher_value
                  OR existing.lesson_source_region
                       IS DISTINCT FROM p_source_region
                  OR existing.lesson_id
                       IS DISTINCT FROM p_source_appoint_id
                  OR existing.source_region
                       IS DISTINCT FROM p_source_region
                  OR existing.source_appoint_id
                       IS DISTINCT FROM p_source_appoint_id
                  OR existing.participation_seq IS DISTINCT FROM
                       (item->>'participation_seq')::integer
                  OR existing.match_kind IS DISTINCT FROM kind_value
                  OR existing.target_task_code
                       IS DISTINCT FROM task_code_value
                  OR existing.assignment_dedupe_key
                       IS DISTINCT FROM assignment_key_value
                  OR existing.seed_rule_rank IS DISTINCT FROM
                       (item->>'seed_rule_rank')::integer
                  OR existing.threshold_required IS DISTINCT FROM
                       (item->>'threshold_required')::integer
                  OR existing.evidence_discriminator
                       IS DISTINCT FROM discriminator_value
                  OR existing.evidence_discriminator_type
                       IS DISTINCT FROM discriminator_type_value
                  OR existing.output_type
                       IS DISTINCT FROM output_type_value
                  OR existing.output_key
                       IS DISTINCT FROM output_key_value
                )
            ) THEN
              RAISE EXCEPTION 'DTS_V2_TRIGGER_MATCH_IDENTITY_CONFLICT'
                USING ERRCODE='23514';
            END IF;
            INSERT INTO public.personalized_trigger_matches(
              trigger_match_id,trigger_code,rule_version,teacher_id,
              lesson_source_region,lesson_id,complaint_rule_id,dedupe_key,
              output_type,output_title,output_id,match_status,
              evidence_snapshot,matched_at,materialized_at,updated_at,
              source_region,source_appoint_id,participation_seq,match_kind,
              match_revision,last_transition_at,target_task_code,
              assignment_dedupe_key,seed_rule_rank,threshold_required,
              source_candidate_active,plan_evidence,plan_evidence_hash,
              teacher_execution_variant,output_key,source_ref,
              assignment_dedupe_key_sort_bytes,source_region_rank,
              source_appoint_id_type,source_appoint_id_type_rank,
              source_appoint_id_numeric,source_appoint_id_sort_bytes,
              evidence_discriminator,evidence_discriminator_type,
              evidence_discriminator_type_rank,
              evidence_discriminator_numeric,
              evidence_discriminator_sort_bytes,dedupe_key_sort_bytes,
              materialization_origin,created_projection_generation,
              serving_projection_generation,is_serving,
              task_assignment_id,ops_case_id,notification_id,
              source_aggregate_revision,projection_generation,
              triggering_event_id
            ) VALUES (
              match_id_value,trigger_code_value,'dts-direct-v2',teacher_value,
              p_source_region,p_source_appoint_id,complaint_rule_value,
              dedupe_value,output_type_value,
              'Trigger output pending materialization',NULL,status_value,
              evidence,transaction_timestamp(),NULL,
              transaction_timestamp(),p_source_region,p_source_appoint_id,
              (item->>'participation_seq')::integer,kind_value,1,
              transaction_timestamp(),task_code_value,assignment_key_value,
              (item->>'seed_rule_rank')::integer,
              (item->>'threshold_required')::integer,true,evidence,
              public.dts_canonical_json_sha256_v1(evidence),variant_value,
              output_key_value,'trigger-match:' || dedupe_value,NULL,0,
              appoint_type,CASE appoint_type WHEN 'NUMERIC' THEN 0 ELSE 1 END,
              CASE WHEN appoint_type='NUMERIC'
                THEN p_source_appoint_id::numeric END,
              CASE WHEN appoint_type='TEXT'
                THEN convert_to(p_source_appoint_id,'UTF8') END,
              discriminator_value,discriminator_type_value,
              CASE discriminator_type_value WHEN 'NUMERIC' THEN 0 ELSE 1 END,
              CASE WHEN discriminator_type_value='NUMERIC'
                THEN discriminator_value::numeric END,
              CASE WHEN discriminator_type_value='TEXT'
                THEN convert_to(discriminator_value,'UTF8') END,
              convert_to(dedupe_value,'UTF8'),'V2_LIVE',
              p_projection_generation,p_projection_generation,true,
              NULL,NULL,NULL,p_expected_course_aggregate_revision,
              p_projection_generation,p_triggering_event_id
            ) ON CONFLICT (dedupe_key) DO UPDATE SET
              trigger_code=excluded.trigger_code,
              rule_version=excluded.rule_version,
              complaint_rule_id=excluded.complaint_rule_id,
              output_type=excluded.output_type,
              match_status=CASE WHEN
                personalized_trigger_matches.output_id IS NOT NULL
                AND excluded.match_status='MATCHED'
                THEN 'MATERIALIZED' ELSE excluded.match_status END,
              evidence_snapshot=excluded.evidence_snapshot,
              updated_at=transaction_timestamp(),
              source_candidate_active=true,
              plan_evidence=excluded.plan_evidence,
              teacher_execution_variant=
                excluded.teacher_execution_variant,
              output_key=excluded.output_key,
              source_aggregate_revision=
                excluded.source_aggregate_revision,
              projection_generation=excluded.projection_generation,
              serving_projection_generation=
                excluded.serving_projection_generation,
              is_serving=true,
              triggering_event_id=excluded.triggering_event_id,
              match_revision=personalized_trigger_matches.match_revision+
                CASE WHEN
                  personalized_trigger_matches.trigger_code
                    IS DISTINCT FROM excluded.trigger_code
                  OR personalized_trigger_matches.complaint_rule_id
                    IS DISTINCT FROM excluded.complaint_rule_id
                  OR personalized_trigger_matches.output_type
                    IS DISTINCT FROM excluded.output_type
                  OR personalized_trigger_matches.match_status
                    IS DISTINCT FROM CASE WHEN
                      personalized_trigger_matches.output_id IS NOT NULL
                      AND excluded.match_status='MATCHED'
                      THEN 'MATERIALIZED' ELSE excluded.match_status END
                  OR personalized_trigger_matches.plan_evidence
                    IS DISTINCT FROM excluded.plan_evidence
                  OR personalized_trigger_matches.teacher_execution_variant
                    IS DISTINCT FROM excluded.teacher_execution_variant
                  OR personalized_trigger_matches.output_key
                    IS DISTINCT FROM excluded.output_key
                  OR NOT personalized_trigger_matches.source_candidate_active
                THEN 1 ELSE 0 END,
              last_transition_at=CASE WHEN
                personalized_trigger_matches.trigger_code
                    IS DISTINCT FROM excluded.trigger_code
                OR personalized_trigger_matches.complaint_rule_id
                    IS DISTINCT FROM excluded.complaint_rule_id
                OR personalized_trigger_matches.output_type
                    IS DISTINCT FROM excluded.output_type
                OR personalized_trigger_matches.plan_evidence
                    IS DISTINCT FROM excluded.plan_evidence
                OR personalized_trigger_matches.match_status
                    IS DISTINCT FROM CASE WHEN
                      personalized_trigger_matches.output_id IS NOT NULL
                      AND excluded.match_status='MATCHED'
                      THEN 'MATERIALIZED' ELSE excluded.match_status END
                OR personalized_trigger_matches.teacher_execution_variant
                    IS DISTINCT FROM excluded.teacher_execution_variant
                OR personalized_trigger_matches.output_key
                    IS DISTINCT FROM excluded.output_key
                OR NOT personalized_trigger_matches.source_candidate_active
                THEN transaction_timestamp()
                ELSE personalized_trigger_matches.last_transition_at END;
            -- The command result is operational telemetry only; semantic
            -- idempotency is proven by the row revision/state checks above.
            IF existed_before THEN
              updated_count:=updated_count+1;
            ELSE
              inserted_count:=inserted_count+1;
            END IF;
            IF assignment_key_value IS NOT NULL
               AND NOT assignment_key_value=ANY(affected_keys) THEN
              affected_keys:=array_append(
                affected_keys,assignment_key_value
              );
            END IF;
          END LOOP;

          FOR group_row IN
            SELECT DISTINCT teacher_id,evidence_discriminator AS label_id
            FROM public.personalized_trigger_matches
            WHERE source_region=p_source_region
              AND source_appoint_id=p_source_appoint_id
              AND match_kind='NEGATIVE_LABEL_COURSE'
          LOOP
            PERFORM public.reconcile_negative_label_group_v2(
              group_row.teacher_id,group_row.label_id,
              p_projection_generation
            );
          END LOOP;
          FOREACH assignment_item IN ARRAY affected_keys LOOP
            PERFORM public.rebuild_task_plan_v2(
              assignment_item,to_jsonb(incoming_keys)
            );
          END LOOP;
          RETURN jsonb_build_object(
            'plan_sha256',plan_hash,
            'counts',jsonb_build_object(
              'matches_inserted',inserted_count,
              'matches_updated',updated_count,
              'matches_suppressed',suppressed_count,
              'task_plans_reconciled',cardinality(affected_keys)
            )
          );
        END
        $function$;
        REVOKE ALL ON FUNCTION
          public.reconcile_course_trigger_matches_v2(
            text,text,bigint,bigint,text,jsonb,text
          ) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION
          public.reconcile_course_trigger_matches_v2(
            text,text,bigint,bigint,text,jsonb,text
          ) TO tit_dts_outbox_worker_runtime;
        """
    )


def _install_blacklist_reconciler() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.reconcile_blacklist_threshold_v2(
          p_source_region text,p_teacher_id text,
          p_expected_teacher_student_revision bigint,
          p_projection_generation bigint,p_triggering_event_id text
        ) RETURNS jsonb
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE guarded_generation bigint;
        DECLARE current_count integer;
        DECLARE token_set_hash text;
        DECLARE match_key text;
        DECLARE assignment_key text;
        DECLARE match_id text;
        DECLARE evidence jsonb;
        DECLARE desired_status text;
        BEGIN
          guarded_generation:=
            public.dts_v2_runtime_primary_guard_v1('TEACHER_STUDENT');
          IF guarded_generation IS NULL
             OR guarded_generation<>p_projection_generation THEN
            RAISE EXCEPTION 'DTS_V2_BLACKLIST_GENERATION_CHANGED'
              USING ERRCODE='40001';
          END IF;
          IF p_source_region NOT IN ('dom','ovs')
             OR p_teacher_id IS NULL OR btrim(p_teacher_id)=''
             OR p_expected_teacher_student_revision<1
             OR p_projection_generation<1
             OR p_triggering_event_id IS NULL
             OR btrim(p_triggering_event_id)='' THEN
            RAISE EXCEPTION 'DTS_V2_BLACKLIST_REQUEST_INVALID'
              USING ERRCODE='22023';
          END IF;
          IF NOT EXISTS(
            SELECT 1 FROM public.outbox_events event
            JOIN public.domain_aggregate_revisions aggregate
              ON aggregate.aggregate_type='TEACHER_STUDENT'
             AND aggregate.aggregate_id=event.aggregate_id
            WHERE event.event_id=p_triggering_event_id
              AND event.aggregate_type='TEACHER_STUDENT'
              AND event.event_type='source_wide.changed.v2'
              AND aggregate.revision=
                    p_expected_teacher_student_revision
              AND aggregate.canonical_key->>'source_region'
                    =p_source_region
              AND aggregate.canonical_key->>'teacher_id'=p_teacher_id
          ) THEN
            RAISE EXCEPTION 'DTS_V2_BLACKLIST_AGGREGATE_MISMATCH'
              USING ERRCODE='40001';
          END IF;
          PERFORM 1 FROM public.teachers
          WHERE teacher_id=p_teacher_id FOR SHARE;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'DTS_V2_BLACKLIST_TEACHER_MISSING'
              USING ERRCODE='23503';
          END IF;
          SELECT count(DISTINCT student_token),
            public.dts_canonical_json_sha256_v1(
              coalesce(jsonb_agg(student_token ORDER BY
                convert_to(student_token,'UTF8')),'[]'::jsonb)
            )
          INTO current_count,token_set_hash
          FROM (
            SELECT DISTINCT student_token
            FROM public.teacher_student_relationship_current
            WHERE source_region=p_source_region
              AND teacher_id=p_teacher_id AND is_blocked IS TRUE
          ) blocked;
          match_key:='blacklist-threshold:' || p_source_region || ':' ||
            p_teacher_id;
          assignment_key:='personalized:P-FB-BLACKLIST:' || p_teacher_id;
          match_id:='TRM-V2-' || substring(encode(sha256(
            convert_to(match_key,'UTF8')),'hex') for 40);
          desired_status:=CASE WHEN current_count>=2
            THEN 'MATCHED' ELSE 'SUPPRESSED' END;
          evidence:=jsonb_build_object(
            'protocol_version','blacklist-threshold-evidence-v1',
            'rule_code','TR-FB-BLACKLIST',
            'source_region',p_source_region,
            'teacher_id',p_teacher_id,'threshold',2,
            'current_count',current_count,
            'student_token_set_hash',token_set_hash
          );
          INSERT INTO public.personalized_trigger_matches(
            trigger_match_id,trigger_code,rule_version,teacher_id,
            lesson_source_region,lesson_id,complaint_rule_id,dedupe_key,
            output_type,output_title,output_id,match_status,
            evidence_snapshot,matched_at,materialized_at,updated_at,
            source_region,source_appoint_id,participation_seq,match_kind,
            match_revision,last_transition_at,target_task_code,
            assignment_dedupe_key,seed_rule_rank,threshold_required,
            source_candidate_active,plan_evidence,plan_evidence_hash,
            teacher_execution_variant,output_key,source_ref,
            assignment_dedupe_key_sort_bytes,source_region_rank,
            source_appoint_id_type,source_appoint_id_type_rank,
            source_appoint_id_numeric,source_appoint_id_sort_bytes,
            evidence_discriminator,evidence_discriminator_type,
            evidence_discriminator_type_rank,
            evidence_discriminator_numeric,
            evidence_discriminator_sort_bytes,dedupe_key_sort_bytes,
            materialization_origin,created_projection_generation,
            serving_projection_generation,is_serving,
            task_assignment_id,ops_case_id,notification_id,
            source_aggregate_revision,projection_generation,
            triggering_event_id
          ) VALUES (
            match_id,'TR-FB-BLACKLIST','dts-direct-v2',p_teacher_id,
            NULL,NULL,NULL,match_key,'TEACHER_TASK',
            'Blacklist prevention improvement',NULL,desired_status,
            evidence,transaction_timestamp(),NULL,transaction_timestamp(),
            p_source_region,NULL,NULL,'BLACKLIST_THRESHOLD',1,
            transaction_timestamp(),'P-FB-BLACKLIST',assignment_key,10,2,
            true,evidence,public.dts_canonical_json_sha256_v1(evidence),
            'GENERAL',assignment_key,'trigger-match:' || match_key,NULL,
            CASE p_source_region WHEN 'dom' THEN 0 ELSE 1 END,'NONE',2,
            NULL,NULL,NULL,'NONE',2,NULL,NULL,
            convert_to(match_key,'UTF8'),'V2_LIVE',
            p_projection_generation,p_projection_generation,true,
            NULL,NULL,NULL,p_expected_teacher_student_revision,
            p_projection_generation,p_triggering_event_id
          ) ON CONFLICT (dedupe_key) DO UPDATE SET
            match_status=CASE WHEN
              personalized_trigger_matches.task_assignment_id IS NOT NULL
                AND excluded.match_status='MATCHED'
              THEN 'MATERIALIZED' ELSE excluded.match_status END,
            evidence_snapshot=excluded.evidence_snapshot,
            plan_evidence=excluded.plan_evidence,
            source_aggregate_revision=excluded.source_aggregate_revision,
            projection_generation=excluded.projection_generation,
            serving_projection_generation=
              excluded.serving_projection_generation,
            is_serving=true,source_candidate_active=true,
            triggering_event_id=excluded.triggering_event_id,
            match_revision=personalized_trigger_matches.match_revision+
              CASE WHEN personalized_trigger_matches.match_status
                   IS DISTINCT FROM CASE WHEN
                     personalized_trigger_matches.task_assignment_id
                       IS NOT NULL AND excluded.match_status='MATCHED'
                     THEN 'MATERIALIZED' ELSE excluded.match_status END
                   OR personalized_trigger_matches.plan_evidence
                     IS DISTINCT FROM excluded.plan_evidence
                   THEN 1 ELSE 0 END,
            last_transition_at=CASE WHEN
              personalized_trigger_matches.match_status
                IS DISTINCT FROM CASE WHEN
                  personalized_trigger_matches.task_assignment_id IS NOT NULL
                    AND excluded.match_status='MATCHED'
                  THEN 'MATERIALIZED' ELSE excluded.match_status END
              OR personalized_trigger_matches.plan_evidence
                IS DISTINCT FROM excluded.plan_evidence
              THEN transaction_timestamp()
              ELSE personalized_trigger_matches.last_transition_at END,
            updated_at=transaction_timestamp();
          PERFORM public.rebuild_task_plan_v2(
            assignment_key,jsonb_build_array(match_key)
          );
          RETURN jsonb_build_object(
            'match_status',desired_status,
            'current_blocked_student_count',current_count,
            'student_token_set_hash',token_set_hash
          );
        END
        $function$;
        REVOKE ALL ON FUNCTION
          public.reconcile_blacklist_threshold_v2(
            text,text,bigint,bigint,text
          ) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION
          public.reconcile_blacklist_threshold_v2(
            text,text,bigint,bigint,text
          ) TO tit_dts_outbox_worker_runtime;
        """
    )


def _install_task_materializer() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.materialize_task_plan_v2(
          p_event_id text,p_aggregate_id text,p_aggregate_revision bigint,
          p_expected_plan_state_hash text
        ) RETURNS jsonb
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE plan_row public.domain_aggregate_revisions%ROWTYPE;
        DECLARE event_row public.outbox_events%ROWTYPE;
        DECLARE state jsonb;
        DECLARE assignment_key text;
        DECLARE teacher_value text;
        DECLARE task_code_value text;
        DECLARE existing_assignment public.task_assignments%ROWTYPE;
        DECLARE seed public.personalized_trigger_matches%ROWTYPE;
        DECLARE template_row public.task_templates%ROWTYPE;
        DECLARE copy_row public.config_versions%ROWTYPE;
        DECLARE title_value text;
        DECLARE mapped_title text;
        DECLARE why_value text;
        DECLARE evidence_value jsonb;
        DECLARE due_hours integer;
        DECLARE eligible_at timestamptz;
        DECLARE due_value timestamptz;
        DECLARE assignment_id_value text;
        DECLARE payload_hash text;
        DECLARE active_hash text;
        DECLARE link_count integer;
        BEGIN
          PERFORM public.dts_v2_runtime_primary_guard_v1('TASK_PLAN');
          IF p_event_id IS NULL OR btrim(p_event_id)=''
             OR p_aggregate_id IS NULL OR btrim(p_aggregate_id)=''
             OR p_aggregate_revision<1
             OR p_expected_plan_state_hash !~ '^[0-9a-f]{64}$' THEN
            RAISE EXCEPTION 'TASK_PLAN_MATERIALIZATION_REQUEST_INVALID'
              USING ERRCODE='22023';
          END IF;
          SELECT * INTO event_row FROM public.outbox_events
          WHERE event_id=p_event_id FOR UPDATE;
          IF NOT FOUND OR event_row.aggregate_type<>'TASK_PLAN'
             OR event_row.aggregate_id<>p_aggregate_id
             OR event_row.event_type<>'task.materialization.requested.v2'
             OR event_row.status<>'PENDING'
             OR (event_row.payload->>'aggregate_revision')::bigint
                  <>p_aggregate_revision
             OR event_row.payload->>'plan_state_hash'
                  <>p_expected_plan_state_hash THEN
            RAISE EXCEPTION 'TASK_PLAN_MATERIALIZATION_EVENT_MISMATCH'
              USING ERRCODE='40001';
          END IF;
          SELECT * INTO plan_row
          FROM public.domain_aggregate_revisions
          WHERE aggregate_type='TASK_PLAN'
            AND aggregate_id=p_aggregate_id FOR UPDATE;
          IF NOT FOUND OR plan_row.revision<>p_aggregate_revision THEN
            RAISE EXCEPTION 'TASK_PLAN_REVISION_CONFLICT'
              USING ERRCODE='40001';
          END IF;
          state:=plan_row.aggregate_state;
          IF state->>'protocol_version'<>'task-plan-state-v1'
             OR state->>'plan_state_hash'<>p_expected_plan_state_hash
             OR public.dts_canonical_json_sha256_v1(
                  state-'plan_state_hash'
                )<>p_expected_plan_state_hash THEN
            RAISE EXCEPTION 'TASK_PLAN_STATE_HASH_MISMATCH'
              USING ERRCODE='23514';
          END IF;
          assignment_key:=state->>'assignment_dedupe_key';
          teacher_value:=state->>'teacher_id';
          task_code_value:=state->>'target_task_code';
          PERFORM pg_advisory_xact_lock_shared(hashtextextended(
            'CONFIG:teacher_personalized_copy',0
          ));
          PERFORM pg_advisory_xact_lock_shared(hashtextextended(
            'TEMPLATE:' || task_code_value,0
          ));
          PERFORM pg_advisory_xact_lock(hashtextextended(
            'TASK_ASSIGNMENT:' || assignment_key,0
          ));
          SELECT * INTO existing_assignment
          FROM public.task_assignments
          WHERE dedupe_key=assignment_key FOR UPDATE;
          IF FOUND THEN
            IF (state->>'materializable')::boolean IS NOT TRUE THEN
              RETURN jsonb_build_object(
                'outcome','NOT_MATERIALIZABLE','assignment_id',NULL
              );
            END IF;
            IF existing_assignment.teacher_id<>teacher_value
               OR existing_assignment.task_code<>task_code_value
               OR existing_assignment.task_kind<>'PERSONALIZED_IMPROVEMENT'
               OR existing_assignment.creator_system<>'TRIGGER_CENTER'
               OR existing_assignment.materialization_seed_payload_hash
                    !~ '^[0-9a-f]{64}$' THEN
              RAISE EXCEPTION 'TASK_PLAN_EXISTING_ASSIGNMENT_CONFLICT'
                USING ERRCODE='23514';
            END IF;
            UPDATE public.personalized_trigger_matches
            SET task_assignment_id=existing_assignment.assignment_id,
                match_status='MATERIALIZED',
                materialized_at=coalesce(
                  materialized_at,transaction_timestamp()
                ),updated_at=transaction_timestamp()
            WHERE assignment_dedupe_key=assignment_key
              AND output_type='TEACHER_TASK' AND is_serving
              AND match_status IN ('MATCHED','MATERIALIZED')
              AND (task_assignment_id IS NULL OR
                   task_assignment_id=existing_assignment.assignment_id);
            GET DIAGNOSTICS link_count=ROW_COUNT;
            RETURN jsonb_build_object(
              'outcome','EXISTING_ASSIGNMENT',
              'assignment_id',existing_assignment.assignment_id
            );
          END IF;
          IF (state->>'materializable')::boolean IS NOT TRUE THEN
            RETURN jsonb_build_object(
              'outcome','NOT_MATERIALIZABLE','assignment_id',NULL
            );
          END IF;
          active_hash:=public.task_plan_active_match_set_hash_v2(
            assignment_key
          );
          IF active_hash<>state->>'active_match_set_hash' THEN
            RAISE EXCEPTION 'TASK_PLAN_ACTIVE_MATCH_SET_CHANGED'
              USING ERRCODE='40001';
          END IF;
          SELECT * INTO seed
          FROM public.personalized_trigger_matches
          WHERE assignment_dedupe_key=assignment_key
            AND output_type='TEACHER_TASK' AND is_serving
            AND match_status IN ('MATCHED','MATERIALIZED')
          ORDER BY seed_rule_rank,source_region_rank,
            source_appoint_id_type_rank,source_appoint_id_numeric NULLS LAST,
            source_appoint_id_sort_bytes NULLS LAST,
            participation_seq NULLS LAST,
            evidence_discriminator_type_rank,
            evidence_discriminator_numeric NULLS LAST,
            evidence_discriminator_sort_bytes NULLS LAST,
            dedupe_key_sort_bytes
          LIMIT 1 FOR UPDATE;
          IF NOT FOUND OR seed.teacher_id<>teacher_value
             OR seed.target_task_code<>task_code_value
             OR seed.teacher_execution_variant NOT IN
                ('GENERAL','TEACHING_ENVIRONMENT_PHOTO') THEN
            RAISE EXCEPTION 'TASK_PLAN_CANONICAL_SEED_MISSING'
              USING ERRCODE='23514';
          END IF;
          SELECT * INTO template_row FROM public.task_templates
          WHERE row_id=state->>'template_version_id'
            AND template_id=task_code_value AND status='PUBLISHED'
            AND revision=(state->>'template_revision')::integer
          FOR SHARE;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'TASK_PLAN_TEMPLATE_CHANGED'
              USING ERRCODE='40001';
          END IF;
          SELECT * INTO copy_row FROM public.config_versions
          WHERE version_id=state->>'teacher_copy_version_id'
            AND config_key='teacher_personalized_copy'
            AND status='PUBLISHED' AND schema_version=1
            AND version_number=
              (state->>'teacher_copy_version_number')::integer
            AND payload_hash=
              public.dts_canonical_json_sha256_v1(payload)
          FOR SHARE;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'TASK_PLAN_COPY_CHANGED'
              USING ERRCODE='40001';
          END IF;
          title_value:=coalesce(
            nullif(template_row.payload->>'title',''),task_code_value
          );
          IF task_code_value='P-FB-NEGATIVE' THEN
            mapped_title:=copy_row.payload->'negative_label_to_en'->>
              (seed.plan_evidence->>'label_name');
            title_value:=CASE WHEN nullif(mapped_title,'') IS NULL
              THEN copy_row.payload->'fallback_titles'->>'negative'
              ELSE coalesce(
                nullif(copy_row.payload->>'negative_title_prefix',''),'') ||
                mapped_title END;
          ELSIF task_code_value='P-FB-COMPLAINT' THEN
            mapped_title:=copy_row.payload->'complaint_category_to_en'->>
              (seed.plan_evidence->>'category_l3');
            title_value:=CASE WHEN nullif(mapped_title,'') IS NULL
              THEN copy_row.payload->'fallback_titles'->>'complaint'
              ELSE coalesce(
                nullif(copy_row.payload->>'complaint_title_prefix',''),'') ||
                mapped_title END;
          END IF;
          why_value:=coalesce(
            nullif(template_row.payload->>'why_template',''),
            'A confirmed business rule identified this improvement need.'
          ) || ' Evidence: ' || CASE seed.match_kind
            WHEN 'ABSENCE_P_REL_MEMO'
              THEN 'A confirmed Lesson Memo absence reason triggered this task.'
            WHEN 'ABSENCE_P_REL_ATTENDANCE'
              THEN 'A confirmed attendance absence reason triggered this task.'
            WHEN 'COMPLAINT_ATTENDANCE'
              THEN 'A confirmed attendance complaint triggered this task.'
            WHEN 'NEGATIVE_LABEL_COURSE'
              THEN 'The same negative feedback pattern appeared in at least two courses.'
            WHEN 'GENERAL_COMPLAINT'
              THEN 'A confirmed general complaint matched this improvement category.'
            WHEN 'BLACKLIST_THRESHOLD'
              THEN 'At least two distinct learners currently blacklist this teacher.'
            ELSE 'A confirmed rule match triggered this task.' END;
          IF title_value ~ U&'[\4E00-\9FFF]'
             OR why_value ~ U&'[\4E00-\9FFF]' THEN
            RAISE EXCEPTION 'TASK_PLAN_TEACHER_COPY_NOT_ENGLISH'
              USING ERRCODE='23514';
          END IF;
          eligible_at:=(state->>'eligible_since_at')::timestamptz;
          due_hours:=CASE WHEN task_code_value LIKE 'P-REL-%'
            THEN 48 ELSE 72 END;
          due_value:=eligible_at+make_interval(hours=>due_hours);
          evidence_value:=jsonb_build_object(
            'protocol_version','task-assignment-evidence-v2',
            'seed_match_key',seed.dedupe_key,
            'seed_match_revision',seed.match_revision,
            'match_kind',seed.match_kind,
            'plan_evidence',seed.plan_evidence,
            'plan_evidence_hash',seed.plan_evidence_hash,
            'teacher_copy_version_id',copy_row.version_id,
            'teacher_copy_config_key','teacher_personalized_copy',
            'teacher_copy_payload_hash',copy_row.payload_hash,
            'teacher_execution_variant',seed.teacher_execution_variant,
            'eligibility_generation',
              (state->>'eligibility_generation')::bigint
          );
          payload_hash:=public.dts_canonical_json_sha256_v1(
            jsonb_build_object(
              'why',why_value,'display_title',title_value,
              'evidence_snapshot',evidence_value,
              'template_version_id',template_row.row_id,
              'template_revision',template_row.revision,
              'teacher_execution_variant',seed.teacher_execution_variant,
              'eligibility_generation',
                (state->>'eligibility_generation')::bigint,
              'eligible_since_at',state->>'eligible_since_at',
              'due_at',to_char(due_value AT TIME ZONE 'UTC',
                'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
              'timezone_used',state->>'timezone_used',
              'timezone_source',state->>'timezone_source',
              'timezone_verified_at',state->>'timezone_verified_at',
              'teacher_copy_version_id',copy_row.version_id,
              'teacher_copy_config_key','teacher_personalized_copy'
            )
          );
          assignment_id_value:='TAS-V2-' || substring(encode(sha256(
            convert_to('task-assignment:v2','UTF8') || decode('00','hex') ||
            convert_to(assignment_key,'UTF8')),'hex') for 40);
          PERFORM set_config('tit.task_materialization_v2','on',true);
          INSERT INTO public.task_assignments(
            assignment_id,teacher_id,task_code,template_version_id,
            task_kind,creator_system,status,priority,why,display_title,
            evidence_snapshot,due_at,timezone_used,timezone_source,
            timezone_verified_at,status_reason_code,source_mode,dedupe_key,
            created_by,updated_by,row_version,assigned_at,status_changed_at,
            completed_at,created_at,updated_at,
            materialization_seed_kind,materialization_seed_key,
            materialization_seed_revision,
            materialization_seed_payload_hash,
            materialization_template_revision,
            materialization_plan_revision,eligibility_generation,
            eligible_since_at,teacher_copy_version_id,
            teacher_copy_config_key,teacher_execution_variant,
            plan_evidence_hash
          ) VALUES (
            assignment_id_value,teacher_value,task_code_value,
            template_row.row_id,'PERSONALIZED_IMPROVEMENT','TRIGGER_CENTER',
            'ASSIGNED',coalesce(template_row.payload->>'priority','P1'),
            why_value,title_value,evidence_value,due_value,
            state->>'timezone_used',state->>'timezone_source',
            (state->>'timezone_verified_at')::timestamptz,NULL,
            template_row.source_mode,assignment_key,'DTS_V2_TASK_PLANNER',
            'DTS_V2_TASK_PLANNER',1,transaction_timestamp(),
            transaction_timestamp(),NULL,transaction_timestamp(),
            transaction_timestamp(),'MATCH',seed.dedupe_key,
            seed.match_revision,payload_hash,template_row.revision,
            p_aggregate_revision,
            (state->>'eligibility_generation')::bigint,eligible_at,
            copy_row.version_id,'teacher_personalized_copy',
            seed.teacher_execution_variant,seed.plan_evidence_hash
          );
          UPDATE public.personalized_trigger_matches
          SET task_assignment_id=assignment_id_value,
              match_status='MATERIALIZED',
              materialized_at=transaction_timestamp(),
              updated_at=transaction_timestamp()
          WHERE assignment_dedupe_key=assignment_key
            AND output_type='TEACHER_TASK' AND is_serving
            AND match_status IN ('MATCHED','MATERIALIZED');
          GET DIAGNOSTICS link_count=ROW_COUNT;
          IF link_count<1 THEN
            RAISE EXCEPTION 'TASK_PLAN_MATCH_LINK_MISSING'
              USING ERRCODE='23514';
          END IF;
          RETURN jsonb_build_object(
            'outcome','MATERIALIZED',
            'assignment_id',assignment_id_value
          );
        END
        $function$;
        REVOKE ALL ON FUNCTION public.materialize_task_plan_v2(
          text,text,bigint,text
        ) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.materialize_task_plan_v2(
          text,text,bigint,text
        ) TO tit_dts_outbox_worker_runtime;
        """
    )


def _install_fanout_and_acl() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.validate_trigger_match_task_link_v2()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        BEGIN
          IF NEW.task_assignment_id IS NOT NULL AND NOT EXISTS(
            SELECT 1 FROM public.task_assignments assignment
            WHERE assignment.assignment_id=NEW.task_assignment_id
              AND assignment.dedupe_key=NEW.assignment_dedupe_key
              AND assignment.teacher_id=NEW.teacher_id
              AND assignment.task_code=NEW.target_task_code
              AND assignment.task_kind='PERSONALIZED_IMPROVEMENT'
              AND assignment.creator_system='TRIGGER_CENTER'
          ) THEN
            RAISE EXCEPTION 'TRIGGER_MATCH_TASK_LINK_MISMATCH'
              USING ERRCODE='23514';
          END IF;
          RETURN NULL;
        END
        $function$;
        REVOKE ALL ON FUNCTION public.validate_trigger_match_task_link_v2()
          FROM PUBLIC;
        CREATE CONSTRAINT TRIGGER trg_validate_trigger_match_task_link_v2
        AFTER INSERT OR UPDATE ON public.personalized_trigger_matches
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION
          public.validate_trigger_match_task_link_v2();

        CREATE FUNCTION public.fanout_task_plans_for_copy_publication_v2(
          p_version_id text
        ) RETURNS jsonb
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE generation_value bigint;
        DECLARE group_row record;
        DECLARE key_value text;
        DECLARE fanout_count integer := 0;
        BEGIN
          IF current_setting('tit.catalog_publication_v2',true)<>'on'
             OR NOT EXISTS(
               SELECT 1 FROM public.config_versions
               WHERE version_id=p_version_id
                 AND config_key='teacher_personalized_copy'
                 AND status='PUBLISHED' AND schema_version=1
                 AND payload_hash=
                   public.dts_canonical_json_sha256_v1(payload)
             ) THEN
            RAISE EXCEPTION 'TASK_COPY_PUBLICATION_CONTEXT_INVALID'
              USING ERRCODE='42501';
          END IF;
          SELECT projection_generation INTO generation_value
          FROM public.dts_pipeline_control WHERE control_id='PRIMARY';
          IF generation_value IS NULL THEN
            generation_value:=0;
          END IF;
          FOR group_row IN
            SELECT DISTINCT match.teacher_id,
              match.evidence_discriminator AS label_id
            FROM public.personalized_trigger_matches match
            LEFT JOIN public.task_assignments assignment
              ON assignment.dedupe_key=match.assignment_dedupe_key
            WHERE match.match_kind='NEGATIVE_LABEL_COURSE'
              AND match.is_serving AND assignment.assignment_id IS NULL
            ORDER BY match.teacher_id,
              match.evidence_discriminator
          LOOP
            PERFORM public.reconcile_negative_label_group_v2(
              group_row.teacher_id,group_row.label_id,
              greatest(generation_value,1)
            );
          END LOOP;
          FOR key_value IN
            SELECT DISTINCT match.assignment_dedupe_key
            FROM public.personalized_trigger_matches match
            LEFT JOIN public.task_assignments assignment
              ON assignment.dedupe_key=match.assignment_dedupe_key
            WHERE match.assignment_dedupe_key IS NOT NULL
              AND match.is_serving AND assignment.assignment_id IS NULL
            ORDER BY match.assignment_dedupe_key
          LOOP
            PERFORM public.rebuild_task_plan_v2(
              key_value,jsonb_build_array(
                'catalog:teacher_personalized_copy:' || p_version_id
              )
            );
            fanout_count:=fanout_count+1;
          END LOOP;
          RETURN jsonb_build_object('task_plan_count',fanout_count);
        END
        $function$;
        REVOKE ALL ON FUNCTION
          public.fanout_task_plans_for_copy_publication_v2(text)
        FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION
          public.fanout_task_plans_for_copy_publication_v2(text)
        TO tit_growth_app;

        REVOKE INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER
          ON TABLE public.personalized_trigger_matches
          FROM PUBLIC,tit_growth_app,tit_teacher_crud,
            tit_dts_outbox_worker_runtime;
        GRANT SELECT ON TABLE public.personalized_trigger_matches
          TO tit_growth_app,tit_dts_outbox_worker_runtime;
        REVOKE INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER
          ON TABLE public.task_assignments
          FROM tit_dts_outbox_worker_runtime;
        """
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("DTS v2 task output contract requires PostgreSQL")
    _preflight()
    _expand_config_contract()
    _expand_assignment_contract()
    _expand_match_contract()
    _install_row_guards()
    _install_task_plan_reducer()
    _install_negative_group_reducer()
    _install_course_reconciler()
    _install_blacklist_reconciler()
    _install_task_materializer()
    _install_fanout_and_acl()


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("DTS v2 task output contract requires PostgreSQL")
    op.execute(
        r"""
        DO $task_output_downgrade_guard$
        BEGIN
          IF EXISTS(
            SELECT 1 FROM public.personalized_trigger_matches
            WHERE materialization_origin<>'LEGACY_REUSED'
          ) OR EXISTS(
            SELECT 1 FROM public.task_assignments
            WHERE materialization_seed_kind IN ('MATCH','LEGACY_COMPAT')
          ) OR EXISTS(
            SELECT 1 FROM public.domain_aggregate_revisions
            WHERE aggregate_type='TASK_PLAN'
          ) OR EXISTS(
            SELECT 1 FROM public.config_versions
            WHERE config_key='teacher_personalized_copy'
          ) THEN
            RAISE EXCEPTION
              'refusing DTS v2 task output downgrade: v2 history exists';
          END IF;
        END
        $task_output_downgrade_guard$;
        DROP TRIGGER IF EXISTS trg_validate_trigger_match_task_link_v2
          ON public.personalized_trigger_matches;
        DROP TRIGGER IF EXISTS trg_normalize_trigger_match_v2
          ON public.personalized_trigger_matches;
        DROP TRIGGER IF EXISTS trg_task_assignment_write
          ON public.task_assignments;
        DROP TRIGGER IF EXISTS trg_config_version_contract_v2
          ON public.config_versions;
        DROP FUNCTION IF EXISTS
          public.fanout_task_plans_for_copy_publication_v2(text);
        DROP FUNCTION IF EXISTS
          public.materialize_task_plan_v2(text,text,bigint,text);
        DROP FUNCTION IF EXISTS
          public.reconcile_blacklist_threshold_v2(
            text,text,bigint,bigint,text
          );
        DROP FUNCTION IF EXISTS
          public.reconcile_course_trigger_matches_v2(
            text,text,bigint,bigint,text,jsonb,text
          );
        DROP FUNCTION IF EXISTS
          public.reconcile_negative_label_group_v2(text,text,bigint);
        DROP FUNCTION IF EXISTS public.rebuild_task_plan_v2(text,jsonb);
        DROP FUNCTION IF EXISTS
          public.task_plan_active_match_set_hash_v2(text);
        DROP FUNCTION IF EXISTS public.validate_trigger_match_task_link_v2();
        DROP FUNCTION IF EXISTS public.normalize_trigger_match_v2();
        DROP FUNCTION IF EXISTS public.guard_config_version_contract_v2();
        """
    )
    for name in (
        "ix_trigger_match_v2_course_current",
        "ix_trigger_match_v2_assignment_active_seed",
    ):
        op.execute(sa.text(f"DROP INDEX IF EXISTS public.{name}"))
    for name, type_ in (
        ("ck_trigger_match_v2_output_link", "check"),
        ("ck_trigger_match_v2_typed_seed", "check"),
        ("ck_trigger_match_v2_identity", "check"),
        ("fk_trigger_match_notification_v2", "foreignkey"),
        ("fk_trigger_match_ops_case_v2", "foreignkey"),
        ("fk_trigger_match_task_assignment_v2", "foreignkey"),
    ):
        op.drop_constraint(
            name,
            "personalized_trigger_matches",
            type_=type_,
            schema="public",
        )
    match_columns = (
        "triggering_event_id","projection_generation",
        "source_aggregate_revision","notification_id","ops_case_id",
        "task_assignment_id","is_serving","serving_projection_generation",
        "created_projection_generation","materialization_origin",
        "dedupe_key_sort_bytes","evidence_discriminator_sort_bytes",
        "evidence_discriminator_numeric",
        "evidence_discriminator_type_rank","evidence_discriminator_type",
        "evidence_discriminator","source_appoint_id_sort_bytes",
        "source_appoint_id_numeric","source_appoint_id_type_rank",
        "source_appoint_id_type","source_region_rank",
        "assignment_dedupe_key_sort_bytes","source_ref","output_key",
        "teacher_execution_variant","plan_evidence_hash","plan_evidence",
        "source_candidate_active","threshold_required","seed_rule_rank",
        "assignment_dedupe_key","target_task_code","last_transition_at",
        "match_revision","match_kind","participation_seq",
        "source_appoint_id","source_region",
    )
    for column in match_columns:
        op.drop_column(
            "personalized_trigger_matches", column, schema="public"
        )
    op.drop_index(
        "ix_task_assignment_materialization_seed_v2",
        table_name="task_assignments",
        schema="public",
    )
    for name, type_ in (
        ("ck_task_assignment_materialization_contract_v2", "check"),
        ("fk_task_assignment_copy_version_v2", "foreignkey"),
        ("fk_task_assignment_template_code_v2", "foreignkey"),
    ):
        op.drop_constraint(
            name,"task_assignments",type_=type_,schema="public"
        )
    for column in (
        "plan_evidence_hash","teacher_execution_variant",
        "teacher_copy_config_key","teacher_copy_version_id",
        "eligible_since_at","eligibility_generation",
        "materialization_plan_revision",
        "materialization_template_revision",
        "materialization_seed_payload_hash",
        "materialization_seed_revision","materialization_seed_key",
        "materialization_seed_kind",
    ):
        op.drop_column("task_assignments", column, schema="public")
    op.drop_constraint(
        "uq_task_template_row_identity",
        "task_templates",
        type_="unique",
        schema="public",
    )
    op.drop_constraint(
        "uq_config_version_identity_key",
        "config_versions",
        type_="unique",
        schema="public",
    )
    op.drop_constraint(
        "ck_config_versions_schema_payload_v2",
        "config_versions",
        type_="check",
        schema="public",
    )
    op.drop_constraint(
        "ck_config_versions_key",
        "config_versions",
        type_="check",
        schema="public",
    )
    op.create_check_constraint(
        "ck_config_versions_key",
        "config_versions",
        "config_key IN ('SCORE_GRADUATION','AGENT_POLICY','DELIVERY_POLICY')",
        schema="public",
    )
    op.drop_column("config_versions", "payload_hash", schema="public")
    op.drop_column("config_versions", "schema_version", schema="public")
