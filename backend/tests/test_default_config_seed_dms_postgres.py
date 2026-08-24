from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil
import socket
import subprocess

import psycopg
import pytest

from app.config_models import ConfigKey, DEFAULT_CONFIG_PAYLOADS
from app.config_service import _payload_hash, validate_config_payload


POSTGRES_BINARIES = ("initdb", "pg_ctl", "postgres", "psql")
DMS_20260820_CONFIG_KEYS = (
    ConfigKey.SCORE_GRADUATION,
    ConfigKey.AGENT_POLICY,
    ConfigKey.DELIVERY_POLICY,
)


def _postgres_tools_available() -> bool:
    return all(shutil.which(binary) for binary in POSTGRES_BINARIES)


def _split_dms_onequery_statements(sql_source: str) -> list[str]:
    """Approximate DMS onequery splitting, which recognizes only bare $$ blocks."""
    statements: list[str] = []
    statement_start = 0
    index = 0
    state = "normal"

    while index < len(sql_source):
        pair = sql_source[index : index + 2]
        character = sql_source[index]

        if state == "normal":
            if pair == "--":
                state = "line_comment"
                index += 2
                continue
            if pair == "/*":
                state = "block_comment"
                index += 2
                continue
            if pair == "$$":
                state = "dollar_quote"
                index += 2
                continue
            if character == "'":
                state = "single_quote"
            elif character == '"':
                state = "double_quote"
            elif character == ";":
                statement = sql_source[statement_start : index + 1].strip()
                if statement:
                    statements.append(statement)
                statement_start = index + 1
            index += 1
            continue

        if state == "line_comment":
            if character == "\n":
                state = "normal"
            index += 1
            continue

        if state == "block_comment":
            if pair == "*/":
                state = "normal"
                index += 2
            else:
                index += 1
            continue

        if state == "dollar_quote":
            if pair == "$$":
                state = "normal"
                index += 2
            else:
                index += 1
            continue

        if state == "single_quote":
            if pair == "''":
                index += 2
            else:
                if character == "'":
                    state = "normal"
                index += 1
            continue

        if state == "double_quote":
            if pair == '""':
                index += 2
            else:
                if character == '"':
                    state = "normal"
                index += 1

    assert state in {"normal", "line_comment"}, f"unterminated SQL state: {state}"
    trailing = sql_source[statement_start:].strip()
    if trailing:
        statements.append(trailing)
    return statements


def _execute_statements(connection: psycopg.Connection, statements: list[str]) -> None:
    for statement in statements:
        connection.execute(statement)


