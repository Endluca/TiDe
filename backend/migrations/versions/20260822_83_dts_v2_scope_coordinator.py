"""add the fail-closed DTS v2 source-scope coordinator foundation.

Revision ID: 20260822_83_dts_v2_scope
Revises: 20260822_82_v2_runtime_acl
Create Date: 2026-08-22

The revision is additive and does not connect to a source database or broker.
It installs the protected snapshot candidate state machine, relational fence
evidence, typed staging/membership identities, and atomic SOURCE_SCOPE
revision/dirty/outbox publication.  This first safe publisher accepts only a
snapshot that is already identical to V2_CONFIRMED live current state.  Any
replacement that would require SNAPSHOT_DIFF or overlay replay fails closed;
that boundary prevents an incomplete implementation from publishing false
completeness.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "20260822_83_dts_v2_scope"
down_revision: Union[str, None] = "20260822_82_v2_runtime_acl"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCOPE_COORDINATOR_ROLE = "tit_dts_scope_coordinator_runtime"

SOURCE_TABLES: tuple[tuple[str, str], ...] = (
    ("dom", "dom_appoint"),
    ("dom", "dom_complaint"),
    ("dom", "dom_complaint_cate"),
    ("dom", "dom_grading_label"),
    ("dom", "dom_grading_label_log"),
    ("dom", "dom_qa_task_close_camera_record"),
    ("dom", "dom_teacher"),
    ("dom", "dom_teacher_absent_reason"),
    ("dom", "dom_teacher_blacklist"),
    ("dom", "dom_teacher_certification"),
    ("dom", "dom_teacher_class_schedule"),
    ("dom", "dom_teacher_favorite"),
    ("dom", "dom_teacher_penalty"),
    ("dom", "dom_user_complaint"),
    ("dom", "dom_user_teacher_grading"),
    ("ovs", "ovs_appoint"),
    ("ovs", "ovs_complaint"),
    ("ovs", "ovs_grading_label"),
    ("ovs", "ovs_grading_label_log"),
    ("ovs", "ovs_qa_task_close_camera_record"),
    ("ovs", "ovs_teacher_blacklist"),
    ("ovs", "ovs_teacher_favorite"),
    ("ovs", "ovs_user_complaint"),
    ("ovs", "ovs_user_teacher_grading"),
)

HISTORY_SOURCE_TABLES: tuple[str, ...] = (
    "dom_appoint",
    "ovs_appoint",
    "dom_teacher_class_schedule",
    "dom_teacher_favorite",
    "ovs_teacher_favorite",
    "dom_teacher_blacklist",
    "ovs_teacher_blacklist",
)

NEW_TABLES: tuple[str, ...] = (
    "dts_source_table_publish_generations",
    "dts_source_scope_snapshots",
    "dts_source_snapshot_fences",
    "dts_source_snapshot_rows",
    "dts_source_scope_states",
    "dts_source_scope_memberships",
    "dts_source_scope_commands",
    "dts_source_scope_transition_audits",
)

PUBLIC_FUNCTION_SIGNATURES: tuple[str, ...] = (
    "public.begin_source_snapshot_candidate_v2(text,text,text,text,text,text,text,timestamptz,text,jsonb,timestamptz,timestamptz,text,integer,bigint,bigint)",
    "public.stage_source_snapshot_row_v2(text,text,text,jsonb,jsonb,jsonb,text)",
    "public.heartbeat_source_snapshot_candidate_v2(text,text,text,text,integer,bigint)",
    "public.verify_source_snapshot_candidate_v2(text,text,text,text,bigint,bigint,bigint,text,text,text)",
    "public.abort_source_snapshot_candidate_v2(text,text,text,text,bigint,bigint,text)",
    "public.takeover_expired_source_snapshot_candidate_v2(text,text,text,bigint,text)",
    "public.publish_source_snapshot_candidate_v2(text,text,text,text,bigint,bigint,text,text)",
    "public.invalidate_source_scope_v2(text,text,text,text,text,text,bigint,bigint,text,text)",
)


def _install_identity_helpers() -> None:
    allowed_values = ",".join(
        f"('{region}','{table}')" for region, table in SOURCE_TABLES
    )
    history_values = ",".join(f"'{table}'" for table in HISTORY_SOURCE_TABLES)
    op.execute(
        f"""
        CREATE FUNCTION public.dts_source_table_allowed_v2(
            p_source_region text,
            p_source_table text
        )
        RETURNS boolean
        LANGUAGE sql
        IMMUTABLE
        STRICT
        PARALLEL SAFE
        SET search_path = pg_catalog, public
        AS $function$
            SELECT (p_source_region,p_source_table) IN (
                VALUES {allowed_values}
            )
        $function$;

        CREATE FUNCTION public.dts_source_scope_identity_valid_v2(
            p_source_region text,
            p_source_table text,
            p_scope_kind text,
            p_scope_level text,
            p_scope_key text
        )
        RETURNS boolean
        LANGUAGE sql
        IMMUTABLE
        STRICT
        PARALLEL SAFE
        SET search_path = pg_catalog, public
        AS $function$
            SELECT public.dts_source_table_allowed_v2(
                       p_source_region,p_source_table
                   )
               AND p_scope_kind IN ('CURRENT','HISTORY')
               AND (
                    p_scope_kind = 'CURRENT'
                    OR p_source_table IN ({history_values})
               )
               AND p_scope_level IN ('GLOBAL','TEACHER')
               AND (
                    (p_scope_level = 'GLOBAL' AND p_scope_key = '*')
                    OR (
                        p_scope_level = 'TEACHER'
                        AND p_scope_key <> ''
                        AND btrim(p_scope_key) = p_scope_key
                    )
               )
        $function$;

        CREATE FUNCTION public.dts_source_key_parts_valid_v2(
            p_source_key text,
            p_source_key_data jsonb,
            p_source_key_type text,
            p_source_key_numeric numeric,
            p_source_key_text text
        )
        RETURNS boolean
        LANGUAGE plpgsql
        IMMUTABLE
        STRICT
        PARALLEL SAFE
        SET search_path = pg_catalog, public
        AS $function$
        BEGIN
            IF jsonb_typeof(p_source_key_data) <> 'object'
               OR p_source_key_data <> jsonb_build_object(
                    'id',p_source_key_data->'id'
               ) THEN
                RETURN false;
            END IF;
            IF p_source_key_type = 'NUMERIC' THEN
                RETURN jsonb_typeof(p_source_key_data->'id') = 'number'
                   AND p_source_key_numeric IS NOT NULL
                   AND p_source_key_text IS NULL
                   AND p_source_key_numeric =
                        (p_source_key_data->>'id')::numeric
                   AND p_source_key = trim_scale(p_source_key_numeric)::text;
            ELSIF p_source_key_type = 'TEXT' THEN
                RETURN jsonb_typeof(p_source_key_data->'id') = 'string'
                   AND p_source_key_numeric IS NULL
                   AND p_source_key_text IS NOT NULL
                   AND p_source_key_text <> ''
                   AND btrim(p_source_key_text) = p_source_key_text
                   AND p_source_key_text = p_source_key_data->>'id'
                   AND p_source_key = p_source_key_text;
            END IF;
            RETURN false;
        EXCEPTION
            WHEN invalid_text_representation OR numeric_value_out_of_range THEN
                RETURN false;
        END
        $function$;

        CREATE FUNCTION public.dts_dependency_keys_valid_v2(
            p_source_region text,
            p_dependency_keys jsonb
        )
        RETURNS boolean
        LANGUAGE sql
        IMMUTABLE
        STRICT
        PARALLEL SAFE
        SET search_path = pg_catalog, public
        AS $function$
            SELECT p_source_region IN ('dom','ovs')
               AND jsonb_typeof(p_dependency_keys) = 'object'
               AND p_dependency_keys = jsonb_build_object(
                    'category_ids',p_dependency_keys->'category_ids',
                    'course_ids',p_dependency_keys->'course_ids',
                    'label_ids',p_dependency_keys->'label_ids',
                    'student_subjects',p_dependency_keys->'student_subjects',
                    'teacher_ids',p_dependency_keys->'teacher_ids'
               )
               AND NOT EXISTS (
                    SELECT 1
                    FROM jsonb_each(p_dependency_keys) AS dependency(k,v)
                    WHERE jsonb_typeof(v) <> 'array'
                       OR EXISTS (
                            SELECT 1
                            FROM jsonb_array_elements(v) AS item(value)
                            WHERE jsonb_typeof(value) <> 'string'
                               OR value #>> '{{}}' = ''
                               OR btrim(value #>> '{{}}') <> value #>> '{{}}'
                               OR (
                                    k = 'student_subjects'
                                    AND p_source_region = 'dom'
                                    AND value #>> '{{}}'
                                        !~ '^dom:v1:[0-9a-f]{{64}}$'
                               )
                       )
                       OR jsonb_array_length(v) <> (
                            SELECT count(DISTINCT value)
                            FROM jsonb_array_elements(v) AS item(value)
                       )
               )
        $function$;

        CREATE FUNCTION public.dts_normalize_scope_partition_offsets_v2(
            p_offsets jsonb,
            p_source_region text
        )
        RETURNS jsonb
        LANGUAGE plpgsql
        IMMUTABLE
        STRICT
        PARALLEL SAFE
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE result jsonb;
        BEGIN
            IF p_source_region NOT IN ('dom','ovs')
               OR jsonb_typeof(p_offsets) <> 'array'
               OR jsonb_array_length(p_offsets) = 0
               OR EXISTS (
                    SELECT 1
                    FROM jsonb_array_elements(p_offsets) AS item(value)
                    WHERE jsonb_typeof(value) <> 'object'
                       OR value <> jsonb_build_object(
                            'source_region',value->'source_region',
                            'source_partition_epoch_id',
                                value->'source_partition_epoch_id',
                            'topic',value->'topic',
                            'partition_id',value->'partition_id',
                            'start_next_offset',value->'start_next_offset',
                            'end_next_offset',value->'end_next_offset'
                       )
                       OR jsonb_typeof(value->'source_region') <> 'string'
                       OR value->>'source_region' <> p_source_region
                       OR jsonb_typeof(
                            value->'source_partition_epoch_id'
                          ) <> 'string'
                       OR btrim(value->>'source_partition_epoch_id') = ''
                       OR jsonb_typeof(value->'topic') <> 'string'
                       OR btrim(value->>'topic') = ''
                       OR jsonb_typeof(value->'partition_id') <> 'number'
                       OR (value->>'partition_id') !~ '^[0-9]+$'
                       OR jsonb_typeof(value->'start_next_offset') <> 'number'
                       OR (value->>'start_next_offset') !~ '^[0-9]+$'
                       OR jsonb_typeof(value->'end_next_offset') <> 'number'
                       OR (value->>'end_next_offset') !~ '^[0-9]+$'
                       OR (value->>'end_next_offset')::numeric <
                            (value->>'start_next_offset')::numeric
               ) THEN
                RAISE EXCEPTION 'SOURCE_SCOPE_FENCE_VECTOR_INVALID'
                    USING ERRCODE = '22023';
            END IF;

            SELECT jsonb_agg(value ORDER BY
                       convert_to(value->>'source_region','UTF8'),
                       convert_to(value->>'source_partition_epoch_id','UTF8'),
                       convert_to(value->>'topic','UTF8'),
                       (value->>'partition_id')::integer)
            INTO result
            FROM jsonb_array_elements(p_offsets) AS item(value);

            IF EXISTS (
                SELECT 1
                FROM jsonb_array_elements(result) AS item(value)
                GROUP BY value->>'source_region',
                         value->>'source_partition_epoch_id',
                         value->>'topic',
                         (value->>'partition_id')::integer
                HAVING count(*) > 1
            ) THEN
                RAISE EXCEPTION 'SOURCE_SCOPE_FENCE_VECTOR_DUPLICATE'
                    USING ERRCODE = '22023';
            END IF;
            RETURN result;
        EXCEPTION
            WHEN invalid_text_representation OR numeric_value_out_of_range THEN
                RAISE EXCEPTION 'SOURCE_SCOPE_FENCE_VECTOR_INVALID'
                    USING ERRCODE = '22023';
        END
        $function$;

        REVOKE ALL ON FUNCTION
            public.dts_source_table_allowed_v2(text,text),
            public.dts_source_scope_identity_valid_v2(
                text,text,text,text,text
            ),
            public.dts_source_key_parts_valid_v2(
                text,jsonb,text,numeric,text
            ),
            public.dts_dependency_keys_valid_v2(text,jsonb),
            public.dts_normalize_scope_partition_offsets_v2(jsonb,text)
        FROM PUBLIC;
        """
    )


def _install_scope_guards() -> None:
    mutable_tables = ",".join(f"'{table}'" for table in NEW_TABLES[:-2])
    op.execute(
        f"""
        CREATE FUNCTION public.guard_dts_scope_internal_write_v2()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $function$
        BEGIN
            IF current_setting(
                    'tit.scope_coordinator_internal',true
               ) IS DISTINCT FROM 'on' THEN
                RAISE EXCEPTION 'DTS_SCOPE_DIRECT_WRITE_FORBIDDEN'
                    USING ERRCODE = '42501';
            END IF;
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END
        $function$;

        CREATE FUNCTION public.guard_dts_scope_append_only_v2()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $function$
        BEGIN
            RAISE EXCEPTION 'DTS_SCOPE_APPEND_ONLY_VIOLATION'
                USING ERRCODE = '55000';
        END
        $function$;

        CREATE FUNCTION public.guard_dts_snapshot_rows_immutable_v2()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $function$
        BEGIN
            RAISE EXCEPTION 'DTS_SCOPE_STAGING_ROW_IMMUTABLE'
                USING ERRCODE = '55000';
        END
        $function$;

        CREATE FUNCTION public.check_dts_scope_state_integrity_v2()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE scope_record record;
        DECLARE snapshot_record record;
        DECLARE head_record record;
        BEGIN
            scope_record := CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
            SELECT * INTO head_record
            FROM public.dts_source_table_publish_generations
            WHERE source_region = scope_record.source_region
              AND source_table = scope_record.source_table;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'SOURCE_TABLE_GENERATION_HEAD_MISSING'
                    USING ERRCODE = '23503';
            END IF;

            IF scope_record.state = 'COMPLETE' THEN
                SELECT * INTO snapshot_record
                FROM public.dts_source_scope_snapshots
                WHERE snapshot_id = scope_record.active_snapshot_id
                  AND source_region = scope_record.source_region
                  AND source_table = scope_record.source_table
                  AND scope_kind = scope_record.scope_kind
                  AND scope_level = scope_record.scope_level
                  AND scope_key = scope_record.scope_key;
                IF NOT FOUND
                   OR snapshot_record.epoch_state <> 'COMPLETE'
                   OR snapshot_record.published_generation IS NULL
                   OR snapshot_record.published_generation >
                        head_record.current_generation THEN
                    RAISE EXCEPTION 'SOURCE_SCOPE_FALSE_COMPLETE_FORBIDDEN'
                        USING ERRCODE = '23514';
                END IF;
            ELSIF scope_record.state IN ('LOADING','VERIFYING') THEN
                SELECT * INTO snapshot_record
                FROM public.dts_source_scope_snapshots
                WHERE snapshot_id = scope_record.candidate_snapshot_id
                  AND source_region = scope_record.source_region
                  AND source_table = scope_record.source_table
                  AND scope_kind = scope_record.scope_kind
                  AND scope_level = scope_record.scope_level
                  AND scope_key = scope_record.scope_key;
                IF NOT FOUND
                   OR snapshot_record.epoch_state <> scope_record.state
                   OR head_record.active_candidate_snapshot_id IS DISTINCT FROM
                        scope_record.candidate_snapshot_id THEN
                    RAISE EXCEPTION 'SOURCE_SCOPE_CANDIDATE_INCONSISTENT'
                        USING ERRCODE = '23514';
                END IF;
            ELSIF scope_record.state = 'FAILED' THEN
                SELECT epoch_state INTO snapshot_record
                FROM public.dts_source_scope_snapshots
                WHERE snapshot_id = scope_record.candidate_snapshot_id;
                IF NOT FOUND OR snapshot_record.epoch_state <> 'FAILED'
                   OR head_record.active_candidate_snapshot_id IS NOT NULL THEN
                    RAISE EXCEPTION 'SOURCE_SCOPE_FAILED_INCONSISTENT'
                        USING ERRCODE = '23514';
                END IF;
            ELSIF scope_record.state = 'STALE' THEN
                SELECT epoch_state INTO snapshot_record
                FROM public.dts_source_scope_snapshots
                WHERE snapshot_id = scope_record.active_snapshot_id;
                IF NOT FOUND OR snapshot_record.epoch_state <> 'STALE' THEN
                    RAISE EXCEPTION 'SOURCE_SCOPE_STALE_INCONSISTENT'
                        USING ERRCODE = '23514';
                END IF;
            END IF;
            RETURN NULL;
        END
        $function$;

        CREATE FUNCTION public.check_dts_scope_head_integrity_v2()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE head_record record;
        DECLARE snapshot_record record;
        BEGIN
            head_record := CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
            IF head_record.current_snapshot_id IS NOT NULL THEN
                SELECT * INTO snapshot_record
                FROM public.dts_source_scope_snapshots
                WHERE snapshot_id = head_record.current_snapshot_id
                  AND source_region = head_record.source_region
                  AND source_table = head_record.source_table;
                IF NOT FOUND
                   OR snapshot_record.published_generation IS DISTINCT FROM
                        head_record.current_generation
                   OR snapshot_record.epoch_state NOT IN ('COMPLETE','STALE') THEN
                    RAISE EXCEPTION 'SOURCE_TABLE_GENERATION_HEAD_INCONSISTENT'
                        USING ERRCODE = '23514';
                END IF;
            END IF;
            IF head_record.active_candidate_snapshot_id IS NOT NULL THEN
                SELECT * INTO snapshot_record
                FROM public.dts_source_scope_snapshots
                WHERE snapshot_id = head_record.active_candidate_snapshot_id
                  AND source_region = head_record.source_region
                  AND source_table = head_record.source_table;
                IF NOT FOUND OR snapshot_record.epoch_state NOT IN (
                    'LOADING','VERIFYING'
                ) OR NOT EXISTS (
                    SELECT 1 FROM public.dts_source_scope_states scope_state
                    WHERE scope_state.candidate_snapshot_id =
                            head_record.active_candidate_snapshot_id
                      AND scope_state.state = snapshot_record.epoch_state
                ) THEN
                    RAISE EXCEPTION 'SOURCE_TABLE_CANDIDATE_HEAD_INCONSISTENT'
                        USING ERRCODE = '23514';
                END IF;
            ELSIF EXISTS (
                SELECT 1 FROM public.dts_source_scope_states scope_state
                WHERE scope_state.source_region = head_record.source_region
                  AND scope_state.source_table = head_record.source_table
                  AND scope_state.state IN ('LOADING','VERIFYING')
            ) THEN
                RAISE EXCEPTION 'SOURCE_TABLE_CANDIDATE_HEAD_MISSING'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NULL;
        END
        $function$;

        DO $install_scope_internal_guards$
        DECLARE table_name text;
        BEGIN
            FOREACH table_name IN ARRAY ARRAY[{mutable_tables}] LOOP
                EXECUTE format(
                    'CREATE TRIGGER guard_%I_internal_write_v2 '
                    'BEFORE INSERT OR UPDATE OR DELETE ON public.%I '
                    'FOR EACH ROW EXECUTE FUNCTION '
                    'public.guard_dts_scope_internal_write_v2()',
                    table_name,table_name
                );
            END LOOP;
        END
        $install_scope_internal_guards$;

        CREATE TRIGGER guard_dts_source_snapshot_rows_immutable_v2
        BEFORE UPDATE OR DELETE ON public.dts_source_snapshot_rows
        FOR EACH ROW EXECUTE FUNCTION
            public.guard_dts_snapshot_rows_immutable_v2();
        CREATE TRIGGER guard_dts_source_scope_commands_append_only_v2
        BEFORE UPDATE OR DELETE ON public.dts_source_scope_commands
        FOR EACH ROW EXECUTE FUNCTION public.guard_dts_scope_append_only_v2();
        CREATE TRIGGER guard_dts_scope_transition_audits_append_only_v2
        BEFORE UPDATE OR DELETE ON public.dts_source_scope_transition_audits
        FOR EACH ROW EXECUTE FUNCTION public.guard_dts_scope_append_only_v2();

        CREATE CONSTRAINT TRIGGER check_dts_scope_state_integrity_v2
        AFTER INSERT OR UPDATE ON public.dts_source_scope_states
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION
            public.check_dts_scope_state_integrity_v2();
        CREATE CONSTRAINT TRIGGER check_dts_scope_head_integrity_v2
        AFTER INSERT OR UPDATE ON public.dts_source_table_publish_generations
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION
            public.check_dts_scope_head_integrity_v2();

        REVOKE ALL ON FUNCTION
            public.guard_dts_scope_internal_write_v2(),
            public.guard_dts_scope_append_only_v2(),
            public.guard_dts_snapshot_rows_immutable_v2(),
            public.check_dts_scope_state_integrity_v2(),
            public.check_dts_scope_head_integrity_v2()
        FROM PUBLIC;
        """
    )


def _install_scope_output_functions() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public._emit_source_scope_revision_v2(
            p_source_region text,
            p_source_table text,
            p_scope_kind text,
            p_scope_level text,
            p_scope_key text,
            p_scope_row_version bigint,
            p_state text,
            p_active_snapshot_id text,
            p_active_fence_hash text
        )
        RETURNS jsonb
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE
            v_canonical_key jsonb;
            v_canonical_hash text;
            aggregate_identifier text;
            aggregate_payload jsonb;
            aggregate_payload_hash text;
            next_revision bigint;
            event_identifier text;
            dirty_identity jsonb;
            dirty_fingerprint text;
            affected record;
        BEGIN
            IF current_setting(
                    'tit.scope_coordinator_internal',true
               ) IS DISTINCT FROM 'on'
               OR public.dts_source_scope_identity_valid_v2(
                    p_source_region,p_source_table,p_scope_kind,
                    p_scope_level,p_scope_key
               ) IS DISTINCT FROM true
               OR p_scope_row_version < 1
               OR p_state NOT IN (
                    'INCOMPLETE','LOADING','VERIFYING','COMPLETE',
                    'FAILED','STALE'
               )
               OR (p_active_fence_hash IS NOT NULL AND
                   p_active_fence_hash !~ '^[0-9a-f]{64}$') THEN
                RAISE EXCEPTION 'SOURCE_SCOPE_OUTPUT_AUTHORITY_DENIED'
                    USING ERRCODE = '42501';
            END IF;

            v_canonical_key := jsonb_build_object(
                'source_region',p_source_region,
                'source_table',p_source_table,
                'scope_kind',p_scope_kind,
                'scope_level',p_scope_level,
                'scope_key',p_scope_key
            );
            v_canonical_hash := public.dts_canonical_json_sha256_v1(
                v_canonical_key
            );
            aggregate_identifier := 'v2:SOURCE_SCOPE:' || v_canonical_hash;
            aggregate_payload := jsonb_build_object(
                'protocol','source-scope-state-v1',
                'source_region',p_source_region,
                'source_table',p_source_table,
                'scope_kind',p_scope_kind,
                'scope_level',p_scope_level,
                'scope_key',p_scope_key,
                'scope_row_version',p_scope_row_version,
                'state',p_state,
                'active_snapshot_id',p_active_snapshot_id,
                'active_epoch_id',p_active_snapshot_id,
                'active_fence_hash',p_active_fence_hash
            );
            aggregate_payload_hash := public.dts_canonical_json_sha256_v1(
                aggregate_payload
            );

            SELECT revision + 1 INTO next_revision
            FROM public.domain_aggregate_revisions
            WHERE aggregate_type = 'SOURCE_SCOPE'
              AND aggregate_id = aggregate_identifier
            FOR UPDATE;
            IF NOT FOUND THEN
                next_revision := 1;
                INSERT INTO public.domain_aggregate_revisions (
                    aggregate_type,aggregate_id,canonical_key,
                    canonical_key_sha256,revision,last_source_row_revision,
                    last_source_position,aggregate_state,
                    aggregate_state_sha256,updated_at
                ) VALUES (
                    'SOURCE_SCOPE',aggregate_identifier,v_canonical_key,
                    v_canonical_hash,next_revision,NULL,NULL,aggregate_payload,
                    aggregate_payload_hash,clock_timestamp()
                );
            ELSE
                UPDATE public.domain_aggregate_revisions
                SET revision = next_revision,
                    canonical_key = v_canonical_key,
                    canonical_key_sha256 = v_canonical_hash,
                    last_source_row_revision = NULL,
                    last_source_position = NULL,
                    aggregate_state = aggregate_payload,
                    aggregate_state_sha256 = aggregate_payload_hash,
                    updated_at = clock_timestamp()
                WHERE aggregate_type = 'SOURCE_SCOPE'
                  AND aggregate_id = aggregate_identifier;
            END IF;

            event_identifier := 'SRC-SCOPE-' || v_canonical_hash || '-'
                                || next_revision::text;
            INSERT INTO public.outbox_events (
                outbox_id,event_id,aggregate_type,aggregate_id,event_type,
                payload,status,available_at,attempt_count,last_error,
                created_at,published_at
            ) VALUES (
                event_identifier,event_identifier,'SOURCE_SCOPE',
                aggregate_identifier,'source_wide.changed.v2',
                jsonb_build_object(
                    'protocol','source-wide-change-v2',
                    'aggregate_type','SOURCE_SCOPE',
                    'aggregate_id',aggregate_identifier,
                    'aggregate_key',v_canonical_key,
                    'aggregate_revision',next_revision,
                    'changed_fields',jsonb_build_array('scope_state'),
                    'scope_state',aggregate_payload
                ),
                'PENDING',clock_timestamp(),0,NULL,clock_timestamp(),NULL
            );

            dirty_identity := jsonb_build_object(
                'source_region',p_source_region,
                'source_table',p_source_table,
                'scope_kind',p_scope_kind,
                'scope_level',p_scope_level,
                'scope_key',p_scope_key
            );
            dirty_fingerprint := public.dts_canonical_json_sha256_v1(
                jsonb_build_object(
                    'protocol','dirty-scope-v1',
                    'identity',dirty_identity,
                    'scope_row_version',p_scope_row_version,
                    'state',p_state,
                    'active_snapshot_id',p_active_snapshot_id,
                    'active_epoch_id',p_active_snapshot_id,
                    'active_fence_hash',p_active_fence_hash
                )
            );

            FOR affected IN
                WITH dependency_documents AS (
                    SELECT dependency_keys
                    FROM public.dts_source_snapshot_rows
                    WHERE source_region = p_source_region
                      AND source_table = p_source_table
                      AND scope_kind = p_scope_kind
                      AND scope_level = p_scope_level
                      AND scope_key = p_scope_key
                    UNION ALL
                    SELECT dependency_keys
                    FROM public.dts_source_scope_memberships
                    WHERE source_region = p_source_region
                      AND source_table = p_source_table
                      AND scope_kind = p_scope_kind
                      AND scope_level = p_scope_level
                      AND scope_key = p_scope_key
                ), keys AS (
                    SELECT 'COURSE'::text key_type,
                           value #>> '{}' key_part_1,''::text key_part_2
                    FROM dependency_documents,
                         jsonb_array_elements(
                            dependency_keys->'course_ids'
                         ) item(value)
                    UNION ALL
                    SELECT 'TEACHER',value #>> '{}',''
                    FROM dependency_documents,
                         jsonb_array_elements(
                            dependency_keys->'teacher_ids'
                         ) item(value)
                    UNION ALL
                    SELECT 'LABEL',value #>> '{}',''
                    FROM dependency_documents,
                         jsonb_array_elements(
                            dependency_keys->'label_ids'
                         ) item(value)
                    UNION ALL
                    SELECT 'COMPLAINT_CATEGORY',value #>> '{}',''
                    FROM dependency_documents,
                         jsonb_array_elements(
                            dependency_keys->'category_ids'
                         ) item(value)
                    WHERE p_source_region = 'dom'
                    UNION ALL
                    SELECT 'TEACHER_STUDENT',teacher.value #>> '{}',
                           student.value #>> '{}'
                    FROM dependency_documents,
                         jsonb_array_elements(
                            dependency_keys->'teacher_ids'
                         ) teacher(value),
                         jsonb_array_elements(
                            dependency_keys->'student_subjects'
                         ) student(value)
                    UNION ALL
                    SELECT 'TEACHER',p_scope_key,''
                    WHERE p_scope_level = 'TEACHER'
                )
                SELECT DISTINCT key_type,key_part_1,key_part_2 FROM keys
            LOOP
                PERFORM public._upsert_dts_dirty_key_input_v2(
                    p_source_region,affected.key_type,
                    affected.key_part_1,affected.key_part_2,
                    'SCOPE_REVISION',dirty_identity,p_scope_row_version,
                    dirty_fingerprint
                );
            END LOOP;

            RETURN jsonb_build_object(
                'aggregate_id',aggregate_identifier,
                'aggregate_revision',next_revision,
                'outbox_event_id',event_identifier
            );
        END
        $function$;

        REVOKE ALL ON FUNCTION
            public._emit_source_scope_revision_v2(
                text,text,text,text,text,bigint,text,text,text
            )
        FROM PUBLIC;
        """
    )


