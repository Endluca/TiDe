"""materialize Beijing-date teacher time rechecks.

Revision ID: 20260822_97_teacher_time_recheck
Revises: 20260822_96_compat_queue_gate
Create Date: 2026-08-22

The DTS aggregate vector changes on source events; NEW/EXISTING also changes
when the Beijing business date crosses day 30.  This revision supplies the
previously reserved TEACHER_TIME_RECHECK queue owner, immutable daily result
evidence and the restricted Outbox-runtime command surface.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260822_97_teacher_time_recheck"
down_revision: Union[str, None] = "20260822_96_compat_queue_gate"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


OUTBOX_ROLE = "tit_dts_outbox_worker_runtime"


def _preflight() -> None:
    op.execute(
        r"""
        DO $teacher_time_recheck_preflight$
        DECLARE required_relation text;
        DECLARE required_function text;
        BEGIN
          FOREACH required_relation IN ARRAY ARRAY[
            'dts_dirty_keys','dts_dirty_key_inputs',
            'dts_dirty_key_dependencies','dts_pipeline_control',
            'teacher_source_wide','teachers','domain_aggregate_revisions'
          ] LOOP
            IF to_regclass('public.'||required_relation) IS NULL THEN
              RAISE EXCEPTION
                'DTS_V2_TIME_RECHECK_PREREQUISITE_MISSING:%',
                required_relation;
            END IF;
          END LOOP;
          FOREACH required_function IN ARRAY ARRAY[
            'public._upsert_dts_dirty_key_input_v2(text,text,text,text,text,jsonb,bigint,text)',
            'public._complete_dts_dirty_key_v2(text,text,text,text,text,text,bigint,bigint)',
            'public._fail_dts_dirty_key_v2(text,text,text,text,text,text,bigint,bigint,text)',
            'public.dts_v2_runtime_primary_guard_v1(text)',
            'public.dts_canonical_json_sha256_v1(jsonb)'
          ] LOOP
            IF to_regprocedure(required_function) IS NULL THEN
              RAISE EXCEPTION
                'DTS_V2_TIME_RECHECK_PREREQUISITE_MISSING:%',
                required_function;
            END IF;
          END LOOP;
          IF to_regrole('tit_dts_outbox_worker_runtime') IS NULL THEN
            RAISE EXCEPTION
              'DTS_V2_TIME_RECHECK_PREREQUISITE_MISSING:OUTBOX_ROLE';
          END IF;
          IF to_regclass('public.dts_teacher_time_recheck_results')
               IS NOT NULL
             OR to_regprocedure(
               'public.materialize_teacher_time_recheck_v2(jsonb,bigint,bigint,bigint,text,bigint)'
             ) IS NOT NULL THEN
            RAISE EXCEPTION 'DTS_V2_TIME_RECHECK_ALREADY_INSTALLED';
          END IF;
        END
        $teacher_time_recheck_preflight$;

        LOCK TABLE public.dts_dirty_keys,public.dts_dirty_key_inputs,
          public.dts_dirty_key_dependencies,public.teacher_source_wide,
          public.teachers,public.domain_aggregate_revisions
        IN SHARE ROW EXCLUSIVE MODE;
        """
    )


def _create_tables() -> None:
    op.create_table(
        "dts_teacher_time_recheck_schedule",
        sa.Column("schedule_id", sa.String(length=16), nullable=False),
        sa.Column("last_enqueued_business_date", sa.Date(), nullable=True),
        sa.Column("last_enqueued_generation", sa.BigInteger(), nullable=True),
        sa.Column(
            "last_enqueued_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "row_version",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.PrimaryKeyConstraint(
            "schedule_id", name="pk_dts_teacher_time_recheck_schedule"
        ),
        sa.CheckConstraint(
            "schedule_id='PRIMARY' AND row_version>=1 AND "
            "((last_enqueued_business_date IS NULL "
            "AND last_enqueued_generation IS NULL "
            "AND last_enqueued_at IS NULL) OR "
            "(last_enqueued_business_date IS NOT NULL "
            "AND last_enqueued_generation>=1 "
            "AND last_enqueued_at IS NOT NULL))",
            name="ck_dts_teacher_time_recheck_schedule_shape",
        ),
        schema="public",
    )
    op.execute(
        """
        INSERT INTO public.dts_teacher_time_recheck_schedule(
          schedule_id,last_enqueued_business_date,last_enqueued_generation,
          last_enqueued_at,row_version
        ) VALUES ('PRIMARY',NULL,NULL,NULL,1)
        """
    )

    op.create_table(
        "dts_teacher_time_recheck_results",
        sa.Column("teacher_id", sa.String(length=64), nullable=False),
        sa.Column("business_date_beijing", sa.Date(), nullable=False),
        sa.Column("teacher_id_type", sa.String(length=16), nullable=False),
        sa.Column("dom_aggregate_revision", sa.BigInteger(), nullable=False),
        sa.Column("ovs_aggregate_revision", sa.BigInteger(), nullable=False),
        sa.Column(
            "regional_state_sha256", postgresql.JSONB(), nullable=False
        ),
        sa.Column("projection_generation", sa.BigInteger(), nullable=False),
        sa.Column(
            "triggering_event_id", sa.String(length=512), nullable=False
        ),
        sa.Column("claimed_work_revision", sa.BigInteger(), nullable=False),
        sa.Column("time_values", postgresql.JSONB(), nullable=False),
        sa.Column("plan_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "materialized_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("transaction_timestamp()"),
        ),
        sa.PrimaryKeyConstraint(
            "teacher_id",
            "business_date_beijing",
            name="pk_dts_teacher_time_recheck_results",
        ),
        sa.ForeignKeyConstraint(
            ["teacher_id"],
            ["public.teacher_source_wide.tchr_id"],
            name="fk_dts_teacher_time_recheck_result_teacher",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.CheckConstraint(
            "teacher_id_type IN ('NUMERIC','TEXT') "
            "AND dom_aggregate_revision>=1 AND ovs_aggregate_revision>=1 "
            "AND projection_generation>=1 AND claimed_work_revision>=1 "
            "AND regional_state_sha256 ?& ARRAY['dom','ovs'] "
            "AND regional_state_sha256=jsonb_build_object("
            "'dom',regional_state_sha256->'dom',"
            "'ovs',regional_state_sha256->'ovs') "
            "AND regional_state_sha256->>'dom' ~ '^[0-9a-f]{64}$' "
            "AND regional_state_sha256->>'ovs' ~ '^[0-9a-f]{64}$' "
            "AND jsonb_typeof(time_values)='object' "
            "AND time_values=jsonb_build_object("
            "'job_days',time_values->'job_days',"
            "'job_month',time_values->'job_month',"
            "'online_status',time_values->'online_status',"
            "'online_status_evidence_status',"
            "time_values->'online_status_evidence_status') "
            "AND plan_sha256 ~ '^[0-9a-f]{64}$' "
            "AND btrim(triggering_event_id)<>''",
            name="ck_dts_teacher_time_recheck_result_shape",
        ),
        schema="public",
    )
    op.create_index(
        "ix_dts_teacher_time_recheck_result_date",
        "dts_teacher_time_recheck_results",
        ["business_date_beijing", "teacher_id"],
        schema="public",
    )

    op.create_table(
        "dts_teacher_time_recheck_audits",
        sa.Column(
            "audit_id", sa.BigInteger(), sa.Identity(), nullable=False
        ),
        sa.Column("teacher_id", sa.String(length=64), nullable=False),
        sa.Column("business_date_beijing", sa.Date(), nullable=False),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("detail", postgresql.JSONB(), nullable=False),
        sa.Column("detail_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("transaction_timestamp()"),
        ),
        sa.PrimaryKeyConstraint(
            "audit_id", name="pk_dts_teacher_time_recheck_audits"
        ),
        sa.CheckConstraint(
            "event_type='SUPERSEDED_BY_CURRENT_DATE' "
            "AND jsonb_typeof(detail)='object' "
            "AND detail_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_dts_teacher_time_recheck_audit_shape",
        ),
        schema="public",
    )


def _install_immutability() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.guard_teacher_time_recheck_append_only_v1()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path=pg_catalog,public
        AS $function$
        BEGIN
          RAISE EXCEPTION 'DTS_V2_TIME_RECHECK_EVIDENCE_IMMUTABLE'
            USING ERRCODE='42501';
        END
        $function$;
        REVOKE ALL ON FUNCTION
          public.guard_teacher_time_recheck_append_only_v1() FROM PUBLIC;

        CREATE TRIGGER trg_guard_teacher_time_recheck_result_append_only_v1
        BEFORE UPDATE OR DELETE
        ON public.dts_teacher_time_recheck_results
        FOR EACH ROW EXECUTE FUNCTION
          public.guard_teacher_time_recheck_append_only_v1();

        CREATE TRIGGER trg_guard_teacher_time_recheck_audit_append_only_v1
        BEFORE UPDATE OR DELETE
        ON public.dts_teacher_time_recheck_audits
        FOR EACH ROW EXECUTE FUNCTION
          public.guard_teacher_time_recheck_append_only_v1();
        """
    )


