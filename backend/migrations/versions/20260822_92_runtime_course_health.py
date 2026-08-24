"""protect COURSE materialization and install DTS v2 runtime health.

Revision ID: 20260822_92_runtime_course_health
Revises: 20260822_91_task_output_contract
"""

from __future__ import annotations

from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260822_92_runtime_course_health"
down_revision: Union[str, None] = "20260822_91_task_output_contract"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None


DOMAIN_ROLE = "tit_dts_domain_projector_runtime"
OUTBOX_ROLE = "tit_dts_outbox_worker_runtime"
COURSE_COMMAND = (
    "public.materialize_course_source_wide_v2("
    "jsonb,bigint,bigint,text,text)"
)
DOMAIN_HEALTH = "public.dts_v2_domain_runtime_health_v1(bigint)"
OUTBOX_HEALTH = "public.dts_v2_outbox_runtime_health_v1(bigint)"
FAVORITE_HEALTH = "public.dts_v2_favorite_runtime_health_v1(bigint)"


def _preflight() -> None:
    required_relations = (
        "dts_pipeline_control",
        "domain_aggregate_revisions",
        "source_courses",
        "source_course_participations",
        "source_course_fact_current",
        "source_participation_fact_current",
        "source_course_labels",
        "teacher_student_relationship_current",
        "lesson_source_wide",
        "lesson_score_component_settlements",
        "score_entries",
        "teachers",
        "dts_dirty_keys",
        "outbox_events",
        "course_favorite_observations",
    )
    required_functions = (
        "public.dts_v2_runtime_primary_guard_v1(text)",
        "public.dts_canonical_json_sha256_v1(jsonb)",
        "public.dts_canonical_json_v1(jsonb)",
        "public.reconcile_course_trigger_matches_v2(text,text,bigint,bigint,text,jsonb,text)",
        "public.rebuild_lesson_score_result_v2(text,text,bigint)",
        "public.teacher_score_projection_vector_v2(text)",
        "public.rebuild_teacher_score_and_qualification_v2(text,jsonb,bigint)",
        "public.materialize_task_plan_v2(text,text,bigint,text)",
        "public.reconcile_blacklist_threshold_v2(text,text,bigint,bigint,text)",
    )
    row = op.get_bind().execute(
        sa.text(
            """
            SELECT
              to_regrole(:domain_role) IS NOT NULL,
              to_regrole(:outbox_role) IS NOT NULL,
              NOT EXISTS (
                SELECT 1 FROM unnest(CAST(:relations AS text[])) name
                WHERE to_regclass('public.' || name) IS NULL
              ),
              NOT EXISTS (
                SELECT 1 FROM unnest(CAST(:functions AS text[])) signature
                WHERE to_regprocedure(signature) IS NULL
              )
            """
        ),
        {
            "domain_role": DOMAIN_ROLE,
            "outbox_role": OUTBOX_ROLE,
            "relations": list(required_relations),
            "functions": list(required_functions),
        },
    ).one()
    if row != (True, True, True, True):
        raise RuntimeError("DTS v2 COURSE/health prerequisites are missing")


