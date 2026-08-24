"""install the fail-closed DTS v2 projection read-route cutover gate.

Revision ID: 20260822_95_projection_cutover
Revises: 20260822_94_complaint_catalog_fanout
Create Date: 2026-08-22

This revision intentionally does not claim that the fourteen-result full
shadow materializer exists.  A production PASS requires the database-owned
``dts_v2_full_reconciliation_manifest_v1(timestamptz)`` provider and the v1
compatibility drain provider installed by the following rollout slice.  An
absent provider is reported as UNAVAILABLE and can never be cut over.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260822_95_projection_cutover"
down_revision: Union[str, None] = "20260822_94_complaint_catalog_fanout"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


CUTOVER_ROLE = "tit_growth_app"
ROUTE_CONTRACT_VERSION = "teacher-read-route-v1"
RECONCILIATION_PROTOCOL = "dts-v2-reconciliation-pass-v1"
FULL_RECONCILIATION_PROVIDER = (
    "public.dts_v2_full_reconciliation_manifest_v1(timestamptz)"
)
COMPAT_DIRTY_PROVIDER = (
    "public.dts_v1_compat_dirty_not_complete_count_v1()"
)
RESULT_TYPES: tuple[str, ...] = (
    "TEACHER_WIDE",
    "LESSON_SCORE",
    "LESSON_COMPONENT_SETTLEMENT",
    "FAVORITE_OBSERVATION",
    "FAVORITE_ATTRIBUTION",
    "TRIGGER_MATCH",
    "TASK_PLAN",
    "CASE_PLAN",
    "NOTIFICATION_PLAN",
    "SCORE_ACCOUNT",
    "SCORE_COMPONENT_ACCOUNT",
    "SCORE_ENTRY_PLAN",
    "QUALIFICATION",
    "OUTBOX_COVERAGE",
)
SOURCE_PROFILES: tuple[tuple[str, str], ...] = (
    ("dom", "dom_appoint"),
    ("dom", "dom_complaint"),
    ("dom", "dom_complaint_cate"),
    ("dom", "dom_grading_label"),
    ("dom", "dom_grading_label_log"),
    ("dom", "dom_qa_task_close_camera_record"),
    ("dom", "dom_teacher"),
    ("dom", "dom_teacher_absent_reason"),
    ("dom", "dom_teacher_blacklist"),
    ("dom", "dom_teacher_certification"),
    ("dom", "dom_teacher_class_schedule"),
    ("dom", "dom_teacher_favorite"),
    ("dom", "dom_teacher_penalty"),
    ("dom", "dom_user_complaint"),
    ("dom", "dom_user_teacher_grading"),
    ("ovs", "ovs_appoint"),
    ("ovs", "ovs_complaint"),
    ("ovs", "ovs_grading_label"),
    ("ovs", "ovs_grading_label_log"),
    ("ovs", "ovs_qa_task_close_camera_record"),
    ("ovs", "ovs_teacher_blacklist"),
    ("ovs", "ovs_teacher_favorite"),
    ("ovs", "ovs_user_complaint"),
    ("ovs", "ovs_user_teacher_grading"),
)


def _preflight_and_role() -> None:
    op.execute(
        f"""
        DO $projection_cutover_preflight$
        DECLARE relation_name text;
        BEGIN
          FOREACH relation_name IN ARRAY ARRAY[
            'dts_pipeline_control','dts_pipeline_bootstrap_audits',
            'dts_ingest_checkpoints','dts_source_partition_epochs',
            'dts_source_rows','dts_dirty_keys','dts_ingest_issues',
            'dts_source_scope_states','outbox_events',
            'course_favorite_observations','personalized_trigger_matches',
            'task_assignments','notifications','ops_cases','task_templates',
            'complaint_rule_imports','teacher_scorecard_current',
            'teacher_lesson_score_current',
            'teacher_scorecard_v1_compat_v1','teacher_scorecard_v2_v1',
            'teacher_lesson_score_v1_compat_v1',
            'teacher_lesson_score_v2_v1'
          ] LOOP
            IF to_regclass('public.' || relation_name) IS NULL THEN
              RAISE EXCEPTION
                'DTS_V2_PROJECTION_CUTOVER_PREREQUISITE_MISSING:%',
                relation_name;
            END IF;
          END LOOP;
          IF to_regprocedure(
               'public.dts_canonical_json_sha256_v1(jsonb)'
             ) IS NULL
             OR to_regprocedure(
               'public.dts_v2_json_has_forbidden_student_key(jsonb)'
             ) IS NULL THEN
            RAISE EXCEPTION
              'DTS_V2_PROJECTION_CUTOVER_PREREQUISITE_MISSING';
          END IF;
          IF to_regclass('public.dts_projection_read_routes') IS NOT NULL
             OR to_regprocedure(
               'public.switch_dts_projection_mode_v2(text,bigint,bigint,text)'
             ) IS NOT NULL THEN
            RAISE EXCEPTION 'DTS_V2_PROJECTION_CUTOVER_ALREADY_INSTALLED';
          END IF;
        END
        $projection_cutover_preflight$;

        DO $projection_cutover_role$
        BEGIN
          IF to_regrole('{CUTOVER_ROLE}') IS NULL THEN
            RAISE EXCEPTION
              'required application role is missing: {CUTOVER_ROLE}';
          END IF;
          IF EXISTS (
            SELECT 1 FROM pg_roles WHERE rolname='{CUTOVER_ROLE}'
              AND (NOT rolcanlogin OR rolinherit OR rolsuper OR rolcreatedb
                   OR rolcreaterole OR rolreplication OR rolbypassrls)
          ) THEN
            RAISE EXCEPTION
              '{CUTOVER_ROLE} must be a restricted NOINHERIT LOGIN role';
          END IF;
        END
        $projection_cutover_role$;
        """
    )


def _create_tables() -> None:
    json_value = sa.JSON().with_variant(
        postgresql.JSONB(astext_type=sa.Text()), "postgresql"
    )
    op.create_table(
        "dts_projection_read_routes",
        sa.Column("route_id", sa.String(length=16), nullable=False),
        sa.Column("active_projection", sa.String(length=16), nullable=False),
        sa.Column(
            "route_contract_version", sa.String(length=64), nullable=False
        ),
        sa.Column(
            "row_version", sa.BigInteger(), nullable=False,
            server_default=sa.text("1")
        ),
        sa.Column("switched_by_run_id", sa.String(length=128)),
        sa.Column("switched_at", sa.DateTime(timezone=True)),
        sa.Column("switched_by", sa.String(length=128), nullable=False),
        sa.PrimaryKeyConstraint(
            "route_id", name="pk_dts_projection_read_routes"
        ),
        sa.CheckConstraint(
            "route_id='PRIMARY'",
            name="ck_dts_projection_read_route_singleton",
        ),
        sa.CheckConstraint(
            "active_projection IN ('V1_COMPAT','V2') AND row_version>=1",
            name="ck_dts_projection_read_route_state",
        ),
        sa.CheckConstraint(
            f"route_contract_version='{ROUTE_CONTRACT_VERSION}'",
            name="ck_dts_projection_read_route_contract",
        ),
        sa.CheckConstraint(
            "(row_version=1 AND active_projection='V1_COMPAT' "
            "AND switched_by_run_id IS NULL AND switched_at IS NULL) OR "
            "(row_version>1 AND switched_by_run_id IS NOT NULL "
            "AND switched_at IS NOT NULL)",
            name="ck_dts_projection_read_route_switch_shape",
        ),
        schema="public",
    )
    op.create_table(
        "dts_source_profile_approvals_v2",
        sa.Column("manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("manifest_version", sa.Integer(), nullable=False),
        sa.Column("profile_vector", json_value, nullable=False),
        sa.Column("profile_vector_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "approved_by_change_id", sa.String(length=160), nullable=False
        ),
        sa.Column(
            "approved_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("clock_timestamp()")
        ),
        sa.Column("approved_by", sa.String(length=128), nullable=False),
        sa.PrimaryKeyConstraint(
            "manifest_sha256", name="pk_dts_source_profile_approvals_v2"
        ),
        sa.CheckConstraint(
            "manifest_sha256 ~ '^[0-9a-f]{64}$' "
            "AND profile_vector_hash=manifest_sha256 "
            "AND manifest_version=1 AND status='APPROVED' "
            "AND jsonb_typeof(profile_vector)='array' "
            "AND btrim(approved_by_change_id)<>'' "
            "AND btrim(approved_by)<>''",
            name="ck_dts_source_profile_approval_shape_v2",
        ),
        schema="public",
    )
    op.create_table(
        "dts_v2_reconciliation_runs",
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("base_mode", sa.String(length=32), nullable=False),
        sa.Column("base_control_version", sa.BigInteger(), nullable=False),
        sa.Column("base_route_version", sa.BigInteger(), nullable=False),
        sa.Column(
            "target_projection_generation", sa.BigInteger(), nullable=False
        ),
        sa.Column("source_profile_manifest_version", sa.Integer(), nullable=False),
        sa.Column(
            "source_profile_manifest_sha256", sa.String(length=64), nullable=False
        ),
        sa.Column("source_profile_vector", json_value, nullable=False),
        sa.Column("source_profile_vector_hash", sa.String(length=64), nullable=False),
        sa.Column("source_fence_vector", json_value, nullable=False),
        sa.Column("source_fence_hash", sa.String(length=64), nullable=False),
        sa.Column("full_reconciliation_manifest", json_value, nullable=False),
        sa.Column(
            "full_reconciliation_manifest_hash", sa.String(length=64), nullable=False
        ),
        sa.Column("v1_result_manifest", json_value, nullable=False),
        sa.Column("v1_result_manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("v2_result_manifest", json_value, nullable=False),
        sa.Column("v2_result_manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("legacy_output_manifest", json_value, nullable=False),
        sa.Column("legacy_output_manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("technical_gate_manifest", json_value, nullable=False),
        sa.Column("technical_gate_manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("evaluation_as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evaluation_business_date_beijing", sa.Date(), nullable=False),
        sa.Column("result_status", sa.String(length=16), nullable=False),
        sa.Column(
            "recorded_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("clock_timestamp()")
        ),
        sa.Column("recorded_by", sa.String(length=128), nullable=False),
        sa.PrimaryKeyConstraint("run_id", name="pk_dts_v2_reconciliation_runs"),
        sa.CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$' "
            "AND source_profile_manifest_sha256 ~ '^[0-9a-f]{64}$' "
            "AND source_profile_vector_hash ~ '^[0-9a-f]{64}$' "
            "AND source_fence_hash ~ '^[0-9a-f]{64}$' "
            "AND full_reconciliation_manifest_hash ~ '^[0-9a-f]{64}$' "
            "AND v1_result_manifest_hash ~ '^[0-9a-f]{64}$' "
            "AND v2_result_manifest_hash ~ '^[0-9a-f]{64}$' "
            "AND legacy_output_manifest_hash ~ '^[0-9a-f]{64}$' "
            "AND technical_gate_manifest_hash ~ '^[0-9a-f]{64}$' "
            "AND base_control_version>=1 AND base_route_version>=1 "
            "AND target_projection_generation>=1 "
            "AND source_profile_manifest_version=1 "
            "AND base_mode IN ('V1_COMPAT_DUAL_CAPTURE','ROLLED_BACK') "
            "AND result_status='PASS'",
            name="ck_dts_v2_reconciliation_run_shape",
        ),
        schema="public",
    )
    op.create_table(
        "dts_projection_switch_audits",
        sa.Column("switch_run_id", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("reconciliation_run_id", sa.String(length=128)),
        sa.Column("from_mode", sa.String(length=32), nullable=False),
        sa.Column("to_mode", sa.String(length=32), nullable=False),
        sa.Column("from_projection", sa.String(length=16), nullable=False),
        sa.Column("to_projection", sa.String(length=16), nullable=False),
        sa.Column("projection_generation", sa.BigInteger(), nullable=False),
        sa.Column("before_control_version", sa.BigInteger(), nullable=False),
        sa.Column("after_control_version", sa.BigInteger(), nullable=False),
        sa.Column("before_route_version", sa.BigInteger(), nullable=False),
        sa.Column("after_route_version", sa.BigInteger(), nullable=False),
        sa.Column("source_fence_hash", sa.String(length=64), nullable=False),
        sa.Column("response_payload", json_value, nullable=False),
        sa.Column("response_hash", sa.String(length=64), nullable=False),
        sa.Column("result_status", sa.String(length=16), nullable=False),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("clock_timestamp()")
        ),
        sa.Column("occurred_by", sa.String(length=128), nullable=False),
        sa.PrimaryKeyConstraint(
            "switch_run_id", name="pk_dts_projection_switch_audits"
        ),
        sa.ForeignKeyConstraint(
            ["reconciliation_run_id"],
            ["public.dts_v2_reconciliation_runs.run_id"],
            name="fk_dts_projection_switch_reconciliation",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$' "
            "AND source_fence_hash ~ '^[0-9a-f]{64}$' "
            "AND response_hash ~ '^[0-9a-f]{64}$' "
            "AND projection_generation>=0 "
            "AND after_control_version=before_control_version+1 "
            "AND after_route_version=before_route_version+1 "
            "AND result_status='APPLIED' "
            "AND ((to_mode='V2_PRIMARY' AND to_projection='V2' "
            "AND reconciliation_run_id=switch_run_id) OR "
            "(to_mode='ROLLED_BACK' AND to_projection='V1_COMPAT' "
            "AND reconciliation_run_id IS NULL))",
            name="ck_dts_projection_switch_audit_shape",
        ),
        schema="public",
    )


def _install_table_guards_and_seed_route() -> None:
    op.execute(
        f"""
        CREATE FUNCTION public.guard_dts_projection_route_v1()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path=pg_catalog,public
        AS $function$
        BEGIN
          IF current_setting('tit.dts_projection_switch_v1',true)<>'on' THEN
            RAISE EXCEPTION 'DTS_PROJECTION_ROUTE_COMMAND_REQUIRED'
              USING ERRCODE='42501';
          END IF;
          IF TG_OP='DELETE' THEN
            RAISE EXCEPTION 'DTS_PROJECTION_ROUTE_DELETE_FORBIDDEN'
              USING ERRCODE='42501';
          ELSIF TG_OP='INSERT' THEN
            RETURN NEW;
          END IF;
          IF NEW.route_id IS DISTINCT FROM OLD.route_id
             OR NEW.route_contract_version IS DISTINCT FROM
                  OLD.route_contract_version
             OR NEW.row_version<>OLD.row_version+1 THEN
            RAISE EXCEPTION 'DTS_PROJECTION_ROUTE_VERSION_INVALID'
              USING ERRCODE='23514';
          END IF;
          RETURN NEW;
        END
        $function$;

        CREATE FUNCTION public.guard_dts_source_profile_approval_v2()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path=pg_catalog,public
        AS $function$
        BEGIN
          IF TG_OP<>'INSERT' THEN
            RAISE EXCEPTION 'DTS_SOURCE_PROFILE_APPROVAL_IMMUTABLE'
              USING ERRCODE='42501';
          END IF;
          IF current_setting(
               'tit.dts_source_profile_approval_migration',true
             )<>'on' THEN
            RAISE EXCEPTION 'DTS_SOURCE_PROFILE_APPROVAL_MIGRATION_REQUIRED'
              USING ERRCODE='42501';
          END IF;
          IF NOT public.dts_v2_source_profile_vector_valid_v1(
               NEW.profile_vector
             )
             OR NEW.profile_vector_hash<>public.dts_canonical_json_sha256_v1(
                  NEW.profile_vector
                ) THEN
            RAISE EXCEPTION 'DTS_SOURCE_PROFILE_APPROVAL_INVALID'
              USING ERRCODE='23514';
          END IF;
          RETURN NEW;
        END
        $function$;

        CREATE FUNCTION public.guard_dts_projection_cutover_evidence_v1()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path=pg_catalog,public
        AS $function$
        BEGIN
          IF TG_OP<>'INSERT' OR current_setting(
               'tit.dts_projection_cutover_evidence_v1',true
             )<>'on' THEN
            RAISE EXCEPTION 'DTS_PROJECTION_CUTOVER_EVIDENCE_IMMUTABLE'
              USING ERRCODE='42501';
          END IF;
          RETURN NEW;
        END
        $function$;

        REVOKE ALL ON FUNCTION
          public.guard_dts_projection_route_v1(),
          public.guard_dts_source_profile_approval_v2(),
          public.guard_dts_projection_cutover_evidence_v1()
        FROM PUBLIC;

        CREATE TRIGGER trg_guard_dts_projection_route_v1
        BEFORE INSERT OR UPDATE OR DELETE
        ON public.dts_projection_read_routes
        FOR EACH ROW EXECUTE FUNCTION public.guard_dts_projection_route_v1();
        CREATE TRIGGER trg_guard_dts_source_profile_approval_v2
        BEFORE INSERT OR UPDATE OR DELETE
        ON public.dts_source_profile_approvals_v2
        FOR EACH ROW
        EXECUTE FUNCTION public.guard_dts_source_profile_approval_v2();
        CREATE TRIGGER trg_guard_dts_v2_reconciliation_run_v1
        BEFORE INSERT OR UPDATE OR DELETE
        ON public.dts_v2_reconciliation_runs
        FOR EACH ROW
        EXECUTE FUNCTION public.guard_dts_projection_cutover_evidence_v1();
        CREATE TRIGGER trg_guard_dts_projection_switch_audit_v1
        BEFORE INSERT OR UPDATE OR DELETE
        ON public.dts_projection_switch_audits
        FOR EACH ROW
        EXECUTE FUNCTION public.guard_dts_projection_cutover_evidence_v1();

        SELECT set_config('tit.dts_projection_switch_v1','on',true);
        INSERT INTO public.dts_projection_read_routes(
          route_id,active_projection,route_contract_version,row_version,
          switched_by_run_id,switched_at,switched_by
        ) VALUES (
          'PRIMARY','V1_COMPAT','{ROUTE_CONTRACT_VERSION}',1,
          NULL,NULL,'alembic:20260822_95_projection_cutover'
        );
        SELECT set_config('tit.dts_projection_switch_v1','off',true);
        """
    )


def _install_validation_helpers() -> None:
    source_values = ",".join(
        f"('{region}','{table}')" for region, table in SOURCE_PROFILES
    )
    result_values = ",".join(
        f"({position},'{result_type}')"
        for position, result_type in enumerate(RESULT_TYPES, start=1)
    )
    op.execute(
        f"""
        CREATE FUNCTION public.dts_v2_source_profile_vector_valid_v1(
          p_vector jsonb
        ) RETURNS boolean
        LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
        SET search_path=pg_catalog,public
        AS $function$
          WITH expected(position,source_region,source_table) AS (
            VALUES {','.join(
                f"({position},'{region}','{table}')"
                for position, (region, table) in enumerate(
                    SOURCE_PROFILES, start=1
                )
            )}
          ), supplied AS (
            SELECT ordinal::integer AS position,value
            FROM jsonb_array_elements(p_vector) WITH ORDINALITY item(
              value,ordinal
            )
          )
          SELECT jsonb_typeof(p_vector)='array'
             AND jsonb_array_length(p_vector)={len(SOURCE_PROFILES)}
             AND NOT EXISTS (
               SELECT 1 FROM expected
               LEFT JOIN supplied USING(position)
               WHERE supplied.value IS NULL
                  OR jsonb_typeof(supplied.value)<>'object'
                  OR supplied.value<>jsonb_build_object(
                       'source_region',expected.source_region,
                       'source_table',expected.source_table,
                       'source_schema_profile_id',
                         supplied.value->>'source_schema_profile_id'
                     )
                  OR supplied.value->>'source_region'<>
                       expected.source_region
                  OR supplied.value->>'source_table'<>
                       expected.source_table
                  OR supplied.value->>'source_schema_profile_id'
                       !~ '^dts-source-schema:v2:[0-9a-f]{{64}}$'
             )
        $function$;

        CREATE FUNCTION public.dts_v2_result_manifest_valid_v1(
          p_manifest jsonb
        ) RETURNS boolean
        LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
        SET search_path=pg_catalog,public
        AS $function$
          WITH expected(position,result_type) AS (
            VALUES {result_values}
          ), supplied AS (
            SELECT ordinal::integer AS position,value
            FROM jsonb_array_elements(p_manifest) WITH ORDINALITY item(
              value,ordinal
            )
          )
          SELECT jsonb_typeof(p_manifest)='array'
             AND jsonb_array_length(p_manifest)={len(RESULT_TYPES)}
             AND NOT EXISTS (
               SELECT 1 FROM expected
               LEFT JOIN supplied USING(position)
               WHERE supplied.value IS NULL
                  OR jsonb_typeof(supplied.value)<>'object'
                  OR supplied.value<>jsonb_build_object(
                       'result_type',expected.result_type,
                       'row_count',(supplied.value->>'row_count')::numeric,
                       'content_hash',supplied.value->>'content_hash'
                     )
                  OR supplied.value->>'result_type'<>expected.result_type
                  OR supplied.value->>'row_count' !~ '^(0|[1-9][0-9]*)$'
                  OR (supplied.value->>'row_count')::numeric>
                       9223372036854775807
                  OR supplied.value->>'content_hash'
                       !~ '^[0-9a-f]{{64}}$'
             )
        $function$;

        CREATE FUNCTION public.dts_v2_current_fence_vector_v1()
        RETURNS jsonb
        LANGUAGE plpgsql STABLE SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE control_row public.dts_pipeline_control%ROWTYPE;
        DECLARE route_count integer;
        DECLARE fence_vector jsonb;
        BEGIN
          SELECT * INTO STRICT control_row
          FROM public.dts_pipeline_control WHERE control_id='PRIMARY';
          route_count:=jsonb_array_length(control_row.initial_h0_vector);
          IF route_count<1
             OR (SELECT count(*) FROM public.dts_ingest_checkpoints)<>
                  route_count
             OR EXISTS (
               SELECT 1 FROM jsonb_array_elements(
                 control_row.initial_h0_vector
               ) initial(value)
               LEFT JOIN public.dts_ingest_checkpoints checkpoint
                 ON checkpoint.source_region=initial.value->>'source_region'
                AND checkpoint.topic=initial.value->>'topic'
                AND checkpoint.partition_id=
                    (initial.value->>'partition_id')::integer
               LEFT JOIN public.dts_source_partition_epochs epoch
                 ON epoch.source_region=checkpoint.source_region
                AND epoch.source_partition_epoch_id=
                    checkpoint.source_partition_epoch_id
                AND epoch.topic=checkpoint.topic
                AND epoch.partition_id=checkpoint.partition_id
               WHERE checkpoint.source_region IS NULL
                  OR checkpoint.source_partition_epoch_id IS NULL
                  OR checkpoint.consumer_group IS DISTINCT FROM
                       initial.value->>'consumer_group'
                  OR checkpoint.checkpoint_row_version IS NULL
                  OR checkpoint.checkpoint_row_version<1
                  OR checkpoint.is_current_epoch IS DISTINCT FROM true
                  OR checkpoint.next_offset<
                       (initial.value->>'current_next_offset')::bigint
                  OR epoch.source_region IS NULL
                  OR epoch.epoch_kind<>'BROKER' OR epoch.status<>'ACTIVE'
                  OR epoch.stream_generation_id IS DISTINCT FROM
                       initial.value->>'stream_generation_id'
                  OR epoch.epoch_opening_id IS DISTINCT FROM
                       initial.value->>'epoch_opening_id'
             ) THEN
            RAISE EXCEPTION 'DTS_V2_SOURCE_FENCE_UNAVAILABLE'
              USING ERRCODE='23514';
          END IF;
          SELECT jsonb_agg(jsonb_build_object(
                   'source_region',checkpoint.source_region,
                   'topic',checkpoint.topic,
                   'partition_id',checkpoint.partition_id,
                   'source_partition_epoch_id',
                     checkpoint.source_partition_epoch_id,
                   'consumer_group',checkpoint.consumer_group,
                   'stream_generation_id',epoch.stream_generation_id,
                   'epoch_opening_id',epoch.epoch_opening_id,
                   'current_next_offset',checkpoint.next_offset,
                   'checkpoint_row_version',checkpoint.checkpoint_row_version
                 ) ORDER BY checkpoint.source_region COLLATE "C",
                   checkpoint.topic COLLATE "C",checkpoint.partition_id)
          INTO fence_vector
          FROM public.dts_ingest_checkpoints checkpoint
          JOIN public.dts_source_partition_epochs epoch
            ON epoch.source_region=checkpoint.source_region
           AND epoch.source_partition_epoch_id=
               checkpoint.source_partition_epoch_id
           AND epoch.topic=checkpoint.topic
           AND epoch.partition_id=checkpoint.partition_id;
          RETURN fence_vector;
        EXCEPTION WHEN no_data_found OR too_many_rows THEN
          RAISE EXCEPTION 'DTS_V2_SOURCE_FENCE_UNAVAILABLE'
            USING ERRCODE='23514';
        END
        $function$;

        CREATE FUNCTION public.dts_v2_source_rows_match_profile_v1(
          p_vector jsonb
        ) RETURNS boolean
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
          SELECT public.dts_v2_source_profile_vector_valid_v1(p_vector)
             AND NOT EXISTS (
               SELECT 1
               FROM public.dts_source_rows source
               JOIN jsonb_array_elements(p_vector) profile(value)
                 ON profile.value->>'source_region'=source.source_region
                AND profile.value->>'source_table'=source.source_table
               WHERE source.provenance_state IS DISTINCT FROM 'V2_CONFIRMED'
                  OR source.source_schema_profile_id IS DISTINCT FROM
                       profile.value->>'source_schema_profile_id'
             )
        $function$;

        REVOKE ALL ON FUNCTION
          public.dts_v2_source_profile_vector_valid_v1(jsonb),
          public.dts_v2_result_manifest_valid_v1(jsonb),
          public.dts_v2_current_fence_vector_v1(),
          public.dts_v2_source_rows_match_profile_v1(jsonb)
        FROM PUBLIC;
        """
    )


def _install_legacy_and_technical_helpers() -> None:
    source_values = ",".join(
        f"('{region}','{table}')" for region, table in SOURCE_PROFILES
    )
    op.execute(
        f"""
        CREATE FUNCTION public.dts_v2_legacy_output_mapping_manifest_v1()
        RETURNS jsonb
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
          WITH candidates AS (
            SELECT match.trigger_match_id,match.output_type,
              match.output_key,match.output_id,
              CASE match.output_type
                WHEN 'TEACHER_TASK' THEN match.task_assignment_id
                WHEN 'OPS_CASE' THEN match.ops_case_id
                WHEN 'NOTIFICATION' THEN match.notification_id
              END AS typed_output_id,
              CASE match.output_type
                WHEN 'TEACHER_TASK' THEN match.assignment_dedupe_key
                ELSE match.output_key
              END AS canonical_output_key,
              CASE match.output_type
                WHEN 'TEACHER_TASK' THEN assignment.dedupe_key
                WHEN 'OPS_CASE' THEN case_row.source_ref
                WHEN 'NOTIFICATION' THEN notification.source_ref
              END AS physical_output_key,
              CASE match.output_type
                WHEN 'TEACHER_TASK' THEN assignment.assignment_id
                WHEN 'OPS_CASE' THEN case_row.case_id
                WHEN 'NOTIFICATION' THEN notification.notification_id
              END AS physical_output_id,
              match.match_status,match.materialization_origin
            FROM public.personalized_trigger_matches match
            LEFT JOIN public.task_assignments assignment
              ON assignment.assignment_id=match.task_assignment_id
            LEFT JOIN public.ops_cases case_row
              ON case_row.case_id=match.ops_case_id
            LEFT JOIN public.notifications notification
              ON notification.notification_id=match.notification_id
            WHERE match.output_type IN (
              'TEACHER_TASK','OPS_CASE','NOTIFICATION'
            ) AND (
              match.materialization_origin='LEGACY_REUSED'
              OR match.output_id IS NOT NULL
              OR match.task_assignment_id IS NOT NULL
              OR match.ops_case_id IS NOT NULL
              OR match.notification_id IS NOT NULL
              OR match.match_status='MATERIALIZED'
            )
          ), issues AS (
            SELECT count(*) FILTER (WHERE
              output_id IS NULL OR typed_output_id IS NULL
              OR physical_output_id IS NULL
              OR output_id<>typed_output_id
              OR typed_output_id<>physical_output_id
              OR canonical_output_key IS NULL
              OR physical_output_key IS DISTINCT FROM canonical_output_key
            )::bigint AS missing_count,
            (SELECT count(*) FROM (
              SELECT output_type,canonical_output_key
              FROM candidates
              WHERE canonical_output_key IS NOT NULL
                AND typed_output_id IS NOT NULL
              GROUP BY output_type,canonical_output_key
              HAVING count(DISTINCT typed_output_id)>1
            ) conflict)::bigint AS conflict_count,
            (SELECT count(*) FROM (
              SELECT output_type,typed_output_id
              FROM candidates WHERE typed_output_id IS NOT NULL
              GROUP BY output_type,typed_output_id
              HAVING count(DISTINCT canonical_output_key)>1
            ) duplicate)::bigint AS duplicate_count,
            count(*)::bigint AS candidate_count,
            count(*) FILTER (WHERE
              typed_output_id IS NOT NULL
              AND physical_output_id=typed_output_id
              AND output_id=typed_output_id
              AND physical_output_key IS NOT DISTINCT FROM
                  canonical_output_key
            )::bigint AS mapped_count
            FROM candidates
          ), mapping AS (
            SELECT coalesce(jsonb_agg(jsonb_build_object(
              'trigger_match_id',trigger_match_id,
              'output_type',output_type,
              'canonical_output_key',canonical_output_key,
              'typed_output_id',typed_output_id,
              'physical_output_key',physical_output_key,
              'match_status',match_status,
              'materialization_origin',materialization_origin
            ) ORDER BY output_type COLLATE "C",
              canonical_output_key COLLATE "C",
              trigger_match_id COLLATE "C"),'[]'::jsonb) AS value
            FROM candidates
          )
          SELECT jsonb_build_object(
            'protocol_version','dts-v2-legacy-output-mapping-v1',
            'candidate_count',issues.candidate_count,
            'mapped_count',issues.mapped_count,
            'missing_count',issues.missing_count,
            'conflict_count',issues.conflict_count,
            'duplicate_count',issues.duplicate_count,
            'issue_count',issues.missing_count+issues.conflict_count+
              issues.duplicate_count,
            'mapping_hash',public.dts_canonical_json_sha256_v1(mapping.value)
          ) FROM issues,mapping
        $function$;

        CREATE FUNCTION public.dts_v2_cutover_technical_gate_manifest_v1(
          p_evaluation_as_of timestamptz
        ) RETURNS jsonb
        LANGUAGE plpgsql STABLE SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE compat_provider text:='UNAVAILABLE';
        DECLARE compat_count bigint;
        DECLARE full_provider text:='UNAVAILABLE';
        DECLARE dirty_count bigint;
        DECLARE outbox_pending bigint;
        DECLARE outbox_dead bigint;
        DECLARE issue_count bigint;
        DECLARE incomplete_scope bigint;
        DECLARE favorite_count bigint;
        DECLARE legacy_count bigint;
        DECLARE catalog_count bigint;
        DECLARE template_count bigint;
        BEGIN
          IF p_evaluation_as_of IS NULL
             OR p_evaluation_as_of>clock_timestamp() THEN
            RAISE EXCEPTION 'DTS_V2_TECHNICAL_GATE_TIME_INVALID'
              USING ERRCODE='22023';
          END IF;
          IF to_regprocedure('{COMPAT_DIRTY_PROVIDER}') IS NOT NULL THEN
            EXECUTE
              'SELECT public.dts_v1_compat_dirty_not_complete_count_v1()'
              INTO compat_count;
            IF compat_count IS NULL OR compat_count<0 THEN
              RAISE EXCEPTION 'DTS_V1_COMPAT_DIRTY_PROVIDER_INVALID'
                USING ERRCODE='23514';
            END IF;
            compat_provider:='AVAILABLE';
          END IF;
          IF to_regprocedure('{FULL_RECONCILIATION_PROVIDER}') IS NOT NULL THEN
            full_provider:='AVAILABLE';
          END IF;
          SELECT count(*) INTO dirty_count
          FROM public.dts_dirty_keys WHERE status<>'COMPLETED';
          SELECT count(*) FILTER (WHERE status='PENDING'),
                 count(*) FILTER (WHERE status='DEAD_LETTER')
          INTO outbox_pending,outbox_dead
          FROM public.outbox_events
          WHERE event_type IN (
            'source_wide.changed.v2','task.materialization.requested.v2'
          ) AND status<>'PUBLISHED';
          SELECT count(*) INTO issue_count
          FROM public.dts_ingest_issues WHERE status='OPEN';
          WITH required(source_region,source_table) AS (
            VALUES {source_values}
          )
          SELECT count(*) INTO incomplete_scope
          FROM required
          LEFT JOIN public.dts_source_scope_states scope
            ON scope.source_region=required.source_region
           AND scope.source_table=required.source_table
           AND scope.scope_kind='CURRENT'
           AND scope.scope_level='GLOBAL' AND scope.scope_key='*'
           AND scope.state='COMPLETE'
           AND scope.active_snapshot_id IS NOT NULL
          WHERE scope.source_region IS NULL;
          SELECT count(*) INTO favorite_count
          FROM public.course_favorite_observations
          WHERE observed_at<=p_evaluation_as_of
            AND status NOT IN (
              'CONFIRMED_TRUE','CONFIRMED_FALSE','INVALIDATED','VOIDED'
            );
          legacy_count:=(
            public.dts_v2_legacy_output_mapping_manifest_v1()
              ->>'issue_count'
          )::bigint;
          SELECT count(*) INTO catalog_count
          FROM public.complaint_rule_imports WHERE status='PUBLISHED';
          SELECT count(*) INTO template_count
          FROM public.task_templates WHERE status='PUBLISHED';
          RETURN jsonb_build_object(
            'protocol_version','dts-v2-cutover-technical-gate-v1',
            'full_reconciliation_provider',full_provider,
            'compat_dirty_provider',compat_provider,
            'dirty_not_complete_count',dirty_count,
            'compat_dirty_not_complete_count',compat_count,
            'v2_outbox_pending_count',outbox_pending,
            'v2_outbox_dead_letter_count',outbox_dead,
            'open_ingest_issue_count',issue_count,
            'incomplete_source_scope_count',incomplete_scope,
            'due_favorite_unsettled_count',favorite_count,
            'legacy_output_issue_count',legacy_count,
            'published_complaint_catalog_count',catalog_count,
            'published_task_template_count',template_count
          );
        END
        $function$;

        CREATE FUNCTION public.dts_v2_technical_gate_passes_v1(
          p_manifest jsonb
        ) RETURNS boolean
        LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
        SET search_path=pg_catalog,public
        AS $function$
          SELECT p_manifest=jsonb_build_object(
            'protocol_version','dts-v2-cutover-technical-gate-v1',
            'full_reconciliation_provider','AVAILABLE',
            'compat_dirty_provider','AVAILABLE',
            'dirty_not_complete_count',0,
            'compat_dirty_not_complete_count',0,
            'v2_outbox_pending_count',0,
            'v2_outbox_dead_letter_count',0,
            'open_ingest_issue_count',0,
            'incomplete_source_scope_count',0,
            'due_favorite_unsettled_count',0,
            'legacy_output_issue_count',0,
            'published_complaint_catalog_count',1,
            'published_task_template_count',14
          )
        $function$;

        REVOKE ALL ON FUNCTION
          public.dts_v2_legacy_output_mapping_manifest_v1(),
          public.dts_v2_cutover_technical_gate_manifest_v1(timestamptz),
          public.dts_v2_technical_gate_passes_v1(jsonb)
        FROM PUBLIC;
        """
    )


def _install_full_reconciliation_provider_adapter() -> None:
    result_type_array = ",".join(f"'{item}'" for item in RESULT_TYPES)
    op.execute(
        f"""
        CREATE FUNCTION public.dts_v2_read_full_reconciliation_manifest_v1(
          p_evaluation_as_of timestamptz,
          p_source_fence_hash text,
          p_source_profile_manifest_sha256 text
        ) RETURNS jsonb
        LANGUAGE plpgsql STABLE SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE manifest jsonb;
        BEGIN
          IF to_regprocedure('{FULL_RECONCILIATION_PROVIDER}') IS NULL THEN
            RAISE EXCEPTION 'DTS_V2_FULL_RECONCILIATION_UNAVAILABLE'
              USING ERRCODE='55000';
          END IF;
          EXECUTE
            'SELECT public.dts_v2_full_reconciliation_manifest_v1($1)'
            INTO manifest USING p_evaluation_as_of;
          IF jsonb_typeof(manifest)<>'object'
             OR manifest<>jsonb_build_object(
               'protocol_version',
                 'dts-v2-full-reconciliation-manifest-v1',
               'evaluation_as_of',manifest->>'evaluation_as_of',
               'source_fence_hash',manifest->>'source_fence_hash',
               'source_profile_manifest_sha256',
                 manifest->>'source_profile_manifest_sha256',
               'result_types',manifest->'result_types',
               'v1_result_manifest',manifest->'v1_result_manifest',
               'v2_result_manifest',manifest->'v2_result_manifest'
             )
             OR manifest->>'evaluation_as_of'<>to_char(
                  p_evaluation_as_of AT TIME ZONE 'UTC',
                  'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
                )
             OR manifest->>'source_fence_hash'<>p_source_fence_hash
             OR manifest->>'source_profile_manifest_sha256'<>
                  p_source_profile_manifest_sha256
             OR manifest->'result_types'<>to_jsonb(
                  ARRAY[{result_type_array}]::text[]
                )
             OR NOT public.dts_v2_result_manifest_valid_v1(
                  manifest->'v1_result_manifest'
                )
             OR NOT public.dts_v2_result_manifest_valid_v1(
                  manifest->'v2_result_manifest'
                )
             OR manifest->'v1_result_manifest'<>
                  manifest->'v2_result_manifest' THEN
            RAISE EXCEPTION 'DTS_V2_FULL_RECONCILIATION_INVALID'
              USING ERRCODE='23514';
          END IF;
          RETURN manifest;
        END
        $function$;
        REVOKE ALL ON FUNCTION
          public.dts_v2_read_full_reconciliation_manifest_v1(
            timestamptz,text,text
          )
        FROM PUBLIC;
        """
    )


def _install_reconciliation_commands() -> None:
    op.execute(
        f"""
        CREATE FUNCTION public.dts_projection_cutover_actor_allowed_v1()
        RETURNS boolean
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
          SELECT coalesce(
                   nullif(current_setting('role',true),'none'),session_user
                 )='{CUTOVER_ROLE}'
              OR coalesce(
                   nullif(current_setting('role',true),'none'),session_user
                 )=(
                   SELECT pg_get_userbyid(relowner) FROM pg_class
                   WHERE oid='public.dts_projection_read_routes'::regclass
                 )
        $function$;
        REVOKE ALL ON FUNCTION
          public.dts_projection_cutover_actor_allowed_v1()
        FROM PUBLIC;

        CREATE FUNCTION public.preview_dts_v2_reconciliation_evidence_v1(
          p_evaluation_as_of timestamptz,
          p_source_profile_manifest_sha256 text
        ) RETURNS jsonb
        LANGUAGE plpgsql VOLATILE SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE control_row public.dts_pipeline_control%ROWTYPE;
        DECLARE route_row public.dts_projection_read_routes%ROWTYPE;
        DECLARE approval_row public.dts_source_profile_approvals_v2%ROWTYPE;
        DECLARE fence_vector jsonb;
        DECLARE fence_hash text;
        DECLARE full_manifest jsonb;
        DECLARE legacy_manifest jsonb;
        DECLARE technical_manifest jsonb;
        BEGIN
          IF NOT public.dts_projection_cutover_actor_allowed_v1() THEN
            RAISE EXCEPTION 'DTS_PROJECTION_CUTOVER_ROLE_REQUIRED'
              USING ERRCODE='42501';
          END IF;
          PERFORM pg_advisory_xact_lock_shared(
            hashtextextended('tit:dts-v2-cutover',0)
          );
          SELECT * INTO STRICT control_row
          FROM public.dts_pipeline_control WHERE control_id='PRIMARY';
          SELECT * INTO STRICT route_row
          FROM public.dts_projection_read_routes WHERE route_id='PRIMARY';
          IF control_row.mode NOT IN (
               'V1_COMPAT_DUAL_CAPTURE','ROLLED_BACK'
             ) OR route_row.active_projection<>'V1_COMPAT'
             OR control_row.time_catchup_status='PENDING'
             OR p_evaluation_as_of IS NULL
             OR p_evaluation_as_of>clock_timestamp() THEN
            RAISE EXCEPTION 'DTS_V2_RECONCILIATION_BASE_STATE_INVALID'
              USING ERRCODE='55000';
          END IF;
          SELECT * INTO STRICT approval_row
          FROM public.dts_source_profile_approvals_v2
          WHERE manifest_sha256=p_source_profile_manifest_sha256
            AND status='APPROVED';
          IF approval_row.manifest_version<>1
             OR approval_row.profile_vector_hash<>
                  public.dts_canonical_json_sha256_v1(
                    approval_row.profile_vector
                  )
             OR NOT public.dts_v2_source_profile_vector_valid_v1(
                  approval_row.profile_vector
                )
             OR NOT public.dts_v2_source_rows_match_profile_v1(
                  approval_row.profile_vector
                ) THEN
            RAISE EXCEPTION 'DTS_V2_SOURCE_PROFILE_NOT_APPROVED'
              USING ERRCODE='55000';
          END IF;
          fence_vector:=public.dts_v2_current_fence_vector_v1();
          fence_hash:=public.dts_canonical_json_sha256_v1(fence_vector);
          full_manifest:=
            public.dts_v2_read_full_reconciliation_manifest_v1(
              p_evaluation_as_of,fence_hash,
              approval_row.manifest_sha256
            );
          legacy_manifest:=
            public.dts_v2_legacy_output_mapping_manifest_v1();
          technical_manifest:=
            public.dts_v2_cutover_technical_gate_manifest_v1(
              p_evaluation_as_of
            );
          RETURN jsonb_build_object(
            'protocol_version','{RECONCILIATION_PROTOCOL}',
            'base_control_version',control_row.row_version,
            'base_route_version',route_row.row_version,
            'target_projection_generation',
              control_row.projection_generation+1,
            'source_profile_manifest_version',
              approval_row.manifest_version,
            'source_profile_manifest_sha256',
              approval_row.manifest_sha256,
            'source_profile_vector',approval_row.profile_vector,
            'source_fence_hash',fence_hash,
            'v1_result_manifest',full_manifest->'v1_result_manifest',
            'v2_result_manifest',full_manifest->'v2_result_manifest',
            'legacy_output_manifest',legacy_manifest,
            'technical_gate_manifest',technical_manifest,
            'evaluation_as_of',to_char(
              p_evaluation_as_of AT TIME ZONE 'UTC',
              'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
            )
          );
        EXCEPTION WHEN no_data_found OR too_many_rows THEN
          RAISE EXCEPTION 'DTS_V2_RECONCILIATION_BASE_STATE_UNAVAILABLE'
            USING ERRCODE='55000';
        END
        $function$;

        CREATE FUNCTION public.record_dts_v2_reconciliation_pass_v1(
          p_run_id text,p_request jsonb,p_expected_request_hash text
        ) RETURNS jsonb
        LANGUAGE plpgsql VOLATILE SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE actor_name text:=coalesce(
          nullif(current_setting('role',true),'none'),session_user
        );
        DECLARE existing public.dts_v2_reconciliation_runs%ROWTYPE;
        DECLARE control_row public.dts_pipeline_control%ROWTYPE;
        DECLARE route_row public.dts_projection_read_routes%ROWTYPE;
        DECLARE approval_row public.dts_source_profile_approvals_v2%ROWTYPE;
        DECLARE request_hash_value text;
        DECLARE expected_request jsonb;
        DECLARE evaluation_value timestamptz;
        DECLARE fence_vector jsonb;
        DECLARE fence_hash text;
        DECLARE full_manifest jsonb;
        DECLARE legacy_manifest jsonb;
        DECLARE technical_manifest jsonb;
        BEGIN
          IF NOT public.dts_projection_cutover_actor_allowed_v1() THEN
            RAISE EXCEPTION 'DTS_PROJECTION_CUTOVER_ROLE_REQUIRED'
              USING ERRCODE='42501';
          END IF;
          IF nullif(btrim(p_run_id),'') IS NULL OR length(p_run_id)>128
             OR jsonb_typeof(p_request)<>'object'
             OR p_expected_request_hash !~ '^[0-9a-f]{{64}}$'
             OR public.dts_v2_json_has_forbidden_student_key(p_request) THEN
            RAISE EXCEPTION 'DTS_V2_RECONCILIATION_REQUEST_INVALID'
              USING ERRCODE='22023';
          END IF;
          request_hash_value:=public.dts_canonical_json_sha256_v1(p_request);
          IF request_hash_value<>p_expected_request_hash THEN
            RAISE EXCEPTION 'DTS_V2_RECONCILIATION_REQUEST_HASH_MISMATCH'
              USING ERRCODE='23514';
          END IF;
          PERFORM pg_advisory_xact_lock(
            hashtextextended('tit:dts-v2-cutover',0)
          );
          SELECT * INTO existing FROM public.dts_v2_reconciliation_runs
          WHERE run_id=p_run_id;
          IF FOUND THEN
            IF existing.request_hash<>request_hash_value THEN
              RAISE EXCEPTION 'DTS_V2_RECONCILIATION_RUN_CONFLICT'
                USING ERRCODE='23505';
            END IF;
            RETURN jsonb_build_object(
              'status','REPLAYED','run_id',existing.run_id,
              'request_hash',existing.request_hash,
              'target_projection_generation',
                existing.target_projection_generation,
              'source_fence_hash',existing.source_fence_hash
            );
          END IF;
          BEGIN
            evaluation_value:=(p_request->>'evaluation_as_of')::timestamptz;
          EXCEPTION WHEN OTHERS THEN
            RAISE EXCEPTION 'DTS_V2_RECONCILIATION_REQUEST_INVALID'
              USING ERRCODE='22023';
          END;
          expected_request:=jsonb_build_object(
            'protocol_version','{RECONCILIATION_PROTOCOL}',
            'base_control_version',
              (p_request->>'base_control_version')::bigint,
            'base_route_version',
              (p_request->>'base_route_version')::bigint,
            'target_projection_generation',
              (p_request->>'target_projection_generation')::bigint,
            'source_profile_manifest_version',
              (p_request->>'source_profile_manifest_version')::integer,
            'source_profile_manifest_sha256',
              p_request->>'source_profile_manifest_sha256',
            'source_profile_vector',p_request->'source_profile_vector',
            'source_fence_hash',p_request->>'source_fence_hash',
            'v1_result_manifest',p_request->'v1_result_manifest',
            'v2_result_manifest',p_request->'v2_result_manifest',
            'legacy_output_manifest',p_request->'legacy_output_manifest',
            'technical_gate_manifest',p_request->'technical_gate_manifest',
            'evaluation_as_of',to_char(
              evaluation_value AT TIME ZONE 'UTC',
              'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
            )
          );
          IF p_request<>expected_request
             OR evaluation_value>clock_timestamp()
             OR NOT public.dts_v2_source_profile_vector_valid_v1(
                  p_request->'source_profile_vector'
                )
             OR NOT public.dts_v2_result_manifest_valid_v1(
                  p_request->'v1_result_manifest'
                )
             OR NOT public.dts_v2_result_manifest_valid_v1(
                  p_request->'v2_result_manifest'
                ) THEN
            RAISE EXCEPTION 'DTS_V2_RECONCILIATION_REQUEST_INVALID'
              USING ERRCODE='22023';
          END IF;
          SELECT * INTO STRICT control_row
          FROM public.dts_pipeline_control WHERE control_id='PRIMARY'
          FOR UPDATE;
          SELECT * INTO STRICT route_row
          FROM public.dts_projection_read_routes WHERE route_id='PRIMARY'
          FOR UPDATE;
          IF control_row.mode NOT IN (
               'V1_COMPAT_DUAL_CAPTURE','ROLLED_BACK'
             ) OR route_row.active_projection<>'V1_COMPAT'
             OR control_row.time_catchup_status='PENDING'
             OR control_row.row_version<>
                  (p_request->>'base_control_version')::bigint
             OR route_row.row_version<>
                  (p_request->>'base_route_version')::bigint
             OR control_row.projection_generation+1<>
                  (p_request->>'target_projection_generation')::bigint THEN
            RAISE EXCEPTION 'DTS_V2_RECONCILIATION_BASE_STATE_CHANGED'
              USING ERRCODE='40001';
          END IF;
          SELECT * INTO STRICT approval_row
          FROM public.dts_source_profile_approvals_v2
          WHERE manifest_sha256=
                  p_request->>'source_profile_manifest_sha256'
            AND status='APPROVED';
          IF approval_row.manifest_version<>
               (p_request->>'source_profile_manifest_version')::integer
             OR approval_row.profile_vector<>
                  p_request->'source_profile_vector'
             OR approval_row.profile_vector_hash<>
                  public.dts_canonical_json_sha256_v1(
                    approval_row.profile_vector
                  )
             OR NOT public.dts_v2_source_rows_match_profile_v1(
                  approval_row.profile_vector
                ) THEN
            RAISE EXCEPTION 'DTS_V2_SOURCE_PROFILE_NOT_APPROVED'
              USING ERRCODE='55000';
          END IF;
          fence_vector:=public.dts_v2_current_fence_vector_v1();
          fence_hash:=public.dts_canonical_json_sha256_v1(fence_vector);
          IF fence_hash<>p_request->>'source_fence_hash' THEN
            RAISE EXCEPTION 'DTS_V2_RECONCILIATION_SOURCE_FENCE_CHANGED'
              USING ERRCODE='40001';
          END IF;
          full_manifest:=
            public.dts_v2_read_full_reconciliation_manifest_v1(
              evaluation_value,fence_hash,approval_row.manifest_sha256
            );
          legacy_manifest:=
            public.dts_v2_legacy_output_mapping_manifest_v1();
          technical_manifest:=
            public.dts_v2_cutover_technical_gate_manifest_v1(
              evaluation_value
            );
          IF full_manifest->'v1_result_manifest'<>
               p_request->'v1_result_manifest'
             OR full_manifest->'v2_result_manifest'<>
               p_request->'v2_result_manifest'
             OR legacy_manifest<>p_request->'legacy_output_manifest'
             OR (legacy_manifest->>'issue_count')::bigint<>0
             OR technical_manifest<>p_request->'technical_gate_manifest'
             OR NOT public.dts_v2_technical_gate_passes_v1(
                  technical_manifest
                ) THEN
            RAISE EXCEPTION 'DTS_V2_RECONCILIATION_NOT_PASS'
              USING ERRCODE='55000';
          END IF;
          PERFORM set_config(
            'tit.dts_projection_cutover_evidence_v1','on',true
          );
          INSERT INTO public.dts_v2_reconciliation_runs(
            run_id,request_hash,base_mode,base_control_version,
            base_route_version,target_projection_generation,
            source_profile_manifest_version,
            source_profile_manifest_sha256,source_profile_vector,
            source_profile_vector_hash,source_fence_vector,
            source_fence_hash,full_reconciliation_manifest,
            full_reconciliation_manifest_hash,v1_result_manifest,
            v1_result_manifest_hash,v2_result_manifest,
            v2_result_manifest_hash,legacy_output_manifest,
            legacy_output_manifest_hash,technical_gate_manifest,
            technical_gate_manifest_hash,evaluation_as_of,
            evaluation_business_date_beijing,result_status,
            recorded_at,recorded_by
          ) VALUES (
            p_run_id,request_hash_value,control_row.mode,
            control_row.row_version,route_row.row_version,
            control_row.projection_generation+1,
            approval_row.manifest_version,approval_row.manifest_sha256,
            approval_row.profile_vector,approval_row.profile_vector_hash,
            fence_vector,fence_hash,full_manifest,
            public.dts_canonical_json_sha256_v1(full_manifest),
            full_manifest->'v1_result_manifest',
            public.dts_canonical_json_sha256_v1(
              full_manifest->'v1_result_manifest'
            ),full_manifest->'v2_result_manifest',
            public.dts_canonical_json_sha256_v1(
              full_manifest->'v2_result_manifest'
            ),legacy_manifest,
            public.dts_canonical_json_sha256_v1(legacy_manifest),
            technical_manifest,
            public.dts_canonical_json_sha256_v1(technical_manifest),
            evaluation_value,
            (evaluation_value AT TIME ZONE 'Asia/Shanghai')::date,
            'PASS',clock_timestamp(),actor_name
          );
          PERFORM set_config(
            'tit.dts_projection_cutover_evidence_v1','off',true
          );
          RETURN jsonb_build_object(
            'status','APPLIED','run_id',p_run_id,
            'request_hash',request_hash_value,
            'target_projection_generation',
              control_row.projection_generation+1,
            'source_fence_hash',fence_hash
          );
        EXCEPTION WHEN no_data_found OR too_many_rows THEN
          RAISE EXCEPTION 'DTS_V2_RECONCILIATION_BASE_STATE_UNAVAILABLE'
            USING ERRCODE='55000';
        END
        $function$;

        REVOKE ALL ON FUNCTION
          public.preview_dts_v2_reconciliation_evidence_v1(
            timestamptz,text
          ),
          public.record_dts_v2_reconciliation_pass_v1(text,jsonb,text)
        FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION
          public.preview_dts_v2_reconciliation_evidence_v1(
            timestamptz,text
          ),
          public.record_dts_v2_reconciliation_pass_v1(text,jsonb,text)
        TO {CUTOVER_ROLE};
        """
    )


def _install_switch_and_readiness() -> None:
    op.execute(
        f"""
        CREATE FUNCTION public.switch_dts_projection_mode_v2(
          p_run_id text,p_expected_control_version bigint,
          p_expected_route_version bigint,p_target_mode text
        ) RETURNS jsonb
        LANGUAGE plpgsql VOLATILE SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE actor_name text:=coalesce(
          nullif(current_setting('role',true),'none'),session_user
        );
        DECLARE control_row public.dts_pipeline_control%ROWTYPE;
        DECLARE route_row public.dts_projection_read_routes%ROWTYPE;
        DECLARE run_row public.dts_v2_reconciliation_runs%ROWTYPE;
        DECLARE approval_row public.dts_source_profile_approvals_v2%ROWTYPE;
        DECLARE existing public.dts_projection_switch_audits%ROWTYPE;
        DECLARE request_payload jsonb;
        DECLARE request_hash_value text;
        DECLARE response_payload jsonb;
        DECLARE response_hash_value text;
        DECLARE target_projection text;
        DECLARE target_generation bigint;
        DECLARE fence_vector jsonb;
        DECLARE fence_hash text;
        DECLARE full_manifest jsonb;
        DECLARE legacy_manifest jsonb;
        DECLARE technical_manifest jsonb;
        DECLARE switch_time timestamptz;
        BEGIN
          IF NOT public.dts_projection_cutover_actor_allowed_v1() THEN
            RAISE EXCEPTION 'DTS_PROJECTION_CUTOVER_ROLE_REQUIRED'
              USING ERRCODE='42501';
          END IF;
          IF nullif(btrim(p_run_id),'') IS NULL OR length(p_run_id)>128
             OR p_expected_control_version<1
             OR p_expected_route_version<1
             OR p_target_mode NOT IN ('V2_PRIMARY','ROLLED_BACK') THEN
            RAISE EXCEPTION 'DTS_PROJECTION_SWITCH_REQUEST_INVALID'
              USING ERRCODE='22023';
          END IF;
          request_payload:=jsonb_build_object(
            'protocol_version','dts-projection-switch-command-v2',
            'run_id',p_run_id,
            'expected_control_version',p_expected_control_version,
            'expected_route_version',p_expected_route_version,
            'target_mode',p_target_mode
          );
          request_hash_value:=
            public.dts_canonical_json_sha256_v1(request_payload);
          PERFORM pg_advisory_xact_lock(
            hashtextextended('tit:dts-v2-cutover',0)
          );
          SELECT * INTO STRICT control_row
          FROM public.dts_pipeline_control WHERE control_id='PRIMARY'
          FOR UPDATE;
          SELECT * INTO STRICT route_row
          FROM public.dts_projection_read_routes WHERE route_id='PRIMARY'
          FOR UPDATE;
          switch_time:=clock_timestamp();
          SELECT * INTO existing FROM public.dts_projection_switch_audits
          WHERE switch_run_id=p_run_id;
          IF FOUND THEN
            IF existing.request_hash<>request_hash_value
               OR existing.to_mode<>p_target_mode
               OR control_row.mode<>existing.to_mode
               OR route_row.active_projection<>existing.to_projection
               OR control_row.projection_generation<>
                    existing.projection_generation
               OR control_row.row_version<>
                    existing.after_control_version
               OR route_row.row_version<>existing.after_route_version THEN
              RAISE EXCEPTION 'PROJECTION_ROUTE_CONFLICT'
                USING ERRCODE='23505';
            END IF;
            RETURN existing.response_payload ||
              jsonb_build_object('status','REPLAYED');
          END IF;
          IF control_row.row_version<>p_expected_control_version
             OR route_row.row_version<>p_expected_route_version
             OR control_row.time_catchup_status='PENDING' THEN
            RAISE EXCEPTION 'PROJECTION_ROUTE_CONFLICT'
              USING ERRCODE='40001';
          END IF;
          IF p_target_mode='V2_PRIMARY' THEN
            IF control_row.mode NOT IN (
                 'V1_COMPAT_DUAL_CAPTURE','ROLLED_BACK'
               ) OR route_row.active_projection<>'V1_COMPAT' THEN
              RAISE EXCEPTION 'PROJECTION_ROUTE_CONFLICT'
                USING ERRCODE='40001';
            END IF;
            SELECT * INTO STRICT run_row
            FROM public.dts_v2_reconciliation_runs
            WHERE run_id=p_run_id AND result_status='PASS'
            FOR SHARE;
            IF run_row.base_mode<>control_row.mode
               OR run_row.base_control_version<>control_row.row_version
               OR run_row.base_route_version<>route_row.row_version
               OR run_row.target_projection_generation<>
                    control_row.projection_generation+1 THEN
              RAISE EXCEPTION 'DTS_V2_RECONCILIATION_RUN_STALE'
                USING ERRCODE='40001';
            END IF;
            SELECT * INTO STRICT approval_row
            FROM public.dts_source_profile_approvals_v2
            WHERE manifest_sha256=
                    run_row.source_profile_manifest_sha256
              AND status='APPROVED';
            IF approval_row.profile_vector<>run_row.source_profile_vector
               OR approval_row.profile_vector_hash<>
                    run_row.source_profile_vector_hash
               OR NOT public.dts_v2_source_rows_match_profile_v1(
                    approval_row.profile_vector
                  ) THEN
              RAISE EXCEPTION 'DTS_V2_SOURCE_PROFILE_CHANGED'
                USING ERRCODE='40001';
            END IF;
            fence_vector:=public.dts_v2_current_fence_vector_v1();
            fence_hash:=public.dts_canonical_json_sha256_v1(fence_vector);
            full_manifest:=
              public.dts_v2_read_full_reconciliation_manifest_v1(
                run_row.evaluation_as_of,fence_hash,
                approval_row.manifest_sha256
              );
            legacy_manifest:=
              public.dts_v2_legacy_output_mapping_manifest_v1();
            technical_manifest:=
              public.dts_v2_cutover_technical_gate_manifest_v1(
                run_row.evaluation_as_of
              );
            IF fence_hash<>run_row.source_fence_hash
               OR full_manifest<>run_row.full_reconciliation_manifest
               OR public.dts_canonical_json_sha256_v1(full_manifest)<>
                    run_row.full_reconciliation_manifest_hash
               OR legacy_manifest<>run_row.legacy_output_manifest
               OR technical_manifest<>run_row.technical_gate_manifest
               OR NOT public.dts_v2_technical_gate_passes_v1(
                    technical_manifest
                  )
               OR (run_row.evaluation_as_of AT TIME ZONE
                    'Asia/Shanghai')::date<>
                    (switch_time AT TIME ZONE 'Asia/Shanghai')::date
               OR EXISTS (
                 SELECT 1 FROM public.course_favorite_observations
                 WHERE observed_at>run_row.evaluation_as_of
                   AND observed_at<=switch_time
               ) THEN
              RAISE EXCEPTION 'DTS_V2_CUTOVER_INPUT_CHANGED'
                USING ERRCODE='40001';
            END IF;
            target_projection:='V2';
            target_generation:=control_row.projection_generation+1;
            UPDATE public.dts_pipeline_control
            SET mode='V2_PRIMARY',row_version=row_version+1,
                projection_generation=target_generation,
                last_handoff_vector=fence_vector,
                last_handoff_vector_hash=fence_hash,
                last_handoff_run_id=p_run_id,
                time_catchup_status='COMPLETE',
                time_catchup_run_id=p_run_id,
                time_catchup_from=run_row.evaluation_as_of,
                time_catchup_through=switch_time,
                time_catchup_expected_count=0,
                time_catchup_expected_hash=
                  public.dts_canonical_json_sha256_v1('[]'::jsonb),
                changed_at=switch_time,changed_by=actor_name
            WHERE control_id='PRIMARY'
              AND row_version=p_expected_control_version;
          ELSE
            IF control_row.mode<>'V2_PRIMARY'
               OR route_row.active_projection<>'V2'
               OR control_row.last_handoff_vector_hash IS NULL THEN
              RAISE EXCEPTION 'PROJECTION_ROUTE_CONFLICT'
                USING ERRCODE='40001';
            END IF;
            target_projection:='V1_COMPAT';
            target_generation:=control_row.projection_generation;
            fence_hash:=control_row.last_handoff_vector_hash;
            UPDATE public.dts_pipeline_control
            SET mode='ROLLED_BACK',row_version=row_version+1,
                time_catchup_status='NOT_REQUIRED',
                time_catchup_run_id=NULL,time_catchup_from=NULL,
                time_catchup_through=NULL,
                time_catchup_expected_count=NULL,
                time_catchup_expected_hash=NULL,
                changed_at=switch_time,changed_by=actor_name
            WHERE control_id='PRIMARY'
              AND row_version=p_expected_control_version;
          END IF;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'PROJECTION_ROUTE_CONFLICT'
              USING ERRCODE='40001';
          END IF;
          PERFORM set_config('tit.dts_projection_switch_v1','on',true);
          UPDATE public.dts_projection_read_routes
          SET active_projection=target_projection,
              row_version=row_version+1,switched_by_run_id=p_run_id,
              switched_at=switch_time,switched_by=actor_name
          WHERE route_id='PRIMARY'
            AND row_version=p_expected_route_version;
          PERFORM set_config('tit.dts_projection_switch_v1','off',true);
          IF NOT FOUND THEN
            RAISE EXCEPTION 'PROJECTION_ROUTE_CONFLICT'
              USING ERRCODE='40001';
          END IF;
          response_payload:=jsonb_build_object(
            'status','APPLIED','run_id',p_run_id,
            'request_hash',request_hash_value,
            'target_mode',p_target_mode,
            'active_projection',target_projection,
            'projection_generation',target_generation,
            'control_row_version',p_expected_control_version+1,
            'route_row_version',p_expected_route_version+1,
            'source_fence_hash',fence_hash
          );
          response_hash_value:=
            public.dts_canonical_json_sha256_v1(response_payload);
          PERFORM set_config(
            'tit.dts_projection_cutover_evidence_v1','on',true
          );
          INSERT INTO public.dts_projection_switch_audits(
            switch_run_id,request_hash,reconciliation_run_id,
            from_mode,to_mode,from_projection,to_projection,
            projection_generation,before_control_version,
            after_control_version,before_route_version,
            after_route_version,source_fence_hash,response_payload,
            response_hash,result_status,occurred_at,occurred_by
          ) VALUES (
            p_run_id,request_hash_value,
            CASE WHEN p_target_mode='V2_PRIMARY' THEN p_run_id END,
            control_row.mode,p_target_mode,route_row.active_projection,
            target_projection,target_generation,
            p_expected_control_version,p_expected_control_version+1,
            p_expected_route_version,p_expected_route_version+1,
            fence_hash,response_payload,response_hash_value,
            'APPLIED',switch_time,actor_name
          );
          PERFORM set_config(
            'tit.dts_projection_cutover_evidence_v1','off',true
          );
          RETURN response_payload;
        EXCEPTION WHEN no_data_found OR too_many_rows THEN
          RAISE EXCEPTION 'PROJECTION_ROUTE_CONFLICT'
            USING ERRCODE='40001';
        END
        $function$;

        CREATE FUNCTION public.dts_projection_readiness_v1()
        RETURNS jsonb
        LANGUAGE plpgsql STABLE SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE control_row public.dts_pipeline_control%ROWTYPE;
        DECLARE route_row public.dts_projection_read_routes%ROWTYPE;
        DECLARE audit_row public.dts_projection_switch_audits%ROWTYPE;
        DECLARE run_row public.dts_v2_reconciliation_runs%ROWTYPE;
        DECLARE ready_value boolean:=false;
        DECLARE code_value text:='DTS_PROJECTION_STATE_UNAVAILABLE';
        DECLARE reconciliation_id text;
        BEGIN
          IF (SELECT count(*) FROM public.dts_pipeline_control)<>1
             OR (SELECT count(*) FROM public.dts_projection_read_routes)<>1
          THEN
            RETURN jsonb_build_object(
              'ready',false,'code',code_value
            );
          END IF;
          SELECT * INTO control_row FROM public.dts_pipeline_control
          WHERE control_id='PRIMARY';
          SELECT * INTO route_row FROM public.dts_projection_read_routes
          WHERE route_id='PRIMARY';
          IF route_row.route_contract_version<>'{ROUTE_CONTRACT_VERSION}'
             OR control_row.time_catchup_status='PENDING' THEN
            code_value:='DTS_PROJECTION_MAINTENANCE';
          ELSIF (control_row.mode IN (
                    'V1_COMPAT_DUAL_CAPTURE','ROLLED_BACK'
                  ) AND route_row.active_projection<>'V1_COMPAT')
             OR (control_row.mode='V2_PRIMARY'
                  AND route_row.active_projection<>'V2') THEN
            code_value:='DTS_PROJECTION_ROUTE_MODE_MISMATCH';
          ELSIF control_row.mode='V1_COMPAT_DUAL_CAPTURE' THEN
            ready_value:=true;
            code_value:='READY_V1_COMPAT';
          ELSIF control_row.mode='ROLLED_BACK' THEN
            SELECT * INTO audit_row
            FROM public.dts_projection_switch_audits
            WHERE to_mode='ROLLED_BACK'
              AND after_control_version=control_row.row_version
              AND after_route_version=route_row.row_version
              AND projection_generation=control_row.projection_generation;
            IF FOUND THEN
              ready_value:=true;
              code_value:='READY_ROLLED_BACK';
            ELSE
              code_value:='DTS_PROJECTION_ROLLBACK_AUDIT_MISSING';
            END IF;
          ELSIF control_row.mode='V2_PRIMARY' THEN
            SELECT * INTO audit_row
            FROM public.dts_projection_switch_audits
            WHERE to_mode='V2_PRIMARY'
              AND after_control_version=control_row.row_version
              AND after_route_version=route_row.row_version
              AND projection_generation=control_row.projection_generation;
            IF NOT FOUND THEN
              code_value:='DTS_PROJECTION_SWITCH_AUDIT_MISSING';
            ELSE
              reconciliation_id:=audit_row.reconciliation_run_id;
              SELECT * INTO run_row FROM public.dts_v2_reconciliation_runs
              WHERE run_id=reconciliation_id AND result_status='PASS'
                AND target_projection_generation=
                    control_row.projection_generation;
              IF NOT FOUND THEN
                code_value:='DTS_V2_RECONCILIATION_PASS_MISSING';
              ELSIF control_row.time_catchup_status<>'COMPLETE' THEN
                code_value:='DTS_V2_TIME_CATCHUP_INCOMPLETE';
              ELSIF NOT EXISTS (
                SELECT 1 FROM public.dts_source_profile_approvals_v2 approval
                WHERE approval.manifest_sha256=
                        run_row.source_profile_manifest_sha256
                  AND approval.status='APPROVED'
                  AND approval.profile_vector=run_row.source_profile_vector
                  AND public.dts_v2_source_rows_match_profile_v1(
                        approval.profile_vector
                      )
              ) THEN
                code_value:='DTS_V2_SOURCE_PROFILE_NOT_APPROVED';
              ELSE
                ready_value:=true;
                code_value:='READY_V2_PRIMARY';
              END IF;
            END IF;
          ELSE
            code_value:='DTS_PROJECTION_MODE_UNKNOWN';
          END IF;
          RETURN jsonb_build_object(
            'ready',ready_value,'code',code_value,
            'mode',control_row.mode,
            'active_projection',route_row.active_projection,
            'projection_generation',control_row.projection_generation,
            'control_row_version',control_row.row_version,
            'route_row_version',route_row.row_version,
            'time_catchup_status',control_row.time_catchup_status,
            'reconciliation_run_id',reconciliation_id
          );
        EXCEPTION WHEN OTHERS THEN
          RETURN jsonb_build_object(
            'ready',false,'code','DTS_PROJECTION_STATE_UNAVAILABLE'
          );
        END
        $function$;

        CREATE FUNCTION public.dts_projection_route_guard_v1()
        RETURNS text
        LANGUAGE plpgsql STABLE SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE readiness jsonb;
        BEGIN
          readiness:=public.dts_projection_readiness_v1();
          IF (readiness->>'ready')::boolean IS DISTINCT FROM true THEN
            RAISE EXCEPTION 'DTS_PROJECTION_READ_UNAVAILABLE:%',
              coalesce(readiness->>'code','UNKNOWN')
              USING ERRCODE='55000';
          END IF;
          RETURN readiness->>'active_projection';
        END
        $function$;

        REVOKE ALL ON FUNCTION
          public.switch_dts_projection_mode_v2(text,bigint,bigint,text),
          public.dts_projection_readiness_v1(),
          public.dts_projection_route_guard_v1()
        FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION
          public.switch_dts_projection_mode_v2(text,bigint,bigint,text),
          public.dts_projection_readiness_v1()
        TO {CUTOVER_ROLE};
        """
    )


def _replace_stable_read_views() -> None:
    op.execute(
        r"""
        ALTER VIEW public.teacher_scorecard_current
          RENAME TO teacher_scorecard_pre_v2_route_v1;
        ALTER VIEW public.teacher_lesson_score_current
          RENAME TO teacher_lesson_score_pre_v2_route_v1;

        CREATE VIEW public.teacher_scorecard_current AS
        WITH route AS MATERIALIZED (
          SELECT public.dts_projection_route_guard_v1()
            AS active_projection
        )
        SELECT branch.*
        FROM route
        JOIN public.teacher_scorecard_v1_compat_v1 branch
          ON route.active_projection='V1_COMPAT'
        UNION ALL
        SELECT branch.*
        FROM route
        JOIN public.teacher_scorecard_v2_v1 branch
          ON route.active_projection='V2';

        CREATE VIEW public.teacher_lesson_score_current AS
        WITH route AS MATERIALIZED (
          SELECT public.dts_projection_route_guard_v1()
            AS active_projection
        ), selected AS (
          SELECT 'V1_COMPAT'::text AS projection_kind,branch.*
          FROM route
          JOIN public.teacher_lesson_score_v1_compat_v1 branch
            ON route.active_projection='V1_COMPAT'
          UNION ALL
          SELECT 'V2'::text AS projection_kind,branch.*
          FROM route
          JOIN public.teacher_lesson_score_v2_v1 branch
            ON route.active_projection='V2'
        )
        SELECT selected.teacher_id,selected.source_region,
          selected.source_appoint_id,selected.participation_seq,
          selected.participation_role,selected.visible_to_teacher,
          selected.lesson_sequence,selected.lesson_count,
          selected.scheduled_start_at,selected.lesson_local_date,
          selected.lesson_local_time,selected.lesson_lifecycle_status,
          selected.is_perfect,selected.valid_for_scoring,
          selected.evidence_status,selected.lesson_total_score,
          selected.score_rule_version,selected.updated_at,
          normalized.business_facts,dimension_set.dimensions
        FROM selected
        CROSS JOIN LATERAL (
          SELECT jsonb_build_object(
            'attendance',jsonb_build_object(
              'is_late',selected.business_facts#>'{attendance,is_late}',
              'is_early',selected.business_facts#>'{attendance,is_early}',
              'absence_reason_detail',selected.business_facts#>
                '{attendance,absence_reason_detail}',
              'late_evidence_status',CASE
                WHEN selected.business_facts->'attendance'
                       ? 'late_evidence_status'
                  THEN selected.business_facts#>
                    '{attendance,late_evidence_status}'
                ELSE to_jsonb('NOT_APPLICABLE'::text) END,
              'early_evidence_status',CASE
                WHEN selected.business_facts->'attendance'
                       ? 'early_evidence_status'
                  THEN selected.business_facts#>
                    '{attendance,early_evidence_status}'
                ELSE to_jsonb('NOT_APPLICABLE'::text) END
            ),
            'user_feedback',jsonb_build_object(
              'grading_classification',CASE
                WHEN selected.business_facts->'user_feedback'
                       ? 'grading_classification'
                  THEN selected.business_facts#>
                    '{user_feedback,grading_classification}'
                ELSE 'null'::jsonb END,
              'negative_score',CASE
                WHEN selected.business_facts->'user_feedback'
                       ? 'negative_score'
                  THEN selected.business_facts#>
                    '{user_feedback,negative_score}'
                ELSE 'null'::jsonb END,
              'labels',CASE
                WHEN selected.business_facts->'user_feedback' ? 'labels'
                  THEN selected.business_facts#>'{user_feedback,labels}'
                ELSE 'null'::jsonb END,
              'has_positive_feedback_tag',CASE
                WHEN selected.business_facts->'user_feedback'
                       ? 'has_positive_feedback_tag'
                  THEN selected.business_facts#>
                    '{user_feedback,has_positive_feedback_tag}'
                WHEN selected.business_facts#>>
                       '{user_feedback,grading_classification}'='POSITIVE'
                  THEN 'true'::jsonb
                WHEN selected.business_facts#>>
                       '{user_feedback,grading_classification}'='NEGATIVE'
                  THEN 'false'::jsonb
                ELSE 'null'::jsonb END,
              'has_negative_feedback_tag',CASE
                WHEN selected.business_facts->'user_feedback'
                       ? 'has_negative_feedback_tag'
                  THEN selected.business_facts#>
                    '{user_feedback,has_negative_feedback_tag}'
                WHEN selected.business_facts#>>
                       '{user_feedback,grading_classification}'='NEGATIVE'
                  THEN 'true'::jsonb
                WHEN selected.business_facts#>>
                       '{user_feedback,grading_classification}'='POSITIVE'
                  THEN 'false'::jsonb
                ELSE 'null'::jsonb END,
              'feedback_detail',CASE
                WHEN selected.business_facts->'user_feedback'
                       ? 'feedback_detail'
                  THEN selected.business_facts#>
                    '{user_feedback,feedback_detail}'
                ELSE 'null'::jsonb END,
              'favorite_attribution_status',CASE
                WHEN selected.business_facts->'user_feedback'
                       ? 'favorite_attribution_status'
                  THEN selected.business_facts#>
                    '{user_feedback,favorite_attribution_status}'
                ELSE to_jsonb('NOT_APPLICABLE'::text) END,
              'is_favorited',CASE
                WHEN selected.business_facts->'user_feedback'
                       ? 'is_favorited'
                  THEN selected.business_facts#>'{user_feedback,is_favorited}'
                WHEN selected.business_facts#>>
                       '{user_feedback,favorite_attribution_status}' IN (
                         'AWARDED','AWARDED_PENDING_EVIDENCE'
                       ) THEN 'true'::jsonb
                ELSE 'null'::jsonb END,
              'is_rebooked',CASE
                WHEN selected.business_facts->'user_feedback'
                       ? 'is_rebooked'
                  THEN selected.business_facts#>'{user_feedback,is_rebooked}'
                ELSE 'null'::jsonb END,
              'is_blocked',selected.business_facts#>
                '{user_feedback,is_blocked}'
            ),
            'classroom_quality',jsonb_build_object(
              'is_camera_off',selected.business_facts#>
                '{classroom_quality,is_camera_off}',
              'is_cpu_usage_high',selected.business_facts#>
                '{classroom_quality,is_cpu_usage_high}',
              'is_network_delay_high',selected.business_facts#>
                '{classroom_quality,is_network_delay_high}',
              'hardware_quality_passed',selected.business_facts#>
                '{classroom_quality,hardware_quality_passed}',
              'is_perfect',selected.business_facts#>
                '{classroom_quality,is_perfect}'
            ),
            'capacity',jsonb_build_object(
              'is_peak',selected.business_facts#>'{capacity,is_peak}'
            ),
            'complaint',jsonb_build_object(
              'has_complaint',CASE
                WHEN selected.business_facts->'complaint' ? 'has_complaint'
                  THEN selected.business_facts#>'{complaint,has_complaint}'
                ELSE 'null'::jsonb END,
              'has_valid_complaint',CASE
                WHEN selected.business_facts->'complaint'
                       ? 'has_valid_complaint'
                  THEN selected.business_facts#>
                    '{complaint,has_valid_complaint}'
                ELSE 'null'::jsonb END,
              'category_l1',selected.business_facts#>
                '{complaint,category_l1}',
              'category_l2',selected.business_facts#>
                '{complaint,category_l2}',
              'category_l3',selected.business_facts#>
                '{complaint,category_l3}',
              'level',CASE WHEN selected.business_facts->'complaint' ? 'level'
                THEN selected.business_facts#>'{complaint,level}'
                ELSE 'null'::jsonb END,
              'route',CASE WHEN selected.business_facts->'complaint' ? 'route'
                THEN selected.business_facts#>'{complaint,route}'
                ELSE 'null'::jsonb END,
              'evidence_status',CASE
                WHEN selected.business_facts->'complaint'
                       ? 'evidence_status'
                  THEN selected.business_facts#>
                    '{complaint,evidence_status}'
                ELSE to_jsonb('NOT_APPLICABLE'::text) END
            )
          ) AS business_facts
        ) normalized
        CROSS JOIN LATERAL (
          SELECT coalesce(jsonb_agg(
            dimension.value || jsonb_build_object(
              'evidence_status',CASE
                WHEN dimension.value ? 'evidence_status'
                  THEN dimension.value->'evidence_status'
                WHEN selected.valid_for_scoring
                  THEN to_jsonb('SOURCE_MISSING'::text)
                ELSE to_jsonb('NOT_APPLICABLE'::text) END,
              'evidence_coverage',CASE
                WHEN dimension.value ? 'evidence_coverage'
                  THEN dimension.value->'evidence_coverage'
                ELSE 'null'::jsonb END,
              'components',coalesce(
                dimension.value->'components','[]'::jsonb
              )
            ) ORDER BY dimension.ordinal
          ),'[]'::jsonb) AS dimensions
          FROM jsonb_array_elements(selected.dimensions)
            WITH ORDINALITY dimension(value,ordinal)
        ) dimension_set;

        COMMENT ON VIEW public.teacher_lesson_score_current IS
          'Stable routed lesson participation read. Identity is strictly source_region + source_appoint_id + participation_seq; no legacy lesson_id alias. Both routes expose one normalized business_facts/dimensions JSON contract; unavailable facts are null or NOT_APPLICABLE.';
        COMMENT ON VIEW public.teacher_scorecard_current IS
          'Stable routed teacher scorecard read selected atomically with DTS pipeline mode and route generation.';

        REVOKE ALL PRIVILEGES ON TABLE
          public.teacher_scorecard_current,
          public.teacher_lesson_score_current,
          public.teacher_scorecard_pre_v2_route_v1,
          public.teacher_lesson_score_pre_v2_route_v1,
          public.teacher_scorecard_v1_compat_v1,
          public.teacher_scorecard_v2_v1,
          public.teacher_lesson_score_v1_compat_v1,
          public.teacher_lesson_score_v2_v1
        FROM PUBLIC;
        DO $projection_read_acl$
        DECLARE role_name text;
        BEGIN
          FOREACH role_name IN ARRAY ARRAY[
            'tit_growth_app','tit_teacher_crud'
          ] LOOP
            IF to_regrole(role_name) IS NOT NULL THEN
              EXECUTE format(
                'REVOKE ALL PRIVILEGES ON TABLE '
                'public.teacher_scorecard_pre_v2_route_v1,'
                'public.teacher_lesson_score_pre_v2_route_v1,'
                'public.teacher_scorecard_v1_compat_v1,'
                'public.teacher_scorecard_v2_v1,'
                'public.teacher_lesson_score_v1_compat_v1,'
                'public.teacher_lesson_score_v2_v1 FROM %I',role_name
              );
              EXECUTE format(
                'REVOKE ALL PRIVILEGES ON TABLE '
                'public.teacher_scorecard_current,'
                'public.teacher_lesson_score_current FROM %I',role_name
              );
            END IF;
          END LOOP;
          FOREACH role_name IN ARRAY ARRAY[
            'tit_teacher_crud'
          ] LOOP
            IF to_regrole(role_name) IS NOT NULL THEN
              EXECUTE format(
                'GRANT SELECT ON TABLE '
                'public.teacher_scorecard_current,'
                'public.teacher_lesson_score_current TO %I',role_name
              );
              EXECUTE format(
                'GRANT EXECUTE ON FUNCTION '
                'public.dts_projection_readiness_v1(),'
                'public.dts_projection_route_guard_v1() TO %I',role_name
              );
            END IF;
          END LOOP;
        END
        $projection_read_acl$;
        """
    )


def _install_acl_comments_and_assertions() -> None:
    managed_tables = (
        "dts_projection_read_routes",
        "dts_source_profile_approvals_v2",
        "dts_v2_reconciliation_runs",
        "dts_projection_switch_audits",
    )
    qualified = ",".join(f"public.{name}" for name in managed_tables)
    op.execute(
        f"""
        REVOKE ALL PRIVILEGES ON TABLE {qualified}
        FROM PUBLIC,{CUTOVER_ROLE};
        REVOKE CREATE ON SCHEMA public FROM {CUTOVER_ROLE};
        GRANT USAGE ON SCHEMA public TO {CUTOVER_ROLE};

        COMMENT ON TABLE public.dts_projection_read_routes IS
          'Singleton stable read-route fact. Only protected CAS switch/rollback may mutate it.';
        COMMENT ON TABLE public.dts_source_profile_approvals_v2 IS
          'Immutable production source-profile approvals. This revision intentionally seeds no default approval.';
        COMMENT ON TABLE public.dts_v2_reconciliation_runs IS
          'Immutable database-verified fourteen-result reconciliation PASS evidence; operator-supplied hashes alone are never trusted.';
        COMMENT ON TABLE public.dts_projection_switch_audits IS
          'Immutable idempotent cutover and rollback command audit.';

        DO $projection_cutover_contract$
        DECLARE function_name text;
        DECLARE role_name text;
        BEGIN
          IF (SELECT count(*) FROM public.dts_projection_read_routes)<>1
             OR NOT EXISTS (
               SELECT 1 FROM public.dts_projection_read_routes
               WHERE route_id='PRIMARY'
                 AND active_projection='V1_COMPAT'
                 AND route_contract_version='{ROUTE_CONTRACT_VERSION}'
                 AND row_version=1
             ) THEN
            RAISE EXCEPTION 'DTS_PROJECTION_ROUTE_INITIAL_STATE_INVALID';
          END IF;
          IF EXISTS (
            SELECT 1 FROM public.dts_source_profile_approvals_v2
          ) THEN
            RAISE EXCEPTION 'DTS_SOURCE_PROFILE_DEFAULT_APPROVAL_FORBIDDEN';
          END IF;
          FOREACH function_name IN ARRAY ARRAY[
            'preview_dts_v2_reconciliation_evidence_v1',
            'record_dts_v2_reconciliation_pass_v1',
            'switch_dts_projection_mode_v2',
            'dts_projection_readiness_v1',
            'dts_projection_route_guard_v1'
          ] LOOP
            IF NOT EXISTS (
              SELECT 1 FROM pg_proc
              WHERE pronamespace='public'::regnamespace
                AND proname=function_name AND prosecdef
                AND proconfig=ARRAY[
                  'search_path=pg_catalog, public'
                ]::text[]
            ) THEN
              RAISE EXCEPTION
                'DTS_PROJECTION_PROTECTED_FUNCTION_INVALID:%',
                function_name;
            END IF;
          END LOOP;
          IF NOT has_function_privilege(
               '{CUTOVER_ROLE}',
               'public.record_dts_v2_reconciliation_pass_v1('
               'text,jsonb,text)','EXECUTE'
             ) OR NOT has_function_privilege(
               '{CUTOVER_ROLE}',
               'public.switch_dts_projection_mode_v2('
               'text,bigint,bigint,text)','EXECUTE'
             ) OR has_table_privilege(
               '{CUTOVER_ROLE}',
               'public.dts_projection_read_routes','UPDATE'
             ) OR has_table_privilege(
               '{CUTOVER_ROLE}',
               'public.dts_v2_reconciliation_runs','INSERT'
             ) THEN
            RAISE EXCEPTION 'DTS_PROJECTION_CUTOVER_ACL_INVALID';
          END IF;
          IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema='public'
              AND table_name='teacher_lesson_score_current'
              AND column_name='lesson_id'
          ) OR NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema='public'
              AND table_name='teacher_lesson_score_current'
              AND column_name='source_region'
          ) OR NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema='public'
              AND table_name='teacher_lesson_score_current'
              AND column_name='source_appoint_id'
          ) OR NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema='public'
              AND table_name='teacher_lesson_score_current'
              AND column_name='participation_seq'
          ) THEN
            RAISE EXCEPTION 'DTS_PROJECTION_LESSON_IDENTITY_INVALID';
          END IF;
          FOREACH role_name IN ARRAY ARRAY[
            'tit_growth_app','tit_teacher_crud'
          ] LOOP
            IF to_regrole(role_name) IS NOT NULL
               AND EXISTS (
                 SELECT 1 FROM unnest(ARRAY[
                   'public.teacher_scorecard_pre_v2_route_v1',
                   'public.teacher_lesson_score_pre_v2_route_v1',
                   'public.teacher_scorecard_v1_compat_v1',
                   'public.teacher_scorecard_v2_v1',
                   'public.teacher_lesson_score_v1_compat_v1',
                   'public.teacher_lesson_score_v2_v1'
                 ]::text[]) relation(name)
                 WHERE has_table_privilege(
                   role_name,relation.name,'SELECT'
                 )
               ) THEN
              RAISE EXCEPTION
                'DTS_PROJECTION_BRANCH_VIEW_GRANT_LEAK:%',role_name;
            END IF;
          END LOOP;
        END
        $projection_cutover_contract$;
        """
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    _preflight_and_role()
    _create_tables()
    _install_validation_helpers()
    _install_legacy_and_technical_helpers()
    _install_full_reconciliation_provider_adapter()
    _install_table_guards_and_seed_route()
    _install_reconciliation_commands()
    _install_switch_and_readiness()
    _replace_stable_read_views()
    _install_acl_comments_and_assertions()


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        f"""
        DO $projection_cutover_downgrade_guard$
        BEGIN
          IF EXISTS (SELECT 1 FROM public.dts_v2_reconciliation_runs)
             OR EXISTS (SELECT 1 FROM public.dts_projection_switch_audits)
             OR EXISTS (SELECT 1 FROM public.dts_source_profile_approvals_v2)
             OR NOT EXISTS (
               SELECT 1 FROM public.dts_projection_read_routes
               WHERE route_id='PRIMARY' AND active_projection='V1_COMPAT'
                 AND row_version=1 AND switched_by_run_id IS NULL
             ) THEN
            RAISE EXCEPTION
              'DTS_PROJECTION_CUTOVER_DOWNGRADE_REQUIRES_UNUSED_INITIAL_STATE';
          END IF;
        END
        $projection_cutover_downgrade_guard$;

        REVOKE ALL ON FUNCTION
          public.preview_dts_v2_reconciliation_evidence_v1(
            timestamptz,text
          ),
          public.record_dts_v2_reconciliation_pass_v1(text,jsonb,text),
          public.switch_dts_projection_mode_v2(text,bigint,bigint,text),
          public.dts_projection_readiness_v1()
        FROM {CUTOVER_ROLE};
        DO $projection_cutover_revoke_optional$
        DECLARE role_name text;
        BEGIN
          FOREACH role_name IN ARRAY ARRAY[
            'tit_teacher_crud'
          ] LOOP
            IF to_regrole(role_name) IS NOT NULL THEN
              EXECUTE format(
                'REVOKE ALL ON FUNCTION '
                'public.dts_projection_readiness_v1(),'
                'public.dts_projection_route_guard_v1() FROM %I',
                role_name
              );
            END IF;
          END LOOP;
        END
        $projection_cutover_revoke_optional$;

        DROP VIEW public.teacher_lesson_score_current;
        DROP VIEW public.teacher_scorecard_current;
        ALTER VIEW public.teacher_lesson_score_pre_v2_route_v1
          RENAME TO teacher_lesson_score_current;
        ALTER VIEW public.teacher_scorecard_pre_v2_route_v1
          RENAME TO teacher_scorecard_current;

        DO $projection_cutover_restore_acl$
        DECLARE role_name text;
        BEGIN
          FOREACH role_name IN ARRAY ARRAY[
            'tit_teacher_crud'
          ] LOOP
            IF to_regrole(role_name) IS NOT NULL THEN
              EXECUTE format(
                'GRANT SELECT ON TABLE '
                'public.teacher_scorecard_current,'
                'public.teacher_lesson_score_current TO %I',role_name
              );
            END IF;
          END LOOP;
          FOREACH role_name IN ARRAY ARRAY[
            'tit_growth_app','tit_teacher_crud'
          ] LOOP
            IF to_regrole(role_name) IS NOT NULL THEN
              EXECUTE format(
                'GRANT SELECT ON TABLE '
                'public.teacher_scorecard_v1_compat_v1,'
                'public.teacher_scorecard_v2_v1,'
                'public.teacher_lesson_score_v1_compat_v1,'
                'public.teacher_lesson_score_v2_v1 TO %I',role_name
              );
            END IF;
          END LOOP;
        END
        $projection_cutover_restore_acl$;

        DROP FUNCTION public.dts_projection_route_guard_v1();
        DROP FUNCTION public.dts_projection_readiness_v1();
        DROP FUNCTION public.switch_dts_projection_mode_v2(
          text,bigint,bigint,text
        );
        DROP FUNCTION public.record_dts_v2_reconciliation_pass_v1(
          text,jsonb,text
        );
        DROP FUNCTION public.preview_dts_v2_reconciliation_evidence_v1(
          timestamptz,text
        );
        DROP FUNCTION public.dts_projection_cutover_actor_allowed_v1();
        DROP FUNCTION public.dts_v2_read_full_reconciliation_manifest_v1(
          timestamptz,text,text
        );
        DROP FUNCTION public.dts_v2_technical_gate_passes_v1(jsonb);
        DROP FUNCTION public.dts_v2_cutover_technical_gate_manifest_v1(
          timestamptz
        );
        DROP FUNCTION public.dts_v2_legacy_output_mapping_manifest_v1();

        DROP TRIGGER trg_guard_dts_projection_switch_audit_v1
          ON public.dts_projection_switch_audits;
        DROP TRIGGER trg_guard_dts_v2_reconciliation_run_v1
          ON public.dts_v2_reconciliation_runs;
        DROP TRIGGER trg_guard_dts_source_profile_approval_v2
          ON public.dts_source_profile_approvals_v2;
        DROP TRIGGER trg_guard_dts_projection_route_v1
          ON public.dts_projection_read_routes;
        DROP FUNCTION public.guard_dts_projection_cutover_evidence_v1();
        DROP FUNCTION public.guard_dts_source_profile_approval_v2();
        DROP FUNCTION public.guard_dts_projection_route_v1();
        DROP FUNCTION public.dts_v2_source_rows_match_profile_v1(jsonb);
        DROP FUNCTION public.dts_v2_current_fence_vector_v1();
        DROP FUNCTION public.dts_v2_result_manifest_valid_v1(jsonb);
        DROP FUNCTION public.dts_v2_source_profile_vector_valid_v1(jsonb);
        """
    )
    op.drop_table("dts_projection_switch_audits", schema="public")
    op.drop_table("dts_v2_reconciliation_runs", schema="public")
    op.drop_table("dts_source_profile_approvals_v2", schema="public")
    op.drop_table("dts_projection_read_routes", schema="public")
