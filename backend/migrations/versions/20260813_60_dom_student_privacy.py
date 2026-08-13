"""enforce the domestic student privacy boundary in PostgreSQL

Revision ID: 20260813_60_dom_privacy
Revises: 20260812_59_simple_acl
Create Date: 2026-08-13

The China DTS consumer may send only irreversible ``dom:v1`` subjects to the
Singapore target.  Application validation remains the first line of defence;
these database triggers are the fail-closed boundary for every SQL writer.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "20260813_60_dom_privacy"
down_revision: str = "20260812_59_simple_acl"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _install_json_validator() -> None:
    op.execute(
        r"""
        CREATE OR REPLACE FUNCTION public.dom_student_json_is_safe_v1(
            payload jsonb
        )
        RETURNS boolean
        LANGUAGE plpgsql
        IMMUTABLE
        STRICT
        SET search_path = pg_catalog
        AS $function$
        DECLARE
            object_item record;
            array_item jsonb;
            value_text text;
        BEGIN
            IF jsonb_typeof(payload) = 'object' THEN
                FOR object_item IN
                    SELECT key, value FROM jsonb_each(payload)
                LOOP
                    IF object_item.key IN (
                        's_id',
                        'stu_id',
                        'user_id',
                        'student_id',
                        'student_ids'
                    ) THEN
                        RETURN FALSE;
                    END IF;

                    IF object_item.key IN ('cancel_reason', 'reason_desc') THEN
                        IF jsonb_typeof(object_item.value) NOT IN (
                            'null',
                            'string'
                        ) THEN
                            RETURN FALSE;
                        END IF;
                        IF jsonb_typeof(object_item.value) = 'string' THEN
                            value_text := object_item.value #>> '{}';
                            IF value_text NOT IN (
                                '',
                                'Unfilled Lesson Memo',
                                'Domestic reason redacted'
                            ) THEN
                                RETURN FALSE;
                            END IF;
                        END IF;
                    ELSIF object_item.key = 'student_token' THEN
                        IF jsonb_typeof(object_item.value) <> 'string' THEN
                            RETURN FALSE;
                        END IF;
                        value_text := object_item.value #>> '{}';
                        IF value_text !~ '^dom:v1:[0-9a-f]{64}$' THEN
                            RETURN FALSE;
                        END IF;
                    ELSIF object_item.key = 'student_subjects' THEN
                        IF jsonb_typeof(object_item.value) <> 'array' THEN
                            RETURN FALSE;
                        END IF;
                        FOR array_item IN
                            SELECT value
                            FROM jsonb_array_elements(object_item.value)
                        LOOP
                            IF jsonb_typeof(array_item) <> 'string'
                               OR (array_item #>> '{}')
                                  !~ '^dom:v1:[0-9a-f]{64}$' THEN
                                RETURN FALSE;
                            END IF;
                        END LOOP;
                    ELSIF object_item.key = 'info'
                          AND jsonb_typeof(object_item.value) = 'string' THEN
                        value_text := object_item.value #>> '{}';
                        BEGIN
                            IF NOT public.dom_student_json_is_safe_v1(
                                value_text::jsonb
                            ) THEN
                                RETURN FALSE;
                            END IF;
                        EXCEPTION
                            WHEN invalid_text_representation THEN
                                RETURN FALSE;
                        END;
                    ELSIF NOT public.dom_student_json_is_safe_v1(
                        object_item.value
                    ) THEN
                        RETURN FALSE;
                    END IF;
                END LOOP;
            ELSIF jsonb_typeof(payload) = 'array' THEN
                FOR array_item IN SELECT value FROM jsonb_array_elements(payload)
                LOOP
                    IF NOT public.dom_student_json_is_safe_v1(array_item) THEN
                        RETURN FALSE;
                    END IF;
                END LOOP;
            END IF;
            RETURN TRUE;
        END
        $function$;

        REVOKE ALL ON FUNCTION public.dom_student_json_is_safe_v1(jsonb)
        FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.dom_student_json_is_safe_v1(jsonb)
        TO tit_dts_ingest_runtime;
        """
    )


def _reject_existing_violations() -> None:
    op.execute(
        r"""
        DO $dom_student_privacy_existing_state$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM public.dts_source_rows rows
                WHERE rows.source_region = 'dom'
                  AND (
                      NOT public.dom_student_json_is_safe_v1(rows.source_row)
                      OR NOT public.dom_student_json_is_safe_v1(
                          rows.dependency_keys
                      )
                  )
            ) THEN
                RAISE EXCEPTION
                    'domestic DTS source state contains a forbidden student identifier'
                    USING ERRCODE = '23514';
            END IF;

            IF EXISTS (
                SELECT 1
                FROM public.dts_dirty_keys dirty
                WHERE dirty.last_source_region = 'dom'
                  AND dirty.key_type = 'TEACHER_STUDENT'
                  AND dirty.key_part_2 !~ '^dom:v1:[0-9a-f]{64}$'
            ) THEN
                RAISE EXCEPTION
                    'domestic DTS dirty state contains a forbidden student identifier'
                    USING ERRCODE = '23514';
            END IF;

            IF EXISTS (
                SELECT 1
                FROM public.lesson_source_wide lessons
                LEFT JOIN LATERAL (
                    SELECT
                        count(*) FILTER (
                            WHERE appoints.source_region = 'dom'
                        ) AS dom_sources,
                        count(*) FILTER (
                            WHERE appoints.source_region = 'ovs'
                        ) AS ovs_sources
                    FROM public.dts_source_rows appoints
                    WHERE appoints.source_table IN (
                        'dom_appoint',
                        'ovs_appoint'
                    )
                      AND appoints.source_row ->> 'id' = lessons."课程id"
                ) provenance ON TRUE
                WHERE (provenance.dom_sources > 0)::int
                    + (provenance.ovs_sources > 0)::int <> 1
            ) THEN
                RAISE EXCEPTION
                    'lesson wide state lacks one unambiguous source region'
                    USING ERRCODE = '23514';
            END IF;

            IF EXISTS (
                SELECT 1
                FROM public.lesson_source_wide lessons
                JOIN public.dts_source_rows appoints
                 ON appoints.source_region = 'dom'
                 AND appoints.source_table = 'dom_appoint'
                 AND appoints.source_row ->> 'id' = lessons."课程id"
                WHERE lessons."学员id" IS NOT NULL
                  AND lessons."学员id" !~ '^dom:v1:[0-9a-f]{64}$'
            ) THEN
                RAISE EXCEPTION
                    'domestic lesson wide state contains a forbidden student identifier'
                    USING ERRCODE = '23514';
            END IF;
        END
        $dom_student_privacy_existing_state$;
        """
    )


def _install_dts_state_guard() -> None:
    op.execute(
        r"""
        CREATE OR REPLACE FUNCTION public.guard_dts_runtime_state_write()
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
            IF TG_TABLE_NAME = 'dts_source_rows' THEN
                IF (TG_OP = 'DELETE'
                    AND OLD.source_table = '__dom_student_privacy_contract__')
                   OR (TG_OP = 'UPDATE'
                       AND (
                           OLD.source_table = '__dom_student_privacy_contract__'
                           OR NEW.source_table = '__dom_student_privacy_contract__'
                       )) THEN
                    RAISE EXCEPTION
                        'domestic student privacy contract is immutable'
                        USING ERRCODE = '42501';
                END IF;
            END IF;

            IF TG_OP <> 'DELETE' THEN
                IF TG_TABLE_NAME = 'dts_source_rows' THEN
                    IF NEW.source_region = 'dom'
                       AND (
                           NOT public.dom_student_json_is_safe_v1(
                               NEW.source_row
                           )
                           OR NOT public.dom_student_json_is_safe_v1(
                               NEW.dependency_keys
                           )
                       ) THEN
                        RAISE EXCEPTION
                            'domestic DTS source state contains a forbidden student identifier'
                            USING ERRCODE = '23514';
                    END IF;
                    IF NEW.source_region = 'dom'
                       AND NEW.source_table = 'dom_appoint'
                       AND EXISTS (
                           SELECT 1
                           FROM public.lesson_source_wide lessons
                           WHERE lessons."课程id"
                                 = NEW.source_row ->> 'id'
                             AND lessons."学员id" IS NOT NULL
                             AND lessons."学员id"
                                 !~ '^dom:v1:[0-9a-f]{64}$'
                       ) THEN
                        RAISE EXCEPTION
                            'domestic lesson wide state contains a forbidden student identifier'
                            USING ERRCODE = '23514';
                    END IF;
                ELSIF TG_TABLE_NAME = 'dts_dirty_keys' THEN
                    IF NEW.last_source_region = 'dom'
                       AND NEW.key_type = 'TEACHER_STUDENT'
                       AND NEW.key_part_2
                           !~ '^dom:v1:[0-9a-f]{64}$' THEN
                        RAISE EXCEPTION
                            'domestic DTS dirty state contains a forbidden student identifier'
                            USING ERRCODE = '23514';
                    END IF;
                END IF;
            END IF;

            IF actor_name <> 'tit_dts_ingest_runtime' THEN
                IF TG_OP = 'DELETE' THEN
                    RETURN OLD;
                END IF;
                RETURN NEW;
            END IF;

            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'DTS durable state cannot be physically deleted'
                    USING ERRCODE = '42501';
            END IF;

            IF TG_OP = 'INSERT' THEN
                RETURN NEW;
            END IF;

            IF TG_TABLE_NAME = 'dts_ingest_events' THEN
                RAISE EXCEPTION 'DTS event ledger is append-only'
                    USING ERRCODE = '42501';
            ELSIF TG_TABLE_NAME = 'dts_ingest_checkpoints' THEN
                IF NEW.source_region IS DISTINCT FROM OLD.source_region
                   OR NEW.topic IS DISTINCT FROM OLD.topic
                   OR NEW.partition_id IS DISTINCT FROM OLD.partition_id
                   OR NEW.next_offset < OLD.next_offset THEN
                    RAISE EXCEPTION 'DTS checkpoint identity or offset regressed'
                        USING ERRCODE = '23514';
                END IF;
            ELSIF TG_TABLE_NAME = 'dts_source_rows' THEN
                IF NEW.source_region IS DISTINCT FROM OLD.source_region
                   OR NEW.source_table IS DISTINCT FROM OLD.source_table
                   OR NEW.source_key IS DISTINCT FROM OLD.source_key
                   OR NEW.row_version <> OLD.row_version + 1
                   OR (NEW.source_timestamp, NEW.last_record_id, NEW.last_offset)
                      <= (OLD.source_timestamp, OLD.last_record_id, OLD.last_offset)
                THEN
                    RAISE EXCEPTION
                        'DTS source-row identity, version, or source order is invalid'
                        USING ERRCODE = '23514';
                END IF;
            ELSIF TG_TABLE_NAME = 'dts_dirty_keys' THEN
                IF NEW.key_type IS DISTINCT FROM OLD.key_type
                   OR NEW.key_part_1 IS DISTINCT FROM OLD.key_part_1
                   OR NEW.key_part_2 IS DISTINCT FROM OLD.key_part_2
                   OR NEW.row_version <> OLD.row_version + 1 THEN
                    RAISE EXCEPTION
                        'DTS dirty-key identity or version is invalid'
                        USING ERRCODE = '23514';
                END IF;
            END IF;
            RETURN NEW;
        END
        $function$;

        REVOKE ALL ON FUNCTION public.guard_dts_runtime_state_write()
        FROM PUBLIC;

        DO $dts_state_guards$
        DECLARE
            relation_name text;
        BEGIN
            FOREACH relation_name IN ARRAY ARRAY[
                'dts_ingest_checkpoints',
                'dts_ingest_events',
                'dts_source_rows',
                'dts_dirty_keys'
            ]::text[] LOOP
                IF to_regclass('public.' || relation_name) IS NULL THEN
                    RAISE EXCEPTION 'required DTS state table %.% is missing',
                        'public', relation_name;
                END IF;
                EXECUTE format(
                    'DROP TRIGGER IF EXISTS guard_dts_runtime_state_write ON public.%I',
                    relation_name
                );
                EXECUTE format(
                    'CREATE TRIGGER guard_dts_runtime_state_write '
                    'BEFORE INSERT OR UPDATE OR DELETE ON public.%I FOR EACH ROW '
                    'EXECUTE FUNCTION public.guard_dts_runtime_state_write()',
                    relation_name
                );
            END LOOP;
        END
        $dts_state_guards$;
        """
    )


def _install_lesson_guard() -> None:
    op.execute(
        r"""
        CREATE OR REPLACE FUNCTION public.guard_dom_lesson_student_privacy_v1()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE
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
            IF NOT has_dom_source
               AND NOT has_ovs_source THEN
                RAISE EXCEPTION
                    'lesson wide write requires persisted source provenance'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END
        $function$;

        REVOKE ALL ON FUNCTION public.guard_dom_lesson_student_privacy_v1()
        FROM PUBLIC;

        DROP TRIGGER IF EXISTS guard_dom_lesson_student_privacy_v1
        ON public.lesson_source_wide;
        CREATE TRIGGER guard_dom_lesson_student_privacy_v1
        BEFORE INSERT OR UPDATE ON public.lesson_source_wide
        FOR EACH ROW
        EXECUTE FUNCTION public.guard_dom_lesson_student_privacy_v1();
        """
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    _install_json_validator()
    _reject_existing_violations()
    _install_dts_state_guard()
    _install_lesson_guard()


def downgrade() -> None:
    raise RuntimeError(
        "20260813_60_dom_privacy is forward-only: removing the database "
        "privacy boundary could allow domestic student identifiers overseas"
    )
