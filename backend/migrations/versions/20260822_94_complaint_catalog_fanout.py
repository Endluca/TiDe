"""publish one immutable complaint catalog and fan out category changes.

Revision ID: 20260822_94_complaint_catalog_fanout
Revises: 20260822_93_non_task_outputs
Create Date: 2026-08-22

The rule workbook is an independently imported DRAFT.  This revision makes
publication a catalog-CAS command, freezes the exact rule version consumed by
typed complaints/matches, and gives the complaint-category projector one
bounded, indexed reverse-fanout command.  Neither runtime role receives raw
dirty-queue DML.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260822_94_complaint_catalog_fanout"
down_revision: Union[str, None] = "20260822_93_non_task_outputs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


DOMAIN_ROLE = "tit_dts_domain_projector_runtime"
PUBLISHER_ROLE = "tit_dts_complaint_rule_publisher_runtime"
CATALOG_LOCK_KEY = (
    "tit:catalog:COMPLAINT_RULE_SET:ACTIVE_COMPLAINT_RULE_SET"
)


def _preflight_and_role() -> None:
    op.execute(
        rf"""
        DO $complaint_catalog_v2_preflight$
        DECLARE relation_name text;
        BEGIN
          FOREACH relation_name IN ARRAY ARRAY[
            'complaint_rule_imports','complaint_category_rules',
            'source_course_complaints','personalized_trigger_matches',
            'domain_aggregate_revisions','outbox_events','dts_dirty_keys',
            'dts_dirty_key_inputs','audit_events'
          ] LOOP
            IF to_regclass('public.' || relation_name) IS NULL THEN
              RAISE EXCEPTION
                'DTS_V2_COMPLAINT_CATALOG_PREREQUISITE_MISSING:%',
                relation_name;
            END IF;
          END LOOP;
          IF to_regprocedure(
               'public.dts_canonical_json_sha256_v1(jsonb)'
             ) IS NULL
             OR to_regprocedure(
               'public._upsert_dts_dirty_key_input_v2('
               'text,text,text,text,text,jsonb,bigint,text)'
             ) IS NULL
             OR to_regrole('{DOMAIN_ROLE}') IS NULL THEN
            RAISE EXCEPTION
              'DTS_V2_COMPLAINT_CATALOG_PREREQUISITE_MISSING';
          END IF;
          IF to_regprocedure(
               'public.publish_complaint_rule_import_v2(text,bigint,text)'
             ) IS NOT NULL
             OR to_regprocedure(
               'public.enqueue_complaint_category_course_fanout_v2('
               'text,bigint,text,text)'
             ) IS NOT NULL THEN
            RAISE EXCEPTION 'DTS_V2_COMPLAINT_CATALOG_ALREADY_INSTALLED';
          END IF;
          IF (SELECT count(*) FROM public.complaint_rule_imports)>1 THEN
            RAISE EXCEPTION
              'DTS_V2_COMPLAINT_CATALOG_LEGACY_ACTIVE_AMBIGUOUS';
          END IF;
        END
        $complaint_catalog_v2_preflight$;

        DO $complaint_catalog_v2_role$
        BEGIN
          IF to_regrole('{PUBLISHER_ROLE}') IS NULL THEN
            CREATE ROLE {PUBLISHER_ROLE} LOGIN NOINHERIT NOSUPERUSER
              NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
          END IF;
          IF EXISTS (
            SELECT 1 FROM pg_roles WHERE rolname='{PUBLISHER_ROLE}'
              AND (NOT rolcanlogin OR rolinherit OR rolsuper OR rolcreatedb
                   OR rolcreaterole OR rolreplication OR rolbypassrls)
          ) THEN
            RAISE EXCEPTION
              '{PUBLISHER_ROLE} must be a restricted NOINHERIT LOGIN role';
          END IF;
        END
        $complaint_catalog_v2_role$;

        LOCK TABLE public.complaint_rule_imports,
          public.complaint_category_rules,
          public.source_course_complaints,
          public.personalized_trigger_matches
        IN SHARE ROW EXCLUSIVE MODE;
        """
    )


def _expand_import_and_rule_contract() -> None:
    json_value = sa.JSON().with_variant(
        postgresql.JSONB(astext_type=sa.Text()), "postgresql"
    )
    for column in (
        sa.Column("status", sa.String(length=16)),
        sa.Column("publication_revision", sa.BigInteger()),
        sa.Column("activation_generation", sa.BigInteger()),
        sa.Column("row_count", sa.Integer()),
        sa.Column("content_hash", sa.String(length=64)),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("retired_at", sa.DateTime(timezone=True)),
    ):
        op.add_column("complaint_rule_imports", column, schema="public")

    # Freeze the SHA in match evidence as a typed column.  The old single-FK
    # did not prevent a rule_id from being paired with another import SHA.
    op.add_column(
        "personalized_trigger_matches",
        sa.Column(
            "complaint_rule_source_sha256",
            sa.String(length=64),
            nullable=True,
        ),
        schema="public",
    )
    op.drop_constraint(
        "fk_personalized_trigger_match_complaint_rule",
        "personalized_trigger_matches",
        type_="foreignkey",
        schema="public",
    )
    op.drop_constraint(
        "fk_source_course_complaint_rule_version",
        "source_course_complaints",
        type_="foreignkey",
        schema="public",
    )

    op.execute(
        r"""
        CREATE TEMP TABLE complaint_rule_id_rekey_v94(
          old_rule_id text PRIMARY KEY,
          new_rule_id text NOT NULL UNIQUE,
          source_sha256 text NOT NULL
        ) ON COMMIT DROP;
        INSERT INTO complaint_rule_id_rekey_v94(
          old_rule_id,new_rule_id,source_sha256
        )
        SELECT rule_id,
          'complaint-rule:' || lower(source_sha256) || ':' ||
            source_row_number::text,
          lower(source_sha256)
        FROM public.complaint_category_rules;

        UPDATE public.complaint_category_rules rule
        SET rule_id=mapping.new_rule_id,
            source_sha256=lower(rule.source_sha256)
        FROM complaint_rule_id_rekey_v94 mapping
        WHERE rule.rule_id=mapping.old_rule_id;

        UPDATE public.source_course_complaints complaint
        SET complaint_rule_id=mapping.new_rule_id,
            source_sha256=mapping.source_sha256
        FROM complaint_rule_id_rekey_v94 mapping
        WHERE complaint.complaint_rule_id=mapping.old_rule_id;

        UPDATE public.personalized_trigger_matches match
        SET complaint_rule_id=mapping.new_rule_id,
            complaint_rule_source_sha256=mapping.source_sha256
        FROM complaint_rule_id_rekey_v94 mapping
        WHERE match.complaint_rule_id=mapping.old_rule_id;
        """
    )

    op.create_check_constraint(
        "ck_complaint_rule_deterministic_identity_v2",
        "complaint_category_rules",
        "source_sha256 ~ '^[0-9a-f]{64}$' "
        "AND source_row_number>=1 "
        "AND rule_id='complaint-rule:' || lower(source_sha256) || ':' || "
        "source_row_number::text",
        schema="public",
    )
    op.create_unique_constraint(
        "uq_complaint_rule_source_row_v2",
        "complaint_category_rules",
        ["source_sha256", "source_row_number"],
        schema="public",
    )
    op.create_foreign_key(
        "fk_source_course_complaint_rule_version",
        "source_course_complaints",
        "complaint_category_rules",
        ["complaint_rule_id", "source_sha256"],
        ["rule_id", "source_sha256"],
        source_schema="public",
        referent_schema="public",
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_foreign_key(
        "fk_trigger_match_complaint_rule_version_v2",
        "personalized_trigger_matches",
        "complaint_category_rules",
        ["complaint_rule_id", "complaint_rule_source_sha256"],
        ["rule_id", "source_sha256"],
        source_schema="public",
        referent_schema="public",
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_check_constraint(
        "ck_trigger_match_complaint_rule_version_v2",
        "personalized_trigger_matches",
        "(complaint_rule_id IS NULL AND "
        "complaint_rule_source_sha256 IS NULL) OR "
        "(complaint_rule_id IS NOT NULL AND "
        "complaint_rule_source_sha256 ~ '^[0-9a-f]{64}$')",
        schema="public",
    )
    op.create_index(
        "ix_trigger_match_complaint_rule_version_v2",
        "personalized_trigger_matches",
        ["complaint_rule_id", "complaint_rule_source_sha256"],
        schema="public",
    )
    op.create_index(
        "ix_source_course_complaints_rule_category_reverse_v2",
        "source_course_complaints",
        ["category_l3_normalized", "source_region", "source_appoint_id"],
        schema="public",
        postgresql_where=sa.text("NOT is_deleted"),
    )

    op.execute(
        r"""
        CREATE FUNCTION public.complaint_rule_catalog_content_v1(
          p_source_sha256 text
        ) RETURNS jsonb
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
          SELECT jsonb_build_object(
            'protocol_version','complaint-rule-catalog-content-v1',
            'source_sha256',imported.source_sha256,
            'raw_rows',imported.raw_rows,
            'rules',coalesce((
              SELECT jsonb_agg(jsonb_build_object(
                'rule_id',rule.rule_id,
                'source_sha256',rule.source_sha256,
                'source_row_number',rule.source_row_number,
                'category_l1',rule.category_l1,
                'category_l2',rule.category_l2,
                'category_l3',rule.category_l3,
                'category_l3_normalized',rule.category_l3_normalized,
                'source_level',rule.source_level,
                'severity_rank',rule.severity_rank,
                'default_route',rule.default_route
              ) ORDER BY rule.source_row_number)
              FROM public.complaint_category_rules rule
              WHERE rule.source_sha256=imported.source_sha256
            ),'[]'::jsonb)
          )
          FROM public.complaint_rule_imports imported
          WHERE imported.source_sha256=p_source_sha256
        $function$;

        CREATE FUNCTION public.complaint_rule_catalog_content_hash_v1(
          p_source_sha256 text
        ) RETURNS text
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
          SELECT public.dts_canonical_json_sha256_v1(
            public.complaint_rule_catalog_content_v1(p_source_sha256)
          )
        $function$;
        REVOKE ALL ON FUNCTION
          public.complaint_rule_catalog_content_v1(text),
          public.complaint_rule_catalog_content_hash_v1(text)
        FROM PUBLIC;

        UPDATE public.complaint_rule_imports imported
        SET status='PUBLISHED',publication_revision=1,
            activation_generation=1,
            row_count=jsonb_array_length(imported.raw_rows),
            content_hash=public.complaint_rule_catalog_content_hash_v1(
              imported.source_sha256
            ),
            published_at=imported.imported_at,retired_at=NULL;

        DO $complaint_catalog_v2_legacy_consistency$
        DECLARE invalid_sha text;
        BEGIN
          SELECT imported.source_sha256 INTO invalid_sha
          FROM public.complaint_rule_imports imported
          WHERE jsonb_typeof(imported.raw_rows)<>'array'
             OR imported.row_count<>jsonb_array_length(imported.raw_rows)
             OR imported.row_count<>(
               SELECT count(*) FROM public.complaint_category_rules rule
               WHERE rule.source_sha256=imported.source_sha256
             )
             OR imported.content_hash IS DISTINCT FROM
               public.complaint_rule_catalog_content_hash_v1(
                 imported.source_sha256
               )
          LIMIT 1;
          IF invalid_sha IS NOT NULL THEN
            RAISE EXCEPTION
              'DTS_V2_COMPLAINT_CATALOG_LEGACY_CONTENT_INVALID:%',
              invalid_sha;
          END IF;
        END
        $complaint_catalog_v2_legacy_consistency$;
        """
    )

    for name, type_ in (
        ("status", sa.String(length=16)),
        ("publication_revision", sa.BigInteger()),
        ("row_count", sa.Integer()),
        ("content_hash", sa.String(length=64)),
    ):
        op.alter_column(
            "complaint_rule_imports",
            name,
            existing_type=type_,
            nullable=False,
            schema="public",
        )

    op.create_check_constraint(
        "ck_complaint_rule_import_lifecycle_v2",
        "complaint_rule_imports",
        "status IN ('DRAFT','PUBLISHED','RETIRED') "
        "AND publication_revision>=1 AND row_count>=1 "
        "AND jsonb_typeof(raw_rows)='array' "
        "AND row_count=jsonb_array_length(raw_rows) "
        "AND content_hash ~ '^[0-9a-f]{64}$' "
        "AND ((status='DRAFT' AND activation_generation IS NULL "
        "AND published_at IS NULL AND retired_at IS NULL) OR "
        "(status='PUBLISHED' AND activation_generation>=1 "
        "AND published_at IS NOT NULL AND retired_at IS NULL) OR "
        "(status='RETIRED' AND activation_generation>=1 "
        "AND published_at IS NOT NULL AND retired_at IS NOT NULL "
        "AND retired_at>=published_at))",
        schema="public",
    )
    op.create_index(
        "uq_complaint_rule_import_published_v2",
        "complaint_rule_imports",
        ["status"],
        unique=True,
        schema="public",
        postgresql_where=sa.text("status='PUBLISHED'"),
    )
    op.create_index(
        "uq_complaint_rule_activation_generation_v2",
        "complaint_rule_imports",
        ["activation_generation"],
        unique=True,
        schema="public",
        postgresql_where=sa.text("activation_generation IS NOT NULL"),
    )


def _install_immutability_guards() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.guard_complaint_rule_import_v2()
        RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE publication_context boolean :=
          current_setting('tit.complaint_catalog_publication_v2',true)='on';
        BEGIN
          IF TG_OP='DELETE' THEN
            IF OLD.status IN ('PUBLISHED','RETIRED') THEN
              RAISE EXCEPTION 'COMPLAINT_RULE_IMPORT_IMMUTABLE'
                USING ERRCODE='23514';
            END IF;
            RETURN OLD;
          END IF;
          IF TG_OP='INSERT' THEN
            IF NEW.status<>'DRAFT' OR NEW.publication_revision<>1
               OR NEW.activation_generation IS NOT NULL
               OR NEW.published_at IS NOT NULL OR NEW.retired_at IS NOT NULL
            THEN
              RAISE EXCEPTION 'COMPLAINT_RULE_IMPORT_DRAFT_REQUIRED'
                USING ERRCODE='23514';
            END IF;
            RETURN NEW;
          END IF;
          IF NEW.source_sha256 IS DISTINCT FROM OLD.source_sha256
             OR NEW.source_filename IS DISTINCT FROM OLD.source_filename
             OR NEW.imported_at IS DISTINCT FROM OLD.imported_at THEN
            RAISE EXCEPTION 'COMPLAINT_RULE_IMPORT_IDENTITY_IMMUTABLE'
              USING ERRCODE='23514';
          END IF;
          IF OLD.status IN ('PUBLISHED','RETIRED') AND (
               NEW.raw_rows IS DISTINCT FROM OLD.raw_rows
               OR NEW.row_count IS DISTINCT FROM OLD.row_count
               OR NEW.content_hash IS DISTINCT FROM OLD.content_hash
               OR NEW.activation_generation IS DISTINCT FROM
                    OLD.activation_generation
               OR NEW.published_at IS DISTINCT FROM OLD.published_at
          ) THEN
            RAISE EXCEPTION 'COMPLAINT_RULE_IMPORT_IMMUTABLE'
              USING ERRCODE='23514';
          END IF;
          IF OLD.status='RETIRED' AND ROW(NEW.*) IS DISTINCT FROM ROW(OLD.*)
          THEN
            RAISE EXCEPTION 'COMPLAINT_RULE_IMPORT_RETIRED_TERMINAL'
              USING ERRCODE='23514';
          END IF;
          IF NEW.status IS DISTINCT FROM OLD.status THEN
            IF NOT publication_context
               OR NOT ((OLD.status='DRAFT' AND NEW.status='PUBLISHED')
                 OR (OLD.status='PUBLISHED' AND NEW.status='RETIRED'))
               OR NEW.publication_revision<>OLD.publication_revision+1
            THEN
              RAISE EXCEPTION 'COMPLAINT_RULE_PUBLICATION_COMMAND_REQUIRED'
                USING ERRCODE='42501';
            END IF;
          ELSIF NEW.publication_revision IS DISTINCT FROM
                  OLD.publication_revision THEN
            RAISE EXCEPTION 'COMPLAINT_RULE_PUBLICATION_REVISION_INVALID'
              USING ERRCODE='23514';
          END IF;
          RETURN NEW;
        END
        $function$;

        CREATE FUNCTION public.guard_complaint_category_rule_v2()
        RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE parent_status text;
        DECLARE old_parent_status text;
        BEGIN
          IF TG_OP='DELETE' THEN
            SELECT status INTO parent_status
            FROM public.complaint_rule_imports
            WHERE source_sha256=OLD.source_sha256;
            IF parent_status IN ('PUBLISHED','RETIRED') THEN
              RAISE EXCEPTION 'COMPLAINT_RULE_CHILD_IMMUTABLE'
                USING ERRCODE='23514';
            END IF;
            RETURN OLD;
          END IF;
          IF TG_OP='UPDATE' THEN
            SELECT status INTO old_parent_status
            FROM public.complaint_rule_imports
            WHERE source_sha256=OLD.source_sha256 FOR KEY SHARE;
            IF old_parent_status IN ('PUBLISHED','RETIRED')
               AND ROW(NEW.*) IS DISTINCT FROM ROW(OLD.*) THEN
              RAISE EXCEPTION 'COMPLAINT_RULE_CHILD_IMMUTABLE'
                USING ERRCODE='23514';
            END IF;
          END IF;
          SELECT status INTO parent_status
          FROM public.complaint_rule_imports
          WHERE source_sha256=NEW.source_sha256 FOR KEY SHARE;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'COMPLAINT_RULE_IMPORT_MISSING'
              USING ERRCODE='23503';
          END IF;
          IF parent_status IN ('PUBLISHED','RETIRED') AND
             (TG_OP='INSERT' OR ROW(NEW.*) IS DISTINCT FROM ROW(OLD.*)) THEN
            RAISE EXCEPTION 'COMPLAINT_RULE_CHILD_IMMUTABLE'
              USING ERRCODE='23514';
          END IF;
          RETURN NEW;
        END
        $function$;

        CREATE FUNCTION public.freeze_trigger_match_complaint_rule_v2()
        RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE expected_sha text;
        BEGIN
          IF TG_OP='DELETE' THEN RETURN OLD; END IF;
          IF NEW.complaint_rule_id IS NULL THEN
            NEW.complaint_rule_source_sha256:=NULL;
            RETURN NEW;
          END IF;
          SELECT source_sha256 INTO expected_sha
          FROM public.complaint_category_rules
          WHERE rule_id=NEW.complaint_rule_id FOR KEY SHARE;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'COMPLAINT_RULE_REFERENCE_INVALID'
              USING ERRCODE='23503';
          END IF;
          IF nullif(NEW.plan_evidence->>'source_sha256','') IS NOT NULL
             AND NEW.plan_evidence->>'source_sha256'<>expected_sha THEN
            RAISE EXCEPTION 'COMPLAINT_RULE_VERSION_MISMATCH'
              USING ERRCODE='23514';
          END IF;
          NEW.complaint_rule_source_sha256:=expected_sha;
          RETURN NEW;
        END
        $function$;

        REVOKE ALL ON FUNCTION
          public.guard_complaint_rule_import_v2(),
          public.guard_complaint_category_rule_v2(),
          public.freeze_trigger_match_complaint_rule_v2()
        FROM PUBLIC;
        CREATE TRIGGER trg_guard_complaint_rule_import_v2
        BEFORE INSERT OR UPDATE OR DELETE ON public.complaint_rule_imports
        FOR EACH ROW EXECUTE FUNCTION public.guard_complaint_rule_import_v2();
        CREATE TRIGGER trg_guard_complaint_category_rule_v2
        BEFORE INSERT OR UPDATE OR DELETE ON public.complaint_category_rules
        FOR EACH ROW EXECUTE FUNCTION public.guard_complaint_category_rule_v2();
        CREATE TRIGGER trg_aa_freeze_trigger_match_complaint_rule_v2
        BEFORE INSERT OR UPDATE ON public.personalized_trigger_matches
        FOR EACH ROW
        EXECUTE FUNCTION public.freeze_trigger_match_complaint_rule_v2();
        """
    )


