from __future__ import annotations

import hashlib
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

from app.dts_v2_domain_aggregate import (
    DtsV2DomainRevisionStore,
    build_domain_aggregate_identity_v2,
    canonical_domain_state_v2,
)
from app.dts_v2_outbox_worker import (
    DtsV2OutboxProcessingError,
    DtsV2OutboxWorker,
)


POSTGRES_BINARIES = ("initdb", "pg_ctl", "postgres")
REVISION_83 = "20260822_83_dts_v2_scope"
REVISION_84 = "20260822_84_dts_v2_domain_outbox"


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


def _source_position(*, record_id: str, offset: int) -> dict[str, object]:
    return {
        "v": 1,
        "source_timestamp": "2026-08-22T00:00:00.000000Z",
        "record_id_type": "numeric",
        "record_id": record_id,
        "source_partition_epoch_id": "epoch-rev84",
        "topic": "topic-rev84",
        "partition_id": 0,
        "offset_value": offset,
    }


def _source_coverage(
    *,
    revision: int,
    source_region: str = "dom",
    source_table: str = "dom_appoint",
    source_key: str = "NUMERIC:9001",
) -> dict[str, object]:
    return {
        "projection_mode": "SHADOW_BUILD",
        "projection_generation": 1,
        "trigger": {
            "input_kind": "SOURCE_REVISION",
            "input_identity": {
                "source_region": source_region,
                "source_table": source_table,
                "source_key": source_key,
            },
            "input_revision": revision,
            "input_fingerprint": "a" * 64,
            "source_payload_hash": "b" * 64,
        },
    }


def _scope_coverage(*, revision: int) -> dict[str, object]:
    return {
        "projection_mode": "SHADOW_BUILD",
        "projection_generation": 1,
        "trigger": {
            "input_kind": "SCOPE_REVISION",
            "input_identity": {
                "source_region": "dom",
                "source_table": "dom_complaint",
                "scope_kind": "CURRENT",
                "scope_level": "GLOBAL",
                "scope_key": "*",
            },
            "input_revision": revision,
            "input_fingerprint": "c" * 64,
            "scope_state": "COMPLETE",
            "active_snapshot_id": "snapshot-scope-1",
            "active_fence_hash": "d" * 64,
        },
    }


