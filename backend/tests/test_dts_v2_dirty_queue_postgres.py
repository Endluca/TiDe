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
REVISION_79 = "20260822_79_dts_v2_epoch_control"
REVISION_80 = "20260822_80_dts_v2_dirty_queue"


def _postgres_tools_available() -> bool:
    return all(shutil.which(binary) for binary in POSTGRES_BINARIES)


def _run_alembic(
    backend_dir: Path,
    database_url: str,
    *args: str,
    expect_error: str | None = None,
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
    if expect_error is None:
        assert result.returncode == 0, output
    else:
        assert result.returncode != 0, "fail-closed migration unexpectedly passed"
        assert expect_error in output


def _seed_revision_79_shape(connection) -> None:
    connection.execute(
        text(
            f"""
            CREATE EXTENSION IF NOT EXISTS pgcrypto;
            CREATE ROLE tit_growth_app LOGIN NOINHERIT NOSUPERUSER
                NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
            CREATE ROLE tit_dts_ingest_runtime LOGIN NOINHERIT NOSUPERUSER
                NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
            CREATE ROLE tit_teacher_crud LOGIN NOINHERIT NOSUPERUSER
                NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
            CREATE ROLE tide_support_ticket_owner LOGIN NOINHERIT NOSUPERUSER
                NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;

            CREATE TABLE public.alembic_version (
                version_num varchar(64) PRIMARY KEY
            );
            INSERT INTO public.alembic_version VALUES ('{REVISION_79}');

            CREATE TABLE public.dts_source_row_versions (
                source_region varchar(8) NOT NULL,
                source_table varchar(128) NOT NULL,
                source_key varchar(512) NOT NULL,
                source_row_revision bigint,
                protected_source_row_hash varchar(64) NOT NULL,
                version_kind varchar(32) NOT NULL,
                operation varchar(48) NOT NULL
            );
            CREATE TABLE public.dts_source_rows (
                source_region varchar(8) NOT NULL,
                source_table varchar(128) NOT NULL,
                source_key varchar(512) NOT NULL,
                provenance_state varchar(32),
                source_row_revision bigint,
                source_payload_hash varchar(64),
                is_deleted boolean NOT NULL DEFAULT false
            );

            CREATE TABLE public.dts_dirty_keys (
                key_type varchar(32) NOT NULL,
                key_part_1 varchar(256) NOT NULL,
                key_part_2 varchar(256) NOT NULL,
                status varchar(16) NOT NULL,
                pending_event_count integer NOT NULL,
                attempt_count integer NOT NULL,
                last_source_region varchar(8) NOT NULL,
                last_source_table varchar(128),
                last_topic varchar(512) NOT NULL,
                last_partition integer NOT NULL,
                last_offset bigint NOT NULL,
                issue_codes jsonb NOT NULL,
                last_error_code varchar(128),
                next_attempt_at timestamptz,
                claimed_at timestamptz,
                claimed_by varchar(128),
                row_version integer NOT NULL,
                first_seen_at timestamptz NOT NULL DEFAULT clock_timestamp(),
                last_seen_at timestamptz NOT NULL DEFAULT clock_timestamp(),
                source_region varchar(8),
                required_work_revision bigint,
                claimed_through_work_revision bigint,
                completed_work_revision bigint,
                last_input_identity_hash varchar(64),
                last_input_revision bigint,
                work_generation bigint,
                dead_generation bigint,
                blocked_by jsonb,
                lease_owner_kind varchar(32),
                lease_owner varchar(128),
                lease_token varchar(160),
                lease_expires_at timestamptz,
                CONSTRAINT pk_dts_dirty_keys PRIMARY KEY (
                    key_type,key_part_1,key_part_2
                ),
                CONSTRAINT ck_dts_dirty_key_type CHECK (
                    key_type IN ('COURSE','TEACHER','TEACHER_STUDENT',
                                 'LABEL','COMPLAINT_CATEGORY')
                ),
                CONSTRAINT ck_dts_dirty_key_status CHECK (
                    status IN ('PENDING','PROCESSING','RETRY','COMPLETED')
                ),
                CONSTRAINT ck_dts_dirty_key_region CHECK (
                    last_source_region IN ('ovs','dom')
                ),
                CONSTRAINT ck_dts_dirty_key_counters CHECK (
                    pending_event_count >= 1 AND attempt_count >= 0
                    AND last_partition >= 0 AND last_offset >= 0
                    AND row_version >= 1
                )
            );
            CREATE INDEX ix_dts_dirty_keys_ready
                ON public.dts_dirty_keys(status,next_attempt_at,last_seen_at);
            CREATE INDEX ix_dts_dirty_keys_pending_fifo
                ON public.dts_dirty_keys(
                    last_seen_at,key_type,key_part_1,key_part_2
                ) WHERE status='PENDING';
            CREATE INDEX ix_dts_dirty_keys_retry_due
                ON public.dts_dirty_keys(
                    next_attempt_at,last_seen_at,key_type,key_part_1,key_part_2
                ) WHERE status='RETRY';

            INSERT INTO public.dts_dirty_keys (
                key_type,key_part_1,key_part_2,status,pending_event_count,
                attempt_count,last_source_region,last_source_table,last_topic,
                last_partition,last_offset,issue_codes,row_version
            ) VALUES (
                'COURSE','legacy-1','','PENDING',1,0,'dom','dom_appoint',
                'legacy-topic',0,10,'[]'::jsonb,1
            );
            """
        )
    )


def _set_role(connection, role: str) -> None:
    connection.execute(text(f"SET ROLE {role}"))


def _reset_role(connection) -> None:
    connection.execute(text("RESET ROLE"))


def _claim(connection) -> dict[str, object]:
    _set_role(connection, "tit_growth_app")
    row = connection.execute(
        text(
            """
            SELECT * FROM public.claim_domain_dirty_keys_v2(
                'domain-test',1,60
            )
            """
        )
    ).mappings().one()
    _reset_role(connection)
    return dict(row)


@pytest.mark.skipif(
    not _postgres_tools_available(),
    reason="local PostgreSQL binaries are required for dirty queue migration",
)
def test_revision_80_real_state_machine_and_fail_closed_legacy_drain(
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
            _seed_revision_79_shape(connection)

        _run_alembic(
            backend_dir,
            database_url,
            "upgrade",
            REVISION_80,
            expect_error="DIRTY_V2_MIGRATION_LEGACY_NOT_DRAINED",
        )
        with engine.begin() as connection:
            assert connection.execute(
                text("SELECT version_num FROM public.alembic_version")
            ).scalar_one() == REVISION_79
            connection.execute(
                text(
                    "UPDATE public.dts_dirty_keys SET status='COMPLETED'"
                )
            )
        _run_alembic(backend_dir, database_url, "upgrade", REVISION_80)

        with engine.begin() as connection:
            assert connection.execute(
                text("SELECT count(*) FROM public.dts_dirty_keys")
            ).scalar_one() == 0
            archive = connection.execute(
                text(
                    """
                    SELECT legacy_row,legacy_row_hash
                    FROM public.dts_dirty_keys_legacy_archive_v80
                    """
                )
            ).mappings().one()
            assert archive["legacy_row"]["key_part_1"] == "legacy-1"
            assert len(archive["legacy_row_hash"]) == 64

            first_hash = "a" * 64
            connection.execute(
                text(
                    """
                    INSERT INTO public.dts_source_row_versions VALUES (
                        'dom','dom_appoint','9001',1,:hash,'CDC','INSERT'
                    )
                    """
                ),
                {"hash": first_hash},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO public.dts_source_rows VALUES (
                        'dom','dom_appoint','9001','V2_CONFIRMED',1,:hash,false
                    )
                    """
                ),
                {"hash": first_hash},
            )
            _set_role(connection, "tit_dts_ingest_runtime")
            enqueue = connection.execute(
                text(
                    """
                    SELECT public.enqueue_dirty_from_source_revision_v2(
                        'dom','dom_appoint','9001',1,'COURSE','9001',''
                    )
                    """
                )
            ).scalar_one()
            assert enqueue["status"] == "ENQUEUED"
            savepoint = connection.begin_nested()
            with pytest.raises(DBAPIError, match="permission denied"):
                connection.execute(
                    text(
                        "INSERT INTO public.dts_dirty_keys "
                        "(source_region,key_type,key_part_1,key_part_2) "
                        "VALUES ('dom','COURSE','forged','')"
                    )
                )
            savepoint.rollback()
            _reset_role(connection)

        with engine.begin() as connection:
            first_claim = _claim(connection)
            assert first_claim["claimed_work_revision"] == 1

            second_hash = "b" * 64
            connection.execute(
                text(
                    """
                    INSERT INTO public.dts_source_row_versions VALUES (
                        'dom','dom_appoint','9001',2,:hash,'CDC','UPDATE'
                    )
                    """
                ),
                {"hash": second_hash},
            )
            connection.execute(
                text(
                    """
                    UPDATE public.dts_source_rows
                    SET source_row_revision=2,source_payload_hash=:hash
                    WHERE source_region='dom'
                      AND source_table='dom_appoint' AND source_key='9001'
                    """
                ),
                {"hash": second_hash},
            )
            _set_role(connection, "tit_dts_ingest_runtime")
            second_enqueue = connection.execute(
                text(
                    """
                    SELECT public.enqueue_dirty_from_source_revision_v2(
                        'dom','dom_appoint','9001',2,'COURSE','9001',''
                    )
                    """
                )
            ).scalar_one()
            assert second_enqueue["dirty_work_revision"] == 2
            _reset_role(connection)
            processing = connection.execute(
                text(
                    """
                    SELECT status,required_work_revision,
                           claimed_through_work_revision
                    FROM public.dts_dirty_keys
                    """
                )
            ).mappings().one()
            assert dict(processing) == {
                "status": "PROCESSING",
                "required_work_revision": 2,
                "claimed_through_work_revision": 1,
            }

            _set_role(connection, "tit_growth_app")
            completed_old_claim = connection.execute(
                text(
                    """
                    SELECT public.complete_domain_dirty_key_v2(
                        :source_region,:key_type,:key_part_1,:key_part_2,
                        :lease_token,:claimed_work_revision,:row_version
                    )
                    """
                ),
                first_claim,
            ).scalar_one()
            _reset_role(connection)
            assert completed_old_claim["status"] == "PENDING"

            second_claim = _claim(connection)
            dependencies = json.dumps(
                [
                    {
                        "dependency_type": "TEACHER",
                        "dependency_region": "dom",
                        "dependency_key": "7",
                        "source_revision": 1,
                        "dependency_hash": "c" * 64,
                    }
                ],
                separators=(",", ":"),
            )
            _set_role(connection, "tit_growth_app")
            waited = connection.execute(
                text(
                    """
                    SELECT public.wait_domain_dirty_key_v2(
                        :source_region,:key_type,:key_part_1,:key_part_2,
                        :lease_token,:claimed_work_revision,:row_version,
                        CAST(:dependencies AS jsonb)
                    )
                    """
                ),
                {**second_claim, "dependencies": dependencies},
            ).scalar_one()
            _reset_role(connection)
            assert waited["status"] == "WAITING_DEPENDENCY"
            assert connection.execute(
                text("SELECT count(*) FROM public.dts_dirty_key_dependencies")
            ).scalar_one() == 1

            assert connection.execute(
                text(
                    """
                    SELECT public.wake_dts_dirty_keys_for_dependency_v2(
                        'TEACHER','dom','7',2,:hash
                    )
                    """
                ),
                {"hash": "d" * 64},
            ).scalar_one() == 1
            woken = connection.execute(
                text(
                    """
                    SELECT status,required_work_revision
                    FROM public.dts_dirty_keys
                    """
                )
            ).mappings().one()
            assert dict(woken) == {
                "status": "PENDING",
                "required_work_revision": 3,
            }

            dead_result = None
            for attempt in range(1, 9):
                retry_claim = _claim(connection)
                _set_role(connection, "tit_growth_app")
                dead_result = connection.execute(
                    text(
                        """
                        SELECT public.fail_domain_dirty_key_v2(
                            :source_region,:key_type,:key_part_1,:key_part_2,
                            :lease_token,:claimed_work_revision,:row_version,
                            'DOMAIN_PROJECTOR_TRANSIENT'
                        )
                        """
                    ),
                    retry_claim,
                ).scalar_one()
                _reset_role(connection)
                assert dead_result["attempt_count"] == attempt
                if attempt < 8:
                    assert dead_result["status"] == "RETRY"
                    connection.execute(
                        text(
                            """
                            UPDATE public.dts_dirty_keys
                            SET next_attempt_at=transaction_timestamp()
                            WHERE source_region='dom' AND key_type='COURSE'
                              AND key_part_1='9001' AND key_part_2=''
                            """
                        )
                    )
            assert dead_result is not None
            assert dead_result["status"] == "DEAD"
            assert dead_result["dead_generation"] == 1

            recovered = connection.execute(
                text(
                    """
                    SELECT public.recover_dts_dirty_key_v2(
                        'dom','COURSE','9001','',1,:row_version,
                        'operator-request-1','verified source replay requested'
                    )
                    """
                ),
                {"row_version": dead_result["row_version"]},
            ).scalar_one()
            assert recovered["status"] == "PENDING"
            assert recovered["dirty_work_revision"] == 4
            integrity = connection.execute(
                text(
                    """
                    SELECT dirty.required_work_revision,
                           max(input.dirty_work_revision) AS max_input_revision,
                           dirty.work_generation,dirty.dead_generation
                    FROM public.dts_dirty_keys dirty
                    JOIN public.dts_dirty_key_inputs input USING (
                        source_region,key_type,key_part_1,key_part_2
                    )
                    GROUP BY dirty.required_work_revision,
                             dirty.work_generation,dirty.dead_generation
                    """
                )
            ).mappings().one()
            assert dict(integrity) == {
                "required_work_revision": 4,
                "max_input_revision": 4,
                "work_generation": 2,
                "dead_generation": 1,
            }
    finally:
        engine.dispose()
        subprocess.run(
            [pg_ctl, "-D", str(data_dir), "-m", "fast", "-w", "stop"],
            check=False,
            capture_output=True,
            text=True,
        )
