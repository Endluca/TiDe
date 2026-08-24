"""add ledger-authoritative DTS v2 score and read branches.

Revision ID: 20260822_88_v2_score_projection
Revises: 20260822_87_favorite_runtime
Create Date: 2026-08-22

This revision is deliberately additive.  It installs immutable v1/v2 branch
views and protected v2 rebuild commands, but does not replace either stable
``*_current`` view and does not switch the projection route.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260822_88_v2_score_projection"
down_revision: Union[str, None] = "20260822_87_favorite_runtime"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


OUTBOX_RUNTIME_ROLE = "tit_growth_app"
BRANCH_VIEWS = (
    "teacher_scorecard_v1_compat_v1",
    "teacher_scorecard_v2_v1",
    "teacher_lesson_score_v1_compat_v1",
    "teacher_lesson_score_v2_v1",
)


def _assert_preconditions() -> None:
    op.execute(
        r"""
        DO $score_projection_preflight$
        DECLARE required_relation text;
        BEGIN
          FOREACH required_relation IN ARRAY ARRAY[
            'dts_pipeline_control','config_versions','teachers',
            'teacher_qualifications','teacher_source_wide','task_assignments',
            'score_entries','score_accounts','score_component_accounts',
            'lesson_source_wide','lesson_score_results',
            'lesson_score_component_settlements','source_courses',
            'source_course_participations','source_course_fact_current',
            'source_participation_fact_current','source_course_labels',
            'source_course_complaints','complaint_category_rules',
            'course_favorite_observations','course_favorite_attributions',
            'teacher_student_relationship_current'
          ] LOOP
            IF to_regclass('public.' || required_relation) IS NULL THEN
              RAISE EXCEPTION
                'DTS_V2_SCORE_PROJECTION_PREREQUISITE_MISSING:%',
                required_relation;
            END IF;
          END LOOP;
          IF to_regprocedure(
               'public.dts_canonical_json_sha256_v1(jsonb)'
             ) IS NULL
             OR to_regprocedure(
               'public.dts_v2_assert_lesson_score_course(text,text)'
             ) IS NULL
             OR to_regrole('tit_growth_app') IS NULL THEN
            RAISE EXCEPTION
              'DTS_V2_SCORE_PROJECTION_PREREQUISITE_MISSING';
          END IF;
          IF EXISTS (
            SELECT 1 FROM pg_catalog.pg_proc
            WHERE pronamespace='public'::regnamespace
              AND proname IN (
                'score_projection_config_snapshot_v2',
                'teacher_score_projection_vector_v2',
                'rebuild_lesson_score_result_v2',
                'rebuild_teacher_score_and_qualification_v2',
                'dts_v2_assert_lesson_score_course_rev78'
              )
          ) OR EXISTS (
            SELECT 1 FROM pg_catalog.pg_class
            WHERE relnamespace='public'::regnamespace
              AND relname=ANY(ARRAY[
                'teacher_scorecard_v1_compat_v1',
                'teacher_scorecard_v2_v1',
                'teacher_lesson_score_v1_compat_v1',
                'teacher_lesson_score_v2_v1'
              ])
          ) THEN
            RAISE EXCEPTION 'DTS_V2_SCORE_PROJECTION_ALREADY_INSTALLED';
          END IF;
          IF EXISTS (
            SELECT 1
            FROM public.lesson_score_results
            WHERE v2_source_region IS NOT NULL
            LIMIT 1
          ) THEN
            RAISE EXCEPTION
              'DTS_V2_SCORE_RESULT_GENERATION_BACKFILL_UNPROVEN';
          END IF;
        END
        $score_projection_preflight$;

        LOCK TABLE public.source_course_participations,
          public.lesson_score_results,public.score_accounts,
          public.score_component_accounts,public.teacher_qualifications
        IN SHARE ROW EXCLUSIVE MODE;
        """
    )


def _extend_score_ownership() -> None:
    op.create_unique_constraint(
        "uq_source_course_participation_score_owner_v2",
        "source_course_participations",
        [
            "source_region",
            "source_appoint_id",
            "participation_seq",
            "teacher_id",
        ],
        schema="public",
    )
    op.add_column(
        "lesson_score_results",
        sa.Column("v2_teacher_id", sa.String(length=64), nullable=True),
        schema="public",
    )
    op.add_column(
        "lesson_score_results",
        sa.Column("v2_projection_generation", sa.BigInteger(), nullable=True),
        schema="public",
    )
    op.drop_constraint(
        "fk_lesson_score_result_v2_participation",
        "lesson_score_results",
        type_="foreignkey",
        schema="public",
    )
    op.drop_constraint(
        "ck_lesson_score_result_v2_ownership",
        "lesson_score_results",
        type_="check",
        schema="public",
    )
    op.create_check_constraint(
        "ck_lesson_score_result_v2_ownership",
        "lesson_score_results",
        "(v2_source_region IS NULL "
        "AND v2_source_appoint_id IS NULL "
        "AND v2_completion_participation_seq IS NULL "
        "AND v2_teacher_id IS NULL "
        "AND v2_projection_generation IS NULL) OR "
        "(v2_source_region IN ('dom','ovs') "
        "AND v2_source_appoint_id IS NOT NULL "
        "AND v2_completion_participation_seq >= 1 "
        "AND v2_teacher_id IS NOT NULL "
        "AND btrim(v2_teacher_id) <> '' "
        "AND v2_projection_generation >= 1)",
        schema="public",
    )
    op.create_foreign_key(
        "fk_lesson_score_result_v2_score_owner",
        "lesson_score_results",
        "source_course_participations",
        [
            "v2_source_region",
            "v2_source_appoint_id",
            "v2_completion_participation_seq",
            "v2_teacher_id",
        ],
        [
            "source_region",
            "source_appoint_id",
            "participation_seq",
            "teacher_id",
        ],
        source_schema="public",
        referent_schema="public",
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )
    op.execute(
        """
        COMMENT ON COLUMN public.lesson_score_results.v2_teacher_id IS
          'Frozen completion teacher for the v2 lesson score result';
        COMMENT ON COLUMN
          public.lesson_score_results.v2_projection_generation IS
          'Serving generation owned by the v2 score projection';
        """
    )


def _install_score_ownership_guards() -> None:
    op.execute(
        r"""
        ALTER FUNCTION public.dts_v2_assert_lesson_score_course(text,text)
          RENAME TO dts_v2_assert_lesson_score_course_rev78;

        CREATE FUNCTION public.dts_v2_assert_lesson_score_course(
          guard_region text,guard_appoint_id text
        )
        RETURNS void
        LANGUAGE plpgsql
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE course_row public.source_courses%ROWTYPE;
        DECLARE result_row public.lesson_score_results%ROWTYPE;
        DECLARE current_generation bigint;
        BEGIN
          PERFORM public.dts_v2_assert_lesson_score_course_rev78(
            guard_region,guard_appoint_id
          );
          SELECT * INTO course_row
          FROM public.source_courses
          WHERE source_region=guard_region
            AND source_appoint_id=guard_appoint_id;
          SELECT projection_generation INTO current_generation
          FROM public.dts_pipeline_control WHERE control_id='PRIMARY';

          FOR result_row IN
            SELECT * FROM public.lesson_score_results
            WHERE v2_source_region=guard_region
              AND v2_source_appoint_id=guard_appoint_id
          LOOP
            IF course_row.source_appoint_id IS NULL
               OR course_row.completion_voided_at IS NOT NULL
               OR ROW(
                    result_row.v2_completion_participation_seq,
                    result_row.v2_teacher_id
                  ) IS DISTINCT FROM ROW(
                    course_row.completion_participation_seq,
                    course_row.completion_teacher_id
                  )
               OR result_row.v2_projection_generation<1
               OR current_generation IS NULL
               OR result_row.v2_projection_generation>current_generation
               OR NOT EXISTS (
                 SELECT 1
                 FROM public.source_course_participations part
                 WHERE part.source_region=guard_region
                   AND part.source_appoint_id=guard_appoint_id
                   AND part.participation_seq=
                     result_row.v2_completion_participation_seq
                   AND part.teacher_id=result_row.v2_teacher_id
                   AND part.participation_role='COMPLETION'
                   AND part.source_deleted IS FALSE
               ) THEN
              RAISE EXCEPTION
                'DTS_V2_LESSON_SCORE_RESULT_OWNER_MISMATCH';
            END IF;
          END LOOP;
        END
        $function$;

        CREATE OR REPLACE FUNCTION
          public.dts_v2_lesson_score_result_ownership_guard()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE actor_name text := COALESCE(
          NULLIF(current_setting('role',true),'none'),session_user
        );
        DECLARE old_is_v2 boolean;
        DECLARE new_is_v2 boolean := NEW.v2_source_region IS NOT NULL;
        BEGIN
          IF actor_name IN (
               'tit_growth_app','tit_dts_ingest_runtime','tit_teacher_crud'
             ) AND new_is_v2 THEN
            RAISE EXCEPTION
              'DTS_V2_LESSON_SCORE_RESULT_RUNTIME_ROUTE_INACTIVE'
              USING ERRCODE='42501';
          END IF;
          IF TG_OP='INSERT' THEN RETURN NEW; END IF;
          old_is_v2 := OLD.v2_source_region IS NOT NULL;
          IF NOT old_is_v2 AND new_is_v2 THEN
            RAISE EXCEPTION
              'DTS_V2_LESSON_SCORE_RESULT_LEGACY_OWNERSHIP_IMMUTABLE';
          END IF;
          IF old_is_v2 AND NOT new_is_v2 THEN
            RAISE EXCEPTION 'DTS_V2_LESSON_SCORE_RESULT_OWNERSHIP_REQUIRED';
          END IF;
          IF old_is_v2 AND ROW(
               NEW.v2_source_region,NEW.v2_source_appoint_id
             ) IS DISTINCT FROM ROW(
               OLD.v2_source_region,OLD.v2_source_appoint_id
             ) THEN
            RAISE EXCEPTION
              'DTS_V2_LESSON_SCORE_RESULT_COURSE_IMMUTABLE';
          END IF;
          IF old_is_v2 AND (
               ROW(
                 NEW.v2_completion_participation_seq,NEW.v2_teacher_id,
                 NEW.v2_projection_generation
               ) IS DISTINCT FROM ROW(
                 OLD.v2_completion_participation_seq,OLD.v2_teacher_id,
                 OLD.v2_projection_generation
               )
             ) AND NEW.projection_revision<>OLD.projection_revision+1 THEN
            RAISE EXCEPTION
              'DTS_V2_LESSON_SCORE_RESULT_TRANSFER_REVISION_INVALID';
          END IF;
          IF old_is_v2
             AND NEW.v2_projection_generation<OLD.v2_projection_generation THEN
            RAISE EXCEPTION
              'DTS_V2_LESSON_SCORE_RESULT_GENERATION_REGRESSION';
          END IF;
          RETURN NEW;
        END
        $function$;

        REVOKE ALL ON FUNCTION
          public.dts_v2_assert_lesson_score_course(text,text),
          public.dts_v2_assert_lesson_score_course_rev78(text,text),
          public.dts_v2_lesson_score_result_ownership_guard()
        FROM PUBLIC;
        """
    )


def _install_projection_helpers() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.score_projection_config_snapshot_v2()
        RETURNS jsonb
        LANGUAGE plpgsql
        STABLE
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE config_count integer;
        DECLARE config_row public.config_versions%ROWTYPE;
        DECLARE snapshot jsonb;
        BEGIN
          SELECT count(*) INTO config_count
          FROM public.config_versions
          WHERE config_key='SCORE_GRADUATION' AND status='PUBLISHED';
          IF config_count IS DISTINCT FROM 1 THEN
            RAISE EXCEPTION 'DTS_V2_SCORE_CONFIG_NOT_UNIQUE'
              USING ERRCODE='55000';
          END IF;
          SELECT * INTO STRICT config_row
          FROM public.config_versions
          WHERE config_key='SCORE_GRADUATION' AND status='PUBLISHED';
          IF jsonb_typeof(config_row.payload)<>'object'
             OR config_row.payload->>'policy_version' IS NULL
             OR btrim(config_row.payload->>'policy_version')=''
             OR (config_row.payload#>>
                  '{thresholds,graduation_raw_score}')::numeric
                    IS DISTINCT FROM 100
             OR (config_row.payload#>>
                  '{thresholds,gold_raw_score}')::numeric
                    IS DISTINCT FROM 200
             OR (config_row.payload#>>
                  '{hard_gates,graduation,required_mandatory_task_count}'
                )::integer IS DISTINCT FROM 9
             OR (config_row.payload#>>
                  '{hard_gates,graduation,maximum_l0_complaint_count}'
                )::integer IS DISTINCT FROM 0
             OR (config_row.payload#>>
                  '{hard_gates,gold,maximum_late_count}')::integer
                    IS DISTINCT FROM 1
             OR (config_row.payload#>>
                  '{hard_gates,gold,maximum_early_count}')::integer
                    IS DISTINCT FROM 0
             OR (config_row.payload#>>
                  '{hard_gates,gold,maximum_absent_count}')::integer
                    IS DISTINCT FROM 0
             OR (config_row.payload#>>
                  '{scoring_items,capacity,milestone_id}')
                    IS DISTINCT FROM 'CAPACITY_PEAK_SLOT_40'
             OR (config_row.payload#>>
                  '{scoring_items,capacity,threshold}')::numeric
                    IS DISTINCT FROM 40
             OR (config_row.payload#>>
                  '{scoring_items,capacity,score_value}')::numeric
                    IS DISTINCT FROM 10
             OR (config_row.payload#>>
                  '{scoring_items,feedback_praise,points_per_unit}'
                )::numeric IS DISTINCT FROM 5
             OR (config_row.payload#>>
                  '{scoring_items,feedback_favorite,points_per_unit}'
                )::numeric IS DISTINCT FROM 5
             OR (config_row.payload#>>
                  '{scoring_items,reliability_perfect,points_per_unit}'
                )::numeric IS DISTINCT FROM 4
             OR (config_row.payload#>>
                  '{scoring_items,reliability_peak,points_per_unit}'
                )::numeric IS DISTINCT FROM 2
             OR (config_row.payload#>>
                  '{scoring_items,classroom_quality,points_per_unit}'
                )::numeric IS DISTINCT FROM 2 THEN
            RAISE EXCEPTION 'DTS_V2_SCORE_CONFIG_CONTRACT_INVALID'
              USING ERRCODE='23514';
          END IF;
          snapshot := jsonb_build_object(
            'config_key','SCORE_GRADUATION',
            'version_id',config_row.version_id,
            'version_number',config_row.version_number,
            'policy_version',config_row.payload->>'policy_version',
            'payload_sha256',
              public.dts_canonical_json_sha256_v1(config_row.payload),
            'graduation_threshold',100,
            'gold_threshold',200,
            'required_mandatory_task_count',9,
            'maximum_l0_complaint_count',0,
            'maximum_late_count',1,
            'maximum_early_count',0,
            'maximum_absent_count',0,
            'points',jsonb_build_object(
              'FEEDBACK_PRAISE',5,'FEEDBACK_FAVORITE',5,
              'PERFECT_COMPLETED',4,'PEAK_COMPLETED',2,
              'CLASS_QUALITY_HARDWARE',2,
              'CAPACITY_PEAK_SLOT_40',10
            )
          );
          RETURN snapshot;
        EXCEPTION
          WHEN invalid_text_representation OR numeric_value_out_of_range THEN
            RAISE EXCEPTION 'DTS_V2_SCORE_CONFIG_CONTRACT_INVALID'
              USING ERRCODE='23514';
        END
        $function$;

        CREATE FUNCTION public.teacher_score_projection_vector_v2(
          p_teacher_id text
        )
        RETURNS jsonb
        LANGUAGE plpgsql
        STABLE
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE control_count integer;
        DECLARE control_mode text;
        DECLARE control_generation bigint;
        DECLARE control_version bigint;
        DECLARE qualification_grants_enabled boolean;
        DECLARE config_snapshot jsonb;
        DECLARE vector jsonb;
        BEGIN
          IF p_teacher_id IS NULL OR btrim(p_teacher_id)=''
             OR p_teacher_id<>btrim(p_teacher_id) THEN
            RAISE EXCEPTION 'DTS_V2_SCORE_TEACHER_ID_INVALID'
              USING ERRCODE='22023';
          END IF;
          SELECT count(*),min(mode),min(projection_generation),min(row_version),
                 bool_and(control.qualification_grants_enabled)
          INTO control_count,control_mode,control_generation,control_version,
               qualification_grants_enabled
          FROM public.dts_pipeline_control AS control
          WHERE control.control_id='PRIMARY';
          IF control_count IS DISTINCT FROM 1
             OR control_mode<>'V2_PRIMARY' OR control_generation<1 THEN
            RAISE EXCEPTION 'DTS_V2_SCORE_PRIMARY_MODE_REQUIRED'
              USING ERRCODE='55000';
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM public.teachers WHERE teacher_id=p_teacher_id
          ) THEN
            RAISE EXCEPTION 'DTS_V2_SCORE_TEACHER_NOT_FOUND'
              USING ERRCODE='23503';
          END IF;
          config_snapshot := public.score_projection_config_snapshot_v2();
          SELECT jsonb_build_object(
            'vector_version',1,
            'teacher_id',p_teacher_id,
            'control',jsonb_build_object(
              'mode',control_mode,'projection_generation',control_generation,
              'row_version',control_version,
              'qualification_grants_enabled',qualification_grants_enabled
            ),
            'config',config_snapshot,
            'teacher_sha256',public.dts_canonical_json_sha256_v1(
              (SELECT jsonb_build_object(
                 'teacher_id',t.teacher_id,
                 'camp_enrollment_id',t.camp_enrollment_id,
                 'online_status',t.online_status
               ) FROM public.teachers t WHERE t.teacher_id=p_teacher_id)
            ),
            'teacher_source_sha256',public.dts_canonical_json_sha256_v1(
              coalesce(
                (SELECT to_jsonb(w) FROM public.teacher_source_wide w
                 WHERE w.tchr_id=p_teacher_id),
                '{}'::jsonb
              )
            ),
            'score_entries',jsonb_build_object(
              'count',(SELECT count(*) FROM public.score_entries e
                       WHERE e.teacher_id=p_teacher_id),
              'sha256',public.dts_canonical_json_sha256_v1(coalesce(
                (SELECT jsonb_agg(to_jsonb(e) ORDER BY e.score_entry_id)
                 FROM public.score_entries e
                 WHERE e.teacher_id=p_teacher_id),'[]'::jsonb))
            ),
            'mandatory_assignments',jsonb_build_object(
              'count',(SELECT count(*) FROM public.task_assignments a
                       WHERE a.teacher_id=p_teacher_id
                         AND a.task_kind='FIXED_GROWTH'
                         AND a.task_code BETWEEN 'G01' AND 'G09'),
              'sha256',public.dts_canonical_json_sha256_v1(coalesce(
                (SELECT jsonb_agg(
                   jsonb_build_object(
                     'assignment_id',a.assignment_id,'task_code',a.task_code,
                     'status',a.status,'completed_at',a.completed_at,
                     'row_version',a.row_version
                   ) ORDER BY a.task_code,a.assignment_id)
                 FROM public.task_assignments a
                 WHERE a.teacher_id=p_teacher_id
                   AND a.task_kind='FIXED_GROWTH'
                   AND a.task_code BETWEEN 'G01' AND 'G09'),'[]'::jsonb))
            ),
            'lesson_settlements_sha256',
              public.dts_canonical_json_sha256_v1(coalesce(
                (SELECT jsonb_agg(to_jsonb(s) ORDER BY
                   s.source_region,s.source_appoint_id,
                   s.completion_participation_seq,s.component_code)
                 FROM public.lesson_score_component_settlements s
                 WHERE s.teacher_id=p_teacher_id),'[]'::jsonb)),
            'favorite_attributions_sha256',
              public.dts_canonical_json_sha256_v1(coalesce(
                (SELECT jsonb_agg(to_jsonb(f) ORDER BY
                   f.source_region,f.teacher_id,f.student_token)
                 FROM public.course_favorite_attributions f
                 WHERE f.teacher_id=p_teacher_id),'[]'::jsonb)),
            'complaint_evidence_sha256',
              public.dts_canonical_json_sha256_v1(coalesce(
                (SELECT jsonb_agg(to_jsonb(c) ORDER BY
                   c.source_region,c.source_complaint_id)
                 FROM public.source_course_complaints c
                 JOIN public.source_courses course
                   ON course.source_region=c.source_region
                  AND course.source_appoint_id=c.source_appoint_id
                 WHERE course.completion_teacher_id=p_teacher_id),
                '[]'::jsonb))
          ) INTO vector;
          RETURN vector;
        END
        $function$;

        REVOKE ALL ON FUNCTION
          public.score_projection_config_snapshot_v2(),
          public.teacher_score_projection_vector_v2(text)
        FROM PUBLIC;
        """
    )