def _install_scope_state_machine_helpers() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public._dts_scope_command_replay_v2(
            p_command_id text,
            p_command_type text,
            p_request_hash text
        )
        RETURNS jsonb
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE command_record record;
        BEGIN
            IF p_command_id IS NULL OR p_command_id = ''
               OR length(p_command_id) > 160
               OR p_command_type NOT IN (
                    'BEGIN','HEARTBEAT','VERIFY','ABORT',
                    'TAKEOVER','PUBLISH','INVALIDATE'
               )
               OR p_request_hash !~ '^[0-9a-f]{64}$' THEN
                RAISE EXCEPTION 'SOURCE_SCOPE_COMMAND_INVALID'
                    USING ERRCODE = '22023';
            END IF;
            PERFORM pg_advisory_xact_lock(
                hashtextextended('source-scope-command:' || p_command_id,0)
            );
            SELECT * INTO command_record
            FROM public.dts_source_scope_commands
            WHERE command_id = p_command_id;
            IF NOT FOUND THEN
                RETURN NULL;
            END IF;
            IF command_record.command_type IS DISTINCT FROM p_command_type
               OR command_record.request_hash IS DISTINCT FROM p_request_hash THEN
                RAISE EXCEPTION 'SOURCE_SCOPE_COMMAND_CONFLICT'
                    USING ERRCODE = '23514';
            END IF;
            RETURN command_record.response_payload
                   || jsonb_build_object('replay_status','REPLAYED');
        END
        $function$;

        CREATE FUNCTION public._record_source_scope_command_v2(
            p_command_id text,
            p_command_type text,
            p_request_hash text,
            p_snapshot_id text,
            p_source_region text,
            p_source_table text,
            p_scope_kind text,
            p_scope_level text,
            p_scope_key text,
            p_response jsonb,
            p_actor text,
            p_from_state text,
            p_to_state text,
            p_scope_row_version bigint,
            p_detail jsonb
        )
        RETURNS void
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        BEGIN
            IF current_setting(
                    'tit.scope_coordinator_internal',true
               ) IS DISTINCT FROM 'on'
               OR p_actor IS NULL OR btrim(p_actor) = ''
               OR jsonb_typeof(p_response) <> 'object'
               OR jsonb_typeof(p_detail) <> 'object' THEN
                RAISE EXCEPTION 'SOURCE_SCOPE_COMMAND_RECORD_FORBIDDEN'
                    USING ERRCODE = '42501';
            END IF;
            INSERT INTO public.dts_source_scope_commands (
                command_id,command_type,request_hash,snapshot_id,
                scope_identity,response_payload,executed_by
            ) VALUES (
                p_command_id,p_command_type,p_request_hash,p_snapshot_id,
                jsonb_build_object(
                    'source_region',p_source_region,
                    'source_table',p_source_table,
                    'scope_kind',p_scope_kind,
                    'scope_level',p_scope_level,
                    'scope_key',p_scope_key
                ),
                p_response,p_actor
            );
            INSERT INTO public.dts_source_scope_transition_audits (
                command_id,snapshot_id,source_region,source_table,
                scope_kind,scope_level,scope_key,from_state,to_state,
                scope_row_version,detail,actor
            ) VALUES (
                p_command_id,p_snapshot_id,p_source_region,p_source_table,
                p_scope_kind,p_scope_level,p_scope_key,p_from_state,p_to_state,
                p_scope_row_version,p_detail,p_actor
            );
        END
        $function$;

        CREATE FUNCTION public.dts_scope_snapshot_fence_manifest_v2(
            p_snapshot_id text
        )
        RETURNS jsonb
        LANGUAGE sql
        STABLE
        STRICT
        PARALLEL SAFE
        SET search_path = pg_catalog, public
        AS $function$
            SELECT coalesce(jsonb_agg(
                jsonb_build_object(
                    'source_region',source_region,
                    'source_partition_epoch_id',source_partition_epoch_id,
                    'topic',topic,
                    'partition_id',partition_id,
                    'start_next_offset',start_next_offset,
                    'end_next_offset',end_next_offset
                ) ORDER BY convert_to(source_region,'UTF8'),
                           convert_to(source_partition_epoch_id,'UTF8'),
                           convert_to(topic,'UTF8'),partition_id
            ),'[]'::jsonb)
            FROM public.dts_source_snapshot_fences
            WHERE snapshot_id = p_snapshot_id
        $function$;

        CREATE FUNCTION public.dts_scope_snapshot_row_manifest_v2(
            p_snapshot_id text
        )
        RETURNS jsonb
        LANGUAGE sql
        STABLE
        STRICT
        PARALLEL SAFE
        SET search_path = pg_catalog, public
        AS $function$
            SELECT coalesce(jsonb_agg(
                jsonb_build_object(
                    'source_key_data',source_key_data,
                    'snapshot_row_hash',snapshot_row_hash,
                    'dependency_keys_hash',
                        public.dts_canonical_json_sha256_v1(dependency_keys)
                ) ORDER BY CASE source_key_type
                                WHEN 'NUMERIC' THEN 0 ELSE 1 END,
                           source_key_numeric NULLS LAST,
                           convert_to(source_key_text,'UTF8') NULLS LAST
            ),'[]'::jsonb)
            FROM public.dts_source_snapshot_rows
            WHERE snapshot_id = p_snapshot_id
        $function$;

        CREATE FUNCTION public.dts_scope_candidate_error_v2(
            p_snapshot_id text,
            p_require_no_overlay boolean
        )
        RETURNS text
        LANGUAGE plpgsql
        STABLE
        STRICT
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE snapshot_record public.dts_source_scope_snapshots%ROWTYPE;
        BEGIN
            SELECT * INTO snapshot_record
            FROM public.dts_source_scope_snapshots
            WHERE snapshot_id = p_snapshot_id;
            IF NOT FOUND THEN
                RETURN 'SOURCE_SCOPE_SNAPSHOT_NOT_FOUND';
            END IF;
            IF snapshot_record.scope_kind <> 'CURRENT' THEN
                RETURN 'HISTORY_SNAPSHOT_PUBLISH_NOT_IMPLEMENTED';
            END IF;
            IF public.dts_scope_snapshot_fence_manifest_v2(p_snapshot_id)
               IS DISTINCT FROM snapshot_record.snapshot_fence_vector
               OR public.dts_canonical_json_sha256_v1(
                    public.dts_scope_snapshot_fence_manifest_v2(p_snapshot_id)
               ) IS DISTINCT FROM snapshot_record.snapshot_fence_hash THEN
                RETURN 'SOURCE_SCOPE_FENCE_RELATION_MISMATCH';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM public.dts_source_snapshot_fences fence
                LEFT JOIN public.dts_source_partition_epochs epoch
                  ON epoch.source_region = fence.source_region
                 AND epoch.source_partition_epoch_id =
                        fence.source_partition_epoch_id
                 AND epoch.topic = fence.topic
                 AND epoch.partition_id = fence.partition_id
                LEFT JOIN public.dts_ingest_checkpoints checkpoint
                  ON checkpoint.source_region = fence.source_region
                 AND checkpoint.topic = fence.topic
                 AND checkpoint.partition_id = fence.partition_id
                WHERE fence.snapshot_id = p_snapshot_id
                  AND (
                       epoch.source_region IS NULL
                       OR epoch.epoch_kind <> 'BROKER'
                       OR epoch.status <> 'ACTIVE'
                       OR epoch.start_offset > fence.start_next_offset
                       OR checkpoint.source_partition_epoch_id IS DISTINCT FROM
                            fence.source_partition_epoch_id
                       OR checkpoint.is_current_epoch IS DISTINCT FROM true
                       OR checkpoint.next_offset < fence.end_next_offset
                  )
            ) THEN
                RETURN 'SOURCE_SCOPE_FENCE_NOT_REACHED';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM public.dts_source_rows live
                WHERE live.source_region = snapshot_record.source_region
                  AND live.source_table = snapshot_record.source_table
                  AND NOT live.is_deleted
                  AND (
                       snapshot_record.scope_level = 'GLOBAL'
                       OR (
                           jsonb_typeof(live.dependency_keys) = 'object'
                           AND jsonb_typeof(
                                live.dependency_keys->'teacher_ids'
                               ) = 'array'
                           AND live.dependency_keys->'teacher_ids'
                                ? snapshot_record.scope_key
                       )
                  )
                  AND live.provenance_state IS DISTINCT FROM 'V2_CONFIRMED'
            ) THEN
                RETURN 'SOURCE_SCOPE_UNRECONCILED_LIVE_ROW';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM public.dts_source_snapshot_rows staged
                LEFT JOIN public.dts_source_rows live
                  ON live.source_region = staged.source_region
                 AND live.source_table = staged.source_table
                 AND live.source_key = staged.source_key
                WHERE staged.snapshot_id = p_snapshot_id
                  AND (
                       live.source_key IS NULL
                       OR live.is_deleted
                       OR live.provenance_state <> 'V2_CONFIRMED'
                       OR live.source_row_revision IS DISTINCT FROM
                            staged.base_source_row_revision
                       OR live.source_payload_hash IS DISTINCT FROM
                            staged.base_row_hash
                       OR live.source_row IS DISTINCT FROM
                            staged.protected_source_row
                       OR live.source_payload_hash IS DISTINCT FROM
                            staged.snapshot_row_hash
                       OR live.dependency_keys IS DISTINCT FROM
                            staged.dependency_keys
                  )
            ) THEN
                RETURN 'SNAPSHOT_DIFF_REQUIRED_NOT_IMPLEMENTED';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM public.dts_source_rows live
                WHERE live.source_region = snapshot_record.source_region
                  AND live.source_table = snapshot_record.source_table
                  AND NOT live.is_deleted
                  AND (
                       snapshot_record.scope_level = 'GLOBAL'
                       OR (
                           jsonb_typeof(live.dependency_keys) = 'object'
                           AND jsonb_typeof(
                                live.dependency_keys->'teacher_ids'
                               ) = 'array'
                           AND live.dependency_keys->'teacher_ids'
                                ? snapshot_record.scope_key
                       )
                  )
                  AND NOT EXISTS (
                       SELECT 1
                       FROM public.dts_source_snapshot_rows staged
                       WHERE staged.snapshot_id = p_snapshot_id
                         AND staged.source_key = live.source_key
                  )
            ) THEN
                RETURN 'SOURCE_SCOPE_LIVE_ROW_MISSING_FROM_SNAPSHOT';
            END IF;
            IF p_require_no_overlay AND EXISTS (
                SELECT 1
                FROM public.dts_source_scope_memberships membership
                WHERE membership.source_region = snapshot_record.source_region
                  AND membership.source_table = snapshot_record.source_table
                  AND membership.scope_kind = snapshot_record.scope_kind
                  AND membership.scope_level = snapshot_record.scope_level
                  AND membership.scope_key = snapshot_record.scope_key
                  AND membership.cdc_overlay_is_present IS NOT NULL
            ) THEN
                RETURN 'CDC_OVERLAY_REPLAY_NOT_IMPLEMENTED';
            END IF;
            RETURN NULL;
        END
        $function$;

        REVOKE ALL ON FUNCTION
            public._dts_scope_command_replay_v2(text,text,text),
            public._record_source_scope_command_v2(
                text,text,text,text,text,text,text,text,text,jsonb,text,
                text,text,bigint,jsonb
            ),
            public.dts_scope_snapshot_fence_manifest_v2(text),
            public.dts_scope_snapshot_row_manifest_v2(text),
            public.dts_scope_candidate_error_v2(text,boolean)
        FROM PUBLIC;
        """
    )


def _install_scope_begin_stage_heartbeat() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.begin_source_snapshot_candidate_v2(
            p_command_id text,
            p_snapshot_id text,
            p_source_region text,
            p_source_table text,
            p_scope_kind text,
            p_scope_level text,
            p_scope_key text,
            p_snapshot_as_of timestamptz,
            p_snapshot_consistency_token text,
            p_partition_offsets jsonb,
            p_history_from timestamptz,
            p_history_through timestamptz,
            p_owner text,
            p_lease_seconds integer,
            p_expected_head_version bigint,
            p_expected_scope_version bigint
        )
        RETURNS jsonb
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE
            normalized_offsets jsonb;
            request_hash text;
            replay jsonb;
            head_record public.dts_source_table_publish_generations%ROWTYPE;
            scope_record public.dts_source_scope_states%ROWTYPE;
            old_state text;
            old_active_snapshot_id text;
            old_fence_hash text;
            lease_token text;
            response jsonb;
            scope_version bigint;
        BEGIN
            normalized_offsets :=
                public.dts_normalize_scope_partition_offsets_v2(
                    p_partition_offsets,p_source_region
                );
            request_hash := public.dts_canonical_json_sha256_v1(
                jsonb_build_object(
                    'protocol','scope-begin-command-v1',
                    'snapshot_id',p_snapshot_id,
                    'source_region',p_source_region,
                    'source_table',p_source_table,
                    'scope_kind',p_scope_kind,
                    'scope_level',p_scope_level,
                    'scope_key',p_scope_key,
                    'snapshot_as_of',to_jsonb(p_snapshot_as_of),
                    'snapshot_consistency_token_hash',encode(sha256(
                        convert_to(p_snapshot_consistency_token,'UTF8')
                    ),'hex'),
                    'partition_offsets',normalized_offsets,
                    'history_from',to_jsonb(p_history_from),
                    'history_through',to_jsonb(p_history_through),
                    'owner',p_owner,
                    'lease_seconds',p_lease_seconds,
                    'expected_head_version',p_expected_head_version,
                    'expected_scope_version',p_expected_scope_version
                )
            );
            replay := public._dts_scope_command_replay_v2(
                p_command_id,'BEGIN',request_hash
            );
            IF replay IS NOT NULL THEN RETURN replay; END IF;

            IF p_snapshot_id IS NULL OR p_snapshot_id = ''
               OR length(p_snapshot_id) > 160
               OR p_snapshot_as_of IS NULL
               OR p_snapshot_consistency_token IS NULL
               OR btrim(p_snapshot_consistency_token) = ''
               OR p_owner IS NULL OR btrim(p_owner) = ''
               OR length(p_owner) > 128
               OR p_lease_seconds NOT BETWEEN 15 AND 300
               OR p_expected_head_version < 1
               OR p_expected_scope_version < 0
               OR public.dts_source_scope_identity_valid_v2(
                    p_source_region,p_source_table,p_scope_kind,
                    p_scope_level,p_scope_key
               ) IS DISTINCT FROM true
               OR NOT (
                    (p_scope_kind = 'CURRENT'
                     AND p_history_from IS NULL
                     AND p_history_through IS NULL)
                    OR (p_scope_kind = 'HISTORY'
                        AND p_history_from IS NOT NULL
                        AND p_history_through IS NOT NULL
                        AND p_history_from <= p_history_through)
               ) THEN
                RAISE EXCEPTION 'SOURCE_SCOPE_BEGIN_INVALID'
                    USING ERRCODE = '22023';
            END IF;

            SELECT * INTO head_record
            FROM public.dts_source_table_publish_generations
            WHERE source_region = p_source_region
              AND source_table = p_source_table
            FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'SOURCE_TABLE_GENERATION_HEAD_MISSING'
                    USING ERRCODE = '23503';
            END IF;
            IF head_record.row_version <> p_expected_head_version THEN
                RAISE EXCEPTION 'SOURCE_TABLE_GENERATION_HEAD_VERSION_CONFLICT'
                    USING ERRCODE = '40001';
            END IF;
            IF head_record.active_candidate_snapshot_id IS NOT NULL THEN
                RAISE EXCEPTION 'SOURCE_TABLE_ACTIVE_CANDIDATE_CONFLICT'
                    USING ERRCODE = '55000';
            END IF;

            SELECT * INTO scope_record
            FROM public.dts_source_scope_states
            WHERE source_region = p_source_region
              AND source_table = p_source_table
              AND scope_kind = p_scope_kind
              AND scope_level = p_scope_level
              AND scope_key = p_scope_key
            FOR UPDATE;
            IF FOUND THEN
                IF scope_record.row_version <> p_expected_scope_version THEN
                    RAISE EXCEPTION 'SOURCE_SCOPE_VERSION_CONFLICT'
                        USING ERRCODE = '40001';
                END IF;
                IF scope_record.state IN ('LOADING','VERIFYING') THEN
                    RAISE EXCEPTION 'SOURCE_SCOPE_ACTIVE_CANDIDATE_CONFLICT'
                        USING ERRCODE = '55000';
                END IF;
                old_state := scope_record.state;
                old_active_snapshot_id := scope_record.active_snapshot_id;
            ELSE
                IF p_expected_scope_version <> 0 THEN
                    RAISE EXCEPTION 'SOURCE_SCOPE_VERSION_CONFLICT'
                        USING ERRCODE = '40001';
                END IF;
                old_state := 'INCOMPLETE';
                old_active_snapshot_id := NULL;
            END IF;

            IF EXISTS (
                SELECT 1
                FROM jsonb_to_recordset(normalized_offsets) AS fence(
                    source_region text,
                    source_partition_epoch_id text,
                    topic text,
                    partition_id integer,
                    start_next_offset bigint,
                    end_next_offset bigint
                )
                LEFT JOIN public.dts_source_partition_epochs epoch
                  ON epoch.source_region = fence.source_region
                 AND epoch.source_partition_epoch_id =
                        fence.source_partition_epoch_id
                 AND epoch.topic = fence.topic
                 AND epoch.partition_id = fence.partition_id
                WHERE epoch.source_region IS NULL
                   OR epoch.epoch_kind <> 'BROKER'
                   OR epoch.status <> 'ACTIVE'
                   OR epoch.start_offset > fence.start_next_offset
            ) THEN
                RAISE EXCEPTION 'SOURCE_SCOPE_FENCE_EPOCH_INVALID'
                    USING ERRCODE = '23503';
            END IF;

            lease_token := gen_random_uuid()::text;
            PERFORM set_config('tit.scope_coordinator_internal','on',true);
            IF old_active_snapshot_id IS NOT NULL THEN
                SELECT snapshot_fence_hash INTO old_fence_hash
                FROM public.dts_source_scope_snapshots
                WHERE snapshot_id = old_active_snapshot_id;
                UPDATE public.dts_source_scope_snapshots
                SET epoch_state = 'STALE',
                    invalidated_at = clock_timestamp(),
                    row_version = row_version + 1
                WHERE snapshot_id = old_active_snapshot_id
                  AND epoch_state = 'COMPLETE';
            END IF;

            INSERT INTO public.dts_source_scope_snapshots (
                snapshot_id,source_region,source_table,scope_kind,scope_level,
                scope_key,epoch_state,snapshot_as_of,
                snapshot_consistency_token,
                snapshot_consistency_token_hash,snapshot_fence_vector,
                partition_offsets,snapshot_fence_hash,history_from,
                history_through,base_publish_generation,base_snapshot_id,
                begin_request_hash,created_by
            ) VALUES (
                p_snapshot_id,p_source_region,p_source_table,p_scope_kind,
                p_scope_level,p_scope_key,'LOADING',p_snapshot_as_of,
                p_snapshot_consistency_token,encode(sha256(convert_to(
                    p_snapshot_consistency_token,'UTF8'
                )),'hex'),normalized_offsets,normalized_offsets,
                public.dts_canonical_json_sha256_v1(normalized_offsets),
                p_history_from,p_history_through,head_record.current_generation,
                old_active_snapshot_id,request_hash,p_owner
            );
            INSERT INTO public.dts_source_snapshot_fences (
                snapshot_id,source_region,source_table,scope_kind,scope_level,
                scope_key,source_partition_epoch_id,topic,partition_id,
                start_next_offset,end_next_offset
            )
            SELECT p_snapshot_id,p_source_region,p_source_table,p_scope_kind,
                   p_scope_level,p_scope_key,fence.source_partition_epoch_id,
                   fence.topic,fence.partition_id,fence.start_next_offset,
                   fence.end_next_offset
            FROM jsonb_to_recordset(normalized_offsets) AS fence(
                source_region text,
                source_partition_epoch_id text,
                topic text,
                partition_id integer,
                start_next_offset bigint,
                end_next_offset bigint
            );

            IF scope_record.source_region IS NULL THEN
                INSERT INTO public.dts_source_scope_states (
                    source_region,source_table,scope_kind,scope_level,scope_key,
                    state,active_snapshot_id,candidate_snapshot_id,
                    completed_at,invalidated_at,row_version,updated_at
                ) VALUES (
                    p_source_region,p_source_table,p_scope_kind,p_scope_level,
                    p_scope_key,'LOADING',NULL,p_snapshot_id,NULL,NULL,1,
                    clock_timestamp()
                ) RETURNING row_version INTO scope_version;
            ELSE
                UPDATE public.dts_source_scope_states
                SET state = 'LOADING',
                    candidate_snapshot_id = p_snapshot_id,
                    invalidated_at = CASE
                        WHEN active_snapshot_id IS NULL THEN invalidated_at
                        ELSE clock_timestamp() END,
                    row_version = row_version + 1,
                    updated_at = clock_timestamp()
                WHERE source_region = p_source_region
                  AND source_table = p_source_table
                  AND scope_kind = p_scope_kind
                  AND scope_level = p_scope_level
                  AND scope_key = p_scope_key
                RETURNING row_version INTO scope_version;
            END IF;
            UPDATE public.dts_source_table_publish_generations
            SET active_candidate_snapshot_id = p_snapshot_id,
                candidate_owner = p_owner,
                candidate_lease_token = lease_token,
                candidate_lease_expires_at =
                    clock_timestamp() + make_interval(secs=>p_lease_seconds),
                row_version = row_version + 1,
                updated_at = clock_timestamp()
            WHERE source_region = p_source_region
              AND source_table = p_source_table
            RETURNING row_version,candidate_lease_expires_at
            INTO head_record.row_version,head_record.candidate_lease_expires_at;

            PERFORM public._emit_source_scope_revision_v2(
                p_source_region,p_source_table,p_scope_kind,p_scope_level,
                p_scope_key,scope_version,'LOADING',old_active_snapshot_id,
                old_fence_hash
            );
            response := jsonb_build_object(
                'status','STARTED',
                'snapshot_id',p_snapshot_id,
                'lease_token',lease_token,
                'lease_expires_at',head_record.candidate_lease_expires_at,
                'head_row_version',head_record.row_version,
                'scope_row_version',scope_version
            );
            PERFORM public._record_source_scope_command_v2(
                p_command_id,'BEGIN',request_hash,p_snapshot_id,
                p_source_region,p_source_table,p_scope_kind,p_scope_level,
                p_scope_key,response,p_owner,old_state,'LOADING',scope_version,
                jsonb_build_object(
                    'base_publish_generation',head_record.current_generation,
                    'base_snapshot_id',old_active_snapshot_id,
                    'snapshot_fence_hash',
                        public.dts_canonical_json_sha256_v1(normalized_offsets)
                )
            );
            RETURN response;
        END
        $function$;

        CREATE FUNCTION public.stage_source_snapshot_row_v2(
            p_snapshot_id text,
            p_owner text,
            p_lease_token text,
            p_source_key_data jsonb,
            p_dependency_keys jsonb,
            p_protected_source_row jsonb,
            p_actor text
        )
        RETURNS jsonb
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE
            snapshot_record public.dts_source_scope_snapshots%ROWTYPE;
            head_record public.dts_source_table_publish_generations%ROWTYPE;
            live_record public.dts_source_rows%ROWTYPE;
            existing_record public.dts_source_snapshot_rows%ROWTYPE;
            key_type text;
            key_numeric numeric;
            key_text text;
            v_source_key text;
            row_hash text;
        BEGIN
            SELECT * INTO snapshot_record
            FROM public.dts_source_scope_snapshots
            WHERE snapshot_id = p_snapshot_id
            FOR SHARE;
            IF NOT FOUND OR snapshot_record.epoch_state <> 'LOADING' THEN
                RAISE EXCEPTION 'SOURCE_SCOPE_STAGING_NOT_LOADING'
                    USING ERRCODE = '55000';
            END IF;
            SELECT * INTO head_record
            FROM public.dts_source_table_publish_generations
            WHERE source_region = snapshot_record.source_region
              AND source_table = snapshot_record.source_table
            FOR SHARE;
            IF head_record.active_candidate_snapshot_id IS DISTINCT FROM
                    p_snapshot_id
               OR head_record.candidate_owner IS DISTINCT FROM p_owner
               OR head_record.candidate_lease_token IS DISTINCT FROM
                    p_lease_token
               OR head_record.candidate_lease_expires_at <= clock_timestamp()
               OR p_actor IS NULL OR btrim(p_actor) = '' THEN
                RAISE EXCEPTION 'SOURCE_SCOPE_CANDIDATE_AUTHORITY_DENIED'
                    USING ERRCODE = '42501';
            END IF;
            IF jsonb_typeof(p_source_key_data) <> 'object'
               OR p_source_key_data <> jsonb_build_object(
                    'id',p_source_key_data->'id'
               ) THEN
                RAISE EXCEPTION 'SOURCE_SCOPE_SOURCE_KEY_INVALID'
                    USING ERRCODE = '22023';
            END IF;
            IF jsonb_typeof(p_source_key_data->'id') = 'number' THEN
                key_type := 'NUMERIC';
                key_numeric := (p_source_key_data->>'id')::numeric;
                v_source_key := trim_scale(key_numeric)::text;
            ELSIF jsonb_typeof(p_source_key_data->'id') = 'string' THEN
                key_type := 'TEXT';
                key_text := p_source_key_data->>'id';
                v_source_key := key_text;
            ELSE
                RAISE EXCEPTION 'SOURCE_SCOPE_SOURCE_KEY_INVALID'
                    USING ERRCODE = '22023';
            END IF;
            IF length(v_source_key) > 512
               OR public.dts_dependency_keys_valid_v2(
                    snapshot_record.source_region,p_dependency_keys
               ) IS DISTINCT FROM true
               OR jsonb_typeof(p_protected_source_row) <> 'object'
               OR (snapshot_record.source_region = 'dom' AND
                   public.dom_student_json_is_safe_v1(
                       p_protected_source_row
                   ) IS DISTINCT FROM true) THEN
                RAISE EXCEPTION 'SOURCE_SCOPE_STAGING_PAYLOAD_INVALID'
                    USING ERRCODE = '22023';
            END IF;
            row_hash := public.dts_canonical_json_sha256_v1(
                p_protected_source_row
            );
            SELECT * INTO live_record
            FROM public.dts_source_rows
            WHERE source_region = snapshot_record.source_region
              AND source_table = snapshot_record.source_table
              AND public.dts_source_rows.source_key = v_source_key;

            PERFORM set_config('tit.scope_coordinator_internal','on',true);
            INSERT INTO public.dts_source_snapshot_rows (
                snapshot_id,source_region,source_table,scope_kind,scope_level,
                scope_key,source_key,source_key_data,source_key_type,
                source_key_numeric,source_key_text,dependency_keys,
                protected_source_row,snapshot_row_hash,
                base_source_row_revision,base_row_hash,snapshot_as_of
            ) VALUES (
                p_snapshot_id,snapshot_record.source_region,
                snapshot_record.source_table,snapshot_record.scope_kind,
                snapshot_record.scope_level,snapshot_record.scope_key,
                v_source_key,p_source_key_data,key_type,key_numeric,key_text,
                p_dependency_keys,p_protected_source_row,row_hash,
                live_record.source_row_revision,live_record.source_payload_hash,
                snapshot_record.snapshot_as_of
            ) ON CONFLICT DO NOTHING;
            IF FOUND THEN
                RETURN jsonb_build_object(
                    'status','STAGED','source_key',v_source_key,
                    'snapshot_row_hash',row_hash
                );
            END IF;
            SELECT * INTO existing_record
            FROM public.dts_source_snapshot_rows
            WHERE snapshot_id = p_snapshot_id
              AND source_region = snapshot_record.source_region
              AND source_table = snapshot_record.source_table
              AND scope_kind = snapshot_record.scope_kind
              AND scope_level = snapshot_record.scope_level
              AND scope_key = snapshot_record.scope_key
              AND public.dts_source_snapshot_rows.source_key = v_source_key;
            IF existing_record.source_key_data IS DISTINCT FROM p_source_key_data
               OR existing_record.dependency_keys IS DISTINCT FROM
                    p_dependency_keys
               OR existing_record.protected_source_row IS DISTINCT FROM
                    p_protected_source_row
               OR existing_record.snapshot_row_hash IS DISTINCT FROM row_hash
               OR existing_record.base_source_row_revision IS DISTINCT FROM
                    live_record.source_row_revision
               OR existing_record.base_row_hash IS DISTINCT FROM
                    live_record.source_payload_hash THEN
                RAISE EXCEPTION 'SOURCE_SCOPE_STAGING_CONFLICT'
                    USING ERRCODE = '23514';
            END IF;
            RETURN jsonb_build_object(
                'status','NOOP','source_key',v_source_key,
                'snapshot_row_hash',row_hash
            );
        EXCEPTION
            WHEN invalid_text_representation OR numeric_value_out_of_range THEN
                RAISE EXCEPTION 'SOURCE_SCOPE_SOURCE_KEY_INVALID'
                    USING ERRCODE = '22023';
        END
        $function$;

        CREATE FUNCTION public.heartbeat_source_snapshot_candidate_v2(
            p_command_id text,
            p_snapshot_id text,
            p_owner text,
            p_lease_token text,
            p_lease_seconds integer,
            p_expected_head_version bigint
        )
        RETURNS jsonb
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE
            request_hash text;
            replay jsonb;
            head_record public.dts_source_table_publish_generations%ROWTYPE;
            snapshot_record public.dts_source_scope_snapshots%ROWTYPE;
            scope_record public.dts_source_scope_states%ROWTYPE;
            response jsonb;
        BEGIN
            request_hash := public.dts_canonical_json_sha256_v1(
                jsonb_build_object(
                    'protocol','scope-heartbeat-command-v1',
                    'snapshot_id',p_snapshot_id,'owner',p_owner,
                    'lease_token_hash',encode(sha256(convert_to(
                        p_lease_token,'UTF8'
                    )),'hex'),
                    'lease_seconds',p_lease_seconds,
                    'expected_head_version',p_expected_head_version
                )
            );
            replay := public._dts_scope_command_replay_v2(
                p_command_id,'HEARTBEAT',request_hash
            );
            IF replay IS NOT NULL THEN RETURN replay; END IF;
            IF p_lease_seconds NOT BETWEEN 15 AND 300 THEN
                RAISE EXCEPTION 'SOURCE_SCOPE_HEARTBEAT_INVALID'
                    USING ERRCODE = '22023';
            END IF;
            SELECT * INTO snapshot_record
            FROM public.dts_source_scope_snapshots
            WHERE snapshot_id = p_snapshot_id;
            IF NOT FOUND OR snapshot_record.epoch_state NOT IN (
                'LOADING','VERIFYING'
            ) THEN
                RAISE EXCEPTION 'SOURCE_SCOPE_CANDIDATE_NOT_ACTIVE'
                    USING ERRCODE = '55000';
            END IF;
            SELECT * INTO head_record
            FROM public.dts_source_table_publish_generations
            WHERE source_region = snapshot_record.source_region
              AND source_table = snapshot_record.source_table
            FOR UPDATE;
            IF head_record.row_version <> p_expected_head_version THEN
                RAISE EXCEPTION 'SOURCE_TABLE_GENERATION_HEAD_VERSION_CONFLICT'
                    USING ERRCODE = '40001';
            END IF;
            IF head_record.active_candidate_snapshot_id IS DISTINCT FROM
                    p_snapshot_id
               OR head_record.candidate_owner IS DISTINCT FROM p_owner
               OR head_record.candidate_lease_token IS DISTINCT FROM
                    p_lease_token
               OR head_record.candidate_lease_expires_at <= clock_timestamp()
            THEN
                RAISE EXCEPTION 'SOURCE_SCOPE_CANDIDATE_AUTHORITY_DENIED'
                    USING ERRCODE = '42501';
            END IF;
            SELECT * INTO scope_record
            FROM public.dts_source_scope_states
            WHERE source_region = snapshot_record.source_region
              AND source_table = snapshot_record.source_table
              AND scope_kind = snapshot_record.scope_kind
              AND scope_level = snapshot_record.scope_level
              AND scope_key = snapshot_record.scope_key;

            PERFORM set_config('tit.scope_coordinator_internal','on',true);
            UPDATE public.dts_source_table_publish_generations
            SET candidate_lease_expires_at =
                    clock_timestamp() + make_interval(secs=>p_lease_seconds),
                row_version = row_version + 1,
                updated_at = clock_timestamp()
            WHERE source_region = snapshot_record.source_region
              AND source_table = snapshot_record.source_table
            RETURNING row_version,candidate_lease_expires_at
            INTO head_record.row_version,head_record.candidate_lease_expires_at;
            response := jsonb_build_object(
                'status','HEARTBEAT',
                'snapshot_id',p_snapshot_id,
                'lease_expires_at',head_record.candidate_lease_expires_at,
                'head_row_version',head_record.row_version,
                'scope_row_version',scope_record.row_version
            );
            PERFORM public._record_source_scope_command_v2(
                p_command_id,'HEARTBEAT',request_hash,p_snapshot_id,
                snapshot_record.source_region,snapshot_record.source_table,
                snapshot_record.scope_kind,snapshot_record.scope_level,
                snapshot_record.scope_key,response,p_owner,scope_record.state,
                scope_record.state,scope_record.row_version,
                jsonb_build_object('lease_extended',true)
            );
            RETURN response;
        END
        $function$;
        """
    )


