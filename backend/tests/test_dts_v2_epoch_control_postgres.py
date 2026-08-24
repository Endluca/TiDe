from __future__ import annotations

import json
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


POSTGRES_BINARIES = ("initdb", "pg_ctl", "postgres")
REVISION_78 = "20260822_78_pending_score_guard"
REVISION_79 = "20260822_79_dts_v2_epoch_control"


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


def _run_alembic_expect_failure(
    backend_dir: Path,
    database_url: str,
    expected_error: str,
    *args: str,
) -> None:
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
    output = f"{result.stdout}\n{result.stderr}"
    assert result.returncode != 0, "populated epoch-control downgrade succeeded"
    assert expected_error in output


def _seed_revision_78_shape(connection) -> None:
    connection.execute(
        text(
            f"""
            CREATE ROLE tit_growth_app NOLOGIN;
            CREATE ROLE tit_dts_ingest_runtime NOLOGIN;
            CREATE ROLE tit_teacher_crud NOLOGIN;
            CREATE ROLE tit_bootstrap_unauthorized NOLOGIN;

            CREATE TABLE public.alembic_version (
                version_num varchar(64) PRIMARY KEY
            );
            INSERT INTO public.alembic_version VALUES ('{REVISION_78}');

            CREATE TABLE public.dts_ingest_checkpoints (
                source_region varchar(8) NOT NULL,
                topic varchar(512) NOT NULL,
                partition_id integer NOT NULL,
                next_offset bigint NOT NULL,
                source_timestamp bigint NOT NULL,
                source_position text NOT NULL,
                updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
                source_partition_epoch_id varchar(160),
                consumer_group varchar(256),
                checkpoint_row_version bigint,
                is_current_epoch boolean,
                CONSTRAINT pk_dts_ingest_checkpoints PRIMARY KEY (
                    source_region, topic, partition_id
                ),
                CONSTRAINT ck_dts_checkpoint_region CHECK (
                    source_region IN ('dom', 'ovs')
                ),
                CONSTRAINT ck_dts_checkpoint_offsets CHECK (
                    partition_id >= 0 AND next_offset >= 0
                )
            );

            CREATE TABLE public.dts_source_partition_epochs (
                source_region varchar(8) NOT NULL,
                source_partition_epoch_id varchar(160) NOT NULL,
                topic varchar(512) NOT NULL,
                partition_id integer NOT NULL,
                epoch_kind varchar(32) NOT NULL,
                status varchar(32) NOT NULL,
                stream_generation_id text,
                epoch_opening_id text,
                epoch_sequence bigint,
                predecessor_epoch_id varchar(160),
                start_offset bigint,
                v2_epoch_bootstrap_floor bigint,
                activation_mode varchar(32),
                activation_manifest_hash varchar(64),
                snapshot_id varchar(160),
                source_table varchar(128),
                created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
                activated_at timestamptz,
                sealed_at timestamptz,
                superseded_at timestamptz,
                row_version bigint NOT NULL DEFAULT 1,
                CONSTRAINT pk_dts_source_partition_epochs PRIMARY KEY (
                    source_region, source_partition_epoch_id, topic, partition_id
                ),
                CONSTRAINT uq_dts_source_partition_epoch_identity UNIQUE (
                    source_region, source_partition_epoch_id
                ),
                CONSTRAINT uq_dts_source_partition_epoch_sequence UNIQUE (
                    source_region, topic, partition_id, epoch_sequence
                )
            );
            CREATE UNIQUE INDEX uq_dts_source_partition_epoch_active_broker
            ON public.dts_source_partition_epochs (
                source_region, topic, partition_id
            ) WHERE epoch_kind = 'BROKER' AND status = 'ACTIVE';

            GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE
                public.dts_ingest_checkpoints
            TO tit_dts_ingest_runtime;
            """
        )
    )


def _epoch_id(connection, route: dict[str, object]) -> str:
    return str(
        connection.execute(
            text(
                """
                SELECT public.dts_broker_epoch_id_v2(
                    :source_region,
                    :topic,
                    :partition_id,
                    :stream_generation_id,
                    :epoch_opening_id
                )
                """
            ),
            route,
        ).scalar_one()
    )


