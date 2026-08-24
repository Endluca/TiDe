"""make blacklist threshold materialization fail closed on incomplete evidence.

Revision ID: 20260822_99_blacklist_three_state
Revises: 20260822_98_task_v2_refresh
"""

from __future__ import annotations

from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260822_99_blacklist_three_state"
down_revision: Union[str, None] = "20260822_98_task_v2_refresh"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None


OUTBOX_ROLE = "tit_dts_outbox_worker_runtime"
OLD_COMMAND = (
    "public.reconcile_blacklist_threshold_v2("
    "text,text,bigint,bigint,text)"
)
NEW_COMMAND = (
    "public.reconcile_blacklist_threshold_v2("
    "text,text,bigint,bigint,text,jsonb)"
)
RETIRED_COMMAND = (
    "public.reconcile_blacklist_threshold_retired_v1("
    "text,text,bigint,bigint,text)"
)


def _preflight() -> None:
    row = op.get_bind().execute(
        sa.text(
            """
            SELECT to_regrole(:outbox_role) IS NOT NULL,
                   to_regprocedure(:old_command) IS NOT NULL,
                   to_regprocedure(:new_command) IS NULL,
                   to_regprocedure(:retired_command) IS NULL,
                   to_regclass(
                     'public.dts_source_scope_states'
                   ) IS NOT NULL,
                   to_regclass(
                     'public.dts_source_scope_snapshots'
                   ) IS NOT NULL,
                   to_regclass(
                     'public.domain_aggregate_revisions'
                   ) IS NOT NULL,
                   to_regclass('public.outbox_events') IS NOT NULL,
                   to_regclass(
                     'public.personalized_trigger_matches'
                   ) IS NOT NULL
            """
        ),
        {
            "outbox_role": OUTBOX_ROLE,
            "old_command": OLD_COMMAND,
            "new_command": NEW_COMMAND,
            "retired_command": RETIRED_COMMAND,
        },
    ).one()
    if row != (True,) * 9:
        raise RuntimeError(
            "DTS v2 blacklist three-state prerequisites are missing"
        )