def _install_lesson_rebuild() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.rebuild_lesson_score_result_v2(
          p_source_region text,p_source_appoint_id text,
          p_projection_generation bigint
        )
        RETURNS jsonb
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE control_row public.dts_pipeline_control%ROWTYPE;
        DECLARE course_row public.source_courses%ROWTYPE;
        DECLARE part_row public.source_course_participations%ROWTYPE;
        DECLARE config_snapshot jsonb;
        DECLARE policy_version text;
        DECLARE praise_score numeric := 0;
        DECLARE favorite_score numeric := 0;
        DECLARE perfect_score numeric := 0;
        DECLARE peak_score numeric := 0;
        DECLARE hardware_score numeric := 0;
        DECLARE praise_evidence text := 'SOURCE_MISSING';
        DECLARE favorite_evidence text := 'SOURCE_MISSING';
        DECLARE perfect_evidence text := 'SOURCE_MISSING';
        DECLARE peak_evidence text := 'SOURCE_MISSING';
        DECLARE hardware_evidence text := 'NOT_APPLICABLE';
        DECLARE dimensions_document jsonb;
        DECLARE change_count integer := 0;
        DECLARE active_count integer;
        DECLARE legacy_conflict boolean;
        BEGIN
          IF p_source_region NOT IN ('dom','ovs')
             OR p_source_appoint_id IS NULL
             OR btrim(p_source_appoint_id)=''
             OR p_source_appoint_id<>btrim(p_source_appoint_id)
             OR p_projection_generation<1 THEN
            RAISE EXCEPTION 'DTS_V2_SCORE_COURSE_IDENTITY_INVALID'
              USING ERRCODE='22023';
          END IF;
          PERFORM pg_advisory_xact_lock(544954,1);
          SELECT * INTO control_row
          FROM public.dts_pipeline_control
          WHERE control_id='PRIMARY' FOR SHARE;
          IF NOT FOUND OR control_row.mode<>'V2_PRIMARY'
             OR control_row.projection_generation IS DISTINCT FROM
                  p_projection_generation THEN
            RAISE EXCEPTION 'DTS_V2_SCORE_PRIMARY_GENERATION_MISMATCH'
              USING ERRCODE='55000';
          END IF;
          config_snapshot := public.score_projection_config_snapshot_v2();
          policy_version := config_snapshot->>'policy_version';

          SELECT * INTO course_row FROM public.source_courses
          WHERE source_region=p_source_region
            AND source_appoint_id=p_source_appoint_id
          FOR UPDATE;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'DTS_V2_SCORE_COURSE_NOT_FOUND'
              USING ERRCODE='23503';
          END IF;
          IF course_row.completion_conflict_status='PENDING' THEN
            RAISE EXCEPTION 'DTS_V2_SCORE_COMPLETION_PENDING'
              USING ERRCODE='55000';
          END IF;

          IF course_row.completion_participation_seq IS NULL
             OR course_row.completion_teacher_id IS NULL
             OR course_row.completion_voided_at IS NOT NULL THEN
            DELETE FROM public.lesson_score_results
            WHERE v2_source_region=p_source_region
              AND v2_source_appoint_id=p_source_appoint_id;
            GET DIAGNOSTICS change_count=ROW_COUNT;
            RETURN jsonb_build_object(
              'lesson_result_changes',change_count
            );
          END IF;

          SELECT * INTO part_row
          FROM public.source_course_participations
          WHERE source_region=p_source_region
            AND source_appoint_id=p_source_appoint_id
            AND participation_seq=course_row.completion_participation_seq
            AND teacher_id=course_row.completion_teacher_id
          FOR SHARE;
          IF NOT FOUND OR part_row.participation_role<>'COMPLETION'
             OR part_row.source_deleted THEN
            RAISE EXCEPTION 'DTS_V2_SCORE_COMPLETION_OWNER_INVALID'
              USING ERRCODE='23514';
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM public.lesson_source_wide source
            WHERE source.source_region=p_source_region
              AND source."课程id"=p_source_appoint_id
          ) THEN
            RAISE EXCEPTION 'DTS_V2_SCORE_COMPAT_LESSON_MISSING'
              USING ERRCODE='23503';
          END IF;
          SELECT EXISTS (
            SELECT 1 FROM public.lesson_score_results result
            WHERE result.lesson_source_region=p_source_region
              AND result.lesson_id=p_source_appoint_id
              AND result.v2_source_region IS NULL
          ) INTO legacy_conflict;
          IF legacy_conflict THEN
            RAISE EXCEPTION 'DTS_V2_SCORE_LEGACY_RESULT_CONFLICT'
              USING ERRCODE='55000';
          END IF;

          PERFORM public.dts_v2_assert_lesson_score_course(
            p_source_region,p_source_appoint_id
          );
          IF EXISTS (
            SELECT 1
            FROM public.lesson_score_component_settlements settlement
            WHERE settlement.source_region=p_source_region
              AND settlement.source_appoint_id=p_source_appoint_id
              AND settlement.status='AWARDED'
              AND settlement.materialization_origin<>'LEGACY_REUSED'
              AND settlement.award_projection_generation IS DISTINCT FROM
                    p_projection_generation
          ) OR EXISTS (
            SELECT 1
            FROM public.course_favorite_attributions favorite
            WHERE favorite.source_region=p_source_region
              AND favorite.source_appoint_id=p_source_appoint_id
              AND favorite.status IN (
                    'AWARDED','AWARDED_PENDING_EVIDENCE'
                  )
              AND favorite.materialization_origin<>'LEGACY_REUSED'
              AND favorite.award_projection_generation IS DISTINCT FROM
                    p_projection_generation
          ) THEN
            RAISE EXCEPTION 'DTS_V2_SCORE_COMPONENT_GENERATION_STALE'
              USING ERRCODE='55000';
          END IF;

          SELECT
            coalesce(sum(entry.delta_score) FILTER (
              WHERE settlement.component_code='FEEDBACK_PRAISE'
            ),0),
            coalesce(sum(entry.delta_score) FILTER (
              WHERE settlement.component_code='PERFECT_COMPLETED'
            ),0),
            coalesce(sum(entry.delta_score) FILTER (
              WHERE settlement.component_code='PEAK_COMPLETED'
            ),0),
            coalesce(sum(entry.delta_score) FILTER (
              WHERE settlement.component_code='CLASS_QUALITY_HARDWARE'
            ),0)
          INTO praise_score,perfect_score,peak_score,hardware_score
          FROM public.lesson_score_component_settlements settlement
          JOIN public.score_entries entry
            ON entry.score_entry_id=settlement.current_award_score_entry_id
          WHERE settlement.source_region=p_source_region
            AND settlement.source_appoint_id=p_source_appoint_id
            AND settlement.completion_participation_seq=
                  course_row.completion_participation_seq
            AND settlement.teacher_id=course_row.completion_teacher_id
            AND settlement.status='AWARDED';

          SELECT coalesce(sum(entry.delta_score),0),count(*)
          INTO favorite_score,active_count
          FROM public.course_favorite_attributions favorite
          JOIN public.score_entries entry
            ON entry.score_entry_id=favorite.current_score_entry_id
          WHERE favorite.source_region=p_source_region
            AND favorite.source_appoint_id=p_source_appoint_id
            AND favorite.completion_participation_seq=
                  course_row.completion_participation_seq
            AND favorite.teacher_id=course_row.completion_teacher_id
            AND favorite.status IN ('AWARDED','AWARDED_PENDING_EVIDENCE');

          IF praise_score<0 OR favorite_score<0 OR perfect_score<0
             OR peak_score<0 OR hardware_score<0 THEN
            RAISE EXCEPTION 'DTS_V2_SCORE_NEGATIVE_ACTIVE_COMPONENT'
              USING ERRCODE='23514';
          END IF;

          SELECT CASE
            WHEN fact.grading_evidence_status='CONFIRMED' THEN 'CONFIRMED'
            ELSE 'SOURCE_MISSING' END,
            CASE
              WHEN participation.late_evidence_status='CONFIRMED'
               AND participation.early_evidence_status='CONFIRMED'
                THEN 'CONFIRMED'
              ELSE 'SOURCE_MISSING' END,
            CASE WHEN course_row.completion_is_peak IS NOT NULL
              THEN 'CONFIRMED' ELSE 'SOURCE_MISSING' END
          INTO praise_evidence,perfect_evidence,peak_evidence
          FROM public.source_course_fact_current fact
          LEFT JOIN public.source_participation_fact_current participation
            ON participation.source_region=fact.source_region
           AND participation.source_appoint_id=fact.source_appoint_id
           AND participation.participation_seq=
                 course_row.completion_participation_seq
          WHERE fact.source_region=p_source_region
            AND fact.source_appoint_id=p_source_appoint_id;
          hardware_evidence := CASE
            WHEN hardware_score>0 THEN 'CONFIRMED'
            ELSE 'NOT_APPLICABLE' END;

          IF active_count>0 THEN
            favorite_evidence := CASE WHEN EXISTS (
              SELECT 1 FROM public.course_favorite_attributions favorite
              WHERE favorite.source_region=p_source_region
                AND favorite.source_appoint_id=p_source_appoint_id
                AND favorite.status='AWARDED_PENDING_EVIDENCE'
            ) THEN 'HISTORY_INCOMPLETE' ELSE 'CONFIRMED' END;
          ELSIF EXISTS (
            SELECT 1 FROM public.course_favorite_observations observation
            WHERE observation.source_region=p_source_region
              AND observation.source_appoint_id=p_source_appoint_id
              AND observation.completion_participation_seq=
                    course_row.completion_participation_seq
              AND observation.teacher_id=course_row.completion_teacher_id
              AND observation.status='CONFIRMED_FALSE'
          ) THEN
            favorite_evidence := 'CONFIRMED';
          END IF;

          IF praise_evidence='CONFIRMED' AND EXISTS (
            SELECT 1 FROM public.source_course_fact_current fact
            WHERE fact.source_region=p_source_region
              AND fact.source_appoint_id=p_source_appoint_id
              AND fact.grading_classification='POSITIVE'
          ) AND praise_score=0 THEN
            RAISE EXCEPTION 'DTS_V2_SCORE_PRAISE_LEDGER_INCOMPLETE'
              USING ERRCODE='55000';
          END IF;
          IF perfect_evidence='CONFIRMED' AND EXISTS (
            SELECT 1 FROM public.source_participation_fact_current fact
            WHERE fact.source_region=p_source_region
              AND fact.source_appoint_id=p_source_appoint_id
              AND fact.participation_seq=
                    course_row.completion_participation_seq
              AND fact.is_late IS FALSE AND fact.is_early IS FALSE
          ) AND perfect_score=0 THEN
            RAISE EXCEPTION 'DTS_V2_SCORE_PERFECT_LEDGER_INCOMPLETE'
              USING ERRCODE='55000';
          END IF;
          IF peak_evidence='CONFIRMED'
             AND course_row.completion_is_peak IS TRUE AND peak_score=0 THEN
            RAISE EXCEPTION 'DTS_V2_SCORE_PEAK_LEDGER_INCOMPLETE'
              USING ERRCODE='55000';
          END IF;

          dimensions_document := jsonb_build_object(
            '_projection',jsonb_build_object(
              'projection_generation',p_projection_generation,
              'config_version_id',config_snapshot->>'version_id',
              'config_payload_sha256',config_snapshot->>'payload_sha256'
            ),
            'USER_FEEDBACK',jsonb_build_object(
              'score',praise_score+favorite_score,
              'components',jsonb_build_array(
                jsonb_build_object(
                  'code','FEEDBACK_PRAISE','score',praise_score,
                  'evidence_status',praise_evidence
                ),
                jsonb_build_object(
                  'code','FEEDBACK_FAVORITE','score',favorite_score,
                  'evidence_status',favorite_evidence
                )
              )
            ),
            'RELIABILITY',jsonb_build_object(
              'score',perfect_score+peak_score,
              'components',jsonb_build_array(
                jsonb_build_object(
                  'code','PERFECT_COMPLETED','score',perfect_score,
                  'evidence_status',perfect_evidence
                ),
                jsonb_build_object(
                  'code','PEAK_COMPLETED','score',peak_score,
                  'evidence_status',peak_evidence
                )
              )
            ),
            'CLASS_QUALITY',jsonb_build_object(
              'score',hardware_score,
              'components',jsonb_build_array(jsonb_build_object(
                'code','CLASS_QUALITY_HARDWARE','score',hardware_score,
                'evidence_status',hardware_evidence
              ))
            )
          );

          INSERT INTO public.lesson_score_results(
            lesson_source_region,lesson_id,user_feedback_score,
            reliability_score,class_quality_score,lesson_total_score,
            dimensions,score_rule_version,projection_revision,calculated_at,
            v2_source_region,v2_source_appoint_id,
            v2_completion_participation_seq,v2_teacher_id,
            v2_projection_generation
          ) VALUES (
            p_source_region,p_source_appoint_id,
            (praise_score+favorite_score)::double precision,
            (perfect_score+peak_score)::double precision,
            hardware_score::double precision,
            (praise_score+favorite_score+perfect_score+peak_score+
             hardware_score)::double precision,
            dimensions_document,policy_version,1,transaction_timestamp(),
            p_source_region,p_source_appoint_id,
            course_row.completion_participation_seq,
            course_row.completion_teacher_id,p_projection_generation
          )
          ON CONFLICT (lesson_source_region,lesson_id) DO UPDATE SET
            user_feedback_score=excluded.user_feedback_score,
            reliability_score=excluded.reliability_score,
            class_quality_score=excluded.class_quality_score,
            lesson_total_score=excluded.lesson_total_score,
            dimensions=excluded.dimensions,
            score_rule_version=excluded.score_rule_version,
            projection_revision=
              public.lesson_score_results.projection_revision+1,
            calculated_at=transaction_timestamp(),
            v2_completion_participation_seq=
              excluded.v2_completion_participation_seq,
            v2_teacher_id=excluded.v2_teacher_id,
            v2_projection_generation=excluded.v2_projection_generation
          WHERE ROW(
            public.lesson_score_results.user_feedback_score,
            public.lesson_score_results.reliability_score,
            public.lesson_score_results.class_quality_score,
            public.lesson_score_results.lesson_total_score,
            public.lesson_score_results.dimensions,
            public.lesson_score_results.score_rule_version,
            public.lesson_score_results.v2_completion_participation_seq,
            public.lesson_score_results.v2_teacher_id,
            public.lesson_score_results.v2_projection_generation
          ) IS DISTINCT FROM ROW(
            excluded.user_feedback_score,excluded.reliability_score,
            excluded.class_quality_score,excluded.lesson_total_score,
            excluded.dimensions,excluded.score_rule_version,
            excluded.v2_completion_participation_seq,
            excluded.v2_teacher_id,excluded.v2_projection_generation
          );
          GET DIAGNOSTICS change_count=ROW_COUNT;
          SET CONSTRAINTS
            fk_lesson_score_result_v2_score_owner,
            ct_lesson_score_result_v2_course_guard IMMEDIATE;
          SET CONSTRAINTS
            fk_lesson_score_result_v2_score_owner,
            ct_lesson_score_result_v2_course_guard DEFERRED;
          RETURN jsonb_build_object(
            'lesson_result_changes',change_count
          );
        END
        $function$;

        REVOKE ALL ON FUNCTION
          public.rebuild_lesson_score_result_v2(text,text,bigint)
        FROM PUBLIC;
        """
    )


def _install_teacher_rebuild() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.rebuild_teacher_score_and_qualification_v2(
          p_teacher_id text,p_expected_vector jsonb,
          p_projection_generation bigint
        )
        RETURNS jsonb
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE control_row public.dts_pipeline_control%ROWTYPE;
        DECLARE teacher_row public.teachers%ROWTYPE;
        DECLARE wide_row public.teacher_source_wide%ROWTYPE;
        DECLARE config_snapshot jsonb;
        DECLARE current_vector jsonb;
        DECLARE vector_sha256 text;
        DECLARE policy_version text;
        DECLARE component_changes integer := 0;
        DECLARE account_changes integer := 0;
        DECLARE qualification_changes integer := 0;
        DECLARE teacher_changes integer := 0;
        DECLARE raw_total numeric := 0;
        DECLARE account_total numeric := 0;
        DECLARE mandatory_total integer := 0;
        DECLARE mandatory_completed integer := 0;
        DECLARE valid_complaint_course_count integer := 0;
        DECLARE l0_complaint_count integer := 0;
        DECLARE complaint_evidence_complete boolean := false;
        DECLARE attendance_evidence_complete boolean := false;
        DECLARE graduation_criteria boolean := false;
        DECLARE gold_criteria boolean := false;
        DECLARE qualification_row public.teacher_qualifications%ROWTYPE;
        DECLARE gate_document jsonb;
        BEGIN
          IF p_teacher_id IS NULL OR btrim(p_teacher_id)=''
             OR p_teacher_id<>btrim(p_teacher_id)
             OR jsonb_typeof(p_expected_vector)<>'object'
             OR p_projection_generation<1 THEN
            RAISE EXCEPTION 'DTS_V2_SCORE_REBUILD_REQUEST_INVALID'
              USING ERRCODE='22023';
          END IF;
          PERFORM pg_advisory_xact_lock(544954,1);
          SELECT * INTO control_row FROM public.dts_pipeline_control
          WHERE control_id='PRIMARY' FOR SHARE;
          IF NOT FOUND OR control_row.mode<>'V2_PRIMARY'
             OR control_row.projection_generation IS DISTINCT FROM
                  p_projection_generation THEN
            RAISE EXCEPTION 'DTS_V2_SCORE_PRIMARY_GENERATION_MISMATCH'
              USING ERRCODE='55000';
          END IF;
          config_snapshot := public.score_projection_config_snapshot_v2();
          policy_version := config_snapshot->>'policy_version';
          SELECT * INTO teacher_row FROM public.teachers
          WHERE teacher_id=p_teacher_id FOR UPDATE;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'DTS_V2_SCORE_TEACHER_NOT_FOUND'
              USING ERRCODE='23503';
          END IF;
          SELECT * INTO wide_row FROM public.teacher_source_wide
          WHERE tchr_id=p_teacher_id FOR SHARE;
          PERFORM 1 FROM public.task_assignments
          WHERE teacher_id=p_teacher_id
          ORDER BY assignment_id FOR SHARE;
          PERFORM 1 FROM public.score_entries
          WHERE teacher_id=p_teacher_id
          ORDER BY score_entry_id FOR SHARE;
          PERFORM 1 FROM public.lesson_score_component_settlements
          WHERE teacher_id=p_teacher_id
          ORDER BY source_region,source_appoint_id,
            completion_participation_seq,component_code FOR SHARE;
          PERFORM 1 FROM public.course_favorite_attributions
          WHERE teacher_id=p_teacher_id
          ORDER BY source_region,teacher_id,student_token FOR SHARE;
          PERFORM 1 FROM public.score_accounts
          WHERE teacher_id=p_teacher_id ORDER BY dimension FOR UPDATE;
          PERFORM 1 FROM public.score_component_accounts
          WHERE teacher_id=p_teacher_id ORDER BY component_code FOR UPDATE;
          SELECT * INTO qualification_row
          FROM public.teacher_qualifications
          WHERE teacher_id=p_teacher_id FOR UPDATE;

          current_vector := public.teacher_score_projection_vector_v2(
            p_teacher_id
          );
          IF current_vector IS DISTINCT FROM p_expected_vector
             OR current_vector#>>'{control,projection_generation}'
                  IS DISTINCT FROM p_projection_generation::text THEN
            RAISE EXCEPTION 'DTS_V2_SCORE_PROJECTION_VECTOR_STALE'
              USING ERRCODE='40001';
          END IF;
          vector_sha256 := public.dts_canonical_json_sha256_v1(
            current_vector
          );

          IF EXISTS (
            SELECT 1
            FROM public.score_entries entry
            LEFT JOIN public.task_assignments assignment
              ON assignment.assignment_id=entry.task_assignment_id
            WHERE entry.teacher_id=p_teacher_id
              AND entry.delta_score<>0
              AND CASE
                WHEN entry.reason_code IN (
                  'FEEDBACK_PRAISE','FEEDBACK_FAVORITE',
                  'PERFECT_COMPLETED','PEAK_COMPLETED',
                  'CLASS_QUALITY_HARDWARE',
                  'CAPACITY_PEAK_SLOT_40_ACHIEVED'
                ) THEN entry.reason_code
                WHEN entry.entry_type='FIXED_TASK_AWARD'
                  AND assignment.task_code BETWEEN 'G01' AND 'G09'
                  THEN assignment.task_code
                ELSE NULL END IS NULL
          ) THEN
            RAISE EXCEPTION 'DTS_V2_SCORE_LEDGER_COMPONENT_UNMAPPED'
              USING ERRCODE='55000';
          END IF;

          IF EXISTS (
            WITH ledger AS (
              SELECT CASE
                WHEN entry.reason_code IN (
                  'FEEDBACK_PRAISE','FEEDBACK_FAVORITE',
                  'PERFECT_COMPLETED','PEAK_COMPLETED',
                  'CLASS_QUALITY_HARDWARE'
                ) THEN entry.reason_code
                ELSE NULL END AS component_code,
                sum(entry.delta_score)::numeric AS score
              FROM public.score_entries entry
              WHERE entry.teacher_id=p_teacher_id
              GROUP BY 1
            ), attributed AS (
              SELECT settlement.component_code,
                coalesce(sum(entry.delta_score),0)::numeric AS score
              FROM public.lesson_score_component_settlements settlement
              JOIN public.score_entries entry
                ON entry.score_entry_id=
                   settlement.current_award_score_entry_id
              WHERE settlement.teacher_id=p_teacher_id
                AND settlement.status='AWARDED'
              GROUP BY settlement.component_code
              UNION ALL
              SELECT 'FEEDBACK_FAVORITE',
                coalesce(sum(entry.delta_score),0)::numeric
              FROM public.course_favorite_attributions favorite
              JOIN public.score_entries entry
                ON entry.score_entry_id=favorite.current_score_entry_id
              WHERE favorite.teacher_id=p_teacher_id
                AND favorite.status IN (
                  'AWARDED','AWARDED_PENDING_EVIDENCE'
                )
            )
            SELECT 1
            FROM ledger
            LEFT JOIN (
              SELECT component_code,sum(score) score
              FROM attributed GROUP BY component_code
            ) attributed USING(component_code)
            WHERE ledger.component_code IS NOT NULL
              AND ledger.score IS DISTINCT FROM coalesce(attributed.score,0)
          ) THEN
            RAISE EXCEPTION 'DTS_V2_SCORE_LEDGER_ATTRIBUTION_MISMATCH'
              USING ERRCODE='55000';
          END IF;

          WITH expected(
            component_code,dimension,source_scope,source_metric,points_per_unit
          ) AS (VALUES
            ('FEEDBACK_PRAISE','USER_FEEDBACK','LESSON',
             'feedback_praise_cnt',5::numeric),
            ('FEEDBACK_FAVORITE','USER_FEEDBACK','LESSON',
             'favorite_attribution_cnt',5::numeric),
            ('PERFECT_COMPLETED','RELIABILITY','LESSON',
             'perfect_cnt',4::numeric),
            ('PEAK_COMPLETED','RELIABILITY','LESSON',
             'peak_completed_cnt',2::numeric),
            ('CLASS_QUALITY_HARDWARE','CLASS_QUALITY','LESSON',
             'lesson_hardware_quality_passed',2::numeric),
            ('CAPACITY_PEAK_SLOT_40','CAPACITY','TEACHER',
             'peak_slot_cnt',NULL::numeric),
            ('G01','NEW_TEACHER_TASK','TASK','G01',3::numeric),
            ('G02','NEW_TEACHER_TASK','TASK','G02',2::numeric),
            ('G03','NEW_TEACHER_TASK','TASK','G03',2::numeric),
            ('G04','NEW_TEACHER_TASK','TASK','G04',3::numeric),
            ('G05','NEW_TEACHER_TASK','TASK','G05',3::numeric),
            ('G06','NEW_TEACHER_TASK','TASK','G06',4::numeric),
            ('G07','NEW_TEACHER_TASK','TASK','G07',3::numeric),
            ('G08','NEW_TEACHER_TASK','TASK','G08',5::numeric),
            ('G09','NEW_TEACHER_TASK','TASK','G09',5::numeric)
          ), ledger AS (
            SELECT CASE
              WHEN entry.reason_code IN (
                'FEEDBACK_PRAISE','FEEDBACK_FAVORITE',
                'PERFECT_COMPLETED','PEAK_COMPLETED',
                'CLASS_QUALITY_HARDWARE'
              ) THEN entry.reason_code
              WHEN entry.reason_code='CAPACITY_PEAK_SLOT_40_ACHIEVED'
                THEN 'CAPACITY_PEAK_SLOT_40'
              WHEN entry.entry_type='FIXED_TASK_AWARD'
                AND assignment.task_code BETWEEN 'G01' AND 'G09'
                THEN assignment.task_code
              ELSE NULL END AS component_code,
              sum(entry.delta_score)::numeric AS score
            FROM public.score_entries entry
            LEFT JOIN public.task_assignments assignment
              ON assignment.assignment_id=entry.task_assignment_id
            WHERE entry.teacher_id=p_teacher_id
            GROUP BY 1
          ), attributed AS (
            SELECT settlement.component_code,count(*)::integer AS item_count,
              coalesce(sum(entry.delta_score),0)::numeric AS score
            FROM public.lesson_score_component_settlements settlement
            JOIN public.score_entries entry
              ON entry.score_entry_id=settlement.current_award_score_entry_id
            WHERE settlement.teacher_id=p_teacher_id
              AND settlement.status='AWARDED'
            GROUP BY settlement.component_code
            UNION ALL
            SELECT 'FEEDBACK_FAVORITE',count(*)::integer,
              coalesce(sum(entry.delta_score),0)::numeric
            FROM public.course_favorite_attributions favorite
            JOIN public.score_entries entry
              ON entry.score_entry_id=favorite.current_score_entry_id
            WHERE favorite.teacher_id=p_teacher_id
              AND favorite.status IN (
                'AWARDED','AWARDED_PENDING_EVIDENCE'
              )
          ), projected AS (
            SELECT expected.*,
              coalesce(ledger.score,0) AS current_score,
              CASE
                WHEN expected.component_code='CAPACITY_PEAK_SLOT_40'
                  THEN coalesce(wide_row.peak_slot_cnt,0)::numeric
                WHEN expected.points_per_unit IS NULL THEN 0::numeric
                ELSE coalesce(ledger.score,0)/expected.points_per_unit
              END AS unit_count,
              coalesce(attributed.item_count,0) AS attributed_count,
              coalesce(attributed.score,0) AS attributed_score
            FROM expected
            LEFT JOIN ledger USING(component_code)
            LEFT JOIN (
              SELECT component_code,sum(item_count)::integer item_count,
                sum(score)::numeric score
              FROM attributed GROUP BY component_code
            ) attributed USING(component_code)
          )
          INSERT INTO public.score_component_accounts(
            teacher_id,dimension,component_code,source_scope,source_metric,
            unit_count,points_per_unit,current_score,
            lesson_attributed_count,lesson_attributed_score,
            unattributed_score,reconciliation_status,score_rule_version,
            projection_revision,calculated_at,payload
          )
          SELECT p_teacher_id,projected.dimension,projected.component_code,
            projected.source_scope,projected.source_metric,
            projected.unit_count::double precision,
            projected.points_per_unit::double precision,
            projected.current_score::double precision,
            projected.attributed_count,
            projected.attributed_score::double precision,
            CASE WHEN projected.source_scope='LESSON' THEN
              (projected.current_score-projected.attributed_score)
            ELSE projected.current_score END::double precision,
            CASE
              WHEN projected.source_scope<>'LESSON' THEN 'NOT_APPLICABLE'
              WHEN projected.current_score=0
               AND projected.attributed_score=0 THEN 'MATCHED_ZERO'
              WHEN projected.current_score=projected.attributed_score
                THEN 'MATCHED'
              ELSE 'MISMATCH' END,
            policy_version,1,transaction_timestamp(),
            jsonb_build_object(
              'projection_generation',p_projection_generation,
              'projection_vector_sha256',vector_sha256,
              'config_version_id',config_snapshot->>'version_id',
              'config_payload_sha256',config_snapshot->>'payload_sha256'
            )
          FROM projected
          WHERE projected.current_score>=0
          ON CONFLICT (teacher_id,component_code) DO UPDATE SET
            dimension=excluded.dimension,source_scope=excluded.source_scope,
            source_metric=excluded.source_metric,unit_count=excluded.unit_count,
            points_per_unit=excluded.points_per_unit,
            current_score=excluded.current_score,
            lesson_attributed_count=excluded.lesson_attributed_count,
            lesson_attributed_score=excluded.lesson_attributed_score,
            unattributed_score=excluded.unattributed_score,
            reconciliation_status=excluded.reconciliation_status,
            score_rule_version=excluded.score_rule_version,
            projection_revision=
              public.score_component_accounts.projection_revision+1,
            calculated_at=transaction_timestamp(),payload=excluded.payload
          WHERE ROW(
            public.score_component_accounts.dimension,
            public.score_component_accounts.source_scope,
            public.score_component_accounts.source_metric,
            public.score_component_accounts.unit_count,
            public.score_component_accounts.points_per_unit,
            public.score_component_accounts.current_score,
            public.score_component_accounts.lesson_attributed_count,
            public.score_component_accounts.lesson_attributed_score,
            public.score_component_accounts.unattributed_score,
            public.score_component_accounts.reconciliation_status,
            public.score_component_accounts.score_rule_version,
            public.score_component_accounts.payload
          ) IS DISTINCT FROM ROW(
            excluded.dimension,excluded.source_scope,excluded.source_metric,
            excluded.unit_count,excluded.points_per_unit,
            excluded.current_score,excluded.lesson_attributed_count,
            excluded.lesson_attributed_score,excluded.unattributed_score,
            excluded.reconciliation_status,excluded.score_rule_version,
            excluded.payload
          );
          GET DIAGNOSTICS component_changes=ROW_COUNT;
          IF component_changes<15 AND NOT EXISTS (
            SELECT 1 FROM public.score_component_accounts
            WHERE teacher_id=p_teacher_id AND current_score<0
          ) THEN
            NULL;
          END IF;
          IF (SELECT count(*) FROM public.score_component_accounts
              WHERE teacher_id=p_teacher_id
                AND component_code IN (
                  'FEEDBACK_PRAISE','FEEDBACK_FAVORITE',
                  'PERFECT_COMPLETED','PEAK_COMPLETED',
                  'CLASS_QUALITY_HARDWARE','CAPACITY_PEAK_SLOT_40',
                  'G01','G02','G03','G04','G05','G06','G07','G08','G09'
                ))<>15 THEN
            RAISE EXCEPTION 'DTS_V2_SCORE_COMPONENT_SET_INCOMPLETE'
              USING ERRCODE='55000';
          END IF;

          WITH dimensions(dimension) AS (VALUES
            ('USER_FEEDBACK'),('RELIABILITY'),('CLASS_QUALITY'),
            ('CAPACITY'),('NEW_TEACHER_TASK')
          ), projected AS (
            SELECT dimensions.dimension,
              coalesce(sum(component.current_score),0)::double precision score
            FROM dimensions
            LEFT JOIN public.score_component_accounts component
              ON component.teacher_id=p_teacher_id
             AND component.dimension=dimensions.dimension
            GROUP BY dimensions.dimension
          )
          INSERT INTO public.score_accounts(
            teacher_id,dimension,current_score,score_rule_version,
            version,updated_at,payload
          )
          SELECT p_teacher_id,dimension,score,policy_version,1,
            transaction_timestamp(),jsonb_build_object(
              'projection_generation',p_projection_generation,
              'projection_vector_sha256',vector_sha256,
              'config_version_id',config_snapshot->>'version_id',
              'config_payload_sha256',config_snapshot->>'payload_sha256'
            )
          FROM projected
          ON CONFLICT (teacher_id,dimension) DO UPDATE SET
            current_score=excluded.current_score,
            score_rule_version=excluded.score_rule_version,
            version=public.score_accounts.version+1,
            updated_at=transaction_timestamp(),payload=excluded.payload
          WHERE ROW(
            public.score_accounts.current_score,
            public.score_accounts.score_rule_version,
            public.score_accounts.payload
          ) IS DISTINCT FROM ROW(
            excluded.current_score,excluded.score_rule_version,
            excluded.payload
          );
          GET DIAGNOSTICS account_changes=ROW_COUNT;

          SELECT coalesce(sum(delta_score),0)::numeric INTO raw_total
          FROM public.score_entries WHERE teacher_id=p_teacher_id;
          SELECT coalesce(sum(current_score),0)::numeric INTO account_total
          FROM public.score_accounts WHERE teacher_id=p_teacher_id
            AND dimension IN (
              'USER_FEEDBACK','RELIABILITY','CLASS_QUALITY',
              'CAPACITY','NEW_TEACHER_TASK'
            );
          IF raw_total IS DISTINCT FROM account_total THEN
            RAISE EXCEPTION 'DTS_V2_SCORE_ACCOUNT_LEDGER_MISMATCH'
              USING ERRCODE='55000';
          END IF;

          SELECT count(*)::integer,
            count(*) FILTER (WHERE status='COMPLETED')::integer
          INTO mandatory_total,mandatory_completed
          FROM public.task_assignments
          WHERE teacher_id=p_teacher_id
            AND task_kind='FIXED_GROWTH'
            AND creator_system='TRIGGER_CENTER'
            AND task_code BETWEEN 'G01' AND 'G09';
          IF mandatory_total<>9 OR EXISTS (
            SELECT 1 FROM generate_series(1,9) number
            WHERE NOT EXISTS (
              SELECT 1 FROM public.task_assignments assignment
              WHERE assignment.teacher_id=p_teacher_id
                AND assignment.task_kind='FIXED_GROWTH'
                AND assignment.creator_system='TRIGGER_CENTER'
                AND assignment.task_code='G0'||number::text
            )
          ) THEN
            RAISE EXCEPTION 'TASK_BASELINE_INCOMPLETE'
              USING ERRCODE='55000';
          END IF;

          SELECT count(DISTINCT ROW(
                   complaint.source_region,complaint.source_appoint_id
                 )) FILTER (
                   WHERE complaint.is_deleted IS FALSE
                     AND complaint.is_valid IS TRUE
                 )::integer,
                 count(*) FILTER (
                   WHERE complaint.is_deleted IS FALSE
                     AND complaint.is_valid IS TRUE
                     AND rule.source_level IN ('P0','L0')
                 )::integer
          INTO valid_complaint_course_count,l0_complaint_count
          FROM public.source_course_complaints complaint
          JOIN public.source_courses course
            ON course.source_region=complaint.source_region
           AND course.source_appoint_id=complaint.source_appoint_id
          LEFT JOIN public.complaint_category_rules rule
            ON rule.rule_id=complaint.complaint_rule_id
           AND rule.source_sha256=complaint.source_sha256
          WHERE course.completion_teacher_id=p_teacher_id
            AND course.completion_voided_at IS NULL;

          complaint_evidence_complete :=
            wide_row.tchr_id IS NOT NULL
            AND wide_row.feedback_valid_complaint_cnt IS NOT NULL
            AND wide_row.feedback_valid_complaint_cnt=
                  valid_complaint_course_count
            AND NOT EXISTS (
              SELECT 1 FROM public.source_courses course
              LEFT JOIN public.source_course_fact_current fact
                ON fact.source_region=course.source_region
               AND fact.source_appoint_id=course.source_appoint_id
              WHERE course.completion_teacher_id=p_teacher_id
                AND course.completion_voided_at IS NULL
                AND coalesce(fact.complaint_evidence_status,'SOURCE_MISSING')
                    <>'CONFIRMED'
            )
            AND NOT EXISTS (
              SELECT 1 FROM public.source_course_complaints complaint
              JOIN public.source_courses course
                ON course.source_region=complaint.source_region
               AND course.source_appoint_id=complaint.source_appoint_id
              WHERE course.completion_teacher_id=p_teacher_id
                AND course.completion_voided_at IS NULL
                AND complaint.is_deleted IS FALSE
                AND (
                  complaint.evidence_status<>'CONFIRMED'
                  OR (complaint.is_valid IS TRUE
                      AND complaint.complaint_rule_id IS NULL)
                )
            );

          attendance_evidence_complete :=
            wide_row.tchr_id IS NOT NULL
            AND wide_row.late_cnt IS NOT NULL
            AND wide_row.early_cnt IS NOT NULL
            AND wide_row.absent_cnt IS NOT NULL
            AND NOT EXISTS (
              SELECT 1 FROM public.source_course_participations participation
              LEFT JOIN public.source_participation_fact_current fact
                ON fact.source_region=participation.source_region
               AND fact.source_appoint_id=participation.source_appoint_id
               AND fact.participation_seq=participation.participation_seq
              WHERE participation.teacher_id=p_teacher_id
                AND participation.participation_role IN ('NORMAL','COMPLETION')
                AND participation.source_deleted IS FALSE
                AND (
                  participation.assigned_at_evidence_status<>'CONFIRMED'
                  OR fact.late_evidence_status IS DISTINCT FROM 'CONFIRMED'
                  OR fact.early_evidence_status IS DISTINCT FROM 'CONFIRMED'
                  OR (lower(coalesce(participation.participation_status,''))
                        IN ('t_absent','absent')
                      AND nullif(btrim(
                        participation.absence_reason_detail
                      ),'') IS NULL)
                )
            );

          graduation_criteria := raw_total>=100
            AND mandatory_completed=9
            AND complaint_evidence_complete
            AND l0_complaint_count=0;
          gold_criteria := raw_total>=200
            AND graduation_criteria
            AND attendance_evidence_complete
            AND wide_row.late_cnt<=1
            AND wide_row.early_cnt=0
            AND wide_row.absent_cnt=0;
          gate_document := jsonb_build_object(
            'projection_generation',p_projection_generation,
            'projection_vector_sha256',vector_sha256,
            'config_version_id',config_snapshot->>'version_id',
            'config_payload_sha256',config_snapshot->>'payload_sha256',
            'raw_total_score',raw_total,
            'public_total_score',least(raw_total,200),
            'graduation_raw_score_threshold',100,
            'gold_raw_score_threshold',200,
            'mandatory_task_completed_count',mandatory_completed,
            'mandatory_task_expected_count',9,
            'mandatory_task_evidence_complete',mandatory_total=9,
            'valid_complaint_course_count',valid_complaint_course_count,
            'l0_complaint_count',l0_complaint_count,
            'complaint_evidence_complete',complaint_evidence_complete,
            'late_count',wide_row.late_cnt,
            'early_count',wide_row.early_cnt,
            'absent_count',wide_row.absent_cnt,
            'attendance_evidence_complete',attendance_evidence_complete,
            'graduation_criteria_met',graduation_criteria,
            'gold_criteria_met',gold_criteria,
            'qualification_grants_enabled',
              control_row.qualification_grants_enabled
          );

          INSERT INTO public.teacher_qualifications(
            teacher_id,graduation_criteria_met,graduation_qualified,
            graduation_qualified_at,graduation_score_locked,
            gold_criteria_met,gold_qualified,gold_qualified_at,
            score_rule_version,gate_results,revision,calculated_at
          ) VALUES (
            p_teacher_id,graduation_criteria,
            control_row.qualification_grants_enabled
              AND graduation_criteria,
            CASE WHEN control_row.qualification_grants_enabled
                         AND graduation_criteria
              THEN transaction_timestamp()
              ELSE NULL END,
            CASE WHEN control_row.qualification_grants_enabled
                         AND graduation_criteria
              THEN 100 ELSE NULL END,
            gold_criteria,
            control_row.qualification_grants_enabled AND gold_criteria,
            CASE WHEN control_row.qualification_grants_enabled
                         AND gold_criteria
              THEN transaction_timestamp() ELSE NULL END,
            policy_version,gate_document,1,transaction_timestamp()
          )
          ON CONFLICT (teacher_id) DO UPDATE SET
            graduation_criteria_met=excluded.graduation_criteria_met,
            graduation_qualified=
              public.teacher_qualifications.graduation_qualified
              OR excluded.graduation_qualified,
            graduation_qualified_at=coalesce(
              public.teacher_qualifications.graduation_qualified_at,
              excluded.graduation_qualified_at
            ),
            graduation_score_locked=coalesce(
              public.teacher_qualifications.graduation_score_locked,
              CASE WHEN
                public.teacher_qualifications.graduation_qualified
                OR excluded.graduation_qualified THEN 100 ELSE NULL END
            ),
            gold_criteria_met=excluded.gold_criteria_met,
            gold_qualified=public.teacher_qualifications.gold_qualified
              OR excluded.gold_qualified,
            gold_qualified_at=coalesce(
              public.teacher_qualifications.gold_qualified_at,
              excluded.gold_qualified_at
            ),
            score_rule_version=excluded.score_rule_version,
            gate_results=excluded.gate_results,
            revision=public.teacher_qualifications.revision+1,
            calculated_at=transaction_timestamp()
          WHERE ROW(
            public.teacher_qualifications.graduation_criteria_met,
            public.teacher_qualifications.graduation_qualified,
            public.teacher_qualifications.graduation_score_locked,
            public.teacher_qualifications.gold_criteria_met,
            public.teacher_qualifications.gold_qualified,
            public.teacher_qualifications.score_rule_version,
            public.teacher_qualifications.gate_results
          ) IS DISTINCT FROM ROW(
            excluded.graduation_criteria_met,
            public.teacher_qualifications.graduation_qualified
              OR excluded.graduation_qualified,
            coalesce(
              public.teacher_qualifications.graduation_score_locked,
              CASE WHEN
                public.teacher_qualifications.graduation_qualified
                OR excluded.graduation_qualified THEN 100 ELSE NULL END
            ),
            excluded.gold_criteria_met,
            public.teacher_qualifications.gold_qualified
              OR excluded.gold_qualified,
            excluded.score_rule_version,excluded.gate_results
          );
          GET DIAGNOSTICS qualification_changes=ROW_COUNT;
          SELECT * INTO STRICT qualification_row
          FROM public.teacher_qualifications WHERE teacher_id=p_teacher_id;

          UPDATE public.teachers teacher SET
            total_score=raw_total::double precision,
            graduation_threshold=100,
            graduation_state=CASE
              WHEN qualification_row.graduation_qualified THEN 'GRADUATED'
              ELSE 'IN_CAMP' END,
            gold_qualified=qualification_row.gold_qualified,
            payload=(coalesce(teacher.payload,'{}'::jsonb) ||
              jsonb_build_object(
                'raw_total_score',raw_total,
                'external_display_score',least(raw_total,200),
                'graduation_state',CASE
                  WHEN qualification_row.graduation_qualified
                    THEN 'GRADUATED' ELSE 'IN_CAMP' END,
                'graduation_qualified',
                  qualification_row.graduation_qualified,
                'gold_qualified',qualification_row.gold_qualified,
                'graduation_criteria_met',graduation_criteria,
                'gold_criteria_met',gold_criteria,
                'qualification_grants_enabled',
                  control_row.qualification_grants_enabled,
                'score_policy_sha256',
                  config_snapshot->>'payload_sha256',
                'projection_generation',p_projection_generation
              )),
            updated_at=transaction_timestamp()
          WHERE teacher.teacher_id=p_teacher_id
            AND ROW(
              teacher.total_score,teacher.graduation_threshold,
              teacher.graduation_state,teacher.gold_qualified,
              teacher.payload
            ) IS DISTINCT FROM ROW(
              raw_total::double precision,100::double precision,
              CASE WHEN qualification_row.graduation_qualified
                THEN 'GRADUATED' ELSE 'IN_CAMP' END,
              qualification_row.gold_qualified,
              (coalesce(teacher.payload,'{}'::jsonb) || jsonb_build_object(
                'raw_total_score',raw_total,
                'external_display_score',least(raw_total,200),
                'graduation_state',CASE
                  WHEN qualification_row.graduation_qualified
                    THEN 'GRADUATED' ELSE 'IN_CAMP' END,
                'graduation_qualified',
                  qualification_row.graduation_qualified,
                'gold_qualified',qualification_row.gold_qualified,
                'graduation_criteria_met',graduation_criteria,
                'gold_criteria_met',gold_criteria,
                'qualification_grants_enabled',
                  control_row.qualification_grants_enabled,
                'score_policy_sha256',
                  config_snapshot->>'payload_sha256',
                'projection_generation',p_projection_generation
              ))
            );
          GET DIAGNOSTICS teacher_changes=ROW_COUNT;
          RETURN jsonb_build_object(
            'score_component_changes',component_changes,
            'score_account_changes',account_changes,
            'qualification_changes',qualification_changes,
            'teacher_changes',teacher_changes
          );
        END
        $function$;

        REVOKE ALL ON FUNCTION
          public.rebuild_teacher_score_and_qualification_v2(
            text,jsonb,bigint
          )
        FROM PUBLIC;
        """
    )


