"""remove retired V1 fanout and add set-oriented DTS ingest guards.

Revision ID: 20260825_102_dts_ingest_batch_throughput
Revises: 20260824_101_dts_single_pipeline_reset
Create Date: 2026-08-25
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from alembic import op


revision: str = "20260825_102_dts_ingest_batch_throughput"
down_revision: Union[str, None] = "20260824_101_dts_single_pipeline_reset"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _retire_single_pipeline_compat_triggers() -> None:
    op.execute(
        """
        DROP TRIGGER IF EXISTS trg_sync_v1_compat_dirty_input_v1
          ON public.dts_dirty_key_inputs;
        DROP TRIGGER IF EXISTS trg_v1_compat_processing_commit_v1
          ON public.dts_dirty_keys;

        COMMENT ON FUNCTION public.sync_v1_compat_dirty_input_v1() IS
          'Retired by the irreversible SINGLE_PIPELINE reset; retained only so historical catalog references remain resolvable.';
        COMMENT ON FUNCTION public.guard_v1_compat_processing_commit_v1() IS
          'Retired by the irreversible SINGLE_PIPELINE reset; no V1 compatibility state is executed.';
        """
    )


def _deduplicate_deferred_integrity_checks() -> None:
    op.execute(
        r"""
        CREATE OR REPLACE FUNCTION public.dts_v2_source_current_pair_guard()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE inserted_identity integer;
        BEGIN
            IF to_regclass('pg_temp.dts_source_pair_guard_cache_v102') IS NULL
            THEN
                CREATE TEMP TABLE dts_source_pair_guard_cache_v102 (
                    source_region text NOT NULL,
                    source_table text NOT NULL,
                    source_key text NOT NULL,
                    PRIMARY KEY (source_region,source_table,source_key)
                ) ON COMMIT DELETE ROWS;
            END IF;

            IF TG_TABLE_NAME = 'dts_source_row_versions' THEN
                INSERT INTO pg_temp.dts_source_pair_guard_cache_v102
                    (source_region,source_table,source_key)
                VALUES (NEW.source_region,NEW.source_table,NEW.source_key)
                ON CONFLICT DO NOTHING RETURNING 1 INTO inserted_identity;
                IF inserted_identity = 1 THEN
                    PERFORM public.dts_v2_assert_source_current_pair(
                        NEW.source_region,NEW.source_table,NEW.source_key
                    );
                END IF;
                RETURN NULL;
            END IF;

            IF TG_OP IN ('UPDATE','DELETE') THEN
                inserted_identity := NULL;
                INSERT INTO pg_temp.dts_source_pair_guard_cache_v102
                    (source_region,source_table,source_key)
                VALUES (OLD.source_region,OLD.source_table,OLD.source_key)
                ON CONFLICT DO NOTHING RETURNING 1 INTO inserted_identity;
                IF inserted_identity = 1 THEN
                    PERFORM public.dts_v2_assert_source_current_pair(
                        OLD.source_region,OLD.source_table,OLD.source_key
                    );
                END IF;
            END IF;
            IF TG_OP IN ('INSERT','UPDATE')
               AND (
                    TG_OP = 'INSERT'
                    OR ROW(OLD.source_region,OLD.source_table,OLD.source_key)
                       IS DISTINCT FROM
                       ROW(NEW.source_region,NEW.source_table,NEW.source_key)
                    OR NEW.provenance_state IS DISTINCT FROM OLD.provenance_state
                    OR NEW.source_row_revision IS DISTINCT FROM
                       OLD.source_row_revision
                    OR NEW.source_payload_hash IS DISTINCT FROM
                       OLD.source_payload_hash
                    OR NEW.source_row IS DISTINCT FROM OLD.source_row
                    OR NEW.is_deleted IS DISTINCT FROM OLD.is_deleted
               ) THEN
                inserted_identity := NULL;
                INSERT INTO pg_temp.dts_source_pair_guard_cache_v102
                    (source_region,source_table,source_key)
                VALUES (NEW.source_region,NEW.source_table,NEW.source_key)
                ON CONFLICT DO NOTHING RETURNING 1 INTO inserted_identity;
                IF inserted_identity = 1 THEN
                    PERFORM public.dts_v2_assert_source_current_pair(
                        NEW.source_region,NEW.source_table,NEW.source_key
                    );
                END IF;
            END IF;
            RETURN NULL;
        END
        $function$;

        CREATE OR REPLACE FUNCTION public.check_dts_dirty_key_integrity_v2()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE
            dirty public.dts_dirty_keys%ROWTYPE;
            max_work_revision bigint;
            dependency_count bigint;
            guard_region text;
            guard_type text;
            guard_part_1 text;
            guard_part_2 text;
            inserted_identity integer;
        BEGIN
            guard_region := coalesce(NEW.source_region,OLD.source_region);
            guard_type := coalesce(NEW.key_type,OLD.key_type);
            guard_part_1 := coalesce(NEW.key_part_1,OLD.key_part_1);
            guard_part_2 := coalesce(NEW.key_part_2,OLD.key_part_2);
            IF to_regclass('pg_temp.dts_dirty_guard_cache_v102') IS NULL THEN
                CREATE TEMP TABLE dts_dirty_guard_cache_v102 (
                    source_region text NOT NULL,
                    key_type text NOT NULL,
                    key_part_1 text NOT NULL,
                    key_part_2 text NOT NULL,
                    PRIMARY KEY (
                      source_region,key_type,key_part_1,key_part_2
                    )
                ) ON COMMIT DELETE ROWS;
            END IF;
            INSERT INTO pg_temp.dts_dirty_guard_cache_v102(
                source_region,key_type,key_part_1,key_part_2
            ) VALUES (
                guard_region,guard_type,guard_part_1,guard_part_2
            ) ON CONFLICT DO NOTHING RETURNING 1 INTO inserted_identity;
            IF inserted_identity IS NULL THEN RETURN NULL; END IF;

            SELECT * INTO dirty FROM public.dts_dirty_keys
            WHERE source_region=guard_region AND key_type=guard_type
              AND key_part_1=guard_part_1 AND key_part_2=guard_part_2;
            IF NOT FOUND THEN RETURN NULL; END IF;
            SELECT max(dirty_work_revision) INTO max_work_revision
            FROM public.dts_dirty_key_inputs
            WHERE source_region=dirty.source_region
              AND key_type=dirty.key_type
              AND key_part_1=dirty.key_part_1
              AND key_part_2=dirty.key_part_2;
            IF max_work_revision IS NULL
               OR dirty.required_work_revision IS DISTINCT FROM
                    max_work_revision THEN
                RAISE EXCEPTION 'DIRTY_REQUIRED_REVISION_MISMATCH'
                    USING ERRCODE='23514';
            END IF;
            SELECT count(*) INTO dependency_count
            FROM public.dts_dirty_key_dependencies
            WHERE source_region=dirty.source_region
              AND key_type=dirty.key_type
              AND key_part_1=dirty.key_part_1
              AND key_part_2=dirty.key_part_2;
            IF (dirty.status='WAITING_DEPENDENCY') IS DISTINCT FROM
               (dependency_count>0) THEN
                RAISE EXCEPTION 'DIRTY_DEPENDENCY_STATE_MISMATCH'
                    USING ERRCODE='23514';
            END IF;
            RETURN NULL;
        END
        $function$;
        """
    )


def _install_set_oriented_dirty_enqueue() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.enqueue_dirty_from_source_revisions_batch_v3(
            p_commands jsonb
        ) RETURNS jsonb
        LANGUAGE plpgsql VOLATILE SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE command_count integer;
        DECLARE response_count integer;
        BEGIN
          IF session_user<>'tit_dts_ingest_runtime'
             OR jsonb_typeof(p_commands)<>'array'
             OR jsonb_array_length(p_commands) NOT BETWEEN 1 AND 10000 THEN
            RAISE EXCEPTION 'DIRTY_BATCH_AUTHORITY_DENIED'
              USING ERRCODE='42501';
          END IF;
          CREATE TEMP TABLE IF NOT EXISTS dts_dirty_batch_v102(
            ordinal integer PRIMARY KEY,
            source_region text NOT NULL,source_table text NOT NULL,
            source_key text NOT NULL,source_row_revision bigint NOT NULL,
            key_type text NOT NULL,key_part_1 text NOT NULL,
            key_part_2 text NOT NULL,peer_teacher boolean NOT NULL,
            dirty_region text,input_identity jsonb,
            input_identity_hash text,input_fingerprint text,
            dirty_work_revision bigint,
            fast_path boolean NOT NULL DEFAULT false,response jsonb
          ) ON COMMIT DELETE ROWS;
          TRUNCATE pg_temp.dts_dirty_batch_v102;
          INSERT INTO pg_temp.dts_dirty_batch_v102(
            ordinal,source_region,source_table,source_key,
            source_row_revision,key_type,key_part_1,key_part_2,peer_teacher,
            dirty_region,input_identity
          )
          SELECT item.ordinal,item.source_region,item.source_table,
                 item.source_key,item.source_row_revision,item.key_type,
                 item.key_part_1,item.key_part_2,item.peer_teacher,
                 CASE WHEN item.peer_teacher THEN 'ovs'
                      WHEN item.key_type='COMPLAINT_CATEGORY' THEN 'dom'
                      ELSE item.source_region END,
                 jsonb_build_object(
                   'source_region',item.source_region,
                   'source_table',item.source_table,
                   'source_key',item.source_key
                 )
          FROM jsonb_to_recordset(p_commands) AS item(
            ordinal integer,source_region text,source_table text,
            source_key text,source_row_revision bigint,key_type text,
            key_part_1 text,key_part_2 text,peer_teacher boolean
          );
          GET DIAGNOSTICS command_count=ROW_COUNT;
          IF command_count<>jsonb_array_length(p_commands)
             OR EXISTS (
               SELECT 1 FROM pg_temp.dts_dirty_batch_v102 input
               WHERE input.ordinal<0 OR input.source_region NOT IN ('dom','ovs')
                 OR nullif(btrim(input.source_table),'') IS NULL
                 OR nullif(btrim(input.source_key),'') IS NULL
                 OR input.source_row_revision<1
                 OR public.dts_dirty_key_identity_valid_v2(
                      input.dirty_region,input.key_type,
                      input.key_part_1,input.key_part_2
                    ) IS DISTINCT FROM true
                 OR (input.peer_teacher AND NOT (
                      input.source_region='dom'
                      AND input.source_table='dom_teacher'
                      AND input.dirty_region='ovs'
                      AND input.key_type='TEACHER'
                      AND input.key_part_1=input.source_key
                      AND input.key_part_2=''
                    ))
                 OR (NOT input.peer_teacher
                     AND input.key_type<>'COMPLAINT_CATEGORY'
                     AND input.dirty_region<>input.source_region)
             ) THEN
            RAISE EXCEPTION 'DIRTY_BATCH_REQUEST_INVALID'
              USING ERRCODE='22023';
          END IF;
          IF EXISTS (
            SELECT 1 FROM pg_temp.dts_dirty_batch_v102 input
            LEFT JOIN public.dts_source_row_versions version
              ON version.source_region=input.source_region
             AND version.source_table=input.source_table
             AND version.source_key=input.source_key
             AND version.source_row_revision=input.source_row_revision
            LEFT JOIN public.dts_source_rows current_row
              ON current_row.source_region=input.source_region
             AND current_row.source_table=input.source_table
             AND current_row.source_key=input.source_key
            WHERE version.source_key IS NULL OR version.version_kind<>'CDC'
               OR current_row.source_key IS NULL
               OR current_row.provenance_state<>'V2_CONFIRMED'
               OR current_row.source_row_revision<>input.source_row_revision
               OR current_row.source_payload_hash<>
                    version.protected_source_row_hash
               OR (input.key_type='COMPLAINT_CATEGORY'
                   AND input.source_table NOT IN (
                     'dom_complaint','ovs_complaint','dom_complaint_cate'
                   ))
          ) THEN
            RAISE EXCEPTION 'DIRTY_INPUT_REFERENCE_INVALID'
              USING ERRCODE='23503';
          END IF;
          UPDATE pg_temp.dts_dirty_batch_v102 input
          SET input_identity_hash=public.dts_canonical_json_sha256_v1(
                input.input_identity
              ),
              input_fingerprint=public.dts_canonical_json_sha256_v1(
                jsonb_build_object(
                  'protocol','dirty-source-v1',
                  'identity',input.input_identity,
                  'revision',input.source_row_revision,
                  'version_kind',version.version_kind,
                  'operation',version.operation,
                  'is_deleted',current_row.is_deleted,
                  'protected_source_row_hash',
                    version.protected_source_row_hash
                )
              )
          FROM public.dts_source_row_versions version,
               public.dts_source_rows current_row
          WHERE version.source_region=input.source_region
            AND version.source_table=input.source_table
            AND version.source_key=input.source_key
            AND version.source_row_revision=input.source_row_revision
            AND current_row.source_region=input.source_region
            AND current_row.source_table=input.source_table
            AND current_row.source_key=input.source_key;

          PERFORM pg_advisory_xact_lock(hashtextextended(
            'tit:dts-dirty-region:'||locked.dirty_region,0
          )) FROM (
            SELECT DISTINCT dirty_region
            FROM pg_temp.dts_dirty_batch_v102 ORDER BY dirty_region
          ) locked;
          PERFORM 1
          FROM public.dts_dirty_keys dirty
          JOIN (
            SELECT DISTINCT dirty_region,key_type,key_part_1,key_part_2
            FROM pg_temp.dts_dirty_batch_v102
          ) input
            ON dirty.source_region=input.dirty_region
           AND dirty.key_type=input.key_type
           AND dirty.key_part_1=input.key_part_1
           AND dirty.key_part_2=input.key_part_2
          ORDER BY dirty.source_region,dirty.key_type,
                   dirty.key_part_1,dirty.key_part_2
          FOR UPDATE OF dirty;
          IF EXISTS (
            SELECT 1 FROM pg_temp.dts_dirty_batch_v102 input
            JOIN LATERAL (
              SELECT prior.input_revision,prior.input_fingerprint
              FROM public.dts_dirty_key_inputs prior
              WHERE prior.source_region=input.dirty_region
                AND prior.key_type=input.key_type
                AND prior.key_part_1=input.key_part_1
                AND prior.key_part_2=input.key_part_2
                AND prior.input_kind='SOURCE_REVISION'
                AND prior.input_identity_hash=input.input_identity_hash
              ORDER BY prior.input_revision DESC LIMIT 1
            ) latest ON true
            WHERE input.source_row_revision<=latest.input_revision
          ) THEN
            RAISE EXCEPTION 'DIRTY_BATCH_INPUT_NOT_NEW'
              USING ERRCODE='23514';
          END IF;
          WITH ordered AS (
            SELECT input.ordinal,
              coalesce(dirty.required_work_revision,0)+row_number() OVER (
                PARTITION BY input.dirty_region,input.key_type,
                             input.key_part_1,input.key_part_2
                ORDER BY input.ordinal
              ) AS dirty_work_revision
            FROM pg_temp.dts_dirty_batch_v102 input
            LEFT JOIN public.dts_dirty_keys dirty
              ON dirty.source_region=input.dirty_region
             AND dirty.key_type=input.key_type
             AND dirty.key_part_1=input.key_part_1
             AND dirty.key_part_2=input.key_part_2
          )
          UPDATE pg_temp.dts_dirty_batch_v102 input
          SET dirty_work_revision=ordered.dirty_work_revision,
              fast_path=true
          FROM ordered WHERE ordered.ordinal=input.ordinal;

          INSERT INTO public.dts_dirty_key_state_audits(
            source_region,key_type,key_part_1,key_part_2,event_type,
            work_generation,dead_generation,detail
          )
          SELECT dirty.source_region,dirty.key_type,dirty.key_part_1,
                 dirty.key_part_2,'DEAD_REOPENED_BY_INPUT',
                 dirty.work_generation,dirty.dead_generation,
                 jsonb_build_object(
                   'input_kind','SOURCE_REVISION',
                   'input_identity_hash',final.input_identity_hash,
                   'input_revision',final.source_row_revision
                 )
          FROM public.dts_dirty_keys dirty
          JOIN LATERAL (
            SELECT input.input_identity_hash,input.source_row_revision
            FROM pg_temp.dts_dirty_batch_v102 input
            WHERE input.dirty_region=dirty.source_region
              AND input.key_type=dirty.key_type
              AND input.key_part_1=dirty.key_part_1
              AND input.key_part_2=dirty.key_part_2
            ORDER BY input.ordinal DESC LIMIT 1
          ) final ON true
          WHERE dirty.status='DEAD';

          DELETE FROM public.dts_dirty_key_dependencies dependency
          USING public.dts_dirty_keys dirty
          WHERE dependency.source_region=dirty.source_region
            AND dependency.key_type=dirty.key_type
            AND dependency.key_part_1=dirty.key_part_1
            AND dependency.key_part_2=dirty.key_part_2
            AND dirty.status<>'PROCESSING'
            AND EXISTS (
              SELECT 1 FROM pg_temp.dts_dirty_batch_v102 input
              WHERE input.dirty_region=dirty.source_region
                AND input.key_type=dirty.key_type
                AND input.key_part_1=dirty.key_part_1
                AND input.key_part_2=dirty.key_part_2
            );

          INSERT INTO public.dts_dirty_keys(
            source_region,key_type,key_part_1,key_part_2,status,
            pending_event_count,attempt_count,last_source_region,
            last_source_table,last_topic,last_partition,last_offset,
            issue_codes,last_error_code,next_attempt_at,claimed_at,claimed_by,
            row_version,first_seen_at,last_seen_at,required_work_revision,
            claimed_through_work_revision,completed_work_revision,
            last_input_identity_hash,last_input_revision,work_generation,
            dead_generation,blocked_by,lease_owner_kind,lease_owner,
            lease_token,lease_expires_at,created_at,updated_at
          )
          SELECT dirty_region,key_type,key_part_1,key_part_2,'PENDING',
                 count(*)::integer,0,dirty_region,
                 (array_agg(source_table ORDER BY ordinal DESC))[1],
                 'v2-dirty-input',0,0,
                 '[]'::jsonb,NULL,transaction_timestamp(),NULL,NULL,1,
                 clock_timestamp(),clock_timestamp(),max(dirty_work_revision),
                 NULL,0,
                 (array_agg(input_identity_hash ORDER BY ordinal DESC))[1],
                 (array_agg(source_row_revision ORDER BY ordinal DESC))[1],
                 1,0,NULL,
                 NULL,NULL,NULL,NULL,clock_timestamp(),clock_timestamp()
          FROM pg_temp.dts_dirty_batch_v102 input
          WHERE NOT EXISTS (
            SELECT 1 FROM public.dts_dirty_keys dirty
            WHERE dirty.source_region=input.dirty_region
              AND dirty.key_type=input.key_type
              AND dirty.key_part_1=input.key_part_1
              AND dirty.key_part_2=input.key_part_2
          )
          GROUP BY dirty_region,key_type,key_part_1,key_part_2;

          WITH grouped AS (
            SELECT input.dirty_region,input.key_type,input.key_part_1,
                   input.key_part_2,count(*)::integer input_count,
                   max(input.dirty_work_revision) required_work_revision,
                   (array_agg(input.input_identity_hash
                              ORDER BY input.ordinal DESC))[1]
                     last_input_identity_hash,
                   (array_agg(input.source_row_revision
                              ORDER BY input.ordinal DESC))[1]
                     last_input_revision
            FROM pg_temp.dts_dirty_batch_v102 input
            GROUP BY input.dirty_region,input.key_type,
                     input.key_part_1,input.key_part_2
          )
          UPDATE public.dts_dirty_keys dirty
          SET status=CASE WHEN dirty.status='PROCESSING'
                          THEN dirty.status ELSE 'PENDING' END,
              required_work_revision=grouped.required_work_revision,
              claimed_through_work_revision=CASE
                WHEN dirty.status='PROCESSING'
                THEN dirty.claimed_through_work_revision ELSE NULL END,
              last_input_identity_hash=grouped.last_input_identity_hash,
              last_input_revision=grouped.last_input_revision,
              pending_event_count=dirty.pending_event_count+
                                  grouped.input_count,
              attempt_count=CASE WHEN dirty.status='PROCESSING'
                                 THEN dirty.attempt_count ELSE 0 END,
              last_error_code=CASE WHEN dirty.status='PROCESSING'
                                   THEN dirty.last_error_code ELSE NULL END,
              next_attempt_at=CASE WHEN dirty.status='PROCESSING'
                                   THEN dirty.next_attempt_at
                                   ELSE transaction_timestamp() END,
              blocked_by=CASE WHEN dirty.status='PROCESSING'
                              THEN dirty.blocked_by ELSE NULL END,
              lease_owner_kind=CASE WHEN dirty.status='PROCESSING'
                                    THEN dirty.lease_owner_kind ELSE NULL END,
              lease_owner=CASE WHEN dirty.status='PROCESSING'
                               THEN dirty.lease_owner ELSE NULL END,
              lease_token=CASE WHEN dirty.status='PROCESSING'
                               THEN dirty.lease_token ELSE NULL END,
              claimed_at=CASE WHEN dirty.status='PROCESSING'
                              THEN dirty.claimed_at ELSE NULL END,
              lease_expires_at=CASE WHEN dirty.status='PROCESSING'
                                    THEN dirty.lease_expires_at ELSE NULL END,
              claimed_by=CASE WHEN dirty.status='PROCESSING'
                              THEN dirty.claimed_by ELSE NULL END,
              last_seen_at=clock_timestamp(),updated_at=clock_timestamp(),
              row_version=dirty.row_version+1
          FROM grouped
          WHERE dirty.source_region=grouped.dirty_region
            AND dirty.key_type=grouped.key_type
            AND dirty.key_part_1=grouped.key_part_1
            AND dirty.key_part_2=grouped.key_part_2
            AND dirty.required_work_revision<>
                grouped.required_work_revision;

          INSERT INTO public.dts_dirty_key_inputs(
            source_region,key_type,key_part_1,key_part_2,input_kind,
            input_identity_hash,input_revision,input_identity,
            input_fingerprint,dirty_work_revision
          )
          SELECT dirty_region,key_type,key_part_1,key_part_2,
                 'SOURCE_REVISION',input_identity_hash,
                 source_row_revision,input_identity,input_fingerprint,
                 dirty_work_revision
          FROM pg_temp.dts_dirty_batch_v102
          ORDER BY ordinal;
          UPDATE pg_temp.dts_dirty_batch_v102 SET response=jsonb_build_object(
            'status','ENQUEUED',
            'dirty_work_revision',dirty_work_revision
          );
          SELECT count(*) INTO response_count
          FROM pg_temp.dts_dirty_batch_v102
          WHERE response->>'status'='ENQUEUED';
          IF response_count<>command_count THEN
            RAISE EXCEPTION 'DIRTY_BATCH_RESULT_INVALID'
              USING ERRCODE='23514';
          END IF;
          RETURN jsonb_build_object(
            'command_count',command_count,
            'fast_path_count',(SELECT count(*)
              FROM pg_temp.dts_dirty_batch_v102 WHERE fast_path),
            'responses',(SELECT jsonb_agg(response ORDER BY ordinal)
              FROM pg_temp.dts_dirty_batch_v102)
          );
        END
        $function$;

        REVOKE ALL ON FUNCTION
          public.enqueue_dirty_from_source_revisions_batch_v3(jsonb)
        FROM PUBLIC,tit_growth_app,tit_teacher_crud,tide_support_ticket_owner;
        GRANT EXECUTE ON FUNCTION
          public.enqueue_dirty_from_source_revisions_batch_v3(jsonb)
        TO tit_dts_ingest_runtime;
        COMMENT ON FUNCTION
          public.enqueue_dirty_from_source_revisions_batch_v3(jsonb) IS
          'Validate one coalesced source-revision fanout set; persist new and existing dirty identities set-wise while preserving PROCESSING leases and the dirty state machine.';
        """
    )


