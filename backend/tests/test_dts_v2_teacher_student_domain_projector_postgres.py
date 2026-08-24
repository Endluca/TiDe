from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import shutil
import socket
import subprocess

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

import app.dts_v2_teacher_student_domain_projector as relation_domain
import app.dts_v2_teacher_domain_projector as teacher_domain
from test_relationship_favorite_schema_postgres import (
    DOM_STUDENT,
    POSTGRES_BINARIES,
    _insert_relationship_version,
    _run_alembic,
    _seed_revision_73_shape,
)


REVISION_74 = "20260822_74_favorite_schema"


def _postgres_tools_available() -> bool:
    return all(shutil.which(binary) for binary in POSTGRES_BINARIES)


def _version(
    *,
    offset: int,
    revision: int,
    operation: str,
    before: dict | None,
    after: dict | None,
) -> relation_domain._RelationshipVersion:
    return relation_domain._RelationshipVersion(
        source_region="dom",
        source_partition_epoch_id="epoch-1",
        topic="relationship-topic",
        partition_id=0,
        offset_value=offset,
        source_table="dom_teacher_favorite",
        source_record_id="1",
        source_record_id_type="NUMERIC",
        source_row_revision=revision,
        operation=operation,
        before_row=before,
        after_row=after,
        source_field_types={
            "id": "NUMERIC",
            "tea_id": "TEXT",
            "student_token": "TEXT",
            "add_time": "TEMPORAL",
        },
        source_timestamp=datetime(2026, 8, 22, revision, tzinfo=timezone.utc),
    )


def _image(add_time: str) -> dict:
    return {
        "id": 1,
        "tea_id": "T1",
        "student_token": DOM_STUDENT,
        "add_time": add_time,
    }


