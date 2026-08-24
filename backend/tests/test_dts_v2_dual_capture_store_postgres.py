from __future__ import annotations

from pathlib import Path
import shutil
import socket
import subprocess
from typing import Any

import psycopg
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

from app import dts_ingest_store
from app.dts_ingest_store import DtsIngestDatabaseSettings
from app.dts_source_consumer import DirtyKeySet, DtsChangeEvent
from app.dts_source_contract_v2 import with_v2_source_image_completeness
from app.dts_v2_dual_capture_store import (
    DtsV2DualCaptureStoreError,
    PostgresDtsSourceEventSink,
)
from app.dts_v2_runtime_composition import (
    DOMAIN_COMPONENT,
    FAVORITE_COMPONENT,
    OUTBOX_COMPONENT,
    validate_runtime_startup,
)
from dts_v2_test_profiles import SYNTHETIC_APPOINT_SOURCE_FIELDS
from test_dts_v2_source_current_guards_postgres import (
    _postgres_tools_available,
    _run_alembic,
    _seed_external_personalized_catalog,
)
from test_mr60_dms_sql_postgres import _split_dms_onequery_statements


pytestmark = pytest.mark.usefixtures("synthetic_v2_appoint_profiles")

TOPIC = "topic-single-pipeline"
CONSUMER_GROUP = "group-single-pipeline"
EPOCH_ID = "epoch-single-pipeline-20260824"
START_TIMESTAMP = 1_787_500_000
APPOINT_ID = 9001


def _execute_dms_onequery_file(database_url: str, sql_path: Path) -> None:
    statements = _split_dms_onequery_statements(
        sql_path.read_text(encoding="utf-8")
    )
    with psycopg.connect(database_url, autocommit=True) as connection:
        for statement_number, statement in enumerate(statements, start=1):
            try:
                connection.execute(statement)
            except Exception as error:
                raise AssertionError(
                    "DMS onequery statement "
                    f"{statement_number} failed: {statement[:160]}"
                ) from error


def _row(teacher_id: int, *, appoint_id: int = APPOINT_ID) -> dict[str, Any]:
    row = {field: None for field in SYNTHETIC_APPOINT_SOURCE_FIELDS}
    row.update(
        {
            "id": appoint_id,
            "t_id": teacher_id,
            "status": "on",
            "use_point": "buy",
        }
    )
    return row


def _appoint_event(
    offset: int,
    *,
    operation: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    complete: bool,
    partition: int = 0,
    source_timestamp: int | None = None,
) -> DtsChangeEvent:
    event = DtsChangeEvent(
        source_region="ovs",
        topic=TOPIC,
        partition=partition,
        offset=offset,
        record_id=10_000 + offset,
        source_timestamp=(
            START_TIMESTAMP + offset
            if source_timestamp is None
            else source_timestamp
        ),
        source_txid=f"tx-{partition}-{offset}",
        source_position=f"position-{partition}-{offset}",
        operation=operation,
        database_name="source",
        schema_name="public",
        table_name="ovs_appoint",
        before=before,
        after=after,
        source_field_types={
            "id": "NUMERIC",
            "t_id": "NUMERIC",
            **({"status": "TEXT", "use_point": "TEXT"} if complete else {}),
        },
    )
    event = with_v2_source_image_completeness(event)
    assert event.source_images_complete is complete
    return event


