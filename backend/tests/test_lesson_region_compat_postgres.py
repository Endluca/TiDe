from __future__ import annotations

import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import DBAPIError

from app.dts_ingest_store import (
    EXPANDED_DOMESTIC_PRIVACY_FUNCTIONS,
    EXPECTED_DOMESTIC_PRIVACY_FUNCTIONS,
)
from test_dts_v2_source_current_guards_postgres import (
    _seed_external_personalized_catalog,
)


POSTGRES_BINARIES = ("initdb", "pg_ctl", "postgres")
REVISION_65 = "20260819_65_g09_set_course"
REVISION_EXPAND = "20260822_65a_lesson_region_exp"
REVISION_PRE_CONTRACT = "20260822_84_dts_v2_domain_outbox"
REVISION_CONTRACT = "20260822_84a_lesson_region_contract"
REVISION_HEAD = "20260822_86_ops_case_v2"


def _postgres_tools_available() -> bool:
    return all(shutil.which(binary) for binary in POSTGRES_BINARIES)


def _run_alembic(backend_dir: Path, database_url: str, *args: str) -> None:
    environment = os.environ.copy()
    environment["APP_ENV"] = "test"
    environment["DATABASE_URL"] = database_url
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=backend_dir,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"alembic {' '.join(args)} failed\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def _stage_source_appoint(connection, *, region: str, course_id: str) -> None:
    connection.execute(text("SET LOCAL ROLE tit_dts_ingest_runtime"))
    connection.execute(
        text(
            """
            INSERT INTO public.dts_source_rows (
                source_region,source_table,source_key,source_key_data,
                dependency_keys,source_row,is_deleted,source_timestamp,
                last_record_id,source_position,last_topic,last_partition,
                last_offset,row_version
            ) VALUES (
                :region,:source_table,:course_id,
                jsonb_build_object('id',CAST(:source_id AS text)),
                '{}'::jsonb,
                jsonb_build_object('id',CAST(:source_id AS text)),
                false,1,1,'region-test','region-test',0,1,1
            )
            """
        ),
        {
            "region": region,
            "source_table": f"{region}_appoint",
            "course_id": course_id,
            "source_id": course_id,
        },
    )


def _insert_lesson(
    connection,
    *,
    course_id: str,
    source_region: str | None = None,
    context_region: str | None = None,
) -> None:
    connection.execute(text("SET LOCAL ROLE tit_dts_ingest_runtime"))
    if context_region is not None:
        connection.execute(
            text(
                "SELECT public.set_lesson_source_region_context_v1("
                ":source_region)"
            ),
            {"source_region": context_region},
        )
    if source_region is None:
        connection.execute(
            text(
                """
                INSERT INTO public.lesson_source_wide ("课程id","老师id")
                VALUES (:course_id,'REGION-TEST-TEACHER')
                """
            ),
            {"course_id": course_id},
        )
    else:
        connection.execute(
            text(
                """
                INSERT INTO public.lesson_source_wide (
                    source_region,"课程id","老师id"
                ) VALUES (:source_region,:course_id,'REGION-TEST-TEACHER')
                """
            ),
            {"source_region": source_region, "course_id": course_id},
        )


def _function_hash(connection, function_name: str) -> str:
    return connection.execute(
        text(
            """
            SELECT encode(
                sha256(convert_to(function_record.prosrc,'UTF8')),
                'hex'
            )
            FROM pg_proc AS function_record
            JOIN pg_namespace AS function_namespace
              ON function_namespace.oid=function_record.pronamespace
            WHERE function_namespace.nspname='public'
              AND function_record.proname=:function_name
              AND pg_get_function_identity_arguments(function_record.oid)=''
            """
        ),
        {"function_name": function_name},
    ).scalar_one()


