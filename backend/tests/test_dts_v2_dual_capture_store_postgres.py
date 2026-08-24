from __future__ import annotations

import json
from pathlib import Path
import shutil
import socket
import subprocess
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import DBAPIError

from app import dts_ingest_store
from app.dts_ingest_store import DtsIngestDatabaseSettings
from app.dts_source_consumer import DirtyKeySet, DtsChangeEvent
from app.dts_source_contract_v2 import with_v2_source_image_completeness
from app.dts_v2_dirty_queue_store import DtsV2DirtyQueueStore
from app.dts_v2_dual_capture_store import PostgresDtsV2DualCaptureSink
from dts_v2_test_profiles import SYNTHETIC_APPOINT_SOURCE_FIELDS
from test_dts_v2_source_current_guards_postgres import (
    _postgres_tools_available,
    _run_alembic,
    _seed_external_personalized_catalog,
)


pytestmark = pytest.mark.usefixtures("synthetic_v2_appoint_profiles")

TOPIC = "topic-v2-dual-fresh-head"
CONSUMER_GROUP = "group-v2-dual-fresh-head"
CONTROL_GROUP = "fleet-v2-dual-fresh-head"
EPOCH_OPENING_ID = "opening-v2-dual-fresh-head"
STREAM_GENERATION_ID = "generation-v2-dual-fresh-head"
APPOINT_ID = 9001


class _FailSecondEnqueue:
    def __init__(self) -> None:
        self._delegate = DtsV2DirtyQueueStore()
        self._calls = 0

    def enqueue_source_revision(self, connection, **kwargs):
        self._calls += 1
        if self._calls == 2:
            raise RuntimeError("synthetic-enqueue-failure")
        return self._delegate.enqueue_source_revision(connection, **kwargs)