def _create_scorecard_views() -> None:
    op.execute(
        r"""
        CREATE VIEW public.teacher_scorecard_v1_compat_v1 AS
        SELECT teacher.teacher_id,teacher.camp_enrollment_id,
          teacher.online_status,
          projection.raw_total_score::double precision AS raw_total_score,
          least(projection.raw_total_score,200)::double precision
            AS public_total_score,
          teacher.graduation_state,qualification.graduation_qualified,
          qualification.graduation_qualified_at,
          qualification.graduation_score_locked,
          qualification.gold_qualified,
          (CASE WHEN qualification.gold_qualified THEN 'GOLD'
             ELSE 'NOT_GOLD' END)::varchar(16) AS gold_status,
          qualification.gold_qualified_at,
          coalesce((qualification.gate_results->>
            'graduation_raw_score_threshold')::double precision,100)
            AS graduation_threshold,
          coalesce((qualification.gate_results->>
            'gold_raw_score_threshold')::double precision,200)
            AS gold_threshold,
          (qualification.gate_results->>
            'mandatory_task_completed_count')::integer
            AS mandatory_task_completed_count,
          (qualification.gate_results->>
            'mandatory_task_expected_count')::integer
            AS mandatory_task_total_count,
          qualification.score_rule_version,
          greatest(qualification.calculated_at,projection.calculated_at)
            AS calculated_at,
          projection.dimensions,
          (CASE WHEN source.tchr_id IS NULL THEN 'SOURCE_MISSING'
             ELSE 'CONFIRMED' END)::varchar(32) AS teacher_source_status
        FROM public.teachers teacher
        JOIN public.teacher_qualifications qualification
          ON qualification.teacher_id=teacher.teacher_id
        LEFT JOIN public.teacher_source_wide source
          ON source.tchr_id=teacher.teacher_id
        JOIN public.dts_pipeline_control control
          ON control.control_id='PRIMARY'
         AND control.mode IN ('V1_COMPAT_DUAL_CAPTURE','ROLLED_BACK')
        JOIN LATERAL (
          SELECT count(*)::integer AS dimension_count,
            coalesce(sum(account.current_score),0)::numeric
              AS raw_total_score,
            max(greatest(account.updated_at,
              coalesce(component_set.calculated_at,account.updated_at)))
              AS calculated_at,
            jsonb_agg(jsonb_build_object(
              'code',account.dimension,'score',account.current_score,
              'score_rule_version',account.score_rule_version,
              'projection_revision',account.version,
              'calculated_at',account.updated_at,
              'components',coalesce(component_set.components,'[]'::jsonb)
            ) ORDER BY CASE account.dimension
              WHEN 'USER_FEEDBACK' THEN 1 WHEN 'RELIABILITY' THEN 2
              WHEN 'CLASS_QUALITY' THEN 3 WHEN 'CAPACITY' THEN 4
              WHEN 'NEW_TEACHER_TASK' THEN 5 ELSE 99 END) AS dimensions
          FROM public.score_accounts account
          LEFT JOIN LATERAL (
            SELECT max(component.calculated_at) AS calculated_at,
              jsonb_agg(jsonb_build_object(
                'code',component.component_code,
                'source_scope',component.source_scope,
                'source_metric',component.source_metric,
                'unit_count',component.unit_count,
                'points_per_unit',component.points_per_unit,
                'score',component.current_score,
                'lesson_attributed_count',component.lesson_attributed_count,
                'lesson_attributed_score',component.lesson_attributed_score,
                'unattributed_score',component.unattributed_score,
                'reconciliation_status',component.reconciliation_status
              ) ORDER BY component.component_code) AS components
            FROM public.score_component_accounts component
            WHERE component.teacher_id=account.teacher_id
              AND component.dimension=account.dimension
          ) component_set ON true
          WHERE account.teacher_id=teacher.teacher_id
            AND account.dimension IN (
              'USER_FEEDBACK','RELIABILITY','CLASS_QUALITY',
              'CAPACITY','NEW_TEACHER_TASK'
            )
        ) projection ON projection.dimension_count=5;

        CREATE VIEW public.teacher_scorecard_v2_v1 AS
        SELECT teacher.teacher_id,teacher.camp_enrollment_id,
          teacher.online_status,
          projection.raw_total_score::double precision AS raw_total_score,
          least(projection.raw_total_score,200)::double precision
            AS public_total_score,
          teacher.graduation_state,qualification.graduation_qualified,
          qualification.graduation_qualified_at,
          qualification.graduation_score_locked,
          qualification.gold_qualified,
          (CASE WHEN qualification.gold_qualified THEN 'GOLD'
             ELSE 'NOT_GOLD' END)::varchar(16) AS gold_status,
          qualification.gold_qualified_at,
          (qualification.gate_results->>
            'graduation_raw_score_threshold')::double precision
            AS graduation_threshold,
          (qualification.gate_results->>
            'gold_raw_score_threshold')::double precision
            AS gold_threshold,
          (qualification.gate_results->>
            'mandatory_task_completed_count')::integer
            AS mandatory_task_completed_count,
          (qualification.gate_results->>
            'mandatory_task_expected_count')::integer
            AS mandatory_task_total_count,
          qualification.score_rule_version,
          greatest(qualification.calculated_at,projection.calculated_at)
            AS calculated_at,
          projection.dimensions,
          (CASE WHEN source.tchr_id IS NULL THEN 'SOURCE_MISSING'
             ELSE 'CONFIRMED' END)::varchar(32) AS teacher_source_status
        FROM public.teachers teacher
        JOIN public.teacher_qualifications qualification
          ON qualification.teacher_id=teacher.teacher_id
        LEFT JOIN public.teacher_source_wide source
          ON source.tchr_id=teacher.teacher_id
        JOIN public.dts_pipeline_control control
          ON control.control_id='PRIMARY' AND control.mode='V2_PRIMARY'
         AND control.projection_generation>=1
        JOIN public.config_versions config
          ON config.config_key='SCORE_GRADUATION'
         AND config.status='PUBLISHED'
         AND config.version_id=
               qualification.gate_results->>'config_version_id'
         AND public.dts_canonical_json_sha256_v1(config.payload)=
               qualification.gate_results->>'config_payload_sha256'
        JOIN LATERAL (
          SELECT count(*)::integer AS dimension_count,
            count(*) FILTER (
              WHERE (account.payload->>'projection_generation')::bigint=
                    control.projection_generation
            )::integer AS current_dimension_count,
            coalesce(sum(account.current_score),0)::numeric
              AS raw_total_score,
            max(greatest(account.updated_at,
              coalesce(component_set.calculated_at,account.updated_at)))
              AS calculated_at,
            sum(component_set.component_count)::integer AS component_count,
            sum(component_set.current_component_count)::integer
              AS current_component_count,
            jsonb_agg(jsonb_build_object(
              'code',account.dimension,'score',account.current_score,
              'score_rule_version',account.score_rule_version,
              'projection_revision',account.version,
              'calculated_at',account.updated_at,
              'components',coalesce(component_set.components,'[]'::jsonb)
            ) ORDER BY CASE account.dimension
              WHEN 'USER_FEEDBACK' THEN 1 WHEN 'RELIABILITY' THEN 2
              WHEN 'CLASS_QUALITY' THEN 3 WHEN 'CAPACITY' THEN 4
              WHEN 'NEW_TEACHER_TASK' THEN 5 ELSE 99 END) AS dimensions
          FROM public.score_accounts account
          JOIN LATERAL (
            SELECT count(*)::integer AS component_count,
              count(*) FILTER (
                WHERE (component.payload->>'projection_generation')::bigint=
                      control.projection_generation
              )::integer AS current_component_count,
              max(component.calculated_at) AS calculated_at,
              jsonb_agg(jsonb_build_object(
                'code',component.component_code,
                'source_scope',component.source_scope,
                'source_metric',component.source_metric,
                'unit_count',component.unit_count,
                'points_per_unit',component.points_per_unit,
                'score',component.current_score,
                'lesson_attributed_count',component.lesson_attributed_count,
                'lesson_attributed_score',component.lesson_attributed_score,
                'unattributed_score',component.unattributed_score,
                'reconciliation_status',component.reconciliation_status
              ) ORDER BY component.component_code) AS components
            FROM public.score_component_accounts component
            WHERE component.teacher_id=account.teacher_id
              AND component.dimension=account.dimension
          ) component_set ON true
          WHERE account.teacher_id=teacher.teacher_id
            AND account.dimension IN (
              'USER_FEEDBACK','RELIABILITY','CLASS_QUALITY',
              'CAPACITY','NEW_TEACHER_TASK'
            )
        ) projection ON projection.dimension_count=5
          AND projection.current_dimension_count=5
          AND projection.component_count=15
          AND projection.current_component_count=15
        WHERE (qualification.gate_results->>
                 'projection_generation')::bigint=
                control.projection_generation
          AND (qualification.gate_results->>'raw_total_score')::numeric=
                projection.raw_total_score;
        """
    )


