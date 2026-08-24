-- Formal DMS migration: public rev100 -> rev101.
-- Release commit: flow/release@2881507
-- This migration clears every historical consumed fact and is forward-only.
-- Application settings after this transaction:
-- TIT_DTS_START_AT=2026-08-18T00:00:00+08:00
-- DOM TIT_DTS_SOURCE_PARTITION_EPOCH_ID=dom-single-20260818-2881507-01
-- OVS TIT_DTS_SOURCE_PARTITION_EPOCH_ID=ovs-single-20260818-2881507-01
--
-- Prerequisites enforced below:
-- public head is exactly 20260823_100_scope_snapshot_diff;
-- teacher ledger is exactly 38 rows ending at 0043;
-- runtime roles have no active transaction.

BEGIN;

SET LOCAL lock_timeout = '10s';
SET LOCAL statement_timeout = '15min';

SELECT pg_advisory_xact_lock(
    hashtextextended('tit:dts-single-pipeline-formal-migration', 0)
);

DO $$
DECLARE
    public_head text;
    teacher_head text;
    teacher_count integer;
BEGIN
    IF current_setting('transaction_read_only')::boolean THEN
        RAISE EXCEPTION 'public rev101 migration requires a writable transaction';
    END IF;

    IF to_regclass('public.alembic_version') IS NULL
       OR to_regclass('tide.schema_migrations') IS NULL THEN
        RAISE EXCEPTION 'public rev101 migration requires public and tide ledgers';
    END IF;

    SELECT CASE WHEN count(*) = 1 THEN min(version_num) END
    INTO public_head
    FROM public.alembic_version;

    IF public_head IS DISTINCT FROM '20260823_100_scope_snapshot_diff' THEN
        RAISE EXCEPTION
            'public rev101 DMS requires public head 100; current=%',
            coalesce(public_head, '<invalid>');
    END IF;

    SELECT count(*), max(migration_id) FILTER (
        WHERE migration_order = (
            SELECT max(migration_order) FROM tide.schema_migrations
        )
    )
    INTO teacher_count, teacher_head
    FROM tide.schema_migrations;

    IF teacher_count <> 38
       OR teacher_head IS DISTINCT FROM '0043_p_rel_execution_catalog'
       OR NOT EXISTS (
           SELECT 1
           FROM tide.schema_migrations
           WHERE migration_order = 38
             AND migration_id = '0043_p_rel_execution_catalog'
             AND filename = '0043_p_rel_execution_catalog.up.sql'
             AND sha256 =
                 '0bb25fd49de5aac915dfb9a4e52ad567183a97865b4fc4dfd6d0a35d660492bf'
       ) THEN
        RAISE EXCEPTION
            'public rev101 DMS requires exact 38-row teacher ledger ending at 0043; count=%, head=%',
            teacher_count,
            coalesce(teacher_head, '<invalid>');
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_stat_activity
        WHERE pid <> pg_catalog.pg_backend_pid()
          AND state IS DISTINCT FROM 'idle'
          AND usename IN (
              'tit_dts_ingest_runtime',
              'tit_growth_app',
              'tit_teacher_crud',
              'tide_support_ticket_owner'
          )
    ) THEN
        RAISE EXCEPTION 'public rev101 DMS requires all DTS/application runtimes stopped';
    END IF;
END
$$;

-- Running upgrade 20260823_100_scope_snapshot_diff -> 20260824_101_dts_single_pipeline_reset

CREATE TABLE public.dts_pipeline_reset_audits (
    reset_id VARCHAR(128) NOT NULL,
    source_profile_manifest_sha256 VARCHAR(64) NOT NULL,
    history_policy VARCHAR(32) NOT NULL,
    event_scope VARCHAR(32) NOT NULL,
    reset_at TIMESTAMP WITH TIME ZONE DEFAULT clock_timestamp() NOT NULL,
    reset_by VARCHAR(128) NOT NULL,
    PRIMARY KEY (reset_id),
    CONSTRAINT ck_dts_pipeline_reset_audit_shape CHECK (source_profile_manifest_sha256 ~ '^[0-9a-f]{64}$' AND history_policy='CLEAR_ALL_CONSUMED_HISTORY' AND event_scope='POST_CONFIGURED_START' AND btrim(reset_by)<>'')
);