def _row(teacher_id: int) -> dict[str, Any]:
    row = {field: None for field in SYNTHETIC_APPOINT_SOURCE_FIELDS}
    row.update(
        {
            "id": APPOINT_ID,
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
) -> DtsChangeEvent:
    event = DtsChangeEvent(
        source_region="ovs",
        topic=TOPIC,
        partition=0,
        offset=offset,
        record_id=10_000 + offset,
        source_timestamp=1_787_500_000 + offset,
        source_txid=f"tx-{offset}",
        source_position=f"opaque-position-{offset}",
        operation=operation,
        database_name="source",
        schema_name="public",
        table_name="ovs_appoint",
        before=before,
        after=after,
        source_field_types={
            "id": "NUMERIC",
            "t_id": "NUMERIC",
            **(
                {"status": "TEXT", "use_point": "TEXT"}
                if complete
                else {}
            ),
        },
    )
    event = with_v2_source_image_completeness(event)
    assert event.source_images_complete is complete
    return event


def _ignored_event(
    offset: int,
    *,
    operation: str,
    table_name: str | None,
) -> DtsChangeEvent:
    return DtsChangeEvent(
        source_region="ovs",
        topic=TOPIC,
        partition=0,
        offset=offset,
        record_id=10_000 + offset,
        source_timestamp=1_787_500_000 + offset,
        source_txid=f"tx-{offset}",
        source_position=f"opaque-position-{offset}",
        operation=operation,
        database_name="source",
        schema_name="public",
        table_name=table_name,
        before=(None if operation == "HEARTBEAT" else {"id": 77}),
        after=(None if operation == "HEARTBEAT" else {"id": 77}),
        source_field_types={"id": "NUMERIC"},
    )


def _bootstrap_epoch(connection) -> str:
    route = {
        "source_region": "ovs",
        "topic": TOPIC,
        "partition_id": 0,
        "current_next_offset": 0,
        "consumer_group": CONSUMER_GROUP,
        "stream_generation_id": STREAM_GENERATION_ID,
        "epoch_opening_id": EPOCH_OPENING_ID,
    }
    epoch_id = str(
        connection.execute(
            text(
                """
                SELECT public.dts_broker_epoch_id_v2(
                    :source_region,:topic,:partition_id,
                    :stream_generation_id,:epoch_opening_id
                )
                """
            ),
            route,
        ).scalar_one()
    )
    route["source_partition_epoch_id"] = epoch_id
    routes = json.dumps([route], separators=(",", ":"))
    vector_hash = str(
        connection.execute(
            text(
                """
                SELECT public.dts_initial_broker_epoch_vector_hash_v2(
                    CAST(:routes AS jsonb)
                )
                """
            ),
            {"routes": routes},
        ).scalar_one()
    )
    result = connection.execute(
        text(
            """
            SELECT public.bootstrap_initial_broker_epoch_v2(
                'run-v2-dual-fresh-head',:control_group,
                CAST(:routes AS jsonb),:vector_hash
            )
            """
        ),
        {
            "control_group": CONTROL_GROUP,
            "routes": routes,
            "vector_hash": vector_hash,
        },
    ).scalar_one()
    assert result["status"] == "APPLIED"
    return epoch_id


@pytest.mark.skipif(
    not _postgres_tools_available(),
    reason="local PostgreSQL binaries are required for fresh-head dual capture",
)
def test_fresh_head_dual_capture_is_atomic_replay_safe_and_least_privilege(
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
    cutover_engine = None
    outbox_engine = None
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
                "tit_dts_scope_coordinator_runtime",
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
                        f"CREATE ROLE {role_name} NOLOGIN NOSUPERUSER "
                        "NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
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
            "head",
        )
        with admin_engine.begin() as connection:
            assert connection.execute(
                text("SELECT version_num FROM public.alembic_version")
            ).scalar_one() == "20260823_100_scope_snapshot_diff"
            connection.execute(
                text(
                    """
                    INSERT INTO public.dts_ingest_checkpoints (
                        source_region,topic,partition_id,next_offset,
                        source_timestamp,source_position
                    ) VALUES ('ovs',:topic,0,0,0,'bootstrap')
                    """
                ),
                {"topic": TOPIC},
            )
            epoch_id = _bootstrap_epoch(connection)

            acl = connection.execute(
                text(
                    """
                    SELECT
                      has_table_privilege(
                        'tit_dts_ingest_runtime',
                        'public.dts_source_row_versions','INSERT'
                      ),
                      has_table_privilege(
                        'tit_dts_ingest_runtime',
                        'public.dts_source_partition_epochs','SELECT'
                      ),
                      has_table_privilege(
                        'tit_dts_ingest_runtime',
                        'public.dts_source_partition_epochs','UPDATE'
                      ),
                      has_table_privilege(
                        'tit_dts_ingest_runtime',
                        'public.dts_dirty_keys','INSERT'
                      ),
                      has_function_privilege(
                        'tit_dts_ingest_runtime',
                        'public.enqueue_dirty_from_source_revision_v2('
                        'text,text,text,bigint,text,text,text)','EXECUTE'
                      ),
                      has_function_privilege(
                        'tit_dts_ingest_runtime',
                        'public._upsert_dts_dirty_key_input_v2('
                        'text,text,text,text,text,jsonb,bigint,text)','EXECUTE'
                      )
                    """
                )
            ).one()
            assert tuple(acl) == (True, True, False, False, True, False)

        runtime_url = URL.create(
            "postgresql+psycopg",
            username="tit_dts_ingest_runtime",
            host="127.0.0.1",
            port=postgres_port,
            database="tide_system_test",
            query={"sslmode": "disable", "options": "-c search_path=public"},
        ).render_as_string(hide_password=False)
        runtime_engine = create_engine(runtime_url)
        cutover_url = URL.create(
            "postgresql+psycopg",
            username="tit_dts_projection_cutover_runtime",
            host="127.0.0.1",
            port=postgres_port,
            database="tide_system_test",
            query={"sslmode": "disable", "options": "-c search_path=public"},
        ).render_as_string(hide_password=False)
        cutover_engine = create_engine(cutover_url)
        outbox_url = URL.create(
            "postgresql+psycopg",
            username="tit_dts_outbox_worker_runtime",
            host="127.0.0.1",
            port=postgres_port,
            database="tide_system_test",
            query={"sslmode": "disable", "options": "-c search_path=public"},
        ).render_as_string(hide_password=False)
        outbox_engine = create_engine(outbox_url)
        monkeypatch.setattr(
            dts_ingest_store,
            "APPROVED_INSECURE_PRE_HOST",
            "127.0.0.1",
        )
        monkeypatch.setattr(
            dts_ingest_store,
            "APPROVED_INSECURE_PRE_PORT",
            postgres_port,
        )
        settings = DtsIngestDatabaseSettings(
            host="127.0.0.1",
            port=postgres_port,
            password="unused-under-local-trust",
            sslmode="disable",
            allow_insecure_db=True,
        )
        sink = PostgresDtsV2DualCaptureSink(
            settings,
            source_region="ovs",
            source_partition_epoch_id=epoch_id,
            consumer_group=CONSUMER_GROUP,
            control_group=CONTROL_GROUP,
            engine=runtime_engine,
        )
        assert sink.validate_startup(
            source_region="ovs", topic=TOPIC, partition=0
        ).next_offset == 0

        row_a = _row(10)
        insert_a = _appoint_event(
            0,
            operation="INSERT",
            before=None,
            after=row_a,
            complete=True,
        )
        update_b = _appoint_event(
            1,
            operation="UPDATE",
            before={"id": APPOINT_ID, "t_id": 10},
            after={"id": APPOINT_ID, "t_id": 20},
            complete=False,
        )
        update_a = _appoint_event(
            2,
            operation="UPDATE",
            before={"id": APPOINT_ID, "t_id": 20},
            after={"id": APPOINT_ID, "t_id": 10},
            complete=False,
        )
        batch = tuple(
            (event, DirtyKeySet(course_ids=frozenset({"legacy-hint"})), None)
            for event in (insert_a, update_b, update_a)
        )
        assert sink.apply_batch(batch) == (False, False, False)

        with admin_engine.connect() as connection:
            assert connection.execute(
                text(
                    """
                    SELECT source_row_revision,source_row ->> 't_id'
                    FROM public.dts_source_rows
                    WHERE source_region='ovs' AND source_table='ovs_appoint'
                      AND source_key=:source_key
                    """
                ),
                {"source_key": str(APPOINT_ID)},
            ).one() == (3, "10")
            assert connection.execute(
                text(
                    """
                    SELECT source_row_revision,operation
                    FROM public.dts_source_row_versions
                    WHERE source_region='ovs' AND source_table='ovs_appoint'
                      AND source_key=:source_key
                    ORDER BY source_row_revision
                    """
                ),
                {"source_key": str(APPOINT_ID)},
            ).all() == [(1, "INSERT"), (2, "UPDATE"), (3, "UPDATE")]
            assert connection.execute(
                text(
                    """
                    SELECT offset_value,route_status,dirty_key_count,issue_codes
                    FROM public.dts_ingest_events ORDER BY offset_value
                    """
                )
            ).all() == [
                (0, "PROCESSED", 2, []),
                (1, "PROCESSED", 3, []),
                (2, "PROCESSED", 3, []),
            ]
            assert connection.execute(
                text("SELECT count(*) FROM public.dts_dirty_key_inputs")
            ).scalar_one() == 8
            assert connection.execute(
                text(
                    """
                    SELECT key_type,key_part_1,status,compat_status,
                           required_work_revision,
                           compat_required_work_revision
                    FROM public.dts_dirty_keys
                    ORDER BY key_type,key_part_1
                    """
                )
            ).all() == [
                ("COURSE", str(APPOINT_ID), "PENDING", "PENDING", 3, 3),
                ("TEACHER", "10", "PENDING", "PENDING", 3, 3),
                ("TEACHER", "20", "PENDING", "PENDING", 2, 2),
            ]
            assert connection.execute(
                text(
                    """
                    SELECT next_offset,checkpoint_row_version
                    FROM public.dts_ingest_checkpoints
                    WHERE source_region='ovs' AND topic=:topic
                      AND partition_id=0
                    """
                ),
                {"topic": TOPIC},
            ).one() == (3, 4)

        # One input fans out to independent compatibility and V2 states.  A
        # compatibility claim may not survive its transaction: projection and
        # completion share one transaction so a worker crash rolls both back.
        with pytest.raises(DBAPIError, match="CLAIM_MUST_COMPLETE"):
            with runtime_engine.begin() as connection:
                connection.execute(
                    text(
                        "SELECT * FROM public.claim_v1_compat_dirty_key_v1("
                        "'crash-before-complete')"
                    )
                ).mappings().one()
        with admin_engine.connect() as connection:
            assert connection.execute(
                text(
                    """
                    SELECT count(*) FROM public.dts_dirty_keys
                    WHERE status='PENDING' AND compat_status='PENDING'
                    """
                )
            ).scalar_one() == 3

        # A compatibility completion must not consume or mutate V2 work.
        with runtime_engine.begin() as connection:
            claim = connection.execute(
                text(
                    "SELECT * FROM public.claim_v1_compat_dirty_key_v1("
                    "'compat-pg-test')"
                )
            ).mappings().one()
            identity = {
                "source_region": claim["source_region"],
                "key_type": claim["key_type"],
                "key_part_1": claim["key_part_1"],
                "key_part_2": claim["key_part_2"],
            }
            completed = connection.execute(
                text(
                    """
                    SELECT public.complete_v1_compat_dirty_key_v1(
                      'compat-pg-test',:source_region,:key_type,
                      :key_part_1,:key_part_2,:claimed_revision,:row_version
                    )
                    """
                ),
                {
                    **identity,
                    "claimed_revision": claim[
                        "compat_claimed_work_revision"
                    ],
                    "row_version": claim["compat_row_version"],
                },
            ).scalar_one()
            assert completed["status"] == "COMPLETED"
            assert connection.execute(
                text(
                    "SELECT public.dts_v1_compat_dirty_not_complete_count_v1()"
                )
            ).scalar_one() == 2
        with admin_engine.connect() as connection:
            assert connection.execute(
                text(
                    """
                    SELECT status,compat_status FROM public.dts_dirty_keys
                    WHERE source_region=:source_region AND key_type=:key_type
                      AND key_part_1=:key_part_1 AND key_part_2=:key_part_2
                    """
                ),
                identity,
            ).one() == ("PENDING", "COMPLETED")
            recovery_identity = dict(
                connection.execute(
                    text(
                        """
                        SELECT source_region,key_type,key_part_1,key_part_2
                        FROM public.dts_dirty_keys
                        WHERE compat_status='PENDING'
                        ORDER BY key_type,key_part_1 LIMIT 1
                        """
                    )
                ).mappings().one()
            )
        with runtime_engine.begin() as connection:
            failed = connection.execute(
                text(
                    """
                    SELECT public.fail_v1_compat_dirty_key_v1(
                      'compat-pg-test',:source_region,:key_type,
                      :key_part_1,:key_part_2,'SYNTHETIC_BUG',1,1,1
                    )
                    """
                ),
                recovery_identity,
            ).scalar_one()
            assert failed["status"] == "DEAD"
        with admin_engine.connect() as connection:
            recovery_version = connection.execute(
                text(
                    """
                    SELECT compat_row_version FROM public.dts_dirty_keys
                    WHERE source_region=:source_region AND key_type=:key_type
                      AND key_part_1=:key_part_1 AND key_part_2=:key_part_2
                    """
                ),
                recovery_identity,
            ).scalar_one()
        with cutover_engine.begin() as connection:
            recovered = connection.execute(
                text(
                    """
                    SELECT public.recover_v1_compat_dirty_key_v1(
                      'compat-recovery-test',:source_region,:key_type,
                      :key_part_1,:key_part_2,:expected_version,
                      'fixed synthetic projection bug'
                    )
                    """
                ),
                {**recovery_identity, "expected_version": recovery_version},
            ).scalar_one()
            assert recovered["status"] == "APPLIED"
        with cutover_engine.begin() as connection:
            replayed = connection.execute(
                text(
                    """
                    SELECT public.recover_v1_compat_dirty_key_v1(
                      'compat-recovery-test',:source_region,:key_type,
                      :key_part_1,:key_part_2,:expected_version,
                      'fixed synthetic projection bug'
                    )
                    """
                ),
                {**recovery_identity, "expected_version": recovery_version},
            ).scalar_one()
            assert replayed["status"] == "REPLAYED"
        with admin_engine.connect() as connection:
            assert connection.execute(
                text(
                    """
                    SELECT status,compat_status FROM public.dts_dirty_keys
                    WHERE source_region=:source_region AND key_type=:key_type
                      AND key_part_1=:key_part_1 AND key_part_2=:key_part_2
                    """
                ),
                recovery_identity,
            ).one() == ("PENDING", "PENDING")

        # Exact delivery replays validate immutable versions/ledger but do not
        # create a second dirty input or advance the checkpoint.
        assert sink.apply_batch(batch) == (True, True, True)
        with admin_engine.connect() as connection:
            assert connection.execute(
                text("SELECT count(*) FROM public.dts_dirty_key_inputs")
            ).scalar_one() == 8
            assert connection.execute(
                text(
                    """
                    SELECT next_offset,checkpoint_row_version
                    FROM public.dts_ingest_checkpoints
                    WHERE source_region='ovs' AND topic=:topic
                      AND partition_id=0
                    """
                ),
                {"topic": TOPIC},
            ).one() == (3, 4)

        failing_sink = PostgresDtsV2DualCaptureSink(
            settings,
            source_region="ovs",
            source_partition_epoch_id=epoch_id,
            consumer_group=CONSUMER_GROUP,
            control_group=CONTROL_GROUP,
            engine=runtime_engine,
            dirty_queue_store=_FailSecondEnqueue(),
        )
        failed_update = _appoint_event(
            3,
            operation="UPDATE",
            before={"id": APPOINT_ID, "t_id": 10},
            after={"id": APPOINT_ID, "t_id": 30},
            complete=False,
        )
        with pytest.raises(RuntimeError, match="synthetic-enqueue-failure"):
            failing_sink.apply(failed_update, DirtyKeySet(), None)
        with admin_engine.connect() as connection:
            assert connection.execute(
                text(
                    """
                    SELECT source_row_revision,source_row ->> 't_id'
                    FROM public.dts_source_rows
                    WHERE source_region='ovs' AND source_table='ovs_appoint'
                      AND source_key=:source_key
                    """
                ),
                {"source_key": str(APPOINT_ID)},
            ).one() == (3, "10")
            assert connection.execute(
                text("SELECT count(*) FROM public.dts_source_row_versions")
            ).scalar_one() == 3
            assert connection.execute(
                text("SELECT count(*) FROM public.dts_dirty_key_inputs")
            ).scalar_one() == 8
            assert connection.execute(
                text(
                    """
                    SELECT count(*) FROM public.dts_ingest_events
                    WHERE offset_value=3
                    """
                )
            ).scalar_one() == 0

        retired = _ignored_event(
            3,
            operation="UPDATE",
            table_name="ovs_qa_ac_classroom_record",
        )
        control = _ignored_event(4, operation="HEARTBEAT", table_name=None)
        ignored_batch = (
            (
                retired,
                DirtyKeySet(ignored_reason="IGNORED_RETIRED_SOURCE"),
                None,
            ),
            (control, DirtyKeySet(ignored_reason="CONTROL_RECORD"), None),
        )
        assert sink.apply_batch(ignored_batch) == (False, False)
        assert sink.apply_batch(ignored_batch) == (True, True)
        with admin_engine.connect() as connection:
            assert connection.execute(
                text(
                    """
                    SELECT offset_value,route_status,dirty_key_count,issue_codes
                    FROM public.dts_ingest_events
                    WHERE offset_value IN (3,4) ORDER BY offset_value
                    """
                )
            ).all() == [
                (3, "IGNORED", 0, ["IGNORED_RETIRED_SOURCE"]),
                (4, "IGNORED", 0, ["CONTROL_RECORD"]),
            ]
            assert connection.execute(
                text(
                    """
                    SELECT count(*) FROM public.dts_source_rows
                    WHERE source_table='ovs_qa_ac_classroom_record'
                    """
                )
            ).scalar_one() == 0
            assert connection.execute(
                text(
                    """
                    SELECT next_offset,checkpoint_row_version
                    FROM public.dts_ingest_checkpoints
                    WHERE source_region='ovs' AND topic=:topic
                      AND partition_id=0
                    """
                ),
                {"topic": TOPIC},
            ).one() == (5, 6)
            ledger_columns = set(
                connection.execute(
                    text(
                        """
                        SELECT column_name FROM information_schema.columns
                        WHERE table_schema='public'
                          AND table_name='dts_ingest_events'
                        """
                    )
                ).scalars()
            )
            assert {"before", "after", "payload"}.isdisjoint(ledger_columns)

        # The database gate protects both qualification facts and the legacy
        # teacher mirror.  Before activation, requested first grants are
        # suppressed consistently in both tables.
        with admin_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO public.teachers(
                      teacher_id,camp_enrollment_id,name,timezone,camp_day,
                      online_status,graduation_state,gold_qualified,
                      total_score,graduation_threshold,data_mode,
                      source_snapshot_label,payload,created_at,updated_at
                    ) VALUES
                      ('T-GATE-BLOCKED','E-GATE-BLOCKED','blocked','UTC',30,
                       'EXISTING','GRADUATED',true,250,100,'SOURCE','gate-test',
                       '{"graduation_state":"GRADUATED"}'::jsonb,
                       clock_timestamp(),clock_timestamp()),
                      ('T-GATE-ALLOWED','E-GATE-ALLOWED','allowed','UTC',30,
                       'EXISTING','IN_CAMP',false,250,100,'SOURCE','gate-test',
                       '{"graduation_state":"IN_CAMP"}'::jsonb,
                       clock_timestamp(),clock_timestamp())
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO public.teacher_qualifications(
                      teacher_id,graduation_criteria_met,
                      graduation_qualified,graduation_qualified_at,
                      graduation_score_locked,gold_criteria_met,
                      gold_qualified,gold_qualified_at,score_rule_version,
                      gate_results,revision,calculated_at
                    ) VALUES (
                      'T-GATE-BLOCKED',true,true,clock_timestamp(),100,
                      true,true,clock_timestamp(),'gate-test','{}'::jsonb,
                      1,clock_timestamp()
                    ),(
                      'T-GATE-ALLOWED',true,false,NULL,NULL,
                      true,false,NULL,'gate-test','{}'::jsonb,
                      1,clock_timestamp()
                    )
                    """
                )
            )
            assert connection.execute(
                text(
                    """
                    SELECT graduation_state,gold_qualified,
                           payload->>'graduation_state'
                    FROM public.teachers
                    WHERE teacher_id='T-GATE-BLOCKED'
                    """
                )
            ).one() == ("IN_CAMP", False, "IN_CAMP")
            qualification = connection.execute(
                text(
                    """
                    SELECT graduation_qualified,gold_qualified,
                           graduation_qualified_at,gold_qualified_at,
                           graduation_score_locked,
                           gate_results->>'qualification_grants_enabled',
                           gate_results->>
                             'irreversible_qualification_grants_enabled'
                    FROM public.teacher_qualifications
                    WHERE teacher_id='T-GATE-BLOCKED'
                    """
                )
            ).one()
            assert tuple(qualification) == (
                False,
                False,
                None,
                None,
                None,
                "false",
                "false",
            )

            control_version = connection.execute(
                text(
                    """
                    UPDATE public.dts_pipeline_control
                    SET mode='V2_PRIMARY',row_version=row_version+1,
                        projection_generation=projection_generation+1,
                        changed_at=clock_timestamp(),changed_by='test-fixture'
                    WHERE control_id='PRIMARY'
                    RETURNING row_version
                    """
                )
            ).scalar_one()
            connection.execute(
                text(
                    """
                    CREATE FUNCTION public.test_outbox_grant_v1(text)
                    RETURNS void LANGUAGE plpgsql SECURITY DEFINER
                    SET search_path=pg_catalog,public AS $function$
                    BEGIN
                      UPDATE public.teacher_qualifications
                      SET graduation_criteria_met=true,
                          graduation_qualified=true,
                          graduation_qualified_at=clock_timestamp(),
                          graduation_score_locked=100,
                          gold_criteria_met=true,gold_qualified=true,
                          gold_qualified_at=clock_timestamp(),
                          revision=revision+1,calculated_at=clock_timestamp()
                      WHERE teacher_id=$1;
                      UPDATE public.teachers
                      SET graduation_state='GRADUATED',gold_qualified=true,
                          payload=jsonb_set(payload,'{graduation_state}',
                            '"GRADUATED"'::jsonb,true),
                          updated_at=clock_timestamp()
                      WHERE teacher_id=$1;
                    END
                    $function$;
                    REVOKE ALL ON FUNCTION public.test_outbox_grant_v1(text)
                    FROM PUBLIC;
                    GRANT EXECUTE ON FUNCTION
                      public.test_outbox_grant_v1(text)
                    TO tit_dts_outbox_worker_runtime
                    """
                )
            )

        with cutover_engine.begin() as connection:
            enabled = connection.execute(
                text(
                    """
                    SELECT public.set_irreversible_qualification_grants_v2(
                      'gate-enable-test',true,:expected_version,
                      'verified v2 score projection is primary'
                    )
                    """
                ),
                {"expected_version": control_version},
            ).scalar_one()
            assert enabled["status"] == "APPLIED"
            enabled_version = enabled["control_row_version"]

        with pytest.raises(DBAPIError, match="GRANT_ROLE_REQUIRED"):
            with admin_engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        UPDATE public.teacher_qualifications
                        SET graduation_qualified=true,
                            graduation_qualified_at=clock_timestamp(),
                            graduation_score_locked=100,revision=revision+1
                        WHERE teacher_id='T-GATE-BLOCKED'
                        """
                    )
                )

        with outbox_engine.begin() as connection:
            connection.execute(
                text("SELECT public.test_outbox_grant_v1('T-GATE-ALLOWED')")
            )
        with admin_engine.connect() as connection:
            assert connection.execute(
                text(
                    """
                    SELECT teacher.graduation_state,teacher.gold_qualified,
                           qualification.graduation_qualified,
                           qualification.gold_qualified
                    FROM public.teachers teacher
                    JOIN public.teacher_qualifications qualification
                      USING (teacher_id)
                    WHERE teacher.teacher_id='T-GATE-ALLOWED'
                    """
                )
            ).one() == ("GRADUATED", True, True, True)

        # Rollback is blocked while grants are enabled. Disabling is an
        # independently audited CAS command, and stale command replay fails.
        with pytest.raises(DBAPIError, match="DISABLE_REQUIRED"):
            with admin_engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        UPDATE public.dts_pipeline_control
                        SET mode='ROLLED_BACK',row_version=row_version+1,
                            changed_at=clock_timestamp(),changed_by='test'
                        WHERE control_id='PRIMARY'
                        """
                    )
                )
        with cutover_engine.begin() as connection:
            disabled = connection.execute(
                text(
                    """
                    SELECT public.set_irreversible_qualification_grants_v2(
                      'gate-disable-test',false,:expected_version,
                      'disable before compatibility rollback'
                    )
                    """
                ),
                {"expected_version": enabled_version},
            ).scalar_one()
            assert disabled["status"] == "APPLIED"
        with pytest.raises(DBAPIError, match="COMMAND_CONFLICT"):
            with cutover_engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        SELECT public.set_irreversible_qualification_grants_v2(
                          'gate-enable-test',true,:expected_version,
                          'verified v2 score projection is primary'
                        )
                        """
                    ),
                    {"expected_version": control_version},
                )

        # Runtime has no permission to toggle the gate.
        with pytest.raises(DBAPIError):
            with runtime_engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        SELECT public.set_irreversible_qualification_grants_v2(
                          'forged-gate',true,1,'forged'
                        )
                        """
                    )
                )

        # Runtime has no direct dirty-table write even though the wrapper works.
        with pytest.raises(DBAPIError):
            with runtime_engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO public.dts_dirty_keys (
                            source_region,key_type,key_part_1,key_part_2,status
                        ) VALUES ('ovs','COURSE','forged','', 'PENDING')
                        """
                    )
                )
    finally:
        if outbox_engine is not None:
            outbox_engine.dispose()
        if cutover_engine is not None:
            cutover_engine.dispose()
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
