"""reset consumed history and activate the only DTS event pipeline.

Revision ID: 20260824_101_dts_single_pipeline_reset
Revises: 20260823_100_scope_snapshot_diff
Create Date: 2026-08-24

This is an intentionally destructive release migration. It preserves catalog,
rule/configuration and operator identity tables, but removes every historical
DTS/source/domain/teacher execution fact and every FK-dependent fact. DOM and
OVS then establish a fresh stream epoch/checkpoint from the first event at or
after their configured TIT_DTS_START_AT.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa

from app.dts_source_contract_v2 import V2_EVENT_CONTRACT_MANIFEST_SHA256


revision: str = "20260824_101_dts_single_pipeline_reset"
down_revision: Union[str, None] = "20260823_100_scope_snapshot_diff"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


RESET_ID = revision
PROFILE_MANIFEST_SHA256 = V2_EVENT_CONTRACT_MANIFEST_SHA256
INGEST_ROLE = "tit_dts_ingest_runtime"
ROUTE_CONTRACT_VERSION = "teacher-read-route-v1"
STREAM_INIT_SIGNATURE = (
    "public.initialize_dts_event_stream_v1("
    "text,text,integer,text,text,bigint,bigint,text,text)"
)

# Explicit roots are consumption/runtime facts only. The recursive FK closure
# also includes Tide/external tables that cannot remain after teachers or shared
# task assignments are removed. Configuration/catalog/identity tables are not
# roots and therefore survive unless they directly depend on consumed facts.
CONSUMED_FACT_ROOTS = (
    "agent_decisions",
    "audit_events",
    "completion_correction_pointer_upgrade_archive",
    "course_favorite_attributions",
    "course_favorite_observations",
    "data_import_batches",
    "domain_aggregate_revisions",
    "dts_dirty_key_dependencies",
    "dts_dirty_key_inputs",
    "dts_dirty_key_state_audits",
    "dts_dirty_keys",
    "dts_dirty_keys_legacy_archive_v80",
    "dts_ingest_checkpoints",
    "dts_ingest_events",
    "dts_ingest_issues",
    "dts_pipeline_bootstrap_audits",
    "dts_pipeline_control",
    "dts_projection_read_routes",
    "dts_projection_switch_audits",
    "dts_qualification_gate_commands",
    "dts_source_partition_epochs",
    "dts_source_profile_approvals_v2",
    "dts_source_row_versions",
    "dts_source_rows",
    "dts_source_scope_commands",
    "dts_source_scope_memberships",
    "dts_source_scope_snapshots",
    "dts_source_scope_states",
    "dts_source_scope_transition_audits",
    "dts_source_snapshot_desired_rows",
    "dts_source_snapshot_fences",
    "dts_source_snapshot_rows",
    "dts_source_table_publish_generations",
    "dts_teacher_time_recheck_audits",
    "dts_teacher_time_recheck_results",
    "dts_teacher_time_recheck_schedule",
    "dts_v2_reconciliation_runs",
    "fixed_task_instances_v2",
    "fixed_task_status_events_v2",
    "idempotency_records",
    "lesson_dimension_scores",
    "lesson_facts",
    "lesson_score_component_settlements",
    "lesson_score_results",
    "lesson_source_region_backfill_manifest",
    "lesson_source_region_migration_control",
    "lesson_source_wide",
    "notification_events",
    "notifications",
    "ops_case_recovery_events",
    "ops_cases",
    "ops_decisions",
    "outbound_outputs",
    "outbox_events",
    "outbox_events_legacy_archive",
    "personalized_trigger_matches",
    "provider_calls",
    "score_accounts",
    "score_component_accounts",
    "score_entries",
    "score_entry_idempotency_aliases",
    "source_course_complaints",
    "source_course_fact_current",
    "source_course_labels",
    "source_course_participations",
    "source_courses",
    "source_participation_fact_current",
    "source_records",
    "task_assignments",
    "task_executions",
    "task_runtime_events",
    "task_status_callbacks_v2",
    "teacher_metric_snapshots",
    "teacher_qualifications",
    "teacher_source_wide",
    "teacher_student_relationship_current",
    "teacher_student_relationship_events",
    "teachers",
)


def _create_reset_audit() -> None:
    op.create_table(
        "dts_pipeline_reset_audits",
        sa.Column("reset_id", sa.String(length=128), primary_key=True),
        sa.Column(
            "source_profile_manifest_sha256",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("history_policy", sa.String(length=32), nullable=False),
        sa.Column("event_scope", sa.String(length=32), nullable=False),
        sa.Column(
            "reset_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column("reset_by", sa.String(length=128), nullable=False),
        sa.CheckConstraint(
            "source_profile_manifest_sha256 ~ '^[0-9a-f]{64}$' "
            "AND history_policy='CLEAR_ALL_CONSUMED_HISTORY' "
            "AND event_scope='POST_CONFIGURED_START' "
            "AND btrim(reset_by)<>''",
            name="ck_dts_pipeline_reset_audit_shape",
        ),
        schema="public",
        comment=(
            "One-shot destructive DTS reset evidence. Catalog/configuration "
            "and operator identity survive; consumed and dependent facts do not."
        ),
    )
    op.execute(
        "COMMENT ON TABLE public.dts_pipeline_reset_audits IS "
        "'One-shot destructive DTS reset evidence. Catalog/configuration "
        "and operator identity survive; consumed and dependent facts do not.'"
    )


def _assert_runtimes_stopped() -> None:
    op.execute(
        """
        DO $runtime_stop_gate$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM pg_catalog.pg_stat_activity
            WHERE pid<>pg_catalog.pg_backend_pid()
              AND state IS DISTINCT FROM 'idle'
              AND usename IN (
                'tit_dts_ingest_runtime','tit_dts_domain_projector_runtime',
                'tit_dts_outbox_worker_runtime','tit_dts_scope_coordinator_runtime',
                'tit_source_wide_runtime','tit_source_worker_runtime'
              )
          ) THEN
            RAISE EXCEPTION 'DTS_RESET_REQUIRES_STOPPED_RUNTIMES'
              USING ERRCODE='55006';
          END IF;
          PERFORM pg_advisory_xact_lock(
            hashtextextended('tit:dts-single-pipeline-reset',0)
          );
        END
        $runtime_stop_gate$;
        """
    )


def _truncate_consumed_facts() -> None:
    root_values = ",".join(f"('public.{name}')" for name in CONSUMED_FACT_ROOTS)
    # Alembic may apply 101 in the same transaction as older migrations.  Flush
    # their deferred constraint-trigger events before TRUNCATE; PostgreSQL
    # otherwise rejects a table with pending trigger events even when the rows
    # are internally consistent.
    op.execute("SET CONSTRAINTS ALL IMMEDIATE")
    op.execute(
        f"""
        DO $truncate_consumed_history$
        DECLARE relation_list text;
        BEGIN
          WITH RECURSIVE root_names(name) AS (
            VALUES {root_values}
          ), roots(relid) AS (
            SELECT to_regclass(name) FROM root_names
            WHERE to_regclass(name) IS NOT NULL
          ), closure(relid) AS (
            SELECT relid FROM roots
            UNION
            SELECT dependency.conrelid
            FROM pg_catalog.pg_constraint dependency
            JOIN closure parent ON parent.relid=dependency.confrelid
            WHERE dependency.contype='f'
          )
          SELECT string_agg(
                   format('%I.%I',namespace.nspname,relation.relname),
                   ',' ORDER BY namespace.nspname,relation.relname
                 )
          INTO STRICT relation_list
          FROM closure
          JOIN pg_catalog.pg_class relation ON relation.oid=closure.relid
          JOIN pg_catalog.pg_namespace namespace
            ON namespace.oid=relation.relnamespace
          WHERE relation.relkind IN ('r','p');

          EXECUTE 'TRUNCATE TABLE ' || relation_list || ' RESTART IDENTITY';
        END
        $truncate_consumed_history$;
        """
    )


def _seed_single_pipeline_control() -> None:
    op.execute(
        f"""
        SELECT set_config('tit.dts_initial_epoch_bootstrap','on',true);
        INSERT INTO public.dts_pipeline_control(
          control_id,mode,row_version,projection_generation,
          qualification_grants_enabled,consumer_group,initial_h0_vector,
          initial_h0_vector_hash,initial_h0_bootstrap_run_id,
          time_catchup_status,changed_at,changed_by
        )
        SELECT
          'PRIMARY','V2_PRIMARY',1,1,false,'SINGLE_PIPELINE',vector.value,
          public.dts_canonical_json_sha256_v1(vector.value),'{RESET_ID}',
          'NOT_REQUIRED',clock_timestamp(),'alembic:{RESET_ID}'
        FROM (VALUES (
          jsonb_build_array(jsonb_build_object(
            'event_scope','POST_CONFIGURED_START',
            'checkpoint_policy','FIRST_COMMITTED_EVENT_PER_STREAM'
          ))
        )) AS vector(value);
        SELECT set_config('tit.dts_initial_epoch_bootstrap','off',true);

        SELECT set_config('tit.dts_projection_switch_v1','on',true);
        INSERT INTO public.dts_projection_read_routes(
          route_id,active_projection,route_contract_version,row_version,
          switched_by_run_id,switched_at,switched_by
        ) VALUES (
          'PRIMARY','V2','{ROUTE_CONTRACT_VERSION}',2,'{RESET_ID}',
          clock_timestamp(),'alembic:{RESET_ID}'
        );
        SELECT set_config('tit.dts_projection_switch_v1','off',true);

        INSERT INTO public.dts_pipeline_reset_audits(
          reset_id,source_profile_manifest_sha256,history_policy,event_scope,
          reset_by
        ) VALUES (
          '{RESET_ID}','{PROFILE_MANIFEST_SHA256}',
          'CLEAR_ALL_CONSUMED_HISTORY','POST_CONFIGURED_START',
          'alembic:{RESET_ID}'
        );
        """
    )


def _lock_single_pipeline_control() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.guard_dts_pipeline_control_v2()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path=pg_catalog,public
        AS $function$
        BEGIN
          IF TG_OP='DELETE' THEN
            RAISE EXCEPTION 'DTS_PIPELINE_CONTROL_DELETE_FORBIDDEN'
              USING ERRCODE='42501';
          END IF;
          IF NEW.control_id<>'PRIMARY'
             OR NEW.mode<>'V2_PRIMARY'
             OR NEW.projection_generation<>1 THEN
            RAISE EXCEPTION 'DTS_SINGLE_PIPELINE_CONTROL_FIXED'
              USING ERRCODE='23514';
          END IF;
          IF TG_OP='INSERT' THEN
            IF current_setting(
                 'tit.dts_initial_epoch_bootstrap',true
               ) IS DISTINCT FROM 'on' THEN
              RAISE EXCEPTION
                'DTS_PIPELINE_CONTROL_INSERT_REQUIRES_BOOTSTRAP'
                USING ERRCODE='42501';
            END IF;
            RETURN NEW;
          END IF;
          IF NEW.consumer_group IS DISTINCT FROM OLD.consumer_group
             OR NEW.initial_h0_vector IS DISTINCT FROM OLD.initial_h0_vector
             OR NEW.initial_h0_vector_hash IS DISTINCT FROM
                  OLD.initial_h0_vector_hash
             OR NEW.initial_h0_bootstrap_run_id IS DISTINCT FROM
                  OLD.initial_h0_bootstrap_run_id
             OR NEW.row_version<>OLD.row_version+1 THEN
            RAISE EXCEPTION 'DTS_SINGLE_PIPELINE_CONTROL_IMMUTABLE'
              USING ERRCODE='23514';
          END IF;
          RETURN NEW;
        END
        $function$;
        """
    )


