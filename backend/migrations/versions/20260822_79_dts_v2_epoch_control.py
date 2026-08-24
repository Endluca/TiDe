"""add the controlled initial DTS v2 broker epoch bootstrap.

Revision ID: 20260822_79_dts_v2_epoch_control
Revises: 20260822_78_pending_score_guard
Create Date: 2026-08-22

This revision is additive.  It does not seed an epoch while Alembic is
running.  The sole H0 write path is the whole-vector SECURITY DEFINER
function ``bootstrap_initial_broker_epoch_v2``.  The function validates the
complete legacy checkpoint vector, creates every sequence-one ACTIVE broker
epoch, backfills the nullable v2 checkpoint identity, and writes the pipeline
control plus immutable bootstrap audit in one transaction.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "20260822_79_dts_v2_epoch_control"
down_revision: Union[str, None] = "20260822_78_pending_score_guard"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


CONTROL_TABLES: tuple[str, ...] = (
    "dts_pipeline_control",
    "dts_pipeline_bootstrap_audits",
    "dts_ingest_issues",
)

BOOTSTRAP_FUNCTION_SIGNATURE = (
    "public.bootstrap_initial_broker_epoch_v2(text,text,jsonb,text)"
)


def _create_control_tables() -> None:
    op.create_table(
        "dts_pipeline_control",
        sa.Column("control_id", sa.String(length=16), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column(
            "row_version",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "projection_generation",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "qualification_grants_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment=(
                "Database-authoritative gate for first irreversible "
                "graduation and gold grants"
            ),
        ),
        sa.Column(
            "consumer_group",
            sa.String(length=256),
            nullable=False,
            comment=(
                "Bootstrap fleet identity; per-subscription consumer groups "
                "are stored in initial_h0_vector routes"
            ),
        ),
        sa.Column("initial_h0_vector", postgresql.JSONB(), nullable=False),
        sa.Column(
            "initial_h0_vector_hash",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "initial_h0_bootstrap_run_id",
            sa.String(length=160),
            nullable=False,
        ),
        sa.Column("last_handoff_vector", postgresql.JSONB(), nullable=True),
        sa.Column(
            "last_handoff_vector_hash",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column(
            "last_handoff_run_id",
            sa.String(length=160),
            nullable=True,
        ),
        sa.Column(
            "time_catchup_status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'NOT_REQUIRED'"),
        ),
        sa.Column("time_catchup_run_id", sa.String(length=160), nullable=True),
        sa.Column(
            "time_catchup_from",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "time_catchup_through",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "time_catchup_expected_count",
            sa.BigInteger(),
            nullable=True,
        ),
        sa.Column(
            "time_catchup_expected_hash",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column("changed_by", sa.String(length=128), nullable=False),
        sa.PrimaryKeyConstraint(
            "control_id",
            name="pk_dts_pipeline_control",
        ),
        sa.CheckConstraint(
            "control_id = 'PRIMARY'",
            name="ck_dts_pipeline_control_singleton",
        ),
        sa.CheckConstraint(
            "mode IN ('V1_COMPAT_DUAL_CAPTURE', 'V2_PRIMARY', 'ROLLED_BACK')",
            name="ck_dts_pipeline_control_mode",
        ),
        sa.CheckConstraint(
            "row_version >= 1 AND projection_generation >= 0",
            name="ck_dts_pipeline_control_versions",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(initial_h0_vector) = 'array' "
            "AND jsonb_array_length(initial_h0_vector) > 0",
            name="ck_dts_pipeline_control_initial_vector",
        ),
        sa.CheckConstraint(
            "initial_h0_vector_hash ~ '^[0-9a-f]{64}$'",
            name="ck_dts_pipeline_control_initial_hash",
        ),
        sa.CheckConstraint(
            "(last_handoff_vector IS NULL "
            "AND last_handoff_vector_hash IS NULL "
            "AND last_handoff_run_id IS NULL) "
            "OR (last_handoff_vector IS NOT NULL "
            "AND last_handoff_vector_hash IS NOT NULL "
            "AND last_handoff_run_id IS NOT NULL "
            "AND jsonb_typeof(last_handoff_vector) = 'array' "
            "AND last_handoff_vector_hash ~ '^[0-9a-f]{64}$' "
            "AND btrim(last_handoff_run_id) <> '')",
            name="ck_dts_pipeline_control_handoff_shape",
        ),
        sa.CheckConstraint(
            "time_catchup_status IN ('NOT_REQUIRED', 'PENDING', 'COMPLETE')",
            name="ck_dts_pipeline_control_catchup_status",
        ),
        schema="public",
    )

    op.create_table(
        "dts_pipeline_bootstrap_audits",
        sa.Column(
            "bootstrap_run_id",
            sa.String(length=160),
            nullable=False,
        ),
        sa.Column(
            "control_id",
            sa.String(length=16),
            nullable=False,
        ),
        sa.Column(
            "consumer_group",
            sa.String(length=256),
            nullable=False,
            comment="Bootstrap fleet identity, not a route consumer group",
        ),
        sa.Column("h0_vector", postgresql.JSONB(), nullable=False),
        sa.Column("h0_vector_hash", sa.String(length=64), nullable=False),
        sa.Column("route_count", sa.Integer(), nullable=False),
        sa.Column("result_status", sa.String(length=16), nullable=False),
        sa.Column(
            "executed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column("executed_by", sa.String(length=128), nullable=False),
        sa.PrimaryKeyConstraint(
            "bootstrap_run_id",
            name="pk_dts_pipeline_bootstrap_audits",
        ),
        sa.UniqueConstraint(
            "control_id",
            name="uq_dts_pipeline_bootstrap_audit_control",
        ),
        sa.ForeignKeyConstraint(
            ["control_id"],
            ["public.dts_pipeline_control.control_id"],
            name="fk_dts_pipeline_bootstrap_audit_control",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "control_id = 'PRIMARY'",
            name="ck_dts_pipeline_bootstrap_audit_control",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(h0_vector) = 'array' "
            "AND jsonb_array_length(h0_vector) = route_count "
            "AND route_count > 0",
            name="ck_dts_pipeline_bootstrap_audit_vector",
        ),
        sa.CheckConstraint(
            "h0_vector_hash ~ '^[0-9a-f]{64}$'",
            name="ck_dts_pipeline_bootstrap_audit_hash",
        ),
        sa.CheckConstraint(
            "result_status = 'APPLIED'",
            name="ck_dts_pipeline_bootstrap_audit_result",
        ),
        schema="public",
    )

    op.create_table(
        "dts_ingest_issues",
        sa.Column("ingest_issue_id", sa.String(length=64), nullable=False),
        sa.Column(
            "connector_delivery_identity_hash",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("payload_hmac", sa.String(length=64), nullable=False),
        sa.Column("hmac_key_version", sa.String(length=64), nullable=False),
        sa.Column("source_region", sa.String(length=8), nullable=True),
        sa.Column(
            "source_partition_epoch_id",
            sa.String(length=160),
            nullable=True,
        ),
        sa.Column("topic", sa.String(length=512), nullable=True),
        sa.Column("partition_id", sa.Integer(), nullable=True),
        sa.Column("offset_value", sa.BigInteger(), nullable=True),
        sa.Column("current_error_codes", postgresql.JSONB(), nullable=False),
        sa.Column(
            "issue_revision",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column(
            "attempt_count",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("case_id", sa.String(length=128), nullable=True),
        sa.Column("diagnostic_summary", postgresql.JSONB(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint(
            "ingest_issue_id",
            name="pk_dts_ingest_issues",
        ),
        sa.UniqueConstraint(
            "connector_delivery_identity_hash",
            "payload_hmac",
            "hmac_key_version",
            name="uq_dts_ingest_issue_delivery_payload",
        ),
        sa.CheckConstraint(
            "ingest_issue_id ~ '^[0-9a-f]{64}$' "
            "AND connector_delivery_identity_hash ~ '^[0-9a-f]{64}$' "
            "AND payload_hmac ~ '^[0-9a-f]{64}$' "
            "AND btrim(hmac_key_version) <> ''",
            name="ck_dts_ingest_issue_identity",
        ),
        sa.CheckConstraint(
            "source_region IS NULL OR source_region IN ('dom', 'ovs')",
            name="ck_dts_ingest_issue_region",
        ),
        sa.CheckConstraint(
            "partition_id IS NULL OR partition_id >= 0",
            name="ck_dts_ingest_issue_partition",
        ),
        sa.CheckConstraint(
            "offset_value IS NULL OR offset_value >= 0",
            name="ck_dts_ingest_issue_offset",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(current_error_codes) = 'array' "
            "AND jsonb_array_length(current_error_codes) > 0",
            name="ck_dts_ingest_issue_errors",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(diagnostic_summary) = 'object'",
            name="ck_dts_ingest_issue_diagnostic",
        ),
        sa.CheckConstraint(
            "issue_revision >= 1 AND attempt_count >= 1",
            name="ck_dts_ingest_issue_versions",
        ),
        sa.CheckConstraint(
            "(status = 'OPEN' AND resolved_at IS NULL) "
            "OR (status = 'RESOLVED' AND resolved_at IS NOT NULL)",
            name="ck_dts_ingest_issue_status",
        ),
        schema="public",
    )
    op.create_index(
        "ix_dts_ingest_issues_open_seen",
        "dts_ingest_issues",
        ["last_seen_at", "ingest_issue_id"],
        unique=False,
        schema="public",
        postgresql_where=sa.text("status = 'OPEN'"),
    )


def _install_identity_helpers() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.dts_broker_epoch_id_v2(
            source_region text,
            topic text,
            partition_id integer,
            stream_generation_id text,
            epoch_opening_id text
        )
        RETURNS text
        LANGUAGE sql
        IMMUTABLE
        STRICT
        PARALLEL SAFE
        SET search_path = pg_catalog, public
        AS $function$
            SELECT 'epoch:v1:' || encode(
                sha256(
                    convert_to(
                        '{"epoch_opening_id":'
                        || to_jsonb(epoch_opening_id)::text
                        || ',"partition_id":' || partition_id::text
                        || ',"source_region":'
                        || to_jsonb(source_region)::text
                        || ',"stream_generation_id":'
                        || to_jsonb(stream_generation_id)::text
                        || ',"topic":' || to_jsonb(topic)::text
                        || '}',
                        'UTF8'
                    )
                ),
                'hex'
            )
        $function$;

        CREATE FUNCTION public.dts_normalize_initial_broker_epoch_vector_v2(
            routes jsonb
        )
        RETURNS jsonb
        LANGUAGE plpgsql
        IMMUTABLE
        STRICT
        PARALLEL SAFE
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE
            route_item jsonb;
            normalized_item jsonb;
            normalized_routes jsonb := '[]'::jsonb;
            normalized_vector jsonb;
            partition_value numeric;
            offset_value numeric;
            expected_epoch_id text;
            duplicate_count integer;
        BEGIN
            IF jsonb_typeof(routes) IS DISTINCT FROM 'array'
               OR jsonb_array_length(routes) = 0 THEN
                RAISE EXCEPTION
                    'INITIAL_EPOCH_BOOTSTRAP_CONFLICT:INVALID_ROUTE_VECTOR'
                    USING ERRCODE = '23514';
            END IF;

            FOR route_item IN
                SELECT value FROM jsonb_array_elements(routes)
            LOOP
                IF jsonb_typeof(route_item) IS DISTINCT FROM 'object'
                   OR NOT route_item ?& ARRAY[
                        'source_region',
                        'topic',
                        'partition_id',
                        'current_next_offset',
                        'consumer_group',
                        'stream_generation_id',
                        'epoch_opening_id',
                        'source_partition_epoch_id'
                   ]
                   OR (
                        SELECT count(*) FROM jsonb_object_keys(route_item)
                   ) <> 8
                   OR jsonb_typeof(route_item -> 'source_region')
                        IS DISTINCT FROM 'string'
                   OR jsonb_typeof(route_item -> 'topic')
                        IS DISTINCT FROM 'string'
                   OR jsonb_typeof(route_item -> 'partition_id')
                        IS DISTINCT FROM 'number'
                   OR jsonb_typeof(route_item -> 'current_next_offset')
                        IS DISTINCT FROM 'number'
                   OR jsonb_typeof(route_item -> 'consumer_group')
                        IS DISTINCT FROM 'string'
                   OR jsonb_typeof(route_item -> 'stream_generation_id')
                        IS DISTINCT FROM 'string'
                   OR jsonb_typeof(route_item -> 'epoch_opening_id')
                        IS DISTINCT FROM 'string'
                   OR jsonb_typeof(route_item -> 'source_partition_epoch_id')
                        IS DISTINCT FROM 'string' THEN
                    RAISE EXCEPTION
                        'INITIAL_EPOCH_BOOTSTRAP_CONFLICT:INVALID_ROUTE_VECTOR'
                        USING ERRCODE = '23514';
                END IF;

                BEGIN
                    partition_value := (route_item ->> 'partition_id')::numeric;
                    offset_value :=
                        (route_item ->> 'current_next_offset')::numeric;
                EXCEPTION WHEN OTHERS THEN
                    RAISE EXCEPTION
                        'INITIAL_EPOCH_BOOTSTRAP_CONFLICT:INVALID_ROUTE_VECTOR'
                        USING ERRCODE = '23514';
                END;

                IF route_item ->> 'source_region' NOT IN ('dom', 'ovs')
                   OR btrim(route_item ->> 'topic') = ''
                   OR length(route_item ->> 'topic') > 512
                   OR partition_value <> trunc(partition_value)
                   OR partition_value < 0
                   OR partition_value > 2147483647
                   OR offset_value <> trunc(offset_value)
                   OR offset_value < 0
                   OR offset_value > 9223372036854775807
                   OR btrim(route_item ->> 'consumer_group') = ''
                   OR length(route_item ->> 'consumer_group') > 256
                   OR btrim(route_item ->> 'stream_generation_id') = ''
                   OR btrim(route_item ->> 'epoch_opening_id') = ''
                   OR btrim(route_item ->> 'source_partition_epoch_id') = ''
                   OR length(
                        route_item ->> 'source_partition_epoch_id'
                   ) > 160 THEN
                    RAISE EXCEPTION
                        'INITIAL_EPOCH_BOOTSTRAP_CONFLICT:INVALID_ROUTE_VECTOR'
                        USING ERRCODE = '23514';
                END IF;

                expected_epoch_id := public.dts_broker_epoch_id_v2(
                    route_item ->> 'source_region',
                    route_item ->> 'topic',
                    partition_value::integer,
                    route_item ->> 'stream_generation_id',
                    route_item ->> 'epoch_opening_id'
                );
                IF route_item ->> 'source_partition_epoch_id'
                    IS DISTINCT FROM expected_epoch_id THEN
                    RAISE EXCEPTION
                        'INITIAL_EPOCH_BOOTSTRAP_CONFLICT:EPOCH_ID_MISMATCH'
                        USING ERRCODE = '23514';
                END IF;

                normalized_item := jsonb_build_object(
                    'source_region', route_item ->> 'source_region',
                    'topic', route_item ->> 'topic',
                    'partition_id', partition_value::integer,
                    'current_next_offset', offset_value::bigint,
                    'consumer_group', route_item ->> 'consumer_group',
                    'stream_generation_id',
                        route_item ->> 'stream_generation_id',
                    'epoch_opening_id', route_item ->> 'epoch_opening_id',
                    'source_partition_epoch_id', expected_epoch_id
                );
                normalized_routes := normalized_routes
                    || jsonb_build_array(normalized_item);
            END LOOP;

            SELECT count(*)
            INTO duplicate_count
            FROM (
                SELECT value ->> 'source_region',
                       value ->> 'topic',
                       value ->> 'partition_id'
                FROM jsonb_array_elements(normalized_routes)
                GROUP BY 1, 2, 3
                HAVING count(*) > 1
            ) AS duplicate_routes;
            IF duplicate_count <> 0 THEN
                RAISE EXCEPTION
                    'INITIAL_EPOCH_BOOTSTRAP_CONFLICT:DUPLICATE_ROUTE'
                    USING ERRCODE = '23514';
            END IF;

            SELECT jsonb_agg(
                       value
                       ORDER BY (value ->> 'source_region') COLLATE "C",
                                (value ->> 'topic') COLLATE "C",
                                (value ->> 'partition_id')::integer
                   )
            INTO normalized_vector
            FROM jsonb_array_elements(normalized_routes);
            RETURN normalized_vector;
        END
        $function$;

        CREATE FUNCTION public.dts_initial_broker_epoch_vector_hash_v2(
            routes jsonb
        )
        RETURNS text
        LANGUAGE sql
        IMMUTABLE
        STRICT
        PARALLEL SAFE
        SET search_path = pg_catalog, public
        AS $function$
            WITH normalized AS (
                SELECT public.dts_normalize_initial_broker_epoch_vector_v2(
                    routes
                ) AS route_vector
            ),
            canonical AS (
                SELECT '[' || string_agg(
                    '{"consumer_group":'
                    || to_jsonb(value ->> 'consumer_group')::text
                    || ',"current_next_offset":'
                    || (value ->> 'current_next_offset')::bigint::text
                    || ',"epoch_opening_id":'
                    || to_jsonb(value ->> 'epoch_opening_id')::text
                    || ',"partition_id":'
                    || (value ->> 'partition_id')::integer::text
                    || ',"source_partition_epoch_id":'
                    || to_jsonb(
                        value ->> 'source_partition_epoch_id'
                    )::text
                    || ',"source_region":'
                    || to_jsonb(value ->> 'source_region')::text
                    || ',"stream_generation_id":'
                    || to_jsonb(value ->> 'stream_generation_id')::text
                    || ',"topic":' || to_jsonb(value ->> 'topic')::text
                    || '}',
                    ',' ORDER BY
                        (value ->> 'source_region') COLLATE "C",
                        (value ->> 'topic') COLLATE "C",
                        (value ->> 'partition_id')::integer
                ) || ']' AS payload
                FROM normalized,
                     jsonb_array_elements(normalized.route_vector)
            )
            SELECT encode(
                sha256(convert_to(canonical.payload, 'UTF8')),
                'hex'
            )
            FROM canonical
        $function$;

        CREATE FUNCTION public.dts_ingest_issue_id_v2(
            connector_delivery_identity_hash text,
            payload_hmac text,
            hmac_key_version text
        )
        RETURNS text
        LANGUAGE sql
        IMMUTABLE
        STRICT
        PARALLEL SAFE
        SET search_path = pg_catalog, public
        AS $function$
            SELECT encode(
                sha256(
                    convert_to(
                        '{"connector_delivery_identity_hash":'
                        || to_jsonb(
                            connector_delivery_identity_hash
                        )::text
                        || ',"hmac_key_version":'
                        || to_jsonb(hmac_key_version)::text
                        || ',"payload_hmac":'
                        || to_jsonb(payload_hmac)::text
                        || '}',
                        'UTF8'
                    )
                ),
                'hex'
            )
        $function$;

        REVOKE ALL ON FUNCTION
            public.dts_broker_epoch_id_v2(text,text,integer,text,text),
            public.dts_normalize_initial_broker_epoch_vector_v2(jsonb),
            public.dts_initial_broker_epoch_vector_hash_v2(jsonb),
            public.dts_ingest_issue_id_v2(text,text,text)
        FROM PUBLIC, tit_growth_app, tit_dts_ingest_runtime;
        """
    )
    op.create_check_constraint(
        "ck_dts_ingest_issue_derived_id",
        "dts_ingest_issues",
        "ingest_issue_id = public.dts_ingest_issue_id_v2("
        "connector_delivery_identity_hash, payload_hmac, hmac_key_version)",
        schema="public",
    )