def _seed_revision_83_shape(connection) -> None:
    connection.execute(
        text(
            f"""
            CREATE EXTENSION IF NOT EXISTS pgcrypto;
            CREATE ROLE tit_growth_app LOGIN NOINHERIT NOSUPERUSER
                NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
            CREATE ROLE tit_dts_ingest_runtime LOGIN NOINHERIT NOSUPERUSER
                NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
            CREATE ROLE tit_dts_domain_projector_runtime LOGIN NOINHERIT
                NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
            CREATE ROLE tit_dts_scope_coordinator_runtime LOGIN NOINHERIT
                NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
            CREATE ROLE tit_dts_outbox_worker_runtime LOGIN NOINHERIT
                NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
            CREATE ROLE rev84_public_probe LOGIN NOINHERIT NOSUPERUSER
                NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;

            CREATE TABLE public.alembic_version (
                version_num varchar(64) PRIMARY KEY
            );
            INSERT INTO public.alembic_version VALUES ('{REVISION_83}');

            CREATE FUNCTION public.dts_canonical_json_v1(value jsonb)
            RETURNS text
            LANGUAGE plpgsql
            IMMUTABLE
            STRICT
            PARALLEL SAFE
            SET search_path = pg_catalog, public
            AS $function$
            DECLARE result text;
            BEGIN
                CASE jsonb_typeof(value)
                    WHEN 'object' THEN
                        SELECT '{{' || coalesce(string_agg(
                            to_jsonb(item.key)::text || ':' ||
                                public.dts_canonical_json_v1(item.value),
                            ',' ORDER BY convert_to(item.key, 'UTF8')
                        ), '') || '}}'
                        INTO result
                        FROM jsonb_each(value) AS item(key, value);
                    WHEN 'array' THEN
                        SELECT '[' || coalesce(string_agg(
                            public.dts_canonical_json_v1(item.value),
                            ',' ORDER BY item.ordinality
                        ), '') || ']'
                        INTO result
                        FROM jsonb_array_elements(value)
                             WITH ORDINALITY AS item(value, ordinality);
                    ELSE result := value::text;
                END CASE;
                RETURN result;
            END
            $function$;

            CREATE FUNCTION public.dts_canonical_json_sha256_v1(value jsonb)
            RETURNS text
            LANGUAGE sql
            IMMUTABLE
            STRICT
            PARALLEL SAFE
            SET search_path = pg_catalog, public
            AS $function$
                SELECT encode(
                    sha256(convert_to(
                        public.dts_canonical_json_v1(value), 'UTF8'
                    )),
                    'hex'
                )
            $function$;

            CREATE FUNCTION public.dts_domain_aggregate_key_valid_v2(
                p_aggregate_type text,
                p_key jsonb
            )
            RETURNS boolean
            LANGUAGE sql
            IMMUTABLE
            STRICT
            PARALLEL SAFE
            SET search_path = pg_catalog, public
            AS $function$
                SELECT CASE
                    WHEN jsonb_typeof(p_key) <> 'object' THEN false
                    WHEN p_aggregate_type = 'COURSE' THEN
                        p_key = jsonb_build_object(
                            'source_region',p_key->'source_region',
                            'source_appoint_id',p_key->'source_appoint_id'
                        )
                        AND p_key->>'source_region' IN ('dom','ovs')
                        AND jsonb_typeof(
                            p_key->'source_appoint_id'
                        ) = 'string'
                        AND p_key->>'source_appoint_id' <> ''
                        AND btrim(p_key->>'source_appoint_id') =
                            p_key->>'source_appoint_id'
                    WHEN p_aggregate_type = 'SOURCE_SCOPE' THEN
                        p_key = jsonb_build_object(
                            'source_region',p_key->'source_region',
                            'source_table',p_key->'source_table',
                            'scope_kind',p_key->'scope_kind',
                            'scope_level',p_key->'scope_level',
                            'scope_key',p_key->'scope_key'
                        )
                        AND p_key->>'source_region' IN ('dom','ovs')
                        AND strpos(
                            p_key->>'source_table',
                            p_key->>'source_region' || '_'
                        ) = 1
                        AND p_key->>'scope_kind' IN ('CURRENT','HISTORY')
                        AND p_key->>'scope_level' IN ('GLOBAL','TEACHER')
                        AND (
                            (p_key->>'scope_level' = 'GLOBAL'
                             AND p_key->>'scope_key' = '*')
                            OR (p_key->>'scope_level' = 'TEACHER'
                                AND p_key->>'scope_key' <> '')
                        )
                    ELSE false
                END
            $function$;

            REVOKE ALL ON FUNCTION
                public.dts_canonical_json_v1(jsonb),
                public.dts_canonical_json_sha256_v1(jsonb),
                public.dts_domain_aggregate_key_valid_v2(text,jsonb)
            FROM PUBLIC;
            GRANT EXECUTE ON FUNCTION
                public.dts_canonical_json_v1(jsonb),
                public.dts_canonical_json_sha256_v1(jsonb)
            TO tit_dts_domain_projector_runtime;

            CREATE TABLE public.dts_source_partition_epochs (id bigint);
            CREATE TABLE public.dts_source_row_versions (id bigint);
            CREATE TABLE public.dts_source_rows (id bigint);
            CREATE TABLE public.complaint_rule_imports (id bigint);
            CREATE TABLE public.complaint_category_rules (id bigint);
            CREATE TABLE public.source_courses (id bigint);
            CREATE TABLE public.source_course_participations (id bigint);
            CREATE TABLE public.dts_pipeline_control (
                control_id varchar(16) PRIMARY KEY,
                mode varchar(32) NOT NULL
            );
            INSERT INTO public.dts_pipeline_control(control_id,mode)
            VALUES ('PRIMARY','V2_PRIMARY');

            CREATE TABLE public.domain_aggregate_revisions (
                aggregate_type varchar(48) NOT NULL,
                aggregate_id varchar(160) NOT NULL,
                canonical_key jsonb NOT NULL,
                canonical_key_sha256 varchar(64) NOT NULL,
                revision bigint NOT NULL,
                last_source_row_revision bigint,
                last_source_position jsonb,
                aggregate_state jsonb NOT NULL DEFAULT '{{}}'::jsonb,
                aggregate_state_sha256 varchar(64) NOT NULL,
                updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
                PRIMARY KEY (aggregate_type,aggregate_id),
                CHECK (revision >= 1),
                CHECK (
                    canonical_key_sha256 =
                        public.dts_canonical_json_sha256_v1(canonical_key)
                    AND aggregate_id =
                        'v2:' || aggregate_type || ':' ||
                        canonical_key_sha256
                ),
                CHECK (
                    aggregate_state_sha256 =
                        public.dts_canonical_json_sha256_v1(aggregate_state)
                )
            );

            CREATE TABLE public.outbox_events (
                outbox_id varchar(128) PRIMARY KEY,
                event_id varchar(128) NOT NULL UNIQUE,
                aggregate_type varchar(48) NOT NULL,
                aggregate_id varchar(128) NOT NULL,
                event_type varchar(128) NOT NULL,
                payload jsonb NOT NULL,
                status varchar(24) NOT NULL,
                available_at timestamptz NOT NULL,
                attempt_count integer NOT NULL,
                last_error text,
                created_at timestamptz NOT NULL,
                published_at timestamptz
            );

            CREATE FUNCTION public.guard_outbox_event_update()
            RETURNS trigger
            LANGUAGE plpgsql
            SET search_path = pg_catalog, public
            AS $function$
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'outbox events cannot be deleted';
                END IF;
                IF NEW.payload IS DISTINCT FROM OLD.payload THEN
                    RAISE EXCEPTION 'outbox payload immutable';
                END IF;
                RETURN NEW;
            END
            $function$;
            CREATE TRIGGER guard_outbox_event_update
            BEFORE UPDATE OR DELETE ON public.outbox_events
            FOR EACH ROW EXECUTE FUNCTION public.guard_outbox_event_update();
            REVOKE ALL ON FUNCTION public.guard_outbox_event_update()
            FROM PUBLIC;

            CREATE FUNCTION public.rev83_scope_outbox_probe()
            RETURNS text
            LANGUAGE plpgsql
            SECURITY DEFINER
            SET search_path = pg_catalog, public
            AS $function$
            DECLARE event_identity text;
            BEGIN
                event_identity := 'SRC-SCOPE-' || repeat('a',64) || '-1';
                INSERT INTO public.outbox_events (
                    outbox_id,event_id,aggregate_type,aggregate_id,event_type,
                    payload,status,available_at,attempt_count,last_error,
                    created_at,published_at
                ) VALUES (
                    event_identity,event_identity,'SOURCE_SCOPE',
                    'v2:SOURCE_SCOPE:' || repeat('a',64),
                    'source_wide.changed.v2',
                    jsonb_build_object(
                        'protocol','source-wide-change-v2',
                        'aggregate_type','SOURCE_SCOPE'
                    ),
                    'PENDING',clock_timestamp(),0,NULL,clock_timestamp(),NULL
                );
                RETURN event_identity;
            END
            $function$;
            REVOKE ALL ON FUNCTION public.rev83_scope_outbox_probe()
            FROM PUBLIC;
            GRANT EXECUTE ON FUNCTION public.rev83_scope_outbox_probe()
            TO tit_dts_scope_coordinator_runtime;

            REVOKE ALL PRIVILEGES ON TABLE
                public.dts_source_partition_epochs,
                public.dts_source_row_versions,
                public.dts_source_rows,
                public.complaint_rule_imports,
                public.complaint_category_rules,
                public.source_courses,
                public.source_course_participations,
                public.domain_aggregate_revisions,
                public.outbox_events
            FROM PUBLIC,tit_growth_app,tit_dts_ingest_runtime,
                 tit_dts_domain_projector_runtime,
                 tit_dts_scope_coordinator_runtime,rev84_public_probe;
            GRANT SELECT ON TABLE public.domain_aggregate_revisions
            TO tit_dts_domain_projector_runtime;
            GRANT SELECT,INSERT ON TABLE public.outbox_events
            TO tit_growth_app;
            GRANT UPDATE (
                status,attempt_count,last_error,available_at,published_at
            ) ON TABLE public.outbox_events TO tit_growth_app;
            """
        )
    )


def _assert_statement_rejected(engine, statement: str, **parameters) -> None:
    with pytest.raises(DBAPIError):
        with engine.begin() as connection:
            connection.execute(text(statement), parameters)