def _fixture_sql() -> str:
    score_by_code = {
        "G01": 3,
        "G02": 2,
        "G03": 2,
        "G04": 3,
        "G05": 3,
        "G06": 4,
        "G07": 3,
        "G08": 5,
        "G09": 5,
    }
    template_values = ",\n".join(
        f"('TPL-{code}', '{code}', 'PUBLISHED', '{{\"score_value\":{score}}}'::jsonb)"
        for code, score in score_by_code.items()
    )
    assignment_states = {
        "G03": ("IN_PROGRESS", 3),
        "G04": ("VIEWED", 2),
    }
    assignment_values = ",\n".join(
        "("
        f"'ASN-{code}', 'TEST-SOURCE-TEACHER-001', '{code}', 'TPL-{code}', "
        f"'FIXED_GROWTH', 'TRIGGER_CENTER', "
        f"'{assignment_states.get(code, ('ASSIGNED', 1))[0]}', 'REAL', "
        f"{assignment_states.get(code, ('ASSIGNED', 1))[1]}, "
        f"'fixed:TEST-SOURCE-TEACHER-001:{code}', NULL"
        ")"
        for code in score_by_code
    )
    return f"""
        CREATE TABLE public.alembic_version (version_num text PRIMARY KEY);
        INSERT INTO public.alembic_version VALUES ('20260819_65_g09_set_course');

        CREATE TABLE public.teachers (
            teacher_id text PRIMARY KEY,
            source_snapshot_label text,
            data_mode text NOT NULL,
            total_score numeric NOT NULL,
            graduation_state text NOT NULL,
            gold_qualified boolean NOT NULL
        );
        INSERT INTO public.teachers VALUES (
            'TEST-SOURCE-TEACHER-001', 'INTERNAL_TEST_ACCOUNT', 'REAL', 0,
            'IN_PROGRESS', false
        );

        CREATE TABLE public.task_templates (
            row_id text PRIMARY KEY,
            template_id text NOT NULL,
            status text NOT NULL,
            payload jsonb NOT NULL
        );
        INSERT INTO public.task_templates(row_id, template_id, status, payload) VALUES
        {template_values};

        CREATE TABLE public.task_assignments (
            assignment_id text PRIMARY KEY,
            teacher_id text NOT NULL REFERENCES public.teachers(teacher_id) ON DELETE RESTRICT,
            task_code text NOT NULL,
            template_version_id text NOT NULL REFERENCES public.task_templates(row_id),
            task_kind text NOT NULL,
            creator_system text NOT NULL,
            status text NOT NULL,
            source_mode text NOT NULL,
            row_version integer NOT NULL,
            dedupe_key text NOT NULL,
            completed_at timestamptz
        );
        INSERT INTO public.task_assignments(
            assignment_id, teacher_id, task_code, template_version_id,
            task_kind, creator_system, status, source_mode, row_version,
            dedupe_key, completed_at
        ) VALUES
        {assignment_values};

        CREATE FUNCTION public.reject_task_assignment_delete()
        RETURNS trigger LANGUAGE plpgsql AS $function$
        BEGIN
            RAISE EXCEPTION 'task assignment cannot be deleted';
        END
        $function$;
        CREATE TRIGGER trg_task_assignment_reject_delete
        BEFORE DELETE ON public.task_assignments
        FOR EACH ROW EXECUTE FUNCTION public.reject_task_assignment_delete();

        CREATE TABLE public.teacher_source_wide (tchr_id text PRIMARY KEY);
        CREATE TABLE public.lesson_source_wide (
            "课程id" text PRIMARY KEY,
            "老师id" text NOT NULL
        );
        CREATE TABLE public.outbox_events (
            outbox_id text PRIMARY KEY,
            aggregate_type text NOT NULL,
            aggregate_id text NOT NULL,
            event_type text NOT NULL,
            payload jsonb NOT NULL,
            status text NOT NULL
        );
        INSERT INTO public.outbox_events VALUES
        (
            'OUT-G03-VIEWED', 'TASK_ASSIGNMENT', 'ASN-G03',
            'task.assignment_changed.shared',
            '{{"assignment_id":"ASN-G03","teacher_id":"TEST-SOURCE-TEACHER-001","from_status":"ASSIGNED","to_status":"VIEWED"}}'::jsonb,
            'PUBLISHED'
        ),
        (
            'OUT-G03-IN-PROGRESS', 'TASK_ASSIGNMENT', 'ASN-G03',
            'task.assignment_changed.shared',
            '{{"assignment_id":"ASN-G03","teacher_id":"TEST-SOURCE-TEACHER-001","from_status":"VIEWED","to_status":"IN_PROGRESS"}}'::jsonb,
            'PENDING'
        ),
        (
            'OUT-G04-VIEWED', 'TASK_ASSIGNMENT', 'ASN-G04',
            'task.assignment_changed.shared',
            '{{"assignment_id":"ASN-G04","teacher_id":"TEST-SOURCE-TEACHER-001","from_status":"ASSIGNED","to_status":"VIEWED"}}'::jsonb,
            'PUBLISHED'
        );
        CREATE TABLE public.audit_events (
            sequence bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            event_id text UNIQUE NOT NULL,
            event_type text NOT NULL,
            teacher_id text,
            task_id text,
            case_id text,
            occurred_at timestamptz NOT NULL,
            actor_type text NOT NULL,
            payload_hash text NOT NULL,
            payload jsonb NOT NULL
        );

        CREATE TABLE public.config_versions (
            version_id text PRIMARY KEY,
            config_key text NOT NULL,
            version_number integer NOT NULL,
            status text NOT NULL,
            high_impact boolean NOT NULL,
            payload jsonb NOT NULL,
            validation_errors jsonb NOT NULL,
            source_version_id text REFERENCES public.config_versions(version_id),
            created_by text NOT NULL,
            updated_by text NOT NULL,
            validated_by text,
            published_by text,
            retired_by text,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            validated_at timestamptz,
            published_at timestamptz,
            retired_at timestamptz,
            UNIQUE(config_key, version_number)
        );
        CREATE UNIQUE INDEX uq_one_published_config_per_key
        ON public.config_versions(config_key) WHERE status = 'PUBLISHED';
        CREATE TABLE public.config_publication_audits (
            audit_id text PRIMARY KEY,
            version_id text NOT NULL REFERENCES public.config_versions(version_id),
            config_key text NOT NULL,
            action text NOT NULL,
            actor_id text NOT NULL,
            from_status text,
            to_status text NOT NULL,
            payload_hash text NOT NULL,
            detail text NOT NULL,
            occurred_at timestamptz NOT NULL
        );

        CREATE TABLE public.teacher_usage (
            usage_id text PRIMARY KEY,
            teacher_id text NOT NULL REFERENCES public.teachers(teacher_id)
        );
        INSERT INTO public.teacher_usage VALUES ('BLOCKER-1', 'TEST-SOURCE-TEACHER-001');

        CREATE SCHEMA tide;
        CREATE TABLE tide.schema_migrations (
            migration_id text PRIMARY KEY,
            migration_order integer UNIQUE NOT NULL,
            filename text NOT NULL,
            sha256 char(64) NOT NULL
        );
        INSERT INTO tide.schema_migrations
        SELECT
            lpad(value::text, 4, '0') || '_fixture',
            value,
            lpad(value::text, 4, '0') || '_fixture.up.sql',
            repeat('a', 64)
        FROM generate_series(1, 36) AS value;
        INSERT INTO tide.schema_migrations VALUES (
            '0042_g09_set_kuozhi_course',
            37,
            '0042_g09_set_kuozhi_course.up.sql',
            '59c6f757ec6ca9ee60ad4e51f594bc62c678112139e8d1f70ab6905b1a8b663a'
        );

        CREATE TABLE tide.user_accounts (
            id uuid PRIMARY KEY,
            email text NOT NULL
        );
        CREATE TABLE tide.teacher_bindings (
            id uuid PRIMARY KEY,
            account_id uuid NOT NULL REFERENCES tide.user_accounts(id) ON DELETE RESTRICT,
            teacher_id text NOT NULL UNIQUE
        );
        CREATE TABLE tide.binding_audit_events (
            id uuid PRIMARY KEY,
            binding_id uuid REFERENCES tide.teacher_bindings(id) ON DELETE SET NULL,
            account_id uuid REFERENCES tide.user_accounts(id) ON DELETE SET NULL,
            new_teacher_id text,
            actor_ref text,
            reason text
        );
        INSERT INTO tide.user_accounts VALUES (
            '00000000-0000-0000-0000-000000000001', 'test@example.invalid'
        );
        INSERT INTO tide.teacher_bindings VALUES (
            '00000000-0000-0000-0000-000000000002',
            '00000000-0000-0000-0000-000000000001',
            'TEST-SOURCE-TEACHER-001'
        );
        INSERT INTO tide.binding_audit_events VALUES (
            '00000000-0000-0000-0000-000000000003',
            '00000000-0000-0000-0000-000000000002',
            NULL,
            'LEGACY-TEST-TEACHER',
            'LEGACY_PROVISIONER',
            'LEGACY_TEST_ACCOUNT'
        );

        CREATE TABLE tide.app_events (
            id uuid PRIMARY KEY,
            teacher_binding_id uuid,
            task_assignment_id text,
            CONSTRAINT client_events_teacher_binding_id_fkey
                FOREIGN KEY (teacher_binding_id)
                REFERENCES tide.teacher_bindings(id) ON DELETE CASCADE,
            CONSTRAINT app_events_task_assignment_id_fkey
                FOREIGN KEY (task_assignment_id)
                REFERENCES public.task_assignments(assignment_id) ON DELETE SET NULL
        );
        INSERT INTO tide.app_events(id, teacher_binding_id, task_assignment_id)
        SELECT
            md5('app-event-' || value::text)::uuid,
            '00000000-0000-0000-0000-000000000002'::uuid,
            NULL
        FROM generate_series(1, 333) AS value;
        INSERT INTO tide.app_events(id, teacher_binding_id, task_assignment_id)
        SELECT
            md5('task-app-event-' || value::text)::uuid,
            NULL,
            'ASN-G' || lpad((((value - 1) % 9) + 1)::text, 2, '0')
        FROM generate_series(1, 62) AS value;

        CREATE TABLE tide.kuozhi_course_syncs (
            id uuid PRIMARY KEY,
            task_assignment_id text NOT NULL,
            CONSTRAINT kuozhi_course_syncs_task_assignment_id_fkey
                FOREIGN KEY (task_assignment_id)
                REFERENCES public.task_assignments(assignment_id) ON DELETE CASCADE
        );
        INSERT INTO tide.kuozhi_course_syncs VALUES
        ('10000000-0000-0000-0000-000000000001', 'ASN-G09'),
        ('10000000-0000-0000-0000-000000000002', 'ASN-G09');

        CREATE TABLE tide.task_command_receipts (
            id uuid PRIMARY KEY,
            task_assignment_id text NOT NULL,
            CONSTRAINT task_command_receipts_task_assignment_id_fkey
                FOREIGN KEY (task_assignment_id)
                REFERENCES public.task_assignments(assignment_id) ON DELETE CASCADE
        );
        INSERT INTO tide.task_command_receipts VALUES
        ('20000000-0000-0000-0000-000000000001', 'ASN-G03'),
        ('20000000-0000-0000-0000-000000000002', 'ASN-G03'),
        ('20000000-0000-0000-0000-000000000003', 'ASN-G04');

        CREATE TABLE tide.source_read_status (
            id uuid PRIMARY KEY,
            teacher_binding_id uuid NOT NULL,
            CONSTRAINT source_read_status_teacher_binding_id_fkey
                FOREIGN KEY (teacher_binding_id)
                REFERENCES tide.teacher_bindings(id) ON DELETE CASCADE
        );
        INSERT INTO tide.source_read_status VALUES
        ('30000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000002'),
        ('30000000-0000-0000-0000-000000000002', '00000000-0000-0000-0000-000000000002'),
        ('30000000-0000-0000-0000-000000000003', '00000000-0000-0000-0000-000000000002'),
        ('30000000-0000-0000-0000-000000000004', '00000000-0000-0000-0000-000000000002');
    """


