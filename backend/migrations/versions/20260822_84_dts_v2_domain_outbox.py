"""install protected DTS v2 domain revision and Outbox publication.

Revision ID: 20260822_84_dts_v2_domain_outbox
Revises: 20260822_83_dts_v2_scope
Create Date: 2026-08-22

This revision does not activate a Worker or a read route.  It makes one
normalized domain semantic change and its immutable v2 Outbox event a single
owner-checked database command.  Legacy Outbox statuses remain accepted until
their separately audited archive/enforcement migration.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from alembic import op


revision: str = "20260822_84_dts_v2_domain_outbox"
down_revision: Union[str, None] = "20260822_83_dts_v2_scope"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _assert_preconditions() -> None:
    op.execute(
        r"""
        DO $domain_outbox_preflight$
        BEGIN
            IF to_regclass('public.domain_aggregate_revisions') IS NULL
               OR to_regclass('public.outbox_events') IS NULL
               OR to_regprocedure(
                    'public.dts_canonical_json_v1(jsonb)'
                  ) IS NULL
               OR to_regprocedure(
                    'public.dts_canonical_json_sha256_v1(jsonb)'
                  ) IS NULL
               OR to_regprocedure(
                    'public.dts_domain_aggregate_key_valid_v2(text,jsonb)'
                  ) IS NULL THEN
                RAISE EXCEPTION 'DTS_V2_DOMAIN_OUTBOX_SCHEMA_NOT_READY';
            END IF;
            IF to_regprocedure(
                    'public.publish_domain_aggregate_revision_v2('
                    'text,jsonb,jsonb,text,jsonb,bigint,jsonb,text,jsonb)'
               ) IS NOT NULL THEN
                RAISE EXCEPTION 'DTS_V2_DOMAIN_OUTBOX_ALREADY_INSTALLED';
            END IF;
        END
        $domain_outbox_preflight$;
        """
    )


def _extend_outbox_protocol() -> None:
    op.execute(
        r"""
        ALTER TABLE public.outbox_events
            ALTER COLUMN outbox_id TYPE varchar(160),
            ALTER COLUMN event_id TYPE varchar(512),
            ALTER COLUMN aggregate_id TYPE varchar(160);

        ALTER TABLE public.outbox_events
            ADD COLUMN payload_sha256 varchar(64),
            ADD COLUMN recovery_count bigint NOT NULL DEFAULT 0,
            ADD COLUMN recovered_at timestamptz,
            ADD COLUMN row_version bigint NOT NULL DEFAULT 1;

        UPDATE public.outbox_events
        SET payload_sha256 = public.dts_canonical_json_sha256_v1(payload)
        WHERE payload_sha256 IS NULL;

        ALTER TABLE public.outbox_events
            ALTER COLUMN payload_sha256 SET NOT NULL,
            ADD CONSTRAINT ck_outbox_payload_sha256_v2 CHECK (
                payload_sha256 ~ '^[0-9a-f]{64}$'
                AND payload_sha256 =
                    public.dts_canonical_json_sha256_v1(payload)
            ),
            ADD CONSTRAINT ck_outbox_recovery_v2 CHECK (
                recovery_count >= 0 AND row_version >= 1
                AND ((recovery_count = 0 AND recovered_at IS NULL)
                     OR (recovery_count > 0 AND recovered_at IS NOT NULL))
            );

        CREATE FUNCTION public.guard_v2_outbox_insert_v2()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY INVOKER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE table_owner text;
        BEGIN
            IF NEW.event_type IN (
                'source_wide.changed.v2',
                'task.materialization.requested.v2'
            ) THEN
                SELECT pg_get_userbyid(relowner) INTO table_owner
                FROM pg_class
                WHERE oid='public.outbox_events'::regclass;
                IF current_user IS DISTINCT FROM table_owner THEN
                    RAISE EXCEPTION
                        'DTS_V2_OUTBOX_DIRECT_INSERT_FORBIDDEN'
                        USING ERRCODE = '42501';
                END IF;
            END IF;
            RETURN NEW;
        END
        $function$;

        CREATE TRIGGER guard_v2_outbox_insert_v2
        BEFORE INSERT ON public.outbox_events
        FOR EACH ROW EXECUTE FUNCTION public.guard_v2_outbox_insert_v2();

        CREATE FUNCTION public.set_outbox_payload_sha256_v2()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE expected_hash text;
        BEGIN
            expected_hash := public.dts_canonical_json_sha256_v1(NEW.payload);
            IF NEW.payload_sha256 IS NULL THEN
                NEW.payload_sha256 := expected_hash;
            ELSIF NEW.payload_sha256 IS DISTINCT FROM expected_hash THEN
                RAISE EXCEPTION 'OUTBOX_PAYLOAD_HASH_MISMATCH'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END
        $function$;

        CREATE TRIGGER set_outbox_payload_sha256_v2
        BEFORE INSERT ON public.outbox_events
        FOR EACH ROW EXECUTE FUNCTION public.set_outbox_payload_sha256_v2();

        CREATE OR REPLACE FUNCTION public.guard_outbox_event_update()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $function$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'outbox events cannot be deleted'
                    USING ERRCODE = '42501';
            END IF;
            IF NEW.outbox_id IS DISTINCT FROM OLD.outbox_id
               OR NEW.event_id IS DISTINCT FROM OLD.event_id
               OR NEW.aggregate_type IS DISTINCT FROM OLD.aggregate_type
               OR NEW.aggregate_id IS DISTINCT FROM OLD.aggregate_id
               OR NEW.event_type IS DISTINCT FROM OLD.event_type
               OR NEW.payload IS DISTINCT FROM OLD.payload
               OR NEW.payload_sha256 IS DISTINCT FROM OLD.payload_sha256
               OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                RAISE EXCEPTION
                    'outbox event identity and payload are immutable'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.recovery_count < OLD.recovery_count
               OR NEW.row_version < OLD.row_version THEN
                RAISE EXCEPTION 'outbox event version cannot regress'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END
        $function$;

        REVOKE ALL ON FUNCTION
            public.guard_v2_outbox_insert_v2(),
            public.set_outbox_payload_sha256_v2(),
            public.guard_outbox_event_update()
        FROM PUBLIC;
        """
    )


def _install_validation_helpers() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.dts_v2_json_has_forbidden_student_key(
            p_value jsonb
        )
        RETURNS boolean
        LANGUAGE plpgsql
        IMMUTABLE
        STRICT
        PARALLEL SAFE
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE item record;
        BEGIN
            IF jsonb_typeof(p_value) = 'object' THEN
                FOR item IN SELECT key,value FROM jsonb_each(p_value) LOOP
                    IF lower(item.key) IN (
                        'student_id','studentid','s_id','sid','stu_id','stuid',
                        'raw_student_id','rawstudentid',
                        'raw_student','rawstudent','user_id','userid',
                        'uid','u_id','mobile','mobile_number','mobilenumber',
                        'phone','phone_number','phonenumber'
                    ) OR public.dts_v2_json_has_forbidden_student_key(
                        item.value
                    ) THEN
                        RETURN true;
                    END IF;
                END LOOP;
            ELSIF jsonb_typeof(p_value) = 'array' THEN
                FOR item IN SELECT value FROM jsonb_array_elements(p_value)
                LOOP
                    IF public.dts_v2_json_has_forbidden_student_key(
                        item.value
                    ) THEN
                        RETURN true;
                    END IF;
                END LOOP;
            END IF;
            RETURN false;
        END
        $function$;

        CREATE FUNCTION public.dts_v2_source_position_valid(
            p_value jsonb
        )
        RETURNS boolean
        LANGUAGE sql
        IMMUTABLE
        STRICT
        PARALLEL SAFE
        SET search_path = pg_catalog, public
        AS $function$
            SELECT jsonb_typeof(p_value) = 'object'
              AND p_value = jsonb_build_object(
                    'v',p_value->'v',
                    'source_timestamp',p_value->'source_timestamp',
                    'record_id_type',p_value->'record_id_type',
                    'record_id',p_value->'record_id',
                    'source_partition_epoch_id',
                        p_value->'source_partition_epoch_id',
                    'topic',p_value->'topic',
                    'partition_id',p_value->'partition_id',
                    'offset_value',p_value->'offset_value'
                  )
              AND p_value->'v' = '1'::jsonb
              AND jsonb_typeof(p_value->'source_timestamp')
                    IN ('null','string')
              AND jsonb_typeof(p_value->'record_id_type') = 'string'
              AND p_value->>'record_id_type' IN ('none','numeric','text')
              AND (
                    (p_value->>'record_id_type' = 'none'
                     AND jsonb_typeof(p_value->'record_id') = 'null')
                    OR
                    (p_value->>'record_id_type' IN ('numeric','text')
                     AND jsonb_typeof(p_value->'record_id') = 'string'
                     AND p_value->>'record_id' <> '')
                  )
              AND jsonb_typeof(
                    p_value->'source_partition_epoch_id'
                  ) = 'string'
              AND p_value->>'source_partition_epoch_id' <> ''
              AND jsonb_typeof(p_value->'topic') = 'string'
              AND p_value->>'topic' <> ''
              AND jsonb_typeof(p_value->'partition_id') = 'number'
              AND p_value->>'partition_id' ~ '^(0|[1-9][0-9]*)$'
              AND jsonb_typeof(p_value->'offset_value') = 'number'
              AND p_value->>'offset_value' ~ '^(0|[1-9][0-9]*)$'
        $function$;

        CREATE FUNCTION public.dts_v2_json_numbers_are_integers(
            p_value jsonb
        )
        RETURNS boolean
        LANGUAGE plpgsql
        IMMUTABLE
        STRICT
        PARALLEL SAFE
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE item jsonb;
        BEGIN
            IF jsonb_typeof(p_value) = 'number' THEN
                RETURN p_value #>> '{}' ~ '^-?(0|[1-9][0-9]*)$';
            ELSIF jsonb_typeof(p_value) = 'object' THEN
                FOR item IN SELECT value FROM jsonb_each(p_value) LOOP
                    IF public.dts_v2_json_numbers_are_integers(item)
                            IS DISTINCT FROM true THEN
                        RETURN false;
                    END IF;
                END LOOP;
            ELSIF jsonb_typeof(p_value) = 'array' THEN
                FOR item IN SELECT value FROM jsonb_array_elements(p_value)
                LOOP
                    IF public.dts_v2_json_numbers_are_integers(item)
                            IS DISTINCT FROM true THEN
                        RETURN false;
                    END IF;
                END LOOP;
            END IF;
            RETURN true;
        END
        $function$;

        CREATE FUNCTION public.dts_v2_domain_coverage_valid(
            p_coverage jsonb,
            p_source_row_revision bigint,
            p_source_position jsonb
        )
        RETURNS boolean
        LANGUAGE plpgsql
        IMMUTABLE
        PARALLEL SAFE
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE
            trigger_value jsonb;
            identity_value jsonb;
            input_kind text;
            source_region text;
            source_table text;
            scope_level text;
            scope_key text;
        BEGIN
            IF jsonb_typeof(p_coverage) IS DISTINCT FROM 'object'
               OR p_coverage = '{}'::jsonb
               OR jsonb_typeof(p_coverage->'trigger')
                    IS DISTINCT FROM 'object' THEN
                RETURN false;
            END IF;
            trigger_value := p_coverage->'trigger';
            identity_value := trigger_value->'input_identity';
            input_kind := trigger_value->>'input_kind';
            IF jsonb_typeof(identity_value) IS DISTINCT FROM 'object' THEN
                RETURN false;
            END IF;
            source_region := identity_value->>'source_region';
            source_table := identity_value->>'source_table';

            IF input_kind = 'SOURCE_REVISION' THEN
                RETURN trigger_value = jsonb_build_object(
                           'input_kind',trigger_value->'input_kind',
                           'input_identity',identity_value,
                           'input_revision',trigger_value->'input_revision',
                           'input_fingerprint',
                                trigger_value->'input_fingerprint',
                           'source_payload_hash',
                                trigger_value->'source_payload_hash'
                       )
                   AND identity_value = jsonb_build_object(
                           'source_region',identity_value->'source_region',
                           'source_table',identity_value->'source_table',
                           'source_key',identity_value->'source_key'
                       )
                   AND source_region IN ('dom','ovs')
                   AND jsonb_typeof(identity_value->'source_table') =
                        'string'
                   AND strpos(source_table,source_region || '_') = 1
                   AND jsonb_typeof(identity_value->'source_key') = 'string'
                   AND identity_value->>'source_key' <> ''
                   AND jsonb_typeof(trigger_value->'input_revision') =
                        'number'
                   AND trigger_value->>'input_revision' ~ '^[1-9][0-9]*$'
                   AND p_source_row_revision IS NOT NULL
                   AND trigger_value->>'input_revision' =
                        p_source_row_revision::text
                   AND public.dts_v2_source_position_valid(
                        p_source_position
                       ) IS TRUE
                   AND jsonb_typeof(trigger_value->'input_fingerprint') =
                        'string'
                   AND trigger_value->>'input_fingerprint' ~
                        '^[0-9a-f]{64}$'
                   AND jsonb_typeof(trigger_value->'source_payload_hash') =
                        'string'
                   AND trigger_value->>'source_payload_hash' ~
                        '^[0-9a-f]{64}$';
            ELSIF input_kind = 'SCOPE_REVISION' THEN
                scope_level := identity_value->>'scope_level';
                scope_key := identity_value->>'scope_key';
                RETURN p_source_row_revision IS NULL
                   AND p_source_position IS NULL
                   AND trigger_value = jsonb_build_object(
                           'input_kind',trigger_value->'input_kind',
                           'input_identity',identity_value,
                           'input_revision',trigger_value->'input_revision',
                           'input_fingerprint',
                                trigger_value->'input_fingerprint',
                           'scope_state',trigger_value->'scope_state',
                           'active_snapshot_id',
                                trigger_value->'active_snapshot_id',
                           'active_fence_hash',
                                trigger_value->'active_fence_hash'
                       )
                   AND identity_value = jsonb_build_object(
                           'source_region',identity_value->'source_region',
                           'source_table',identity_value->'source_table',
                           'scope_kind',identity_value->'scope_kind',
                           'scope_level',identity_value->'scope_level',
                           'scope_key',identity_value->'scope_key'
                       )
                   AND source_region IN ('dom','ovs')
                   AND jsonb_typeof(identity_value->'source_table') =
                        'string'
                   AND strpos(source_table,source_region || '_') = 1
                   AND identity_value->>'scope_kind' IN ('CURRENT','HISTORY')
                   AND scope_level IN ('GLOBAL','TEACHER')
                   AND jsonb_typeof(identity_value->'scope_key') = 'string'
                   AND ((scope_level = 'GLOBAL' AND scope_key = '*')
                        OR (scope_level = 'TEACHER' AND scope_key <> ''))
                   AND jsonb_typeof(trigger_value->'input_revision') =
                        'number'
                   AND trigger_value->>'input_revision' ~ '^[1-9][0-9]*$'
                   AND jsonb_typeof(trigger_value->'input_fingerprint') =
                        'string'
                   AND trigger_value->>'input_fingerprint' ~
                        '^[0-9a-f]{64}$'
                   AND trigger_value->>'scope_state' IN (
                        'INCOMPLETE','LOADING','VERIFYING','COMPLETE',
                        'FAILED','STALE'
                       )
                   AND (
                        jsonb_typeof(
                            trigger_value->'active_snapshot_id'
                        ) = 'null'
                        OR (
                            jsonb_typeof(
                                trigger_value->'active_snapshot_id'
                            ) = 'string'
                            AND trigger_value->>'active_snapshot_id' <> ''
                        )
                       )
                   AND (
                        jsonb_typeof(
                            trigger_value->'active_fence_hash'
                        ) = 'null'
                        OR (
                            jsonb_typeof(
                                trigger_value->'active_fence_hash'
                            ) = 'string'
                            AND trigger_value->>'active_fence_hash' ~
                                '^[0-9a-f]{64}$'
                        )
                       );
            END IF;
            RETURN false;
        END
        $function$;

        REVOKE ALL ON FUNCTION
            public.dts_v2_json_has_forbidden_student_key(jsonb),
            public.dts_v2_source_position_valid(jsonb),
            public.dts_v2_json_numbers_are_integers(jsonb),
            public.dts_v2_domain_coverage_valid(jsonb,bigint,jsonb)
        FROM PUBLIC;
        """
    )


