"""activate durable favorite observation and attribution runtime.

Revision ID: 20260822_87_favorite_runtime
Revises: 20260822_86_ops_case_v2
Create Date: 2026-08-22

This revision keeps favorite as the dedicated rev74 settlement and never adds
it to the rev76 lesson components.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260822_87_favorite_runtime"
down_revision: Union[str, None] = "20260822_86_ops_case_v2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


OUTBOX_RUNTIME_ROLE = "tit_dts_outbox_worker_runtime"
RECOVERY_RUNTIME_ROLE = "tit_favorite_observation_recovery_runtime"
RULE_VERSION = "favorite-score-v1"


def _assert_preconditions() -> None:
    op.execute(
        r"""
        DO $favorite_runtime_preflight$
        BEGIN
          IF to_regclass('public.course_favorite_observations') IS NULL
             OR to_regclass('public.course_favorite_attributions') IS NULL
             OR to_regclass('public.teacher_student_relationship_events') IS NULL
             OR to_regclass('public.teacher_student_relationship_current') IS NULL
             OR to_regclass('public.source_courses') IS NULL
             OR to_regclass('public.source_course_participations') IS NULL
             OR to_regclass('public.dts_source_scope_states') IS NULL
             OR to_regclass('public.dts_source_scope_snapshots') IS NULL
             OR to_regclass('public.dts_source_rows') IS NULL
             OR to_regclass('public.score_entries') IS NULL
             OR to_regclass('public.teachers') IS NULL
             OR to_regclass('public.ops_cases') IS NULL
             OR to_regclass('public.idempotency_records') IS NULL
             OR to_regclass('public.dts_pipeline_control') IS NULL
             OR to_regprocedure(
                  'public.dts_canonical_json_sha256_v1(jsonb)'
                ) IS NULL
             OR to_regprocedure(
                  'public.dts_v2_typed_id_valid(text,text)'
                ) IS NULL
             OR to_regprocedure(
                  'public.dts_technical_source_ref_shape_valid_v2(text,text)'
                ) IS NULL
             OR to_regrole('tit_dts_outbox_worker_runtime') IS NULL THEN
            RAISE EXCEPTION 'DTS_V2_FAVORITE_RUNTIME_PREREQUISITE_MISSING';
          END IF;
          IF EXISTS (
            SELECT 1 FROM pg_proc
            WHERE pronamespace='public'::regnamespace
              AND proname IN (
                'materialize_favorite_observation_v2',
                'claim_favorite_observations_v2',
                'complete_favorite_observation_v2',
                'fail_favorite_observation_v2',
                'reap_expired_favorite_observations_v2'
              )
          ) THEN
            RAISE EXCEPTION 'DTS_V2_FAVORITE_RUNTIME_ALREADY_INSTALLED';
          END IF;
          IF EXISTS (
            SELECT reversal_of_score_entry_id
            FROM public.score_entries
            WHERE reversal_of_score_entry_id IS NOT NULL
            GROUP BY reversal_of_score_entry_id HAVING count(*)>1
          ) THEN
            RAISE EXCEPTION 'DTS_V2_SCORE_REVERSAL_DUPLICATE';
          END IF;
        END
        $favorite_runtime_preflight$;

        DO $favorite_recovery_role$
        BEGIN
          IF to_regrole('tit_favorite_observation_recovery_runtime') IS NULL THEN
            CREATE ROLE tit_favorite_observation_recovery_runtime
              LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE
              NOREPLICATION NOBYPASSRLS;
          END IF;
          IF EXISTS (
            SELECT 1 FROM pg_roles
            WHERE rolname='tit_favorite_observation_recovery_runtime'
              AND (NOT rolcanlogin OR rolinherit OR rolsuper OR rolcreatedb
                   OR rolcreaterole OR rolreplication OR rolbypassrls)
          ) THEN
            RAISE EXCEPTION
              'tit_favorite_observation_recovery_runtime must be a restricted NOINHERIT LOGIN role';
          END IF;
        END
        $favorite_recovery_role$;

        LOCK TABLE public.course_favorite_observations,
          public.course_favorite_attributions,public.score_entries
        IN SHARE ROW EXCLUSIVE MODE;
        """
    )


def _expand_constraints_and_indexes() -> None:
    op.alter_column(
        "score_entries",
        "idempotency_key",
        existing_type=sa.String(length=256),
        type_=sa.String(length=1024),
        existing_nullable=False,
        schema="public",
    )
    op.create_index(
        "uq_score_entries_reversal_once_v2",
        "score_entries",
        ["reversal_of_score_entry_id"],
        unique=True,
        schema="public",
        postgresql_where=sa.text("reversal_of_score_entry_id IS NOT NULL"),
    )
    op.drop_constraint(
        "ck_favorite_observation_evidence_shape",
        "course_favorite_observations",
        type_="check",
        schema="public",
    )
    op.create_check_constraint(
        "ck_favorite_observation_evidence_shape",
        "course_favorite_observations",
        "(status = 'CONFIRMED_TRUE' AND relation_state IS TRUE "
        "AND relation_evidence_status = 'CONFIRMED' "
        "AND relation_error_code IS NULL "
        "AND completed_evidence_revision = required_evidence_revision) OR "
        "(status = 'CONFIRMED_FALSE' AND relation_state IS FALSE "
        "AND relation_evidence_status = 'CONFIRMED' "
        "AND relation_error_code IS NULL "
        "AND completed_evidence_revision = required_evidence_revision) OR "
        "(status = 'WAITING_HISTORY' AND relation_state IS NULL "
        "AND relation_evidence_status = 'HISTORY_INCOMPLETE' "
        "AND relation_error_code = "
        "'PENDING_DATA:FAVORITE_HISTORY_INCOMPLETE' "
        "AND completed_evidence_revision = required_evidence_revision) OR "
        "(status = 'WAITING_EVIDENCE' AND relation_state IS NULL "
        "AND relation_evidence_status = 'SOURCE_MISSING' "
        "AND relation_error_code IN ("
        "'SOURCE_MISSING:FAVORITE_EFFECTIVE_TIME_MISSING',"
        "'SOURCE_CONFLICT:COURSE_COMPLETION_PENDING') "
        "AND completed_evidence_revision = required_evidence_revision) OR "
        "(status IN ('PENDING','EVALUATING','RETRY','DEAD') "
        "AND relation_state IS NULL "
        "AND relation_evidence_status = 'PENDING' "
        "AND relation_error_code IS NULL) OR "
        "status IN ('INVALIDATED','VOIDED')",
        schema="public",
    )
    op.create_index(
        "ix_relationship_events_favorite_old_pair_v2",
        "teacher_student_relationship_events",
        [
            "source_region",
            "old_teacher_id",
            "old_student_token",
            "source_table",
            "source_record_id_type",
            "source_record_id",
            "source_row_revision",
            "event_sequence",
        ],
        schema="public",
        postgresql_where=sa.text(
            "relationship_type='FAVORITE' AND old_teacher_id IS NOT NULL"
        ),
    )
    op.create_index(
        "ix_relationship_events_favorite_new_pair_v2",
        "teacher_student_relationship_events",
        [
            "source_region",
            "new_teacher_id",
            "new_student_token",
            "source_table",
            "source_record_id_type",
            "source_record_id",
            "source_row_revision",
            "event_sequence",
        ],
        schema="public",
        postgresql_where=sa.text(
            "relationship_type='FAVORITE' AND new_teacher_id IS NOT NULL"
        ),
    )
    op.create_index(
        "ix_source_courses_favorite_completion_pair_v2",
        "source_courses",
        [
            "source_region",
            "completion_teacher_id",
            "completion_student_token",
            "completion_end_time",
            "source_appoint_id",
        ],
        schema="public",
        postgresql_where=sa.text(
            "completion_participation_seq IS NOT NULL "
            "AND completion_end_time IS NOT NULL "
            "AND completion_voided_at IS NULL"
        ),
    )
    op.create_index(
        "ix_favorite_observation_pair_state_v2",
        "course_favorite_observations",
        [
            "source_region",
            "teacher_id",
            "student_token",
            "status",
            "observed_at",
            "source_appoint_id",
            "observation_revision",
        ],
        schema="public",
        postgresql_where=sa.text(
            "status NOT IN ('INVALIDATED','VOIDED')"
        ),
    )


def _install_evidence_helpers() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.favorite_runtime_mode_v1()
        RETURNS text
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE control_count integer;
        DECLARE control_mode text;
        BEGIN
          SELECT count(*),min(mode) INTO control_count,control_mode
          FROM public.dts_pipeline_control
          WHERE control_id='PRIMARY';
          IF control_count IS DISTINCT FROM 1
             OR control_mode NOT IN (
                  'V1_COMPAT_DUAL_CAPTURE','V2_PRIMARY','ROLLED_BACK'
                ) THEN
            RAISE EXCEPTION 'DTS_V2_FAVORITE_PIPELINE_CONTROL_INVALID'
              USING ERRCODE='55000';
          END IF;
          RETURN control_mode;
        END
        $function$;

        CREATE FUNCTION public.favorite_runtime_projection_generation_v1()
        RETURNS bigint
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE control_row public.dts_pipeline_control%ROWTYPE;
        BEGIN
          IF public.favorite_runtime_mode_v1() IS DISTINCT FROM 'V2_PRIMARY' THEN
            RAISE EXCEPTION 'DTS_V2_FAVORITE_RUNTIME_MODE_NOT_PRIMARY'
              USING ERRCODE='55000';
          END IF;
          SELECT * INTO STRICT control_row FROM public.dts_pipeline_control
          WHERE control_id='PRIMARY' FOR SHARE;
          IF control_row.projection_generation<1 THEN
            RAISE EXCEPTION 'DTS_V2_FAVORITE_RUNTIME_MODE_NOT_PRIMARY'
              USING ERRCODE='55000';
          END IF;
          RETURN control_row.projection_generation;
        END
        $function$;

        CREATE FUNCTION public.favorite_observation_evidence_fingerprint_v1(
          p_source_region text,p_source_appoint_id text,p_rule_version text
        )
        RETURNS text
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE course_row public.source_courses%ROWTYPE;
        DECLARE current_document jsonb;
        DECLARE events_document jsonb;
        DECLARE scope_document jsonb;
        DECLARE evidence_document jsonb;
        BEGIN
          IF p_source_region NOT IN ('dom','ovs')
             OR p_source_appoint_id IS NULL OR btrim(p_source_appoint_id)=''
             OR p_rule_version IS DISTINCT FROM 'favorite-score-v1' THEN
            RAISE EXCEPTION 'DTS_V2_FAVORITE_EVIDENCE_REQUEST_INVALID'
              USING ERRCODE='22023';
          END IF;
          SELECT * INTO course_row FROM public.source_courses
          WHERE source_region=p_source_region
            AND source_appoint_id=p_source_appoint_id FOR SHARE;
          IF NOT FOUND OR course_row.completion_participation_seq IS NULL
             OR course_row.completion_teacher_id IS NULL
             OR course_row.completion_teacher_id_type IS NULL
             OR course_row.completion_student_token IS NULL
             OR course_row.completion_end_time IS NULL THEN
            RAISE EXCEPTION 'DTS_V2_FAVORITE_COMPLETION_EVIDENCE_MISSING'
              USING ERRCODE='23514';
          END IF;

          SELECT jsonb_build_object(
            'row_version',rel.row_version,
            'is_favorited',rel.is_favorited,
            'effective_time_evidence_status',
              rel.effective_time_evidence_status,
            'last_event_sequence',rel.last_event_sequence,
            'last_source_row_revision',rel.last_source_row_revision,
            'last_source_partition_epoch_id',
              rel.last_source_partition_epoch_id,
            'last_topic',rel.last_topic,
            'last_partition_id',rel.last_partition_id,
            'last_offset_value',rel.last_offset_value
          ) INTO current_document
          FROM public.teacher_student_relationship_current rel
          WHERE rel.source_region=p_source_region
            AND rel.teacher_id=course_row.completion_teacher_id
            AND rel.teacher_id_type=course_row.completion_teacher_id_type
            AND rel.student_token=course_row.completion_student_token;

          WITH latest AS (
            SELECT DISTINCT ON (
              event.source_table,event.source_record_id_type,
              event.source_record_id
            ) event.*
            FROM public.teacher_student_relationship_events event
            WHERE event.source_region=p_source_region
              AND event.relationship_type='FAVORITE'
              AND ((event.old_teacher_id=course_row.completion_teacher_id
                    AND event.old_teacher_id_type=
                        course_row.completion_teacher_id_type
                    AND event.old_student_token=
                        course_row.completion_student_token)
                OR (event.new_teacher_id=course_row.completion_teacher_id
                    AND event.new_teacher_id_type=
                        course_row.completion_teacher_id_type
                    AND event.new_student_token=
                        course_row.completion_student_token))
            ORDER BY event.source_table,event.source_record_id_type,
              event.source_record_id,event.source_row_revision DESC,
              event.event_sequence DESC
          )
          SELECT coalesce(jsonb_agg(jsonb_build_object(
            'source_table',source_table,
            'source_record_id_type',source_record_id_type,
            'source_record_id',source_record_id,
            'source_row_revision',source_row_revision,
            'event_sequence',event_sequence,'operation',operation,
            'old_teacher_id',old_teacher_id,
            'old_teacher_id_type',old_teacher_id_type,
            'old_student_token',old_student_token,
            'new_teacher_id',new_teacher_id,
            'new_teacher_id_type',new_teacher_id_type,
            'new_student_token',new_student_token,
            'old_valid_start_at',old_valid_start_at,
            'old_valid_end_at',old_valid_end_at,
            'new_valid_start_at',new_valid_start_at,
            'new_valid_end_at',new_valid_end_at,
            'effective_time_evidence_status',
              effective_time_evidence_status,
            'source_timestamp',source_timestamp
          ) ORDER BY convert_to(source_table,'UTF8'),
            CASE source_record_id_type WHEN 'NUMERIC' THEN 0 ELSE 1 END,
            source_record_id_numeric NULLS LAST,
            convert_to(source_record_id,'UTF8'),event_sequence),'[]'::jsonb)
          INTO events_document FROM latest;

          WITH preferred AS (
            SELECT state.source_table,state.scope_kind,state.scope_level,
              state.scope_key,state.state,state.row_version,
              state.active_snapshot_id,snapshot.snapshot_fence_hash,
              snapshot.history_from,snapshot.history_through
            FROM public.dts_source_scope_states state
            LEFT JOIN public.dts_source_scope_snapshots snapshot
              ON snapshot.snapshot_id=state.active_snapshot_id
             AND snapshot.source_region=state.source_region
             AND snapshot.source_table=state.source_table
             AND snapshot.scope_kind=state.scope_kind
             AND snapshot.scope_level=state.scope_level
             AND snapshot.scope_key=state.scope_key
            WHERE state.source_region=p_source_region
              AND state.source_table=p_source_region || '_teacher_favorite'
              AND state.scope_kind='HISTORY'
              AND ((state.scope_level='TEACHER'
                    AND state.scope_key=course_row.completion_teacher_id)
                OR (state.scope_level='GLOBAL' AND state.scope_key='*'))
            ORDER BY CASE state.scope_level WHEN 'TEACHER' THEN 0 ELSE 1 END
            LIMIT 1
          )
          SELECT jsonb_build_object(
            'source_table',source_table,'scope_kind',scope_kind,
            'scope_level',scope_level,'scope_key',scope_key,'state',state,
            'row_version',row_version,'active_snapshot_id',active_snapshot_id,
            'active_fence_hash',snapshot_fence_hash,
            'history_from',history_from,'history_through',history_through
          ) INTO scope_document FROM preferred;
          scope_document := coalesce(
            scope_document,
            jsonb_build_object('state','UNKNOWN','row_version',NULL)
          );

          evidence_document := jsonb_build_object(
            'protocol_version','favorite-observation-evidence-v1',
            'rule_version',p_rule_version,
            'course',jsonb_build_object(
              'source_region',course_row.source_region,
              'source_appoint_id',course_row.source_appoint_id,
              'row_version',course_row.row_version,
              'completion_participation_seq',
                course_row.completion_participation_seq,
              'completion_teacher_id',course_row.completion_teacher_id,
              'completion_teacher_id_type',
                course_row.completion_teacher_id_type,
              'completion_student_token',
                course_row.completion_student_token,
              'completion_end_time',course_row.completion_end_time,
              'completion_source_revision',
                course_row.completion_source_revision,
              'completion_conflict_status',
                course_row.completion_conflict_status,
              'completion_voided_at',course_row.completion_voided_at,
              'evidence_status',course_row.evidence_status
            ),
            'relationship_current',current_document,
            'favorite_latest_events',events_document,
            'favorite_history_scope',scope_document
          );
          RETURN public.dts_canonical_json_sha256_v1(evidence_document);
        END
        $function$;

        REVOKE ALL ON FUNCTION
          public.favorite_runtime_mode_v1(),
          public.favorite_runtime_projection_generation_v1(),
          public.favorite_observation_evidence_fingerprint_v1(text,text,text)
        FROM PUBLIC;
        """
    )


