"""complete source-scope snapshot replacement and CDC membership overlays.

Revision ID: 20260823_100_scope_snapshot_diff
Revises: 20260822_99_blacklist_three_state

Revision 83 deliberately shipped a no-diff publisher.  This revision keeps
that historical fail-closed API in place and adds the v3 runtime surface that
binds an attested source profile, catches a candidate up through the locked
broker checkpoint, publishes deterministic SNAPSHOT_DIFF revisions, replaces
the selected scope membership baseline, and records the final desired set.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from alembic import context, op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260823_100_scope_snapshot_diff"
down_revision: Union[str, None] = "20260822_99_blacklist_three_state"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCOPE_ROLE = "tit_growth_app"
INGEST_ROLE = "tit_dts_ingest_runtime"


def _preflight() -> None:
    required = (
        "public.dts_source_scope_snapshots",
        "public.dts_source_snapshot_rows",
        "public.dts_source_scope_memberships",
        "public.dts_source_table_publish_generations",
        "public.dts_source_row_versions",
        "public.dts_source_rows",
        "public.dts_source_partition_epochs",
        "public.dts_ingest_checkpoints",
    )
    missing = [
        relation
        for relation in required
        if op.get_bind().execute(
            sa.text("SELECT to_regclass(:relation) IS NOT NULL"),
            {"relation": relation},
        ).scalar_one()
        is not True
    ]
    if missing:
        raise RuntimeError("DTS v2 source-scope prerequisites are missing")
    if op.get_bind().execute(
        sa.text("SELECT to_regrole(:role) IS NOT NULL"),
        {"role": INGEST_ROLE},
    ).scalar_one() is not True:
        raise RuntimeError(f"required runtime role is missing: {INGEST_ROLE}")
    if _scope_role_exists():
        invalid_scope_role = op.get_bind().execute(
            sa.text(
                """
                SELECT NOT rolcanlogin OR rolinherit OR rolsuper
                    OR rolcreatedb OR rolcreaterole
                    OR rolreplication OR rolbypassrls
                FROM pg_roles
                WHERE rolname = :role
                """
            ),
            {"role": SCOPE_ROLE},
        ).scalar_one()
        if invalid_scope_role:
            raise RuntimeError(
                f"{SCOPE_ROLE} must be a restricted LOGIN NOINHERIT role"
            )


def _scope_role_exists() -> bool:
    return (
        op.get_bind().execute(
            sa.text("SELECT to_regrole(:role) IS NOT NULL"),
            {"role": SCOPE_ROLE},
        ).scalar_one()
        is True
    )


def _replace_source_key_validator(*, strict: bool) -> None:
    # Revision 83 marked this branch-aware validator STRICT even though a
    # valid NUMERIC key requires source_key_text=NULL and a valid TEXT key
    # requires source_key_numeric=NULL.  PostgreSQL therefore returned NULL
    # without entering the function for every valid key.
    strict_clause = "STRICT" if strict else "CALLED ON NULL INPUT"
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION public.dts_source_key_parts_valid_v2(
            p_source_key text,
            p_source_key_data jsonb,
            p_source_key_type text,
            p_source_key_numeric numeric,
            p_source_key_text text
        )
        RETURNS boolean
        LANGUAGE plpgsql
        IMMUTABLE
        {strict_clause}
        PARALLEL SAFE
        SET search_path = pg_catalog, public
        AS $function$
        BEGIN
            IF p_source_key IS NULL OR p_source_key_data IS NULL
               OR p_source_key_type IS NULL
               OR jsonb_typeof(p_source_key_data) <> 'object'
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
        """
    )


def _set_source_pair_guard_security(*, definer: bool) -> None:
    # The constraint trigger is deferred until COMMIT.  It therefore runs
    # after the SECURITY DEFINER publisher/writer function has returned and
    # must retain narrowly scoped read authority for its private pair check.
    mode = "DEFINER" if definer else "INVOKER"
    op.execute(
        f"""
        ALTER FUNCTION public.dts_v2_source_current_pair_guard()
        SECURITY {mode};
        REVOKE ALL ON FUNCTION public.dts_v2_source_current_pair_guard()
        FROM PUBLIC,tit_growth_app,tit_dts_ingest_runtime;
        """
    )


def _add_snapshot_publication_evidence() -> None:
    for column in (
        sa.Column("source_schema_profile_id", sa.String(length=160)),
        sa.Column("source_field_types", postgresql.JSONB()),
        sa.Column("profile_bound_at", sa.DateTime(timezone=True)),
        sa.Column("published_through_offsets", postgresql.JSONB()),
        sa.Column("published_through_hash", sa.String(length=64)),
    ):
        op.add_column("dts_source_scope_snapshots", column, schema="public")

    op.create_check_constraint(
        "ck_dts_source_scope_snapshot_profile_v3",
        "dts_source_scope_snapshots",
        "(source_schema_profile_id IS NULL AND source_field_types IS NULL "
        "AND profile_bound_at IS NULL) OR "
        "(source_schema_profile_id IS NOT NULL "
        "AND btrim(source_schema_profile_id) <> '' "
        "AND public.dts_v2_source_field_types_valid(source_field_types) "
        "IS TRUE AND source_field_types ? 'id' "
        "AND profile_bound_at IS NOT NULL)",
        schema="public",
    )
    op.create_check_constraint(
        "ck_dts_source_scope_snapshot_published_through_v3",
        "dts_source_scope_snapshots",
        "(published_through_offsets IS NULL "
        "AND published_through_hash IS NULL) OR "
        "(jsonb_typeof(published_through_offsets) = 'array' "
        "AND jsonb_array_length(published_through_offsets) > 0 "
        "AND published_through_hash ~ '^[0-9a-f]{64}$' "
        "AND published_through_hash = "
        "public.dts_canonical_json_sha256_v1(published_through_offsets))",
        schema="public",
    )