COMMENT ON TABLE public.dts_pipeline_reset_audits IS 'One-shot destructive DTS reset evidence. Catalog/configuration and operator identity survive; consumed and dependent facts do not.';

COMMENT ON TABLE public.dts_pipeline_reset_audits IS 'One-shot destructive DTS reset evidence. Catalog/configuration and operator identity survive; consumed and dependent facts do not.';

DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM pg_catalog.pg_stat_activity
            WHERE pid<>pg_catalog.pg_backend_pid()
              AND state IS DISTINCT FROM 'idle'
              AND usename IN (
                'tit_dts_ingest_runtime','tit_growth_app',
                'tit_teacher_crud','tide_support_ticket_owner'
              )
          ) THEN
            RAISE EXCEPTION 'DTS_RESET_REQUIRES_STOPPED_RUNTIMES'
              USING ERRCODE='55006';
          END IF;
          PERFORM pg_advisory_xact_lock(
            hashtextextended('tit:dts-single-pipeline-reset',0)
          );
        END
        $$;

SET CONSTRAINTS ALL IMMEDIATE;

DO $$
        DECLARE relation_list text;
        BEGIN
          WITH RECURSIVE root_names(name) AS (
            VALUES ('public.agent_decisions'),('public.audit_events'),('public.completion_correction_pointer_upgrade_archive'),('public.course_favorite_attributions'),('public.course_favorite_observations'),('public.data_import_batches'),('public.domain_aggregate_revisions'),('public.dts_dirty_key_dependencies'),('public.dts_dirty_key_inputs'),('public.dts_dirty_key_state_audits'),('public.dts_dirty_keys'),('public.dts_dirty_keys_legacy_archive_v80'),('public.dts_ingest_checkpoints'),('public.dts_ingest_events'),('public.dts_ingest_issues'),('public.dts_pipeline_bootstrap_audits'),('public.dts_pipeline_control'),('public.dts_projection_read_routes'),('public.dts_projection_switch_audits'),('public.dts_qualification_gate_commands'),('public.dts_source_partition_epochs'),('public.dts_source_profile_approvals_v2'),('public.dts_source_row_versions'),('public.dts_source_rows'),('public.dts_source_scope_commands'),('public.dts_source_scope_memberships'),('public.dts_source_scope_snapshots'),('public.dts_source_scope_states'),('public.dts_source_scope_transition_audits'),('public.dts_source_snapshot_desired_rows'),('public.dts_source_snapshot_fences'),('public.dts_source_snapshot_rows'),('public.dts_source_table_publish_generations'),('public.dts_teacher_time_recheck_audits'),('public.dts_teacher_time_recheck_results'),('public.dts_teacher_time_recheck_schedule'),('public.dts_v2_reconciliation_runs'),('public.fixed_task_instances_v2'),('public.fixed_task_status_events_v2'),('public.idempotency_records'),('public.lesson_dimension_scores'),('public.lesson_facts'),('public.lesson_score_component_settlements'),('public.lesson_score_results'),('public.lesson_source_region_backfill_manifest'),('public.lesson_source_region_migration_control'),('public.lesson_source_wide'),('public.notification_events'),('public.notifications'),('public.ops_case_recovery_events'),('public.ops_cases'),('public.ops_decisions'),('public.outbound_outputs'),('public.outbox_events'),('public.outbox_events_legacy_archive'),('public.personalized_trigger_matches'),('public.provider_calls'),('public.score_accounts'),('public.score_component_accounts'),('public.score_entries'),('public.score_entry_idempotency_aliases'),('public.source_course_complaints'),('public.source_course_fact_current'),('public.source_course_labels'),('public.source_course_participations'),('public.source_courses'),('public.source_participation_fact_current'),('public.source_records'),('public.task_assignments'),('public.task_executions'),('public.task_runtime_events'),('public.task_status_callbacks_v2'),('public.teacher_metric_snapshots'),('public.teacher_qualifications'),('public.teacher_source_wide'),('public.teacher_student_relationship_current'),('public.teacher_student_relationship_events'),('public.teachers')
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
        $$;

SELECT set_config('tit.dts_initial_epoch_bootstrap','on',true);
        INSERT INTO public.dts_pipeline_control(
          control_id,mode,row_version,projection_generation,
          qualification_grants_enabled,consumer_group,initial_h0_vector,
          initial_h0_vector_hash,initial_h0_bootstrap_run_id,
          time_catchup_status,changed_at,changed_by
        )
        SELECT
          'PRIMARY','V2_PRIMARY',1,1,false,'SINGLE_PIPELINE',vector.value,
          public.dts_canonical_json_sha256_v1(vector.value),'20260824_101_dts_single_pipeline_reset',
          'NOT_REQUIRED',clock_timestamp(),'alembic:20260824_101_dts_single_pipeline_reset'
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
          'PRIMARY','V2','teacher-read-route-v1',2,'20260824_101_dts_single_pipeline_reset',
          clock_timestamp(),'alembic:20260824_101_dts_single_pipeline_reset'
        );
        SELECT set_config('tit.dts_projection_switch_v1','off',true);

        INSERT INTO public.dts_pipeline_reset_audits(
          reset_id,source_profile_manifest_sha256,history_policy,event_scope,
          reset_by
        ) VALUES (
          '20260824_101_dts_single_pipeline_reset','9bf93b1e7519e66d56bc97a975bc7fbd1f8500e8a15d91dcc06058ae5edc12fd',
          'CLEAR_ALL_CONSUMED_HISTORY','POST_CONFIGURED_START',
          'alembic:20260824_101_dts_single_pipeline_reset'
        );

