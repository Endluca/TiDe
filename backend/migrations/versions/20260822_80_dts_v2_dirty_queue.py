"""replace the legacy dirty queue with the region-qualified v2 state machine.

Revision ID: 20260822_80_dts_v2_dirty_queue
Revises: 20260822_79_dts_v2_epoch_control
Create Date: 2026-08-22

The migration is intentionally fail closed.  Every legacy row must already be
COMPLETED; it is archived verbatim with a canonical hash before the v2 queue is
rebuilt empty.  Pending legacy work blocks the migration, and legacy
``last_source_region`` is never promoted into a new v2 business identity.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "20260822_80_dts_v2_dirty_queue"
down_revision: Union[str, None] = "20260822_79_dts_v2_epoch_control"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


DIRTY_KEY_TYPES: tuple[str, ...] = (
    "COURSE",
    "TEACHER",
    "TEACHER_STUDENT",
    "LABEL",
    "COMPLAINT_CATEGORY",
    "TEACHER_TIME_RECHECK",
)

DIRTY_STATUSES: tuple[str, ...] = (
    "PENDING",
    "PROCESSING",
    "WAITING_DEPENDENCY",
    "RETRY",
    "DEAD",
    "COMPLETED",
)

INPUT_KINDS: tuple[str, ...] = (
    "SOURCE_REVISION",
    "SCOPE_REVISION",
    "CATALOG_REVISION",
    "DEPENDENCY_WAKE",
    "OPERATOR_RECOVERY",
    "TIME_RECHECK",
)

DOMAIN_KEY_TYPES: tuple[str, ...] = (
    "COURSE",
    "TEACHER",
    "TEACHER_STUDENT",
    "LABEL",
    "COMPLAINT_CATEGORY",
)


def _install_canonical_helpers() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.dts_canonical_json_v1(value jsonb)
        RETURNS text
        LANGUAGE plpgsql
        IMMUTABLE
        STRICT
        PARALLEL SAFE
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE
            result text;
        BEGIN
            CASE jsonb_typeof(value)
                WHEN 'object' THEN
                    SELECT '{' || coalesce(string_agg(
                        to_jsonb(item.key)::text || ':' ||
                            public.dts_canonical_json_v1(item.value),
                        ',' ORDER BY convert_to(item.key, 'UTF8')
                    ), '') || '}'
                    INTO result
                    FROM jsonb_each(value) AS item(key, value);
                WHEN 'array' THEN
                    SELECT '[' || coalesce(string_agg(
                        public.dts_canonical_json_v1(item.value),
                        ',' ORDER BY item.ordinality
                    ), '') || ']'
                    INTO result
                    FROM jsonb_array_elements(value)
                         WITH ORDINALITY AS item(value, ordinality);
                ELSE
                    result := value::text;
            END CASE;
            RETURN result;
        END
        $function$;

        CREATE FUNCTION public.dts_canonical_json_sha256_v1(value jsonb)
        RETURNS text
        LANGUAGE sql
        IMMUTABLE
        STRICT
        PARALLEL SAFE
        SET search_path = pg_catalog, public
        AS $function$
            SELECT encode(
                sha256(convert_to(public.dts_canonical_json_v1(value), 'UTF8')),
                'hex'
            )
        $function$;

        CREATE FUNCTION public.dts_dirty_key_identity_v2(
            p_source_region text,
            p_key_type text,
            p_key_part_1 text,
            p_key_part_2 text
        )
        RETURNS jsonb
        LANGUAGE sql
        IMMUTABLE
        STRICT
        PARALLEL SAFE
        SET search_path = pg_catalog, public
        AS $function$
            SELECT jsonb_build_object(
                'source_region', p_source_region,
                'key_type', p_key_type,
                'key_part_1', p_key_part_1,
                'key_part_2', p_key_part_2
            )
        $function$;

        CREATE FUNCTION public.dts_dirty_key_identity_valid_v2(
            p_source_region text,
            p_key_type text,
            p_key_part_1 text,
            p_key_part_2 text
        )
        RETURNS boolean
        LANGUAGE sql
        IMMUTABLE
        PARALLEL SAFE
        SET search_path = pg_catalog, public
        AS $function$
            SELECT p_source_region IN ('dom', 'ovs')
               AND p_key_part_1 IS NOT NULL
               AND btrim(p_key_part_1) <> ''
               AND p_key_part_2 IS NOT NULL
               AND (
                    (p_key_type IN ('COURSE', 'TEACHER', 'LABEL')
                     AND p_key_part_2 = '')
                    OR (p_key_type = 'COMPLAINT_CATEGORY'
                        AND p_source_region = 'dom'
                        AND p_key_part_2 = '')
                    OR (p_key_type = 'TEACHER_STUDENT'
                        AND btrim(p_key_part_2) <> ''
                        AND (p_source_region <> 'dom'
                             OR p_key_part_2 ~
                                '^dom:v1:[0-9a-f]{64}$'))
                    OR (p_key_type = 'TEACHER_TIME_RECHECK'
                        AND p_source_region = 'dom'
                        AND p_key_part_2 ~
                            '^[0-9]{4}-(0[1-9]|1[0-2])-([0-2][0-9]|3[01])$'
                        AND to_char(
                            to_date(p_key_part_2, 'YYYY-MM-DD'),
                            'YYYY-MM-DD'
                        ) = p_key_part_2)
               )
        $function$;

        REVOKE ALL ON FUNCTION
            public.dts_canonical_json_v1(jsonb),
            public.dts_canonical_json_sha256_v1(jsonb),
            public.dts_dirty_key_identity_v2(text,text,text,text),
            public.dts_dirty_key_identity_valid_v2(text,text,text,text)
        FROM PUBLIC;
        """
    )


