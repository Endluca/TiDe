"""remove superseded stores and redundant projection columns

Revision ID: 20260807_48_schema_cleanup
Revises: 20260807_47_legacy_drop
Create Date: 2026-08-07

The migration keeps complaint-workbook evidence lossless, but specializes its
storage to one immutable import row.  Empty generic runtime stores are removed
only after a locked, fail-closed emptiness/dependency check.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260807_48_schema_cleanup"
down_revision: str | None = "20260807_47_legacy_drop"
branch_labels: str | None = None
depends_on: str | None = None


JSON_VALUE = sa.JSON().with_variant(
    postgresql.JSONB(astext_type=sa.Text()),
    "postgresql",
)

COMPLAINT_SHEET = "客服&销售&学员端投诉分级"
EMPTY_RETIRED_TABLES = (
    "agent_decisions",
    "provider_calls",
    "outbound_outputs",
)


def _guard_upgrade() -> None:
    """Lock every target and refuse ambiguous or lossy cleanup."""

    op.execute(
        """
        DO $schema_cleanup_presence_guard$
        DECLARE
            missing_tables text;
        BEGIN
            SELECT string_agg(name, ', ' ORDER BY name)
            INTO missing_tables
            FROM unnest(ARRAY[
                'data_import_batches', 'source_records',
                'complaint_category_rules', 'personalized_trigger_matches',
                'teachers', 'score_accounts', 'score_component_accounts',
                'agent_decisions', 'provider_calls', 'outbound_outputs'
            ]::text[]) AS expected(name)
            WHERE to_regclass('public.' || expected.name) IS NULL;

            IF missing_tables IS NOT NULL THEN
                RAISE EXCEPTION
                    'schema cleanup expected missing public tables: %',
                    missing_tables;
            END IF;
        END
        $schema_cleanup_presence_guard$;

        LOCK TABLE
            public.data_import_batches,
            public.source_records,
            public.complaint_category_rules,
            public.personalized_trigger_matches,
            public.teachers,
            public.score_accounts,
            public.score_component_accounts,
            public.agent_decisions,
            public.provider_calls,
            public.outbound_outputs
        IN ACCESS EXCLUSIVE MODE;

        DO $schema_cleanup_locked_guard$
        DECLARE
            populated_retired text;
            dependent_views text;
            external_foreign_keys text;
            inherited_relations text;
            publication_memberships text;
            dependent_routines text;
            noninternal_triggers text;
            invalid_batch text;
            invalid_score_account text;
            invalid_component_account text;
            invalid_trigger_match text;
        BEGIN
            SELECT string_agg(name, ', ' ORDER BY name)
            INTO populated_retired
            FROM (
                SELECT 'agent_decisions' AS name
                WHERE EXISTS (SELECT 1 FROM public.agent_decisions LIMIT 1)
                UNION ALL
                SELECT 'provider_calls'
                WHERE EXISTS (SELECT 1 FROM public.provider_calls LIMIT 1)
                UNION ALL
                SELECT 'outbound_outputs'
                WHERE EXISTS (SELECT 1 FROM public.outbound_outputs LIMIT 1)
            ) AS populated;
            IF populated_retired IS NOT NULL THEN
                RAISE EXCEPTION
                    'refusing to drop populated retired stores: %',
                    populated_retired;
            END IF;

            SELECT batch.batch_id
            INTO invalid_batch
            FROM public.data_import_batches AS batch
            WHERE batch.source_kind IS DISTINCT FROM 'COMPLAINT_RULES'
               OR batch.source_sheet IS DISTINCT FROM
                    '客服&销售&学员端投诉分级'
               OR batch.row_count <> 43
               OR (
                    SELECT count(*)
                    FROM public.source_records AS source
                    WHERE source.batch_id = batch.batch_id
               ) <> batch.row_count
               OR EXISTS (
                    SELECT 1
                    FROM public.source_records AS source
                    WHERE source.batch_id = batch.batch_id
                      AND (
                            source.source_sheet IS DISTINCT FROM
                                '客服&销售&学员端投诉分级'
                         OR jsonb_typeof(source.raw_payload) <> 'object'
                      )
               )
            LIMIT 1;
            IF invalid_batch IS NOT NULL THEN
                RAISE EXCEPTION
                    'data_import_batches contains a non-complaint or incomplete batch: %',
                    invalid_batch;
            END IF;

            IF EXISTS (
                SELECT 1
                FROM public.complaint_category_rules AS rule
                LEFT JOIN public.source_records AS source
                  ON source.batch_id = rule.batch_id
                 AND source.source_row_number = rule.source_row_number
                WHERE source.source_record_id IS NULL
            ) THEN
                RAISE EXCEPTION
                    'complaint rule rows are not fully traceable to source_records';
            END IF;
            IF EXISTS (
                SELECT 1 FROM public.personalized_trigger_matches
                WHERE source_record_id IS NOT NULL
            ) THEN
                RAISE EXCEPTION
                    'personalized source_record_id is still populated';
            END IF;
            IF EXISTS (
                SELECT 1 FROM public.teachers
                WHERE source_batch_id IS NOT NULL
            ) THEN
                RAISE EXCEPTION 'teachers.source_batch_id is still populated';
            END IF;

            SELECT account.account_id
            INTO invalid_score_account
            FROM public.score_accounts AS account
            JOIN public.teachers AS teacher
              ON teacher.teacher_id = account.teacher_id
            WHERE account.account_id IS DISTINCT FROM
                    account.teacher_id || ':' || account.dimension
               OR account.camp_enrollment_id IS DISTINCT FROM
                    teacher.camp_enrollment_id
               OR account.minimum_score IS DISTINCT FROM 0::double precision
               OR account.weight IS DISTINCT FROM 0::double precision
            LIMIT 1;
            IF invalid_score_account IS NOT NULL THEN
                RAISE EXCEPTION
                    'score account contains non-redundant legacy values: %',
                    invalid_score_account;
            END IF;

            SELECT component.component_account_id
            INTO invalid_component_account
            FROM public.score_component_accounts AS component
            JOIN public.teachers AS teacher
              ON teacher.teacher_id = component.teacher_id
            WHERE component.component_account_id IS DISTINCT FROM
                    component.teacher_id || ':' || component.component_code
               OR component.camp_enrollment_id IS DISTINCT FROM
                    teacher.camp_enrollment_id
               OR component.source_teacher_batch_id IS NOT NULL
               OR component.source_lesson_batch_id IS NOT NULL
            LIMIT 1;
            IF invalid_component_account IS NOT NULL THEN
                RAISE EXCEPTION
                    'score component contains non-redundant legacy values: %',
                    invalid_component_account;
            END IF;

            SELECT match.trigger_match_id
            INTO invalid_trigger_match
            FROM public.personalized_trigger_matches AS match
            WHERE match.created_at IS DISTINCT FROM match.matched_at
               OR match.scope_key IS DISTINCT FROM CASE
                    WHEN match.trigger_code = 'TR-FB-BLACKLIST'
                    THEN 'teacher' || chr(58) || match.teacher_id ||
                         chr(58) || 'blacklist'
                    WHEN match.trigger_code = 'TR-FB-NEGATIVE-REPEAT'
                    THEN 'teacher' || chr(58) || match.teacher_id ||
                         chr(58) || 'negative-label' || chr(58) ||
                         substring(
                             match.dedupe_key FROM char_length(
                                 'TR-FB-NEGATIVE-REPEAT' || chr(58) ||
                                 match.teacher_id || chr(58)
                             ) + 1
                         )
                    WHEN match.trigger_code = 'TR-FB-NEGATIVE-TAG-MISSING'
                    THEN 'teacher' || chr(58) || match.teacher_id ||
                         chr(58) || 'negative-tag-missing'
                    WHEN match.lesson_id IS NOT NULL
                    THEN 'lesson' || chr(58) || match.lesson_id
                    ELSE NULL
                  END
            LIMIT 1;
            IF invalid_trigger_match IS NOT NULL THEN
                RAISE EXCEPTION
                    'personalized trigger match contains non-redundant legacy values: %',
                    invalid_trigger_match;
            END IF;

            SELECT string_agg(
                DISTINCT format('%I.%I', dependent_ns.nspname, dependent.relname),
                ', ' ORDER BY format('%I.%I', dependent_ns.nspname, dependent.relname)
            )
            INTO dependent_views
            FROM pg_depend AS dependency
            JOIN pg_rewrite AS rewrite ON rewrite.oid = dependency.objid
            JOIN pg_class AS dependent ON dependent.oid = rewrite.ev_class
            JOIN pg_namespace AS dependent_ns
              ON dependent_ns.oid = dependent.relnamespace
            WHERE dependency.refobjid = ANY (ARRAY[
                'public.source_records'::regclass,
                'public.agent_decisions'::regclass,
                'public.provider_calls'::regclass,
                'public.outbound_outputs'::regclass
            ]::oid[])
              AND dependent.oid <> dependency.refobjid;
            IF dependent_views IS NOT NULL THEN
                RAISE EXCEPTION
                    'refusing to drop stores with dependent views: %',
                    dependent_views;
            END IF;

            SELECT string_agg(
                format('%I.%I (%I)', ns.nspname, relation.relname, constraint_row.conname),
                ', ' ORDER BY ns.nspname, relation.relname, constraint_row.conname
            )
            INTO external_foreign_keys
            FROM pg_constraint AS constraint_row
            JOIN pg_class AS relation ON relation.oid = constraint_row.conrelid
            JOIN pg_namespace AS ns ON ns.oid = relation.relnamespace
            WHERE constraint_row.contype = 'f'
              AND constraint_row.confrelid = ANY (ARRAY[
                    'public.source_records'::regclass,
                    'public.agent_decisions'::regclass,
                    'public.provider_calls'::regclass,
                    'public.outbound_outputs'::regclass
              ]::oid[])
              AND NOT (
                    constraint_row.conname =
                        'fk_personalized_trigger_match_source_record'
                AND constraint_row.conrelid =
                        'public.personalized_trigger_matches'::regclass
              );
            IF external_foreign_keys IS NOT NULL THEN
                RAISE EXCEPTION
                    'refusing to drop stores with external foreign keys: %',
                    external_foreign_keys;
            END IF;

            SELECT string_agg(
                DISTINCT format('%I.%I', routine_ns.nspname, routine.proname),
                ', ' ORDER BY format('%I.%I', routine_ns.nspname, routine.proname)
            )
            INTO dependent_routines
            FROM pg_depend AS dependency
            JOIN pg_proc AS routine ON routine.oid = dependency.objid
            JOIN pg_namespace AS routine_ns
              ON routine_ns.oid = routine.pronamespace
            WHERE dependency.refobjid = ANY (ARRAY[
                'public.source_records'::regclass,
                'public.agent_decisions'::regclass,
                'public.provider_calls'::regclass,
                'public.outbound_outputs'::regclass
            ]::oid[]);
            IF dependent_routines IS NOT NULL THEN
                RAISE EXCEPTION
                    'refusing to drop stores with dependent routines: %',
                    dependent_routines;
            END IF;

            SELECT string_agg(
                format('%I.%I (%I)', ns.nspname, relation.relname, trigger_row.tgname),
                ', ' ORDER BY ns.nspname, relation.relname, trigger_row.tgname
            )
            INTO noninternal_triggers
            FROM pg_trigger AS trigger_row
            JOIN pg_class AS relation ON relation.oid = trigger_row.tgrelid
            JOIN pg_namespace AS ns ON ns.oid = relation.relnamespace
            WHERE NOT trigger_row.tgisinternal
              AND trigger_row.tgrelid = ANY (ARRAY[
                    'public.source_records'::regclass,
                    'public.agent_decisions'::regclass,
                    'public.provider_calls'::regclass,
                    'public.outbound_outputs'::regclass
              ]::oid[]);
            IF noninternal_triggers IS NOT NULL THEN
                RAISE EXCEPTION
                    'refusing to drop stores with non-internal triggers: %',
                    noninternal_triggers;
            END IF;

            SELECT string_agg(
                DISTINCT format('%I.%I', ns.nspname, relation.relname),
                ', ' ORDER BY format('%I.%I', ns.nspname, relation.relname)
            )
            INTO inherited_relations
            FROM pg_inherits AS inheritance
            JOIN pg_class AS relation
              ON relation.oid = CASE
                    WHEN inheritance.inhparent = ANY (ARRAY[
                        'public.source_records'::regclass,
                        'public.agent_decisions'::regclass,
                        'public.provider_calls'::regclass,
                        'public.outbound_outputs'::regclass
                    ]::oid[])
                    THEN inheritance.inhrelid ELSE inheritance.inhparent
                 END
            JOIN pg_namespace AS ns ON ns.oid = relation.relnamespace
            WHERE inheritance.inhparent = ANY (ARRAY[
                    'public.source_records'::regclass,
                    'public.agent_decisions'::regclass,
                    'public.provider_calls'::regclass,
                    'public.outbound_outputs'::regclass
                  ]::oid[])
               OR inheritance.inhrelid = ANY (ARRAY[
                    'public.source_records'::regclass,
                    'public.agent_decisions'::regclass,
                    'public.provider_calls'::regclass,
                    'public.outbound_outputs'::regclass
                  ]::oid[]);
            IF inherited_relations IS NOT NULL THEN
                RAISE EXCEPTION
                    'refusing to drop inherited or partitioned stores: %',
                    inherited_relations;
            END IF;

            SELECT string_agg(DISTINCT publication.pubname, ', ' ORDER BY publication.pubname)
            INTO publication_memberships
            FROM pg_publication_rel AS membership
            JOIN pg_publication AS publication ON publication.oid = membership.prpubid
            WHERE membership.prrelid = ANY (ARRAY[
                'public.source_records'::regclass,
                'public.agent_decisions'::regclass,
                'public.provider_calls'::regclass,
                'public.outbound_outputs'::regclass
            ]::oid[]);
            IF publication_memberships IS NOT NULL THEN
                RAISE EXCEPTION
                    'refusing to drop stores still published by: %',
                    publication_memberships;
            END IF;
        END
        $schema_cleanup_locked_guard$;
        """
    )


def _compact_complaint_imports() -> None:
    op.add_column(
        "data_import_batches",
        sa.Column("raw_rows", JSON_VALUE, nullable=True),
        schema="public",
    )
    op.execute(
        """
        UPDATE public.data_import_batches AS batch
        SET raw_rows = (
            SELECT jsonb_agg(
                source.raw_payload || jsonb_build_object(
                    'source_row_number', source.source_row_number
                )
                ORDER BY source.source_row_number
            )
            FROM public.source_records AS source
            WHERE source.batch_id = batch.batch_id
        )
        """
    )
    op.alter_column(
        "data_import_batches",
        "raw_rows",
        nullable=False,
        existing_type=JSON_VALUE,
        schema="public",
    )

    op.add_column(
        "complaint_category_rules",
        sa.Column("source_sha256", sa.String(length=64), nullable=True),
        schema="public",
    )
    op.execute(
        """
        UPDATE public.complaint_category_rules AS rule
        SET source_sha256 = batch.source_sha256
        FROM public.data_import_batches AS batch
        WHERE batch.batch_id = rule.batch_id
        """
    )

    op.drop_constraint(
        "fk_personalized_trigger_match_source_record",
        "personalized_trigger_matches",
        type_="foreignkey",
        schema="public",
    )
    op.drop_index(
        "ix_personalized_trigger_matches_source_record_id",
        table_name="personalized_trigger_matches",
        schema="public",
    )
    op.drop_index(
        "ix_personalized_trigger_matches_teacher_id",
        table_name="personalized_trigger_matches",
        schema="public",
    )
    for column_name in ("source_record_id", "scope_key", "created_at"):
        op.drop_column(
            "personalized_trigger_matches",
            column_name,
            schema="public",
        )

    op.drop_constraint(
        "fk_teachers_source_batch_id",
        "teachers",
        type_="foreignkey",
        schema="public",
    )
    op.drop_index(
        "ix_teachers_source_batch_id",
        table_name="teachers",
        schema="public",
    )
    op.drop_column("teachers", "source_batch_id", schema="public")

    op.drop_constraint(
        "fk_complaint_rule_batch",
        "complaint_category_rules",
        type_="foreignkey",
        schema="public",
    )
    op.drop_constraint(
        "uq_complaint_rule_batch_l3",
        "complaint_category_rules",
        type_="unique",
        schema="public",
    )
    op.drop_index(
        "ix_complaint_category_rules_batch_id",
        table_name="complaint_category_rules",
        schema="public",
    )
    op.drop_index(
        "ix_complaint_rule_l3_current",
        table_name="complaint_category_rules",
        schema="public",
    )
    op.drop_constraint(
        "ck_complaint_rule_level",
        "complaint_category_rules",
        type_="check",
        schema="public",
    )

    # All source rows have now been folded into raw_rows and no live column
    # points at them.  Do not use CASCADE: the guard owns the dependency set.
    op.drop_table("source_records", schema="public")

    op.drop_constraint(
        "uq_data_import_content_sheet",
        "data_import_batches",
        type_="unique",
        schema="public",
    )
    for constraint_name in (
        "ck_data_import_batch_sync_mode",
        "ck_data_import_batch_data_mode",
        "ck_data_import_batch_status",
    ):
        op.drop_constraint(
            constraint_name,
            "data_import_batches",
            type_="check",
            schema="public",
        )
    for index_name in (
        "ix_data_import_batches_snapshot_label",
        "ix_data_import_batches_status",
        "ix_data_import_source_time",
    ):
        op.drop_index(
            index_name,
            table_name="data_import_batches",
            schema="public",
        )
    op.drop_constraint(
        "data_import_batches_pkey",
        "data_import_batches",
        type_="primary",
        schema="public",
    )
    for column_name in (
        "batch_id",
        "source_kind",
        "sync_mode",
        "source_system",
        "source_uri",
        "source_sheet",
        "snapshot_label",
        "data_mode",
        "column_count",
        "row_count",
        "header",
        "status",
        "payload",
        "created_at",
        "updated_at",
    ):
        op.drop_column("data_import_batches", column_name, schema="public")
    op.create_primary_key(
        "complaint_rule_imports_pkey",
        "data_import_batches",
        ["source_sha256"],
        schema="public",
    )
    op.rename_table(
        "data_import_batches",
        "complaint_rule_imports",
        schema="public",
    )

    op.alter_column(
        "complaint_category_rules",
        "source_sha256",
        existing_type=sa.String(length=64),
        nullable=False,
        schema="public",
    )
    for column_name in (
        "batch_id",
        "source_sheet",
        "normalized_level",
        "raw_payload",
    ):
        op.drop_column(
            "complaint_category_rules",
            column_name,
            schema="public",
        )
    op.create_foreign_key(
        "fk_complaint_rule_import",
        "complaint_category_rules",
        "complaint_rule_imports",
        ["source_sha256"],
        ["source_sha256"],
        source_schema="public",
        referent_schema="public",
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_complaint_rule_source_l3",
        "complaint_category_rules",
        ["source_sha256", "category_l3_normalized"],
        schema="public",
    )
    op.create_index(
        "ix_complaint_rule_l3_current",
        "complaint_category_rules",
        ["category_l3_normalized", "source_sha256"],
        schema="public",
    )


def _compact_score_accounts() -> None:
    op.drop_constraint(
        "score_accounts_pkey",
        "score_accounts",
        type_="primary",
        schema="public",
    )
    op.drop_constraint(
        "uq_score_account_teacher_dimension",
        "score_accounts",
        type_="unique",
        schema="public",
    )
    for index_name in (
        "ix_score_accounts_teacher_id",
        "ix_score_accounts_camp_enrollment_id",
    ):
        op.drop_index(
            index_name,
            table_name="score_accounts",
            schema="public",
        )
    for column_name in (
        "account_id",
        "camp_enrollment_id",
        "minimum_score",
        "weight",
    ):
        op.drop_column("score_accounts", column_name, schema="public")
    op.create_primary_key(
        "score_accounts_pkey",
        "score_accounts",
        ["teacher_id", "dimension"],
        schema="public",
    )

    op.drop_constraint(
        "score_component_accounts_pkey",
        "score_component_accounts",
        type_="primary",
        schema="public",
    )
    op.drop_constraint(
        "uq_score_component_account_teacher_component",
        "score_component_accounts",
        type_="unique",
        schema="public",
    )
    for index_name in (
        "ix_score_component_account_teacher_id",
        "ix_score_component_account_camp_enrollment_id",
        "ix_score_component_account_source_teacher_batch_id",
        "ix_score_component_account_source_lesson_batch_id",
        "ix_score_component_account_calculated_at",
    ):
        op.drop_index(
            index_name,
            table_name="score_component_accounts",
            schema="public",
        )
    for column_name in (
        "component_account_id",
        "camp_enrollment_id",
        "source_teacher_batch_id",
        "source_lesson_batch_id",
    ):
        op.drop_column(
            "score_component_accounts",
            column_name,
            schema="public",
        )
    op.create_primary_key(
        "score_component_accounts_pkey",
        "score_component_accounts",
        ["teacher_id", "component_code"],
        schema="public",
    )


def _drop_empty_stores() -> None:
    for table_name in EMPTY_RETIRED_TABLES:
        op.drop_table(table_name, schema="public")


def _tighten_import_acl() -> None:
    op.execute(
        """
        REVOKE ALL PRIVILEGES ON TABLE public.complaint_rule_imports
        FROM PUBLIC, tit_growth_app;

        DO $complaint_import_optional_acl$
        DECLARE
            role_name text;
        BEGIN
            FOREACH role_name IN ARRAY ARRAY[
                'tit_teacher_crud', 'tide_business_app'
            ]::text[] LOOP
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
                    EXECUTE format(
                        'REVOKE ALL PRIVILEGES ON TABLE '
                        'public.complaint_rule_imports FROM %I',
                        role_name
                    );
                END IF;
            END LOOP;
        END
        $complaint_import_optional_acl$;

        DO $complaint_import_acl_assertion$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM unnest(ARRAY[
                    'SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE',
                    'REFERENCES', 'TRIGGER'
                ]::text[]) AS privilege(name)
                WHERE has_table_privilege(
                    'tit_growth_app',
                    'public.complaint_rule_imports',
                    privilege.name
                )
            ) THEN
                RAISE EXCEPTION
                    'tit_growth_app must not access complaint_rule_imports';
            END IF;
        END
        $complaint_import_acl_assertion$;
        """
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    _guard_upgrade()
    _compact_complaint_imports()
    _compact_score_accounts()
    _drop_empty_stores()
    _tighten_import_acl()


def _guard_downgrade() -> None:
    """Refuse to invent retired provenance once new imports exist."""

    op.execute(
        """
        DO $schema_cleanup_downgrade_guard$
        BEGIN
            IF EXISTS (SELECT 1 FROM public.complaint_rule_imports LIMIT 1) THEN
                RAISE EXCEPTION
                    'revision 48 downgrade is structural-only after complaint imports; export or remove reviewed imports before downgrade';
            END IF;
        END
        $schema_cleanup_downgrade_guard$;
        """
    )


def _restore_complaint_evidence() -> None:
    op.drop_constraint(
        "fk_complaint_rule_import",
        "complaint_category_rules",
        type_="foreignkey",
        schema="public",
    )
    op.drop_constraint(
        "uq_complaint_rule_source_l3",
        "complaint_category_rules",
        type_="unique",
        schema="public",
    )
    op.drop_index(
        "ix_complaint_rule_l3_current",
        table_name="complaint_category_rules",
        schema="public",
    )
    for column in (
        sa.Column("batch_id", sa.String(length=96), nullable=True),
        sa.Column("source_sheet", sa.String(length=128), nullable=True),
        sa.Column("normalized_level", sa.String(length=2), nullable=True),
        sa.Column("raw_payload", JSON_VALUE, nullable=True),
    ):
        op.add_column("complaint_category_rules", column, schema="public")
    op.execute(
        f"""
        UPDATE public.complaint_category_rules AS rule
        SET
            batch_id = 'COMPLAINT-' || left(rule.source_sha256, 24),
            source_sheet = '{COMPLAINT_SHEET}',
            normalized_level = 'L' || rule.severity_rank::text,
            raw_payload = jsonb_build_object(
                'source_row', source.raw_row - 'source_row_number',
                'effective_category_l1', rule.category_l1,
                'effective_category_l2', rule.category_l2
            )
        FROM public.complaint_rule_imports AS imported
        CROSS JOIN LATERAL jsonb_array_elements(imported.raw_rows)
            AS source(raw_row)
        WHERE imported.source_sha256 = rule.source_sha256
          AND (source.raw_row ->> 'source_row_number')::integer =
                rule.source_row_number
        """
    )
    for column_name, existing_type in (
        ("batch_id", sa.String(length=96)),
        ("source_sheet", sa.String(length=128)),
        ("normalized_level", sa.String(length=2)),
        ("raw_payload", JSON_VALUE),
    ):
        op.alter_column(
            "complaint_category_rules",
            column_name,
            existing_type=existing_type,
            nullable=False,
            schema="public",
        )

    old_import_columns = (
        sa.Column("batch_id", sa.String(length=96), nullable=True),
        sa.Column("source_kind", sa.String(length=32), nullable=True),
        sa.Column("sync_mode", sa.String(length=24), nullable=True),
        sa.Column("source_system", sa.String(length=128), nullable=True),
        sa.Column("source_uri", sa.Text(), nullable=True),
        sa.Column("source_sheet", sa.String(length=128), nullable=True),
        sa.Column("snapshot_label", sa.String(length=128), nullable=True),
        sa.Column("data_mode", sa.String(length=16), nullable=True),
        sa.Column("column_count", sa.Integer(), nullable=True),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.Column("header", JSON_VALUE, nullable=True),
        sa.Column("status", sa.String(length=24), nullable=True),
        sa.Column("payload", JSON_VALUE, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    for column in old_import_columns:
        op.add_column("complaint_rule_imports", column, schema="public")
    op.execute(
        f"""
        UPDATE public.complaint_rule_imports
        SET
            batch_id = 'COMPLAINT-' || left(source_sha256, 24),
            source_kind = 'COMPLAINT_RULES',
            sync_mode = 'MANUAL_BASELINE',
            source_system = 'MANUAL_XLSX' || chr(58) ||
                'COMPLAINT_LEVEL_RULES',
            source_uri = source_filename,
            source_sheet = '{COMPLAINT_SHEET}',
            snapshot_label = 'COMPLAINT_RULES:' || left(source_sha256, 16),
            data_mode = 'REAL',
            column_count = 6,
            row_count = jsonb_array_length(raw_rows),
            header = jsonb_build_array(
                '一级分类', '二级分类', '三级分类', 'P级',
                'Course Title in the Learning Hub', 'link'
            ),
            status = 'COMPLETED',
            payload = jsonb_build_object(
                'source_region', 'A1:F45',
                'mapping_key', '三级分类精确匹配'
            ),
            created_at = imported_at,
            updated_at = imported_at
        """
    )
    for column in old_import_columns:
        op.alter_column(
            "complaint_rule_imports",
            column.name,
            existing_type=column.type,
            nullable=False,
            schema="public",
        )
    op.drop_constraint(
        "complaint_rule_imports_pkey",
        "complaint_rule_imports",
        type_="primary",
        schema="public",
    )
    op.create_primary_key(
        "data_import_batches_pkey",
        "complaint_rule_imports",
        ["batch_id"],
        schema="public",
    )
    op.rename_table(
        "complaint_rule_imports",
        "data_import_batches",
        schema="public",
    )
    op.create_unique_constraint(
        "uq_data_import_content_sheet",
        "data_import_batches",
        ["source_sha256", "source_sheet"],
        schema="public",
    )
    op.create_check_constraint(
        "ck_data_import_batch_sync_mode",
        "data_import_batches",
        "sync_mode IN ('MANUAL_BASELINE', 'API_DAILY')",
        schema="public",
    )
    op.create_check_constraint(
        "ck_data_import_batch_data_mode",
        "data_import_batches",
        "data_mode IN ('REAL', 'MIXED')",
        schema="public",
    )
    op.create_check_constraint(
        "ck_data_import_batch_status",
        "data_import_batches",
        "status IN ('VALIDATED', 'COMPLETED', 'FAILED')",
        schema="public",
    )
    op.create_index(
        "ix_data_import_batches_snapshot_label",
        "data_import_batches",
        ["snapshot_label"],
        schema="public",
    )
    op.create_index(
        "ix_data_import_batches_status",
        "data_import_batches",
        ["status"],
        schema="public",
    )
    op.create_index(
        "ix_data_import_source_time",
        "data_import_batches",
        ["source_system", "imported_at"],
        schema="public",
    )

    op.drop_column(
        "complaint_category_rules",
        "source_sha256",
        schema="public",
    )
    op.create_foreign_key(
        "fk_complaint_rule_batch",
        "complaint_category_rules",
        "data_import_batches",
        ["batch_id"],
        ["batch_id"],
        source_schema="public",
        referent_schema="public",
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_complaint_rule_batch_l3",
        "complaint_category_rules",
        ["batch_id", "category_l3_normalized"],
        schema="public",
    )
    op.create_check_constraint(
        "ck_complaint_rule_level",
        "complaint_category_rules",
        "normalized_level IN ('L0', 'L1', 'L2', 'L3', 'L4')",
        schema="public",
    )
    op.create_index(
        "ix_complaint_category_rules_batch_id",
        "complaint_category_rules",
        ["batch_id"],
        schema="public",
    )
    op.create_index(
        "ix_complaint_rule_l3_current",
        "complaint_category_rules",
        ["category_l3_normalized", "batch_id"],
        schema="public",
    )

    _restore_source_records()
    op.drop_column("data_import_batches", "raw_rows", schema="public")


def _restore_source_records() -> None:
    op.create_table(
        "source_records",
        sa.Column("source_record_id", sa.String(length=160), nullable=False),
        sa.Column("batch_id", sa.String(length=96), nullable=False),
        sa.Column("source_sheet", sa.String(length=128), nullable=False),
        sa.Column("source_row_number", sa.Integer(), nullable=False),
        sa.Column("business_key", sa.String(length=256), nullable=False),
        sa.Column("teacher_id", sa.String(length=64), nullable=True),
        sa.Column("lesson_id", sa.String(length=128), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("row_sha256", sa.String(length=64), nullable=False),
        sa.Column("raw_payload", JSON_VALUE, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["public.data_import_batches.batch_id"],
            name="fk_source_record_batch",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("source_record_id", name="source_records_pkey"),
        sa.UniqueConstraint(
            "batch_id",
            "source_sheet",
            "source_row_number",
            name="uq_source_record_batch_sheet_row",
        ),
        schema="public",
    )
    op.execute(
        f"""
        INSERT INTO public.source_records (
            source_record_id, batch_id, source_sheet, source_row_number,
            business_key, teacher_id, lesson_id, occurred_at, row_sha256,
            raw_payload, created_at
        )
        SELECT
            'SRC-' || batch.batch_id || '-' ||
                (source.raw_row ->> 'source_row_number'),
            batch.batch_id,
            '{COMPLAINT_SHEET}',
            (source.raw_row ->> 'source_row_number')::integer,
            'complaint-source-row:' ||
                (source.raw_row ->> 'source_row_number'),
            NULL,
            NULL,
            NULL,
            repeat(md5((source.raw_row - 'source_row_number')::text), 2),
            source.raw_row - 'source_row_number',
            batch.imported_at
        FROM public.data_import_batches AS batch
        CROSS JOIN LATERAL jsonb_array_elements(batch.raw_rows)
            AS source(raw_row)
        """
    )
    for index_name, columns in (
        ("ix_source_records_batch_id", ["batch_id"]),
        ("ix_source_records_teacher_id", ["teacher_id"]),
        ("ix_source_records_lesson_id", ["lesson_id"]),
        ("ix_source_records_occurred_at", ["occurred_at"]),
        ("ix_source_records_row_sha256", ["row_sha256"]),
        ("ix_source_record_business_key", ["batch_id", "business_key"]),
        ("ix_source_record_teacher_time", ["teacher_id", "occurred_at"]),
    ):
        op.create_index(
            index_name,
            "source_records",
            columns,
            schema="public",
        )


def _restore_projection_columns() -> None:
    for column in (
        sa.Column("source_record_id", sa.String(length=160), nullable=True),
        sa.Column("scope_key", sa.String(length=256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    ):
        op.add_column("personalized_trigger_matches", column, schema="public")
    op.execute(
        """
        UPDATE public.personalized_trigger_matches
        SET
            scope_key = CASE
            WHEN trigger_code = 'TR-FB-BLACKLIST'
                THEN 'teacher' || chr(58) || teacher_id ||
                     chr(58) || 'blacklist'
            WHEN trigger_code = 'TR-FB-NEGATIVE-REPEAT'
                THEN 'teacher' || chr(58) || teacher_id ||
                     chr(58) || 'negative-label' || chr(58) ||
                     substring(
                         dedupe_key FROM char_length(
                             'TR-FB-NEGATIVE-REPEAT' || chr(58) ||
                             teacher_id || chr(58)
                         ) + 1
                     )
            WHEN trigger_code = 'TR-FB-NEGATIVE-TAG-MISSING'
                THEN 'teacher' || chr(58) || teacher_id ||
                     chr(58) || 'negative-tag-missing'
            WHEN lesson_id IS NOT NULL
                THEN 'lesson' || chr(58) || lesson_id
                ELSE NULL
            END,
            created_at = matched_at
        """
    )
    op.alter_column(
        "personalized_trigger_matches",
        "scope_key",
        existing_type=sa.String(length=256),
        nullable=False,
        schema="public",
    )
    op.alter_column(
        "personalized_trigger_matches",
        "created_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
        schema="public",
    )
    op.create_foreign_key(
        "fk_personalized_trigger_match_source_record",
        "personalized_trigger_matches",
        "source_records",
        ["source_record_id"],
        ["source_record_id"],
        source_schema="public",
        referent_schema="public",
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_personalized_trigger_matches_source_record_id",
        "personalized_trigger_matches",
        ["source_record_id"],
        schema="public",
    )
    op.create_index(
        "ix_personalized_trigger_matches_teacher_id",
        "personalized_trigger_matches",
        ["teacher_id"],
        schema="public",
    )

    op.add_column(
        "teachers",
        sa.Column("source_batch_id", sa.String(length=96), nullable=True),
        schema="public",
    )
    op.create_foreign_key(
        "fk_teachers_source_batch_id",
        "teachers",
        "data_import_batches",
        ["source_batch_id"],
        ["batch_id"],
        source_schema="public",
        referent_schema="public",
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_teachers_source_batch_id",
        "teachers",
        ["source_batch_id"],
        schema="public",
    )


def _restore_score_accounts() -> None:
    for column in (
        sa.Column("account_id", sa.String(length=160), nullable=True),
        sa.Column("camp_enrollment_id", sa.String(length=96), nullable=True),
        sa.Column("minimum_score", sa.Float(), nullable=True),
        sa.Column("weight", sa.Float(), nullable=True),
    ):
        op.add_column("score_accounts", column, schema="public")
    op.execute(
        """
        UPDATE public.score_accounts AS account
        SET
            account_id = account.teacher_id || ':' || account.dimension,
            camp_enrollment_id = teacher.camp_enrollment_id,
            minimum_score = 0,
            weight = 0
        FROM public.teachers AS teacher
        WHERE teacher.teacher_id = account.teacher_id
        """
    )
    for column_name, existing_type in (
        ("account_id", sa.String(length=160)),
        ("camp_enrollment_id", sa.String(length=96)),
        ("minimum_score", sa.Float()),
        ("weight", sa.Float()),
    ):
        op.alter_column(
            "score_accounts",
            column_name,
            existing_type=existing_type,
            nullable=False,
            schema="public",
        )
    op.drop_constraint(
        "score_accounts_pkey",
        "score_accounts",
        type_="primary",
        schema="public",
    )
    op.create_primary_key(
        "score_accounts_pkey",
        "score_accounts",
        ["account_id"],
        schema="public",
    )
    op.create_unique_constraint(
        "uq_score_account_teacher_dimension",
        "score_accounts",
        ["teacher_id", "dimension"],
        schema="public",
    )
    op.create_index(
        "ix_score_accounts_teacher_id",
        "score_accounts",
        ["teacher_id"],
        schema="public",
    )
    op.create_index(
        "ix_score_accounts_camp_enrollment_id",
        "score_accounts",
        ["camp_enrollment_id"],
        schema="public",
    )

    for column in (
        sa.Column("component_account_id", sa.String(length=192), nullable=True),
        sa.Column("camp_enrollment_id", sa.String(length=96), nullable=True),
        sa.Column("source_teacher_batch_id", sa.String(length=160), nullable=True),
        sa.Column("source_lesson_batch_id", sa.String(length=160), nullable=True),
    ):
        op.add_column("score_component_accounts", column, schema="public")
    op.execute(
        """
        UPDATE public.score_component_accounts AS component
        SET
            component_account_id =
                component.teacher_id || ':' || component.component_code,
            camp_enrollment_id = teacher.camp_enrollment_id
        FROM public.teachers AS teacher
        WHERE teacher.teacher_id = component.teacher_id
        """
    )
    for column_name, existing_type in (
        ("component_account_id", sa.String(length=192)),
        ("camp_enrollment_id", sa.String(length=96)),
    ):
        op.alter_column(
            "score_component_accounts",
            column_name,
            existing_type=existing_type,
            nullable=False,
            schema="public",
        )
    op.drop_constraint(
        "score_component_accounts_pkey",
        "score_component_accounts",
        type_="primary",
        schema="public",
    )
    op.create_primary_key(
        "score_component_accounts_pkey",
        "score_component_accounts",
        ["component_account_id"],
        schema="public",
    )
    op.create_unique_constraint(
        "uq_score_component_account_teacher_component",
        "score_component_accounts",
        ["teacher_id", "component_code"],
        schema="public",
    )
    for index_name, columns in (
        ("ix_score_component_account_teacher_id", ["teacher_id"]),
        ("ix_score_component_account_camp_enrollment_id", ["camp_enrollment_id"]),
        ("ix_score_component_account_source_teacher_batch_id", ["source_teacher_batch_id"]),
        ("ix_score_component_account_source_lesson_batch_id", ["source_lesson_batch_id"]),
        ("ix_score_component_account_calculated_at", ["calculated_at"]),
    ):
        op.create_index(
            index_name,
            "score_component_accounts",
            columns,
            schema="public",
        )


def _restore_empty_stores() -> None:
    op.create_table(
        "agent_decisions",
        sa.Column("plan_id", sa.String(length=128), nullable=False),
        sa.Column("plan_key", sa.String(length=512), nullable=False),
        sa.Column("route", sa.String(length=24), nullable=False),
        sa.Column("planner", sa.String(length=64), nullable=False),
        sa.Column("teacher_id", sa.String(length=64), nullable=False),
        sa.Column("constraints", JSON_VALUE, nullable=False),
        sa.Column("selected_template_ids", JSON_VALUE, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", JSON_VALUE, nullable=False),
        sa.PrimaryKeyConstraint("plan_id", name="agent_decisions_pkey"),
        sa.UniqueConstraint("plan_key", name="agent_decisions_plan_key_key"),
        schema="public",
    )
    op.create_index(
        "ix_agent_decisions_teacher_id",
        "agent_decisions",
        ["teacher_id"],
        schema="public",
    )
    op.create_table(
        "provider_calls",
        sa.Column("provider_call_id", sa.String(length=128), nullable=False),
        sa.Column("provider_event_id", sa.String(length=128), nullable=True),
        sa.Column("task_id", sa.String(length=128), nullable=False),
        sa.Column("provider_id", sa.String(length=128), nullable=False),
        sa.Column("call_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("request_payload", JSON_VALUE, nullable=False),
        sa.Column("result_payload", JSON_VALUE, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("provider_call_id", name="provider_calls_pkey"),
        sa.UniqueConstraint(
            "provider_event_id",
            name="provider_calls_provider_event_id_key",
        ),
        schema="public",
    )
    op.create_index(
        "ix_provider_calls_task_id",
        "provider_calls",
        ["task_id"],
        schema="public",
    )
    op.create_table(
        "outbound_outputs",
        sa.Column("output_id", sa.String(length=128), nullable=False),
        sa.Column("output_type", sa.String(length=32), nullable=False),
        sa.Column("display_type", sa.String(length=40), nullable=False),
        sa.Column("delivery_kind", sa.String(length=40), nullable=True),
        sa.Column("audience_type", sa.String(length=32), nullable=False),
        sa.Column("recipient_id", sa.String(length=128), nullable=True),
        sa.Column("recipient_name", sa.String(length=255), nullable=True),
        sa.Column("channel", sa.String(length=40), nullable=True),
        sa.Column("source_type", sa.String(length=48), nullable=False),
        sa.Column("source_id", sa.String(length=128), nullable=False),
        sa.Column("teacher_id", sa.String(length=64), nullable=True),
        sa.Column("task_id", sa.String(length=128), nullable=True),
        sa.Column("case_id", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=False),
        sa.Column("requires_human_approval", sa.Boolean(), nullable=False),
        sa.Column("payload", JSON_VALUE, nullable=False),
        sa.Column("idempotency_key", sa.String(length=256), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("output_id", name="outbound_outputs_pkey"),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_outbound_output_idempotency",
        ),
        schema="public",
    )
    for index_name, columns in (
        ("ix_outbound_outputs_case_id", ["case_id"]),
        ("ix_outbound_outputs_display_type", ["display_type"]),
        ("ix_outbound_outputs_output_type", ["output_type"]),
        ("ix_outbound_outputs_status", ["status"]),
        ("ix_outbound_outputs_task_id", ["task_id"]),
        ("ix_outbound_outputs_teacher_id", ["teacher_id"]),
        ("ix_outputs_type_status_teacher", ["output_type", "status", "teacher_id"]),
    ):
        op.create_index(
            index_name,
            "outbound_outputs",
            columns,
            schema="public",
        )


def _restore_acl() -> None:
    op.execute(
        """
        REVOKE ALL PRIVILEGES ON TABLE
            public.data_import_batches,
            public.source_records,
            public.agent_decisions,
            public.provider_calls,
            public.outbound_outputs
        FROM PUBLIC, tit_growth_app;

        GRANT SELECT, UPDATE ON TABLE public.outbound_outputs
        TO tit_growth_app;

        DO $schema_cleanup_downgrade_optional_acl$
        DECLARE
            role_name text;
        BEGIN
            FOREACH role_name IN ARRAY ARRAY[
                'tit_teacher_crud', 'tide_business_app'
            ]::text[] LOOP
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
                    EXECUTE format(
                        'REVOKE ALL PRIVILEGES ON TABLE '
                        'public.data_import_batches, public.source_records, '
                        'public.agent_decisions, public.provider_calls, '
                        'public.outbound_outputs FROM %I',
                        role_name
                    );
                END IF;
            END LOOP;
        END
        $schema_cleanup_downgrade_optional_acl$;
        """
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    _guard_downgrade()
    _restore_complaint_evidence()
    _restore_projection_columns()
    _restore_score_accounts()
    _restore_empty_stores()
    _restore_acl()
