from __future__ import annotations

import json
from pathlib import Path
import shutil
import socket
import subprocess

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import DBAPIError

from test_dts_v2_source_current_guards_postgres import (
    _postgres_tools_available,
    _run_alembic,
    _seed_external_personalized_catalog,
)


TOPIC = "topic-scope-v2"
REGION = "dom"
SOURCE_TABLE = "dom_teacher"
EPOCH_GROUP = "scope-source-group"


def _call(connection, sql: str, parameters: dict[str, object]):
    connection.execute(text("SET LOCAL ROLE tit_growth_app"))
    result = connection.execute(text(sql), parameters).scalar_one()
    connection.execute(text("RESET ROLE"))
    return result


@pytest.mark.skipif(
    not _postgres_tools_available(),
    reason="local PostgreSQL binaries are required for scope coordinator tests",
)
def test_revision_83_blocks_false_complete_and_is_response_lost_safe(
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
                "tide_support_ticket_owner",
            ):
                connection.execute(
                    text(
                        f"CREATE ROLE {role_name} LOGIN NOINHERIT "
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
        _run_alembic(
            backend_dir,
            database_url,
            "upgrade",
            "20260822_83_dts_v2_scope",
        )

        with engine.begin() as connection:
            assert connection.execute(
                text(
                    """
                    SELECT
                      has_function_privilege(
                        'tit_growth_app',
                        'public.begin_source_snapshot_candidate_v2('
                        'text,text,text,text,text,text,text,timestamptz,text,'
                        'jsonb,timestamptz,timestamptz,text,integer,bigint,bigint)',
                        'EXECUTE'
                      )
                      AND NOT has_function_privilege(
                        'tit_dts_ingest_runtime',
                        'public.begin_source_snapshot_candidate_v2('
                        'text,text,text,text,text,text,text,timestamptz,text,'
                        'jsonb,timestamptz,timestamptz,text,integer,bigint,bigint)',
                        'EXECUTE'
                      )
                      AND NOT has_table_privilege(
                        'tit_growth_app',
                        'public.dts_source_scope_states','INSERT'
                      )
                      AND has_table_privilege(
                        'tit_growth_app',
                        'public.dts_source_scope_states','SELECT'
                      )
                    """
                )
            ).scalar_one()

        # An unused rev83 is reversible, then upgrades cleanly again.
        _run_alembic(
            backend_dir,
            database_url,
            "downgrade",
            "20260822_82_v2_runtime_acl",
        )
        _run_alembic(
            backend_dir,
            database_url,
            "upgrade",
            "20260822_83_dts_v2_scope",
        )

        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO public.dts_ingest_checkpoints (
                        source_region,topic,partition_id,next_offset,
                        source_timestamp,source_position,updated_at
                    ) VALUES (:region,:topic,0,0,0,'scope-fixture',CURRENT_TIMESTAMP)
                    """
                ),
                {"region": REGION, "topic": TOPIC},
            )
            epoch_id = connection.execute(
                text(
                    "SELECT public.dts_broker_epoch_id_v2("
                    ":region,:topic,0,'generation-1','opening-1')"
                ),
                {"region": REGION, "topic": TOPIC},
            ).scalar_one()
            routes = [
                {
                    "source_region": REGION,
                    "topic": TOPIC,
                    "partition_id": 0,
                    "current_next_offset": 0,
                    "consumer_group": EPOCH_GROUP,
                    "stream_generation_id": "generation-1",
                    "epoch_opening_id": "opening-1",
                    "source_partition_epoch_id": epoch_id,
                }
            ]
            vector_hash = connection.execute(
                text(
                    "SELECT public.dts_initial_broker_epoch_vector_hash_v2("
                    "CAST(:routes AS jsonb))"
                ),
                {"routes": json.dumps(routes)},
            ).scalar_one()
            connection.execute(
                text(
                    "SELECT public.bootstrap_initial_broker_epoch_v2("
                    "'scope-h0','scope-control-fleet',CAST(:routes AS jsonb),"
                    ":vector_hash)"
                ),
                {"routes": json.dumps(routes), "vector_hash": vector_hash},
            )
            empty_content_hash = connection.execute(
                text(
                    "SELECT public.dts_canonical_json_sha256_v1('[]'::jsonb)"
                )
            ).scalar_one()
            fence = [
                {
                    "source_region": REGION,
                    "source_partition_epoch_id": epoch_id,
                    "topic": TOPIC,
                    "partition_id": 0,
                    "start_next_offset": 0,
                    "end_next_offset": 0,
                }
            ]
            fence_hash = connection.execute(
                text(
                    "SELECT public.dts_canonical_json_sha256_v1("
                    "public.dts_normalize_scope_partition_offsets_v2("
                    "CAST(:fence AS jsonb),:region))"
                ),
                {"fence": json.dumps(fence), "region": REGION},
            ).scalar_one()

        # A concurrent table-head lock prevents a second publisher from
        # observing or advancing a partially changed generation.
        lock_connection = engine.connect()
        lock_transaction = lock_connection.begin()
        try:
            lock_connection.execute(
                text(
                    "SELECT 1 FROM public.dts_source_table_publish_generations "
                    "WHERE source_region='dom' AND source_table='dom_teacher' "
                    "FOR UPDATE"
                )
            )
            with pytest.raises(DBAPIError) as lock_timeout:
                with engine.begin() as connection:
                    connection.execute(text("SET LOCAL lock_timeout='250ms'"))
                    _call(
                        connection,
                        """
                        SELECT public.begin_source_snapshot_candidate_v2(
                            'begin-lock-timeout','scope-lock-timeout','dom',
                            'dom_teacher','CURRENT','GLOBAL','*',
                            CURRENT_TIMESTAMP,'token-lock-timeout',
                            CAST(:fence AS jsonb),NULL,NULL,'scope-worker',60,
                            1,0)
                        """,
                        {"fence": json.dumps(fence)},
                    )
            assert "lock timeout" in str(lock_timeout.value).lower()
        finally:
            lock_transaction.rollback()
            lock_connection.close()
        with engine.begin() as connection:
            assert connection.execute(
                text(
                    "SELECT count(*) FROM public.dts_source_scope_commands "
                    "WHERE command_id='begin-lock-timeout'"
                )
            ).scalar_one() == 0

        with engine.begin() as connection:
            with pytest.raises(DBAPIError) as direct_write:
                connection.execute(
                    text(
                        """
                        INSERT INTO public.dts_source_scope_states (
                            source_region,source_table,scope_kind,scope_level,
                            scope_key,state,row_version
                        ) VALUES ('dom','dom_teacher','CURRENT','GLOBAL','*',
                                  'INCOMPLETE',1)
                        """
                    )
                )
            assert "DTS_SCOPE_DIRECT_WRITE_FORBIDDEN" in str(direct_write.value)

        fence_json = json.dumps(fence)
        with engine.begin() as connection:
            begin_failed = _call(
                connection,
                """
                SELECT public.begin_source_snapshot_candidate_v2(
                    'begin-false','scope-false','dom','dom_teacher',
                    'CURRENT','GLOBAL','*',CURRENT_TIMESTAMP,'token-false',
                    CAST(:fence AS jsonb),NULL,NULL,'scope-worker',60,1,0)
                """,
                {"fence": fence_json},
            )
        with engine.begin() as connection:
            failed = _call(
                connection,
                """
                SELECT public.verify_source_snapshot_candidate_v2(
                    'verify-false','scope-false','scope-worker',:token,
                    CAST(:head_version AS bigint),CAST(:scope_version AS bigint),
                    1,:content_hash,:fence_hash,'scope-worker')
                """,
                {
                    "token": begin_failed["lease_token"],
                    "head_version": begin_failed["head_row_version"],
                    "scope_version": begin_failed["scope_row_version"],
                    "content_hash": empty_content_hash,
                    "fence_hash": fence_hash,
                },
            )
            assert failed["status"] == "FAILED"
            assert failed["error_code"] == "SOURCE_SCOPE_ROW_COUNT_MISMATCH"
        with engine.begin() as connection:
            assert connection.execute(
                text(
                    "SELECT state FROM public.dts_source_scope_states "
                    "WHERE source_region='dom' AND source_table='dom_teacher' "
                    "AND scope_kind='CURRENT' AND scope_level='GLOBAL' "
                    "AND scope_key='*'"
                )
            ).scalar_one() == "FAILED"

        dependencies = {
            "category_ids": [],
            "course_ids": [],
            "label_ids": [],
            "student_subjects": [],
            "teacher_ids": ["teacher-4"],
        }
        with engine.begin() as connection:
            staging_candidate = _call(
                connection,
                """
                SELECT public.begin_source_snapshot_candidate_v2(
                    'begin-staging','scope-staging','dom','dom_teacher',
                    'CURRENT','TEACHER','teacher-4',CURRENT_TIMESTAMP,
                    'token-staging',CAST(:fence AS jsonb),NULL,NULL,
                    'scope-worker',60,CAST(:head_version AS bigint),0)
                """,
                {
                    "fence": fence_json,
                    "head_version": failed["head_row_version"],
                },
            )
        stage_sql = """
            SELECT public.stage_source_snapshot_row_v2(
                'scope-staging','scope-worker',:token,
                CAST(:source_key AS jsonb),CAST(:dependencies AS jsonb),
                CAST(:protected_row AS jsonb),'scope-worker')
        """
        stage_parameters = {
            "token": staging_candidate["lease_token"],
            "source_key": json.dumps({"id": 999}),
            "dependencies": json.dumps(dependencies),
            "protected_row": json.dumps({"id": 999, "status": "on"}),
        }
        with engine.begin() as connection:
            staged = _call(connection,stage_sql,stage_parameters)
            assert staged["status"] == "STAGED"
        with engine.begin() as connection:
            staged_replay = _call(connection,stage_sql,stage_parameters)
            assert staged_replay["status"] == "NOOP"
        with pytest.raises(DBAPIError) as staging_conflict:
            with engine.begin() as connection:
                changed_parameters = dict(stage_parameters)
                changed_parameters["protected_row"] = json.dumps(
                    {"id": 999, "status": "off"}
                )
                _call(connection,stage_sql,changed_parameters)
        assert "SOURCE_SCOPE_STAGING_CONFLICT" in str(staging_conflict.value)
        with engine.begin() as connection:
            staged_aborted = _call(
                connection,
                """
                SELECT public.abort_source_snapshot_candidate_v2(
                    'abort-staging','scope-staging','scope-worker',:token,
                    CAST(:head_version AS bigint),
                    CAST(:scope_version AS bigint),'STAGING_TEST_DONE')
                """,
                {
                    "token": staging_candidate["lease_token"],
                    "head_version": staging_candidate["head_row_version"],
                    "scope_version": staging_candidate["scope_row_version"],
                },
            )
            assert staged_aborted["status"] == "FAILED"

            assert connection.execute(
                text(
                    "SELECT count(*) FROM public.dts_source_scope_states "
                    "WHERE state='COMPLETE'"
                )
            ).scalar_one() == 0

        # A different command cannot race the active per-table candidate.
        with engine.begin() as connection:
            head_version = connection.execute(
                text(
                    "SELECT row_version FROM "
                    "public.dts_source_table_publish_generations "
                    "WHERE source_region='dom' AND source_table='dom_teacher'"
                )
            ).scalar_one()
            scope_version = connection.execute(
                text(
                    "SELECT row_version FROM public.dts_source_scope_states "
                    "WHERE source_region='dom' AND source_table='dom_teacher' "
                    "AND scope_kind='CURRENT' AND scope_level='GLOBAL' "
                    "AND scope_key='*'"
                )
            ).scalar_one()
        with engine.begin() as connection:
            begin_ok = _call(
                connection,
                """
                SELECT public.begin_source_snapshot_candidate_v2(
                    'begin-empty','scope-empty','dom','dom_teacher',
                    'CURRENT','TEACHER','teacher-1',CURRENT_TIMESTAMP,'token-empty',
                    CAST(:fence AS jsonb),NULL,NULL,'scope-worker',60,
                    CAST(:head_version AS bigint),0)
                """,
                {"fence": fence_json, "head_version": head_version},
            )
        with engine.begin() as connection:
            heartbeat = _call(
                connection,
                """
                SELECT public.heartbeat_source_snapshot_candidate_v2(
                    'heartbeat-empty','scope-empty','scope-worker',:token,60,
                    CAST(:head_version AS bigint))
                """,
                {
                    "token": begin_ok["lease_token"],
                    "head_version": begin_ok["head_row_version"],
                },
            )
            assert heartbeat["status"] == "HEARTBEAT"
            begin_ok["head_row_version"] = heartbeat["head_row_version"]
        with pytest.raises(DBAPIError) as concurrent_candidate:
            with engine.begin() as connection:
                _call(
                    connection,
                    """
                    SELECT public.begin_source_snapshot_candidate_v2(
                        'begin-race','scope-race','dom','dom_teacher',
                        'CURRENT','GLOBAL','*',CURRENT_TIMESTAMP,'token-race',
                        CAST(:fence AS jsonb),NULL,NULL,'scope-worker',60,
                        CAST(:head_version AS bigint),
                        CAST(:scope_version AS bigint))
                    """,
                    {
                        "fence": fence_json,
                        "head_version": begin_ok["head_row_version"],
                        "scope_version": scope_version,
                    },
                )
        assert "SOURCE_TABLE_ACTIVE_CANDIDATE_CONFLICT" in str(
            concurrent_candidate.value
        )

        with engine.begin() as connection:
            verified = _call(
                connection,
                """
                SELECT public.verify_source_snapshot_candidate_v2(
                    'verify-empty','scope-empty','scope-worker',:token,
                    CAST(:head_version AS bigint),CAST(:scope_version AS bigint),
                    0,:content_hash,:fence_hash,'scope-worker')
                """,
                {
                    "token": begin_ok["lease_token"],
                    "head_version": begin_ok["head_row_version"],
                    "scope_version": begin_ok["scope_row_version"],
                    "content_hash": empty_content_hash,
                    "fence_hash": fence_hash,
                },
            )
            assert verified["status"] == "VERIFIED"
        publish_parameters = {
            "token": begin_ok["lease_token"],
            "head_version": verified["head_row_version"],
            "scope_version": verified["scope_row_version"],
            "content_hash": empty_content_hash,
        }
        publish_sql = """
            SELECT public.publish_source_snapshot_candidate_v2(
                'publish-empty','scope-empty','scope-worker',:token,
                CAST(:head_version AS bigint),CAST(:scope_version AS bigint),
                :content_hash,'scope-worker')
        """
        with engine.begin() as connection:
            published = _call(connection, publish_sql, publish_parameters)
            assert published["status"] == "PUBLISHED"
            counts_before_replay = connection.execute(
                text(
                    """
                    SELECT
                      (SELECT count(*) FROM public.dts_source_scope_commands),
                      (SELECT count(*) FROM public.outbox_events
                       WHERE aggregate_type='SOURCE_SCOPE'),
                      (SELECT revision FROM public.domain_aggregate_revisions
                       WHERE aggregate_type='SOURCE_SCOPE'
                         AND canonical_key->>'scope_key'='teacher-1'),
                      (SELECT count(*) FROM public.dts_dirty_key_inputs
                       WHERE input_kind='SCOPE_REVISION'
                         AND key_type='TEACHER' AND key_part_1='teacher-1')
                    """
                )
            ).one()
            dirty_evidence = connection.execute(
                text(
                    """
                    SELECT input_identity,input_revision,input_fingerprint
                    FROM public.dts_dirty_key_inputs
                    WHERE input_kind='SCOPE_REVISION'
                      AND key_type='TEACHER' AND key_part_1='teacher-1'
                    ORDER BY input_revision DESC
                    LIMIT 1
                    """
                )
            ).one()
            aggregate_evidence = connection.execute(
                text(
                    """
                    SELECT revision,last_source_row_revision,
                           last_source_position,aggregate_state
                    FROM public.domain_aggregate_revisions
                    WHERE aggregate_type='SOURCE_SCOPE'
                      AND canonical_key->>'scope_key'='teacher-1'
                    """
                )
            ).one()
            assert dirty_evidence.input_identity == {
                "source_region": "dom",
                "source_table": "dom_teacher",
                "scope_kind": "CURRENT",
                "scope_level": "TEACHER",
                "scope_key": "teacher-1",
            }
            assert dirty_evidence.input_revision == published["scope_row_version"]
            assert aggregate_evidence.revision == 3
            assert aggregate_evidence.last_source_row_revision is None
            assert aggregate_evidence.last_source_position is None
            assert aggregate_evidence.aggregate_state["active_snapshot_id"] == "scope-empty"
            assert aggregate_evidence.aggregate_state["active_epoch_id"] == "scope-empty"
            assert aggregate_evidence.aggregate_state["active_fence_hash"] == fence_hash
            expected_fingerprint = connection.execute(
                text(
                    """
                    SELECT public.dts_canonical_json_sha256_v1(
                      jsonb_build_object(
                        'protocol','dirty-scope-v1',
                        'identity',CAST(:identity AS jsonb),
                        'scope_row_version',CAST(:scope_version AS bigint),
                        'state','COMPLETE',
                        'active_snapshot_id','scope-empty',
                        'active_epoch_id','scope-empty',
                        'active_fence_hash',CAST(:fence_hash AS text)
                      )
                    )
                    """
                ),
                {
                    "identity": json.dumps(dirty_evidence.input_identity),
                    "scope_version": published["scope_row_version"],
                    "fence_hash": fence_hash,
                },
            ).scalar_one()
            assert dirty_evidence.input_fingerprint == expected_fingerprint
        with engine.begin() as connection:
            replay = _call(connection, publish_sql, publish_parameters)
            assert replay["replay_status"] == "REPLAYED"
            assert connection.execute(
                text(
                    """
                    SELECT
                      (SELECT count(*) FROM public.dts_source_scope_commands),
                      (SELECT count(*) FROM public.outbox_events
                       WHERE aggregate_type='SOURCE_SCOPE'),
                      (SELECT revision FROM public.domain_aggregate_revisions
                       WHERE aggregate_type='SOURCE_SCOPE'
                         AND canonical_key->>'scope_key'='teacher-1'),
                      (SELECT count(*) FROM public.dts_dirty_key_inputs
                       WHERE input_kind='SCOPE_REVISION'
                         AND key_type='TEACHER' AND key_part_1='teacher-1')
                    """
                )
            ).one() == counts_before_replay
            assert connection.execute(
                text(
                    "SELECT state FROM public.dts_source_scope_states "
                    "WHERE source_region='dom' AND source_table='dom_teacher' "
                    "AND scope_kind='CURRENT' AND scope_level='TEACHER' "
                    "AND scope_key='teacher-1'"
                )
            ).scalar_one() == "COMPLETE"

        invalidate_sql = """
            SELECT public.invalidate_source_scope_v2(
                'invalidate-empty','dom','dom_teacher','CURRENT','TEACHER',
                'teacher-1',CAST(:head_version AS bigint),
                CAST(:scope_version AS bigint),'SOURCE_REFRESH_REQUIRED',
                'scope-worker')
        """
        invalidate_parameters = {
            "head_version": published["head_row_version"],
            "scope_version": published["scope_row_version"],
        }
        with engine.begin() as connection:
            invalidated = _call(
                connection,invalidate_sql,invalidate_parameters
            )
            assert invalidated["status"] == "INVALIDATED"
            invalidated_counts = connection.execute(
                text(
                    """
                    SELECT
                      (SELECT count(*) FROM public.dts_source_scope_commands),
                      (SELECT count(*) FROM public.outbox_events
                       WHERE aggregate_type='SOURCE_SCOPE'),
                      (SELECT count(*) FROM public.dts_dirty_key_inputs
                       WHERE input_kind='SCOPE_REVISION'
                         AND key_type='TEACHER' AND key_part_1='teacher-1')
                    """
                )
            ).one()
        with engine.begin() as connection:
            invalidate_replay = _call(
                connection,invalidate_sql,invalidate_parameters
            )
            assert invalidate_replay["replay_status"] == "REPLAYED"
            assert connection.execute(
                text(
                    """
                    SELECT
                      (SELECT count(*) FROM public.dts_source_scope_commands),
                      (SELECT count(*) FROM public.outbox_events
                       WHERE aggregate_type='SOURCE_SCOPE'),
                      (SELECT count(*) FROM public.dts_dirty_key_inputs
                       WHERE input_kind='SCOPE_REVISION'
                         AND key_type='TEACHER' AND key_part_1='teacher-1')
                    """
                )
            ).one() == invalidated_counts
            assert connection.execute(
                text(
                    "SELECT state FROM public.dts_source_scope_states "
                    "WHERE source_region='dom' AND source_table='dom_teacher' "
                    "AND scope_kind='CURRENT' AND scope_level='TEACHER' "
                    "AND scope_key='teacher-1'"
                )
            ).scalar_one() == "STALE"

        with engine.begin() as connection:
            abort_candidate = _call(
                connection,
                """
                SELECT public.begin_source_snapshot_candidate_v2(
                    'begin-abort','scope-abort','dom','dom_teacher',
                    'CURRENT','TEACHER','teacher-2',CURRENT_TIMESTAMP,
                    'token-abort',CAST(:fence AS jsonb),NULL,NULL,
                    'scope-worker',60,CAST(:head_version AS bigint),0)
                """,
                {
                    "fence": fence_json,
                    "head_version": invalidated["head_row_version"],
                },
            )
        with engine.begin() as connection:
            aborted = _call(
                connection,
                """
                SELECT public.abort_source_snapshot_candidate_v2(
                    'abort-candidate','scope-abort','scope-worker',:token,
                    CAST(:head_version AS bigint),
                    CAST(:scope_version AS bigint),'OPERATOR_ABORTED')
                """,
                {
                    "token": abort_candidate["lease_token"],
                    "head_version": abort_candidate["head_row_version"],
                    "scope_version": abort_candidate["scope_row_version"],
                },
            )
            assert aborted["status"] == "FAILED"
            assert aborted["error_code"] == "OPERATOR_ABORTED"
            assert connection.execute(
                text(
                    "SELECT state FROM public.dts_source_scope_states "
                    "WHERE source_region='dom' AND source_table='dom_teacher' "
                    "AND scope_kind='CURRENT' AND scope_level='TEACHER' "
                    "AND scope_key='teacher-2'"
                )
            ).scalar_one() == "FAILED"

        with engine.begin() as connection:
            takeover_candidate = _call(
                connection,
                """
                SELECT public.begin_source_snapshot_candidate_v2(
                    'begin-takeover','scope-takeover','dom','dom_teacher',
                    'CURRENT','TEACHER','teacher-3',CURRENT_TIMESTAMP,
                    'token-takeover',CAST(:fence AS jsonb),NULL,NULL,
                    'scope-worker',15,CAST(:head_version AS bigint),0)
                """,
                {
                    "fence": fence_json,
                    "head_version": aborted["head_row_version"],
                },
            )
        with engine.begin() as connection:
            connection.execute(
                text(
                    "SELECT set_config("
                    "'tit.scope_coordinator_internal','on',true)"
                )
            )
            expired_head_version = connection.execute(
                text(
                    """
                    UPDATE public.dts_source_table_publish_generations
                    SET candidate_lease_expires_at =
                            clock_timestamp() - interval '1 second',
                        row_version = row_version + 1
                    WHERE source_region='dom' AND source_table='dom_teacher'
                    RETURNING row_version
                    """
                )
            ).scalar_one()
        with engine.begin() as connection:
            taken_over = _call(
                connection,
                """
                SELECT public.takeover_expired_source_snapshot_candidate_v2(
                    'takeover-expired','dom','dom_teacher',
                    CAST(:head_version AS bigint),'scope-recovery-worker')
                """,
                {"head_version": expired_head_version},
            )
            assert taken_over["status"] == "FAILED"
            assert (
                taken_over["error_code"]
                == "SNAPSHOT_CANDIDATE_LEASE_EXPIRED"
            )
            assert connection.execute(
                text(
                    "SELECT state FROM public.dts_source_scope_states "
                    "WHERE source_region='dom' AND source_table='dom_teacher' "
                    "AND scope_kind='CURRENT' AND scope_level='TEACHER' "
                    "AND scope_key='teacher-3'"
                )
            ).scalar_one() == "FAILED"
    finally:
        engine.dispose()
        subprocess.run(
            [pg_ctl, "-D", str(data_dir), "-m", "fast", "-w", "stop"],
            check=False,
            capture_output=True,
            text=True,
        )