def _install_guards() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.guard_dts_pipeline_control_v2()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $function$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'DTS_PIPELINE_CONTROL_DELETE_FORBIDDEN'
                    USING ERRCODE = '42501';
            END IF;
            IF TG_OP = 'INSERT' THEN
                IF current_setting(
                    'tit.dts_initial_epoch_bootstrap', true
                ) IS DISTINCT FROM 'on' THEN
                    RAISE EXCEPTION
                        'DTS_PIPELINE_CONTROL_INSERT_REQUIRES_BOOTSTRAP'
                        USING ERRCODE = '42501';
                END IF;
                RETURN NEW;
            END IF;
            IF NEW.control_id IS DISTINCT FROM OLD.control_id
               OR NEW.consumer_group IS DISTINCT FROM OLD.consumer_group
               OR NEW.initial_h0_vector IS DISTINCT FROM OLD.initial_h0_vector
               OR NEW.initial_h0_vector_hash IS DISTINCT FROM
                    OLD.initial_h0_vector_hash
               OR NEW.initial_h0_bootstrap_run_id IS DISTINCT FROM
                    OLD.initial_h0_bootstrap_run_id THEN
                RAISE EXCEPTION 'DTS_PIPELINE_INITIAL_H0_IMMUTABLE'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.row_version <> OLD.row_version + 1
               OR NEW.projection_generation < OLD.projection_generation THEN
                RAISE EXCEPTION 'DTS_PIPELINE_CONTROL_VERSION_INVALID'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END
        $function$;

        CREATE TRIGGER guard_dts_pipeline_control_v2
        BEFORE INSERT OR UPDATE OR DELETE
        ON public.dts_pipeline_control
        FOR EACH ROW
        EXECUTE FUNCTION public.guard_dts_pipeline_control_v2();

        CREATE FUNCTION public.guard_dts_pipeline_bootstrap_audit_v2()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $function$
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                RAISE EXCEPTION 'DTS_PIPELINE_BOOTSTRAP_AUDIT_IMMUTABLE'
                    USING ERRCODE = '42501';
            END IF;
            IF current_setting(
                'tit.dts_initial_epoch_bootstrap', true
            ) IS DISTINCT FROM 'on' THEN
                RAISE EXCEPTION
                    'DTS_PIPELINE_BOOTSTRAP_AUDIT_REQUIRES_BOOTSTRAP'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END
        $function$;

        CREATE TRIGGER guard_dts_pipeline_bootstrap_audit_v2
        BEFORE INSERT OR UPDATE OR DELETE
        ON public.dts_pipeline_bootstrap_audits
        FOR EACH ROW
        EXECUTE FUNCTION public.guard_dts_pipeline_bootstrap_audit_v2();

        CREATE FUNCTION public.guard_dts_ingest_issue_v2()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $function$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'DTS_INGEST_ISSUE_DELETE_FORBIDDEN'
                    USING ERRCODE = '42501';
            END IF;
            IF TG_OP = 'INSERT' THEN
                RETURN NEW;
            END IF;
            IF NEW.ingest_issue_id IS DISTINCT FROM OLD.ingest_issue_id
               OR NEW.connector_delivery_identity_hash IS DISTINCT FROM
                    OLD.connector_delivery_identity_hash
               OR NEW.payload_hmac IS DISTINCT FROM OLD.payload_hmac
               OR NEW.hmac_key_version IS DISTINCT FROM OLD.hmac_key_version
               OR NEW.first_seen_at IS DISTINCT FROM OLD.first_seen_at THEN
                RAISE EXCEPTION 'DTS_INGEST_ISSUE_IDENTITY_IMMUTABLE'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.issue_revision <> OLD.issue_revision + 1
               OR NEW.attempt_count < OLD.attempt_count
               OR NEW.last_seen_at < OLD.last_seen_at THEN
                RAISE EXCEPTION 'DTS_INGEST_ISSUE_VERSION_INVALID'
                    USING ERRCODE = '23514';
            END IF;
            IF OLD.status = 'OPEN'
               AND NEW.status = 'RESOLVED'
               AND current_setting(
                    'tit.dts_ingest_issue_resolve_authorized', true
               ) IS DISTINCT FROM 'on' THEN
                RAISE EXCEPTION 'DTS_INGEST_ISSUE_RESOLVE_REQUIRES_LEDGER_TX'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END
        $function$;

        CREATE TRIGGER guard_dts_ingest_issue_v2
        BEFORE UPDATE OR DELETE
        ON public.dts_ingest_issues
        FOR EACH ROW
        EXECUTE FUNCTION public.guard_dts_ingest_issue_v2();

        CREATE FUNCTION public.guard_dts_v2_checkpoint_identity()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE
            actor_name text := COALESCE(
                NULLIF(current_setting('role', true), 'none'),
                session_user
            );
        BEGIN
            IF actor_name <> 'tit_dts_ingest_runtime' THEN
                RETURN NEW;
            END IF;
            IF TG_OP = 'INSERT' THEN
                IF EXISTS (
                    SELECT 1 FROM public.dts_pipeline_control
                    WHERE control_id = 'PRIMARY'
                ) OR NEW.source_partition_epoch_id IS NOT NULL
                  OR NEW.consumer_group IS NOT NULL
                  OR NEW.checkpoint_row_version IS NOT NULL
                  OR NEW.is_current_epoch IS NOT NULL THEN
                    RAISE EXCEPTION
                        'DTS_V2_CHECKPOINT_ROUTE_REQUIRES_CONTROL_PLANE'
                        USING ERRCODE = '42501';
                END IF;
                RETURN NEW;
            END IF;
            IF NEW.source_partition_epoch_id IS DISTINCT FROM
                    OLD.source_partition_epoch_id
               OR NEW.consumer_group IS DISTINCT FROM OLD.consumer_group
               OR NEW.is_current_epoch IS DISTINCT FROM OLD.is_current_epoch
               OR NEW.checkpoint_row_version < OLD.checkpoint_row_version THEN
                RAISE EXCEPTION 'DTS_V2_CHECKPOINT_IDENTITY_IMMUTABLE'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END
        $function$;

        CREATE TRIGGER guard_dts_v2_checkpoint_identity
        BEFORE INSERT OR UPDATE
        ON public.dts_ingest_checkpoints
        FOR EACH ROW
        EXECUTE FUNCTION public.guard_dts_v2_checkpoint_identity();

        REVOKE ALL ON FUNCTION
            public.guard_dts_pipeline_control_v2(),
            public.guard_dts_pipeline_bootstrap_audit_v2(),
            public.guard_dts_ingest_issue_v2(),
            public.guard_dts_v2_checkpoint_identity()
        FROM PUBLIC, tit_growth_app, tit_dts_ingest_runtime;
        """
    )


def _install_bootstrap_function() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.bootstrap_initial_broker_epoch_v2(
            p_bootstrap_run_id text,
            p_consumer_group text,
            p_routes jsonb,
            p_expected_vector_hash text
        )
        RETURNS jsonb
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE
            normalized_vector jsonb;
            actual_vector_hash text;
            route_item jsonb;
            expected_route_count integer;
            control_count integer;
            audit_count integer;
            actor_name text := COALESCE(
                NULLIF(current_setting('role', true), 'none'),
                session_user
            );
            updated_count integer;
        BEGIN
            IF p_bootstrap_run_id IS NULL
               OR btrim(p_bootstrap_run_id) = ''
               OR length(p_bootstrap_run_id) > 160
               OR p_consumer_group IS NULL
               OR btrim(p_consumer_group) = ''
               OR length(p_consumer_group) > 256
               OR p_expected_vector_hash IS NULL
               OR p_expected_vector_hash !~ '^[0-9a-f]{64}$' THEN
                RAISE EXCEPTION
                    'INITIAL_EPOCH_BOOTSTRAP_CONFLICT:INVALID_COMMAND'
                    USING ERRCODE = '23514';
            END IF;

            normalized_vector :=
                public.dts_normalize_initial_broker_epoch_vector_v2(p_routes);
            actual_vector_hash :=
                public.dts_initial_broker_epoch_vector_hash_v2(
                    normalized_vector
                );
            IF actual_vector_hash IS DISTINCT FROM p_expected_vector_hash THEN
                RAISE EXCEPTION
                    'INITIAL_EPOCH_BOOTSTRAP_CONFLICT:MANIFEST_HASH_MISMATCH'
                    USING ERRCODE = '23514';
            END IF;
            expected_route_count := jsonb_array_length(normalized_vector);

            LOCK TABLE
                public.dts_ingest_checkpoints,
                public.dts_source_partition_epochs,
                public.dts_pipeline_control,
                public.dts_pipeline_bootstrap_audits
            IN SHARE ROW EXCLUSIVE MODE;

            SELECT count(*) INTO control_count
            FROM public.dts_pipeline_control;
            SELECT count(*) INTO audit_count
            FROM public.dts_pipeline_bootstrap_audits;

            IF control_count <> 0 OR audit_count <> 0 THEN
                IF control_count = 1
                   AND audit_count = 1
                   AND EXISTS (
                        SELECT 1
                        FROM public.dts_pipeline_control AS control
                        JOIN public.dts_pipeline_bootstrap_audits AS audit
                          ON audit.control_id = control.control_id
                        WHERE control.control_id = 'PRIMARY'
                          AND control.mode = 'V1_COMPAT_DUAL_CAPTURE'
                          AND control.row_version = 1
                          AND control.projection_generation = 0
                          AND control.consumer_group = p_consumer_group
                          AND control.initial_h0_vector = normalized_vector
                          AND control.initial_h0_vector_hash =
                                actual_vector_hash
                          AND control.initial_h0_bootstrap_run_id =
                                p_bootstrap_run_id
                          AND audit.bootstrap_run_id = p_bootstrap_run_id
                          AND audit.consumer_group = p_consumer_group
                          AND audit.h0_vector = normalized_vector
                          AND audit.h0_vector_hash = actual_vector_hash
                          AND audit.route_count = expected_route_count
                          AND audit.result_status = 'APPLIED'
                   )
                   AND (
                        SELECT count(*)
                        FROM public.dts_ingest_checkpoints
                   ) = expected_route_count
                   AND (
                        SELECT count(*)
                        FROM public.dts_source_partition_epochs
                        WHERE epoch_kind = 'BROKER'
                   ) = expected_route_count
                   AND NOT EXISTS (
                        SELECT 1
                        FROM jsonb_array_elements(normalized_vector)
                            AS route(value)
                        LEFT JOIN public.dts_ingest_checkpoints AS checkpoint
                          ON checkpoint.source_region =
                                route.value ->> 'source_region'
                         AND checkpoint.topic = route.value ->> 'topic'
                         AND checkpoint.partition_id =
                                (route.value ->> 'partition_id')::integer
                        LEFT JOIN public.dts_source_partition_epochs AS epoch
                          ON epoch.source_region =
                                route.value ->> 'source_region'
                         AND epoch.source_partition_epoch_id =
                                route.value ->> 'source_partition_epoch_id'
                         AND epoch.topic = route.value ->> 'topic'
                         AND epoch.partition_id =
                                (route.value ->> 'partition_id')::integer
                        WHERE checkpoint.source_region IS NULL
                           OR checkpoint.next_offset IS DISTINCT FROM
                                (route.value ->>
                                    'current_next_offset')::bigint
                           OR checkpoint.source_partition_epoch_id IS DISTINCT
                                FROM route.value ->>
                                    'source_partition_epoch_id'
                           OR checkpoint.consumer_group IS DISTINCT FROM
                                route.value ->> 'consumer_group'
                           OR checkpoint.checkpoint_row_version IS DISTINCT
                                FROM 1
                           OR checkpoint.is_current_epoch IS DISTINCT FROM true
                           OR epoch.source_region IS NULL
                           OR epoch.epoch_kind IS DISTINCT FROM 'BROKER'
                           OR epoch.status IS DISTINCT FROM 'ACTIVE'
                           OR epoch.stream_generation_id IS DISTINCT FROM
                                route.value ->> 'stream_generation_id'
                           OR epoch.epoch_opening_id IS DISTINCT FROM
                                route.value ->> 'epoch_opening_id'
                           OR epoch.epoch_sequence IS DISTINCT FROM 1
                           OR epoch.predecessor_epoch_id IS NOT NULL
                           OR epoch.start_offset IS DISTINCT FROM
                                (route.value ->>
                                    'current_next_offset')::bigint
                           OR epoch.v2_epoch_bootstrap_floor IS DISTINCT FROM
                                (route.value ->>
                                    'current_next_offset')::bigint
                           OR epoch.activation_mode IS DISTINCT FROM
                                'H0_BOOTSTRAP'
                           OR epoch.activation_manifest_hash IS NOT NULL
                   ) THEN
                    RETURN jsonb_build_object(
                        'status', 'NOOP',
                        'route_count', expected_route_count,
                        'vector_hash', actual_vector_hash
                    );
                END IF;
                RAISE EXCEPTION
                    'INITIAL_EPOCH_BOOTSTRAP_CONFLICT:PARTIAL_OR_DIFFERENT_STATE'
                    USING ERRCODE = '23514';
            END IF;

            IF EXISTS (
                SELECT 1
                FROM public.dts_source_partition_epochs
                WHERE epoch_kind = 'BROKER'
            )
               OR (
                    SELECT count(*)
                    FROM public.dts_ingest_checkpoints
               ) <> expected_route_count
               OR EXISTS (
                    SELECT 1
                    FROM jsonb_array_elements(normalized_vector)
                        AS route(value)
                    LEFT JOIN public.dts_ingest_checkpoints AS checkpoint
                      ON checkpoint.source_region =
                            route.value ->> 'source_region'
                     AND checkpoint.topic = route.value ->> 'topic'
                     AND checkpoint.partition_id =
                            (route.value ->> 'partition_id')::integer
                    WHERE checkpoint.source_region IS NULL
                       OR checkpoint.next_offset IS DISTINCT FROM
                            (route.value ->> 'current_next_offset')::bigint
                       OR checkpoint.source_partition_epoch_id IS NOT NULL
                       OR checkpoint.consumer_group IS NOT NULL
                       OR checkpoint.checkpoint_row_version IS NOT NULL
                       OR checkpoint.is_current_epoch IS NOT NULL
               ) THEN
                RAISE EXCEPTION
                    'INITIAL_EPOCH_BOOTSTRAP_CONFLICT:CHECKPOINT_VECTOR_MISMATCH'
                    USING ERRCODE = '23514';
            END IF;

            PERFORM set_config(
                'tit.dts_initial_epoch_bootstrap',
                'on',
                true
            );
            FOR route_item IN
                SELECT value FROM jsonb_array_elements(normalized_vector)
            LOOP
                INSERT INTO public.dts_source_partition_epochs (
                    source_region,
                    source_partition_epoch_id,
                    topic,
                    partition_id,
                    epoch_kind,
                    status,
                    stream_generation_id,
                    epoch_opening_id,
                    epoch_sequence,
                    predecessor_epoch_id,
                    start_offset,
                    v2_epoch_bootstrap_floor,
                    activation_mode,
                    activation_manifest_hash,
                    activated_at,
                    row_version
                ) VALUES (
                    route_item ->> 'source_region',
                    route_item ->> 'source_partition_epoch_id',
                    route_item ->> 'topic',
                    (route_item ->> 'partition_id')::integer,
                    'BROKER',
                    'ACTIVE',
                    route_item ->> 'stream_generation_id',
                    route_item ->> 'epoch_opening_id',
                    1,
                    NULL,
                    (route_item ->> 'current_next_offset')::bigint,
                    (route_item ->> 'current_next_offset')::bigint,
                    'H0_BOOTSTRAP',
                    NULL,
                    clock_timestamp(),
                    1
                );

                UPDATE public.dts_ingest_checkpoints
                SET source_partition_epoch_id =
                        route_item ->> 'source_partition_epoch_id',
                    consumer_group = route_item ->> 'consumer_group',
                    checkpoint_row_version = 1,
                    is_current_epoch = true,
                    updated_at = clock_timestamp()
                WHERE source_region = route_item ->> 'source_region'
                  AND topic = route_item ->> 'topic'
                  AND partition_id =
                        (route_item ->> 'partition_id')::integer
                  AND next_offset =
                        (route_item ->> 'current_next_offset')::bigint
                  AND source_partition_epoch_id IS NULL
                  AND dts_ingest_checkpoints.consumer_group IS NULL
                  AND checkpoint_row_version IS NULL
                  AND is_current_epoch IS NULL;
                GET DIAGNOSTICS updated_count = ROW_COUNT;
                IF updated_count <> 1 THEN
                    RAISE EXCEPTION
                        'INITIAL_EPOCH_BOOTSTRAP_CONFLICT:CHECKPOINT_CHANGED'
                        USING ERRCODE = '23514';
                END IF;
            END LOOP;

            INSERT INTO public.dts_pipeline_control (
                control_id,
                mode,
                row_version,
                projection_generation,
                consumer_group,
                initial_h0_vector,
                initial_h0_vector_hash,
                initial_h0_bootstrap_run_id,
                time_catchup_status,
                changed_at,
                changed_by
            ) VALUES (
                'PRIMARY',
                'V1_COMPAT_DUAL_CAPTURE',
                1,
                0,
                p_consumer_group,
                normalized_vector,
                actual_vector_hash,
                p_bootstrap_run_id,
                'NOT_REQUIRED',
                clock_timestamp(),
                actor_name
            );

            INSERT INTO public.dts_pipeline_bootstrap_audits (
                bootstrap_run_id,
                control_id,
                consumer_group,
                h0_vector,
                h0_vector_hash,
                route_count,
                result_status,
                executed_at,
                executed_by
            ) VALUES (
                p_bootstrap_run_id,
                'PRIMARY',
                p_consumer_group,
                normalized_vector,
                actual_vector_hash,
                expected_route_count,
                'APPLIED',
                clock_timestamp(),
                actor_name
            );
            PERFORM set_config(
                'tit.dts_initial_epoch_bootstrap',
                'off',
                true
            );

            RETURN jsonb_build_object(
                'status', 'APPLIED',
                'route_count', expected_route_count,
                'vector_hash', actual_vector_hash
            );
        END
        $function$;

        REVOKE ALL ON FUNCTION
            public.bootstrap_initial_broker_epoch_v2(text,text,jsonb,text)
        FROM PUBLIC, tit_growth_app, tit_dts_ingest_runtime;
        """
    )