def _create_desired_rows() -> None:
    op.create_table(
        "dts_source_snapshot_desired_rows",
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
        sa.Column("desired_row_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("clock_timestamp()")),
        sa.PrimaryKeyConstraint(
            "snapshot_id", "source_region", "source_table", "scope_kind",
            "scope_level", "scope_key", "source_key",
            name="pk_dts_source_snapshot_desired_rows",
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
            name="fk_dts_source_snapshot_desired_snapshot",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "public.dts_source_key_parts_valid_v2(source_key,source_key_data,"
            "source_key_type,source_key_numeric,source_key_text)",
            name="ck_dts_source_snapshot_desired_key",
        ),
        sa.CheckConstraint(
            "public.dts_dependency_keys_valid_v2(source_region,"
            "dependency_keys)",
            name="ck_dts_source_snapshot_desired_dependencies",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(protected_source_row) = 'object' "
            "AND desired_row_hash ~ '^[0-9a-f]{64}$' "
            "AND desired_row_hash = "
            "public.dts_canonical_json_sha256_v1(protected_source_row) "
            "AND (source_region <> 'dom' OR "
            "public.dom_student_json_is_safe_v1(protected_source_row))",
            name="ck_dts_source_snapshot_desired_payload",
        ),
        schema="public",
        comment=(
            "Immutable final desired set after candidate CDC catch-up; raw "
            "source-export rows remain separately preserved in "
            "dts_source_snapshot_rows."
        ),
    )
    op.execute(
        "COMMENT ON TABLE public.dts_source_snapshot_desired_rows IS "
        "'Immutable final desired set after candidate CDC catch-up; raw "
        "source-export rows remain separately preserved in "
        "dts_source_snapshot_rows.'"
    )

    op.create_foreign_key(
        "fk_dts_source_row_version_snapshot_generation_v3",
        "dts_source_row_versions",
        "dts_source_scope_snapshots",
        ["source_region", "source_table",
         "source_table_publish_generation", "snapshot_id"],
        ["source_region", "source_table", "published_generation",
         "snapshot_id"],
        source_schema="public",
        referent_schema="public",
        deferrable=True,
        initially="DEFERRED",
        ondelete="RESTRICT",
    )


def _install_profile_and_lock_functions() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.lock_dts_source_table_for_ingest_v3(
            p_source_region text,
            p_source_table text
        )
        RETURNS boolean
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        BEGIN
            IF public.dts_source_table_allowed_v2(
                    p_source_region,p_source_table
               ) IS DISTINCT FROM true THEN
                RAISE EXCEPTION 'DTS_SOURCE_TABLE_LOCK_INVALID'
                    USING ERRCODE = '22023';
            END IF;
            PERFORM pg_advisory_xact_lock_shared(
                hashtextextended(
                    'dts-source-table:' || p_source_region || ':' ||
                    p_source_table,0
                )
            );
            RETURN true;
        END
        $function$;

        CREATE FUNCTION public.bind_source_snapshot_profile_v3(
            p_snapshot_id text,
            p_owner text,
            p_lease_token text,
            p_source_schema_profile_id text,
            p_source_field_types jsonb,
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
        BEGIN
            SELECT * INTO snapshot_record
            FROM public.dts_source_scope_snapshots
            WHERE snapshot_id = p_snapshot_id
            FOR UPDATE;
            IF NOT FOUND OR snapshot_record.epoch_state NOT IN (
                'LOADING','VERIFYING'
            ) THEN
                RAISE EXCEPTION 'SOURCE_SCOPE_PROFILE_BIND_NOT_ACTIVE'
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
               OR p_actor IS NULL OR btrim(p_actor) = ''
               OR p_source_schema_profile_id IS NULL
               OR btrim(p_source_schema_profile_id) = ''
               OR length(p_source_schema_profile_id) > 160
               OR public.dts_v2_source_field_types_valid(
                    p_source_field_types
                  ) IS DISTINCT FROM true
               OR NOT p_source_field_types ? 'id' THEN
                RAISE EXCEPTION 'SOURCE_SCOPE_PROFILE_BIND_DENIED'
                    USING ERRCODE = '42501';
            END IF;
            IF snapshot_record.source_schema_profile_id IS NOT NULL THEN
                IF snapshot_record.source_schema_profile_id IS DISTINCT FROM
                        p_source_schema_profile_id
                   OR snapshot_record.source_field_types IS DISTINCT FROM
                        p_source_field_types THEN
                    RAISE EXCEPTION 'SOURCE_SCOPE_PROFILE_BIND_CONFLICT'
                        USING ERRCODE = '23514';
                END IF;
                RETURN jsonb_build_object(
                    'status','NOOP','snapshot_id',p_snapshot_id
                );
            END IF;
            IF EXISTS (
                SELECT 1
                FROM public.dts_source_snapshot_rows staged
                WHERE staged.snapshot_id = p_snapshot_id
                  AND (
                       staged.source_key_type IS DISTINCT FROM
                            p_source_field_types->>'id'
                       OR (SELECT array_agg(key ORDER BY key)
                           FROM jsonb_object_keys(
                                staged.protected_source_row
                           ) key)
                          IS DISTINCT FROM
                          (SELECT array_agg(key ORDER BY key)
                           FROM jsonb_object_keys(
                                p_source_field_types
                           ) key)
                  )
            ) THEN
                RAISE EXCEPTION 'SOURCE_SCOPE_PROFILE_STAGED_ROW_MISMATCH'
                    USING ERRCODE = '23514';
            END IF;
            PERFORM set_config('tit.scope_coordinator_internal','on',true);
            UPDATE public.dts_source_scope_snapshots
            SET source_schema_profile_id = p_source_schema_profile_id,
                source_field_types = p_source_field_types,
                profile_bound_at = clock_timestamp(),
                row_version = row_version + 1
            WHERE snapshot_id = p_snapshot_id;
            RETURN jsonb_build_object(
                'status','BOUND','snapshot_id',p_snapshot_id,
                'source_schema_profile_id',p_source_schema_profile_id
            );
        END
        $function$;

        REVOKE ALL ON FUNCTION
            public.lock_dts_source_table_for_ingest_v3(text,text),
            public.bind_source_snapshot_profile_v3(
                text,text,text,text,jsonb,text
            )
        FROM PUBLIC,tit_growth_app,tit_dts_ingest_runtime;
        GRANT EXECUTE ON FUNCTION
            public.lock_dts_source_table_for_ingest_v3(text,text)
        TO tit_dts_ingest_runtime;
        """
    )


def _install_stage_v3() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.stage_source_snapshot_row_v3(
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
            IF NOT FOUND OR snapshot_record.epoch_state <> 'LOADING'
               OR snapshot_record.source_schema_profile_id IS NULL
               OR public.dts_v2_source_field_types_valid(
                    snapshot_record.source_field_types
                  ) IS DISTINCT FROM true THEN
                RAISE EXCEPTION 'SOURCE_SCOPE_STAGING_NOT_PROFILED'
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
            IF key_type IS DISTINCT FROM
                    snapshot_record.source_field_types->>'id'
               OR length(v_source_key) > 512
               OR public.dts_dependency_keys_valid_v2(
                    snapshot_record.source_region,p_dependency_keys
                  ) IS DISTINCT FROM true
               OR jsonb_typeof(p_protected_source_row) <> 'object'
               OR (SELECT array_agg(key ORDER BY key)
                   FROM jsonb_object_keys(p_protected_source_row) key)
                  IS DISTINCT FROM
                  (SELECT array_agg(key ORDER BY key)
                   FROM jsonb_object_keys(
                        snapshot_record.source_field_types
                   ) key)
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

        REVOKE ALL ON FUNCTION public.stage_source_snapshot_row_v3(
            text,text,text,jsonb,jsonb,jsonb,text
        ) FROM PUBLIC,tit_growth_app,tit_dts_ingest_runtime;
        """
    )


def _install_membership_overlay_functions() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public._apply_source_membership_overlay_v3(
            p_source_region text,
            p_source_table text,
            p_source_key text,
            p_source_row_revision bigint,
            p_before_dependency_keys jsonb,
            p_after_dependency_keys jsonb,
            p_after_is_present boolean
        )
        RETURNS integer
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE
            version_record public.dts_source_row_versions%ROWTYPE;
            current_record public.dts_source_rows%ROWTYPE;
            scope_record public.dts_source_scope_states%ROWTYPE;
            desired_present boolean;
            desired_dependencies jsonb;
            changed_count integer := 0;
        BEGIN
            IF current_setting(
                    'tit.source_membership_internal',true
               ) IS DISTINCT FROM 'on'
               OR public.dts_dependency_keys_valid_v2(
                    p_source_region,p_before_dependency_keys
                  ) IS DISTINCT FROM true
               OR public.dts_dependency_keys_valid_v2(
                    p_source_region,p_after_dependency_keys
                  ) IS DISTINCT FROM true THEN
                RAISE EXCEPTION 'SOURCE_MEMBERSHIP_OVERLAY_AUTHORITY_DENIED'
                    USING ERRCODE = '42501';
            END IF;
            SELECT * INTO version_record
            FROM public.dts_source_row_versions
            WHERE source_region = p_source_region
              AND source_table = p_source_table
              AND source_key = p_source_key
              AND source_row_revision = p_source_row_revision
            FOR KEY SHARE;
            SELECT * INTO current_record
            FROM public.dts_source_rows
            WHERE source_region = p_source_region
              AND source_table = p_source_table
              AND source_key = p_source_key
            FOR KEY SHARE;
            IF version_record.source_key IS NULL
               OR current_record.source_key IS NULL
               OR current_record.provenance_state IS DISTINCT FROM
                    'V2_CONFIRMED'
               OR current_record.source_row_revision IS DISTINCT FROM
                    p_source_row_revision
               OR current_record.source_payload_hash IS DISTINCT FROM
                    version_record.protected_source_row_hash
               OR current_record.is_deleted IS DISTINCT FROM
                    NOT p_after_is_present
               OR current_record.dependency_keys IS DISTINCT FROM (
                    CASE WHEN p_after_is_present
                         THEN p_after_dependency_keys
                         ELSE p_before_dependency_keys END
                  ) THEN
                RAISE EXCEPTION 'SOURCE_MEMBERSHIP_OVERLAY_REFERENCE_INVALID'
                    USING ERRCODE = '23503';
            END IF;

            PERFORM set_config('tit.scope_coordinator_internal','on',true);
            FOR scope_record IN
                SELECT scope.*
                FROM public.dts_source_scope_states scope
                WHERE scope.source_region = p_source_region
                  AND scope.source_table = p_source_table
                  AND scope.scope_kind = 'CURRENT'
                  AND scope.active_snapshot_id IS NOT NULL
                  AND (
                       scope.scope_level = 'GLOBAL'
                       OR (
                           scope.scope_level = 'TEACHER'
                           AND (
                               p_before_dependency_keys->'teacher_ids'
                                   ? scope.scope_key
                               OR p_after_dependency_keys->'teacher_ids'
                                   ? scope.scope_key
                           )
                       )
                  )
                ORDER BY convert_to(scope.scope_level,'UTF8'),
                         convert_to(scope.scope_key,'UTF8')
                FOR UPDATE
            LOOP
                desired_present := p_after_is_present AND (
                    scope_record.scope_level = 'GLOBAL'
                    OR p_after_dependency_keys->'teacher_ids'
                        ? scope_record.scope_key
                );
                desired_dependencies := CASE
                    WHEN desired_present THEN p_after_dependency_keys
                    ELSE p_before_dependency_keys
                END;
                INSERT INTO public.dts_source_scope_memberships (
                    source_region,source_table,scope_kind,scope_level,
                    scope_key,source_key,source_key_data,source_key_type,
                    source_key_numeric,source_key_text,active_snapshot_id,
                    snapshot_is_present,snapshot_row_hash,dependency_keys,
                    cdc_overlay_is_present,cdc_overlay_row_hash,
                    last_cdc_source_revision,membership_revision,updated_at
                ) VALUES (
                    p_source_region,p_source_table,scope_record.scope_kind,
                    scope_record.scope_level,scope_record.scope_key,
                    p_source_key,current_record.source_key_data,
                    current_record.source_key_type,
                    current_record.source_key_numeric,
                    current_record.source_key_text,
                    scope_record.active_snapshot_id,false,NULL,
                    desired_dependencies,desired_present,
                    CASE WHEN desired_present
                         THEN current_record.source_payload_hash ELSE NULL END,
                    p_source_row_revision,1,clock_timestamp()
                ) ON CONFLICT (
                    source_region,source_table,scope_kind,scope_level,
                    scope_key,source_key
                ) DO UPDATE SET
                    source_key_data = EXCLUDED.source_key_data,
                    source_key_type = EXCLUDED.source_key_type,
                    source_key_numeric = EXCLUDED.source_key_numeric,
                    source_key_text = EXCLUDED.source_key_text,
                    dependency_keys = EXCLUDED.dependency_keys,
                    cdc_overlay_is_present =
                        EXCLUDED.cdc_overlay_is_present,
                    cdc_overlay_row_hash = EXCLUDED.cdc_overlay_row_hash,
                    last_cdc_source_revision =
                        EXCLUDED.last_cdc_source_revision,
                    membership_revision =
                        public.dts_source_scope_memberships
                            .membership_revision + 1,
                    updated_at = clock_timestamp();
                changed_count := changed_count + 1;
            END LOOP;
            RETURN changed_count;
        END
        $function$;

        CREATE FUNCTION public.scope_membership_apply_cdc_v3(
            p_source_region text,
            p_source_table text,
            p_source_key text,
            p_source_row_revision bigint,
            p_before_dependency_keys jsonb,
            p_after_dependency_keys jsonb,
            p_after_is_present boolean
        )
        RETURNS jsonb
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE
            version_record public.dts_source_row_versions%ROWTYPE;
            changed_count integer;
        BEGIN
            IF public.dts_source_table_allowed_v2(
                    p_source_region,p_source_table
               ) IS DISTINCT FROM true THEN
                RAISE EXCEPTION 'SOURCE_MEMBERSHIP_CDC_REQUEST_INVALID'
                    USING ERRCODE = '22023';
            END IF;
            PERFORM public.lock_dts_source_table_for_ingest_v3(
                p_source_region,p_source_table
            );
            SELECT * INTO version_record
            FROM public.dts_source_row_versions
            WHERE source_region = p_source_region
              AND source_table = p_source_table
              AND source_key = p_source_key
              AND source_row_revision = p_source_row_revision
            FOR KEY SHARE;
            IF NOT FOUND OR version_record.version_kind <> 'CDC' THEN
                RAISE EXCEPTION 'SOURCE_MEMBERSHIP_CDC_REFERENCE_INVALID'
                    USING ERRCODE = '23503';
            END IF;
            PERFORM set_config('tit.source_membership_internal','on',true);
            changed_count := public._apply_source_membership_overlay_v3(
                p_source_region,p_source_table,p_source_key,
                p_source_row_revision,p_before_dependency_keys,
                p_after_dependency_keys,p_after_is_present
            );
            RETURN jsonb_build_object(
                'status','APPLIED','membership_count',changed_count,
                'source_row_revision',p_source_row_revision
            );
        END
        $function$;

        CREATE FUNCTION public._enqueue_snapshot_diff_dirty_v3(
            p_source_region text,
            p_source_table text,
            p_source_key text,
            p_source_row_revision bigint,
            p_before_dependency_keys jsonb,
            p_after_dependency_keys jsonb
        )
        RETURNS integer
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE
            version_record public.dts_source_row_versions%ROWTYPE;
            current_record public.dts_source_rows%ROWTYPE;
            affected record;
            identity jsonb;
            fingerprint text;
            dirty_count integer := 0;
        BEGIN
            IF current_setting(
                    'tit.scope_coordinator_internal',true
               ) IS DISTINCT FROM 'on'
               OR public.dts_dependency_keys_valid_v2(
                    p_source_region,p_before_dependency_keys
                  ) IS DISTINCT FROM true
               OR public.dts_dependency_keys_valid_v2(
                    p_source_region,p_after_dependency_keys
                  ) IS DISTINCT FROM true THEN
                RAISE EXCEPTION 'SNAPSHOT_DIFF_DIRTY_AUTHORITY_DENIED'
                    USING ERRCODE = '42501';
            END IF;
            SELECT * INTO version_record
            FROM public.dts_source_row_versions
            WHERE source_region = p_source_region
              AND source_table = p_source_table
              AND source_key = p_source_key
              AND source_row_revision = p_source_row_revision
            FOR KEY SHARE;
            SELECT * INTO current_record
            FROM public.dts_source_rows
            WHERE source_region = p_source_region
              AND source_table = p_source_table
              AND source_key = p_source_key
            FOR KEY SHARE;
            IF version_record.version_kind IS DISTINCT FROM 'SNAPSHOT_DIFF'
               OR current_record.source_row_revision IS DISTINCT FROM
                    p_source_row_revision
               OR current_record.source_payload_hash IS DISTINCT FROM
                    version_record.protected_source_row_hash THEN
                RAISE EXCEPTION 'SNAPSHOT_DIFF_DIRTY_REFERENCE_INVALID'
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
                    'version_kind',version_record.version_kind,
                    'operation',version_record.operation,
                    'is_deleted',current_record.is_deleted,
                    'protected_source_row_hash',
                        version_record.protected_source_row_hash
                )
            );
            FOR affected IN
                WITH dependency_documents AS (
                    SELECT p_before_dependency_keys dependency_keys
                    UNION ALL
                    SELECT p_after_dependency_keys
                ), keys AS (
                    SELECT p_source_region dirty_region,'COURSE'::text key_type,
                           value #>> '{}' key_part_1,''::text key_part_2
                    FROM dependency_documents,
                         jsonb_array_elements(
                            dependency_keys->'course_ids'
                         ) item(value)
                    UNION ALL
                    SELECT p_source_region,'TEACHER',value #>> '{}',''
                    FROM dependency_documents,
                         jsonb_array_elements(
                            dependency_keys->'teacher_ids'
                         ) item(value)
                    UNION ALL
                    SELECT p_source_region,'LABEL',value #>> '{}',''
                    FROM dependency_documents,
                         jsonb_array_elements(
                            dependency_keys->'label_ids'
                         ) item(value)
                    UNION ALL
                    SELECT 'dom','COMPLAINT_CATEGORY',value #>> '{}',''
                    FROM dependency_documents,
                         jsonb_array_elements(
                            dependency_keys->'category_ids'
                         ) item(value)
                    UNION ALL
                    SELECT p_source_region,'TEACHER_STUDENT',
                           teacher.value #>> '{}',student.value #>> '{}'
                    FROM dependency_documents,
                         jsonb_array_elements(
                            dependency_keys->'teacher_ids'
                         ) teacher(value),
                         jsonb_array_elements(
                            dependency_keys->'student_subjects'
                         ) student(value)
                    UNION ALL
                    SELECT 'ovs','TEACHER',p_source_key,''
                    WHERE p_source_region = 'dom'
                      AND p_source_table = 'dom_teacher'
                )
                SELECT DISTINCT dirty_region,key_type,key_part_1,key_part_2
                FROM keys
            LOOP
                PERFORM public._upsert_dts_dirty_key_input_v2(
                    affected.dirty_region,affected.key_type,
                    affected.key_part_1,affected.key_part_2,
                    'SOURCE_REVISION',identity,p_source_row_revision,
                    fingerprint
                );
                dirty_count := dirty_count + 1;
            END LOOP;
            RETURN dirty_count;
        END
        $function$;

        REVOKE ALL ON FUNCTION
            public._apply_source_membership_overlay_v3(
                text,text,text,bigint,jsonb,jsonb,boolean
            ),
            public.scope_membership_apply_cdc_v3(
                text,text,text,bigint,jsonb,jsonb,boolean
            ),
            public._enqueue_snapshot_diff_dirty_v3(
                text,text,text,bigint,jsonb,jsonb
            )
        FROM PUBLIC,tit_growth_app,tit_dts_ingest_runtime;
        GRANT EXECUTE ON FUNCTION public.scope_membership_apply_cdc_v3(
            text,text,text,bigint,jsonb,jsonb,boolean
        ) TO tit_dts_ingest_runtime;
        """
    )


def _install_candidate_verification_v3() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.dts_scope_published_through_offsets_v3(
            p_snapshot_id text
        )
        RETURNS jsonb
        LANGUAGE sql
        STABLE
        STRICT
        SET search_path = pg_catalog, public
        AS $function$
            SELECT coalesce(jsonb_agg(
                jsonb_build_object(
                    'source_region',fence.source_region,
                    'source_partition_epoch_id',
                        fence.source_partition_epoch_id,
                    'topic',fence.topic,
                    'partition_id',fence.partition_id,
                    'start_next_offset',fence.start_next_offset,
                    'end_next_offset',checkpoint.next_offset
                ) ORDER BY convert_to(fence.source_region,'UTF8'),
                           convert_to(
                            fence.source_partition_epoch_id,'UTF8'
                           ),convert_to(fence.topic,'UTF8'),
                           fence.partition_id
            ),'[]'::jsonb)
            FROM public.dts_source_snapshot_fences fence
            JOIN public.dts_source_partition_epochs epoch
              ON epoch.source_region = fence.source_region
             AND epoch.source_partition_epoch_id =
                    fence.source_partition_epoch_id
             AND epoch.topic = fence.topic
             AND epoch.partition_id = fence.partition_id
             AND epoch.epoch_kind = 'BROKER'
             AND epoch.status = 'ACTIVE'
            JOIN public.dts_ingest_checkpoints checkpoint
              ON checkpoint.source_region = fence.source_region
             AND checkpoint.source_partition_epoch_id =
                    fence.source_partition_epoch_id
             AND checkpoint.topic = fence.topic
             AND checkpoint.partition_id = fence.partition_id
             AND checkpoint.is_current_epoch IS TRUE
             AND checkpoint.next_offset >= fence.end_next_offset
            WHERE fence.snapshot_id = p_snapshot_id
        $function$;

        CREATE FUNCTION public.dts_source_current_replayed_by_snapshot_v3(
            p_source_region text,
            p_source_partition_epoch_id text,
            p_topic text,
            p_partition integer,
            p_offset bigint,
            p_last_version_kind text,
            p_published_through_offsets jsonb
        )
        RETURNS boolean
        LANGUAGE sql
        IMMUTABLE
        SET search_path = pg_catalog, public
        AS $function$
            SELECT p_last_version_kind = 'CDC'
               AND EXISTS (
                    SELECT 1
                    FROM jsonb_array_elements(
                        p_published_through_offsets
                    ) item(value)
                    WHERE value->>'source_region' = p_source_region
                      AND value->>'source_partition_epoch_id' =
                            p_source_partition_epoch_id
                      AND value->>'topic' = p_topic
                      AND (value->>'partition_id')::integer = p_partition
                      AND p_offset >=
                            (value->>'start_next_offset')::bigint
                      AND p_offset <
                            (value->>'end_next_offset')::bigint
               )
        $function$;

        CREATE FUNCTION public.dts_scope_candidate_error_v3(
            p_snapshot_id text
        )
        RETURNS text
        LANGUAGE plpgsql
        STABLE
        STRICT
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE
            snapshot_record public.dts_source_scope_snapshots%ROWTYPE;
            fence_manifest jsonb;
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
            IF snapshot_record.source_schema_profile_id IS NULL
               OR public.dts_v2_source_field_types_valid(
                    snapshot_record.source_field_types
                  ) IS DISTINCT FROM true
               OR NOT snapshot_record.source_field_types ? 'id' THEN
                RETURN 'SOURCE_SCOPE_PROFILE_NOT_BOUND';
            END IF;
            fence_manifest :=
                public.dts_scope_snapshot_fence_manifest_v2(p_snapshot_id);
            IF fence_manifest IS DISTINCT FROM
                    snapshot_record.snapshot_fence_vector
               OR public.dts_canonical_json_sha256_v1(fence_manifest)
                    IS DISTINCT FROM snapshot_record.snapshot_fence_hash THEN
                RETURN 'SOURCE_SCOPE_FENCE_RELATION_MISMATCH';
            END IF;
            IF jsonb_array_length(
                    public.dts_scope_published_through_offsets_v3(
                        p_snapshot_id
                    )
               ) IS DISTINCT FROM jsonb_array_length(fence_manifest) THEN
                RETURN 'SOURCE_SCOPE_FENCE_NOT_REACHED';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM public.dts_source_snapshot_rows staged
                WHERE staged.snapshot_id = p_snapshot_id
                  AND (
                       staged.source_key_type IS DISTINCT FROM
                            snapshot_record.source_field_types->>'id'
                       OR (SELECT array_agg(key ORDER BY key)
                           FROM jsonb_object_keys(
                                staged.protected_source_row
                           ) key)
                          IS DISTINCT FROM
                          (SELECT array_agg(key ORDER BY key)
                           FROM jsonb_object_keys(
                                snapshot_record.source_field_types
                           ) key)
                  )
            ) THEN
                RETURN 'SOURCE_SCOPE_PROFILE_STAGED_ROW_MISMATCH';
            END IF;
            RETURN NULL;
        END
        $function$;

        CREATE FUNCTION public.verify_source_snapshot_candidate_v3(
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
                    'protocol','scope-verify-command-v3',
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
                validation_error := public.dts_scope_candidate_error_v3(
                    p_snapshot_id
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
                    'fence_hash',snapshot_record.snapshot_fence_hash,
                    'source_schema_profile_id',
                        snapshot_record.source_schema_profile_id
                )
            );
            RETURN response;
        END
        $function$;

        REVOKE ALL ON FUNCTION
            public.dts_scope_published_through_offsets_v3(text),
            public.dts_source_current_replayed_by_snapshot_v3(
                text,text,text,integer,bigint,text,jsonb
            ),
            public.dts_scope_candidate_error_v3(text),
            public.verify_source_snapshot_candidate_v3(
                text,text,text,text,bigint,bigint,bigint,text,text,text
            )
        FROM PUBLIC,tit_growth_app,tit_dts_ingest_runtime;
        """
    )