def _create_publication_audit() -> None:
    op.create_table(
        "complaint_rule_publication_audits",
        sa.Column("publication_audit_id", sa.String(length=160), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("previous_source_sha256", sa.String(length=64)),
        sa.Column("expected_catalog_generation", sa.BigInteger(), nullable=False),
        sa.Column("activation_generation", sa.BigInteger(), nullable=False),
        sa.Column("publication_revision", sa.BigInteger(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("affected_categories", postgresql.JSONB(), nullable=False),
        sa.Column("affected_category_count", sa.Integer(), nullable=False),
        sa.Column("course_count", sa.Integer(), nullable=False),
        sa.Column("response_payload", postgresql.JSONB(), nullable=False),
        sa.Column("response_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.PrimaryKeyConstraint(
            "publication_audit_id",
            name="pk_complaint_rule_publication_audits",
        ),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_complaint_rule_publication_idempotency",
        ),
        sa.UniqueConstraint(
            "activation_generation",
            name="uq_complaint_rule_publication_generation",
        ),
        sa.ForeignKeyConstraint(
            ["source_sha256"],
            ["public.complaint_rule_imports.source_sha256"],
            name="fk_complaint_rule_publication_source",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["previous_source_sha256"],
            ["public.complaint_rule_imports.source_sha256"],
            name="fk_complaint_rule_publication_previous",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$' "
            "AND content_hash ~ '^[0-9a-f]{64}$' "
            "AND response_hash ~ '^[0-9a-f]{64}$' "
            "AND response_hash=public.dts_canonical_json_sha256_v1(response_payload) "
            "AND jsonb_typeof(affected_categories)='array' "
            "AND affected_category_count=jsonb_array_length(affected_categories) "
            "AND expected_catalog_generation>=0 "
            "AND activation_generation=expected_catalog_generation+1 "
            "AND publication_revision>=2 AND course_count>=0",
            name="ck_complaint_rule_publication_audit_v2",
        ),
        schema="public",
    )
    op.execute(
        r"""
        CREATE FUNCTION public.guard_complaint_rule_publication_audit_v2()
        RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        BEGIN
          IF TG_OP IN ('UPDATE','DELETE') THEN
            RAISE EXCEPTION 'COMPLAINT_RULE_PUBLICATION_AUDIT_IMMUTABLE'
              USING ERRCODE='42501';
          END IF;
          RETURN NEW;
        END
        $function$;
        REVOKE ALL ON FUNCTION
          public.guard_complaint_rule_publication_audit_v2()
        FROM PUBLIC;
        CREATE TRIGGER trg_guard_complaint_rule_publication_audit_v2
        BEFORE UPDATE OR DELETE
        ON public.complaint_rule_publication_audits
        FOR EACH ROW
        EXECUTE FUNCTION public.guard_complaint_rule_publication_audit_v2();
        """
    )


def _install_catalog_dirty_wrapper() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.enqueue_dirty_from_catalog_revision_v2(
          p_source_region text,p_source_appoint_id text,
          p_activation_generation bigint,p_source_sha256 text,
          p_content_hash text
        ) RETURNS jsonb
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE actor_name text:=coalesce(
          nullif(current_setting('role',true),'none'),session_user
        );
        DECLARE publication
          public.complaint_rule_publication_audits%ROWTYPE;
        DECLARE identity jsonb;
        DECLARE fingerprint text;
        BEGIN
          IF actor_name<>current_user AND actor_name<>
               'tit_dts_complaint_rule_publisher_runtime' THEN
            RAISE EXCEPTION 'COMPLAINT_RULE_CATALOG_CALLER_FORBIDDEN'
              USING ERRCODE='42501';
          END IF;
          SELECT * INTO publication
          FROM public.complaint_rule_publication_audits
          WHERE activation_generation=p_activation_generation
            AND source_sha256=p_source_sha256
            AND content_hash=p_content_hash
          FOR SHARE;
          IF NOT FOUND OR p_source_region NOT IN ('dom','ovs')
             OR p_source_appoint_id IS NULL
             OR btrim(p_source_appoint_id)='' OR
             NOT EXISTS(
               SELECT 1 FROM public.complaint_rule_imports imported
               WHERE imported.source_sha256=p_source_sha256
                 AND imported.status='PUBLISHED'
                 AND imported.activation_generation=p_activation_generation
                 AND imported.content_hash=p_content_hash
             ) OR NOT EXISTS(
               SELECT 1 FROM public.source_course_complaints complaint
               WHERE complaint.source_region=p_source_region
                 AND complaint.source_appoint_id=p_source_appoint_id
                 AND NOT complaint.is_deleted
                 AND complaint.category_l3_normalized IN (
                   SELECT jsonb_array_elements_text(
                     publication.affected_categories
                   )
                 )
             ) THEN
            RAISE EXCEPTION 'DIRTY_INPUT_REFERENCE_INVALID'
              USING ERRCODE='23503';
          END IF;
          identity:=jsonb_build_object(
            'catalog_type','COMPLAINT_RULE_SET',
            'catalog_key','ACTIVE_COMPLAINT_RULE_SET'
          );
          fingerprint:=public.dts_canonical_json_sha256_v1(
            jsonb_build_object(
              'protocol','dirty-catalog-v1','identity',identity,
              'revision',p_activation_generation,
              'version_id',p_source_sha256,
              'payload_hash',p_content_hash
            )
          );
          RETURN public._upsert_dts_dirty_key_input_v2(
            p_source_region,'COURSE',p_source_appoint_id,'',
            'CATALOG_REVISION',identity,p_activation_generation,
            fingerprint
          );
        END
        $function$;
        REVOKE ALL ON FUNCTION
          public.enqueue_dirty_from_catalog_revision_v2(
            text,text,bigint,text,text
          )
        FROM PUBLIC;
        """
    )


def _install_category_reverse_fanout() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.enqueue_complaint_category_course_fanout_v2(
          p_category_id text,p_expected_aggregate_revision bigint,
          p_triggering_event_id text,p_expected_command_sha256 text
        ) RETURNS jsonb
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE actor_name text:=coalesce(
          nullif(current_setting('role',true),'none'),session_user
        );
        DECLARE aggregate_row public.domain_aggregate_revisions%ROWTYPE;
        DECLARE event_row public.outbox_events%ROWTYPE;
        DECLARE request_document jsonb;
        DECLARE request_hash text;
        DECLARE dependency_identity jsonb;
        DECLARE dependency_fingerprint text;
        DECLARE linked_course record;
        DECLARE dirty_result jsonb;
        DECLARE seen_count integer:=0;
        DECLARE enqueued_count integer:=0;
        DECLARE noop_count integer:=0;
        BEGIN
          IF actor_name<>current_user AND actor_name<>
               'tit_dts_domain_projector_runtime' THEN
            RAISE EXCEPTION
              'DTS_V2_COMPLAINT_CATEGORY_FANOUT_CALLER_FORBIDDEN'
              USING ERRCODE='42501';
          END IF;
          IF p_category_id IS NULL OR btrim(p_category_id)=''
             OR p_category_id<>btrim(p_category_id)
             OR p_expected_aggregate_revision<1
             OR p_triggering_event_id IS NULL
             OR btrim(p_triggering_event_id)=''
             OR p_expected_command_sha256 !~ '^[0-9a-f]{64}$' THEN
            RAISE EXCEPTION
              'DTS_V2_COMPLAINT_CATEGORY_FANOUT_REQUEST_INVALID'
              USING ERRCODE='22023';
          END IF;
          PERFORM pg_advisory_xact_lock_shared(
            hashtextextended('tit:dts-v2-cutover',0)
          );
          request_document:=jsonb_build_object(
            'protocol_version',
              'complaint-category-course-fanout-command-v1',
            'category_id',p_category_id,
            'aggregate_revision',p_expected_aggregate_revision,
            'triggering_event_id',p_triggering_event_id
          );
          request_hash:=public.dts_canonical_json_sha256_v1(
            request_document
          );
          IF request_hash<>p_expected_command_sha256 THEN
            RAISE EXCEPTION
              'DTS_V2_COMPLAINT_CATEGORY_FANOUT_HASH_MISMATCH'
              USING ERRCODE='23514';
          END IF;
          SELECT * INTO aggregate_row
          FROM public.domain_aggregate_revisions
          WHERE aggregate_type='COMPLAINT_CATEGORY'
            AND canonical_key=jsonb_build_object(
              'source_region','dom','category_id',p_category_id
            )
          FOR SHARE;
          IF NOT FOUND OR aggregate_row.revision<>
               p_expected_aggregate_revision THEN
            RAISE EXCEPTION
              'DTS_V2_COMPLAINT_CATEGORY_REVISION_CHANGED'
              USING ERRCODE='40001';
          END IF;
          SELECT * INTO event_row FROM public.outbox_events
          WHERE event_id=p_triggering_event_id FOR SHARE;
          IF NOT FOUND OR event_row.aggregate_type<>'COMPLAINT_CATEGORY'
             OR event_row.aggregate_id<>aggregate_row.aggregate_id
             OR event_row.event_type<>'source_wide.changed.v2'
             OR (event_row.payload->>'aggregate_revision')::bigint
                  IS DISTINCT FROM
                  p_expected_aggregate_revision
             OR event_row.payload->'aggregate_key' IS DISTINCT FROM
                  aggregate_row.canonical_key THEN
            RAISE EXCEPTION
              'DTS_V2_COMPLAINT_CATEGORY_EVENT_INVALID'
              USING ERRCODE='23514';
          END IF;
          dependency_identity:=jsonb_build_object(
            'dependency_type','COMPLAINT_CATEGORY',
            'dependency_region','dom','dependency_key',p_category_id
          );
          dependency_fingerprint:=public.dts_canonical_json_sha256_v1(
            jsonb_build_object(
              'protocol','dirty-dependency-v1',
              'identity',dependency_identity,
              'revision',p_expected_aggregate_revision,
              'dependency_hash',aggregate_row.aggregate_state_sha256
            )
          );
          FOR linked_course IN
            SELECT source_region,source_appoint_id FROM (
              SELECT source_region,source_appoint_id
              FROM public.source_course_complaints
              WHERE complaint_type_type IN ('NUMERIC','TEXT')
                AND complaint_type=p_category_id AND NOT is_deleted
              UNION
              SELECT source_region,source_appoint_id
              FROM public.source_course_complaints
              WHERE complaint_type_child_type IN ('NUMERIC','TEXT')
                AND complaint_type_child=p_category_id AND NOT is_deleted
              UNION
              SELECT source_region,source_appoint_id
              FROM public.source_course_complaints
              WHERE complaint_type_grandson_type IN ('NUMERIC','TEXT')
                AND complaint_type_grandson=p_category_id AND NOT is_deleted
            ) linked
            ORDER BY convert_to(source_region,'UTF8'),
              convert_to(source_appoint_id,'UTF8')
          LOOP
            seen_count:=seen_count+1;
            dirty_result:=public._upsert_dts_dirty_key_input_v2(
              linked_course.source_region,'COURSE',
              linked_course.source_appoint_id,'','DEPENDENCY_WAKE',
              dependency_identity,p_expected_aggregate_revision,
              dependency_fingerprint
            );
            IF dirty_result->>'status'='ENQUEUED' THEN
              enqueued_count:=enqueued_count+1;
            ELSIF dirty_result->>'status' IN ('NOOP','NOOP_OLDER') THEN
              noop_count:=noop_count+1;
            ELSE
              RAISE EXCEPTION
                'DTS_V2_COMPLAINT_CATEGORY_FANOUT_RESULT_INVALID'
                USING ERRCODE='23514';
            END IF;
          END LOOP;
          RETURN jsonb_build_object(
            'protocol_version','complaint-category-course-fanout-v1',
            'command_sha256',request_hash,
            'counts',jsonb_build_object(
              'courses_seen',seen_count,
              'courses_enqueued',enqueued_count,
              'courses_noop',noop_count
            )
          );
        END
        $function$;
        REVOKE ALL ON FUNCTION
          public.enqueue_complaint_category_course_fanout_v2(
            text,bigint,text,text
          )
        FROM PUBLIC;
        """
    )


def _install_publication_command() -> None:
    op.execute(
        rf"""
        CREATE FUNCTION public.publish_complaint_rule_import_v2(
          p_source_sha256 text,p_expected_revision bigint,
          p_idempotency_key text
        ) RETURNS jsonb
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE actor_name text:=coalesce(
          nullif(current_setting('role',true),'none'),session_user
        );
        DECLARE request_document jsonb;
        DECLARE request_hash text;
        DECLARE replay
          public.complaint_rule_publication_audits%ROWTYPE;
        DECLARE target public.complaint_rule_imports%ROWTYPE;
        DECLARE previous public.complaint_rule_imports%ROWTYPE;
        DECLARE previous_found boolean:=false;
        DECLARE current_generation bigint:=0;
        DECLARE next_generation bigint;
        DECLARE actual_raw_count integer;
        DECLARE actual_rule_count integer;
        DECLARE actual_content_hash text;
        DECLARE affected_categories jsonb;
        DECLARE affected_count integer;
        DECLARE course_count integer;
        DECLARE course_row record;
        DECLARE dirty_result jsonb;
        DECLARE enqueued_count integer:=0;
        DECLARE noop_count integer:=0;
        DECLARE publication_revision bigint;
        DECLARE publication_audit_id text;
        DECLARE response_document jsonb;
        DECLARE response_hash text;
        DECLARE audit_event_id text;
        DECLARE audit_payload jsonb;
        BEGIN
          IF actor_name<>current_user AND actor_name<>
               '{PUBLISHER_ROLE}' THEN
            RAISE EXCEPTION 'COMPLAINT_RULE_CATALOG_CALLER_FORBIDDEN'
              USING ERRCODE='42501';
          END IF;
          IF p_source_sha256 !~ '^[0-9a-f]{{64}}$'
             OR p_expected_revision<0
             OR p_idempotency_key IS NULL
             OR p_idempotency_key !~ '^[A-Za-z0-9._:-]{{1,128}}$' THEN
            RAISE EXCEPTION 'COMPLAINT_RULE_PUBLICATION_REQUEST_INVALID'
              USING ERRCODE='22023';
          END IF;
          request_document:=jsonb_build_object(
            'protocol_version','complaint-rule-publication-command-v2',
            'source_sha256',p_source_sha256,
            'expected_revision',p_expected_revision
          );
          request_hash:=public.dts_canonical_json_sha256_v1(
            request_document
          );
          PERFORM pg_advisory_xact_lock_shared(
            hashtextextended('tit:dts-v2-cutover',0)
          );
          PERFORM pg_advisory_xact_lock(
            hashtextextended('{CATALOG_LOCK_KEY}',0)
          );
          SELECT * INTO replay
          FROM public.complaint_rule_publication_audits
          WHERE idempotency_key=p_idempotency_key FOR SHARE;
          IF FOUND THEN
            IF replay.request_hash<>request_hash
               OR replay.source_sha256<>p_source_sha256
               OR replay.expected_catalog_generation<>p_expected_revision
               OR replay.response_hash<>
                  public.dts_canonical_json_sha256_v1(
                    replay.response_payload
                  ) THEN
              RAISE EXCEPTION 'COMPLAINT_RULE_PUBLICATION_CONFLICT'
                USING ERRCODE='23505';
            END IF;
            RETURN replay.response_payload ||
              jsonb_build_object('replay_status','REPLAYED');
          END IF;
          SELECT * INTO previous
          FROM public.complaint_rule_imports
          WHERE status='PUBLISHED' FOR UPDATE;
          previous_found:=FOUND;
          SELECT coalesce(max(activation_generation),0)
          INTO current_generation
          FROM public.complaint_rule_imports;
          IF (previous_found AND previous.activation_generation
                IS DISTINCT FROM current_generation)
             OR (NOT previous_found AND current_generation<>0) THEN
            RAISE EXCEPTION 'COMPLAINT_RULE_PUBLICATION_CONFLICT'
              USING ERRCODE='40001';
          END IF;
          IF current_generation<>p_expected_revision THEN
            RAISE EXCEPTION 'COMPLAINT_RULE_PUBLICATION_CONFLICT'
              USING ERRCODE='40001';
          END IF;
          SELECT * INTO target FROM public.complaint_rule_imports
          WHERE source_sha256=p_source_sha256 FOR UPDATE;
          IF NOT FOUND OR target.status<>'DRAFT'
             OR target.publication_revision<>1 THEN
            RAISE EXCEPTION 'COMPLAINT_RULE_PUBLICATION_CONFLICT'
              USING ERRCODE='40001';
          END IF;
          actual_raw_count:=jsonb_array_length(target.raw_rows);
          SELECT count(*) INTO actual_rule_count
          FROM public.complaint_category_rules
          WHERE source_sha256=p_source_sha256;
          actual_content_hash:=
            public.complaint_rule_catalog_content_hash_v1(
              p_source_sha256
            );
          IF actual_raw_count<>target.row_count
             OR actual_rule_count<>target.row_count
             OR actual_content_hash<>target.content_hash
             OR EXISTS(
               SELECT 1 FROM public.complaint_category_rules rule
               WHERE rule.source_sha256=p_source_sha256
                 AND rule.rule_id<>'complaint-rule:' ||
                   lower(p_source_sha256) || ':' ||
                   rule.source_row_number::text
             ) THEN
            RAISE EXCEPTION 'COMPLAINT_RULE_PUBLICATION_CONTENT_INVALID'
              USING ERRCODE='23514';
          END IF;
          SELECT coalesce(jsonb_agg(name ORDER BY convert_to(name,'UTF8')),
                          '[]'::jsonb)
          INTO affected_categories
          FROM (
            SELECT coalesce(new_rule.category_l3_normalized,
                            old_rule.category_l3_normalized) AS name
            FROM (
              SELECT * FROM public.complaint_category_rules
              WHERE source_sha256=p_source_sha256
            ) new_rule
            FULL JOIN (
              SELECT * FROM public.complaint_category_rules
              WHERE source_sha256=previous.source_sha256
            ) old_rule USING (category_l3_normalized)
            WHERE ROW(new_rule.category_l1,new_rule.category_l2,
                      new_rule.category_l3,new_rule.source_level,
                      new_rule.severity_rank,new_rule.default_route)
              IS DISTINCT FROM
                  ROW(old_rule.category_l1,old_rule.category_l2,
                      old_rule.category_l3,old_rule.source_level,
                      old_rule.severity_rank,old_rule.default_route)
          ) changed;
          affected_count:=jsonb_array_length(affected_categories);
          SELECT count(*) INTO course_count FROM (
            SELECT DISTINCT complaint.source_region,
              complaint.source_appoint_id
            FROM public.source_course_complaints complaint
            WHERE NOT complaint.is_deleted
              AND complaint.category_l3_normalized IN (
                SELECT jsonb_array_elements_text(affected_categories)
              )
          ) affected_course;
          next_generation:=current_generation+1;
          PERFORM set_config(
            'tit.complaint_catalog_publication_v2','on',true
          );
          IF previous.source_sha256 IS NOT NULL THEN
            UPDATE public.complaint_rule_imports imported
            SET status='RETIRED',
                publication_revision=imported.publication_revision+1,
                retired_at=transaction_timestamp()
            WHERE imported.source_sha256=previous.source_sha256
              AND imported.status='PUBLISHED'
              AND imported.activation_generation=current_generation;
            IF NOT FOUND THEN
              RAISE EXCEPTION 'COMPLAINT_RULE_PUBLICATION_CONFLICT'
                USING ERRCODE='40001';
            END IF;
          END IF;
          UPDATE public.complaint_rule_imports imported
          SET status='PUBLISHED',
              publication_revision=imported.publication_revision+1,
              activation_generation=next_generation,
              published_at=transaction_timestamp(),retired_at=NULL
          WHERE imported.source_sha256=p_source_sha256
            AND imported.status='DRAFT'
            AND imported.publication_revision=1
          RETURNING imported.publication_revision
          INTO publication_revision;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'COMPLAINT_RULE_PUBLICATION_CONFLICT'
              USING ERRCODE='40001';
          END IF;
          publication_audit_id:='complaint-rule-publication:' ||
            encode(sha256(convert_to(p_idempotency_key,'UTF8')),'hex');
          response_document:=jsonb_build_object(
            'protocol_version','complaint-rule-publication-result-v2',
            'status','PUBLISHED','replay_status','APPLIED',
            'source_sha256',p_source_sha256,
            'previous_source_sha256',previous.source_sha256,
            'activation_generation',next_generation,
            'publication_revision',publication_revision,
            'content_hash',actual_content_hash,
            'affected_category_count',affected_count,
            'courses_seen',course_count,
            'courses_enqueued',course_count,
            'courses_noop',0,
            'publication_audit_id',publication_audit_id
          );
          response_hash:=public.dts_canonical_json_sha256_v1(
            response_document
          );
          INSERT INTO public.complaint_rule_publication_audits(
            publication_audit_id,idempotency_key,request_hash,
            source_sha256,previous_source_sha256,
            expected_catalog_generation,activation_generation,
            publication_revision,content_hash,affected_categories,
            affected_category_count,course_count,response_payload,
            response_hash
          ) VALUES (
            publication_audit_id,p_idempotency_key,request_hash,
            p_source_sha256,previous.source_sha256,p_expected_revision,
            next_generation,publication_revision,actual_content_hash,
            affected_categories,affected_count,course_count,
            response_document,response_hash
          );
          FOR course_row IN
            SELECT affected_course.source_region,
              affected_course.source_appoint_id
            FROM (
              SELECT DISTINCT complaint.source_region,
                complaint.source_appoint_id
              FROM public.source_course_complaints complaint
              WHERE NOT complaint.is_deleted
                AND complaint.category_l3_normalized IN (
                  SELECT jsonb_array_elements_text(affected_categories)
                )
            ) affected_course
            ORDER BY convert_to(affected_course.source_region,'UTF8'),
              convert_to(affected_course.source_appoint_id,'UTF8')
          LOOP
            dirty_result:=public.enqueue_dirty_from_catalog_revision_v2(
              course_row.source_region,course_row.source_appoint_id,
              next_generation,p_source_sha256,actual_content_hash
            );
            IF dirty_result->>'status'='ENQUEUED' THEN
              enqueued_count:=enqueued_count+1;
            ELSIF dirty_result->>'status' IN ('NOOP','NOOP_OLDER') THEN
              noop_count:=noop_count+1;
            ELSE
              RAISE EXCEPTION 'COMPLAINT_RULE_PUBLICATION_FANOUT_INVALID'
                USING ERRCODE='23514';
            END IF;
          END LOOP;
          IF enqueued_count<>course_count OR noop_count<>0 THEN
            RAISE EXCEPTION 'COMPLAINT_RULE_PUBLICATION_FANOUT_INVALID'
              USING ERRCODE='23514';
          END IF;
          audit_event_id:='audit:complaint-rule-publication:' ||
            encode(sha256(convert_to(p_idempotency_key,'UTF8')),'hex');
          audit_payload:=jsonb_build_object(
            'protocol_version','complaint-rule-publication-audit-v2',
            'source_sha256',p_source_sha256,
            'previous_source_sha256',previous.source_sha256,
            'activation_generation',next_generation,
            'publication_revision',publication_revision,
            'content_hash',actual_content_hash,
            'affected_category_count',affected_count,
            'course_count',course_count,
            'request_hash',request_hash
          );
          INSERT INTO public.audit_events(
            event_id,event_type,teacher_id,task_id,case_id,occurred_at,
            actor_type,payload_hash,payload
          ) VALUES (
            audit_event_id,'COMPLAINT_RULE_CATALOG_PUBLISHED_V2',
            NULL,NULL,NULL,transaction_timestamp(),
            'COMPLAINT_RULE_CATALOG',
            public.dts_canonical_json_sha256_v1(audit_payload),audit_payload
          );
          RETURN response_document;
        END
        $function$;
        REVOKE ALL ON FUNCTION
          public.publish_complaint_rule_import_v2(text,bigint,text)
        FROM PUBLIC;
        """
    )


def _install_health() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.dts_v2_complaint_catalog_health_v1()
        RETURNS jsonb
        LANGUAGE plpgsql VOLATILE SECURITY DEFINER
        SET search_path=pg_catalog,public
        AS $function$
        DECLARE active_count integer;
        DECLARE active_row public.complaint_rule_imports%ROWTYPE;
        DECLARE actual_rule_count integer;
        DECLARE actual_hash text;
        BEGIN
          PERFORM pg_advisory_xact_lock_shared(
            hashtextextended('tit:dts-v2-cutover',0)
          );
          PERFORM pg_advisory_xact_lock_shared(
            hashtextextended(
              'tit:catalog:COMPLAINT_RULE_SET:ACTIVE_COMPLAINT_RULE_SET',0
            )
          );
          SELECT count(*) INTO active_count
          FROM public.complaint_rule_imports WHERE status='PUBLISHED';
          SELECT * INTO active_row FROM public.complaint_rule_imports
          WHERE status='PUBLISHED' FOR SHARE;
          IF active_count=1 THEN
            SELECT count(*) INTO actual_rule_count
            FROM public.complaint_category_rules
            WHERE source_sha256=active_row.source_sha256;
            actual_hash:=public.complaint_rule_catalog_content_hash_v1(
              active_row.source_sha256
            );
          ELSE
            actual_rule_count:=0; actual_hash:=NULL;
          END IF;
          RETURN jsonb_build_object(
            'protocol_version','dts-v2-complaint-catalog-health-v1',
            'ready',active_count=1
              AND active_row.activation_generation>=1
              AND active_row.row_count=actual_rule_count
              AND active_row.content_hash=actual_hash,
            'active_count',active_count,
            'source_sha256',active_row.source_sha256,
            'activation_generation',active_row.activation_generation,
            'row_count',active_row.row_count,
            'content_hash',active_row.content_hash
          );
        END
        $function$;
        REVOKE ALL ON FUNCTION
          public.dts_v2_complaint_catalog_health_v1()
        FROM PUBLIC;
        """
    )


def _apply_acl() -> None:
    op.execute(
        rf"""
        REVOKE ALL PRIVILEGES ON TABLE
          public.complaint_rule_imports,
          public.complaint_category_rules,
          public.complaint_rule_publication_audits
        FROM PUBLIC,{PUBLISHER_ROLE};
        REVOKE ALL PRIVILEGES ON TABLE
          public.dts_dirty_keys,public.dts_dirty_key_inputs
        FROM {PUBLISHER_ROLE},{DOMAIN_ROLE};
        GRANT SELECT ON TABLE public.dts_dirty_key_inputs
        TO {DOMAIN_ROLE};
        REVOKE ALL ON FUNCTION
          public.complaint_rule_catalog_content_v1(text),
          public.complaint_rule_catalog_content_hash_v1(text),
          public._upsert_dts_dirty_key_input_v2(
            text,text,text,text,text,jsonb,bigint,text
          )
        FROM PUBLIC,{PUBLISHER_ROLE},{DOMAIN_ROLE};
        GRANT USAGE ON SCHEMA public TO {PUBLISHER_ROLE};
        GRANT EXECUTE ON FUNCTION
          public.publish_complaint_rule_import_v2(text,bigint,text),
          public.enqueue_dirty_from_catalog_revision_v2(
            text,text,bigint,text,text
          ),
          public.dts_v2_complaint_catalog_health_v1()
        TO {PUBLISHER_ROLE};
        GRANT EXECUTE ON FUNCTION
          public.enqueue_complaint_category_course_fanout_v2(
            text,bigint,text,text
          ),
          public.dts_v2_complaint_catalog_health_v1()
        TO {DOMAIN_ROLE};

        DO $complaint_catalog_v2_optional_acl$
        DECLARE role_name text;
        BEGIN
          FOREACH role_name IN ARRAY ARRAY[
            'tit_growth_app','tit_teacher_crud','tit_dts_ingest_runtime',
            'tit_dts_outbox_worker_runtime',
            'tit_dts_scope_coordinator_runtime','tide_business_app'
          ] LOOP
            IF to_regrole(role_name) IS NOT NULL THEN
              EXECUTE format(
                'REVOKE ALL ON FUNCTION '
                'public.publish_complaint_rule_import_v2(text,bigint,text),'
                'public.enqueue_dirty_from_catalog_revision_v2('
                'text,text,bigint,text,text),'
                'public.enqueue_complaint_category_course_fanout_v2('
                'text,bigint,text,text) FROM %I',role_name
              );
            END IF;
          END LOOP;
        END
        $complaint_catalog_v2_optional_acl$;

        COMMENT ON FUNCTION
          public.publish_complaint_rule_import_v2(text,bigint,text) IS
          'Catalog-CAS publication for one immutable complaint rule import.';
        COMMENT ON FUNCTION
          public.enqueue_complaint_category_course_fanout_v2(
            text,bigint,text,text
          ) IS
          'Indexed DOM complaint-category reverse fanout across DOM and OVS courses.';
        """
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("complaint catalog v2 requires PostgreSQL")
    _preflight_and_role()
    _expand_import_and_rule_contract()
    _install_immutability_guards()
    _create_publication_audit()
    _install_catalog_dirty_wrapper()
    _install_category_reverse_fanout()
    _install_publication_command()
    _install_health()
    _apply_acl()


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("complaint catalog v2 requires PostgreSQL")
    op.execute(
        r"""
        DO $complaint_catalog_v2_downgrade_guard$
        BEGIN
          IF EXISTS(
            SELECT 1 FROM public.complaint_rule_publication_audits
          ) OR (SELECT count(*) FROM public.complaint_rule_imports)>1 THEN
            RAISE EXCEPTION 'COMPLAINT_RULE_CATALOG_DOWNGRADE_UNSAFE';
          END IF;
        END
        $complaint_catalog_v2_downgrade_guard$;
        """
    )
    op.execute(
        r"""
        REVOKE ALL ON FUNCTION
          public.publish_complaint_rule_import_v2(text,bigint,text),
          public.enqueue_dirty_from_catalog_revision_v2(
            text,text,bigint,text,text
          ),
          public.enqueue_complaint_category_course_fanout_v2(
            text,bigint,text,text
          ),
          public.dts_v2_complaint_catalog_health_v1()
        FROM PUBLIC;
        DROP FUNCTION public.publish_complaint_rule_import_v2(
          text,bigint,text
        );
        DROP FUNCTION public.enqueue_complaint_category_course_fanout_v2(
          text,bigint,text,text
        );
        DROP FUNCTION public.enqueue_dirty_from_catalog_revision_v2(
          text,text,bigint,text,text
        );
        DROP FUNCTION public.dts_v2_complaint_catalog_health_v1();
        DROP TRIGGER trg_guard_complaint_rule_publication_audit_v2
          ON public.complaint_rule_publication_audits;
        DROP FUNCTION public.guard_complaint_rule_publication_audit_v2();
        """
    )
    op.drop_table("complaint_rule_publication_audits", schema="public")
    op.execute(
        r"""
        DROP TRIGGER trg_aa_freeze_trigger_match_complaint_rule_v2
          ON public.personalized_trigger_matches;
        DROP TRIGGER trg_guard_complaint_category_rule_v2
          ON public.complaint_category_rules;
        DROP TRIGGER trg_guard_complaint_rule_import_v2
          ON public.complaint_rule_imports;
        DROP FUNCTION public.freeze_trigger_match_complaint_rule_v2();
        DROP FUNCTION public.guard_complaint_category_rule_v2();
        DROP FUNCTION public.guard_complaint_rule_import_v2();
        """
    )
    op.drop_constraint(
        "fk_trigger_match_complaint_rule_version_v2",
        "personalized_trigger_matches",
        type_="foreignkey",
        schema="public",
    )
    op.drop_constraint(
        "ck_trigger_match_complaint_rule_version_v2",
        "personalized_trigger_matches",
        type_="check",
        schema="public",
    )
    op.drop_index(
        "ix_trigger_match_complaint_rule_version_v2",
        table_name="personalized_trigger_matches",
        schema="public",
    )
    op.drop_constraint(
        "fk_source_course_complaint_rule_version",
        "source_course_complaints",
        type_="foreignkey",
        schema="public",
    )
    op.drop_constraint(
        "ck_complaint_rule_deterministic_identity_v2",
        "complaint_category_rules",
        type_="check",
        schema="public",
    )
    op.drop_constraint(
        "uq_complaint_rule_source_row_v2",
        "complaint_category_rules",
        type_="unique",
        schema="public",
    )
    op.execute(
        r"""
        CREATE TEMP TABLE complaint_rule_id_rekey_down_v94(
          new_rule_id text PRIMARY KEY,
          old_rule_id text NOT NULL UNIQUE,
          source_sha256 text NOT NULL
        ) ON COMMIT DROP;
        INSERT INTO complaint_rule_id_rekey_down_v94(
          new_rule_id,old_rule_id,source_sha256
        )
        SELECT rule_id,
          'CR-COMPLAINT-' || left(source_sha256,24) || '-' ||
            source_row_number::text,
          source_sha256
        FROM public.complaint_category_rules;
        UPDATE public.complaint_category_rules rule
        SET rule_id=mapping.old_rule_id
        FROM complaint_rule_id_rekey_down_v94 mapping
        WHERE rule.rule_id=mapping.new_rule_id;
        UPDATE public.source_course_complaints complaint
        SET complaint_rule_id=mapping.old_rule_id
        FROM complaint_rule_id_rekey_down_v94 mapping
        WHERE complaint.complaint_rule_id=mapping.new_rule_id;
        UPDATE public.personalized_trigger_matches match
        SET complaint_rule_id=mapping.old_rule_id
        FROM complaint_rule_id_rekey_down_v94 mapping
        WHERE match.complaint_rule_id=mapping.new_rule_id;
        """
    )
    op.create_foreign_key(
        "fk_source_course_complaint_rule_version",
        "source_course_complaints",
        "complaint_category_rules",
        ["complaint_rule_id", "source_sha256"],
        ["rule_id", "source_sha256"],
        source_schema="public",
        referent_schema="public",
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_foreign_key(
        "fk_personalized_trigger_match_complaint_rule",
        "personalized_trigger_matches",
        "complaint_category_rules",
        ["complaint_rule_id"],
        ["rule_id"],
        source_schema="public",
        referent_schema="public",
        ondelete="RESTRICT",
    )
    op.drop_column(
        "personalized_trigger_matches",
        "complaint_rule_source_sha256",
        schema="public",
    )
    op.drop_index(
        "ix_source_course_complaints_rule_category_reverse_v2",
        table_name="source_course_complaints",
        schema="public",
    )
    op.drop_index(
        "uq_complaint_rule_activation_generation_v2",
        table_name="complaint_rule_imports",
        schema="public",
    )
    op.drop_index(
        "uq_complaint_rule_import_published_v2",
        table_name="complaint_rule_imports",
        schema="public",
    )
    op.drop_constraint(
        "ck_complaint_rule_import_lifecycle_v2",
        "complaint_rule_imports",
        type_="check",
        schema="public",
    )
    op.execute(
        r"""
        DROP FUNCTION public.complaint_rule_catalog_content_hash_v1(text);
        DROP FUNCTION public.complaint_rule_catalog_content_v1(text);
        """
    )
    for column_name in (
        "retired_at","published_at","content_hash","row_count",
        "activation_generation","publication_revision","status",
    ):
        op.drop_column(
            "complaint_rule_imports", column_name, schema="public"
        )