def _install_stream_initializer() -> None:
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION public.guard_dts_v2_checkpoint_identity()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE actor_name text:=coalesce(
          nullif(current_setting('role',true),'none'),session_user
        );
        BEGIN
          IF actor_name<>'{INGEST_ROLE}' THEN
            RETURN NEW;
          END IF;
          IF TG_OP='INSERT' THEN
            IF current_setting('tit.dts_stream_initialize_v1',true)='on'
               AND NEW.source_partition_epoch_id IS NOT NULL
               AND NEW.consumer_group IS NOT NULL
               AND NEW.checkpoint_row_version=1
               AND NEW.is_current_epoch IS TRUE THEN
              RETURN NEW;
            END IF;
            RAISE EXCEPTION 'DTS_V2_CHECKPOINT_ROUTE_REQUIRES_CONTROL_PLANE'
              USING ERRCODE='42501';
          END IF;
          IF NEW.source_partition_epoch_id IS DISTINCT FROM
               OLD.source_partition_epoch_id
             OR NEW.consumer_group IS DISTINCT FROM OLD.consumer_group
             OR NEW.is_current_epoch IS DISTINCT FROM OLD.is_current_epoch
             OR NEW.checkpoint_row_version<OLD.checkpoint_row_version THEN
            RAISE EXCEPTION 'DTS_V2_CHECKPOINT_IDENTITY_IMMUTABLE'
              USING ERRCODE='23514';
          END IF;
          RETURN NEW;
        END
        $function$;

        CREATE FUNCTION public.initialize_dts_event_stream_v1(
          p_source_region text,p_topic text,p_partition integer,
          p_epoch_id text,p_consumer_group text,p_initial_offset bigint,
          p_source_timestamp bigint,p_source_position text,
          p_profile_manifest_sha256 text
        ) RETURNS text
        LANGUAGE plpgsql VOLATILE SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE existing_checkpoint public.dts_ingest_checkpoints%ROWTYPE;
        BEGIN
          IF session_user<>'{INGEST_ROLE}' THEN
            RAISE EXCEPTION 'DTS_STREAM_INIT_CALLER_FORBIDDEN'
              USING ERRCODE='42501';
          END IF;
          IF p_source_region NOT IN ('dom','ovs')
             OR nullif(btrim(p_topic),'') IS NULL OR length(p_topic)>512
             OR p_partition<0 OR nullif(btrim(p_epoch_id),'') IS NULL
             OR length(p_epoch_id)>160
             OR nullif(btrim(p_consumer_group),'') IS NULL
             OR length(p_consumer_group)>256
             OR p_initial_offset<0 OR p_source_timestamp<0
             OR p_source_position IS NULL
             OR p_profile_manifest_sha256 !~ '^[0-9a-f]{{64}}$' THEN
            RAISE EXCEPTION 'DTS_STREAM_INIT_REQUEST_INVALID'
              USING ERRCODE='22023';
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM public.dts_pipeline_control
            WHERE control_id='PRIMARY' AND mode='V2_PRIMARY'
              AND projection_generation=1
          ) OR NOT EXISTS (
            SELECT 1 FROM public.dts_pipeline_reset_audits
            WHERE reset_id='{RESET_ID}'
              AND source_profile_manifest_sha256=
                    p_profile_manifest_sha256
          ) THEN
            RAISE EXCEPTION 'DTS_SINGLE_PIPELINE_CONTROL_INVALID'
              USING ERRCODE='55000';
          END IF;

          PERFORM pg_advisory_xact_lock(hashtextextended(
            'tit:dts-stream:'||p_source_region||':'||p_topic||':'||p_partition,
            0
          ));
          SELECT * INTO existing_checkpoint
          FROM public.dts_ingest_checkpoints
          WHERE source_region=p_source_region AND topic=p_topic
            AND partition_id=p_partition
          FOR UPDATE;
          IF FOUND THEN
            IF existing_checkpoint.source_partition_epoch_id<>p_epoch_id
               OR existing_checkpoint.consumer_group<>p_consumer_group
               OR existing_checkpoint.is_current_epoch IS NOT TRUE
               OR NOT EXISTS (
                 SELECT 1 FROM public.dts_source_partition_epochs
                 WHERE source_region=p_source_region
                   AND source_partition_epoch_id=p_epoch_id
                   AND topic=p_topic AND partition_id=p_partition
                   AND epoch_kind='BROKER' AND status='ACTIVE'
               ) THEN
              RAISE EXCEPTION 'DTS_STREAM_INIT_IDENTITY_CONFLICT'
                USING ERRCODE='23514';
            END IF;
            RETURN 'EXISTS';
          END IF;
          IF EXISTS (
            SELECT 1 FROM public.dts_source_partition_epochs
            WHERE source_region=p_source_region
              AND topic=p_topic AND partition_id=p_partition
          ) THEN
            RAISE EXCEPTION 'DTS_STREAM_INIT_PARTIAL_STATE'
              USING ERRCODE='55000';
          END IF;

          INSERT INTO public.dts_source_partition_epochs(
            source_region,source_partition_epoch_id,topic,partition_id,
            epoch_kind,status,stream_generation_id,epoch_opening_id,
            epoch_sequence,start_offset,v2_epoch_bootstrap_floor,
            activation_mode,activated_at,row_version
          ) VALUES (
            p_source_region,p_epoch_id,p_topic,p_partition,'BROKER','ACTIVE',
            p_consumer_group,p_epoch_id,1,p_initial_offset,p_initial_offset,
            'H0_BOOTSTRAP',clock_timestamp(),1
          );
          PERFORM set_config('tit.dts_stream_initialize_v1','on',true);
          INSERT INTO public.dts_ingest_checkpoints(
            source_region,topic,partition_id,next_offset,source_timestamp,
            source_position,updated_at,source_partition_epoch_id,
            consumer_group,checkpoint_row_version,is_current_epoch
          ) VALUES (
            p_source_region,p_topic,p_partition,p_initial_offset,
            p_source_timestamp,p_source_position,clock_timestamp(),p_epoch_id,
            p_consumer_group,1,true
          );
          PERFORM set_config('tit.dts_stream_initialize_v1','off',true);
          RETURN 'INITIALIZED';
        END
        $function$;

        REVOKE ALL ON FUNCTION {STREAM_INIT_SIGNATURE} FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION {STREAM_INIT_SIGNATURE} TO {INGEST_ROLE};
        COMMENT ON FUNCTION {STREAM_INIT_SIGNATURE} IS
          'Create one DOM/OVS stream epoch and checkpoint from the first post-start event after the one-shot history reset.';
        """
    )


def _replace_readiness() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.dts_projection_readiness_v1()
        RETURNS jsonb
        LANGUAGE plpgsql STABLE SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE control_row public.dts_pipeline_control%ROWTYPE;
        DECLARE route_row public.dts_projection_read_routes%ROWTYPE;
        BEGIN
          SELECT * INTO STRICT control_row FROM public.dts_pipeline_control
          WHERE control_id='PRIMARY';
          SELECT * INTO STRICT route_row FROM public.dts_projection_read_routes
          WHERE route_id='PRIMARY';
          IF control_row.mode<>'V2_PRIMARY'
             OR control_row.projection_generation<>1
             OR route_row.active_projection<>'V2' THEN
            RETURN jsonb_build_object(
              'ready',false,'code','DTS_SINGLE_PIPELINE_STATE_INVALID'
            );
          END IF;
          RETURN jsonb_build_object(
            'ready',true,'code','READY_SINGLE_PIPELINE',
            'mode','V2_PRIMARY','active_projection','V2',
            'projection_generation',1,
            'control_row_version',control_row.row_version,
            'route_row_version',route_row.row_version,
            'time_catchup_status','NOT_REQUIRED'
          );
        EXCEPTION WHEN no_data_found OR too_many_rows THEN
          RETURN jsonb_build_object(
            'ready',false,'code','DTS_SINGLE_PIPELINE_STATE_UNAVAILABLE'
          );
        END
        $function$;
        """
    )


