"""route shared fixed-task settlement back through the DTS v2 projector.

Revision ID: 20260822_98_task_v2_refresh
Revises: 20260822_97_teacher_time_recheck
Create Date: 2026-08-22

The shared-task worker remains the sole writer of fixed-task ledger awards.
In V2_PRIMARY it must not also project teacher qualification rows with the
legacy algorithm.  This restricted command proves the pending shared-task
event and its ledger award, then appends an OPERATOR_RECOVERY input to the
existing TEACHER dirty key.  The normal domain/outbox path performs the only
teacher score and qualification rebuild.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from alembic import op


revision: str = "20260822_98_task_v2_refresh"
down_revision: Union[str, None] = "20260822_97_teacher_time_recheck"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


APP_ROLE = "tit_growth_app"


def upgrade() -> None:
    op.execute(
        f"""
        DO $shared_task_refresh_preflight$
        BEGIN
          IF to_regrole('{APP_ROLE}') IS NULL
             OR to_regclass('public.dts_pipeline_control') IS NULL
             OR to_regclass('public.dts_dirty_keys') IS NULL
             OR to_regclass('public.dts_dirty_key_inputs') IS NULL
             OR to_regclass('public.task_assignments') IS NULL
             OR to_regclass('public.score_entries') IS NULL
             OR to_regclass('public.outbox_events') IS NULL
             OR to_regprocedure(
               'public._upsert_dts_dirty_key_input_v2('
               'text,text,text,text,text,jsonb,bigint,text)'
             ) IS NULL
             OR to_regprocedure(
               'public.dts_canonical_json_sha256_v1(jsonb)'
             ) IS NULL THEN
            RAISE EXCEPTION 'DTS_V2_SHARED_TASK_REFRESH_PREFLIGHT_FAILED';
          END IF;
        END
        $shared_task_refresh_preflight$;

        CREATE FUNCTION public.enqueue_shared_task_teacher_refresh_v2(
          p_teacher_id text,p_assignment_id text,p_outbox_id text
        ) RETURNS jsonb
        LANGUAGE plpgsql
        VOLATILE
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE actor_name text:=coalesce(
          nullif(current_setting('role',true),'none'),session_user
        );
        DECLARE control_row public.dts_pipeline_control%ROWTYPE;
        DECLARE assignment_row public.task_assignments%ROWTYPE;
        DECLARE outbox_row public.outbox_events%ROWTYPE;
        DECLARE input_identity jsonb;
        DECLARE fixed_ledger jsonb;
        DECLARE input_fingerprint text;
        BEGIN
          IF actor_name<>'{APP_ROLE}'
             OR nullif(btrim(p_teacher_id),'') IS NULL
             OR nullif(btrim(p_assignment_id),'') IS NULL
             OR nullif(btrim(p_outbox_id),'') IS NULL
             OR p_teacher_id<>btrim(p_teacher_id)
             OR p_assignment_id<>btrim(p_assignment_id)
             OR p_outbox_id<>btrim(p_outbox_id) THEN
            RAISE EXCEPTION 'DTS_V2_SHARED_TASK_REFRESH_REQUEST_INVALID'
              USING ERRCODE='22023';
          END IF;
          PERFORM pg_advisory_xact_lock(544954,1);
          SELECT * INTO STRICT control_row
          FROM public.dts_pipeline_control
          WHERE control_id='PRIMARY' FOR SHARE;
          IF control_row.mode<>'V2_PRIMARY'
             OR control_row.projection_generation<1 THEN
            RAISE EXCEPTION 'DTS_V2_SHARED_TASK_REFRESH_NOT_PRIMARY'
              USING ERRCODE='55000';
          END IF;
          SELECT * INTO STRICT assignment_row
          FROM public.task_assignments
          WHERE assignment_id=p_assignment_id FOR SHARE;
          IF assignment_row.teacher_id<>p_teacher_id
             OR assignment_row.task_kind<>'FIXED_GROWTH'
             OR assignment_row.creator_system<>'TRIGGER_CENTER'
             OR assignment_row.source_mode<>'REAL'
             OR assignment_row.task_code NOT BETWEEN 'G01' AND 'G09'
             OR assignment_row.status<>'COMPLETED'
             OR assignment_row.completed_at IS NULL
             OR assignment_row.row_version<1 THEN
            RAISE EXCEPTION 'DTS_V2_SHARED_TASK_REFRESH_ASSIGNMENT_INVALID'
              USING ERRCODE='23514';
          END IF;
          SELECT * INTO STRICT outbox_row
          FROM public.outbox_events
          WHERE outbox_id=p_outbox_id FOR SHARE;
          IF outbox_row.aggregate_type<>'TASK_ASSIGNMENT'
             OR outbox_row.aggregate_id<>p_assignment_id
             OR outbox_row.event_type NOT IN (
               'task.assignment_changed.shared',
               'task.assignment_changed.shared.v1'
             )
             OR outbox_row.status<>'PENDING'
             OR outbox_row.payload->>'to_status'<>'COMPLETED' THEN
            RAISE EXCEPTION 'DTS_V2_SHARED_TASK_REFRESH_EVENT_INVALID'
              USING ERRCODE='23514';
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM public.score_entries
            WHERE teacher_id=p_teacher_id
              AND task_assignment_id=p_assignment_id
              AND entry_type='FIXED_TASK_AWARD'
              AND evidence_status='CONFIRMED'
              AND delta_score>0
          ) THEN
            RAISE EXCEPTION 'DTS_V2_SHARED_TASK_REFRESH_LEDGER_MISSING'
              USING ERRCODE='23503';
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM public.dts_dirty_key_inputs
            WHERE source_region='dom' AND key_type='TEACHER'
              AND key_part_1=p_teacher_id AND key_part_2=''
              AND input_kind IN ('SOURCE_REVISION','SCOPE_REVISION')
          ) THEN
            RAISE EXCEPTION 'DTS_V2_SHARED_TASK_REFRESH_CAUSAL_INPUT_MISSING'
              USING ERRCODE='23503';
          END IF;
          SELECT coalesce(
            jsonb_agg(
              jsonb_build_object(
                'score_entry_id',score_entry_id,
                'task_assignment_id',task_assignment_id,
                'delta_score',delta_score::text,
                'reason_code',reason_code,
                'score_rule_version',score_rule_version
              ) ORDER BY convert_to(score_entry_id,'UTF8')
            ),'[]'::jsonb
          ) INTO fixed_ledger
          FROM public.score_entries
          WHERE teacher_id=p_teacher_id
            AND entry_type='FIXED_TASK_AWARD';
          input_identity:=jsonb_build_object(
            'protocol_version','shared-task-teacher-refresh-v1',
            'source_region','dom','source_table','task_assignments',
            'teacher_id',p_teacher_id,
            'assignment_id',p_assignment_id,
            'outbox_id',p_outbox_id
          );
          input_fingerprint:=public.dts_canonical_json_sha256_v1(
            jsonb_build_object(
              'protocol_version','shared-task-teacher-refresh-v1',
              'input_identity',input_identity,
              'assignment_row_version',assignment_row.row_version,
              'assignment_status',assignment_row.status,
              'completed_at',assignment_row.completed_at,
              'outbox_payload_sha256',outbox_row.payload_sha256,
              'fixed_task_ledger',fixed_ledger,
              'projection_generation',control_row.projection_generation
            )
          );
          RETURN public._upsert_dts_dirty_key_input_v2(
            'dom','TEACHER',p_teacher_id,'','OPERATOR_RECOVERY',
            input_identity,assignment_row.row_version,input_fingerprint
          );
        EXCEPTION WHEN no_data_found OR too_many_rows THEN
          RAISE EXCEPTION 'DTS_V2_SHARED_TASK_REFRESH_REFERENCE_INVALID'
            USING ERRCODE='23503';
        END
        $function$;

        REVOKE ALL ON FUNCTION
          public.enqueue_shared_task_teacher_refresh_v2(text,text,text)
        FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION
          public.enqueue_shared_task_teacher_refresh_v2(text,text,text)
        TO {APP_ROLE};
        COMMENT ON FUNCTION
          public.enqueue_shared_task_teacher_refresh_v2(text,text,text) IS
          'V2_PRIMARY-only bridge from a proved pending fixed-task event and ledger award to the normal TEACHER domain/outbox rebuild; no direct qualification write';
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $shared_task_refresh_downgrade_guard$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM public.dts_pipeline_control
            WHERE control_id='PRIMARY' AND mode='V2_PRIMARY'
          ) THEN
            RAISE EXCEPTION
              'DTS_V2_SHARED_TASK_REFRESH_DOWNGRADE_REQUIRES_NON_PRIMARY';
          END IF;
        END
        $shared_task_refresh_downgrade_guard$;
        DROP FUNCTION IF EXISTS
          public.enqueue_shared_task_teacher_refresh_v2(text,text,text);
        """
    )