@pytest.mark.skipif(
    not _postgres_tools_available(),
    reason="local PostgreSQL binaries are required for single-pipeline ingest",
)
def test_single_pipeline_first_event_missing_update_and_insert(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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

    postgres_url = URL.create(
        "postgresql+psycopg",
        username="postgres",
        host="127.0.0.1",
        port=postgres_port,
        database="postgres",
    ).render_as_string(hide_password=False)
    bootstrap_engine = create_engine(postgres_url)
    admin_engine = None
    runtime_engine = None
    application_engine = None
    try:
        with bootstrap_engine.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        ) as connection:
            connection.execute(text("CREATE DATABASE tide_system_test"))
        with bootstrap_engine.begin() as connection:
            for role_name in (
                "tit_growth_app",
                "tit_teacher_crud",
                "tit_dts_ingest_runtime",
                "tide_support_ticket_owner",
            ):
                connection.execute(
                    text(
                        f"CREATE ROLE {role_name} LOGIN NOINHERIT "
                        "NOSUPERUSER NOCREATEDB NOCREATEROLE "
                        "NOREPLICATION NOBYPASSRLS"
                    )
                )
        admin_url = URL.create(
            "postgresql+psycopg",
            username="postgres",
            host="127.0.0.1",
            port=postgres_port,
            database="tide_system_test",
        ).render_as_string(hide_password=False)
        admin_engine = create_engine(admin_url)
        _run_alembic(
            backend_dir,
            admin_url,
            "upgrade",
            "20260807_46_teacher_g01_source",
        )
        with admin_engine.begin() as connection:
            _seed_external_personalized_catalog(connection)
        _run_alembic(
            backend_dir,
            admin_url,
            "upgrade",
            "20260819_65_g09_set_course",
        )
        with admin_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO public.outbox_events(
                      outbox_id,event_id,aggregate_type,aggregate_id,event_type,
                      payload,status,available_at,attempt_count,last_error,
                      created_at,published_at
                    ) VALUES (
                      'LEGACY-INVALID-STATE','LEGACY-INVALID-STATE',
                      'LEGACY','LEGACY','legacy.invalid.v1','{}'::jsonb,
                      'PUBLISHED',clock_timestamp(),0,NULL,
                      clock_timestamp(),NULL
                    )
                    """
                )
            )
            connection.execute(text("CREATE SCHEMA IF NOT EXISTS tide"))
            connection.execute(
                text(
                    """
                    CREATE TABLE tide.schema_migrations (
                      migration_id text PRIMARY KEY,
                      migration_order integer NOT NULL UNIQUE,
                      filename text NOT NULL,
                      sha256 char(64) NOT NULL,
                      applied_at timestamptz NOT NULL DEFAULT now(),
                      applied_by text NOT NULL DEFAULT current_user
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO tide.schema_migrations(
                      migration_id,migration_order,filename,sha256
                    )
                    SELECT
                      'fixture_'||lpad(value::text,4,'0'),
                      value,
                      'fixture_'||value||'.up.sql',
                      repeat('a',64)
                    FROM generate_series(1,36) AS value
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO tide.schema_migrations(
                      migration_id,migration_order,filename,sha256
                    ) VALUES (
                      '0042_g09_set_kuozhi_course',37,
                      '0042_g09_set_kuozhi_course.up.sql',
                      repeat('b',64)
                    )
                    """
                )
            )
        psql_url = admin_url.replace(
            "postgresql+psycopg://", "postgresql://"
        )
        public_65_100_sql = (
            backend_dir
            / "migrations"
            / "dms"
            / "20260824_public65_to_100_dts_domain_schema.sql"
        )
        _execute_dms_onequery_file(psql_url, public_65_100_sql)
        with admin_engine.begin() as connection:
            assert connection.execute(
                text("SELECT version_num FROM public.alembic_version")
            ).scalar_one() == "20260823_100_scope_snapshot_diff"
            assert connection.execute(
                text(
                    "SELECT count(*) FROM public.outbox_events "
                    "WHERE event_id='LEGACY-INVALID-STATE'"
                )
            ).scalar_one() == 0
            connection.execute(
                text(
                    """
                    INSERT INTO tide.schema_migrations(
                      migration_id,migration_order,filename,sha256
                    ) VALUES (
                      '0043_p_rel_execution_catalog',38,
                      '0043_p_rel_execution_catalog.up.sql',
                      '0bb25fd49de5aac915dfb9a4e52ad567183a97865b4fc4dfd6d0a35d660492bf'
                    )
                    """
                )
            )
        public_100_101_sql = (
            backend_dir
            / "migrations"
            / "dms"
            / "20260824_public100_to_101_single_pipeline_reset.sql"
        )
        _execute_dms_onequery_file(psql_url, public_100_101_sql)
        _run_alembic(backend_dir, admin_url, "check")

        with admin_engine.begin() as connection:
            assert connection.execute(
                text(
                    "SELECT has_table_privilege("
                    "'tit_dts_ingest_runtime',"
                    "'public.dts_dirty_keys','SELECT')"
                )
            ).scalar_one() is True
            connection.execute(
                text(
                    "REVOKE SELECT ON TABLE public.dts_dirty_keys, "
                    "public.lesson_source_wide FROM tit_dts_ingest_runtime"
                )
            )
        dom_privacy_acl_hotfix_sql = (
            backend_dir
            / "migrations"
            / "dms"
            / "20260824_public101_dom_privacy_read_acl_hotfix.sql"
        )
        _execute_dms_onequery_file(psql_url, dom_privacy_acl_hotfix_sql)

        application_url = URL.create(
            "postgresql+psycopg",
            username="tit_growth_app",
            host="127.0.0.1",
            port=postgres_port,
            database="tide_system_test",
            query={"sslmode": "disable", "options": "-c search_path=public"},
        ).render_as_string(hide_password=False)
        application_engine = create_engine(application_url)
        with application_engine.connect() as connection:
            for component in (
                DOMAIN_COMPONENT,
                OUTBOX_COMPONENT,
                FAVORITE_COMPONENT,
            ):
                validate_runtime_startup(
                    connection,
                    component=component,
                    expected_database="tide_system_test",
                )

        with admin_engine.connect() as connection:
            assert connection.execute(
                text("SELECT version_num FROM public.alembic_version")
            ).scalar_one() == "20260824_101_dts_single_pipeline_reset"
            profile_manifest_sha256 = connection.execute(
                text(
                    "SELECT source_profile_manifest_sha256 "
                    "FROM public.dts_pipeline_reset_audits"
                )
            ).scalar_one()
            assert connection.execute(
                text("SELECT count(*) FROM public.dts_ingest_checkpoints")
            ).scalar_one() == 0
            assert connection.execute(
                text("SELECT count(*) FROM public.dts_source_rows")
            ).scalar_one() == 0
            assert connection.execute(
                text(
                    "SELECT has_function_privilege("
                    "'tit_dts_ingest_runtime',"
                    "'public.initialize_dts_event_stream_v1("
                    "text,text,integer,text,text,bigint,bigint,text,text)',"
                    "'EXECUTE')"
                )
            ).scalar_one() is True
            assert connection.execute(
                text(
                    "SELECT has_function_privilege("
                    "'tit_dts_ingest_runtime',"
                    "'public.claim_v1_compat_dirty_key_v1(text)',"
                    "'EXECUTE')"
                )
            ).scalar_one() is False

        runtime_url = URL.create(
            "postgresql+psycopg",
            username="tit_dts_ingest_runtime",
            host="127.0.0.1",
            port=postgres_port,
            database="tide_system_test",
            query={"sslmode": "disable", "options": "-c search_path=public"},
        ).render_as_string(hide_password=False)
        runtime_engine = create_engine(runtime_url)
        monkeypatch.setattr(
            dts_ingest_store, "APPROVED_INSECURE_PRE_HOST", "127.0.0.1"
        )
        monkeypatch.setattr(
            dts_ingest_store, "APPROVED_INSECURE_PRE_PORT", postgres_port
        )
        settings = DtsIngestDatabaseSettings(
            host="127.0.0.1",
            port=postgres_port,
            password="unused-under-local-trust",
            sslmode="disable",
            allow_insecure_db=True,
        )
        sink = PostgresDtsSourceEventSink(
            settings,
            source_region="ovs",
            source_partition_epoch_id=EPOCH_ID,
            consumer_group=CONSUMER_GROUP,
            start_timestamp_seconds=START_TIMESTAMP,
            source_profile_manifest_sha256=profile_manifest_sha256,
            engine=runtime_engine,
        )

        # Empty reset state has no synthetic offset.  The first accepted event
        # creates H0 from its real offset and source timestamp.
        assert sink.validate_startup(
            source_region="ovs", topic=TOPIC, partition=0
        ) is None

        missing_update = _appoint_event(
            40,
            operation="UPDATE",
            before={"id": APPOINT_ID, "t_id": 10},
            after={"id": APPOINT_ID, "t_id": 20},
            complete=False,
        )
        assert sink.apply(
            missing_update,
            DirtyKeySet(course_ids=frozenset({str(APPOINT_ID)})),
            None,
        ) is False
        with admin_engine.connect() as connection:
            assert connection.execute(
                text(
                    "SELECT next_offset,source_timestamp,"
                    "source_partition_epoch_id,consumer_group "
                    "FROM public.dts_ingest_checkpoints "
                    "WHERE source_region='ovs' AND topic=:topic "
                    "AND partition_id=0"
                ),
                {"topic": TOPIC},
            ).one() == (
                41,
                START_TIMESTAMP + 40,
                EPOCH_ID,
                CONSUMER_GROUP,
            )
            assert connection.execute(
                text(
                    "SELECT route_status,dirty_key_count,issue_codes "
                    "FROM public.dts_ingest_events WHERE offset_value=40"
                )
            ).one() == (
                "IGNORED",
                0,
                ["COURSE_UPDATE_WITHOUT_CURRENT_IGNORED"],
            )
            assert connection.execute(
                text("SELECT count(*) FROM public.dts_source_rows")
            ).scalar_one() == 0
            assert connection.execute(
                text("SELECT count(*) FROM public.dts_dirty_keys")
            ).scalar_one() == 0

        insert_event = _appoint_event(
            41,
            operation="INSERT",
            before=None,
            after=_row(50),
            complete=True,
        )
        teacher_change = _appoint_event(
            42,
            operation="UPDATE",
            before={"id": APPOINT_ID, "t_id": 50},
            after={"id": APPOINT_ID, "t_id": 60},
            complete=False,
        )
        assert sink.apply_batch(
            (
                (insert_event, DirtyKeySet(), None),
                (teacher_change, DirtyKeySet(), None),
            )
        ) == (False, False)
        assert sink.apply(missing_update, DirtyKeySet(), None) is True

        with admin_engine.connect() as connection:
            assert connection.execute(
                text(
                    "SELECT source_row_revision,source_row ->> 't_id' "
                    "FROM public.dts_source_rows "
                    "WHERE source_region='ovs' "
                    "AND source_table='ovs_appoint' AND source_key=:key"
                ),
                {"key": str(APPOINT_ID)},
            ).one() == (2, "60")
            assert connection.execute(
                text(
                    "SELECT source_row_revision,operation "
                    "FROM public.dts_source_row_versions "
                    "WHERE source_region='ovs' "
                    "AND source_table='ovs_appoint' AND source_key=:key "
                    "ORDER BY source_row_revision"
                ),
                {"key": str(APPOINT_ID)},
            ).all() == [(1, "INSERT"), (2, "UPDATE")]
            assert connection.execute(
                text(
                    "SELECT next_offset FROM public.dts_ingest_checkpoints "
                    "WHERE source_region='ovs' AND topic=:topic "
                    "AND partition_id=0"
                ),
                {"topic": TOPIC},
            ).scalar_one() == 43
            assert connection.execute(
                text(
                    "SELECT key_type,key_part_1 FROM public.dts_dirty_keys "
                    "ORDER BY key_type,key_part_1"
                )
            ).all() == [
                ("COURSE", str(APPOINT_ID)),
                ("TEACHER", "50"),
                ("TEACHER", "60"),
            ]

        before_start = _appoint_event(
            0,
            operation="INSERT",
            before=None,
            after=_row(70, appoint_id=APPOINT_ID + 1),
            complete=True,
            partition=1,
            source_timestamp=START_TIMESTAMP - 1,
        )
        with pytest.raises(
            DtsV2DualCaptureStoreError,
            match="DTS_EVENT_BEFORE_CONFIGURED_START",
        ):
            sink.apply(before_start, DirtyKeySet(), None)
        with admin_engine.connect() as connection:
            assert connection.execute(
                text(
                    "SELECT count(*) FROM public.dts_ingest_checkpoints "
                    "WHERE source_region='ovs' AND topic=:topic "
                    "AND partition_id=1"
                ),
                {"topic": TOPIC},
            ).scalar_one() == 0

        dom_topic = "dom-topic-v2"
        dom_sink = PostgresDtsSourceEventSink(
            settings,
            source_region="dom",
            source_partition_epoch_id="dom-epoch-fresh-001",
            consumer_group="dom-consumer-fresh-001",
            start_timestamp_seconds=START_TIMESTAMP,
            source_profile_manifest_sha256=profile_manifest_sha256,
            engine=runtime_engine,
        )
        assert dom_sink.validate_startup(
            source_region="dom", topic=dom_topic, partition=0
        ) is None
        dom_sink.validate_domestic_student_privacy_state()
        dom_sink.validate_domestic_student_privacy_contract(
            key_fingerprint="a" * 64,
            topic=dom_topic,
            partition=0,
        )
    finally:
        if application_engine is not None:
            application_engine.dispose()
        if runtime_engine is not None:
            runtime_engine.dispose()
        if admin_engine is not None:
            admin_engine.dispose()
        bootstrap_engine.dispose()
        subprocess.run(
            [pg_ctl, "-D", str(data_dir), "-m", "immediate", "-w", "stop"],
            check=True,
            capture_output=True,
            text=True,
        )