def _install_enqueue() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public._enqueue_teacher_time_recheck_key_v2(
          p_teacher_id text,p_business_date date
        ) RETURNS jsonb
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE identity jsonb;
        DECLARE fingerprint text;
        DECLARE input_revision bigint;
        BEGIN
          IF p_teacher_id IS NULL OR btrim(p_teacher_id)=''
             OR length(p_teacher_id)>64 OR p_business_date IS NULL
             OR NOT EXISTS (
               SELECT 1 FROM public.teacher_source_wide
               WHERE tchr_id=p_teacher_id AND v2_row_version IS NOT NULL
             ) THEN
            RAISE EXCEPTION 'DTS_V2_TIME_RECHECK_INPUT_INVALID'
              USING ERRCODE='22023';
          END IF;
          identity := jsonb_build_object(
            'business_date_beijing',p_business_date,
            'teacher_id',p_teacher_id
          );
          input_revision := to_char(p_business_date,'YYYYMMDD')::bigint;
          fingerprint := public.dts_canonical_json_sha256_v1(
            jsonb_build_object(
              'protocol','teacher-time-recheck-input-v1',
              'identity',identity,'input_revision',input_revision
            )
          );
          RETURN public._upsert_dts_dirty_key_input_v2(
            'dom','TEACHER_TIME_RECHECK',p_teacher_id,
            to_char(p_business_date,'YYYY-MM-DD'),'TIME_RECHECK',
            identity,input_revision,fingerprint
          );
        END
        $function$;
        REVOKE ALL ON FUNCTION
          public._enqueue_teacher_time_recheck_key_v2(text,date)
        FROM PUBLIC;

        CREATE FUNCTION public.enqueue_due_teacher_time_rechecks_v2()
        RETURNS jsonb
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE generation bigint;
        DECLARE local_now timestamp;
        DECLARE business_date date;
        DECLARE schedule public.dts_teacher_time_recheck_schedule%ROWTYPE;
        DECLARE teacher record;
        DECLARE outcome jsonb;
        DECLARE enqueued integer := 0;
        DECLARE superseded integer := 0;
        DECLARE obsolete public.dts_dirty_keys%ROWTYPE;
        DECLARE audit_detail jsonb;
        BEGIN
          generation := public.dts_v2_runtime_primary_guard_v1('OUTBOX');
          IF generation IS NULL THEN
            RETURN jsonb_build_object(
              'enqueued',0,'superseded',0,'schedule_due',false
            );
          END IF;
          local_now := transaction_timestamp()
            AT TIME ZONE 'Asia/Shanghai';
          business_date := local_now::date;
          IF local_now::time < time '00:05:00' THEN
            RETURN jsonb_build_object(
              'enqueued',0,'superseded',0,'schedule_due',true
            );
          END IF;

          SELECT * INTO schedule
          FROM public.dts_teacher_time_recheck_schedule
          WHERE schedule_id='PRIMARY' FOR UPDATE;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'DTS_V2_TIME_RECHECK_SCHEDULE_SINGLETON_REQUIRED'
              USING ERRCODE='55000';
          END IF;

          FOR obsolete IN
            SELECT * FROM public.dts_dirty_keys
            WHERE source_region='dom'
              AND key_type='TEACHER_TIME_RECHECK'
              AND key_part_2<to_char(business_date,'YYYY-MM-DD')
              AND status<>'COMPLETED'
              AND (status<>'PROCESSING'
                   OR lease_expires_at<=transaction_timestamp())
            ORDER BY key_part_2,key_part_1
            FOR UPDATE
          LOOP
            DELETE FROM public.dts_dirty_key_dependencies
            WHERE source_region=obsolete.source_region
              AND key_type=obsolete.key_type
              AND key_part_1=obsolete.key_part_1
              AND key_part_2=obsolete.key_part_2;
            UPDATE public.dts_dirty_keys
            SET status='COMPLETED',attempt_count=0,last_error_code=NULL,
                next_attempt_at=NULL,blocked_by=NULL,
                completed_work_revision=required_work_revision,
                claimed_through_work_revision=NULL,
                lease_owner_kind=NULL,lease_owner=NULL,lease_token=NULL,
                claimed_at=NULL,lease_expires_at=NULL,claimed_by=NULL,
                row_version=row_version+1,last_seen_at=clock_timestamp(),
                updated_at=clock_timestamp()
            WHERE source_region=obsolete.source_region
              AND key_type=obsolete.key_type
              AND key_part_1=obsolete.key_part_1
              AND key_part_2=obsolete.key_part_2;
            audit_detail := jsonb_build_object(
              'current_business_date_beijing',business_date,
              'prior_status',obsolete.status,
              'required_work_revision',obsolete.required_work_revision,
              'completed_work_revision',obsolete.completed_work_revision
            );
            INSERT INTO public.dts_teacher_time_recheck_audits(
              teacher_id,business_date_beijing,event_type,detail,
              detail_sha256
            ) VALUES (
              obsolete.key_part_1,obsolete.key_part_2::date,
              'SUPERSEDED_BY_CURRENT_DATE',audit_detail,
              public.dts_canonical_json_sha256_v1(audit_detail)
            );
            superseded := superseded+1;
          END LOOP;

          IF schedule.last_enqueued_business_date IS DISTINCT FROM
               business_date THEN
            FOR teacher IN
              SELECT tchr_id FROM public.teacher_source_wide
              WHERE v2_row_version IS NOT NULL
              ORDER BY convert_to(tchr_id,'UTF8')
            LOOP
              outcome := public._enqueue_teacher_time_recheck_key_v2(
                teacher.tchr_id,business_date
              );
              IF outcome->>'status'='ENQUEUED' THEN
                enqueued := enqueued+1;
              ELSIF outcome->>'status' NOT IN ('NOOP','NOOP_OLDER') THEN
                RAISE EXCEPTION 'DTS_V2_TIME_RECHECK_ENQUEUE_RESULT_INVALID'
                  USING ERRCODE='55000';
              END IF;
            END LOOP;
            UPDATE public.dts_teacher_time_recheck_schedule
            SET last_enqueued_business_date=business_date,
                last_enqueued_generation=generation,
                last_enqueued_at=transaction_timestamp(),
                row_version=row_version+1
            WHERE schedule_id='PRIMARY';
          END IF;
          RETURN jsonb_build_object(
            'enqueued',enqueued,'superseded',superseded,
            'schedule_due',false
          );
        END
        $function$;
        REVOKE ALL ON FUNCTION
          public.enqueue_due_teacher_time_rechecks_v2() FROM PUBLIC;

        CREATE FUNCTION public.enqueue_teacher_time_recheck_after_wide_v2()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE generation bigint;
        DECLARE local_now timestamp;
        BEGIN
          IF NEW.v2_row_version IS NULL THEN
            RETURN NEW;
          END IF;
          generation := public.dts_v2_runtime_primary_guard_v1('OUTBOX');
          IF generation IS NULL THEN
            RETURN NEW;
          END IF;
          local_now := transaction_timestamp()
            AT TIME ZONE 'Asia/Shanghai';
          IF local_now::time>=time '00:05:00' THEN
            PERFORM public._enqueue_teacher_time_recheck_key_v2(
              NEW.tchr_id,local_now::date
            );
          END IF;
          RETURN NEW;
        END
        $function$;
        REVOKE ALL ON FUNCTION
          public.enqueue_teacher_time_recheck_after_wide_v2() FROM PUBLIC;

        CREATE TRIGGER trg_enqueue_teacher_time_recheck_after_wide_v2
        AFTER INSERT OR UPDATE OF v2_row_version
        ON public.teacher_source_wide
        FOR EACH ROW
        WHEN (NEW.v2_row_version IS NOT NULL)
        EXECUTE FUNCTION
          public.enqueue_teacher_time_recheck_after_wide_v2();
        """
    )


def _install_claim_and_settlement() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.claim_teacher_time_rechecks_v2(
          p_worker_id text,p_batch_size integer,p_lease_seconds integer
        ) RETURNS TABLE(
          teacher_id text,business_date_beijing date,lease_token text,
          claimed_work_revision bigint,row_version bigint
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE generation bigint;
        DECLARE business_date date;
        DECLARE candidate record;
        DECLARE new_token text;
        BEGIN
          IF p_worker_id IS NULL OR btrim(p_worker_id)=''
             OR p_batch_size NOT BETWEEN 1 AND 1000
             OR p_lease_seconds NOT BETWEEN 15 AND 300 THEN
            RAISE EXCEPTION 'DTS_V2_TIME_RECHECK_CLAIM_INVALID'
              USING ERRCODE='22023';
          END IF;
          generation := public.dts_v2_runtime_primary_guard_v1('OUTBOX');
          IF generation IS NULL THEN RETURN; END IF;
          business_date := (
            transaction_timestamp() AT TIME ZONE 'Asia/Shanghai'
          )::date;
          FOR candidate IN
            SELECT dirty.source_region,dirty.key_type,dirty.key_part_1,
                   dirty.key_part_2
            FROM public.dts_dirty_keys dirty
            WHERE dirty.source_region='dom'
              AND dirty.key_type='TEACHER_TIME_RECHECK'
              AND dirty.key_part_2=to_char(business_date,'YYYY-MM-DD')
              AND dirty.status IN ('PENDING','RETRY')
              AND dirty.next_attempt_at<=transaction_timestamp()
            ORDER BY dirty.next_attempt_at,dirty.updated_at,
                     convert_to(dirty.key_part_1,'UTF8')
            FOR UPDATE SKIP LOCKED LIMIT p_batch_size
          LOOP
            new_token := gen_random_uuid()::text;
            UPDATE public.dts_dirty_keys dirty
            SET status='PROCESSING',
                claimed_through_work_revision=required_work_revision,
                lease_owner_kind='SOURCEWIDE_TIME_RECHECK',
                lease_owner=p_worker_id,lease_token=new_token,
                claimed_at=transaction_timestamp(),
                lease_expires_at=transaction_timestamp()
                  +make_interval(secs=>p_lease_seconds),
                next_attempt_at=NULL,row_version=dirty.row_version+1,
                updated_at=clock_timestamp()
            WHERE dirty.source_region=candidate.source_region
              AND dirty.key_type=candidate.key_type
              AND dirty.key_part_1=candidate.key_part_1
              AND dirty.key_part_2=candidate.key_part_2
            RETURNING dirty.key_part_1,dirty.key_part_2::date,
              dirty.lease_token,dirty.claimed_through_work_revision,
              dirty.row_version
            INTO teacher_id,business_date_beijing,lease_token,
              claimed_work_revision,row_version;
            RETURN NEXT;
          END LOOP;
        END
        $function$;

        CREATE FUNCTION public.complete_teacher_time_recheck_v2(
          p_teacher_id text,p_business_date date,p_lease_token text,
          p_claimed_work_revision bigint,p_expected_row_version bigint
        ) RETURNS jsonb
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE generation bigint;
        BEGIN
          generation := public.dts_v2_runtime_primary_guard_v1('OUTBOX');
          IF generation IS NULL THEN
            RAISE EXCEPTION 'DTS_V2_TIME_RECHECK_PRIMARY_REQUIRED'
              USING ERRCODE='55000';
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM public.dts_teacher_time_recheck_results result
            WHERE result.teacher_id=p_teacher_id
              AND result.business_date_beijing=p_business_date
              AND result.claimed_work_revision=p_claimed_work_revision
              AND result.projection_generation=generation
          ) THEN
            RAISE EXCEPTION 'DTS_V2_TIME_RECHECK_RESULT_REQUIRED'
              USING ERRCODE='23503';
          END IF;
          RETURN public._complete_dts_dirty_key_v2(
            'SOURCEWIDE_TIME_RECHECK','dom','TEACHER_TIME_RECHECK',
            p_teacher_id,to_char(p_business_date,'YYYY-MM-DD'),p_lease_token,
            p_claimed_work_revision,p_expected_row_version
          );
        END
        $function$;

        CREATE FUNCTION public.fail_teacher_time_recheck_v2(
          p_teacher_id text,p_business_date date,p_lease_token text,
          p_claimed_work_revision bigint,p_expected_row_version bigint,
          p_error_code text
        ) RETURNS jsonb
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE generation bigint;
        BEGIN
          generation := public.dts_v2_runtime_primary_guard_v1('OUTBOX');
          IF generation IS NULL THEN
            RAISE EXCEPTION 'DTS_V2_TIME_RECHECK_PRIMARY_REQUIRED'
              USING ERRCODE='55000';
          END IF;
          RETURN public._fail_dts_dirty_key_v2(
            'SOURCEWIDE_TIME_RECHECK','dom','TEACHER_TIME_RECHECK',
            p_teacher_id,to_char(p_business_date,'YYYY-MM-DD'),p_lease_token,
            p_claimed_work_revision,p_expected_row_version,p_error_code
          );
        END
        $function$;

        CREATE FUNCTION public.reap_expired_teacher_time_rechecks_v2(
          p_batch_size integer
        ) RETURNS integer
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE generation bigint;
        DECLARE business_date date;
        DECLARE dirty public.dts_dirty_keys%ROWTYPE;
        DECLARE next_attempt integer;
        DECLARE reaped integer := 0;
        DECLARE audit_detail jsonb;
        BEGIN
          IF p_batch_size NOT BETWEEN 1 AND 1000 THEN
            RAISE EXCEPTION 'DTS_V2_TIME_RECHECK_REAP_INVALID'
              USING ERRCODE='22023';
          END IF;
          generation := public.dts_v2_runtime_primary_guard_v1('OUTBOX');
          IF generation IS NULL THEN RETURN 0; END IF;
          business_date := (
            transaction_timestamp() AT TIME ZONE 'Asia/Shanghai'
          )::date;
          FOR dirty IN
            SELECT * FROM public.dts_dirty_keys
            WHERE source_region='dom'
              AND key_type='TEACHER_TIME_RECHECK'
              AND status='PROCESSING'
              AND lease_owner_kind='SOURCEWIDE_TIME_RECHECK'
              AND lease_expires_at<=transaction_timestamp()
            ORDER BY lease_expires_at,key_part_2,key_part_1
            FOR UPDATE SKIP LOCKED LIMIT p_batch_size
          LOOP
            IF dirty.key_part_2<to_char(business_date,'YYYY-MM-DD') THEN
              UPDATE public.dts_dirty_keys
              SET status='COMPLETED',attempt_count=0,last_error_code=NULL,
                  next_attempt_at=NULL,blocked_by=NULL,
                  completed_work_revision=required_work_revision,
                  claimed_through_work_revision=NULL,
                  lease_owner_kind=NULL,lease_owner=NULL,lease_token=NULL,
                  claimed_at=NULL,lease_expires_at=NULL,claimed_by=NULL,
                  row_version=row_version+1,updated_at=clock_timestamp()
              WHERE source_region=dirty.source_region
                AND key_type=dirty.key_type
                AND key_part_1=dirty.key_part_1
                AND key_part_2=dirty.key_part_2;
              audit_detail := jsonb_build_object(
                'current_business_date_beijing',business_date,
                'prior_status',dirty.status,
                'required_work_revision',dirty.required_work_revision,
                'completed_work_revision',dirty.completed_work_revision
              );
              INSERT INTO public.dts_teacher_time_recheck_audits(
                teacher_id,business_date_beijing,event_type,detail,
                detail_sha256
              ) VALUES (
                dirty.key_part_1,dirty.key_part_2::date,
                'SUPERSEDED_BY_CURRENT_DATE',audit_detail,
                public.dts_canonical_json_sha256_v1(audit_detail)
              );
            ELSIF dirty.required_work_revision>
                    dirty.claimed_through_work_revision THEN
              UPDATE public.dts_dirty_keys
              SET status='PENDING',attempt_count=0,last_error_code=NULL,
                  next_attempt_at=transaction_timestamp(),blocked_by=NULL,
                  claimed_through_work_revision=NULL,
                  lease_owner_kind=NULL,lease_owner=NULL,lease_token=NULL,
                  claimed_at=NULL,lease_expires_at=NULL,claimed_by=NULL,
                  row_version=row_version+1,updated_at=clock_timestamp()
              WHERE source_region=dirty.source_region
                AND key_type=dirty.key_type
                AND key_part_1=dirty.key_part_1
                AND key_part_2=dirty.key_part_2;
            ELSE
              next_attempt := dirty.attempt_count+1;
              UPDATE public.dts_dirty_keys
              SET status=CASE WHEN next_attempt=8 THEN 'DEAD'
                              ELSE 'RETRY' END,
                  attempt_count=next_attempt,
                  dead_generation=dead_generation
                    +CASE WHEN next_attempt=8 THEN 1 ELSE 0 END,
                  last_error_code='DIRTY_LEASE_EXPIRED',blocked_by=NULL,
                  next_attempt_at=CASE WHEN next_attempt=8 THEN NULL
                    ELSE transaction_timestamp()+least(
                      interval '30 minutes',
                      interval '5 seconds'*power(2,next_attempt-1)
                    ) END,
                  claimed_through_work_revision=NULL,
                  lease_owner_kind=NULL,lease_owner=NULL,lease_token=NULL,
                  claimed_at=NULL,lease_expires_at=NULL,claimed_by=NULL,
                  row_version=row_version+1,updated_at=clock_timestamp()
              WHERE source_region=dirty.source_region
                AND key_type=dirty.key_type
                AND key_part_1=dirty.key_part_1
                AND key_part_2=dirty.key_part_2;
              IF next_attempt=8 THEN
                INSERT INTO public.dts_dirty_key_state_audits(
                  source_region,key_type,key_part_1,key_part_2,event_type,
                  work_generation,dead_generation,detail
                ) VALUES (
                  dirty.source_region,dirty.key_type,dirty.key_part_1,
                  dirty.key_part_2,'DEAD',dirty.work_generation,
                  dirty.dead_generation+1,
                  jsonb_build_object('error_code','DIRTY_LEASE_EXPIRED')
                );
              END IF;
            END IF;
            reaped := reaped+1;
          END LOOP;
          RETURN reaped;
        END
        $function$;

        REVOKE ALL ON FUNCTION
          public.claim_teacher_time_rechecks_v2(text,integer,integer),
          public.complete_teacher_time_recheck_v2(
            text,date,text,bigint,bigint
          ),
          public.fail_teacher_time_recheck_v2(
            text,date,text,bigint,bigint,text
          ),
          public.reap_expired_teacher_time_rechecks_v2(integer)
        FROM PUBLIC;
        """
    )


