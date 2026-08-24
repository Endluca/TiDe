"""Install the shared DTS v2 PRIMARY transaction guard.

Revision ID: 20260822_88a_runtime_primary_guard
Revises: 20260822_88_v2_score_projection
"""

from __future__ import annotations

from typing import Union

from alembic import context, op
import sqlalchemy as sa


revision: str = "20260822_88a_runtime_primary_guard"
down_revision: Union[str, None] = "20260822_88_v2_score_projection"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None


GUARD_SIGNATURE = "public.dts_v2_runtime_primary_guard_v1(text)"
DOMAIN_ROLE = "tit_growth_app"
OUTBOX_ROLE = "tit_growth_app"


def _assert_preconditions() -> None:
    row = op.get_bind().execute(
        sa.text(
            """
            SELECT
              to_regclass('public.dts_pipeline_control') IS NOT NULL,
              to_regrole(:outbox_role) IS NOT NULL
            """
        ),
        {"domain_role": DOMAIN_ROLE, "outbox_role": OUTBOX_ROLE},
    ).one()
    if row != (True, True):
        raise RuntimeError("DTS v2 runtime guard prerequisites are missing")


def _ensure_runtime_roles() -> None:
    op.execute(
        rf"""
        DO $runtime_guard_roles$
        BEGIN
          IF to_regrole('{DOMAIN_ROLE}') IS NULL THEN
            RAISE EXCEPTION 'required application role is missing: {DOMAIN_ROLE}';
          END IF;
          IF EXISTS (
            SELECT 1 FROM pg_roles
            WHERE rolname = '{DOMAIN_ROLE}'
              AND (NOT rolcanlogin OR rolinherit OR rolsuper
                   OR rolcreatedb OR rolcreaterole OR rolreplication
                   OR rolbypassrls)
          ) THEN
            RAISE EXCEPTION
              'tit_growth_app must be a restricted NOINHERIT LOGIN role';
          END IF;
        END
        $runtime_guard_roles$;
        """
    )


def _install_guard() -> None:
    op.execute(
        rf"""
        CREATE FUNCTION public.dts_v2_runtime_primary_guard_v1(
          p_component text
        ) RETURNS bigint
        LANGUAGE plpgsql
        VOLATILE
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE
          caller_name text := session_user;
          owner_name text := current_user;
          control_count integer;
          control_mode text;
          generation bigint;
        BEGIN
          IF p_component NOT IN (
            'DOMAIN','OUTBOX','FAVORITE','COURSE','TEACHER',
            'TEACHER_STUDENT','COMPLETION_CONFLICT','TASK_PLAN','SCORE'
          ) THEN
            RAISE EXCEPTION 'DTS_V2_RUNTIME_GUARD_COMPONENT_INVALID'
              USING ERRCODE='22023';
          END IF;
          IF caller_name <> owner_name AND (
            (p_component='DOMAIN' AND caller_name <> '{DOMAIN_ROLE}')
            OR
            (p_component<>'DOMAIN' AND caller_name <> '{OUTBOX_ROLE}')
          ) THEN
            RAISE EXCEPTION 'DTS_V2_RUNTIME_GUARD_CALLER_FORBIDDEN'
              USING ERRCODE='42501';
          END IF;

          PERFORM pg_advisory_xact_lock_shared(
            hashtextextended('tit:dts-v2-cutover',0)
          );
          SELECT count(*) INTO control_count
          FROM public.dts_pipeline_control;
          IF control_count <> 1 THEN
            RAISE EXCEPTION 'DTS_V2_PIPELINE_CONTROL_SINGLETON_REQUIRED'
              USING ERRCODE='55000';
          END IF;
          SELECT mode,projection_generation
          INTO control_mode,generation
          FROM public.dts_pipeline_control
          WHERE control_id='PRIMARY';
          IF NOT FOUND THEN
            RAISE EXCEPTION 'DTS_V2_PIPELINE_CONTROL_PRIMARY_REQUIRED'
              USING ERRCODE='55000';
          END IF;
          IF control_mode NOT IN (
            'V1_COMPAT_DUAL_CAPTURE','V2_PRIMARY','ROLLED_BACK'
          ) OR generation IS NULL OR generation < 0 THEN
            RAISE EXCEPTION 'DTS_V2_PIPELINE_CONTROL_STATE_INVALID'
              USING ERRCODE='55000';
          END IF;
          PERFORM set_config('tit.dts_v2_mode',control_mode,true);

          IF p_component <> 'DOMAIN' AND control_mode <> 'V2_PRIMARY' THEN
            PERFORM set_config(
              'tit.dts_v2_projection_generation','',true
            );
            RETURN NULL;
          END IF;
          IF p_component <> 'DOMAIN' AND generation < 1 THEN
            RAISE EXCEPTION 'DTS_V2_PROJECTION_GENERATION_INVALID'
              USING ERRCODE='55000';
          END IF;
          PERFORM set_config(
            'tit.dts_v2_projection_generation',generation::text,true
          );
          RETURN generation;
        END
        $function$;
        """
    )


