"""enforce the v2 Outbox three-state, archive, and recovery protocol.

Revision ID: 20260822_85_outbox_three_state
Revises: 20260822_84_dts_v2_domain_outbox
Create Date: 2026-08-22

This revision is deliberately database-only.  It does not start an Outbox
worker, consume an event, settle cutover coverage, or connect to DTS/Kafka.
Legacy CANCELLED/PARKED rows leave the active queue only when the frozen typed
proof and payload-safety predicates are all true; every other legacy row makes
the upgrade fail closed and leaves the original transaction intact.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260822_85_outbox_three_state"
down_revision: Union[str, None] = "20260822_84a_lesson_region_contract"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


RECOVERY_ROLE = "tit_dts_outbox_recovery_runtime"
CUTOVER_MIGRATION_ROLE = "tit_dts_cutover_migration"
MIGRATION_RUN_ID = "alembic:20260822_85_outbox_three_state"


def _assert_preconditions_and_lock() -> None:
    op.execute(
        r"""
        DO $outbox_v2_preflight$
        BEGIN
            IF to_regclass('public.outbox_events') IS NULL
               OR to_regclass('public.audit_events') IS NULL
               OR to_regclass('public.idempotency_records') IS NULL
               OR to_regclass('public.task_assignments') IS NULL
               OR to_regclass('public.score_entries') IS NULL
               OR to_regprocedure(
                    'public.dts_canonical_json_v1(jsonb)'
                  ) IS NULL
               OR to_regprocedure(
                    'public.dts_canonical_json_sha256_v1(jsonb)'
                  ) IS NULL
               OR to_regprocedure(
                    'public.set_outbox_payload_sha256_v2()'
                  ) IS NULL THEN
                RAISE EXCEPTION 'OUTBOX_V2_SCHEMA_NOT_READY';
            END IF;
            IF NOT EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema='public' AND table_name='outbox_events'
                  AND column_name='payload_sha256' AND is_nullable='NO'
            ) OR NOT EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema='public' AND table_name='outbox_events'
                  AND column_name='recovery_count' AND is_nullable='NO'
            ) OR NOT EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema='public' AND table_name='outbox_events'
                  AND column_name='recovered_at'
            ) OR NOT EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema='public' AND table_name='outbox_events'
                  AND column_name='row_version' AND is_nullable='NO'
            ) THEN
                RAISE EXCEPTION 'OUTBOX_V2_SCHEMA_NOT_READY';
            END IF;
            IF to_regclass('public.outbox_events_legacy_archive') IS NOT NULL
               OR to_regprocedure(
                    'public.recover_outbox_event_v2(text,text,text,bigint,text)'
                  ) IS NOT NULL THEN
                RAISE EXCEPTION 'OUTBOX_V2_THREE_STATE_ALREADY_INSTALLED';
            END IF;
        END
        $outbox_v2_preflight$;

        LOCK TABLE public.outbox_events IN ACCESS EXCLUSIVE MODE;
        """
    )


def _create_archive_table() -> None:
    op.add_column(
        "outbox_events",
        sa.Column("settled_by_run_id", sa.String(length=160), nullable=True),
        schema="public",
    )
    op.create_table(
        "outbox_events_legacy_archive",
        sa.Column("event_id", sa.String(length=512), nullable=False),
        sa.Column("outbox_id", sa.String(length=160), nullable=False),
        sa.Column("aggregate_type", sa.String(length=48), nullable=False),
        sa.Column("aggregate_id", sa.String(length=160), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column(
            "available_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column(
            "recovery_count", sa.BigInteger(), nullable=False
        ),
        sa.Column("recovered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("row_version", sa.BigInteger(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("settled_by_run_id", sa.String(length=160), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "archive_source_row_hash", sa.String(length=64), nullable=False
        ),
        sa.Column("archive_reason", sa.String(length=64), nullable=False),
        sa.Column("proof_type", sa.String(length=64), nullable=False),
        sa.Column("proof_hash", sa.String(length=64), nullable=False),
        sa.Column("migration_run_id", sa.String(length=160), nullable=False),
        sa.Column(
            "archive_audit_event_id", sa.String(length=128), nullable=False
        ),
        sa.Column(
            "archived_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("archived_by", sa.String(length=128), nullable=False),
        sa.PrimaryKeyConstraint(
            "event_id", name="pk_outbox_events_legacy_archive"
        ),
        sa.UniqueConstraint(
            "outbox_id", name="uq_outbox_events_legacy_archive_outbox_id"
        ),
        sa.ForeignKeyConstraint(
            ["archive_audit_event_id"],
            ["public.audit_events.event_id"],
            name="fk_outbox_legacy_archive_audit_event",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "status IN ('CANCELLED','PARKED')",
            name="ck_outbox_legacy_archive_status",
        ),
        sa.CheckConstraint(
            "payload_hash ~ '^[0-9a-f]{64}$' "
            "AND archive_source_row_hash ~ '^[0-9a-f]{64}$' "
            "AND proof_hash ~ '^[0-9a-f]{64}$' "
            "AND payload_hash = public.dts_canonical_json_sha256_v1(payload)",
            name="ck_outbox_legacy_archive_hashes",
        ),
        sa.CheckConstraint(
            "(proof_type='MOCK_SEED_CANCELLED' "
            " AND archive_reason='PROVEN_NON_REAL_MOCK_CANCELLED' "
            " AND status='CANCELLED') OR "
            "(proof_type='MIGRATION_20260729_37_RETRY_PARKED' "
            " AND archive_reason='PROVEN_RETIRED_CONTROL_PARKED' "
            " AND status='PARKED')",
            name="ck_outbox_legacy_archive_proof_reason",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0 AND recovery_count = 0 "
            "AND recovered_at IS NULL AND row_version >= 1 "
            "AND published_at IS NULL AND settled_by_run_id IS NULL "
            "AND btrim(migration_run_id) <> '' "
            "AND btrim(archived_by) <> ''",
            name="ck_outbox_legacy_archive_state",
        ),
        schema="public",
        comment=(
            "Append-only audit archive for legacy Outbox rows proven to be "
            "non-real mock work or retired control work; never active work."
        ),
    )


def _install_legacy_helpers() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.outbox_legacy_timestamp_v1(
            p_value timestamptz
        )
        RETURNS jsonb
        LANGUAGE sql
        IMMUTABLE
        PARALLEL SAFE
        SET search_path = pg_catalog, public
        AS $function$
            SELECT CASE
                WHEN p_value IS NULL THEN 'null'::jsonb
                ELSE to_jsonb(
                    to_char(
                        p_value AT TIME ZONE 'UTC',
                        'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
                    )
                )
            END
        $function$;

        CREATE FUNCTION public.outbox_legacy_json_pointer_escape_v1(
            p_value text
        )
        RETURNS text
        LANGUAGE sql
        IMMUTABLE
        STRICT
        PARALLEL SAFE
        SET search_path = pg_catalog, public
        AS $function$
            SELECT replace(replace(p_value,'~','~0'),'/','~1')
        $function$;

        CREATE FUNCTION public.outbox_legacy_json_depth_v1(p_value jsonb)
        RETURNS integer
        LANGUAGE plpgsql
        IMMUTABLE
        PARALLEL SAFE
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE child jsonb;
        DECLARE child_depth integer;
        DECLARE max_depth integer := 0;
        BEGIN
            IF p_value IS NULL OR jsonb_typeof(p_value) IN (
                'null','string','number','boolean'
            ) THEN
                RETURN 0;
            END IF;
            IF jsonb_typeof(p_value) = 'object' THEN
                FOR child IN SELECT value FROM jsonb_each(p_value) LOOP
                    child_depth := public.outbox_legacy_json_depth_v1(child);
                    max_depth := greatest(max_depth,child_depth);
                END LOOP;
            ELSIF jsonb_typeof(p_value) = 'array' THEN
                FOR child IN SELECT value FROM jsonb_array_elements(p_value)
                LOOP
                    child_depth := public.outbox_legacy_json_depth_v1(child);
                    max_depth := greatest(max_depth,child_depth);
                END LOOP;
            END IF;
            RETURN max_depth + 1;
        END
        $function$;

        CREATE FUNCTION public.outbox_legacy_string_safety_issues_v1(
            p_value jsonb,
            p_pointer text
        )
        RETURNS TABLE(issue text)
        LANGUAGE plpgsql
        IMMUTABLE
        PARALLEL SAFE
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE item record;
        DECLARE string_value text;
        DECLARE child_pointer text;
        BEGIN
            IF p_value IS NULL THEN
                RETURN;
            END IF;
            IF jsonb_typeof(p_value) = 'string' THEN
                string_value := p_value #>> '{}';
                IF string_value ~* (
                    'Bearer[[:space:]]|password=|postgres://|'
                    'postgresql://|mysql://|jdbc:|BEGIN[^\n]{0,64}PRIVATE KEY'
                ) THEN
                    issue := 'CREDENTIAL_PATTERN:' || p_pointer;
                    RETURN NEXT;
                END IF;
                IF string_value ~* '(dom|ovs):v1:' THEN
                    issue := 'STUDENT_TOKEN_NOT_ALLOWED:' || p_pointer;
                    RETURN NEXT;
                END IF;
                RETURN;
            END IF;
            IF jsonb_typeof(p_value) = 'object' THEN
                FOR item IN
                    SELECT key,value FROM jsonb_each(p_value)
                LOOP
                    child_pointer := p_pointer || '/' ||
                        public.outbox_legacy_json_pointer_escape_v1(item.key);
                    RETURN QUERY SELECT nested.issue
                    FROM public.outbox_legacy_string_safety_issues_v1(
                        item.value,child_pointer
                    ) AS nested;
                END LOOP;
            ELSIF jsonb_typeof(p_value) = 'array' THEN
                FOR item IN
                    SELECT value,ordinality
                    FROM jsonb_array_elements(p_value) WITH ORDINALITY
                LOOP
                    child_pointer := p_pointer || '/' ||
                        (item.ordinality - 1)::text;
                    RETURN QUERY SELECT nested.issue
                    FROM public.outbox_legacy_string_safety_issues_v1(
                        item.value,child_pointer
                    ) AS nested;
                END LOOP;
            END IF;
        END
        $function$;

        CREATE FUNCTION public.legacy_outbox_payload_safety_issues_v1(
            p_payload jsonb,
            p_proof_type text
        )
        RETURNS text[]
        LANGUAGE plpgsql
        IMMUTABLE
        PARALLEL SAFE
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE issues text[] := ARRAY[]::text[];
        DECLARE item record;
        DECLARE marker jsonb;
        DECLARE pointer text;
        BEGIN
            IF jsonb_typeof(p_payload) IS DISTINCT FROM 'object' THEN
                issues := array_append(issues,'PAYLOAD_NOT_OBJECT');
                RETURN issues;
            END IF;
            IF octet_length(convert_to(
                public.dts_canonical_json_v1(p_payload),'UTF8'
            )) > 16384 THEN
                issues := array_append(issues,'PAYLOAD_TOO_LARGE');
            END IF;
            IF public.outbox_legacy_json_depth_v1(p_payload) > 2 THEN
                issues := array_append(issues,'PAYLOAD_TOO_DEEP');
            END IF;

            FOR item IN
                SELECT DISTINCT s.issue
                FROM public.outbox_legacy_string_safety_issues_v1(
                    p_payload,''
                ) AS s
            LOOP
                issues := array_append(issues,item.issue);
            END LOOP;

            IF p_proof_type = 'MOCK_SEED_CANCELLED' THEN
                FOR item IN SELECT key,value FROM jsonb_each(p_payload) LOOP
                    pointer := '/' ||
                        public.outbox_legacy_json_pointer_escape_v1(item.key);
                    IF item.key NOT IN (
                        'assignment_id','teacher_id','task_code','task_kind',
                        'from_status','to_status','status_reason_code',
                        'result_code','completed_at','row_version','scenario',
                        'origin','source','source_mode','mock_only',
                        'delivery_disabled','execution_allowed'
                    ) THEN
                        issues := array_append(
                            issues,'KEY_NOT_ALLOWED:' || pointer
                        );
                    ELSIF item.key IN (
                        'from_status','status_reason_code','completed_at'
                    ) AND jsonb_typeof(item.value) = 'null' THEN
                        CONTINUE;
                    ELSIF item.key = 'row_version' THEN
                        IF jsonb_typeof(item.value) <> 'number'
                           OR item.value #>> '{}' !~ '^[1-9][0-9]*$' THEN
                            issues := array_append(
                                issues,'TYPE_INVALID:' || pointer
                            );
                        END IF;
                    ELSIF item.key IN (
                        'mock_only','delivery_disabled','execution_allowed'
                    ) THEN
                        IF jsonb_typeof(item.value) <> 'boolean' THEN
                            issues := array_append(
                                issues,'TYPE_INVALID:' || pointer
                            );
                        END IF;
                    ELSIF item.key = 'completed_at' THEN
                        IF jsonb_typeof(item.value) <> 'string' THEN
                            issues := array_append(
                                issues,'TYPE_INVALID:' || pointer
                            );
                        ELSIF item.value #>> '{}' !~
                            '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:'
                            '[0-9]{2}:[0-9]{2}\.[0-9]{6}(Z|\+00:00)$'
                        THEN
                            issues := array_append(
                                issues,'VALUE_INVALID:' || pointer
                            );
                        END IF;
                    ELSE
                        IF jsonb_typeof(item.value) <> 'string' THEN
                            issues := array_append(
                                issues,'TYPE_INVALID:' || pointer
                            );
                        ELSIF item.value #>> '{}' !~
                            '^[A-Za-z0-9._:-]{1,128}$' THEN
                            issues := array_append(
                                issues,'VALUE_INVALID:' || pointer
                            );
                        END IF;
                    END IF;
                END LOOP;
                IF NOT (p_payload ? 'assignment_id')
                   OR NOT (p_payload ? 'scenario')
                   OR NOT (p_payload ? 'origin')
                   OR NOT (p_payload ? 'source')
                   OR NOT (p_payload ? 'source_mode')
                   OR NOT (p_payload ? 'mock_only')
                   OR NOT (p_payload ? 'delivery_disabled')
                   OR NOT (p_payload ? 'execution_allowed') THEN
                    issues := array_append(issues,'VALUE_INVALID:/');
                END IF;
                IF p_payload->>'scenario' IS DISTINCT FROM
                        'MOCK_SEED_SHARED_TASKS' THEN
                    issues := array_append(
                        issues,'VALUE_INVALID:/scenario'
                    );
                END IF;
                IF p_payload->>'origin' IS DISTINCT FROM 'MOCK_SEED' THEN
                    issues := array_append(issues,'VALUE_INVALID:/origin');
                END IF;
                IF p_payload->>'source' IS DISTINCT FROM 'MOCK_SEED' THEN
                    issues := array_append(issues,'VALUE_INVALID:/source');
                END IF;
                IF p_payload->>'source_mode' IS DISTINCT FROM 'MOCK' THEN
                    issues := array_append(
                        issues,'VALUE_INVALID:/source_mode'
                    );
                END IF;
                IF p_payload->'mock_only' IS DISTINCT FROM 'true'::jsonb THEN
                    issues := array_append(
                        issues,'VALUE_INVALID:/mock_only'
                    );
                END IF;
                IF p_payload->'delivery_disabled' IS DISTINCT FROM
                        'true'::jsonb THEN
                    issues := array_append(
                        issues,'VALUE_INVALID:/delivery_disabled'
                    );
                END IF;
                IF p_payload->'execution_allowed' IS DISTINCT FROM
                        'false'::jsonb THEN
                    issues := array_append(
                        issues,'VALUE_INVALID:/execution_allowed'
                    );
                END IF;
            ELSIF p_proof_type =
                    'MIGRATION_20260729_37_RETRY_PARKED' THEN
                FOR item IN SELECT key,value FROM jsonb_each(p_payload) LOOP
                    pointer := '/' ||
                        public.outbox_legacy_json_pointer_escape_v1(item.key);
                    IF item.key NOT IN (
                        'output_id','attempt_count','display_type','actor_id',
                        '_migration_20260729_37'
                    ) THEN
                        issues := array_append(
                            issues,'KEY_NOT_ALLOWED:' || pointer
                        );
                    ELSIF item.key = 'attempt_count' THEN
                        IF jsonb_typeof(item.value) <> 'number'
                           OR item.value #>> '{}' !~ '^(0|[1-9][0-9]*)$'
                        THEN
                            issues := array_append(
                                issues,'TYPE_INVALID:' || pointer
                            );
                        END IF;
                    ELSIF item.key = '_migration_20260729_37' THEN
                        IF jsonb_typeof(item.value) <> 'object' THEN
                            issues := array_append(
                                issues,'TYPE_INVALID:' || pointer
                            );
                        END IF;
                    ELSE
                        IF jsonb_typeof(item.value) <> 'string' THEN
                            issues := array_append(
                                issues,'TYPE_INVALID:' || pointer
                            );
                        ELSIF item.value #>> '{}' !~
                                '^[A-Za-z0-9._:-]+$'
                           OR length(item.value #>> '{}') NOT BETWEEN 1 AND 256
                        THEN
                            issues := array_append(
                                issues,'VALUE_INVALID:' || pointer
                            );
                        END IF;
                    END IF;
                END LOOP;
                IF NOT (p_payload ? 'output_id')
                   OR NOT (p_payload ? 'attempt_count')
                   OR NOT (p_payload ? 'display_type')
                   OR NOT (p_payload ? 'actor_id')
                   OR NOT (p_payload ? '_migration_20260729_37') THEN
                    issues := array_append(issues,'VALUE_INVALID:/');
                END IF;
                marker := p_payload->'_migration_20260729_37';
                IF jsonb_typeof(marker) = 'object' THEN
                    FOR item IN SELECT key,value FROM jsonb_each(marker) LOOP
                        pointer := '/_migration_20260729_37/' ||
                            public.outbox_legacy_json_pointer_escape_v1(
                                item.key
                            );
                        IF item.key NOT IN (
                            'previous_status','previous_last_error'
                        ) THEN
                            issues := array_append(
                                issues,'KEY_NOT_ALLOWED:' || pointer
                            );
                        ELSIF item.key = 'previous_status' THEN
                            IF jsonb_typeof(item.value) <> 'string' THEN
                                issues := array_append(
                                    issues,'TYPE_INVALID:' || pointer
                                );
                            ELSIF item.value #>> '{}' <> 'PENDING' THEN
                                issues := array_append(
                                    issues,'VALUE_INVALID:' || pointer
                                );
                            END IF;
                        ELSIF jsonb_typeof(item.value) <> 'null' THEN
                            IF jsonb_typeof(item.value) <> 'string' THEN
                                issues := array_append(
                                    issues,'TYPE_INVALID:' || pointer
                                );
                            ELSIF item.value #>> '{}' !~
                                    '^[A-Za-z0-9._:-]+$'
                               OR length(item.value #>> '{}')
                                    NOT BETWEEN 1 AND 256
                            THEN
                                issues := array_append(
                                    issues,'VALUE_INVALID:' || pointer
                                );
                            END IF;
                        END IF;
                    END LOOP;
                    IF NOT (marker ? 'previous_status')
                       OR NOT (marker ? 'previous_last_error') THEN
                        issues := array_append(
                            issues,
                            'VALUE_INVALID:/_migration_20260729_37'
                        );
                    END IF;
                END IF;
            END IF;

            SELECT coalesce(
                array_agg(dedup.issue ORDER BY convert_to(dedup.issue,'UTF8')),
                ARRAY[]::text[]
            ) INTO issues
            FROM (SELECT DISTINCT unnest(issues) AS issue) AS dedup;
            RETURN issues;
        END
        $function$;

        CREATE FUNCTION public.outbox_legacy_source_row_hash_v1(
            p_row public.outbox_events
        )
        RETURNS text
        LANGUAGE sql
        IMMUTABLE
        STRICT
        PARALLEL SAFE
        SET search_path = pg_catalog, public
        AS $function$
            SELECT public.dts_canonical_json_sha256_v1(
                jsonb_build_object(
                    'outbox_id',p_row.outbox_id,
                    'event_id',p_row.event_id,
                    'aggregate_type',p_row.aggregate_type,
                    'aggregate_id',p_row.aggregate_id,
                    'event_type',p_row.event_type,
                    'payload_hash',p_row.payload_sha256,
                    'status',p_row.status,
                    'available_at',
                        public.outbox_legacy_timestamp_v1(p_row.available_at),
                    'attempt_count',p_row.attempt_count,
                    'recovery_count',p_row.recovery_count,
                    'last_error',p_row.last_error,
                    'settled_by_run_id',p_row.settled_by_run_id,
                    'created_at',
                        public.outbox_legacy_timestamp_v1(p_row.created_at),
                    'published_at',
                        public.outbox_legacy_timestamp_v1(p_row.published_at)
                )
            )
        $function$;

        REVOKE ALL ON FUNCTION
            public.outbox_legacy_timestamp_v1(timestamptz),
            public.outbox_legacy_json_pointer_escape_v1(text),
            public.outbox_legacy_json_depth_v1(jsonb),
            public.outbox_legacy_string_safety_issues_v1(jsonb,text),
            public.legacy_outbox_payload_safety_issues_v1(jsonb,text),
            public.outbox_legacy_source_row_hash_v1(
                public.outbox_events
            )
        FROM PUBLIC;
        """
    )


