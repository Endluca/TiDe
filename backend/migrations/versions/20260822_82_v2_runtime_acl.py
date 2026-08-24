"""Activate the least-privilege DTS v2 dual-capture write surface.

Revision ID: 20260822_82_v2_runtime_acl
Revises: 20260822_81_dts_v2_domain_facts
Create Date: 2026-08-22
"""

from __future__ import annotations

from typing import Union

from alembic import op


revision: str = "20260822_82_v2_runtime_acl"
down_revision: Union[str, None] = "20260822_81_dts_v2_domain_facts"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


_ENQUEUE_FUNCTION_TEMPLATE = r"""
CREATE OR REPLACE FUNCTION public.enqueue_dirty_from_source_revision_v2(
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
    dirty_region text;
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

    dirty_region := {dirty_region_expression};
    {complaint_category_authority_guard}

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
            'protected_source_row_hash',version.protected_source_row_hash
        )
    );
    RETURN public._upsert_dts_dirty_key_input_v2(
        dirty_region,p_key_type,p_key_part_1,p_key_part_2,
        'SOURCE_REVISION',identity,p_source_row_revision,fingerprint
    );
END
$function$;
"""


def _install_enqueue_function(*, cross_region_category: bool) -> None:
    dirty_region_expression = (
        "CASE WHEN p_key_type = 'COMPLAINT_CATEGORY' "
        "THEN 'dom' ELSE p_source_region END"
        if cross_region_category
        else "p_source_region"
    )
    complaint_category_authority_guard = (
        "IF p_key_type = 'COMPLAINT_CATEGORY' "
        "AND version.source_table NOT IN ("
        "'dom_complaint','ovs_complaint','dom_complaint_cate') THEN "
        "RAISE EXCEPTION 'DIRTY_INPUT_AUTHORITY_DENIED' "
        "USING ERRCODE = '42501'; END IF;"
        if cross_region_category
        else ""
    )
    op.execute(
        _ENQUEUE_FUNCTION_TEMPLATE.format(
            dirty_region_expression=dirty_region_expression,
            complaint_category_authority_guard=(
                complaint_category_authority_guard
            ),
        )
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    op.execute(
        r"""
        DO $dual_capture_acl_preflight$
        BEGIN
            IF to_regrole('tit_dts_ingest_runtime') IS NULL THEN
                RAISE EXCEPTION 'DTS_V2_DUAL_CAPTURE_RUNTIME_ROLE_MISSING';
            END IF;
            IF to_regclass('public.dts_source_partition_epochs') IS NULL
               OR to_regclass('public.dts_source_row_versions') IS NULL
               OR to_regclass('public.dts_source_rows') IS NULL
               OR to_regclass('public.dts_ingest_events') IS NULL
               OR to_regclass('public.dts_ingest_checkpoints') IS NULL
               OR to_regclass('public.dts_dirty_keys') IS NULL
               OR to_regclass('public.dts_dirty_key_inputs') IS NULL
               OR to_regprocedure(
                    'public.enqueue_dirty_from_source_revision_v2('
                    'text,text,text,bigint,text,text,text)'
                  ) IS NULL THEN
                RAISE EXCEPTION 'DTS_V2_DUAL_CAPTURE_SCHEMA_NOT_READY';
            END IF;
        END
        $dual_capture_acl_preflight$;

        CREATE FUNCTION public.lock_dts_source_partition_epoch_for_ingest_v2(
            p_source_region text,
            p_source_partition_epoch_id text,
            p_topic text,
            p_partition_id integer
        )
        RETURNS boolean
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE locked_epoch record;
        BEGIN
            IF p_source_region NOT IN ('dom','ovs')
               OR p_source_partition_epoch_id IS NULL
               OR btrim(p_source_partition_epoch_id) = ''
               OR p_topic IS NULL OR btrim(p_topic) = ''
               OR p_partition_id IS NULL OR p_partition_id < 0 THEN
                RAISE EXCEPTION 'DTS_V2_DUAL_CAPTURE_EPOCH_LOCK_INVALID'
                    USING ERRCODE = '22023';
            END IF;
            SELECT epoch_kind,status
            INTO locked_epoch
            FROM public.dts_source_partition_epochs
            WHERE source_region = p_source_region
              AND source_partition_epoch_id = p_source_partition_epoch_id
              AND topic = p_topic
              AND partition_id = p_partition_id
            FOR SHARE;
            IF NOT FOUND
               OR locked_epoch.epoch_kind IS DISTINCT FROM 'BROKER'
               OR locked_epoch.status IS DISTINCT FROM 'ACTIVE' THEN
                RAISE EXCEPTION 'DTS_V2_DUAL_CAPTURE_EPOCH_LOCK_REJECTED'
                    USING ERRCODE = '23503';
            END IF;
            RETURN true;
        END
        $function$;

        REVOKE ALL ON FUNCTION
            public.lock_dts_source_partition_epoch_for_ingest_v2(
                text,text,text,integer
            )
        FROM PUBLIC, tit_growth_app, tit_dts_ingest_runtime;
        """
    )
    _install_enqueue_function(cross_region_category=True)
    op.execute(
        r"""
        REVOKE ALL ON FUNCTION
            public.enqueue_dirty_from_source_revision_v2(
                text,text,text,bigint,text,text,text
            ),
            public._upsert_dts_dirty_key_input_v2(
                text,text,text,text,text,jsonb,bigint,text
            )
        FROM PUBLIC, tit_growth_app, tit_dts_ingest_runtime;

        GRANT EXECUTE ON FUNCTION
            public.lock_dts_source_partition_epoch_for_ingest_v2(
                text,text,text,integer
            ),
            public.enqueue_dirty_from_source_revision_v2(
                text,text,text,bigint,text,text,text
            ),
            public.dts_v2_source_field_types_valid(jsonb),
            public.dts_v2_source_row_transition_valid(
                text,jsonb,bigint,text,text,jsonb,text,numeric,text,
                timestamptz,text,text,text,numeric,text,text,jsonb
            ),
            public.dts_v2_assert_source_current_pair(text,text,text)
        TO tit_dts_ingest_runtime;

        REVOKE ALL ON FUNCTION
            public.dts_v2_source_current_pair_guard()
        FROM PUBLIC, tit_growth_app, tit_dts_ingest_runtime;

        ALTER FUNCTION public.check_dts_dirty_key_integrity_v2()
        SECURITY DEFINER;
        REVOKE ALL ON FUNCTION
            public.check_dts_dirty_key_integrity_v2()
        FROM PUBLIC, tit_growth_app, tit_dts_ingest_runtime;

        REVOKE ALL PRIVILEGES ON TABLE
            public.dts_source_partition_epochs,
            public.dts_source_row_versions,
            public.dts_source_rows,
            public.dts_ingest_events,
            public.dts_ingest_checkpoints
        FROM tit_dts_ingest_runtime;

        GRANT SELECT ON TABLE public.dts_source_partition_epochs
        TO tit_dts_ingest_runtime;
        GRANT SELECT, INSERT ON TABLE public.dts_source_row_versions
        TO tit_dts_ingest_runtime;
        GRANT SELECT, INSERT, UPDATE ON TABLE public.dts_source_rows
        TO tit_dts_ingest_runtime;
        GRANT SELECT, INSERT ON TABLE public.dts_ingest_events
        TO tit_dts_ingest_runtime;
        GRANT SELECT, UPDATE ON TABLE public.dts_ingest_checkpoints
        TO tit_dts_ingest_runtime;

        REVOKE ALL PRIVILEGES ON TABLE
            public.dts_dirty_keys,
            public.dts_dirty_key_inputs,
            public.dts_dirty_key_dependencies,
            public.dts_dirty_key_state_audits
        FROM tit_dts_ingest_runtime;

        -- DOM startup validates privacy state against current lesson and dirty
        -- facts.  Keep this surface read-only; dirty writes still go through
        -- the SECURITY DEFINER functions above.
        GRANT SELECT ON TABLE
            public.dts_dirty_keys,
            public.lesson_source_wide
        TO tit_dts_ingest_runtime;

        DO $dual_capture_optional_acl$
        BEGIN
            IF to_regrole('tit_teacher_crud') IS NOT NULL THEN
                EXECUTE 'REVOKE ALL ON FUNCTION '
                    'public.lock_dts_source_partition_epoch_for_ingest_v2('
                    'text,text,text,integer) FROM tit_teacher_crud';
            END IF;
            IF to_regrole('tit_growth_app') IS NOT NULL THEN
                EXECUTE 'REVOKE ALL ON FUNCTION '
                    'public.lock_dts_source_partition_epoch_for_ingest_v2('
                    'text,text,text,integer) '
                    'FROM tit_growth_app';
            END IF;
        END
        $dual_capture_optional_acl$;

        COMMENT ON FUNCTION
            public.lock_dts_source_partition_epoch_for_ingest_v2(
                text,text,text,integer
            ) IS
            'Least-privilege row lock for one ACTIVE broker epoch during an ingest transaction.';
        """
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    op.execute(
        r"""
        REVOKE ALL ON FUNCTION
            public.lock_dts_source_partition_epoch_for_ingest_v2(
                text,text,text,integer
            ),
            public.dts_v2_source_field_types_valid(jsonb),
            public.dts_v2_source_row_transition_valid(
                text,jsonb,bigint,text,text,jsonb,text,numeric,text,
                timestamptz,text,text,text,numeric,text,text,jsonb
            ),
            public.dts_v2_assert_source_current_pair(text,text,text)
        FROM PUBLIC, tit_growth_app, tit_dts_ingest_runtime;

        ALTER FUNCTION public.check_dts_dirty_key_integrity_v2()
        SECURITY INVOKER;

        REVOKE ALL PRIVILEGES ON TABLE
            public.dts_source_partition_epochs,
            public.dts_source_row_versions,
            public.dts_source_rows,
            public.dts_ingest_events,
            public.dts_ingest_checkpoints
        FROM tit_dts_ingest_runtime;

        GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE
            public.dts_source_rows,
            public.dts_ingest_events,
            public.dts_ingest_checkpoints
        TO tit_dts_ingest_runtime;
        """
    )
    _install_enqueue_function(cross_region_category=False)
    op.execute(
        r"""
        REVOKE ALL ON FUNCTION
            public.enqueue_dirty_from_source_revision_v2(
                text,text,text,bigint,text,text,text
            )
        FROM PUBLIC, tit_growth_app, tit_dts_ingest_runtime;
        GRANT EXECUTE ON FUNCTION
            public.enqueue_dirty_from_source_revision_v2(
                text,text,text,bigint,text,text,text
            )
        TO tit_dts_ingest_runtime;

        DROP FUNCTION
            public.lock_dts_source_partition_epoch_for_ingest_v2(
                text,text,text,integer
            );
        """
    )