def _insert_runtime_probe_event(
    engine,
    *,
    name: str,
    event_type: str | None,
) -> None:
    with engine.begin() as connection:
        connection.execute(text("SET LOCAL ROLE tit_growth_app"))
        connection.execute(
            text(
                """
                INSERT INTO public.outbox_events (
                    outbox_id,event_id,aggregate_type,aggregate_id,event_type,
                    payload,status,available_at,attempt_count,last_error,
                    created_at,published_at
                ) VALUES (
                    :outbox_id,:event_id,'LEGACY_PROBE',:aggregate_id,
                    :event_type,jsonb_build_object(
                        'probe',CAST(:name AS text)
                    ),
                    'PENDING',transaction_timestamp(),0,NULL,
                    transaction_timestamp(),NULL
                )
                """
            ),
            {
                "outbox_id": f"runtime-probe:{name}",
                "event_id": f"runtime-probe:{name}",
                "aggregate_id": f"runtime-probe:{name}",
                "event_type": event_type,
                "name": name,
            },
        )


def _insert_worker_event(
    connection,
    *,
    name: str,
    attempt_count: int = 0,
    available_offset_seconds: int = 0,
) -> tuple[str, str]:
    aggregate_hash = hashlib.sha256(name.encode("utf-8")).hexdigest()
    aggregate_id = f"v2:COURSE:{aggregate_hash}"
    event_id = f"source_wide.changed.v2:COURSE:{aggregate_id}:1"
    outbox_id = "outbox:v2:" + hashlib.sha256(
        event_id.encode("utf-8")
    ).hexdigest()
    connection.execute(
        text(
            """
            INSERT INTO public.outbox_events (
                outbox_id,event_id,aggregate_type,aggregate_id,event_type,
                payload,status,available_at,attempt_count,last_error,
                created_at,published_at
            ) VALUES (
                :outbox_id,:event_id,'COURSE',:aggregate_id,
                'source_wide.changed.v2',
                jsonb_build_object(
                    'protocol_version','domain-aggregate-outbox-v2',
                    'worker_test',CAST(:name AS text)
                ),
                'PENDING',transaction_timestamp()
                    + make_interval(
                        secs=>CAST(:available_offset_seconds AS integer)
                      ),
                :attempt_count,NULL,transaction_timestamp(),NULL
            )
            """
        ),
        {
            "outbox_id": outbox_id,
            "event_id": event_id,
            "aggregate_id": aggregate_id,
            "name": name,
            "available_offset_seconds": available_offset_seconds,
            "attempt_count": attempt_count,
        },
    )
    return outbox_id, event_id


class _PostgresOutboxProcessor:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    def process_event(self, connection, event):
        connection.execute(
            text(
                """
                INSERT INTO public.rev84_worker_effects(event_id,effect_kind)
                VALUES (:event_id,'HANDLER')
                """
            ),
            {"event_id": event.event_id},
        )
        if self.fail:
            raise DtsV2OutboxProcessingError("WORKER_TEST_TRANSIENT")
        return {"effects": 1}


class _PostgresOutboxCases:
    def __init__(self, *, fail_dead: bool = False) -> None:
        self.fail_dead = fail_dead

    def record_dead_letter(self, connection, event, *, error_code: str) -> None:
        connection.execute(
            text(
                """
                INSERT INTO public.rev84_worker_cases(event_id,case_action)
                VALUES (:event_id,'DEAD:' || :error_code)
                """
            ),
            {"event_id": event.event_id, "error_code": error_code},
        )
        if self.fail_dead:
            raise RuntimeError("case recorder must roll back outer transaction")

    def resolve_after_success(self, connection, event) -> None:
        connection.execute(
            text(
                """
                INSERT INTO public.rev84_worker_cases(event_id,case_action)
                VALUES (:event_id,'RESOLVED')
                """
            ),
            {"event_id": event.event_id},
        )


