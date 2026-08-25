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

from app import dts_ingest_store, dts_v2_shadow_source_writer
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
from scripts import run_dts_v2_runtime as runtime_runner
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
        public_101_102_sql = (
            backend_dir
            / "migrations"
            / "dms"
            / "20260825_public101_to_102_dts_ingest_batch_throughput.sql"
        )
        _execute_dms_onequery_file(psql_url, public_101_102_sql)
        public_102_or_103_104_sql = (
            backend_dir
            / "migrations"
            / "dms"
            / "20260825_public102_or_103_to_104_runtime_table_acl.sql"
        )
        _execute_dms_onequery_file(psql_url, public_102_or_103_104_sql)
        public_104_105_sql = (
            backend_dir
            / "migrations"
            / "dms"
            / "20260825_public104_to_105_runtime_pipeline_read_acl.sql"
        )
        _execute_dms_onequery_file(psql_url, public_104_105_sql)
        public_105_106_sql = (
            backend_dir
            / "migrations"
            / "dms"
            / "20260825_public105_to_106_dts_hot_indexes.sql"
        )
        _execute_dms_onequery_file(psql_url, public_105_106_sql)
        public_106_107_sql = (
            backend_dir
            / "migrations"
            / "dms"
            / "20260825_public106_to_107_domain_queue_read_acl.sql"
        )
        _execute_dms_onequery_file(psql_url, public_106_107_sql)
        _run_alembic(backend_dir, admin_url, "check")

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
            monkeypatch.setenv(
                "TIT_IRREVERSIBLE_QUALIFICATION_GRANTS_ENABLED", "false"
            )
            snapshot = runtime_runner._runtime_snapshot(
                application_engine,
                component=OUTBOX_COMPONENT,
                expected_database="tide_system_test",
                threshold=900,
            )
            assert snapshot.mode == "V2_PRIMARY"

        with admin_engine.connect() as connection:
            assert connection.execute(
                text("SELECT version_num FROM public.alembic_version")
            ).scalar_one() == "20260825_107_domain_queue_read_acl"
            assert connection.execute(
                text(
                    "SELECT has_table_privilege("
                    "'tit_growth_app','public.outbox_events','UPDATE')"
                )
            ).scalar_one() is True
            assert connection.execute(
                text(
                    "SELECT has_table_privilege("
                    "'tit_growth_app','public.dts_pipeline_control','SELECT')"
                )
            ).scalar_one() is True
            assert connection.execute(
                text(
                    "SELECT has_table_privilege("
                    "'tit_growth_app','public.dts_dirty_keys','SELECT')"
                )
            ).scalar_one() is True
            assert connection.execute(
                text(
                    "SELECT has_table_privilege("
                    "'tit_growth_app','public.dts_dirty_key_inputs','SELECT')"
                )
            ).scalar_one() is True
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
                ["SOURCE_CHANGE_WITHOUT_CURRENT_IGNORED"],
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
        repeated_identity_metrics = sink.consume_last_batch_metrics()
        assert repeated_identity_metrics["db_source_version_write_count"] == 2
        assert repeated_identity_metrics["db_source_current_write_count"] == 1
        assert repeated_identity_metrics["db_source_membership_write_count"] == 1
        assert repeated_identity_metrics["db_repeated_source_identity_count"] == 1
        assert repeated_identity_metrics["db_raw_dirty_command_count"] == 5
        assert repeated_identity_metrics["db_coalesced_dirty_command_count"] == 3
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

        with pytest.raises(
            Exception,
            match="DTS_V2_SOURCE_CURRENT_VERSION_MISMATCH",
        ):
            with admin_engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE public.dts_source_rows "
                        "SET source_payload_hash=repeat('f',64) "
                        "WHERE source_region='ovs' "
                        "AND source_table='ovs_appoint' AND source_key=:key"
                    ),
                    {"key": str(APPOINT_ID)},
                )
        with pytest.raises(
            Exception,
            match="DIRTY_REQUIRED_REVISION_MISMATCH",
        ):
            with admin_engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE public.dts_dirty_keys "
                        "SET required_work_revision="
                        "required_work_revision+1,row_version=row_version+1 "
                        "WHERE source_region='ovs' AND key_type='COURSE' "
                        "AND key_part_1=:key AND key_part_2=''"
                    ),
                    {"key": str(APPOINT_ID)},
                )

        flushed_batch_sizes: list[int] = []
        current_batch_sizes: list[int] = []
        membership_batch_sizes: list[int] = []
        dirty_batch_sizes: list[int] = []
        original_append_versions_batch = (
            dts_v2_shadow_source_writer._append_versions_batch
        )
        original_upsert_currents_batch = (
            dts_v2_shadow_source_writer._upsert_currents_batch
        )
        original_membership_batch = (
            dts_v2_shadow_source_writer._apply_cdc_membership_overlays_batch
        )
        original_dirty_batch = (
            sink._dirty_queue_store.enqueue_source_revisions_batch
        )

        def _record_append_versions_batch(*args: Any, **kwargs: Any) -> None:
            flushed_batch_sizes.append(len(kwargs["writes"]))
            original_append_versions_batch(*args, **kwargs)

        def _record_upsert_currents_batch(*args: Any, **kwargs: Any) -> None:
            current_batch_sizes.append(len(kwargs["writes"]))
            original_upsert_currents_batch(*args, **kwargs)

        def _record_membership_batch(*args: Any, **kwargs: Any) -> None:
            membership_batch_sizes.append(len(kwargs["writes"]))
            original_membership_batch(*args, **kwargs)

        def _record_dirty_batch(*args: Any, **kwargs: Any) -> Any:
            dirty_batch_sizes.append(len(kwargs["commands"]))
            return original_dirty_batch(*args, **kwargs)

        monkeypatch.setattr(
            dts_v2_shadow_source_writer,
            "_append_versions_batch",
            _record_append_versions_batch,
        )
        monkeypatch.setattr(
            dts_v2_shadow_source_writer,
            "_upsert_currents_batch",
            _record_upsert_currents_batch,
        )
        monkeypatch.setattr(
            dts_v2_shadow_source_writer,
            "_apply_cdc_membership_overlays_batch",
            _record_membership_batch,
        )
        monkeypatch.setattr(
            sink._dirty_queue_store,
            "enqueue_source_revisions_batch",
            _record_dirty_batch,
        )
        repeated_count = 100
        repeated_first_offset = 43
        repeated_updates = tuple(
            _appoint_event(
                repeated_first_offset + index,
                operation="UPDATE",
                before={"id": APPOINT_ID, "t_id": 60 + index},
                after={"id": APPOINT_ID, "t_id": 61 + index},
                complete=False,
            )
            for index in range(repeated_count)
        )
        assert sink.apply_batch(
            tuple(
                (event, DirtyKeySet(), None)
                for event in repeated_updates
            )
        ) == (False,) * repeated_count
        repeated_metrics = sink.consume_last_batch_metrics()
        assert flushed_batch_sizes == [repeated_count]
        assert current_batch_sizes == [1]
        assert membership_batch_sizes == [1]
        assert dirty_batch_sizes == [repeated_count + 2]
        assert repeated_metrics["db_source_version_write_count"] == repeated_count
        assert repeated_metrics["db_source_current_write_count"] == 1
        assert repeated_metrics["db_source_membership_write_count"] == 1
        assert repeated_metrics["db_repeated_source_identity_count"] == 1
        assert repeated_metrics["db_raw_dirty_command_count"] == (
            repeated_count * 3
        )
        assert repeated_metrics["db_coalesced_dirty_command_count"] == (
            repeated_count + 2
        )
        with admin_engine.connect() as connection:
            assert connection.execute(
                text(
                    "SELECT source_row_revision,row_version,"
                    "source_row ->> 't_id' "
                    "FROM public.dts_source_rows "
                    "WHERE source_region='ovs' "
                    "AND source_table='ovs_appoint' AND source_key=:key"
                ),
                {"key": str(APPOINT_ID)},
            ).one() == (repeated_count + 2, 2, "160")
        flushed_batch_sizes.clear()
        current_batch_sizes.clear()
        membership_batch_sizes.clear()
        dirty_batch_sizes.clear()

        net_delete_first_offset = repeated_first_offset + repeated_count
        net_delete_events = (
            _appoint_event(
                net_delete_first_offset,
                operation="UPDATE",
                before={"id": APPOINT_ID, "t_id": 160},
                after={"id": APPOINT_ID, "t_id": 170},
                complete=False,
            ),
            _appoint_event(
                net_delete_first_offset + 1,
                operation="DELETE",
                before=_row(170),
                after=None,
                complete=True,
            ),
        )
        assert sink.apply_batch(
            tuple(
                (event, DirtyKeySet(), None)
                for event in net_delete_events
            )
        ) == (False, False)
        net_delete_metrics = sink.consume_last_batch_metrics()
        assert flushed_batch_sizes == [2]
        assert current_batch_sizes == [1]
        assert membership_batch_sizes == [1]
        assert dirty_batch_sizes == [3]
        assert net_delete_metrics["db_source_version_write_count"] == 2
        assert net_delete_metrics["db_source_current_write_count"] == 1
        assert net_delete_metrics["db_source_membership_write_count"] == 1
        assert net_delete_metrics["db_raw_dirty_command_count"] == 5
        assert net_delete_metrics["db_coalesced_dirty_command_count"] == 3
        with admin_engine.connect() as connection:
            assert connection.execute(
                text(
                    "SELECT source_row_revision,row_version,is_deleted,"
                    "source_row ->> 't_id',dependency_keys -> 'teacher_ids' "
                    "FROM public.dts_source_rows "
                    "WHERE source_region='ovs' "
                    "AND source_table='ovs_appoint' AND source_key=:key"
                ),
                {"key": str(APPOINT_ID)},
            ).one() == (repeated_count + 4, 3, True, "170", ["160"])
        flushed_batch_sizes.clear()
        current_batch_sizes.clear()
        membership_batch_sizes.clear()
        dirty_batch_sizes.clear()

        bulk_count = 200
        bulk_first_offset = net_delete_first_offset + len(net_delete_events)
        bulk_first_appoint = APPOINT_ID + 10_000
        bulk_events = tuple(
            _appoint_event(
                bulk_first_offset + index,
                operation="INSERT",
                before=None,
                after=_row(
                    1_000 + index,
                    appoint_id=bulk_first_appoint + index,
                ),
                complete=True,
            )
            for index in range(bulk_count)
        )
        assert sink.apply_batch(
            tuple(
                (event, DirtyKeySet(), None) for event in bulk_events
            )
        ) == (False,) * bulk_count
        batch_metrics = sink.consume_last_batch_metrics()
        assert set(batch_metrics) == {
            "db_state_lock_elapsed_ms",
            "db_batch_prepare_elapsed_ms",
            "db_event_route_elapsed_ms",
            "db_source_flush_elapsed_ms",
            "db_dirty_elapsed_ms",
            "db_ledger_elapsed_ms",
            "db_checkpoint_elapsed_ms",
            "db_transaction_body_elapsed_ms",
            "db_source_version_elapsed_ms",
            "db_source_current_elapsed_ms",
            "db_source_membership_elapsed_ms",
            "db_source_version_write_count",
            "db_source_current_write_count",
            "db_source_membership_write_count",
            "db_repeated_source_identity_count",
            "db_raw_dirty_command_count",
            "db_coalesced_dirty_command_count",
        }
        assert all(
            isinstance(value, int) and value >= 0
            for value in batch_metrics.values()
        )
        assert batch_metrics["db_transaction_body_elapsed_ms"] >= max(
            batch_metrics["db_batch_prepare_elapsed_ms"],
            batch_metrics["db_source_flush_elapsed_ms"],
            batch_metrics["db_dirty_elapsed_ms"],
            batch_metrics["db_ledger_elapsed_ms"],
            batch_metrics["db_checkpoint_elapsed_ms"],
        )
        assert flushed_batch_sizes == [bulk_count]
        assert current_batch_sizes == [bulk_count]
        assert membership_batch_sizes == [bulk_count]
        assert dirty_batch_sizes == [bulk_count * 2]
        assert batch_metrics["db_source_version_write_count"] == bulk_count
        assert batch_metrics["db_source_current_write_count"] == bulk_count
        assert batch_metrics["db_source_membership_write_count"] == bulk_count
        assert batch_metrics["db_repeated_source_identity_count"] == 0
        assert batch_metrics["db_raw_dirty_command_count"] == bulk_count * 2
        assert batch_metrics["db_coalesced_dirty_command_count"] == bulk_count * 2

        with admin_engine.connect() as connection:
            assert connection.execute(
                text(
                    "SELECT next_offset FROM public.dts_ingest_checkpoints "
                    "WHERE source_region='ovs' AND topic=:topic "
                    "AND partition_id=0"
                ),
                {"topic": TOPIC},
            ).scalar_one() == bulk_first_offset + bulk_count
            assert connection.execute(
                text(
                    "SELECT count(*) FROM public.dts_source_row_versions "
                    "WHERE source_region='ovs' AND topic=:topic "
                    "AND partition_id=0 AND offset_value>=:first_offset "
                    "AND offset_value<:next_offset"
                ),
                {
                    "topic": TOPIC,
                    "first_offset": bulk_first_offset,
                    "next_offset": bulk_first_offset + bulk_count,
                },
            ).scalar_one() == bulk_count
            assert connection.execute(
                text(
                    "SELECT count(*) FROM public.dts_source_rows "
                    "WHERE source_region='ovs' "
                    "AND source_table='ovs_appoint' "
                    "AND source_key_numeric>=:first_appoint "
                    "AND source_key_numeric<:next_appoint "
                    "AND source_row_revision=1 "
                    "AND provenance_state='V2_CONFIRMED'"
                ),
                {
                    "first_appoint": bulk_first_appoint,
                    "next_appoint": bulk_first_appoint + bulk_count,
                },
            ).scalar_one() == bulk_count
            assert connection.execute(
                text(
                    "SELECT count(*) FROM public.dts_dirty_keys "
                    "WHERE source_region='ovs' AND key_type='COURSE' "
                    "AND key_part_1::numeric>=:first_appoint "
                    "AND key_part_1::numeric<:next_appoint"
                ),
                {
                    "first_appoint": bulk_first_appoint,
                    "next_appoint": bulk_first_appoint + bulk_count,
                },
            ).scalar_one() == bulk_count

        # Exercise the batch enqueue against live PROCESSING work.  New CDC
        # input must advance required work without stealing or clearing the
        # projector lease that was acquired before the batch committed.
        with application_engine.begin() as connection:
            claimed_count = len(
                connection.execute(
                    text(
                        "SELECT * FROM public.claim_domain_dirty_keys_v2("
                        "'batch-throughput-test',1000,300)"
                    )
                ).all()
            )
        assert claimed_count >= bulk_count

        bulk_update_first_offset = bulk_first_offset + bulk_count
        bulk_updates = tuple(
            _appoint_event(
                bulk_update_first_offset + index,
                operation="UPDATE",
                before={
                    "id": bulk_first_appoint + index,
                    "t_id": 1_000 + index,
                },
                after={
                    "id": bulk_first_appoint + index,
                    "t_id": 2_000 + index,
                },
                complete=False,
            )
            for index in range(bulk_count)
        )
        assert sink.apply_batch(
            tuple(
                (event, DirtyKeySet(), None) for event in bulk_updates
            )
        ) == (False,) * bulk_count
        assert flushed_batch_sizes == [bulk_count, bulk_count]
        assert current_batch_sizes == [bulk_count, bulk_count]
        assert membership_batch_sizes == [bulk_count, bulk_count]
        assert dirty_batch_sizes == [bulk_count * 2, bulk_count * 3]

        with admin_engine.connect() as connection:
            assert connection.execute(
                text(
                    "SELECT next_offset FROM public.dts_ingest_checkpoints "
                    "WHERE source_region='ovs' AND topic=:topic "
                    "AND partition_id=0"
                ),
                {"topic": TOPIC},
            ).scalar_one() == bulk_update_first_offset + bulk_count
            assert connection.execute(
                text(
                    "SELECT count(*) FROM public.dts_source_rows "
                    "WHERE source_region='ovs' "
                    "AND source_table='ovs_appoint' "
                    "AND source_key_numeric>=:first_appoint "
                    "AND source_key_numeric<:next_appoint "
                    "AND source_row_revision=2 "
                    "AND (source_row ->> 't_id')::numeric>=2000 "
                    "AND (source_row ->> 't_id')::numeric<:next_teacher"
                ),
                {
                    "first_appoint": bulk_first_appoint,
                    "next_appoint": bulk_first_appoint + bulk_count,
                    "next_teacher": 2_000 + bulk_count,
                },
            ).scalar_one() == bulk_count
            assert connection.execute(
                text(
                    "SELECT count(*) FROM public.dts_dirty_keys "
                    "WHERE source_region='ovs' AND key_type='COURSE' "
                    "AND key_part_1::numeric>=:first_appoint "
                    "AND key_part_1::numeric<:next_appoint "
                    "AND status='PROCESSING' "
                    "AND required_work_revision=2 "
                    "AND claimed_through_work_revision=1 "
                    "AND pending_event_count=2 "
                    "AND lease_owner='batch-throughput-test' "
                    "AND lease_token IS NOT NULL"
                ),
                {
                    "first_appoint": bulk_first_appoint,
                    "next_appoint": bulk_first_appoint + bulk_count,
                },
            ).scalar_one() == bulk_count

        bulk_delete_first_offset = bulk_update_first_offset + bulk_count
        bulk_deletes = tuple(
            _appoint_event(
                bulk_delete_first_offset + index,
                operation="DELETE",
                before=_row(
                    2_000 + index,
                    appoint_id=bulk_first_appoint + index,
                ),
                after=None,
                complete=True,
            )
            for index in range(bulk_count)
        )
        assert sink.apply_batch(
            tuple(
                (event, DirtyKeySet(), None) for event in bulk_deletes
            )
        ) == (False,) * bulk_count
        assert flushed_batch_sizes == [
            bulk_count,
            bulk_count,
            bulk_count,
        ]
        assert current_batch_sizes == flushed_batch_sizes
        assert membership_batch_sizes == flushed_batch_sizes
        assert dirty_batch_sizes == [
            bulk_count * 2,
            bulk_count * 3,
            bulk_count * 2,
        ]

        with admin_engine.connect() as connection:
            assert connection.execute(
                text(
                    "SELECT next_offset FROM public.dts_ingest_checkpoints "
                    "WHERE source_region='ovs' AND topic=:topic "
                    "AND partition_id=0"
                ),
                {"topic": TOPIC},
            ).scalar_one() == bulk_delete_first_offset + bulk_count
            assert connection.execute(
                text(
                    "SELECT count(*) FROM public.dts_source_rows "
                    "WHERE source_region='ovs' "
                    "AND source_table='ovs_appoint' "
                    "AND source_key_numeric>=:first_appoint "
                    "AND source_key_numeric<:next_appoint "
                    "AND source_row_revision=3 AND is_deleted IS TRUE"
                ),
                {
                    "first_appoint": bulk_first_appoint,
                    "next_appoint": bulk_first_appoint + bulk_count,
                },
            ).scalar_one() == bulk_count

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