def _install_score_helpers() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.dts_v2_favorite_score_entry_valid(
          p_score_entry_id text,p_entry_kind text,p_source_region text,
          p_source_appoint_id text,p_completion_participation_seq integer,
          p_teacher_id text,p_student_token text,p_observation_revision bigint,
          p_award_generation bigint,p_rule_version text,
          p_projection_generation bigint,p_expected_original_entry_id text
        )
        RETURNS boolean
        LANGUAGE sql
        STABLE
        SET search_path=pg_catalog,public
        AS $function$
          SELECT EXISTS (
            SELECT 1 FROM public.score_entries entry
            JOIN public.teachers teacher
              ON teacher.teacher_id=p_teacher_id
            WHERE entry.score_entry_id=p_score_entry_id
              AND entry.camp_enrollment_id=teacher.camp_enrollment_id
              AND entry.lesson_id IS NULL
              AND entry.source_region=p_source_region
              AND entry.source_appoint_id=p_source_appoint_id
              AND entry.participation_seq=p_completion_participation_seq
              AND entry.teacher_id=p_teacher_id
              AND entry.dimension='USER_FEEDBACK'
              AND entry.entry_type=CASE p_entry_kind
                    WHEN 'AWARD' THEN 'FAVORITE_AWARD'
                    WHEN 'REVERSAL' THEN 'FAVORITE_REVERSAL'
                  END
              AND entry.delta_score::numeric=CASE p_entry_kind
                    WHEN 'AWARD' THEN 5::numeric
                    WHEN 'REVERSAL' THEN -5::numeric
                  END
              AND entry.reason_code='FEEDBACK_FAVORITE'
              AND entry.evidence_status='CONFIRMED'
              AND entry.score_rule_version=p_rule_version
              AND entry.reversal_of_score_entry_id IS NOT DISTINCT FROM
                    CASE p_entry_kind
                      WHEN 'REVERSAL' THEN p_expected_original_entry_id
                      ELSE NULL
                    END
              AND entry.task_assignment_id IS NULL
              AND entry.projection_origin='V2_LIVE'
              AND entry.materialized_by_run_id IS NULL
              AND entry.projection_generation=p_projection_generation
              AND entry.idempotency_key=CASE p_entry_kind
                    WHEN 'AWARD' THEN
                      'favorite:' || p_source_region || ':' || p_teacher_id ||
                      ':' || p_student_token || ':' || p_source_appoint_id ||
                      ':' || 'obs' || p_observation_revision::text ||
                      ':' || 'gen' ||
                      p_award_generation::text || ':' || p_rule_version
                    WHEN 'REVERSAL' THEN
                      'favorite-reversal:' || p_expected_original_entry_id
                  END
              AND jsonb_typeof(entry.payload)='object'
              AND entry.payload=jsonb_build_object(
                    'settlement_contract','favorite-attribution-v2',
                    'entry_kind',p_entry_kind,
                    'source_region',p_source_region,
                    'source_appoint_id',p_source_appoint_id,
                    'completion_participation_seq',
                      p_completion_participation_seq,
                    'teacher_id',p_teacher_id,
                    'pair_sha256',public.dts_canonical_json_sha256_v1(
                      jsonb_build_object(
                        'source_region',p_source_region,
                        'teacher_id',p_teacher_id,
                        'student_token',p_student_token
                      )
                    ),
                    'observation_revision',p_observation_revision,
                    'award_generation',p_award_generation,
                    'rule_version',p_rule_version,
                    'original_score_entry_id',p_expected_original_entry_id
                  )
          )
        $function$;

        CREATE FUNCTION public.write_favorite_score_entry_v2(
          p_entry_kind text,p_source_region text,p_source_appoint_id text,
          p_completion_participation_seq integer,p_teacher_id text,
          p_student_token text,p_observation_revision bigint,
          p_award_generation bigint,p_rule_version text,
          p_projection_generation bigint,p_expected_original_entry_id text
        )
        RETURNS text
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE teacher_row public.teachers%ROWTYPE;
        DECLARE observation_row public.course_favorite_observations%ROWTYPE;
        DECLARE v_idempotency_key text;
        DECLARE v_score_entry_id text;
        DECLARE payload_document jsonb;
        DECLARE inserted_id text;
        BEGIN
          IF p_entry_kind NOT IN ('AWARD','REVERSAL')
             OR p_source_region NOT IN ('dom','ovs')
             OR p_source_appoint_id IS NULL OR btrim(p_source_appoint_id)=''
             OR p_teacher_id IS NULL OR btrim(p_teacher_id)=''
             OR p_student_token IS NULL OR p_student_token=''
             OR p_completion_participation_seq<1
             OR p_observation_revision<1 OR p_award_generation<1
             OR p_rule_version IS DISTINCT FROM 'favorite-score-v1'
             OR p_projection_generation IS DISTINCT FROM
                  public.favorite_runtime_projection_generation_v1()
             OR ((p_entry_kind='AWARD') <>
                  (p_expected_original_entry_id IS NULL)) THEN
            RAISE EXCEPTION 'DTS_V2_FAVORITE_SCORE_REQUEST_INVALID'
              USING ERRCODE='22023';
          END IF;
          SELECT * INTO teacher_row FROM public.teachers
          WHERE teacher_id=p_teacher_id FOR SHARE;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'DTS_V2_FAVORITE_TEACHER_DEPENDENCY_PENDING'
              USING ERRCODE='23503';
          END IF;
          SELECT * INTO observation_row
          FROM public.course_favorite_observations
          WHERE source_region=p_source_region
            AND source_appoint_id=p_source_appoint_id
            AND observation_revision=p_observation_revision
            AND completion_participation_seq=p_completion_participation_seq
            AND teacher_id=p_teacher_id
            AND student_token=p_student_token
          FOR SHARE;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'DTS_V2_FAVORITE_SCORE_OBSERVATION_MISSING'
              USING ERRCODE='23503';
          END IF;
          IF p_entry_kind='AWARD' AND
             observation_row.status IS DISTINCT FROM 'CONFIRMED_TRUE' THEN
            RAISE EXCEPTION 'DTS_V2_FAVORITE_AWARD_EVIDENCE_INVALID'
              USING ERRCODE='23514';
          END IF;
          IF p_entry_kind='REVERSAL' AND NOT EXISTS (
            SELECT 1 FROM public.score_entries original
            WHERE original.score_entry_id=p_expected_original_entry_id
              AND original.entry_type='FAVORITE_AWARD'
              AND original.source_region=p_source_region
              AND original.source_appoint_id=p_source_appoint_id
              AND original.participation_seq=
                    p_completion_participation_seq
              AND original.teacher_id=p_teacher_id
              AND original.delta_score::numeric=5::numeric
              AND original.reversal_of_score_entry_id IS NULL
          ) THEN
            RAISE EXCEPTION 'DTS_V2_FAVORITE_REVERSAL_AWARD_INVALID'
              USING ERRCODE='23514';
          END IF;

          v_idempotency_key := CASE p_entry_kind
            WHEN 'AWARD' THEN
              'favorite:' || p_source_region || ':' || p_teacher_id || ':' ||
              p_student_token || ':' || p_source_appoint_id || ':' || 'obs' ||
              p_observation_revision::text || ':' || 'gen' ||
              p_award_generation::text || ':' || p_rule_version
            ELSE 'favorite-reversal:' || p_expected_original_entry_id
          END;
          v_score_entry_id := CASE p_entry_kind
            WHEN 'AWARD' THEN 'FAV-'
            ELSE 'FAVR-'
          END || encode(sha256(convert_to(v_idempotency_key,'UTF8')),'hex');
          payload_document := jsonb_build_object(
            'settlement_contract','favorite-attribution-v2',
            'entry_kind',p_entry_kind,'source_region',p_source_region,
            'source_appoint_id',p_source_appoint_id,
            'completion_participation_seq',p_completion_participation_seq,
            'teacher_id',p_teacher_id,
            'pair_sha256',public.dts_canonical_json_sha256_v1(
              jsonb_build_object(
                'source_region',p_source_region,'teacher_id',p_teacher_id,
                'student_token',p_student_token
              )
            ),
            'observation_revision',p_observation_revision,
            'award_generation',p_award_generation,
            'rule_version',p_rule_version,
            'original_score_entry_id',p_expected_original_entry_id
          );
          INSERT INTO public.score_entries(
            score_entry_id,camp_enrollment_id,lesson_id,source_region,
            source_appoint_id,participation_seq,teacher_id,dimension,
            entry_type,delta_score,reason_code,evidence_status,
            score_rule_version,occurred_at,recorded_at,
            reversal_of_score_entry_id,task_assignment_id,projection_origin,
            materialized_by_run_id,projection_generation,idempotency_key,payload
          ) VALUES (
            v_score_entry_id,teacher_row.camp_enrollment_id,NULL,p_source_region,
            p_source_appoint_id,p_completion_participation_seq,p_teacher_id,
            'USER_FEEDBACK',CASE p_entry_kind
              WHEN 'AWARD' THEN 'FAVORITE_AWARD'
              ELSE 'FAVORITE_REVERSAL' END,
            CASE p_entry_kind WHEN 'AWARD' THEN 5 ELSE -5 END,
            'FEEDBACK_FAVORITE','CONFIRMED',p_rule_version,
            observation_row.observed_at,transaction_timestamp(),
            p_expected_original_entry_id,NULL,'V2_LIVE',NULL,
            p_projection_generation,v_idempotency_key,payload_document
          ) ON CONFLICT (idempotency_key) DO NOTHING
          RETURNING public.score_entries.score_entry_id INTO inserted_id;
          IF inserted_id IS NULL THEN
            IF NOT public.dts_v2_favorite_score_entry_valid(
              v_score_entry_id,p_entry_kind,p_source_region,p_source_appoint_id,
              p_completion_participation_seq,p_teacher_id,p_student_token,
              p_observation_revision,p_award_generation,p_rule_version,
              p_projection_generation,p_expected_original_entry_id
            ) THEN
              RAISE EXCEPTION 'DTS_V2_FAVORITE_SCORE_IDEMPOTENCY_CONFLICT'
                USING ERRCODE='23505';
            END IF;
            inserted_id := v_score_entry_id;
          END IF;
          RETURN inserted_id;
        END
        $function$;

        REVOKE ALL ON FUNCTION
          public.dts_v2_favorite_score_entry_valid(
            text,text,text,text,integer,text,text,bigint,bigint,text,bigint,text
          ),
          public.write_favorite_score_entry_v2(
            text,text,text,integer,text,text,bigint,bigint,text,bigint,text
          )
        FROM PUBLIC;
        """
    )


def _install_attribution_helpers() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.favorite_attribution_outcome_v1(
          p_action text,p_source_region text,p_teacher_id text,
          p_student_token text,p_previous_source_appoint_id text,
          p_current_source_appoint_id text,p_award_generation bigint,
          p_score_entry_ids text[]
        )
        RETURNS jsonb
        LANGUAGE sql
        IMMUTABLE
        SET search_path=pg_catalog,public
        AS $function$
          SELECT jsonb_build_object(
            'action',p_action,'source_region',p_source_region,
            'teacher_id',p_teacher_id,'student_token',p_student_token,
            'previous_source_appoint_id',p_previous_source_appoint_id,
            'current_source_appoint_id',p_current_source_appoint_id,
            'award_generation',p_award_generation,
            'score_entry_ids',to_jsonb(coalesce(p_score_entry_ids,ARRAY[]::text[]))
          )
        $function$;

        CREATE FUNCTION public.hold_favorite_attribution_v2(
          p_source_region text,p_teacher_id text,p_teacher_id_type text,
          p_student_token text,p_source_appoint_id text,
          p_observation_revision bigint,p_hold_reason text
        )
        RETURNS jsonb
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE attribution_row public.course_favorite_attributions%ROWTYPE;
        BEGIN
          PERFORM public.favorite_runtime_projection_generation_v1();
          IF p_hold_reason NOT IN (
               'REVALIDATION_PENDING','WAITING_HISTORY','WAITING_EVIDENCE'
             ) THEN
            RAISE EXCEPTION 'DTS_V2_FAVORITE_HOLD_REASON_INVALID'
              USING ERRCODE='22023';
          END IF;
          SELECT * INTO attribution_row
          FROM public.course_favorite_attributions
          WHERE source_region=p_source_region AND teacher_id=p_teacher_id
            AND teacher_id_type=p_teacher_id_type
            AND student_token=p_student_token FOR UPDATE;
          IF NOT FOUND
             OR attribution_row.status NOT IN (
                  'AWARDED','AWARDED_PENDING_EVIDENCE'
                )
             OR attribution_row.source_appoint_id IS DISTINCT FROM
                  p_source_appoint_id
             OR attribution_row.observation_revision IS DISTINCT FROM
                  p_observation_revision THEN
            RETURN public.favorite_attribution_outcome_v1(
              'NONE',p_source_region,p_teacher_id,p_student_token,
              CASE WHEN FOUND THEN attribution_row.source_appoint_id ELSE NULL END,
              CASE WHEN FOUND THEN attribution_row.source_appoint_id ELSE NULL END,
              CASE WHEN FOUND THEN attribution_row.award_generation ELSE NULL END,
              ARRAY[]::text[]
            );
          END IF;
          IF attribution_row.status='AWARDED'
             OR attribution_row.hold_reason IS DISTINCT FROM p_hold_reason THEN
            UPDATE public.course_favorite_attributions
            SET status='AWARDED_PENDING_EVIDENCE',hold_reason=p_hold_reason,
                recompute_reason='FAVORITE_EVIDENCE_REVALIDATION',
                row_version=row_version+1,updated_at=clock_timestamp()
            WHERE source_region=p_source_region AND teacher_id=p_teacher_id
              AND student_token=p_student_token
              AND row_version=attribution_row.row_version;
            IF NOT FOUND THEN
              RAISE EXCEPTION 'DTS_V2_FAVORITE_ATTRIBUTION_STALE'
                USING ERRCODE='40001';
            END IF;
          END IF;
          RETURN public.favorite_attribution_outcome_v1(
            'HOLD',p_source_region,p_teacher_id,p_student_token,
            attribution_row.source_appoint_id,
            attribution_row.source_appoint_id,
            attribution_row.award_generation,ARRAY[]::text[]
          );
        END
        $function$;

        CREATE FUNCTION public.reconcile_favorite_attribution_v2(
          p_source_region text,p_teacher_id text,p_teacher_id_type text,
          p_student_token text,p_projection_generation bigint,
          p_rule_version text,p_recompute_reason text
        )
        RETURNS jsonb
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE attribution_row public.course_favorite_attributions%ROWTYPE;
        DECLARE referenced_observation public.course_favorite_observations%ROWTYPE;
        DECLARE candidate public.course_favorite_observations%ROWTYPE;
        DECLARE first_relevant public.course_favorite_observations%ROWTYPE;
        DECLARE referenced_effective_status text;
        DECLARE first_effective_status text;
        DECLARE candidate_found boolean := false;
        DECLARE first_relevant_found boolean := false;
        DECLARE attribution_found boolean := false;
        DECLARE old_appoint_id text;
        DECLARE reversal_id text;
        DECLARE award_id text;
        DECLARE next_generation bigint;
        DECLARE outcome_action text;
        DECLARE score_ids text[] := ARRAY[]::text[];
        DECLARE active_before boolean := false;
        BEGIN
          IF p_source_region NOT IN ('dom','ovs')
             OR p_teacher_id IS NULL OR btrim(p_teacher_id)=''
             OR p_teacher_id_type NOT IN ('NUMERIC','TEXT')
             OR NOT public.dts_v2_typed_id_valid(
                  p_teacher_id_type,p_teacher_id
                )
             OR p_student_token IS NULL OR p_student_token=''
             OR (p_source_region='dom' AND p_student_token !~
                  '^dom:v1:[0-9a-f]{64}$')
             OR p_projection_generation IS DISTINCT FROM
                  public.favorite_runtime_projection_generation_v1()
             OR p_rule_version IS DISTINCT FROM 'favorite-score-v1'
             OR p_recompute_reason IS NULL OR btrim(p_recompute_reason)='' THEN
            RAISE EXCEPTION 'DTS_V2_FAVORITE_RECONCILE_REQUEST_INVALID'
              USING ERRCODE='22023';
          END IF;
          PERFORM pg_advisory_xact_lock(hashtextextended(
            'tit:favorite-pair:' || p_source_region || ':' || p_teacher_id ||
            ':' || p_student_token,0
          ));
          SELECT * INTO attribution_row
          FROM public.course_favorite_attributions
          WHERE source_region=p_source_region AND teacher_id=p_teacher_id
            AND student_token=p_student_token FOR UPDATE;
          attribution_found := FOUND;
          IF attribution_found THEN
            IF attribution_row.teacher_id_type IS DISTINCT FROM
                 p_teacher_id_type THEN
              RAISE EXCEPTION 'DTS_V2_FAVORITE_TEACHER_TYPE_DRIFT'
                USING ERRCODE='23514';
            END IF;
            active_before := attribution_row.status IN (
              'AWARDED','AWARDED_PENDING_EVIDENCE'
            );
            old_appoint_id := attribution_row.source_appoint_id;
            IF active_before THEN
              SELECT * INTO referenced_observation
              FROM public.course_favorite_observations
              WHERE source_region=attribution_row.source_region
                AND source_appoint_id=attribution_row.source_appoint_id
                AND observation_revision=attribution_row.observation_revision
              FOR UPDATE;
              IF NOT FOUND THEN
                RAISE EXCEPTION 'DTS_V2_FAVORITE_ATTRIBUTION_OBSERVATION_MISSING'
                  USING ERRCODE='23503';
              END IF;
              SELECT CASE
                WHEN course.completion_conflict_status='PENDING'
                  OR course.evidence_status='SOURCE_CONFLICT'
                  OR course.completion_voided_at IS NOT NULL
                THEN 'WAITING_EVIDENCE'
                ELSE referenced_observation.status END
              INTO referenced_effective_status
              FROM public.source_courses course
              WHERE course.source_region=referenced_observation.source_region
                AND course.source_appoint_id=
                      referenced_observation.source_appoint_id;
              IF referenced_effective_status IS NULL THEN
                referenced_effective_status := 'WAITING_EVIDENCE';
              END IF;
              IF referenced_effective_status IN (
                   'PENDING','EVALUATING','RETRY','DEAD',
                   'WAITING_HISTORY','WAITING_EVIDENCE'
                 ) THEN
                IF attribution_row.status='AWARDED_PENDING_EVIDENCE' THEN
                  RETURN public.hold_favorite_attribution_v2(
                    p_source_region,p_teacher_id,p_teacher_id_type,
                    p_student_token,attribution_row.source_appoint_id,
                    attribution_row.observation_revision,
                    CASE referenced_effective_status
                      WHEN 'WAITING_HISTORY' THEN 'WAITING_HISTORY'
                      WHEN 'WAITING_EVIDENCE' THEN 'WAITING_EVIDENCE'
                      ELSE 'REVALIDATION_PENDING' END
                  );
                END IF;
                -- A completion conflict can become visible before its
                -- TEACHER_STUDENT event requeues the confirmed observation.
                -- Preserve the old award and never select a new course.
                RETURN public.favorite_attribution_outcome_v1(
                  'NONE',p_source_region,p_teacher_id,p_student_token,
                  attribution_row.source_appoint_id,
                  attribution_row.source_appoint_id,
                  attribution_row.award_generation,ARRAY[]::text[]
                );
              END IF;
            END IF;
          END IF;

          SELECT observation.* INTO candidate
          FROM public.course_favorite_observations observation
          JOIN public.source_courses course
            ON course.source_region=observation.source_region
           AND course.source_appoint_id=observation.source_appoint_id
          WHERE observation.source_region=p_source_region
            AND observation.teacher_id=p_teacher_id
            AND observation.teacher_id_type=p_teacher_id_type
            AND observation.student_token=p_student_token
            AND observation.status='CONFIRMED_TRUE'
            AND course.completion_conflict_status<>'PENDING'
            AND course.evidence_status<>'SOURCE_CONFLICT'
            AND course.completion_voided_at IS NULL
          ORDER BY observation.observed_at,
            CASE observation.appoint_id_type WHEN 'NUMERIC' THEN 0 ELSE 1 END,
            observation.appoint_id_numeric NULLS LAST,
            observation.appoint_id_text_sort NULLS LAST,
            observation.observation_revision
          LIMIT 1 FOR UPDATE OF observation;
          candidate_found := FOUND;

          SELECT observation.* INTO first_relevant
          FROM public.course_favorite_observations observation
          WHERE observation.source_region=p_source_region
            AND observation.teacher_id=p_teacher_id
            AND observation.teacher_id_type=p_teacher_id_type
            AND observation.student_token=p_student_token
            AND observation.status IN (
              'CONFIRMED_TRUE','PENDING','EVALUATING','RETRY','DEAD',
              'WAITING_HISTORY','WAITING_EVIDENCE'
            )
          ORDER BY observation.observed_at,
            CASE observation.appoint_id_type WHEN 'NUMERIC' THEN 0 ELSE 1 END,
            observation.appoint_id_numeric NULLS LAST,
            observation.appoint_id_text_sort NULLS LAST,
            observation.observation_revision
          LIMIT 1;
          first_relevant_found := FOUND;
          IF first_relevant_found THEN
            SELECT CASE
              WHEN course.source_appoint_id IS NULL
                OR course.completion_conflict_status='PENDING'
                OR course.evidence_status='SOURCE_CONFLICT'
                OR course.completion_voided_at IS NOT NULL
              THEN 'WAITING_EVIDENCE'
              ELSE first_relevant.status END
            INTO first_effective_status
            FROM (SELECT 1) singleton
            LEFT JOIN public.source_courses course
              ON course.source_region=first_relevant.source_region
             AND course.source_appoint_id=first_relevant.source_appoint_id;
          END IF;

          IF NOT candidate_found THEN
            IF first_relevant_found AND NOT active_before THEN
              RETURN public.favorite_attribution_outcome_v1(
                'NONE',p_source_region,p_teacher_id,p_student_token,
                CASE WHEN active_before THEN old_appoint_id ELSE NULL END,
                CASE WHEN active_before THEN old_appoint_id ELSE NULL END,
                CASE WHEN attribution_found
                     THEN attribution_row.award_generation ELSE NULL END,
                ARRAY[]::text[]
              );
            END IF;
          ELSIF first_relevant_found
             AND first_effective_status IS DISTINCT FROM 'CONFIRMED_TRUE' THEN
            IF active_before AND old_appoint_id=candidate.source_appoint_id THEN
              IF attribution_row.status='AWARDED_PENDING_EVIDENCE' THEN
                UPDATE public.course_favorite_attributions
                SET status='AWARDED',hold_reason=NULL,
                    recompute_reason=p_recompute_reason,
                    row_version=row_version+1,updated_at=clock_timestamp()
                WHERE source_region=p_source_region AND teacher_id=p_teacher_id
                  AND student_token=p_student_token;
                RETURN public.favorite_attribution_outcome_v1(
                  'RESTORE',p_source_region,p_teacher_id,p_student_token,
                  old_appoint_id,old_appoint_id,
                  attribution_row.award_generation,ARRAY[]::text[]
                );
              END IF;
              RETURN public.favorite_attribution_outcome_v1(
                'NONE',p_source_region,p_teacher_id,p_student_token,
                old_appoint_id,old_appoint_id,
                attribution_row.award_generation,ARRAY[]::text[]
              );
            END IF;
            candidate_found := false;
          END IF;

          IF active_before AND (
               NOT candidate_found
               OR ROW(attribution_row.source_appoint_id,
                      attribution_row.observation_revision)
                  IS DISTINCT FROM
                  ROW(candidate.source_appoint_id,
                      candidate.observation_revision)
             ) THEN
            reversal_id := public.write_favorite_score_entry_v2(
              'REVERSAL',attribution_row.source_region,
              attribution_row.source_appoint_id,
              attribution_row.completion_participation_seq,
              attribution_row.teacher_id,attribution_row.student_token,
              attribution_row.observation_revision,
              attribution_row.award_generation,attribution_row.rule_version,
              p_projection_generation,
              attribution_row.current_score_entry_id
            );
            UPDATE public.course_favorite_attributions
            SET status='REVERSED',hold_reason=NULL,
                last_reversal_score_entry_id=reversal_id,
                recompute_reason=p_recompute_reason,
                row_version=row_version+1,reversed_at=transaction_timestamp(),
                updated_at=clock_timestamp()
            WHERE source_region=p_source_region AND teacher_id=p_teacher_id
              AND student_token=p_student_token
              AND row_version=attribution_row.row_version;
            IF NOT FOUND THEN
              RAISE EXCEPTION 'DTS_V2_FAVORITE_ATTRIBUTION_STALE'
                USING ERRCODE='40001';
            END IF;
            score_ids := array_append(score_ids,reversal_id);
            SELECT * INTO attribution_row
            FROM public.course_favorite_attributions
            WHERE source_region=p_source_region AND teacher_id=p_teacher_id
              AND student_token=p_student_token FOR UPDATE;
            active_before := false;
            IF NOT candidate_found THEN
              RETURN public.favorite_attribution_outcome_v1(
                'REVERSE',p_source_region,p_teacher_id,p_student_token,
                old_appoint_id,NULL,attribution_row.award_generation,score_ids
              );
            END IF;
            outcome_action := 'RESELECT';
          END IF;

          IF NOT candidate_found THEN
            RETURN public.favorite_attribution_outcome_v1(
              'NONE',p_source_region,p_teacher_id,p_student_token,
              old_appoint_id,NULL,
              CASE WHEN attribution_row.teacher_id IS NULL
                   THEN NULL ELSE attribution_row.award_generation END,
              ARRAY[]::text[]
            );
          END IF;
          IF active_before THEN
            IF attribution_row.status='AWARDED_PENDING_EVIDENCE' THEN
              UPDATE public.course_favorite_attributions
              SET status='AWARDED',hold_reason=NULL,
                  recompute_reason=p_recompute_reason,
                  row_version=row_version+1,updated_at=clock_timestamp()
              WHERE source_region=p_source_region AND teacher_id=p_teacher_id
                AND student_token=p_student_token;
              outcome_action := 'RESTORE';
            ELSE
              outcome_action := 'NONE';
            END IF;
            RETURN public.favorite_attribution_outcome_v1(
              outcome_action,p_source_region,p_teacher_id,p_student_token,
              candidate.source_appoint_id,candidate.source_appoint_id,
              attribution_row.award_generation,ARRAY[]::text[]
            );
          END IF;

          next_generation := CASE WHEN attribution_row.teacher_id IS NULL
            THEN 1 ELSE attribution_row.award_generation+1 END;
          award_id := public.write_favorite_score_entry_v2(
            'AWARD',candidate.source_region,candidate.source_appoint_id,
            candidate.completion_participation_seq,candidate.teacher_id,
            candidate.student_token,candidate.observation_revision,
            next_generation,p_rule_version,p_projection_generation,NULL
          );
          score_ids := array_append(score_ids,award_id);
          IF attribution_row.teacher_id IS NULL THEN
            INSERT INTO public.course_favorite_attributions(
              source_region,teacher_id,teacher_id_type,student_token,
              source_appoint_id,observation_revision,
              completion_participation_seq,status,hold_reason,points,
              rule_version,award_generation,current_score_entry_id,
              last_reversal_score_entry_id,recompute_reason,
              materialization_origin,materialized_by_run_id,
              award_projection_generation,row_version,awarded_at,reversed_at,
              updated_at
            ) VALUES (
              p_source_region,p_teacher_id,p_teacher_id_type,p_student_token,
              candidate.source_appoint_id,candidate.observation_revision,
              candidate.completion_participation_seq,'AWARDED',NULL,5,
              p_rule_version,next_generation,award_id,NULL,
              p_recompute_reason,'V2_LIVE',NULL,p_projection_generation,1,
              transaction_timestamp(),NULL,clock_timestamp()
            );
            outcome_action := 'AWARD';
          ELSE
            UPDATE public.course_favorite_attributions
            SET source_appoint_id=candidate.source_appoint_id,
                observation_revision=candidate.observation_revision,
                completion_participation_seq=
                  candidate.completion_participation_seq,
                status='AWARDED',hold_reason=NULL,points=5,
                rule_version=p_rule_version,
                award_generation=next_generation,
                current_score_entry_id=award_id,
                recompute_reason=p_recompute_reason,
                materialization_origin='V2_LIVE',materialized_by_run_id=NULL,
                award_projection_generation=p_projection_generation,
                row_version=row_version+1,awarded_at=transaction_timestamp(),
                reversed_at=NULL,updated_at=clock_timestamp()
            WHERE source_region=p_source_region AND teacher_id=p_teacher_id
              AND student_token=p_student_token
              AND status='REVERSED'
              AND row_version=attribution_row.row_version;
            IF NOT FOUND THEN
              RAISE EXCEPTION 'DTS_V2_FAVORITE_ATTRIBUTION_STALE'
                USING ERRCODE='40001';
            END IF;
            outcome_action := coalesce(outcome_action,'AWARD');
          END IF;
          RETURN public.favorite_attribution_outcome_v1(
            outcome_action,p_source_region,p_teacher_id,p_student_token,
            old_appoint_id,candidate.source_appoint_id,next_generation,score_ids
          );
        END
        $function$;

        REVOKE ALL ON FUNCTION
          public.favorite_attribution_outcome_v1(
            text,text,text,text,text,text,bigint,text[]
          ),
          public.hold_favorite_attribution_v2(
            text,text,text,text,text,bigint,text
          ),
          public.reconcile_favorite_attribution_v2(
            text,text,text,text,bigint,text,text
          )
        FROM PUBLIC;
        """
    )


