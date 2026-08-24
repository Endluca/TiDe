"""split v1 compatibility work and protect irreversible qualification grants.

Revision ID: 20260822_96_compat_queue_gate
Revises: 20260822_95_projection_cutover
Create Date: 2026-08-22
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260822_96_compat_queue_gate"
down_revision: Union[str, None] = "20260822_95_projection_cutover"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


CUTOVER_ROLE = "tit_dts_projection_cutover_runtime"
INGEST_ROLE = "tit_dts_ingest_runtime"
OUTBOX_ROLE = "tit_dts_outbox_worker_runtime"
COMPAT_KEY_TYPES = (
    "COURSE",
    "TEACHER",
    "TEACHER_STUDENT",
    "LABEL",
    "COMPLAINT_CATEGORY",
)


def _preflight_and_columns() -> None:
    key_types = ",".join(f"'{value}'" for value in COMPAT_KEY_TYPES)
    op.execute(
        f"""
        DO $compat_gate_preflight$
        DECLARE relation_name text;
        BEGIN
          FOREACH relation_name IN ARRAY ARRAY[
            'dts_pipeline_control','dts_projection_read_routes',
            'dts_dirty_keys','dts_dirty_key_inputs',
            'teacher_qualifications'
          ] LOOP
            IF to_regclass('public.' || relation_name) IS NULL THEN
              RAISE EXCEPTION
                'DTS_V1_COMPAT_GATE_PREREQUISITE_MISSING:%',relation_name;
            END IF;
          END LOOP;
          IF to_regprocedure(
               'public._upsert_dts_dirty_key_input_v2('
               'text,text,text,text,text,jsonb,bigint,text)'
             ) IS NULL
             OR to_regrole('{CUTOVER_ROLE}') IS NULL
             OR to_regrole('{INGEST_ROLE}') IS NULL
             OR to_regrole('{OUTBOX_ROLE}') IS NULL THEN
            RAISE EXCEPTION 'DTS_V1_COMPAT_GATE_PREREQUISITE_MISSING';
          END IF;
        END
        $compat_gate_preflight$;

        ALTER TABLE public.dts_pipeline_control
          ADD COLUMN IF NOT EXISTS qualification_grants_enabled boolean
          NOT NULL DEFAULT false;
        COMMENT ON COLUMN
          public.dts_pipeline_control.qualification_grants_enabled IS
          'Database-authoritative gate for first irreversible graduation '
          'and gold grants';

        ALTER TABLE public.dts_dirty_keys
          ADD COLUMN compat_status varchar(16) NOT NULL DEFAULT 'STANDBY',
          ADD COLUMN compat_required_work_revision bigint NOT NULL DEFAULT 0,
          ADD COLUMN compat_claimed_work_revision bigint,
          ADD COLUMN compat_completed_work_revision bigint NOT NULL DEFAULT 0,
          ADD COLUMN compat_attempt_count integer NOT NULL DEFAULT 0,
          ADD COLUMN compat_last_error_code varchar(128),
          ADD COLUMN compat_next_attempt_at timestamptz,
          ADD COLUMN compat_claimed_at timestamptz,
          ADD COLUMN compat_claimed_by varchar(128),
          ADD COLUMN compat_row_version bigint NOT NULL DEFAULT 1;

        UPDATE public.dts_dirty_keys AS dirty
        SET compat_required_work_revision=dirty.required_work_revision,
            compat_claimed_work_revision=NULL,
            compat_completed_work_revision=0,
            compat_status=CASE WHEN coalesce(
              (SELECT mode FROM public.dts_pipeline_control
               WHERE control_id='PRIMARY'),'V2_PRIMARY'
            ) IN ('V1_COMPAT_DUAL_CAPTURE','ROLLED_BACK')
              AND dirty.key_type IN ({key_types})
              THEN 'PENDING' ELSE 'STANDBY' END,
            compat_attempt_count=0,compat_last_error_code=NULL,
            compat_next_attempt_at=CASE WHEN coalesce(
              (SELECT mode FROM public.dts_pipeline_control
               WHERE control_id='PRIMARY'),'V2_PRIMARY'
            ) IN ('V1_COMPAT_DUAL_CAPTURE','ROLLED_BACK')
              AND dirty.key_type IN ({key_types})
              THEN transaction_timestamp() ELSE NULL END,
            compat_claimed_at=NULL,compat_claimed_by=NULL,
            compat_row_version=1;

        ALTER TABLE public.dts_dirty_keys
          ADD CONSTRAINT ck_dts_dirty_key_compat_state_v1 CHECK (
            compat_status IN (
              'STANDBY','PENDING','PROCESSING','RETRY','DEAD','COMPLETED'
            )
            AND compat_required_work_revision>=0
            AND compat_completed_work_revision>=0
            AND compat_completed_work_revision<=compat_required_work_revision
            AND (compat_claimed_work_revision IS NULL OR
                 (compat_claimed_work_revision>=1 AND
                  compat_claimed_work_revision<=
                    compat_required_work_revision))
            AND compat_attempt_count BETWEEN 0 AND 100
            AND compat_row_version>=1
            AND (
              (compat_status='STANDBY' AND compat_attempt_count=0
               AND compat_claimed_work_revision IS NULL
               AND compat_next_attempt_at IS NULL
               AND compat_claimed_at IS NULL AND compat_claimed_by IS NULL
               AND compat_last_error_code IS NULL)
              OR (compat_status='PENDING' AND compat_attempt_count=0
               AND compat_claimed_work_revision IS NULL
               AND compat_next_attempt_at IS NOT NULL
               AND compat_claimed_at IS NULL AND compat_claimed_by IS NULL
               AND compat_last_error_code IS NULL)
              OR (compat_status='PROCESSING'
               AND compat_claimed_work_revision IS NOT NULL
               AND compat_next_attempt_at IS NULL
               AND compat_claimed_at IS NOT NULL
               AND nullif(btrim(compat_claimed_by),'') IS NOT NULL
               AND compat_last_error_code IS NULL)
              OR (compat_status='RETRY' AND compat_attempt_count>=1
               AND compat_claimed_work_revision IS NULL
               AND compat_next_attempt_at IS NOT NULL
               AND compat_claimed_at IS NULL AND compat_claimed_by IS NULL
               AND compat_last_error_code IS NOT NULL)
              OR (compat_status='DEAD' AND compat_attempt_count>=1
               AND compat_claimed_work_revision IS NULL
               AND compat_next_attempt_at IS NULL
               AND compat_claimed_at IS NULL AND compat_claimed_by IS NULL
               AND compat_last_error_code IS NOT NULL)
              OR (compat_status='COMPLETED' AND compat_attempt_count=0
               AND compat_completed_work_revision>=
                    compat_required_work_revision
               AND compat_claimed_work_revision IS NULL
               AND compat_next_attempt_at IS NULL
               AND compat_claimed_at IS NULL AND compat_claimed_by IS NULL
               AND compat_last_error_code IS NULL)
            )
          );

        CREATE INDEX ix_dts_dirty_keys_compat_pending_v1
          ON public.dts_dirty_keys(
            updated_at,source_region,key_type,key_part_1,key_part_2
          ) WHERE compat_status='PENDING';
        CREATE INDEX ix_dts_dirty_keys_compat_retry_v1
          ON public.dts_dirty_keys(
            compat_next_attempt_at,updated_at,source_region,key_type,
            key_part_1,key_part_2
          ) WHERE compat_status='RETRY';
        """
    )


def _create_gate_audit() -> None:
    op.create_table(
        "dts_qualification_gate_commands",
        sa.Column("command_id", sa.String(length=128), nullable=False),
        sa.Column("requested_enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "expected_control_row_version", sa.BigInteger(), nullable=False
        ),
        sa.Column(
            "resulting_control_row_version", sa.BigInteger(), nullable=False
        ),
        sa.Column("reason", sa.String(length=512), nullable=False),
        sa.Column("command_sha256", sa.String(length=64), nullable=False),
        sa.Column("fanout_count", sa.BigInteger(), nullable=False),
        sa.Column("result_status", sa.String(length=16), nullable=False),
        sa.Column(
            "executed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column("executed_by", sa.String(length=128), nullable=False),
        sa.PrimaryKeyConstraint(
            "command_id", name="pk_dts_qualification_gate_commands"
        ),
        sa.CheckConstraint(
            "expected_control_row_version>=1 "
            "AND resulting_control_row_version>=expected_control_row_version "
            "AND nullif(btrim(reason),'') IS NOT NULL "
            "AND command_sha256 ~ '^[0-9a-f]{64}$' "
            "AND fanout_count>=0 AND result_status='APPLIED'",
            name="ck_dts_qualification_gate_command_shape",
        ),
        schema="public",
        comment=(
            "Immutable CAS commands for the database-authoritative "
            "irreversible qualification grant gate"
        ),
    )


def _install_compat_dirty_guard() -> None:
    op.execute(
        """
        DROP TRIGGER IF EXISTS guard_dts_runtime_state_write
          ON public.dts_dirty_keys;
        CREATE FUNCTION public.guard_dts_dirty_key_state_write_v96()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE actor_name text:=coalesce(
          nullif(current_setting('role',true),'none'),session_user
        );
        DECLARE compat_columns text[]:=ARRAY[
          'compat_status','compat_required_work_revision',
          'compat_claimed_work_revision','compat_completed_work_revision',
          'compat_attempt_count','compat_last_error_code',
          'compat_next_attempt_at','compat_claimed_at','compat_claimed_by',
          'compat_row_version'
        ];
        BEGIN
          IF TG_OP<>'DELETE' AND NEW.last_source_region='dom'
             AND NEW.key_type='TEACHER_STUDENT'
             AND NEW.key_part_2 !~ '^dom:v1:[0-9a-f]{64}$' THEN
            RAISE EXCEPTION
              'domestic DTS dirty state contains a forbidden student identifier'
              USING ERRCODE='23514';
          END IF;
          IF current_setting(
               'tit.dts_v1_compat_state_write',true
             )='on' THEN
            IF TG_OP<>'UPDATE'
               OR (to_jsonb(NEW)-compat_columns) IS DISTINCT FROM
                  (to_jsonb(OLD)-compat_columns) THEN
              RAISE EXCEPTION 'DTS_V1_COMPAT_WRITE_SCOPE_INVALID'
                USING ERRCODE='42501';
            END IF;
            RETURN NEW;
          END IF;
          IF actor_name<>'tit_dts_ingest_runtime' THEN
            IF TG_OP='DELETE' THEN RETURN OLD; END IF;
            RETURN NEW;
          END IF;
          IF TG_OP='DELETE' THEN
            RAISE EXCEPTION 'DTS durable state cannot be physically deleted'
              USING ERRCODE='42501';
          END IF;
          IF TG_OP='INSERT' THEN RETURN NEW; END IF;
          IF NEW.key_type IS DISTINCT FROM OLD.key_type
             OR NEW.key_part_1 IS DISTINCT FROM OLD.key_part_1
             OR NEW.key_part_2 IS DISTINCT FROM OLD.key_part_2
             OR NEW.row_version<>OLD.row_version+1 THEN
            RAISE EXCEPTION 'DTS dirty-key identity or version is invalid'
              USING ERRCODE='23514';
          END IF;
          RETURN NEW;
        END
        $function$;
        REVOKE ALL ON FUNCTION
          public.guard_dts_dirty_key_state_write_v96()
        FROM PUBLIC;
        CREATE TRIGGER guard_dts_runtime_state_write
        BEFORE INSERT OR UPDATE OR DELETE ON public.dts_dirty_keys
        FOR EACH ROW
        EXECUTE FUNCTION public.guard_dts_dirty_key_state_write_v96();
        """
    )


def _install_compat_state_machine() -> None:
    key_types = ",".join(f"'{value}'" for value in COMPAT_KEY_TYPES)
    op.execute(
        f"""
        CREATE FUNCTION public.sync_v1_compat_dirty_input_v1()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE pipeline_mode text;
        BEGIN
          IF NEW.key_type NOT IN ({key_types}) THEN
            RETURN NEW;
          END IF;
          SELECT mode INTO pipeline_mode FROM public.dts_pipeline_control
          WHERE control_id='PRIMARY';
          PERFORM set_config('tit.dts_v1_compat_state_write','on',true);
          UPDATE public.dts_dirty_keys AS dirty
          SET compat_required_work_revision=greatest(
                dirty.compat_required_work_revision,
                NEW.dirty_work_revision
              ),
              compat_status=CASE
                WHEN pipeline_mode='V2_PRIMARY' THEN 'STANDBY'
                WHEN pipeline_mode IN (
                     'V1_COMPAT_DUAL_CAPTURE','ROLLED_BACK'
                   ) AND dirty.compat_status='PROCESSING'
                  THEN 'PROCESSING'
                WHEN pipeline_mode IN (
                     'V1_COMPAT_DUAL_CAPTURE','ROLLED_BACK'
                   ) THEN 'PENDING'
                ELSE 'STANDBY'
              END,
              compat_claimed_work_revision=CASE
                WHEN pipeline_mode IN (
                     'V1_COMPAT_DUAL_CAPTURE','ROLLED_BACK'
                   ) AND dirty.compat_status='PROCESSING'
                  THEN dirty.compat_claimed_work_revision ELSE NULL END,
              compat_attempt_count=CASE
                WHEN pipeline_mode IN (
                     'V1_COMPAT_DUAL_CAPTURE','ROLLED_BACK'
                   ) AND dirty.compat_status='PROCESSING'
                  THEN dirty.compat_attempt_count ELSE 0 END,
              compat_last_error_code=NULL,
              compat_next_attempt_at=CASE
                WHEN pipeline_mode IN (
                     'V1_COMPAT_DUAL_CAPTURE','ROLLED_BACK'
                   ) AND dirty.compat_status='PROCESSING' THEN NULL
                WHEN pipeline_mode IN (
                     'V1_COMPAT_DUAL_CAPTURE','ROLLED_BACK'
                   ) THEN transaction_timestamp()
                ELSE NULL END,
              compat_claimed_at=CASE
                WHEN pipeline_mode IN (
                     'V1_COMPAT_DUAL_CAPTURE','ROLLED_BACK'
                   ) AND dirty.compat_status='PROCESSING'
                  THEN dirty.compat_claimed_at ELSE NULL END,
              compat_claimed_by=CASE
                WHEN pipeline_mode IN (
                     'V1_COMPAT_DUAL_CAPTURE','ROLLED_BACK'
                   ) AND dirty.compat_status='PROCESSING'
                  THEN dirty.compat_claimed_by ELSE NULL END,
              compat_row_version=dirty.compat_row_version+1
          WHERE dirty.source_region=NEW.source_region
            AND dirty.key_type=NEW.key_type
            AND dirty.key_part_1=NEW.key_part_1
            AND dirty.key_part_2=NEW.key_part_2;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'DTS_V1_COMPAT_DIRTY_KEY_MISSING'
              USING ERRCODE='23503';
          END IF;
          PERFORM set_config('tit.dts_v1_compat_state_write','off',true);
          RETURN NEW;
        END
        $function$;

        CREATE TRIGGER trg_sync_v1_compat_dirty_input_v1
        AFTER INSERT ON public.dts_dirty_key_inputs
        FOR EACH ROW EXECUTE FUNCTION public.sync_v1_compat_dirty_input_v1();

        CREATE FUNCTION public.sync_v1_compat_pipeline_mode_v1()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        BEGIN
          IF TG_OP='UPDATE' AND NEW.mode IS NOT DISTINCT FROM OLD.mode THEN
            RETURN NEW;
          END IF;
          UPDATE public.dts_dirty_keys AS dirty
          SET compat_status=CASE
                WHEN NEW.mode='V2_PRIMARY' THEN 'STANDBY'
                WHEN dirty.compat_completed_work_revision>=
                     dirty.compat_required_work_revision THEN 'COMPLETED'
                ELSE 'PENDING' END,
              compat_claimed_work_revision=NULL,
              compat_attempt_count=0,compat_last_error_code=NULL,
              compat_next_attempt_at=CASE
                WHEN NEW.mode<>'V2_PRIMARY'
                 AND dirty.compat_completed_work_revision<
                     dirty.compat_required_work_revision
                  THEN transaction_timestamp() ELSE NULL END,
              compat_claimed_at=NULL,compat_claimed_by=NULL,
              compat_row_version=dirty.compat_row_version+1
          WHERE dirty.key_type IN ({key_types});
          RETURN NEW;
        END
        $function$;

        CREATE TRIGGER trg_sync_v1_compat_pipeline_mode_v1
        AFTER INSERT OR UPDATE OF mode ON public.dts_pipeline_control
        FOR EACH ROW EXECUTE FUNCTION public.sync_v1_compat_pipeline_mode_v1();

        CREATE FUNCTION public.claim_v1_compat_dirty_key_v1(
          p_worker_id text
        ) RETURNS TABLE(
          source_region text,key_type text,key_part_1 text,key_part_2 text,
          compat_claimed_work_revision bigint,compat_row_version bigint
        )
        LANGUAGE plpgsql
        VOLATILE
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE candidate record;
        DECLARE pipeline_mode text;
        BEGIN
          IF nullif(btrim(p_worker_id),'') IS NULL
             OR length(p_worker_id)>128 THEN
            RAISE EXCEPTION 'DTS_V1_COMPAT_WORKER_ID_INVALID'
              USING ERRCODE='22023';
          END IF;
          SELECT mode INTO pipeline_mode FROM public.dts_pipeline_control
          WHERE control_id='PRIMARY' FOR SHARE;
          IF pipeline_mode NOT IN (
               'V1_COMPAT_DUAL_CAPTURE','ROLLED_BACK'
             ) THEN
            RETURN;
          END IF;
          SELECT dirty.source_region,dirty.key_type,dirty.key_part_1,
                 dirty.key_part_2
          INTO candidate
          FROM public.dts_dirty_keys AS dirty
          WHERE dirty.key_type IN ({key_types})
            AND dirty.compat_status IN ('PENDING','RETRY')
            AND dirty.compat_next_attempt_at<=transaction_timestamp()
          ORDER BY dirty.compat_next_attempt_at,dirty.updated_at,
                   convert_to(dirty.source_region,'UTF8'),
                   convert_to(dirty.key_type,'UTF8'),
                   convert_to(dirty.key_part_1,'UTF8'),
                   convert_to(dirty.key_part_2,'UTF8')
          FOR UPDATE SKIP LOCKED LIMIT 1;
          IF NOT FOUND THEN RETURN; END IF;
          PERFORM set_config('tit.dts_v1_compat_state_write','on',true);
          RETURN QUERY
          UPDATE public.dts_dirty_keys AS dirty
          SET compat_status='PROCESSING',
              compat_claimed_work_revision=
                dirty.compat_required_work_revision,
              compat_next_attempt_at=NULL,compat_last_error_code=NULL,
              compat_claimed_at=transaction_timestamp(),
              compat_claimed_by=p_worker_id,
              compat_row_version=dirty.compat_row_version+1
          WHERE dirty.source_region=candidate.source_region
            AND dirty.key_type=candidate.key_type
            AND dirty.key_part_1=candidate.key_part_1
            AND dirty.key_part_2=candidate.key_part_2
          RETURNING dirty.source_region::text,dirty.key_type::text,
            dirty.key_part_1::text,dirty.key_part_2::text,
            dirty.compat_claimed_work_revision,dirty.compat_row_version;
          PERFORM set_config('tit.dts_v1_compat_state_write','off',true);
        END
        $function$;

        CREATE FUNCTION public.complete_v1_compat_dirty_key_v1(
          p_worker_id text,p_source_region text,p_key_type text,
          p_key_part_1 text,p_key_part_2 text,
          p_claimed_work_revision bigint,p_expected_row_version bigint
        ) RETURNS jsonb
        LANGUAGE plpgsql
        VOLATILE
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE dirty public.dts_dirty_keys%ROWTYPE;
        BEGIN
          IF nullif(btrim(p_worker_id),'') IS NULL
             OR p_source_region IS NULL OR p_key_type IS NULL
             OR p_key_part_1 IS NULL OR p_key_part_2 IS NULL
             OR p_claimed_work_revision IS NULL
             OR p_claimed_work_revision<1
             OR p_expected_row_version IS NULL
             OR p_expected_row_version<1 THEN
            RAISE EXCEPTION 'DTS_V1_COMPAT_COMPLETION_REQUEST_INVALID'
              USING ERRCODE='22023';
          END IF;
          SELECT * INTO dirty FROM public.dts_dirty_keys
          WHERE source_region=p_source_region AND key_type=p_key_type
            AND key_part_1=p_key_part_1 AND key_part_2=p_key_part_2
          FOR UPDATE;
          IF NOT FOUND OR dirty.compat_status<>'PROCESSING'
             OR dirty.compat_claimed_by IS DISTINCT FROM p_worker_id
             OR dirty.compat_claimed_work_revision IS DISTINCT FROM
                  p_claimed_work_revision
             OR dirty.compat_row_version<p_expected_row_version THEN
            RAISE EXCEPTION 'DTS_V1_COMPAT_CLAIM_LOST'
              USING ERRCODE='40001';
          END IF;
          PERFORM set_config('tit.dts_v1_compat_state_write','on',true);
          UPDATE public.dts_dirty_keys AS target
          SET compat_status=CASE WHEN target.compat_required_work_revision>
                    p_claimed_work_revision THEN 'PENDING' ELSE 'COMPLETED' END,
              compat_completed_work_revision=greatest(
                target.compat_completed_work_revision,
                p_claimed_work_revision
              ),
              compat_claimed_work_revision=NULL,
              compat_attempt_count=0,compat_last_error_code=NULL,
              compat_next_attempt_at=CASE WHEN
                target.compat_required_work_revision>p_claimed_work_revision
                THEN transaction_timestamp() ELSE NULL END,
              compat_claimed_at=NULL,compat_claimed_by=NULL,
              compat_row_version=target.compat_row_version+1
          WHERE target.source_region=p_source_region
            AND target.key_type=p_key_type
            AND target.key_part_1=p_key_part_1
            AND target.key_part_2=p_key_part_2
          RETURNING * INTO dirty;
          PERFORM set_config('tit.dts_v1_compat_state_write','off',true);
          RETURN jsonb_build_object(
            'status',dirty.compat_status,
            'completed_work_revision',
              dirty.compat_completed_work_revision,
            'row_version',dirty.compat_row_version
          );
        END
        $function$;

        CREATE FUNCTION public.fail_v1_compat_dirty_key_v1(
          p_worker_id text,p_source_region text,p_key_type text,
          p_key_part_1 text,p_key_part_2 text,p_error_code text,
          p_max_attempts integer,p_retry_base_seconds integer,
          p_retry_max_seconds integer
        ) RETURNS jsonb
        LANGUAGE plpgsql
        VOLATILE
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE dirty public.dts_dirty_keys%ROWTYPE;
        DECLARE next_attempt integer;
        DECLARE retry_seconds integer;
        DECLARE pipeline_mode text;
        BEGIN
          IF nullif(btrim(p_worker_id),'') IS NULL
             OR nullif(btrim(p_error_code),'') IS NULL
             OR length(p_error_code)>128
             OR p_source_region IS NULL OR p_key_type IS NULL
             OR p_key_part_1 IS NULL OR p_key_part_2 IS NULL
             OR p_max_attempts IS NULL
             OR p_retry_base_seconds IS NULL
             OR p_retry_max_seconds IS NULL
             OR p_max_attempts NOT BETWEEN 1 AND 100
             OR p_retry_base_seconds NOT BETWEEN 1 AND 3600
             OR p_retry_max_seconds NOT BETWEEN p_retry_base_seconds AND 86400
          THEN
            RAISE EXCEPTION 'DTS_V1_COMPAT_FAILURE_REQUEST_INVALID'
              USING ERRCODE='22023';
          END IF;
          SELECT mode INTO pipeline_mode FROM public.dts_pipeline_control
          WHERE control_id='PRIMARY' FOR SHARE;
          IF pipeline_mode NOT IN (
               'V1_COMPAT_DUAL_CAPTURE','ROLLED_BACK'
             ) THEN
            RAISE EXCEPTION 'DTS_V1_COMPAT_MODE_INACTIVE'
              USING ERRCODE='55000';
          END IF;
          SELECT * INTO dirty FROM public.dts_dirty_keys
          WHERE source_region=p_source_region AND key_type=p_key_type
            AND key_part_1=p_key_part_1 AND key_part_2=p_key_part_2
          FOR UPDATE;
          IF NOT FOUND OR dirty.compat_status NOT IN ('PENDING','RETRY') THEN
            RAISE EXCEPTION 'DTS_V1_COMPAT_FAILURE_STATE_CONFLICT'
              USING ERRCODE='40001';
          END IF;
          next_attempt:=dirty.compat_attempt_count+1;
          retry_seconds:=least(
            p_retry_max_seconds,
            p_retry_base_seconds*(1<<least(next_attempt-1,8))
          );
          PERFORM set_config('tit.dts_v1_compat_state_write','on',true);
          UPDATE public.dts_dirty_keys AS target
          SET compat_status=CASE WHEN next_attempt>=p_max_attempts
                THEN 'DEAD' ELSE 'RETRY' END,
              compat_attempt_count=next_attempt,
              compat_last_error_code=p_error_code,
              compat_next_attempt_at=CASE WHEN next_attempt>=p_max_attempts
                THEN NULL ELSE transaction_timestamp()+
                  make_interval(secs=>retry_seconds) END,
              compat_claimed_work_revision=NULL,
              compat_claimed_at=NULL,compat_claimed_by=NULL,
              compat_row_version=target.compat_row_version+1
          WHERE target.source_region=p_source_region
            AND target.key_type=p_key_type
            AND target.key_part_1=p_key_part_1
            AND target.key_part_2=p_key_part_2
          RETURNING * INTO dirty;
          PERFORM set_config('tit.dts_v1_compat_state_write','off',true);
          RETURN jsonb_build_object(
            'status',dirty.compat_status,
            'attempt_count',dirty.compat_attempt_count,
            'row_version',dirty.compat_row_version
          );
        END
        $function$;

        CREATE FUNCTION public.guard_v1_compat_processing_commit_v1()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        BEGIN
          IF NEW.compat_status='PROCESSING' AND EXISTS (
            SELECT 1 FROM public.dts_dirty_keys AS current_state
            WHERE current_state.source_region=NEW.source_region
              AND current_state.key_type=NEW.key_type
              AND current_state.key_part_1=NEW.key_part_1
              AND current_state.key_part_2=NEW.key_part_2
              AND current_state.compat_status='PROCESSING'
          ) THEN
            RAISE EXCEPTION 'DTS_V1_COMPAT_CLAIM_MUST_COMPLETE_IN_TRANSACTION'
              USING ERRCODE='40001';
          END IF;
          RETURN NULL;
        END
        $function$;

        CREATE CONSTRAINT TRIGGER trg_v1_compat_processing_commit_v1
        AFTER INSERT OR UPDATE ON public.dts_dirty_keys
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE FUNCTION public.guard_v1_compat_processing_commit_v1();

        CREATE FUNCTION public.recover_v1_compat_dirty_key_v1(
          p_operator_request_id text,p_source_region text,p_key_type text,
          p_key_part_1 text,p_key_part_2 text,
          p_expected_compat_row_version bigint,p_reason text
        ) RETURNS jsonb
        LANGUAGE plpgsql
        VOLATILE
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE actor_name text:=coalesce(
          nullif(current_setting('role',true),'none'),session_user
        );
        DECLARE dirty public.dts_dirty_keys%ROWTYPE;
        DECLARE existing_audit public.dts_dirty_key_state_audits%ROWTYPE;
        DECLARE reason_hash text;
        BEGIN
          IF actor_name<>'{CUTOVER_ROLE}'
             OR nullif(btrim(p_operator_request_id),'') IS NULL
             OR length(p_operator_request_id)>128
             OR p_source_region IS NULL OR p_key_type IS NULL
             OR p_key_part_1 IS NULL OR p_key_part_2 IS NULL
             OR p_expected_compat_row_version IS NULL
             OR p_expected_compat_row_version<1
             OR nullif(btrim(p_reason),'') IS NULL
             OR p_reason<>btrim(p_reason) OR length(p_reason)>512 THEN
            RAISE EXCEPTION 'DTS_V1_COMPAT_RECOVERY_REQUEST_INVALID'
              USING ERRCODE='22023';
          END IF;
          PERFORM pg_advisory_xact_lock(hashtextextended(
            'tit:dts-v1-compat-recovery:'||p_operator_request_id,0
          ));
          reason_hash:=encode(sha256(convert_to(p_reason,'UTF8')),'hex');
          SELECT * INTO existing_audit
          FROM public.dts_dirty_key_state_audits
          WHERE event_type='OPERATOR_RECOVERY'
            AND detail->>'state_channel'='V1_COMPAT'
            AND detail->>'operator_request_id'=p_operator_request_id
          ORDER BY audit_id LIMIT 1;
          IF FOUND THEN
            IF existing_audit.source_region IS DISTINCT FROM p_source_region
               OR existing_audit.key_type IS DISTINCT FROM p_key_type
               OR existing_audit.key_part_1 IS DISTINCT FROM p_key_part_1
               OR existing_audit.key_part_2 IS DISTINCT FROM p_key_part_2
               OR existing_audit.detail->>'reason_hash' IS DISTINCT FROM
                    reason_hash THEN
              RAISE EXCEPTION 'DTS_V1_COMPAT_RECOVERY_COMMAND_CONFLICT'
                USING ERRCODE='23505';
            END IF;
            RETURN jsonb_build_object(
              'status','REPLAYED','operator_request_id',p_operator_request_id,
              'compat_row_version',
                (existing_audit.detail->>'compat_after_row_version')::bigint
            );
          END IF;
          SELECT * INTO dirty FROM public.dts_dirty_keys
          WHERE source_region=p_source_region AND key_type=p_key_type
            AND key_part_1=p_key_part_1 AND key_part_2=p_key_part_2
          FOR UPDATE;
          IF NOT FOUND OR dirty.compat_status<>'DEAD'
             OR dirty.compat_row_version IS DISTINCT FROM
                  p_expected_compat_row_version THEN
            RAISE EXCEPTION 'DTS_V1_COMPAT_RECOVERY_STATE_CONFLICT'
              USING ERRCODE='40001';
          END IF;
          PERFORM set_config('tit.dts_v1_compat_state_write','on',true);
          UPDATE public.dts_dirty_keys AS target
          SET compat_status='PENDING',compat_attempt_count=0,
              compat_last_error_code=NULL,
              compat_next_attempt_at=transaction_timestamp(),
              compat_claimed_work_revision=NULL,
              compat_claimed_at=NULL,compat_claimed_by=NULL,
              compat_row_version=target.compat_row_version+1
          WHERE target.source_region=p_source_region
            AND target.key_type=p_key_type
            AND target.key_part_1=p_key_part_1
            AND target.key_part_2=p_key_part_2
          RETURNING * INTO dirty;
          PERFORM set_config('tit.dts_v1_compat_state_write','off',true);
          INSERT INTO public.dts_dirty_key_state_audits(
            source_region,key_type,key_part_1,key_part_2,event_type,
            work_generation,dead_generation,detail
          ) VALUES (
            p_source_region,p_key_type,p_key_part_1,p_key_part_2,
            'OPERATOR_RECOVERY',dirty.work_generation,
            dirty.dead_generation,jsonb_build_object(
              'state_channel','V1_COMPAT',
              'operator_request_id',p_operator_request_id,
              'reason_hash',reason_hash,
              'compat_before_row_version',p_expected_compat_row_version,
              'compat_after_row_version',dirty.compat_row_version
            )
          );
          RETURN jsonb_build_object(
            'status','APPLIED','operator_request_id',p_operator_request_id,
            'compat_status',dirty.compat_status,
            'compat_row_version',dirty.compat_row_version
          );
        END
        $function$;

        CREATE FUNCTION public.dts_v1_compat_dirty_not_complete_count_v1()
        RETURNS bigint
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
          SELECT CASE WHEN coalesce(
            (SELECT mode FROM public.dts_pipeline_control
             WHERE control_id='PRIMARY'),'V2_PRIMARY'
          ) NOT IN ('V1_COMPAT_DUAL_CAPTURE','ROLLED_BACK') THEN 0::bigint
          ELSE count(*)::bigint END
          FROM public.dts_dirty_keys
          WHERE key_type IN ({key_types})
            AND (compat_status<>'COMPLETED' OR
                 compat_completed_work_revision<
                   compat_required_work_revision)
        $function$;

        REVOKE ALL ON FUNCTION
          public.sync_v1_compat_dirty_input_v1(),
          public.sync_v1_compat_pipeline_mode_v1(),
          public.claim_v1_compat_dirty_key_v1(text),
          public.complete_v1_compat_dirty_key_v1(
            text,text,text,text,text,bigint,bigint
          ),
          public.fail_v1_compat_dirty_key_v1(
            text,text,text,text,text,text,integer,integer,integer
          ),
          public.guard_v1_compat_processing_commit_v1(),
          public.recover_v1_compat_dirty_key_v1(
            text,text,text,text,text,bigint,text
          ),
          public.dts_v1_compat_dirty_not_complete_count_v1()
        FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION
          public.claim_v1_compat_dirty_key_v1(text),
          public.complete_v1_compat_dirty_key_v1(
            text,text,text,text,text,bigint,bigint
          ),
          public.fail_v1_compat_dirty_key_v1(
            text,text,text,text,text,text,integer,integer,integer
          ),
          public.dts_v1_compat_dirty_not_complete_count_v1()
        TO {INGEST_ROLE};
        GRANT EXECUTE ON FUNCTION
          public.recover_v1_compat_dirty_key_v1(
            text,text,text,text,text,bigint,text
          ),
          public.dts_v1_compat_dirty_not_complete_count_v1()
        TO {CUTOVER_ROLE};
        """
    )


def _install_qualification_gate() -> None:
    op.execute(
        f"""
        CREATE FUNCTION public.guard_qualification_gate_rollback_v2()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path=pg_catalog,public
        AS $function$
        BEGIN
          IF OLD.mode='V2_PRIMARY' AND NEW.mode='ROLLED_BACK'
             AND OLD.qualification_grants_enabled IS TRUE THEN
            RAISE EXCEPTION
              'DTS_QUALIFICATION_GATE_DISABLE_REQUIRED_BEFORE_ROLLBACK'
              USING ERRCODE='55000';
          END IF;
          RETURN NEW;
        END
        $function$;
        REVOKE ALL ON FUNCTION
          public.guard_qualification_gate_rollback_v2()
        FROM PUBLIC;
        CREATE TRIGGER aa_guard_qualification_gate_rollback_v2
        BEFORE UPDATE OF mode ON public.dts_pipeline_control
        FOR EACH ROW
        EXECUTE FUNCTION public.guard_qualification_gate_rollback_v2();

        CREATE FUNCTION public.enforce_irreversible_qualification_gate_v2()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE grants_enabled boolean:=false;
        DECLARE pipeline_mode text;
        DECLARE old_graduated boolean:=false;
        DECLARE old_gold boolean:=false;
        DECLARE actor_name text:=coalesce(
          nullif(current_setting('role',true),'none'),session_user
        );
        BEGIN
          SELECT qualification_grants_enabled,mode
          INTO grants_enabled,pipeline_mode
          FROM public.dts_pipeline_control WHERE control_id='PRIMARY'
          FOR SHARE;
          grants_enabled:=coalesce(grants_enabled,false)
            AND pipeline_mode='V2_PRIMARY';
          IF TG_OP='UPDATE' THEN
            old_graduated:=OLD.graduation_qualified;
            old_gold:=OLD.gold_qualified;
          END IF;
          IF NOT grants_enabled THEN
            NEW.graduation_qualified:=old_graduated;
            NEW.gold_qualified:=old_gold;
            IF NOT old_graduated THEN
              NEW.graduation_qualified_at:=NULL;
              NEW.graduation_score_locked:=NULL;
            END IF;
            IF NOT old_gold THEN NEW.gold_qualified_at:=NULL; END IF;
          ELSIF actor_name<>'{OUTBOX_ROLE}' AND (
            (NEW.graduation_qualified AND NOT old_graduated)
            OR (NEW.gold_qualified AND NOT old_gold)
          ) THEN
            RAISE EXCEPTION 'DTS_QUALIFICATION_GRANT_ROLE_REQUIRED'
              USING ERRCODE='42501';
          END IF;
          NEW.gate_results:=coalesce(NEW.gate_results,'{{}}'::jsonb) ||
            jsonb_build_object(
              'qualification_grants_enabled',grants_enabled,
              'irreversible_qualification_grants_enabled',grants_enabled
            );
          RETURN NEW;
        END
        $function$;

        CREATE TRIGGER aa_enforce_irreversible_qualification_gate_v2
        BEFORE INSERT OR UPDATE ON public.teacher_qualifications
        FOR EACH ROW
        EXECUTE FUNCTION public.enforce_irreversible_qualification_gate_v2();

        CREATE FUNCTION public.enforce_teacher_qualification_gate_v2()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE grants_enabled boolean:=false;
        DECLARE pipeline_mode text;
        DECLARE old_graduated boolean:=false;
        DECLARE old_gold boolean:=false;
        DECLARE actor_name text:=coalesce(
          nullif(current_setting('role',true),'none'),session_user
        );
        BEGIN
          SELECT qualification_grants_enabled,mode
          INTO grants_enabled,pipeline_mode
          FROM public.dts_pipeline_control WHERE control_id='PRIMARY'
          FOR SHARE;
          grants_enabled:=coalesce(grants_enabled,false)
            AND pipeline_mode='V2_PRIMARY';
          IF TG_OP='UPDATE' THEN
            old_graduated:=OLD.graduation_state='GRADUATED';
            old_gold:=OLD.gold_qualified;
          END IF;
          IF NOT grants_enabled THEN
            NEW.graduation_state:=CASE WHEN old_graduated
              THEN 'GRADUATED' ELSE 'IN_CAMP' END;
            NEW.gold_qualified:=old_gold;
          ELSIF actor_name<>'{OUTBOX_ROLE}' AND (
            (NEW.graduation_state='GRADUATED' AND NOT old_graduated)
            OR (NEW.gold_qualified AND NOT old_gold)
          ) THEN
            RAISE EXCEPTION 'DTS_TEACHER_QUALIFICATION_GRANT_ROLE_REQUIRED'
              USING ERRCODE='42501';
          END IF;
          NEW.payload:=jsonb_set(
            coalesce(NEW.payload,'{{}}'::jsonb),
            '{{graduation_state}}',to_jsonb(NEW.graduation_state),true
          );
          RETURN NEW;
        END
        $function$;

        CREATE TRIGGER aa_enforce_teacher_qualification_gate_v2
        BEFORE INSERT OR UPDATE OF graduation_state,gold_qualified,payload
        ON public.teachers
        FOR EACH ROW
        EXECUTE FUNCTION public.enforce_teacher_qualification_gate_v2();

        CREATE FUNCTION public.guard_dts_qualification_gate_command_v2()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path=pg_catalog,public
        AS $function$
        BEGIN
          IF TG_OP<>'INSERT' OR current_setting(
               'tit.dts_qualification_gate_command_v1',true
             )<>'on' THEN
            RAISE EXCEPTION 'DTS_QUALIFICATION_GATE_AUDIT_IMMUTABLE'
              USING ERRCODE='42501';
          END IF;
          RETURN NEW;
        END
        $function$;
        CREATE TRIGGER trg_guard_dts_qualification_gate_command_v2
        BEFORE INSERT OR UPDATE OR DELETE
        ON public.dts_qualification_gate_commands
        FOR EACH ROW
        EXECUTE FUNCTION public.guard_dts_qualification_gate_command_v2();

        CREATE FUNCTION public.set_irreversible_qualification_grants_v2(
          p_command_id text,p_enabled boolean,
          p_expected_control_row_version bigint,p_reason text
        ) RETURNS jsonb
        LANGUAGE plpgsql
        VOLATILE
        SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE actor_name text:=coalesce(
          nullif(current_setting('role',true),'none'),session_user
        );
        DECLARE existing public.dts_qualification_gate_commands%ROWTYPE;
        DECLARE control_row public.dts_pipeline_control%ROWTYPE;
        DECLARE command_document jsonb;
        DECLARE command_sha256 text;
        DECLARE resulting_version bigint;
        DECLARE fanout_count bigint:=0;
        DECLARE teacher_key record;
        DECLARE input_identity jsonb;
        DECLARE input_fingerprint text;
        BEGIN
          IF actor_name<>'{CUTOVER_ROLE}'
             OR nullif(btrim(p_command_id),'') IS NULL
             OR length(p_command_id)>128
             OR p_enabled IS NULL
             OR p_expected_control_row_version IS NULL
             OR p_expected_control_row_version<1
             OR nullif(btrim(p_reason),'') IS NULL
             OR p_reason<>btrim(p_reason) OR length(p_reason)>512 THEN
            RAISE EXCEPTION 'DTS_QUALIFICATION_GATE_COMMAND_INVALID'
              USING ERRCODE='22023';
          END IF;
          command_document:=jsonb_build_object(
            'protocol_version','dts-qualification-gate-command-v1',
            'command_id',p_command_id,'enabled',p_enabled,
            'expected_control_row_version',p_expected_control_row_version,
            'reason',p_reason
          );
          command_sha256:=
            public.dts_canonical_json_sha256_v1(command_document);
          PERFORM pg_advisory_xact_lock(
            hashtextextended('tit:dts-qualification-gate',0)
          );
          SELECT * INTO existing
          FROM public.dts_qualification_gate_commands
          WHERE command_id=p_command_id;
          IF FOUND THEN
            IF existing.requested_enabled IS DISTINCT FROM p_enabled
               OR existing.expected_control_row_version IS DISTINCT FROM
                    p_expected_control_row_version
               OR existing.reason IS DISTINCT FROM p_reason
               OR existing.command_sha256 IS DISTINCT FROM command_sha256 THEN
              RAISE EXCEPTION 'DTS_QUALIFICATION_GATE_COMMAND_CONFLICT'
                USING ERRCODE='23505';
            END IF;
            SELECT * INTO STRICT control_row
            FROM public.dts_pipeline_control WHERE control_id='PRIMARY'
            FOR SHARE;
            IF control_row.mode<>'V2_PRIMARY'
               OR control_row.qualification_grants_enabled IS DISTINCT FROM
                    existing.requested_enabled
               OR control_row.row_version IS DISTINCT FROM
                    existing.resulting_control_row_version THEN
              RAISE EXCEPTION 'DTS_QUALIFICATION_GATE_COMMAND_CONFLICT'
                USING ERRCODE='40001';
            END IF;
            RETURN jsonb_build_object(
              'status','REPLAYED','command_id',existing.command_id,
              'enabled',existing.requested_enabled,
              'control_row_version',
                existing.resulting_control_row_version,
              'fanout_count',existing.fanout_count,
              'command_sha256',existing.command_sha256
            );
          END IF;
          SELECT * INTO STRICT control_row
          FROM public.dts_pipeline_control WHERE control_id='PRIMARY'
          FOR UPDATE;
          IF control_row.mode<>'V2_PRIMARY'
             OR control_row.row_version<>p_expected_control_row_version THEN
            RAISE EXCEPTION 'DTS_QUALIFICATION_GATE_CONTROL_CONFLICT'
              USING ERRCODE='40001';
          END IF;
          resulting_version:=control_row.row_version;
          IF control_row.qualification_grants_enabled IS DISTINCT FROM
               p_enabled THEN
            UPDATE public.dts_pipeline_control
            SET qualification_grants_enabled=p_enabled,
                row_version=row_version+1,
                changed_at=transaction_timestamp(),changed_by=actor_name
            WHERE control_id='PRIMARY'
              AND row_version=p_expected_control_row_version
            RETURNING row_version INTO resulting_version;
            IF NOT FOUND THEN
              RAISE EXCEPTION 'DTS_QUALIFICATION_GATE_CONTROL_CONFLICT'
                USING ERRCODE='40001';
            END IF;
            input_identity:=jsonb_build_object(
              'protocol_version','dts-qualification-gate-fanout-v1',
              'command_id',p_command_id,'enabled',p_enabled,
              'control_row_version',resulting_version
            );
            FOR teacher_key IN
              SELECT source_region,key_part_1
              FROM public.dts_dirty_keys
              WHERE key_type='TEACHER' AND key_part_2=''
              ORDER BY convert_to(source_region,'UTF8'),
                       convert_to(key_part_1,'UTF8')
            LOOP
              input_fingerprint:=public.dts_canonical_json_sha256_v1(
                input_identity || jsonb_build_object(
                  'source_region',teacher_key.source_region,
                  'teacher_id',teacher_key.key_part_1
                )
              );
              PERFORM public._upsert_dts_dirty_key_input_v2(
                teacher_key.source_region,'TEACHER',
                teacher_key.key_part_1,'','OPERATOR_RECOVERY',
                input_identity,resulting_version,input_fingerprint
              );
              fanout_count:=fanout_count+1;
            END LOOP;
          END IF;
          PERFORM set_config(
            'tit.dts_qualification_gate_command_v1','on',true
          );
          INSERT INTO public.dts_qualification_gate_commands(
            command_id,requested_enabled,expected_control_row_version,
            resulting_control_row_version,reason,command_sha256,
            fanout_count,result_status,executed_by
          ) VALUES (
            p_command_id,p_enabled,p_expected_control_row_version,
            resulting_version,p_reason,command_sha256,fanout_count,
            'APPLIED',actor_name
          );
          PERFORM set_config(
            'tit.dts_qualification_gate_command_v1','off',true
          );
          RETURN jsonb_build_object(
            'status','APPLIED','command_id',p_command_id,
            'enabled',p_enabled,'control_row_version',resulting_version,
            'fanout_count',fanout_count,
            'command_sha256',command_sha256
          );
        EXCEPTION WHEN no_data_found OR too_many_rows THEN
          RAISE EXCEPTION 'DTS_QUALIFICATION_GATE_CONTROL_CONFLICT'
            USING ERRCODE='40001';
        END
        $function$;

        REVOKE ALL ON TABLE public.dts_qualification_gate_commands
          FROM PUBLIC,{INGEST_ROLE};
        REVOKE ALL ON FUNCTION
          public.enforce_irreversible_qualification_gate_v2(),
          public.enforce_teacher_qualification_gate_v2(),
          public.guard_dts_qualification_gate_command_v2(),
          public.set_irreversible_qualification_grants_v2(
            text,boolean,bigint,text
          )
        FROM PUBLIC;
        GRANT SELECT ON TABLE public.dts_qualification_gate_commands
          TO {CUTOVER_ROLE};
        GRANT EXECUTE ON FUNCTION
          public.set_irreversible_qualification_grants_v2(
            text,boolean,bigint,text
          )
        TO {CUTOVER_ROLE};
        """
    )


def upgrade() -> None:
    _preflight_and_columns()
    _create_gate_audit()
    _install_compat_dirty_guard()
    _install_compat_state_machine()
    _install_qualification_gate()


def downgrade() -> None:
    op.execute(
        """
        DO $compat_gate_downgrade_guard$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM public.dts_pipeline_control
            WHERE qualification_grants_enabled IS TRUE
          ) OR EXISTS (
            SELECT 1 FROM public.dts_qualification_gate_commands
          ) OR EXISTS (
            SELECT 1 FROM public.dts_dirty_keys
            WHERE key_type IN (
                    'COURSE','TEACHER','TEACHER_STUDENT','LABEL',
                    'COMPLAINT_CATEGORY'
                  )
              AND compat_required_work_revision>0
              AND compat_completed_work_revision<>
                  compat_required_work_revision
          ) THEN
            RAISE EXCEPTION
              'DTS_V1_COMPAT_GATE_DOWNGRADE_REQUIRES_EMPTY_STATE';
          END IF;
        END
        $compat_gate_downgrade_guard$;

        DROP TRIGGER IF EXISTS
          aa_enforce_irreversible_qualification_gate_v2
          ON public.teacher_qualifications;
        DROP TRIGGER IF EXISTS aa_enforce_teacher_qualification_gate_v2
          ON public.teachers;
        DROP TRIGGER IF EXISTS aa_guard_qualification_gate_rollback_v2
          ON public.dts_pipeline_control;
        DROP TRIGGER IF EXISTS
          trg_guard_dts_qualification_gate_command_v2
          ON public.dts_qualification_gate_commands;
        DROP TRIGGER IF EXISTS trg_sync_v1_compat_dirty_input_v1
          ON public.dts_dirty_key_inputs;
        DROP TRIGGER IF EXISTS trg_sync_v1_compat_pipeline_mode_v1
          ON public.dts_pipeline_control;
        DROP TRIGGER IF EXISTS trg_v1_compat_processing_commit_v1
          ON public.dts_dirty_keys;
        DROP FUNCTION IF EXISTS
          public.set_irreversible_qualification_grants_v2(
            text,boolean,bigint,text
          );
        DROP FUNCTION IF EXISTS
          public.guard_dts_qualification_gate_command_v2();
        DROP FUNCTION IF EXISTS
          public.enforce_irreversible_qualification_gate_v2();
        DROP FUNCTION IF EXISTS
          public.enforce_teacher_qualification_gate_v2();
        DROP FUNCTION IF EXISTS
          public.guard_qualification_gate_rollback_v2();
        DROP FUNCTION IF EXISTS
          public.dts_v1_compat_dirty_not_complete_count_v1();
        DROP FUNCTION IF EXISTS
          public.guard_v1_compat_processing_commit_v1();
        DROP FUNCTION IF EXISTS public.recover_v1_compat_dirty_key_v1(
          text,text,text,text,text,bigint,text
        );
        DROP FUNCTION IF EXISTS public.fail_v1_compat_dirty_key_v1(
          text,text,text,text,text,text,integer,integer,integer
        );
        DROP FUNCTION IF EXISTS public.complete_v1_compat_dirty_key_v1(
          text,text,text,text,text,bigint,bigint
        );
        DROP FUNCTION IF EXISTS public.claim_v1_compat_dirty_key_v1(text);
        DROP FUNCTION IF EXISTS public.sync_v1_compat_pipeline_mode_v1();
        DROP FUNCTION IF EXISTS public.sync_v1_compat_dirty_input_v1();
        DROP TRIGGER IF EXISTS guard_dts_runtime_state_write
          ON public.dts_dirty_keys;
        DROP FUNCTION IF EXISTS public.guard_dts_dirty_key_state_write_v96();
        CREATE TRIGGER guard_dts_runtime_state_write
        BEFORE INSERT OR UPDATE OR DELETE ON public.dts_dirty_keys
        FOR EACH ROW EXECUTE FUNCTION public.guard_dts_runtime_state_write();
        DROP INDEX IF EXISTS public.ix_dts_dirty_keys_compat_retry_v1;
        DROP INDEX IF EXISTS public.ix_dts_dirty_keys_compat_pending_v1;
        ALTER TABLE public.dts_dirty_keys
          DROP CONSTRAINT IF EXISTS ck_dts_dirty_key_compat_state_v1,
          DROP COLUMN compat_row_version,
          DROP COLUMN compat_claimed_by,
          DROP COLUMN compat_claimed_at,
          DROP COLUMN compat_next_attempt_at,
          DROP COLUMN compat_last_error_code,
          DROP COLUMN compat_attempt_count,
          DROP COLUMN compat_completed_work_revision,
          DROP COLUMN compat_claimed_work_revision,
          DROP COLUMN compat_required_work_revision,
          DROP COLUMN compat_status;
        """
    )
    op.drop_table("dts_qualification_gate_commands", schema="public")
    # The control column belongs to the updated rev79 baseline and therefore
    # intentionally survives a rev96-only downgrade.