def _install_materializer() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.materialize_teacher_time_recheck_v2(
          p_payload jsonb,p_dom_revision bigint,p_ovs_revision bigint,
          p_projection_generation bigint,p_event_id text,
          p_claimed_work_revision bigint
        ) RETURNS jsonb
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE actor_name text := COALESCE(
          NULLIF(current_setting('role',true),'none'),session_user
        );
        DECLARE generation bigint;
        DECLARE local_now timestamp;
        DECLARE business_date date;
        DECLARE wide public.teacher_source_wide%ROWTYPE;
        DECLARE existing_result
          public.dts_teacher_time_recheck_results%ROWTYPE;
        DECLARE latest_result_date date;
        DECLARE aggregate_count integer;
        DECLARE expected_job_days integer;
        DECLARE expected_job_month double precision;
        DECLARE expected_online_status text;
        DECLARE expected_online_evidence text;
        DECLARE plan_document jsonb;
        DECLARE plan_sha256 text;
        DECLARE wide_changes integer := 0;
        DECLARE teacher_changes integer := 0;
        DECLARE result_changes integer := 0;
        BEGIN
          IF actor_name<>'tit_dts_outbox_worker_runtime'
             OR jsonb_typeof(p_payload)<>'object'
             OR (SELECT array_agg(key ORDER BY key)
                 FROM jsonb_object_keys(p_payload) keys(key))
                  IS DISTINCT FROM ARRAY[
                    'business_date_beijing','regional_state_sha256',
                    'teacher_id','teacher_id_type','time_values','v'
                  ]::text[]
             OR p_payload->>'v'<>'1'
             OR p_payload->>'teacher_id' IS NULL
             OR btrim(p_payload->>'teacher_id')=''
             OR length(p_payload->>'teacher_id')>64
             OR p_payload->>'teacher_id_type' NOT IN ('NUMERIC','TEXT')
             OR jsonb_typeof(p_payload->'regional_state_sha256')<>'object'
             OR p_payload->'regional_state_sha256'<>
                  jsonb_build_object(
                    'dom',p_payload#>'{regional_state_sha256,dom}',
                    'ovs',p_payload#>'{regional_state_sha256,ovs}'
                  )
             OR p_payload#>>'{regional_state_sha256,dom}'
                  !~ '^[0-9a-f]{64}$'
             OR p_payload#>>'{regional_state_sha256,ovs}'
                  !~ '^[0-9a-f]{64}$'
             OR jsonb_typeof(p_payload->'time_values')<>'object'
             OR (SELECT array_agg(key ORDER BY key)
                 FROM jsonb_object_keys(p_payload->'time_values') keys(key))
                  IS DISTINCT FROM ARRAY[
                    'job_days','job_month','online_status',
                    'online_status_evidence_status'
                  ]::text[]
             OR jsonb_typeof(p_payload#>'{time_values,job_days}')
                  NOT IN ('number','null')
             OR jsonb_typeof(p_payload#>'{time_values,job_month}')
                  NOT IN ('number','null')
             OR jsonb_typeof(p_payload#>'{time_values,online_status}')
                  NOT IN ('string','null')
             OR jsonb_typeof(
                  p_payload#>'{time_values,online_status_evidence_status}'
                )<>'string'
             OR p_dom_revision<1 OR p_ovs_revision<1
             OR p_projection_generation<1 OR p_claimed_work_revision<1
             OR p_event_id IS NULL OR btrim(p_event_id)=''
             OR length(p_event_id)>512 THEN
            RAISE EXCEPTION 'DTS_V2_TEACHER_TIME_RECHECK_REQUEST_INVALID'
              USING ERRCODE='22023';
          END IF;
          BEGIN
            business_date := (p_payload->>'business_date_beijing')::date;
          EXCEPTION WHEN invalid_text_representation OR datetime_field_overflow
          THEN
            RAISE EXCEPTION 'DTS_V2_TEACHER_TIME_RECHECK_DATE_INVALID'
              USING ERRCODE='22023';
          END;
          IF to_char(business_date,'YYYY-MM-DD') IS DISTINCT FROM
               p_payload->>'business_date_beijing' THEN
            RAISE EXCEPTION 'DTS_V2_TEACHER_TIME_RECHECK_DATE_INVALID'
              USING ERRCODE='22023';
          END IF;

          generation := public.dts_v2_runtime_primary_guard_v1('TEACHER');
          IF generation IS NULL
             OR generation IS DISTINCT FROM p_projection_generation THEN
            RAISE EXCEPTION 'DTS_V2_TEACHER_TIME_RECHECK_PRIMARY_REQUIRED'
              USING ERRCODE='55000';
          END IF;
          local_now := transaction_timestamp()
            AT TIME ZONE 'Asia/Shanghai';
          IF business_date IS DISTINCT FROM local_now::date THEN
            RAISE EXCEPTION
              'DTS_V2_TEACHER_TIME_RECHECK_DATE_REGRESSION'
              USING ERRCODE='40001';
          END IF;

          SELECT max(result.business_date_beijing)
          INTO latest_result_date
          FROM public.dts_teacher_time_recheck_results result
          WHERE result.teacher_id=p_payload->>'teacher_id';
          IF latest_result_date>business_date THEN
            RAISE EXCEPTION
              'DTS_V2_TEACHER_TIME_RECHECK_DATE_REGRESSION'
              USING ERRCODE='40001';
          END IF;

          PERFORM 1 FROM public.domain_aggregate_revisions aggregate
          WHERE aggregate.aggregate_type='TEACHER'
            AND aggregate.canonical_key IN (
              jsonb_build_object(
                'source_region','dom','teacher_id',
                p_payload->>'teacher_id'
              ),
              jsonb_build_object(
                'source_region','ovs','teacher_id',
                p_payload->>'teacher_id'
              )
            )
            AND ((aggregate.canonical_key->>'source_region'='dom'
                  AND aggregate.revision=p_dom_revision
                  AND aggregate.aggregate_state_sha256=
                    p_payload#>>'{regional_state_sha256,dom}')
                 OR
                 (aggregate.canonical_key->>'source_region'='ovs'
                  AND aggregate.revision=p_ovs_revision
                  AND aggregate.aggregate_state_sha256=
                    p_payload#>>'{regional_state_sha256,ovs}'))
          ORDER BY aggregate.aggregate_id FOR SHARE;
          SELECT count(*) INTO aggregate_count
          FROM public.domain_aggregate_revisions aggregate
          WHERE aggregate.aggregate_type='TEACHER'
            AND aggregate.canonical_key IN (
              jsonb_build_object(
                'source_region','dom','teacher_id',
                p_payload->>'teacher_id'
              ),
              jsonb_build_object(
                'source_region','ovs','teacher_id',
                p_payload->>'teacher_id'
              )
            )
            AND ((aggregate.canonical_key->>'source_region'='dom'
                  AND aggregate.revision=p_dom_revision
                  AND aggregate.aggregate_state_sha256=
                    p_payload#>>'{regional_state_sha256,dom}')
                 OR
                 (aggregate.canonical_key->>'source_region'='ovs'
                  AND aggregate.revision=p_ovs_revision
                  AND aggregate.aggregate_state_sha256=
                    p_payload#>>'{regional_state_sha256,ovs}'));
          IF aggregate_count<>2 THEN
            RAISE EXCEPTION
              'DTS_V2_TEACHER_TIME_RECHECK_REGIONAL_VECTOR_STALE'
              USING ERRCODE='40001';
          END IF;

          SELECT * INTO wide FROM public.teacher_source_wide
          WHERE tchr_id=p_payload->>'teacher_id' FOR UPDATE;
          IF NOT FOUND OR wide.v2_row_version IS NULL
             OR wide.v2_dom_aggregate_revision IS DISTINCT FROM p_dom_revision
             OR wide.v2_ovs_aggregate_revision IS DISTINCT FROM p_ovs_revision
             OR wide.v2_projection_generation>p_projection_generation THEN
            RAISE EXCEPTION
              'DTS_V2_TEACHER_TIME_RECHECK_SERVING_VECTOR_STALE'
              USING ERRCODE='40001';
          END IF;

          expected_job_days := CASE
            WHEN wide.onboard_date IS NULL THEN NULL
            WHEN coalesce(wide.status_off_date,business_date)
                   <wide.onboard_date THEN NULL
            ELSE coalesce(wide.status_off_date,business_date)
                   -wide.onboard_date
          END;
          expected_job_month := CASE
            WHEN expected_job_days IS NULL THEN NULL
            ELSE floor(expected_job_days::numeric/30)+1
          END;
          expected_online_status := CASE
            WHEN lower(btrim(wide.status))='off' THEN 'LEFT'
            WHEN lower(btrim(wide.status))='hei' THEN 'BLOCKED'
            WHEN lower(btrim(wide.status))='on'
                 AND wide.status_on_date IS NOT NULL
                 AND business_date-wide.status_on_date BETWEEN 0 AND 29
              THEN 'NEW'
            WHEN lower(btrim(wide.status))='on'
                 AND wide.status_on_date IS NOT NULL
                 AND business_date-wide.status_on_date>=30
              THEN 'EXISTING'
            ELSE NULL
          END;
          expected_online_evidence := CASE
            WHEN expected_online_status IS NULL THEN 'SOURCE_MISSING'
            ELSE 'CONFIRMED'
          END;
          BEGIN
            IF (p_payload#>>'{time_values,job_days}')::integer
                  IS DISTINCT FROM expected_job_days
               OR (p_payload#>>'{time_values,job_month}')::double precision
                  IS DISTINCT FROM expected_job_month
               OR p_payload#>>'{time_values,online_status}'
                  IS DISTINCT FROM expected_online_status
               OR p_payload#>>'{time_values,online_status_evidence_status}'
                  IS DISTINCT FROM expected_online_evidence THEN
              RAISE EXCEPTION
                'DTS_V2_TEACHER_TIME_RECHECK_VALUES_MISMATCH'
                USING ERRCODE='23514';
            END IF;
          EXCEPTION WHEN invalid_text_representation OR numeric_value_out_of_range
          THEN
            RAISE EXCEPTION
              'DTS_V2_TEACHER_TIME_RECHECK_VALUES_MISMATCH'
              USING ERRCODE='23514';
          END;

          plan_document := jsonb_build_object(
            'protocol','teacher-time-recheck-plan-v1','payload',p_payload,
            'dom_aggregate_revision',p_dom_revision,
            'ovs_aggregate_revision',p_ovs_revision,
            'projection_generation',p_projection_generation,
            'triggering_event_id',p_event_id,
            'claimed_work_revision',p_claimed_work_revision
          );
          plan_sha256 := public.dts_canonical_json_sha256_v1(plan_document);
          SELECT * INTO existing_result
          FROM public.dts_teacher_time_recheck_results
          WHERE teacher_id=p_payload->>'teacher_id'
            AND business_date_beijing=business_date
          FOR SHARE;
          IF FOUND THEN
            IF existing_result.plan_sha256<>plan_sha256 THEN
              RAISE EXCEPTION
                'DTS_V2_TEACHER_TIME_RECHECK_NONDETERMINISTIC'
                USING ERRCODE='23514';
            END IF;
            RETURN jsonb_build_object(
              'teacher_source_changes',0,
              'teacher_identity_changes',0,
              'time_recheck_result_changes',0
            );
          END IF;

          PERFORM set_config('tit.dts_v2_materializer','teacher',true);
          UPDATE public.teacher_source_wide
          SET job_days=expected_job_days,job_month=expected_job_month,
              v2_projection_generation=p_projection_generation,
              v2_materialized_event_id=p_event_id,
              v2_materialized_at=transaction_timestamp(),
              v2_row_version=v2_row_version+1
          WHERE tchr_id=p_payload->>'teacher_id'
            AND ROW(
              job_days,job_month,v2_projection_generation,
              v2_materialized_event_id
            ) IS DISTINCT FROM ROW(
              expected_job_days,expected_job_month,p_projection_generation,
              p_event_id
            );
          GET DIAGNOSTICS wide_changes=ROW_COUNT;

          UPDATE public.teachers
          SET camp_day=least(greatest(coalesce(expected_job_days,0),0),30),
              online_status=expected_online_status,
              payload=payload||jsonb_build_object(
                'teacher_time_recheck_v2',jsonb_build_object(
                  'business_date_beijing',business_date,
                  'online_status_evidence_status',expected_online_evidence,
                  'dom_aggregate_revision',p_dom_revision,
                  'ovs_aggregate_revision',p_ovs_revision,
                  'projection_generation',p_projection_generation,
                  'plan_sha256',plan_sha256
                )
              ),updated_at=transaction_timestamp()
          WHERE teacher_id=p_payload->>'teacher_id'
            AND ROW(
              camp_day,online_status,payload->'teacher_time_recheck_v2'
            ) IS DISTINCT FROM ROW(
              least(greatest(coalesce(expected_job_days,0),0),30),
              expected_online_status,
              jsonb_build_object(
                'business_date_beijing',business_date,
                'online_status_evidence_status',expected_online_evidence,
                'dom_aggregate_revision',p_dom_revision,
                'ovs_aggregate_revision',p_ovs_revision,
                'projection_generation',p_projection_generation,
                'plan_sha256',plan_sha256
              )
            );
          GET DIAGNOSTICS teacher_changes=ROW_COUNT;
          IF teacher_changes<>1 THEN
            RAISE EXCEPTION
              'DTS_V2_TEACHER_TIME_RECHECK_TEACHER_MISSING'
              USING ERRCODE='23503';
          END IF;

          INSERT INTO public.dts_teacher_time_recheck_results(
            teacher_id,business_date_beijing,teacher_id_type,
            dom_aggregate_revision,ovs_aggregate_revision,
            regional_state_sha256,projection_generation,
            triggering_event_id,claimed_work_revision,time_values,
            plan_sha256,materialized_at
          ) VALUES (
            p_payload->>'teacher_id',business_date,
            p_payload->>'teacher_id_type',p_dom_revision,p_ovs_revision,
            p_payload->'regional_state_sha256',p_projection_generation,
            p_event_id,p_claimed_work_revision,p_payload->'time_values',
            plan_sha256,transaction_timestamp()
          );
          GET DIAGNOSTICS result_changes=ROW_COUNT;
          RETURN jsonb_build_object(
            'teacher_source_changes',wide_changes,
            'teacher_identity_changes',teacher_changes,
            'time_recheck_result_changes',result_changes
          );
        END
        $function$;
        REVOKE ALL ON FUNCTION
          public.materialize_teacher_time_recheck_v2(
            jsonb,bigint,bigint,bigint,text,bigint
          ) FROM PUBLIC;

        CREATE FUNCTION public.teacher_time_recheck_result_proof_v1(
          p_teacher_id text,p_business_date date
        ) RETURNS jsonb
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
          SELECT jsonb_build_object(
            'teacher_id',result.teacher_id,
            'business_date_beijing',result.business_date_beijing,
            'dom_aggregate_revision',result.dom_aggregate_revision,
            'ovs_aggregate_revision',result.ovs_aggregate_revision,
            'regional_state_sha256',result.regional_state_sha256,
            'projection_generation',result.projection_generation,
            'triggering_event_id',result.triggering_event_id,
            'claimed_work_revision',result.claimed_work_revision,
            'time_values',result.time_values,
            'plan_sha256',result.plan_sha256
          )
          FROM public.dts_teacher_time_recheck_results result
          WHERE result.teacher_id=p_teacher_id
            AND result.business_date_beijing=p_business_date
        $function$;
        REVOKE ALL ON FUNCTION
          public.teacher_time_recheck_result_proof_v1(text,date)
        FROM PUBLIC;
        """
    )


def _install_health() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.dts_v2_teacher_time_recheck_health_v1(
          p_stale_after_seconds bigint
        ) RETURNS jsonb
        LANGUAGE plpgsql
        STABLE
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE control_count integer;
        DECLARE control_mode text;
        DECLARE generation bigint;
        DECLARE local_now timestamp;
        DECLARE business_date date;
        DECLARE due boolean;
        DECLARE missing_count bigint := 0;
        DECLARE runnable bigint := 0;
        DECLARE active bigint := 0;
        DECLARE expired bigint := 0;
        DECLARE dead bigint := 0;
        DECLARE stale bigint := 0;
        DECLARE oldest bigint;
        BEGIN
          IF p_stale_after_seconds NOT BETWEEN 1 AND 86400 THEN
            RAISE EXCEPTION 'DTS_V2_TIME_RECHECK_HEALTH_THRESHOLD_INVALID'
              USING ERRCODE='22023';
          END IF;
          PERFORM pg_advisory_xact_lock_shared(
            hashtextextended('tit:dts-v2-cutover',0)
          );
          SELECT count(*) INTO control_count
          FROM public.dts_pipeline_control;
          SELECT mode,projection_generation INTO control_mode,generation
          FROM public.dts_pipeline_control WHERE control_id='PRIMARY';
          IF control_count<>1 OR control_mode NOT IN (
               'V1_COMPAT_DUAL_CAPTURE','V2_PRIMARY','ROLLED_BACK'
             ) OR generation IS NULL OR generation<0 THEN
            RAISE EXCEPTION 'DTS_V2_PIPELINE_CONTROL_STATE_INVALID'
              USING ERRCODE='55000';
          END IF;
          local_now := transaction_timestamp()
            AT TIME ZONE 'Asia/Shanghai';
          business_date := local_now::date;
          SELECT control_mode='V2_PRIMARY'
                 AND local_now::time>=time '00:05:00'
                 AND schedule.last_enqueued_business_date
                       IS DISTINCT FROM business_date
          INTO due
          FROM public.dts_teacher_time_recheck_schedule schedule
          WHERE schedule_id='PRIMARY';
          IF due IS NULL THEN
            RAISE EXCEPTION 'DTS_V2_TIME_RECHECK_SCHEDULE_SINGLETON_REQUIRED'
              USING ERRCODE='55000';
          END IF;
          IF control_mode='V2_PRIMARY'
             AND local_now::time>=time '00:05:00' THEN
            SELECT count(*) INTO missing_count
            FROM public.teacher_source_wide wide
            WHERE wide.v2_row_version IS NOT NULL
              AND NOT EXISTS (
                SELECT 1
                FROM public.dts_teacher_time_recheck_results result
                WHERE result.teacher_id=wide.tchr_id
                  AND result.business_date_beijing=business_date
              );
            SELECT
              count(*) FILTER (
                WHERE status IN ('PENDING','RETRY')
                  AND next_attempt_at<=transaction_timestamp()
              ),
              count(*) FILTER (WHERE status='PROCESSING'),
              count(*) FILTER (
                WHERE status='PROCESSING'
                  AND lease_expires_at<=transaction_timestamp()
              ),
              count(*) FILTER (WHERE status='DEAD'),
              count(*) FILTER (
                WHERE status IN ('PENDING','RETRY')
                  AND next_attempt_at<=transaction_timestamp()
                  AND updated_at<=transaction_timestamp()
                    -make_interval(secs=>p_stale_after_seconds::integer)
              ),
              floor(extract(epoch FROM transaction_timestamp()
                    -min(updated_at) FILTER (
                      WHERE status IN ('PENDING','RETRY')
                        AND next_attempt_at<=transaction_timestamp()
                    )))::bigint
            INTO runnable,active,expired,dead,stale,oldest
            FROM public.dts_dirty_keys
            WHERE source_region='dom'
              AND key_type='TEACHER_TIME_RECHECK'
              AND key_part_2=to_char(business_date,'YYYY-MM-DD');
          END IF;
          RETURN jsonb_build_object(
            'protocol_version',
              'dts-v2-teacher-time-recheck-health-v1',
            'mode',control_mode,'projection_generation',generation,
            'schedule_due',due,
            'current_date_missing_count',coalesce(missing_count,0),
            'runnable_count',coalesce(runnable,0),
            'active_lease_count',coalesce(active,0),
            'expired_lease_count',coalesce(expired,0),
            'dead_count',coalesce(dead,0),
            'stale_runnable_count',coalesce(stale,0),
            'oldest_runnable_age_seconds',oldest
          );
        END
        $function$;
        REVOKE ALL ON FUNCTION
          public.dts_v2_teacher_time_recheck_health_v1(bigint)
        FROM PUBLIC;
        """
    )


def _apply_acl() -> None:
    op.execute(
        f"""
        REVOKE ALL PRIVILEGES ON TABLE
          public.dts_teacher_time_recheck_schedule,
          public.dts_teacher_time_recheck_results,
          public.dts_teacher_time_recheck_audits
        FROM PUBLIC,tit_growth_app,tit_dts_ingest_runtime,
          tit_dts_domain_projector_runtime,{OUTBOX_ROLE};

        -- The application rebuilds the date-derived plan from the same two
        -- immutable TEACHER aggregate states used by the regular Outbox
        -- processor.  Read-only access is therefore required; every write
        -- remains behind a SECURITY DEFINER command below.
        REVOKE ALL PRIVILEGES ON TABLE public.domain_aggregate_revisions
          FROM {OUTBOX_ROLE};
        GRANT SELECT ON TABLE public.domain_aggregate_revisions
          TO {OUTBOX_ROLE};

        GRANT EXECUTE ON FUNCTION
          public.enqueue_due_teacher_time_rechecks_v2(),
          public.claim_teacher_time_rechecks_v2(text,integer,integer),
          public.complete_teacher_time_recheck_v2(
            text,date,text,bigint,bigint
          ),
          public.fail_teacher_time_recheck_v2(
            text,date,text,bigint,bigint,text
          ),
          public.reap_expired_teacher_time_rechecks_v2(integer),
          public.materialize_teacher_time_recheck_v2(
            jsonb,bigint,bigint,bigint,text,bigint
          ),
          public.teacher_time_recheck_result_proof_v1(text,date),
          public.dts_v2_teacher_time_recheck_health_v1(bigint)
        TO {OUTBOX_ROLE};

        REVOKE ALL ON FUNCTION
          public.enqueue_due_teacher_time_rechecks_v2(),
          public.claim_teacher_time_rechecks_v2(text,integer,integer),
          public.complete_teacher_time_recheck_v2(
            text,date,text,bigint,bigint
          ),
          public.fail_teacher_time_recheck_v2(
            text,date,text,bigint,bigint,text
          ),
          public.reap_expired_teacher_time_rechecks_v2(integer),
          public.materialize_teacher_time_recheck_v2(
            jsonb,bigint,bigint,bigint,text,bigint
          ),
          public.teacher_time_recheck_result_proof_v1(text,date),
          public.dts_v2_teacher_time_recheck_health_v1(bigint)
        FROM tit_growth_app,tit_dts_ingest_runtime,
          tit_dts_domain_projector_runtime;

        DO $teacher_time_recheck_acl$
        BEGIN
          IF has_table_privilege(
               '{OUTBOX_ROLE}',
               'public.dts_teacher_time_recheck_results','SELECT'
             ) OR has_table_privilege(
               '{OUTBOX_ROLE}',
               'public.dts_teacher_time_recheck_results','INSERT'
             ) OR has_table_privilege(
               '{OUTBOX_ROLE}','public.dts_dirty_keys','UPDATE'
             ) OR NOT has_table_privilege(
               '{OUTBOX_ROLE}',
               'public.domain_aggregate_revisions','SELECT'
             ) OR has_table_privilege(
               '{OUTBOX_ROLE}',
               'public.domain_aggregate_revisions','UPDATE'
             ) OR NOT has_function_privilege(
               '{OUTBOX_ROLE}',
               'public.materialize_teacher_time_recheck_v2(jsonb,bigint,bigint,bigint,text,bigint)',
               'EXECUTE'
             ) OR has_function_privilege(
               'tit_growth_app',
               'public.materialize_teacher_time_recheck_v2(jsonb,bigint,bigint,bigint,text,bigint)',
               'EXECUTE'
             ) THEN
            RAISE EXCEPTION 'DTS_V2_TEACHER_TIME_RECHECK_ACL_INVALID';
          END IF;
        END
        $teacher_time_recheck_acl$;
        """
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    _preflight()
    _create_tables()
    _install_immutability()
    _install_enqueue()
    _install_claim_and_settlement()
    _install_materializer()
    _install_health()
    _apply_acl()


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        r"""
        LOCK TABLE public.dts_dirty_keys,
          public.dts_teacher_time_recheck_results,
          public.dts_teacher_time_recheck_audits,
          public.dts_teacher_time_recheck_schedule
        IN ACCESS EXCLUSIVE MODE;
        DO $teacher_time_recheck_downgrade_guard$
        BEGIN
          IF EXISTS (
               SELECT 1 FROM public.dts_dirty_keys
               WHERE key_type='TEACHER_TIME_RECHECK'
             ) OR EXISTS (
               SELECT 1 FROM public.dts_teacher_time_recheck_results
             ) OR EXISTS (
               SELECT 1 FROM public.dts_teacher_time_recheck_audits
             ) OR EXISTS (
               SELECT 1 FROM public.dts_teacher_time_recheck_schedule
               WHERE last_enqueued_business_date IS NOT NULL
             ) THEN
            RAISE EXCEPTION
              'refusing teacher time recheck downgrade: runtime evidence exists';
          END IF;
        END
        $teacher_time_recheck_downgrade_guard$;

        REVOKE SELECT ON TABLE public.domain_aggregate_revisions
          FROM tit_dts_outbox_worker_runtime;

        DROP TRIGGER trg_enqueue_teacher_time_recheck_after_wide_v2
          ON public.teacher_source_wide;
        DROP FUNCTION public.enqueue_teacher_time_recheck_after_wide_v2();
        DROP FUNCTION public.dts_v2_teacher_time_recheck_health_v1(bigint);
        DROP FUNCTION public.teacher_time_recheck_result_proof_v1(text,date);
        DROP FUNCTION public.materialize_teacher_time_recheck_v2(
          jsonb,bigint,bigint,bigint,text,bigint
        );
        DROP FUNCTION public.reap_expired_teacher_time_rechecks_v2(integer);
        DROP FUNCTION public.fail_teacher_time_recheck_v2(
          text,date,text,bigint,bigint,text
        );
        DROP FUNCTION public.complete_teacher_time_recheck_v2(
          text,date,text,bigint,bigint
        );
        DROP FUNCTION public.claim_teacher_time_rechecks_v2(
          text,integer,integer
        );
        DROP FUNCTION public.enqueue_due_teacher_time_rechecks_v2();
        DROP FUNCTION public._enqueue_teacher_time_recheck_key_v2(text,date);

        DROP TRIGGER trg_guard_teacher_time_recheck_audit_append_only_v1
          ON public.dts_teacher_time_recheck_audits;
        DROP TRIGGER trg_guard_teacher_time_recheck_result_append_only_v1
          ON public.dts_teacher_time_recheck_results;
        DROP FUNCTION public.guard_teacher_time_recheck_append_only_v1();
        """
    )
    op.drop_table("dts_teacher_time_recheck_audits", schema="public")
    op.drop_index(
        "ix_dts_teacher_time_recheck_result_date",
        table_name="dts_teacher_time_recheck_results",
        schema="public",
    )
    op.drop_table("dts_teacher_time_recheck_results", schema="public")
    op.drop_table("dts_teacher_time_recheck_schedule", schema="public")