@pytest.mark.skipif(
    not _postgres_tools_available(),
    reason="local PostgreSQL binaries are required for relationship projector test",
)
def test_relationship_event_current_update_and_replay_are_transactional(
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
    pair = relation_domain._Pair("dom", "TEXT", "T1", DOM_STUDENT)
    try:
        with engine.begin() as connection:
            _seed_revision_73_shape(connection)
        _run_alembic(backend_dir, database_url, "upgrade", REVISION_74)

        first_image = _image("2026-08-20T00:00:00+00:00")
        first = _version(
            offset=1,
            revision=1,
            operation="INSERT",
            before=None,
            after=first_image,
        )
        with engine.begin() as connection:
            _insert_relationship_version(
                connection,
                offset=1,
                revision=1,
                operation="INSERT",
            )
            assert relation_domain._insert_relationship_event(
                connection,
                relation_domain._relationship_event_plan(first),
            )
            latest = relation_domain._read_latest_pair_event(
                connection,
                pair=pair,
            )
            assert latest is not None
            assert relation_domain._upsert_relationship_current(
                connection,
                pair=pair,
                current=relation_domain._CurrentRelationship(
                    is_favorited=True,
                    favorite_evidence_status="CONFIRMED",
                    is_blocked=None,
                    block_evidence_status="SOURCE_MISSING",
                    blacklist_threshold_state="SOURCE_MISSING",
                    blacklist_threshold_evidence_status="SOURCE_MISSING",
                    blacklist_source_collection_complete=False,
                    blacklist_distinct_active_student_count=0,
                    blacklist_source_missing_student_count=0,
                    blacklist_active_student_token_set_hash="a" * 64,
                    blacklist_source_missing_student_token_set_hash="b" * 64,
                ),
                latest_event=latest,
            )

        corrected_image = _image("2026-08-19T00:00:00+00:00")
        corrected = _version(
            offset=2,
            revision=2,
            operation="UPDATE",
            before=first_image,
            after=corrected_image,
        )
        with engine.begin() as connection:
            _insert_relationship_version(
                connection,
                offset=2,
                revision=2,
                operation="UPDATE",
            )
            plan = relation_domain._relationship_event_plan(corrected)
            assert relation_domain._insert_relationship_event(connection, plan)
            latest = relation_domain._read_latest_pair_event(
                connection,
                pair=pair,
            )
            assert latest is not None
            assert relation_domain._upsert_relationship_current(
                connection,
                pair=pair,
                current=relation_domain._CurrentRelationship(
                    is_favorited=True,
                    favorite_evidence_status="CONFIRMED",
                    is_blocked=False,
                    block_evidence_status="CONFIRMED",
                    blacklist_threshold_state="SUPPRESSED",
                    blacklist_threshold_evidence_status="CONFIRMED",
                    blacklist_source_collection_complete=True,
                    blacklist_distinct_active_student_count=0,
                    blacklist_source_missing_student_count=0,
                    blacklist_active_student_token_set_hash="a" * 64,
                    blacklist_source_missing_student_token_set_hash="b" * 64,
                ),
                latest_event=latest,
            )

        with engine.begin() as connection:
            assert not relation_domain._insert_relationship_event(
                connection,
                relation_domain._relationship_event_plan(corrected),
            )
            latest = relation_domain._read_latest_pair_event(
                connection,
                pair=pair,
            )
            assert latest is not None
            assert not relation_domain._upsert_relationship_current(
                connection,
                pair=pair,
                current=relation_domain._CurrentRelationship(
                    is_favorited=True,
                    favorite_evidence_status="CONFIRMED",
                    is_blocked=False,
                    block_evidence_status="CONFIRMED",
                    blacklist_threshold_state="SUPPRESSED",
                    blacklist_threshold_evidence_status="CONFIRMED",
                    blacklist_source_collection_complete=True,
                    blacklist_distinct_active_student_count=0,
                    blacklist_source_missing_student_count=0,
                    blacklist_active_student_token_set_hash="a" * 64,
                    blacklist_source_missing_student_token_set_hash="b" * 64,
                ),
                latest_event=latest,
            )
            row = connection.execute(
                text(
                    """
                    SELECT is_favorited,is_blocked,row_version,
                           last_source_row_revision,
                           last_business_effective_at
                    FROM public.teacher_student_relationship_current
                    WHERE source_region='dom' AND teacher_id='T1'
                      AND student_token=:student_token
                    """
                ),
                {"student_token": DOM_STUDENT},
            ).mappings().one()
            assert row["is_favorited"] is True
            assert row["is_blocked"] is False
            assert row["row_version"] == 2
            assert row["last_source_row_revision"] == 2
            assert row["last_business_effective_at"] == datetime(
                2026, 8, 19, tzinfo=timezone.utc
            )
            connection.execute(
                text(
                    """
                    CREATE TABLE public.domain_aggregate_revisions (
                        aggregate_type text NOT NULL,
                        canonical_key jsonb NOT NULL,
                        aggregate_state jsonb NOT NULL
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO public.domain_aggregate_revisions (
                        aggregate_type,canonical_key,aggregate_state
                    ) VALUES (
                        'TEACHER_STUDENT',
                            jsonb_build_object(
                                'source_region','dom','teacher_id','T1',
                                'student_token',CAST(:student_token AS text)
                            ),
                        jsonb_build_object(
                            'relationship_current',jsonb_build_object(
                                'favorite_evidence_status','SOURCE_MISSING',
                                'block_evidence_status','CONFIRMED'
                            )
                        )
                    )
                    """
                ),
                {"student_token": DOM_STUDENT},
            )

            teacher_relationship = teacher_domain._read_teacher_relationships(
                connection,
                source_region="dom",
                teacher_id="T1",
            )[0]
            assert (
                teacher_relationship["favorite_evidence_status"]
                == "SOURCE_MISSING"
            )
            assert teacher_relationship["block_evidence_status"] == "CONFIRMED"
    finally:
        engine.dispose()
        subprocess.run(
            [pg_ctl, "-D", str(data_dir), "-m", "fast", "-w", "stop"],
            check=True,
            capture_output=True,
            text=True,
        )