def _install_publisher() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.publish_domain_aggregate_revision_v2(
            p_aggregate_type text,
            p_canonical_key jsonb,
            p_aggregate_state jsonb,
            p_aggregate_state_sha256 text,
            p_changed_fields jsonb,
            p_source_row_revision bigint,
            p_source_position jsonb,
            p_rule_version text,
            p_cutover_coverage_identity jsonb
        )
        RETURNS jsonb
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE
            key_sha256 text;
            aggregate_identity text;
            state_sha256 text;
            sorted_changed_fields jsonb;
            existing public.domain_aggregate_revisions%ROWTYPE;
            next_revision bigint;
            event_identity text;
            outbox_identity text;
            event_payload jsonb;
            event_payload_sha256 text;
            inserted_count integer;
            existing_outbox public.outbox_events%ROWTYPE;
        BEGIN
            IF p_aggregate_type NOT IN (
                'COURSE','PARTICIPATION','TEACHER','TEACHER_STUDENT',
                'LABEL','COMPLAINT_CATEGORY','COMPLETION_CONFLICT'
            ) THEN
                RAISE EXCEPTION
                    'DTS_V2_DOMAIN_PUBLISHER_AGGREGATE_TYPE_INVALID'
                    USING ERRCODE = '22023';
            END IF;
            IF public.dts_domain_aggregate_key_valid_v2(
                    p_aggregate_type,p_canonical_key
                 ) IS DISTINCT FROM true THEN
                RAISE EXCEPTION 'DTS_V2_DOMAIN_AGGREGATE_KEY_INVALID'
                    USING ERRCODE = '22023';
            END IF;
            IF jsonb_typeof(p_aggregate_state) IS DISTINCT FROM 'object'
               OR public.dts_v2_json_has_forbidden_student_key(
                    p_aggregate_state
                  )
               OR p_aggregate_state_sha256 !~ '^[0-9a-f]{64}$' THEN
                RAISE EXCEPTION 'DTS_V2_DOMAIN_AGGREGATE_STATE_INVALID'
                    USING ERRCODE = '22023';
            END IF;
            IF public.dts_v2_json_numbers_are_integers(p_aggregate_state)
                    IS DISTINCT FROM true THEN
                RAISE EXCEPTION 'DTS_V2_DOMAIN_JSON_NUMBER_INVALID'
                    USING ERRCODE = '22023';
            END IF;
            state_sha256 := public.dts_canonical_json_sha256_v1(
                p_aggregate_state
            );
            IF state_sha256 IS DISTINCT FROM p_aggregate_state_sha256 THEN
                RAISE EXCEPTION 'DTS_V2_DOMAIN_AGGREGATE_STATE_HASH_MISMATCH'
                    USING ERRCODE = '23514';
            END IF;
            IF jsonb_typeof(p_changed_fields) IS DISTINCT FROM 'array'
               OR jsonb_array_length(p_changed_fields) = 0
               OR EXISTS (
                    SELECT 1
                    FROM jsonb_array_elements(p_changed_fields) AS item(value)
                    WHERE jsonb_typeof(item.value) <> 'string'
                       OR item.value #>> '{}' !~ '^[a-z][a-z0-9_]*$'
               ) THEN
                RAISE EXCEPTION 'DTS_V2_DOMAIN_CHANGED_FIELDS_INVALID'
                    USING ERRCODE = '22023';
            END IF;
            SELECT jsonb_agg(to_jsonb(value) ORDER BY convert_to(value,'UTF8'))
            INTO sorted_changed_fields
            FROM (
                SELECT value,count(*) AS occurrences
                FROM jsonb_array_elements_text(p_changed_fields) AS value
                GROUP BY value
            ) AS fields;
            IF sorted_changed_fields IS DISTINCT FROM p_changed_fields
               OR EXISTS (
                    SELECT 1
                    FROM jsonb_array_elements_text(p_changed_fields) AS value
                    GROUP BY value HAVING count(*) <> 1
               ) THEN
                RAISE EXCEPTION 'DTS_V2_DOMAIN_CHANGED_FIELDS_NOT_CANONICAL'
                    USING ERRCODE = '22023';
            END IF;
            IF p_rule_version IS NOT NULL
               AND btrim(p_rule_version) = '' THEN
                RAISE EXCEPTION 'DTS_V2_DOMAIN_RULE_VERSION_INVALID'
                    USING ERRCODE = '22023';
            END IF;
            IF jsonb_typeof(p_cutover_coverage_identity)
                    IS DISTINCT FROM 'object'
               OR p_cutover_coverage_identity = '{}'::jsonb
               OR public.dts_v2_json_has_forbidden_student_key(
                    p_cutover_coverage_identity
                  ) THEN
                RAISE EXCEPTION 'DTS_V2_DOMAIN_COVERAGE_IDENTITY_INVALID'
                    USING ERRCODE = '22023';
            END IF;
            IF public.dts_v2_json_numbers_are_integers(
                    p_cutover_coverage_identity
               ) IS DISTINCT FROM true THEN
                RAISE EXCEPTION 'DTS_V2_DOMAIN_JSON_NUMBER_INVALID'
                    USING ERRCODE = '22023';
            END IF;
            IF public.dts_v2_domain_coverage_valid(
                    p_cutover_coverage_identity,
                    p_source_row_revision,
                    p_source_position
               ) IS DISTINCT FROM true THEN
                RAISE EXCEPTION 'DTS_V2_DOMAIN_COVERAGE_TRIGGER_INVALID'
                    USING ERRCODE = '22023';
            END IF;

            key_sha256 := public.dts_canonical_json_sha256_v1(
                p_canonical_key
            );
            aggregate_identity :=
                'v2:' || p_aggregate_type || ':' || key_sha256;
            PERFORM pg_advisory_xact_lock(
                hashtextextended(
                    'dts-v2-domain-aggregate:' || aggregate_identity,
                    0
                )
            );
            SELECT * INTO existing
            FROM public.domain_aggregate_revisions
            WHERE aggregate_type = p_aggregate_type
              AND aggregate_id = aggregate_identity
            FOR UPDATE;

            IF FOUND THEN
                IF existing.canonical_key IS DISTINCT FROM p_canonical_key
                   OR existing.canonical_key_sha256 IS DISTINCT FROM key_sha256
                THEN
                    RAISE EXCEPTION 'DTS_V2_DOMAIN_AGGREGATE_ID_CONFLICT'
                        USING ERRCODE = '23514';
                END IF;
                IF existing.aggregate_state_sha256 = state_sha256
                   AND existing.aggregate_state = p_aggregate_state THEN
                    IF p_source_row_revision IS NOT NULL THEN
                        UPDATE public.domain_aggregate_revisions
                        SET last_source_row_revision=p_source_row_revision,
                            last_source_position=p_source_position,
                            updated_at=clock_timestamp()
                        WHERE aggregate_type=p_aggregate_type
                          AND aggregate_id=aggregate_identity;
                    END IF;
                    RETURN jsonb_build_object(
                        'status','UNCHANGED',
                        'aggregate_id',aggregate_identity,
                        'aggregate_revision',existing.revision,
                        'event_id',NULL,
                        'outbox_id',NULL,
                        'payload_sha256',NULL
                    );
                END IF;
                next_revision := existing.revision + 1;
                UPDATE public.domain_aggregate_revisions
                SET revision=next_revision,
                    last_source_row_revision=CASE
                        WHEN p_source_row_revision IS NULL THEN
                            last_source_row_revision
                        ELSE p_source_row_revision
                    END,
                    last_source_position=CASE
                        WHEN p_source_row_revision IS NULL THEN
                            last_source_position
                        ELSE p_source_position
                    END,
                    aggregate_state=p_aggregate_state,
                    aggregate_state_sha256=state_sha256,
                    updated_at=clock_timestamp()
                WHERE aggregate_type=p_aggregate_type
                  AND aggregate_id=aggregate_identity;
            ELSE
                next_revision := 1;
                INSERT INTO public.domain_aggregate_revisions (
                    aggregate_type,aggregate_id,canonical_key,
                    canonical_key_sha256,revision,last_source_row_revision,
                    last_source_position,aggregate_state,
                    aggregate_state_sha256
                ) VALUES (
                    p_aggregate_type,aggregate_identity,p_canonical_key,
                    key_sha256,next_revision,p_source_row_revision,
                    p_source_position,p_aggregate_state,state_sha256
                );
            END IF;

            event_identity :=
                'source_wide.changed.v2:' || p_aggregate_type || ':' ||
                aggregate_identity || ':' || next_revision::text;
            outbox_identity := 'outbox:v2:' || encode(
                sha256(convert_to(event_identity,'UTF8')),'hex'
            );
            event_payload := jsonb_build_object(
                'protocol_version','domain-aggregate-outbox-v2',
                'aggregate_key',p_canonical_key,
                'changed_fields',p_changed_fields,
                'aggregate_revision',next_revision,
                'source_row_revision',p_source_row_revision,
                'source_position',p_source_position,
                'rule_version',p_rule_version,
                'cutover_coverage_identity',p_cutover_coverage_identity
            );
            event_payload_sha256 :=
                public.dts_canonical_json_sha256_v1(event_payload);
            INSERT INTO public.outbox_events (
                outbox_id,event_id,aggregate_type,aggregate_id,event_type,
                payload,payload_sha256,status,available_at,attempt_count,
                last_error,created_at,published_at,recovery_count,
                recovered_at,row_version
            ) VALUES (
                outbox_identity,event_identity,p_aggregate_type,
                aggregate_identity,'source_wide.changed.v2',event_payload,
                event_payload_sha256,'PENDING',transaction_timestamp(),0,
                NULL,transaction_timestamp(),NULL,0,NULL,1
            ) ON CONFLICT DO NOTHING;
            GET DIAGNOSTICS inserted_count = ROW_COUNT;
            IF inserted_count = 0 THEN
                SELECT * INTO existing_outbox
                FROM public.outbox_events
                WHERE event_id=event_identity
                FOR UPDATE;
                IF NOT FOUND
                   OR existing_outbox.outbox_id IS DISTINCT FROM outbox_identity
                   OR existing_outbox.aggregate_type IS DISTINCT FROM
                        p_aggregate_type
                   OR existing_outbox.aggregate_id IS DISTINCT FROM
                        aggregate_identity
                   OR existing_outbox.event_type IS DISTINCT FROM
                        'source_wide.changed.v2'
                   OR existing_outbox.payload IS DISTINCT FROM event_payload
                   OR existing_outbox.payload_sha256 IS DISTINCT FROM
                        event_payload_sha256 THEN
                    RAISE EXCEPTION 'DTS_V2_DOMAIN_OUTBOX_ID_CONFLICT'
                        USING ERRCODE = '23514';
                END IF;
            END IF;
            RETURN jsonb_build_object(
                'status','CHANGED',
                'aggregate_id',aggregate_identity,
                'aggregate_revision',next_revision,
                'event_id',event_identity,
                'outbox_id',outbox_identity,
                'payload_sha256',event_payload_sha256
            );
        END
        $function$;

        REVOKE ALL ON FUNCTION
            public.publish_domain_aggregate_revision_v2(
                text,jsonb,jsonb,text,jsonb,bigint,jsonb,text,jsonb
            )
        FROM PUBLIC, tit_growth_app, tit_dts_ingest_runtime;

        DO $domain_outbox_optional_acl$
        BEGIN
            IF to_regrole('tit_growth_app') IS NOT NULL THEN
                EXECUTE 'REVOKE ALL ON FUNCTION '
                    'public.publish_domain_aggregate_revision_v2('
                    'text,jsonb,jsonb,text,jsonb,bigint,jsonb,text,jsonb) '
                    'FROM tit_growth_app';
            END IF;
            IF to_regrole('tit_growth_app') IS NOT NULL THEN
                IF EXISTS (
                    SELECT 1 FROM pg_roles
                    WHERE rolname='tit_growth_app'
                      AND (NOT rolcanlogin OR rolinherit OR rolsuper
                           OR rolcreatedb OR rolcreaterole OR rolreplication
                           OR rolbypassrls)
                ) THEN
                    RAISE EXCEPTION
                        'tit_growth_app must be a restricted NOINHERIT LOGIN role';
                END IF;
                EXECUTE 'GRANT EXECUTE ON FUNCTION '
                    'public.publish_domain_aggregate_revision_v2('
                    'text,jsonb,jsonb,text,jsonb,bigint,jsonb,text,jsonb) '
                    'TO tit_growth_app';
            END IF;
        END
        $domain_outbox_optional_acl$;
        """
    )


def _apply_domain_runtime_acl() -> None:
    op.execute(
        r"""
        DO $domain_projection_optional_acl$
        BEGIN
            GRANT EXECUTE ON FUNCTION
                public.dts_canonical_json_v1(jsonb),
                public.dts_canonical_json_sha256_v1(jsonb)
            TO tit_growth_app;
            GRANT UPDATE (row_version) ON TABLE public.outbox_events
            TO tit_growth_app;
            IF to_regrole('tit_growth_app') IS NOT NULL THEN
                EXECUTE 'GRANT EXECUTE ON FUNCTION '
                    'public.dts_canonical_json_sha256_v1(jsonb) '
                    'TO tit_growth_app';
                EXECUTE 'GRANT SELECT ON TABLE '
                    'public.dts_source_partition_epochs,'
                    'public.dts_source_row_versions,'
                    'public.dts_source_rows,'
                    'public.complaint_category_rules,'
                    'public.complaint_rule_imports '
                    'TO tit_growth_app';
                EXECUTE 'GRANT SELECT,INSERT,UPDATE ON TABLE '
                    'public.source_courses,'
                    'public.source_course_participations '
                    'TO tit_growth_app';
                EXECUTE 'REVOKE INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER ON TABLE '
                    'public.domain_aggregate_revisions '
                    'FROM tit_growth_app';
                EXECUTE 'GRANT SELECT ON TABLE '
                    'public.domain_aggregate_revisions '
                    'TO tit_growth_app';
            END IF;
            -- tit_growth_app already owns the application Outbox CRUD contract
            -- from rev59.  Reusing that role for the V2 publisher/worker must
            -- add protected command capabilities without narrowing the
            -- existing application grant.
            GRANT SELECT,INSERT,UPDATE,DELETE ON TABLE public.outbox_events
            TO tit_growth_app;
        END
        $domain_projection_optional_acl$;
        """
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("DTS v2 domain Outbox requires PostgreSQL")
    _assert_preconditions()
    _extend_outbox_protocol()
    _install_validation_helpers()
    _install_publisher()
    _apply_domain_runtime_acl()


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("DTS v2 domain Outbox requires PostgreSQL")
    op.execute(
        r"""
        DO $domain_outbox_downgrade_guard$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM public.domain_aggregate_revisions LIMIT 1
            ) OR EXISTS (
                SELECT 1 FROM public.outbox_events
                WHERE event_type IN (
                    'source_wide.changed.v2',
                    'task.materialization.requested.v2'
                ) LIMIT 1
            ) OR EXISTS (
                SELECT 1 FROM public.outbox_events
                WHERE length(event_id)>128 OR length(aggregate_id)>128
                   OR length(outbox_id)>128
                LIMIT 1
            ) THEN
                RAISE EXCEPTION
                    'refusing DTS v2 domain Outbox downgrade: v2 history exists';
            END IF;
        END
        $domain_outbox_downgrade_guard$;

        DO $domain_outbox_downgrade_optional_acl$
        BEGIN
            IF to_regrole('tit_growth_app') IS NOT NULL THEN
                -- rev81 already owns both canonical helper EXECUTE grants. Only
                -- remove the source/course privileges introduced by rev84 so
                -- the rev83 ACL is restored exactly.
                EXECUTE 'REVOKE SELECT ON TABLE '
                    'public.dts_source_partition_epochs,'
                    'public.dts_source_row_versions,'
                    'public.dts_source_rows,'
                    'public.complaint_category_rules,'
                    'public.complaint_rule_imports '
                    'FROM tit_growth_app';
                EXECUTE 'REVOKE SELECT,INSERT,UPDATE ON TABLE '
                    'public.source_courses,'
                    'public.source_course_participations '
                    'FROM tit_growth_app';
            END IF;
        END
        $domain_outbox_downgrade_optional_acl$;

        DROP FUNCTION IF EXISTS
            public.publish_domain_aggregate_revision_v2(
                text,jsonb,jsonb,text,jsonb,bigint,jsonb,text,jsonb
            );
        DROP FUNCTION IF EXISTS
            public.dts_v2_domain_coverage_valid(jsonb,bigint,jsonb);
        DROP FUNCTION IF EXISTS public.dts_v2_source_position_valid(jsonb);
        DROP FUNCTION IF EXISTS
            public.dts_v2_json_numbers_are_integers(jsonb);
        DROP FUNCTION IF EXISTS
            public.dts_v2_json_has_forbidden_student_key(jsonb);
        DROP TRIGGER IF EXISTS set_outbox_payload_sha256_v2
            ON public.outbox_events;
        DROP TRIGGER IF EXISTS guard_v2_outbox_insert_v2
            ON public.outbox_events;
        DROP FUNCTION IF EXISTS public.guard_v2_outbox_insert_v2();
        DROP FUNCTION IF EXISTS public.set_outbox_payload_sha256_v2();

        ALTER TABLE public.outbox_events
            DROP CONSTRAINT ck_outbox_recovery_v2,
            DROP CONSTRAINT ck_outbox_payload_sha256_v2,
            DROP COLUMN row_version,
            DROP COLUMN recovered_at,
            DROP COLUMN recovery_count,
            DROP COLUMN payload_sha256,
            ALTER COLUMN aggregate_id TYPE varchar(128),
            ALTER COLUMN event_id TYPE varchar(128),
            ALTER COLUMN outbox_id TYPE varchar(128);

        CREATE OR REPLACE FUNCTION public.guard_outbox_event_update()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $function$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'outbox events cannot be deleted'
                    USING ERRCODE = '42501';
            END IF;
            IF NEW.outbox_id IS DISTINCT FROM OLD.outbox_id
               OR NEW.event_id IS DISTINCT FROM OLD.event_id
               OR NEW.aggregate_type IS DISTINCT FROM OLD.aggregate_type
               OR NEW.aggregate_id IS DISTINCT FROM OLD.aggregate_id
               OR NEW.event_type IS DISTINCT FROM OLD.event_type
               OR NEW.payload IS DISTINCT FROM OLD.payload
               OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                RAISE EXCEPTION
                    'outbox event identity and payload are immutable'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END
        $function$;
        REVOKE ALL ON FUNCTION public.guard_outbox_event_update()
        FROM PUBLIC;
        """
    )