@pytest.mark.skipif(
    not _postgres_tools_available(),
    reason="local PostgreSQL binaries are required for the DMS SQL test",
)
def test_default_config_seed_dms_is_fail_closed_atomic_and_exact(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    dms_sql = (
        repo_root
        / "backend"
        / "migrations"
        / "dms"
        / "20260820_test_cleanup_and_default_config_seed.sql"
    )
    sql_source = dms_sql.read_text(encoding="utf-8")
    statements = _split_dms_onequery_statements(sql_source)

    assert sql_source.count("\nBEGIN;") == 1
    assert sql_source.count("\nCOMMIT;") == 1
    assert not re.search(r"\$[A-Za-z_][A-Za-z0-9_]*\$", sql_source)
    assert sum(statement.startswith("DO $$") for statement in statements) == 6
    assert "DISABLE TRIGGER trg_task_assignment_reject_delete" in sql_source
    assert "ENABLE TRIGGER trg_task_assignment_reject_delete" in sql_source

    normalized_hashes = {
        key.value: _payload_hash(
            validate_config_payload(key, DEFAULT_CONFIG_PAYLOADS[key])
        )
        for key in DMS_20260820_CONFIG_KEYS
    }
    assert normalized_hashes == {
        "SCORE_GRADUATION": "356319eb34e67822e9c8322d2f94a3e7cc232fcf1f8da5abc8b70965245ab1ce",
        "AGENT_POLICY": "8fcf2ad938bf0498996a6c27c3c67a253d9b757ee2ae0fee19848e5e089b20ff",
        "DELIVERY_POLICY": "aa21bff2c6cf8779c1da1ac034ada5bcebedd35a10be49b18ce251689a2fd48b",
    }
    for payload_hash in normalized_hashes.values():
        assert payload_hash in sql_source

    data_dir = tmp_path / "postgres-data"
    log_path = tmp_path / "postgres.log"
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        postgres_port = int(probe.getsockname()[1])

    subprocess.run(
        [
            shutil.which("initdb") or "initdb",
            "-D",
            str(data_dir),
            "-A",
            "trust",
            "-U",
            "postgres",
            "--no-locale",
            "--encoding=UTF8",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    pg_ctl = shutil.which("pg_ctl") or "pg_ctl"
    subprocess.run(
        [
            pg_ctl,
            "-D",
            str(data_dir),
            "-l",
            str(log_path),
            "-o",
            f"-p {postgres_port} -c listen_addresses=127.0.0.1 -c fsync=off",
            "-w",
            "start",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    admin_url = f"postgresql://postgres@127.0.0.1:{postgres_port}/postgres"
    test_url = (
        f"postgresql://tide_sys_admin@127.0.0.1:{postgres_port}/tide_system_test"
    )
    try:
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute("CREATE ROLE tide_sys_admin LOGIN")
            admin.execute("CREATE DATABASE tide_system_test OWNER tide_sys_admin")

        with psycopg.connect(test_url) as connection:
            connection.execute(_fixture_sql())
            connection.commit()

            # A newly discovered reference must abort the whole package without disabling the guard.
            with pytest.raises(psycopg.errors.RaiseException, match="target teacher is referenced"):
                _execute_statements(connection, statements)
            connection.rollback()
            assert connection.execute("SELECT count(*) FROM public.teachers").fetchone() == (1,)
            assert connection.execute("SELECT count(*) FROM public.task_assignments").fetchone() == (9,)
            assert connection.execute("SELECT count(*) FROM public.config_versions").fetchone() == (0,)
            assert connection.execute(
                """
                SELECT tgenabled
                FROM pg_trigger
                WHERE tgrelid = 'public.task_assignments'::regclass
                  AND tgname = 'trg_task_assignment_reject_delete'
                """
            ).fetchone() == ("O",)

            connection.execute("DELETE FROM public.teacher_usage")
            connection.commit()

            # The audit row is required by direct binding identity, not by legacy payload wording.
            connection.execute("UPDATE tide.binding_audit_events SET binding_id = NULL")
            connection.commit()
            with pytest.raises(
                psycopg.errors.RaiseException,
                match="unexpected binding audit/source-read status counts",
            ):
                _execute_statements(connection, statements)
            connection.rollback()
            assert connection.execute("SELECT count(*) FROM public.teachers").fetchone() == (1,)
            assert connection.execute("SELECT count(*) FROM public.config_versions").fetchone() == (0,)

            connection.execute(
                """
                UPDATE tide.binding_audit_events
                SET binding_id = '00000000-0000-0000-0000-000000000002'
                """
            )
            connection.commit()

            # A completed/malformed event must not be treated as harmless test-history outbox.
            connection.execute(
                """
                UPDATE public.outbox_events
                SET payload = jsonb_set(payload, '{to_status}', '"COMPLETED"')
                WHERE outbox_id = 'OUT-G04-VIEWED'
                """
            )
            connection.commit()
            with pytest.raises(psycopg.errors.RaiseException, match="unsafe or malformed outbox"):
                _execute_statements(connection, statements)
            connection.rollback()
            assert connection.execute("SELECT count(*) FROM public.teachers").fetchone() == (1,)
            assert connection.execute("SELECT count(*) FROM public.config_versions").fetchone() == (0,)

            connection.execute(
                """
                UPDATE public.outbox_events
                SET payload = jsonb_set(payload, '{to_status}', '"VIEWED"')
                WHERE outbox_id = 'OUT-G04-VIEWED'
                """
            )
            connection.execute(
                """
                INSERT INTO tide.app_events(id, teacher_binding_id, task_assignment_id) VALUES
                (
                    '40000000-0000-0000-0000-000000000001',
                    '00000000-0000-0000-0000-000000000002',
                    NULL
                ),
                (
                    '40000000-0000-0000-0000-000000000002',
                    NULL,
                    'ASN-G01'
                )
                """
            )
            connection.commit()
            _execute_statements(connection, statements)

            assert connection.execute("SELECT count(*) FROM public.teachers").fetchone() == (0,)
            assert connection.execute("SELECT count(*) FROM public.task_assignments").fetchone() == (0,)
            assert connection.execute("SELECT count(*) FROM public.outbox_events").fetchone() == (3,)
            assert connection.execute("SELECT count(*) FROM tide.app_events").fetchone() == (397,)
            assert connection.execute(
                """
                SELECT count(*)
                FROM tide.app_events
                WHERE teacher_binding_id IS NULL AND task_assignment_id IS NULL
                """
            ).fetchone() == (397,)
            assert connection.execute(
                "SELECT count(*) FROM tide.kuozhi_course_syncs"
            ).fetchone() == (0,)
            assert connection.execute(
                "SELECT count(*) FROM tide.task_command_receipts"
            ).fetchone() == (0,)
            assert connection.execute(
                "SELECT count(*) FROM tide.source_read_status"
            ).fetchone() == (0,)
            assert connection.execute("SELECT count(*) FROM public.config_versions").fetchone() == (3,)
            assert connection.execute(
                "SELECT count(*) FROM public.config_versions WHERE status = 'PUBLISHED'"
            ).fetchone() == (3,)
            assert connection.execute(
                "SELECT count(*) FROM public.config_publication_audits"
            ).fetchone() == (9,)
            audit_rows = connection.execute(
                """
                SELECT config_key, array_agg(action ORDER BY occurred_at)
                FROM public.config_publication_audits
                GROUP BY config_key
                """
            ).fetchall()
            assert {row[0]: row[1] for row in audit_rows} == {
                key.value: ["CREATE_DRAFT", "VALIDATE", "PUBLISH"]
                for key in DMS_20260820_CONFIG_KEYS
            }
            assert connection.execute("SELECT count(*) FROM tide.teacher_bindings").fetchone() == (0,)
            assert connection.execute("SELECT count(*) FROM tide.user_accounts").fetchone() == (1,)
            assert connection.execute(
                "SELECT binding_id FROM tide.binding_audit_events"
            ).fetchone() == (None,)
            assert connection.execute(
                """
                SELECT tgenabled
                FROM pg_trigger
                WHERE tgrelid = 'public.task_assignments'::regclass
                  AND tgname = 'trg_task_assignment_reject_delete'
                """
            ).fetchone() == ("O",)

            rows = connection.execute(
                "SELECT config_key, payload FROM public.config_versions ORDER BY config_key"
            ).fetchall()
            assert {row[0]: row[1] for row in rows} == {
                key.value: validate_config_payload(
                    key,
                    DEFAULT_CONFIG_PAYLOADS[key],
                )
                for key in DMS_20260820_CONFIG_KEYS
            }
            cleanup_hash, cleanup_payload = connection.execute(
                """
                SELECT payload_hash, payload
                FROM public.audit_events
                WHERE event_id = 'DMS-TEST-CLEANUP-20260820-TEST-SOURCE-TEACHER-001'
                """
            ).fetchone()
            assert cleanup_payload["detached_app_event_count"] == 334
            assert cleanup_payload["detached_task_event_count"] == 63
            assert cleanup_payload["schema_version"] == "dms_test_cleanup.v3"
            cleanup_canonical = json.dumps(
                cleanup_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            assert cleanup_hash == hashlib.sha256(
                cleanup_canonical.encode("utf-8")
            ).hexdigest()
    finally:
        subprocess.run(
            [pg_ctl, "-D", str(data_dir), "-m", "fast", "-w", "stop"],
            check=True,
            capture_output=True,
            text=True,
        )

    # The checksum in the runbook must describe the exact tested file.
    dms_sha256 = hashlib.sha256(dms_sql.read_bytes()).hexdigest()
    runbook = dms_sql.with_name("README_test_cleanup_and_default_config_seed.md")
    assert dms_sha256 in runbook.read_text(encoding="utf-8")
