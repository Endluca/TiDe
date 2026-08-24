from __future__ import annotations

from decimal import Decimal
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
from sqlalchemy.exc import IntegrityError

from test_dts_v2_shadow_source_writer_postgres import (
    _install_revision_65_fixture,
)


POSTGRES_BINARIES = ("initdb", "pg_ctl", "postgres")


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


def _version_parameters(
    *,
    offset: int,
    source_key: str,
    source_key_data: object,
    source_key_type: str,
    source_key_numeric: Decimal | None,
    source_key_text: str | None,
    source_schema_profile_id: str = "dom_teacher:v1",
    source_field_types: object | None = None,
    record_id_type: str = "none",
    record_id_numeric: Decimal | None = None,
    record_id_text: str | None = None,
) -> dict[str, object]:
    return {
        "offset": offset,
        "source_key": source_key,
        "source_key_data": json.dumps(
            source_key_data,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        "source_key_type": source_key_type,
        "source_key_numeric": source_key_numeric,
        "source_key_text": source_key_text,
        "source_schema_profile_id": source_schema_profile_id,
        "source_field_types": json.dumps(
            {"id": "NUMERIC"}
            if source_field_types is None
            else source_field_types,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        "record_id_type": record_id_type,
        "record_id_numeric": record_id_numeric,
        "record_id_text": record_id_text,
    }


INSERT_VERSION_SQL = text(
    """
    INSERT INTO public.dts_source_row_versions (
        source_region,
        source_partition_epoch_id,
        topic,
        partition_id,
        offset_value,
        version_kind,
        source_table,
        source_schema_profile_id,
        source_field_types,
        source_key,
        source_key_data,
        source_key_type,
        source_key_numeric,
        source_key_text,
        operation,
        before_row,
        after_row,
        source_timestamp,
        record_id_type,
        record_id_numeric,
        record_id_text,
        source_position,
        source_row_revision,
        snapshot_id,
        snapshot_as_of,
        covered_through_offsets,
        diff_step,
        source_table_publish_generation,
        protected_source_row_hash
    ) VALUES (
        'dom',
        'epoch:v1:test',
        'topic-v2',
        0,
        :offset,
        'CDC',
        'dom_teacher',
        :source_schema_profile_id,
        CAST(:source_field_types AS jsonb),
        :source_key,
        CAST(:source_key_data AS jsonb),
        :source_key_type,
        :source_key_numeric,
        :source_key_text,
        'INSERT',
        NULL,
        '{}'::jsonb,
        NULL,
        :record_id_type,
        :record_id_numeric,
        :record_id_text,
        '{}'::jsonb,
        :offset,
        NULL,
        NULL,
        NULL,
        NULL,
        NULL,
        repeat('a', 64)
    )
    """
)


@pytest.mark.skipif(
    not _postgres_tools_available(),
    reason="local PostgreSQL binaries are required for typed-key checks",
)
def test_revision_66_enforces_typed_source_key_evidence_on_postgresql(
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
            "20260822_66_dts_v2_shadow",
        )

        with engine.begin() as connection:
            runtime_privileges = connection.execute(
                text(
                    """
                    SELECT role_name, table_name, privilege_name,
                           has_table_privilege(
                               role_name,
                               'public.' || table_name,
                               privilege_name
                           )
                    FROM unnest(
                        ARRAY['tit_growth_app', 'tit_dts_ingest_runtime']
                    ) AS roles(role_name)
                    CROSS JOIN unnest(
                        ARRAY[
                            'dts_source_partition_epochs',
                            'dts_source_row_versions'
                        ]
                    ) AS tables(table_name)
                    CROSS JOIN unnest(
                        ARRAY['SELECT', 'INSERT', 'UPDATE', 'DELETE']
                    ) AS privileges(privilege_name)
                    ORDER BY role_name, table_name, privilege_name
                    """
                )
            ).all()
            assert len(runtime_privileges) == 16
            assert all(not row[3] for row in runtime_privileges)

            connection.execute(
                text(
                    """
                    INSERT INTO public.dts_source_partition_epochs (
                        source_region,
                        source_partition_epoch_id,
                        topic,
                        partition_id,
                        epoch_kind,
                        status,
                        stream_generation_id,
                        epoch_opening_id,
                        epoch_sequence,
                        start_offset,
                        v2_epoch_bootstrap_floor,
                        activation_mode
                    ) VALUES (
                        'dom',
                        'epoch:v1:test',
                        'topic-v2',
                        0,
                        'BROKER',
                        'ACTIVE',
                        'generation-1',
                        'opening-1',
                        1,
                        0,
                        0,
                        'H0_BOOTSTRAP'
                    )
                    """
                )
            )
            connection.execute(
                INSERT_VERSION_SQL,
                _version_parameters(
                    offset=1,
                    source_key="1",
                    source_key_data={"id": 1},
                    source_key_type="NUMERIC",
                    source_key_numeric=Decimal("1.000"),
                    source_key_text=None,
                ),
            )
            connection.execute(
                INSERT_VERSION_SQL,
                _version_parameters(
                    offset=2,
                    source_key="001",
                    source_key_data={"id": "001"},
                    source_key_type="TEXT",
                    source_key_numeric=None,
                    source_key_text="001",
                    source_field_types={"id": "TEXT"},
                ),
            )

        invalid_rows = (
            _version_parameters(
                offset=11,
                source_key="1",
                source_key_data={"id": "1"},
                source_key_type="NUMERIC",
                source_key_numeric=Decimal("1"),
                source_key_text=None,
            ),
            _version_parameters(
                offset=12,
                source_key="1",
                source_key_data={"id": 1},
                source_key_type="TEXT",
                source_key_numeric=None,
                source_key_text="1",
            ),
            _version_parameters(
                offset=13,
                source_key="1.0",
                source_key_data={"id": 1},
                source_key_type="NUMERIC",
                source_key_numeric=Decimal("1.0"),
                source_key_text=None,
            ),
            _version_parameters(
                offset=14,
                source_key="001",
                source_key_data={"id": "001", "extra": True},
                source_key_type="TEXT",
                source_key_numeric=None,
                source_key_text="001",
            ),
            _version_parameters(
                offset=15,
                source_key="1",
                source_key_data={"id": 1},
                source_key_type="NUMERIC",
                source_key_numeric=None,
                source_key_text=None,
                record_id_type="numeric",
                record_id_numeric=Decimal("1"),
            ),
        )
        for parameters in invalid_rows:
            with pytest.raises(IntegrityError) as caught:
                with engine.begin() as connection:
                    connection.execute(INSERT_VERSION_SQL, parameters)
            assert caught.value.orig.diag.constraint_name == (
                "ck_dts_source_row_version_source_key"
            )

        invalid_profile_rows = (
            (
                _version_parameters(
                    offset=21,
                    source_key="21",
                    source_key_data={"id": 21},
                    source_key_type="NUMERIC",
                    source_key_numeric=Decimal("21"),
                    source_key_text=None,
                    source_schema_profile_id="",
                ),
                "ck_dts_source_row_version_schema_profile",
            ),
            (
                _version_parameters(
                    offset=22,
                    source_key="22",
                    source_key_data={"id": 22},
                    source_key_type="NUMERIC",
                    source_key_numeric=Decimal("22"),
                    source_key_text=None,
                    source_field_types=[],
                ),
                "ck_dts_source_row_version_field_types",
            ),
        )
        for parameters, constraint_name in invalid_profile_rows:
            with pytest.raises(IntegrityError) as caught:
                with engine.begin() as connection:
                    connection.execute(INSERT_VERSION_SQL, parameters)
            assert caught.value.orig.diag.constraint_name == constraint_name

        with engine.connect() as connection:
            stored = connection.execute(
                text(
                    """
                    SELECT source_key, source_key_type,
                           trim_scale(source_key_numeric)::text,
                           source_key_text, source_schema_profile_id,
                           source_field_types
                    FROM public.dts_source_row_versions
                    ORDER BY offset_value
                    """
                )
            ).all()
            assert stored == [
                (
                    "1",
                    "NUMERIC",
                    "1",
                    None,
                    "dom_teacher:v1",
                    {"id": "NUMERIC"},
                ),
                (
                    "001",
                    "TEXT",
                    None,
                    "001",
                    "dom_teacher:v1",
                    {"id": "TEXT"},
                ),
            ]
    finally:
        engine.dispose()
        subprocess.run(
            [pg_ctl, "-D", str(data_dir), "-m", "fast", "-w", "stop"],
            check=False,
            capture_output=True,
            text=True,
        )