def _install_integrity_guards() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.assert_favorite_attribution_score_v2(
          p_source_region text,p_teacher_id text,p_student_token text
        )
        RETURNS void
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE attribution_row public.course_favorite_attributions%ROWTYPE;
        DECLARE reversal_row public.score_entries%ROWTYPE;
        DECLARE original_row public.score_entries%ROWTYPE;
        DECLARE pair_hash text;
        BEGIN
          SELECT * INTO attribution_row
          FROM public.course_favorite_attributions
          WHERE source_region=p_source_region AND teacher_id=p_teacher_id
            AND student_token=p_student_token;
          IF NOT FOUND THEN
            RETURN;
          END IF;
          IF NOT public.dts_v2_favorite_score_entry_valid(
               attribution_row.current_score_entry_id,'AWARD',
               attribution_row.source_region,
               attribution_row.source_appoint_id,
               attribution_row.completion_participation_seq,
               attribution_row.teacher_id,attribution_row.student_token,
               attribution_row.observation_revision,
               attribution_row.award_generation,
               attribution_row.rule_version,
               attribution_row.award_projection_generation,NULL
             ) THEN
            RAISE EXCEPTION 'DTS_V2_FAVORITE_ATTRIBUTION_AWARD_INVALID'
              USING ERRCODE='23514';
          END IF;
          IF attribution_row.last_reversal_score_entry_id IS NULL THEN
            IF attribution_row.status='REVERSED' THEN
              RAISE EXCEPTION 'DTS_V2_FAVORITE_ATTRIBUTION_REVERSAL_MISSING'
                USING ERRCODE='23514';
            END IF;
            RETURN;
          END IF;
          SELECT * INTO reversal_row FROM public.score_entries
          WHERE score_entry_id=attribution_row.last_reversal_score_entry_id;
          IF NOT FOUND OR reversal_row.entry_type IS DISTINCT FROM
               'FAVORITE_REVERSAL'
             OR reversal_row.dimension IS DISTINCT FROM 'USER_FEEDBACK'
             OR reversal_row.delta_score::numeric IS DISTINCT FROM
                  -5::numeric
             OR reversal_row.reason_code IS DISTINCT FROM
                  'FEEDBACK_FAVORITE'
             OR reversal_row.evidence_status IS DISTINCT FROM 'CONFIRMED'
             OR reversal_row.score_rule_version IS DISTINCT FROM
                  attribution_row.rule_version
             OR reversal_row.projection_origin IS DISTINCT FROM 'V2_LIVE'
             OR reversal_row.reversal_of_score_entry_id IS NULL
             OR reversal_row.idempotency_key IS DISTINCT FROM
                  'favorite-reversal:' ||
                  reversal_row.reversal_of_score_entry_id
             OR reversal_row.payload->>'settlement_contract' IS DISTINCT FROM
                  'favorite-attribution-v2'
             OR reversal_row.payload->>'entry_kind' IS DISTINCT FROM
                  'REVERSAL' THEN
            RAISE EXCEPTION 'DTS_V2_FAVORITE_ATTRIBUTION_REVERSAL_INVALID'
              USING ERRCODE='23514';
          END IF;
          SELECT * INTO original_row FROM public.score_entries
          WHERE score_entry_id=reversal_row.reversal_of_score_entry_id;
          pair_hash := public.dts_canonical_json_sha256_v1(
            jsonb_build_object(
              'source_region',attribution_row.source_region,
              'teacher_id',attribution_row.teacher_id,
              'student_token',attribution_row.student_token
            )
          );
          IF NOT FOUND
             OR original_row.entry_type IS DISTINCT FROM 'FAVORITE_AWARD'
             OR original_row.delta_score::numeric IS DISTINCT FROM 5::numeric
             OR original_row.payload->>'pair_sha256' IS DISTINCT FROM pair_hash
             OR reversal_row.payload->>'pair_sha256' IS DISTINCT FROM pair_hash
             OR reversal_row.source_region IS DISTINCT FROM
                  original_row.source_region
             OR reversal_row.source_appoint_id IS DISTINCT FROM
                  original_row.source_appoint_id
             OR reversal_row.participation_seq IS DISTINCT FROM
                  original_row.participation_seq
             OR reversal_row.teacher_id IS DISTINCT FROM
                  original_row.teacher_id THEN
            RAISE EXCEPTION 'DTS_V2_FAVORITE_ATTRIBUTION_REVERSAL_INVALID'
              USING ERRCODE='23514';
          END IF;
        END
        $function$;

        CREATE FUNCTION public.favorite_attribution_score_guard_v2()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        BEGIN
          PERFORM public.assert_favorite_attribution_score_v2(
            coalesce(NEW.source_region,OLD.source_region),
            coalesce(NEW.teacher_id,OLD.teacher_id),
            coalesce(NEW.student_token,OLD.student_token)
          );
          RETURN NULL;
        END
        $function$;

        CREATE CONSTRAINT TRIGGER ct_favorite_attribution_score_v2
        AFTER INSERT OR UPDATE OR DELETE
        ON public.course_favorite_attributions
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE FUNCTION public.favorite_attribution_score_guard_v2();

        REVOKE ALL ON FUNCTION
          public.assert_favorite_attribution_score_v2(text,text,text),
          public.favorite_attribution_score_guard_v2()
        FROM PUBLIC;
        """
    )


def _install_materialize_command() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.materialize_favorite_observation_v2(
          p_source_region text,p_source_appoint_id text,p_appoint_id_type text,
          p_teacher_id text,p_teacher_id_type text,p_student_token text,
          p_completion_participation_seq integer,p_observed_at timestamptz,
          p_rule_version text,p_projection_generation bigint,
          p_triggering_event_id text
        )
        RETURNS jsonb
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE course_row public.source_courses%ROWTYPE;
        DECLARE source_row public.dts_source_rows%ROWTYPE;
        DECLARE observation_row public.course_favorite_observations%ROWTYPE;
        DECLARE next_observation_revision bigint;
        DECLARE next_evidence_revision bigint;
        DECLARE evidence_fingerprint text;
        DECLARE outcome jsonb;
        DECLARE held boolean := false;
        DECLARE result_status text;
        DECLARE global_generation bigint;
        BEGIN
          global_generation :=
            public.favorite_runtime_projection_generation_v1();
          IF p_projection_generation IS DISTINCT FROM global_generation
             OR p_source_region NOT IN ('dom','ovs')
             OR p_source_appoint_id IS NULL OR btrim(p_source_appoint_id)=''
             OR p_appoint_id_type NOT IN ('NUMERIC','TEXT')
             OR NOT public.dts_v2_typed_id_valid(
                  p_appoint_id_type,p_source_appoint_id
                )
             OR p_teacher_id IS NULL OR btrim(p_teacher_id)=''
             OR p_teacher_id_type NOT IN ('NUMERIC','TEXT')
             OR NOT public.dts_v2_typed_id_valid(
                  p_teacher_id_type,p_teacher_id
                )
             OR p_student_token IS NULL OR p_student_token=''
             OR (p_source_region='dom' AND p_student_token !~
                  '^dom:v1:[0-9a-f]{64}$')
             OR p_completion_participation_seq<1
             OR p_observed_at IS NULL
             OR p_rule_version IS DISTINCT FROM 'favorite-score-v1'
             OR p_triggering_event_id IS NULL
             OR btrim(p_triggering_event_id)=''
             OR length(p_triggering_event_id)>512 THEN
            RAISE EXCEPTION 'DTS_V2_FAVORITE_MATERIALIZE_REQUEST_INVALID'
              USING ERRCODE='22023';
          END IF;
          PERFORM pg_advisory_xact_lock(hashtextextended(
            'tit:favorite-pair:' || p_source_region || ':' || p_teacher_id ||
            ':' || p_student_token,0
          ));
          SELECT * INTO course_row FROM public.source_courses
          WHERE source_region=p_source_region
            AND source_appoint_id=p_source_appoint_id FOR SHARE;
          IF NOT FOUND OR course_row.completion_voided_at IS NOT NULL
             OR ROW(
                  course_row.completion_participation_seq,
                  course_row.completion_teacher_id,
                  course_row.completion_teacher_id_type,
                  course_row.completion_student_token,
                  course_row.completion_end_time + interval '24 hours'
                ) IS DISTINCT FROM ROW(
                  p_completion_participation_seq,p_teacher_id,
                  p_teacher_id_type,p_student_token,p_observed_at
                ) THEN
            RAISE EXCEPTION 'DTS_V2_FAVORITE_COMPLETION_IDENTITY_MISMATCH'
              USING ERRCODE='23514';
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM public.source_course_participations part
            WHERE part.source_region=p_source_region
              AND part.source_appoint_id=p_source_appoint_id
              AND part.participation_seq=p_completion_participation_seq
              AND part.teacher_id=p_teacher_id
              AND part.teacher_id_type=p_teacher_id_type
              AND part.participation_role='COMPLETION'
          ) THEN
            RAISE EXCEPTION 'DTS_V2_FAVORITE_COMPLETION_PARTICIPATION_MISSING'
              USING ERRCODE='23503';
          END IF;
          SELECT * INTO source_row FROM public.dts_source_rows
          WHERE source_region=p_source_region
            AND source_table=p_source_region || '_appoint'
            AND source_key=p_source_appoint_id FOR SHARE;
          IF NOT FOUND OR source_row.provenance_state IS DISTINCT FROM
               'V2_CONFIRMED'
             OR source_row.source_key_type IS DISTINCT FROM p_appoint_id_type
             OR source_row.source_row_revision IS NULL THEN
            RAISE EXCEPTION 'DTS_V2_FAVORITE_APPOINT_TYPE_EVIDENCE_MISSING'
              USING ERRCODE='23514';
          END IF;
          evidence_fingerprint :=
            public.favorite_observation_evidence_fingerprint_v1(
              p_source_region,p_source_appoint_id,p_rule_version
            );
          SELECT * INTO observation_row
          FROM public.course_favorite_observations
          WHERE source_region=p_source_region
            AND source_appoint_id=p_source_appoint_id
            AND status NOT IN ('INVALIDATED','VOIDED')
          FOR UPDATE;
          IF NOT FOUND THEN
            SELECT coalesce(max(observation_revision),0)+1
            INTO next_observation_revision
            FROM public.course_favorite_observations
            WHERE source_region=p_source_region
              AND source_appoint_id=p_source_appoint_id;
            INSERT INTO public.course_favorite_observations(
              source_region,source_appoint_id,observation_revision,
              appoint_id_type,appoint_id_numeric,appoint_id_text_sort,
              teacher_id,teacher_id_type,student_token,
              completion_participation_seq,observed_at,relation_state,
              relation_evidence_status,relation_error_code,status,
              required_evidence_revision,claimed_evidence_revision,
              completed_evidence_revision,required_evidence_fingerprint,
              attempt_count,next_attempt_at,lease_owner,lease_token,
              lease_acquired_at,lease_expires_at,last_error,dead_generation,
              technical_case_id,terminal_reason,terminal_at,rule_version,
              materialization_origin,materialized_by_run_id,
              created_projection_generation,serving_projection_generation,
              is_serving,row_version,created_at,updated_at
            ) VALUES (
              p_source_region,p_source_appoint_id,next_observation_revision,
              p_appoint_id_type,
              CASE WHEN p_appoint_id_type='NUMERIC'
                   THEN p_source_appoint_id::numeric ELSE NULL END,
              CASE WHEN p_appoint_id_type='TEXT'
                   THEN convert_to(p_source_appoint_id,'UTF8') ELSE NULL END,
              p_teacher_id,p_teacher_id_type,p_student_token,
              p_completion_participation_seq,p_observed_at,NULL,'PENDING',NULL,
              'PENDING',1,NULL,0,evidence_fingerprint,0,p_observed_at,
              NULL,NULL,NULL,NULL,NULL,0,NULL,NULL,NULL,p_rule_version,
              'V2_LIVE',NULL,p_projection_generation,
              p_projection_generation,true,1,clock_timestamp(),
              clock_timestamp()
            );
            RETURN jsonb_build_object(
              'status','CREATED','source_region',p_source_region,
              'source_appoint_id',p_source_appoint_id,
              'observation_revision',next_observation_revision,
              'required_evidence_revision',1,
              'projection_generation',p_projection_generation,
              'attribution_held',false,'attribution_outcome',NULL
            );
          END IF;
          IF ROW(
               observation_row.appoint_id_type,observation_row.teacher_id,
               observation_row.teacher_id_type,observation_row.student_token,
               observation_row.completion_participation_seq,
               observation_row.observed_at,observation_row.rule_version
             ) IS DISTINCT FROM ROW(
               p_appoint_id_type,p_teacher_id,p_teacher_id_type,
               p_student_token,p_completion_participation_seq,p_observed_at,
               p_rule_version
             ) THEN
            RAISE EXCEPTION
              'DTS_V2_FAVORITE_OBSERVATION_CORRECTION_COMMAND_REQUIRED'
              USING ERRCODE='23514';
          END IF;
          IF observation_row.status='DEAD' THEN
            RETURN jsonb_build_object(
              'status','UNCHANGED','source_region',p_source_region,
              'source_appoint_id',p_source_appoint_id,
              'observation_revision',observation_row.observation_revision,
              'required_evidence_revision',
                observation_row.required_evidence_revision,
              'projection_generation',
                observation_row.serving_projection_generation,
              'attribution_held',
                EXISTS(SELECT 1 FROM public.course_favorite_attributions attr
                  WHERE attr.source_region=p_source_region
                    AND attr.teacher_id=p_teacher_id
                    AND attr.student_token=p_student_token
                    AND attr.source_appoint_id=p_source_appoint_id
                    AND attr.observation_revision=
                      observation_row.observation_revision
                    AND attr.status='AWARDED_PENDING_EVIDENCE'),
              'attribution_outcome',NULL
            );
          END IF;
          IF observation_row.required_evidence_fingerprint=
               evidence_fingerprint
             AND observation_row.serving_projection_generation=
               p_projection_generation THEN
            RETURN jsonb_build_object(
              'status','UNCHANGED','source_region',p_source_region,
              'source_appoint_id',p_source_appoint_id,
              'observation_revision',observation_row.observation_revision,
              'required_evidence_revision',
                observation_row.required_evidence_revision,
              'projection_generation',p_projection_generation,
              'attribution_held',
                EXISTS(SELECT 1 FROM public.course_favorite_attributions attr
                  WHERE attr.source_region=p_source_region
                    AND attr.teacher_id=p_teacher_id
                    AND attr.student_token=p_student_token
                    AND attr.source_appoint_id=p_source_appoint_id
                    AND attr.observation_revision=
                      observation_row.observation_revision
                    AND attr.status='AWARDED_PENDING_EVIDENCE'),
              'attribution_outcome',NULL
            );
          END IF;

          next_evidence_revision :=
            observation_row.required_evidence_revision+1;
          IF observation_row.status='EVALUATING' THEN
            UPDATE public.course_favorite_observations
            SET required_evidence_revision=next_evidence_revision,
                required_evidence_fingerprint=evidence_fingerprint,
                serving_projection_generation=p_projection_generation,
                row_version=row_version+1,updated_at=clock_timestamp()
            WHERE source_region=p_source_region
              AND source_appoint_id=p_source_appoint_id
              AND observation_revision=observation_row.observation_revision
              AND row_version=observation_row.row_version;
            IF NOT FOUND THEN
              RAISE EXCEPTION 'DTS_V2_FAVORITE_OBSERVATION_STALE'
                USING ERRCODE='40001';
            END IF;
            result_status := 'EVIDENCE_ADVANCED';
          ELSE
            UPDATE public.course_favorite_observations
            SET status='PENDING',relation_state=NULL,
                relation_evidence_status='PENDING',relation_error_code=NULL,
                required_evidence_revision=next_evidence_revision,
                claimed_evidence_revision=NULL,
                required_evidence_fingerprint=evidence_fingerprint,
                attempt_count=0,next_attempt_at=transaction_timestamp(),
                lease_owner=NULL,lease_token=NULL,lease_acquired_at=NULL,
                lease_expires_at=NULL,last_error=NULL,
                serving_projection_generation=p_projection_generation,
                is_serving=true,row_version=row_version+1,
                updated_at=clock_timestamp()
            WHERE source_region=p_source_region
              AND source_appoint_id=p_source_appoint_id
              AND observation_revision=observation_row.observation_revision
              AND row_version=observation_row.row_version;
            IF NOT FOUND THEN
              RAISE EXCEPTION 'DTS_V2_FAVORITE_OBSERVATION_STALE'
                USING ERRCODE='40001';
            END IF;
            result_status := 'REQUEUED';
            IF observation_row.status='CONFIRMED_TRUE' THEN
              outcome := public.hold_favorite_attribution_v2(
                p_source_region,p_teacher_id,p_teacher_id_type,
                p_student_token,p_source_appoint_id,
                observation_row.observation_revision,
                'REVALIDATION_PENDING'
              );
              held := outcome->>'action'='HOLD';
            END IF;
          END IF;
          RETURN jsonb_build_object(
            'status',result_status,'source_region',p_source_region,
            'source_appoint_id',p_source_appoint_id,
            'observation_revision',observation_row.observation_revision,
            'required_evidence_revision',next_evidence_revision,
            'projection_generation',p_projection_generation,
            'attribution_held',held,'attribution_outcome',outcome
          );
        END
        $function$;

        REVOKE ALL ON FUNCTION public.materialize_favorite_observation_v2(
          text,text,text,text,text,text,integer,timestamptz,text,bigint,text
        ) FROM PUBLIC;
        """
    )