def _install_active_scope_probe() -> None:
    op.execute(
        """
        CREATE FUNCTION public.dts_active_source_scope_tables_v1(
          p_source_region text,p_source_tables text[]
        ) RETURNS text[]
        LANGUAGE plpgsql STABLE SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE active_tables text[];
        BEGIN
          IF session_user<>'tit_dts_ingest_runtime'
             OR p_source_region NOT IN ('dom','ovs')
             OR p_source_tables IS NULL
             OR cardinality(p_source_tables) NOT BETWEEN 1 AND 100 THEN
            RAISE EXCEPTION 'DTS_ACTIVE_SCOPE_PROBE_INVALID'
              USING ERRCODE='22023';
          END IF;
          SELECT coalesce(array_agg(DISTINCT source_table ORDER BY source_table),
                          ARRAY[]::text[])
          INTO active_tables
          FROM public.dts_source_scope_states
          WHERE source_region=p_source_region
            AND source_table=ANY(p_source_tables)
            AND scope_kind='CURRENT' AND active_snapshot_id IS NOT NULL;
          RETURN active_tables;
        END
        $function$;
        REVOKE ALL ON FUNCTION
          public.dts_active_source_scope_tables_v1(text,text[])
        FROM PUBLIC,tit_growth_app,tit_teacher_crud,tide_support_ticket_owner;
        GRANT EXECUTE ON FUNCTION
          public.dts_active_source_scope_tables_v1(text,text[])
        TO tit_dts_ingest_runtime;
        """
    )