CREATE OR REPLACE FUNCTION public.guard_dts_pipeline_control_v2()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path=pg_catalog,public
        AS $$
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
        $$;

CREATE OR REPLACE FUNCTION public.guard_dts_v2_checkpoint_identity()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path=pg_catalog,public
        AS $$
        DECLARE actor_name text:=coalesce(
          nullif(current_setting('role',true),'none'),session_user
        );
        BEGIN
          IF actor_name<>'tit_dts_ingest_runtime' THEN
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
        $$;

        CREATE FUNCTION public.initialize_dts_event_stream_v1(
          p_source_region text,p_topic text,p_partition integer,
          p_epoch_id text,p_consumer_group text,p_initial_offset bigint,
          p_source_timestamp bigint,p_source_position text,
          p_profile_manifest_sha256 text
        ) RETURNS text
        LANGUAGE plpgsql VOLATILE SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $$
        DECLARE existing_checkpoint public.dts_ingest_checkpoints%ROWTYPE;
        BEGIN
          IF session_user<>'tit_dts_ingest_runtime' THEN
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
             OR p_profile_manifest_sha256 !~ '^[0-9a-f]{64}$' THEN
            RAISE EXCEPTION 'DTS_STREAM_INIT_REQUEST_INVALID'
              USING ERRCODE='22023';
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM public.dts_pipeline_control
            WHERE control_id='PRIMARY' AND mode='V2_PRIMARY'
              AND projection_generation=1
          ) OR NOT EXISTS (
            SELECT 1 FROM public.dts_pipeline_reset_audits
            WHERE reset_id='20260824_101_dts_single_pipeline_reset'
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
        $$;

        REVOKE ALL ON FUNCTION public.initialize_dts_event_stream_v1(text,text,integer,text,text,bigint,bigint,text,text) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.initialize_dts_event_stream_v1(text,text,integer,text,text,bigint,bigint,text,text) TO tit_dts_ingest_runtime;
        COMMENT ON FUNCTION public.initialize_dts_event_stream_v1(text,text,integer,text,text,bigint,bigint,text,text) IS
          'Create one DOM/OVS stream epoch and checkpoint from the first post-start event after the one-shot history reset.';

CREATE OR REPLACE FUNCTION public.dts_projection_readiness_v1()
        RETURNS jsonb
        LANGUAGE plpgsql STABLE SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $$
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
        $$;

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

DO $$
        DECLARE relation regclass; remaining bigint;
        BEGIN
          FOR relation IN
            SELECT to_regclass(name)
            FROM (VALUES ('public.agent_decisions'),('public.audit_events'),('public.completion_correction_pointer_upgrade_archive'),('public.course_favorite_attributions'),('public.course_favorite_observations'),('public.data_import_batches'),('public.domain_aggregate_revisions'),('public.dts_dirty_key_dependencies'),('public.dts_dirty_key_inputs'),('public.dts_dirty_key_state_audits'),('public.dts_dirty_keys'),('public.dts_dirty_keys_legacy_archive_v80'),('public.dts_ingest_checkpoints'),('public.dts_ingest_events'),('public.dts_ingest_issues'),('public.dts_pipeline_bootstrap_audits'),('public.dts_pipeline_control'),('public.dts_projection_read_routes'),('public.dts_projection_switch_audits'),('public.dts_qualification_gate_commands'),('public.dts_source_partition_epochs'),('public.dts_source_profile_approvals_v2'),('public.dts_source_row_versions'),('public.dts_source_rows'),('public.dts_source_scope_commands'),('public.dts_source_scope_memberships'),('public.dts_source_scope_snapshots'),('public.dts_source_scope_states'),('public.dts_source_scope_transition_audits'),('public.dts_source_snapshot_desired_rows'),('public.dts_source_snapshot_fences'),('public.dts_source_snapshot_rows'),('public.dts_source_table_publish_generations'),('public.dts_teacher_time_recheck_audits'),('public.dts_teacher_time_recheck_results'),('public.dts_teacher_time_recheck_schedule'),('public.dts_v2_reconciliation_runs'),('public.fixed_task_instances_v2'),('public.fixed_task_status_events_v2'),('public.idempotency_records'),('public.lesson_dimension_scores'),('public.lesson_facts'),('public.lesson_score_component_settlements'),('public.lesson_score_results'),('public.lesson_source_region_backfill_manifest'),('public.lesson_source_region_migration_control'),('public.lesson_source_wide'),('public.notification_events'),('public.notifications'),('public.ops_case_recovery_events'),('public.ops_cases'),('public.ops_decisions'),('public.outbound_outputs'),('public.outbox_events'),('public.outbox_events_legacy_archive'),('public.personalized_trigger_matches'),('public.provider_calls'),('public.score_accounts'),('public.score_component_accounts'),('public.score_entries'),('public.score_entry_idempotency_aliases'),('public.source_course_complaints'),('public.source_course_fact_current'),('public.source_course_labels'),('public.source_course_participations'),('public.source_courses'),('public.source_participation_fact_current'),('public.source_records'),('public.task_assignments'),('public.task_executions'),('public.task_runtime_events'),('public.task_status_callbacks_v2'),('public.teacher_metric_snapshots'),('public.teacher_qualifications'),('public.teacher_source_wide'),('public.teacher_student_relationship_current'),('public.teacher_student_relationship_events'),('public.teachers')) AS roots(name)
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
        $$;

UPDATE alembic_version SET version_num='20260824_101_dts_single_pipeline_reset' WHERE alembic_version.version_num = '20260823_100_scope_snapshot_diff';

DO $$
BEGIN
    IF (SELECT count(*) FROM public.alembic_version) <> 1
       OR (SELECT version_num FROM public.alembic_version)
            IS DISTINCT FROM '20260824_101_dts_single_pipeline_reset'
       OR (public.dts_projection_readiness_v1()->>'code')
            IS DISTINCT FROM 'READY_SINGLE_PIPELINE'
       OR (SELECT count(*) FROM public.dts_pipeline_reset_audits) <> 1
       OR (SELECT count(*) FROM public.dts_ingest_checkpoints) <> 0
       OR (SELECT count(*) FROM public.dts_ingest_events) <> 0
       OR (SELECT count(*) FROM public.dts_source_rows) <> 0
       OR (SELECT count(*) FROM public.dts_source_row_versions) <> 0
       OR (SELECT count(*) FROM public.source_courses) <> 0
       OR (SELECT count(*) FROM public.teachers) <> 0
       OR (SELECT count(*) FROM public.task_assignments) <> 0
       OR (SELECT count(*) FROM public.score_entries) <> 0 THEN
        RAISE EXCEPTION 'public rev101 postflight verification failed';
    END IF;
END
$$;

COMMIT;

SELECT version_num FROM public.alembic_version;
SELECT * FROM public.dts_projection_readiness_v1();
SELECT reset_id, history_policy, event_scope, reset_at, reset_by
FROM public.dts_pipeline_reset_audits;
