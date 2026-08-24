"""materialize and reconcile DTS v2 task-less outputs.

Revision ID: 20260822_93_non_task_outputs
Revises: 20260822_92_runtime_course_health
Create Date: 2026-08-22
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260822_93_non_task_outputs"
down_revision: Union[str, None] = "20260822_92_runtime_course_health"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


OUTBOX_ROLE = "tit_dts_outbox_worker_runtime"


def _preflight() -> None:
    op.execute(
        r"""
        DO $non_task_output_preflight$
        DECLARE relation_name text;
        BEGIN
          FOREACH relation_name IN ARRAY ARRAY[
            'personalized_trigger_matches','notifications',
            'notification_events','ops_cases','audit_events',
            'domain_aggregate_revisions','outbox_events'
          ] LOOP
            IF to_regclass('public.' || relation_name) IS NULL THEN
              RAISE EXCEPTION 'DTS_V2_NON_TASK_OUTPUT_PREREQUISITE_MISSING:%',
                relation_name;
            END IF;
          END LOOP;
          IF to_regprocedure(
               'public.dts_canonical_json_sha256_v1(jsonb)'
             ) IS NULL
             OR to_regprocedure(
               'public.dts_v2_runtime_primary_guard_v1(text)'
             ) IS NULL
             OR to_regprocedure(
               'public.reconcile_course_trigger_matches_v2(text,text,bigint,bigint,text,jsonb,text)'
             ) IS NULL
             OR to_regrole('tit_dts_outbox_worker_runtime') IS NULL THEN
            RAISE EXCEPTION 'DTS_V2_NON_TASK_OUTPUT_PREREQUISITE_MISSING';
          END IF;
          IF to_regprocedure(
               'public.reconcile_course_non_task_outputs_v2(text,text,bigint,bigint,text,text)'
             ) IS NOT NULL THEN
            RAISE EXCEPTION 'DTS_V2_NON_TASK_OUTPUT_ALREADY_INSTALLED';
          END IF;
        END
        $non_task_output_preflight$;

        LOCK TABLE public.personalized_trigger_matches,
          public.notifications,public.notification_events,
          public.ops_cases,public.audit_events
        IN SHARE ROW EXCLUSIVE MODE;
        """
    )


def _expand_notification_identity() -> None:
    op.alter_column(
        "notifications",
        "source_ref",
        type_=sa.String(length=768),
        existing_type=sa.String(length=256),
        existing_nullable=True,
        schema="public",
    )


def _install_history_helpers() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.append_dts_v2_notification_lifecycle_event_v1(
          p_notification_id text,p_delivery_status text,p_transition text,
          p_trigger_match_id text,p_triggering_event_id text,
          p_plan_evidence_hash text
        ) RETURNS void
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE event_payload jsonb;
        DECLARE request_hash_value text;
        DECLARE event_id_value text;
        DECLARE inserted_count integer;
        BEGIN
          IF p_delivery_status NOT IN ('STORED','CANCELLED')
             OR p_transition NOT IN (
               'CREATED_STORED','SOURCE_CANCELLED','SOURCE_RESTORED'
             )
             OR nullif(btrim(p_notification_id),'') IS NULL
             OR nullif(btrim(p_trigger_match_id),'') IS NULL
             OR nullif(btrim(p_triggering_event_id),'') IS NULL
             OR p_plan_evidence_hash !~ '^[0-9a-f]{64}$' THEN
            RAISE EXCEPTION 'DTS_V2_NOTIFICATION_EVENT_INVALID'
              USING ERRCODE='22023';
          END IF;
          event_payload:=jsonb_build_object(
            'protocol_version','notification-lifecycle-v2',
            'transition',p_transition,
            'trigger_match_id',p_trigger_match_id,
            'triggering_event_id',p_triggering_event_id,
            'plan_evidence_hash',p_plan_evidence_hash,
            'actor','DTS_V2_OUTBOX'
          );
          request_hash_value:=public.dts_canonical_json_sha256_v1(
            event_payload
          );
          event_id_value:='v2nevt:' || encode(sha256(convert_to(
            p_notification_id || ':' || request_hash_value,'UTF8'
          )),'hex');
          INSERT INTO public.notification_events(
            notification_event_id,notification_id,delivery_status,
            occurred_at,failure_reason,request_hash,payload
          ) VALUES (
            event_id_value,p_notification_id,p_delivery_status,
            transaction_timestamp(),NULL,request_hash_value,event_payload
          ) ON CONFLICT DO NOTHING;
          GET DIAGNOSTICS inserted_count=ROW_COUNT;
          IF inserted_count=0 AND NOT EXISTS(
            SELECT 1 FROM public.notification_events
            WHERE notification_event_id=event_id_value
              AND notification_id=p_notification_id
              AND delivery_status=p_delivery_status
              AND request_hash=request_hash_value
              AND payload=event_payload
          ) THEN
            RAISE EXCEPTION 'DTS_V2_NOTIFICATION_EVENT_CONFLICT'
              USING ERRCODE='23514';
          END IF;
        END
        $function$;

        CREATE FUNCTION public.append_dts_v2_case_lifecycle_audit_v1(
          p_case_id text,p_teacher_id text,p_event_type text,
          p_previous_status text,p_status text,p_trigger_match_id text,
          p_triggering_event_id text,p_plan_evidence_hash text
        ) RETURNS void
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE audit_payload jsonb;
        DECLARE audit_hash text;
        DECLARE audit_id text;
        DECLARE inserted_count integer;
        BEGIN
          IF p_event_type NOT IN (
               'DTS_V2_SEVERE_COMPLAINT_CASE_CREATED',
               'DTS_V2_SEVERE_COMPLAINT_CASE_SOURCE_CANCELLED',
               'DTS_V2_SEVERE_COMPLAINT_CASE_SOURCE_RESTORED'
             )
             OR nullif(btrim(p_case_id),'') IS NULL
             OR nullif(btrim(p_teacher_id),'') IS NULL
             OR nullif(btrim(p_status),'') IS NULL
             OR nullif(btrim(p_trigger_match_id),'') IS NULL
             OR nullif(btrim(p_triggering_event_id),'') IS NULL
             OR p_plan_evidence_hash !~ '^[0-9a-f]{64}$' THEN
            RAISE EXCEPTION 'DTS_V2_CASE_AUDIT_INVALID'
              USING ERRCODE='22023';
          END IF;
          audit_payload:=jsonb_build_object(
            'protocol_version','non-task-case-lifecycle-v1',
            'case_id',p_case_id,
            'previous_status',p_previous_status,
            'status',p_status,
            'trigger_match_id',p_trigger_match_id,
            'triggering_event_id',p_triggering_event_id,
            'plan_evidence_hash',p_plan_evidence_hash
          );
          audit_hash:=public.dts_canonical_json_sha256_v1(audit_payload);
          audit_id:='audit:v2-output:' || audit_hash;
          INSERT INTO public.audit_events(
            event_id,event_type,teacher_id,task_id,case_id,occurred_at,
            actor_type,payload_hash,payload
          ) VALUES (
            audit_id,p_event_type,p_teacher_id,NULL,p_case_id,
            transaction_timestamp(),'DTS_V2_OUTBOX',audit_hash,audit_payload
          ) ON CONFLICT DO NOTHING;
          GET DIAGNOSTICS inserted_count=ROW_COUNT;
          IF inserted_count=0 AND NOT EXISTS(
            SELECT 1 FROM public.audit_events
            WHERE event_id=audit_id AND event_type=p_event_type
              AND teacher_id=p_teacher_id AND case_id=p_case_id
              AND payload_hash=audit_hash AND payload=audit_payload
          ) THEN
            RAISE EXCEPTION 'DTS_V2_CASE_AUDIT_CONFLICT'
              USING ERRCODE='23514';
          END IF;
        END
        $function$;

        REVOKE ALL ON FUNCTION
          public.append_dts_v2_notification_lifecycle_event_v1(
            text,text,text,text,text,text
          ),
          public.append_dts_v2_case_lifecycle_audit_v1(
            text,text,text,text,text,text,text,text
          )
        FROM PUBLIC;
        """
    )