def _vector_hash(connection, routes_json: str) -> str:
    return str(
        connection.execute(
            text(
                """
                SELECT public.dts_initial_broker_epoch_vector_hash_v2(
                    CAST(:routes AS jsonb)
                )
                """
            ),
            {"routes": routes_json},
        ).scalar_one()
    )


def _bootstrap(
    connection,
    *,
    run_id: str,
    consumer_group: str,
    routes_json: str,
    vector_hash: str,
):
    return connection.execute(
        text(
            """
            SELECT public.bootstrap_initial_broker_epoch_v2(
                :run_id,
                :consumer_group,
                CAST(:routes AS jsonb),
                :vector_hash
            )
            """
        ),
        {
            "run_id": run_id,
            "consumer_group": consumer_group,
            "routes": routes_json,
            "vector_hash": vector_hash,
        },
    ).scalar_one()


@pytest.mark.skipif(
    not _postgres_tools_available(),
    reason="local PostgreSQL binaries are required for epoch-control migration",
)
def test_revision_79_whole_vector_bootstrap_acl_and_downgrade(
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
            (
                f"-p {postgres_port} -c listen_addresses=127.0.0.1 "
                "-c fsync=off"
            ),
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
            _seed_revision_78_shape(connection)

        # An unused expand migration must be reversible without touching 66-78.
        _run_alembic(backend_dir, database_url, "upgrade", REVISION_79)
        _run_alembic(backend_dir, database_url, "downgrade", REVISION_78)
        with engine.begin() as connection:
            assert connection.execute(
                text("SELECT to_regclass('public.dts_pipeline_control')")
            ).scalar_one_or_none() is None
        _run_alembic(backend_dir, database_url, "upgrade", REVISION_79)

        with engine.begin() as connection:
            base_routes: list[dict[str, object]] = [
                {
                    "source_region": "dom",
                    "topic": "dom-topic",
                    "partition_id": 0,
                    "current_next_offset": 101,
                    "consumer_group": "dom-dts-subscription-group",
                    "stream_generation_id": "dom-generation-1",
                    "epoch_opening_id": "dom-opening-1",
                },
                {
                    "source_region": "ovs",
                    "topic": "ovs-topic",
                    "partition_id": 3,
                    "current_next_offset": 707,
                    "consumer_group": "ovs-dts-subscription-group",
                    "stream_generation_id": "ovs-generation-1",
                    "epoch_opening_id": "ovs-opening-1",
                },
            ]
            routes: list[dict[str, object]] = []
            for route in base_routes:
                routes.append(
                    {
                        **route,
                        "source_partition_epoch_id": _epoch_id(
                            connection,
                            route,
                        ),
                    }
                )
            routes_json = json.dumps(
                list(reversed(routes)),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            vector_hash = _vector_hash(connection, routes_json)
            changed_route_groups = [dict(route) for route in routes]
            changed_route_groups[1]["consumer_group"] = (
                "ovs-dts-subscription-group-replaced"
            )
            changed_route_groups_json = json.dumps(
                list(reversed(changed_route_groups)),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            changed_route_groups_hash = _vector_hash(
                connection,
                changed_route_groups_json,
            )
            assert changed_route_groups_hash != vector_hash
            connection.execute(
                text(
                    """
                    INSERT INTO public.dts_ingest_checkpoints (
                        source_region, topic, partition_id, next_offset,
                        source_timestamp, source_position
                    ) VALUES
                        ('dom', 'dom-topic', 0, 101, 1, 'dom-position'),
                        ('ovs', 'ovs-topic', 3, 707, 2, 'ovs-position')
                    """
                )
            )

        missing_route_json = json.dumps(
            [routes[0]],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        with pytest.raises(DBAPIError) as missing_route:
            with engine.begin() as connection:
                _bootstrap(
                    connection,
                    run_id="H0-RUN-1",
                    consumer_group="tit-dts-v2-control-fleet",
                    routes_json=missing_route_json,
                    vector_hash=_vector_hash(connection, missing_route_json),
                )
        assert "INITIAL_EPOCH_BOOTSTRAP_CONFLICT" in str(missing_route.value)
        with engine.begin() as connection:
            assert connection.execute(
                text("SELECT count(*) FROM public.dts_pipeline_control")
            ).scalar_one() == 0
            assert connection.execute(
                text(
                    """
                    SELECT count(*)
                    FROM public.dts_source_partition_epochs
                    WHERE epoch_kind = 'BROKER'
                    """
                )
            ).scalar_one() == 0

        with pytest.raises(DBAPIError) as wrong_hash:
            with engine.begin() as connection:
                _bootstrap(
                    connection,
                    run_id="H0-RUN-1",
                    consumer_group="tit-dts-v2-control-fleet",
                    routes_json=routes_json,
                    vector_hash="0" * 64,
                )
        assert "MANIFEST_HASH_MISMATCH" in str(wrong_hash.value)

        with engine.begin() as connection:
            applied = _bootstrap(
                connection,
                run_id="H0-RUN-1",
                consumer_group="tit-dts-v2-control-fleet",
                routes_json=routes_json,
                vector_hash=vector_hash,
            )
            assert applied == {
                "status": "APPLIED",
                "route_count": 2,
                "vector_hash": vector_hash,
            }
            assert connection.execute(
                text(
                    """
                    SELECT control_id, mode, row_version,
                           projection_generation, consumer_group,
                           initial_h0_vector_hash,
                           initial_h0_bootstrap_run_id
                    FROM public.dts_pipeline_control
                    """
                )
            ).one() == (
                "PRIMARY",
                "V1_COMPAT_DUAL_CAPTURE",
                1,
                0,
                "tit-dts-v2-control-fleet",
                vector_hash,
                "H0-RUN-1",
            )
            assert connection.execute(
                text(
                    """
                    SELECT source_region, topic, partition_id, next_offset,
                           consumer_group, checkpoint_row_version,
                           is_current_epoch,
                           source_partition_epoch_id
                    FROM public.dts_ingest_checkpoints
                    ORDER BY source_region, topic, partition_id
                    """
                )
            ).all() == [
                (
                    "dom",
                    "dom-topic",
                    0,
                    101,
                    "dom-dts-subscription-group",
                    1,
                    True,
                    routes[0]["source_partition_epoch_id"],
                ),
                (
                    "ovs",
                    "ovs-topic",
                    3,
                    707,
                    "ovs-dts-subscription-group",
                    1,
                    True,
                    routes[1]["source_partition_epoch_id"],
                ),
            ]
            assert connection.execute(
                text(
                    """
                    SELECT count(*)
                    FROM public.dts_source_partition_epochs
                    WHERE epoch_kind = 'BROKER'
                      AND status = 'ACTIVE'
                      AND epoch_sequence = 1
                      AND predecessor_epoch_id IS NULL
                      AND start_offset = v2_epoch_bootstrap_floor
                      AND activation_mode = 'H0_BOOTSTRAP'
                      AND activation_manifest_hash IS NULL
                    """
                )
            ).scalar_one() == 2

        # Response-loss replay is a strict no-op, including reversed input order.
        with engine.begin() as connection:
            replay = _bootstrap(
                connection,
                run_id="H0-RUN-1",
                consumer_group="tit-dts-v2-control-fleet",
                routes_json=routes_json,
                vector_hash=vector_hash,
            )
            assert replay["status"] == "NOOP"
            assert connection.execute(
                text(
                    """
                    SELECT
                        (SELECT count(*) FROM public.dts_pipeline_control),
                        (SELECT count(*)
                         FROM public.dts_pipeline_bootstrap_audits),
                        (SELECT count(*)
                         FROM public.dts_source_partition_epochs
                         WHERE epoch_kind = 'BROKER')
                    """
                )
            ).one() == (1, 1, 2)

        with pytest.raises(DBAPIError) as changed_run:
            with engine.begin() as connection:
                _bootstrap(
                    connection,
                    run_id="H0-RUN-2",
                    consumer_group="tit-dts-v2-control-fleet",
                    routes_json=routes_json,
                    vector_hash=vector_hash,
                )
        assert "PARTIAL_OR_DIFFERENT_STATE" in str(changed_run.value)

        with pytest.raises(DBAPIError) as changed_route_group:
            with engine.begin() as connection:
                _bootstrap(
                    connection,
                    run_id="H0-RUN-1",
                    consumer_group="tit-dts-v2-control-fleet",
                    routes_json=changed_route_groups_json,
                    vector_hash=changed_route_groups_hash,
                )
        assert "PARTIAL_OR_DIFFERENT_STATE" in str(changed_route_group.value)

        with pytest.raises(DBAPIError) as runtime_execute:
            with engine.begin() as connection:
                connection.execute(text("SET LOCAL ROLE tit_dts_ingest_runtime"))
                _bootstrap(
                    connection,
                    run_id="H0-RUN-1",
                    consumer_group="tit-dts-v2-control-fleet",
                    routes_json=routes_json,
                    vector_hash=vector_hash,
                )
        assert "permission denied for function" in str(runtime_execute.value)

        # A role relying only on PUBLIC receives no bootstrap capability.
        with pytest.raises(DBAPIError) as public_execute:
            with engine.begin() as connection:
                connection.execute(
                    text("SET LOCAL ROLE tit_bootstrap_unauthorized")
                )
                _bootstrap(
                    connection,
                    run_id="H0-RUN-1",
                    consumer_group="tit-dts-v2-control-fleet",
                    routes_json=routes_json,
                    vector_hash=vector_hash,
                )
        assert "permission denied for function" in str(public_execute.value)

        with pytest.raises(DBAPIError) as immutable_audit:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        UPDATE public.dts_pipeline_bootstrap_audits
                        SET executed_by = 'rewritten'
                        WHERE bootstrap_run_id = 'H0-RUN-1'
                        """
                    )
                )
        assert "DTS_PIPELINE_BOOTSTRAP_AUDIT_IMMUTABLE" in str(
            immutable_audit.value
        )

        with engine.begin() as connection:
            issue_id = connection.execute(
                text(
                    """
                    SELECT public.dts_ingest_issue_id_v2(
                        repeat('a', 64), repeat('b', 64), 'key-v1'
                    )
                    """
                )
            ).scalar_one()
            connection.execute(
                text(
                    """
                    INSERT INTO public.dts_ingest_issues (
                        ingest_issue_id,
                        connector_delivery_identity_hash,
                        payload_hmac,
                        hmac_key_version,
                        current_error_codes,
                        status,
                        diagnostic_summary
                    ) VALUES (
                        :issue_id,
                        repeat('a', 64),
                        repeat('b', 64),
                        'key-v1',
                        '["BROKER_GENERATION_UNVERIFIED"]'::jsonb,
                        'OPEN',
                        '{"safe_code":"BROKER_GENERATION_UNVERIFIED"}'::jsonb
                    )
                    """
                ),
                {"issue_id": issue_id},
            )

        with pytest.raises(DBAPIError) as issue_delete:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "DELETE FROM public.dts_ingest_issues "
                        "WHERE ingest_issue_id = :issue_id"
                    ),
                    {"issue_id": issue_id},
                )
        assert "DTS_INGEST_ISSUE_DELETE_FORBIDDEN" in str(issue_delete.value)

        _run_alembic_expect_failure(
            backend_dir,
            database_url,
            "refusing DTS v2 epoch-control downgrade: control data exists",
            "downgrade",
            REVISION_78,
        )
        with engine.begin() as connection:
            assert connection.execute(
                text("SELECT version_num FROM public.alembic_version")
            ).scalar_one() == REVISION_79
    finally:
        engine.dispose()
        subprocess.run(
            [pg_ctl, "-D", str(data_dir), "-m", "fast", "-w", "stop"],
            check=True,
            capture_output=True,
            text=True,
        )
