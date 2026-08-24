from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

from app.dts_source_consumer import (
    DtsChangeEvent,
    DtsConsumerSettings,
    protect_domestic_student_ids,
)
from app.dts_source_contract_v2 import with_v2_source_image_completeness
from app.dts_v2_shadow_source_writer import (
    DtsV2ShadowSourceWriter,
    DtsV2ShadowSourceWriterError,
    _require_source_position_advances,
)
from dts_v2_test_profiles import SYNTHETIC_APPOINT_SOURCE_FIELDS


pytestmark = pytest.mark.usefixtures("synthetic_v2_appoint_profiles")


POSTGRES_BINARIES = ("initdb", "pg_ctl", "postgres")
TOPIC = "topic-v2"
ACTIVE_EPOCH = "epoch:v1:active"
REJECTED_EPOCH = "epoch:v1:rejected"
DESCENDANT_EPOCH = "epoch:v1:descendant"
PARALLEL_EPOCH = "epoch:v1:parallel"


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


def _install_scope_v3_stubs(connection) -> None:
    """Keep this rev69 fixture focused on source writer atomicity.

    The real v3 lock and membership implementation is exercised by the
    source-scope runtime PostgreSQL tests at migration head.
    """

    connection.execute(
        text(
            """
            CREATE FUNCTION public.lock_dts_source_table_for_ingest_v3(
                p_source_region text,p_source_table text
            ) RETURNS boolean LANGUAGE sql AS $$ SELECT true $$;
            CREATE FUNCTION public.scope_membership_apply_cdc_v3(
                p_source_region text,p_source_table text,p_source_key text,
                p_source_row_revision bigint,p_before_dependency_keys jsonb,
                p_after_dependency_keys jsonb,p_after_is_present boolean
            ) RETURNS jsonb LANGUAGE sql AS $$
                SELECT jsonb_build_object(
                    'status','APPLIED',
                    'source_row_revision',p_source_row_revision,
                    'membership_count',0
                )
            $$;
            """
        )
    )


def _row(
    source_id: Any,
    teacher_id: Any,
    student_id: Any,
    *,
    status: str = "on",
) -> dict[str, Any]:
    row = {
        field_name: None
        for field_name in SYNTHETIC_APPOINT_SOURCE_FIELDS
    }
    row.update(
        {
            "id": source_id,
            "t_id": teacher_id,
            "s_id": student_id,
            "status": status,
            "use_point": "buy",
        }
    )
    return row


def _event(
    *,
    region: str,
    operation: str,
    offset: int,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    id_type: str = "NUMERIC",
    teacher_type: str = "NUMERIC",
    student_type: str = "TEXT",
    record_id: int | None = None,
    source_timestamp: int | None = None,
    topic: str = TOPIC,
    partition: int = 0,
) -> DtsChangeEvent:
    event = DtsChangeEvent(
        source_region=region,
        topic=topic,
        partition=partition,
        offset=offset,
        record_id=1000 + offset if record_id is None else record_id,
        source_timestamp=(
            1_787_500_000 + offset
            if source_timestamp is None
            else source_timestamp
        ),
        source_txid=f"tx-{offset}",
        source_position=f"opaque-and-not-persisted-{offset}",
        operation=operation,
        database_name="source",
        schema_name="public",
        table_name=f"{region}_appoint",
        before=before,
        after=after,
        source_field_types={
            "id": id_type,
            "t_id": teacher_type,
            "s_id": student_type,
            "status": "TEXT",
            "use_point": "TEXT",
        },
    )
    event = with_v2_source_image_completeness(event)
    if region == "dom":
        event = protect_domestic_student_ids(
            event,
            DtsConsumerSettings(
                source_region="dom",
                broker_urls=("broker.invalid:9092",),
                topic=TOPIC,
                group_id="dom-v2-shadow-writer-test",
                account="test-account",
                password="test-password",
                execution_region="cn",
                domestic_student_hmac_key="11" * 32,
            ),
        )
    assert event.source_images_complete is True
    assert event.source_image_profile_id is not None
    return event