def _apply_acl_and_comments() -> None:
    op.execute(
        rf"""
        REVOKE ALL ON FUNCTION {GUARD_SIGNATURE} FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION {GUARD_SIGNATURE}
        TO {DOMAIN_ROLE},{OUTBOX_ROLE};

        REVOKE SELECT ON TABLE public.dts_pipeline_control
        FROM {DOMAIN_ROLE},{OUTBOX_ROLE};
        REVOKE CREATE ON SCHEMA public
        FROM {DOMAIN_ROLE},{OUTBOX_ROLE};

        COMMENT ON FUNCTION {GUARD_SIGNATURE} IS
          'Acquire the DTS v2 cutover shared xact lock; DOMAIN receives every legal shadow mode/generation while production materializers receive V2_PRIMARY only, without raw pipeline-control reads.';
        """
    )


def _backfill_domain_runtime_acl() -> None:
    """Apply grants skipped by rev80-87 when the optional role was absent."""

    op.execute(
        rf"""
        GRANT USAGE ON SCHEMA public TO {DOMAIN_ROLE};

        GRANT EXECUTE ON FUNCTION
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
          public.reap_expired_domain_dirty_keys_v2(integer),
          public.dts_canonical_json_v1(jsonb),
          public.dts_canonical_json_sha256_v1(jsonb),
          public.publish_domain_aggregate_revision_v2(
            text,jsonb,jsonb,text,jsonb,bigint,jsonb,text,jsonb
          )
        TO {DOMAIN_ROLE};

        GRANT SELECT ON TABLE
          public.dts_source_partition_epochs,
          public.dts_source_row_versions,
          public.dts_source_rows,
          public.dts_dirty_key_inputs,
          public.dts_source_table_publish_generations,
          public.dts_source_scope_snapshots,
          public.dts_source_snapshot_fences,
          public.dts_source_scope_states,
          public.dts_source_scope_memberships,
          public.complaint_category_rules,
          public.complaint_rule_imports,
          public.domain_aggregate_revisions,
          public.course_favorite_observations,
          public.course_favorite_attributions
        TO {DOMAIN_ROLE};

        GRANT SELECT,INSERT,UPDATE ON TABLE
          public.source_courses,
          public.source_course_participations,
          public.source_course_labels,
          public.source_course_complaints,
          public.source_course_fact_current,
          public.source_participation_fact_current,
          public.teacher_student_relationship_current
        TO {DOMAIN_ROLE};
        GRANT SELECT,INSERT ON TABLE
          public.teacher_student_relationship_events
        TO {DOMAIN_ROLE};
        GRANT USAGE,SELECT ON SEQUENCE
          public.teacher_student_relationship_events_event_sequence_seq
        TO {DOMAIN_ROLE};

        REVOKE INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER ON TABLE
          public.domain_aggregate_revisions,
          public.course_favorite_observations,
          public.course_favorite_attributions
        FROM {DOMAIN_ROLE};
        """
    )


def _assert_installed_contract() -> None:
    row = op.get_bind().execute(
        sa.text(
            """
            SELECT
              procedure_oid IS NOT NULL,
              procedure_row.prosecdef,
              procedure_row.provolatile='v',
              procedure_row.proconfig @> ARRAY[
                'search_path=pg_catalog, public'
              ],
              has_function_privilege(
                :domain_role,procedure_oid,'EXECUTE'
              ),
              has_function_privilege(
                :outbox_role,procedure_oid,'EXECUTE'
              ),
              NOT EXISTS (
                SELECT 1
                FROM aclexplode(COALESCE(
                  procedure_row.proacl,
                  acldefault('f',procedure_row.proowner)
                )) acl
                WHERE acl.grantee=0 AND acl.privilege_type='EXECUTE'
              ),
              has_table_privilege(
                :domain_role,'public.dts_pipeline_control','SELECT'
              ),
              has_table_privilege(
                :outbox_role,'public.dts_pipeline_control','SELECT'
              )
            FROM (SELECT to_regprocedure(:signature) AS procedure_oid) found
            LEFT JOIN pg_proc procedure_row
              ON procedure_row.oid=found.procedure_oid
            """
        ),
        {
            "signature": GUARD_SIGNATURE,
            "domain_role": DOMAIN_ROLE,
            "outbox_role": OUTBOX_ROLE,
        },
    ).one()
    if row != (True, True, True, True, True, True, True, False, False):
        raise RuntimeError("DTS v2 runtime guard contract is incomplete")


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("DTS v2 runtime guard requires PostgreSQL")
    if not context.is_offline_mode():
        _assert_preconditions()
    _ensure_runtime_roles()
    _install_guard()
    _apply_acl_and_comments()
    _backfill_domain_runtime_acl()
    if not context.is_offline_mode():
        _assert_installed_contract()


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("DTS v2 runtime guard requires PostgreSQL")
    op.execute(
        rf"""
        REVOKE ALL ON FUNCTION {GUARD_SIGNATURE}
        FROM PUBLIC,{DOMAIN_ROLE},{OUTBOX_ROLE};
        DROP FUNCTION public.dts_v2_runtime_primary_guard_v1(text);

        -- Restore the exact rev88 Outbox ACL.  The domain role did not own a
        -- direct pipeline-control read before this revision.
        GRANT SELECT ON TABLE public.dts_pipeline_control TO {OUTBOX_ROLE};
        """
    )