def _install_score_entry_helper() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.write_course_component_score_entry_v2(
          p_entry_kind text,p_source_region text,p_source_appoint_id text,
          p_completion_seq integer,p_teacher_id text,p_component_code text,
          p_component_score numeric,p_award_generation bigint,
          p_score_rule_version text,p_evidence_fingerprint text,
          p_evidence_status text,p_aggregate_revision bigint,
          p_projection_generation bigint,p_triggering_event_id text,
          p_reversal_of text
        ) RETURNS text
        LANGUAGE plpgsql
        VOLATILE
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE
          v_idempotency_key text;
          v_score_entry_id text;
          v_dimension text;
          v_entry_type text;
          v_delta numeric;
          v_occurred_at timestamptz;
          v_camp_enrollment_id text;
          v_payload jsonb;
          existing public.score_entries%ROWTYPE;
        BEGIN
          IF p_entry_kind NOT IN ('AWARD','REVERSAL')
             OR p_source_region NOT IN ('dom','ovs')
             OR p_source_appoint_id IS NULL OR btrim(p_source_appoint_id)=''
             OR p_completion_seq<1 OR p_teacher_id IS NULL
             OR btrim(p_teacher_id)='' OR p_component_code NOT IN (
               'FEEDBACK_PRAISE','PERFECT_COMPLETED','PEAK_COMPLETED',
               'CLASS_QUALITY_HARDWARE'
             ) OR p_component_score<=0 OR p_award_generation<1
             OR p_score_rule_version IS NULL OR btrim(p_score_rule_version)=''
             OR p_evidence_fingerprint !~ '^[0-9a-f]{64}$'
             OR p_evidence_status NOT IN ('CONFIRMED','SOURCE_MISSING')
             OR p_aggregate_revision<1 OR p_projection_generation<1
             OR p_triggering_event_id IS NULL
             OR btrim(p_triggering_event_id)='' THEN
            RAISE EXCEPTION 'DTS_V2_COURSE_SCORE_REQUEST_INVALID'
              USING ERRCODE='22023';
          END IF;
          IF (p_entry_kind='AWARD' AND p_reversal_of IS NOT NULL)
             OR (p_entry_kind='REVERSAL' AND p_reversal_of IS NULL) THEN
            RAISE EXCEPTION 'DTS_V2_COURSE_SCORE_REVERSAL_SHAPE_INVALID'
              USING ERRCODE='22023';
          END IF;
          SELECT camp_enrollment_id INTO v_camp_enrollment_id
          FROM public.teachers WHERE teacher_id=p_teacher_id FOR SHARE;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'DTS_V2_COURSE_TEACHER_DEPENDENCY_PENDING'
              USING ERRCODE='23503';
          END IF;
          SELECT completion_end_time INTO v_occurred_at
          FROM public.source_courses
          WHERE source_region=p_source_region
            AND source_appoint_id=p_source_appoint_id
            AND completion_participation_seq=p_completion_seq
            AND completion_teacher_id=p_teacher_id FOR SHARE;
          IF p_entry_kind='AWARD' AND v_occurred_at IS NULL THEN
            RAISE EXCEPTION 'DTS_V2_COURSE_COMPLETION_TIME_REQUIRED'
              USING ERRCODE='23503';
          END IF;
          IF p_entry_kind='REVERSAL' THEN
            v_occurred_at := transaction_timestamp();
            v_idempotency_key := 'lesson-reversal:' || p_reversal_of;
          ELSE
            v_idempotency_key := 'lesson' || chr(58) || p_source_region
              || chr(58) || p_source_appoint_id || chr(58) || 'p'
              || p_completion_seq::text || chr(58) || p_component_code
              || chr(58) || 'gen' || p_award_generation::text || chr(58)
              || p_score_rule_version;
          END IF;
          IF length(v_idempotency_key)>256 THEN
            RAISE EXCEPTION 'DTS_V2_COURSE_SCORE_IDEMPOTENCY_KEY_INVALID'
              USING ERRCODE='22023';
          END IF;
          v_score_entry_id := 'v2score-' || encode(
            sha256(convert_to(v_idempotency_key,'UTF8')),'hex'
          );
          v_dimension := CASE p_component_code
            WHEN 'FEEDBACK_PRAISE' THEN 'USER_FEEDBACK'
            WHEN 'CLASS_QUALITY_HARDWARE' THEN 'CLASS_QUALITY'
            ELSE 'RELIABILITY' END;
          v_entry_type := CASE p_entry_kind WHEN 'AWARD'
            THEN 'LESSON_COMPONENT_AWARD'
            ELSE 'LESSON_COMPONENT_REVERSAL' END;
          v_delta := CASE p_entry_kind WHEN 'AWARD'
            THEN p_component_score ELSE -p_component_score END;
          v_payload := jsonb_build_object(
            'settlement_contract','lesson-score-component-v2',
            'source_region',p_source_region,
            'source_appoint_id',p_source_appoint_id,
            'completion_participation_seq',p_completion_seq,
            'component_code',p_component_code,
            'award_generation',p_award_generation,
            'evidence_fingerprint',p_evidence_fingerprint,
            'triggering_event_id',p_triggering_event_id,
            'aggregate_revision',p_aggregate_revision,
            'projection_generation',p_projection_generation
          );
          INSERT INTO public.score_entries(
            score_entry_id,camp_enrollment_id,lesson_id,source_region,
            source_appoint_id,participation_seq,teacher_id,dimension,
            entry_type,delta_score,reason_code,evidence_status,
            score_rule_version,occurred_at,recorded_at,
            reversal_of_score_entry_id,task_assignment_id,
            projection_origin,materialized_by_run_id,projection_generation,
            idempotency_key,payload
          ) VALUES (
            v_score_entry_id,v_camp_enrollment_id,p_source_appoint_id,
            p_source_region,p_source_appoint_id,p_completion_seq,p_teacher_id,
            v_dimension,v_entry_type,v_delta,p_component_code,
            CASE p_entry_kind WHEN 'AWARD' THEN 'CONFIRMED'
                 ELSE p_evidence_status END,
            p_score_rule_version,v_occurred_at,transaction_timestamp(),
            p_reversal_of,NULL,'V2_LIVE',NULL,p_projection_generation,
            v_idempotency_key,v_payload
          ) ON CONFLICT (idempotency_key) DO NOTHING
          RETURNING score_entry_id INTO v_score_entry_id;
          IF v_score_entry_id IS NULL THEN
            SELECT * INTO existing FROM public.score_entries
            WHERE idempotency_key=v_idempotency_key FOR SHARE;
            IF NOT FOUND OR existing.score_entry_id IS DISTINCT FROM
                 ('v2score-' || encode(
                   sha256(convert_to(v_idempotency_key,'UTF8')),'hex'
                 ))
               OR existing.teacher_id IS DISTINCT FROM p_teacher_id
               OR existing.source_region IS DISTINCT FROM p_source_region
               OR existing.source_appoint_id IS DISTINCT FROM p_source_appoint_id
               OR existing.participation_seq IS DISTINCT FROM p_completion_seq
               OR existing.dimension IS DISTINCT FROM v_dimension
               OR existing.entry_type IS DISTINCT FROM v_entry_type
               OR existing.delta_score::numeric IS DISTINCT FROM v_delta
               OR existing.reason_code IS DISTINCT FROM p_component_code
               OR existing.score_rule_version IS DISTINCT FROM p_score_rule_version
               OR existing.reversal_of_score_entry_id IS DISTINCT FROM p_reversal_of
               OR existing.projection_origin IS DISTINCT FROM 'V2_LIVE'
               OR existing.projection_generation IS DISTINCT FROM
                    p_projection_generation
               OR existing.payload IS DISTINCT FROM v_payload THEN
              RAISE EXCEPTION 'DTS_V2_COURSE_SCORE_IDEMPOTENCY_CONFLICT'
                USING ERRCODE='23505';
            END IF;
            v_score_entry_id := existing.score_entry_id;
          END IF;
          RETURN v_score_entry_id;
        END
        $function$;
        REVOKE ALL ON FUNCTION public.write_course_component_score_entry_v2(
          text,text,text,integer,text,text,numeric,bigint,text,text,text,
          bigint,bigint,text,text
        ) FROM PUBLIC;
        """
    )


def _install_component_command() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.settle_course_components_v2(
          p_source_region text,p_source_appoint_id text,
          p_aggregate_revision bigint,p_projection_generation bigint,
          p_triggering_event_id text
        ) RETURNS jsonb
        LANGUAGE plpgsql
        VOLATILE
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE
          course_row public.source_courses%ROWTYPE;
          owner_row public.source_course_participations%ROWTYPE;
          fact_row public.source_course_fact_current%ROWTYPE;
          penalty_row public.source_participation_fact_current%ROWTYPE;
          settlement public.lesson_score_component_settlements%ROWTYPE;
          condition record;
          v_owner boolean := false;
          v_should_award boolean;
          v_evidence_status text;
          v_evidence_fingerprint text;
          v_component_score numeric;
          v_award_generation bigint;
          v_award_id text;
          v_reversal_id text;
          v_stale_reason text;
          c_entries integer := 0;
          c_awards integer := 0;
          c_reversals integer := 0;
          c_replacements integer := 0;
          c_touches integer := 0;
        BEGIN
          SELECT * INTO course_row FROM public.source_courses
          WHERE source_region=p_source_region
            AND source_appoint_id=p_source_appoint_id FOR SHARE;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'DTS_V2_COURSE_SOURCE_MISSING'
              USING ERRCODE='23503';
          END IF;
          IF course_row.completion_conflict_status='PENDING' THEN
            RETURN jsonb_build_object(
              'score_entries',0,'component_awards',0,
              'component_reversals',0,'component_replacements',0,
              'component_projection_touches',0,
              'completion_conflict_holds',1
            );
          END IF;
          IF NOT course_row.source_is_deleted
             AND course_row.evidence_status='CONFIRMED'
             AND course_row.completion_participation_seq IS NOT NULL
             AND course_row.completion_teacher_id IS NOT NULL THEN
            SELECT * INTO owner_row
            FROM public.source_course_participations
            WHERE source_region=p_source_region
              AND source_appoint_id=p_source_appoint_id
              AND participation_seq=course_row.completion_participation_seq
              AND teacher_id=course_row.completion_teacher_id
              AND participation_role='COMPLETION'
              AND NOT source_deleted
              AND teacher_region_evidence_status='CONFIRMED'
              AND teacher_expected_source_region=p_source_region
            FOR SHARE;
            v_owner := FOUND;
          END IF;
          v_stale_reason := CASE WHEN course_row.source_is_deleted
            THEN 'COURSE_DELETED' ELSE 'COMPLETION_OWNER_CHANGED' END;
          FOR settlement IN
            SELECT * FROM public.lesson_score_component_settlements
            WHERE source_region=p_source_region
              AND source_appoint_id=p_source_appoint_id
              AND status='AWARDED'
              AND (NOT v_owner OR completion_participation_seq<>
                   owner_row.participation_seq)
            ORDER BY completion_participation_seq,component_code FOR UPDATE
          LOOP
            v_evidence_fingerprint := public.dts_canonical_json_sha256_v1(
              jsonb_build_object(
                'reason',v_stale_reason,'current_completion_seq',
                CASE WHEN v_owner THEN owner_row.participation_seq ELSE NULL END
              )
            );
            v_reversal_id := public.write_course_component_score_entry_v2(
              'REVERSAL',p_source_region,p_source_appoint_id,
              settlement.completion_participation_seq,settlement.teacher_id,
              settlement.component_code,settlement.component_score,
              settlement.award_generation,settlement.score_rule_version,
              v_evidence_fingerprint,'CONFIRMED',p_aggregate_revision,
              p_projection_generation,p_triggering_event_id,
              settlement.current_award_score_entry_id
            );
            UPDATE public.lesson_score_component_settlements SET
              status='REVERSED',score_rule_version=settlement.score_rule_version,
              evidence_fingerprint=v_evidence_fingerprint,
              current_award_score_entry_id=NULL,
              last_reversal_score_entry_id=v_reversal_id,
              award_projection_generation=p_projection_generation,
              row_version=row_version+1,reversed_at=transaction_timestamp(),
              updated_at=transaction_timestamp()
            WHERE source_region=settlement.source_region
              AND source_appoint_id=settlement.source_appoint_id
              AND completion_participation_seq=
                  settlement.completion_participation_seq
              AND component_code=settlement.component_code
              AND row_version=settlement.row_version;
            IF NOT FOUND THEN
              RAISE EXCEPTION 'DTS_V2_COURSE_COMPONENT_CONCURRENT_UPDATE'
                USING ERRCODE='40001';
            END IF;
            c_entries:=c_entries+1; c_reversals:=c_reversals+1;
          END LOOP;
          IF NOT v_owner THEN
            RETURN jsonb_build_object(
              'score_entries',c_entries,'component_awards',c_awards,
              'component_reversals',c_reversals,
              'component_replacements',c_replacements,
              'component_projection_touches',c_touches,
              'completion_conflict_holds',0
            );
          END IF;
          SELECT * INTO fact_row FROM public.source_course_fact_current
          WHERE source_region=p_source_region
            AND source_appoint_id=p_source_appoint_id FOR SHARE;
          SELECT * INTO penalty_row
          FROM public.source_participation_fact_current
          WHERE source_region=p_source_region
            AND source_appoint_id=p_source_appoint_id
            AND participation_seq=owner_row.participation_seq FOR SHARE;

          FOR condition IN
            SELECT * FROM (VALUES
              ('FEEDBACK_PRAISE',5::numeric,
               CASE WHEN fact_row.grading_evidence_status='CONFIRMED'
                    THEN p_source_region='dom'
                         AND fact_row.grading_classification='POSITIVE'
                    ELSE NULL END,
               jsonb_build_object(
                 'source_region',p_source_region,
                 'grading_classification',CASE
                   WHEN fact_row.grading_classification IN ('POSITIVE','NEGATIVE')
                   THEN fact_row.grading_classification ELSE NULL END,
                 'scope_complete',coalesce(
                   fact_row.grading_evidence_status='CONFIRMED',false)
               )),
              ('PERFECT_COMPLETED',4::numeric,
               CASE WHEN penalty_row.late_evidence_status='CONFIRMED'
                         AND penalty_row.early_evidence_status='CONFIRMED'
                         AND penalty_row.is_late IS NOT NULL
                         AND penalty_row.is_early IS NOT NULL
                    THEN NOT penalty_row.is_late AND NOT penalty_row.is_early
                    ELSE NULL END,
               jsonb_build_object(
                 'late',penalty_row.is_late,'early',penalty_row.is_early,
                 'scope_complete',coalesce(
                   penalty_row.late_evidence_status='CONFIRMED'
                   AND penalty_row.early_evidence_status='CONFIRMED',false)
               )),
              ('PEAK_COMPLETED',2::numeric,course_row.completion_is_peak,
               jsonb_build_object(
                 'completion_is_peak',course_row.completion_is_peak
               )),
              ('CLASS_QUALITY_HARDWARE',2::numeric,NULL::boolean,
               jsonb_build_object(
                 'camera_off',fact_row.is_camera_off,
                 'cpu_high',NULL,'network_high',NULL
               ))
            ) AS value(
              component_code,component_score,should_award,evidence_document
            ) ORDER BY component_code
          LOOP
            v_should_award:=condition.should_award;
            v_component_score:=condition.component_score;
            v_evidence_status:=CASE WHEN v_should_award IS NULL
              THEN 'SOURCE_MISSING' ELSE 'CONFIRMED' END;
            v_evidence_fingerprint:=public.dts_canonical_json_sha256_v1(
              condition.evidence_document
            );
            SELECT * INTO settlement
            FROM public.lesson_score_component_settlements
            WHERE source_region=p_source_region
              AND source_appoint_id=p_source_appoint_id
              AND completion_participation_seq=owner_row.participation_seq
              AND component_code=condition.component_code FOR UPDATE;
            IF NOT FOUND THEN
              IF v_should_award IS TRUE THEN
                v_award_generation:=1;
                v_award_id:=public.write_course_component_score_entry_v2(
                  'AWARD',p_source_region,p_source_appoint_id,
                  owner_row.participation_seq,owner_row.teacher_id,
                  condition.component_code,v_component_score,
                  v_award_generation,'dts-lesson-score-v2',
                  v_evidence_fingerprint,v_evidence_status,
                  p_aggregate_revision,p_projection_generation,
                  p_triggering_event_id,NULL
                );
                INSERT INTO public.lesson_score_component_settlements(
                  source_region,source_appoint_id,
                  completion_participation_seq,component_code,teacher_id,
                  status,award_generation,component_score,
                  score_rule_version,evidence_fingerprint,
                  current_award_score_entry_id,last_reversal_score_entry_id,
                  materialization_origin,materialized_by_run_id,
                  award_projection_generation,row_version,awarded_at,
                  reversed_at,created_at,updated_at
                ) VALUES (
                  p_source_region,p_source_appoint_id,
                  owner_row.participation_seq,condition.component_code,
                  owner_row.teacher_id,'AWARDED',v_award_generation,
                  v_component_score,'dts-lesson-score-v2',
                  v_evidence_fingerprint,v_award_id,NULL,'V2_LIVE',NULL,
                  p_projection_generation,1,transaction_timestamp(),NULL,
                  transaction_timestamp(),transaction_timestamp()
                );
                c_entries:=c_entries+1; c_awards:=c_awards+1;
              END IF;
              CONTINUE;
            END IF;
            IF settlement.teacher_id IS DISTINCT FROM owner_row.teacher_id THEN
              RAISE EXCEPTION 'DTS_V2_COURSE_COMPONENT_OWNER_CONFLICT'
                USING ERRCODE='55000';
            END IF;
            IF settlement.status='AWARDED' AND v_should_award IS TRUE
               AND settlement.component_score IS NOT DISTINCT FROM
                   v_component_score
               AND settlement.score_rule_version='dts-lesson-score-v2'
               AND settlement.evidence_fingerprint=v_evidence_fingerprint THEN
              IF p_projection_generation>settlement.award_projection_generation THEN
                UPDATE public.lesson_score_component_settlements SET
                  award_projection_generation=p_projection_generation,
                  row_version=row_version+1,updated_at=transaction_timestamp()
                WHERE source_region=settlement.source_region
                  AND source_appoint_id=settlement.source_appoint_id
                  AND completion_participation_seq=
                      settlement.completion_participation_seq
                  AND component_code=settlement.component_code
                  AND row_version=settlement.row_version;
                IF NOT FOUND THEN
                  RAISE EXCEPTION 'DTS_V2_COURSE_COMPONENT_CONCURRENT_UPDATE'
                    USING ERRCODE='40001';
                END IF;
                c_touches:=c_touches+1;
              END IF;
              CONTINUE;
            END IF;
            IF settlement.status='REVERSED' AND v_should_award IS NOT TRUE THEN
              IF p_projection_generation>settlement.award_projection_generation THEN
                UPDATE public.lesson_score_component_settlements SET
                  award_projection_generation=p_projection_generation,
                  row_version=row_version+1,updated_at=transaction_timestamp()
                WHERE source_region=settlement.source_region
                  AND source_appoint_id=settlement.source_appoint_id
                  AND completion_participation_seq=
                      settlement.completion_participation_seq
                  AND component_code=settlement.component_code
                  AND row_version=settlement.row_version;
                c_touches:=c_touches+1;
              END IF;
              CONTINUE;
            END IF;
            v_reversal_id:=NULL; v_award_id:=NULL;
            IF settlement.status='AWARDED' THEN
              v_reversal_id:=public.write_course_component_score_entry_v2(
                'REVERSAL',p_source_region,p_source_appoint_id,
                owner_row.participation_seq,owner_row.teacher_id,
                condition.component_code,settlement.component_score,
                settlement.award_generation,'dts-lesson-score-v2',
                v_evidence_fingerprint,v_evidence_status,
                p_aggregate_revision,p_projection_generation,
                p_triggering_event_id,
                settlement.current_award_score_entry_id
              );
              c_entries:=c_entries+1;
            END IF;
            IF v_should_award IS TRUE THEN
              v_award_generation:=settlement.award_generation+1;
              v_award_id:=public.write_course_component_score_entry_v2(
                'AWARD',p_source_region,p_source_appoint_id,
                owner_row.participation_seq,owner_row.teacher_id,
                condition.component_code,v_component_score,
                v_award_generation,'dts-lesson-score-v2',
                v_evidence_fingerprint,v_evidence_status,
                p_aggregate_revision,p_projection_generation,
                p_triggering_event_id,NULL
              );
              c_entries:=c_entries+1;
            ELSE
              v_award_generation:=settlement.award_generation;
            END IF;
            UPDATE public.lesson_score_component_settlements SET
              status=CASE WHEN v_should_award IS TRUE
                          THEN 'AWARDED' ELSE 'REVERSED' END,
              award_generation=v_award_generation,
              component_score=CASE WHEN v_should_award IS TRUE
                                   THEN v_component_score
                                   ELSE settlement.component_score END,
              score_rule_version='dts-lesson-score-v2',
              evidence_fingerprint=v_evidence_fingerprint,
              current_award_score_entry_id=v_award_id,
              last_reversal_score_entry_id=coalesce(
                v_reversal_id,settlement.last_reversal_score_entry_id
              ),award_projection_generation=p_projection_generation,
              row_version=row_version+1,
              awarded_at=CASE WHEN v_should_award IS TRUE
                              THEN transaction_timestamp() ELSE awarded_at END,
              reversed_at=CASE WHEN v_should_award IS TRUE THEN NULL
                               ELSE transaction_timestamp() END,
              updated_at=transaction_timestamp()
            WHERE source_region=settlement.source_region
              AND source_appoint_id=settlement.source_appoint_id
              AND completion_participation_seq=
                  settlement.completion_participation_seq
              AND component_code=settlement.component_code
              AND row_version=settlement.row_version;
            IF NOT FOUND THEN
              RAISE EXCEPTION 'DTS_V2_COURSE_COMPONENT_CONCURRENT_UPDATE'
                USING ERRCODE='40001';
            END IF;
            IF v_should_award IS TRUE AND v_reversal_id IS NOT NULL THEN
              c_replacements:=c_replacements+1;
            ELSIF v_should_award IS TRUE THEN
              c_awards:=c_awards+1;
            ELSE
              c_reversals:=c_reversals+1;
            END IF;
          END LOOP;
          RETURN jsonb_build_object(
            'score_entries',c_entries,'component_awards',c_awards,
            'component_reversals',c_reversals,
            'component_replacements',c_replacements,
            'component_projection_touches',c_touches,
            'completion_conflict_holds',0
          );
        END
        $function$;
        REVOKE ALL ON FUNCTION public.settle_course_components_v2(
          text,text,bigint,bigint,text
        ) FROM PUBLIC;
        """
    )