def _sparse_dom_event(
    *,
    offset: int,
    before: dict[str, Any],
    after: dict[str, Any],
    source_field_types: dict[str, str],
    topic: str = TOPIC,
    partition: int = 0,
) -> DtsChangeEvent:
    event = DtsChangeEvent(
        source_region="dom",
        topic=topic,
        partition=partition,
        offset=offset,
        record_id=1000 + offset,
        source_timestamp=1_787_500_000 + offset,
        source_txid=f"tx-{topic}-{partition}-{offset}",
        source_position=f"opaque-and-not-persisted-{offset}",
        operation="UPDATE",
        database_name="source",
        schema_name="public",
        table_name="dom_appoint",
        before=before,
        after=after,
        source_field_types=source_field_types,
    )
    event = with_v2_source_image_completeness(event)
    assert event.source_images_complete is False
    return protect_domestic_student_ids(
        event,
        DtsConsumerSettings(
            source_region="dom",
            broker_urls=("broker.invalid:9092",),
            topic=topic,
            group_id="dom-v2-shadow-writer-sparse-test",
            account="test-account",
            password="test-password",
            execution_region="cn",
            domestic_student_hmac_key="11" * 32,
        ),
    )


def _install_revision_65_fixture(connection) -> None:
    connection.execute(
        text(
            """
            CREATE ROLE tit_growth_app NOLOGIN;
            CREATE ROLE tit_dts_ingest_runtime LOGIN;
            CREATE ROLE tit_teacher_crud NOLOGIN;

            CREATE TABLE public.alembic_version (
                version_num varchar(32) PRIMARY KEY
            );
            INSERT INTO public.alembic_version
            VALUES ('20260819_65_g09_set_course');

            CREATE TABLE public.dts_ingest_events (legacy_id integer);
            CREATE TABLE public.dts_ingest_checkpoints (legacy_id integer);
            CREATE TABLE public.dts_dirty_keys (legacy_id integer);
            CREATE TABLE public.outbox_events (legacy_id integer);

            -- Minimal rev65 source/result consumers required by the
            -- lesson-region expand revision.  This fixture intentionally
            -- starts at rev65 instead of replaying the full historical chain,
            -- so every relation touched by 65a must still be represented.
            CREATE TABLE public.lesson_source_wide (
                "课程id" varchar(128) NOT NULL,
                "上课日期" date,
                "上课时间" time,
                "老师id" varchar(64) NOT NULL,
                "学员id" varchar(128),
                CONSTRAINT pk_lesson_source_wide PRIMARY KEY ("课程id")
            );
            CREATE TABLE public.lesson_score_results (
                lesson_id varchar(128) NOT NULL,
                CONSTRAINT pk_lesson_score_results PRIMARY KEY (lesson_id),
                CONSTRAINT fk_lesson_score_result_source_lesson
                    FOREIGN KEY (lesson_id)
                    REFERENCES public.lesson_source_wide ("课程id")
                    ON DELETE CASCADE
            );
            CREATE TABLE public.personalized_trigger_matches (
                trigger_match_id varchar(160) NOT NULL,
                lesson_id varchar(128),
                CONSTRAINT pk_personalized_trigger_matches
                    PRIMARY KEY (trigger_match_id),
                CONSTRAINT fk_personalized_trigger_match_lesson
                    FOREIGN KEY (lesson_id)
                    REFERENCES public.lesson_source_wide ("课程id")
                    ON DELETE SET NULL
            );
            CREATE TABLE public.score_entries (
                entry_id varchar(160) PRIMARY KEY,
                lesson_id varchar(128)
            );

            CREATE TABLE public.dts_source_rows (
                source_region varchar(8) NOT NULL,
                source_table varchar(128) NOT NULL,
                source_key varchar(512) NOT NULL,
                source_key_data jsonb NOT NULL,
                dependency_keys jsonb NOT NULL,
                source_row jsonb NOT NULL,
                is_deleted boolean NOT NULL,
                source_timestamp bigint NOT NULL,
                last_record_id bigint NOT NULL,
                source_position text NOT NULL,
                last_topic varchar(512) NOT NULL,
                last_partition integer NOT NULL,
                last_offset bigint NOT NULL,
                row_version integer NOT NULL,
                updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
                CONSTRAINT pk_dts_source_rows PRIMARY KEY (
                    source_region, source_table, source_key
                ),
                CONSTRAINT ck_dts_source_row_region
                    CHECK (source_region IN ('ovs', 'dom')),
                CONSTRAINT ck_dts_source_row_version CHECK (
                    last_partition >= 0 AND last_offset >= 0
                    AND row_version >= 1
                )
            );
            CREATE INDEX ix_dts_source_rows_table_active
                ON public.dts_source_rows (
                    source_region, source_table, is_deleted
                );
            CREATE INDEX ix_dts_source_rows_dependency_keys
                ON public.dts_source_rows USING gin (
                    dependency_keys jsonb_path_ops
                );

            INSERT INTO public.dts_source_rows (
                source_region, source_table, source_key, source_key_data,
                dependency_keys, source_row, is_deleted, source_timestamp,
                last_record_id, source_position, last_topic, last_partition,
                last_offset, row_version
            ) VALUES (
                'dom', 'dom_appoint', '2002',
                jsonb_build_object('id', 2002), '{}'::jsonb,
                jsonb_build_object('id', 2002, 't_id', '22'),
                false, 1, 1, 'legacy', 'legacy-topic', 0, 1, 1
            );
            """
        )
    )


