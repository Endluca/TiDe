"""close the DTS v2 completion-ownership correction transaction.

Revision ID: 20260822_90_completion_correction
Revises: 20260822_89_teacher_materializer
Create Date: 2026-08-22

The migration does not switch a read route.  It gives the domain transaction
the sole mode-aware Case/pointer command and gives the operations API one
request-hashed correction command.  Every approved correction, score reversal
and COURSE/PARTICIPATION Outbox event is committed or rolled back together.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260822_90_completion_correction"
down_revision: Union[str, None] = "20260822_89_teacher_materializer"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


DOMAIN_ROLE = "tit_growth_app"
OUTBOX_ROLE = "tit_growth_app"
APP_ROLE = "tit_growth_app"


def _preflight_and_expand_pointer() -> None:
    op.create_table(
        "completion_correction_pointer_upgrade_archive",
        sa.Column("source_region", sa.String(length=8), nullable=False),
        sa.Column("source_appoint_id", sa.String(length=512), nullable=False),
        sa.Column("prior_case_id", sa.String(length=768), nullable=False),
        sa.Column("prior_course_row_version", sa.BigInteger(), nullable=False),
        sa.Column(
            "archived_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint(
            "source_region",
            "source_appoint_id",
            name="pk_completion_correction_pointer_upgrade_archive",
        ),
        sa.CheckConstraint(
            "source_region IN ('dom','ovs') AND prior_course_row_version>=1",
            name="ck_completion_correction_pointer_archive_shape",
        ),
        schema="public",
        comment=(
            "Owner-only rollback evidence for compatibility pointers cleared "
            "when rev90 establishes mode-aware Case ownership."
        ),
    )
    op.execute(
        r"""
        DO $completion_correction_preflight$
        DECLARE required_relation text;
        DECLARE mode_value text;
        BEGIN
          FOREACH required_relation IN ARRAY ARRAY[
            'source_courses','source_course_participations',
            'dts_source_rows','dts_source_row_versions',
            'domain_aggregate_revisions','outbox_events','ops_cases',
            'ops_decisions','audit_events','score_entries',
            'lesson_score_component_settlements',
            'course_favorite_observations','course_favorite_attributions',
            'lesson_score_results','teachers'
          ] LOOP
            IF to_regclass('public.' || required_relation) IS NULL THEN
              RAISE EXCEPTION
                'DTS_V2_COMPLETION_CORRECTION_PREREQUISITE_MISSING:%',
                required_relation;
            END IF;
          END LOOP;
          IF to_regprocedure(
               'public.publish_domain_aggregate_revision_v2(text,jsonb,jsonb,text,jsonb,bigint,jsonb,text,jsonb)'
             ) IS NULL
             OR to_regprocedure(
               'public.rebuild_lesson_score_result_v2(text,text,bigint)'
             ) IS NULL
             OR to_regprocedure(
               'public.rebuild_teacher_score_and_qualification_v2(text,jsonb,bigint)'
             ) IS NULL
             OR to_regprocedure(
               'public.materialize_favorite_observation_v2(text,text,text,text,text,text,integer,timestamp with time zone,text,bigint,text)'
             ) IS NULL
             OR to_regprocedure(
               'public.reconcile_favorite_attribution_v2(text,text,text,text,bigint,text,text)'
             ) IS NULL
             OR to_regrole('tit_growth_app') IS NULL
             OR to_regrole('tit_growth_app') IS NULL THEN
            RAISE EXCEPTION
              'DTS_V2_COMPLETION_CORRECTION_PREREQUISITE_MISSING';
          END IF;
          IF to_regprocedure(
               'public.reconcile_completion_conflict_case_v2(text,text,bigint,text)'
             ) IS NOT NULL
             OR to_regprocedure(
               'public.apply_completion_correction_decision_v2(jsonb,text)'
             ) IS NOT NULL THEN
            RAISE EXCEPTION 'DTS_V2_COMPLETION_CORRECTION_ALREADY_INSTALLED';
          END IF;

          SELECT mode INTO mode_value FROM public.dts_pipeline_control
          WHERE control_id='PRIMARY';
          IF NOT FOUND THEN
            IF EXISTS (SELECT 1 FROM public.source_courses)
               OR EXISTS (
                 SELECT 1 FROM public.ops_cases
                 WHERE case_type='COURSE_COMPLETION_CORRECTION'
               ) THEN
              RAISE EXCEPTION 'DTS_V2_PIPELINE_CONTROL_PRIMARY_REQUIRED';
            END IF;
            -- A fresh schema is intentionally not broker-bootstrapped by an
            -- Alembic revision.  With no course/case evidence there is
            -- nothing to convert, so use only the compatibility branch of
            -- this schema preflight; runtime commands still require PRIMARY.
            mode_value := 'V1_COMPAT_DUAL_CAPTURE';
          END IF;
          IF mode_value='V1_COMPAT_DUAL_CAPTURE' THEN
            IF EXISTS (
              SELECT 1 FROM public.ops_cases
              WHERE case_type='COURSE_COMPLETION_CORRECTION'
            ) THEN
              RAISE EXCEPTION
                'DTS_V2_COMPAT_VISIBLE_COMPLETION_CASE_REQUIRES_REVIEW';
            END IF;
            -- Older shadow code wrote a planned identifier into the course
            -- even though no visible Case existed.  Clear only that invalid
            -- compatibility pointer; row_version preserves the mutation.
            INSERT INTO public.completion_correction_pointer_upgrade_archive(
              source_region,source_appoint_id,prior_case_id,
              prior_course_row_version
            ) SELECT source_region,source_appoint_id,
                completion_conflict_case_id,row_version
              FROM public.source_courses
              WHERE completion_conflict_case_id IS NOT NULL;
            UPDATE public.source_courses
            SET completion_conflict_case_id=NULL,
                row_version=row_version+1,
                updated_at=clock_timestamp()
            WHERE completion_conflict_case_id IS NOT NULL;
          ELSE
            IF EXISTS (
              SELECT 1 FROM public.source_courses course
              LEFT JOIN public.ops_cases case_row
                ON case_row.case_id=course.completion_conflict_case_id
               AND case_row.case_type='COURSE_COMPLETION_CORRECTION'
               AND case_row.source_region=course.source_region
               AND case_row.source_appoint_id=course.source_appoint_id
              WHERE course.completion_conflict_case_id IS NOT NULL
                AND case_row.case_id IS NULL
            ) THEN
              RAISE EXCEPTION
                'DTS_V2_COMPLETION_CASE_POINTER_BACKFILL_REQUIRED';
            END IF;
          END IF;
        END
        $completion_correction_preflight$;

        REVOKE ALL ON TABLE
          public.completion_correction_pointer_upgrade_archive
        FROM PUBLIC,tit_growth_app,tit_dts_ingest_runtime,
          tit_teacher_crud,tide_support_ticket_owner;

        LOCK TABLE public.source_courses,public.source_course_participations,
          public.ops_cases,public.ops_decisions,
          public.domain_aggregate_revisions,public.outbox_events
        IN SHARE ROW EXCLUSIVE MODE;
        """
    )
    op.alter_column(
        "source_courses",
        "completion_conflict_case_id",
        existing_type=sa.String(length=160),
        type_=sa.String(length=768),
        existing_nullable=True,
        schema="public",
    )
    op.create_foreign_key(
        "fk_source_course_completion_conflict_case_v2",
        "source_courses",
        "ops_cases",
        ["completion_conflict_case_id"],
        ["case_id"],
        source_schema="public",
        referent_schema="public",
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )


def _install_case_guard() -> None:
    # tit_growth_app keeps its existing Ops Case CRUD grant.  SECURITY DEFINER
    # commands run as the table owner; direct application DML remains limited
    # to the explicit manual-review transitions below.
    op.execute(
        rf"""
        CREATE OR REPLACE FUNCTION public.guard_ops_cases_v2()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE actor_name text:=current_user;
        DECLARE table_owner text;
        DECLARE has_completed_recovery boolean;
        DECLARE has_completion_decision boolean;
        BEGIN
          SELECT pg_get_userbyid(relowner) INTO table_owner
          FROM pg_class WHERE oid='public.ops_cases'::regclass;
          IF TG_OP='DELETE' THEN
            RAISE EXCEPTION 'OPS_CASE_APPEND_ONLY' USING ERRCODE='42501';
          END IF;
          IF NEW.case_type NOT IN (
            'COURSE_COMPLETION_CORRECTION','DTS_DIRTY_KEY_DEAD',
            'DTS_SOURCE_CONFLICT','FAVORITE_OBSERVATION_DEAD',
            'TASK_MATERIALIZATION_DEAD','DOWNSTREAM_PROJECTION_DEAD'
          ) THEN
            RETURN NEW;
          END IF;
          IF TG_OP='INSERT' THEN
            IF actor_name<>table_owner
               OR NEW.case_revision<>1 OR NEW.row_version<>1
               OR NEW.recovery_evidence_count<>0
               OR NEW.last_recovery_event_id IS NOT NULL
               OR NEW.last_recovery_count IS NOT NULL
               OR NEW.last_recovered_at IS NOT NULL THEN
              RAISE EXCEPTION 'DTS_V2_PROTECTED_CASE_DIRECT_WRITE_DENIED'
                USING ERRCODE='42501';
            END IF;
            RETURN NEW;
          END IF;
          IF NEW.case_id IS DISTINCT FROM OLD.case_id
             OR NEW.case_type IS DISTINCT FROM OLD.case_type
             OR NEW.source_ref IS DISTINCT FROM OLD.source_ref
             OR NEW.source_region IS DISTINCT FROM OLD.source_region
             OR NEW.source_appoint_id IS DISTINCT FROM OLD.source_appoint_id
             OR NEW.task_id IS DISTINCT FROM OLD.task_id
             OR NEW.priority IS DISTINCT FROM OLD.priority
             OR NEW.external_action_status IS DISTINCT FROM
                  OLD.external_action_status
             OR NEW.created_at IS DISTINCT FROM OLD.created_at
             OR (NEW.teacher_id IS DISTINCT FROM OLD.teacher_id
                 AND NOT (OLD.teacher_id IS NULL
                          AND NEW.teacher_id IS NOT NULL)) THEN
            RAISE EXCEPTION 'DTS_V2_PROTECTED_CASE_IMMUTABLE'
              USING ERRCODE='23514';
          END IF;
          IF NEW.row_version IS DISTINCT FROM OLD.row_version+1
             OR NEW.case_revision NOT IN (
                  OLD.case_revision,OLD.case_revision+1
                )
             OR NEW.recovery_evidence_count NOT IN (
                  OLD.recovery_evidence_count,
                  OLD.recovery_evidence_count+1
                ) THEN
            RAISE EXCEPTION 'DTS_V2_PROTECTED_CASE_VERSION_INVALID'
              USING ERRCODE='23514';
          END IF;
          IF actor_name=table_owner THEN
            RETURN NEW;
          END IF;
          IF actor_name='{APP_ROLE}' THEN
            IF OLD.case_type='COURSE_COMPLETION_CORRECTION'
               AND OLD.status IN ('OPEN','IN_REVIEW')
               AND NEW.status='RESOLVED'
               AND NEW.case_revision=OLD.case_revision
               AND NEW.teacher_id IS NOT DISTINCT FROM OLD.teacher_id
               AND NEW.source_reason IS DISTINCT FROM OLD.source_reason
               AND NEW.evidence_fingerprint IS NOT DISTINCT FROM
                    OLD.evidence_fingerprint
               AND NEW.recovery_evidence_count=
                    OLD.recovery_evidence_count
               AND NEW.last_recovery_event_id IS NOT DISTINCT FROM
                    OLD.last_recovery_event_id
               AND NEW.last_recovery_count IS NOT DISTINCT FROM
                    OLD.last_recovery_count
               AND NEW.last_recovered_at IS NOT DISTINCT FROM
                    OLD.last_recovered_at
               AND NEW.updated_at>OLD.updated_at THEN
              SELECT EXISTS (
                SELECT 1 FROM public.ops_decisions decision_row
                WHERE decision_row.case_id=OLD.case_id
                  AND decision_row.expected_case_revision=OLD.case_revision
                  AND decision_row.expected_conflict_fingerprint=
                        OLD.evidence_fingerprint
                  AND decision_row.downstream_projection_status='PENDING'
                  AND decision_row.decision_id=
                        NEW.payload#>>'{{resolution,decision_id}}'
              ) INTO has_completion_decision;
              IF has_completion_decision THEN RETURN NEW; END IF;
            END IF;
            IF NEW.case_revision IS DISTINCT FROM OLD.case_revision
               OR NEW.teacher_id IS DISTINCT FROM OLD.teacher_id
               OR NEW.source_reason IS DISTINCT FROM OLD.source_reason
               OR NEW.payload IS DISTINCT FROM OLD.payload
               OR NEW.evidence_fingerprint IS DISTINCT FROM
                    OLD.evidence_fingerprint
               OR NEW.recovery_evidence_count IS DISTINCT FROM
                    OLD.recovery_evidence_count
               OR NEW.last_recovery_event_id IS DISTINCT FROM
                    OLD.last_recovery_event_id
               OR NEW.last_recovery_count IS DISTINCT FROM
                    OLD.last_recovery_count
               OR NEW.last_recovered_at IS DISTINCT FROM
                    OLD.last_recovered_at
               OR NEW.updated_at<=OLD.updated_at THEN
              RAISE EXCEPTION 'DTS_V2_PROTECTED_CASE_DIRECT_WRITE_DENIED'
                USING ERRCODE='42501';
            END IF;
            IF OLD.status='OPEN' AND NEW.status='IN_REVIEW' THEN RETURN NEW; END IF;
            IF OLD.status='IN_REVIEW' AND NEW.status='RESOLVED' THEN
              SELECT EXISTS (
                SELECT 1 FROM public.ops_case_recovery_events
                WHERE case_id=OLD.case_id AND work_status='PUBLISHED'
              ) INTO has_completed_recovery;
              IF has_completed_recovery THEN RETURN NEW; END IF;
            END IF;
          END IF;
          RAISE EXCEPTION 'DTS_V2_PROTECTED_CASE_DIRECT_WRITE_DENIED'
            USING ERRCODE='42501';
        END
        $function$;
        """
    )


def _install_state_helpers() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.completion_correction_source_coverage_v2(
          p_source_region text,p_source_appoint_id text,
          p_source_revision bigint,p_decision_id text,
          p_correction jsonb
        ) RETURNS jsonb
        LANGUAGE plpgsql
        STABLE
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE version_row public.dts_source_row_versions%ROWTYPE;
        DECLARE current_row public.dts_source_rows%ROWTYPE;
        DECLARE identity jsonb;
        DECLARE fingerprint text;
        BEGIN
          SELECT * INTO version_row FROM public.dts_source_row_versions
          WHERE source_region=p_source_region
            AND source_table=p_source_region || '_appoint'
            AND source_key=p_source_appoint_id
            AND source_row_revision=p_source_revision FOR SHARE;
          SELECT * INTO current_row FROM public.dts_source_rows
          WHERE source_region=p_source_region
            AND source_table=p_source_region || '_appoint'
            AND source_key=p_source_appoint_id FOR SHARE;
          IF version_row.source_row_revision IS NULL
             OR current_row.source_row_revision IS DISTINCT FROM
                  p_source_revision
             OR current_row.provenance_state<>'V2_CONFIRMED'
             OR current_row.source_payload_hash IS DISTINCT FROM
                  version_row.protected_source_row_hash THEN
            RAISE EXCEPTION 'DTS_V2_COMPLETION_SOURCE_CURRENT_STALE'
              USING ERRCODE='40001';
          END IF;
          identity := jsonb_build_object(
            'source_region',p_source_region,
            'source_table',p_source_region || '_appoint',
            'source_key',p_source_appoint_id
          );
          fingerprint := public.dts_canonical_json_sha256_v1(
            jsonb_build_object(
              'protocol','dirty-source-v1','identity',identity,
              'revision',p_source_revision,
              'version_kind',version_row.version_kind,
              'operation',version_row.operation,
              'is_deleted',current_row.is_deleted,
              'protected_source_row_hash',
                version_row.protected_source_row_hash
            )
          );
          RETURN jsonb_build_object(
            'protocol_version','completion-correction-projection-v1',
            'decision_id',p_decision_id,
            'correction',p_correction,
            'trigger',jsonb_build_object(
              'input_kind','SOURCE_REVISION',
              'input_identity',identity,
              'input_revision',p_source_revision,
              'input_fingerprint',fingerprint,
              'source_payload_hash',current_row.source_payload_hash
            )
          );
        END
        $function$;

        CREATE FUNCTION public.completion_course_aggregate_state_v2(
          p_source_region text,p_source_appoint_id text
        ) RETURNS jsonb
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
          SELECT jsonb_build_object(
            'course',to_jsonb(course_row),
            'course_fact',CASE WHEN fact_row.source_region IS NULL
              THEN NULL ELSE to_jsonb(fact_row) - 'source_region' -
                   'source_appoint_id' - 'created_at' - 'updated_at' END,
            'labels',coalesce((SELECT jsonb_agg(to_jsonb(label_row)
              ORDER BY label_id_type,convert_to(label_id,'UTF8'))
              FROM (SELECT DISTINCT ON (label_id_type,label_id)
                label_id,label_id_type,label_name_snapshot
                FROM public.source_course_labels
                WHERE source_region=p_source_region
                  AND source_appoint_id=p_source_appoint_id
                  AND is_deleted=false
                ORDER BY label_id_type,label_id,create_time DESC NULLS LAST,
                  dt DESC NULLS LAST,source_row_revision DESC) label_row
            ),'[]'::jsonb),
            'complaints',coalesce((SELECT jsonb_agg(to_jsonb(complaint_row)
              ORDER BY source_complaint_id_type,
                convert_to(source_complaint_id,'UTF8'))
              FROM (SELECT source_complaint_id,source_complaint_id_type,
                is_valid,complaint_type,complaint_type_type,
                complaint_type_child,complaint_type_child_type,
                complaint_type_grandson,complaint_type_grandson_type,
                add_time,course_date,source_row_revision,
                category_l1_snapshot,category_l2_snapshot,
                category_l3_snapshot,evidence_status,evidence_error_code,
                complaint_rule_id,source_sha256,severity_rank
                FROM public.source_course_complaints
                WHERE source_region=p_source_region
                  AND source_appoint_id=p_source_appoint_id
                  AND is_deleted=false) complaint_row
            ),'[]'::jsonb),
            'participations',coalesce((SELECT jsonb_agg(to_jsonb(part_row)
              ORDER BY participation_seq)
              FROM (SELECT p.participation_seq,p.teacher_id,
                p.teacher_id_type,p.participation_status,
                p.participation_role,p.is_current,p.assigned_at,
                p.assigned_at_evidence_status,p.ended_at,
                p.absence_reason_detail,p.no_notice,p.source_deleted,
                p.assignment_source_row_revision,p.row_version,
                f.is_late,f.late_evidence_status,f.is_early,
                f.early_evidence_status,
                f.row_version AS participation_fact_row_version
                FROM public.source_course_participations p
                LEFT JOIN public.source_participation_fact_current f
                  ON f.source_region=p.source_region
                 AND f.source_appoint_id=p.source_appoint_id
                 AND f.participation_seq=p.participation_seq
                WHERE p.source_region=p_source_region
                  AND p.source_appoint_id=p_source_appoint_id) part_row
            ),'[]'::jsonb)
          )
          FROM (SELECT student_token,lesson_local_date,lesson_local_time,
             scheduled_start_at,end_time,source_status,current_teacher_id,
             current_teacher_id_type,current_participation_seq,is_peak,
             completion_teacher_id,completion_teacher_id_type,
             completion_participation_seq,completion_frozen_at,
             completion_end_time,completion_student_token,
             completion_is_peak,completion_lesson_local_date,
             completion_lesson_local_time,completion_source_revision,
             completion_conflict_status,completion_conflict_case_id,
             conflict_fingerprint,source_is_deleted,evidence_status,
             row_version
             FROM public.source_courses
             WHERE source_region=p_source_region
               AND source_appoint_id=p_source_appoint_id) course_row
          LEFT JOIN public.source_course_fact_current fact_row
            ON fact_row.source_region=p_source_region
           AND fact_row.source_appoint_id=p_source_appoint_id
        $function$;

        CREATE FUNCTION public.completion_participation_aggregate_state_v2(
          p_source_region text,p_source_appoint_id text,p_seq integer
        ) RETURNS jsonb
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
          SELECT jsonb_build_object('participation',to_jsonb(part_row))
          FROM (SELECT p.teacher_id,p.teacher_id_type,
            p.participation_status,p.participation_role,p.is_current,
            p.absence_reason_detail,p.no_notice,f.is_late,
            f.late_evidence_status,f.is_early,f.early_evidence_status,
            f.penalty_source_keys
            FROM public.source_course_participations p
            LEFT JOIN public.source_participation_fact_current f
              ON f.source_region=p.source_region
             AND f.source_appoint_id=p.source_appoint_id
             AND f.participation_seq=p.participation_seq
            WHERE p.source_region=p_source_region
              AND p.source_appoint_id=p_source_appoint_id
              AND p.participation_seq=p_seq) part_row
        $function$;

        REVOKE ALL ON FUNCTION
          public.completion_correction_source_coverage_v2(
            text,text,bigint,text,jsonb),
          public.completion_course_aggregate_state_v2(text,text),
          public.completion_participation_aggregate_state_v2(
            text,text,integer)
        FROM PUBLIC;
        """
    )