def _install_scope_verify_abort_takeover() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public._fail_source_scope_candidate_v2(
            p_command_id text,
            p_command_type text,
            p_request_hash text,
            p_snapshot_id text,
            p_actor text,
            p_error_code text
        )
        RETURNS jsonb
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE
            snapshot_record public.dts_source_scope_snapshots%ROWTYPE;
            scope_record public.dts_source_scope_states%ROWTYPE;
            head_record public.dts_source_table_publish_generations%ROWTYPE;
            active_fence_hash text;
            response jsonb;
            old_state text;
        BEGIN
            IF p_command_type NOT IN ('VERIFY','ABORT','TAKEOVER')
               OR p_error_code IS NULL OR btrim(p_error_code) = ''
               OR length(p_error_code) > 128
               OR p_error_code !~ '^[A-Z0-9_]+$' THEN
                RAISE EXCEPTION 'SOURCE_SCOPE_FAILURE_CODE_INVALID'
                    USING ERRCODE = '22023';
            END IF;
            SELECT * INTO snapshot_record
            FROM public.dts_source_scope_snapshots
            WHERE snapshot_id = p_snapshot_id
            FOR UPDATE;
            SELECT * INTO scope_record
            FROM public.dts_source_scope_states
            WHERE source_region = snapshot_record.source_region
              AND source_table = snapshot_record.source_table
              AND scope_kind = snapshot_record.scope_kind
              AND scope_level = snapshot_record.scope_level
              AND scope_key = snapshot_record.scope_key
            FOR UPDATE;
            SELECT * INTO head_record
            FROM public.dts_source_table_publish_generations
            WHERE source_region = snapshot_record.source_region
              AND source_table = snapshot_record.source_table
            FOR UPDATE;
            IF snapshot_record.snapshot_id IS NULL
               OR snapshot_record.epoch_state NOT IN ('LOADING','VERIFYING')
               OR scope_record.candidate_snapshot_id IS DISTINCT FROM
                    p_snapshot_id
               OR head_record.active_candidate_snapshot_id IS DISTINCT FROM
                    p_snapshot_id THEN
                RAISE EXCEPTION 'SOURCE_SCOPE_CANDIDATE_NOT_ACTIVE'
                    USING ERRCODE = '55000';
            END IF;
            old_state := scope_record.state;
            IF scope_record.active_snapshot_id IS NOT NULL THEN
                SELECT snapshot_fence_hash INTO active_fence_hash
                FROM public.dts_source_scope_snapshots
                WHERE snapshot_id = scope_record.active_snapshot_id;
            END IF;
            PERFORM set_config('tit.scope_coordinator_internal','on',true);
            UPDATE public.dts_source_scope_snapshots
            SET epoch_state = 'FAILED',
                verify_request_hash = CASE WHEN p_command_type = 'VERIFY'
                    THEN p_request_hash ELSE verify_request_hash END,
                terminal_request_hash = p_request_hash,
                error_code = p_error_code,
                failed_at = clock_timestamp(),
                row_version = row_version + 1
            WHERE snapshot_id = p_snapshot_id;
            UPDATE public.dts_source_scope_states
            SET state = 'FAILED',
                row_version = row_version + 1,
                updated_at = clock_timestamp()
            WHERE source_region = snapshot_record.source_region
              AND source_table = snapshot_record.source_table
              AND scope_kind = snapshot_record.scope_kind
              AND scope_level = snapshot_record.scope_level
              AND scope_key = snapshot_record.scope_key
            RETURNING * INTO scope_record;
            UPDATE public.dts_source_table_publish_generations
            SET active_candidate_snapshot_id = NULL,
                candidate_owner = NULL,
                candidate_lease_token = NULL,
                candidate_lease_expires_at = NULL,
                row_version = row_version + 1,
                updated_at = clock_timestamp()
            WHERE source_region = snapshot_record.source_region
              AND source_table = snapshot_record.source_table
            RETURNING * INTO head_record;
            PERFORM public._emit_source_scope_revision_v2(
                snapshot_record.source_region,snapshot_record.source_table,
                snapshot_record.scope_kind,snapshot_record.scope_level,
                snapshot_record.scope_key,scope_record.row_version,'FAILED',
                scope_record.active_snapshot_id,active_fence_hash
            );
            response := jsonb_build_object(
                'status','FAILED','error_code',p_error_code,
                'snapshot_id',p_snapshot_id,
                'head_row_version',head_record.row_version,
                'scope_row_version',scope_record.row_version
            );
            PERFORM public._record_source_scope_command_v2(
                p_command_id,p_command_type,p_request_hash,p_snapshot_id,
                snapshot_record.source_region,snapshot_record.source_table,
                snapshot_record.scope_kind,snapshot_record.scope_level,
                snapshot_record.scope_key,response,p_actor,old_state,'FAILED',
                scope_record.row_version,
                jsonb_build_object('error_code',p_error_code)
            );
            RETURN response;
        END
        $function$;

        CREATE FUNCTION public.verify_source_snapshot_candidate_v2(
            p_command_id text,
            p_snapshot_id text,
            p_owner text,
            p_lease_token text,
            p_expected_head_version bigint,
            p_expected_scope_version bigint,
            p_expected_row_count bigint,
            p_expected_content_hash text,
            p_expected_fence_hash text,
            p_actor text
        )
        RETURNS jsonb
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE
            request_hash text;
            replay jsonb;
            snapshot_record public.dts_source_scope_snapshots%ROWTYPE;
            scope_record public.dts_source_scope_states%ROWTYPE;
            head_record public.dts_source_table_publish_generations%ROWTYPE;
            manifest jsonb;
            actual_count bigint;
            actual_hash text;
            validation_error text;
            active_fence_hash text;
            response jsonb;
        BEGIN
            request_hash := public.dts_canonical_json_sha256_v1(
                jsonb_build_object(
                    'protocol','scope-verify-command-v1',
                    'snapshot_id',p_snapshot_id,'owner',p_owner,
                    'lease_token_hash',encode(sha256(convert_to(
                        p_lease_token,'UTF8'
                    )),'hex'),
                    'expected_head_version',p_expected_head_version,
                    'expected_scope_version',p_expected_scope_version,
                    'expected_row_count',p_expected_row_count,
                    'expected_content_hash',p_expected_content_hash,
                    'expected_fence_hash',p_expected_fence_hash,
                    'actor',p_actor
                )
            );
            replay := public._dts_scope_command_replay_v2(
                p_command_id,'VERIFY',request_hash
            );
            IF replay IS NOT NULL THEN RETURN replay; END IF;
            IF p_expected_row_count < 0
               OR p_expected_content_hash !~ '^[0-9a-f]{64}$'
               OR p_expected_fence_hash !~ '^[0-9a-f]{64}$'
               OR p_actor IS NULL OR btrim(p_actor) = '' THEN
                RAISE EXCEPTION 'SOURCE_SCOPE_VERIFY_INVALID'
                    USING ERRCODE = '22023';
            END IF;
            SELECT * INTO snapshot_record
            FROM public.dts_source_scope_snapshots
            WHERE snapshot_id = p_snapshot_id
            FOR UPDATE;
            IF NOT FOUND OR snapshot_record.epoch_state <> 'LOADING' THEN
                RAISE EXCEPTION 'SOURCE_SCOPE_VERIFY_NOT_LOADING'
                    USING ERRCODE = '55000';
            END IF;
            SELECT * INTO head_record
            FROM public.dts_source_table_publish_generations
            WHERE source_region = snapshot_record.source_region
              AND source_table = snapshot_record.source_table
            FOR UPDATE;
            SELECT * INTO scope_record
            FROM public.dts_source_scope_states
            WHERE source_region = snapshot_record.source_region
              AND source_table = snapshot_record.source_table
              AND scope_kind = snapshot_record.scope_kind
              AND scope_level = snapshot_record.scope_level
              AND scope_key = snapshot_record.scope_key
            FOR UPDATE;
            IF head_record.row_version <> p_expected_head_version
               OR scope_record.row_version <> p_expected_scope_version THEN
                RAISE EXCEPTION 'SOURCE_SCOPE_VERSION_CONFLICT'
                    USING ERRCODE = '40001';
            END IF;
            IF head_record.active_candidate_snapshot_id IS DISTINCT FROM
                    p_snapshot_id
               OR head_record.candidate_owner IS DISTINCT FROM p_owner
               OR head_record.candidate_lease_token IS DISTINCT FROM
                    p_lease_token
               OR head_record.candidate_lease_expires_at <= clock_timestamp()
               OR scope_record.state <> 'LOADING'
               OR scope_record.candidate_snapshot_id IS DISTINCT FROM
                    p_snapshot_id THEN
                RAISE EXCEPTION 'SOURCE_SCOPE_CANDIDATE_AUTHORITY_DENIED'
                    USING ERRCODE = '42501';
            END IF;
            manifest := public.dts_scope_snapshot_row_manifest_v2(
                p_snapshot_id
            );
            SELECT count(*) INTO actual_count
            FROM public.dts_source_snapshot_rows
            WHERE snapshot_id = p_snapshot_id;
            actual_hash := public.dts_canonical_json_sha256_v1(manifest);
            IF actual_count <> p_expected_row_count THEN
                validation_error := 'SOURCE_SCOPE_ROW_COUNT_MISMATCH';
            ELSIF actual_hash <> p_expected_content_hash THEN
                validation_error := 'SOURCE_SCOPE_CONTENT_HASH_MISMATCH';
            ELSIF snapshot_record.snapshot_fence_hash <>
                    p_expected_fence_hash THEN
                validation_error := 'SOURCE_SCOPE_FENCE_HASH_MISMATCH';
            ELSE
                validation_error := public.dts_scope_candidate_error_v2(
                    p_snapshot_id,false
                );
            END IF;
            IF validation_error IS NOT NULL THEN
                RETURN public._fail_source_scope_candidate_v2(
                    p_command_id,'VERIFY',request_hash,p_snapshot_id,p_actor,
                    validation_error
                );
            END IF;
            IF scope_record.active_snapshot_id IS NOT NULL THEN
                SELECT snapshot_fence_hash INTO active_fence_hash
                FROM public.dts_source_scope_snapshots
                WHERE snapshot_id = scope_record.active_snapshot_id;
            END IF;
            PERFORM set_config('tit.scope_coordinator_internal','on',true);
            UPDATE public.dts_source_scope_snapshots
            SET epoch_state = 'VERIFYING',
                row_count = actual_count,
                content_hash = actual_hash,
                verify_request_hash = request_hash,
                verified_at = clock_timestamp(),
                row_version = row_version + 1
            WHERE snapshot_id = p_snapshot_id;
            UPDATE public.dts_source_scope_states
            SET state = 'VERIFYING',row_version = row_version + 1,
                updated_at = clock_timestamp()
            WHERE source_region = snapshot_record.source_region
              AND source_table = snapshot_record.source_table
              AND scope_kind = snapshot_record.scope_kind
              AND scope_level = snapshot_record.scope_level
              AND scope_key = snapshot_record.scope_key
            RETURNING * INTO scope_record;
            UPDATE public.dts_source_table_publish_generations
            SET row_version = row_version + 1,updated_at = clock_timestamp()
            WHERE source_region = snapshot_record.source_region
              AND source_table = snapshot_record.source_table
            RETURNING * INTO head_record;
            PERFORM public._emit_source_scope_revision_v2(
                snapshot_record.source_region,snapshot_record.source_table,
                snapshot_record.scope_kind,snapshot_record.scope_level,
                snapshot_record.scope_key,scope_record.row_version,'VERIFYING',
                scope_record.active_snapshot_id,active_fence_hash
            );
            response := jsonb_build_object(
                'status','VERIFIED','snapshot_id',p_snapshot_id,
                'row_count',actual_count,'content_hash',actual_hash,
                'fence_hash',snapshot_record.snapshot_fence_hash,
                'head_row_version',head_record.row_version,
                'scope_row_version',scope_record.row_version
            );
            PERFORM public._record_source_scope_command_v2(
                p_command_id,'VERIFY',request_hash,p_snapshot_id,
                snapshot_record.source_region,snapshot_record.source_table,
                snapshot_record.scope_kind,snapshot_record.scope_level,
                snapshot_record.scope_key,response,p_actor,'LOADING',
                'VERIFYING',scope_record.row_version,
                jsonb_build_object(
                    'row_count',actual_count,'content_hash',actual_hash,
                    'fence_hash',snapshot_record.snapshot_fence_hash
                )
            );
            RETURN response;
        END
        $function$;

        CREATE FUNCTION public.abort_source_snapshot_candidate_v2(
            p_command_id text,
            p_snapshot_id text,
            p_owner text,
            p_lease_token text,
            p_expected_head_version bigint,
            p_expected_scope_version bigint,
            p_error_code text
        )
        RETURNS jsonb
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE request_hash text; replay jsonb;
        DECLARE snapshot_record public.dts_source_scope_snapshots%ROWTYPE;
        DECLARE head_record public.dts_source_table_publish_generations%ROWTYPE;
        DECLARE scope_record public.dts_source_scope_states%ROWTYPE;
        BEGIN
            request_hash := public.dts_canonical_json_sha256_v1(
                jsonb_build_object(
                    'protocol','scope-abort-command-v1',
                    'snapshot_id',p_snapshot_id,'owner',p_owner,
                    'lease_token_hash',encode(sha256(convert_to(
                        p_lease_token,'UTF8'
                    )),'hex'),
                    'expected_head_version',p_expected_head_version,
                    'expected_scope_version',p_expected_scope_version,
                    'error_code',p_error_code
                )
            );
            replay := public._dts_scope_command_replay_v2(
                p_command_id,'ABORT',request_hash
            );
            IF replay IS NOT NULL THEN RETURN replay; END IF;
            SELECT * INTO snapshot_record
            FROM public.dts_source_scope_snapshots
            WHERE snapshot_id = p_snapshot_id FOR UPDATE;
            IF NOT FOUND OR snapshot_record.epoch_state NOT IN (
                'LOADING','VERIFYING'
            ) THEN
                RAISE EXCEPTION 'SOURCE_SCOPE_CANDIDATE_NOT_ACTIVE'
                    USING ERRCODE = '55000';
            END IF;
            SELECT * INTO head_record
            FROM public.dts_source_table_publish_generations
            WHERE source_region = snapshot_record.source_region
              AND source_table = snapshot_record.source_table FOR UPDATE;
            SELECT * INTO scope_record
            FROM public.dts_source_scope_states
            WHERE source_region = snapshot_record.source_region
              AND source_table = snapshot_record.source_table
              AND scope_kind = snapshot_record.scope_kind
              AND scope_level = snapshot_record.scope_level
              AND scope_key = snapshot_record.scope_key FOR UPDATE;
            IF head_record.row_version <> p_expected_head_version
               OR scope_record.row_version <> p_expected_scope_version THEN
                RAISE EXCEPTION 'SOURCE_SCOPE_VERSION_CONFLICT'
                    USING ERRCODE = '40001';
            END IF;
            IF head_record.active_candidate_snapshot_id IS DISTINCT FROM
                    p_snapshot_id
               OR head_record.candidate_owner IS DISTINCT FROM p_owner
               OR head_record.candidate_lease_token IS DISTINCT FROM
                    p_lease_token
               OR head_record.candidate_lease_expires_at <= clock_timestamp()
               OR scope_record.candidate_snapshot_id IS DISTINCT FROM
                    p_snapshot_id THEN
                RAISE EXCEPTION 'SOURCE_SCOPE_CANDIDATE_AUTHORITY_DENIED'
                    USING ERRCODE = '42501';
            END IF;
            RETURN public._fail_source_scope_candidate_v2(
                p_command_id,'ABORT',request_hash,p_snapshot_id,p_owner,
                p_error_code
            );
        END
        $function$;

        CREATE FUNCTION public.takeover_expired_source_snapshot_candidate_v2(
            p_command_id text,
            p_source_region text,
            p_source_table text,
            p_expected_head_version bigint,
            p_actor text
        )
        RETURNS jsonb
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE request_hash text; replay jsonb;
        DECLARE head_record public.dts_source_table_publish_generations%ROWTYPE;
        BEGIN
            request_hash := public.dts_canonical_json_sha256_v1(
                jsonb_build_object(
                    'protocol','scope-takeover-command-v1',
                    'source_region',p_source_region,
                    'source_table',p_source_table,
                    'expected_head_version',p_expected_head_version,
                    'actor',p_actor
                )
            );
            replay := public._dts_scope_command_replay_v2(
                p_command_id,'TAKEOVER',request_hash
            );
            IF replay IS NOT NULL THEN RETURN replay; END IF;
            SELECT * INTO head_record
            FROM public.dts_source_table_publish_generations
            WHERE source_region = p_source_region
              AND source_table = p_source_table FOR UPDATE;
            IF NOT FOUND OR head_record.row_version <>
                    p_expected_head_version THEN
                RAISE EXCEPTION 'SOURCE_TABLE_GENERATION_HEAD_VERSION_CONFLICT'
                    USING ERRCODE = '40001';
            END IF;
            IF head_record.active_candidate_snapshot_id IS NULL
               OR head_record.candidate_lease_expires_at > clock_timestamp()
               OR p_actor IS NULL OR btrim(p_actor) = '' THEN
                RAISE EXCEPTION 'SOURCE_SCOPE_TAKEOVER_NOT_ALLOWED'
                    USING ERRCODE = '55000';
            END IF;
            RETURN public._fail_source_scope_candidate_v2(
                p_command_id,'TAKEOVER',request_hash,
                head_record.active_candidate_snapshot_id,p_actor,
                'SNAPSHOT_CANDIDATE_LEASE_EXPIRED'
            );
        END
        $function$;
        """
    )


def _install_scope_publish_invalidate() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.publish_source_snapshot_candidate_v2(
            p_command_id text,
            p_snapshot_id text,
            p_owner text,
            p_lease_token text,
            p_expected_head_version bigint,
            p_expected_scope_version bigint,
            p_expected_content_hash text,
            p_actor text
        )
        RETURNS jsonb
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE
            request_hash text;
            replay jsonb;
            snapshot_record public.dts_source_scope_snapshots%ROWTYPE;
            scope_record public.dts_source_scope_states%ROWTYPE;
            head_record public.dts_source_table_publish_generations%ROWTYPE;
            validation_error text;
            actual_count bigint;
            actual_hash text;
            next_generation bigint;
            old_state text;
            response jsonb;
        BEGIN
            request_hash := public.dts_canonical_json_sha256_v1(
                jsonb_build_object(
                    'protocol','scope-publish-command-v1',
                    'snapshot_id',p_snapshot_id,'owner',p_owner,
                    'lease_token_hash',encode(sha256(convert_to(
                        p_lease_token,'UTF8'
                    )),'hex'),
                    'expected_head_version',p_expected_head_version,
                    'expected_scope_version',p_expected_scope_version,
                    'expected_content_hash',p_expected_content_hash,
                    'actor',p_actor
                )
            );
            replay := public._dts_scope_command_replay_v2(
                p_command_id,'PUBLISH',request_hash
            );
            IF replay IS NOT NULL THEN RETURN replay; END IF;
            IF p_expected_content_hash !~ '^[0-9a-f]{64}$'
               OR p_actor IS NULL OR btrim(p_actor) = '' THEN
                RAISE EXCEPTION 'SOURCE_SCOPE_PUBLISH_INVALID'
                    USING ERRCODE = '22023';
            END IF;
            SELECT * INTO snapshot_record
            FROM public.dts_source_scope_snapshots
            WHERE snapshot_id = p_snapshot_id FOR UPDATE;
            IF NOT FOUND OR snapshot_record.epoch_state <> 'VERIFYING' THEN
                RAISE EXCEPTION 'SOURCE_SCOPE_PUBLISH_NOT_VERIFIED'
                    USING ERRCODE = '55000';
            END IF;
            SELECT * INTO head_record
            FROM public.dts_source_table_publish_generations
            WHERE source_region = snapshot_record.source_region
              AND source_table = snapshot_record.source_table FOR UPDATE;
            SELECT * INTO scope_record
            FROM public.dts_source_scope_states
            WHERE source_region = snapshot_record.source_region
              AND source_table = snapshot_record.source_table
              AND scope_kind = snapshot_record.scope_kind
              AND scope_level = snapshot_record.scope_level
              AND scope_key = snapshot_record.scope_key FOR UPDATE;
            IF head_record.row_version <> p_expected_head_version
               OR scope_record.row_version <> p_expected_scope_version THEN
                RAISE EXCEPTION 'SOURCE_SCOPE_VERSION_CONFLICT'
                    USING ERRCODE = '40001';
            END IF;
            IF head_record.active_candidate_snapshot_id IS DISTINCT FROM
                    p_snapshot_id
               OR head_record.candidate_owner IS DISTINCT FROM p_owner
               OR head_record.candidate_lease_token IS DISTINCT FROM
                    p_lease_token
               OR head_record.candidate_lease_expires_at <= clock_timestamp()
               OR scope_record.state <> 'VERIFYING'
               OR scope_record.candidate_snapshot_id IS DISTINCT FROM
                    p_snapshot_id THEN
                RAISE EXCEPTION 'SOURCE_SCOPE_CANDIDATE_AUTHORITY_DENIED'
                    USING ERRCODE = '42501';
            END IF;
            IF snapshot_record.base_publish_generation IS DISTINCT FROM
                    head_record.current_generation THEN
                RAISE EXCEPTION 'SOURCE_SCOPE_PUBLISH_GENERATION_STALE'
                    USING ERRCODE = '40001';
            END IF;
            SELECT count(*) INTO actual_count
            FROM public.dts_source_snapshot_rows
            WHERE snapshot_id = p_snapshot_id;
            actual_hash := public.dts_canonical_json_sha256_v1(
                public.dts_scope_snapshot_row_manifest_v2(p_snapshot_id)
            );
            IF actual_count IS DISTINCT FROM snapshot_record.row_count
               OR actual_hash IS DISTINCT FROM snapshot_record.content_hash
               OR actual_hash IS DISTINCT FROM p_expected_content_hash THEN
                RAISE EXCEPTION 'SOURCE_SCOPE_VERIFIED_EVIDENCE_CHANGED'
                    USING ERRCODE = '40001';
            END IF;
            validation_error := public.dts_scope_candidate_error_v2(
                p_snapshot_id,true
            );
            IF validation_error IS NOT NULL THEN
                RAISE EXCEPTION '%',validation_error USING ERRCODE = '55000';
            END IF;
            old_state := scope_record.state;
            next_generation := head_record.current_generation + 1;
            PERFORM set_config('tit.scope_coordinator_internal','on',true);

            UPDATE public.dts_source_scope_memberships membership
            SET active_snapshot_id = p_snapshot_id,
                snapshot_is_present = staged.source_key IS NOT NULL,
                snapshot_row_hash = staged.snapshot_row_hash,
                source_key_data = coalesce(
                    staged.source_key_data,membership.source_key_data
                ),
                source_key_type = coalesce(
                    staged.source_key_type,membership.source_key_type
                ),
                source_key_numeric = CASE
                    WHEN staged.source_key IS NULL
                    THEN membership.source_key_numeric
                    ELSE staged.source_key_numeric END,
                source_key_text = CASE
                    WHEN staged.source_key IS NULL
                    THEN membership.source_key_text
                    ELSE staged.source_key_text END,
                dependency_keys = coalesce(
                    staged.dependency_keys,membership.dependency_keys
                ),
                membership_revision = membership.membership_revision + 1,
                updated_at = clock_timestamp()
            FROM (
                SELECT * FROM public.dts_source_snapshot_rows
                WHERE snapshot_id = p_snapshot_id
            ) staged
            RIGHT JOIN public.dts_source_scope_memberships existing
              ON existing.source_region = snapshot_record.source_region
             AND existing.source_table = snapshot_record.source_table
             AND existing.scope_kind = snapshot_record.scope_kind
             AND existing.scope_level = snapshot_record.scope_level
             AND existing.scope_key = snapshot_record.scope_key
             AND existing.source_key = staged.source_key
            WHERE membership.source_region = existing.source_region
              AND membership.source_table = existing.source_table
              AND membership.scope_kind = existing.scope_kind
              AND membership.scope_level = existing.scope_level
              AND membership.scope_key = existing.scope_key
              AND membership.source_key = existing.source_key;

            INSERT INTO public.dts_source_scope_memberships (
                source_region,source_table,scope_kind,scope_level,scope_key,
                source_key,source_key_data,source_key_type,source_key_numeric,
                source_key_text,active_snapshot_id,snapshot_is_present,
                snapshot_row_hash,dependency_keys,membership_revision
            )
            SELECT staged.source_region,staged.source_table,staged.scope_kind,
                   staged.scope_level,staged.scope_key,staged.source_key,
                   staged.source_key_data,staged.source_key_type,
                   staged.source_key_numeric,staged.source_key_text,
                   p_snapshot_id,true,staged.snapshot_row_hash,
                   staged.dependency_keys,1
            FROM public.dts_source_snapshot_rows staged
            WHERE staged.snapshot_id = p_snapshot_id
              AND NOT EXISTS (
                  SELECT 1
                  FROM public.dts_source_scope_memberships membership
                  WHERE membership.source_region = staged.source_region
                    AND membership.source_table = staged.source_table
                    AND membership.scope_kind = staged.scope_kind
                    AND membership.scope_level = staged.scope_level
                    AND membership.scope_key = staged.scope_key
                    AND membership.source_key = staged.source_key
              );

            IF scope_record.active_snapshot_id IS NOT NULL THEN
                UPDATE public.dts_source_scope_snapshots
                SET epoch_state = 'SUPERSEDED',
                    invalidated_at = coalesce(
                        invalidated_at,clock_timestamp()
                    ),
                    row_version = row_version + 1
                WHERE snapshot_id = scope_record.active_snapshot_id;
            END IF;
            UPDATE public.dts_source_scope_snapshots
            SET epoch_state = 'COMPLETE',
                published_generation = next_generation,
                generation_diff_count = 0,
                generation_diff_hash =
                    public.dts_canonical_json_sha256_v1('[]'::jsonb),
                publish_request_hash = request_hash,
                completed_at = clock_timestamp(),
                row_version = row_version + 1
            WHERE snapshot_id = p_snapshot_id;
            UPDATE public.dts_source_scope_states
            SET state = 'COMPLETE',active_snapshot_id = p_snapshot_id,
                candidate_snapshot_id = NULL,completed_at = clock_timestamp(),
                invalidated_at = NULL,last_invalidation_hash = NULL,
                row_version = row_version + 1,updated_at = clock_timestamp()
            WHERE source_region = snapshot_record.source_region
              AND source_table = snapshot_record.source_table
              AND scope_kind = snapshot_record.scope_kind
              AND scope_level = snapshot_record.scope_level
              AND scope_key = snapshot_record.scope_key
            RETURNING * INTO scope_record;
            UPDATE public.dts_source_table_publish_generations
            SET current_generation = next_generation,
                current_snapshot_id = p_snapshot_id,
                active_candidate_snapshot_id = NULL,
                candidate_owner = NULL,candidate_lease_token = NULL,
                candidate_lease_expires_at = NULL,
                row_version = row_version + 1,updated_at = clock_timestamp()
            WHERE source_region = snapshot_record.source_region
              AND source_table = snapshot_record.source_table
            RETURNING * INTO head_record;
            PERFORM public._emit_source_scope_revision_v2(
                snapshot_record.source_region,snapshot_record.source_table,
                snapshot_record.scope_kind,snapshot_record.scope_level,
                snapshot_record.scope_key,scope_record.row_version,'COMPLETE',
                p_snapshot_id,snapshot_record.snapshot_fence_hash
            );
            response := jsonb_build_object(
                'status','PUBLISHED','snapshot_id',p_snapshot_id,
                'publish_generation',next_generation,
                'generation_diff_count',0,
                'generation_diff_hash',
                    public.dts_canonical_json_sha256_v1('[]'::jsonb),
                'head_row_version',head_record.row_version,
                'scope_row_version',scope_record.row_version
            );
            PERFORM public._record_source_scope_command_v2(
                p_command_id,'PUBLISH',request_hash,p_snapshot_id,
                snapshot_record.source_region,snapshot_record.source_table,
                snapshot_record.scope_kind,snapshot_record.scope_level,
                snapshot_record.scope_key,response,p_actor,old_state,'COMPLETE',
                scope_record.row_version,
                jsonb_build_object(
                    'publish_generation',next_generation,
                    'generation_diff_count',0,
                    'content_hash',actual_hash,
                    'fence_hash',snapshot_record.snapshot_fence_hash
                )
            );
            RETURN response;
        END
        $function$;

        CREATE FUNCTION public.invalidate_source_scope_v2(
            p_command_id text,
            p_source_region text,
            p_source_table text,
            p_scope_kind text,
            p_scope_level text,
            p_scope_key text,
            p_expected_head_version bigint,
            p_expected_scope_version bigint,
            p_reason_code text,
            p_actor text
        )
        RETURNS jsonb
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE request_hash text; replay jsonb;
        DECLARE head_record public.dts_source_table_publish_generations%ROWTYPE;
        DECLARE scope_record public.dts_source_scope_states%ROWTYPE;
        DECLARE snapshot_record public.dts_source_scope_snapshots%ROWTYPE;
        DECLARE response jsonb;
        BEGIN
            request_hash := public.dts_canonical_json_sha256_v1(
                jsonb_build_object(
                    'protocol','scope-invalidate-command-v1',
                    'source_region',p_source_region,
                    'source_table',p_source_table,
                    'scope_kind',p_scope_kind,
                    'scope_level',p_scope_level,
                    'scope_key',p_scope_key,
                    'expected_head_version',p_expected_head_version,
                    'expected_scope_version',p_expected_scope_version,
                    'reason_code',p_reason_code,'actor',p_actor
                )
            );
            replay := public._dts_scope_command_replay_v2(
                p_command_id,'INVALIDATE',request_hash
            );
            IF replay IS NOT NULL THEN RETURN replay; END IF;
            IF public.dts_source_scope_identity_valid_v2(
                    p_source_region,p_source_table,p_scope_kind,
                    p_scope_level,p_scope_key
               ) IS DISTINCT FROM true
               OR p_reason_code IS NULL OR p_reason_code !~ '^[A-Z0-9_]+$'
               OR length(p_reason_code) > 128
               OR p_actor IS NULL OR btrim(p_actor) = '' THEN
                RAISE EXCEPTION 'SOURCE_SCOPE_INVALIDATION_INVALID'
                    USING ERRCODE = '22023';
            END IF;
            SELECT * INTO head_record
            FROM public.dts_source_table_publish_generations
            WHERE source_region = p_source_region
              AND source_table = p_source_table FOR UPDATE;
            SELECT * INTO scope_record
            FROM public.dts_source_scope_states
            WHERE source_region = p_source_region
              AND source_table = p_source_table
              AND scope_kind = p_scope_kind
              AND scope_level = p_scope_level
              AND scope_key = p_scope_key FOR UPDATE;
            IF NOT FOUND OR head_record.row_version <>
                    p_expected_head_version
               OR scope_record.row_version <> p_expected_scope_version THEN
                RAISE EXCEPTION 'SOURCE_SCOPE_VERSION_CONFLICT'
                    USING ERRCODE = '40001';
            END IF;
            IF head_record.active_candidate_snapshot_id IS NOT NULL
               OR scope_record.state <> 'COMPLETE'
               OR scope_record.candidate_snapshot_id IS NOT NULL
               OR scope_record.active_snapshot_id IS NULL THEN
                RAISE EXCEPTION 'SOURCE_SCOPE_INVALIDATION_NOT_ALLOWED'
                    USING ERRCODE = '55000';
            END IF;
            SELECT * INTO snapshot_record
            FROM public.dts_source_scope_snapshots
            WHERE snapshot_id = scope_record.active_snapshot_id FOR UPDATE;
            IF snapshot_record.epoch_state <> 'COMPLETE' THEN
                RAISE EXCEPTION 'SOURCE_SCOPE_FALSE_COMPLETE_FORBIDDEN'
                    USING ERRCODE = '23514';
            END IF;
            PERFORM set_config('tit.scope_coordinator_internal','on',true);
            UPDATE public.dts_source_scope_snapshots
            SET epoch_state = 'STALE',invalidated_at = clock_timestamp(),
                terminal_request_hash = request_hash,
                row_version = row_version + 1
            WHERE snapshot_id = scope_record.active_snapshot_id;
            UPDATE public.dts_source_scope_states
            SET state = 'STALE',invalidated_at = clock_timestamp(),
                last_invalidation_hash = request_hash,
                row_version = row_version + 1,updated_at = clock_timestamp()
            WHERE source_region = p_source_region
              AND source_table = p_source_table
              AND scope_kind = p_scope_kind
              AND scope_level = p_scope_level
              AND scope_key = p_scope_key
            RETURNING * INTO scope_record;
            UPDATE public.dts_source_table_publish_generations
            SET row_version = row_version + 1,updated_at = clock_timestamp()
            WHERE source_region = p_source_region
              AND source_table = p_source_table
            RETURNING * INTO head_record;
            PERFORM public._emit_source_scope_revision_v2(
                p_source_region,p_source_table,p_scope_kind,p_scope_level,
                p_scope_key,scope_record.row_version,'STALE',
                scope_record.active_snapshot_id,
                snapshot_record.snapshot_fence_hash
            );
            response := jsonb_build_object(
                'status','INVALIDATED',
                'snapshot_id',scope_record.active_snapshot_id,
                'reason_code',p_reason_code,
                'head_row_version',head_record.row_version,
                'scope_row_version',scope_record.row_version
            );
            PERFORM public._record_source_scope_command_v2(
                p_command_id,'INVALIDATE',request_hash,
                scope_record.active_snapshot_id,p_source_region,
                p_source_table,p_scope_kind,p_scope_level,p_scope_key,
                response,p_actor,'COMPLETE','STALE',scope_record.row_version,
                jsonb_build_object('reason_code',p_reason_code)
            );
            RETURN response;
        END
        $function$;
        """
    )