def _install_course_command() -> None:
    op.execute(
        rf"""
        CREATE FUNCTION public.materialize_course_source_wide_v2(
          p_request jsonb,p_expected_aggregate_revision bigint,
          p_projection_generation bigint,p_triggering_event_id text,
          p_expected_request_sha256 text
        ) RETURNS jsonb
        LANGUAGE plpgsql
        VOLATILE
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE
          actor_name text:=session_user;
          owner_name text:=current_user;
          request_hash text;
          source_region_value text;
          source_appoint_id_value text;
          aggregate_row public.domain_aggregate_revisions%ROWTYPE;
          course_row public.source_courses%ROWTYPE;
          selected_row public.source_course_participations%ROWTYPE;
          fact_row public.source_course_fact_current%ROWTYPE;
          relationship_row public.teacher_student_relationship_current%ROWTYPE;
          v_generation bigint;
          v_selected boolean:=false;
          v_scoring boolean:=false;
          v_fact_version bigint;
          v_feedback_detail text;
          v_grading text;
          v_student_token text;
          v_relationship_found boolean:=false;
          v_affected_teachers jsonb;
          v_versions jsonb;
          v_compatibility_changes integer:=0;
          v_component_counts jsonb;
        BEGIN
          IF actor_name<>owner_name AND actor_name<>'{OUTBOX_ROLE}' THEN
            RAISE EXCEPTION 'DTS_V2_COURSE_MATERIALIZER_CALLER_FORBIDDEN'
              USING ERRCODE='42501';
          END IF;
          v_generation:=public.dts_v2_runtime_primary_guard_v1('COURSE');
          IF v_generation IS NULL OR v_generation<>p_projection_generation THEN
            RAISE EXCEPTION 'DTS_V2_COURSE_PRIMARY_GENERATION_MISMATCH'
              USING ERRCODE='55000';
          END IF;
          IF jsonb_typeof(p_request)<>'object'
             OR (SELECT array_agg(key ORDER BY key)
                 FROM jsonb_object_keys(p_request) key) IS DISTINCT FROM
                ARRAY['affected_teacher_ids','aggregate_state_sha256',
                      'course_fact_row_version','course_row_version',
                      'participation_versions','protocol_version',
                      'source_appoint_id','source_region']::text[]
             OR p_request->>'protocol_version'<>
                  'course-materialization-request-v1'
             OR p_expected_aggregate_revision<1
             OR p_projection_generation<1
             OR p_triggering_event_id IS NULL
             OR btrim(p_triggering_event_id)=''
             OR length(p_triggering_event_id)>512
             OR p_expected_request_sha256 !~ '^[0-9a-f]{{64}}$' THEN
            RAISE EXCEPTION 'DTS_V2_COURSE_MATERIALIZATION_REQUEST_INVALID'
              USING ERRCODE='22023';
          END IF;
          request_hash:=public.dts_canonical_json_sha256_v1(p_request);
          IF request_hash<>p_expected_request_sha256 THEN
            RAISE EXCEPTION 'DTS_V2_COURSE_MATERIALIZATION_HASH_MISMATCH'
              USING ERRCODE='22023';
          END IF;
          source_region_value:=p_request->>'source_region';
          source_appoint_id_value:=p_request->>'source_appoint_id';
          IF source_region_value NOT IN ('dom','ovs')
             OR source_appoint_id_value IS NULL
             OR btrim(source_appoint_id_value)=''
             OR p_request->>'aggregate_state_sha256' !~ '^[0-9a-f]{{64}}$'
             OR jsonb_typeof(p_request->'participation_versions')<>'array'
             OR jsonb_typeof(p_request->'affected_teacher_ids')<>'array'
             OR (p_request->>'course_row_version') !~ '^[1-9][0-9]*$' THEN
            RAISE EXCEPTION 'DTS_V2_COURSE_MATERIALIZATION_IDENTITY_INVALID'
              USING ERRCODE='22023';
          END IF;
          SELECT * INTO aggregate_row
          FROM public.domain_aggregate_revisions
          WHERE aggregate_type='COURSE'
            AND canonical_key=jsonb_build_object(
              'source_region',source_region_value,
              'source_appoint_id',source_appoint_id_value
            ) FOR SHARE;
          IF NOT FOUND
             OR aggregate_row.revision<>p_expected_aggregate_revision
             OR aggregate_row.aggregate_state_sha256<>
                  p_request->>'aggregate_state_sha256' THEN
            RAISE EXCEPTION 'DTS_V2_COURSE_AGGREGATE_STALE'
              USING ERRCODE='40001';
          END IF;
          SELECT * INTO course_row FROM public.source_courses
          WHERE source_region=source_region_value
            AND source_appoint_id=source_appoint_id_value FOR SHARE;
          IF NOT FOUND OR course_row.row_version<>
               (p_request->>'course_row_version')::bigint THEN
            RAISE EXCEPTION 'DTS_V2_COURSE_SOURCE_VERSION_STALE'
              USING ERRCODE='40001';
          END IF;
          SELECT row_version INTO v_fact_version
          FROM public.source_course_fact_current
          WHERE source_region=source_region_value
            AND source_appoint_id=source_appoint_id_value FOR SHARE;
          IF (CASE WHEN v_fact_version IS NULL THEN 'null'::jsonb
                   ELSE to_jsonb(v_fact_version) END)
             IS DISTINCT FROM p_request->'course_fact_row_version' THEN
            RAISE EXCEPTION 'DTS_V2_COURSE_FACT_VERSION_STALE'
              USING ERRCODE='40001';
          END IF;
          SELECT coalesce(jsonb_agg(jsonb_build_object(
                   'participation_seq',p.participation_seq,
                   'participation_row_version',p.row_version,
                   'participation_fact_row_version',f.row_version
                 ) ORDER BY p.participation_seq),'[]'::jsonb),
                 coalesce(to_jsonb(array_agg(DISTINCT p.teacher_id
                   ORDER BY p.teacher_id)),'[]'::jsonb)
          INTO v_versions,v_affected_teachers
          FROM public.source_course_participations p
          LEFT JOIN public.source_participation_fact_current f
            ON f.source_region=p.source_region
           AND f.source_appoint_id=p.source_appoint_id
           AND f.participation_seq=p.participation_seq
          WHERE p.source_region=source_region_value
            AND p.source_appoint_id=source_appoint_id_value;
          IF v_versions IS DISTINCT FROM p_request->'participation_versions'
             OR v_affected_teachers IS DISTINCT FROM
                  p_request->'affected_teacher_ids' THEN
            RAISE EXCEPTION 'DTS_V2_COURSE_PARTICIPATION_VERSION_STALE'
              USING ERRCODE='40001';
          END IF;
          IF course_row.completion_participation_seq IS NOT NULL THEN
            SELECT * INTO selected_row
            FROM public.source_course_participations
            WHERE source_region=source_region_value
              AND source_appoint_id=source_appoint_id_value
              AND participation_seq=course_row.completion_participation_seq
              AND teacher_id=course_row.completion_teacher_id FOR SHARE;
            v_selected:=FOUND;
          ELSIF course_row.current_participation_seq IS NOT NULL THEN
            SELECT * INTO selected_row
            FROM public.source_course_participations
            WHERE source_region=source_region_value
              AND source_appoint_id=source_appoint_id_value
              AND participation_seq=course_row.current_participation_seq
              AND teacher_id=course_row.current_teacher_id
              AND is_current FOR SHARE;
            v_selected:=FOUND;
          END IF;
          IF NOT v_selected AND (
               course_row.completion_participation_seq IS NOT NULL
               OR course_row.current_participation_seq IS NOT NULL
             ) THEN
            RAISE EXCEPTION 'DTS_V2_COURSE_COMPAT_PARTICIPATION_MISSING'
              USING ERRCODE='23503';
          END IF;
          IF NOT v_selected AND course_row.source_is_deleted THEN
            DELETE FROM public.lesson_source_wide
            WHERE source_region=source_region_value
              AND "课程id"=source_appoint_id_value;
            GET DIAGNOSTICS v_compatibility_changes=ROW_COUNT;
          ELSE
            SELECT * INTO fact_row FROM public.source_course_fact_current
            WHERE source_region=source_region_value
              AND source_appoint_id=source_appoint_id_value FOR SHARE;
            v_scoring:=v_selected AND NOT course_row.source_is_deleted
              AND selected_row.participation_role='COMPLETION'
              AND selected_row.participation_seq=
                  course_row.completion_participation_seq
              AND selected_row.teacher_id=course_row.completion_teacher_id
              AND NOT selected_row.source_deleted
              AND course_row.evidence_status='CONFIRMED'
              AND selected_row.teacher_region_evidence_status='CONFIRMED'
              AND selected_row.teacher_expected_source_region=
                  source_region_value;
            v_student_token:=CASE WHEN v_scoring
              THEN course_row.completion_student_token
              ELSE course_row.student_token END;
            IF v_selected AND v_student_token IS NOT NULL THEN
              SELECT * INTO relationship_row
              FROM public.teacher_student_relationship_current
              WHERE source_region=source_region_value
                AND teacher_id=selected_row.teacher_id
                AND student_token=v_student_token FOR SHARE;
              v_relationship_found:=FOUND;
            END IF;
            SELECT CASE WHEN count(*)=0 THEN NULL ELSE
                     public.dts_canonical_json_v1(jsonb_agg(
                       jsonb_build_object(
                         'label_id',label_id,
                         'label_name',label_name_snapshot
                       ) ORDER BY label_id_type,label_id
                     )) END
            INTO v_feedback_detail FROM (
              SELECT DISTINCT ON (label_id_type,label_id)
                     label_id_type,label_id,label_name_snapshot
              FROM public.source_course_labels
              WHERE source_region=source_region_value
                AND source_appoint_id=source_appoint_id_value
                AND NOT is_deleted
              ORDER BY label_id_type,label_id,
                       create_time DESC NULLS LAST,dt DESC NULLS LAST,
                       source_log_id_numeric DESC NULLS LAST,
                       convert_to(coalesce(source_log_id_text,''),'UTF8') DESC,
                       source_row_revision DESC
            ) current_labels;
            v_grading:=CASE WHEN v_selected THEN
              coalesce(fact_row.grading_classification,'SOURCE_MISSING')
              ELSE 'SOURCE_MISSING' END;
            INSERT INTO public.lesson_source_wide(
              source_region,"课程id","上课日期","上课时间","是否高峰",
              "老师id","学员id","课程状态","缺席原因明细","迟到","早退",
              "差评分","差评标签","投诉一级分类","投诉二级分类",
              "投诉三级分类","是否拉黑","收藏","好评标签","评价详情",
              "未开摄像头","cpu占用过高","网络延迟过高"
            ) VALUES (
              source_region_value,source_appoint_id_value,
              CASE WHEN v_scoring THEN course_row.completion_lesson_local_date
                   ELSE course_row.lesson_local_date END,
              CASE WHEN v_scoring THEN course_row.completion_lesson_local_time
                   ELSE course_row.lesson_local_time END,
              CASE WHEN v_scoring THEN course_row.completion_is_peak
                   ELSE course_row.is_peak END,
              CASE WHEN v_selected THEN selected_row.teacher_id ELSE NULL END,
              v_student_token,
              CASE WHEN v_selected THEN selected_row.participation_status
                   ELSE course_row.source_status END,
              CASE WHEN v_selected THEN selected_row.absence_reason_detail
                   ELSE NULL END,
              CASE WHEN v_selected THEN (
                SELECT is_late FROM public.source_participation_fact_current
                WHERE source_region=source_region_value
                  AND source_appoint_id=source_appoint_id_value
                  AND participation_seq=selected_row.participation_seq
              ) ELSE NULL END,
              CASE WHEN v_selected THEN (
                SELECT is_early FROM public.source_participation_fact_current
                WHERE source_region=source_region_value
                  AND source_appoint_id=source_appoint_id_value
                  AND participation_seq=selected_row.participation_seq
              ) ELSE NULL END,
              CASE WHEN v_selected THEN fact_row.negative_score::double precision
                   ELSE NULL END,
              CASE WHEN v_grading='NEGATIVE' THEN true
                   WHEN v_grading IN ('POSITIVE','UNCLASSIFIED') THEN false
                   ELSE NULL END,
              CASE WHEN v_selected THEN fact_row.latest_category_l1_snapshot END,
              CASE WHEN v_selected THEN fact_row.latest_category_l2_snapshot END,
              CASE WHEN v_selected THEN fact_row.latest_category_l3_snapshot END,
              CASE WHEN v_relationship_found
                   THEN relationship_row.is_blocked END,
              CASE WHEN v_relationship_found
                   THEN relationship_row.is_favorited END,
              CASE WHEN v_grading='POSITIVE' THEN true
                   WHEN v_grading IN ('NEGATIVE','UNCLASSIFIED') THEN false
                   ELSE NULL END,
              CASE WHEN v_selected THEN v_feedback_detail END,
              CASE WHEN v_selected THEN fact_row.is_camera_off END,NULL,NULL
            ) ON CONFLICT (source_region,"课程id") DO UPDATE SET
              "上课日期"=EXCLUDED."上课日期","上课时间"=EXCLUDED."上课时间",
              "是否高峰"=EXCLUDED."是否高峰","老师id"=EXCLUDED."老师id",
              "学员id"=EXCLUDED."学员id","课程状态"=EXCLUDED."课程状态",
              "缺席原因明细"=EXCLUDED."缺席原因明细","迟到"=EXCLUDED."迟到",
              "早退"=EXCLUDED."早退","差评分"=EXCLUDED."差评分",
              "差评标签"=EXCLUDED."差评标签",
              "投诉一级分类"=EXCLUDED."投诉一级分类",
              "投诉二级分类"=EXCLUDED."投诉二级分类",
              "投诉三级分类"=EXCLUDED."投诉三级分类",
              "是否拉黑"=EXCLUDED."是否拉黑","收藏"=EXCLUDED."收藏",
              "好评标签"=EXCLUDED."好评标签","评价详情"=EXCLUDED."评价详情",
              "未开摄像头"=EXCLUDED."未开摄像头",
              "cpu占用过高"=NULL,"网络延迟过高"=NULL
            WHERE ROW(
              lesson_source_wide."上课日期",lesson_source_wide."上课时间",
              lesson_source_wide."是否高峰",lesson_source_wide."老师id",
              lesson_source_wide."学员id",lesson_source_wide."课程状态",
              lesson_source_wide."缺席原因明细",lesson_source_wide."迟到",
              lesson_source_wide."早退",lesson_source_wide."差评分",
              lesson_source_wide."差评标签",
              lesson_source_wide."投诉一级分类",
              lesson_source_wide."投诉二级分类",
              lesson_source_wide."投诉三级分类",
              lesson_source_wide."是否拉黑",lesson_source_wide."收藏",
              lesson_source_wide."好评标签",lesson_source_wide."评价详情",
              lesson_source_wide."未开摄像头",
              lesson_source_wide."cpu占用过高",
              lesson_source_wide."网络延迟过高"
            ) IS DISTINCT FROM ROW(
              EXCLUDED."上课日期",EXCLUDED."上课时间",EXCLUDED."是否高峰",
              EXCLUDED."老师id",EXCLUDED."学员id",EXCLUDED."课程状态",
              EXCLUDED."缺席原因明细",EXCLUDED."迟到",EXCLUDED."早退",
              EXCLUDED."差评分",EXCLUDED."差评标签",
              EXCLUDED."投诉一级分类",EXCLUDED."投诉二级分类",
              EXCLUDED."投诉三级分类",EXCLUDED."是否拉黑",EXCLUDED."收藏",
              EXCLUDED."好评标签",EXCLUDED."评价详情",
              EXCLUDED."未开摄像头",NULL,NULL
            );
            GET DIAGNOSTICS v_compatibility_changes=ROW_COUNT;
          END IF;
          v_component_counts:=public.settle_course_components_v2(
            source_region_value,source_appoint_id_value,
            p_expected_aggregate_revision,p_projection_generation,
            p_triggering_event_id
          );
          -- The rev76 ownership assertion is a deferred invoker-security
          -- trigger.  Flush it while this protected command still runs as the
          -- owner; the restricted Outbox login must not need direct EXECUTE on
          -- internal assertion helpers at caller COMMIT.
          SET CONSTRAINTS ct_lesson_component_course_guard IMMEDIATE;
          SET CONSTRAINTS ct_lesson_component_course_guard DEFERRED;
          RETURN jsonb_build_object(
            'protocol_version','course-materialization-result-v1',
            'request_sha256',request_hash,
            'aggregate_state_sha256',aggregate_row.aggregate_state_sha256,
            'projection_generation',p_projection_generation,
            'affected_teacher_ids',v_affected_teachers,
            'counts',v_component_counts || jsonb_build_object(
              'compatibility_changes',v_compatibility_changes
            )
          );
        END
        $function$;
        REVOKE ALL ON FUNCTION {COURSE_COMMAND} FROM PUBLIC;
        """
    )