def _create_lesson_views() -> None:
    op.execute(
        r"""
        CREATE VIEW public.teacher_lesson_score_v1_compat_v1 AS
        WITH visible AS (
          SELECT participation.*,course.scheduled_start_at,
            course.lesson_local_date,course.lesson_local_time,
            course.source_status,course.completion_participation_seq,
            course.completion_teacher_id,course.completion_voided_at,
            course.completion_conflict_status,course.evidence_status,
            course.updated_at AS course_updated_at
          FROM public.source_course_participations participation
          JOIN public.source_courses course
            ON course.source_region=participation.source_region
           AND course.source_appoint_id=participation.source_appoint_id
          JOIN public.dts_pipeline_control control
            ON control.control_id='PRIMARY'
           AND control.mode IN ('V1_COMPAT_DUAL_CAPTURE','ROLLED_BACK')
          WHERE participation.participation_role IN ('NORMAL','COMPLETION')
            AND participation.source_deleted IS FALSE
        ), ranked AS (
          SELECT visible.*,
            row_number() OVER (PARTITION BY teacher_id ORDER BY
              scheduled_start_at NULLS LAST,
              lesson_local_date NULLS LAST,lesson_local_time NULLS LAST,
              source_region,source_appoint_id,participation_seq)::integer
              AS lesson_sequence,
            count(*) OVER (PARTITION BY teacher_id)::integer AS lesson_count
          FROM visible
        )
        SELECT ranked.teacher_id,ranked.source_region,
          ranked.source_appoint_id,ranked.participation_seq,
          ranked.participation_role,true AS visible_to_teacher,
          ranked.lesson_sequence,ranked.lesson_count,
          ranked.scheduled_start_at,ranked.lesson_local_date,
          ranked.lesson_local_time,
          coalesce(nullif(btrim(ranked.participation_status),''),
            nullif(btrim(ranked.source_status),''),'SOURCE_MISSING'
          )::varchar(48) AS lesson_lifecycle_status,
          CASE WHEN scoring.valid_for_scoring
            AND source."迟到" IS NOT NULL AND source."早退" IS NOT NULL
            THEN source."迟到" IS FALSE AND source."早退" IS FALSE
            ELSE NULL END AS is_perfect,
          scoring.valid_for_scoring,
          (CASE WHEN source."课程id" IS NULL THEN 'SOURCE_MISSING'
             ELSE ranked.evidence_status END)::varchar(32)
            AS evidence_status,
          CASE WHEN scoring.valid_for_scoring
            THEN coalesce(result.lesson_total_score,0) ELSE 0 END
            AS lesson_total_score,
          CASE WHEN scoring.valid_for_scoring
            THEN result.score_rule_version ELSE NULL END::text
            AS score_rule_version,
          coalesce(result.calculated_at,ranked.course_updated_at)
            AS updated_at,
          jsonb_build_object(
            'attendance',jsonb_build_object(
              'is_late',CASE WHEN scoring.valid_for_scoring
                THEN source."迟到" ELSE NULL END,
              'is_early',CASE WHEN scoring.valid_for_scoring
                THEN source."早退" ELSE NULL END,
              'absence_reason_detail',ranked.absence_reason_detail
            ),
            'user_feedback',jsonb_build_object(
              'has_positive_feedback_tag',source."好评标签",
              'has_negative_feedback_tag',source."差评标签",
              'feedback_detail',source."评价详情",
              'is_favorited',source."收藏",
              'is_blocked',source."是否拉黑"
            ),
            'classroom_quality',jsonb_build_object(
              'is_camera_off',source."未开摄像头",
              'is_cpu_usage_high',NULL,'is_network_delay_high',NULL,
              'hardware_quality_passed',NULL,
              'is_perfect',CASE WHEN scoring.valid_for_scoring
                AND source."迟到" IS NOT NULL AND source."早退" IS NOT NULL
                THEN source."迟到" IS FALSE AND source."早退" IS FALSE
                ELSE NULL END
            ),
            'capacity',jsonb_build_object('is_peak',source."是否高峰"),
            'complaint',jsonb_build_object(
              'category_l1',source."投诉一级分类",
              'category_l2',source."投诉二级分类",
              'category_l3',source."投诉三级分类"
            )
          ) AS business_facts,
          CASE WHEN scoring.valid_for_scoring AND result.lesson_id IS NOT NULL
            THEN jsonb_build_array(
              coalesce(result.dimensions->'USER_FEEDBACK','{}'::jsonb)
                || jsonb_build_object('code','USER_FEEDBACK'),
              coalesce(result.dimensions->'RELIABILITY','{}'::jsonb)
                || jsonb_build_object('code','RELIABILITY'),
              coalesce(result.dimensions->'CLASS_QUALITY','{}'::jsonb)
                || jsonb_build_object('code','CLASS_QUALITY')
            ) ELSE jsonb_build_array(
              jsonb_build_object('code','USER_FEEDBACK','score',0,
                'components','[]'::jsonb),
              jsonb_build_object('code','RELIABILITY','score',0,
                'components','[]'::jsonb),
              jsonb_build_object('code','CLASS_QUALITY','score',0,
                'components','[]'::jsonb)
            ) END AS dimensions
        FROM ranked
        LEFT JOIN public.lesson_source_wide source
          ON source.source_region=ranked.source_region
         AND source."课程id"=ranked.source_appoint_id
        LEFT JOIN public.lesson_score_results result
          ON result.lesson_source_region=ranked.source_region
         AND result.lesson_id=ranked.source_appoint_id
         AND result.v2_source_region IS NULL
        CROSS JOIN LATERAL (
          SELECT ranked.participation_seq=
                   ranked.completion_participation_seq
             AND ranked.teacher_id=ranked.completion_teacher_id
             AND ranked.completion_voided_at IS NULL
             AND ranked.completion_conflict_status<>'PENDING'
             AS valid_for_scoring
        ) scoring;

        CREATE VIEW public.teacher_lesson_score_v2_v1 AS
        WITH visible AS (
          SELECT participation.*,course.scheduled_start_at,
            course.lesson_local_date,course.lesson_local_time,
            course.source_status,course.completion_participation_seq,
            course.completion_teacher_id,course.completion_voided_at,
            course.completion_conflict_status,course.completion_is_peak,
            course.completion_student_token,course.evidence_status,
            course.updated_at AS course_updated_at,
            control.projection_generation
          FROM public.source_course_participations participation
          JOIN public.source_courses course
            ON course.source_region=participation.source_region
           AND course.source_appoint_id=participation.source_appoint_id
          JOIN public.dts_pipeline_control control
            ON control.control_id='PRIMARY' AND control.mode='V2_PRIMARY'
           AND control.projection_generation>=1
          WHERE participation.participation_role IN ('NORMAL','COMPLETION')
            AND participation.source_deleted IS FALSE
        ), ranked AS (
          SELECT visible.*,
            row_number() OVER (PARTITION BY teacher_id ORDER BY
              scheduled_start_at NULLS LAST,
              lesson_local_date NULLS LAST,lesson_local_time NULLS LAST,
              source_region,source_appoint_id,participation_seq)::integer
              AS lesson_sequence,
            count(*) OVER (PARTITION BY teacher_id)::integer AS lesson_count
          FROM visible
        )
        SELECT ranked.teacher_id,ranked.source_region,
          ranked.source_appoint_id,ranked.participation_seq,
          ranked.participation_role,true AS visible_to_teacher,
          ranked.lesson_sequence,ranked.lesson_count,
          ranked.scheduled_start_at,ranked.lesson_local_date,
          ranked.lesson_local_time,
          coalesce(nullif(btrim(ranked.participation_status),''),
            nullif(btrim(ranked.source_status),''),'SOURCE_MISSING'
          )::varchar(48) AS lesson_lifecycle_status,
          quality.is_perfect,scoring.valid_for_scoring,
          (CASE
            WHEN scoring.valid_for_scoring AND result.lesson_id IS NULL
              THEN 'SOURCE_MISSING'
            WHEN course_fact.source_appoint_id IS NULL
              OR participation_fact.participation_seq IS NULL
              THEN 'SOURCE_MISSING'
            ELSE ranked.evidence_status END)::varchar(32)
              AS evidence_status,
          CASE WHEN scoring.valid_for_scoring
            THEN coalesce(result.lesson_total_score,0) ELSE 0 END
              AS lesson_total_score,
          CASE WHEN scoring.valid_for_scoring
            THEN result.score_rule_version ELSE NULL END::text
              AS score_rule_version,
          coalesce(result.calculated_at,ranked.course_updated_at)
              AS updated_at,
          jsonb_build_object(
            'attendance',jsonb_build_object(
              'is_late',participation_fact.is_late,
              'is_early',participation_fact.is_early,
              'absence_reason_detail',ranked.absence_reason_detail,
              'late_evidence_status',
                coalesce(participation_fact.late_evidence_status,
                  'SOURCE_MISSING'),
              'early_evidence_status',
                coalesce(participation_fact.early_evidence_status,
                  'SOURCE_MISSING')
            ),
            'user_feedback',jsonb_build_object(
              'grading_classification',
                course_fact.grading_classification,
              'negative_score',course_fact.negative_score,
              'labels',coalesce(labels.items,'[]'::jsonb),
              'favorite_attribution_status',favorite.status,
              'is_blocked',relationship.is_blocked
            ),
            'classroom_quality',jsonb_build_object(
              'is_camera_off',course_fact.is_camera_off,
              'is_cpu_usage_high',NULL,'is_network_delay_high',NULL,
              'hardware_quality_passed',quality.hardware_quality_passed,
              'is_perfect',quality.is_perfect
            ),
            'capacity',jsonb_build_object(
              'is_peak',ranked.completion_is_peak
            ),
            'complaint',jsonb_build_object(
              'has_complaint',course_fact.has_complaint,
              'has_valid_complaint',course_fact.has_valid_complaint,
              'category_l1',course_fact.latest_category_l1_snapshot,
              'category_l2',course_fact.latest_category_l2_snapshot,
              'category_l3',course_fact.latest_category_l3_snapshot,
              'level',complaint_rule.source_level,
              'route',complaint_rule.default_route,
              'evidence_status',course_fact.complaint_evidence_status
            )
          ) AS business_facts,
          CASE WHEN scoring.valid_for_scoring AND result.lesson_id IS NOT NULL
            THEN jsonb_build_array(
              coalesce(result.dimensions->'USER_FEEDBACK','{}'::jsonb)
                || jsonb_build_object('code','USER_FEEDBACK'),
              coalesce(result.dimensions->'RELIABILITY','{}'::jsonb)
                || jsonb_build_object('code','RELIABILITY'),
              coalesce(result.dimensions->'CLASS_QUALITY','{}'::jsonb)
                || jsonb_build_object('code','CLASS_QUALITY')
            ) ELSE jsonb_build_array(
              jsonb_build_object('code','USER_FEEDBACK','score',0,
                'components','[]'::jsonb),
              jsonb_build_object('code','RELIABILITY','score',0,
                'components','[]'::jsonb),
              jsonb_build_object('code','CLASS_QUALITY','score',0,
                'components','[]'::jsonb)
            ) END AS dimensions
        FROM ranked
        LEFT JOIN public.source_course_fact_current course_fact
          ON course_fact.source_region=ranked.source_region
         AND course_fact.source_appoint_id=ranked.source_appoint_id
        LEFT JOIN public.source_participation_fact_current participation_fact
          ON participation_fact.source_region=ranked.source_region
         AND participation_fact.source_appoint_id=ranked.source_appoint_id
         AND participation_fact.participation_seq=ranked.participation_seq
        LEFT JOIN public.lesson_score_results result
          ON result.v2_source_region=ranked.source_region
         AND result.v2_source_appoint_id=ranked.source_appoint_id
         AND result.v2_completion_participation_seq=ranked.participation_seq
         AND result.v2_teacher_id=ranked.teacher_id
         AND result.v2_projection_generation=ranked.projection_generation
        LEFT JOIN public.source_course_complaints complaint
          ON complaint.source_region=course_fact.source_region
         AND complaint.source_complaint_id=
               course_fact.latest_valid_complaint_id
        LEFT JOIN public.complaint_category_rules complaint_rule
          ON complaint_rule.rule_id=complaint.complaint_rule_id
         AND complaint_rule.source_sha256=complaint.source_sha256
        LEFT JOIN LATERAL (
          SELECT jsonb_agg(jsonb_build_object(
            'label_id',label.label_id,'label_name',label.label_name_snapshot
          ) ORDER BY label.label_id) AS items
          FROM public.source_course_labels label
          WHERE label.source_region=ranked.source_region
            AND label.source_appoint_id=ranked.source_appoint_id
            AND label.is_deleted IS FALSE
        ) labels ON true
        LEFT JOIN public.course_favorite_attributions favorite
          ON favorite.source_region=ranked.source_region
         AND favorite.source_appoint_id=ranked.source_appoint_id
         AND favorite.completion_participation_seq=ranked.participation_seq
         AND favorite.teacher_id=ranked.teacher_id
         AND favorite.status IN ('AWARDED','AWARDED_PENDING_EVIDENCE')
        LEFT JOIN public.teacher_student_relationship_current relationship
          ON relationship.source_region=ranked.source_region
         AND relationship.teacher_id=ranked.teacher_id
         AND relationship.student_token=ranked.completion_student_token
        CROSS JOIN LATERAL (
          SELECT ranked.participation_seq=
                   ranked.completion_participation_seq
             AND ranked.teacher_id=ranked.completion_teacher_id
             AND ranked.completion_voided_at IS NULL
             AND ranked.completion_conflict_status<>'PENDING'
             AS valid_for_scoring
        ) scoring
        CROSS JOIN LATERAL (
          SELECT CASE
            WHEN NOT scoring.valid_for_scoring THEN NULL
            WHEN participation_fact.late_evidence_status='CONFIRMED'
             AND participation_fact.early_evidence_status='CONFIRMED'
              THEN participation_fact.is_late IS FALSE
               AND participation_fact.is_early IS FALSE
            ELSE NULL END AS is_perfect,
            NULL::boolean AS hardware_quality_passed
        ) quality;
        """
    )


