from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import socket
import subprocess

import pytest
from sqlalchemy import create_engine, text

from app.task_catalog import MANDATORY_TASKS, TASK_COPY


POSTGRES_BINARIES = ("initdb", "pg_ctl", "postgres", "psql")


def _postgres_tools_available() -> bool:
    return all(shutil.which(binary) for binary in POSTGRES_BINARIES)


@pytest.mark.skipif(
    not _postgres_tools_available(),
    reason="local PostgreSQL binaries are required for the DMS SQL test",
)
def test_mr60_dms_sql_advances_exact_public63_teacher0041_state(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    dms_sql = (
        repo_root
        / "backend"
        / "migrations"
        / "dms"
        / "20260819_mr60_public63_teacher0041_to_public65_teacher0042.sql"
    )
    sql_source = dms_sql.read_text(encoding="utf-8")
    dms_sha256 = hashlib.sha256(dms_sql.read_bytes()).hexdigest()
    dms_readme = dms_sql.with_name("README_mr60.md").read_text(encoding="utf-8")
    assert dms_sha256 in dms_readme
    assert sql_source.count("\nBEGIN;") == 1
    assert sql_source.count("\nCOMMIT;") == 1

    catalog_names = {
        item[0]: (item[1], item[2])
        for item in MANDATORY_TASKS
    }
    for task_code in ("G05", "G08", "G09"):
        for catalog_name in catalog_names[task_code]:
            assert catalog_name in sql_source
        for copy_value in TASK_COPY[task_code]:
            assert copy_value in sql_source

    teacher_migration = (
        repo_root
        / "teacher"
        / "backend"
        / "database"
        / "migrations"
        / "0042_g09_set_kuozhi_course.up.sql"
    )
    teacher_sha256 = hashlib.sha256(teacher_migration.read_bytes()).hexdigest()
    assert teacher_sha256 in sql_source

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

    database_url = f"postgresql://postgres@127.0.0.1:{postgres_port}/postgres"
    engine = create_engine(
        f"postgresql+psycopg://postgres@127.0.0.1:{postgres_port}/postgres"
    )
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                """
                    CREATE TABLE public.alembic_version (
                        version_num text PRIMARY KEY
                    );
                    INSERT INTO public.alembic_version(version_num)
                    VALUES ('20260819_63_dts_direct_privacy');

                    CREATE TABLE public.task_templates (
                        row_id text PRIMARY KEY,
                        template_id text NOT NULL,
                        template_version integer NOT NULL,
                        status text NOT NULL,
                        execution_owner text NOT NULL,
                        revision integer NOT NULL,
                        payload jsonb NOT NULL,
                        updated_by text NOT NULL,
                        updated_at timestamptz NOT NULL
                    );
                    CREATE TABLE public.task_assignments (
                        assignment_id text PRIMARY KEY,
                        task_code text NOT NULL,
                        task_kind text NOT NULL,
                        why text NOT NULL,
                        display_title text,
                        row_version integer NOT NULL,
                        updated_by text NOT NULL,
                        updated_at timestamptz NOT NULL
                    );
                    CREATE FUNCTION public.enforce_task_assignment_write()
                    RETURNS trigger
                    LANGUAGE plpgsql
                    AS $function$
                    BEGIN
                        RETURN NEW;
                    END
                    $function$;
                    CREATE TRIGGER trg_task_assignment_write
                    BEFORE INSERT OR UPDATE ON public.task_assignments
                    FOR EACH ROW
                    EXECUTE FUNCTION public.enforce_task_assignment_write();

                    CREATE SCHEMA tide;
                    CREATE TABLE tide.schema_migrations (
                        migration_id text PRIMARY KEY,
                        migration_order integer NOT NULL UNIQUE,
                        filename text NOT NULL,
                        sha256 char(64) NOT NULL
                    );
                    INSERT INTO tide.schema_migrations(
                        migration_id, migration_order, filename, sha256
                    )
                    SELECT
                        CASE WHEN migration_order = 36
                            THEN '0041_crm_sso_hybrid'
                            ELSE lpad(migration_order::text, 4, '0') || '_fixture'
                        END,
                        migration_order,
                        CASE WHEN migration_order = 36
                            THEN '0041_crm_sso_hybrid.up.sql'
                            ELSE lpad(migration_order::text, 4, '0') || '_fixture.up.sql'
                        END,
                        repeat('a', 64)
                    FROM generate_series(1, 36)
                        AS sequence_number(migration_order);

                    CREATE TABLE tide.task_execution_versions (
                        id uuid PRIMARY KEY,
                        shared_template_row_id text NOT NULL UNIQUE,
                        task_code text NOT NULL,
                        status text NOT NULL,
                        execution_contract_version text NOT NULL,
                        config jsonb NOT NULL,
                        updated_at timestamptz NOT NULL
                    );
                    CREATE TABLE tide.task_step_definitions (
                        execution_version_id uuid NOT NULL
                    );
                    CREATE TABLE tide.task_validation_rules (
                        execution_version_id uuid NOT NULL
                    );

                    INSERT INTO public.task_templates VALUES
                    (
                        'G06:v1', 'G05', 1, 'PUBLISHED', 'TEACHER_APP', 4,
                        jsonb_build_object(
                            'template_id', 'G05',
                            'category', 'MANDATORY_GROWTH',
                            'score_type', 'FIXED',
                            'score_value', 3,
                            'content_status', 'READY',
                            'ops_name_zh', 'TTP 入门',
                            'title', 'TTP Orientation',
                            'why_template',
                                'Understand TTP and its key business scenarios.',
                            'how_summary',
                                'Watch the in-platform TTP video and confirm every item in the learning checklist.',
                            'completion_standard',
                                'The TTP video is watched in full and every published checklist item is confirmed.',
                            'benefit',
                                'You understand the key TTP workflow and commitments.'
                        ),
                        'FIXTURE', now()
                    ),
                    (
                        'G09:v1', 'G08', 1, 'PUBLISHED', 'TEACHER_APP', 4,
                        jsonb_build_object(
                            'template_id', 'G08',
                            'category', 'MANDATORY_GROWTH',
                            'score_type', 'FIXED',
                            'score_value', 5,
                            'content_status', 'READY',
                            'ops_name_zh', 'Global Communicator 培训',
                            'title', 'Global Communicator Training',
                            'why_template',
                                'Learn the core Global Communicator teaching flow.',
                            'how_summary',
                                'Complete the configured in-platform videos and quiz.',
                            'completion_standard',
                                'All configured videos and quiz requirements pass.',
                            'benefit',
                                'You can now confidently prepare for a Global Communicator lesson.'
                        ),
                        'FIXTURE', now()
                    ),
                    (
                        'G10:v1', 'G09', 1, 'PUBLISHED', 'TEACHER_APP', 4,
                        jsonb_build_object(
                            'template_id', 'G09',
                            'category', 'MANDATORY_GROWTH',
                            'score_type', 'FIXED',
                            'score_value', 5,
                            'content_status', 'READY',
                            'ops_name_zh', 'SET 教学基础',
                            'title', 'SET Teaching Fundamentals',
                            'why_template',
                                'Learn the fundamentals of SET teaching.',
                            'how_summary',
                                'Watch the in-platform Mock video slot and complete the five-question Mock check.',
                            'completion_standard',
                                'The Mock video is watched in full and the five-question check reaches 80%%.',
                            'benefit',
                                'You understand the SET teaching foundation.'
                        ),
                        'FIXTURE', now()
                    );

                    INSERT INTO public.task_assignments VALUES (
                        'G08-FIXTURE', 'G08', 'FIXED_GROWTH',
                        'Learn the core Cocos teaching flow.',
                        'Cocos Course Training', 7, 'FIXTURE', now()
                    );

                    INSERT INTO tide.task_execution_versions VALUES (
                        '10000000-0000-4000-8000-000000000009'::uuid,
                        'G10:v1', 'G09', 'ACTIVE', 'v1',
                        '{"estimatedMinutes":25,"allowRetry":true,"contentStatus":"PENDING","contentVersion":"2026-08-06","pendingReason":"KUOZHI_G09_COURSE_MAPPING_PENDING"}'::jsonb,
                        now()
                    );
                    """
            )

        result = subprocess.run(
            [
                shutil.which("psql") or "psql",
                "-X",
                "-v",
                "ON_ERROR_STOP=1",
                "-d",
                database_url,
                "-f",
                str(dms_sql),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"DMS SQL failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT version_num FROM public.alembic_version")
            ).scalar_one() == "20260819_65_g09_set_course"
            assert connection.execute(
                text(
                    """
                    SELECT migration_order, migration_id, sha256
                    FROM tide.schema_migrations
                    ORDER BY migration_order DESC
                    LIMIT 1
                    """
                )
            ).one() == (37, "0042_g09_set_kuozhi_course", teacher_sha256)
            assert connection.execute(
                text(
                    """
                    SELECT
                        payload->>'how_summary',
                        payload->>'completion_standard',
                        revision
                    FROM public.task_templates
                    WHERE row_id = 'G10:v1'
                    """
                )
            ).one() == (
                "Complete the three SET videos and their three paired quizzes "
                "in Kuozhi.",
                "All three required videos and all three paired quizzes reach "
                "100% progress in Kuozhi.",
                5,
            )
            assert connection.execute(
                text(
                    """
                    SELECT why, display_title, row_version
                    FROM public.task_assignments
                    WHERE assignment_id = 'G08-FIXTURE'
                    """
                )
            ).one() == (
                "Learn the core Global Communicator teaching flow.",
                "Global Communicator Training",
                8,
            )
            assert connection.execute(
                text(
                    """
                    SELECT execution_contract_version, config->>'contentStatus'
                    FROM tide.task_execution_versions
                    WHERE shared_template_row_id = 'G10:v1'
                    """
                )
            ).one() == ("task-contract-v3", "READY")
    finally:
        engine.dispose()
        subprocess.run(
            [pg_ctl, "-D", str(data_dir), "-m", "immediate", "-w", "stop"],
            check=True,
            capture_output=True,
            text=True,
        )