def _install_archive_protocol() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.preview_legacy_outbox_archive_v2(
            p_event_id text,
            p_proof_type text
        )
        RETURNS jsonb
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE active public.outbox_events%ROWTYPE;
        DECLARE linked_assignment public.task_assignments%ROWTYPE;
        DECLARE fixed_task_award_count bigint := 0;
        DECLARE source_row_hash text;
        DECLARE safety_issues text[];
        DECLARE proof_document jsonb;
        DECLARE proof_hash text;
        DECLARE eligible boolean := false;
        DECLARE archive_reason text;
        BEGIN
            SELECT * INTO active
            FROM public.outbox_events
            WHERE event_id=p_event_id;
            IF NOT FOUND THEN
                RETURN jsonb_build_object(
                    'archive_source_row_hash',NULL,
                    'payload_hash',NULL,
                    'proof_hash',NULL,
                    'safety_issue_codes','[]'::jsonb,
                    'eligible',false,
                    'archive_reason',NULL,
                    'found',false
                );
            END IF;
            source_row_hash :=
                public.outbox_legacy_source_row_hash_v1(active);
            safety_issues :=
                public.legacy_outbox_payload_safety_issues_v1(
                    active.payload,p_proof_type
                );

            IF p_proof_type = 'MOCK_SEED_CANCELLED' THEN
                SELECT * INTO linked_assignment
                FROM public.task_assignments
                WHERE assignment_id=active.aggregate_id;
                SELECT count(*) INTO fixed_task_award_count
                FROM public.score_entries
                WHERE task_assignment_id=active.aggregate_id
                  AND entry_type='FIXED_TASK_AWARD';
                proof_document := jsonb_build_object(
                    'proof_type',p_proof_type,
                    'event_id',active.event_id,
                    'outbox_id',active.outbox_id,
                    'aggregate_type',active.aggregate_type,
                    'aggregate_id',active.aggregate_id,
                    'event_type',active.event_type,
                    'status',active.status,
                    'published_at',
                        public.outbox_legacy_timestamp_v1(
                            active.published_at
                        ),
                    'settled_by_run_id',active.settled_by_run_id,
                    'last_error',active.last_error,
                    'payload_hash',active.payload_sha256,
                    'scenario',active.payload->'scenario',
                    'origin',active.payload->'origin',
                    'source',active.payload->'source',
                    'source_mode',active.payload->'source_mode',
                    'mock_only',active.payload->'mock_only',
                    'delivery_disabled',
                        active.payload->'delivery_disabled',
                    'execution_allowed',
                        active.payload->'execution_allowed',
                    'payload_assignment_id',
                        active.payload->'assignment_id',
                    'assignment_id',linked_assignment.assignment_id,
                    'assignment_source_mode',linked_assignment.source_mode,
                    'assignment_created_by',linked_assignment.created_by,
                    'fixed_task_award_count',fixed_task_award_count
                );
                proof_hash := public.dts_canonical_json_sha256_v1(
                    proof_document
                );
                archive_reason := 'PROVEN_NON_REAL_MOCK_CANCELLED';
                eligible := coalesce((
                    coalesce(array_length(safety_issues,1),0) = 0
                    AND active.status='CANCELLED'
                    AND active.aggregate_type='TASK_ASSIGNMENT'
                    AND active.published_at IS NULL
                    AND active.settled_by_run_id IS NULL
                    AND active.last_error='MOCK_SEED_DELIVERY_DISABLED'
                    AND active.recovery_count=0
                    AND active.recovered_at IS NULL
                    AND active.payload->>'scenario'=
                        'MOCK_SEED_SHARED_TASKS'
                    AND active.payload->>'origin'='MOCK_SEED'
                    AND active.payload->>'source'='MOCK_SEED'
                    AND active.payload->>'source_mode'='MOCK'
                    AND active.payload->'mock_only'='true'::jsonb
                    AND active.payload->'delivery_disabled'='true'::jsonb
                    AND active.payload->'execution_allowed'='false'::jsonb
                    AND active.payload->>'assignment_id'=active.aggregate_id
                    AND linked_assignment.assignment_id=active.aggregate_id
                    AND linked_assignment.source_mode LIKE 'MOCK%'
                    AND linked_assignment.created_by='MOCK_SEED'
                    AND fixed_task_award_count=0
                ),false);
            ELSIF p_proof_type =
                    'MIGRATION_20260729_37_RETRY_PARKED' THEN
                proof_document := jsonb_build_object(
                    'proof_type',p_proof_type,
                    'event_id',active.event_id,
                    'outbox_id',active.outbox_id,
                    'aggregate_type',active.aggregate_type,
                    'aggregate_id',active.aggregate_id,
                    'event_type',active.event_type,
                    'status',active.status,
                    'published_at',
                        public.outbox_legacy_timestamp_v1(
                            active.published_at
                        ),
                    'settled_by_run_id',active.settled_by_run_id,
                    'last_error',active.last_error,
                    'payload_hash',active.payload_sha256,
                    'output_id',active.payload->'output_id',
                    'attempt_count',active.payload->'attempt_count',
                    'display_type',active.payload->'display_type',
                    'actor_id',active.payload->'actor_id',
                    'marker_previous_status',
                        active.payload #>
                            '{_migration_20260729_37,previous_status}',
                    'marker_previous_last_error',
                        active.payload #>
                            '{_migration_20260729_37,previous_last_error}'
                );
                proof_hash := public.dts_canonical_json_sha256_v1(
                    proof_document
                );
                archive_reason := 'PROVEN_RETIRED_CONTROL_PARKED';
                eligible := coalesce((
                    coalesce(array_length(safety_issues,1),0) = 0
                    AND active.status='PARKED'
                    AND active.event_type=
                        'outbound_output.retry_requested.v1'
                    AND active.last_error=
                        'NO_OUTPUT_CONSUMER_CONFIGURED'
                    AND active.published_at IS NULL
                    AND active.settled_by_run_id IS NULL
                    AND active.recovery_count=0
                    AND active.recovered_at IS NULL
                    AND active.payload #>>
                        '{_migration_20260729_37,previous_status}'=
                        'PENDING'
                    AND active.payload #>
                        '{_migration_20260729_37,previous_last_error}'
                        IS NOT NULL
                ),false);
            ELSE
                safety_issues := ARRAY[]::text[];
                proof_hash := NULL;
                archive_reason := NULL;
                eligible := false;
            END IF;

            RETURN jsonb_build_object(
                'archive_source_row_hash',source_row_hash,
                'payload_hash',active.payload_sha256,
                'proof_hash',proof_hash,
                'safety_issue_codes',to_jsonb(safety_issues),
                'eligible',eligible,
                'archive_reason',archive_reason,
                'found',true
            );
        END
        $function$;

        CREATE OR REPLACE FUNCTION public.guard_outbox_event_update()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE table_owner text;
        DECLARE archived_row public.outbox_events_legacy_archive%ROWTYPE;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                SELECT pg_get_userbyid(relowner) INTO table_owner
                FROM pg_class
                WHERE oid='public.outbox_events'::regclass;
                SELECT * INTO archived_row
                FROM public.outbox_events_legacy_archive
                WHERE event_id=OLD.event_id;
                IF current_user IS DISTINCT FROM table_owner
                   OR NOT FOUND
                   OR archived_row.outbox_id IS DISTINCT FROM OLD.outbox_id
                   OR archived_row.payload_hash IS DISTINCT FROM
                        OLD.payload_sha256
                   OR archived_row.archive_source_row_hash IS DISTINCT FROM
                        public.outbox_legacy_source_row_hash_v1(OLD) THEN
                    RAISE EXCEPTION 'OUTBOX_IMMUTABLE'
                        USING ERRCODE = '42501';
                END IF;
                RETURN OLD;
            END IF;

            IF NEW.outbox_id IS DISTINCT FROM OLD.outbox_id
               OR NEW.event_id IS DISTINCT FROM OLD.event_id
               OR NEW.aggregate_type IS DISTINCT FROM OLD.aggregate_type
               OR NEW.aggregate_id IS DISTINCT FROM OLD.aggregate_id
               OR NEW.event_type IS DISTINCT FROM OLD.event_type
               OR NEW.payload IS DISTINCT FROM OLD.payload
               OR NEW.payload_sha256 IS DISTINCT FROM OLD.payload_sha256
               OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                RAISE EXCEPTION 'OUTBOX_IMMUTABLE'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.row_version IS DISTINCT FROM OLD.row_version + 1 THEN
                RAISE EXCEPTION 'OUTBOX_ROW_VERSION_INVALID'
                    USING ERRCODE = '23514';
            END IF;
            IF OLD.status='PUBLISHED' THEN
                RAISE EXCEPTION 'OUTBOX_STATUS_TRANSITION_INVALID'
                    USING ERRCODE = '23514';
            END IF;
            IF OLD.status='DEAD_LETTER' THEN
                SELECT pg_get_userbyid(proc.proowner) INTO table_owner
                FROM pg_proc AS proc
                WHERE proc.oid=to_regprocedure(
                    'public.recover_outbox_event_v2('
                    'text,text,text,bigint,text)'
                );
                IF NEW.status IS DISTINCT FROM 'PENDING'
                   OR current_user IS DISTINCT FROM table_owner
                   OR NEW.attempt_count IS DISTINCT FROM 0
                   OR NEW.recovery_count IS DISTINCT FROM
                        OLD.recovery_count + 1
                   OR NEW.recovered_at IS NULL
                   OR NEW.recovered_at IS NOT DISTINCT FROM OLD.recovered_at
                   OR NEW.available_at IS DISTINCT FROM
                        transaction_timestamp()
                   OR NEW.last_error IS NOT NULL
                   OR NEW.published_at IS NOT NULL
                   OR NEW.settled_by_run_id IS NOT NULL THEN
                    RAISE EXCEPTION 'OUTBOX_RECOVERY_DENIED'
                        USING ERRCODE = '42501';
                END IF;
                RETURN NEW;
            END IF;
            IF OLD.status IS DISTINCT FROM 'PENDING'
               OR NEW.status NOT IN (
                    'PENDING','PUBLISHED','DEAD_LETTER'
               ) THEN
                RAISE EXCEPTION 'OUTBOX_STATUS_TRANSITION_INVALID'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.recovery_count IS DISTINCT FROM OLD.recovery_count
               OR NEW.recovered_at IS DISTINCT FROM OLD.recovered_at THEN
                RAISE EXCEPTION 'OUTBOX_RECOVERY_DENIED'
                    USING ERRCODE = '42501';
            END IF;
            IF NEW.status='PENDING' THEN
                IF NEW.attempt_count IS DISTINCT FROM
                        OLD.attempt_count + 1
                   OR NEW.available_at <= transaction_timestamp()
                   OR NEW.last_error IS NULL
                   OR NEW.published_at IS NOT NULL
                   OR NEW.settled_by_run_id IS NOT NULL THEN
                    RAISE EXCEPTION 'OUTBOX_STATUS_TRANSITION_INVALID'
                        USING ERRCODE = '23514';
                END IF;
            ELSIF NEW.status='PUBLISHED' THEN
                IF NEW.attempt_count IS DISTINCT FROM OLD.attempt_count
                   OR NEW.published_at IS NULL
                   OR NEW.last_error IS NOT NULL THEN
                    RAISE EXCEPTION 'OUTBOX_STATUS_TRANSITION_INVALID'
                        USING ERRCODE = '23514';
                END IF;
            ELSE
                IF OLD.attempt_count IS DISTINCT FROM 7
                   OR NEW.attempt_count IS DISTINCT FROM 8
                   OR NEW.available_at IS DISTINCT FROM
                        transaction_timestamp()
                   OR NEW.last_error IS NULL
                   OR NEW.published_at IS NOT NULL
                   OR NEW.settled_by_run_id IS NOT NULL THEN
                    RAISE EXCEPTION 'OUTBOX_STATUS_TRANSITION_INVALID'
                        USING ERRCODE = '23514';
                END IF;
            END IF;
            RETURN NEW;
        END
        $function$;

        CREATE FUNCTION public.guard_outbox_event_insert_v2()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $function$
        BEGIN
            IF NEW.status IS DISTINCT FROM 'PENDING'
               OR NEW.attempt_count IS DISTINCT FROM 0
               OR NEW.recovery_count IS DISTINCT FROM 0
               OR NEW.recovered_at IS NOT NULL
               OR NEW.row_version IS DISTINCT FROM 1
               OR NEW.published_at IS NOT NULL
               OR NEW.settled_by_run_id IS NOT NULL THEN
                RAISE EXCEPTION 'OUTBOX_INITIAL_STATE_INVALID'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END
        $function$;

        DROP TRIGGER IF EXISTS guard_outbox_event_update
            ON public.outbox_events;
        CREATE TRIGGER guard_outbox_event_update
        BEFORE UPDATE OR DELETE ON public.outbox_events
        FOR EACH ROW EXECUTE FUNCTION public.guard_outbox_event_update();
        CREATE TRIGGER guard_outbox_event_insert_v2
        BEFORE INSERT ON public.outbox_events
        FOR EACH ROW EXECUTE FUNCTION public.guard_outbox_event_insert_v2();

        CREATE FUNCTION public.guard_outbox_legacy_archive_append_only_v2()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $function$
        BEGIN
            RAISE EXCEPTION 'LEGACY_OUTBOX_ARCHIVE_IMMUTABLE'
                USING ERRCODE = '42501';
        END
        $function$;

        CREATE TRIGGER guard_outbox_legacy_archive_append_only_v2
        BEFORE UPDATE OR DELETE ON public.outbox_events_legacy_archive
        FOR EACH ROW EXECUTE FUNCTION
            public.guard_outbox_legacy_archive_append_only_v2();

        CREATE FUNCTION public.check_outbox_active_archive_exclusive_v2()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE target_event_id text := coalesce(NEW.event_id,OLD.event_id);
        BEGIN
            IF EXISTS (
                SELECT 1 FROM public.outbox_events
                WHERE event_id=target_event_id
            ) AND EXISTS (
                SELECT 1 FROM public.outbox_events_legacy_archive
                WHERE event_id=target_event_id
            ) THEN
                RAISE EXCEPTION 'LEGACY_OUTBOX_ARCHIVE_CONFLICT'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NULL;
        END
        $function$;

        CREATE CONSTRAINT TRIGGER check_outbox_active_archive_from_active_v2
        AFTER INSERT OR UPDATE ON public.outbox_events
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION
            public.check_outbox_active_archive_exclusive_v2();
        CREATE CONSTRAINT TRIGGER check_outbox_active_archive_from_archive_v2
        AFTER INSERT OR UPDATE ON public.outbox_events_legacy_archive
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION
            public.check_outbox_active_archive_exclusive_v2();

        REVOKE ALL ON FUNCTION
            public.preview_legacy_outbox_archive_v2(text,text),
            public.guard_outbox_event_update(),
            public.guard_outbox_event_insert_v2(),
            public.guard_outbox_legacy_archive_append_only_v2(),
            public.check_outbox_active_archive_exclusive_v2()
        FROM PUBLIC;
        """
    )


def _archive_legacy_and_enforce_three_state() -> None:
    op.execute(
        rf"""
        DO $archive_legacy_outbox_rows_v2$
        DECLARE legacy record;
        DECLARE proof_type text;
        DECLARE preview jsonb;
        DECLARE before_count bigint;
        DECLARE before_hash text;
        DECLARE archived_count bigint;
        DECLARE archived_hash text;
        BEGIN
            SELECT count(*),public.dts_canonical_json_sha256_v1(
                coalesce(
                    jsonb_agg(
                        jsonb_build_object(
                            'event_id',event_id,
                            'archive_source_row_hash',
                                public.outbox_legacy_source_row_hash_v1(
                                    event_row
                                )
                        )
                        ORDER BY convert_to(event_id,'UTF8')
                    ),
                    '[]'::jsonb
                )
            ) INTO before_count,before_hash
            FROM public.outbox_events AS event_row
            WHERE status NOT IN ('PENDING','PUBLISHED','DEAD_LETTER');

            FOR legacy IN
                SELECT event_id,status
                FROM public.outbox_events
                WHERE status NOT IN ('PENDING','PUBLISHED','DEAD_LETTER')
                ORDER BY convert_to(event_id,'UTF8')
            LOOP
                proof_type := CASE legacy.status
                    WHEN 'CANCELLED' THEN 'MOCK_SEED_CANCELLED'
                    WHEN 'PARKED' THEN
                        'MIGRATION_20260729_37_RETRY_PARKED'
                    ELSE NULL
                END;
                IF proof_type IS NULL THEN
                    RAISE EXCEPTION 'LEGACY_OUTBOX_STATUS_CONFLICT'
                        USING ERRCODE = '23514';
                END IF;
                preview := public.preview_legacy_outbox_archive_v2(
                    legacy.event_id,proof_type
                );
                IF jsonb_array_length(
                    preview->'safety_issue_codes'
                ) > 0 THEN
                    RAISE EXCEPTION 'LEGACY_OUTBOX_PAYLOAD_UNSAFE'
                        USING ERRCODE = '23514';
                END IF;
                IF (preview->>'eligible')::boolean IS DISTINCT FROM true THEN
                    RAISE EXCEPTION 'LEGACY_OUTBOX_STATUS_CONFLICT'
                        USING ERRCODE = '23514';
                END IF;
                PERFORM public.archive_legacy_outbox_event_v2(
                    legacy.event_id,
                    preview->>'archive_source_row_hash',
                    proof_type,
                    preview->>'proof_hash',
                    '{MIGRATION_RUN_ID}'
                );
            END LOOP;

            IF EXISTS (
                SELECT 1 FROM public.outbox_events
                WHERE status NOT IN ('PENDING','PUBLISHED','DEAD_LETTER')
            ) THEN
                RAISE EXCEPTION 'LEGACY_OUTBOX_STATUS_CONFLICT'
                    USING ERRCODE = '23514';
            END IF;
            SELECT count(*),public.dts_canonical_json_sha256_v1(
                coalesce(
                    jsonb_agg(
                        jsonb_build_object(
                            'event_id',event_id,
                            'archive_source_row_hash',
                                archive_source_row_hash
                        )
                        ORDER BY convert_to(event_id,'UTF8')
                    ),
                    '[]'::jsonb
                )
            ) INTO archived_count,archived_hash
            FROM public.outbox_events_legacy_archive
            WHERE migration_run_id='{MIGRATION_RUN_ID}';
            IF archived_count IS DISTINCT FROM before_count
               OR archived_hash IS DISTINCT FROM before_hash THEN
                RAISE EXCEPTION 'LEGACY_OUTBOX_ARCHIVE_CONFLICT'
                    USING ERRCODE = '23514';
            END IF;
        END
        $archive_legacy_outbox_rows_v2$;

        ALTER TABLE public.outbox_events
            ADD CONSTRAINT ck_outbox_status_v2 CHECK (
                status IN ('PENDING','PUBLISHED','DEAD_LETTER')
            ) NOT VALID,
            ADD CONSTRAINT ck_outbox_state_shape_v2 CHECK (
                attempt_count >= 0
                AND recovery_count >= 0
                AND row_version >= 1
                AND (
                    (status='PENDING'
                     AND attempt_count BETWEEN 0 AND 7
                     AND published_at IS NULL
                     AND settled_by_run_id IS NULL
                     AND ((attempt_count=0 AND last_error IS NULL)
                          OR (attempt_count>0 AND last_error IS NOT NULL)))
                    OR
                    (status='PUBLISHED'
                     AND attempt_count BETWEEN 0 AND 7
                     AND published_at IS NOT NULL)
                    OR
                    (status='DEAD_LETTER'
                     AND attempt_count=8
                     AND last_error IS NOT NULL
                     AND published_at IS NULL
                     AND settled_by_run_id IS NULL)
                )
            ) NOT VALID;
        ALTER TABLE public.outbox_events
            VALIDATE CONSTRAINT ck_outbox_status_v2;
        ALTER TABLE public.outbox_events
            VALIDATE CONSTRAINT ck_outbox_state_shape_v2;

        CREATE INDEX ix_outbox_dead_letter_v2
        ON public.outbox_events (
            event_type,created_at,outbox_id
        ) WHERE status='DEAD_LETTER';
        """
    )


def _install_recovery_protocol() -> None:
    op.execute(
        r"""
        CREATE OR REPLACE FUNCTION public.recover_outbox_event_v2(
            p_command_id text,
            p_event_id text,
            p_expected_payload_hash text,
            p_expected_recovery_count bigint,
            p_reason text
        )
        RETURNS jsonb
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE command_key text;
        DECLARE request_document jsonb;
        DECLARE request_hash text;
        DECLARE resource_identity text;
        DECLARE existing_command public.idempotency_records%ROWTYPE;
        DECLARE event_before public.outbox_events%ROWTYPE;
        DECLARE event_after public.outbox_events%ROWTYPE;
        DECLARE audit_event_id text;
        DECLARE audit_payload jsonb;
        DECLARE audit_payload_hash text;
        DECLARE response_payload jsonb;
        BEGIN
            IF p_command_id IS NULL
               OR p_command_id !~ '^[A-Za-z0-9._:-]{1,128}$'
               OR p_event_id IS NULL OR btrim(p_event_id)=''
               OR length(p_event_id)>512
               OR p_expected_payload_hash !~ '^[0-9a-f]{64}$'
               OR p_expected_recovery_count IS NULL
               OR p_expected_recovery_count<0
               OR p_reason IS NULL OR btrim(p_reason)=''
               OR length(p_reason)>1024 THEN
                RAISE EXCEPTION 'OUTBOX_RECOVERY_REQUEST_INVALID'
                    USING ERRCODE = '22023';
            END IF;
            command_key := 'recover-outbox:v2:' || p_command_id;
            request_document := jsonb_build_object(
                'protocol_version','outbox-recovery-v2',
                'command_id',p_command_id,
                'event_id',p_event_id,
                'expected_payload_hash',p_expected_payload_hash,
                'expected_recovery_count',p_expected_recovery_count,
                'reason',p_reason
            );
            request_hash := public.dts_canonical_json_sha256_v1(
                request_document
            );
            resource_identity := 'outbox-event:' ||
                public.dts_canonical_json_sha256_v1(to_jsonb(p_event_id));
            PERFORM pg_advisory_xact_lock(hashtextextended(command_key,0));
            SELECT * INTO existing_command
            FROM public.idempotency_records
            WHERE scope='OUTBOX_RECOVERY_V2'
              AND idempotency_key=command_key
            FOR UPDATE;
            IF FOUND THEN
                IF existing_command.request_hash IS DISTINCT FROM
                        request_hash THEN
                    RAISE EXCEPTION 'OUTBOX_RECOVERY_COMMAND_CONFLICT'
                        USING ERRCODE = '23505';
                END IF;
                IF existing_command.expires_at IS NOT NULL
                   OR existing_command.resource_id IS DISTINCT FROM
                        resource_identity
                   OR jsonb_typeof(existing_command.response_payload)
                        IS DISTINCT FROM 'object'
                   OR existing_command.response_payload->>'event_id'
                        IS DISTINCT FROM p_event_id
                   OR existing_command.response_payload->>'payload_sha256'
                        IS DISTINCT FROM p_expected_payload_hash
                   OR NOT EXISTS (
                        SELECT 1 FROM public.outbox_events
                        WHERE event_id=p_event_id
                          AND payload_sha256=p_expected_payload_hash
                   ) THEN
                    RAISE EXCEPTION 'OUTBOX_RECOVERY_REPLAY_INVALID'
                        USING ERRCODE = '23514';
                END IF;
                RETURN existing_command.response_payload ||
                    jsonb_build_object('replay_status','REPLAYED');
            END IF;

            SELECT * INTO event_before
            FROM public.outbox_events
            WHERE event_id=p_event_id
            FOR UPDATE;
            IF NOT FOUND
               OR event_before.status IS DISTINCT FROM 'DEAD_LETTER'
               OR event_before.payload_sha256 IS DISTINCT FROM
                    p_expected_payload_hash
               OR event_before.recovery_count IS DISTINCT FROM
                    p_expected_recovery_count THEN
                RAISE EXCEPTION 'OUTBOX_RECOVERY_STALE'
                    USING ERRCODE = '40001';
            END IF;
            UPDATE public.outbox_events
            SET status='PENDING',attempt_count=0,last_error=NULL,
                available_at=transaction_timestamp(),published_at=NULL,
                settled_by_run_id=NULL,
                recovery_count=recovery_count+1,
                recovered_at=transaction_timestamp(),
                row_version=row_version+1
            WHERE outbox_id=event_before.outbox_id
              AND event_id=event_before.event_id
              AND status='DEAD_LETTER'
              AND payload_sha256=p_expected_payload_hash
              AND recovery_count=p_expected_recovery_count
              AND row_version=event_before.row_version
            RETURNING * INTO event_after;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'OUTBOX_RECOVERY_STALE'
                    USING ERRCODE = '40001';
            END IF;

            audit_event_id := 'audit:outbox-recovery:v2:' ||
                public.dts_canonical_json_sha256_v1(to_jsonb(p_command_id));
            audit_payload := jsonb_build_object(
                'protocol_version','outbox-recovery-v2',
                'command_id',p_command_id,
                'event_id',event_after.event_id,
                'outbox_id',event_after.outbox_id,
                'payload_sha256',event_after.payload_sha256,
                'previous_recovery_count',event_before.recovery_count,
                'recovery_count',event_after.recovery_count,
                'previous_row_version',event_before.row_version,
                'row_version',event_after.row_version,
                'reason_hash',encode(
                    sha256(convert_to(p_reason,'UTF8')),'hex'
                )
            );
            audit_payload_hash :=
                public.dts_canonical_json_sha256_v1(audit_payload);
            INSERT INTO public.audit_events (
                event_id,event_type,teacher_id,task_id,case_id,occurred_at,
                actor_type,payload_hash,payload
            ) VALUES (
                audit_event_id,'OUTBOX_EVENT_RECOVERED_V2',NULL,NULL,NULL,
                transaction_timestamp(),'OUTBOX_RECOVERY',
                audit_payload_hash,audit_payload
            );
            response_payload := jsonb_build_object(
                'status','PENDING',
                'replay_status','APPLIED',
                'command_id',p_command_id,
                'event_id',event_after.event_id,
                'outbox_id',event_after.outbox_id,
                'payload_sha256',event_after.payload_sha256,
                'recovery_count',event_after.recovery_count,
                'row_version',event_after.row_version,
                'audit_event_id',audit_event_id
            );
            INSERT INTO public.idempotency_records (
                scope,idempotency_key,request_hash,resource_id,
                response_payload,created_at,expires_at
            ) VALUES (
                'OUTBOX_RECOVERY_V2',command_key,request_hash,
                resource_identity,response_payload,
                transaction_timestamp(),NULL
            );
            RETURN response_payload;
        END
        $function$;

        REVOKE ALL ON FUNCTION public.recover_outbox_event_v2(
            text,text,text,bigint,text
        ) FROM PUBLIC;
        """
    )


def _apply_acl_and_comments() -> None:
    op.execute(
        r"""
        REVOKE ALL PRIVILEGES ON TABLE
            public.outbox_events_legacy_archive
        FROM PUBLIC, tit_growth_app, tit_dts_ingest_runtime;
        REVOKE ALL ON FUNCTION
            public.preview_legacy_outbox_archive_v2(text,text),
            public.archive_legacy_outbox_event_v2(
                text,text,text,text,text
            ),
            public.recover_outbox_event_v2(text,text,text,bigint,text),
            public.legacy_outbox_payload_safety_issues_v1(jsonb,text)
        FROM PUBLIC, tit_growth_app, tit_dts_ingest_runtime;
        REVOKE UPDATE (recovery_count,recovered_at,settled_by_run_id)
        ON public.outbox_events FROM tit_growth_app;

        DO $outbox_v2_optional_acl$
        DECLARE role_name text;
        BEGIN
            FOREACH role_name IN ARRAY ARRAY[
                'tit_teacher_crud','tit_dts_domain_projector_runtime',
                'tit_dts_scope_coordinator_runtime','tit_source_monitor',
                'tit_source_worker','tit_source_wide_runtime',
                'tide_business_app'
            ]::text[] LOOP
                IF to_regrole(role_name) IS NOT NULL THEN
                    EXECUTE format(
                        'REVOKE ALL PRIVILEGES ON TABLE '
                        'public.outbox_events_legacy_archive FROM %I',
                        role_name
                    );
                    EXECUTE format(
                        'REVOKE ALL ON FUNCTION '
                        'public.preview_legacy_outbox_archive_v2(text,text),'
                        'public.archive_legacy_outbox_event_v2('
                        'text,text,text,text,text),'
                        'public.recover_outbox_event_v2('
                        'text,text,text,bigint,text) FROM %I',
                        role_name
                    );
                END IF;
            END LOOP;

            IF to_regrole('tit_dts_outbox_recovery_runtime') IS NOT NULL THEN
                IF EXISTS (
                    SELECT 1 FROM pg_roles
                    WHERE rolname='tit_dts_outbox_recovery_runtime'
                      AND (NOT rolcanlogin OR rolinherit OR rolsuper
                           OR rolcreatedb OR rolcreaterole OR rolreplication
                           OR rolbypassrls)
                ) THEN
                    RAISE EXCEPTION
                        'tit_dts_outbox_recovery_runtime must be a restricted NOINHERIT LOGIN role';
                END IF;
                EXECUTE 'GRANT SELECT ON TABLE public.outbox_events,'
                    'public.outbox_events_legacy_archive '
                    'TO tit_dts_outbox_recovery_runtime';
                EXECUTE 'GRANT EXECUTE ON FUNCTION '
                    'public.recover_outbox_event_v2('
                    'text,text,text,bigint,text) '
                    'TO tit_dts_outbox_recovery_runtime';
            END IF;

            IF to_regrole('tit_dts_cutover_migration') IS NOT NULL THEN
                IF EXISTS (
                    SELECT 1 FROM pg_roles
                    WHERE rolname='tit_dts_cutover_migration'
                      AND (NOT rolcanlogin OR rolinherit OR rolsuper
                           OR rolcreatedb OR rolcreaterole OR rolreplication
                           OR rolbypassrls)
                ) THEN
                    RAISE EXCEPTION
                        'tit_dts_cutover_migration must be a restricted NOINHERIT LOGIN role';
                END IF;
                EXECUTE 'GRANT SELECT ON TABLE public.outbox_events,'
                    'public.outbox_events_legacy_archive '
                    'TO tit_dts_cutover_migration';
                EXECUTE 'GRANT EXECUTE ON FUNCTION '
                    'public.preview_legacy_outbox_archive_v2(text,text),'
                    'public.archive_legacy_outbox_event_v2('
                    'text,text,text,text,text) '
                    'TO tit_dts_cutover_migration';
            END IF;
        END
        $outbox_v2_optional_acl$;

        COMMENT ON TABLE public.outbox_events IS
            'Active v2 Outbox work. Only PENDING, PUBLISHED, and DEAD_LETTER are valid; DEAD_LETTER recovery is an audited command.';
        COMMENT ON COLUMN public.outbox_events.settled_by_run_id IS
            'Optional cutover run identity; ordinary Worker publication leaves it NULL.';
        COMMENT ON FUNCTION public.recover_outbox_event_v2(
            text,text,text,bigint,text
        ) IS
            'Audited, idempotent DEAD_LETTER to PENDING recovery; never implies downstream success.';
        COMMENT ON FUNCTION public.archive_legacy_outbox_event_v2(
            text,text,text,text,text
        ) IS
            'Atomic typed-proof archive plus audit plus active-row removal for approved legacy Outbox rows only.';
        """
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("Outbox v2 three-state requires PostgreSQL")
    _assert_preconditions_and_lock()
    _create_archive_table()
    _install_legacy_helpers()
    _install_archive_protocol()
    _install_archive_commands()
    _archive_legacy_and_enforce_three_state()
    _install_recovery_protocol()
    _apply_acl_and_comments()


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("Outbox v2 three-state requires PostgreSQL")
    op.execute(
        r"""
        LOCK TABLE public.outbox_events,
                   public.outbox_events_legacy_archive
        IN ACCESS EXCLUSIVE MODE;
        DO $outbox_v2_downgrade_guard$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM public.idempotency_records
                WHERE scope='OUTBOX_RECOVERY_V2'
            ) THEN
                RAISE EXCEPTION
                    'refusing Outbox v2 downgrade: recovery history exists';
            END IF;
            IF EXISTS (
                SELECT 1 FROM public.outbox_events
                WHERE settled_by_run_id IS NOT NULL
            ) THEN
                RAISE EXCEPTION
                    'refusing Outbox v2 downgrade: cutover settlement exists';
            END IF;
            IF EXISTS (
                SELECT 1 FROM public.outbox_events_legacy_archive
                WHERE migration_run_id <>
                    'alembic:20260822_85_outbox_three_state'
            ) OR EXISTS (
                SELECT 1
                FROM public.outbox_events AS active
                JOIN public.outbox_events_legacy_archive AS archived
                  USING (event_id)
            ) THEN
                RAISE EXCEPTION
                    'refusing Outbox v2 downgrade: archive conflict exists';
            END IF;
        END
        $outbox_v2_downgrade_guard$;

        DROP TRIGGER IF EXISTS check_outbox_active_archive_from_active_v2
            ON public.outbox_events;
        DROP TRIGGER IF EXISTS check_outbox_active_archive_from_archive_v2
            ON public.outbox_events_legacy_archive;
        DROP TRIGGER IF EXISTS guard_outbox_event_insert_v2
            ON public.outbox_events;
        DROP TRIGGER IF EXISTS guard_outbox_event_update
            ON public.outbox_events;
        DROP TRIGGER IF EXISTS guard_outbox_legacy_archive_append_only_v2
            ON public.outbox_events_legacy_archive;
        ALTER TABLE public.outbox_events
            DROP CONSTRAINT ck_outbox_state_shape_v2,
            DROP CONSTRAINT ck_outbox_status_v2;
        DROP INDEX IF EXISTS public.ix_outbox_dead_letter_v2;

        INSERT INTO public.outbox_events (
            outbox_id,event_id,aggregate_type,aggregate_id,event_type,
            payload,payload_sha256,status,available_at,attempt_count,
            last_error,created_at,published_at,recovery_count,recovered_at,
            row_version,settled_by_run_id
        )
        SELECT outbox_id,event_id,aggregate_type,aggregate_id,event_type,
               payload,payload_hash,status,available_at,attempt_count,
               last_error,created_at,published_at,recovery_count,recovered_at,
               row_version,settled_by_run_id
        FROM public.outbox_events_legacy_archive
        ORDER BY convert_to(event_id,'UTF8');

        DROP FUNCTION IF EXISTS public.recover_outbox_event_v2(
            text,text,text,bigint,text
        );
        DROP FUNCTION IF EXISTS public.archive_legacy_outbox_event_v2(
            text,text,text,text,text
        );
        DROP FUNCTION IF EXISTS public.preview_legacy_outbox_archive_v2(
            text,text
        );
        DROP FUNCTION IF EXISTS
            public.check_outbox_active_archive_exclusive_v2();
        DROP FUNCTION IF EXISTS
            public.guard_outbox_legacy_archive_append_only_v2();
        DROP FUNCTION IF EXISTS public.guard_outbox_event_insert_v2();
        DROP FUNCTION IF EXISTS public.guard_outbox_event_update();
        DROP FUNCTION IF EXISTS public.outbox_legacy_source_row_hash_v1(
            public.outbox_events
        );
        DROP FUNCTION IF EXISTS
            public.legacy_outbox_payload_safety_issues_v1(jsonb,text);
        DROP FUNCTION IF EXISTS
            public.outbox_legacy_string_safety_issues_v1(jsonb,text);
        DROP FUNCTION IF EXISTS public.outbox_legacy_json_depth_v1(jsonb);
        DROP FUNCTION IF EXISTS
            public.outbox_legacy_json_pointer_escape_v1(text);
        DROP FUNCTION IF EXISTS
            public.outbox_legacy_timestamp_v1(timestamptz);
        """
    )
    op.drop_table("outbox_events_legacy_archive", schema="public")
    op.drop_column("outbox_events", "settled_by_run_id", schema="public")
    op.execute(
        r"""
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
        CREATE TRIGGER guard_outbox_event_update
        BEFORE UPDATE OR DELETE ON public.outbox_events
        FOR EACH ROW EXECUTE FUNCTION public.guard_outbox_event_update();
        REVOKE ALL ON FUNCTION public.guard_outbox_event_update()
        FROM PUBLIC;
        COMMENT ON TABLE public.outbox_events IS NULL;
        """
    )


def _install_archive_commands() -> None:
    # The guard references the recovery function by regprocedure.  Install a
    # denied stub first so ordinary legacy updates remain fail closed while the
    # archive function is being created in the same migration transaction.
    op.execute(
        r"""
        CREATE FUNCTION public.recover_outbox_event_v2(
            p_command_id text,
            p_event_id text,
            p_expected_payload_hash text,
            p_expected_recovery_count bigint,
            p_reason text
        )
        RETURNS jsonb
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        BEGIN
            RAISE EXCEPTION 'OUTBOX_RECOVERY_NOT_READY'
                USING ERRCODE = '55000';
        END
        $function$;
        REVOKE ALL ON FUNCTION public.recover_outbox_event_v2(
            text,text,text,bigint,text
        ) FROM PUBLIC;

        CREATE FUNCTION public.archive_legacy_outbox_event_v2(
            p_event_id text,
            p_expected_row_hash text,
            p_proof_type text,
            p_expected_proof_hash text,
            p_migration_run_id text
        )
        RETURNS jsonb
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE active public.outbox_events%ROWTYPE;
        DECLARE archived public.outbox_events_legacy_archive%ROWTYPE;
        DECLARE preview jsonb;
        DECLARE actual_row_hash text;
        DECLARE actual_proof_hash text;
        DECLARE audit_event_id text;
        DECLARE audit_payload jsonb;
        DECLARE audit_payload_hash text;
        DECLARE archive_reason text;
        DECLARE existing_audit public.audit_events%ROWTYPE;
        BEGIN
            IF p_event_id IS NULL OR btrim(p_event_id)=''
               OR p_expected_row_hash !~ '^[0-9a-f]{64}$'
               OR p_expected_proof_hash !~ '^[0-9a-f]{64}$'
               OR p_proof_type NOT IN (
                    'MOCK_SEED_CANCELLED',
                    'MIGRATION_20260729_37_RETRY_PARKED'
               ) OR p_migration_run_id IS NULL
               OR btrim(p_migration_run_id)='' THEN
                RAISE EXCEPTION 'LEGACY_OUTBOX_PROOF_INVALID'
                    USING ERRCODE = '22023';
            END IF;
            PERFORM pg_advisory_xact_lock(hashtextextended(
                'legacy-outbox-archive:v2:' || p_event_id,0
            ));
            SELECT * INTO archived
            FROM public.outbox_events_legacy_archive
            WHERE event_id=p_event_id
            FOR UPDATE;
            IF FOUND THEN
                IF EXISTS (
                    SELECT 1 FROM public.outbox_events
                    WHERE event_id=p_event_id
                ) OR archived.archive_source_row_hash IS DISTINCT FROM
                        p_expected_row_hash
                   OR archived.proof_type IS DISTINCT FROM p_proof_type
                   OR archived.proof_hash IS DISTINCT FROM
                        p_expected_proof_hash
                   OR archived.migration_run_id IS DISTINCT FROM
                        p_migration_run_id THEN
                    RAISE EXCEPTION 'LEGACY_OUTBOX_ARCHIVE_CONFLICT'
                        USING ERRCODE = '23514';
                END IF;
                RETURN jsonb_build_object(
                    'status','NOOP','event_id',archived.event_id,
                    'outbox_id',archived.outbox_id,
                    'archive_source_row_hash',
                        archived.archive_source_row_hash,
                    'proof_hash',archived.proof_hash,
                    'archive_audit_event_id',
                        archived.archive_audit_event_id
                );
            END IF;

            SELECT * INTO active
            FROM public.outbox_events
            WHERE event_id=p_event_id
            FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'LEGACY_OUTBOX_ARCHIVE_CONFLICT'
                    USING ERRCODE = '23514';
            END IF;
            actual_row_hash :=
                public.outbox_legacy_source_row_hash_v1(active);
            IF actual_row_hash IS DISTINCT FROM p_expected_row_hash THEN
                RAISE EXCEPTION 'LEGACY_OUTBOX_ROW_CHANGED'
                    USING ERRCODE = '40001';
            END IF;
            preview := public.preview_legacy_outbox_archive_v2(
                p_event_id,p_proof_type
            );
            IF jsonb_array_length(preview->'safety_issue_codes') > 0 THEN
                RAISE EXCEPTION 'LEGACY_OUTBOX_PAYLOAD_UNSAFE'
                    USING ERRCODE = '23514';
            END IF;
            actual_proof_hash := preview->>'proof_hash';
            IF (preview->>'eligible')::boolean IS DISTINCT FROM true
               OR actual_proof_hash IS DISTINCT FROM
                    p_expected_proof_hash THEN
                RAISE EXCEPTION 'LEGACY_OUTBOX_PROOF_INVALID'
                    USING ERRCODE = '23514';
            END IF;
            archive_reason := preview->>'archive_reason';
            audit_event_id := 'audit:legacy-outbox:v2:' ||
                public.dts_canonical_json_sha256_v1(
                    jsonb_build_object(
                        'event_id',p_event_id,
                        'archive_source_row_hash',p_expected_row_hash,
                        'proof_hash',p_expected_proof_hash,
                        'migration_run_id',p_migration_run_id
                    )
            );
            audit_payload := jsonb_build_object(
                'protocol_version','legacy-outbox-archive-v2',
                'event_id',active.event_id,
                'outbox_id',active.outbox_id,
                'payload_hash',active.payload_sha256,
                'archive_source_row_hash',p_expected_row_hash,
                'proof_type',p_proof_type,
                'proof_hash',p_expected_proof_hash,
                'archive_reason',archive_reason,
                'migration_run_id',p_migration_run_id
            );
            audit_payload_hash :=
                public.dts_canonical_json_sha256_v1(audit_payload);
            INSERT INTO public.audit_events (
                event_id,event_type,teacher_id,task_id,case_id,occurred_at,
                actor_type,payload_hash,payload
            ) VALUES (
                audit_event_id,'LEGACY_OUTBOX_ARCHIVED_V2',NULL,NULL,NULL,
                transaction_timestamp(),'MIGRATION',audit_payload_hash,
                audit_payload
            ) ON CONFLICT (event_id) DO NOTHING;
            IF NOT FOUND THEN
                SELECT * INTO existing_audit
                FROM public.audit_events
                WHERE event_id=audit_event_id;
                IF existing_audit.event_type IS DISTINCT FROM
                        'LEGACY_OUTBOX_ARCHIVED_V2'
                   OR existing_audit.payload_hash IS DISTINCT FROM
                        audit_payload_hash
                   OR existing_audit.payload IS DISTINCT FROM audit_payload
                THEN
                    RAISE EXCEPTION 'LEGACY_OUTBOX_ARCHIVE_CONFLICT'
                        USING ERRCODE = '23514';
                END IF;
            END IF;

            INSERT INTO public.outbox_events_legacy_archive (
                event_id,outbox_id,aggregate_type,aggregate_id,event_type,
                payload,payload_hash,status,available_at,attempt_count,
                recovery_count,recovered_at,row_version,last_error,
                settled_by_run_id,created_at,published_at,
                archive_source_row_hash,archive_reason,proof_type,proof_hash,
                migration_run_id,archive_audit_event_id,archived_at,
                archived_by
            ) VALUES (
                active.event_id,active.outbox_id,active.aggregate_type,
                active.aggregate_id,active.event_type,active.payload,
                active.payload_sha256,active.status,active.available_at,
                active.attempt_count,active.recovery_count,
                active.recovered_at,active.row_version,active.last_error,
                active.settled_by_run_id,active.created_at,
                active.published_at,p_expected_row_hash,archive_reason,
                p_proof_type,p_expected_proof_hash,p_migration_run_id,
                audit_event_id,transaction_timestamp(),current_user
            );
            DELETE FROM public.outbox_events WHERE event_id=p_event_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'LEGACY_OUTBOX_ARCHIVE_CONFLICT'
                    USING ERRCODE = '23514';
            END IF;
            RETURN jsonb_build_object(
                'status','ARCHIVED','event_id',active.event_id,
                'outbox_id',active.outbox_id,
                'archive_source_row_hash',p_expected_row_hash,
                'proof_hash',p_expected_proof_hash,
                'archive_audit_event_id',audit_event_id
            );
        END
        $function$;

        REVOKE ALL ON FUNCTION public.archive_legacy_outbox_event_v2(
            text,text,text,text,text
        ) FROM PUBLIC;
        """
    )