def _install_three_state_reconciler() -> None:
    op.execute(
        r"""
        REVOKE ALL ON FUNCTION
          public.reconcile_blacklist_threshold_v2(
            text,text,bigint,bigint,text
          ) FROM PUBLIC,tit_dts_outbox_worker_runtime;
        ALTER FUNCTION public.reconcile_blacklist_threshold_v2(
          text,text,bigint,bigint,text
        ) RENAME TO reconcile_blacklist_threshold_retired_v1;

        CREATE FUNCTION public.reconcile_blacklist_threshold_v2(
          p_source_region text,p_teacher_id text,
          p_expected_teacher_student_revision bigint,
          p_projection_generation bigint,p_triggering_event_id text,
          p_threshold_evidence jsonb
        ) RETURNS jsonb
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE guarded_generation bigint;
        DECLARE aggregate_evidence jsonb;
        DECLARE scope_evidence jsonb;
        DECLARE threshold_state text;
        DECLARE evidence_status text;
        DECLARE collection_complete boolean;
        DECLARE active_count integer;
        DECLARE source_missing_count integer;
        DECLARE scope_found boolean;
        DECLARE live_scope_level text;
        DECLARE live_scope_key text;
        DECLARE live_scope_state text;
        DECLARE live_scope_row_version bigint;
        DECLARE live_snapshot_id text;
        DECLARE live_fence_hash text;
        DECLARE match_key text;
        DECLARE assignment_key text;
        DECLARE match_id text;
        DECLARE desired_status text;
        DECLARE desired_candidate_active boolean;
        DECLARE existing_match public.personalized_trigger_matches%ROWTYPE;
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
             OR btrim(p_triggering_event_id)=''
             OR jsonb_typeof(p_threshold_evidence)<>'object'
             OR p_threshold_evidence - ARRAY[
                  'protocol_version','source_region','teacher_id',
                  'teacher_id_type','threshold','threshold_state',
                  'evidence_status','source_collection_complete',
                  'distinct_active_student_count',
                  'source_missing_student_count',
                  'active_student_token_set_hash',
                  'source_missing_student_token_set_hash','scope'
                ]::text[] <> '{}'::jsonb
             OR p_threshold_evidence->>'protocol_version'
                  IS DISTINCT FROM 'blacklist-threshold-evidence-v1'
             OR p_threshold_evidence->>'source_region'
                  IS DISTINCT FROM p_source_region
             OR p_threshold_evidence->>'teacher_id'
                  IS DISTINCT FROM p_teacher_id
             OR p_threshold_evidence->>'teacher_id_type'
                  NOT IN ('NUMERIC','TEXT')
             OR jsonb_typeof(p_threshold_evidence->'threshold')
                  IS DISTINCT FROM 'number'
             OR p_threshold_evidence->>'threshold' IS DISTINCT FROM '2'
             OR p_threshold_evidence->>'threshold_state'
                  NOT IN ('ACTIVE','SUPPRESSED','SOURCE_MISSING')
             OR p_threshold_evidence->>'evidence_status'
                  NOT IN ('CONFIRMED','SOURCE_MISSING')
             OR jsonb_typeof(
                  p_threshold_evidence->'source_collection_complete'
                ) IS DISTINCT FROM 'boolean'
             OR jsonb_typeof(
                  p_threshold_evidence->'distinct_active_student_count'
                ) IS DISTINCT FROM 'number'
             OR p_threshold_evidence->>'distinct_active_student_count'
                  !~ '^(0|[1-9][0-9]*)$'
             OR jsonb_typeof(
                  p_threshold_evidence->'source_missing_student_count'
                ) IS DISTINCT FROM 'number'
             OR p_threshold_evidence->>'source_missing_student_count'
                  !~ '^(0|[1-9][0-9]*)$'
             OR p_threshold_evidence->>'active_student_token_set_hash'
                  !~ '^[0-9a-f]{64}$'
             OR p_threshold_evidence
                  ->>'source_missing_student_token_set_hash'
                  !~ '^[0-9a-f]{64}$'
             OR jsonb_typeof(p_threshold_evidence->'scope')
                  IS DISTINCT FROM 'object' THEN
            RAISE EXCEPTION 'DTS_V2_BLACKLIST_REQUEST_INVALID'
              USING ERRCODE='22023';
          END IF;

          SELECT aggregate.aggregate_state->'blacklist_threshold'
          INTO aggregate_evidence
          FROM public.outbox_events event
          JOIN public.domain_aggregate_revisions aggregate
            ON aggregate.aggregate_type='TEACHER_STUDENT'
           AND aggregate.aggregate_id=event.aggregate_id
           AND aggregate.revision=
                 p_expected_teacher_student_revision
          WHERE event.event_id=p_triggering_event_id
            AND event.aggregate_type='TEACHER_STUDENT'
            AND event.event_type='source_wide.changed.v2'
            AND aggregate.canonical_key->>'source_region'=p_source_region
            AND aggregate.canonical_key->>'teacher_id'=p_teacher_id;
          IF NOT FOUND OR aggregate_evidence IS DISTINCT FROM
             p_threshold_evidence THEN
            RAISE EXCEPTION 'DTS_V2_BLACKLIST_AGGREGATE_MISMATCH'
              USING ERRCODE='40001';
          END IF;

          threshold_state:=p_threshold_evidence->>'threshold_state';
          evidence_status:=p_threshold_evidence->>'evidence_status';
          collection_complete:=(p_threshold_evidence
            ->>'source_collection_complete')::boolean;
          active_count:=(p_threshold_evidence
            ->>'distinct_active_student_count')::integer;
          source_missing_count:=(p_threshold_evidence
            ->>'source_missing_student_count')::integer;
          IF NOT (
            (threshold_state='ACTIVE' AND evidence_status='CONFIRMED'
              AND active_count>=2)
            OR (threshold_state='SUPPRESSED'
              AND evidence_status='CONFIRMED' AND active_count<2
              AND collection_complete AND source_missing_count=0)
            OR (threshold_state='SOURCE_MISSING'
              AND evidence_status='SOURCE_MISSING' AND active_count<2
              AND (NOT collection_complete OR source_missing_count>0))
          ) THEN
            RAISE EXCEPTION 'DTS_V2_BLACKLIST_THRESHOLD_BOUNDARY_INVALID'
              USING ERRCODE='23514';
          END IF;

          scope_evidence:=p_threshold_evidence->'scope';
          IF scope_evidence->>'source_table' IS DISTINCT FROM
               p_source_region || '_teacher_blacklist'
             OR scope_evidence->>'scope_kind' IS DISTINCT FROM 'CURRENT'
             OR collection_complete IS DISTINCT FROM
               (scope_evidence->>'state'='COMPLETE') THEN
            RAISE EXCEPTION 'DTS_V2_BLACKLIST_SCOPE_INVALID'
              USING ERRCODE='23514';
          END IF;
          SELECT state.scope_level,state.scope_key,state.state,
                 state.row_version,state.active_snapshot_id,
                 snapshot.snapshot_fence_hash
          INTO live_scope_level,live_scope_key,live_scope_state,
               live_scope_row_version,live_snapshot_id,live_fence_hash
          FROM public.dts_source_scope_states state
          LEFT JOIN public.dts_source_scope_snapshots snapshot
            ON snapshot.snapshot_id=state.active_snapshot_id
           AND snapshot.source_region=state.source_region
           AND snapshot.source_table=state.source_table
           AND snapshot.scope_kind=state.scope_kind
           AND snapshot.scope_level=state.scope_level
           AND snapshot.scope_key=state.scope_key
          WHERE state.source_region=p_source_region
            AND state.source_table=
                  p_source_region || '_teacher_blacklist'
            AND state.scope_kind='CURRENT'
            AND ((state.scope_level='TEACHER'
                  AND state.scope_key=p_teacher_id)
              OR (state.scope_level='GLOBAL' AND state.scope_key='*'))
          ORDER BY CASE state.scope_level WHEN 'TEACHER' THEN 0 ELSE 1 END
          LIMIT 1 FOR SHARE OF state;
          scope_found:=FOUND;
          IF scope_found THEN
            IF scope_evidence->>'scope_level'
                 IS DISTINCT FROM live_scope_level
               OR scope_evidence->>'scope_key'
                 IS DISTINCT FROM live_scope_key
               OR scope_evidence->>'state'
                 IS DISTINCT FROM live_scope_state
               OR scope_evidence->>'row_version'
                 IS DISTINCT FROM live_scope_row_version::text
               OR scope_evidence->>'active_snapshot_id'
                 IS DISTINCT FROM live_snapshot_id
               OR scope_evidence->>'active_fence_hash'
                 IS DISTINCT FROM live_fence_hash THEN
              RAISE EXCEPTION 'DTS_V2_BLACKLIST_SCOPE_CHANGED'
                USING ERRCODE='40001';
            END IF;
          ELSIF scope_evidence->>'scope_level' IS NOT NULL
             OR scope_evidence->>'scope_key' IS NOT NULL
             OR scope_evidence->>'state' IS DISTINCT FROM 'UNKNOWN'
             OR scope_evidence->>'row_version' IS NOT NULL
             OR scope_evidence->>'active_snapshot_id' IS NOT NULL
             OR scope_evidence->>'active_fence_hash' IS NOT NULL THEN
            RAISE EXCEPTION 'DTS_V2_BLACKLIST_SCOPE_CHANGED'
              USING ERRCODE='40001';
          END IF;

          PERFORM 1 FROM public.teachers
          WHERE teacher_id=p_teacher_id FOR SHARE;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'DTS_V2_BLACKLIST_TEACHER_MISSING'
              USING ERRCODE='23503';
          END IF;

          match_key:='blacklist-threshold:' || p_source_region || ':' ||
            p_teacher_id;
          assignment_key:='personalized:P-FB-BLACKLIST:' || p_teacher_id;
          match_id:='TRM-V2-' || substring(encode(sha256(
            convert_to(match_key,'UTF8')),'hex') for 40);
          SELECT * INTO existing_match
          FROM public.personalized_trigger_matches
          WHERE dedupe_key=match_key FOR UPDATE;
          IF threshold_state='ACTIVE' THEN
            desired_status:=CASE
              WHEN FOUND AND existing_match.task_assignment_id IS NOT NULL
                THEN 'MATERIALIZED'
              ELSE 'MATCHED' END;
            desired_candidate_active:=true;
          ELSIF threshold_state='SUPPRESSED' THEN
            desired_status:='SUPPRESSED';
            desired_candidate_active:=false;
          ELSIF FOUND THEN
            -- Missing evidence may advance provenance, but it must never
            -- demote a previously confirmed MATCHED/MATERIALIZED fact.
            desired_status:=existing_match.match_status;
            desired_candidate_active:=
              existing_match.source_candidate_active;
          ELSE
            desired_status:='PENDING_DATA';
            desired_candidate_active:=false;
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
            match_id,'TR-FB-BLACKLIST','dts-direct-v2',p_teacher_id,
            NULL,NULL,NULL,match_key,'TEACHER_TASK',
            'Blacklist prevention improvement',NULL,desired_status,
            p_threshold_evidence,transaction_timestamp(),NULL,
            transaction_timestamp(),p_source_region,NULL,NULL,
            'BLACKLIST_THRESHOLD',1,transaction_timestamp(),
            'P-FB-BLACKLIST',assignment_key,10,2,
            desired_candidate_active,p_threshold_evidence,
            public.dts_canonical_json_sha256_v1(p_threshold_evidence),
            'GENERAL',assignment_key,'trigger-match:' || match_key,NULL,
            CASE p_source_region WHEN 'dom' THEN 0 ELSE 1 END,'NONE',2,
            NULL,NULL,NULL,'NONE',2,NULL,NULL,
            convert_to(match_key,'UTF8'),'V2_LIVE',
            p_projection_generation,p_projection_generation,true,
            NULL,NULL,NULL,p_expected_teacher_student_revision,
            p_projection_generation,p_triggering_event_id
          ) ON CONFLICT (dedupe_key) DO UPDATE SET
            match_status=desired_status,
            source_candidate_active=desired_candidate_active,
            evidence_snapshot=excluded.evidence_snapshot,
            plan_evidence=excluded.plan_evidence,
            source_aggregate_revision=excluded.source_aggregate_revision,
            projection_generation=excluded.projection_generation,
            serving_projection_generation=
              excluded.serving_projection_generation,
            is_serving=true,
            triggering_event_id=excluded.triggering_event_id,
            match_revision=personalized_trigger_matches.match_revision+
              CASE WHEN personalized_trigger_matches.match_status
                    IS DISTINCT FROM desired_status
                OR personalized_trigger_matches.source_candidate_active
                    IS DISTINCT FROM desired_candidate_active
                OR personalized_trigger_matches.plan_evidence
                    IS DISTINCT FROM excluded.plan_evidence
                THEN 1 ELSE 0 END,
            last_transition_at=CASE WHEN
              personalized_trigger_matches.match_status
                  IS DISTINCT FROM desired_status
              OR personalized_trigger_matches.source_candidate_active
                  IS DISTINCT FROM desired_candidate_active
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
            'threshold_state',threshold_state,
            'evidence_status',evidence_status,
            'source_collection_complete',collection_complete,
            'distinct_active_student_count',active_count,
            'source_missing_student_count',source_missing_count
          );
        END
        $function$;
        REVOKE ALL ON FUNCTION
          public.reconcile_blacklist_threshold_v2(
            text,text,bigint,bigint,text,jsonb
          ) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION
          public.reconcile_blacklist_threshold_v2(
            text,text,bigint,bigint,text,jsonb
          ) TO tit_dts_outbox_worker_runtime;
        """
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("DTS v2 blacklist threshold requires PostgreSQL")
    _preflight()
    _install_three_state_reconciler()


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("DTS v2 blacklist threshold requires PostgreSQL")
    op.execute(
        r"""
        DO $blacklist_three_state_downgrade_guard$
        BEGIN
          IF EXISTS(
            SELECT 1 FROM public.personalized_trigger_matches
            WHERE match_kind='BLACKLIST_THRESHOLD'
              AND plan_evidence ? 'threshold_state'
          ) THEN
            RAISE EXCEPTION
              'refusing blacklist three-state downgrade: new evidence exists';
          END IF;
        END
        $blacklist_three_state_downgrade_guard$;
        REVOKE ALL ON FUNCTION
          public.reconcile_blacklist_threshold_v2(
            text,text,bigint,bigint,text,jsonb
          ) FROM PUBLIC,tit_dts_outbox_worker_runtime;
        DROP FUNCTION public.reconcile_blacklist_threshold_v2(
          text,text,bigint,bigint,text,jsonb
        );
        ALTER FUNCTION public.reconcile_blacklist_threshold_retired_v1(
          text,text,bigint,bigint,text
        ) RENAME TO reconcile_blacklist_threshold_v2;
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