def _install_scope_state_machine() -> None:
    _install_scope_state_machine_helpers()
    _install_scope_begin_stage_heartbeat()
    _install_scope_verify_abort_takeover()
    _install_scope_publish_invalidate()


def _apply_scope_acl() -> None:
    table_list = ",\n                ".join(
        f"public.{table_name}" for table_name in NEW_TABLES
    )
    public_functions = ",\n                ".join(PUBLIC_FUNCTION_SIGNATURES)
    readable_tables = ",\n                        ".join(
        f"public.{table_name}"
        for table_name in (
            "dts_source_table_publish_generations",
            "dts_source_scope_snapshots",
            "dts_source_snapshot_fences",
            "dts_source_scope_states",
            "dts_source_scope_memberships",
        )
    )
    op.execute(
        f"""
        REVOKE ALL PRIVILEGES ON TABLE
                {table_list}
        FROM PUBLIC, tit_growth_app, tit_dts_ingest_runtime;
        REVOKE ALL PRIVILEGES ON SEQUENCE
            public.dts_source_scope_transition_audits_audit_id_seq
        FROM PUBLIC, tit_growth_app, tit_dts_ingest_runtime;
        REVOKE ALL ON FUNCTION
                {public_functions}
        FROM PUBLIC, tit_growth_app, tit_dts_ingest_runtime;

        DO $scope_coordinator_acl$
        DECLARE role_name text;
        BEGIN
            IF to_regrole('{SCOPE_COORDINATOR_ROLE}') IS NOT NULL THEN
                IF EXISTS (
                    SELECT 1 FROM pg_roles
                    WHERE rolname = '{SCOPE_COORDINATOR_ROLE}'
                      AND (NOT rolcanlogin OR rolinherit OR rolsuper
                           OR rolcreatedb OR rolcreaterole
                           OR rolreplication OR rolbypassrls)
                ) THEN
                    RAISE EXCEPTION
                        '{SCOPE_COORDINATOR_ROLE} must be a restricted LOGIN NOINHERIT role';
                END IF;
                EXECUTE 'REVOKE ALL PRIVILEGES ON TABLE '
                    '{table_list.replace(chr(10), " ")} '
                    'FROM {SCOPE_COORDINATOR_ROLE}';
                EXECUTE 'GRANT SELECT ON TABLE '
                    '{table_list.replace(chr(10), " ")} '
                    'TO {SCOPE_COORDINATOR_ROLE}';
                EXECUTE 'REVOKE ALL PRIVILEGES ON SEQUENCE '
                    'public.dts_source_scope_transition_audits_audit_id_seq '
                    'FROM {SCOPE_COORDINATOR_ROLE}';
                EXECUTE 'GRANT USAGE ON SCHEMA public '
                    'TO {SCOPE_COORDINATOR_ROLE}';
                EXECUTE 'GRANT EXECUTE ON FUNCTION '
                    '{public_functions.replace(chr(10), " ")} '
                    'TO {SCOPE_COORDINATOR_ROLE}';
            END IF;

            FOREACH role_name IN ARRAY ARRAY[
                'tit_teacher_crud','tit_dts_domain_projector_runtime',
                'tit_source_monitor','tit_source_worker','tide_business_app'
            ] LOOP
                IF to_regrole(role_name) IS NOT NULL THEN
                    EXECUTE format(
                        'REVOKE ALL PRIVILEGES ON TABLE {table_list.replace(chr(10), " ")} FROM %I',
                        role_name
                    );
                    EXECUTE format(
                        'REVOKE ALL ON FUNCTION {public_functions.replace(chr(10), " ")} FROM %I',
                        role_name
                    );
                END IF;
            END LOOP;

            FOREACH role_name IN ARRAY ARRAY[
                'tit_growth_app','tit_dts_domain_projector_runtime',
                'tit_source_monitor','tit_source_worker','tide_business_app'
            ] LOOP
                IF to_regrole(role_name) IS NOT NULL THEN
                    EXECUTE format(
                        'GRANT SELECT ON TABLE {readable_tables.replace(chr(10), " ")} TO %I',
                        role_name
                    );
                END IF;
            END LOOP;
        END
        $scope_coordinator_acl$;

        COMMENT ON FUNCTION public.begin_source_snapshot_candidate_v2(
            text,text,text,text,text,text,text,timestamptz,text,jsonb,
            timestamptz,timestamptz,text,integer,bigint,bigint
        ) IS 'Begin one table-serialized source-scope candidate with an epoch-aware relational fence.';
        COMMENT ON FUNCTION public.stage_source_snapshot_row_v2(
            text,text,text,jsonb,jsonb,jsonb,text
        ) IS 'Append one protected typed row to an active LOADING source-scope candidate.';
        COMMENT ON FUNCTION public.verify_source_snapshot_candidate_v2(
            text,text,text,text,bigint,bigint,bigint,text,text,text
        ) IS 'Verify count, content, broker fence, checkpoint and exact V2 current-state equality; failures are terminal and never COMPLETE.';
        COMMENT ON FUNCTION public.publish_source_snapshot_candidate_v2(
            text,text,text,text,bigint,bigint,text,text
        ) IS 'Publish only a reverified no-diff/no-overlay candidate and atomically emit SOURCE_SCOPE revision, dirty inputs and outbox.';
        COMMENT ON TABLE public.dts_source_snapshot_rows IS
            'Candidate-only protected staging rows with typed keys and frozen live-current base evidence.';
        """
    )