def _create_queue_tables() -> None:
    op.create_table(
        "dts_dirty_keys_legacy_archive_v80",
        sa.Column("legacy_key_type", sa.String(length=32), nullable=False),
        sa.Column("legacy_key_part_1", sa.String(length=256), nullable=False),
        sa.Column("legacy_key_part_2", sa.String(length=256), nullable=False),
        sa.Column("migration_id", sa.String(length=64), nullable=False),
        sa.Column("legacy_row", postgresql.JSONB(), nullable=False),
        sa.Column("legacy_row_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "archived_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.PrimaryKeyConstraint(
            "legacy_key_type",
            "legacy_key_part_1",
            "legacy_key_part_2",
            "migration_id",
            name="pk_dts_dirty_keys_legacy_archive_v80",
        ),
        sa.CheckConstraint(
            "migration_id = '20260822_80_dts_v2_dirty_queue' "
            "AND jsonb_typeof(legacy_row) = 'object' "
            "AND legacy_row_hash ~ '^[0-9a-f]{64}$'",
            name="ck_dts_dirty_keys_legacy_archive_v80_shape",
        ),
        schema="public",
    )

    op.create_table(
        "dts_dirty_key_inputs",
        sa.Column("source_region", sa.String(length=8), nullable=False),
        sa.Column("key_type", sa.String(length=32), nullable=False),
        sa.Column("key_part_1", sa.String(length=256), nullable=False),
        sa.Column("key_part_2", sa.String(length=256), nullable=False),
        sa.Column("input_kind", sa.String(length=32), nullable=False),
        sa.Column("input_identity_hash", sa.String(length=64), nullable=False),
        sa.Column("input_revision", sa.BigInteger(), nullable=False),
        sa.Column("input_identity", postgresql.JSONB(), nullable=False),
        sa.Column("input_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("dirty_work_revision", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.PrimaryKeyConstraint(
            "source_region",
            "key_type",
            "key_part_1",
            "key_part_2",
            "input_kind",
            "input_identity_hash",
            "input_revision",
            name="pk_dts_dirty_key_inputs",
        ),
        sa.UniqueConstraint(
            "source_region",
            "key_type",
            "key_part_1",
            "key_part_2",
            "dirty_work_revision",
            name="uq_dts_dirty_key_input_work_revision",
        ),
        sa.CheckConstraint(
            "input_kind IN ('SOURCE_REVISION','SCOPE_REVISION',"
            "'CATALOG_REVISION','DEPENDENCY_WAKE','OPERATOR_RECOVERY',"
            "'TIME_RECHECK')",
            name="ck_dts_dirty_key_input_kind",
        ),
        sa.CheckConstraint(
            "input_identity_hash ~ '^[0-9a-f]{64}$' "
            "AND input_fingerprint ~ '^[0-9a-f]{64}$' "
            "AND input_revision >= 1 AND dirty_work_revision >= 1 "
            "AND jsonb_typeof(input_identity) = 'object'",
            name="ck_dts_dirty_key_input_shape",
        ),
        schema="public",
    )

    op.create_table(
        "dts_dirty_key_dependencies",
        sa.Column("source_region", sa.String(length=8), nullable=False),
        sa.Column("key_type", sa.String(length=32), nullable=False),
        sa.Column("key_part_1", sa.String(length=256), nullable=False),
        sa.Column("key_part_2", sa.String(length=256), nullable=False),
        sa.Column("dependency_type", sa.String(length=64), nullable=False),
        sa.Column("dependency_region", sa.String(length=8), nullable=False),
        sa.Column("dependency_key", sa.String(length=512), nullable=False),
        sa.Column(
            "observed_source_revision", sa.BigInteger(), nullable=False
        ),
        sa.Column("observed_dependency_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.PrimaryKeyConstraint(
            "source_region",
            "key_type",
            "key_part_1",
            "key_part_2",
            "dependency_type",
            "dependency_region",
            "dependency_key",
            name="pk_dts_dirty_key_dependencies",
        ),
        sa.CheckConstraint(
            "dependency_type ~ '^[A-Z][A-Z0-9_]{0,63}$' "
            "AND dependency_region IN ('dom','ovs') "
            "AND btrim(dependency_key) <> '' "
            "AND observed_source_revision >= 1 "
            "AND observed_dependency_hash ~ '^[0-9a-f]{64}$'",
            name="ck_dts_dirty_key_dependency_shape",
        ),
        schema="public",
    )
    op.create_index(
        "ix_dts_dirty_key_dependencies_reverse",
        "dts_dirty_key_dependencies",
        ["dependency_type", "dependency_region", "dependency_key"],
        unique=False,
        schema="public",
    )

    op.create_table(
        "dts_dirty_key_state_audits",
        sa.Column("audit_id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("source_region", sa.String(length=8), nullable=False),
        sa.Column("key_type", sa.String(length=32), nullable=False),
        sa.Column("key_part_1", sa.String(length=256), nullable=False),
        sa.Column("key_part_2", sa.String(length=256), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("work_generation", sa.BigInteger(), nullable=False),
        sa.Column("dead_generation", sa.BigInteger(), nullable=False),
        sa.Column("detail", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.PrimaryKeyConstraint("audit_id", name="pk_dts_dirty_key_state_audits"),
        sa.CheckConstraint(
            "event_type IN ('DEAD','DEAD_REOPENED_BY_INPUT','OPERATOR_RECOVERY') "
            "AND work_generation >= 1 AND dead_generation >= 0 "
            "AND jsonb_typeof(detail) = 'object'",
            name="ck_dts_dirty_key_state_audit_shape",
        ),
        schema="public",
    )


def _migrate_legacy_queue() -> None:
    op.execute(
        r"""
        LOCK TABLE public.dts_dirty_keys IN ACCESS EXCLUSIVE MODE;

        DO $legacy_dirty_drain_guard$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM public.dts_dirty_keys
                WHERE status <> 'COMPLETED'
            ) THEN
                RAISE EXCEPTION
                    'DIRTY_V2_MIGRATION_LEGACY_NOT_DRAINED'
                    USING ERRCODE = '23514';
            END IF;
        END
        $legacy_dirty_drain_guard$;

        INSERT INTO public.dts_dirty_keys_legacy_archive_v80 (
            legacy_key_type,legacy_key_part_1,legacy_key_part_2,
            migration_id,legacy_row,legacy_row_hash
        )
        SELECT dirty.key_type,dirty.key_part_1,dirty.key_part_2,
               '20260822_80_dts_v2_dirty_queue',to_jsonb(dirty),
               public.dts_canonical_json_sha256_v1(to_jsonb(dirty))
        FROM public.dts_dirty_keys AS dirty;

        DELETE FROM public.dts_dirty_keys;

        ALTER TABLE public.dts_dirty_keys
            DROP CONSTRAINT ck_dts_dirty_key_type,
            DROP CONSTRAINT ck_dts_dirty_key_status,
            DROP CONSTRAINT ck_dts_dirty_key_region,
            DROP CONSTRAINT ck_dts_dirty_key_counters;

        ALTER TABLE public.dts_dirty_keys
            ADD COLUMN created_at timestamptz,
            ADD COLUMN updated_at timestamptz;

        ALTER TABLE public.dts_dirty_keys
            ALTER COLUMN status TYPE varchar(32);

        ALTER TABLE public.dts_dirty_keys
            ALTER COLUMN source_region SET NOT NULL,
            ALTER COLUMN required_work_revision SET NOT NULL,
            ALTER COLUMN claimed_through_work_revision DROP NOT NULL,
            ALTER COLUMN completed_work_revision SET NOT NULL,
            ALTER COLUMN last_input_identity_hash SET NOT NULL,
            ALTER COLUMN last_input_revision SET NOT NULL,
            ALTER COLUMN work_generation SET NOT NULL,
            ALTER COLUMN dead_generation SET NOT NULL,
            ALTER COLUMN created_at SET NOT NULL,
            ALTER COLUMN updated_at SET NOT NULL;

        ALTER TABLE public.dts_dirty_keys
            DROP CONSTRAINT pk_dts_dirty_keys;
        ALTER TABLE public.dts_dirty_keys
            ADD CONSTRAINT pk_dts_dirty_keys PRIMARY KEY (
                source_region, key_type, key_part_1, key_part_2
            );

        ALTER TABLE public.dts_dirty_key_inputs
            ADD CONSTRAINT fk_dts_dirty_key_input_key
            FOREIGN KEY (source_region,key_type,key_part_1,key_part_2)
            REFERENCES public.dts_dirty_keys
                (source_region,key_type,key_part_1,key_part_2)
            ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED;

        ALTER TABLE public.dts_dirty_key_dependencies
            ADD CONSTRAINT fk_dts_dirty_key_dependency_key
            FOREIGN KEY (source_region,key_type,key_part_1,key_part_2)
            REFERENCES public.dts_dirty_keys
                (source_region,key_type,key_part_1,key_part_2)
            ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED;

        ALTER TABLE public.dts_dirty_key_state_audits
            ADD CONSTRAINT fk_dts_dirty_key_state_audit_key
            FOREIGN KEY (source_region,key_type,key_part_1,key_part_2)
            REFERENCES public.dts_dirty_keys
                (source_region,key_type,key_part_1,key_part_2)
            ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED;
        """
    )


def _install_queue_constraints_and_indexes() -> None:
    op.execute(
        r"""
        DROP INDEX IF EXISTS public.ix_dts_dirty_keys_ready;
        DROP INDEX IF EXISTS public.ix_dts_dirty_keys_pending_fifo;
        DROP INDEX IF EXISTS public.ix_dts_dirty_keys_retry_due;

        ALTER TABLE public.dts_dirty_keys
        ADD CONSTRAINT ck_dts_dirty_key_identity_v2 CHECK (
            public.dts_dirty_key_identity_valid_v2(
                source_region,key_type,key_part_1,key_part_2
            ) IS TRUE
        ),
        ADD CONSTRAINT ck_dts_dirty_key_status_v2 CHECK (
            status IN ('PENDING','PROCESSING','WAITING_DEPENDENCY',
                       'RETRY','DEAD','COMPLETED')
        ),
        ADD CONSTRAINT ck_dts_dirty_key_revisions_v2 CHECK (
            required_work_revision >= 1
            AND completed_work_revision >= 0
            AND completed_work_revision <= required_work_revision
            AND (claimed_through_work_revision IS NULL
                 OR (claimed_through_work_revision >= 1
                     AND claimed_through_work_revision
                         <= required_work_revision))
            AND work_generation >= 1
            AND dead_generation >= 0
            AND last_input_revision >= 1
            AND last_input_identity_hash ~ '^[0-9a-f]{64}$'
            AND row_version >= 1
        ),
        ADD CONSTRAINT ck_dts_dirty_key_lease_shape_v2 CHECK (
            (status = 'PROCESSING'
             AND claimed_through_work_revision IS NOT NULL
             AND lease_owner_kind IS NOT NULL
             AND lease_owner IS NOT NULL
             AND btrim(lease_owner) <> ''
             AND lease_token IS NOT NULL
             AND btrim(lease_token) <> ''
             AND claimed_at IS NOT NULL
             AND lease_expires_at IS NOT NULL
             AND lease_expires_at > claimed_at
             AND next_attempt_at IS NULL
             AND ((lease_owner_kind = 'DOMAIN_PROJECTOR'
                   AND key_type IN ('COURSE','TEACHER','TEACHER_STUDENT',
                                    'LABEL','COMPLAINT_CATEGORY'))
                  OR (lease_owner_kind = 'SOURCEWIDE_TIME_RECHECK'
                      AND key_type = 'TEACHER_TIME_RECHECK')))
            OR
            (status <> 'PROCESSING'
             AND claimed_through_work_revision IS NULL
             AND lease_owner_kind IS NULL
             AND lease_owner IS NULL
             AND lease_token IS NULL
             AND claimed_at IS NULL
             AND lease_expires_at IS NULL)
        ),
        ADD CONSTRAINT ck_dts_dirty_key_state_shape_v2 CHECK (
            (status = 'PENDING' AND attempt_count = 0
             AND next_attempt_at IS NOT NULL AND blocked_by IS NULL
             AND last_error_code IS NULL)
            OR (status = 'PROCESSING' AND attempt_count BETWEEN 0 AND 7
                AND blocked_by IS NULL)
            OR (status = 'WAITING_DEPENDENCY'
                AND attempt_count BETWEEN 0 AND 7
                AND next_attempt_at IS NULL
                AND blocked_by IS NOT NULL
                AND jsonb_typeof(blocked_by) = 'array'
                AND jsonb_array_length(blocked_by) > 0
                AND last_error_code IS NULL)
            OR (status = 'RETRY' AND attempt_count BETWEEN 1 AND 7
                AND next_attempt_at IS NOT NULL
                AND last_error_code IS NOT NULL
                AND blocked_by IS NULL)
            OR (status = 'DEAD' AND attempt_count = 8
                AND next_attempt_at IS NULL
                AND last_error_code IS NOT NULL
                AND blocked_by IS NULL)
            OR (status = 'COMPLETED' AND attempt_count = 0
                AND next_attempt_at IS NULL
                AND completed_work_revision >= required_work_revision
                AND last_error_code IS NULL
                AND blocked_by IS NULL)
        );

        CREATE INDEX ix_dts_dirty_keys_ready_v2
        ON public.dts_dirty_keys (
            status,next_attempt_at,updated_at,source_region,key_type,
            key_part_1,key_part_2
        );
        CREATE INDEX ix_dts_dirty_keys_pending_fifo_v2
        ON public.dts_dirty_keys (
            next_attempt_at,updated_at,source_region,key_type,key_part_1,key_part_2
        ) WHERE status = 'PENDING';
        CREATE INDEX ix_dts_dirty_keys_retry_due_v2
        ON public.dts_dirty_keys (
            next_attempt_at,updated_at,source_region,key_type,key_part_1,key_part_2
        ) WHERE status = 'RETRY';
        """
    )


def _install_integrity_guards() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.guard_dts_dirty_input_append_only_v2()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        BEGIN
            RAISE EXCEPTION 'DIRTY_INPUT_IMMUTABLE' USING ERRCODE = '42501';
        END
        $function$;

        CREATE TRIGGER guard_dts_dirty_input_append_only_v2
        BEFORE UPDATE OR DELETE ON public.dts_dirty_key_inputs
        FOR EACH ROW EXECUTE FUNCTION
            public.guard_dts_dirty_input_append_only_v2();

        CREATE TRIGGER guard_dts_dirty_legacy_archive_append_only_v80
        BEFORE UPDATE OR DELETE ON public.dts_dirty_keys_legacy_archive_v80
        FOR EACH ROW EXECUTE FUNCTION
            public.guard_dts_dirty_input_append_only_v2();

        CREATE FUNCTION public.check_dts_dirty_key_integrity_v2()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE
            dirty public.dts_dirty_keys%ROWTYPE;
            max_work_revision bigint;
            dependency_count bigint;
        BEGIN
            SELECT * INTO dirty
            FROM public.dts_dirty_keys
            WHERE source_region = coalesce(NEW.source_region, OLD.source_region)
              AND key_type = coalesce(NEW.key_type, OLD.key_type)
              AND key_part_1 = coalesce(NEW.key_part_1, OLD.key_part_1)
              AND key_part_2 = coalesce(NEW.key_part_2, OLD.key_part_2);
            IF NOT FOUND THEN
                RETURN NULL;
            END IF;

            SELECT max(dirty_work_revision) INTO max_work_revision
            FROM public.dts_dirty_key_inputs
            WHERE source_region = dirty.source_region
              AND key_type = dirty.key_type
              AND key_part_1 = dirty.key_part_1
              AND key_part_2 = dirty.key_part_2;
            IF max_work_revision IS NULL
               OR dirty.required_work_revision IS DISTINCT FROM
                    max_work_revision THEN
                RAISE EXCEPTION 'DIRTY_REQUIRED_REVISION_MISMATCH'
                    USING ERRCODE = '23514';
            END IF;

            SELECT count(*) INTO dependency_count
            FROM public.dts_dirty_key_dependencies
            WHERE source_region = dirty.source_region
              AND key_type = dirty.key_type
              AND key_part_1 = dirty.key_part_1
              AND key_part_2 = dirty.key_part_2;
            IF (dirty.status = 'WAITING_DEPENDENCY')
               IS DISTINCT FROM (dependency_count > 0) THEN
                RAISE EXCEPTION 'DIRTY_DEPENDENCY_STATE_MISMATCH'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NULL;
        END
        $function$;

        CREATE CONSTRAINT TRIGGER check_dts_dirty_key_integrity_from_key_v2
        AFTER INSERT OR UPDATE ON public.dts_dirty_keys
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION public.check_dts_dirty_key_integrity_v2();
        CREATE CONSTRAINT TRIGGER check_dts_dirty_key_integrity_from_input_v2
        AFTER INSERT OR UPDATE OR DELETE ON public.dts_dirty_key_inputs
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION public.check_dts_dirty_key_integrity_v2();
        CREATE CONSTRAINT TRIGGER check_dts_dirty_key_integrity_from_dep_v2
        AFTER INSERT OR UPDATE OR DELETE ON public.dts_dirty_key_dependencies
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION public.check_dts_dirty_key_integrity_v2();

        REVOKE ALL ON FUNCTION
            public.guard_dts_dirty_input_append_only_v2(),
            public.check_dts_dirty_key_integrity_v2()
        FROM PUBLIC;
        """
    )


def _install_input_functions() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public._upsert_dts_dirty_key_input_v2(
            p_source_region text,
            p_key_type text,
            p_key_part_1 text,
            p_key_part_2 text,
            p_input_kind text,
            p_input_identity jsonb,
            p_input_revision bigint,
            p_input_fingerprint text
        )
        RETURNS jsonb
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE
            dirty public.dts_dirty_keys%ROWTYPE;
            latest_input public.dts_dirty_key_inputs%ROWTYPE;
            identity_hash text;
            next_work_revision bigint;
            reopened_dead boolean := false;
        BEGIN
            IF public.dts_dirty_key_identity_valid_v2(
                    p_source_region,p_key_type,p_key_part_1,p_key_part_2
               ) IS DISTINCT FROM true
               OR p_input_kind NOT IN (
                    'SOURCE_REVISION','SCOPE_REVISION','CATALOG_REVISION',
                    'DEPENDENCY_WAKE','OPERATOR_RECOVERY','TIME_RECHECK'
               )
               OR jsonb_typeof(p_input_identity) IS DISTINCT FROM 'object'
               OR p_input_revision < 1
               OR p_input_fingerprint !~ '^[0-9a-f]{64}$' THEN
                RAISE EXCEPTION 'DIRTY_INPUT_AUTHORITY_DENIED'
                    USING ERRCODE = '42501';
            END IF;
            identity_hash := public.dts_canonical_json_sha256_v1(
                p_input_identity
            );

            SELECT * INTO dirty
            FROM public.dts_dirty_keys
            WHERE source_region = p_source_region
              AND key_type = p_key_type
              AND key_part_1 = p_key_part_1
              AND key_part_2 = p_key_part_2
            FOR UPDATE;

            SELECT * INTO latest_input
            FROM public.dts_dirty_key_inputs
            WHERE source_region = p_source_region
              AND key_type = p_key_type
              AND key_part_1 = p_key_part_1
              AND key_part_2 = p_key_part_2
              AND input_kind = p_input_kind
              AND input_identity_hash = identity_hash
            ORDER BY input_revision DESC
            LIMIT 1;

            IF FOUND AND p_input_revision = latest_input.input_revision THEN
                IF p_input_fingerprint IS DISTINCT FROM
                        latest_input.input_fingerprint THEN
                    RAISE EXCEPTION 'DIRTY_INPUT_CONFLICT'
                        USING ERRCODE = '23514';
                END IF;
                RETURN jsonb_build_object(
                    'status','NOOP',
                    'dirty_work_revision',latest_input.dirty_work_revision
                );
            ELSIF FOUND AND p_input_revision < latest_input.input_revision THEN
                RETURN jsonb_build_object(
                    'status','NOOP_OLDER',
                    'dirty_work_revision',latest_input.dirty_work_revision
                );
            END IF;

            IF dirty.source_region IS NULL THEN
                next_work_revision := 1;
                INSERT INTO public.dts_dirty_keys (
                    source_region,key_type,key_part_1,key_part_2,status,
                    pending_event_count,attempt_count,last_source_region,
                    last_source_table,last_topic,last_partition,last_offset,
                    issue_codes,last_error_code,next_attempt_at,claimed_at,
                    claimed_by,row_version,first_seen_at,last_seen_at,
                    required_work_revision,claimed_through_work_revision,
                    completed_work_revision,last_input_identity_hash,
                    last_input_revision,work_generation,dead_generation,
                    blocked_by,lease_owner_kind,lease_owner,lease_token,
                    lease_expires_at,created_at,updated_at
                ) VALUES (
                    p_source_region,p_key_type,p_key_part_1,p_key_part_2,
                    'PENDING',1,0,p_source_region,
                    nullif(p_input_identity ->> 'source_table',''),
                    'v2-dirty-input',0,0,'[]'::jsonb,NULL,
                    transaction_timestamp(),NULL,NULL,1,
                    clock_timestamp(),clock_timestamp(),1,NULL,0,
                    identity_hash,p_input_revision,1,0,NULL,NULL,NULL,NULL,NULL,
                    clock_timestamp(),clock_timestamp()
                );
            ELSE
                next_work_revision := dirty.required_work_revision + 1;
                reopened_dead := dirty.status = 'DEAD';
            END IF;

            INSERT INTO public.dts_dirty_key_inputs (
                source_region,key_type,key_part_1,key_part_2,input_kind,
                input_identity_hash,input_revision,input_identity,
                input_fingerprint,dirty_work_revision
            ) VALUES (
                p_source_region,p_key_type,p_key_part_1,p_key_part_2,
                p_input_kind,identity_hash,p_input_revision,p_input_identity,
                p_input_fingerprint,next_work_revision
            );

            IF dirty.source_region IS NOT NULL THEN
                IF dirty.status = 'PROCESSING' THEN
                    UPDATE public.dts_dirty_keys
                    SET required_work_revision = next_work_revision,
                        last_input_identity_hash = identity_hash,
                        last_input_revision = p_input_revision,
                        pending_event_count = pending_event_count + 1,
                        last_seen_at = clock_timestamp(),
                        updated_at = clock_timestamp(),
                        row_version = row_version + 1
                    WHERE source_region = p_source_region
                      AND key_type = p_key_type
                      AND key_part_1 = p_key_part_1
                      AND key_part_2 = p_key_part_2;
                ELSE
                    DELETE FROM public.dts_dirty_key_dependencies
                    WHERE source_region = p_source_region
                      AND key_type = p_key_type
                      AND key_part_1 = p_key_part_1
                      AND key_part_2 = p_key_part_2;
                    UPDATE public.dts_dirty_keys
                    SET status = 'PENDING',
                        required_work_revision = next_work_revision,
                        claimed_through_work_revision = NULL,
                        last_input_identity_hash = identity_hash,
                        last_input_revision = p_input_revision,
                        pending_event_count = pending_event_count + 1,
                        attempt_count = 0,
                        last_error_code = NULL,
                        next_attempt_at = transaction_timestamp(),
                        blocked_by = NULL,
                        lease_owner_kind = NULL,
                        lease_owner = NULL,
                        lease_token = NULL,
                        claimed_at = NULL,
                        lease_expires_at = NULL,
                        claimed_by = NULL,
                        last_seen_at = clock_timestamp(),
                        updated_at = clock_timestamp(),
                        row_version = row_version + 1
                    WHERE source_region = p_source_region
                      AND key_type = p_key_type
                      AND key_part_1 = p_key_part_1
                      AND key_part_2 = p_key_part_2;
                END IF;
            END IF;

            IF reopened_dead THEN
                INSERT INTO public.dts_dirty_key_state_audits (
                    source_region,key_type,key_part_1,key_part_2,event_type,
                    work_generation,dead_generation,detail
                ) VALUES (
                    p_source_region,p_key_type,p_key_part_1,p_key_part_2,
                    'DEAD_REOPENED_BY_INPUT',dirty.work_generation,
                    dirty.dead_generation,
                    jsonb_build_object(
                        'input_kind',p_input_kind,
                        'input_identity_hash',identity_hash,
                        'input_revision',p_input_revision
                    )
                );
            END IF;
            RETURN jsonb_build_object(
                'status','ENQUEUED',
                'dirty_work_revision',next_work_revision
            );
        END
        $function$;

        CREATE FUNCTION public.enqueue_dirty_from_source_revision_v2(
            p_source_region text,
            p_source_table text,
            p_source_key text,
            p_source_row_revision bigint,
            p_key_type text,
            p_key_part_1 text,
            p_key_part_2 text
        )
        RETURNS jsonb
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE
            version public.dts_source_row_versions%ROWTYPE;
            current_row public.dts_source_rows%ROWTYPE;
            identity jsonb;
            fingerprint text;
        BEGIN
            SELECT * INTO version
            FROM public.dts_source_row_versions
            WHERE source_region = p_source_region
              AND source_table = p_source_table
              AND source_key = p_source_key
              AND source_row_revision = p_source_row_revision
            FOR KEY SHARE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'DIRTY_INPUT_REFERENCE_INVALID'
                    USING ERRCODE = '23503';
            END IF;

            SELECT * INTO current_row
            FROM public.dts_source_rows
            WHERE source_region = p_source_region
              AND source_table = p_source_table
              AND source_key = p_source_key
            FOR KEY SHARE;
            IF NOT FOUND
               OR current_row.provenance_state IS DISTINCT FROM 'V2_CONFIRMED'
               OR current_row.source_row_revision IS DISTINCT FROM
                    p_source_row_revision
               OR current_row.source_payload_hash IS DISTINCT FROM
                    version.protected_source_row_hash THEN
                RAISE EXCEPTION 'DIRTY_INPUT_REFERENCE_INVALID'
                    USING ERRCODE = '23503';
            END IF;

            identity := jsonb_build_object(
                'source_region',p_source_region,
                'source_table',p_source_table,
                'source_key',p_source_key
            );
            fingerprint := public.dts_canonical_json_sha256_v1(
                jsonb_build_object(
                    'protocol','dirty-source-v1',
                    'identity',identity,
                    'revision',p_source_row_revision,
                    'version_kind',version.version_kind,
                    'operation',version.operation,
                    'is_deleted',current_row.is_deleted,
                    'protected_source_row_hash',
                        version.protected_source_row_hash
                )
            );
            RETURN public._upsert_dts_dirty_key_input_v2(
                p_source_region,p_key_type,p_key_part_1,p_key_part_2,
                'SOURCE_REVISION',identity,p_source_row_revision,fingerprint
            );
        END
        $function$;

        REVOKE ALL ON FUNCTION
            public._upsert_dts_dirty_key_input_v2(
                text,text,text,text,text,jsonb,bigint,text
            ),
            public.enqueue_dirty_from_source_revision_v2(
                text,text,text,bigint,text,text,text
            )
        FROM PUBLIC;

        GRANT EXECUTE ON FUNCTION
            public.enqueue_dirty_from_source_revision_v2(
                text,text,text,bigint,text,text,text
            )
        TO tit_dts_ingest_runtime;
        """
    )


def _install_domain_state_machine() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public._claim_dts_dirty_keys_v2(
            p_owner_kind text,
            p_worker_id text,
            p_batch_size integer,
            p_lease_seconds integer
        )
        RETURNS TABLE (
            source_region text,key_type text,key_part_1 text,key_part_2 text,
            lease_token text,claimed_work_revision bigint,row_version bigint
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE
            candidate record;
            new_token text;
        BEGIN
            IF p_owner_kind <> 'DOMAIN_PROJECTOR'
               OR p_worker_id IS NULL OR btrim(p_worker_id) = ''
               OR p_batch_size NOT BETWEEN 1 AND 1000
               OR p_lease_seconds NOT BETWEEN 15 AND 300 THEN
                RAISE EXCEPTION 'DIRTY_CLAIM_INVALID'
                    USING ERRCODE = '22023';
            END IF;
            FOR candidate IN
                SELECT dirty.source_region,dirty.key_type,
                       dirty.key_part_1,dirty.key_part_2
                FROM public.dts_dirty_keys AS dirty
                WHERE dirty.key_type IN (
                        'COURSE','TEACHER','TEACHER_STUDENT','LABEL',
                        'COMPLAINT_CATEGORY'
                      )
                  AND dirty.status IN ('PENDING','RETRY')
                  AND dirty.next_attempt_at <= transaction_timestamp()
                ORDER BY dirty.next_attempt_at,dirty.updated_at,
                         convert_to(dirty.source_region,'UTF8'),
                         convert_to(dirty.key_type,'UTF8'),
                         convert_to(dirty.key_part_1,'UTF8'),
                         convert_to(dirty.key_part_2,'UTF8')
                FOR UPDATE SKIP LOCKED
                LIMIT p_batch_size
            LOOP
                new_token := gen_random_uuid()::text;
                UPDATE public.dts_dirty_keys AS dirty
                SET status = 'PROCESSING',
                    claimed_through_work_revision =
                        dirty.required_work_revision,
                    lease_owner_kind = p_owner_kind,
                    lease_owner = p_worker_id,
                    lease_token = new_token,
                    claimed_at = transaction_timestamp(),
                    lease_expires_at = transaction_timestamp()
                        + make_interval(secs => p_lease_seconds),
                    next_attempt_at = NULL,
                    row_version = dirty.row_version + 1,
                    updated_at = clock_timestamp()
                WHERE dirty.source_region = candidate.source_region
                  AND dirty.key_type = candidate.key_type
                  AND dirty.key_part_1 = candidate.key_part_1
                  AND dirty.key_part_2 = candidate.key_part_2
                RETURNING dirty.source_region,dirty.key_type,
                          dirty.key_part_1,dirty.key_part_2,
                          dirty.lease_token,
                          dirty.claimed_through_work_revision,
                          dirty.row_version
                INTO source_region,key_type,key_part_1,key_part_2,
                     lease_token,claimed_work_revision,row_version;
                RETURN NEXT;
            END LOOP;
        END
        $function$;

        CREATE FUNCTION public.claim_domain_dirty_keys_v2(
            p_worker_id text,p_batch_size integer,p_lease_seconds integer
        )
        RETURNS TABLE (
            source_region text,key_type text,key_part_1 text,key_part_2 text,
            lease_token text,claimed_work_revision bigint,row_version bigint
        )
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
            SELECT * FROM public._claim_dts_dirty_keys_v2(
                'DOMAIN_PROJECTOR',p_worker_id,p_batch_size,p_lease_seconds
            )
        $function$;

        CREATE FUNCTION public._renew_dts_dirty_key_v2(
            p_owner_kind text,p_source_region text,p_key_type text,
            p_key_part_1 text,p_key_part_2 text,p_lease_token text,
            p_expected_row_version bigint,p_lease_seconds integer
        )
        RETURNS bigint
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE new_row_version bigint;
        BEGIN
            IF p_lease_seconds NOT BETWEEN 15 AND 300 THEN
                RAISE EXCEPTION 'DIRTY_LEASE_INVALID' USING ERRCODE='22023';
            END IF;
            -- A valid new input may advance row_version while deliberately
            -- preserving this lease.  The unforgeable token and frozen
            -- claimed revision remain the lease authority; the caller's
            -- version is therefore a monotonic lower bound, not equality.
            UPDATE public.dts_dirty_keys
            SET lease_expires_at = transaction_timestamp()
                    + make_interval(secs => p_lease_seconds),
                row_version = row_version + 1,
                updated_at = clock_timestamp()
            WHERE source_region=p_source_region AND key_type=p_key_type
              AND key_part_1=p_key_part_1 AND key_part_2=p_key_part_2
              AND status='PROCESSING'
              AND lease_owner_kind=p_owner_kind
              AND lease_token=p_lease_token
              AND p_expected_row_version >= 1
              AND row_version >= p_expected_row_version
              AND lease_expires_at > transaction_timestamp()
            RETURNING row_version INTO new_row_version;
            IF new_row_version IS NULL THEN
                RAISE EXCEPTION 'DIRTY_LEASE_LOST' USING ERRCODE='40001';
            END IF;
            RETURN new_row_version;
        END
        $function$;

        CREATE FUNCTION public.renew_domain_dirty_key_v2(
            p_source_region text,p_key_type text,p_key_part_1 text,
            p_key_part_2 text,p_lease_token text,
            p_expected_row_version bigint,p_lease_seconds integer
        ) RETURNS bigint
        LANGUAGE sql SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
            SELECT public._renew_dts_dirty_key_v2(
                'DOMAIN_PROJECTOR',p_source_region,p_key_type,p_key_part_1,
                p_key_part_2,p_lease_token,p_expected_row_version,
                p_lease_seconds
            )
        $function$;

        CREATE FUNCTION public._complete_dts_dirty_key_v2(
            p_owner_kind text,p_source_region text,p_key_type text,
            p_key_part_1 text,p_key_part_2 text,p_lease_token text,
            p_claimed_work_revision bigint,p_expected_row_version bigint
        ) RETURNS jsonb
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE dirty public.dts_dirty_keys%ROWTYPE;
        BEGIN
            SELECT * INTO dirty FROM public.dts_dirty_keys
            WHERE source_region=p_source_region AND key_type=p_key_type
              AND key_part_1=p_key_part_1 AND key_part_2=p_key_part_2
            FOR UPDATE;
            IF NOT FOUND OR dirty.status <> 'PROCESSING'
               OR dirty.lease_owner_kind IS DISTINCT FROM p_owner_kind
               OR dirty.lease_token IS DISTINCT FROM p_lease_token
               OR p_expected_row_version < 1
               OR dirty.row_version < p_expected_row_version
               OR dirty.claimed_through_work_revision IS DISTINCT FROM
                    p_claimed_work_revision
               OR dirty.lease_expires_at <= transaction_timestamp() THEN
                RAISE EXCEPTION 'DIRTY_LEASE_LOST' USING ERRCODE='40001';
            END IF;
            UPDATE public.dts_dirty_keys
            SET status = CASE WHEN required_work_revision >
                                   p_claimed_work_revision
                              THEN 'PENDING' ELSE 'COMPLETED' END,
                completed_work_revision = greatest(
                    completed_work_revision,p_claimed_work_revision
                ),
                attempt_count=0,last_error_code=NULL,blocked_by=NULL,
                next_attempt_at = CASE WHEN required_work_revision >
                                            p_claimed_work_revision
                                       THEN transaction_timestamp()
                                       ELSE NULL END,
                claimed_through_work_revision=NULL,lease_owner_kind=NULL,
                lease_owner=NULL,lease_token=NULL,claimed_at=NULL,
                lease_expires_at=NULL,claimed_by=NULL,
                row_version=row_version+1,updated_at=clock_timestamp()
            WHERE source_region=p_source_region AND key_type=p_key_type
              AND key_part_1=p_key_part_1 AND key_part_2=p_key_part_2
            RETURNING * INTO dirty;
            RETURN jsonb_build_object(
                'status',dirty.status,
                'completed_work_revision',dirty.completed_work_revision,
                'row_version',dirty.row_version
            );
        END
        $function$;

        CREATE FUNCTION public.complete_domain_dirty_key_v2(
            p_source_region text,p_key_type text,p_key_part_1 text,
            p_key_part_2 text,p_lease_token text,
            p_claimed_work_revision bigint,p_expected_row_version bigint
        ) RETURNS jsonb
        LANGUAGE sql SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
            SELECT public._complete_dts_dirty_key_v2(
                'DOMAIN_PROJECTOR',p_source_region,p_key_type,p_key_part_1,
                p_key_part_2,p_lease_token,p_claimed_work_revision,
                p_expected_row_version
            )
        $function$;

        CREATE FUNCTION public._wait_dts_dirty_key_v2(
            p_owner_kind text,p_source_region text,p_key_type text,
            p_key_part_1 text,p_key_part_2 text,p_lease_token text,
            p_claimed_work_revision bigint,p_expected_row_version bigint,
            p_dependencies jsonb
        ) RETURNS jsonb
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE dirty public.dts_dirty_keys%ROWTYPE;
        DECLARE canonical_dependencies jsonb;
        BEGIN
            IF jsonb_typeof(p_dependencies) IS DISTINCT FROM 'array'
               OR jsonb_array_length(p_dependencies)=0
               OR EXISTS (
                    SELECT 1 FROM jsonb_array_elements(p_dependencies) item
                    WHERE jsonb_typeof(item) <> 'object'
                       OR (SELECT count(*) FROM jsonb_object_keys(item)) <> 5
                       OR NOT item ?& ARRAY[
                            'dependency_type','dependency_region',
                            'dependency_key','source_revision','dependency_hash'
                          ]
                       OR item->>'dependency_type'
                            !~ '^[A-Z][A-Z0-9_]{0,63}$'
                       OR item->>'dependency_region' NOT IN ('dom','ovs')
                       OR btrim(item->>'dependency_key')=''
                       OR (item->>'source_revision')::bigint < 1
                       OR item->>'dependency_hash' !~ '^[0-9a-f]{64}$'
               ) THEN
                RAISE EXCEPTION 'DIRTY_DEPENDENCY_INVALID'
                    USING ERRCODE='22023';
            END IF;
            SELECT jsonb_agg(item ORDER BY
                    convert_to(item->>'dependency_type','UTF8'),
                    convert_to(item->>'dependency_region','UTF8'),
                    convert_to(item->>'dependency_key','UTF8'))
            INTO canonical_dependencies
            FROM jsonb_array_elements(p_dependencies) item;
            IF canonical_dependencies IS DISTINCT FROM p_dependencies
               OR (SELECT count(*) FROM jsonb_array_elements(p_dependencies))
                  <> (SELECT count(*) FROM (
                        SELECT DISTINCT item->>'dependency_type',
                                        item->>'dependency_region',
                                        item->>'dependency_key'
                        FROM jsonb_array_elements(p_dependencies) item
                     ) unique_dependencies) THEN
                RAISE EXCEPTION 'DIRTY_DEPENDENCY_INVALID'
                    USING ERRCODE='22023';
            END IF;

            SELECT * INTO dirty FROM public.dts_dirty_keys
            WHERE source_region=p_source_region AND key_type=p_key_type
              AND key_part_1=p_key_part_1 AND key_part_2=p_key_part_2
            FOR UPDATE;
            IF NOT FOUND OR dirty.status <> 'PROCESSING'
               OR dirty.lease_owner_kind IS DISTINCT FROM p_owner_kind
               OR dirty.lease_token IS DISTINCT FROM p_lease_token
               OR p_expected_row_version < 1
               OR dirty.row_version < p_expected_row_version
               OR dirty.claimed_through_work_revision IS DISTINCT FROM
                    p_claimed_work_revision
               OR dirty.lease_expires_at <= transaction_timestamp() THEN
                RAISE EXCEPTION 'DIRTY_LEASE_LOST' USING ERRCODE='40001';
            END IF;
            DELETE FROM public.dts_dirty_key_dependencies
            WHERE source_region=p_source_region AND key_type=p_key_type
              AND key_part_1=p_key_part_1 AND key_part_2=p_key_part_2;
            IF dirty.required_work_revision > p_claimed_work_revision THEN
                UPDATE public.dts_dirty_keys
                SET status='PENDING',attempt_count=0,last_error_code=NULL,
                    blocked_by=NULL,next_attempt_at=transaction_timestamp(),
                    claimed_through_work_revision=NULL,lease_owner_kind=NULL,
                    lease_owner=NULL,lease_token=NULL,claimed_at=NULL,
                    lease_expires_at=NULL,claimed_by=NULL,
                    row_version=row_version+1,updated_at=clock_timestamp()
                WHERE source_region=p_source_region AND key_type=p_key_type
                  AND key_part_1=p_key_part_1 AND key_part_2=p_key_part_2
                RETURNING * INTO dirty;
            ELSE
                INSERT INTO public.dts_dirty_key_dependencies (
                    source_region,key_type,key_part_1,key_part_2,
                    dependency_type,dependency_region,dependency_key,
                    observed_source_revision,observed_dependency_hash
                )
                SELECT p_source_region,p_key_type,p_key_part_1,p_key_part_2,
                       item->>'dependency_type',item->>'dependency_region',
                       item->>'dependency_key',
                       (item->>'source_revision')::bigint,
                       item->>'dependency_hash'
                FROM jsonb_array_elements(p_dependencies) item;
                UPDATE public.dts_dirty_keys
                SET status='WAITING_DEPENDENCY',blocked_by=p_dependencies,
                    next_attempt_at=NULL,last_error_code=NULL,
                    claimed_through_work_revision=NULL,lease_owner_kind=NULL,
                    lease_owner=NULL,lease_token=NULL,claimed_at=NULL,
                    lease_expires_at=NULL,claimed_by=NULL,
                    row_version=row_version+1,updated_at=clock_timestamp()
                WHERE source_region=p_source_region AND key_type=p_key_type
                  AND key_part_1=p_key_part_1 AND key_part_2=p_key_part_2
                RETURNING * INTO dirty;
            END IF;
            RETURN jsonb_build_object('status',dirty.status,
                                      'row_version',dirty.row_version);
        EXCEPTION WHEN invalid_text_representation OR numeric_value_out_of_range
        THEN
            RAISE EXCEPTION 'DIRTY_DEPENDENCY_INVALID'
                USING ERRCODE='22023';
        END
        $function$;

        CREATE FUNCTION public.wait_domain_dirty_key_v2(
            p_source_region text,p_key_type text,p_key_part_1 text,
            p_key_part_2 text,p_lease_token text,
            p_claimed_work_revision bigint,p_expected_row_version bigint,
            p_dependencies jsonb
        ) RETURNS jsonb
        LANGUAGE sql SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
            SELECT public._wait_dts_dirty_key_v2(
                'DOMAIN_PROJECTOR',p_source_region,p_key_type,p_key_part_1,
                p_key_part_2,p_lease_token,p_claimed_work_revision,
                p_expected_row_version,p_dependencies
            )
        $function$;

        CREATE FUNCTION public.dts_dirty_transient_error_v2(p_error_code text)
        RETURNS boolean LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
        SET search_path = pg_catalog, public
        AS $function$
            SELECT p_error_code IN (
                'DB_CONNECTION_TRANSIENT','DB_SERIALIZATION_TRANSIENT',
                'DB_DEADLOCK_TRANSIENT','DOMAIN_PROJECTOR_TRANSIENT',
                'DIRTY_LEASE_EXPIRED'
            )
        $function$;

        CREATE FUNCTION public._fail_dts_dirty_key_v2(
            p_owner_kind text,p_source_region text,p_key_type text,
            p_key_part_1 text,p_key_part_2 text,p_lease_token text,
            p_claimed_work_revision bigint,p_expected_row_version bigint,
            p_error_code text
        ) RETURNS jsonb
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE dirty public.dts_dirty_keys%ROWTYPE;
        DECLARE next_attempt integer;
        BEGIN
            IF public.dts_dirty_transient_error_v2(p_error_code)
                    IS DISTINCT FROM true THEN
                RAISE EXCEPTION 'DIRTY_ERROR_NOT_TRANSIENT'
                    USING ERRCODE='22023';
            END IF;
            SELECT * INTO dirty FROM public.dts_dirty_keys
            WHERE source_region=p_source_region AND key_type=p_key_type
              AND key_part_1=p_key_part_1 AND key_part_2=p_key_part_2
            FOR UPDATE;
            IF NOT FOUND OR dirty.status <> 'PROCESSING'
               OR dirty.lease_owner_kind IS DISTINCT FROM p_owner_kind
               OR dirty.lease_token IS DISTINCT FROM p_lease_token
               OR p_expected_row_version < 1
               OR dirty.row_version < p_expected_row_version
               OR dirty.claimed_through_work_revision IS DISTINCT FROM
                    p_claimed_work_revision
               OR dirty.lease_expires_at <= transaction_timestamp() THEN
                RAISE EXCEPTION 'DIRTY_LEASE_LOST' USING ERRCODE='40001';
            END IF;
            IF dirty.required_work_revision > p_claimed_work_revision THEN
                UPDATE public.dts_dirty_keys
                SET status='PENDING',attempt_count=0,last_error_code=NULL,
                    next_attempt_at=transaction_timestamp(),blocked_by=NULL,
                    claimed_through_work_revision=NULL,lease_owner_kind=NULL,
                    lease_owner=NULL,lease_token=NULL,claimed_at=NULL,
                    lease_expires_at=NULL,claimed_by=NULL,
                    row_version=row_version+1,updated_at=clock_timestamp()
                WHERE source_region=p_source_region AND key_type=p_key_type
                  AND key_part_1=p_key_part_1 AND key_part_2=p_key_part_2
                RETURNING * INTO dirty;
            ELSE
                next_attempt := dirty.attempt_count + 1;
                UPDATE public.dts_dirty_keys
                SET status=CASE WHEN next_attempt=8 THEN 'DEAD'
                                ELSE 'RETRY' END,
                    attempt_count=next_attempt,
                    dead_generation=dead_generation
                        + CASE WHEN next_attempt=8 THEN 1 ELSE 0 END,
                    last_error_code=p_error_code,blocked_by=NULL,
                    next_attempt_at=CASE WHEN next_attempt=8 THEN NULL
                        ELSE transaction_timestamp() + least(
                            interval '30 minutes',
                            interval '5 seconds' * power(2,next_attempt-1)
                        ) END,
                    claimed_through_work_revision=NULL,lease_owner_kind=NULL,
                    lease_owner=NULL,lease_token=NULL,claimed_at=NULL,
                    lease_expires_at=NULL,claimed_by=NULL,
                    row_version=row_version+1,updated_at=clock_timestamp()
                WHERE source_region=p_source_region AND key_type=p_key_type
                  AND key_part_1=p_key_part_1 AND key_part_2=p_key_part_2
                RETURNING * INTO dirty;
                IF next_attempt=8 THEN
                    INSERT INTO public.dts_dirty_key_state_audits (
                        source_region,key_type,key_part_1,key_part_2,event_type,
                        work_generation,dead_generation,detail
                    ) VALUES (
                        p_source_region,p_key_type,p_key_part_1,p_key_part_2,
                        'DEAD',dirty.work_generation,dirty.dead_generation,
                        jsonb_build_object('error_code',p_error_code)
                    );
                END IF;
            END IF;
            RETURN jsonb_build_object('status',dirty.status,
                'attempt_count',dirty.attempt_count,
                'dead_generation',dirty.dead_generation,
                'row_version',dirty.row_version);
        END
        $function$;

        CREATE FUNCTION public.fail_domain_dirty_key_v2(
            p_source_region text,p_key_type text,p_key_part_1 text,
            p_key_part_2 text,p_lease_token text,
            p_claimed_work_revision bigint,p_expected_row_version bigint,
            p_error_code text
        ) RETURNS jsonb LANGUAGE sql SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
            SELECT public._fail_dts_dirty_key_v2(
                'DOMAIN_PROJECTOR',p_source_region,p_key_type,p_key_part_1,
                p_key_part_2,p_lease_token,p_claimed_work_revision,
                p_expected_row_version,p_error_code
            )
        $function$;

        CREATE FUNCTION public._reap_expired_dts_dirty_keys_v2(
            p_owner_kind text,p_batch_size integer
        ) RETURNS integer
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE dirty public.dts_dirty_keys%ROWTYPE;
        DECLARE reaped integer := 0;
        DECLARE next_attempt integer;
        BEGIN
            IF p_owner_kind <> 'DOMAIN_PROJECTOR'
               OR p_batch_size NOT BETWEEN 1 AND 1000 THEN
                RAISE EXCEPTION 'DIRTY_REAPER_INVALID' USING ERRCODE='22023';
            END IF;
            FOR dirty IN
                SELECT * FROM public.dts_dirty_keys
                WHERE status='PROCESSING'
                  AND lease_owner_kind=p_owner_kind
                  AND lease_expires_at <= transaction_timestamp()
                ORDER BY lease_expires_at,source_region,key_type,
                         key_part_1,key_part_2
                FOR UPDATE SKIP LOCKED LIMIT p_batch_size
            LOOP
                IF dirty.required_work_revision >
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
                    next_attempt := dirty.attempt_count + 1;
                    UPDATE public.dts_dirty_keys
                    SET status=CASE WHEN next_attempt=8 THEN 'DEAD'
                                    ELSE 'RETRY' END,
                        attempt_count=next_attempt,
                        dead_generation=dead_generation
                            + CASE WHEN next_attempt=8 THEN 1 ELSE 0 END,
                        last_error_code='DIRTY_LEASE_EXPIRED',blocked_by=NULL,
                        next_attempt_at=CASE WHEN next_attempt=8 THEN NULL
                            ELSE transaction_timestamp() + least(
                                interval '30 minutes',
                                interval '5 seconds' * power(2,next_attempt-1)
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
                        INSERT INTO public.dts_dirty_key_state_audits (
                            source_region,key_type,key_part_1,key_part_2,
                            event_type,work_generation,dead_generation,detail
                        ) VALUES (
                            dirty.source_region,dirty.key_type,
                            dirty.key_part_1,dirty.key_part_2,'DEAD',
                            dirty.work_generation,dirty.dead_generation+1,
                            jsonb_build_object(
                                'error_code','DIRTY_LEASE_EXPIRED'
                            )
                        );
                    END IF;
                END IF;
                reaped := reaped + 1;
            END LOOP;
            RETURN reaped;
        END
        $function$;

        CREATE FUNCTION public.reap_expired_domain_dirty_keys_v2(
            p_batch_size integer
        ) RETURNS integer LANGUAGE sql SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
            SELECT public._reap_expired_dts_dirty_keys_v2(
                'DOMAIN_PROJECTOR',p_batch_size
            )
        $function$;
        """
    )


def _install_wake_and_recovery() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.wake_dts_dirty_keys_for_dependency_v2(
            p_dependency_type text,p_dependency_region text,
            p_dependency_key text,p_source_revision bigint,
            p_dependency_hash text
        ) RETURNS integer
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE dependency record;
        DECLARE identity jsonb;
        DECLARE fingerprint text;
        DECLARE wake_count integer := 0;
        BEGIN
            IF p_dependency_type !~ '^[A-Z][A-Z0-9_]{0,63}$'
               OR p_dependency_region NOT IN ('dom','ovs')
               OR btrim(p_dependency_key)=''
               OR p_source_revision < 1
               OR p_dependency_hash !~ '^[0-9a-f]{64}$' THEN
                RAISE EXCEPTION 'DIRTY_INPUT_REFERENCE_INVALID'
                    USING ERRCODE='23503';
            END IF;
            identity := jsonb_build_object(
                'dependency_type',p_dependency_type,
                'dependency_region',p_dependency_region,
                'dependency_key',p_dependency_key
            );
            fingerprint := public.dts_canonical_json_sha256_v1(
                jsonb_build_object(
                    'protocol','dirty-dependency-v1','identity',identity,
                    'revision',p_source_revision,
                    'dependency_hash',p_dependency_hash
                )
            );
            FOR dependency IN
                SELECT * FROM public.dts_dirty_key_dependencies
                WHERE dependency_type=p_dependency_type
                  AND dependency_region=p_dependency_region
                  AND dependency_key=p_dependency_key
                  AND (observed_source_revision IS DISTINCT FROM
                           p_source_revision
                       OR observed_dependency_hash IS DISTINCT FROM
                           p_dependency_hash)
                ORDER BY source_region,key_type,key_part_1,key_part_2
                FOR UPDATE
            LOOP
                PERFORM public._upsert_dts_dirty_key_input_v2(
                    dependency.source_region,dependency.key_type,
                    dependency.key_part_1,dependency.key_part_2,
                    'DEPENDENCY_WAKE',identity,p_source_revision,fingerprint
                );
                wake_count := wake_count + 1;
            END LOOP;
            RETURN wake_count;
        END
        $function$;

        CREATE FUNCTION public.recover_dts_dirty_key_v2(
            p_source_region text,p_key_type text,p_key_part_1 text,
            p_key_part_2 text,p_expected_dead_generation bigint,
            p_expected_row_version bigint,p_operator_request_id text,
            p_reason text
        ) RETURNS jsonb
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE dirty public.dts_dirty_keys%ROWTYPE;
        DECLARE identity jsonb;
        DECLARE identity_hash text;
        DECLARE fingerprint text;
        DECLARE prior public.dts_dirty_key_inputs%ROWTYPE;
        DECLARE next_work_revision bigint;
        BEGIN
            IF btrim(coalesce(p_operator_request_id,''))=''
               OR btrim(coalesce(p_reason,''))='' THEN
                RAISE EXCEPTION 'DIRTY_RECOVERY_INVALID'
                    USING ERRCODE='22023';
            END IF;
            identity := jsonb_build_object(
                'canonical_dirty_key',public.dts_dirty_key_identity_v2(
                    p_source_region,p_key_type,p_key_part_1,p_key_part_2
                ),
                'dead_generation',p_expected_dead_generation,
                'operator_request_id',p_operator_request_id
            );
            identity_hash := public.dts_canonical_json_sha256_v1(identity);
            fingerprint := public.dts_canonical_json_sha256_v1(
                jsonb_build_object(
                    'protocol','dirty-recovery-v1','identity',identity,
                    'expected_row_version',p_expected_row_version,
                    'reason_hash',encode(
                        sha256(convert_to(p_reason,'UTF8')),'hex'
                    )
                )
            );
            SELECT * INTO dirty FROM public.dts_dirty_keys
            WHERE source_region=p_source_region AND key_type=p_key_type
              AND key_part_1=p_key_part_1 AND key_part_2=p_key_part_2
            FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'DIRTY_RECOVERY_INVALID'
                    USING ERRCODE='22023';
            END IF;
            SELECT * INTO prior FROM public.dts_dirty_key_inputs
            WHERE source_region=p_source_region AND key_type=p_key_type
              AND key_part_1=p_key_part_1 AND key_part_2=p_key_part_2
              AND input_kind='OPERATOR_RECOVERY'
              AND input_identity_hash=identity_hash AND input_revision=1;
            IF FOUND THEN
                IF prior.input_fingerprint IS DISTINCT FROM fingerprint THEN
                    RAISE EXCEPTION 'DIRTY_INPUT_CONFLICT'
                        USING ERRCODE='23514';
                END IF;
                RETURN jsonb_build_object(
                    'status','NOOP',
                    'dirty_work_revision',prior.dirty_work_revision
                );
            END IF;
            IF dirty.status <> 'DEAD'
               OR dirty.dead_generation IS DISTINCT FROM
                    p_expected_dead_generation
               OR dirty.row_version IS DISTINCT FROM p_expected_row_version THEN
                RAISE EXCEPTION 'DIRTY_RECOVERY_CONFLICT'
                    USING ERRCODE='40001';
            END IF;
            next_work_revision := dirty.required_work_revision + 1;
            INSERT INTO public.dts_dirty_key_inputs (
                source_region,key_type,key_part_1,key_part_2,input_kind,
                input_identity_hash,input_revision,input_identity,
                input_fingerprint,dirty_work_revision
            ) VALUES (
                p_source_region,p_key_type,p_key_part_1,p_key_part_2,
                'OPERATOR_RECOVERY',identity_hash,1,identity,fingerprint,
                next_work_revision
            );
            UPDATE public.dts_dirty_keys
            SET status='PENDING',required_work_revision=next_work_revision,
                last_input_identity_hash=identity_hash,last_input_revision=1,
                attempt_count=0,last_error_code=NULL,blocked_by=NULL,
                next_attempt_at=transaction_timestamp(),work_generation=
                    work_generation+1,row_version=row_version+1,
                updated_at=clock_timestamp()
            WHERE source_region=p_source_region AND key_type=p_key_type
              AND key_part_1=p_key_part_1 AND key_part_2=p_key_part_2
            RETURNING * INTO dirty;
            INSERT INTO public.dts_dirty_key_state_audits (
                source_region,key_type,key_part_1,key_part_2,event_type,
                work_generation,dead_generation,detail
            ) VALUES (
                p_source_region,p_key_type,p_key_part_1,p_key_part_2,
                'OPERATOR_RECOVERY',dirty.work_generation,
                dirty.dead_generation,
                jsonb_build_object(
                    'operator_request_id',p_operator_request_id,
                    'reason_hash',encode(
                        sha256(convert_to(p_reason,'UTF8')),'hex'
                    )
                )
            );
            RETURN jsonb_build_object(
                'status','PENDING','dirty_work_revision',next_work_revision,
                'row_version',dirty.row_version
            );
        END
        $function$;
        """
    )


def _apply_acl_and_function_grants() -> None:
    op.execute(
        r"""
        REVOKE ALL PRIVILEGES ON TABLE
            public.dts_dirty_keys,
            public.dts_dirty_keys_legacy_archive_v80,
            public.dts_dirty_key_inputs,
            public.dts_dirty_key_dependencies,
            public.dts_dirty_key_state_audits
        FROM PUBLIC, tit_growth_app, tit_dts_ingest_runtime;

        DO $dirty_optional_roles_acl$
        DECLARE role_name text;
        BEGIN
            FOREACH role_name IN ARRAY ARRAY[
                'tit_teacher_crud','tit_dts_domain_projector_runtime',
                'tit_source_wide_runtime'
            ]::text[] LOOP
                IF to_regrole(role_name) IS NOT NULL THEN
                    EXECUTE format(
                        'REVOKE ALL PRIVILEGES ON TABLE '
                        'public.dts_dirty_keys,'
                        'public.dts_dirty_keys_legacy_archive_v80,'
                        'public.dts_dirty_key_inputs,'
                        'public.dts_dirty_key_dependencies,'
                        'public.dts_dirty_key_state_audits FROM %I',
                        role_name
                    );
                END IF;
            END LOOP;

            IF to_regrole('tit_dts_domain_projector_runtime') IS NOT NULL THEN
                IF EXISTS (
                    SELECT 1 FROM pg_roles
                    WHERE rolname='tit_dts_domain_projector_runtime'
                      AND (NOT rolcanlogin OR rolinherit OR rolsuper
                           OR rolcreatedb OR rolcreaterole OR rolreplication
                           OR rolbypassrls)
                ) THEN
                    RAISE EXCEPTION
                        'tit_dts_domain_projector_runtime must be a restricted NOINHERIT LOGIN role';
                END IF;
                EXECUTE 'GRANT EXECUTE ON FUNCTION '
                    'public.claim_domain_dirty_keys_v2(text,integer,integer),'
                    'public.renew_domain_dirty_key_v2(text,text,text,text,text,bigint,integer),'
                    'public.complete_domain_dirty_key_v2(text,text,text,text,text,bigint,bigint),'
                    'public.wait_domain_dirty_key_v2(text,text,text,text,text,bigint,bigint,jsonb),'
                    'public.fail_domain_dirty_key_v2(text,text,text,text,text,bigint,bigint,text),'
                    'public.reap_expired_domain_dirty_keys_v2(integer) '
                    'TO tit_dts_domain_projector_runtime';
            END IF;
        END
        $dirty_optional_roles_acl$;

        REVOKE ALL ON FUNCTION
            public._claim_dts_dirty_keys_v2(text,text,integer,integer),
            public._renew_dts_dirty_key_v2(
                text,text,text,text,text,text,bigint,integer
            ),
            public._complete_dts_dirty_key_v2(
                text,text,text,text,text,text,bigint,bigint
            ),
            public._wait_dts_dirty_key_v2(
                text,text,text,text,text,text,bigint,bigint,jsonb
            ),
            public.dts_dirty_transient_error_v2(text),
            public._fail_dts_dirty_key_v2(
                text,text,text,text,text,text,bigint,bigint,text
            ),
            public._reap_expired_dts_dirty_keys_v2(text,integer),
            public.wake_dts_dirty_keys_for_dependency_v2(
                text,text,text,bigint,text
            ),
            public.recover_dts_dirty_key_v2(
                text,text,text,text,bigint,bigint,text,text
            )
        FROM PUBLIC, tit_growth_app, tit_dts_ingest_runtime;

        REVOKE ALL ON FUNCTION
            public.claim_domain_dirty_keys_v2(text,integer,integer),
            public.renew_domain_dirty_key_v2(
                text,text,text,text,text,bigint,integer
            ),
            public.complete_domain_dirty_key_v2(
                text,text,text,text,text,bigint,bigint
            ),
            public.wait_domain_dirty_key_v2(
                text,text,text,text,text,bigint,bigint,jsonb
            ),
            public.fail_domain_dirty_key_v2(
                text,text,text,text,text,bigint,bigint,text
            ),
            public.reap_expired_domain_dirty_keys_v2(integer)
        FROM PUBLIC, tit_growth_app, tit_dts_ingest_runtime;

        COMMENT ON TABLE public.dts_dirty_keys IS
            'Region-qualified v2 recomputation work; state changes only through SECURITY DEFINER commands.';
        COMMENT ON TABLE public.dts_dirty_key_inputs IS
            'Append-only authoritative dirty inputs with dirty-local work revisions.';
        COMMENT ON TABLE public.dts_dirty_keys_legacy_archive_v80 IS
            'Immutable verbatim archive of drained legacy dirty rows; never used as v2 business identity.';
        COMMENT ON TABLE public.dts_dirty_key_dependencies IS
            'Typed WAITING_DEPENDENCY reverse index; never scan blocked_by JSON for wakeups.';
        """
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    _install_canonical_helpers()
    _create_queue_tables()
    _migrate_legacy_queue()
    _install_queue_constraints_and_indexes()
    _install_integrity_guards()
    _install_input_functions()
    _install_domain_state_machine()
    _install_wake_and_recovery()
    _apply_acl_and_function_grants()


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    op.execute(
        r"""
        LOCK TABLE public.dts_dirty_keys,
                   public.dts_dirty_keys_legacy_archive_v80,
                   public.dts_dirty_key_inputs,
                   public.dts_dirty_key_dependencies,
                   public.dts_dirty_key_state_audits
        IN ACCESS EXCLUSIVE MODE;
        DO $dirty_v2_downgrade_guard$
        BEGIN
            IF EXISTS (
                    SELECT 1
                    FROM public.dts_dirty_keys_legacy_archive_v80 LIMIT 1
               )
               OR EXISTS (SELECT 1 FROM public.dts_dirty_key_inputs LIMIT 1)
               OR EXISTS (SELECT 1 FROM public.dts_dirty_key_dependencies LIMIT 1)
               OR EXISTS (SELECT 1 FROM public.dts_dirty_key_state_audits LIMIT 1)
               OR EXISTS (SELECT 1 FROM public.dts_dirty_keys LIMIT 1) THEN
                RAISE EXCEPTION
                    'refusing DTS v2 dirty-queue downgrade: queue history exists';
            END IF;
        END
        $dirty_v2_downgrade_guard$;
        """
    )
    # An empty-only downgrade can safely recreate the legacy shape.  The
    # application must have been rolled back before this command is used.
    op.execute(
        r"""
        DROP FUNCTION IF EXISTS public.recover_dts_dirty_key_v2(
            text,text,text,text,bigint,bigint,text,text);
        DROP FUNCTION IF EXISTS public.wake_dts_dirty_keys_for_dependency_v2(
            text,text,text,bigint,text);
        DROP FUNCTION IF EXISTS public.reap_expired_domain_dirty_keys_v2(integer);
        DROP FUNCTION IF EXISTS public._reap_expired_dts_dirty_keys_v2(text,integer);
        DROP FUNCTION IF EXISTS public.fail_domain_dirty_key_v2(
            text,text,text,text,text,bigint,bigint,text);
        DROP FUNCTION IF EXISTS public._fail_dts_dirty_key_v2(
            text,text,text,text,text,text,bigint,bigint,text);
        DROP FUNCTION IF EXISTS public.dts_dirty_transient_error_v2(text);
        DROP FUNCTION IF EXISTS public.wait_domain_dirty_key_v2(
            text,text,text,text,text,bigint,bigint,jsonb);
        DROP FUNCTION IF EXISTS public._wait_dts_dirty_key_v2(
            text,text,text,text,text,text,bigint,bigint,jsonb);
        DROP FUNCTION IF EXISTS public.complete_domain_dirty_key_v2(
            text,text,text,text,text,bigint,bigint);
        DROP FUNCTION IF EXISTS public._complete_dts_dirty_key_v2(
            text,text,text,text,text,text,bigint,bigint);
        DROP FUNCTION IF EXISTS public.renew_domain_dirty_key_v2(
            text,text,text,text,text,bigint,integer);
        DROP FUNCTION IF EXISTS public._renew_dts_dirty_key_v2(
            text,text,text,text,text,text,bigint,integer);
        DROP FUNCTION IF EXISTS public.claim_domain_dirty_keys_v2(
            text,integer,integer);
        DROP FUNCTION IF EXISTS public._claim_dts_dirty_keys_v2(
            text,text,integer,integer);
        DROP FUNCTION IF EXISTS public.enqueue_dirty_from_source_revision_v2(
            text,text,text,bigint,text,text,text);
        DROP FUNCTION IF EXISTS public._upsert_dts_dirty_key_input_v2(
            text,text,text,text,text,jsonb,bigint,text);
        DROP TRIGGER IF EXISTS check_dts_dirty_key_integrity_from_dep_v2
            ON public.dts_dirty_key_dependencies;
        DROP TRIGGER IF EXISTS check_dts_dirty_key_integrity_from_input_v2
            ON public.dts_dirty_key_inputs;
        DROP TRIGGER IF EXISTS check_dts_dirty_key_integrity_from_key_v2
            ON public.dts_dirty_keys;
        DROP FUNCTION IF EXISTS public.check_dts_dirty_key_integrity_v2();
        DROP TRIGGER IF EXISTS guard_dts_dirty_input_append_only_v2
            ON public.dts_dirty_key_inputs;
        DROP TRIGGER IF EXISTS guard_dts_dirty_legacy_archive_append_only_v80
            ON public.dts_dirty_keys_legacy_archive_v80;
        DROP FUNCTION IF EXISTS public.guard_dts_dirty_input_append_only_v2();
        DROP INDEX IF EXISTS public.ix_dts_dirty_keys_retry_due_v2;
        DROP INDEX IF EXISTS public.ix_dts_dirty_keys_pending_fifo_v2;
        DROP INDEX IF EXISTS public.ix_dts_dirty_keys_ready_v2;

        ALTER TABLE public.dts_dirty_key_inputs
            DROP CONSTRAINT fk_dts_dirty_key_input_key;
        ALTER TABLE public.dts_dirty_key_dependencies
            DROP CONSTRAINT fk_dts_dirty_key_dependency_key;
        ALTER TABLE public.dts_dirty_key_state_audits
            DROP CONSTRAINT fk_dts_dirty_key_state_audit_key;

        ALTER TABLE public.dts_dirty_keys
            DROP CONSTRAINT ck_dts_dirty_key_state_shape_v2,
            DROP CONSTRAINT ck_dts_dirty_key_lease_shape_v2,
            DROP CONSTRAINT ck_dts_dirty_key_revisions_v2,
            DROP CONSTRAINT ck_dts_dirty_key_status_v2,
            DROP CONSTRAINT ck_dts_dirty_key_identity_v2,
            DROP CONSTRAINT pk_dts_dirty_keys;
        ALTER TABLE public.dts_dirty_keys
            ADD CONSTRAINT pk_dts_dirty_keys PRIMARY KEY
                (key_type,key_part_1,key_part_2),
            ALTER COLUMN status TYPE varchar(16),
            ALTER COLUMN source_region DROP NOT NULL,
            ALTER COLUMN required_work_revision DROP NOT NULL,
            ALTER COLUMN completed_work_revision DROP NOT NULL,
            ALTER COLUMN last_input_identity_hash DROP NOT NULL,
            ALTER COLUMN last_input_revision DROP NOT NULL,
            ALTER COLUMN work_generation DROP NOT NULL,
            ALTER COLUMN dead_generation DROP NOT NULL,
            DROP COLUMN created_at,
            DROP COLUMN updated_at;
        ALTER TABLE public.dts_dirty_keys
            ADD CONSTRAINT ck_dts_dirty_key_type CHECK (
                key_type IN ('COURSE','TEACHER','TEACHER_STUDENT','LABEL',
                             'COMPLAINT_CATEGORY')
            ),
            ADD CONSTRAINT ck_dts_dirty_key_status CHECK (
                status IN ('PENDING','PROCESSING','RETRY','COMPLETED')
            ),
            ADD CONSTRAINT ck_dts_dirty_key_region CHECK (
                last_source_region IN ('ovs','dom')
            ),
            ADD CONSTRAINT ck_dts_dirty_key_counters CHECK (
                pending_event_count >= 1 AND attempt_count >= 0
                AND last_partition >= 0 AND last_offset >= 0
                AND row_version >= 1
            );
        CREATE INDEX ix_dts_dirty_keys_ready
            ON public.dts_dirty_keys(status,next_attempt_at,last_seen_at);
        CREATE INDEX ix_dts_dirty_keys_pending_fifo
            ON public.dts_dirty_keys(last_seen_at,key_type,key_part_1,key_part_2)
            WHERE status='PENDING';
        CREATE INDEX ix_dts_dirty_keys_retry_due
            ON public.dts_dirty_keys(
                next_attempt_at,last_seen_at,key_type,key_part_1,key_part_2
            ) WHERE status='RETRY' AND next_attempt_at < 'infinity';
        """
    )
    op.drop_table("dts_dirty_key_state_audits", schema="public")
    op.drop_index(
        "ix_dts_dirty_key_dependencies_reverse",
        table_name="dts_dirty_key_dependencies",
        schema="public",
    )
    op.drop_table("dts_dirty_key_dependencies", schema="public")
    op.drop_table("dts_dirty_key_inputs", schema="public")
    op.drop_table("dts_dirty_keys_legacy_archive_v80", schema="public")
    op.execute(
        r"""
        DROP FUNCTION IF EXISTS public.dts_dirty_key_identity_valid_v2(
            text,text,text,text);
        DROP FUNCTION IF EXISTS public.dts_dirty_key_identity_v2(
            text,text,text,text);
        DROP FUNCTION IF EXISTS public.dts_canonical_json_sha256_v1(jsonb);
        DROP FUNCTION IF EXISTS public.dts_canonical_json_v1(jsonb);
        """
    )