def _install_reconcile_command() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.reconcile_completion_conflict_case_v2(
          p_source_region text,p_source_appoint_id text,
          p_expected_aggregate_revision bigint,p_triggering_event_id text
        ) RETURNS jsonb
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE control_mode text;
        DECLARE aggregate_row public.domain_aggregate_revisions%ROWTYPE;
        DECLARE course_row public.source_courses%ROWTYPE;
        DECLARE case_row public.ops_cases%ROWTYPE;
        DECLARE conflict jsonb;
        DECLARE canonical_case_id_value text;
        DECLARE aggregate_id_value text;
        DECLARE expected_event_id text;
        DECLARE fingerprint text;
        DECLARE next_revision integer;
        DECLARE result_status text;
        DECLARE stored_teacher_id text;
        DECLARE evidence_document jsonb;
        DECLARE audit_document jsonb;
        BEGIN
          IF p_source_region NOT IN ('dom','ovs')
             OR p_source_appoint_id IS NULL OR btrim(p_source_appoint_id)=''
             OR p_expected_aggregate_revision<1
             OR p_triggering_event_id IS NULL
             OR length(p_triggering_event_id)>512 THEN
            RAISE EXCEPTION 'DTS_V2_COMPLETION_CONFLICT_REQUEST_INVALID'
              USING ERRCODE='22023';
          END IF;
          PERFORM pg_advisory_xact_lock_shared(
            hashtextextended('tit:dts-v2-cutover',0)
          );
          SELECT mode INTO control_mode FROM public.dts_pipeline_control
          WHERE control_id='PRIMARY' FOR SHARE;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'DTS_V2_PIPELINE_CONTROL_PRIMARY_REQUIRED'
              USING ERRCODE='55000';
          END IF;
          canonical_case_id_value := 'course-completion-correction:' ||
            p_source_region || ':' || p_source_appoint_id;
          aggregate_id_value := 'v2:COMPLETION_CONFLICT:' ||
            public.dts_canonical_json_sha256_v1(jsonb_build_object(
              'source_region',p_source_region,
              'source_appoint_id',p_source_appoint_id
            ));
          SELECT * INTO aggregate_row
          FROM public.domain_aggregate_revisions AS aggregate_revision
          WHERE aggregate_revision.aggregate_type='COMPLETION_CONFLICT'
            AND aggregate_revision.aggregate_id=aggregate_id_value FOR SHARE;
          IF NOT FOUND OR aggregate_row.revision IS DISTINCT FROM
               p_expected_aggregate_revision THEN
            RAISE EXCEPTION 'DTS_V2_COMPLETION_CONFLICT_AGGREGATE_STALE'
              USING ERRCODE='40001';
          END IF;
          expected_event_id := 'source_wide.changed.v2:' ||
            'COMPLETION_CONFLICT:' || aggregate_id_value || ':' ||
            aggregate_row.revision::text;
          IF p_triggering_event_id IS DISTINCT FROM expected_event_id
             OR NOT EXISTS (
               SELECT 1 FROM public.outbox_events event_row
               WHERE event_row.event_id=expected_event_id
                 AND event_row.aggregate_type='COMPLETION_CONFLICT'
                 AND event_row.aggregate_id=aggregate_id_value
                 AND event_row.event_type='source_wide.changed.v2'
                 AND event_row.payload->>'aggregate_revision'=
                       aggregate_row.revision::text
             ) THEN
            RAISE EXCEPTION 'DTS_V2_COMPLETION_CONFLICT_EVENT_INVALID'
              USING ERRCODE='23514';
          END IF;
          IF aggregate_row.aggregate_state <> jsonb_build_object(
               'completion_conflict',
               aggregate_row.aggregate_state->'completion_conflict'
             ) OR jsonb_typeof(
               aggregate_row.aggregate_state->'completion_conflict'
             )<>'object' THEN
            RAISE EXCEPTION 'DTS_V2_COMPLETION_CONFLICT_STATE_INVALID'
              USING ERRCODE='23514';
          END IF;
          conflict := aggregate_row.aggregate_state->'completion_conflict';
          SELECT * INTO course_row FROM public.source_courses
          WHERE source_region=p_source_region
            AND source_appoint_id=p_source_appoint_id FOR UPDATE;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'DTS_V2_COMPLETION_CONFLICT_COURSE_MISSING'
              USING ERRCODE='23503';
          END IF;
          IF conflict->>'status'<>'PENDING' THEN
            IF course_row.completion_conflict_status='PENDING' THEN
              RAISE EXCEPTION 'DTS_V2_COMPLETION_CONFLICT_STATE_STALE'
                USING ERRCODE='40001';
            END IF;
            RETURN jsonb_build_object('outcome','NOT_PENDING','case_id',NULL);
          END IF;
          fingerprint := conflict->>'fingerprint';
          IF conflict->>'case_id' IS DISTINCT FROM canonical_case_id_value
             OR fingerprint !~ '^[0-9a-f]{64}$'
             OR course_row.completion_conflict_status<>'PENDING'
             OR course_row.conflict_fingerprint IS DISTINCT FROM fingerprint
             OR conflict->>'completion_teacher_id' IS DISTINCT FROM
                  course_row.completion_teacher_id
             OR conflict->>'completion_teacher_id_type' IS DISTINCT FROM
                  course_row.completion_teacher_id_type
             OR (conflict->>'completion_participation_seq')::integer
                  IS DISTINCT FROM course_row.completion_participation_seq THEN
            RAISE EXCEPTION 'DTS_V2_COMPLETION_CONFLICT_STATE_STALE'
              USING ERRCODE='40001';
          END IF;

          IF control_mode='V1_COMPAT_DUAL_CAPTURE' THEN
            IF course_row.completion_conflict_case_id IS NOT NULL THEN
              RAISE EXCEPTION 'DTS_V2_COMPAT_COMPLETION_POINTER_FORBIDDEN'
                USING ERRCODE='23514';
            END IF;
            RETURN jsonb_build_object('outcome','SHADOW_ONLY','case_id',NULL);
          ELSIF control_mode='ROLLED_BACK' THEN
            IF course_row.completion_conflict_case_id IS NOT NULL AND (
              course_row.completion_conflict_case_id<>canonical_case_id_value
              OR NOT EXISTS (
                SELECT 1 FROM public.ops_cases existing_case
                WHERE existing_case.case_id=canonical_case_id_value
                  AND existing_case.case_type=
                        'COURSE_COMPLETION_CORRECTION'
                  AND existing_case.source_region=p_source_region
                  AND existing_case.source_appoint_id=p_source_appoint_id
              )
            ) THEN
              RAISE EXCEPTION 'DTS_V2_ROLLBACK_COMPLETION_POINTER_INVALID'
                USING ERRCODE='23514';
            END IF;
            RETURN jsonb_build_object(
              'outcome','SHADOW_ONLY',
              'case_id',course_row.completion_conflict_case_id
            );
          ELSIF control_mode<>'V2_PRIMARY' THEN
            RAISE EXCEPTION 'DTS_V2_PIPELINE_MODE_INVALID'
              USING ERRCODE='55000';
          END IF;

          stored_teacher_id := CASE WHEN EXISTS (
            SELECT 1 FROM public.teachers teacher_row
            WHERE teacher_row.teacher_id=course_row.completion_teacher_id
          ) THEN course_row.completion_teacher_id ELSE NULL END;
          evidence_document := jsonb_build_object(
            'protocol_version','completion-conflict-case-v2',
            'case_id',canonical_case_id_value,'case_revision',1,
            'source_region',p_source_region,
            'source_appoint_id',p_source_appoint_id,
            'conflict_fingerprint',fingerprint,
            'latest_source_revision',course_row.last_applied_source_revision,
            'source_position',course_row.last_applied_event_position,
            'aggregate_revision',aggregate_row.revision,
            'aggregate_id',aggregate_id_value,
            'frozen_completion',jsonb_build_object(
              'participation_seq',course_row.completion_participation_seq,
              'teacher_id',course_row.completion_teacher_id,
              'teacher_id_type',course_row.completion_teacher_id_type,
              'end_time',course_row.completion_end_time,
              'student_token',course_row.completion_student_token,
              'lesson_local_date',course_row.completion_lesson_local_date,
              'lesson_local_time',course_row.completion_lesson_local_time,
              'is_peak',course_row.completion_is_peak
            )
          );
          SELECT * INTO case_row FROM public.ops_cases
          WHERE ops_cases.case_id=canonical_case_id_value FOR UPDATE;
          IF NOT FOUND THEN
            INSERT INTO public.ops_cases(
              case_id,case_type,teacher_id,task_id,priority,status,
              source_reason,external_action_status,created_at,payload,
              updated_at,source_ref,source_region,source_appoint_id,
              case_revision,row_version,evidence_fingerprint,
              recovery_evidence_count
            ) VALUES (
              canonical_case_id_value,'COURSE_COMPLETION_CORRECTION',
              stored_teacher_id,NULL,'P1','OPEN','COMPLETION_CONFLICT',
              'NOT_REQUESTED',transaction_timestamp(),evidence_document,
              transaction_timestamp(),canonical_case_id_value,p_source_region,
              p_source_appoint_id,1,1,fingerprint,0
            );
            result_status := 'CREATED'; next_revision := 1;
          ELSE
            IF case_row.case_type<>'COURSE_COMPLETION_CORRECTION'
               OR case_row.source_ref<>canonical_case_id_value
               OR case_row.source_region<>p_source_region
               OR case_row.source_appoint_id<>p_source_appoint_id THEN
              RAISE EXCEPTION 'DTS_V2_COMPLETION_CASE_IDENTITY_CONFLICT'
                USING ERRCODE='23505';
            END IF;
            IF case_row.evidence_fingerprint=fingerprint THEN
              result_status := 'UNCHANGED';
              next_revision := case_row.case_revision;
            ELSE
              next_revision := case_row.case_revision+1;
              UPDATE public.ops_cases SET
                teacher_id=coalesce(case_row.teacher_id,stored_teacher_id),
                status=CASE WHEN case_row.status='IN_REVIEW'
                            THEN 'IN_REVIEW' ELSE 'OPEN' END,
                source_reason='COMPLETION_CONFLICT',
                case_revision=next_revision,row_version=row_version+1,
                evidence_fingerprint=fingerprint,
                payload=evidence_document || jsonb_build_object(
                  'case_revision',next_revision
                ),updated_at=transaction_timestamp()
              WHERE ops_cases.case_id=canonical_case_id_value;
              result_status := 'UPDATED';
            END IF;
          END IF;
          IF course_row.completion_conflict_case_id IS DISTINCT FROM
               canonical_case_id_value THEN
            UPDATE public.source_courses
            SET completion_conflict_case_id=canonical_case_id_value,
                row_version=row_version+1,updated_at=clock_timestamp()
            WHERE source_region=p_source_region
              AND source_appoint_id=p_source_appoint_id;
          END IF;
          IF result_status<>'UNCHANGED' THEN
            audit_document := jsonb_build_object(
              'protocol_version','completion-conflict-case-v2',
              'case_id',canonical_case_id_value,'case_revision',next_revision,
              'conflict_fingerprint',fingerprint,
              'aggregate_revision',aggregate_row.revision,
              'result',result_status
            );
            INSERT INTO public.audit_events(
              event_id,event_type,teacher_id,task_id,case_id,occurred_at,
              actor_type,payload_hash,payload
            ) VALUES (
              'audit:completion-case:v2:' ||
                public.dts_canonical_json_sha256_v1(jsonb_build_object(
                  'case_id',canonical_case_id_value,'case_revision',next_revision
                )),
              'DTS_V2_COMPLETION_CONFLICT_CASE_RECONCILED',stored_teacher_id,
              NULL,canonical_case_id_value,transaction_timestamp(),
              'DTS_V2_DOMAIN_PROJECTOR',
              public.dts_canonical_json_sha256_v1(audit_document),
              audit_document
            ) ON CONFLICT (event_id) DO NOTHING;
          END IF;
          -- The course pointer also schedules the existing deferred score
          -- ownership assertion.  Flush every pending constraint while the
          -- SECURITY DEFINER owner is still active; otherwise PostgreSQL
          -- would execute the assertion after returning to the restricted
          -- caller at COMMIT and incorrectly require direct helper EXECUTE.
          SET CONSTRAINTS ALL IMMEDIATE;
          SET CONSTRAINTS ALL DEFERRED;
          RETURN jsonb_build_object(
            'outcome',result_status,'case_id',canonical_case_id_value
          );
        END
        $function$;

        REVOKE ALL ON FUNCTION
          public.reconcile_completion_conflict_case_v2(
            text,text,bigint,text)
        FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION
          public.reconcile_completion_conflict_case_v2(
            text,text,bigint,text)
        TO tit_growth_app;
        """
    )


def _install_score_reversal_helper() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.reverse_lesson_components_for_correction_v2(
          p_source_region text,p_source_appoint_id text,
          p_reverse_all boolean,p_reverse_peak boolean,
          p_projection_generation bigint,p_decision_id text
        ) RETURNS jsonb
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE settlement public.lesson_score_component_settlements%ROWTYPE;
        DECLARE original public.score_entries%ROWTYPE;
        DECLARE existing public.score_entries%ROWTYPE;
        DECLARE reversal_key text;
        DECLARE reversal_id text;
        DECLARE score_ids text[] := ARRAY[]::text[];
        BEGIN
          IF NOT p_reverse_all AND NOT p_reverse_peak THEN
            RETURN jsonb_build_object(
              'reversed_count',0,'score_entry_ids','[]'::jsonb
            );
          END IF;
          FOR settlement IN
            SELECT * FROM public.lesson_score_component_settlements
            WHERE source_region=p_source_region
              AND source_appoint_id=p_source_appoint_id
              AND status='AWARDED'
              AND (p_reverse_all OR component_code='PEAK_COMPLETED')
            ORDER BY completion_participation_seq,component_code
            FOR UPDATE
          LOOP
            SELECT * INTO original FROM public.score_entries
            WHERE score_entry_id=settlement.current_award_score_entry_id
            FOR SHARE;
            IF NOT FOUND OR original.teacher_id<>settlement.teacher_id
               OR original.delta_score::numeric<>
                    settlement.component_score::numeric THEN
              RAISE EXCEPTION 'DTS_V2_COMPLETION_SCORE_AWARD_INVALID'
                USING ERRCODE='23514';
            END IF;
            reversal_key := 'lesson-reversal:' || original.score_entry_id;
            reversal_id := 'v2score-' || encode(
              sha256(convert_to(reversal_key,'UTF8')),'hex'
            );
            INSERT INTO public.score_entries(
              score_entry_id,camp_enrollment_id,lesson_id,source_region,
              source_appoint_id,participation_seq,teacher_id,dimension,
              entry_type,delta_score,reason_code,evidence_status,
              score_rule_version,occurred_at,recorded_at,
              reversal_of_score_entry_id,task_assignment_id,
              projection_origin,materialized_by_run_id,
              projection_generation,idempotency_key,payload
            ) VALUES (
              reversal_id,original.camp_enrollment_id,
              p_source_appoint_id,p_source_region,p_source_appoint_id,
              settlement.completion_participation_seq,
              settlement.teacher_id,original.dimension,
              'LESSON_COMPONENT_REVERSAL',-settlement.component_score,
              settlement.component_code,'CONFIRMED',
              settlement.score_rule_version,transaction_timestamp(),
              transaction_timestamp(),original.score_entry_id,NULL,
              'V2_LIVE',NULL,p_projection_generation,reversal_key,
              jsonb_build_object(
                'settlement_contract','lesson-score-component-v2',
                'entry_kind','REVERSAL',
                'source_region',p_source_region,
                'source_appoint_id',p_source_appoint_id,
                'completion_participation_seq',
                  settlement.completion_participation_seq,
                'component_code',settlement.component_code,
                'award_generation',settlement.award_generation,
                'evidence_fingerprint',settlement.evidence_fingerprint,
                'correction_decision_id',p_decision_id
              )
            ) ON CONFLICT (idempotency_key) DO NOTHING;
            SELECT * INTO existing FROM public.score_entries
            WHERE idempotency_key=reversal_key FOR SHARE;
            IF NOT FOUND OR existing.score_entry_id<>reversal_id
               OR existing.reversal_of_score_entry_id<>
                    original.score_entry_id
               OR existing.teacher_id<>settlement.teacher_id
               OR existing.delta_score::numeric<>
                    -settlement.component_score::numeric
               OR existing.reason_code<>settlement.component_code
               OR existing.projection_generation IS DISTINCT FROM
                    p_projection_generation THEN
              RAISE EXCEPTION 'DTS_V2_COMPLETION_SCORE_REVERSAL_CONFLICT'
                USING ERRCODE='23505';
            END IF;
            UPDATE public.lesson_score_component_settlements SET
              status='REVERSED',current_award_score_entry_id=NULL,
              last_reversal_score_entry_id=reversal_id,
              row_version=row_version+1,reversed_at=transaction_timestamp(),
              updated_at=transaction_timestamp()
            WHERE source_region=settlement.source_region
              AND source_appoint_id=settlement.source_appoint_id
              AND completion_participation_seq=
                    settlement.completion_participation_seq
              AND component_code=settlement.component_code
              AND status='AWARDED';
            score_ids := array_append(score_ids,reversal_id);
          END LOOP;
          RETURN jsonb_build_object(
            'reversed_count',cardinality(score_ids),
            'score_entry_ids',to_jsonb(score_ids)
          );
        END
        $function$;
        REVOKE ALL ON FUNCTION
          public.reverse_lesson_components_for_correction_v2(
            text,text,boolean,boolean,bigint,text)
        FROM PUBLIC;
        """
    )