def _guard_unused_downgrade() -> None:
    op.execute(
        r"""
        DO $guard_scope_downgrade$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM public.dts_source_table_publish_generations
                WHERE current_generation <> 0
                   OR current_snapshot_id IS NOT NULL
                   OR active_candidate_snapshot_id IS NOT NULL
            )
            OR EXISTS (SELECT 1 FROM public.dts_source_scope_snapshots)
            OR EXISTS (SELECT 1 FROM public.dts_source_snapshot_rows)
            OR EXISTS (SELECT 1 FROM public.dts_source_scope_states)
            OR EXISTS (SELECT 1 FROM public.dts_source_scope_memberships)
            OR EXISTS (SELECT 1 FROM public.dts_source_scope_commands)
            OR EXISTS (SELECT 1 FROM public.dts_source_scope_transition_audits)
            OR EXISTS (
                SELECT 1 FROM public.domain_aggregate_revisions
                WHERE aggregate_type = 'SOURCE_SCOPE'
            )
            OR EXISTS (
                SELECT 1 FROM public.outbox_events
                WHERE aggregate_type = 'SOURCE_SCOPE'
            ) THEN
                RAISE EXCEPTION 'DTS_SCOPE_COORDINATOR_DOWNGRADE_REQUIRES_UNUSED_STATE'
                    USING ERRCODE = '55000';
            END IF;
        END
        $guard_scope_downgrade$;
        """
    )