def _install_case_helpers() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.record_favorite_observation_dead_case_v2(
          p_source_region text,p_source_appoint_id text,
          p_observation_revision bigint,p_dead_generation bigint,
          p_error_code text
        )
        RETURNS text
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE observation_row public.course_favorite_observations%ROWTYPE;
        DECLARE case_row public.ops_cases%ROWTYPE;
        DECLARE case_source_ref text;
        DECLARE case_id text;
        DECLARE stored_teacher_id text;
        DECLARE evidence jsonb;
        DECLARE evidence_hash text;
        DECLARE audit_payload jsonb;
        DECLARE audit_id text;
        BEGIN
          PERFORM public.favorite_runtime_projection_generation_v1();
          IF p_source_region NOT IN ('dom','ovs')
             OR p_source_appoint_id IS NULL OR btrim(p_source_appoint_id)=''
             OR p_observation_revision<1 OR p_dead_generation<1
             OR p_error_code !~ '^[A-Z][A-Z0-9_]*$'
             OR length(p_error_code)>128 THEN
            RAISE EXCEPTION 'DTS_V2_FAVORITE_DEAD_CASE_REQUEST_INVALID'
              USING ERRCODE='22023';
          END IF;
          SELECT * INTO observation_row
          FROM public.course_favorite_observations
          WHERE source_region=p_source_region
            AND source_appoint_id=p_source_appoint_id
            AND observation_revision=p_observation_revision FOR UPDATE;
          IF NOT FOUND OR observation_row.status IS DISTINCT FROM 'DEAD'
             OR observation_row.attempt_count IS DISTINCT FROM 8
             OR observation_row.dead_generation IS DISTINCT FROM
                  p_dead_generation THEN
            RAISE EXCEPTION 'DTS_V2_FAVORITE_DEAD_CASE_WORK_NOT_DEAD'
              USING ERRCODE='23514';
          END IF;
          case_source_ref := 'tech-case:favorite-observation:' ||
            p_source_region || ':' || p_source_appoint_id || ':' || 'r' ||
            p_observation_revision::text || ':' || 'g' ||
            p_dead_generation::text;
          IF NOT public.dts_technical_source_ref_shape_valid_v2(
               'FAVORITE_OBSERVATION_DEAD',case_source_ref
             ) THEN
            RAISE EXCEPTION 'DTS_V2_FAVORITE_DEAD_CASE_SOURCE_REF_INVALID'
              USING ERRCODE='23514';
          END IF;
          case_id := 'v2case:' ||
            encode(sha256(convert_to(case_source_ref,'UTF8')),'hex');
          SELECT teacher_id INTO stored_teacher_id FROM public.teachers
          WHERE teacher_id=observation_row.teacher_id;
          evidence := jsonb_build_object(
            'protocol_version','favorite-observation-dead-v2',
            'source_region',p_source_region,
            'source_appoint_id',p_source_appoint_id,
            'observation_revision',p_observation_revision,
            'dead_generation',p_dead_generation,
            'attempt_count',observation_row.attempt_count,
            'error_code',p_error_code,
            'required_evidence_revision',
              observation_row.required_evidence_revision,
            'required_evidence_fingerprint',
              observation_row.required_evidence_fingerprint,
            'pair_sha256',public.dts_canonical_json_sha256_v1(
              jsonb_build_object(
                'source_region',p_source_region,
                'teacher_id',observation_row.teacher_id,
                'student_token',observation_row.student_token
              )
            )
          );
          evidence_hash := public.dts_canonical_json_sha256_v1(evidence);
          SELECT * INTO case_row FROM public.ops_cases
          WHERE public.ops_cases.source_ref=case_source_ref FOR UPDATE;
          IF FOUND THEN
            IF case_row.case_id IS DISTINCT FROM case_id
               OR case_row.case_type IS DISTINCT FROM
                    'FAVORITE_OBSERVATION_DEAD' THEN
              RAISE EXCEPTION 'DTS_V2_FAVORITE_DEAD_CASE_CONFLICT'
                USING ERRCODE='23505';
            END IF;
            IF case_row.evidence_fingerprint IS NOT DISTINCT FROM
                 evidence_hash THEN
              RETURN case_id;
            END IF;
            UPDATE public.ops_cases
            SET teacher_id=coalesce(teacher_id,stored_teacher_id),
                source_reason='FAVORITE_OBSERVATION_DEAD',payload=evidence,
                evidence_fingerprint=evidence_hash,
                case_revision=case_revision+1,row_version=row_version+1,
                updated_at=clock_timestamp()
            WHERE case_id=case_row.case_id
              AND row_version=case_row.row_version;
            IF NOT FOUND THEN
              RAISE EXCEPTION 'DTS_V2_FAVORITE_DEAD_CASE_STALE'
                USING ERRCODE='40001';
            END IF;
          ELSE
            INSERT INTO public.ops_cases(
              case_id,case_type,teacher_id,task_id,priority,status,
              source_reason,external_action_status,created_at,payload,
              updated_at,source_ref,source_region,source_appoint_id,
              case_revision,row_version,evidence_fingerprint,
              recovery_evidence_count,last_recovery_event_id,
              last_recovery_count,last_recovered_at
            ) VALUES (
              case_id,'FAVORITE_OBSERVATION_DEAD',stored_teacher_id,NULL,
              'P1','OPEN','FAVORITE_OBSERVATION_DEAD','NOT_REQUESTED',
              transaction_timestamp(),evidence,clock_timestamp(),case_source_ref,
              p_source_region,p_source_appoint_id,1,1,evidence_hash,0,
              NULL,NULL,NULL
            );
          END IF;
          audit_payload := jsonb_build_object(
            'protocol_version','favorite-observation-dead-case-v2',
            'case_id',case_id,'source_ref',case_source_ref,
            'evidence_fingerprint',evidence_hash,
            'observation_revision',p_observation_revision,
            'dead_generation',p_dead_generation
          );
          audit_id := 'audit:fav-dead:' || encode(sha256(convert_to(
            case_source_ref || ':' || evidence_hash,'UTF8')),'hex');
          INSERT INTO public.audit_events(
            event_id,event_type,teacher_id,task_id,case_id,occurred_at,
            actor_type,payload_hash,payload
          ) VALUES (
            audit_id,'FAVORITE_OBSERVATION_DEAD_V2',stored_teacher_id,NULL,
            case_id,transaction_timestamp(),'FAVORITE_OBSERVATION_WORKER',
            public.dts_canonical_json_sha256_v1(audit_payload),audit_payload
          ) ON CONFLICT (event_id) DO NOTHING;
          RETURN case_id;
        END
        $function$;

        CREATE FUNCTION public.resolve_favorite_observation_case_v2(
          p_source_region text,p_source_appoint_id text,
          p_observation_revision bigint
        )
        RETURNS text
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE observation_row public.course_favorite_observations%ROWTYPE;
        DECLARE case_row public.ops_cases%ROWTYPE;
        DECLARE existing_recovery public.ops_case_recovery_events%ROWTYPE;
        DECLARE case_source_ref text;
        DECLARE recovery_event_id text;
        DECLARE work_event_id text;
        DECLARE evidence jsonb;
        DECLARE evidence_hash text;
        DECLARE status_after text;
        DECLARE next_case_revision integer;
        DECLARE audit_payload jsonb;
        DECLARE audit_id text;
        BEGIN
          PERFORM public.favorite_runtime_projection_generation_v1();
          SELECT * INTO observation_row
          FROM public.course_favorite_observations
          WHERE source_region=p_source_region
            AND source_appoint_id=p_source_appoint_id
            AND observation_revision=p_observation_revision FOR UPDATE;
          IF NOT FOUND OR observation_row.status NOT IN (
               'CONFIRMED_TRUE','CONFIRMED_FALSE',
               'WAITING_HISTORY','WAITING_EVIDENCE'
             ) THEN
            RAISE EXCEPTION 'DTS_V2_FAVORITE_CASE_RECOVERY_WORK_INCOMPLETE'
              USING ERRCODE='23514';
          END IF;
          IF observation_row.technical_case_id IS NULL THEN
            RETURN 'NO_CASE';
          END IF;
          case_source_ref := 'tech-case:favorite-observation:' ||
            p_source_region || ':' || p_source_appoint_id || ':' || 'r' ||
            p_observation_revision::text || ':' || 'g' ||
            observation_row.dead_generation::text;
          SELECT * INTO case_row FROM public.ops_cases
          WHERE case_id=observation_row.technical_case_id
            AND public.ops_cases.source_ref=case_source_ref
            AND case_type='FAVORITE_OBSERVATION_DEAD' FOR UPDATE;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'DTS_V2_FAVORITE_CASE_RECOVERY_CASE_MISSING'
              USING ERRCODE='23503';
          END IF;
          work_event_id := 'favorite-observation:' || p_source_region || ':' ||
            p_source_appoint_id || ':' || 'r' ||
            p_observation_revision::text;
          recovery_event_id := 'recovery:fav:' || encode(sha256(convert_to(
            case_source_ref || ':' || observation_row.row_version::text,
            'UTF8')),'hex');
          evidence := jsonb_build_object(
            'protocol_version','favorite-observation-recovery-v2',
            'source_ref',case_source_ref,'work_event_id',work_event_id,
            'status',observation_row.status,
            'observation_revision',p_observation_revision,
            'dead_generation',observation_row.dead_generation,
            'required_evidence_revision',
              observation_row.required_evidence_revision,
            'required_evidence_fingerprint',
              observation_row.required_evidence_fingerprint,
            'observation_row_version',observation_row.row_version
          );
          evidence_hash := public.dts_canonical_json_sha256_v1(evidence);
          SELECT * INTO existing_recovery
          FROM public.ops_case_recovery_events
          WHERE case_id=case_row.case_id AND event_id=work_event_id
            AND recovery_count=observation_row.dead_generation FOR SHARE;
          IF FOUND THEN
            IF existing_recovery.evidence_sha256 IS DISTINCT FROM
                 evidence_hash THEN
              RAISE EXCEPTION 'DTS_V2_FAVORITE_CASE_RECOVERY_CONFLICT'
                USING ERRCODE='23505';
            END IF;
            RETURN 'REPLAYED';
          END IF;
          status_after := CASE case_row.status
            WHEN 'OPEN' THEN 'RESOLVED' ELSE case_row.status END;
          next_case_revision := case_row.case_revision+1;
          INSERT INTO public.ops_case_recovery_events(
            recovery_event_id,case_id,source_ref,event_id,recovery_count,
            work_status,outbox_payload_sha256,outbox_row_version,published_at,
            case_status_before,case_status_after,case_revision_after,
            evidence,evidence_sha256,recorded_at
          ) VALUES (
            recovery_event_id,case_row.case_id,case_source_ref,work_event_id,
            observation_row.dead_generation,'PUBLISHED',
            observation_row.required_evidence_fingerprint,
            observation_row.row_version,transaction_timestamp(),
            case_row.status,status_after,next_case_revision,evidence,
            evidence_hash,transaction_timestamp()
          );
          UPDATE public.ops_cases
          SET status=status_after,source_reason='FAVORITE_OBSERVATION_RECOVERED',
              payload=payload || jsonb_build_object(
                'last_recovery_evidence_sha256',evidence_hash,
                'last_recovery_status',observation_row.status
              ),
              case_revision=next_case_revision,row_version=row_version+1,
              recovery_evidence_count=recovery_evidence_count+1,
              last_recovery_event_id=work_event_id,
              last_recovery_count=observation_row.dead_generation,
              last_recovered_at=transaction_timestamp(),
              updated_at=clock_timestamp()
          WHERE case_id=case_row.case_id AND row_version=case_row.row_version;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'DTS_V2_FAVORITE_CASE_RECOVERY_STALE'
              USING ERRCODE='40001';
          END IF;
          audit_payload := jsonb_build_object(
            'protocol_version','favorite-observation-case-recovered-v2',
            'case_id',case_row.case_id,'source_ref',case_source_ref,
            'recovery_event_id',recovery_event_id,
            'evidence_sha256',evidence_hash,'status',status_after
          );
          audit_id := 'audit:fav-recovery:' || encode(sha256(convert_to(
            recovery_event_id,'UTF8')),'hex');
          INSERT INTO public.audit_events(
            event_id,event_type,teacher_id,task_id,case_id,occurred_at,
            actor_type,payload_hash,payload
          ) VALUES (
            audit_id,'FAVORITE_OBSERVATION_RECOVERED_V2',case_row.teacher_id,
            NULL,case_row.case_id,transaction_timestamp(),
            'FAVORITE_OBSERVATION_WORKER',
            public.dts_canonical_json_sha256_v1(audit_payload),audit_payload
          );
          RETURN status_after;
        END
        $function$;

        REVOKE ALL ON FUNCTION
          public.record_favorite_observation_dead_case_v2(
            text,text,bigint,bigint,text
          ),
          public.resolve_favorite_observation_case_v2(text,text,bigint)
        FROM PUBLIC;
        """
    )


def _install_worker_commands() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.claim_favorite_observations_v2(
          p_worker_id text,p_limit integer
        )
        RETURNS SETOF jsonb
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE claim_as_of timestamptz := clock_timestamp();
        DECLARE candidate public.course_favorite_observations%ROWTYPE;
        DECLARE claimed public.course_favorite_observations%ROWTYPE;
        DECLARE new_token text;
        BEGIN
          IF p_worker_id !~ '^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,159}$'
             OR p_limit<1 OR p_limit>1000 THEN
            RAISE EXCEPTION 'DTS_V2_FAVORITE_CLAIM_REQUEST_INVALID'
              USING ERRCODE='22023';
          END IF;
          IF public.favorite_runtime_mode_v1() IS DISTINCT FROM 'V2_PRIMARY' THEN
            RETURN;
          END IF;
          PERFORM public.favorite_runtime_projection_generation_v1();
          FOR candidate IN
            SELECT observation.*
            FROM public.course_favorite_observations observation
            WHERE observation.status IN ('PENDING','RETRY')
              AND observation.is_serving IS TRUE
              AND observation.observed_at<=claim_as_of
              AND observation.next_attempt_at<=claim_as_of
              AND observation.attempt_count<8
            ORDER BY observation.next_attempt_at,observation.observed_at,
              observation.source_region,observation.source_appoint_id,
              observation.observation_revision
            LIMIT p_limit FOR UPDATE SKIP LOCKED
          LOOP
            new_token := 'favlease-' ||
              replace(gen_random_uuid()::text,'-','') ||
              replace(gen_random_uuid()::text,'-','');
            UPDATE public.course_favorite_observations
            SET status='EVALUATING',attempt_count=attempt_count+1,
                next_attempt_at=NULL,
                claimed_evidence_revision=required_evidence_revision,
                lease_owner=p_worker_id,lease_token=new_token,
                lease_acquired_at=claim_as_of,
                lease_expires_at=claim_as_of+interval '60 seconds',
                last_error=NULL,row_version=row_version+1,
                updated_at=clock_timestamp()
            WHERE source_region=candidate.source_region
              AND source_appoint_id=candidate.source_appoint_id
              AND observation_revision=candidate.observation_revision
              AND row_version=candidate.row_version
              AND status IN ('PENDING','RETRY')
            RETURNING * INTO claimed;
            IF NOT FOUND THEN
              CONTINUE;
            END IF;
            RETURN NEXT jsonb_build_object(
              'source_region',claimed.source_region,
              'source_appoint_id',claimed.source_appoint_id,
              'observation_revision',claimed.observation_revision,
              'teacher_id',claimed.teacher_id,
              'teacher_id_type',claimed.teacher_id_type,
              'student_token',claimed.student_token,
              'completion_participation_seq',
                claimed.completion_participation_seq,
              'observed_at',claimed.observed_at,
              'required_evidence_revision',
                claimed.required_evidence_revision,
              'claimed_evidence_revision',
                claimed.claimed_evidence_revision,
              'required_evidence_fingerprint',
                claimed.required_evidence_fingerprint,
              'lease_owner',claimed.lease_owner,
              'lease_token',claimed.lease_token,
              'row_version',claimed.row_version
            );
          END LOOP;
          RETURN;
        END
        $function$;

        CREATE FUNCTION public.heartbeat_favorite_observation_v2(
          p_source_region text,p_source_appoint_id text,
          p_observation_revision bigint,p_lease_owner text,p_lease_token text,
          p_expected_row_version bigint,p_claimed_evidence_revision bigint
        )
        RETURNS jsonb
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE heartbeat_as_of timestamptz := clock_timestamp();
        DECLARE updated_row public.course_favorite_observations%ROWTYPE;
        BEGIN
          PERFORM public.favorite_runtime_projection_generation_v1();
          UPDATE public.course_favorite_observations
          SET lease_expires_at=heartbeat_as_of+interval '60 seconds',
              row_version=row_version+1,updated_at=clock_timestamp()
          WHERE source_region=p_source_region
            AND source_appoint_id=p_source_appoint_id
            AND observation_revision=p_observation_revision
            AND status='EVALUATING' AND lease_owner=p_lease_owner
            AND lease_token=p_lease_token
            AND lease_expires_at>heartbeat_as_of
            AND row_version=p_expected_row_version
            AND claimed_evidence_revision=p_claimed_evidence_revision
          RETURNING * INTO updated_row;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'DTS_V2_FAVORITE_OBSERVATION_LEASE_MISMATCH'
              USING ERRCODE='40001';
          END IF;
          RETURN jsonb_build_object(
            'status','EVALUATING','row_version',updated_row.row_version,
            'lease_expires_at',updated_row.lease_expires_at
          );
        END
        $function$;

        CREATE FUNCTION public.fail_favorite_observation_v2(
          p_source_region text,p_source_appoint_id text,
          p_observation_revision bigint,p_lease_owner text,p_lease_token text,
          p_expected_row_version bigint,p_claimed_evidence_revision bigint,
          p_error_code text
        )
        RETURNS jsonb
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE failure_as_of timestamptz := clock_timestamp();
        DECLARE observation_row public.course_favorite_observations%ROWTYPE;
        DECLARE result_status text;
        DECLARE case_source_ref text;
        DECLARE case_id text;
        BEGIN
          PERFORM public.favorite_runtime_projection_generation_v1();
          IF p_error_code !~ '^[A-Z][A-Z0-9_]*$'
             OR length(p_error_code)>128 THEN
            RAISE EXCEPTION 'DTS_V2_FAVORITE_FAILURE_CODE_INVALID'
              USING ERRCODE='22023';
          END IF;
          SELECT * INTO observation_row
          FROM public.course_favorite_observations
          WHERE source_region=p_source_region
            AND source_appoint_id=p_source_appoint_id
            AND observation_revision=p_observation_revision FOR UPDATE;
          IF NOT FOUND OR observation_row.status IS DISTINCT FROM 'EVALUATING'
             OR observation_row.lease_owner IS DISTINCT FROM p_lease_owner
             OR observation_row.lease_token IS DISTINCT FROM p_lease_token
             OR observation_row.lease_expires_at<=failure_as_of
             OR observation_row.claimed_evidence_revision IS DISTINCT FROM
                  p_claimed_evidence_revision
             OR observation_row.row_version IS DISTINCT FROM
                  p_expected_row_version THEN
            RAISE EXCEPTION 'DTS_V2_FAVORITE_OBSERVATION_LEASE_MISMATCH'
              USING ERRCODE='40001';
          END IF;
          IF observation_row.required_evidence_revision>
               p_claimed_evidence_revision THEN
            UPDATE public.course_favorite_observations
            SET status='PENDING',attempt_count=0,
                next_attempt_at=transaction_timestamp(),
                claimed_evidence_revision=NULL,lease_owner=NULL,
                lease_token=NULL,lease_acquired_at=NULL,
                lease_expires_at=NULL,last_error=NULL,
                row_version=row_version+1,updated_at=clock_timestamp()
            WHERE source_region=p_source_region
              AND source_appoint_id=p_source_appoint_id
              AND observation_revision=p_observation_revision
              AND row_version=observation_row.row_version;
            IF NOT FOUND THEN
              RAISE EXCEPTION 'DTS_V2_FAVORITE_OBSERVATION_STALE'
                USING ERRCODE='40001';
            END IF;
            result_status := 'STALE_REQUEUED';
          ELSIF observation_row.attempt_count<8 THEN
            UPDATE public.course_favorite_observations
            SET status='RETRY',next_attempt_at=failure_as_of+
                  interval '5 seconds' *
                    power(2,observation_row.attempt_count-1),
                claimed_evidence_revision=NULL,lease_owner=NULL,
                lease_token=NULL,lease_acquired_at=NULL,
                lease_expires_at=NULL,last_error=p_error_code,
                row_version=row_version+1,updated_at=clock_timestamp()
            WHERE source_region=p_source_region
              AND source_appoint_id=p_source_appoint_id
              AND observation_revision=p_observation_revision
              AND row_version=observation_row.row_version;
            IF NOT FOUND THEN
              RAISE EXCEPTION 'DTS_V2_FAVORITE_OBSERVATION_STALE'
                USING ERRCODE='40001';
            END IF;
            result_status := 'RETRY';
          ELSE
            case_source_ref := 'tech-case:favorite-observation:' ||
              p_source_region || ':' || p_source_appoint_id || ':' || 'r' ||
              p_observation_revision::text || ':' || 'g' ||
              (observation_row.dead_generation+1)::text;
            case_id := 'v2case:' || encode(sha256(convert_to(
              case_source_ref,'UTF8')),'hex');
            UPDATE public.course_favorite_observations
            SET status='DEAD',next_attempt_at=NULL,
                claimed_evidence_revision=NULL,lease_owner=NULL,
                lease_token=NULL,lease_acquired_at=NULL,
                lease_expires_at=NULL,last_error=p_error_code,
                dead_generation=dead_generation+1,
                technical_case_id=case_id,row_version=row_version+1,
                updated_at=clock_timestamp()
            WHERE source_region=p_source_region
              AND source_appoint_id=p_source_appoint_id
              AND observation_revision=p_observation_revision
              AND row_version=observation_row.row_version;
            IF NOT FOUND THEN
              RAISE EXCEPTION 'DTS_V2_FAVORITE_OBSERVATION_STALE'
                USING ERRCODE='40001';
            END IF;
            PERFORM public.record_favorite_observation_dead_case_v2(
              p_source_region,p_source_appoint_id,p_observation_revision,
              observation_row.dead_generation+1,p_error_code
            );
            result_status := 'DEAD';
          END IF;
          RETURN jsonb_build_object(
            'status',result_status,'source_region',p_source_region,
            'source_appoint_id',p_source_appoint_id,
            'observation_revision',p_observation_revision
          );
        END
        $function$;

        CREATE FUNCTION public.reap_expired_favorite_observations_v2(
          p_limit integer
        )
        RETURNS jsonb
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE reap_as_of timestamptz := clock_timestamp();
        DECLARE observation_row public.course_favorite_observations%ROWTYPE;
        DECLARE case_source_ref text;
        DECLARE case_id text;
        DECLARE reaped_count integer := 0;
        DECLARE retry_count integer := 0;
        DECLARE dead_count integer := 0;
        DECLARE stale_count integer := 0;
        BEGIN
          IF p_limit<1 OR p_limit>1000 THEN
            RAISE EXCEPTION 'DTS_V2_FAVORITE_REAP_REQUEST_INVALID'
              USING ERRCODE='22023';
          END IF;
          IF public.favorite_runtime_mode_v1() IS DISTINCT FROM 'V2_PRIMARY' THEN
            RETURN jsonb_build_object(
              'reaped',0,'retry',0,'dead',0,'stale_requeued',0
            );
          END IF;
          PERFORM public.favorite_runtime_projection_generation_v1();
          FOR observation_row IN
            SELECT observation.*
            FROM public.course_favorite_observations observation
            WHERE observation.status='EVALUATING'
              AND observation.lease_expires_at<=reap_as_of
            ORDER BY observation.lease_expires_at,
              observation.source_region,observation.source_appoint_id,
              observation.observation_revision
            LIMIT p_limit FOR UPDATE SKIP LOCKED
          LOOP
            reaped_count := reaped_count+1;
            IF observation_row.required_evidence_revision>
                 observation_row.claimed_evidence_revision THEN
              UPDATE public.course_favorite_observations
              SET status='PENDING',attempt_count=0,
                  next_attempt_at=transaction_timestamp(),
                  claimed_evidence_revision=NULL,lease_owner=NULL,
                  lease_token=NULL,lease_acquired_at=NULL,
                  lease_expires_at=NULL,last_error=NULL,
                  row_version=row_version+1,updated_at=clock_timestamp()
              WHERE source_region=observation_row.source_region
                AND source_appoint_id=observation_row.source_appoint_id
                AND observation_revision=observation_row.observation_revision
                AND row_version=observation_row.row_version;
              IF NOT FOUND THEN
                RAISE EXCEPTION 'DTS_V2_FAVORITE_OBSERVATION_STALE'
                  USING ERRCODE='40001';
              END IF;
              stale_count := stale_count+1;
            ELSIF observation_row.attempt_count<8 THEN
              UPDATE public.course_favorite_observations
              SET status='RETRY',next_attempt_at=reap_as_of+
                    interval '5 seconds' *
                      power(2,observation_row.attempt_count-1),
                  claimed_evidence_revision=NULL,lease_owner=NULL,
                  lease_token=NULL,lease_acquired_at=NULL,
                  lease_expires_at=NULL,
                  last_error='FAVORITE_OBSERVATION_LEASE_EXPIRED',
                  row_version=row_version+1,updated_at=clock_timestamp()
              WHERE source_region=observation_row.source_region
                AND source_appoint_id=observation_row.source_appoint_id
                AND observation_revision=observation_row.observation_revision
                AND row_version=observation_row.row_version;
              IF NOT FOUND THEN
                RAISE EXCEPTION 'DTS_V2_FAVORITE_OBSERVATION_STALE'
                  USING ERRCODE='40001';
              END IF;
              retry_count := retry_count+1;
            ELSE
              case_source_ref := 'tech-case:favorite-observation:' ||
                observation_row.source_region || ':' ||
                observation_row.source_appoint_id || ':' || 'r' ||
                observation_row.observation_revision::text || ':' || 'g' ||
                (observation_row.dead_generation+1)::text;
              case_id := 'v2case:' || encode(sha256(convert_to(
                case_source_ref,'UTF8')),'hex');
              UPDATE public.course_favorite_observations
              SET status='DEAD',next_attempt_at=NULL,
                  claimed_evidence_revision=NULL,lease_owner=NULL,
                  lease_token=NULL,lease_acquired_at=NULL,
                  lease_expires_at=NULL,
                  last_error='FAVORITE_OBSERVATION_LEASE_EXPIRED',
                  dead_generation=dead_generation+1,
                  technical_case_id=case_id,row_version=row_version+1,
                  updated_at=clock_timestamp()
              WHERE source_region=observation_row.source_region
                AND source_appoint_id=observation_row.source_appoint_id
                AND observation_revision=observation_row.observation_revision
                AND row_version=observation_row.row_version;
              IF NOT FOUND THEN
                RAISE EXCEPTION 'DTS_V2_FAVORITE_OBSERVATION_STALE'
                  USING ERRCODE='40001';
              END IF;
              PERFORM public.record_favorite_observation_dead_case_v2(
                observation_row.source_region,
                observation_row.source_appoint_id,
                observation_row.observation_revision,
                observation_row.dead_generation+1,
                'FAVORITE_OBSERVATION_LEASE_EXPIRED'
              );
              dead_count := dead_count+1;
            END IF;
          END LOOP;
          RETURN jsonb_build_object(
            'reaped',reaped_count,'retry',retry_count,'dead',dead_count,
            'stale_requeued',stale_count
          );
        END
        $function$;

        REVOKE ALL ON FUNCTION
          public.claim_favorite_observations_v2(text,integer),
          public.heartbeat_favorite_observation_v2(
            text,text,bigint,text,text,bigint,bigint
          ),
          public.fail_favorite_observation_v2(
            text,text,bigint,text,text,bigint,bigint,text
          ),
          public.reap_expired_favorite_observations_v2(integer)
        FROM PUBLIC;
        """
    )


