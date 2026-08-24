"""guard the v2 source-version/current-row shadow pair.

Revision ID: 20260822_69_dts_v2_source_guard
Revises: 20260822_68_course_part_guards
Create Date: 2026-08-22

This revision remains shadow-only.  It validates typed source evidence and
requires the immutable latest source version and its current-row projection to
be committed as one consistent pair.  It grants no runtime privilege and does
not activate a writer or read route.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from alembic import op


revision: str = "20260822_69_dts_v2_source_guard"
down_revision: Union[str, None] = "20260822_68_course_part_guards"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SHADOW_SCHEMA_ONLY = True
DEFERRED_SOURCE_CURRENT_PAIR_GUARD_IMPLEMENTED = True


def _create_validators_and_constraints() -> None:
    op.execute(
        """
        CREATE FUNCTION public.dts_v2_source_field_types_valid(value jsonb)
        RETURNS boolean
        LANGUAGE sql
        IMMUTABLE
        STRICT
        SET search_path = pg_catalog, public
        AS $function$
            SELECT jsonb_typeof(value) = 'object'
               AND NOT EXISTS (
                    SELECT 1
                    FROM jsonb_each(value) AS field(field_name, field_type)
                    WHERE jsonb_typeof(field_type) <> 'string'
                       OR field_type #>> '{}' NOT IN (
                            'NUMERIC', 'TEXT', 'BOOLEAN', 'TEMPORAL'
                       )
               )
        $function$;

        CREATE FUNCTION public.dts_v2_source_row_transition_valid(
            current_source_key text,
            current_source_key_data jsonb,
            current_source_row_revision bigint,
            current_epoch_id text,
            current_version_kind text,
            current_position jsonb,
            current_record_id_type text,
            current_record_id_numeric numeric,
            current_record_id_text text,
            current_source_timestamp timestamptz,
            current_payload_hash text,
            current_provenance_state text,
            current_source_key_type text,
            current_source_key_numeric numeric,
            current_source_key_text text,
            current_schema_profile_id text,
            current_field_types jsonb
        )
        RETURNS boolean
        LANGUAGE plpgsql
        IMMUTABLE
        SET search_path = pg_catalog, public
        AS $function$
        BEGIN
            IF current_source_row_revision IS NULL
               AND current_epoch_id IS NULL
               AND current_version_kind IS NULL
               AND current_position IS NULL
               AND current_record_id_type IS NULL
               AND current_record_id_numeric IS NULL
               AND current_record_id_text IS NULL
               AND current_source_timestamp IS NULL
               AND current_payload_hash IS NULL
               AND current_provenance_state IS NULL
               AND current_source_key_type IS NULL
               AND current_source_key_numeric IS NULL
               AND current_source_key_text IS NULL
               AND current_schema_profile_id IS NULL
               AND current_field_types IS NULL THEN
                RETURN true;
            END IF;

            IF current_provenance_state = 'LEGACY_PENDING'
               AND current_source_row_revision IS NULL
               AND current_epoch_id IS NULL
               AND current_version_kind IS NULL
               AND current_position IS NULL
               AND current_record_id_type IS NULL
               AND current_record_id_numeric IS NULL
               AND current_record_id_text IS NULL
               AND current_source_timestamp IS NULL
               AND current_payload_hash IS NULL
               AND current_source_key_type IS NULL
               AND current_source_key_numeric IS NULL
               AND current_source_key_text IS NULL
               AND current_schema_profile_id IS NULL
               AND current_field_types IS NULL THEN
                RETURN true;
            END IF;

            IF current_provenance_state IS DISTINCT FROM 'V2_CONFIRMED'
               OR current_source_row_revision IS NULL
               OR current_source_row_revision < 1
               OR current_epoch_id IS NULL
               OR btrim(current_epoch_id) = ''
               OR current_version_kind NOT IN ('CDC', 'SNAPSHOT_DIFF')
               OR jsonb_typeof(current_position) IS DISTINCT FROM 'object'
               OR current_payload_hash IS NULL
               OR current_payload_hash !~ '^[0-9a-f]{64}$'
               OR current_schema_profile_id IS NULL
               OR btrim(current_schema_profile_id) = ''
               OR public.dts_v2_source_field_types_valid(
                    current_field_types
               ) IS DISTINCT FROM true
               OR current_field_types ->> 'id' IS DISTINCT FROM
                    current_source_key_type THEN
                RETURN false;
            END IF;

            IF NOT (
                (current_record_id_type = 'none'
                 AND current_record_id_numeric IS NULL
                 AND current_record_id_text IS NULL)
                OR (current_record_id_type = 'numeric'
                    AND current_record_id_numeric IS NOT NULL
                    AND current_record_id_text IS NULL)
                OR (current_record_id_type = 'text'
                    AND current_record_id_numeric IS NULL
                    AND current_record_id_text IS NOT NULL)
            ) THEN
                RETURN false;
            END IF;

            IF jsonb_typeof(current_source_key_data) <> 'object'
               OR current_source_key_data <> jsonb_build_object(
                    'id', current_source_key_data -> 'id'
               ) THEN
                RETURN false;
            END IF;

            IF current_source_key_type = 'NUMERIC' THEN
                IF jsonb_typeof(current_source_key_data -> 'id') <> 'number'
                   OR current_source_key_numeric IS NULL
                   OR current_source_key_text IS NOT NULL
                   OR current_source_key_numeric IS DISTINCT FROM
                        (current_source_key_data ->> 'id')::numeric
                   OR current_source_key IS DISTINCT FROM
                        trim_scale(current_source_key_numeric)::text THEN
                    RETURN false;
                END IF;
            ELSIF current_source_key_type = 'TEXT' THEN
                IF jsonb_typeof(current_source_key_data -> 'id') <> 'string'
                   OR current_source_key_numeric IS NOT NULL
                   OR current_source_key_text IS NULL
                   OR current_source_key_text = ''
                   OR current_source_key_text IS DISTINCT FROM
                        current_source_key_data ->> 'id'
                   OR current_source_key IS DISTINCT FROM
                        current_source_key_text THEN
                    RETURN false;
                END IF;
            ELSE
                RETURN false;
            END IF;

            RETURN true;
        EXCEPTION
            WHEN invalid_text_representation OR numeric_value_out_of_range THEN
                RETURN false;
        END
        $function$;

        ALTER TABLE public.dts_source_row_versions
        ADD CONSTRAINT ck_dts_source_row_version_field_type_values
        CHECK (
            public.dts_v2_source_field_types_valid(source_field_types) IS TRUE
            AND (
                source_row_revision IS NULL
                OR (
                    source_field_types ? 'id'
                    AND source_field_types ->> 'id'
                        IS NOT DISTINCT FROM source_key_type
                )
            )
        );

        ALTER TABLE public.dts_source_row_versions
        ADD CONSTRAINT ck_dts_source_row_version_profiled_key_type
        CHECK (
            source_table NOT IN ('dom_appoint', 'ovs_appoint')
            OR source_key_type = 'NUMERIC'
        );

        ALTER TABLE public.dts_source_rows
        ADD CONSTRAINT ck_dts_source_row_profiled_key_type
        CHECK (
            source_table NOT IN ('dom_appoint', 'ovs_appoint')
            OR source_key_type IS NULL
            OR source_key_type = 'NUMERIC'
        );

        ALTER TABLE public.dts_source_rows
        ADD CONSTRAINT ck_dts_source_row_v2_transition_shape
        CHECK (
            public.dts_v2_source_row_transition_valid(
                source_key,
                source_key_data,
                source_row_revision,
                last_source_partition_epoch_id,
                last_version_kind,
                source_position_v2,
                record_id_type,
                record_id_numeric,
                record_id_text,
                source_timestamp_v2,
                source_payload_hash,
                provenance_state,
                source_key_type,
                source_key_numeric,
                source_key_text,
                source_schema_profile_id,
                source_field_types
            ) IS TRUE
        );

        ALTER TABLE public.dts_source_rows
        ADD CONSTRAINT fk_dts_source_row_current_version_event
        FOREIGN KEY (
            source_region,
            last_source_partition_epoch_id,
            last_topic,
            last_partition,
            last_offset
        ) REFERENCES public.dts_source_row_versions (
            source_region,
            source_partition_epoch_id,
            topic,
            partition_id,
            offset_value
        )
        DEFERRABLE INITIALLY DEFERRED;
        """
    )


def _create_deferred_pair_guard() -> None:
    op.execute(
        """
        CREATE FUNCTION public.dts_v2_assert_source_current_pair(
            guard_region text,
            guard_table text,
            guard_key text
        )
        RETURNS void
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE
            latest_revision bigint;
            earliest_revision bigint;
            revision_count bigint;
            version_row public.dts_source_row_versions%ROWTYPE;
            current_row public.dts_source_rows%ROWTYPE;
            expected_row jsonb;
            expected_deleted boolean;
        BEGIN
            SELECT max(source_row_revision), min(source_row_revision), count(*)
            INTO latest_revision, earliest_revision, revision_count
            FROM public.dts_source_row_versions
            WHERE source_region = guard_region
              AND source_table = guard_table
              AND source_key = guard_key
              AND source_row_revision IS NOT NULL;

            SELECT *
            INTO current_row
            FROM public.dts_source_rows
            WHERE source_region = guard_region
              AND source_table = guard_table
              AND source_key = guard_key;

            IF latest_revision IS NULL THEN
                IF FOUND
                   AND current_row.provenance_state = 'V2_CONFIRMED' THEN
                    RAISE EXCEPTION
                        'DTS_V2_SOURCE_CURRENT_WITHOUT_VERSION: current row %/%/% has no revisioned version',
                        guard_region,
                        guard_table,
                        guard_key;
                END IF;
                RETURN;
            END IF;

            IF earliest_revision IS DISTINCT FROM 1
               OR revision_count IS DISTINCT FROM latest_revision THEN
                RAISE EXCEPTION
                    'DTS_V2_SOURCE_REVISION_GAP: source history for %/%/% must be contiguous from revision 1 through %',
                    guard_region,
                    guard_table,
                    guard_key,
                    latest_revision;
            END IF;

            IF NOT FOUND
               OR current_row.provenance_state IS DISTINCT FROM
                    'V2_CONFIRMED' THEN
                RAISE EXCEPTION
                    'DTS_V2_SOURCE_LATEST_VERSION_ORPHAN: latest version % for %/%/% has no confirmed current row',
                    latest_revision,
                    guard_region,
                    guard_table,
                    guard_key;
            END IF;

            SELECT *
            INTO STRICT version_row
            FROM public.dts_source_row_versions
            WHERE source_region = guard_region
              AND source_table = guard_table
              AND source_key = guard_key
              AND source_row_revision = latest_revision;

            IF version_row.operation IN (
                'DELETE',
                'SNAPSHOT_DELETE',
                'SNAPSHOT_BOOTSTRAP_TOMBSTONE'
            ) THEN
                expected_row := version_row.before_row;
                expected_deleted := true;
            ELSE
                expected_row := version_row.after_row;
                expected_deleted := false;
            END IF;

            IF expected_row IS NULL THEN
                RAISE EXCEPTION
                    'DTS_V2_SOURCE_VERSION_IMAGE_MISSING: latest version has no authoritative current image';
            END IF;

            IF current_row.source_row_revision IS DISTINCT FROM latest_revision
               OR current_row.last_source_partition_epoch_id IS DISTINCT FROM
                    version_row.source_partition_epoch_id
               OR current_row.last_topic IS DISTINCT FROM version_row.topic
               OR current_row.last_partition IS DISTINCT FROM
                    version_row.partition_id
               OR current_row.last_offset IS DISTINCT FROM
                    version_row.offset_value
               OR current_row.last_version_kind IS DISTINCT FROM
                    version_row.version_kind
               OR current_row.source_key_type IS DISTINCT FROM
                    version_row.source_key_type
               OR current_row.source_key_numeric IS DISTINCT FROM
                    version_row.source_key_numeric
               OR current_row.source_key_text IS DISTINCT FROM
                    version_row.source_key_text
               OR current_row.source_schema_profile_id IS DISTINCT FROM
                    version_row.source_schema_profile_id
               OR current_row.source_field_types IS DISTINCT FROM
                    version_row.source_field_types
               OR current_row.source_payload_hash IS DISTINCT FROM
                    version_row.protected_source_row_hash
               OR current_row.source_position_v2 IS DISTINCT FROM
                    version_row.source_position
               OR current_row.record_id_type IS DISTINCT FROM
                    version_row.record_id_type
               OR current_row.record_id_numeric IS DISTINCT FROM
                    version_row.record_id_numeric
               OR current_row.record_id_text IS DISTINCT FROM
                    version_row.record_id_text
               OR current_row.source_timestamp_v2 IS DISTINCT FROM
                    version_row.source_timestamp
               OR current_row.source_row IS DISTINCT FROM expected_row
               OR current_row.is_deleted IS DISTINCT FROM expected_deleted THEN
                RAISE EXCEPTION
                    'DTS_V2_SOURCE_CURRENT_VERSION_MISMATCH: current row does not equal latest protected version % for %/%/%',
                    latest_revision,
                    guard_region,
                    guard_table,
                    guard_key;
            END IF;
        END
        $function$;

        CREATE FUNCTION public.dts_v2_source_current_pair_guard()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $function$
        BEGIN
            IF TG_TABLE_NAME = 'dts_source_row_versions' THEN
                PERFORM public.dts_v2_assert_source_current_pair(
                    NEW.source_region,
                    NEW.source_table,
                    NEW.source_key
                );
                RETURN NULL;
            END IF;

            IF TG_OP IN ('UPDATE', 'DELETE') THEN
                PERFORM public.dts_v2_assert_source_current_pair(
                    OLD.source_region,
                    OLD.source_table,
                    OLD.source_key
                );
            END IF;
            IF TG_OP IN ('INSERT', 'UPDATE')
               AND (
                    TG_OP = 'INSERT'
                    OR ROW(OLD.source_region, OLD.source_table, OLD.source_key)
                       IS DISTINCT FROM
                       ROW(NEW.source_region, NEW.source_table, NEW.source_key)
                    OR NEW.provenance_state IS DISTINCT FROM
                       OLD.provenance_state
                    OR NEW.source_row_revision IS DISTINCT FROM
                       OLD.source_row_revision
                    OR NEW.source_payload_hash IS DISTINCT FROM
                       OLD.source_payload_hash
                    OR NEW.source_row IS DISTINCT FROM OLD.source_row
                    OR NEW.is_deleted IS DISTINCT FROM OLD.is_deleted
               ) THEN
                PERFORM public.dts_v2_assert_source_current_pair(
                    NEW.source_region,
                    NEW.source_table,
                    NEW.source_key
                );
            END IF;
            RETURN NULL;
        END
        $function$;

        CREATE CONSTRAINT TRIGGER ct_dts_source_version_current_pair_guard
        AFTER INSERT ON public.dts_source_row_versions
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE FUNCTION public.dts_v2_source_current_pair_guard();

        CREATE CONSTRAINT TRIGGER ct_dts_source_current_version_pair_guard
        AFTER INSERT OR UPDATE OR DELETE ON public.dts_source_rows
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE FUNCTION public.dts_v2_source_current_pair_guard();

        DO $validate_existing_source_pairs$
        DECLARE
            source_identity record;
        BEGIN
            FOR source_identity IN
                SELECT source_region, source_table, source_key
                FROM public.dts_source_row_versions
                WHERE source_row_revision IS NOT NULL
                UNION
                SELECT source_region, source_table, source_key
                FROM public.dts_source_rows
                WHERE provenance_state = 'V2_CONFIRMED'
            LOOP
                PERFORM public.dts_v2_assert_source_current_pair(
                    source_identity.source_region,
                    source_identity.source_table,
                    source_identity.source_key
                );
            END LOOP;
        END
        $validate_existing_source_pairs$;
        """
    )


def _lock_shadow_guards_from_runtime() -> None:
    op.execute(
        """
        REVOKE ALL PRIVILEGES ON TABLE
            public.dts_source_partition_epochs,
            public.dts_source_row_versions
        FROM PUBLIC, tit_growth_app, tit_dts_ingest_runtime;

        REVOKE ALL PRIVILEGES ON FUNCTION
            public.dts_v2_source_field_types_valid(jsonb),
            public.dts_v2_source_row_transition_valid(
                text, jsonb, bigint, text, text, jsonb, text, numeric, text,
                timestamptz, text, text, text, numeric, text, text, jsonb
            ),
            public.dts_v2_assert_source_current_pair(text, text, text),
            public.dts_v2_source_current_pair_guard()
        FROM PUBLIC, tit_growth_app, tit_dts_ingest_runtime;

        DO $source_current_guard_acl$
        BEGIN
            IF to_regrole('tit_teacher_crud') IS NOT NULL THEN
                EXECUTE 'REVOKE ALL PRIVILEGES ON TABLE '
                    'public.dts_source_partition_epochs, '
                    'public.dts_source_row_versions FROM tit_teacher_crud';
                EXECUTE 'REVOKE ALL PRIVILEGES ON FUNCTION '
                    'public.dts_v2_source_field_types_valid(jsonb), '
                    'public.dts_v2_source_row_transition_valid('
                    'text, jsonb, bigint, text, text, jsonb, text, numeric, '
                    'text, timestamptz, text, text, text, numeric, text, text, '
                    'jsonb), '
                    'public.dts_v2_assert_source_current_pair(text, text, text), '
                    'public.dts_v2_source_current_pair_guard() '
                    'FROM tit_teacher_crud';
            END IF;
        END
        $source_current_guard_acl$;
        """
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    _create_validators_and_constraints()
    _create_deferred_pair_guard()
    _lock_shadow_guards_from_runtime()


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    op.execute(
        """
        LOCK TABLE
            public.dts_source_row_versions,
            public.dts_source_rows
        IN ACCESS EXCLUSIVE MODE;

        DO $source_current_guard_downgrade_guard$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM public.dts_source_row_versions LIMIT 1
            ) OR EXISTS (
                SELECT 1
                FROM public.dts_source_rows
                WHERE provenance_state = 'V2_CONFIRMED'
                LIMIT 1
            ) THEN
                RAISE EXCEPTION
                    'refusing v2 source-current guard downgrade: shadow data exists';
            END IF;
        END
        $source_current_guard_downgrade_guard$;

        DROP TRIGGER ct_dts_source_current_version_pair_guard
            ON public.dts_source_rows;
        DROP TRIGGER ct_dts_source_version_current_pair_guard
            ON public.dts_source_row_versions;

        ALTER TABLE public.dts_source_rows
        DROP CONSTRAINT fk_dts_source_row_current_version_event;
        ALTER TABLE public.dts_source_rows
        DROP CONSTRAINT ck_dts_source_row_profiled_key_type;
        ALTER TABLE public.dts_source_rows
        DROP CONSTRAINT ck_dts_source_row_v2_transition_shape;
        ALTER TABLE public.dts_source_row_versions
        DROP CONSTRAINT ck_dts_source_row_version_profiled_key_type;
        ALTER TABLE public.dts_source_row_versions
        DROP CONSTRAINT ck_dts_source_row_version_field_type_values;

        DROP FUNCTION public.dts_v2_source_current_pair_guard();
        DROP FUNCTION public.dts_v2_assert_source_current_pair(
            text, text, text
        );
        DROP FUNCTION public.dts_v2_source_row_transition_valid(
            text, jsonb, bigint, text, text, jsonb, text, numeric, text,
            timestamptz, text, text, text, numeric, text, text, jsonb
        );
        DROP FUNCTION public.dts_v2_source_field_types_valid(jsonb);
        """
    )