def _install_acl_and_comments() -> None:
    view_list = ",".join(f"public.{name}" for name in BRANCH_VIEWS)
    op.execute(
        f"""
        COMMENT ON VIEW public.teacher_scorecard_v1_compat_v1 IS
          'Versioned v1-compatible scorecard branch; mode-gated and not the stable current route';
        COMMENT ON VIEW public.teacher_scorecard_v2_v1 IS
          'Versioned ledger-authoritative v2 scorecard branch; serves only current projection_generation';
        COMMENT ON VIEW public.teacher_lesson_score_v1_compat_v1 IS
          'Versioned v1-compatible participation-grain lesson branch; mode-gated and not the stable current route';
        COMMENT ON VIEW public.teacher_lesson_score_v2_v1 IS
          'Versioned v2 participation-grain lesson branch; stale score generations are never served';

        REVOKE ALL ON FUNCTION
          public.score_projection_config_snapshot_v2(),
          public.teacher_score_projection_vector_v2(text),
          public.rebuild_lesson_score_result_v2(text,text,bigint),
          public.rebuild_teacher_score_and_qualification_v2(
            text,jsonb,bigint
          )
        FROM PUBLIC;
        REVOKE CREATE ON SCHEMA public FROM {OUTBOX_RUNTIME_ROLE};
        GRANT USAGE ON SCHEMA public TO {OUTBOX_RUNTIME_ROLE};
        GRANT EXECUTE ON FUNCTION
          public.teacher_score_projection_vector_v2(text),
          public.rebuild_lesson_score_result_v2(text,text,bigint),
          public.rebuild_teacher_score_and_qualification_v2(
            text,jsonb,bigint
          )
        TO {OUTBOX_RUNTIME_ROLE};

        REVOKE ALL PRIVILEGES ON TABLE {view_list} FROM PUBLIC;
        GRANT SELECT ON TABLE {view_list} TO tit_growth_app;
        DO $score_branch_read_acl$
        DECLARE role_name text;
        BEGIN
          FOREACH role_name IN ARRAY ARRAY[
            'tit_teacher_crud','tide_support_ticket_owner'
          ] LOOP
            IF to_regrole(role_name) IS NOT NULL THEN
              EXECUTE format(
                'REVOKE ALL ON FUNCTION '
                'public.score_projection_config_snapshot_v2(),'
                'public.teacher_score_projection_vector_v2(text),'
                'public.rebuild_lesson_score_result_v2(text,text,bigint),'
                'public.rebuild_teacher_score_and_qualification_v2('
                'text,jsonb,bigint) FROM %I',role_name
              );
              EXECUTE format(
                'GRANT SELECT ON TABLE {view_list} TO %I',role_name
              );
            END IF;
          END LOOP;
        END
        $score_branch_read_acl$;
        """
    )