def _install_complete_command() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.complete_favorite_observation_v2(
          p_source_region text,p_source_appoint_id text,
          p_observation_revision bigint,p_lease_owner text,p_lease_token text,
          p_expected_row_version bigint,p_claimed_evidence_revision bigint,
          p_evidence_fingerprint text,p_result_status text,
          p_relation_state boolean,p_relation_evidence_status text,
          p_relation_error_code text,p_rule_version text
        )
        RETURNS jsonb
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE complete_as_of timestamptz := clock_timestamp();
        DECLARE observation_row public.course_favorite_observations%ROWTYPE;
        DECLARE completed_row public.course_favorite_observations%ROWTYPE;
        DECLARE current_fingerprint text;
        DECLARE global_generation bigint;
        DECLARE outcome jsonb;
        DECLARE outcome_action text;
        DECLARE score_entry_count integer;
        DECLARE case_resolution text;
        BEGIN
          global_generation :=
            public.favorite_runtime_projection_generation_v1();
          IF p_rule_version IS DISTINCT FROM 'favorite-score-v1'
             OR p_evidence_fingerprint !~ '^[0-9a-f]{64}$'
             OR NOT (
               (p_result_status='CONFIRMED_TRUE'
                AND p_relation_state IS TRUE
                AND p_relation_evidence_status='CONFIRMED'
                AND p_relation_error_code IS NULL)
               OR (p_result_status='CONFIRMED_FALSE'
                AND p_relation_state IS FALSE
                AND p_relation_evidence_status='CONFIRMED'
                AND p_relation_error_code IS NULL)
               OR (p_result_status='WAITING_HISTORY'
                AND p_relation_state IS NULL
                AND p_relation_evidence_status='HISTORY_INCOMPLETE'
                AND p_relation_error_code=
                  'PENDING_DATA:FAVORITE_HISTORY_INCOMPLETE')
               OR (p_result_status='WAITING_EVIDENCE'
                AND p_relation_state IS NULL
                AND p_relation_evidence_status='SOURCE_MISSING'
                AND p_relation_error_code IN (
                  'SOURCE_MISSING:FAVORITE_EFFECTIVE_TIME_MISSING',
                  'SOURCE_CONFLICT:COURSE_COMPLETION_PENDING'
                ))
             ) THEN
            RAISE EXCEPTION 'DTS_V2_FAVORITE_COMPLETION_RESULT_INVALID'
              USING ERRCODE='22023';
          END IF;
          SELECT * INTO observation_row
          FROM public.course_favorite_observations
          WHERE source_region=p_source_region
            AND source_appoint_id=p_source_appoint_id
            AND observation_revision=p_observation_revision FOR UPDATE;
          IF NOT FOUND OR observation_row.status IS DISTINCT FROM 'EVALUATING'
             OR observation_row.lease_owner IS DISTINCT FROM p_lease_owner
             OR observation_row.lease_token IS DISTINCT FROM p_lease_token
             OR observation_row.lease_expires_at<=complete_as_of
             OR observation_row.claimed_evidence_revision IS DISTINCT FROM
                  p_claimed_evidence_revision
             OR observation_row.row_version<p_expected_row_version
             OR (observation_row.row_version<>p_expected_row_version
                 AND observation_row.required_evidence_revision<=
                      p_claimed_evidence_revision) THEN
            RAISE EXCEPTION 'DTS_V2_FAVORITE_OBSERVATION_LEASE_MISMATCH'
              USING ERRCODE='40001';
          END IF;
          PERFORM pg_advisory_xact_lock(hashtextextended(
            'tit:favorite-pair:' || observation_row.source_region || ':' ||
            observation_row.teacher_id || ':' ||
            observation_row.student_token,0
          ));
          current_fingerprint :=
            public.favorite_observation_evidence_fingerprint_v1(
              p_source_region,p_source_appoint_id,p_rule_version
            );
          IF observation_row.required_evidence_revision>
               p_claimed_evidence_revision
             OR observation_row.required_evidence_fingerprint IS DISTINCT FROM
                  p_evidence_fingerprint
             OR current_fingerprint IS DISTINCT FROM p_evidence_fingerprint
             OR observation_row.serving_projection_generation IS DISTINCT FROM
                  global_generation THEN
            UPDATE public.course_favorite_observations
            SET status='PENDING',relation_state=NULL,
                relation_evidence_status='PENDING',relation_error_code=NULL,
                required_evidence_revision=CASE
                  WHEN current_fingerprint IS DISTINCT FROM
                         required_evidence_fingerprint
                    OR serving_projection_generation IS DISTINCT FROM
                         global_generation
                  THEN required_evidence_revision+1
                  ELSE required_evidence_revision END,
                required_evidence_fingerprint=current_fingerprint,
                claimed_evidence_revision=NULL,attempt_count=0,
                next_attempt_at=transaction_timestamp(),lease_owner=NULL,
                lease_token=NULL,lease_acquired_at=NULL,
                lease_expires_at=NULL,last_error=NULL,
                serving_projection_generation=global_generation,
                row_version=row_version+1,updated_at=clock_timestamp()
            WHERE source_region=p_source_region
              AND source_appoint_id=p_source_appoint_id
              AND observation_revision=p_observation_revision
              AND row_version=observation_row.row_version;
            IF NOT FOUND THEN
              RAISE EXCEPTION 'DTS_V2_FAVORITE_OBSERVATION_STALE'
                USING ERRCODE='40001';
            END IF;
            outcome := public.favorite_attribution_outcome_v1(
              'NONE',observation_row.source_region,
              observation_row.teacher_id,observation_row.student_token,
              NULL,NULL,NULL,ARRAY[]::text[]
            );
            RETURN jsonb_build_object(
              'status','STALE_REQUEUED','score_entries_created',0,
              'attributions_awarded',0,'attributions_reversed',0,
              'attributions_reselected',0,
              'attribution_outcome',outcome
            );
          END IF;
          UPDATE public.course_favorite_observations
          SET status=p_result_status,relation_state=p_relation_state,
              relation_evidence_status=p_relation_evidence_status,
              relation_error_code=p_relation_error_code,
              completed_evidence_revision=p_claimed_evidence_revision,
              claimed_evidence_revision=NULL,next_attempt_at=NULL,
              lease_owner=NULL,lease_token=NULL,lease_acquired_at=NULL,
              lease_expires_at=NULL,last_error=NULL,
              row_version=row_version+1,updated_at=clock_timestamp()
          WHERE source_region=p_source_region
            AND source_appoint_id=p_source_appoint_id
            AND observation_revision=p_observation_revision
            AND row_version=observation_row.row_version
          RETURNING * INTO completed_row;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'DTS_V2_FAVORITE_OBSERVATION_STALE'
              USING ERRCODE='40001';
          END IF;
          outcome := public.reconcile_favorite_attribution_v2(
            completed_row.source_region,completed_row.teacher_id,
            completed_row.teacher_id_type,completed_row.student_token,
            global_generation,p_rule_version,
            'OBSERVATION_' || p_result_status
          );
          outcome_action := outcome->>'action';
          score_entry_count := jsonb_array_length(
            outcome->'score_entry_ids'
          );
          case_resolution := public.resolve_favorite_observation_case_v2(
            p_source_region,p_source_appoint_id,p_observation_revision
          );
          RETURN jsonb_build_object(
            'status',p_result_status,
            'source_region',p_source_region,
            'source_appoint_id',p_source_appoint_id,
            'observation_revision',p_observation_revision,
            'completed_evidence_revision',p_claimed_evidence_revision,
            'projection_generation',global_generation,
            'score_entries_created',score_entry_count,
            'attributions_awarded',
              CASE WHEN outcome_action='AWARD' THEN 1 ELSE 0 END,
            'attributions_reversed',
              CASE WHEN outcome_action IN ('REVERSE','RESELECT')
                   THEN 1 ELSE 0 END,
            'attributions_reselected',
              CASE WHEN outcome_action='RESELECT' THEN 1 ELSE 0 END,
            'attribution_outcome',outcome,
            'technical_case_resolution',case_resolution
          );
        END
        $function$;

        REVOKE ALL ON FUNCTION public.complete_favorite_observation_v2(
          text,text,bigint,text,text,bigint,bigint,text,text,boolean,
          text,text,text
        ) FROM PUBLIC;
        """
    )


def _install_recovery_command() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.recover_favorite_observation_v2(
          p_command_id text,p_source_region text,p_source_appoint_id text,
          p_expected_observation_revision bigint,
          p_expected_dead_generation bigint,p_expected_row_version bigint,
          p_reason text
        )
        RETURNS jsonb
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE command_key text;
        DECLARE request_document jsonb;
        DECLARE request_hash text;
        DECLARE resource_identity text;
        DECLARE existing_command public.idempotency_records%ROWTYPE;
        DECLARE observation_row public.course_favorite_observations%ROWTYPE;
        DECLARE recovered_row public.course_favorite_observations%ROWTYPE;
        DECLARE current_fingerprint text;
        DECLARE global_generation bigint;
        DECLARE response_payload jsonb;
        DECLARE audit_payload jsonb;
        DECLARE audit_id text;
        BEGIN
          global_generation :=
            public.favorite_runtime_projection_generation_v1();
          IF p_command_id !~ '^[A-Za-z0-9._:-]{1,128}$'
             OR p_source_region NOT IN ('dom','ovs')
             OR p_source_appoint_id IS NULL OR btrim(p_source_appoint_id)=''
             OR p_expected_observation_revision<1
             OR p_expected_dead_generation<1
             OR p_expected_row_version<1
             OR p_reason IS NULL OR btrim(p_reason)=''
             OR length(p_reason)>1024 THEN
            RAISE EXCEPTION 'FAVORITE_OBSERVATION_RECOVERY_REQUEST_INVALID'
              USING ERRCODE='22023';
          END IF;
          command_key :=
            'recover-favorite-observation:v2:' || p_command_id;
          request_document := jsonb_build_object(
            'protocol_version','favorite-observation-recovery-command-v2',
            'command_id',p_command_id,'source_region',p_source_region,
            'source_appoint_id',p_source_appoint_id,
            'expected_observation_revision',
              p_expected_observation_revision,
            'expected_dead_generation',p_expected_dead_generation,
            'expected_row_version',p_expected_row_version,
            'reason',p_reason
          );
          request_hash :=
            public.dts_canonical_json_sha256_v1(request_document);
          resource_identity := 'favorite-observation:' ||
            public.dts_canonical_json_sha256_v1(jsonb_build_object(
              'source_region',p_source_region,
              'source_appoint_id',p_source_appoint_id,
              'observation_revision',p_expected_observation_revision
            ));
          PERFORM pg_advisory_xact_lock(hashtextextended(command_key,0));
          SELECT * INTO existing_command
          FROM public.idempotency_records
          WHERE scope='FAVORITE_OBSERVATION_RECOVERY_V2'
            AND idempotency_key=command_key FOR UPDATE;
          IF FOUND THEN
            IF existing_command.request_hash IS DISTINCT FROM request_hash THEN
              RAISE EXCEPTION 'FAVORITE_OBSERVATION_RECOVERY_COMMAND_CONFLICT'
                USING ERRCODE='23505';
            END IF;
            IF existing_command.expires_at IS NOT NULL
               OR existing_command.resource_id IS DISTINCT FROM
                    resource_identity
               OR jsonb_typeof(existing_command.response_payload)<>'object'
               OR existing_command.response_payload->>'source_region'
                    IS DISTINCT FROM p_source_region
               OR existing_command.response_payload->>'source_appoint_id'
                    IS DISTINCT FROM p_source_appoint_id
               OR existing_command.response_payload->>
                    'observation_revision' IS DISTINCT FROM
                    p_expected_observation_revision::text
               OR existing_command.response_payload->>'dead_generation'
                    IS DISTINCT FROM p_expected_dead_generation::text THEN
              RAISE EXCEPTION 'FAVORITE_OBSERVATION_RECOVERY_REPLAY_INVALID'
                USING ERRCODE='23514';
            END IF;
            RETURN existing_command.response_payload ||
              jsonb_build_object('replay_status','REPLAYED');
          END IF;
          SELECT * INTO observation_row
          FROM public.course_favorite_observations
          WHERE source_region=p_source_region
            AND source_appoint_id=p_source_appoint_id
            AND observation_revision=p_expected_observation_revision
          FOR UPDATE;
          IF NOT FOUND OR observation_row.status IS DISTINCT FROM 'DEAD'
             OR observation_row.dead_generation IS DISTINCT FROM
                  p_expected_dead_generation
             OR observation_row.row_version IS DISTINCT FROM
                  p_expected_row_version THEN
            RAISE EXCEPTION 'FAVORITE_OBSERVATION_RECOVERY_STALE'
              USING ERRCODE='40001';
          END IF;
          current_fingerprint :=
            public.favorite_observation_evidence_fingerprint_v1(
              p_source_region,p_source_appoint_id,
              observation_row.rule_version
            );
          UPDATE public.course_favorite_observations
          SET status='PENDING',relation_state=NULL,
              relation_evidence_status='PENDING',relation_error_code=NULL,
              required_evidence_revision=CASE
                WHEN required_evidence_fingerprint IS DISTINCT FROM
                       current_fingerprint
                  OR serving_projection_generation IS DISTINCT FROM
                       global_generation
                THEN required_evidence_revision+1
                ELSE required_evidence_revision END,
              required_evidence_fingerprint=current_fingerprint,
              claimed_evidence_revision=NULL,attempt_count=0,
              next_attempt_at=transaction_timestamp(),lease_owner=NULL,
              lease_token=NULL,lease_acquired_at=NULL,lease_expires_at=NULL,
              last_error=NULL,serving_projection_generation=global_generation,
              row_version=row_version+1,updated_at=clock_timestamp()
          WHERE source_region=p_source_region
            AND source_appoint_id=p_source_appoint_id
            AND observation_revision=p_expected_observation_revision
            AND status='DEAD' AND dead_generation=p_expected_dead_generation
            AND row_version=p_expected_row_version
          RETURNING * INTO recovered_row;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'FAVORITE_OBSERVATION_RECOVERY_STALE'
              USING ERRCODE='40001';
          END IF;
          audit_payload := jsonb_build_object(
            'protocol_version','favorite-observation-recovery-command-v2',
            'command_id',p_command_id,'source_region',p_source_region,
            'source_appoint_id',p_source_appoint_id,
            'observation_revision',recovered_row.observation_revision,
            'dead_generation',recovered_row.dead_generation,
            'previous_row_version',observation_row.row_version,
            'row_version',recovered_row.row_version,
            'reason_hash',encode(sha256(convert_to(p_reason,'UTF8')),'hex')
          );
          audit_id := 'audit:fav-recover-command:' || encode(sha256(convert_to(
            p_command_id,'UTF8')),'hex');
          INSERT INTO public.audit_events(
            event_id,event_type,teacher_id,task_id,case_id,occurred_at,
            actor_type,payload_hash,payload
          ) VALUES (
            audit_id,'FAVORITE_OBSERVATION_RECOVERY_REQUESTED_V2',
            CASE WHEN EXISTS(SELECT 1 FROM public.teachers teacher
                 WHERE teacher.teacher_id=recovered_row.teacher_id)
                 THEN recovered_row.teacher_id ELSE NULL END,
            NULL,recovered_row.technical_case_id,transaction_timestamp(),
            'OPS_RECOVERY',
            public.dts_canonical_json_sha256_v1(audit_payload),audit_payload
          );
          response_payload := jsonb_build_object(
            'status','PENDING','replay_status','APPLIED',
            'command_id',p_command_id,'source_region',p_source_region,
            'source_appoint_id',p_source_appoint_id,
            'observation_revision',recovered_row.observation_revision,
            'dead_generation',recovered_row.dead_generation,
            'row_version',recovered_row.row_version,
            'projection_generation',global_generation,
            'audit_event_id',audit_id
          );
          INSERT INTO public.idempotency_records(
            scope,idempotency_key,request_hash,resource_id,response_payload,
            created_at,expires_at
          ) VALUES (
            'FAVORITE_OBSERVATION_RECOVERY_V2',command_key,request_hash,
            resource_identity,response_payload,transaction_timestamp(),NULL
          );
          RETURN response_payload;
        END
        $function$;

        REVOKE ALL ON FUNCTION public.recover_favorite_observation_v2(
          text,text,text,bigint,bigint,bigint,text
        ) FROM PUBLIC;
        """
    )