@pytest.mark.skipif(
    not _postgres_tools_available(),
    reason="local PostgreSQL binaries are unavailable",
)
def test_domain_outbox_revision_uses_one_protected_atomic_protocol(
    tmp_path: Path,
) -> None:
    backend_dir = Path(__file__).resolve().parents[1]
    data_dir = tmp_path / "postgres"
    log_path = tmp_path / "postgres.log"
    port_socket = socket.socket()
    port_socket.bind(("127.0.0.1", 0))
    port = port_socket.getsockname()[1]
    port_socket.close()

    initdb = shutil.which("initdb")
    pg_ctl = shutil.which("pg_ctl")
    assert initdb is not None and pg_ctl is not None
    subprocess.run(
        [initdb, "-D", str(data_dir), "-A", "trust", "-U", "postgres"],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            pg_ctl,
            "-D",
            str(data_dir),
            "-l",
            str(log_path),
            "-o",
            f"-p {port} -c listen_addresses=127.0.0.1 -c fsync=off",
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
        port=port,
        database="postgres",
    ).render_as_string(hide_password=False)
    engine = create_engine(database_url)
    runtime_engine = None
    try:
        with engine.begin() as connection:
            _seed_revision_83_shape(connection)

        _run_alembic(backend_dir, database_url, "upgrade", REVISION_84)
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT version_num FROM public.alembic_version")
            ).scalar_one() == REVISION_84
            assert connection.execute(
                text(
                    """
                    SELECT prosecdef,
                           proconfig @> ARRAY['search_path=pg_catalog, public'],
                           has_function_privilege(
                               'tit_dts_domain_projector_runtime',
                               oid,'EXECUTE'
                           ),
                           has_function_privilege(
                               'tit_dts_scope_coordinator_runtime',
                               oid,'EXECUTE'
                           )
                    FROM pg_proc
                    WHERE oid = to_regprocedure(
                        'public.publish_domain_aggregate_revision_v2('
                        'text,jsonb,jsonb,text,jsonb,bigint,jsonb,text,jsonb)'
                    )
                    """
                    )
                ).one() == (True, True, True, False)
            assert connection.execute(
                text(
                    """
                    SELECT prosecdef,
                           proconfig @> ARRAY['search_path=pg_catalog, public'],
                           has_function_privilege(
                               'tit_growth_app',oid,'EXECUTE'
                           ),
                           has_function_privilege(
                               'rev84_public_probe',oid,'EXECUTE'
                           )
                    FROM pg_proc
                    WHERE oid=to_regprocedure(
                        'public.guard_v2_outbox_insert_v2()'
                    )
                    """
                )
            ).one() == (False, True, False, False)
            assert connection.execute(
                text(
                    """
                    SELECT character_maximum_length
                    FROM information_schema.columns
                    WHERE table_schema='public' AND table_name='outbox_events'
                      AND column_name='event_id'
                    """
                )
            ).scalar_one() == 512
            assert connection.execute(
                text(
                    """
                    SELECT has_column_privilege(
                               'tit_growth_app','public.outbox_events',
                               'row_version','UPDATE'
                           ),
                           has_column_privilege(
                               'tit_growth_app','public.outbox_events',
                               'recovery_count','UPDATE'
                           ),
                           has_column_privilege(
                               'tit_growth_app','public.outbox_events',
                               'recovered_at','UPDATE'
                           ),
                           has_function_privilege(
                               'tit_growth_app',
                               'public.dts_canonical_json_v1(jsonb)',
                               'EXECUTE'
                           ),
                           has_function_privilege(
                               'tit_growth_app',
                               'public.dts_canonical_json_sha256_v1(jsonb)',
                               'EXECUTE'
                           )
                    """
                )
            ).one() == (True, False, False, True, True)

        # The application role keeps legacy INSERT compatibility, but neither
        # v2 event type may bypass its owner-checked publisher.  Case variants
        # and unknown types stay legacy rows and are invisible to the v2 Worker;
        # NULL remains rejected by the established NOT NULL contract.
        for name, event_type in (
            ("forged-domain", "source_wide.changed.v2"),
            ("forged-task", "task.materialization.requested.v2"),
        ):
            with pytest.raises(
                DBAPIError,
                match="DTS_V2_OUTBOX_DIRECT_INSERT_FORBIDDEN",
            ):
                _insert_runtime_probe_event(
                    engine,
                    name=name,
                    event_type=event_type,
                )
        with pytest.raises(DBAPIError):
            _insert_runtime_probe_event(
                engine,
                name="null-event-type",
                event_type=None,
            )
        _insert_runtime_probe_event(
            engine,
            name="case-variant",
            event_type="SOURCE_WIDE.CHANGED.V2",
        )
        _insert_runtime_probe_event(
            engine,
            name="unknown-type",
            event_type="legacy.unknown.v999",
        )
        with engine.connect() as connection:
            assert connection.execute(
                text(
                    """
                    SELECT event_type FROM public.outbox_events
                    WHERE aggregate_type='LEGACY_PROBE'
                    ORDER BY event_type
                    """
                )
            ).scalars().all() == [
                "SOURCE_WIDE.CHANGED.V2",
                "legacy.unknown.v999",
            ]
            assert connection.execute(
                text(
                    """
                    SELECT count(*) FROM public.outbox_events
                    WHERE aggregate_type='LEGACY_PROBE'
                      AND event_type IN (
                          'source_wide.changed.v2',
                          'task.materialization.requested.v2'
                      )
                    """
                )
            ).scalar_one() == 0
        _assert_statement_rejected(
            engine,
            """
            SET LOCAL ROLE tit_growth_app;
            UPDATE public.outbox_events
            SET event_type='source_wide.changed.v2'
            WHERE outbox_id='runtime-probe:case-variant'
            """,
        )

        # A clean downgrade must restore rev83's privileges, not revoke the
        # canonical helper grant that rev81 already owned.
        _run_alembic(backend_dir, database_url, "downgrade", REVISION_83)
        with engine.connect() as connection:
            assert connection.execute(
                text(
                    """
                    SELECT has_function_privilege(
                               'tit_dts_domain_projector_runtime',
                               'public.dts_canonical_json_sha256_v1(jsonb)',
                               'EXECUTE'
                           ),
                           has_table_privilege(
                               'tit_dts_domain_projector_runtime',
                               'public.dts_source_rows','SELECT'
                           ),
                           has_table_privilege(
                               'tit_dts_domain_projector_runtime',
                               'public.source_courses','UPDATE'
                           ),
                           has_function_privilege(
                               'tit_growth_app',
                               'public.dts_canonical_json_v1(jsonb)',
                               'EXECUTE'
                           ),
                           has_function_privilege(
                               'tit_growth_app',
                               'public.dts_canonical_json_sha256_v1(jsonb)',
                               'EXECUTE'
                           )
                    """
                )
            ).one() == (True, False, False, False, False)
        _run_alembic(backend_dir, database_url, "upgrade", REVISION_84)

        with engine.connect() as connection:
            for canonical_state in (
                {"value": 1},
                {"value": 10**30},
                {"value": -10},
                {"nested": [True, False, {"value": 0}]},
                {"message": "老师 \"A\"\\path\nline\t结束"},
                {"unicode": "中文😀\u2028", "null": None},
                {"keys": {"é": "composed", "e\u0301": "decomposed"}},
            ):
                database_json, database_hash = connection.execute(
                    text(
                        """
                        SELECT public.dts_canonical_json_v1(
                                   CAST(:value AS jsonb)
                               ),
                               public.dts_canonical_json_sha256_v1(
                                   CAST(:value AS jsonb)
                               )
                        """
                    ),
                    {
                        "value": json.dumps(
                            canonical_state,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                    },
                ).one()
                assert database_hash == canonical_domain_state_v2(
                    canonical_state
                )[1], (canonical_state, database_json)
            for decimal_json in (
                '{"value":1.0}',
                '{"nested":[true,{"value":1.5}]}',
            ):
                assert connection.execute(
                    text(
                        """
                        SELECT public.dts_v2_json_numbers_are_integers(
                            CAST(:value AS jsonb)
                        )
                        """
                    ),
                    {"value": decimal_json},
                ).scalar_one() is False

        identity = build_domain_aggregate_identity_v2(
            "COURSE",
            {"source_region": "dom", "source_appoint_id": "课程-9001"},
        )
        position = _source_position(record_id="9001", offset=8)
        store = DtsV2DomainRevisionStore()
        state = {
            "status": "on",
            "teacher_id": "T-1",
            "flags": [True, False, None],
            "score": 100,
        }
        coverage = _source_coverage(revision=8)
        with engine.begin() as connection:
            connection.execute(
                text("SET LOCAL ROLE tit_dts_domain_projector_runtime")
            )
            first = store.publish_change(
                connection,
                aggregate_type="COURSE",
                aggregate_key=identity.aggregate_key,
                aggregate_state=state,
                changed_fields=["teacher_id", "status", "status"],
                source_row_revision=8,
                source_position=position,
                rule_version="course-projection-v2",
                cutover_coverage_identity=coverage,
            )
            assert first.status == "CHANGED"
            assert first.aggregate_revision == 1
            assert first.event is not None

        with engine.connect() as connection:
            aggregate = connection.execute(
                text(
                    """
                    SELECT revision,aggregate_id,aggregate_state_sha256,
                           last_source_row_revision,last_source_position
                    FROM public.domain_aggregate_revisions
                    WHERE aggregate_type='COURSE' AND aggregate_id=:id
                    """
                ),
                {"id": identity.aggregate_id},
            ).mappings().one()
            outbox = connection.execute(
                text(
                    """
                    SELECT outbox_id,event_id,payload,payload_sha256,
                           recovery_count,recovered_at,row_version
                    FROM public.outbox_events
                    WHERE aggregate_type='COURSE'
                    """
                )
            ).mappings().one()
            assert aggregate["revision"] == 1
            assert aggregate["aggregate_state_sha256"] == (
                canonical_domain_state_v2(state)[1]
            )
            assert aggregate["last_source_row_revision"] == 8
            assert aggregate["last_source_position"] == position
            assert first.event is not None
            assert outbox["outbox_id"] == first.event.outbox_id
            assert outbox["event_id"] == first.event.event_id
            assert outbox["payload"] == dict(first.event.payload)
            assert outbox["payload_sha256"] == first.event.payload_sha256
            assert (outbox["recovery_count"], outbox["recovered_at"]) == (
                0,
                None,
            )
            assert outbox["row_version"] == 1

        # Equal state is a no-op even when newer source provenance is observed.
        with engine.begin() as connection:
            connection.execute(
                text("SET LOCAL ROLE tit_dts_domain_projector_runtime")
            )
            unchanged = store.publish_change(
                connection,
                aggregate_type="COURSE",
                aggregate_key=identity.aggregate_key,
                aggregate_state=state,
                changed_fields=["status"],
                source_row_revision=9,
                source_position=_source_position(record_id="9001", offset=9),
                rule_version="course-projection-v2",
                cutover_coverage_identity=_source_coverage(revision=9),
            )
            assert unchanged.status == "UNCHANGED"
            assert unchanged.aggregate_revision == 1
            assert unchanged.event is None
        with engine.connect() as connection:
            assert connection.execute(
                text(
                    """
                    SELECT revision,last_source_row_revision,
                           (SELECT count(*) FROM public.outbox_events
                            WHERE aggregate_type='COURSE')
                    FROM public.domain_aggregate_revisions
                    WHERE aggregate_type='COURSE' AND aggregate_id=:id
                    """
                ),
                {"id": identity.aggregate_id},
            ).one() == (1, 9, 1)

        # A source revision is comparable only inside its own source identity.
        # The aggregate publisher therefore must not compare grading r1 with
        # the last observed appoint r9 or discard the new causal work.
        with engine.begin() as connection:
            connection.execute(
                text("SET LOCAL ROLE tit_dts_domain_projector_runtime")
            )
            changed = store.publish_change(
                connection,
                aggregate_type="COURSE",
                aggregate_key=identity.aggregate_key,
                aggregate_state={**state, "status": "end"},
                changed_fields=["status"],
                source_row_revision=1,
                source_position=_source_position(record_id="77", offset=10),
                rule_version="course-projection-v2",
                cutover_coverage_identity=_source_coverage(
                    revision=1,
                    source_table="dom_user_teacher_grading",
                    source_key="NUMERIC:77",
                ),
            )
            assert changed.status == "CHANGED"
            assert changed.aggregate_revision == 2
        with engine.connect() as connection:
            assert connection.execute(
                text(
                    """
                    SELECT revision,last_source_row_revision,
                           (SELECT count(*) FROM public.outbox_events
                            WHERE aggregate_type='COURSE')
                    FROM public.domain_aggregate_revisions
                    WHERE aggregate_type='COURSE' AND aggregate_id=:id
                    """
                ),
                {"id": identity.aggregate_id},
            ).one() == (2, 1, 2)

        # A scope transition has no single source row.  It emits a typed NULL
        # provenance event and preserves the last concrete source diagnostics.
        with engine.begin() as connection:
            connection.execute(
                text("SET LOCAL ROLE tit_dts_domain_projector_runtime")
            )
            scoped = store.publish_change(
                connection,
                aggregate_type="COURSE",
                aggregate_key=identity.aggregate_key,
                aggregate_state={
                    **state,
                    "status": "end",
                    "complaint_evidence_status": "CONFIRMED",
                },
                changed_fields=["complaint_evidence_status"],
                source_row_revision=None,
                source_position=None,
                rule_version="course-projection-v2",
                cutover_coverage_identity=_scope_coverage(revision=4),
            )
            assert scoped.status == "CHANGED"
            assert scoped.aggregate_revision == 3
            assert scoped.event is not None
            assert scoped.event.payload["source_row_revision"] is None
            assert scoped.event.payload["source_position"] is None
        with engine.connect() as connection:
            assert connection.execute(
                text(
                    """
                    SELECT revision,last_source_row_revision,
                           (SELECT count(*) FROM public.outbox_events
                            WHERE aggregate_type='COURSE')
                    FROM public.domain_aggregate_revisions
                    WHERE aggregate_type='COURSE' AND aggregate_id=:id
                    """
                ),
                {"id": identity.aggregate_id},
            ).one() == (3, 1, 3)

        _assert_statement_rejected(
            engine,
            """
            SET LOCAL ROLE tit_dts_domain_projector_runtime;
            UPDATE public.domain_aggregate_revisions SET revision=99
            WHERE aggregate_type='COURSE'
            """,
        )
        _assert_statement_rejected(
            engine,
            """
            SET LOCAL ROLE tit_dts_domain_projector_runtime;
            UPDATE public.outbox_events SET payload=jsonb_build_object()
            WHERE aggregate_type='COURSE'
            """,
        )
        with pytest.raises(
            DBAPIError,
            match="DTS_V2_DOMAIN_PUBLISHER_AGGREGATE_TYPE_INVALID",
        ):
            with engine.begin() as connection:
                connection.execute(
                    text("SET LOCAL ROLE tit_dts_domain_projector_runtime")
                )
                connection.execute(
                    text(
                        """
                        SELECT public.publish_domain_aggregate_revision_v2(
                            'SOURCE_SCOPE',CAST(:key AS jsonb),'{}'::jsonb,
                            :state_hash,jsonb_build_array('status'),NULL,NULL,
                            NULL,CAST(:coverage AS jsonb)
                        )
                        """
                    ),
                    {
                        "key": json.dumps(
                            {
                                "source_region": "dom",
                                "source_table": "dom_complaint",
                                "scope_kind": "CURRENT",
                                "scope_level": "GLOBAL",
                                "scope_key": "*",
                            },
                            separators=(",", ":"),
                        ),
                        "state_hash": canonical_domain_state_v2({})[1],
                        "coverage": json.dumps(
                            _scope_coverage(revision=1),
                            separators=(",", ":"),
                        ),
                    },
                )
        for role_name in (
            "tit_growth_app",
            "tit_dts_ingest_runtime",
            "tit_dts_scope_coordinator_runtime",
            "rev84_public_probe",
        ):
            _assert_statement_rejected(
                engine,
                f"""
                SET LOCAL ROLE {role_name};
                SELECT public.publish_domain_aggregate_revision_v2(
                    'COURSE',jsonb_build_object(),jsonb_build_object(),
                    repeat('0',64),jsonb_build_array('status'),1,
                    CAST(:position AS jsonb),NULL,jsonb_build_object('v',1)
                )
                """,
                position=json.dumps(position, separators=(",", ":")),
            )

        # Database protection remains effective when a caller bypasses Python.
        for forbidden_alias in (
            "student_id",
            "s_id",
            "sId",
            "stu_id",
            "stuId",
            "user_id",
            "userId",
            "rawStudentId",
        ):
            _assert_statement_rejected(
                engine,
                """
                SET LOCAL ROLE tit_dts_domain_projector_runtime;
                SELECT public.publish_domain_aggregate_revision_v2(
                    'COURSE',CAST(:key AS jsonb),CAST(:state AS jsonb),
                    :state_hash,jsonb_build_array('status'),11,
                    CAST(:position AS jsonb),NULL,CAST(:coverage AS jsonb)
                )
                """,
                key=identity.canonical_key_json,
                state=json.dumps(
                    {"nested": [{forbidden_alias: "10086"}]},
                    separators=(",", ":"),
                ),
                state_hash=canonical_domain_state_v2({"safe": True})[1],
                position=json.dumps(
                    _source_position(record_id="9001", offset=11),
                    separators=(",", ":"),
                ),
                coverage=json.dumps(
                    _source_coverage(revision=11),
                    separators=(",", ":"),
                ),
            )

        with pytest.raises(DBAPIError, match="DOMAIN_JSON_NUMBER_INVALID"):
            with engine.begin() as connection:
                connection.execute(
                    text("SET LOCAL ROLE tit_dts_domain_projector_runtime")
                )
                connection.execute(
                    text(
                        """
                        WITH proposed(state) AS (
                            VALUES (jsonb_build_object('ratio',1.5))
                        )
                        SELECT public.publish_domain_aggregate_revision_v2(
                            'COURSE',CAST(:key AS jsonb),state,
                            public.dts_canonical_json_sha256_v1(state),
                            jsonb_build_array('ratio'),11,
                            CAST(:position AS jsonb),NULL,
                            CAST(:coverage AS jsonb)
                        )
                        FROM proposed
                        """
                    ),
                    {
                        "key": identity.canonical_key_json,
                        "position": json.dumps(
                            _source_position(record_id="9001", offset=11),
                            separators=(",", ":"),
                        ),
                        "coverage": json.dumps(
                            _source_coverage(revision=11),
                            separators=(",", ":"),
                        ),
                    },
                )

        # A pre-existing conflicting event must roll back the aggregate write.
        conflict_identity = build_domain_aggregate_identity_v2(
            "COURSE",
            {"source_region": "ovs", "source_appoint_id": "atomic"},
        )
        conflict_event_id = (
            "source_wide.changed.v2:COURSE:"
            f"{conflict_identity.aggregate_id}:1"
        )
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO public.outbox_events (
                        outbox_id,event_id,aggregate_type,aggregate_id,
                        event_type,payload,status,available_at,attempt_count,
                        last_error,created_at,published_at
                    ) VALUES (
                        :outbox_id,:event_id,'COURSE','conflict',
                        'source_wide.changed.v2',jsonb_build_object('bad',true),
                        'PENDING',now(),0,NULL,now(),NULL
                    )
                    """
                ),
                {
                    "outbox_id": "outbox:v2:" + "f" * 64,
                    "event_id": conflict_event_id,
                },
            )
        with pytest.raises(DBAPIError, match="OUTBOX_ID_CONFLICT"):
            with engine.begin() as connection:
                connection.execute(
                    text("SET LOCAL ROLE tit_dts_domain_projector_runtime")
                )
                store.publish_change(
                    connection,
                    aggregate_type="COURSE",
                    aggregate_key=conflict_identity.aggregate_key,
                    aggregate_state={"status": "on"},
                    changed_fields=["status"],
                    source_row_revision=1,
                    source_position=_source_position(
                        record_id="atomic", offset=20
                    ),
                    rule_version=None,
                    cutover_coverage_identity=_source_coverage(
                        revision=1,
                        source_region="ovs",
                        source_table="ovs_appoint",
                        source_key="TEXT:atomic",
                    ),
                )
        with engine.connect() as connection:
            assert connection.execute(
                text(
                    """
                    SELECT count(*) FROM public.domain_aggregate_revisions
                    WHERE aggregate_type='COURSE' AND aggregate_id=:id
                    """
                ),
                {"id": conflict_identity.aggregate_id},
            ).scalar_one() == 0

        # The deterministic outbox_id is equally part of the immutable
        # identity.  Occupying that primary key under another event_id must
        # produce the same stable conflict and roll back the aggregate row.
        outbox_conflict_identity = build_domain_aggregate_identity_v2(
            "COURSE",
            {
                "source_region": "ovs",
                "source_appoint_id": "atomic-outbox-id",
            },
        )
        outbox_conflict_event_id = (
            "source_wide.changed.v2:COURSE:"
            f"{outbox_conflict_identity.aggregate_id}:1"
        )
        occupied_outbox_id = "outbox:v2:" + hashlib.sha256(
            outbox_conflict_event_id.encode("utf-8")
        ).hexdigest()
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO public.outbox_events (
                        outbox_id,event_id,aggregate_type,aggregate_id,
                        event_type,payload,status,available_at,attempt_count,
                        last_error,created_at,published_at
                    ) VALUES (
                        :outbox_id,'occupied-by-another-event','COURSE',
                        'occupied','source_wide.changed.v2',
                        jsonb_build_object('bad',true),'PENDING',now(),0,
                        NULL,now(),NULL
                    )
                    """
                ),
                {"outbox_id": occupied_outbox_id},
            )
        with pytest.raises(DBAPIError, match="OUTBOX_ID_CONFLICT"):
            with engine.begin() as connection:
                connection.execute(
                    text("SET LOCAL ROLE tit_dts_domain_projector_runtime")
                )
                store.publish_change(
                    connection,
                    aggregate_type="COURSE",
                    aggregate_key=outbox_conflict_identity.aggregate_key,
                    aggregate_state={"status": "on"},
                    changed_fields=["status"],
                    source_row_revision=1,
                    source_position=_source_position(
                        record_id="atomic-outbox-id", offset=21
                    ),
                    rule_version=None,
                    cutover_coverage_identity=_source_coverage(
                        revision=1,
                        source_region="ovs",
                        source_table="ovs_appoint",
                        source_key="TEXT:atomic-outbox-id",
                    ),
                )
        with engine.connect() as connection:
            assert connection.execute(
                text(
                    """
                    SELECT count(*) FROM public.domain_aggregate_revisions
                    WHERE aggregate_type='COURSE' AND aggregate_id=:id
                    """
                ),
                {"id": outbox_conflict_identity.aggregate_id},
            ).scalar_one() == 0

        # The real Worker keeps the row lock, handler write and settlement in
        # one outer transaction.  Existing domain events are closed first so
        # each scenario has one deterministic claim candidate.
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                        UPDATE public.outbox_events
                        SET status='PUBLISHED',published_at=transaction_timestamp(),
                            row_version=row_version+1
                        WHERE status='PENDING'
                          AND event_type IN (
                              'source_wide.changed.v2',
                              'task.materialization.requested.v2'
                          )
                        """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE TABLE public.rev84_worker_effects (
                        event_id varchar(512) PRIMARY KEY,
                        effect_kind text NOT NULL
                    );
                    CREATE TABLE public.rev84_worker_cases (
                        event_id varchar(512) NOT NULL,
                        case_action text NOT NULL
                    );
                    GRANT INSERT ON TABLE public.rev84_worker_effects,
                        public.rev84_worker_cases
                        TO tit_dts_outbox_worker_runtime;
                    GRANT SELECT ON TABLE public.outbox_events,
                        public.domain_aggregate_revisions,
                        public.dts_pipeline_control
                        TO tit_dts_outbox_worker_runtime;
                    GRANT UPDATE(
                        status,attempt_count,last_error,available_at,
                        published_at,row_version
                    ) ON public.outbox_events
                        TO tit_dts_outbox_worker_runtime;
                    GRANT EXECUTE ON FUNCTION
                        public.dts_canonical_json_v1(jsonb),
                        public.dts_canonical_json_sha256_v1(jsonb)
                        TO tit_dts_outbox_worker_runtime;
                    """
                )
            )

        runtime_url = URL.create(
            "postgresql+psycopg",
            username="tit_dts_outbox_worker_runtime",
            host="127.0.0.1",
            port=port,
            database="postgres",
        ).render_as_string(hide_password=False)
        runtime_engine = create_engine(runtime_url)

        with engine.begin() as connection:
            success_outbox_id, success_event_id = _insert_worker_event(
                connection,
                name="success-atomic",
            )
        success_worker = DtsV2OutboxWorker(
            runtime_engine,
            processor=_PostgresOutboxProcessor(),
            technical_cases=_PostgresOutboxCases(),
            primary_guard=None,
        )
        assert success_worker.run_once(max_events=1) == {
            "claimed": 1,
            "published": 1,
            "retries": 0,
            "dead_letters": 0,
            "handler_counts": {"effects": 1},
        }
        with engine.connect() as connection:
            assert connection.execute(
                text(
                    """
                    SELECT status,row_version FROM public.outbox_events
                    WHERE outbox_id=:outbox_id
                    """
                ),
                {"outbox_id": success_outbox_id},
            ).one() == ("PUBLISHED", 2)
            assert connection.execute(
                text(
                    """
                    SELECT effect_kind FROM public.rev84_worker_effects
                    WHERE event_id=:event_id
                    """
                ),
                {"event_id": success_event_id},
            ).scalar_one() == "HANDLER"
            assert connection.execute(
                text(
                    """
                    SELECT case_action FROM public.rev84_worker_cases
                    WHERE event_id=:event_id
                    """
                ),
                {"event_id": success_event_id},
            ).scalar_one() == "RESOLVED"

        with engine.begin() as connection:
            retry_outbox_id, retry_event_id = _insert_worker_event(
                connection,
                name="handler-rollback",
            )
        retry_worker = DtsV2OutboxWorker(
            runtime_engine,
            processor=_PostgresOutboxProcessor(fail=True),
            technical_cases=_PostgresOutboxCases(),
            primary_guard=None,
        )
        retry_result = retry_worker.run_once(max_events=1)
        assert retry_result["retries"] == 1
        with engine.connect() as connection:
            assert connection.execute(
                text(
                    """
                    SELECT status,attempt_count,last_error,row_version
                    FROM public.outbox_events WHERE outbox_id=:outbox_id
                    """
                ),
                {"outbox_id": retry_outbox_id},
            ).one() == ("PENDING", 1, "WORKER_TEST_TRANSIENT", 2)
            assert connection.execute(
                text(
                    """
                    SELECT count(*) FROM public.rev84_worker_effects
                    WHERE event_id=:event_id
                    """
                ),
                {"event_id": retry_event_id},
            ).scalar_one() == 0
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE public.outbox_events
                    SET status='PUBLISHED',published_at=transaction_timestamp(),
                        row_version=row_version+1
                    WHERE outbox_id=:outbox_id
                    """
                ),
                {"outbox_id": retry_outbox_id},
            )

        with engine.begin() as connection:
            dead_outbox_id, dead_event_id = _insert_worker_event(
                connection,
                name="dead-case-outer-rollback",
                attempt_count=7,
            )
        dead_worker = DtsV2OutboxWorker(
            runtime_engine,
            processor=_PostgresOutboxProcessor(fail=True),
            technical_cases=_PostgresOutboxCases(fail_dead=True),
            primary_guard=None,
        )
        with pytest.raises(RuntimeError, match="case recorder"):
            dead_worker.run_once(max_events=1)
        with engine.connect() as connection:
            assert connection.execute(
                text(
                    """
                    SELECT status,attempt_count,row_version
                    FROM public.outbox_events WHERE outbox_id=:outbox_id
                    """
                ),
                {"outbox_id": dead_outbox_id},
            ).one() == ("PENDING", 7, 1)
            assert connection.execute(
                text(
                    """
                    SELECT count(*) FROM public.rev84_worker_effects
                    WHERE event_id=:event_id
                    """
                ),
                {"event_id": dead_event_id},
            ).scalar_one() == 0
            assert connection.execute(
                text(
                    """
                    SELECT count(*) FROM public.rev84_worker_cases
                    WHERE event_id=:event_id
                    """
                ),
                {"event_id": dead_event_id},
            ).scalar_one() == 0
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE public.outbox_events
                    SET status='PUBLISHED',published_at=transaction_timestamp(),
                        row_version=row_version+1
                    WHERE outbox_id=:outbox_id
                    """
                ),
                {"outbox_id": dead_outbox_id},
            )

        # Lock the oldest row in another transaction.  SKIP LOCKED must let
        # the Worker publish the second row without waiting or double-claiming.
        with engine.begin() as connection:
            locked_outbox_id, locked_event_id = _insert_worker_event(
                connection,
                name="locked-oldest",
                available_offset_seconds=-2,
            )
            free_outbox_id, free_event_id = _insert_worker_event(
                connection,
                name="free-second",
                available_offset_seconds=-1,
            )
        locker = engine.connect()
        locker_transaction = locker.begin()
        try:
            locker.execute(
                text(
                    """
                    SELECT outbox_id FROM public.outbox_events
                    WHERE outbox_id=:outbox_id FOR UPDATE
                    """
                ),
                {"outbox_id": locked_outbox_id},
            ).scalar_one()
            concurrent_worker = DtsV2OutboxWorker(
                runtime_engine,
                processor=_PostgresOutboxProcessor(),
                technical_cases=_PostgresOutboxCases(),
                primary_guard=None,
            )
            concurrent_result = concurrent_worker.run_once(max_events=1)
            assert concurrent_result["published"] == 1
            with engine.connect() as observer:
                assert observer.execute(
                    text(
                        """
                        SELECT outbox_id,status FROM public.outbox_events
                        WHERE outbox_id IN (:locked_id,:free_id)
                        ORDER BY outbox_id
                        """
                    ),
                    {
                        "locked_id": locked_outbox_id,
                        "free_id": free_outbox_id,
                    },
                ).all() == sorted(
                    [(locked_outbox_id, "PENDING"),
                     (free_outbox_id, "PUBLISHED")]
                )
                assert observer.execute(
                    text(
                        """
                        SELECT count(*) FROM public.rev84_worker_effects
                        WHERE event_id=:event_id
                        """
                    ),
                    {"event_id": locked_event_id},
                ).scalar_one() == 0
                assert observer.execute(
                    text(
                        """
                        SELECT count(*) FROM public.rev84_worker_effects
                        WHERE event_id=:event_id
                        """
                    ),
                    {"event_id": free_event_id},
                ).scalar_one() == 1
        finally:
            locker_transaction.rollback()
            locker.close()

        _assert_statement_rejected(
            engine,
            """
            UPDATE public.outbox_events
            SET payload=jsonb_build_object('tampered',true)
            WHERE outbox_id=:outbox_id
            """,
            outbox_id=success_outbox_id,
        )
        _assert_statement_rejected(
            engine,
            """
            UPDATE public.outbox_events SET payload_sha256=repeat('0',64)
            WHERE outbox_id=:outbox_id
            """,
            outbox_id=success_outbox_id,
        )
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE public.outbox_events
                    SET status='PUBLISHED',published_at=transaction_timestamp(),
                        row_version=row_version+1
                    WHERE outbox_id=:outbox_id
                    """
                ),
                {"outbox_id": locked_outbox_id},
            )

        # rev83's independent SOURCE_SCOPE publisher omits all rev84 columns;
        # the new trigger/defaults must populate them without extra privileges.
        with engine.begin() as connection:
            connection.execute(
                text("SET LOCAL ROLE tit_dts_scope_coordinator_runtime")
            )
            scope_event_id = connection.execute(
                text("SELECT public.rev83_scope_outbox_probe()")
            ).scalar_one()
        with engine.connect() as connection:
            scope_outbox = connection.execute(
                text(
                    """
                    SELECT payload,payload_sha256,recovery_count,recovered_at,
                           row_version
                    FROM public.outbox_events WHERE event_id=:event_id
                    """
                ),
                {"event_id": scope_event_id},
            ).mappings().one()
            expected_scope_hash = hashlib.sha256(
                json.dumps(
                    dict(scope_outbox["payload"]),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            assert scope_outbox["payload_sha256"] == expected_scope_hash
            assert scope_outbox["recovery_count"] == 0
            assert scope_outbox["recovered_at"] is None
            assert scope_outbox["row_version"] == 1
            assert connection.execute(
                text(
                    """
                    SELECT event_type,status FROM public.outbox_events
                    WHERE aggregate_type='LEGACY_PROBE'
                    ORDER BY event_type
                    """
                )
            ).all() == [
                ("SOURCE_WIDE.CHANGED.V2", "PENDING"),
                ("legacy.unknown.v999", "PENDING"),
            ]

        # Once v2 history exists the destructive downgrade is fail closed.
        environment = os.environ.copy()
        environment["APP_ENV"] = "test"
        environment["DATABASE_URL"] = database_url
        downgrade = subprocess.run(
            [
                sys.executable,
                "-m",
                "alembic",
                "downgrade",
                REVISION_83,
            ],
            cwd=backend_dir,
            env=environment,
            capture_output=True,
            text=True,
        )
        assert downgrade.returncode != 0
        assert "refusing DTS v2 domain Outbox downgrade" in downgrade.stderr
    finally:
        if runtime_engine is not None:
            runtime_engine.dispose()
        engine.dispose()
        subprocess.run(
            [pg_ctl, "-D", str(data_dir), "-m", "immediate", "-w", "stop"],
            check=True,
            capture_output=True,
            text=True,
        )