def _install_health_functions() -> None:
    op.execute(
        rf"""
        CREATE FUNCTION public.dts_v2_domain_runtime_health_v1(
          p_stale_after_seconds bigint
        ) RETURNS jsonb
        LANGUAGE plpgsql STABLE SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE actor_name text:=session_user; owner_name text:=current_user;
        DECLARE control_count integer; DECLARE control_mode text;
        DECLARE generation bigint; DECLARE now_at timestamptz:=transaction_timestamp();
        DECLARE result jsonb;
        BEGIN
          IF actor_name<>owner_name AND actor_name<>'{DOMAIN_ROLE}' THEN
            RAISE EXCEPTION 'DTS_V2_DOMAIN_HEALTH_CALLER_FORBIDDEN'
              USING ERRCODE='42501'; END IF;
          IF p_stale_after_seconds<1 OR p_stale_after_seconds>86400 THEN
            RAISE EXCEPTION 'DTS_V2_RUNTIME_HEALTH_THRESHOLD_INVALID'
              USING ERRCODE='22023'; END IF;
          PERFORM pg_advisory_xact_lock_shared(hashtextextended('tit:dts-v2-cutover',0));
          SELECT count(*) INTO control_count FROM public.dts_pipeline_control;
          SELECT mode,projection_generation INTO control_mode,generation
          FROM public.dts_pipeline_control WHERE control_id='PRIMARY';
          IF control_count<>1 OR NOT FOUND OR control_mode NOT IN (
               'V1_COMPAT_DUAL_CAPTURE','V2_PRIMARY','ROLLED_BACK'
             ) OR generation<0 THEN
            RAISE EXCEPTION 'DTS_V2_RUNTIME_HEALTH_CONTROL_INVALID'
              USING ERRCODE='55000'; END IF;
          IF to_regprocedure('public.claim_domain_dirty_keys_v2(text,integer,integer)') IS NULL
             OR NOT has_function_privilege(actor_name,
                  'public.claim_domain_dirty_keys_v2(text,integer,integer)','EXECUTE')
             OR NOT has_function_privilege(actor_name,
                  'public.publish_domain_aggregate_revision_v2(text,jsonb,jsonb,text,jsonb,bigint,jsonb,text,jsonb)','EXECUTE') THEN
            RAISE EXCEPTION 'DTS_V2_DOMAIN_HEALTH_CAPABILITY_INVALID'
              USING ERRCODE='42501'; END IF;
          SELECT jsonb_build_object(
            'protocol_version','dts-v2-domain-runtime-health-v1',
            'mode',control_mode,'projection_generation',generation,
            'runnable_count',count(*) FILTER (WHERE status IN ('PENDING','RETRY') AND next_attempt_at<=now_at),
            'active_lease_count',count(*) FILTER (WHERE status='PROCESSING' AND lease_expires_at>now_at),
            'expired_lease_count',count(*) FILTER (WHERE status='PROCESSING' AND lease_expires_at<=now_at),
            'business_wait_count',count(*) FILTER (WHERE status='WAITING_DEPENDENCY'),
            'dead_count',count(*) FILTER (WHERE status='DEAD'),
            'stale_runnable_count',count(*) FILTER (WHERE status IN ('PENDING','RETRY') AND next_attempt_at<=now_at-make_interval(secs=>p_stale_after_seconds::double precision)),
            'oldest_runnable_age_seconds',floor(extract(epoch FROM now_at-min(next_attempt_at) FILTER (WHERE status IN ('PENDING','RETRY') AND next_attempt_at<=now_at)))::bigint,
            'oldest_active_lease_age_seconds',floor(extract(epoch FROM now_at-min(claimed_at) FILTER (WHERE status='PROCESSING' AND lease_expires_at>now_at)))::bigint
          ) INTO result FROM public.dts_dirty_keys;
          RETURN result;
        END $function$;

        CREATE FUNCTION public.dts_v2_outbox_runtime_health_v1(
          p_stale_after_seconds bigint
        ) RETURNS jsonb
        LANGUAGE plpgsql STABLE SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE actor_name text:=session_user; owner_name text:=current_user;
        DECLARE control_count integer; DECLARE control_mode text;
        DECLARE generation bigint; DECLARE now_at timestamptz:=transaction_timestamp();
        DECLARE result jsonb;
        BEGIN
          IF actor_name<>owner_name AND actor_name<>'{OUTBOX_ROLE}' THEN
            RAISE EXCEPTION 'DTS_V2_OUTBOX_HEALTH_CALLER_FORBIDDEN'
              USING ERRCODE='42501'; END IF;
          IF p_stale_after_seconds<1 OR p_stale_after_seconds>86400 THEN
            RAISE EXCEPTION 'DTS_V2_RUNTIME_HEALTH_THRESHOLD_INVALID'
              USING ERRCODE='22023'; END IF;
          PERFORM pg_advisory_xact_lock_shared(hashtextextended('tit:dts-v2-cutover',0));
          SELECT count(*) INTO control_count FROM public.dts_pipeline_control;
          SELECT mode,projection_generation INTO control_mode,generation
          FROM public.dts_pipeline_control WHERE control_id='PRIMARY';
          IF control_count<>1 OR NOT FOUND OR control_mode NOT IN (
               'V1_COMPAT_DUAL_CAPTURE','V2_PRIMARY','ROLLED_BACK'
             ) OR generation<0 THEN
            RAISE EXCEPTION 'DTS_V2_RUNTIME_HEALTH_CONTROL_INVALID'
              USING ERRCODE='55000'; END IF;
          IF to_regprocedure('{COURSE_COMMAND}') IS NULL
             OR NOT has_function_privilege(actor_name,'{COURSE_COMMAND}','EXECUTE')
             OR NOT has_function_privilege(actor_name,
                  'public.materialize_teacher_source_wide_v2(jsonb,bigint,bigint,bigint,text)','EXECUTE')
             OR NOT has_function_privilege(actor_name,
                  'public.materialize_task_plan_v2(text,text,bigint,text)','EXECUTE') THEN
            RAISE EXCEPTION 'DTS_V2_OUTBOX_HEALTH_CAPABILITY_INVALID'
              USING ERRCODE='42501'; END IF;
          SELECT jsonb_build_object(
            'protocol_version','dts-v2-outbox-runtime-health-v1',
            'mode',control_mode,'projection_generation',generation,
            'runnable_count',count(*) FILTER (WHERE status='PENDING' AND available_at<=now_at),
            'active_lease_count',0,'expired_lease_count',0,
            'business_wait_count',count(*) FILTER (WHERE status='PENDING' AND available_at>now_at),
            'dead_count',count(*) FILTER (WHERE status='DEAD_LETTER'),
            'stale_runnable_count',count(*) FILTER (WHERE status='PENDING' AND available_at<=now_at-make_interval(secs=>p_stale_after_seconds::double precision)),
            'oldest_runnable_age_seconds',floor(extract(epoch FROM now_at-min(available_at) FILTER (WHERE status='PENDING' AND available_at<=now_at)))::bigint,
            'oldest_active_lease_age_seconds',NULL
          ) INTO result FROM public.outbox_events
          WHERE event_type IN (
            'source_wide.changed.v2','task.materialization.requested.v2'
          );
          RETURN result;
        END $function$;

        CREATE FUNCTION public.dts_v2_favorite_runtime_health_v1(
          p_stale_after_seconds bigint
        ) RETURNS jsonb
        LANGUAGE plpgsql STABLE SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE actor_name text:=session_user; owner_name text:=current_user;
        DECLARE control_count integer; DECLARE control_mode text;
        DECLARE generation bigint; DECLARE now_at timestamptz:=transaction_timestamp();
        DECLARE result jsonb;
        BEGIN
          IF actor_name<>owner_name AND actor_name<>'{OUTBOX_ROLE}' THEN
            RAISE EXCEPTION 'DTS_V2_FAVORITE_HEALTH_CALLER_FORBIDDEN'
              USING ERRCODE='42501'; END IF;
          IF p_stale_after_seconds<1 OR p_stale_after_seconds>86400 THEN
            RAISE EXCEPTION 'DTS_V2_RUNTIME_HEALTH_THRESHOLD_INVALID'
              USING ERRCODE='22023'; END IF;
          PERFORM pg_advisory_xact_lock_shared(hashtextextended('tit:dts-v2-cutover',0));
          SELECT count(*) INTO control_count FROM public.dts_pipeline_control;
          SELECT mode,projection_generation INTO control_mode,generation
          FROM public.dts_pipeline_control WHERE control_id='PRIMARY';
          IF control_count<>1 OR NOT FOUND OR control_mode NOT IN (
               'V1_COMPAT_DUAL_CAPTURE','V2_PRIMARY','ROLLED_BACK'
             ) OR generation<0 THEN
            RAISE EXCEPTION 'DTS_V2_RUNTIME_HEALTH_CONTROL_INVALID'
              USING ERRCODE='55000'; END IF;
          IF NOT has_function_privilege(actor_name,
               'public.claim_favorite_observations_v2(text,integer)','EXECUTE')
             OR NOT has_function_privilege(actor_name,
               'public.complete_favorite_observation_v2(text,text,bigint,text,text,bigint,bigint,text,text,boolean,text,text,text)','EXECUTE')
             OR NOT has_function_privilege(actor_name,
               'public.reap_expired_favorite_observations_v2(integer)','EXECUTE') THEN
            RAISE EXCEPTION 'DTS_V2_FAVORITE_HEALTH_CAPABILITY_INVALID'
              USING ERRCODE='42501'; END IF;
          SELECT jsonb_build_object(
            'protocol_version','dts-v2-favorite-runtime-health-v1',
            'mode',control_mode,'projection_generation',generation,
            'runnable_count',count(*) FILTER (WHERE status IN ('PENDING','RETRY') AND next_attempt_at<=now_at),
            'active_lease_count',count(*) FILTER (WHERE status='EVALUATING' AND lease_expires_at>now_at),
            'expired_lease_count',count(*) FILTER (WHERE status='EVALUATING' AND lease_expires_at<=now_at),
            'business_wait_count',count(*) FILTER (WHERE status IN ('WAITING_HISTORY','WAITING_EVIDENCE')),
            'dead_count',count(*) FILTER (WHERE status='DEAD'),
            'stale_runnable_count',count(*) FILTER (WHERE status IN ('PENDING','RETRY') AND next_attempt_at<=now_at-make_interval(secs=>p_stale_after_seconds::double precision)),
            'oldest_runnable_age_seconds',floor(extract(epoch FROM now_at-min(next_attempt_at) FILTER (WHERE status IN ('PENDING','RETRY') AND next_attempt_at<=now_at)))::bigint,
            'oldest_active_lease_age_seconds',floor(extract(epoch FROM now_at-min(lease_acquired_at) FILTER (WHERE status='EVALUATING' AND lease_expires_at>now_at)))::bigint
          ) INTO result FROM public.course_favorite_observations;
          RETURN result;
        END $function$;
        """
    )