def _verify() -> None:
    op.execute(
        """
        DO $verify_dts_batch_throughput_v102$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM pg_catalog.pg_trigger
            WHERE tgrelid IN (
              'public.dts_dirty_keys'::regclass,
              'public.dts_dirty_key_inputs'::regclass
            ) AND tgname IN (
              'trg_sync_v1_compat_dirty_input_v1',
              'trg_v1_compat_processing_commit_v1'
            ) AND NOT tgisinternal
          ) OR NOT has_function_privilege(
            'tit_dts_ingest_runtime',
            'public.enqueue_dirty_from_source_revisions_batch_v3(jsonb)',
            'EXECUTE'
          ) OR NOT has_function_privilege(
            'tit_dts_ingest_runtime',
            'public.dts_active_source_scope_tables_v1(text,text[])',
            'EXECUTE'
          ) OR has_function_privilege(
            'tit_growth_app',
            'public.enqueue_dirty_from_source_revisions_batch_v3(jsonb)',
            'EXECUTE'
          ) THEN
            RAISE EXCEPTION 'DTS_BATCH_THROUGHPUT_V102_INVALID';
          END IF;
        END
        $verify_dts_batch_throughput_v102$;
        """
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    _retire_single_pipeline_compat_triggers()
    _deduplicate_deferred_integrity_checks()
    _install_set_oriented_dirty_enqueue()
    _install_active_scope_probe()
    _verify()


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    raise RuntimeError(
        "20260825_102 follows the destructive single-pipeline reset and is forward-only"
    )