def _apply_acl_and_comments() -> None:
    op.execute(
        rf"""
        ALTER FUNCTION public.dts_v2_relationship_event_provenance_guard()
          SECURITY DEFINER;
        ALTER FUNCTION public.dts_v2_relationship_current_pointer_guard()
          SECURITY DEFINER;
        ALTER FUNCTION public.dts_v2_favorite_course_guard()
          SECURITY DEFINER;
        REVOKE CREATE ON SCHEMA public FROM {OUTBOX_RUNTIME_ROLE};
        GRANT USAGE ON SCHEMA public TO {OUTBOX_RUNTIME_ROLE};
        REVOKE ALL PRIVILEGES ON TABLE
          public.teacher_student_relationship_events,
          public.teacher_student_relationship_current,
          public.course_favorite_observations,
          public.course_favorite_attributions,
          public.source_courses,
          public.source_course_participations,
          public.dts_source_scope_states,
          public.dts_source_scope_snapshots,
          public.dts_source_rows,
          public.score_entries,
          public.teachers,
          public.ops_cases,
          public.ops_case_recovery_events,
          public.audit_events,
          public.idempotency_records
        FROM {OUTBOX_RUNTIME_ROLE};
        GRANT SELECT ON TABLE
          public.teacher_student_relationship_events,
          public.teacher_student_relationship_current,
          public.source_courses,
          public.source_course_participations,
          public.dts_source_scope_states,
          public.dts_source_scope_snapshots
        TO {OUTBOX_RUNTIME_ROLE};
        GRANT EXECUTE ON FUNCTION
          public.favorite_observation_evidence_fingerprint_v1(text,text,text),
          public.materialize_favorite_observation_v2(
            text,text,text,text,text,text,integer,timestamptz,text,bigint,text
          ),
          public.claim_favorite_observations_v2(text,integer),
          public.heartbeat_favorite_observation_v2(
            text,text,bigint,text,text,bigint,bigint
          ),
          public.complete_favorite_observation_v2(
            text,text,bigint,text,text,bigint,bigint,text,text,boolean,
            text,text,text
          ),
          public.fail_favorite_observation_v2(
            text,text,bigint,text,text,bigint,bigint,text
          ),
          public.reap_expired_favorite_observations_v2(integer)
        TO {OUTBOX_RUNTIME_ROLE};

        REVOKE CREATE ON SCHEMA public FROM {RECOVERY_RUNTIME_ROLE};
        GRANT USAGE ON SCHEMA public TO {RECOVERY_RUNTIME_ROLE};
        REVOKE ALL PRIVILEGES ON TABLE
          public.course_favorite_observations,
          public.course_favorite_attributions,
          public.score_entries,public.idempotency_records,
          public.ops_cases,public.audit_events,public.dts_pipeline_control
        FROM {RECOVERY_RUNTIME_ROLE};
        GRANT EXECUTE ON FUNCTION
          public.recover_favorite_observation_v2(
            text,text,text,bigint,bigint,bigint,text
          )
        TO {RECOVERY_RUNTIME_ROLE};

        REVOKE ALL ON FUNCTION
          public.favorite_runtime_mode_v1(),
          public.favorite_runtime_projection_generation_v1(),
          public.favorite_observation_evidence_fingerprint_v1(text,text,text),
          public.dts_v2_favorite_score_entry_valid(
            text,text,text,text,integer,text,text,bigint,bigint,text,bigint,text
          ),
          public.write_favorite_score_entry_v2(
            text,text,text,integer,text,text,bigint,bigint,text,bigint,text
          ),
          public.favorite_attribution_outcome_v1(
            text,text,text,text,text,text,bigint,text[]
          ),
          public.hold_favorite_attribution_v2(
            text,text,text,text,text,bigint,text
          ),
          public.reconcile_favorite_attribution_v2(
            text,text,text,text,bigint,text,text
          ),
          public.assert_favorite_attribution_score_v2(text,text,text),
          public.favorite_attribution_score_guard_v2(),
          public.materialize_favorite_observation_v2(
            text,text,text,text,text,text,integer,timestamptz,text,bigint,text
          ),
          public.record_favorite_observation_dead_case_v2(
            text,text,bigint,bigint,text
          ),
          public.resolve_favorite_observation_case_v2(text,text,bigint),
          public.claim_favorite_observations_v2(text,integer),
          public.heartbeat_favorite_observation_v2(
            text,text,bigint,text,text,bigint,bigint
          ),
          public.fail_favorite_observation_v2(
            text,text,bigint,text,text,bigint,bigint,text
          ),
          public.reap_expired_favorite_observations_v2(integer),
          public.complete_favorite_observation_v2(
            text,text,bigint,text,text,bigint,bigint,text,text,boolean,
            text,text,text
          ),
          public.recover_favorite_observation_v2(
            text,text,text,bigint,bigint,bigint,text
          )
        FROM PUBLIC,tit_growth_app,tit_dts_ingest_runtime;

        DO $favorite_optional_runtime_acl$
        DECLARE role_name text;
        BEGIN
          FOREACH role_name IN ARRAY ARRAY[
            'tit_teacher_crud','tit_source_monitor','tit_source_worker',
            'tide_business_app','tit_dts_scope_coordinator_runtime'
          ]::text[] LOOP
            IF to_regrole(role_name) IS NOT NULL THEN
              EXECUTE format(
                'REVOKE ALL PRIVILEGES ON TABLE '
                'public.course_favorite_observations,'
                'public.course_favorite_attributions FROM %I',role_name
              );
              EXECUTE format(
                'REVOKE ALL ON FUNCTION '
                'public.materialize_favorite_observation_v2('
                'text,text,text,text,text,text,integer,timestamptz,text,bigint,text),'
                'public.claim_favorite_observations_v2(text,integer),'
                'public.complete_favorite_observation_v2('
                'text,text,bigint,text,text,bigint,bigint,text,text,boolean,text,text,text),'
                'public.fail_favorite_observation_v2('
                'text,text,bigint,text,text,bigint,bigint,text),'
                'public.reap_expired_favorite_observations_v2(integer),'
                'public.recover_favorite_observation_v2('
                'text,text,text,bigint,bigint,bigint,text) FROM %I',role_name
              );
            END IF;
          END LOOP;
          IF to_regrole('tit_dts_domain_projector_runtime') IS NOT NULL THEN
            EXECUTE 'REVOKE ALL PRIVILEGES ON TABLE '
              'public.teacher_student_relationship_events,'
              'public.teacher_student_relationship_current,'
              'public.course_favorite_observations,'
              'public.course_favorite_attributions '
              'FROM tit_dts_domain_projector_runtime';
            EXECUTE 'GRANT SELECT,INSERT ON TABLE '
              'public.teacher_student_relationship_events '
              'TO tit_dts_domain_projector_runtime';
            EXECUTE 'GRANT SELECT,INSERT,UPDATE ON TABLE '
              'public.teacher_student_relationship_current '
              'TO tit_dts_domain_projector_runtime';
            EXECUTE 'GRANT SELECT ON TABLE '
              'public.course_favorite_observations,'
              'public.course_favorite_attributions '
              'TO tit_dts_domain_projector_runtime';
            EXECUTE 'GRANT USAGE,SELECT ON SEQUENCE '
              'public.teacher_student_relationship_events_event_sequence_seq '
              'TO tit_dts_domain_projector_runtime';
          END IF;
        END
        $favorite_optional_runtime_acl$;

        COMMENT ON TABLE public.teacher_student_relationship_events IS
          'DTS v2 append-only typed relationship history; domain-projector command path owns writes.';
        COMMENT ON TABLE public.teacher_student_relationship_current IS
          'DTS v2 course-independent relationship current state; no course-end prerequisite.';
        COMMENT ON TABLE public.course_favorite_observations IS
          'DTS v2 durable end_time plus 24-hour favorite observation work; database time owns due and lease decisions.';
        COMMENT ON TABLE public.course_favorite_attributions IS
          'DTS v2 retained one-course favorite attribution per regional teacher/student pair; reversals are append-only score entries.';
        COMMENT ON FUNCTION public.materialize_favorite_observation_v2(
          text,text,text,text,text,text,integer,timestamptz,text,bigint,text
        ) IS
          'Create or requeue end_time plus 24-hour observation work under V2_PRIMARY using global projection_generation.';
        COMMENT ON FUNCTION public.complete_favorite_observation_v2(
          text,text,bigint,text,text,bigint,bigint,text,text,boolean,
          text,text,text
        ) IS
          'Atomically finish evidence, reconcile the unique favorite attribution, and append award or reversal score entries.';
        COMMENT ON ROLE {RECOVERY_RUNTIME_ROLE} IS
          'Dedicated NOINHERIT operator recovery command role for dead favorite observations; no direct table DML.';
        """
    )