def _apply_acl_and_assert() -> None:
    op.execute(
        rf"""
        REVOKE ALL ON FUNCTION {COURSE_COMMAND},{DOMAIN_HEALTH},
          {OUTBOX_HEALTH},{FAVORITE_HEALTH}
        FROM PUBLIC,tit_growth_app,tit_dts_ingest_runtime;
        REVOKE ALL ON FUNCTION {COURSE_COMMAND},{OUTBOX_HEALTH},
          {FAVORITE_HEALTH} FROM {DOMAIN_ROLE};
        REVOKE ALL ON FUNCTION {DOMAIN_HEALTH} FROM {OUTBOX_ROLE};
        GRANT EXECUTE ON FUNCTION {COURSE_COMMAND},{OUTBOX_HEALTH},
          {FAVORITE_HEALTH} TO {OUTBOX_ROLE};
        GRANT EXECUTE ON FUNCTION {DOMAIN_HEALTH} TO {DOMAIN_ROLE};

        REVOKE INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER ON TABLE
          public.lesson_source_wide,
          public.lesson_score_component_settlements,
          public.score_entries,
          public.dts_pipeline_control
        FROM {OUTBOX_ROLE},{DOMAIN_ROLE},tit_growth_app,
             tit_dts_ingest_runtime;
        REVOKE SELECT ON TABLE public.dts_pipeline_control
        FROM {OUTBOX_ROLE},{DOMAIN_ROLE};
        REVOKE CREATE ON SCHEMA public FROM {OUTBOX_ROLE},{DOMAIN_ROLE};

        COMMENT ON FUNCTION {COURSE_COMMAND} IS
          'PRIMARY-only COURSE compatibility and four-component settlement command; derives writes from locked typed facts and proves request hash/generation.';
        COMMENT ON FUNCTION {DOMAIN_HEALTH} IS
          'Non-sensitive DOMAIN queue health under the cutover shared lock.';
        COMMENT ON FUNCTION {OUTBOX_HEALTH} IS
          'Non-sensitive Outbox queue/capability health under the cutover shared lock.';
        COMMENT ON FUNCTION {FAVORITE_HEALTH} IS
          'Non-sensitive favorite observation lease/queue health under the cutover shared lock.';

        DO $runtime_course_health_shape$
        BEGIN
          IF NOT has_function_privilege('{OUTBOX_ROLE}','{COURSE_COMMAND}','EXECUTE')
             OR has_function_privilege('{DOMAIN_ROLE}','{COURSE_COMMAND}','EXECUTE')
             OR has_function_privilege('tit_growth_app','{COURSE_COMMAND}','EXECUTE')
             OR has_table_privilege('{OUTBOX_ROLE}','public.lesson_source_wide','INSERT')
             OR has_table_privilege('{OUTBOX_ROLE}','public.lesson_source_wide','UPDATE')
             OR has_table_privilege('{OUTBOX_ROLE}','public.lesson_source_wide','DELETE')
             OR has_table_privilege('{OUTBOX_ROLE}','public.lesson_score_component_settlements','INSERT')
             OR has_table_privilege('{OUTBOX_ROLE}','public.score_entries','INSERT')
             OR has_table_privilege('{OUTBOX_ROLE}','public.dts_pipeline_control','SELECT')
             OR NOT has_function_privilege('{DOMAIN_ROLE}','{DOMAIN_HEALTH}','EXECUTE')
             OR has_function_privilege('{OUTBOX_ROLE}','{DOMAIN_HEALTH}','EXECUTE')
             OR NOT has_function_privilege('{OUTBOX_ROLE}','{OUTBOX_HEALTH}','EXECUTE')
             OR NOT has_function_privilege('{OUTBOX_ROLE}','{FAVORITE_HEALTH}','EXECUTE') THEN
            RAISE EXCEPTION 'DTS_V2_RUNTIME_COURSE_HEALTH_ACL_INVALID';
          END IF;
        END $runtime_course_health_shape$;
        """
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("DTS v2 COURSE/health runtime requires PostgreSQL")
    _preflight()
    _install_score_entry_helper()
    _install_component_command()
    _install_course_command()
    _install_health_functions()
    _apply_acl_and_assert()


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("DTS v2 COURSE/health runtime requires PostgreSQL")
    op.execute(
        rf"""
        REVOKE ALL ON FUNCTION {COURSE_COMMAND},{DOMAIN_HEALTH},
          {OUTBOX_HEALTH},{FAVORITE_HEALTH}
        FROM PUBLIC,{DOMAIN_ROLE},{OUTBOX_ROLE},tit_growth_app,
             tit_dts_ingest_runtime;
        DROP FUNCTION public.dts_v2_favorite_runtime_health_v1(bigint);
        DROP FUNCTION public.dts_v2_outbox_runtime_health_v1(bigint);
        DROP FUNCTION public.dts_v2_domain_runtime_health_v1(bigint);
        DROP FUNCTION public.materialize_course_source_wide_v2(
          jsonb,bigint,bigint,text,text
        );
        DROP FUNCTION public.settle_course_components_v2(
          text,text,bigint,bigint,text
        );
        DROP FUNCTION public.write_course_component_score_entry_v2(
          text,text,text,integer,text,text,numeric,bigint,text,text,text,
          bigint,bigint,text,text
        );
        """
    )