def _retire_legacy_runtime_commands() -> None:
    op.execute(
        """
        REVOKE EXECUTE ON FUNCTION
          public.claim_v1_compat_dirty_key_v1(text),
          public.complete_v1_compat_dirty_key_v1(
            text,text,text,text,text,bigint,bigint
          ),
          public.fail_v1_compat_dirty_key_v1(
            text,text,text,text,text,text,integer,integer,integer
          ),
          public.dts_v1_compat_dirty_not_complete_count_v1()
        FROM tit_dts_ingest_runtime;
        """
    )


def _verify_reset() -> None:
    fact_values = ",".join(f"('public.{name}')" for name in CONSUMED_FACT_ROOTS)
    op.execute(
        f"""
        DO $verify_single_pipeline_reset$
        DECLARE relation regclass; remaining bigint;
        BEGIN
          FOR relation IN
            SELECT to_regclass(name)
            FROM (VALUES {fact_values}) AS roots(name)
            WHERE to_regclass(name) IS NOT NULL
          LOOP
            EXECUTE format('SELECT count(*) FROM %s',relation) INTO remaining;
            IF remaining<>0 AND relation NOT IN (
              'public.dts_pipeline_control'::regclass,
              'public.dts_projection_read_routes'::regclass
            ) THEN
              RAISE EXCEPTION 'DTS_RESET_FACT_NOT_EMPTY:%',relation;
            END IF;
          END LOOP;
          IF (SELECT count(*) FROM public.dts_pipeline_control)<>1
             OR (SELECT count(*) FROM public.dts_projection_read_routes)<>1
             OR (SELECT count(*) FROM public.dts_pipeline_reset_audits)<>1
             OR (public.dts_projection_readiness_v1()->>'ready')::boolean
                  IS NOT TRUE THEN
            RAISE EXCEPTION 'DTS_SINGLE_PIPELINE_RESET_INVALID';
          END IF;
        END
        $verify_single_pipeline_reset$;
        """
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    _create_reset_audit()
    _assert_runtimes_stopped()
    _truncate_consumed_facts()
    _seed_single_pipeline_control()
    _lock_single_pipeline_control()
    _install_stream_initializer()
    _replace_readiness()
    _retire_legacy_runtime_commands()
    _verify_reset()


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    raise RuntimeError(
        "20260824_101_dts_single_pipeline_reset is destructive and forward-only"
    )