def _assert_upgrade_shape() -> None:
    op.execute(
        rf"""
        DO $favorite_runtime_shape$
        BEGIN
          IF to_regprocedure(
               'public.materialize_favorite_observation_v2('
               'text,text,text,text,text,text,integer,timestamptz,text,bigint,text)'
             ) IS NULL
             OR to_regprocedure(
               'public.complete_favorite_observation_v2('
               'text,text,bigint,text,text,bigint,bigint,text,text,boolean,text,text,text)'
             ) IS NULL
             OR to_regprocedure(
               'public.recover_favorite_observation_v2('
               'text,text,text,bigint,bigint,bigint,text)'
             ) IS NULL
             OR NOT EXISTS (
               SELECT 1 FROM pg_trigger
               WHERE tgrelid='public.course_favorite_attributions'::regclass
                 AND tgname='ct_favorite_attribution_score_v2'
                 AND tgdeferrable AND tginitdeferred
             )
             OR has_table_privilege(
               '{OUTBOX_RUNTIME_ROLE}',
               'public.course_favorite_observations','INSERT'
             )
             OR has_table_privilege(
               '{OUTBOX_RUNTIME_ROLE}','public.score_entries','INSERT'
             )
             OR NOT has_function_privilege(
               '{OUTBOX_RUNTIME_ROLE}',
               'public.claim_favorite_observations_v2(text,integer)',
               'EXECUTE'
             )
             OR has_function_privilege(
               'tit_growth_app',
               'public.complete_favorite_observation_v2('
               'text,text,bigint,text,text,bigint,bigint,text,text,boolean,text,text,text)',
               'EXECUTE'
             ) THEN
            RAISE EXCEPTION 'DTS_V2_FAVORITE_RUNTIME_SHAPE_INVALID';
          END IF;
        END
        $favorite_runtime_shape$;
        """
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("DTS v2 favorite runtime requires PostgreSQL")
    _assert_preconditions()
    _expand_constraints_and_indexes()
    _install_evidence_helpers()
    _install_score_helpers()
    _install_attribution_helpers()
    _install_integrity_guards()
    _install_materialize_command()
    _install_case_helpers()
    _install_worker_commands()
    _install_complete_command()
    _install_recovery_command()
    _apply_acl_and_comments()
    _assert_upgrade_shape()


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("DTS v2 favorite runtime requires PostgreSQL")
    op.execute(
        rf"""
        LOCK TABLE public.course_favorite_observations,
          public.course_favorite_attributions,public.score_entries
        IN ACCESS EXCLUSIVE MODE;
        DO $favorite_runtime_downgrade_guard$
        BEGIN
          IF EXISTS (
               SELECT 1 FROM public.course_favorite_observations
               WHERE materialization_origin='V2_LIVE'
             ) OR EXISTS (
               SELECT 1 FROM public.course_favorite_attributions
               WHERE materialization_origin='V2_LIVE'
             ) OR EXISTS (
               SELECT 1 FROM public.score_entries
               WHERE entry_type IN ('FAVORITE_AWARD','FAVORITE_REVERSAL')
             ) OR EXISTS (
               SELECT 1 FROM public.idempotency_records
               WHERE scope='FAVORITE_OBSERVATION_RECOVERY_V2'
             ) THEN
            RAISE EXCEPTION 'DTS_V2_FAVORITE_RUNTIME_DOWNGRADE_DATA_PRESENT';
          END IF;
        END
        $favorite_runtime_downgrade_guard$;

        REVOKE ALL ON FUNCTION
          public.materialize_favorite_observation_v2(
            text,text,text,text,text,text,integer,timestamptz,text,bigint,text
          ),
          public.claim_favorite_observations_v2(text,integer),
          public.heartbeat_favorite_observation_v2(
            text,text,bigint,text,text,bigint,bigint
          ),
          public.complete_favorite_observation_v2(
            text,text,bigint,text,text,bigint,bigint,text,text,boolean,
            text,text,text
          ),
          public.fail_favorite_observation_v2(
            text,text,bigint,text,text,bigint,bigint,text
          ),
          public.reap_expired_favorite_observations_v2(integer)
        FROM {OUTBOX_RUNTIME_ROLE};
        REVOKE ALL PRIVILEGES ON TABLE
          public.teacher_student_relationship_events,
          public.teacher_student_relationship_current,
          public.source_courses,
          public.source_course_participations,
          public.dts_source_scope_states,
          public.dts_source_scope_snapshots
        FROM {OUTBOX_RUNTIME_ROLE};
        REVOKE ALL ON FUNCTION
          public.recover_favorite_observation_v2(
            text,text,text,bigint,bigint,bigint,text
          )
        FROM {RECOVERY_RUNTIME_ROLE};
        REVOKE USAGE ON SCHEMA public FROM {RECOVERY_RUNTIME_ROLE};
        DO $favorite_runtime_revoke_projector$
        BEGIN
          IF to_regrole('tit_dts_domain_projector_runtime') IS NOT NULL THEN
            EXECUTE 'REVOKE ALL PRIVILEGES ON TABLE '
              'public.teacher_student_relationship_events,'
              'public.teacher_student_relationship_current,'
              'public.course_favorite_observations,'
              'public.course_favorite_attributions '
              'FROM tit_dts_domain_projector_runtime';
            EXECUTE 'REVOKE ALL PRIVILEGES ON SEQUENCE '
              'public.teacher_student_relationship_events_event_sequence_seq '
              'FROM tit_dts_domain_projector_runtime';
          END IF;
        END
        $favorite_runtime_revoke_projector$;
        ALTER FUNCTION public.dts_v2_relationship_event_provenance_guard()
          SECURITY INVOKER;
        ALTER FUNCTION public.dts_v2_relationship_current_pointer_guard()
          SECURITY INVOKER;
        ALTER FUNCTION public.dts_v2_favorite_course_guard()
          SECURITY INVOKER;
        DROP TRIGGER ct_favorite_attribution_score_v2
          ON public.course_favorite_attributions;
        DROP FUNCTION public.favorite_attribution_score_guard_v2();
        DROP FUNCTION public.assert_favorite_attribution_score_v2(
          text,text,text
        );
        DROP FUNCTION public.recover_favorite_observation_v2(
          text,text,text,bigint,bigint,bigint,text
        );
        DROP FUNCTION public.complete_favorite_observation_v2(
          text,text,bigint,text,text,bigint,bigint,text,text,boolean,
          text,text,text
        );
        DROP FUNCTION public.reap_expired_favorite_observations_v2(integer);
        DROP FUNCTION public.fail_favorite_observation_v2(
          text,text,bigint,text,text,bigint,bigint,text
        );
        DROP FUNCTION public.heartbeat_favorite_observation_v2(
          text,text,bigint,text,text,bigint,bigint
        );
        DROP FUNCTION public.claim_favorite_observations_v2(text,integer);
        DROP FUNCTION public.resolve_favorite_observation_case_v2(
          text,text,bigint
        );
        DROP FUNCTION public.record_favorite_observation_dead_case_v2(
          text,text,bigint,bigint,text
        );
        DROP FUNCTION public.materialize_favorite_observation_v2(
          text,text,text,text,text,text,integer,timestamptz,text,bigint,text
        );
        DROP FUNCTION public.reconcile_favorite_attribution_v2(
          text,text,text,text,bigint,text,text
        );
        DROP FUNCTION public.hold_favorite_attribution_v2(
          text,text,text,text,text,bigint,text
        );
        DROP FUNCTION public.favorite_attribution_outcome_v1(
          text,text,text,text,text,text,bigint,text[]
        );
        DROP FUNCTION public.write_favorite_score_entry_v2(
          text,text,text,integer,text,text,bigint,bigint,text,bigint,text
        );
        DROP FUNCTION public.dts_v2_favorite_score_entry_valid(
          text,text,text,text,integer,text,text,bigint,bigint,text,bigint,text
        );
        DROP FUNCTION public.favorite_observation_evidence_fingerprint_v1(
          text,text,text
        );
        DROP FUNCTION public.favorite_runtime_projection_generation_v1();
        DROP FUNCTION public.favorite_runtime_mode_v1();
        """
    )
    op.drop_index(
        "ix_favorite_observation_pair_state_v2",
        table_name="course_favorite_observations",
        schema="public",
    )
    op.drop_index(
        "ix_source_courses_favorite_completion_pair_v2",
        table_name="source_courses",
        schema="public",
    )
    op.drop_index(
        "ix_relationship_events_favorite_new_pair_v2",
        table_name="teacher_student_relationship_events",
        schema="public",
    )
    op.drop_index(
        "ix_relationship_events_favorite_old_pair_v2",
        table_name="teacher_student_relationship_events",
        schema="public",
    )
    op.drop_index(
        "uq_score_entries_reversal_once_v2",
        table_name="score_entries",
        schema="public",
    )
    op.drop_constraint(
        "ck_favorite_observation_evidence_shape",
        "course_favorite_observations",
        type_="check",
        schema="public",
    )
    op.create_check_constraint(
        "ck_favorite_observation_evidence_shape",
        "course_favorite_observations",
        "(status = 'CONFIRMED_TRUE' AND relation_state IS TRUE "
        "AND relation_evidence_status = 'CONFIRMED' "
        "AND relation_error_code IS NULL "
        "AND completed_evidence_revision = required_evidence_revision) OR "
        "(status = 'CONFIRMED_FALSE' AND relation_state IS FALSE "
        "AND relation_evidence_status = 'CONFIRMED' "
        "AND relation_error_code IS NULL "
        "AND completed_evidence_revision = required_evidence_revision) OR "
        "(status = 'WAITING_HISTORY' AND relation_state IS NULL "
        "AND relation_evidence_status = 'HISTORY_INCOMPLETE' "
        "AND relation_error_code = "
        "'PENDING_DATA:FAVORITE_HISTORY_INCOMPLETE' "
        "AND completed_evidence_revision = required_evidence_revision) OR "
        "(status = 'WAITING_EVIDENCE' AND relation_state IS NULL "
        "AND relation_evidence_status = 'SOURCE_MISSING' "
        "AND relation_error_code = "
        "'SOURCE_MISSING:FAVORITE_EFFECTIVE_TIME_MISSING' "
        "AND completed_evidence_revision = required_evidence_revision) OR "
        "(status IN ('PENDING','EVALUATING','RETRY','DEAD') "
        "AND relation_state IS NULL "
        "AND relation_evidence_status = 'PENDING' "
        "AND relation_error_code IS NULL) OR "
        "status IN ('INVALIDATED','VOIDED')",
        schema="public",
    )
    op.alter_column(
        "score_entries",
        "idempotency_key",
        existing_type=sa.String(length=1024),
        type_=sa.String(length=256),
        existing_nullable=False,
        schema="public",
    )
    op.execute(
        rf"""
        COMMENT ON TABLE public.teacher_student_relationship_events IS
          'DTS v2 shadow: append-only typed relationship history; no runtime writer is active';
        COMMENT ON TABLE public.teacher_student_relationship_current IS
          'DTS v2 shadow: course-independent relationship current state';
        COMMENT ON TABLE public.course_favorite_observations IS
          'DTS v2 shadow: authoritative completion end plus 24-hour observation';
        COMMENT ON TABLE public.course_favorite_attributions IS
          'DTS v2 shadow: one lifetime current favorite award per regional teacher/student pair';
        DO $favorite_runtime_drop_recovery_role$
        BEGIN
          IF to_regrole('{RECOVERY_RUNTIME_ROLE}') IS NOT NULL THEN
            EXECUTE 'DROP ROLE {RECOVERY_RUNTIME_ROLE}';
          END IF;
        END
        $favorite_runtime_drop_recovery_role$;
        """
    )