def _drop_scope_functions() -> None:
    op.execute(
        r"""
        DROP FUNCTION public.invalidate_source_scope_v2(
            text,text,text,text,text,text,bigint,bigint,text,text
        );
        DROP FUNCTION public.publish_source_snapshot_candidate_v2(
            text,text,text,text,bigint,bigint,text,text
        );
        DROP FUNCTION public.takeover_expired_source_snapshot_candidate_v2(
            text,text,text,bigint,text
        );
        DROP FUNCTION public.abort_source_snapshot_candidate_v2(
            text,text,text,text,bigint,bigint,text
        );
        DROP FUNCTION public.verify_source_snapshot_candidate_v2(
            text,text,text,text,bigint,bigint,bigint,text,text,text
        );
        DROP FUNCTION public.heartbeat_source_snapshot_candidate_v2(
            text,text,text,text,integer,bigint
        );
        DROP FUNCTION public.stage_source_snapshot_row_v2(
            text,text,text,jsonb,jsonb,jsonb,text
        );
        DROP FUNCTION public.begin_source_snapshot_candidate_v2(
            text,text,text,text,text,text,text,timestamptz,text,jsonb,
            timestamptz,timestamptz,text,integer,bigint,bigint
        );
        DROP FUNCTION public._fail_source_scope_candidate_v2(
            text,text,text,text,text,text
        );
        DROP FUNCTION public._record_source_scope_command_v2(
            text,text,text,text,text,text,text,text,text,jsonb,text,
            text,text,bigint,jsonb
        );
        DROP FUNCTION public._dts_scope_command_replay_v2(text,text,text);
        DROP FUNCTION public.dts_scope_candidate_error_v2(text,boolean);
        DROP FUNCTION public.dts_scope_snapshot_row_manifest_v2(text);
        DROP FUNCTION public.dts_scope_snapshot_fence_manifest_v2(text);
        DROP FUNCTION public._emit_source_scope_revision_v2(
            text,text,text,text,text,bigint,text,text,text
        );
        """
    )