@pytest.mark.skipif(
    not _postgres_tools_available(),
    reason="local PostgreSQL binaries are required for lesson-region checks",
)
def test_lesson_region_expand_manifest_contract_and_composite_identity(
    tmp_path: Path,
) -> None:
    backend_dir = Path(__file__).resolve().parents[1]
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

    database_url = URL.create(
        "postgresql+psycopg",
        username="postgres",
        host="127.0.0.1",
        port=postgres_port,
        database="postgres",
    ).render_as_string(hide_password=False)
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            for role_name in (
                "tit_growth_app",
                "tit_teacher_crud",
                "tit_dts_ingest_runtime",
                "tit_dts_domain_projector_runtime",
                "tit_dts_scope_coordinator_runtime",
                "tit_dts_outbox_worker_runtime",
                "tit_dts_outbox_recovery_runtime",
                "tit_dts_cutover_migration",
            ):
                connection.execute(
                    text(
                        f"CREATE ROLE {role_name} LOGIN NOINHERIT "
                        "NOSUPERUSER NOCREATEDB NOCREATEROLE "
                        "NOREPLICATION NOBYPASSRLS"
                    )
                )
            for role_name in (
                "tit_source_monitor",
                "tit_source_worker",
                "tide_business_app",
            ):
                connection.execute(
                    text(
                        f"CREATE ROLE {role_name} NOLOGIN NOINHERIT "
                        "NOSUPERUSER NOCREATEDB NOCREATEROLE "
                        "NOREPLICATION NOBYPASSRLS"
                    )
                )
        _run_alembic(
            backend_dir,
            database_url,
            "upgrade",
            "20260807_46_teacher_g01_source",
        )
        with engine.begin() as connection:
            _seed_external_personalized_catalog(connection)
        _run_alembic(backend_dir, database_url, "upgrade", REVISION_65)
        with engine.begin() as connection:
            _stage_source_appoint(
                connection, region="dom", course_id="REGION-LEGACY-1"
            )
            connection.execute(text("SET LOCAL ROLE tit_dts_ingest_runtime"))
            connection.execute(
                text(
                    """
                    INSERT INTO public.lesson_source_wide ("课程id","老师id")
                    VALUES ('REGION-LEGACY-1','REGION-TEST-TEACHER')
                    """
                )
            )

        _run_alembic(backend_dir, database_url, "upgrade", REVISION_EXPAND)
        with engine.begin() as connection:
            expand_hashes = {
                definition[0]: definition[-1]
                for definition in EXPANDED_DOMESTIC_PRIVACY_FUNCTIONS
            }
            assert _function_hash(
                connection, "guard_lesson_source_region_expand_v1"
            ) == expand_hashes["guard_lesson_source_region_expand_v1"]
            assert connection.execute(
                text(
                    """
                    SELECT source_region FROM public.lesson_source_wide
                    WHERE "课程id"='REGION-LEGACY-1'
                    """
                )
            ).scalar_one() is None
            _stage_source_appoint(
                connection, region="dom", course_id="REGION-EXPLICIT-1"
            )
            _insert_lesson(
                connection,
                course_id="REGION-EXPLICIT-1",
                source_region="dom",
            )
            _insert_lesson(
                connection,
                course_id="REGION-CONTEXT-1",
                context_region="ovs",
            )

        with pytest.raises(DBAPIError, match="COMPAT_REGION_MISSING"):
            with engine.begin() as connection:
                _stage_source_appoint(
                    connection, region="dom", course_id="REGION-MISSING-1"
                )
                _insert_lesson(connection, course_id="REGION-MISSING-1")

        with pytest.raises(DBAPIError, match="COMPAT_REGION_CONTEXT_MISMATCH"):
            with engine.begin() as connection:
                _insert_lesson(
                    connection,
                    course_id="REGION-MISMATCH-1",
                    source_region="dom",
                    context_region="ovs",
                )

        with engine.begin() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT "课程id",source_region
                    FROM public.lesson_source_wide
                    ORDER BY "课程id"
                    """
                )
            ).all()
            assert rows == [
                ("REGION-CONTEXT-1", "ovs"),
                ("REGION-EXPLICIT-1", "dom"),
                ("REGION-LEGACY-1", None),
            ]
            connection.execute(
                text(
                    """
                    SELECT public.stage_lesson_source_region_backfill_v1(
                        'REGION-LEGACY-1','dom','MANUAL_VERIFIED',
                        'TEST:AUTHORITATIVE-MANIFEST',:evidence_sha,'test-operator'
                    )
                    """
                ),
                {"evidence_sha": "a" * 64},
            )
            connection.execute(
                text(
                    """
                    SELECT public.confirm_lesson_region_compat_writer_v1(
                        'lesson-region-compat-v1',:smoke_sha,'test-operator'
                    )
                    """
                ),
                {"smoke_sha": "b" * 64},
            )
            # A pre-expand lesson produced a regionless v1 event.  Contract
            # migration may cross it only after an operator has proved it was
            # already consumed; it must never rewrite that historical event.
            connection.execute(
                text(
                    """
                    UPDATE public.outbox_events
                    SET status='PUBLISHED',published_at=clock_timestamp()
                    WHERE event_type='source_wide.changed.v1'
                    """
                )
            )

        _run_alembic(
            backend_dir, database_url, "upgrade", REVISION_PRE_CONTRACT
        )
        _run_alembic(backend_dir, database_url, "upgrade", REVISION_CONTRACT)

        with engine.begin() as connection:
            contract_hashes = {
                definition[0]: definition[-1]
                for definition in EXPECTED_DOMESTIC_PRIVACY_FUNCTIONS
            }
            assert _function_hash(
                connection, "guard_lesson_source_region_contract_v1"
            ) == contract_hashes["guard_lesson_source_region_contract_v1"]
            assert _function_hash(
                connection, "guard_dom_lesson_student_privacy_v1"
            ) == contract_hashes["guard_dom_lesson_student_privacy_v1"]
            assert connection.execute(
                text(
                    """
                    SELECT source_region FROM public.lesson_source_wide
                    WHERE "课程id"='REGION-LEGACY-1'
                    """
                )
            ).scalar_one() == "dom"
            assert connection.execute(
                text(
                    """
                    SELECT phase FROM public.lesson_source_region_migration_control
                    WHERE control_id='PRIMARY'
                    """
                )
            ).scalar_one() == "CONTRACTED"
            _insert_lesson(
                connection,
                course_id="REGION-LEGACY-1",
                source_region="ovs",
            )
            assert connection.execute(
                text(
                    """
                    SELECT array_agg(source_region ORDER BY source_region)
                    FROM public.lesson_source_wide
                    WHERE "课程id"='REGION-LEGACY-1'
                    """
                )
            ).scalar_one() == ["dom", "ovs"]
            primary_key_columns = connection.execute(
                text(
                    """
                    SELECT array_agg(attribute.attname ORDER BY key_column.ordinality)
                    FROM pg_constraint AS constraint_record
                    CROSS JOIN LATERAL unnest(constraint_record.conkey)
                        WITH ORDINALITY AS key_column(attnum,ordinality)
                    JOIN pg_attribute AS attribute
                      ON attribute.attrelid=constraint_record.conrelid
                     AND attribute.attnum=key_column.attnum
                    WHERE constraint_record.conrelid=
                          'public.lesson_source_wide'::regclass
                      AND constraint_record.contype='p'
                    """
                )
            ).scalar_one()
            assert primary_key_columns == ["source_region", "课程id"]

        # The three-state Outbox and Ops Case revisions must remain strictly
        # after the identity contract.  Stop at this task's audited tail so
        # later independently developed heads do not change this staged test.
        _run_alembic(backend_dir, database_url, "upgrade", REVISION_HEAD)
        with engine.begin() as connection:
            assert connection.execute(
                text("SELECT version_num FROM public.alembic_version")
            ).scalar_one() == REVISION_HEAD
    finally:
        engine.dispose()
        subprocess.run(
            [pg_ctl, "-D", str(data_dir), "-m", "fast", "-w", "stop"],
            check=False,
            capture_output=True,
            text=True,
        )