def _apply_acl_and_comments() -> None:
    op.execute(
        r"""
        COMMENT ON TABLE public.dts_pipeline_control IS
            'Protected singleton DTS mode and immutable initial H0 vector; created only by whole-vector bootstrap.';
        COMMENT ON TABLE public.dts_source_partition_epochs IS
            'DTS v2 epoch registry; initial ACTIVE BROKER rows are created only by the whole-vector H0 bootstrap function.';
        COMMENT ON TABLE public.dts_pipeline_bootstrap_audits IS
            'Append-only initial broker epoch bootstrap audit; one successful PRIMARY H0 command.';
        COMMENT ON TABLE public.dts_ingest_issues IS
            'Sanitized invalid-delivery work items; raw payload, raw student identity, and HMAC keys are prohibited.';
        COMMENT ON FUNCTION
            public.bootstrap_initial_broker_epoch_v2(text,text,jsonb,text) IS
            'p_consumer_group is the bootstrap fleet identity; each route consumer_group is independently attested in p_routes.';

        REVOKE ALL PRIVILEGES ON TABLE
            public.dts_pipeline_control,
            public.dts_pipeline_bootstrap_audits,
            public.dts_ingest_issues
        FROM PUBLIC, tit_growth_app, tit_dts_ingest_runtime;

        GRANT SELECT ON TABLE public.dts_pipeline_control
        TO tit_growth_app, tit_dts_ingest_runtime;

        DO $optional_epoch_control_acl$
        BEGIN
            IF to_regrole('tit_teacher_crud') IS NOT NULL THEN
                EXECUTE 'REVOKE ALL PRIVILEGES ON TABLE '
                    'public.dts_pipeline_control, '
                    'public.dts_pipeline_bootstrap_audits, '
                    'public.dts_ingest_issues FROM tit_teacher_crud';
                EXECUTE 'REVOKE ALL ON FUNCTION '
                    'public.bootstrap_initial_broker_epoch_v2('
                    'text,text,jsonb,text) FROM tit_teacher_crud';
            END IF;
        END
        $optional_epoch_control_acl$;
        """
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    _create_control_tables()
    _install_identity_helpers()
    _install_guards()
    _install_bootstrap_function()
    _apply_acl_and_comments()


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    op.execute(
        r"""
        LOCK TABLE
            public.dts_ingest_checkpoints,
            public.dts_source_partition_epochs,
            public.dts_pipeline_control,
            public.dts_pipeline_bootstrap_audits,
            public.dts_ingest_issues
        IN ACCESS EXCLUSIVE MODE;

        DO $dts_v2_epoch_control_downgrade_guard$
        BEGIN
            IF EXISTS (SELECT 1 FROM public.dts_pipeline_control LIMIT 1)
               OR EXISTS (
                    SELECT 1
                    FROM public.dts_pipeline_bootstrap_audits
                    LIMIT 1
               )
               OR EXISTS (SELECT 1 FROM public.dts_ingest_issues LIMIT 1)
               OR EXISTS (
                    SELECT 1
                    FROM public.dts_source_partition_epochs
                    WHERE epoch_kind = 'BROKER'
                      AND activation_mode = 'H0_BOOTSTRAP'
               )
               OR EXISTS (
                    SELECT 1
                    FROM public.dts_ingest_checkpoints
                    WHERE source_partition_epoch_id IS NOT NULL
                       OR consumer_group IS NOT NULL
                       OR checkpoint_row_version IS NOT NULL
                       OR is_current_epoch IS NOT NULL
               ) THEN
                RAISE EXCEPTION
                    'refusing DTS v2 epoch-control downgrade: control data exists';
            END IF;
        END
        $dts_v2_epoch_control_downgrade_guard$;

        DROP TRIGGER IF EXISTS guard_dts_v2_checkpoint_identity
        ON public.dts_ingest_checkpoints;
        DROP TRIGGER IF EXISTS guard_dts_ingest_issue_v2
        ON public.dts_ingest_issues;
        DROP TRIGGER IF EXISTS guard_dts_pipeline_bootstrap_audit_v2
        ON public.dts_pipeline_bootstrap_audits;
        DROP TRIGGER IF EXISTS guard_dts_pipeline_control_v2
        ON public.dts_pipeline_control;

        DROP FUNCTION IF EXISTS
            public.bootstrap_initial_broker_epoch_v2(text,text,jsonb,text);
        DROP FUNCTION IF EXISTS public.guard_dts_v2_checkpoint_identity();
        DROP FUNCTION IF EXISTS public.guard_dts_ingest_issue_v2();
        DROP FUNCTION IF EXISTS
            public.guard_dts_pipeline_bootstrap_audit_v2();
        DROP FUNCTION IF EXISTS public.guard_dts_pipeline_control_v2();
        """
    )

    op.drop_constraint(
        "ck_dts_ingest_issue_derived_id",
        "dts_ingest_issues",
        schema="public",
        type_="check",
    )
    op.drop_index(
        "ix_dts_ingest_issues_open_seen",
        table_name="dts_ingest_issues",
        schema="public",
    )
    op.drop_table("dts_ingest_issues", schema="public")
    op.drop_table("dts_pipeline_bootstrap_audits", schema="public")
    op.drop_table("dts_pipeline_control", schema="public")

    op.execute(
        r"""
        DROP FUNCTION IF EXISTS
            public.dts_ingest_issue_id_v2(text,text,text);
        DROP FUNCTION IF EXISTS
            public.dts_initial_broker_epoch_vector_hash_v2(jsonb);
        DROP FUNCTION IF EXISTS
            public.dts_normalize_initial_broker_epoch_vector_v2(jsonb);
        DROP FUNCTION IF EXISTS
            public.dts_broker_epoch_id_v2(text,text,integer,text,text);
        COMMENT ON TABLE public.dts_source_partition_epochs IS
            'DTS v2 shadow only; no epoch bootstrap or activation path installed';
        """
    )