def _assert_installed_contract() -> None:
    op.execute(
        r"""
        DO $score_projection_installed$
        DECLARE function_name text;
        DECLARE view_name text;
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_constraint
            WHERE conrelid='public.lesson_score_results'::regclass
              AND conname='fk_lesson_score_result_v2_score_owner'
              AND condeferrable AND condeferred
          ) THEN
            RAISE EXCEPTION 'DTS_V2_SCORE_OWNER_FK_NOT_DEFERRED';
          END IF;
          FOREACH function_name IN ARRAY ARRAY[
            'score_projection_config_snapshot_v2',
            'teacher_score_projection_vector_v2',
            'rebuild_lesson_score_result_v2',
            'rebuild_teacher_score_and_qualification_v2'
          ] LOOP
            IF NOT EXISTS (
              SELECT 1 FROM pg_catalog.pg_proc
              WHERE pronamespace='public'::regnamespace
                AND proname=function_name AND prosecdef
                AND proconfig=ARRAY['search_path=pg_catalog, public']::text[]
            ) THEN
              RAISE EXCEPTION
                'DTS_V2_SCORE_FUNCTION_CONTRACT_INVALID:%',function_name;
            END IF;
          END LOOP;
          FOREACH view_name IN ARRAY ARRAY[
            'teacher_scorecard_v1_compat_v1','teacher_scorecard_v2_v1',
            'teacher_lesson_score_v1_compat_v1',
            'teacher_lesson_score_v2_v1'
          ] LOOP
            IF to_regclass('public.'||view_name) IS NULL THEN
              RAISE EXCEPTION 'DTS_V2_SCORE_BRANCH_VIEW_MISSING:%',view_name;
            END IF;
          END LOOP;
          IF EXISTS (
            SELECT 1
            FROM pg_catalog.pg_depend dependency
            JOIN pg_catalog.pg_rewrite rewrite
              ON rewrite.oid=dependency.objid
            JOIN pg_catalog.pg_class branch
              ON branch.oid=rewrite.ev_class
            JOIN pg_catalog.pg_class referenced
              ON referenced.oid=dependency.refobjid
            WHERE branch.relnamespace='public'::regnamespace
              AND branch.relname=ANY(ARRAY[
                'teacher_scorecard_v1_compat_v1',
                'teacher_scorecard_v2_v1',
                'teacher_lesson_score_v1_compat_v1',
                'teacher_lesson_score_v2_v1'
              ])
              AND referenced.relnamespace='public'::regnamespace
              AND referenced.relname IN (
                'teacher_scorecard_current','teacher_lesson_score_current'
              )
          ) THEN
            RAISE EXCEPTION 'DTS_V2_SCORE_BRANCH_DEPENDS_ON_STABLE_VIEW';
          END IF;
          IF NOT has_function_privilege(
               'tit_growth_app',
               'public.rebuild_teacher_score_and_qualification_v2('
                 'text,jsonb,bigint)','EXECUTE'
             ) THEN
            RAISE EXCEPTION 'DTS_V2_SCORE_OUTBOX_EXECUTE_MISSING';
          END IF;
        END
        $score_projection_installed$;
        """
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    _assert_preconditions()
    _extend_score_ownership()
    _install_score_ownership_guards()
    _install_projection_helpers()
    _install_lesson_rebuild()
    _install_teacher_rebuild()
    _create_scorecard_views()
    _create_lesson_views()
    _install_acl_and_comments()
    _assert_installed_contract()


def _restore_rev78_ownership_guard() -> None:
    op.execute(
        r"""
        CREATE OR REPLACE FUNCTION
          public.dts_v2_lesson_score_result_ownership_guard()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE actor_name text := COALESCE(
          NULLIF(current_setting('role',true),'none'),session_user
        );
        DECLARE old_is_v2 boolean;
        DECLARE new_is_v2 boolean := NEW.v2_source_region IS NOT NULL;
        BEGIN
          IF actor_name IN (
               'tit_growth_app','tit_dts_ingest_runtime','tit_teacher_crud'
             ) AND new_is_v2 THEN
            RAISE EXCEPTION
              'DTS_V2_LESSON_SCORE_RESULT_RUNTIME_ROUTE_INACTIVE'
              USING ERRCODE='42501';
          END IF;
          IF TG_OP='INSERT' THEN RETURN NEW; END IF;
          old_is_v2 := OLD.v2_source_region IS NOT NULL;
          IF NOT old_is_v2 AND new_is_v2 THEN
            RAISE EXCEPTION
              'DTS_V2_LESSON_SCORE_RESULT_LEGACY_OWNERSHIP_IMMUTABLE';
          END IF;
          IF old_is_v2 AND NOT new_is_v2 THEN
            RAISE EXCEPTION 'DTS_V2_LESSON_SCORE_RESULT_OWNERSHIP_REQUIRED';
          END IF;
          IF old_is_v2 AND ROW(
               NEW.v2_source_region,NEW.v2_source_appoint_id
             ) IS DISTINCT FROM ROW(
               OLD.v2_source_region,OLD.v2_source_appoint_id
             ) THEN
            RAISE EXCEPTION
              'DTS_V2_LESSON_SCORE_RESULT_COURSE_IMMUTABLE';
          END IF;
          IF old_is_v2
             AND NEW.v2_completion_participation_seq IS DISTINCT FROM
                 OLD.v2_completion_participation_seq
             AND NEW.projection_revision<>OLD.projection_revision+1 THEN
            RAISE EXCEPTION
              'DTS_V2_LESSON_SCORE_RESULT_TRANSFER_REVISION_INVALID';
          END IF;
          RETURN NEW;
        END
        $function$;
        REVOKE ALL ON FUNCTION
          public.dts_v2_lesson_score_result_ownership_guard()
        FROM PUBLIC;
        """
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        r"""
        LOCK TABLE public.lesson_score_results,public.score_accounts,
          public.score_component_accounts,public.teacher_qualifications,
          public.teachers IN ACCESS EXCLUSIVE MODE;
        DO $score_projection_downgrade_guard$
        BEGIN
          IF EXISTS (
               SELECT 1 FROM public.lesson_score_results
               WHERE v2_teacher_id IS NOT NULL
                  OR v2_projection_generation IS NOT NULL
             ) OR EXISTS (
               SELECT 1 FROM public.score_accounts
               WHERE payload ? 'projection_generation'
             ) OR EXISTS (
               SELECT 1 FROM public.score_component_accounts
               WHERE payload ? 'projection_generation'
             ) OR EXISTS (
               SELECT 1 FROM public.teacher_qualifications
               WHERE gate_results ? 'projection_generation'
             ) OR EXISTS (
               SELECT 1 FROM public.teachers
               WHERE payload ? 'projection_generation'
             ) THEN
            RAISE EXCEPTION
              'refusing score projection downgrade: v2 serving facts exist';
          END IF;
        END
        $score_projection_downgrade_guard$;

        DROP VIEW public.teacher_lesson_score_v2_v1;
        DROP VIEW public.teacher_lesson_score_v1_compat_v1;
        DROP VIEW public.teacher_scorecard_v2_v1;
        DROP VIEW public.teacher_scorecard_v1_compat_v1;
        DROP FUNCTION public.rebuild_teacher_score_and_qualification_v2(
          text,jsonb,bigint
        );
        DROP FUNCTION public.rebuild_lesson_score_result_v2(
          text,text,bigint
        );
        DROP FUNCTION public.teacher_score_projection_vector_v2(text);
        DROP FUNCTION public.score_projection_config_snapshot_v2();
        DROP FUNCTION public.dts_v2_assert_lesson_score_course(text,text);
        ALTER FUNCTION
          public.dts_v2_assert_lesson_score_course_rev78(text,text)
          RENAME TO dts_v2_assert_lesson_score_course;
        """
    )
    _restore_rev78_ownership_guard()
    op.drop_constraint(
        "fk_lesson_score_result_v2_score_owner",
        "lesson_score_results",
        type_="foreignkey",
        schema="public",
    )
    op.drop_constraint(
        "ck_lesson_score_result_v2_ownership",
        "lesson_score_results",
        type_="check",
        schema="public",
    )
    op.create_check_constraint(
        "ck_lesson_score_result_v2_ownership",
        "lesson_score_results",
        "(v2_source_region IS NULL "
        "AND v2_source_appoint_id IS NULL "
        "AND v2_completion_participation_seq IS NULL) OR "
        "(v2_source_region IN ('dom','ovs') "
        "AND v2_source_appoint_id IS NOT NULL "
        "AND v2_completion_participation_seq >= 1)",
        schema="public",
    )
    op.create_foreign_key(
        "fk_lesson_score_result_v2_participation",
        "lesson_score_results",
        "source_course_participations",
        [
            "v2_source_region",
            "v2_source_appoint_id",
            "v2_completion_participation_seq",
        ],
        ["source_region", "source_appoint_id", "participation_seq"],
        source_schema="public",
        referent_schema="public",
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )
    op.drop_column(
        "lesson_score_results", "v2_projection_generation", schema="public"
    )
    op.drop_column(
        "lesson_score_results", "v2_teacher_id", schema="public"
    )
    op.drop_constraint(
        "uq_source_course_participation_score_owner_v2",
        "source_course_participations",
        type_="unique",
        schema="public",
    )