def _drop_identity_helpers() -> None:
    op.execute(
        r"""
        DROP FUNCTION public.check_dts_scope_head_integrity_v2();
        DROP FUNCTION public.check_dts_scope_state_integrity_v2();
        DROP FUNCTION public.guard_dts_snapshot_rows_immutable_v2();
        DROP FUNCTION public.guard_dts_scope_append_only_v2();
        DROP FUNCTION public.guard_dts_scope_internal_write_v2();
        DROP FUNCTION public.dts_normalize_scope_partition_offsets_v2(
            jsonb,text
        );
        DROP FUNCTION public.dts_dependency_keys_valid_v2(text,jsonb);
        DROP FUNCTION public.dts_source_key_parts_valid_v2(
            text,jsonb,text,numeric,text
        );
        DROP FUNCTION public.dts_source_scope_identity_valid_v2(
            text,text,text,text,text
        );
        DROP FUNCTION public.dts_source_table_allowed_v2(text,text);
        """
    )


def _create_scope_tables() -> None:
    op.create_table(
        "dts_source_table_publish_generations",
        sa.Column("source_region", sa.String(length=8), nullable=False),
        sa.Column("source_table", sa.String(length=128), nullable=False),
        sa.Column(
            "current_generation",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("current_snapshot_id", sa.String(length=160)),
        sa.Column("active_candidate_snapshot_id", sa.String(length=160)),
        sa.Column("candidate_owner", sa.String(length=128)),
        sa.Column("candidate_lease_token", sa.String(length=160)),
        sa.Column("candidate_lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column(
            "row_version",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint(
            "source_region",
            "source_table",
            name="pk_dts_source_table_publish_generations",
        ),
        sa.CheckConstraint(
            "public.dts_source_table_allowed_v2(source_region,source_table)",
            name="ck_dts_source_publish_head_table",
        ),
        sa.CheckConstraint(
            "current_generation >= 0 AND row_version >= 1 "
            "AND ((current_generation = 0 AND current_snapshot_id IS NULL) "
            "OR (current_generation > 0 AND current_snapshot_id IS NOT NULL))",
            name="ck_dts_source_publish_head_generation",
        ),
        sa.CheckConstraint(
            "(active_candidate_snapshot_id IS NULL "
            "AND candidate_owner IS NULL "
            "AND candidate_lease_token IS NULL "
            "AND candidate_lease_expires_at IS NULL) OR "
            "(active_candidate_snapshot_id IS NOT NULL "
            "AND candidate_owner IS NOT NULL "
            "AND btrim(candidate_owner) <> '' "
            "AND candidate_lease_token IS NOT NULL "
            "AND btrim(candidate_lease_token) <> '' "
            "AND candidate_lease_expires_at IS NOT NULL)",
            name="ck_dts_source_publish_head_candidate",
        ),
        schema="public",
        comment=(
            "Per-table serial publication head for all GLOBAL and TEACHER "
            "source-scope candidates."
        ),
    )
    op.create_index(
        "uq_dts_source_publish_head_lease_token",
        "dts_source_table_publish_generations",
        ["candidate_lease_token"],
        unique=True,
        schema="public",
        postgresql_where=sa.text("candidate_lease_token IS NOT NULL"),
    )

    op.create_table(
        "dts_source_scope_snapshots",
        sa.Column("snapshot_id", sa.String(length=160), nullable=False),
        sa.Column("source_region", sa.String(length=8), nullable=False),
        sa.Column("source_table", sa.String(length=128), nullable=False),
        sa.Column("scope_kind", sa.String(length=16), nullable=False),
        sa.Column("scope_level", sa.String(length=16), nullable=False),
        sa.Column("scope_key", sa.String(length=160), nullable=False),
        sa.Column("epoch_state", sa.String(length=16), nullable=False),
        sa.Column("snapshot_as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("snapshot_consistency_token", sa.Text(), nullable=False),
        sa.Column(
            "snapshot_consistency_token_hash",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("snapshot_fence_vector", postgresql.JSONB(), nullable=False),
        sa.Column("partition_offsets", postgresql.JSONB(), nullable=False),
        sa.Column("snapshot_fence_hash", sa.String(length=64), nullable=False),
        sa.Column("row_count", sa.BigInteger()),
        sa.Column("content_hash", sa.String(length=64)),
        sa.Column("history_from", sa.DateTime(timezone=True)),
        sa.Column("history_through", sa.DateTime(timezone=True)),
        sa.Column("base_publish_generation", sa.BigInteger(), nullable=False),
        sa.Column("base_snapshot_id", sa.String(length=160)),
        sa.Column("published_generation", sa.BigInteger()),
        sa.Column("generation_diff_count", sa.BigInteger()),
        sa.Column("generation_diff_hash", sa.String(length=64)),
        sa.Column("begin_request_hash", sa.String(length=64), nullable=False),
        sa.Column("verify_request_hash", sa.String(length=64)),
        sa.Column("publish_request_hash", sa.String(length=64)),
        sa.Column("terminal_request_hash", sa.String(length=64)),
        sa.Column("error_code", sa.String(length=128)),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("failed_at", sa.DateTime(timezone=True)),
        sa.Column("invalidated_at", sa.DateTime(timezone=True)),
        sa.Column("row_version", sa.BigInteger(), nullable=False,
                  server_default=sa.text("1")),
        sa.PrimaryKeyConstraint(
            "snapshot_id", name="pk_dts_source_scope_snapshots"
        ),
        sa.UniqueConstraint(
            "snapshot_id", "source_region", "source_table", "scope_kind",
            "scope_level", "scope_key",
            name="uq_dts_source_scope_snapshot_identity",
        ),
        sa.UniqueConstraint(
            "source_region", "source_table", "snapshot_id",
            name="uq_dts_source_scope_snapshot_table_identity",
        ),
        sa.UniqueConstraint(
            "source_region", "source_table", "published_generation",
            "snapshot_id",
            name="uq_dts_source_scope_snapshot_generation_identity",
        ),
        sa.CheckConstraint(
            "public.dts_source_scope_identity_valid_v2(source_region,"
            "source_table,scope_kind,scope_level,scope_key)",
            name="ck_dts_source_scope_snapshot_identity",
        ),
        sa.CheckConstraint(
            "epoch_state IN ('LOADING','VERIFYING','COMPLETE','FAILED',"
            "'STALE','SUPERSEDED')",
            name="ck_dts_source_scope_snapshot_state",
        ),
        sa.CheckConstraint(
            "snapshot_consistency_token_hash ~ '^[0-9a-f]{64}$' "
            "AND snapshot_fence_hash ~ '^[0-9a-f]{64}$' "
            "AND begin_request_hash ~ '^[0-9a-f]{64}$' "
            "AND (verify_request_hash IS NULL OR verify_request_hash ~ "
            "'^[0-9a-f]{64}$') "
            "AND (publish_request_hash IS NULL OR publish_request_hash ~ "
            "'^[0-9a-f]{64}$') "
            "AND (terminal_request_hash IS NULL OR terminal_request_hash ~ "
            "'^[0-9a-f]{64}$')",
            name="ck_dts_source_scope_snapshot_hashes",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(snapshot_fence_vector) = 'array' "
            "AND jsonb_array_length(snapshot_fence_vector) > 0 "
            "AND partition_offsets = snapshot_fence_vector",
            name="ck_dts_source_scope_snapshot_fence",
        ),
        sa.CheckConstraint(
            "(scope_kind = 'CURRENT' AND history_from IS NULL "
            "AND history_through IS NULL) OR "
            "(scope_kind = 'HISTORY' AND history_from IS NOT NULL "
            "AND history_through IS NOT NULL "
            "AND history_from <= history_through)",
            name="ck_dts_source_scope_snapshot_history",
        ),
        sa.CheckConstraint(
            "base_publish_generation >= 0 AND row_version >= 1 "
            "AND (published_generation IS NULL OR published_generation >= 1) "
            "AND (generation_diff_count IS NULL OR generation_diff_count >= 0)",
            name="ck_dts_source_scope_snapshot_versions",
        ),
        sa.CheckConstraint(
            "(epoch_state = 'LOADING' AND row_count IS NULL "
            "AND content_hash IS NULL AND verified_at IS NULL "
            "AND published_generation IS NULL AND completed_at IS NULL "
            "AND failed_at IS NULL AND error_code IS NULL) OR "
            "(epoch_state = 'VERIFYING' AND row_count >= 0 "
            "AND content_hash ~ '^[0-9a-f]{64}$' AND verified_at IS NOT NULL "
            "AND published_generation IS NULL AND completed_at IS NULL "
            "AND failed_at IS NULL AND error_code IS NULL) OR "
            "(epoch_state = 'COMPLETE' AND row_count >= 0 "
            "AND content_hash ~ '^[0-9a-f]{64}$' "
            "AND verified_at IS NOT NULL AND completed_at IS NOT NULL "
            "AND published_generation >= 1 "
            "AND generation_diff_count >= 0 "
            "AND generation_diff_hash ~ '^[0-9a-f]{64}$' "
            "AND error_code IS NULL) OR "
            "(epoch_state = 'FAILED' AND failed_at IS NOT NULL "
            "AND error_code IS NOT NULL AND btrim(error_code) <> '' "
            "AND published_generation IS NULL) OR "
            "(epoch_state = 'STALE' AND invalidated_at IS NOT NULL "
            "AND published_generation IS NOT NULL) OR "
            "(epoch_state = 'SUPERSEDED' AND invalidated_at IS NOT NULL "
            "AND published_generation IS NOT NULL "
            "AND completed_at IS NOT NULL)",
            name="ck_dts_source_scope_snapshot_lifecycle",
        ),
        schema="public",
        comment=(
            "Immutable source-scope snapshot epochs with relationally "
            "verified fence and publication evidence."
        ),
    )
    op.create_index(
        "uq_dts_source_scope_snapshot_published_generation",
        "dts_source_scope_snapshots",
        ["source_region", "source_table", "published_generation"],
        unique=True,
        schema="public",
        postgresql_where=sa.text("published_generation IS NOT NULL"),
    )

    op.create_table(
        "dts_source_snapshot_fences",
        sa.Column("snapshot_id", sa.String(length=160), nullable=False),
        sa.Column("source_region", sa.String(length=8), nullable=False),
        sa.Column("source_table", sa.String(length=128), nullable=False),
        sa.Column("scope_kind", sa.String(length=16), nullable=False),
        sa.Column("scope_level", sa.String(length=16), nullable=False),
        sa.Column("scope_key", sa.String(length=160), nullable=False),
        sa.Column("source_partition_epoch_id", sa.String(length=160), nullable=False),
        sa.Column("topic", sa.String(length=512), nullable=False),
        sa.Column("partition_id", sa.Integer(), nullable=False),
        sa.Column("start_next_offset", sa.BigInteger(), nullable=False),
        sa.Column("end_next_offset", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint(
            "snapshot_id", "source_partition_epoch_id", "topic", "partition_id",
            name="pk_dts_source_snapshot_fences",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id", "source_region", "source_table", "scope_kind",
             "scope_level", "scope_key"],
            ["public.dts_source_scope_snapshots.snapshot_id",
             "public.dts_source_scope_snapshots.source_region",
             "public.dts_source_scope_snapshots.source_table",
             "public.dts_source_scope_snapshots.scope_kind",
             "public.dts_source_scope_snapshots.scope_level",
             "public.dts_source_scope_snapshots.scope_key"],
            name="fk_dts_source_snapshot_fence_snapshot",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_region", "source_partition_epoch_id", "topic", "partition_id"],
            ["public.dts_source_partition_epochs.source_region",
             "public.dts_source_partition_epochs.source_partition_epoch_id",
             "public.dts_source_partition_epochs.topic",
             "public.dts_source_partition_epochs.partition_id"],
            name="fk_dts_source_snapshot_fence_epoch",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "partition_id >= 0 AND start_next_offset >= 0 "
            "AND end_next_offset >= start_next_offset",
            name="ck_dts_source_snapshot_fence_offsets",
        ),
        schema="public",
        comment=(
            "Typed per-epoch half-open broker next-offset fences for one "
            "source-scope snapshot."
        ),
    )

    op.create_table(
        "dts_source_snapshot_rows",
        sa.Column("snapshot_id", sa.String(length=160), nullable=False),
        sa.Column("source_region", sa.String(length=8), nullable=False),
        sa.Column("source_table", sa.String(length=128), nullable=False),
        sa.Column("scope_kind", sa.String(length=16), nullable=False),
        sa.Column("scope_level", sa.String(length=16), nullable=False),
        sa.Column("scope_key", sa.String(length=160), nullable=False),
        sa.Column("source_key", sa.String(length=512), nullable=False),
        sa.Column("source_key_data", postgresql.JSONB(), nullable=False),
        sa.Column("source_key_type", sa.String(length=16), nullable=False),
        sa.Column("source_key_numeric", sa.Numeric()),
        sa.Column("source_key_text", sa.Text()),
        sa.Column("dependency_keys", postgresql.JSONB(), nullable=False),
        sa.Column("protected_source_row", postgresql.JSONB(), nullable=False),
        sa.Column("snapshot_row_hash", sa.String(length=64), nullable=False),
        sa.Column("base_source_row_revision", sa.BigInteger()),
        sa.Column("base_row_hash", sa.String(length=64)),
        sa.Column("snapshot_as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint(
            "snapshot_id", "source_region", "source_table", "scope_kind",
            "scope_level", "scope_key", "source_key",
            name="pk_dts_source_snapshot_rows",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id", "source_region", "source_table", "scope_kind",
             "scope_level", "scope_key"],
            ["public.dts_source_scope_snapshots.snapshot_id",
             "public.dts_source_scope_snapshots.source_region",
             "public.dts_source_scope_snapshots.source_table",
             "public.dts_source_scope_snapshots.scope_kind",
             "public.dts_source_scope_snapshots.scope_level",
             "public.dts_source_scope_snapshots.scope_key"],
            name="fk_dts_source_snapshot_row_snapshot",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "public.dts_source_key_parts_valid_v2(source_key,source_key_data,"
            "source_key_type,source_key_numeric,source_key_text)",
            name="ck_dts_source_snapshot_row_key",
        ),
        sa.CheckConstraint(
            "public.dts_dependency_keys_valid_v2(source_region,dependency_keys)",
            name="ck_dts_source_snapshot_row_dependencies",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(protected_source_row) = 'object' "
            "AND snapshot_row_hash ~ '^[0-9a-f]{64}$' "
            "AND snapshot_row_hash = "
            "public.dts_canonical_json_sha256_v1(protected_source_row) "
            "AND (source_region <> 'dom' OR "
            "public.dom_student_json_is_safe_v1(protected_source_row))",
            name="ck_dts_source_snapshot_row_payload",
        ),
        sa.CheckConstraint(
            "(base_source_row_revision IS NULL OR "
            "base_source_row_revision >= 1) AND "
            "(base_row_hash IS NULL OR base_row_hash ~ '^[0-9a-f]{64}$')",
            name="ck_dts_source_snapshot_row_base",
        ),
        schema="public",
        comment=(
            "Candidate-only protected staging rows with typed keys and "
            "frozen live-current base evidence."
        ),
    )

    op.create_table(
        "dts_source_scope_states",
        sa.Column("source_region", sa.String(length=8), nullable=False),
        sa.Column("source_table", sa.String(length=128), nullable=False),
        sa.Column("scope_kind", sa.String(length=16), nullable=False),
        sa.Column("scope_level", sa.String(length=16), nullable=False),
        sa.Column("scope_key", sa.String(length=160), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("active_snapshot_id", sa.String(length=160)),
        sa.Column("candidate_snapshot_id", sa.String(length=160)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("invalidated_at", sa.DateTime(timezone=True)),
        sa.Column("last_invalidation_hash", sa.String(length=64)),
        sa.Column("row_version", sa.BigInteger(), nullable=False,
                  server_default=sa.text("1")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint(
            "source_region", "source_table", "scope_kind", "scope_level",
            "scope_key", name="pk_dts_source_scope_states",
        ),
        sa.ForeignKeyConstraint(
            ["active_snapshot_id", "source_region", "source_table", "scope_kind",
             "scope_level", "scope_key"],
            ["public.dts_source_scope_snapshots.snapshot_id",
             "public.dts_source_scope_snapshots.source_region",
             "public.dts_source_scope_snapshots.source_table",
             "public.dts_source_scope_snapshots.scope_kind",
             "public.dts_source_scope_snapshots.scope_level",
             "public.dts_source_scope_snapshots.scope_key"],
            name="fk_dts_source_scope_state_active_snapshot",
            deferrable=True,
            initially="DEFERRED",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_snapshot_id", "source_region", "source_table", "scope_kind",
             "scope_level", "scope_key"],
            ["public.dts_source_scope_snapshots.snapshot_id",
             "public.dts_source_scope_snapshots.source_region",
             "public.dts_source_scope_snapshots.source_table",
             "public.dts_source_scope_snapshots.scope_kind",
             "public.dts_source_scope_snapshots.scope_level",
             "public.dts_source_scope_snapshots.scope_key"],
            name="fk_dts_source_scope_state_candidate_snapshot",
            deferrable=True,
            initially="DEFERRED",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "public.dts_source_scope_identity_valid_v2(source_region,"
            "source_table,scope_kind,scope_level,scope_key)",
            name="ck_dts_source_scope_state_identity",
        ),
        sa.CheckConstraint(
            "state IN ('INCOMPLETE','LOADING','VERIFYING','COMPLETE',"
            "'FAILED','STALE') AND row_version >= 1",
            name="ck_dts_source_scope_state_value",
        ),
        sa.CheckConstraint(
            "(state = 'INCOMPLETE' AND active_snapshot_id IS NULL "
            "AND candidate_snapshot_id IS NULL AND completed_at IS NULL) OR "
            "(state IN ('LOADING','VERIFYING') "
            "AND candidate_snapshot_id IS NOT NULL) OR "
            "(state = 'COMPLETE' AND active_snapshot_id IS NOT NULL "
            "AND candidate_snapshot_id IS NULL AND completed_at IS NOT NULL "
            "AND invalidated_at IS NULL) OR "
            "(state = 'FAILED' AND candidate_snapshot_id IS NOT NULL) OR "
            "(state = 'STALE' AND active_snapshot_id IS NOT NULL "
            "AND candidate_snapshot_id IS NULL "
            "AND invalidated_at IS NOT NULL)",
            name="ck_dts_source_scope_state_shape",
        ),
        sa.CheckConstraint(
            "last_invalidation_hash IS NULL OR "
            "last_invalidation_hash ~ '^[0-9a-f]{64}$'",
            name="ck_dts_source_scope_state_invalidation_hash",
        ),
        schema="public",
        comment=(
            "Authoritative scope completeness state; only COMPLETE plus a "
            "COMPLETE active snapshot proves empty/false/zero."
        ),
    )

    op.create_table(
        "dts_source_scope_memberships",
        sa.Column("source_region", sa.String(length=8), nullable=False),
        sa.Column("source_table", sa.String(length=128), nullable=False),
        sa.Column("scope_kind", sa.String(length=16), nullable=False),
        sa.Column("scope_level", sa.String(length=16), nullable=False),
        sa.Column("scope_key", sa.String(length=160), nullable=False),
        sa.Column("source_key", sa.String(length=512), nullable=False),
        sa.Column("source_key_data", postgresql.JSONB(), nullable=False),
        sa.Column("source_key_type", sa.String(length=16), nullable=False),
        sa.Column("source_key_numeric", sa.Numeric()),
        sa.Column("source_key_text", sa.Text()),
        sa.Column("active_snapshot_id", sa.String(length=160), nullable=False),
        sa.Column("snapshot_is_present", sa.Boolean(), nullable=False),
        sa.Column("snapshot_row_hash", sa.String(length=64)),
        sa.Column("dependency_keys", postgresql.JSONB(), nullable=False),
        sa.Column("cdc_overlay_is_present", sa.Boolean()),
        sa.Column("cdc_overlay_row_hash", sa.String(length=64)),
        sa.Column("last_cdc_source_revision", sa.BigInteger()),
        sa.Column(
            "effective_is_present",
            sa.Boolean(),
            sa.Computed(
                "coalesce(cdc_overlay_is_present,snapshot_is_present)",
                persisted=True,
            ),
            nullable=False,
        ),
        sa.Column("membership_revision", sa.BigInteger(), nullable=False,
                  server_default=sa.text("1")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint(
            "source_region", "source_table", "scope_kind", "scope_level",
            "scope_key", "source_key",
            name="pk_dts_source_scope_memberships",
        ),
        sa.ForeignKeyConstraint(
            ["active_snapshot_id", "source_region", "source_table", "scope_kind",
             "scope_level", "scope_key"],
            ["public.dts_source_scope_snapshots.snapshot_id",
             "public.dts_source_scope_snapshots.source_region",
             "public.dts_source_scope_snapshots.source_table",
             "public.dts_source_scope_snapshots.scope_kind",
             "public.dts_source_scope_snapshots.scope_level",
             "public.dts_source_scope_snapshots.scope_key"],
            name="fk_dts_source_scope_membership_snapshot",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "public.dts_source_scope_identity_valid_v2(source_region,"
            "source_table,scope_kind,scope_level,scope_key)",
            name="ck_dts_source_scope_membership_identity",
        ),
        sa.CheckConstraint(
            "public.dts_source_key_parts_valid_v2(source_key,source_key_data,"
            "source_key_type,source_key_numeric,source_key_text)",
            name="ck_dts_source_scope_membership_key",
        ),
        sa.CheckConstraint(
            "public.dts_dependency_keys_valid_v2(source_region,dependency_keys)",
            name="ck_dts_source_scope_membership_dependencies",
        ),
        sa.CheckConstraint(
            "((snapshot_is_present AND snapshot_row_hash ~ '^[0-9a-f]{64}$') "
            "OR (NOT snapshot_is_present AND snapshot_row_hash IS NULL)) "
            "AND ((cdc_overlay_is_present IS NULL "
            "AND cdc_overlay_row_hash IS NULL "
            "AND last_cdc_source_revision IS NULL) OR "
            "(cdc_overlay_is_present IS TRUE "
            "AND cdc_overlay_row_hash ~ '^[0-9a-f]{64}$' "
            "AND last_cdc_source_revision >= 1) OR "
            "(cdc_overlay_is_present IS FALSE "
            "AND cdc_overlay_row_hash IS NULL "
            "AND last_cdc_source_revision >= 1)) "
            "AND membership_revision >= 1",
            name="ck_dts_source_scope_membership_evidence",
        ),
        schema="public",
        comment=(
            "Published snapshot baseline plus nullable CDC overlay for one "
            "typed source key in a source scope."
        ),
    )
    op.create_index(
        "ix_dts_source_scope_memberships_source_key",
        "dts_source_scope_memberships",
        ["source_region", "source_table", "source_key"],
        schema="public",
    )

    op.create_table(
        "dts_source_scope_commands",
        sa.Column("command_id", sa.String(length=160), nullable=False),
        sa.Column("command_type", sa.String(length=24), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("snapshot_id", sa.String(length=160)),
        sa.Column("scope_identity", postgresql.JSONB(), nullable=False),
        sa.Column("response_payload", postgresql.JSONB(), nullable=False),
        sa.Column("executed_by", sa.String(length=128), nullable=False),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("command_id", name="pk_dts_source_scope_commands"),
        sa.CheckConstraint(
            "command_type IN ('BEGIN','HEARTBEAT','VERIFY','ABORT',"
            "'TAKEOVER','PUBLISH','INVALIDATE') "
            "AND request_hash ~ '^[0-9a-f]{64}$' "
            "AND jsonb_typeof(scope_identity) = 'object' "
            "AND jsonb_typeof(response_payload) = 'object'",
            name="ck_dts_source_scope_command_shape",
        ),
        schema="public",
        comment=(
            "Append-only command idempotency ledger for response-lost-safe "
            "source-scope state transitions."
        ),
    )

    op.create_table(
        "dts_source_scope_transition_audits",
        sa.Column("audit_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("command_id", sa.String(length=160), nullable=False),
        sa.Column("snapshot_id", sa.String(length=160)),
        sa.Column("source_region", sa.String(length=8), nullable=False),
        sa.Column("source_table", sa.String(length=128), nullable=False),
        sa.Column("scope_kind", sa.String(length=16), nullable=False),
        sa.Column("scope_level", sa.String(length=16), nullable=False),
        sa.Column("scope_key", sa.String(length=160), nullable=False),
        sa.Column("from_state", sa.String(length=16)),
        sa.Column("to_state", sa.String(length=16), nullable=False),
        sa.Column("scope_row_version", sa.BigInteger(), nullable=False),
        sa.Column("detail", postgresql.JSONB(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("actor", sa.String(length=128), nullable=False),
        sa.PrimaryKeyConstraint(
            "audit_id", name="pk_dts_source_scope_transition_audits"
        ),
        sa.UniqueConstraint(
            "command_id", name="uq_dts_source_scope_transition_command"
        ),
        sa.ForeignKeyConstraint(
            ["command_id"], ["public.dts_source_scope_commands.command_id"],
            name="fk_dts_source_scope_transition_command",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "public.dts_source_scope_identity_valid_v2(source_region,"
            "source_table,scope_kind,scope_level,scope_key) "
            "AND to_state IN ('LOADING','VERIFYING','COMPLETE','FAILED','STALE') "
            "AND (from_state IS NULL OR from_state IN "
            "('INCOMPLETE','LOADING','VERIFYING','COMPLETE','FAILED','STALE')) "
            "AND scope_row_version >= 1 "
            "AND jsonb_typeof(detail) = 'object'",
            name="ck_dts_source_scope_transition_shape",
        ),
        schema="public",
        comment=(
            "Append-only source-scope state transition audit tied to one "
            "idempotent command."
        ),
    )

    op.create_foreign_key(
        "fk_dts_source_publish_head_current_snapshot",
        "dts_source_table_publish_generations",
        "dts_source_scope_snapshots",
        ["source_region", "source_table", "current_generation",
         "current_snapshot_id"],
        ["source_region", "source_table", "published_generation",
         "snapshot_id"],
        source_schema="public",
        referent_schema="public",
        deferrable=True,
        initially="DEFERRED",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_dts_source_publish_head_candidate_snapshot",
        "dts_source_table_publish_generations",
        "dts_source_scope_snapshots",
        ["source_region", "source_table", "active_candidate_snapshot_id"],
        ["source_region", "source_table", "snapshot_id"],
        source_schema="public",
        referent_schema="public",
        deferrable=True,
        initially="DEFERRED",
        ondelete="RESTRICT",
    )


def _seed_publish_heads() -> None:
    values = ",\n                ".join(
        f"('{region}','{table}')" for region, table in SOURCE_TABLES
    )
    op.execute(
        f"""
        INSERT INTO public.dts_source_table_publish_generations (
            source_region,source_table
        )
        VALUES
                {values}
        ON CONFLICT (source_region,source_table) DO NOTHING;

        DO $seed_scope_heads$
        DECLARE
            expected_count integer := {len(SOURCE_TABLES)};
            actual_count integer;
        BEGIN
            SELECT count(*) INTO actual_count
            FROM public.dts_source_table_publish_generations;
            IF actual_count <> expected_count OR EXISTS (
                SELECT source_region,source_table
                FROM public.dts_source_table_publish_generations
                EXCEPT
                SELECT * FROM (VALUES {values}) AS expected(
                    source_region,source_table
                )
            ) OR EXISTS (
                SELECT * FROM (VALUES {values}) AS expected(
                    source_region,source_table
                )
                EXCEPT
                SELECT source_region,source_table
                FROM public.dts_source_table_publish_generations
            ) THEN
                RAISE EXCEPTION 'SOURCE_TABLE_GENERATION_HEAD_CONFLICT'
                    USING ERRCODE = '23514';
            END IF;
        END
        $seed_scope_heads$;
        """
    )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError("DTS v2 scope coordinator requires PostgreSQL")

    _install_identity_helpers()
    _create_scope_tables()
    _seed_publish_heads()
    _install_scope_guards()
    _install_scope_output_functions()
    _install_scope_state_machine()
    _apply_scope_acl()


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError("DTS v2 scope coordinator requires PostgreSQL")

    _guard_unused_downgrade()
    _drop_scope_functions()
    op.drop_constraint(
        "fk_dts_source_publish_head_candidate_snapshot",
        "dts_source_table_publish_generations",
        type_="foreignkey",
        schema="public",
    )
    op.drop_constraint(
        "fk_dts_source_publish_head_current_snapshot",
        "dts_source_table_publish_generations",
        type_="foreignkey",
        schema="public",
    )
    for table_name in reversed(NEW_TABLES):
        op.drop_table(table_name, schema="public")
    _drop_identity_helpers()