def _install_publisher_v3() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public._append_snapshot_diff_version_v3(
            p_snapshot_id text,
            p_epoch_id text,
            p_topic text,
            p_offset bigint,
            p_generation bigint,
            p_diff_step integer,
            p_source_region text,
            p_source_table text,
            p_source_key text,
            p_source_key_data jsonb,
            p_source_key_type text,
            p_source_key_numeric numeric,
            p_source_key_text text,
            p_operation text,
            p_before_row jsonb,
            p_after_row jsonb,
            p_source_row_revision bigint,
            p_profile_id text,
            p_source_field_types jsonb,
            p_snapshot_as_of timestamptz,
            p_covered_through_offsets jsonb
        )
        RETURNS void
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE
            protected_row jsonb;
            protected_hash text;
            source_position jsonb;
        BEGIN
            IF current_setting(
                    'tit.scope_coordinator_internal',true
               ) IS DISTINCT FROM 'on'
               OR p_diff_step NOT IN (1,2)
               OR p_source_row_revision < 1 THEN
                RAISE EXCEPTION 'SNAPSHOT_DIFF_APPEND_AUTHORITY_DENIED'
                    USING ERRCODE = '42501';
            END IF;
            protected_row := CASE
                WHEN p_operation IN (
                    'SNAPSHOT_DELETE','SNAPSHOT_BOOTSTRAP_TOMBSTONE'
                ) THEN p_before_row
                ELSE p_after_row
            END;
            IF jsonb_typeof(protected_row) <> 'object' THEN
                RAISE EXCEPTION 'SNAPSHOT_DIFF_PROTECTED_ROW_MISSING'
                    USING ERRCODE = '23514';
            END IF;
            protected_hash := public.dts_canonical_json_sha256_v1(
                protected_row
            );
            source_position := jsonb_build_object(
                'v',1,
                'source_timestamp',NULL,
                'record_id_type','text',
                'record_id',p_source_key,
                'source_partition_epoch_id',p_epoch_id,
                'topic',p_topic,
                'partition_id',0,
                'offset_value',p_offset
            );
            INSERT INTO public.dts_source_row_versions (
                source_region,source_partition_epoch_id,topic,partition_id,
                offset_value,version_kind,source_table,
                source_schema_profile_id,source_field_types,source_key,
                source_key_data,source_key_type,source_key_numeric,
                source_key_text,operation,before_row,after_row,
                source_timestamp,record_id_type,record_id_numeric,
                record_id_text,source_position,source_row_revision,
                snapshot_id,snapshot_as_of,covered_through_offsets,diff_step,
                source_table_publish_generation,protected_source_row_hash
            ) VALUES (
                p_source_region,p_epoch_id,p_topic,0,p_offset,
                'SNAPSHOT_DIFF',p_source_table,p_profile_id,
                p_source_field_types,p_source_key,p_source_key_data,
                p_source_key_type,p_source_key_numeric,p_source_key_text,
                p_operation,p_before_row,p_after_row,NULL,'text',NULL,
                p_source_key,source_position,p_source_row_revision,
                p_snapshot_id,p_snapshot_as_of,p_covered_through_offsets,
                p_diff_step,p_generation,protected_hash
            );
        END
        $function$;

        CREATE FUNCTION public.publish_source_snapshot_candidate_v3(
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
            live_record public.dts_source_rows%ROWTYPE;
            plan_record record;
            final_version public.dts_source_row_versions%ROWTYPE;
            validation_error text;
            actual_count bigint;
            actual_hash text;
            next_generation bigint;
            old_state text;
            response jsonb;
            published_offsets jsonb;
            published_hash text;
            diff_epoch_id text;
            diff_topic text;
            diff_count bigint;
            diff_hash text;
            before_dependencies jsonb;
            after_dependencies jsonb;
            final_dependencies jsonb;
            empty_dependencies jsonb := jsonb_build_object(
                'category_ids',jsonb_build_array(),
                'course_ids',jsonb_build_array(),
                'label_ids',jsonb_build_array(),
                'student_subjects',jsonb_build_array(),
                'teacher_ids',jsonb_build_array()
            );
            offset_value bigint;
            source_revision bigint;
            normal_step integer;
            final_row jsonb;
            final_deleted boolean;
            key_data jsonb;
            key_type text;
            key_numeric numeric;
            key_text text;
        BEGIN
            request_hash := public.dts_canonical_json_sha256_v1(
                jsonb_build_object(
                    'protocol','scope-publish-command-v3',
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

            -- The CDC writer takes the shared form of this exact lock before
            -- touching source current or its checkpoint.  Once acquired, the
            -- final checkpoint vector and all compared current rows are one
            -- atomic publication cut.
            PERFORM pg_advisory_xact_lock(
                hashtextextended(
                    'dts-source-table:' || snapshot_record.source_region ||
                    ':' || snapshot_record.source_table,0
                )
            );
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
            validation_error := public.dts_scope_candidate_error_v3(
                p_snapshot_id
            );
            IF validation_error IS NOT NULL THEN
                RAISE EXCEPTION '%',validation_error USING ERRCODE = '55000';
            END IF;
            published_offsets :=
                public.dts_scope_published_through_offsets_v3(p_snapshot_id);
            IF jsonb_array_length(published_offsets) IS DISTINCT FROM
                    jsonb_array_length(snapshot_record.partition_offsets) THEN
                RAISE EXCEPTION 'SNAPSHOT_FENCE_UNVERIFIED'
                    USING ERRCODE = '55000';
            END IF;
            published_hash := public.dts_canonical_json_sha256_v1(
                published_offsets
            );
            next_generation := head_record.current_generation + 1;

            DROP TABLE IF EXISTS pg_temp.dts_scope_desired_v3;
            DROP TABLE IF EXISTS pg_temp.dts_scope_diff_plan_v3;
            CREATE TEMP TABLE pg_temp.dts_scope_desired_v3 (
                source_key text PRIMARY KEY,
                source_key_data jsonb NOT NULL,
                source_key_type text NOT NULL,
                source_key_numeric numeric,
                source_key_text text,
                dependency_keys jsonb NOT NULL,
                protected_source_row jsonb NOT NULL,
                desired_row_hash text NOT NULL
            ) ON COMMIT DROP;

            INSERT INTO pg_temp.dts_scope_desired_v3 (
                source_key,source_key_data,source_key_type,
                source_key_numeric,source_key_text,dependency_keys,
                protected_source_row,desired_row_hash
            )
            SELECT source_key,source_key_data,source_key_type,
                   source_key_numeric,source_key_text,dependency_keys,
                   protected_source_row,snapshot_row_hash
            FROM public.dts_source_snapshot_rows
            WHERE snapshot_id = p_snapshot_id;

            -- A staged row may move only through a CDC revision covered by
            -- the locked catch-up vector.  Snapshot-diff or unknown changes
            -- after staging prove this candidate stale rather than letting an
            -- old source export overwrite current.
            IF EXISTS (
                SELECT 1
                FROM public.dts_source_snapshot_rows staged
                LEFT JOIN public.dts_source_rows live
                  ON live.source_region = staged.source_region
                 AND live.source_table = staged.source_table
                 AND live.source_key = staged.source_key
                WHERE staged.snapshot_id = p_snapshot_id
                  AND (
                       (
                         live.source_key IS NULL
                         AND (
                           staged.base_source_row_revision IS NOT NULL
                           OR staged.base_row_hash IS NOT NULL
                         )
                       )
                       OR (
                         live.source_key IS NOT NULL
                         AND (
                           live.source_row_revision IS DISTINCT FROM
                                staged.base_source_row_revision
                           OR live.source_payload_hash IS DISTINCT FROM
                                staged.base_row_hash
                         )
                       )
                  )
                  AND NOT coalesce(
                    public.dts_source_current_replayed_by_snapshot_v3(
                        live.source_region,
                        live.last_source_partition_epoch_id,
                        live.last_topic,live.last_partition,live.last_offset,
                        live.last_version_kind,published_offsets
                    ),false
                  )
            ) THEN
                RAISE EXCEPTION 'SNAPSHOT_CANDIDATE_STALE'
                    USING ERRCODE = '40001';
            END IF;

            -- Fold covered CDC current into the desired set without writing a
            -- second CDC version.  Ownership moves remove the row from the old
            -- teacher candidate and add it to the new teacher candidate.
            FOR live_record IN
                SELECT live.*
                FROM public.dts_source_rows live
                WHERE live.source_region = snapshot_record.source_region
                  AND live.source_table = snapshot_record.source_table
                  AND public.dts_source_current_replayed_by_snapshot_v3(
                        live.source_region,
                        live.last_source_partition_epoch_id,
                        live.last_topic,live.last_partition,live.last_offset,
                        live.last_version_kind,published_offsets
                      )
                  AND (
                       snapshot_record.scope_level = 'GLOBAL'
                       OR live.dependency_keys->'teacher_ids'
                            ? snapshot_record.scope_key
                       OR EXISTS (
                            SELECT 1
                            FROM pg_temp.dts_scope_desired_v3 desired
                            WHERE desired.source_key = live.source_key
                       )
                       OR EXISTS (
                            SELECT 1
                            FROM public.dts_source_scope_memberships member
                            WHERE member.source_region = live.source_region
                              AND member.source_table = live.source_table
                              AND member.scope_kind =
                                    snapshot_record.scope_kind
                              AND member.scope_level =
                                    snapshot_record.scope_level
                              AND member.scope_key = snapshot_record.scope_key
                              AND member.source_key = live.source_key
                       )
                  )
                ORDER BY CASE live.source_key_type
                            WHEN 'NUMERIC' THEN 0 ELSE 1 END,
                         live.source_key_numeric NULLS LAST,
                         convert_to(live.source_key_text,'UTF8') NULLS LAST
            LOOP
                IF live_record.source_schema_profile_id IS DISTINCT FROM
                        snapshot_record.source_schema_profile_id
                   OR live_record.source_field_types IS DISTINCT FROM
                        snapshot_record.source_field_types THEN
                    RAISE EXCEPTION 'SNAPSHOT_CANDIDATE_PROFILE_STALE'
                        USING ERRCODE = '40001';
                END IF;
                IF NOT live_record.is_deleted AND (
                    snapshot_record.scope_level = 'GLOBAL'
                    OR live_record.dependency_keys->'teacher_ids'
                        ? snapshot_record.scope_key
                ) THEN
                    INSERT INTO pg_temp.dts_scope_desired_v3 (
                        source_key,source_key_data,source_key_type,
                        source_key_numeric,source_key_text,dependency_keys,
                        protected_source_row,desired_row_hash
                    ) VALUES (
                        live_record.source_key,live_record.source_key_data,
                        live_record.source_key_type,
                        live_record.source_key_numeric,
                        live_record.source_key_text,
                        live_record.dependency_keys,live_record.source_row,
                        live_record.source_payload_hash
                    ) ON CONFLICT (source_key) DO UPDATE SET
                        source_key_data = EXCLUDED.source_key_data,
                        source_key_type = EXCLUDED.source_key_type,
                        source_key_numeric = EXCLUDED.source_key_numeric,
                        source_key_text = EXCLUDED.source_key_text,
                        dependency_keys = EXCLUDED.dependency_keys,
                        protected_source_row = EXCLUDED.protected_source_row,
                        desired_row_hash = EXCLUDED.desired_row_hash;
                ELSE
                    DELETE FROM pg_temp.dts_scope_desired_v3
                    WHERE source_key = live_record.source_key;
                END IF;
            END LOOP;

            -- Every active overlay being replaced must be no later than the
            -- locked end vector.  A reset/foreign epoch remains fail-closed.
            IF EXISTS (
                SELECT 1
                FROM public.dts_source_scope_memberships member
                JOIN public.dts_source_row_versions version
                  ON version.source_region = member.source_region
                 AND version.source_table = member.source_table
                 AND version.source_key = member.source_key
                 AND version.source_row_revision =
                        member.last_cdc_source_revision
                WHERE member.source_region = snapshot_record.source_region
                  AND member.source_table = snapshot_record.source_table
                  AND member.scope_kind = snapshot_record.scope_kind
                  AND member.scope_level = snapshot_record.scope_level
                  AND member.scope_key = snapshot_record.scope_key
                  AND member.cdc_overlay_is_present IS NOT NULL
                  AND NOT EXISTS (
                    SELECT 1
                    FROM jsonb_array_elements(published_offsets) item(value)
                    WHERE value->>'source_region' = version.source_region
                      AND value->>'source_partition_epoch_id' =
                            version.source_partition_epoch_id
                      AND value->>'topic' = version.topic
                      AND (value->>'partition_id')::integer =
                            version.partition_id
                      AND version.offset_value <
                            (value->>'end_next_offset')::bigint
                  )
            ) THEN
                RAISE EXCEPTION 'SNAPSHOT_CANDIDATE_STALE'
                    USING ERRCODE = '40001';
            END IF;

            PERFORM set_config('tit.scope_coordinator_internal','on',true);
            INSERT INTO public.dts_source_snapshot_desired_rows (
                snapshot_id,source_region,source_table,scope_kind,scope_level,
                scope_key,source_key,source_key_data,source_key_type,
                source_key_numeric,source_key_text,dependency_keys,
                protected_source_row,desired_row_hash
            )
            SELECT p_snapshot_id,snapshot_record.source_region,
                   snapshot_record.source_table,snapshot_record.scope_kind,
                   snapshot_record.scope_level,snapshot_record.scope_key,
                   desired.source_key,desired.source_key_data,
                   desired.source_key_type,desired.source_key_numeric,
                   desired.source_key_text,desired.dependency_keys,
                   desired.protected_source_row,desired.desired_row_hash
            FROM pg_temp.dts_scope_desired_v3 desired
            ORDER BY CASE desired.source_key_type
                        WHEN 'NUMERIC' THEN 0 ELSE 1 END,
                     desired.source_key_numeric NULLS LAST,
                     convert_to(desired.source_key_text,'UTF8') NULLS LAST;

            CREATE TEMP TABLE pg_temp.dts_scope_diff_plan_v3 (
                source_key text PRIMARY KEY,
                source_key_data jsonb NOT NULL,
                source_key_type text NOT NULL,
                source_key_numeric numeric,
                source_key_text text,
                current_exists boolean NOT NULL,
                current_is_legacy boolean NOT NULL,
                current_is_deleted boolean,
                current_row jsonb,
                current_dependency_keys jsonb,
                current_revision bigint,
                desired_exists boolean NOT NULL,
                desired_row jsonb,
                desired_row_hash text,
                desired_dependency_keys jsonb,
                bootstrap_operation text,
                normal_operation text,
                ordinal bigint
            ) ON COMMIT DROP;

            INSERT INTO pg_temp.dts_scope_diff_plan_v3 (
                source_key,source_key_data,source_key_type,
                source_key_numeric,source_key_text,current_exists,
                current_is_legacy,current_is_deleted,current_row,
                current_dependency_keys,current_revision,desired_exists,
                desired_row,desired_row_hash,desired_dependency_keys,
                bootstrap_operation,normal_operation
            )
            WITH candidate_keys AS (
                SELECT desired.source_key
                FROM pg_temp.dts_scope_desired_v3 desired
                UNION
                SELECT live.source_key
                FROM public.dts_source_rows live
                WHERE snapshot_record.scope_level = 'GLOBAL'
                  AND live.source_region = snapshot_record.source_region
                  AND live.source_table = snapshot_record.source_table
            ), joined AS (
                SELECT keys.source_key,live.source_key IS NOT NULL live_exists,
                       live.provenance_state live_provenance_state,
                       live.is_deleted live_is_deleted,
                       live.source_row live_source_row,
                       live.dependency_keys live_dependencies,
                       live.source_row_revision live_revision,
                       live.source_key_type live_key_type,
                       live.source_schema_profile_id live_profile_id,
                       live.source_field_types live_field_types,
                       desired.source_key IS NOT NULL desired_exists,
                       desired.source_key_data desired_key_data,
                       desired.source_key_type desired_key_type,
                       desired.source_key_numeric desired_key_numeric,
                       desired.source_key_text desired_key_text,
                       desired.protected_source_row desired_row,
                       desired.desired_row_hash,
                       desired.dependency_keys desired_dependencies
                FROM candidate_keys keys
                LEFT JOIN public.dts_source_rows live
                  ON live.source_region = snapshot_record.source_region
                 AND live.source_table = snapshot_record.source_table
                 AND live.source_key = keys.source_key
                LEFT JOIN pg_temp.dts_scope_desired_v3 desired
                  ON desired.source_key = keys.source_key
            )
            SELECT joined.source_key,
                   coalesce(joined.desired_key_data,
                     CASE snapshot_record.source_field_types->>'id'
                       WHEN 'NUMERIC' THEN jsonb_build_object(
                         'id',to_jsonb(joined.source_key::numeric)
                       )
                       ELSE jsonb_build_object('id',joined.source_key)
                     END),
                   coalesce(joined.desired_key_type,
                            joined.live_key_type,
                            snapshot_record.source_field_types->>'id'),
                   CASE WHEN coalesce(joined.desired_key_type,
                                      joined.live_key_type,
                                      snapshot_record.source_field_types->>'id')
                                  = 'NUMERIC'
                        THEN joined.source_key::numeric ELSE NULL END,
                   CASE WHEN coalesce(joined.desired_key_type,
                                      joined.live_key_type,
                                      snapshot_record.source_field_types->>'id')
                                  = 'TEXT'
                        THEN joined.source_key ELSE NULL END,
                   joined.live_exists,
                   joined.live_exists AND joined.live_provenance_state
                        IS DISTINCT FROM 'V2_CONFIRMED',
                   joined.live_is_deleted,joined.live_source_row,
                   joined.live_dependencies,joined.live_revision,
                   joined.desired_exists,joined.desired_row,
                   joined.desired_row_hash,joined.desired_dependencies,
                   CASE WHEN joined.live_exists AND joined.live_provenance_state
                                  IS DISTINCT FROM 'V2_CONFIRMED'
                        THEN CASE WHEN joined.live_is_deleted
                                  THEN 'SNAPSHOT_BOOTSTRAP_TOMBSTONE'
                                  ELSE 'SNAPSHOT_BOOTSTRAP_PRESENT' END
                        ELSE NULL END,
                   CASE
                     WHEN NOT joined.live_exists AND joined.desired_exists
                       THEN 'SNAPSHOT_INSERT'
                     WHEN joined.live_exists AND joined.live_is_deleted
                          AND joined.desired_exists
                       THEN 'SNAPSHOT_INSERT'
                     WHEN joined.live_exists AND NOT joined.live_is_deleted
                          AND joined.desired_exists AND (
                            public.dts_canonical_json_sha256_v1(
                                joined.live_source_row
                            ) IS DISTINCT FROM joined.desired_row_hash
                            OR joined.live_dependencies IS DISTINCT FROM
                                joined.desired_dependencies
                            OR joined.live_provenance_state IS DISTINCT FROM
                                'V2_CONFIRMED'
                               AND public.dts_canonical_json_sha256_v1(
                                    joined.live_source_row
                               ) IS DISTINCT FROM joined.desired_row_hash
                            OR joined.live_provenance_state = 'V2_CONFIRMED' AND (
                                joined.live_profile_id IS DISTINCT FROM
                                    snapshot_record.source_schema_profile_id
                                OR joined.live_field_types IS DISTINCT FROM
                                    snapshot_record.source_field_types
                            )
                          ) THEN 'SNAPSHOT_UPDATE'
                     WHEN joined.live_exists AND NOT joined.live_is_deleted
                          AND NOT joined.desired_exists
                          AND snapshot_record.scope_level = 'GLOBAL'
                       THEN 'SNAPSHOT_DELETE'
                     ELSE NULL
                   END
            FROM joined
            WHERE (joined.live_exists AND joined.live_provenance_state
                       IS DISTINCT FROM 'V2_CONFIRMED')
               OR (NOT joined.live_exists AND joined.desired_exists)
               OR (joined.live_exists AND joined.live_is_deleted
                    AND joined.desired_exists)
               OR (joined.live_exists AND NOT joined.live_is_deleted
                    AND joined.desired_exists AND (
                       public.dts_canonical_json_sha256_v1(joined.live_source_row)
                            IS DISTINCT FROM joined.desired_row_hash
                       OR joined.live_dependencies IS DISTINCT FROM
                            joined.desired_dependencies
                       OR joined.live_provenance_state = 'V2_CONFIRMED' AND (
                            joined.live_profile_id IS DISTINCT FROM
                                snapshot_record.source_schema_profile_id
                            OR joined.live_field_types IS DISTINCT FROM
                                snapshot_record.source_field_types
                       )
                    ))
               OR (joined.live_exists AND NOT joined.live_is_deleted
                    AND NOT joined.desired_exists
                    AND snapshot_record.scope_level = 'GLOBAL');

            UPDATE pg_temp.dts_scope_diff_plan_v3 plan
            SET ordinal = ordered.ordinal
            FROM (
                SELECT source_key,row_number() OVER (
                    ORDER BY CASE source_key_type
                                WHEN 'NUMERIC' THEN 0 ELSE 1 END,
                             source_key_numeric NULLS LAST,
                             convert_to(source_key_text,'UTF8') NULLS LAST
                ) ordinal
                FROM pg_temp.dts_scope_diff_plan_v3
            ) ordered
            WHERE ordered.source_key = plan.source_key;

            IF EXISTS (
                SELECT 1 FROM pg_temp.dts_scope_diff_plan_v3 plan
                WHERE public.dts_source_key_parts_valid_v2(
                        plan.source_key,plan.source_key_data,
                        plan.source_key_type,plan.source_key_numeric,
                        plan.source_key_text
                      ) IS DISTINCT FROM true
                   OR (plan.current_is_legacy AND (
                        jsonb_typeof(plan.current_row) <> 'object'
                        OR (SELECT array_agg(key ORDER BY key)
                            FROM jsonb_object_keys(plan.current_row) key)
                           IS DISTINCT FROM
                           (SELECT array_agg(key ORDER BY key)
                            FROM jsonb_object_keys(
                                snapshot_record.source_field_types
                            ) key)
                        OR plan.current_row->'id' IS DISTINCT FROM
                            plan.source_key_data->'id'
                   ))
            ) THEN
                RAISE EXCEPTION 'SOURCE_SCOPE_LEGACY_ROW_PROFILE_MISMATCH'
                    USING ERRCODE = '23514';
            END IF;

            SELECT coalesce(sum(
                (bootstrap_operation IS NOT NULL)::integer +
                (normal_operation IS NOT NULL)::integer
            ),0) INTO diff_count
            FROM pg_temp.dts_scope_diff_plan_v3;
            diff_epoch_id := 'snapshot-diff:' || right(p_snapshot_id,64);
            diff_topic := '__snapshot_diff__:' || p_snapshot_id || ':' ||
                          snapshot_record.source_table;
            IF diff_count > 0 THEN
                INSERT INTO public.dts_source_partition_epochs (
                    source_region,source_partition_epoch_id,topic,
                    partition_id,epoch_kind,status,snapshot_id,source_table,
                    row_version
                ) VALUES (
                    snapshot_record.source_region,diff_epoch_id,diff_topic,0,
                    'SNAPSHOT_DIFF','SEALED',p_snapshot_id,
                    snapshot_record.source_table,1
                );
            END IF;

            FOR plan_record IN
                SELECT * FROM pg_temp.dts_scope_diff_plan_v3
                ORDER BY ordinal
            LOOP
                key_data := plan_record.source_key_data;
                key_type := plan_record.source_key_type;
                key_numeric := plan_record.source_key_numeric;
                key_text := plan_record.source_key_text;
                before_dependencies := coalesce(
                    plan_record.current_dependency_keys,empty_dependencies
                );
                source_revision := coalesce(plan_record.current_revision,0);

                IF plan_record.bootstrap_operation IS NOT NULL THEN
                    source_revision := 1;
                    offset_value := 2 * plan_record.ordinal - 1;
                    PERFORM public._append_snapshot_diff_version_v3(
                        p_snapshot_id,diff_epoch_id,diff_topic,offset_value,
                        next_generation,1,snapshot_record.source_region,
                        snapshot_record.source_table,plan_record.source_key,
                        key_data,key_type,key_numeric,key_text,
                        plan_record.bootstrap_operation,
                        CASE WHEN plan_record.bootstrap_operation =
                                  'SNAPSHOT_BOOTSTRAP_TOMBSTONE'
                             THEN plan_record.current_row ELSE NULL END,
                        CASE WHEN plan_record.bootstrap_operation =
                                  'SNAPSHOT_BOOTSTRAP_PRESENT'
                             THEN plan_record.current_row ELSE NULL END,
                        source_revision,
                        snapshot_record.source_schema_profile_id,
                        snapshot_record.source_field_types,
                        snapshot_record.snapshot_as_of,published_offsets
                    );
                END IF;

                IF plan_record.normal_operation IS NOT NULL THEN
                    normal_step := CASE WHEN
                        plan_record.bootstrap_operation IS NULL THEN 1 ELSE 2
                    END;
                    offset_value := 2 * plan_record.ordinal -
                                    CASE normal_step WHEN 1 THEN 1 ELSE 0 END;
                    source_revision := CASE
                        WHEN plan_record.bootstrap_operation IS NOT NULL THEN 2
                        WHEN plan_record.current_exists THEN
                            plan_record.current_revision + 1
                        ELSE 1
                    END;
                    PERFORM public._append_snapshot_diff_version_v3(
                        p_snapshot_id,diff_epoch_id,diff_topic,offset_value,
                        next_generation,normal_step,
                        snapshot_record.source_region,
                        snapshot_record.source_table,plan_record.source_key,
                        key_data,key_type,key_numeric,key_text,
                        plan_record.normal_operation,
                        CASE WHEN plan_record.normal_operation IN (
                                  'SNAPSHOT_UPDATE','SNAPSHOT_DELETE'
                             ) THEN plan_record.current_row ELSE NULL END,
                        CASE WHEN plan_record.normal_operation IN (
                                  'SNAPSHOT_INSERT','SNAPSHOT_UPDATE'
                             ) THEN plan_record.desired_row ELSE NULL END,
                        source_revision,
                        snapshot_record.source_schema_profile_id,
                        snapshot_record.source_field_types,
                        snapshot_record.snapshot_as_of,published_offsets
                    );
                END IF;

                SELECT * INTO STRICT final_version
                FROM public.dts_source_row_versions
                WHERE source_region = snapshot_record.source_region
                  AND snapshot_id = p_snapshot_id
                  AND source_table = snapshot_record.source_table
                  AND source_key = plan_record.source_key
                ORDER BY diff_step DESC
                LIMIT 1;
                final_deleted := final_version.operation IN (
                    'SNAPSHOT_DELETE','SNAPSHOT_BOOTSTRAP_TOMBSTONE'
                );
                final_row := CASE WHEN final_deleted
                    THEN final_version.before_row ELSE final_version.after_row
                END;
                final_dependencies := CASE WHEN final_deleted
                    THEN before_dependencies
                    ELSE coalesce(
                        plan_record.desired_dependency_keys,
                        before_dependencies
                    )
                END;

                IF plan_record.current_exists THEN
                    UPDATE public.dts_source_rows
                    SET source_key_data = key_data,
                        dependency_keys = final_dependencies,
                        source_row = final_row,is_deleted = final_deleted,
                        source_timestamp = 0,last_record_id = 0,
                        source_position = final_version.source_position::text,
                        last_topic = diff_topic,last_partition = 0,
                        last_offset = final_version.offset_value,
                        row_version = row_version + 1,
                        updated_at = clock_timestamp(),
                        source_row_revision =
                            final_version.source_row_revision,
                        last_source_partition_epoch_id = diff_epoch_id,
                        last_version_kind = 'SNAPSHOT_DIFF',
                        source_position_v2 = final_version.source_position,
                        record_id_type = 'text',record_id_numeric = NULL,
                        record_id_text = plan_record.source_key,
                        source_timestamp_v2 = NULL,
                        source_payload_hash =
                            final_version.protected_source_row_hash,
                        provenance_state = 'V2_CONFIRMED',
                        source_key_type = key_type,
                        source_key_numeric = key_numeric,
                        source_key_text = key_text,
                        source_schema_profile_id =
                            snapshot_record.source_schema_profile_id,
                        source_field_types =
                            snapshot_record.source_field_types
                    WHERE source_region = snapshot_record.source_region
                      AND source_table = snapshot_record.source_table
                      AND source_key = plan_record.source_key;
                ELSE
                    INSERT INTO public.dts_source_rows (
                        source_region,source_table,source_key,source_key_data,
                        dependency_keys,source_row,is_deleted,source_timestamp,
                        last_record_id,source_position,last_topic,last_partition,
                        last_offset,row_version,source_row_revision,
                        last_source_partition_epoch_id,last_version_kind,
                        source_position_v2,record_id_type,record_id_numeric,
                        record_id_text,source_timestamp_v2,source_payload_hash,
                        provenance_state,source_key_type,source_key_numeric,
                        source_key_text,source_schema_profile_id,
                        source_field_types
                    ) VALUES (
                        snapshot_record.source_region,
                        snapshot_record.source_table,plan_record.source_key,
                        key_data,final_dependencies,final_row,final_deleted,
                        0,0,final_version.source_position::text,diff_topic,0,
                        final_version.offset_value,1,
                        final_version.source_row_revision,diff_epoch_id,
                        'SNAPSHOT_DIFF',final_version.source_position,'text',
                        NULL,plan_record.source_key,NULL,
                        final_version.protected_source_row_hash,
                        'V2_CONFIRMED',key_type,key_numeric,key_text,
                        snapshot_record.source_schema_profile_id,
                        snapshot_record.source_field_types
                    );
                END IF;

                after_dependencies := CASE WHEN final_deleted
                    THEN empty_dependencies ELSE final_dependencies END;
                PERFORM set_config(
                    'tit.source_membership_internal','on',true
                );
                PERFORM public._apply_source_membership_overlay_v3(
                    snapshot_record.source_region,
                    snapshot_record.source_table,plan_record.source_key,
                    final_version.source_row_revision,before_dependencies,
                    after_dependencies,NOT final_deleted
                );
                PERFORM public._enqueue_snapshot_diff_dirty_v3(
                    snapshot_record.source_region,
                    snapshot_record.source_table,plan_record.source_key,
                    final_version.source_row_revision,before_dependencies,
                    after_dependencies
                );
            END LOOP;

            SELECT public.dts_canonical_json_sha256_v1(
                coalesce(jsonb_agg(
                    jsonb_build_object(
                        'source_key',version.source_key,
                        'diff_step',version.diff_step,
                        'operation',version.operation,
                        'source_row_revision',version.source_row_revision,
                        'offset_value',version.offset_value,
                        'protected_source_row_hash',
                            version.protected_source_row_hash
                    ) ORDER BY CASE version.source_key_type
                                WHEN 'NUMERIC' THEN 0 ELSE 1 END,
                               version.source_key_numeric NULLS LAST,
                               convert_to(version.source_key_text,'UTF8')
                                    NULLS LAST,
                               version.diff_step
                ),'[]'::jsonb)
            ) INTO diff_hash
            FROM public.dts_source_row_versions version
            WHERE version.source_region = snapshot_record.source_region
              AND version.source_table = snapshot_record.source_table
              AND version.snapshot_id = p_snapshot_id
              AND version.version_kind = 'SNAPSHOT_DIFF';

            -- Replace the selected scope baseline with the caught-up desired
            -- set.  Source-diff overlays applied above remain on every other
            -- active scope; the selected scope clears all overlays because
            -- its new baseline is complete through published_offsets.
            UPDATE public.dts_source_scope_memberships member
            SET active_snapshot_id = p_snapshot_id,
                snapshot_is_present = false,
                snapshot_row_hash = NULL,
                cdc_overlay_is_present = NULL,
                cdc_overlay_row_hash = NULL,
                last_cdc_source_revision = NULL,
                membership_revision = member.membership_revision + 1,
                updated_at = clock_timestamp()
            WHERE member.source_region = snapshot_record.source_region
              AND member.source_table = snapshot_record.source_table
              AND member.scope_kind = snapshot_record.scope_kind
              AND member.scope_level = snapshot_record.scope_level
              AND member.scope_key = snapshot_record.scope_key
              AND NOT EXISTS (
                  SELECT 1
                  FROM pg_temp.dts_scope_desired_v3 desired
                  WHERE desired.source_key = member.source_key
              );

            UPDATE public.dts_source_scope_memberships member
            SET active_snapshot_id = p_snapshot_id,
                snapshot_is_present = true,
                snapshot_row_hash = desired.desired_row_hash,
                source_key_data = desired.source_key_data,
                source_key_type = desired.source_key_type,
                source_key_numeric = desired.source_key_numeric,
                source_key_text = desired.source_key_text,
                dependency_keys = desired.dependency_keys,
                cdc_overlay_is_present = NULL,
                cdc_overlay_row_hash = NULL,
                last_cdc_source_revision = NULL,
                membership_revision = member.membership_revision + 1,
                updated_at = clock_timestamp()
            FROM pg_temp.dts_scope_desired_v3 desired
            WHERE member.source_region = snapshot_record.source_region
              AND member.source_table = snapshot_record.source_table
              AND member.scope_kind = snapshot_record.scope_kind
              AND member.scope_level = snapshot_record.scope_level
              AND member.scope_key = snapshot_record.scope_key
              AND member.source_key = desired.source_key;

            INSERT INTO public.dts_source_scope_memberships (
                source_region,source_table,scope_kind,scope_level,scope_key,
                source_key,source_key_data,source_key_type,source_key_numeric,
                source_key_text,active_snapshot_id,snapshot_is_present,
                snapshot_row_hash,dependency_keys,membership_revision
            )
            SELECT snapshot_record.source_region,
                   snapshot_record.source_table,snapshot_record.scope_kind,
                   snapshot_record.scope_level,snapshot_record.scope_key,
                   desired.source_key,desired.source_key_data,
                   desired.source_key_type,desired.source_key_numeric,
                   desired.source_key_text,p_snapshot_id,true,
                   desired.desired_row_hash,desired.dependency_keys,1
            FROM pg_temp.dts_scope_desired_v3 desired
            WHERE NOT EXISTS (
                SELECT 1
                FROM public.dts_source_scope_memberships member
                WHERE member.source_region = snapshot_record.source_region
                  AND member.source_table = snapshot_record.source_table
                  AND member.scope_kind = snapshot_record.scope_kind
                  AND member.scope_level = snapshot_record.scope_level
                  AND member.scope_key = snapshot_record.scope_key
                  AND member.source_key = desired.source_key
            );

            old_state := scope_record.state;
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
                published_through_offsets = published_offsets,
                published_through_hash = published_hash,
                generation_diff_count = diff_count,
                generation_diff_hash = diff_hash,
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
                p_snapshot_id,published_hash
            );
            response := jsonb_build_object(
                'status','PUBLISHED','snapshot_id',p_snapshot_id,
                'publish_generation',next_generation,
                'generation_diff_count',diff_count,
                'generation_diff_hash',diff_hash,
                'published_through_hash',published_hash,
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
                    'generation_diff_count',diff_count,
                    'generation_diff_hash',diff_hash,
                    'content_hash',actual_hash,
                    'fence_hash',snapshot_record.snapshot_fence_hash,
                    'published_through_hash',published_hash
                )
            );
            RETURN response;
        EXCEPTION
            WHEN invalid_text_representation OR numeric_value_out_of_range THEN
                RAISE EXCEPTION 'SOURCE_SCOPE_SOURCE_KEY_INVALID'
                    USING ERRCODE = '22023';
        END
        $function$;

        REVOKE ALL ON FUNCTION
            public._append_snapshot_diff_version_v3(
                text,text,text,bigint,bigint,integer,text,text,text,jsonb,text,
                numeric,text,text,jsonb,jsonb,bigint,text,jsonb,timestamptz,jsonb
            ),
            public.publish_source_snapshot_candidate_v3(
                text,text,text,text,bigint,bigint,text,text
            )
        FROM PUBLIC,tit_growth_app,tit_dts_ingest_runtime;
        """
    )


def _install_guards_acl_and_comments() -> None:
    op.execute(
        r"""
        CREATE TRIGGER guard_dts_snapshot_desired_insert_v3
        BEFORE INSERT ON public.dts_source_snapshot_desired_rows
        FOR EACH ROW EXECUTE FUNCTION
            public.guard_dts_scope_internal_write_v2();
        CREATE TRIGGER guard_dts_snapshot_desired_immutable_v3
        BEFORE UPDATE OR DELETE ON public.dts_source_snapshot_desired_rows
        FOR EACH ROW EXECUTE FUNCTION
            public.guard_dts_snapshot_rows_immutable_v2();

        REVOKE ALL PRIVILEGES ON TABLE
            public.dts_source_snapshot_desired_rows
        FROM PUBLIC,tit_growth_app,tit_dts_ingest_runtime;

        COMMENT ON FUNCTION public.publish_source_snapshot_candidate_v3(
            text,text,text,text,bigint,bigint,text,text
        ) IS
        'Catch up one verified CURRENT candidate through the locked broker '
        'checkpoint, emit deterministic SNAPSHOT_DIFF revisions, replace the '
        'selected membership baseline, advance its table generation exactly '
        'once, and emit SOURCE_SCOPE output atomically.';
        COMMENT ON FUNCTION public.scope_membership_apply_cdc_v3(
            text,text,text,bigint,jsonb,jsonb,boolean
        ) IS
        'Apply the same committed CDC source revision to every already-active '
        'GLOBAL/TEACHER membership overlay in the source-current transaction.';
        """
    )


def _apply_optional_scope_role_acl_v3() -> None:
    if not context.is_offline_mode() and not _scope_role_exists():
        return
    op.execute(
        r"""
        GRANT USAGE ON SCHEMA public
        TO tit_growth_app;
        REVOKE ALL PRIVILEGES ON TABLE
            public.dts_source_snapshot_desired_rows
        FROM tit_growth_app;
        GRANT SELECT ON TABLE public.dts_source_snapshot_desired_rows
        TO tit_growth_app;

        REVOKE ALL ON FUNCTION
            public.dts_v2_source_current_pair_guard(),
            public.lock_dts_source_table_for_ingest_v3(text,text),
            public._apply_source_membership_overlay_v3(
                text,text,text,bigint,jsonb,jsonb,boolean
            ),
            public._enqueue_snapshot_diff_dirty_v3(
                text,text,text,bigint,jsonb,jsonb
            ),
            public.dts_scope_published_through_offsets_v3(text),
            public.dts_source_current_replayed_by_snapshot_v3(
                text,text,text,integer,bigint,text,jsonb
            ),
            public.dts_scope_candidate_error_v3(text),
            public._append_snapshot_diff_version_v3(
                text,text,text,bigint,bigint,integer,text,text,text,jsonb,text,
                numeric,text,text,jsonb,jsonb,bigint,text,jsonb,timestamptz,jsonb
            ),
            public.scope_membership_apply_cdc_v3(
                text,text,text,bigint,jsonb,jsonb,boolean
            ),
            public.stage_source_snapshot_row_v2(
                text,text,text,jsonb,jsonb,jsonb,text
            ),
            public.verify_source_snapshot_candidate_v2(
                text,text,text,text,bigint,bigint,bigint,text,text,text
            ),
            public.publish_source_snapshot_candidate_v2(
                text,text,text,text,bigint,bigint,text,text
            )
        FROM tit_growth_app;

        GRANT EXECUTE ON FUNCTION
            public.bind_source_snapshot_profile_v3(
                text,text,text,text,jsonb,text
            ),
            public.stage_source_snapshot_row_v3(
                text,text,text,jsonb,jsonb,jsonb,text
            ),
            public.verify_source_snapshot_candidate_v3(
                text,text,text,text,bigint,bigint,bigint,text,text,text
            ),
            public.publish_source_snapshot_candidate_v3(
                text,text,text,text,bigint,bigint,text,text
            )
        TO tit_growth_app;
        """
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    if not context.is_offline_mode():
        _preflight()
    _replace_source_key_validator(strict=False)
    _set_source_pair_guard_security(definer=True)
    _add_snapshot_publication_evidence()
    _create_desired_rows()
    _install_profile_and_lock_functions()
    _install_stage_v3()
    _install_membership_overlay_functions()
    _install_candidate_verification_v3()
    _install_publisher_v3()
    _install_guards_acl_and_comments()
    _apply_optional_scope_role_acl_v3()


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    used = op.get_bind().execute(
        sa.text(
            """
            SELECT EXISTS (
                       SELECT 1
                       FROM public.dts_source_scope_snapshots
                       WHERE source_schema_profile_id IS NOT NULL
                          OR published_through_offsets IS NOT NULL
                   )
                OR EXISTS (
                       SELECT 1
                       FROM public.dts_source_snapshot_desired_rows
                   )
                OR EXISTS (
                       SELECT 1
                       FROM public.dts_source_row_versions
                       WHERE version_kind = 'SNAPSHOT_DIFF'
                         AND source_table_publish_generation IS NOT NULL
                   )
            """
        )
    ).scalar_one()
    if used:
        raise RuntimeError(
            "source-scope snapshot diff revision has runtime evidence and "
            "cannot be downgraded"
        )

    op.execute(
        r"""
        DROP TRIGGER guard_dts_snapshot_desired_immutable_v3
            ON public.dts_source_snapshot_desired_rows;
        DROP TRIGGER guard_dts_snapshot_desired_insert_v3
            ON public.dts_source_snapshot_desired_rows;

        DROP FUNCTION public.publish_source_snapshot_candidate_v3(
            text,text,text,text,bigint,bigint,text,text
        );
        DROP FUNCTION public._append_snapshot_diff_version_v3(
            text,text,text,bigint,bigint,integer,text,text,text,jsonb,text,
            numeric,text,text,jsonb,jsonb,bigint,text,jsonb,timestamptz,jsonb
        );
        DROP FUNCTION public.verify_source_snapshot_candidate_v3(
            text,text,text,text,bigint,bigint,bigint,text,text,text
        );
        DROP FUNCTION public.dts_scope_candidate_error_v3(text);
        DROP FUNCTION public.dts_source_current_replayed_by_snapshot_v3(
            text,text,text,integer,bigint,text,jsonb
        );
        DROP FUNCTION public.dts_scope_published_through_offsets_v3(text);
        DROP FUNCTION public._enqueue_snapshot_diff_dirty_v3(
            text,text,text,bigint,jsonb,jsonb
        );
        DROP FUNCTION public.scope_membership_apply_cdc_v3(
            text,text,text,bigint,jsonb,jsonb,boolean
        );
        DROP FUNCTION public._apply_source_membership_overlay_v3(
            text,text,text,bigint,jsonb,jsonb,boolean
        );
        DROP FUNCTION public.stage_source_snapshot_row_v3(
            text,text,text,jsonb,jsonb,jsonb,text
        );
        DROP FUNCTION public.bind_source_snapshot_profile_v3(
            text,text,text,text,jsonb,text
        );
        DROP FUNCTION public.lock_dts_source_table_for_ingest_v3(text,text);

        """
    )
    if _scope_role_exists():
        op.execute(
            r"""
            GRANT EXECUTE ON FUNCTION
                public.stage_source_snapshot_row_v2(
                    text,text,text,jsonb,jsonb,jsonb,text
                ),
                public.verify_source_snapshot_candidate_v2(
                    text,text,text,text,bigint,bigint,bigint,text,text,text
                ),
                public.publish_source_snapshot_candidate_v2(
                    text,text,text,text,bigint,bigint,text,text
                )
            TO tit_growth_app;
            """
        )
    op.drop_constraint(
        "fk_dts_source_row_version_snapshot_generation_v3",
        "dts_source_row_versions",
        schema="public",
        type_="foreignkey",
    )
    op.drop_table("dts_source_snapshot_desired_rows", schema="public")
    op.drop_constraint(
        "ck_dts_source_scope_snapshot_published_through_v3",
        "dts_source_scope_snapshots",
        schema="public",
        type_="check",
    )
    op.drop_constraint(
        "ck_dts_source_scope_snapshot_profile_v3",
        "dts_source_scope_snapshots",
        schema="public",
        type_="check",
    )
    for column in (
        "published_through_hash",
        "published_through_offsets",
        "profile_bound_at",
        "source_field_types",
        "source_schema_profile_id",
    ):
        op.drop_column("dts_source_scope_snapshots", column, schema="public")
    _set_source_pair_guard_security(definer=False)
    _replace_source_key_validator(strict=True)
