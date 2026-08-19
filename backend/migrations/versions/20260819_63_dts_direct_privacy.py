"""allow privacy-safe direct DTS lesson writes without source mirrors

Revision ID: 20260819_63_dts_direct_privacy
Revises: 20260818_62_dts_claim_idx
Create Date: 2026-08-19

Queued projection proves a lesson's region through the persisted appoint row.
Direct projection intentionally keeps no general source-row mirror, so it
labels each PostgreSQL transaction with its already validated DTS region.  The
lesson guard keeps the original queued-mode checks and accepts that label only
for the restricted DTS runtime role.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "20260819_63_dts_direct_privacy"
down_revision: str = "20260818_62_dts_claim_idx"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    op.execute(
        r"""
        CREATE OR REPLACE FUNCTION public.guard_dom_lesson_student_privacy_v1()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE
            actor_name text := COALESCE(
                NULLIF(current_setting('role', true), 'none'),
                session_user
            );
            direct_source_region text := NULLIF(
                current_setting('tit.dts_source_region', true),
                ''
            );
            has_dom_source boolean := FALSE;
            has_ovs_source boolean := FALSE;
        BEGIN
            SELECT
                COALESCE(bool_or(appoints.source_region = 'dom'), FALSE),
                COALESCE(bool_or(appoints.source_region = 'ovs'), FALSE)
            INTO has_dom_source, has_ovs_source
            FROM public.dts_source_rows appoints
            WHERE appoints.source_table IN ('dom_appoint', 'ovs_appoint')
              AND appoints.source_row ->> 'id' = NEW."课程id";

            IF has_dom_source AND has_ovs_source THEN
                RAISE EXCEPTION
                    'lesson source region is ambiguous'
                    USING ERRCODE = '23514';
            END IF;
            IF has_dom_source
               AND NEW."学员id" IS NOT NULL
               AND NEW."学员id" !~ '^dom:v1:[0-9a-f]{64}$' THEN
                RAISE EXCEPTION
                    'domestic lesson wide state contains a forbidden student identifier'
                    USING ERRCODE = '23514';
            END IF;
            IF has_dom_source OR has_ovs_source THEN
                RETURN NEW;
            END IF;

            IF actor_name <> 'tit_dts_ingest_runtime'
               OR direct_source_region NOT IN ('dom', 'ovs') THEN
                RAISE EXCEPTION
                    'lesson wide write requires persisted source provenance or direct DTS region'
                    USING ERRCODE = '23514';
            END IF;
            IF direct_source_region = 'dom'
               AND NEW."学员id" IS NOT NULL
               AND NEW."学员id" !~ '^dom:v1:[0-9a-f]{64}$' THEN
                RAISE EXCEPTION
                    'domestic lesson wide state contains a forbidden student identifier'
                    USING ERRCODE = '23514';
            END IF;
            IF direct_source_region = 'ovs'
               AND NEW."学员id" LIKE 'dom:%' THEN
                RAISE EXCEPTION
                    'overseas lesson wide state contains a domestic student token'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END
        $function$;

        REVOKE ALL ON FUNCTION public.guard_dom_lesson_student_privacy_v1()
        FROM PUBLIC;
        """
    )


def downgrade() -> None:
    raise RuntimeError(
        "20260819_63_dts_direct_privacy is forward-only: restoring the old "
        "guard would make direct projection fail after its source mirror is "
        "removed"
    )