def _seed_epochs(connection) -> None:
    connection.execute(
        text(
            """
            INSERT INTO public.dts_source_partition_epochs (
                source_region, source_partition_epoch_id, topic, partition_id,
                epoch_kind, status, stream_generation_id, epoch_opening_id,
                epoch_sequence, start_offset, v2_epoch_bootstrap_floor,
                activation_mode
            ) VALUES
            (
                'dom', :active_epoch, :topic, 0, 'BROKER', 'ACTIVE',
                'generation-1', 'opening-1', 1, 0, 0, 'H0_BOOTSTRAP'
            ),
            (
                'ovs', :active_epoch, :topic, 0, 'BROKER', 'ACTIVE',
                'generation-1', 'opening-1', 1, 0, 0, 'H0_BOOTSTRAP'
            ),
            (
                'dom', :rejected_epoch, :topic, 0, 'BROKER', 'SUPERSEDED',
                'generation-1', 'opening-2', 99, 0, 0, NULL
            )
            """
        ),
        {
            "active_epoch": ACTIVE_EPOCH,
            "rejected_epoch": REJECTED_EPOCH,
            "topic": TOPIC,
        },
    )


@pytest.mark.skipif(
    not _postgres_tools_available(),
    reason="local PostgreSQL binaries are required for shadow-writer checks",
)
def test_appoint_only_shadow_writer_is_atomic_typed_private_and_inert(
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
            _install_revision_65_fixture(connection)
        _run_alembic(
            backend_dir,
            database_url,
            "upgrade",
            "20260822_69_dts_v2_source_guard",
        )
        with engine.begin() as connection:
            _install_scope_v3_stubs(connection)

        with engine.begin() as connection:
            _seed_epochs(connection)
            connection.execute(
                text(
                    """
                    INSERT INTO public.dts_source_partition_epochs (
                        source_region, source_partition_epoch_id, topic,
                        partition_id, epoch_kind, status,
                        stream_generation_id, epoch_opening_id,
                        epoch_sequence, predecessor_epoch_id, start_offset,
                        v2_epoch_bootstrap_floor
                    ) VALUES
                    (
                        'dom', :descendant_epoch, :topic, 0, 'BROKER',
                        'BARRIER_PENDING', 'generation-2', 'opening-3', 2,
                        :active_epoch, 0, 0
                    ),
                    (
                        'dom', :parallel_epoch, :parallel_topic, 1, 'BROKER',
                        'BARRIER_PENDING', 'parallel-generation',
                        'parallel-opening', 2, :active_epoch, 0, 0
                    )
                    """
                ),
                {
                    "active_epoch": ACTIVE_EPOCH,
                    "descendant_epoch": DESCENDANT_EPOCH,
                    "parallel_epoch": PARALLEL_EPOCH,
                    "topic": TOPIC,
                    "parallel_topic": f"{TOPIC}-parallel",
                },
            )
            baseline_counts = {
                table_name: connection.execute(
                    text(f"SELECT count(*) FROM public.{table_name}")
                ).scalar_one()
                for table_name in (
                    "dts_ingest_events",
                    "dts_ingest_checkpoints",
                    "dts_dirty_keys",
                    "source_courses",
                    "source_course_participations",
                    "outbox_events",
                )
            }
            privileges = connection.execute(
                text(
                    """
                    SELECT has_table_privilege(
                        'tit_dts_ingest_runtime',
                        'public.' || table_name,
                        privilege_name
                    )
                    FROM unnest(ARRAY[
                        'dts_source_partition_epochs',
                        'dts_source_row_versions'
                    ]) AS tables(table_name)
                    CROSS JOIN unnest(ARRAY[
                        'SELECT', 'INSERT', 'UPDATE', 'DELETE'
                    ]) AS privileges(privilege_name)
                    """
                )
            ).scalars().all()
            assert len(privileges) == 8
            assert not any(privileges)

        row_a = _row(1001, "009", "student-raw-1")
        row_b = _row(1001, "010", "student-raw-1")
        insert_event = _event(
            region="dom",
            operation="INSERT",
            offset=1,
            before=None,
            after=row_a,
        )
        noop_event = _event(
            region="dom",
            operation="UPDATE",
            offset=2,
            before=row_a,
            after=row_a,
        )
        conflict_event = _event(
            region="dom",
            operation="UPDATE",
            offset=3,
            before=_row(1001, "777", "student-raw-1"),
            after=row_b,
        )
        update_event = _event(
            region="dom",
            operation="UPDATE",
            offset=4,
            before=row_a,
            after=row_b,
        )
        delete_event = _event(
            region="dom",
            operation="DELETE",
            offset=6,
            before=row_b,
            after=None,
        )
        writer = DtsV2ShadowSourceWriter(enabled=True)

        with engine.begin() as connection:
            with pytest.raises(
                DtsV2ShadowSourceWriterError,
                match="DTS_V2_SHADOW_SNAPSHOT_BARRIER_INVALID",
            ):
                _require_source_position_advances(
                    connection,
                    event=insert_event,
                    source_partition_epoch_id=ACTIVE_EPOCH,
                    incoming_epoch={"epoch_sequence": 1},
                    stored_current={"last_version_kind": "SNAPSHOT_DIFF"},
                )
            with pytest.raises(
                DtsV2ShadowSourceWriterError,
                match="DTS_V2_SHADOW_SOURCE_WRITER_DISABLED",
            ):
                DtsV2ShadowSourceWriter().apply_appoint_cdc(
                    connection,
                    insert_event,
                    ACTIVE_EPOCH,
                )
            forged_profile_event = replace(
                insert_event,
                _v2_source_image_completeness_proof=None,
            )
            with pytest.raises(
                DtsV2ShadowSourceWriterError,
                match="DTS_V2_SHADOW_EXACT_SOURCE_PROFILE_REQUIRED",
            ):
                writer.apply_appoint_cdc(
                    connection,
                    forged_profile_event,
                    ACTIVE_EPOCH,
                )
            forged_dom_protection_event = replace(
                insert_event,
                _domestic_protection_proof=None,
            )
            with pytest.raises(
                DtsV2ShadowSourceWriterError,
                match="DTS_DOM_PROTECTION_PROOF_INVALID",
            ):
                writer.apply_appoint_cdc(
                    connection,
                    forged_dom_protection_event,
                    ACTIVE_EPOCH,
                )
            inserted = writer.apply_appoint_cdc(
                connection,
                insert_event,
                ACTIVE_EPOCH,
            )
            assert (inserted.status, inserted.source_row_revision) == (
                "APPLIED",
                1,
            )
            noop = writer.apply_appoint_cdc(
                connection,
                noop_event,
                ACTIVE_EPOCH,
            )
            assert (noop.status, noop.source_row_revision) == ("NOOP", 2)

        with pytest.raises(
            DtsV2ShadowSourceWriterError,
            match="SOURCE_BEFORE_CONFLICT",
        ):
            with engine.begin() as connection:
                writer.apply_appoint_cdc(
                    connection,
                    conflict_event,
                    ACTIVE_EPOCH,
                )
        with engine.connect() as connection:
            assert connection.execute(
                text(
                    """
                    SELECT count(*)
                    FROM public.dts_source_row_versions
                    WHERE source_region = 'dom' AND source_key = '1001'
                    """
                )
            ).scalar_one() == 2

        with engine.begin() as connection:
            updated = writer.apply_appoint_cdc(
                connection,
                update_event,
                ACTIVE_EPOCH,
            )
            assert (updated.status, updated.source_row_revision) == (
                "APPLIED",
                3,
            )
        with engine.connect() as connection:
            assert connection.execute(
                text(
                    """
                    SELECT dependency_keys -> 'teacher_ids'
                    FROM public.dts_source_rows
                    WHERE source_region = 'dom'
                      AND source_table = 'dom_appoint'
                      AND source_key = '1001'
                    """
                )
            ).scalar_one() == ["10"]
        with engine.begin() as connection:
            replayed = writer.apply_appoint_cdc(
                connection,
                update_event,
                ACTIVE_EPOCH,
            )
            assert (replayed.status, replayed.source_row_revision) == (
                "REPLAYED",
                3,
            )

        ordered_a = _row(1101, "11", "student-order")
        ordered_b = _row(1101, "12", "student-order")
        ordered_insert = _event(
            region="dom",
            operation="INSERT",
            offset=20,
            before=None,
            after=ordered_a,
        )
        ordered_update = _event(
            region="dom",
            operation="UPDATE",
            offset=22,
            before=ordered_a,
            after=ordered_b,
        )
        with engine.begin() as connection:
            writer.apply_appoint_cdc(
                connection,
                ordered_insert,
                ACTIVE_EPOCH,
            )
            writer.apply_appoint_cdc(
                connection,
                ordered_update,
                ACTIVE_EPOCH,
            )

        # The immutable delivery at offset 22 may replay, but it may not
        # weaken typed evidence for a non-null field that was actually sent.
        with pytest.raises(
            DtsV2ShadowSourceWriterError,
            match="SOURCE_VERSION_IDENTITY_CONFLICT",
        ):
            with engine.begin() as connection:
                writer.apply_appoint_cdc(
                    connection,
                    _sparse_dom_event(
                        offset=22,
                        before={"id": 1101, "t_id": "11"},
                        after={"id": 1101, "t_id": "12"},
                        source_field_types={"id": "NUMERIC"},
                    ),
                    ACTIVE_EPOCH,
                )

        # A cyclic source state can make an older delivery's before image
        # match current.  Offset order, not that accidental equality, must
        # prevent offset 21 from undoing the already-applied offset 22.
        with pytest.raises(
            DtsV2ShadowSourceWriterError,
            match="DTS_V2_SHADOW_SOURCE_POSITION_NOT_ADVANCING",
        ):
            with engine.begin() as connection:
                writer.apply_appoint_cdc(
                    connection,
                    _event(
                        region="dom",
                        operation="UPDATE",
                        offset=21,
                        before=ordered_b,
                        after=ordered_a,
                    ),
                    ACTIVE_EPOCH,
                )

        noop_order_a = _row(1301, "31", "student-noop-order")
        noop_order_b = _row(1301, "32", "student-noop-order")
        with engine.begin() as connection:
            writer.apply_appoint_cdc(
                connection,
                _event(
                    region="dom",
                    operation="INSERT",
                    offset=40,
                    before=None,
                    after=noop_order_a,
                ),
                ACTIVE_EPOCH,
            )
            noop_order = writer.apply_appoint_cdc(
                connection,
                _event(
                    region="dom",
                    operation="UPDATE",
                    offset=50,
                    before=noop_order_a,
                    after=noop_order_a,
                ),
                ACTIVE_EPOCH,
            )
            assert (noop_order.status, noop_order.source_row_revision) == (
                "NOOP",
                2,
            )

        # A semantic NOOP still proves the later broker position.  Omitting it
        # from immutable history would let this older state-changing event
        # overwrite the source current row.
        with pytest.raises(
            DtsV2ShadowSourceWriterError,
            match="DTS_V2_SHADOW_SOURCE_POSITION_NOT_ADVANCING",
        ):
            with engine.begin() as connection:
                writer.apply_appoint_cdc(
                    connection,
                    _event(
                        region="dom",
                        operation="UPDATE",
                        offset=45,
                        before=noop_order_a,
                        after=noop_order_b,
                    ),
                    ACTIVE_EPOCH,
                )

        lineage_a = _row(1201, "21", "student-lineage")
        lineage_b = _row(1201, "22", "student-lineage")
        lineage_c = _row(1201, "23", "student-lineage")
        with engine.begin() as connection:
            writer.apply_appoint_cdc(
                connection,
                _event(
                    region="dom",
                    operation="INSERT",
                    offset=30,
                    before=None,
                    after=lineage_a,
                ),
                ACTIVE_EPOCH,
            )

        # A forged predecessor pointer cannot move a key across broker
        # topic/partition boundaries, even while current is still the claimed
        # predecessor epoch.
        with pytest.raises(
            DtsV2ShadowSourceWriterError,
            match="DTS_V2_SHADOW_SOURCE_EPOCH_LINEAGE_INVALID",
        ):
            with engine.begin() as connection:
                writer.apply_appoint_cdc(
                    connection,
                    _event(
                        region="dom",
                        operation="UPDATE",
                        offset=0,
                        before=lineage_a,
                        after=lineage_c,
                        topic=f"{TOPIC}-parallel",
                        partition=1,
                    ),
                    PARALLEL_EPOCH,
                )

        with engine.begin() as connection:
            descendant = writer.apply_appoint_cdc(
                connection,
                _event(
                    region="dom",
                    operation="UPDATE",
                    offset=0,
                    before=lineage_a,
                    after=lineage_b,
                ),
                DESCENDANT_EPOCH,
            )
            assert descendant.source_row_revision == 2

        with pytest.raises(
            DtsV2ShadowSourceWriterError,
            match="DTS_V2_SHADOW_SOURCE_EPOCH_LINEAGE_INVALID",
        ):
            with engine.begin() as connection:
                writer.apply_appoint_cdc(
                    connection,
                    _event(
                        region="dom",
                        operation="UPDATE",
                        offset=0,
                        before=lineage_b,
                        after=lineage_c,
                        topic=f"{TOPIC}-parallel",
                        partition=1,
                    ),
                    PARALLEL_EPOCH,
                )

        with engine.connect() as connection:
            ordered_current = connection.execute(
                text(
                    """
                    SELECT source_row_revision, source_row ->> 't_id'
                    FROM public.dts_source_rows
                    WHERE source_region = 'dom'
                      AND source_table = 'dom_appoint'
                      AND source_key = '1101'
                    """
                )
            ).one()
            lineage_current = connection.execute(
                text(
                    """
                    SELECT source_row_revision,
                           last_source_partition_epoch_id,
                           source_row ->> 't_id'
                    FROM public.dts_source_rows
                    WHERE source_region = 'dom'
                      AND source_table = 'dom_appoint'
                      AND source_key = '1201'
                    """
                )
            ).one()
            noop_order_current = connection.execute(
                text(
                    """
                    SELECT source_row_revision, last_offset,
                           source_row ->> 't_id'
                    FROM public.dts_source_rows
                    WHERE source_region = 'dom'
                      AND source_table = 'dom_appoint'
                      AND source_key = '1301'
                    """
                )
            ).one()
            assert ordered_current == (2, "12")
            assert lineage_current == (2, DESCENDANT_EPOCH, "22")
            assert noop_order_current == (2, 50, "31")

        altered_same_delivery = _event(
            region="dom",
            operation="UPDATE",
            offset=4,
            before=row_a,
            after=row_b,
            record_id=999_999,
            source_timestamp=update_event.source_timestamp + 1,
        )
        with pytest.raises(
            DtsV2ShadowSourceWriterError,
            match="SOURCE_VERSION_IDENTITY_CONFLICT",
        ):
            with engine.begin() as connection:
                writer.apply_appoint_cdc(
                    connection,
                    altered_same_delivery,
                    ACTIVE_EPOCH,
                )

        altered_same_identity = _event(
            region="dom",
            operation="UPDATE",
            offset=4,
            before=row_a,
            after={**row_b, "status": "end"},
        )
        with pytest.raises(
            DtsV2ShadowSourceWriterError,
            match="SOURCE_VERSION_IDENTITY_CONFLICT",
        ):
            with engine.begin() as connection:
                writer.apply_appoint_cdc(
                    connection,
                    altered_same_identity,
                    ACTIVE_EPOCH,
                )

        semantic_replay_event = _event(
            region="dom",
            operation="UPDATE",
            offset=5,
            before=row_a,
            after=row_b,
        )
        with engine.begin() as connection:
            semantic_replay = writer.apply_appoint_cdc(
                connection,
                semantic_replay_event,
                ACTIVE_EPOCH,
            )
            assert (
                semantic_replay.status,
                semantic_replay.source_row_revision,
            ) == ("SEMANTIC_REPLAY", 4)

        with engine.begin() as connection:
            deleted = writer.apply_appoint_cdc(
                connection,
                delete_event,
                ACTIVE_EPOCH,
            )
            assert (deleted.status, deleted.source_row_revision) == (
                "APPLIED",
                5,
            )

        legacy_event = _event(
            region="dom",
            operation="UPDATE",
            offset=7,
            before=_row(2004, "22", None),
            after=_row(2004, "23", None),
        )
        with engine.begin() as connection:
            ignored = writer.apply_appoint_cdc(
                connection,
                legacy_event,
                ACTIVE_EPOCH,
            )
            assert (ignored.status, ignored.source_row_revision) == (
                "IGNORED_MISSING_CURRENT",
                None,
            )
            assert connection.execute(
                text(
                    """
                    SELECT count(*) FROM public.dts_source_rows
                    WHERE source_region='dom'
                      AND source_table='dom_appoint'
                      AND source_key='2004'
                    """
                )
            ).scalar_one() == 0
            assert connection.execute(
                text(
                    """
                    SELECT count(*) FROM public.dts_source_row_versions
                    WHERE source_region='dom'
                      AND source_table='dom_appoint'
                      AND source_key='2004'
                    """
                )
            ).scalar_one() == 0

            inserted_after_h0 = writer.apply_appoint_cdc(
                connection,
                _event(
                    region="dom",
                    operation="INSERT",
                    offset=71,
                    before=None,
                    after=_row(2004, "23", None),
                ),
                ACTIVE_EPOCH,
            )
            updated_after_insert = writer.apply_appoint_cdc(
                connection,
                _event(
                    region="dom",
                    operation="UPDATE",
                    offset=72,
                    before=_row(2004, "23", None),
                    after=_row(2004, "24", None),
                ),
                ACTIVE_EPOCH,
            )
            assert (
                inserted_after_h0.status,
                inserted_after_h0.source_row_revision,
                updated_after_insert.status,
                updated_after_insert.source_row_revision,
            ) == ("APPLIED", 1, "APPLIED", 2)

            connection.execute(
                text(
                    """
                    INSERT INTO public.dts_source_rows(
                      source_region,source_table,source_key,source_key_data,
                      dependency_keys,source_row,is_deleted,source_timestamp,
                      last_record_id,source_position,last_topic,
                      last_partition,last_offset,row_version,
                      provenance_state
                    ) VALUES (
                      'dom','dom_appoint','2003',
                      jsonb_build_object('id',2003),'{}'::jsonb,
                      jsonb_build_object('id',2003,'t_id','22'),false,
                      1,1,'legacy','legacy-topic',0,1,1,'LEGACY_PENDING'
                    )
                    """
                )
            )

        with engine.begin() as connection:
            ignored_legacy_placeholder = writer.apply_appoint_cdc(
                connection,
                _sparse_dom_event(
                    offset=70,
                    before={"id": 2003, "t_id": "22"},
                    after={"id": 2003, "t_id": "23"},
                    source_field_types={
                        "id": "NUMERIC",
                        "t_id": "NUMERIC",
                    },
                ),
                ACTIVE_EPOCH,
            )
            assert (
                ignored_legacy_placeholder.status,
                ignored_legacy_placeholder.source_row_revision,
            ) == ("IGNORED_MISSING_CURRENT", None)
            assert connection.execute(
                text(
                    """
                    SELECT provenance_state,source_row_revision,
                           source_row ->> 't_id'
                    FROM public.dts_source_rows
                    WHERE source_region='dom'
                      AND source_table='dom_appoint'
                      AND source_key='2003'
                    """
                )
            ).one() == ("LEGACY_PENDING", None, "22")

        with pytest.raises(
            DtsV2ShadowSourceWriterError,
            match="DTS_V2_SHADOW_EPOCH_REJECTED",
        ):
            with engine.begin() as connection:
                writer.apply_appoint_cdc(
                    connection,
                    _event(
                        region="dom",
                        operation="INSERT",
                        offset=8,
                        before=None,
                        after=_row(3003, "30", "student-3"),
                    ),
                    REJECTED_EPOCH,
                )

        ovs_numeric = _event(
            region="ovs",
            operation="INSERT",
            offset=9,
            before=None,
            after=_row("009", "040", "ovs-student"),
            id_type="NUMERIC",
        )
        ovs_wrong_key_type = _event(
            region="ovs",
            operation="INSERT",
            offset=10,
            before=None,
            after=_row("009", "041", "ovs-student"),
            id_type="TEXT",
            teacher_type="TEXT",
        )
        with pytest.raises(
            DtsV2ShadowSourceWriterError,
            match="DTS_SOURCE_PRIMARY_KEY_TYPE_MISMATCH",
        ):
            with engine.begin() as connection:
                writer.apply_appoint_cdc(
                    connection,
                    ovs_wrong_key_type,
                    ACTIVE_EPOCH,
                )

        ovs_text_teacher = _event(
            region="ovs",
            operation="INSERT",
            offset=11,
            before=None,
            after=_row(10, "041", "ovs-student"),
            id_type="NUMERIC",
            teacher_type="TEXT",
        )
        with engine.begin() as connection:
            numeric_result = writer.apply_appoint_cdc(
                connection,
                ovs_numeric,
                ACTIVE_EPOCH,
            )
            text_teacher_result = writer.apply_appoint_cdc(
                connection,
                ovs_text_teacher,
                ACTIVE_EPOCH,
            )
            assert (numeric_result.source_key, numeric_result.source_key_type) == (
                "9",
                "NUMERIC",
            )
            assert (
                text_teacher_result.source_key,
                text_teacher_result.source_key_type,
            ) == (
                "10",
                "NUMERIC",
            )

        with pytest.raises(
            DtsV2ShadowSourceWriterError,
            match="DTS_V2_SHADOW_EPOCH_REJECTED",
        ):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        UPDATE public.dts_source_partition_epochs
                        SET v2_epoch_bootstrap_floor = 100
                        WHERE source_region = 'dom'
                          AND source_partition_epoch_id = :epoch_id
                          AND topic = :topic
                          AND partition_id = 0
                        """
                    ),
                    {"epoch_id": ACTIVE_EPOCH, "topic": TOPIC},
                )
                writer.apply_appoint_cdc(
                    connection,
                    _event(
                        region="dom",
                        operation="INSERT",
                        offset=12,
                        before=None,
                        after=_row(4004, "40", "student-4"),
                    ),
                    ACTIVE_EPOCH,
                )

        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE public.dts_source_partition_epochs
                    SET status = 'SUPERSEDED', row_version = row_version + 1
                    WHERE source_region = 'dom'
                      AND source_partition_epoch_id = :epoch_id
                      AND topic = :topic
                      AND partition_id = 0
                    """
                ),
                {"epoch_id": ACTIVE_EPOCH, "topic": TOPIC},
            )
            superseded_replay = writer.apply_appoint_cdc(
                connection,
                delete_event,
                ACTIVE_EPOCH,
            )
            assert (
                superseded_replay.status,
                superseded_replay.source_row_revision,
            ) == ("REPLAYED", 5)

        with engine.connect() as connection:
            dom_versions = connection.execute(
                text(
                    """
                    SELECT source_row_revision, operation, before_row,
                           after_row, source_field_types, source_position
                    FROM public.dts_source_row_versions
                    WHERE source_region = 'dom' AND source_key = '1001'
                    ORDER BY source_row_revision
                    """
                )
            ).mappings().all()
            assert [row["source_row_revision"] for row in dom_versions] == [
                1,
                2,
                3,
                4,
                5,
            ]
            assert [row["operation"] for row in dom_versions] == [
                "INSERT",
                "UPDATE",
                "UPDATE",
                "UPDATE",
                "DELETE",
            ]
            assert dom_versions[2]["before_row"]["t_id"] == "009"
            assert dom_versions[2]["after_row"]["t_id"] == "010"
            assert dom_versions[2]["source_field_types"]["t_id"] == "NUMERIC"
            assert dom_versions[0]["source_position"] == {
                "v": 1,
                "source_timestamp": "2026-08-23T15:46:41.000000Z",
                "record_id_type": "numeric",
                "record_id": "1001",
                "source_partition_epoch_id": ACTIVE_EPOCH,
                "topic": TOPIC,
                "partition_id": 0,
                "offset_value": 1,
            }

            current = connection.execute(
                text(
                    """
                    SELECT source_row_revision, row_version, is_deleted,
                           source_row, dependency_keys, provenance_state,
                           source_key_type, source_schema_profile_id,
                           source_field_types, source_position_v2
                    FROM public.dts_source_rows
                    WHERE source_region = 'dom'
                      AND source_table = 'dom_appoint'
                      AND source_key = '1001'
                    """
                )
            ).mappings().one()
            assert (current["source_row_revision"], current["row_version"]) == (
                5,
                5,
            )
            assert current["is_deleted"] is True
            assert current["provenance_state"] == "V2_CONFIRMED"
            assert current["dependency_keys"]["course_ids"] == ["1001"]
            assert current["dependency_keys"]["teacher_ids"] == ["10"]
            student_token = current["source_row"]["student_token"]
            assert student_token.startswith("dom:v1:")
            protected_payloads = json.dumps(
                [
                    *[row["before_row"] for row in dom_versions],
                    *[row["after_row"] for row in dom_versions],
                    current["source_row"],
                    current["dependency_keys"],
                ],
                ensure_ascii=False,
                sort_keys=True,
            )
            assert "s_id" not in protected_payloads
            assert "student-raw-1" not in protected_payloads
            assert "opaque-and-not-persisted" not in json.dumps(
                [row["source_position"] for row in dom_versions]
            )

            ovs_identities = connection.execute(
                text(
                    """
                    SELECT source_key, source_key_type,
                           source_key_numeric::text, source_key_text,
                           source_field_types ->> 'id'
                    FROM public.dts_source_row_versions
                    WHERE source_region = 'ovs'
                    ORDER BY offset_value
                    """
                )
            ).all()
            assert ovs_identities == [
                ("9", "NUMERIC", "9", None, "NUMERIC"),
                ("10", "NUMERIC", "10", None, "NUMERIC"),
            ]
            assert connection.execute(
                text(
                    """
                    SELECT dependency_keys -> 'teacher_ids'
                    FROM public.dts_source_rows
                    WHERE source_region = 'ovs'
                      AND source_table = 'ovs_appoint'
                      AND source_key = '10'
                    """
                )
            ).scalar_one() == ["041"]

            final_counts = {
                table_name: connection.execute(
                    text(f"SELECT count(*) FROM public.{table_name}")
                ).scalar_one()
                for table_name in baseline_counts
            }
            assert final_counts == baseline_counts
    finally:
        engine.dispose()
        subprocess.run(
            [pg_ctl, "-D", str(data_dir), "-m", "fast", "-w", "stop"],
            check=False,
            capture_output=True,
            text=True,
        )