def _install_reconcile_command() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.reconcile_course_non_task_outputs_v2(
          p_source_region text,p_source_appoint_id text,
          p_expected_course_aggregate_revision bigint,
          p_projection_generation bigint,p_triggering_event_id text,
          p_expected_command_sha256 text
        ) RETURNS jsonb
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE guarded_generation bigint;
        DECLARE aggregate_row public.domain_aggregate_revisions%ROWTYPE;
        DECLARE command_payload jsonb;
        DECLARE command_hash text;
        DECLARE match_row public.personalized_trigger_matches%ROWTYPE;
        DECLARE notification_row public.notifications%ROWTYPE;
        DECLARE case_row public.ops_cases%ROWTYPE;
        DECLARE output_found boolean;
        DECLARE output_id_value text;
        DECLARE output_payload jsonb;
        DECLARE lifecycle_payload jsonb;
        DECLARE priority_value text;
        DECLARE transitioned boolean;
        DECLARE notifications_created integer:=0;
        DECLARE notifications_cancelled integer:=0;
        DECLARE notifications_restored integer:=0;
        DECLARE cases_created integer:=0;
        DECLARE cases_cancelled integer:=0;
        DECLARE cases_restored integer:=0;
        DECLARE matches_linked integer:=0;
        DECLARE unchanged_count integer:=0;
        BEGIN
          guarded_generation:=public.dts_v2_runtime_primary_guard_v1(
            'COURSE'
          );
          IF guarded_generation IS NULL
             OR guarded_generation<>p_projection_generation THEN
            RAISE EXCEPTION 'DTS_V2_NON_TASK_OUTPUT_GENERATION_CHANGED'
              USING ERRCODE='40001';
          END IF;
          IF p_source_region NOT IN ('dom','ovs')
             OR nullif(btrim(p_source_appoint_id),'') IS NULL
             OR p_expected_course_aggregate_revision<1
             OR p_projection_generation<1
             OR nullif(btrim(p_triggering_event_id),'') IS NULL
             OR p_expected_command_sha256 !~ '^[0-9a-f]{64}$' THEN
            RAISE EXCEPTION 'DTS_V2_NON_TASK_OUTPUT_REQUEST_INVALID'
              USING ERRCODE='22023';
          END IF;
          command_payload:=jsonb_build_object(
            'protocol_version','course-non-task-output-command-v1',
            'source_region',p_source_region,
            'source_appoint_id',p_source_appoint_id,
            'aggregate_revision',p_expected_course_aggregate_revision,
            'projection_generation',p_projection_generation,
            'triggering_event_id',p_triggering_event_id
          );
          command_hash:=public.dts_canonical_json_sha256_v1(command_payload);
          IF command_hash<>p_expected_command_sha256 THEN
            RAISE EXCEPTION 'DTS_V2_NON_TASK_OUTPUT_COMMAND_HASH_MISMATCH'
              USING ERRCODE='23514';
          END IF;

          SELECT * INTO aggregate_row
          FROM public.domain_aggregate_revisions
          WHERE aggregate_type='COURSE'
            AND canonical_key=jsonb_build_object(
              'source_region',p_source_region,
              'source_appoint_id',p_source_appoint_id
            )
          FOR SHARE;
          IF NOT FOUND
             OR aggregate_row.revision<>
                  p_expected_course_aggregate_revision THEN
            RAISE EXCEPTION 'DTS_V2_NON_TASK_OUTPUT_COURSE_REVISION_CHANGED'
              USING ERRCODE='40001';
          END IF;
          IF NOT EXISTS(
            SELECT 1 FROM public.outbox_events event
            WHERE event.event_id=p_triggering_event_id
              AND event.aggregate_type='COURSE'
              AND event.aggregate_id=aggregate_row.aggregate_id
              AND event.event_type='source_wide.changed.v2'
              AND (event.payload->>'aggregate_revision')::bigint=
                    p_expected_course_aggregate_revision
          ) THEN
            RAISE EXCEPTION 'DTS_V2_NON_TASK_OUTPUT_EVENT_MISMATCH'
              USING ERRCODE='23503';
          END IF;

          FOR match_row IN
            SELECT * FROM public.personalized_trigger_matches match
            WHERE match.source_region=p_source_region
              AND match.source_appoint_id=p_source_appoint_id
              AND match.match_kind IN (
                'COMPLAINT_NETWORK_NOTIFICATION','SEVERE_COMPLAINT',
                'CAMERA_OFF_NOTIFICATION'
              )
            ORDER BY match.dedupe_key_sort_bytes
            FOR UPDATE
          LOOP
            transitioned:=false;
            IF match_row.materialization_origin<>'V2_LIVE'
               OR match_row.created_projection_generation<1
               OR match_row.plan_evidence_hash !~ '^[0-9a-f]{64}$'
               OR public.dts_canonical_json_sha256_v1(
                    match_row.plan_evidence
                  )<>match_row.plan_evidence_hash
               OR match_row.output_key IS NULL
               OR nullif(btrim(match_row.output_key),'') IS NULL THEN
              RAISE EXCEPTION 'DTS_V2_NON_TASK_OUTPUT_MATCH_INVALID'
                USING ERRCODE='23514';
            END IF;
            IF match_row.match_kind='SEVERE_COMPLAINT' THEN
              IF match_row.output_type<>'OPS_CASE'
                 OR match_row.output_key NOT LIKE 'complaint-case:%' THEN
                RAISE EXCEPTION 'DTS_V2_NON_TASK_OUTPUT_MATCH_INVALID'
                  USING ERRCODE='23514';
              END IF;
            ELSIF match_row.output_type<>'NOTIFICATION'
               OR NOT (
                 (match_row.match_kind='COMPLAINT_NETWORK_NOTIFICATION'
                  AND match_row.output_key LIKE 'complaint-notification:%')
                 OR
                 (match_row.match_kind='CAMERA_OFF_NOTIFICATION'
                  AND match_row.output_key LIKE 'camera-notification:%')
               ) THEN
              RAISE EXCEPTION 'DTS_V2_NON_TASK_OUTPUT_MATCH_INVALID'
                USING ERRCODE='23514';
            END IF;

            PERFORM pg_advisory_xact_lock(hashtextextended(
              'DTS_V2_NON_TASK_OUTPUT:' || match_row.output_key,0
            ));
            IF match_row.match_status IN ('MATCHED','MATERIALIZED') THEN
              IF match_row.output_type='NOTIFICATION' THEN
                output_id_value:=match_row.notification_id;
                output_found:=false;
                IF output_id_value IS NOT NULL THEN
                  SELECT * INTO notification_row
                  FROM public.notifications
                  WHERE notification_id=output_id_value FOR UPDATE;
                  output_found:=FOUND;
                ELSE
                  SELECT * INTO notification_row
                  FROM public.notifications
                  WHERE source_ref=match_row.output_key FOR UPDATE;
                  output_found:=FOUND;
                  IF output_found THEN
                    output_id_value:=notification_row.notification_id;
                  END IF;
                END IF;
                IF NOT output_found THEN
                  output_id_value:='v2notif:' || encode(sha256(convert_to(
                    match_row.output_key,'UTF8'
                  )),'hex');
                  IF EXISTS(
                    SELECT 1 FROM public.notifications
                    WHERE notification_id=output_id_value
                  ) THEN
                    RAISE EXCEPTION 'DTS_V2_NOTIFICATION_ID_CONFLICT'
                      USING ERRCODE='23514';
                  END IF;
                  lifecycle_payload:=jsonb_build_object(
                    'auto_cancelled',false,
                    'trigger_match_id',match_row.trigger_match_id,
                    'source_ref',match_row.output_key
                  );
                  output_payload:=jsonb_build_object(
                    'protocol_version','dts-v2-taskless-notification-v1',
                    'title','In-Class Quality Alert',
                    'body',CASE match_row.match_kind
                      WHEN 'CAMERA_OFF_NOTIFICATION' THEN
                        'Evidence: the camera was off for this lesson. Please review the class setup and keep the camera on as required.'
                      ELSE
                        'Evidence: this lesson received a network or device complaint. Please review the class setup and connection quality.'
                      END,
                    'source_ref',match_row.output_key,
                    'rule_code',match_row.trigger_code,
                    'trigger_match_id',match_row.trigger_match_id,
                    'plan_evidence',match_row.plan_evidence,
                    'plan_evidence_hash',match_row.plan_evidence_hash,
                    'materialization_origin','DTS_V2',
                    'projection_generation',p_projection_generation,
                    'source_lifecycle',lifecycle_payload
                  );
                  INSERT INTO public.notifications(
                    notification_id,task_id,source_ref,teacher_id,channel,
                    priority,status,requested_at,stored_at,read_at,
                    clicked_at,response_due_at,failure_reason,payload
                  ) VALUES (
                    output_id_value,NULL,match_row.output_key,
                    match_row.teacher_id,'IN_APP','P1','STORED',
                    transaction_timestamp(),transaction_timestamp(),
                    NULL,NULL,NULL,NULL,output_payload
                  );
                  PERFORM public.append_dts_v2_notification_lifecycle_event_v1(
                    output_id_value,'STORED','CREATED_STORED',
                    match_row.trigger_match_id,p_triggering_event_id,
                    match_row.plan_evidence_hash
                  );
                  notifications_created:=notifications_created+1;
                  transitioned:=true;
                  SELECT * INTO notification_row
                  FROM public.notifications
                  WHERE notification_id=output_id_value;
                END IF;
                IF notification_row.source_ref IS DISTINCT FROM
                     match_row.output_key
                   OR notification_row.teacher_id IS DISTINCT FROM
                     match_row.teacher_id
                   OR notification_row.task_id IS NOT NULL THEN
                  RAISE EXCEPTION 'DTS_V2_NOTIFICATION_IDENTITY_CONFLICT'
                    USING ERRCODE='23514';
                END IF;
                IF notification_row.status='CANCELLED'
                   AND notification_row.payload->'source_lifecycle'
                         ->>'auto_cancelled'='true'
                   AND notification_row.payload->'source_lifecycle'
                         ->>'trigger_match_id'=match_row.trigger_match_id
                   AND notification_row.payload->'source_lifecycle'
                         ->>'source_ref'=match_row.output_key THEN
                  lifecycle_payload:=
                    notification_row.payload->'source_lifecycle' ||
                    jsonb_build_object(
                      'auto_cancelled',false,
                      'restored_at',transaction_timestamp(),
                      'restored_by_event_id',p_triggering_event_id
                    );
                  UPDATE public.notifications
                  SET status='STORED',failure_reason=NULL,
                      payload=payload || jsonb_build_object(
                        'source_lifecycle',lifecycle_payload
                      )
                  WHERE notification_id=output_id_value;
                  PERFORM public.append_dts_v2_notification_lifecycle_event_v1(
                    output_id_value,'STORED','SOURCE_RESTORED',
                    match_row.trigger_match_id,p_triggering_event_id,
                    match_row.plan_evidence_hash
                  );
                  notifications_restored:=notifications_restored+1;
                  transitioned:=true;
                END IF;
                IF match_row.notification_id IS NULL
                   OR match_row.match_status<>'MATERIALIZED' THEN
                  UPDATE public.personalized_trigger_matches
                  SET notification_id=output_id_value,
                      match_status='MATERIALIZED',
                      materialized_at=coalesce(
                        materialized_at,transaction_timestamp()
                      ),match_revision=match_revision+1,
                      last_transition_at=transaction_timestamp(),
                      updated_at=transaction_timestamp()
                  WHERE trigger_match_id=match_row.trigger_match_id;
                  matches_linked:=matches_linked+1;
                  transitioned:=true;
                END IF;
              ELSE
                output_id_value:=match_row.ops_case_id;
                output_found:=false;
                IF output_id_value IS NOT NULL THEN
                  SELECT * INTO case_row FROM public.ops_cases
                  WHERE case_id=output_id_value FOR UPDATE;
                  output_found:=FOUND;
                ELSE
                  SELECT * INTO case_row FROM public.ops_cases
                  WHERE source_ref=match_row.output_key FOR UPDATE;
                  output_found:=FOUND;
                  IF output_found THEN output_id_value:=case_row.case_id;
                  END IF;
                END IF;
                IF NOT output_found THEN
                  output_id_value:='v2case:' || encode(sha256(convert_to(
                    match_row.output_key,'UTF8'
                  )),'hex');
                  IF EXISTS(
                    SELECT 1 FROM public.ops_cases
                    WHERE case_id=output_id_value
                  ) THEN
                    RAISE EXCEPTION 'DTS_V2_CASE_ID_CONFLICT'
                      USING ERRCODE='23514';
                  END IF;
                  priority_value:=CASE
                    WHEN match_row.plan_evidence->>'severity_rank'='0'
                      THEN 'P0' ELSE 'P1' END;
                  lifecycle_payload:=jsonb_build_object(
                    'auto_cancelled',false,
                    'trigger_match_id',match_row.trigger_match_id,
                    'source_ref',match_row.output_key
                  );
                  output_payload:=jsonb_build_object(
                    'protocol_version','dts-v2-severe-complaint-case-v1',
                    'title','Severe Complaint Review',
                    'summary','A severe complaint requires operations review.',
                    'source_ref',match_row.output_key,
                    'rule_code',match_row.trigger_code,
                    'trigger_match_id',match_row.trigger_match_id,
                    'plan_evidence',match_row.plan_evidence,
                    'plan_evidence_hash',match_row.plan_evidence_hash,
                    'materialization_origin','DTS_V2',
                    'projection_generation',p_projection_generation,
                    'source_lifecycle',lifecycle_payload
                  );
                  INSERT INTO public.ops_cases(
                    case_id,case_type,teacher_id,task_id,priority,status,
                    source_reason,external_action_status,created_at,payload,
                    updated_at,source_ref,source_region,source_appoint_id,
                    case_revision,row_version,evidence_fingerprint,
                    recovery_evidence_count,last_recovery_event_id,
                    last_recovery_count,last_recovered_at
                  ) VALUES (
                    output_id_value,'SEVERE_COMPLAINT',
                    match_row.teacher_id,NULL,priority_value,'OPEN',
                    match_row.trigger_code,'OPS_REVIEW_REQUIRED',
                    transaction_timestamp(),output_payload,
                    transaction_timestamp(),match_row.output_key,
                    p_source_region,p_source_appoint_id,1,1,
                    match_row.plan_evidence_hash,0,NULL,NULL,NULL
                  );
                  PERFORM public.append_dts_v2_case_lifecycle_audit_v1(
                    output_id_value,match_row.teacher_id,
                    'DTS_V2_SEVERE_COMPLAINT_CASE_CREATED',NULL,'OPEN',
                    match_row.trigger_match_id,p_triggering_event_id,
                    match_row.plan_evidence_hash
                  );
                  cases_created:=cases_created+1;
                  transitioned:=true;
                  SELECT * INTO case_row FROM public.ops_cases
                  WHERE case_id=output_id_value;
                END IF;
                IF case_row.source_ref IS DISTINCT FROM match_row.output_key
                   OR case_row.case_type<>'SEVERE_COMPLAINT'
                   OR case_row.teacher_id IS DISTINCT FROM
                        match_row.teacher_id
                   OR case_row.source_region IS DISTINCT FROM p_source_region
                   OR case_row.source_appoint_id IS DISTINCT FROM
                        p_source_appoint_id THEN
                  RAISE EXCEPTION 'DTS_V2_CASE_IDENTITY_CONFLICT'
                    USING ERRCODE='23514';
                END IF;
                IF case_row.status='CANCELLED'
                   AND case_row.external_action_status=
                         'SOURCE_EVIDENCE_SUPERSEDED'
                   AND case_row.payload->'source_lifecycle'
                         ->>'auto_cancelled'='true'
                   AND case_row.payload->'source_lifecycle'
                         ->>'trigger_match_id'=match_row.trigger_match_id THEN
                  lifecycle_payload:=case_row.payload->'source_lifecycle' ||
                    jsonb_build_object(
                      'auto_cancelled',false,
                      'restored_at',transaction_timestamp(),
                      'restored_by_event_id',p_triggering_event_id
                    );
                  UPDATE public.ops_cases
                  SET status='OPEN',
                      external_action_status='OPS_REVIEW_REQUIRED',
                      payload=payload || jsonb_build_object(
                        'source_lifecycle',lifecycle_payload
                      ),case_revision=case_revision+1,
                      row_version=row_version+1,
                      updated_at=transaction_timestamp()
                  WHERE case_id=output_id_value;
                  PERFORM public.append_dts_v2_case_lifecycle_audit_v1(
                    output_id_value,match_row.teacher_id,
                    'DTS_V2_SEVERE_COMPLAINT_CASE_SOURCE_RESTORED',
                    'CANCELLED','OPEN',match_row.trigger_match_id,
                    p_triggering_event_id,match_row.plan_evidence_hash
                  );
                  cases_restored:=cases_restored+1;
                  transitioned:=true;
                END IF;
                IF match_row.ops_case_id IS NULL
                   OR match_row.match_status<>'MATERIALIZED' THEN
                  UPDATE public.personalized_trigger_matches
                  SET ops_case_id=output_id_value,
                      match_status='MATERIALIZED',
                      materialized_at=coalesce(
                        materialized_at,transaction_timestamp()
                      ),match_revision=match_revision+1,
                      last_transition_at=transaction_timestamp(),
                      updated_at=transaction_timestamp()
                  WHERE trigger_match_id=match_row.trigger_match_id;
                  matches_linked:=matches_linked+1;
                  transitioned:=true;
                END IF;
              END IF;
            ELSIF match_row.match_status='SUPPRESSED' THEN
              IF match_row.output_type='NOTIFICATION'
                 AND match_row.notification_id IS NOT NULL THEN
                SELECT * INTO notification_row
                FROM public.notifications
                WHERE notification_id=match_row.notification_id FOR UPDATE;
                IF NOT FOUND
                   OR notification_row.source_ref IS DISTINCT FROM
                        match_row.output_key
                   OR notification_row.teacher_id IS DISTINCT FROM
                        match_row.teacher_id
                   OR notification_row.task_id IS NOT NULL THEN
                  RAISE EXCEPTION 'DTS_V2_NOTIFICATION_IDENTITY_CONFLICT'
                    USING ERRCODE='23514';
                END IF;
                IF notification_row.status='STORED' THEN
                  lifecycle_payload:=coalesce(
                    notification_row.payload->'source_lifecycle',
                    '{}'::jsonb
                  ) || jsonb_build_object(
                    'auto_cancelled',true,
                    'cancelled_at',transaction_timestamp(),
                    'cancelled_by_event_id',p_triggering_event_id,
                    'trigger_match_id',match_row.trigger_match_id,
                    'source_ref',match_row.output_key
                  );
                  UPDATE public.notifications
                  SET status='CANCELLED',payload=payload ||
                    jsonb_build_object('source_lifecycle',lifecycle_payload)
                  WHERE notification_id=match_row.notification_id;
                  PERFORM public.append_dts_v2_notification_lifecycle_event_v1(
                    match_row.notification_id,'CANCELLED',
                    'SOURCE_CANCELLED',match_row.trigger_match_id,
                    p_triggering_event_id,match_row.plan_evidence_hash
                  );
                  notifications_cancelled:=notifications_cancelled+1;
                  transitioned:=true;
                END IF;
              ELSIF match_row.output_type='OPS_CASE'
                 AND match_row.ops_case_id IS NOT NULL THEN
                SELECT * INTO case_row FROM public.ops_cases
                WHERE case_id=match_row.ops_case_id FOR UPDATE;
                IF NOT FOUND
                   OR case_row.source_ref IS DISTINCT FROM match_row.output_key
                   OR case_row.case_type<>'SEVERE_COMPLAINT'
                   OR case_row.teacher_id IS DISTINCT FROM
                        match_row.teacher_id
                   OR case_row.source_region IS DISTINCT FROM p_source_region
                   OR case_row.source_appoint_id IS DISTINCT FROM
                        p_source_appoint_id THEN
                  RAISE EXCEPTION 'DTS_V2_CASE_IDENTITY_CONFLICT'
                    USING ERRCODE='23514';
                END IF;
                IF case_row.status='OPEN' THEN
                  lifecycle_payload:=coalesce(
                    case_row.payload->'source_lifecycle','{}'::jsonb
                  ) || jsonb_build_object(
                    'auto_cancelled',true,
                    'cancelled_at',transaction_timestamp(),
                    'cancelled_by_event_id',p_triggering_event_id,
                    'trigger_match_id',match_row.trigger_match_id,
                    'source_ref',match_row.output_key
                  );
                  UPDATE public.ops_cases
                  SET status='CANCELLED',
                      external_action_status=
                        'SOURCE_EVIDENCE_SUPERSEDED',
                      payload=payload || jsonb_build_object(
                        'source_lifecycle',lifecycle_payload
                      ),case_revision=case_revision+1,
                      row_version=row_version+1,
                      updated_at=transaction_timestamp()
                  WHERE case_id=match_row.ops_case_id;
                  PERFORM public.append_dts_v2_case_lifecycle_audit_v1(
                    match_row.ops_case_id,match_row.teacher_id,
                    'DTS_V2_SEVERE_COMPLAINT_CASE_SOURCE_CANCELLED',
                    'OPEN','CANCELLED',match_row.trigger_match_id,
                    p_triggering_event_id,match_row.plan_evidence_hash
                  );
                  cases_cancelled:=cases_cancelled+1;
                  transitioned:=true;
                END IF;
              END IF;
            ELSE
              RAISE EXCEPTION 'DTS_V2_NON_TASK_OUTPUT_MATCH_STATUS_INVALID'
                USING ERRCODE='23514';
            END IF;
            IF NOT transitioned THEN
              unchanged_count:=unchanged_count+1;
            END IF;
          END LOOP;

          RETURN jsonb_build_object(
            'protocol_version','course-non-task-output-v1',
            'command_sha256',command_hash,
            'counts',jsonb_build_object(
              'notifications_created',notifications_created,
              'notifications_cancelled',notifications_cancelled,
              'notifications_restored',notifications_restored,
              'cases_created',cases_created,
              'cases_cancelled',cases_cancelled,
              'cases_restored',cases_restored,
              'matches_linked',matches_linked,
              'unchanged',unchanged_count
            )
          );
        END
        $function$;

        REVOKE ALL ON FUNCTION
          public.reconcile_course_non_task_outputs_v2(
            text,text,bigint,bigint,text,text
          ) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION
          public.reconcile_course_non_task_outputs_v2(
            text,text,bigint,bigint,text,text
          ) TO tit_dts_outbox_worker_runtime;

        REVOKE INSERT,UPDATE,DELETE,TRUNCATE ON TABLE
          public.personalized_trigger_matches,public.notifications,
          public.notification_events,public.ops_cases,public.audit_events
        FROM tit_dts_outbox_worker_runtime;
        """
    )


def upgrade() -> None:
    _preflight()
    _expand_notification_identity()
    _install_history_helpers()
    _install_reconcile_command()


def downgrade() -> None:
    op.execute(
        r"""
        REVOKE ALL ON FUNCTION
          public.reconcile_course_non_task_outputs_v2(
            text,text,bigint,bigint,text,text
          ) FROM PUBLIC,tit_dts_outbox_worker_runtime;
        DROP FUNCTION public.reconcile_course_non_task_outputs_v2(
          text,text,bigint,bigint,text,text
        );
        DROP FUNCTION public.append_dts_v2_case_lifecycle_audit_v1(
          text,text,text,text,text,text,text,text
        );
        DROP FUNCTION public.append_dts_v2_notification_lifecycle_event_v1(
          text,text,text,text,text,text
        );
        DO $notification_source_ref_downgrade$
        BEGIN
          IF EXISTS(
            SELECT 1 FROM public.notifications
            WHERE length(source_ref)>256
          ) THEN
            RAISE EXCEPTION
              'DTS_V2_NOTIFICATION_SOURCE_REF_DOWNGRADE_BLOCKED';
          END IF;
        END
        $notification_source_ref_downgrade$;
        """
    )
    op.alter_column(
        "notifications",
        "source_ref",
        type_=sa.String(length=256),
        existing_type=sa.String(length=768),
        existing_nullable=True,
        schema="public",
    )