def _install_apply_command() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.apply_completion_correction_decision_v2(
          p_request jsonb,p_expected_request_sha256 text
        ) RETURNS jsonb
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE request_sha text;
        DECLARE decision_id_value text;
        DECLARE case_id_value text;
        DECLARE decision_value text;
        DECLARE actor_id_value text;
        DECLARE reason_value text;
        DECLARE expected_case_revision_value integer;
        DECLARE expected_fingerprint text;
        DECLARE expected_source_revision_value bigint;
        DECLARE request_position jsonb;
        DECLARE target_seq integer;
        DECLARE snapshot jsonb;
        DECLARE control_mode text;
        DECLARE projection_generation_value bigint;
        DECLARE existing_decision public.ops_decisions%ROWTYPE;
        DECLARE case_row public.ops_cases%ROWTYPE;
        DECLARE course_row public.source_courses%ROWTYPE;
        DECLARE version_row public.dts_source_row_versions%ROWTYPE;
        DECLARE current_source public.dts_source_rows%ROWTYPE;
        DECLARE target_part public.source_course_participations%ROWTYPE;
        DECLARE old_completion_seq integer;
        DECLARE old_teacher_id text;
        DECLARE old_teacher_type text;
        DECLARE old_student_token text;
        DECLARE old_end_time timestamptz;
        DECLARE old_is_peak boolean;
        DECLARE new_completion_seq integer;
        DECLARE new_teacher_id text;
        DECLARE new_teacher_type text;
        DECLARE new_student_token text;
        DECLARE new_end_time timestamptz;
        DECLARE new_is_peak boolean;
        DECLARE affected_seqs integer[] := ARRAY[]::integer[];
        DECLARE affected_teachers text[] := ARRAY[]::text[];
        DECLARE active_observation record;
        DECLARE active_observation_found boolean := false;
        DECLARE appoint_id_type text;
        DECLARE coverage jsonb;
        DECLARE correction_payload jsonb;
        DECLARE aggregate_state jsonb;
        DECLARE publish_result jsonb;
        DECLARE event_ids text[] := ARRAY[]::text[];
        DECLARE event_id_value text;
        DECLARE conflict_status_value text;
        DECLARE result_document jsonb;
        DECLARE decision_payload jsonb;
        DECLARE audit_document jsonb;
        DECLARE score_reversal_result jsonb;
        DECLARE teacher_value text;
        BEGIN
          IF jsonb_typeof(p_request)<>'object'
             OR p_request<>jsonb_build_object(
               'protocol_version',p_request->'protocol_version',
               'decision_id',p_request->'decision_id',
               'case_id',p_request->'case_id',
               'decision',p_request->'decision',
               'actor_id',p_request->'actor_id',
               'reason',p_request->'reason',
               'expected_case_revision',p_request->'expected_case_revision',
               'expected_conflict_fingerprint',
                 p_request->'expected_conflict_fingerprint',
               'expected_source_revision',
                 p_request->'expected_source_revision',
               'expected_source_position',p_request->'expected_source_position',
               'target_participation_seq',
                 p_request->'target_participation_seq',
               'completion_snapshot',p_request->'completion_snapshot'
             )
             OR p_request->>'protocol_version'<>
                  'completion-correction-command-v1'
             OR p_expected_request_sha256 !~ '^[0-9a-f]{64}$' THEN
            RAISE EXCEPTION 'DTS_V2_COMPLETION_CORRECTION_REQUEST_INVALID'
              USING ERRCODE='22023';
          END IF;
          request_sha := public.dts_canonical_json_sha256_v1(p_request);
          IF request_sha IS DISTINCT FROM p_expected_request_sha256 THEN
            RAISE EXCEPTION 'DTS_V2_COMPLETION_CORRECTION_REQUEST_HASH_MISMATCH'
              USING ERRCODE='23514';
          END IF;
          decision_id_value := p_request->>'decision_id';
          case_id_value := p_request->>'case_id';
          decision_value := p_request->>'decision';
          actor_id_value := p_request->>'actor_id';
          reason_value := p_request->>'reason';
          expected_fingerprint :=
            p_request->>'expected_conflict_fingerprint';
          request_position := p_request->'expected_source_position';
          snapshot := p_request->'completion_snapshot';
          IF decision_id_value !~ '^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,159}$'
             OR case_id_value IS NULL OR length(case_id_value)>768
             OR decision_value NOT IN (
               'KEEP_FROZEN_COMPLETION','UPDATE_COMPLETION_SNAPSHOT',
               'TRANSFER_COMPLETION','VOID_COMPLETION'
             )
             OR actor_id_value IS NULL OR btrim(actor_id_value)=''
             OR actor_id_value<>btrim(actor_id_value)
             OR length(actor_id_value)>160
             OR reason_value IS NULL OR btrim(reason_value)=''
             OR length(reason_value)>2000
             OR jsonb_typeof(p_request->'expected_case_revision')<>'number'
             OR jsonb_typeof(p_request->'expected_source_revision')<>'number'
             OR (p_request->>'expected_case_revision') !~ '^[1-9][0-9]*$'
             OR (p_request->>'expected_source_revision') !~ '^[1-9][0-9]*$'
             OR expected_fingerprint !~ '^[0-9a-f]{64}$'
             OR public.dts_v2_source_position_valid(request_position)
                  IS DISTINCT FROM true THEN
            RAISE EXCEPTION 'DTS_V2_COMPLETION_CORRECTION_REQUEST_INVALID'
              USING ERRCODE='22023';
          END IF;
          expected_case_revision_value :=
            (p_request->>'expected_case_revision')::integer;
          expected_source_revision_value :=
            (p_request->>'expected_source_revision')::bigint;
          IF decision_value IN (
               'KEEP_FROZEN_COMPLETION','VOID_COMPLETION'
             ) THEN
            IF p_request->'target_participation_seq'<>'null'::jsonb
               OR snapshot<>'null'::jsonb THEN
              RAISE EXCEPTION 'DTS_V2_COMPLETION_CORRECTION_DECISION_SHAPE_INVALID'
                USING ERRCODE='22023';
            END IF;
          ELSE
            IF snapshot IS NULL OR jsonb_typeof(snapshot)<>'object'
               OR snapshot<>jsonb_build_object(
                 'teacher_id',snapshot->'teacher_id',
                 'teacher_id_type',snapshot->'teacher_id_type',
                 'status',snapshot->'status','end_time',snapshot->'end_time',
                 'student_token',snapshot->'student_token',
                 'lesson_local_date',snapshot->'lesson_local_date',
                 'lesson_local_time',snapshot->'lesson_local_time',
                 'is_peak',snapshot->'is_peak'
               )
               OR snapshot->>'status'<>'end'
               OR snapshot->>'teacher_id' IS NULL
               OR btrim(snapshot->>'teacher_id')=''
               OR snapshot->>'teacher_id_type' NOT IN ('NUMERIC','TEXT')
               OR NOT public.dts_v2_typed_id_valid(
                    snapshot->>'teacher_id_type',snapshot->>'teacher_id'
                  )
               OR jsonb_typeof(snapshot->'end_time') NOT IN ('string','null')
               OR jsonb_typeof(snapshot->'student_token') NOT IN ('string','null')
               OR jsonb_typeof(snapshot->'lesson_local_date') NOT IN ('string','null')
               OR jsonb_typeof(snapshot->'lesson_local_time') NOT IN ('string','null')
               OR jsonb_typeof(snapshot->'is_peak') NOT IN ('boolean','null') THEN
              RAISE EXCEPTION 'DTS_V2_COMPLETION_CORRECTION_DECISION_SHAPE_INVALID'
                USING ERRCODE='22023';
            END IF;
            IF decision_value='TRANSFER_COMPLETION' THEN
              IF jsonb_typeof(p_request->'target_participation_seq')<>'number'
                 OR (p_request->>'target_participation_seq') !~ '^[1-9][0-9]*$'
              THEN
                RAISE EXCEPTION 'DTS_V2_COMPLETION_CORRECTION_DECISION_SHAPE_INVALID'
                  USING ERRCODE='22023';
              END IF;
              target_seq := (p_request->>'target_participation_seq')::integer;
            ELSIF p_request->'target_participation_seq'<>'null'::jsonb THEN
              RAISE EXCEPTION 'DTS_V2_COMPLETION_CORRECTION_DECISION_SHAPE_INVALID'
                USING ERRCODE='22023';
            END IF;
          END IF;

          PERFORM pg_advisory_xact_lock(hashtextextended(
            'tit:completion-decision:' || decision_id_value,0
          ));
          SELECT * INTO existing_decision FROM public.ops_decisions
          WHERE ops_decisions.decision_id=decision_id_value FOR SHARE;
          IF FOUND THEN
            IF existing_decision.payload->>'request_sha256' IS DISTINCT FROM
                 request_sha
               OR existing_decision.payload->'request' IS DISTINCT FROM
                    p_request THEN
              RAISE EXCEPTION
                'DTS_V2_COMPLETION_CORRECTION_IDEMPOTENCY_CONFLICT'
                USING ERRCODE='23505';
            END IF;
            RETURN jsonb_set(
              existing_decision.payload->'result','{outcome}',
              to_jsonb('REPLAYED'::text),false
            );
          END IF;

          PERFORM pg_advisory_xact_lock_shared(
            hashtextextended('tit:dts-v2-cutover',0)
          );
          SELECT control.mode,control.projection_generation
          INTO control_mode,projection_generation_value
          FROM public.dts_pipeline_control AS control
          WHERE control.control_id='PRIMARY' FOR SHARE;
          IF NOT FOUND OR control_mode<>'V2_PRIMARY' THEN
            RAISE EXCEPTION 'CORRECTION_PROJECTION_MAINTENANCE'
              USING ERRCODE='55000';
          END IF;
          IF projection_generation_value IS NULL
             OR projection_generation_value<1 THEN
            RAISE EXCEPTION 'DTS_V2_PROJECTION_GENERATION_INVALID'
              USING ERRCODE='55000';
          END IF;
          PERFORM pg_advisory_xact_lock(544954,1);

          SELECT * INTO case_row FROM public.ops_cases
          WHERE case_id=case_id_value FOR UPDATE;
          IF NOT FOUND OR case_row.case_type<>
               'COURSE_COMPLETION_CORRECTION' THEN
            RAISE EXCEPTION 'STALE_CASE_REVISION' USING ERRCODE='40001';
          END IF;
          SELECT * INTO course_row FROM public.source_courses
          WHERE source_region=case_row.source_region
            AND source_appoint_id=case_row.source_appoint_id FOR UPDATE;
          IF NOT FOUND
             OR case_id_value<>'course-completion-correction:' ||
                  case_row.source_region || ':' || case_row.source_appoint_id
             OR course_row.completion_conflict_case_id<>case_id_value
             OR course_row.completion_conflict_status<>'PENDING'
             OR case_row.status NOT IN ('OPEN','IN_REVIEW')
             OR case_row.case_revision<>expected_case_revision_value
             OR case_row.evidence_fingerprint<>expected_fingerprint
             OR course_row.conflict_fingerprint<>expected_fingerprint
             OR course_row.last_applied_source_revision<>
                  expected_source_revision_value THEN
            RAISE EXCEPTION 'STALE_CASE_REVISION' USING ERRCODE='40001';
          END IF;
          SELECT * INTO version_row FROM public.dts_source_row_versions
          WHERE source_region=case_row.source_region
            AND source_table=case_row.source_region || '_appoint'
            AND source_key=case_row.source_appoint_id
            AND source_row_revision=expected_source_revision_value FOR SHARE;
          SELECT * INTO current_source FROM public.dts_source_rows
          WHERE source_region=case_row.source_region
            AND source_table=case_row.source_region || '_appoint'
            AND source_key=case_row.source_appoint_id FOR SHARE;
          IF version_row.source_row_revision IS NULL
             OR current_source.source_row_revision<>
                  expected_source_revision_value
             OR current_source.provenance_state<>'V2_CONFIRMED'
             OR version_row.source_position IS DISTINCT FROM request_position
             OR current_source.source_position_v2 IS DISTINCT FROM
                  request_position
             OR current_source.source_payload_hash<>
                  version_row.protected_source_row_hash THEN
            RAISE EXCEPTION 'STALE_CASE_REVISION' USING ERRCODE='40001';
          END IF;

          PERFORM 1 FROM public.source_course_participations
          WHERE source_region=case_row.source_region
            AND source_appoint_id=case_row.source_appoint_id
          ORDER BY participation_seq FOR UPDATE;
          old_completion_seq := course_row.completion_participation_seq;
          old_teacher_id := course_row.completion_teacher_id;
          old_teacher_type := course_row.completion_teacher_id_type;
          old_student_token := course_row.completion_student_token;
          old_end_time := course_row.completion_end_time;
          old_is_peak := course_row.completion_is_peak;
          new_completion_seq := old_completion_seq;
          new_teacher_id := old_teacher_id;
          new_teacher_type := old_teacher_type;
          new_student_token := old_student_token;
          new_end_time := old_end_time;
          new_is_peak := old_is_peak;
          IF decision_value='KEEP_FROZEN_COMPLETION' THEN
            IF old_completion_seq IS NULL THEN
              RAISE EXCEPTION 'DTS_V2_COMPLETION_KEEP_OWNER_MISSING'
                USING ERRCODE='23514';
            END IF;
            conflict_status_value := 'RESOLVED_KEEP';
          ELSIF decision_value='UPDATE_COMPLETION_SNAPSHOT' THEN
            IF old_completion_seq IS NULL
               OR snapshot->>'teacher_id'<>old_teacher_id
               OR snapshot->>'teacher_id_type'<>old_teacher_type THEN
              RAISE EXCEPTION 'DTS_V2_COMPLETION_UPDATE_TEACHER_CHANGED'
                USING ERRCODE='23514';
            END IF;
            new_student_token := NULLIF(snapshot->>'student_token','');
            new_end_time := CASE WHEN snapshot->'end_time'='null'::jsonb
              THEN NULL ELSE (snapshot->>'end_time')::timestamptz END;
            new_is_peak := CASE WHEN snapshot->'is_peak'='null'::jsonb
              THEN NULL ELSE (snapshot->>'is_peak')::boolean END;
            conflict_status_value := 'RESOLVED_UPDATE';
          ELSIF decision_value='TRANSFER_COMPLETION' THEN
            SELECT * INTO target_part
            FROM public.source_course_participations
            WHERE source_region=case_row.source_region
              AND source_appoint_id=case_row.source_appoint_id
              AND participation_seq=target_seq FOR UPDATE;
            IF NOT FOUND OR target_part.source_deleted
               OR target_part.participation_role NOT IN (
                 'NORMAL','PENDING_CORRECTION','REJECTED_CORRECTION',
                 'SUPERSEDED_COMPLETION','VOIDED_COMPLETION'
               )
               OR target_part.teacher_id<>snapshot->>'teacher_id'
               OR target_part.teacher_id_type<>snapshot->>'teacher_id_type'
            THEN
              RAISE EXCEPTION 'DTS_V2_COMPLETION_TRANSFER_TARGET_INVALID'
                USING ERRCODE='23514';
            END IF;
            new_completion_seq := target_seq;
            new_teacher_id := target_part.teacher_id;
            new_teacher_type := target_part.teacher_id_type;
            new_student_token := NULLIF(snapshot->>'student_token','');
            new_end_time := CASE WHEN snapshot->'end_time'='null'::jsonb
              THEN NULL ELSE (snapshot->>'end_time')::timestamptz END;
            new_is_peak := CASE WHEN snapshot->'is_peak'='null'::jsonb
              THEN NULL ELSE (snapshot->>'is_peak')::boolean END;
            conflict_status_value := 'RESOLVED_TRANSFER';
          ELSE
            new_completion_seq := NULL; new_teacher_id := NULL;
            new_teacher_type := NULL; new_student_token := NULL;
            new_end_time := NULL; new_is_peak := NULL;
            conflict_status_value := 'RESOLVED_VOID';
          END IF;

          SELECT coalesce(array_agg(DISTINCT participation_seq
                     ORDER BY participation_seq),ARRAY[]::integer[])
          INTO affected_seqs
          FROM public.source_course_participations
          WHERE source_region=case_row.source_region
            AND source_appoint_id=case_row.source_appoint_id
            AND (
              (participation_role='PENDING_CORRECTION'
               AND assignment_source_row_revision<=
                    expected_source_revision_value
               AND (decision_value<>'TRANSFER_COMPLETION'
                    OR participation_seq<>target_seq))
              OR (decision_value IN ('TRANSFER_COMPLETION','VOID_COMPLETION')
                  AND participation_seq=old_completion_seq)
              OR (decision_value='TRANSFER_COMPLETION'
                  AND participation_seq=target_seq)
            );
          UPDATE public.source_course_participations SET
            participation_role='REJECTED_CORRECTION',
            row_version=row_version+1,updated_at=transaction_timestamp()
          WHERE source_region=case_row.source_region
            AND source_appoint_id=case_row.source_appoint_id
            AND participation_role='PENDING_CORRECTION'
            AND assignment_source_row_revision<=
                  expected_source_revision_value
            AND (decision_value<>'TRANSFER_COMPLETION'
                 OR participation_seq<>target_seq);
          IF decision_value IN ('TRANSFER_COMPLETION','VOID_COMPLETION')
             AND old_completion_seq IS NOT NULL THEN
            UPDATE public.source_course_participations SET
              participation_role=CASE
                WHEN decision_value='TRANSFER_COMPLETION'
                  THEN 'SUPERSEDED_COMPLETION'
                ELSE 'VOIDED_COMPLETION' END,
              row_version=row_version+1,updated_at=transaction_timestamp()
            WHERE source_region=case_row.source_region
              AND source_appoint_id=case_row.source_appoint_id
              AND participation_seq=old_completion_seq
              AND (decision_value<>'TRANSFER_COMPLETION'
                   OR old_completion_seq IS DISTINCT FROM target_seq);
          END IF;
          IF decision_value='TRANSFER_COMPLETION' THEN
            UPDATE public.source_course_participations SET
              participation_status='end',participation_role='COMPLETION',
              row_version=row_version+1,updated_at=transaction_timestamp()
            WHERE source_region=case_row.source_region
              AND source_appoint_id=case_row.source_appoint_id
              AND participation_seq=target_seq;
          END IF;

          score_reversal_result :=
            public.reverse_lesson_components_for_correction_v2(
              case_row.source_region,case_row.source_appoint_id,
              decision_value IN ('TRANSFER_COMPLETION','VOID_COMPLETION'),
              decision_value='UPDATE_COMPLETION_SNAPSHOT'
                AND old_is_peak IS DISTINCT FROM new_is_peak,
              projection_generation_value,decision_id_value
            );

          SELECT observation_revision,teacher_id,teacher_id_type,
                 student_token,completion_participation_seq
          INTO active_observation
          FROM public.course_favorite_observations
          WHERE source_region=case_row.source_region
            AND source_appoint_id=case_row.source_appoint_id
            AND status NOT IN ('INVALIDATED','VOIDED')
          FOR UPDATE;
          active_observation_found := FOUND;
          IF active_observation_found
             AND decision_value<>'KEEP_FROZEN_COMPLETION' THEN
            UPDATE public.course_favorite_observations SET
              status=CASE WHEN decision_value='VOID_COMPLETION'
                          THEN 'VOIDED' ELSE 'INVALIDATED' END,
              next_attempt_at=NULL,claimed_evidence_revision=NULL,
              lease_owner=NULL,lease_token=NULL,lease_acquired_at=NULL,
              lease_expires_at=NULL,last_error=NULL,
              terminal_reason='COMPLETION_CORRECTION:' || decision_value,
              terminal_at=transaction_timestamp(),is_serving=false,
              row_version=row_version+1,updated_at=transaction_timestamp()
            WHERE source_region=case_row.source_region
              AND source_appoint_id=case_row.source_appoint_id
              AND observation_revision=active_observation.observation_revision;
          END IF;

          IF decision_value='VOID_COMPLETION' THEN
            UPDATE public.source_courses SET
              completion_participation_seq=NULL,completion_teacher_id=NULL,
              completion_teacher_id_type=NULL,completion_frozen_at=NULL,
              completion_end_time=NULL,completion_student_token=NULL,
              completion_is_peak=NULL,completion_lesson_local_date=NULL,
              completion_lesson_local_time=NULL,
              completion_source_position=NULL,completion_source_revision=NULL,
              completion_voided_at=transaction_timestamp(),
              completion_conflict_status=conflict_status_value,
              conflict_resolved_against_position=version_row.source_position,
              conflict_resolved_against_revision=
                expected_source_revision_value,
              conflict_fingerprint=NULL,evidence_status='SOURCE_MISSING',
              row_version=row_version+1,updated_at=transaction_timestamp()
            WHERE source_region=case_row.source_region
              AND source_appoint_id=case_row.source_appoint_id;
          ELSIF decision_value='KEEP_FROZEN_COMPLETION' THEN
            UPDATE public.source_courses SET
              completion_conflict_status=conflict_status_value,
              conflict_resolved_against_position=version_row.source_position,
              conflict_resolved_against_revision=
                expected_source_revision_value,
              conflict_fingerprint=NULL,
              evidence_status=CASE WHEN completion_teacher_id IS NOT NULL
                AND completion_end_time IS NOT NULL
                AND completion_student_token IS NOT NULL
                THEN 'CONFIRMED' ELSE 'SOURCE_MISSING' END,
              row_version=row_version+1,updated_at=transaction_timestamp()
            WHERE source_region=case_row.source_region
              AND source_appoint_id=case_row.source_appoint_id;
          ELSE
            UPDATE public.source_courses SET
              completion_participation_seq=new_completion_seq,
              completion_teacher_id=new_teacher_id,
              completion_teacher_id_type=new_teacher_type,
              completion_end_time=new_end_time,
              completion_student_token=new_student_token,
              completion_is_peak=new_is_peak,
              completion_lesson_local_date=CASE
                WHEN snapshot->'lesson_local_date'='null'::jsonb THEN NULL
                ELSE (snapshot->>'lesson_local_date')::date END,
              completion_lesson_local_time=CASE
                WHEN snapshot->'lesson_local_time'='null'::jsonb THEN NULL
                ELSE (snapshot->>'lesson_local_time')::time END,
              completion_source_position=version_row.source_position,
              completion_source_revision=expected_source_revision_value,
              completion_voided_at=NULL,
              completion_conflict_status=conflict_status_value,
              conflict_resolved_against_position=version_row.source_position,
              conflict_resolved_against_revision=
                expected_source_revision_value,
              conflict_fingerprint=NULL,
              evidence_status=CASE WHEN new_teacher_id IS NOT NULL
                AND new_end_time IS NOT NULL
                AND new_student_token IS NOT NULL
                THEN 'CONFIRMED' ELSE 'SOURCE_MISSING' END,
              row_version=row_version+1,updated_at=transaction_timestamp()
            WHERE source_region=case_row.source_region
              AND source_appoint_id=case_row.source_appoint_id;
          END IF;

          IF active_observation_found
             AND decision_value<>'KEEP_FROZEN_COMPLETION' THEN
            PERFORM public.reconcile_favorite_attribution_v2(
              case_row.source_region,active_observation.teacher_id,
              active_observation.teacher_id_type,
              active_observation.student_token,projection_generation_value,
              'favorite-score-v1','COMPLETION_CORRECTION'
            );
          END IF;
          IF decision_value IN (
               'UPDATE_COMPLETION_SNAPSHOT','TRANSFER_COMPLETION'
             ) AND new_teacher_id IS NOT NULL
             AND new_student_token IS NOT NULL AND new_end_time IS NOT NULL
          THEN
            SELECT source_key_type INTO appoint_id_type
            FROM public.dts_source_rows
            WHERE source_region=case_row.source_region
              AND source_table=case_row.source_region || '_appoint'
              AND source_key=case_row.source_appoint_id;
            PERFORM public.materialize_favorite_observation_v2(
              case_row.source_region,case_row.source_appoint_id,
              appoint_id_type,new_teacher_id,new_teacher_type,
              new_student_token,new_completion_seq,
              new_end_time+interval '24 hours','favorite-score-v1',
              projection_generation_value,
              'completion-correction:' || decision_id_value
            );
          END IF;

          PERFORM public.rebuild_lesson_score_result_v2(
            case_row.source_region,case_row.source_appoint_id,
            projection_generation_value
          );
          SELECT coalesce(array_agg(DISTINCT value ORDER BY value),
                          ARRAY[]::text[])
          INTO affected_teachers
          FROM unnest(ARRAY[old_teacher_id,new_teacher_id]) value
          WHERE value IS NOT NULL;
          IF decision_value<>'KEEP_FROZEN_COMPLETION' THEN
            FOREACH teacher_value IN ARRAY affected_teachers LOOP
              PERFORM public.rebuild_teacher_score_and_qualification_v2(
                teacher_value,
                public.teacher_score_projection_vector_v2(teacher_value),
                projection_generation_value
              );
            END LOOP;
          END IF;

          correction_payload := jsonb_build_object(
            'decision_id',decision_id_value,'decision',decision_value,
            'request_sha256',request_sha,
            'old_completion_participation_seq',old_completion_seq,
            'new_completion_participation_seq',new_completion_seq,
            'affected_participation_seqs',to_jsonb(affected_seqs),
            'old_teacher_id',old_teacher_id,'new_teacher_id',new_teacher_id
          );
          coverage := public.completion_correction_source_coverage_v2(
            case_row.source_region,case_row.source_appoint_id,
            expected_source_revision_value,decision_id_value,correction_payload
          );
          aggregate_state := public.completion_course_aggregate_state_v2(
            case_row.source_region,case_row.source_appoint_id
          );
          publish_result := public.publish_domain_aggregate_revision_v2(
            'COURSE',jsonb_build_object(
              'source_region',case_row.source_region,
              'source_appoint_id',case_row.source_appoint_id
            ),aggregate_state,
            public.dts_canonical_json_sha256_v1(aggregate_state),
            '["affected_participation_seqs","decision","decision_id","new_completion_participation_seq","new_teacher_id","old_completion_participation_seq","old_teacher_id"]'::jsonb,
            expected_source_revision_value,version_row.source_position,
            'completion-correction-v1',coverage
          );
          IF publish_result->>'status'<>'CHANGED'
             OR publish_result->>'event_id' IS NULL THEN
            RAISE EXCEPTION 'DTS_V2_COMPLETION_COURSE_EVENT_REQUIRED'
              USING ERRCODE='23514';
          END IF;
          event_ids := array_append(event_ids,publish_result->>'event_id');
          FOREACH target_seq IN ARRAY affected_seqs LOOP
            aggregate_state :=
              public.completion_participation_aggregate_state_v2(
                case_row.source_region,case_row.source_appoint_id,target_seq
              );
            IF aggregate_state IS NULL THEN
              RAISE EXCEPTION
                'DTS_V2_COMPLETION_PARTICIPATION_STATE_MISSING'
                USING ERRCODE='23503';
            END IF;
            publish_result := public.publish_domain_aggregate_revision_v2(
              'PARTICIPATION',jsonb_build_object(
                'source_region',case_row.source_region,
                'source_appoint_id',case_row.source_appoint_id,
                'participation_seq',target_seq
              ),aggregate_state,
              public.dts_canonical_json_sha256_v1(aggregate_state),
              '["affected_participation_seqs","decision","decision_id","new_completion_participation_seq","new_teacher_id","old_completion_participation_seq","old_teacher_id"]'::jsonb,
              expected_source_revision_value,version_row.source_position,
              'completion-correction-v1',coverage
            );
            IF publish_result->>'status'<>'CHANGED'
               OR publish_result->>'event_id' IS NULL THEN
              RAISE EXCEPTION
                'DTS_V2_COMPLETION_PARTICIPATION_EVENT_REQUIRED'
                USING ERRCODE='23514';
            END IF;
            event_ids := array_append(event_ids,publish_result->>'event_id');
          END LOOP;

          result_document := jsonb_build_object(
            'outcome','APPLIED_PENDING_PROJECTION',
            'decision_id',decision_id_value,'case_id',case_id_value,
            'case_status','RESOLVED',
            'completion_conflict_status',conflict_status_value,
            'projection_event_ids',to_jsonb(event_ids)
          );
          decision_payload := jsonb_build_object(
            'protocol_version','completion-correction-decision-v2',
            'request_sha256',request_sha,'request',p_request,
            'result',result_document,
            'score_reversal',score_reversal_result,
            'affected_teachers',to_jsonb(affected_teachers)
          );
          INSERT INTO public.ops_decisions(
            decision_id,case_id,decision,note,decided_at,actor_type,payload,
            expected_case_revision,expected_conflict_fingerprint,
            expected_source_revision,expected_source_position,
            downstream_projection_status,projection_event_ids,row_version
          ) VALUES (
            decision_id_value,case_id_value,decision_value,reason_value,
            transaction_timestamp(),'OPS_USER',decision_payload,
            expected_case_revision_value,expected_fingerprint,
            expected_source_revision_value,request_position,'PENDING',
            to_jsonb(event_ids),1
          );
          UPDATE public.ops_cases SET
            status='RESOLVED',source_reason=decision_value,
            payload=case_row.payload || jsonb_build_object(
              'resolution',jsonb_build_object(
                'decision_id',decision_id_value,'decision',decision_value,
                'request_sha256',request_sha,
                'expected_source_revision',expected_source_revision_value,
                'projection_event_ids',to_jsonb(event_ids)
              )
            ),row_version=row_version+1,updated_at=transaction_timestamp()
          WHERE case_id=case_id_value;
          audit_document := jsonb_build_object(
            'protocol_version','completion-correction-decision-v2',
            'decision_id',decision_id_value,'case_id',case_id_value,
            'decision',decision_value,'request_sha256',request_sha,
            'case_revision',expected_case_revision_value,
            'conflict_fingerprint',expected_fingerprint,
            'source_revision',expected_source_revision_value,
            'projection_event_ids',to_jsonb(event_ids)
          );
          INSERT INTO public.audit_events(
            event_id,event_type,teacher_id,task_id,case_id,occurred_at,
            actor_type,payload_hash,payload
          ) VALUES (
            'audit:completion-decision:v2:' || encode(
              sha256(convert_to(decision_id_value,'UTF8')),'hex'
            ),'DTS_V2_COMPLETION_CORRECTION_DECIDED',old_teacher_id,NULL,
            case_id_value,transaction_timestamp(),'OPS_USER',
            public.dts_canonical_json_sha256_v1(audit_document),
            audit_document
          );
          SET CONSTRAINTS ALL IMMEDIATE;
          SET CONSTRAINTS ALL DEFERRED;
          RETURN result_document;
        END
        $function$;

        REVOKE ALL ON FUNCTION
          public.apply_completion_correction_decision_v2(jsonb,text)
        FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION
          public.apply_completion_correction_decision_v2(jsonb,text)
        TO tit_growth_app;
        """
    )


def _apply_acl_and_comments() -> None:
    op.execute(
        r"""
        REVOKE ALL ON FUNCTION
          public.reconcile_completion_conflict_case_v2(
            text,text,bigint,text),
          public.apply_completion_correction_decision_v2(jsonb,text),
          public.reverse_lesson_components_for_correction_v2(
            text,text,boolean,boolean,bigint,text),
          public.completion_correction_source_coverage_v2(
            text,text,bigint,text,jsonb),
          public.completion_course_aggregate_state_v2(text,text),
          public.completion_participation_aggregate_state_v2(
            text,text,integer)
        FROM PUBLIC,tit_dts_ingest_runtime;
        GRANT EXECUTE ON FUNCTION
          public.reconcile_completion_conflict_case_v2(
            text,text,bigint,text)
        TO tit_growth_app;
        GRANT EXECUTE ON FUNCTION
          public.apply_completion_correction_decision_v2(jsonb,text)
        TO tit_growth_app;

        COMMENT ON COLUMN public.source_courses.completion_conflict_case_id IS
          'Canonical visible completion-correction Case pointer; NULL during first compatibility shadow capture, preserved but never newly filled in ROLLED_BACK, command-owned in V2_PRIMARY.';
        COMMENT ON FUNCTION
          public.reconcile_completion_conflict_case_v2(
            text,text,bigint,text) IS
          'Mode-aware same-transaction verifier for a completion-conflict aggregate: shadow only in compatibility, history preserving in rollback, Case and pointer owner in V2_PRIMARY.';
        COMMENT ON FUNCTION
          public.apply_completion_correction_decision_v2(jsonb,text) IS
          'Request-hashed correction command for KEEP, UPDATE, TRANSFER or VOID; frozen facts, reversals, score refresh and COURSE/PARTICIPATION Outbox publication are atomic.';
        """
    )


def _restore_rev86_case_guard() -> None:
    op.execute(
        rf"""
        CREATE OR REPLACE FUNCTION public.guard_ops_cases_v2()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE actor_name text:=current_user;
        DECLARE table_owner text;
        DECLARE has_completed_recovery boolean;
        BEGIN
          SELECT pg_get_userbyid(relowner) INTO table_owner
          FROM pg_class WHERE oid='public.ops_cases'::regclass;
          IF TG_OP='DELETE' THEN
            RAISE EXCEPTION 'OPS_CASE_APPEND_ONLY' USING ERRCODE='42501';
          END IF;
          IF NEW.case_type NOT IN (
            'COURSE_COMPLETION_CORRECTION','DTS_DIRTY_KEY_DEAD',
            'DTS_SOURCE_CONFLICT','FAVORITE_OBSERVATION_DEAD',
            'TASK_MATERIALIZATION_DEAD','DOWNSTREAM_PROJECTION_DEAD'
          ) THEN RETURN NEW; END IF;
          IF TG_OP='INSERT' THEN
            IF actor_name<>table_owner
               OR NEW.case_revision<>1 OR NEW.row_version<>1
               OR NEW.recovery_evidence_count<>0
               OR NEW.last_recovery_event_id IS NOT NULL
               OR NEW.last_recovery_count IS NOT NULL
               OR NEW.last_recovered_at IS NOT NULL THEN
              RAISE EXCEPTION 'DTS_V2_PROTECTED_CASE_DIRECT_WRITE_DENIED'
                USING ERRCODE='42501';
            END IF;
            RETURN NEW;
          END IF;
          IF NEW.case_id IS DISTINCT FROM OLD.case_id
             OR NEW.case_type IS DISTINCT FROM OLD.case_type
             OR NEW.source_ref IS DISTINCT FROM OLD.source_ref
             OR NEW.source_region IS DISTINCT FROM OLD.source_region
             OR NEW.source_appoint_id IS DISTINCT FROM OLD.source_appoint_id
             OR NEW.task_id IS DISTINCT FROM OLD.task_id
             OR NEW.priority IS DISTINCT FROM OLD.priority
             OR NEW.external_action_status IS DISTINCT FROM
                  OLD.external_action_status
             OR NEW.created_at IS DISTINCT FROM OLD.created_at
             OR (NEW.teacher_id IS DISTINCT FROM OLD.teacher_id
                 AND NOT (OLD.teacher_id IS NULL
                          AND NEW.teacher_id IS NOT NULL)) THEN
            RAISE EXCEPTION 'DTS_V2_PROTECTED_CASE_IMMUTABLE'
              USING ERRCODE='23514';
          END IF;
          IF NEW.row_version IS DISTINCT FROM OLD.row_version+1
             OR NEW.case_revision NOT IN (
                  OLD.case_revision,OLD.case_revision+1
                )
             OR NEW.recovery_evidence_count NOT IN (
                  OLD.recovery_evidence_count,
                  OLD.recovery_evidence_count+1
                ) THEN
            RAISE EXCEPTION 'DTS_V2_PROTECTED_CASE_VERSION_INVALID'
              USING ERRCODE='23514';
          END IF;
          IF actor_name=table_owner THEN
            RETURN NEW;
          END IF;
          IF actor_name='{APP_ROLE}' THEN
            IF NEW.case_revision IS DISTINCT FROM OLD.case_revision
               OR NEW.teacher_id IS DISTINCT FROM OLD.teacher_id
               OR NEW.source_reason IS DISTINCT FROM OLD.source_reason
               OR NEW.payload IS DISTINCT FROM OLD.payload
               OR NEW.evidence_fingerprint IS DISTINCT FROM
                    OLD.evidence_fingerprint
               OR NEW.recovery_evidence_count IS DISTINCT FROM
                    OLD.recovery_evidence_count
               OR NEW.last_recovery_event_id IS DISTINCT FROM
                    OLD.last_recovery_event_id
               OR NEW.last_recovery_count IS DISTINCT FROM
                    OLD.last_recovery_count
               OR NEW.last_recovered_at IS DISTINCT FROM
                    OLD.last_recovered_at
               OR NEW.updated_at<=OLD.updated_at THEN
              RAISE EXCEPTION 'DTS_V2_PROTECTED_CASE_DIRECT_WRITE_DENIED'
                USING ERRCODE='42501';
            END IF;
            IF OLD.status='OPEN' AND NEW.status='IN_REVIEW' THEN
              RETURN NEW;
            END IF;
            IF OLD.status='IN_REVIEW' AND NEW.status='RESOLVED' THEN
              SELECT EXISTS (
                SELECT 1 FROM public.ops_case_recovery_events
                WHERE case_id=OLD.case_id AND work_status='PUBLISHED'
              ) INTO has_completed_recovery;
              IF has_completed_recovery THEN RETURN NEW; END IF;
            END IF;
          END IF;
          RAISE EXCEPTION 'DTS_V2_PROTECTED_CASE_DIRECT_WRITE_DENIED'
            USING ERRCODE='42501';
        END
        $function$;
        """
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("DTS v2 completion correction requires PostgreSQL")
    _preflight_and_expand_pointer()
    _install_case_guard()
    _install_state_helpers()
    _install_reconcile_command()
    _install_score_reversal_helper()
    _install_apply_command()
    _apply_acl_and_comments()


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("DTS v2 completion correction requires PostgreSQL")
    op.execute(
        r"""
        DO $completion_correction_downgrade_guard$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM public.ops_decisions decision_row
            JOIN public.ops_cases case_row
              ON case_row.case_id=decision_row.case_id
            WHERE case_row.case_type='COURSE_COMPLETION_CORRECTION'
          ) OR EXISTS (
            SELECT 1 FROM public.ops_cases
            WHERE case_type='COURSE_COMPLETION_CORRECTION'
              AND payload->>'protocol_version'=
                    'completion-conflict-case-v2'
          ) THEN
            RAISE EXCEPTION
              'DTS_V2_COMPLETION_CORRECTION_DOWNGRADE_EVIDENCE_EXISTS';
          END IF;
          IF EXISTS (
            SELECT 1
            FROM public.completion_correction_pointer_upgrade_archive
            WHERE length(prior_case_id)>160
          ) OR EXISTS (
            SELECT 1 FROM public.source_courses
            WHERE length(completion_conflict_case_id)>160
          ) THEN
            RAISE EXCEPTION
              'DTS_V2_COMPLETION_CORRECTION_DOWNGRADE_POINTER_TOO_LONG';
          END IF;
        END
        $completion_correction_downgrade_guard$;

        DROP FUNCTION IF EXISTS
          public.apply_completion_correction_decision_v2(jsonb,text),
          public.reverse_lesson_components_for_correction_v2(
            text,text,boolean,boolean,bigint,text),
          public.reconcile_completion_conflict_case_v2(
            text,text,bigint,text),
          public.completion_participation_aggregate_state_v2(
            text,text,integer),
          public.completion_course_aggregate_state_v2(text,text),
          public.completion_correction_source_coverage_v2(
            text,text,bigint,text,jsonb);
        ALTER TABLE public.source_courses
          DROP CONSTRAINT fk_source_course_completion_conflict_case_v2;
        UPDATE public.source_courses course
        SET completion_conflict_case_id=archive.prior_case_id,
            row_version=course.row_version+1,
            updated_at=clock_timestamp()
        FROM public.completion_correction_pointer_upgrade_archive archive
        WHERE course.source_region=archive.source_region
          AND course.source_appoint_id=archive.source_appoint_id
          AND course.completion_conflict_case_id IS NULL;
        """
    )
    op.alter_column(
        "source_courses",
        "completion_conflict_case_id",
        existing_type=sa.String(length=768),
        type_=sa.String(length=160),
        existing_nullable=True,
        schema="public",
    )
    _restore_rev86_case_guard()
    op.drop_table(
        "completion_correction_pointer_upgrade_archive", schema="public"
    )
