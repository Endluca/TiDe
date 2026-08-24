from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
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
    _run_alembic_expect_failure,
    _seed_external_personalized_catalog,
)


REV84 = "20260822_84_dts_v2_domain_outbox"
REV85 = "20260822_85_outbox_three_state"
MIGRATION_RUN_ID = "alembic:20260822_85_outbox_three_state"


def _role_call(connection, role: str, sql: str, parameters=None):
    connection.execute(text(f"SET LOCAL ROLE {role}"))
    return connection.execute(text(sql), parameters or {}).scalar_one()


def _insert_pending(connection, suffix: str) -> tuple[str, str]:
    event_id = f"event:outbox-v2:{suffix}"
    outbox_id = f"outbox:outbox-v2:{suffix}"
    connection.execute(
        text(
            """
            INSERT INTO public.outbox_events (
                outbox_id,event_id,aggregate_type,aggregate_id,event_type,
                payload,status,available_at,attempt_count,last_error,
                created_at,published_at
            ) VALUES (
                :outbox_id,:event_id,'TEST','aggregate:test',
                'outbox.test.v2',CAST(:payload AS jsonb),'PENDING',
                transaction_timestamp(),0,NULL,transaction_timestamp(),NULL
            )
            """
        ),
        {
            "outbox_id": outbox_id,
            "event_id": event_id,
            "payload": json.dumps({"kind": "outbox-state-test", "v": 2}),
        },
    )
    return event_id, outbox_id


def _drive_to_dead(engine, event_id: str) -> tuple[str, int, int]:
    for attempt in range(1, 8):
        with engine.begin() as connection:
            connection.execute(text("SET LOCAL ROLE tit_growth_app"))
            connection.execute(
                text(
                    """
                    UPDATE public.outbox_events
                    SET attempt_count=:attempt,
                        last_error='TRANSIENT_DELIVERY_FAILURE',
                        available_at=transaction_timestamp()
                            + interval '1 second',
                        row_version=row_version+1
                    WHERE event_id=:event_id
                    """
                ),
                {"attempt": attempt, "event_id": event_id},
            )
    with engine.begin() as connection:
        connection.execute(text("SET LOCAL ROLE tit_growth_app"))
        connection.execute(
            text(
                """
                UPDATE public.outbox_events
                SET status='DEAD_LETTER',attempt_count=8,
                    last_error='DELIVERY_RETRY_EXHAUSTED',
                    available_at=transaction_timestamp(),published_at=NULL,
                    row_version=row_version+1
                WHERE event_id=:event_id
                """
            ),
            {"event_id": event_id},
        )
    with engine.begin() as connection:
        row = connection.execute(
            text(
                """
                SELECT payload_sha256,recovery_count,row_version
                FROM public.outbox_events WHERE event_id=:event_id
                """
            ),
            {"event_id": event_id},
        ).one()
    return str(row[0]), int(row[1]), int(row[2])


def _recover(engine, command_id: str, event_id: str, payload_hash: str):
    with engine.begin() as connection:
        return _role_call(
            connection,
            "tit_dts_outbox_recovery_runtime",
            """
            SELECT public.recover_outbox_event_v2(
                :command_id,:event_id,:payload_hash,0,'manual-review-approved'
            )
            """,
            {
                "command_id": command_id,
                "event_id": event_id,
                "payload_hash": payload_hash,
            },
        )


@pytest.mark.skipif(
    not _postgres_tools_available(),
    reason="local PostgreSQL binaries are required for Outbox v2 tests",
)
def test_rev85_archive_three_state_recovery_acl_and_concurrency(
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
                "tit_dts_domain_projector_runtime",
                "tit_dts_scope_coordinator_runtime",
                "tit_dts_outbox_recovery_runtime",
                "tit_dts_cutover_migration",
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
                        f"CREATE ROLE {role_name} NOLOGIN NOINHERIT "
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
        _run_alembic(backend_dir, database_url, "upgrade", REV84)

        parked_payload = {
            "output_id": "legacy-output-1",
            "attempt_count": 0,
            "display_type": "TASK",
            "actor_id": "SYSTEM",
            "_migration_20260729_37": {
                "previous_status": "PENDING",
                "previous_last_error": None,
            },
        }
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO public.teachers (
                        teacher_id,camp_enrollment_id,name,country,timezone,
                        camp_day,online_status,graduation_state,gold_qualified,
                        total_score,graduation_threshold,data_mode,
                        source_snapshot_label,payload,created_at,updated_at
                    ) VALUES (
                        'mock-teacher-outbox-v2','mock-camp-outbox-v2',
                        'Mock Teacher',NULL,'UTC',0,'NEW','IN_CAMP',false,
                        0,100,'MOCK',NULL,
                        jsonb_build_object('graduation_state','IN_CAMP'),
                        transaction_timestamp(),transaction_timestamp()
                    );
                    INSERT INTO public.task_assignments (
                        assignment_id,teacher_id,task_code,
                        template_version_id,task_kind,creator_system,status,
                        priority,why,display_title,evidence_snapshot,
                        source_mode,dedupe_key,created_by,updated_by
                    ) VALUES (
                        'mock-assignment-outbox-v2','mock-teacher-outbox-v2',
                        'P-FB-NEGATIVE','P-FB-NEGATIVE:v1',
                        'PERSONALIZED_IMPROVEMENT','TRIGGER_CENTER','ASSIGNED',
                        'P2','Evidence: isolated mock archive fixture.',
                        'Mock archive fixture','{}'::jsonb,'MOCK',
                        'personalized:mock-assignment-outbox-v2',
                        'MOCK_SEED','MOCK_SEED'
                    );
                    INSERT INTO public.outbox_events (
                        outbox_id,event_id,aggregate_type,aggregate_id,
                        event_type,payload,status,available_at,
                        attempt_count,last_error,created_at,published_at
                    ) VALUES (
                        'legacy-outbox-cancelled',
                        'legacy-event-cancelled','TASK_ASSIGNMENT',
                        'mock-assignment-outbox-v2',
                        'task.assignment_changed.shared',
                        jsonb_build_object(
                            'assignment_id','mock-assignment-outbox-v2',
                            'scenario','MOCK_SEED_SHARED_TASKS',
                            'origin','MOCK_SEED','source','MOCK_SEED',
                            'source_mode','MOCK','mock_only',true,
                            'delivery_disabled',true,
                            'execution_allowed',false
                        ),
                        'CANCELLED','2026-07-29T00:00:00+00',0,
                        'MOCK_SEED_DELIVERY_DISABLED',
                        '2026-07-29T00:00:00+00',NULL
                    );
                    """
                )
            )
            for suffix, payload in (
                ("eligible", parked_payload),
                ("unsafe", {**parked_payload, "credential": "password=x"}),
            ):
                connection.execute(
                    text(
                        """
                        INSERT INTO public.outbox_events (
                            outbox_id,event_id,aggregate_type,aggregate_id,
                            event_type,payload,status,available_at,
                            attempt_count,last_error,created_at,published_at
                        ) VALUES (
                            :outbox_id,:event_id,'OUTPUT','legacy-output-1',
                            'outbound_output.retry_requested.v1',
                            CAST(:payload AS jsonb),'PARKED',
                            '2026-07-29T00:00:00+00',0,
                            'NO_OUTPUT_CONSUMER_CONFIGURED',
                            '2026-07-29T00:00:00+00',NULL
                        )
                        """
                    ),
                    {
                        "outbox_id": f"legacy-outbox-{suffix}",
                        "event_id": f"legacy-event-{suffix}",
                        "payload": json.dumps(payload),
                    },
                )

        _run_alembic_expect_failure(
            backend_dir,
            database_url,
            "LEGACY_OUTBOX_PAYLOAD_UNSAFE",
            "upgrade",
            REV85,
        )
        with engine.begin() as connection:
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == REV84
            assert connection.execute(
                text(
                    "SELECT to_regclass('public.outbox_events_legacy_archive')"
                )
            ).scalar_one() is None
            connection.execute(
                text(
                    """
                    UPDATE public.outbox_events
                    SET status='PUBLISHED',published_at=transaction_timestamp(),
                        row_version=row_version+1
                    WHERE event_id='legacy-event-unsafe'
                    """
                )
            )

        _run_alembic(backend_dir, database_url, "upgrade", REV85)
        with engine.begin() as connection:
            assert connection.execute(
                text(
                    """
                    SELECT
                      NOT EXISTS (SELECT 1 FROM public.outbox_events
                                  WHERE event_id='legacy-event-eligible')
                      AND EXISTS (
                          SELECT 1
                          FROM public.outbox_events_legacy_archive
                          WHERE event_id='legacy-event-eligible'
                            AND status='PARKED'
                            AND proof_type=
                              'MIGRATION_20260729_37_RETRY_PARKED'
                      )
                      AND EXISTS (
                          SELECT 1
                          FROM public.outbox_events_legacy_archive
                          WHERE event_id='legacy-event-cancelled'
                            AND status='CANCELLED'
                            AND proof_type='MOCK_SEED_CANCELLED'
                            AND archive_reason=
                              'PROVEN_NON_REAL_MOCK_CANCELLED'
                      )
                      AND EXISTS (SELECT 1 FROM public.audit_events
                                  WHERE event_type=
                                    'LEGACY_OUTBOX_ARCHIVED_V2')
                    """
                )
            ).scalar_one()
            assert connection.execute(
                text(
                    """
                    SELECT
                      has_function_privilege(
                        'tit_dts_outbox_recovery_runtime',
                        'public.recover_outbox_event_v2('
                        'text,text,text,bigint,text)','EXECUTE')
                      AND NOT has_function_privilege(
                        'tit_dts_ingest_runtime',
                        'public.recover_outbox_event_v2('
                        'text,text,text,bigint,text)','EXECUTE')
                      AND NOT has_table_privilege(
                        'tit_dts_outbox_recovery_runtime',
                        'public.outbox_events','UPDATE')
                      AND has_function_privilege(
                        'tit_dts_cutover_migration',
                        'public.archive_legacy_outbox_event_v2('
                        'text,text,text,text,text)','EXECUTE')
                      AND NOT has_table_privilege(
                        'tit_dts_cutover_migration',
                        'public.outbox_events_legacy_archive','INSERT')
                    """
                )
            ).scalar_one()

        # No recovery history exists yet, so the legacy archive is reversible.
        _run_alembic(backend_dir, database_url, "downgrade", REV84)
        with engine.begin() as connection:
            assert connection.execute(
                text(
                    """
                    SELECT status FROM public.outbox_events
                    WHERE event_id='legacy-event-eligible'
                    """
                )
            ).scalar_one() == "PARKED"
        _run_alembic(backend_dir, database_url, "upgrade", REV85)

        with engine.begin() as connection:
            archive = connection.execute(
                text(
                    """
                    SELECT archive_source_row_hash,proof_hash
                    FROM public.outbox_events_legacy_archive
                    WHERE event_id='legacy-event-eligible'
                    """
                )
            ).one()
            noop = _role_call(
                connection,
                "tit_dts_cutover_migration",
                """
                SELECT public.archive_legacy_outbox_event_v2(
                    'legacy-event-eligible',:row_hash,
                    'MIGRATION_20260729_37_RETRY_PARKED',:proof_hash,
                    :migration_run_id
                )
                """,
                {
                    "row_hash": archive[0],
                    "proof_hash": archive[1],
                    "migration_run_id": MIGRATION_RUN_ID,
                },
            )
            assert noop["status"] == "NOOP"
        with pytest.raises(DBAPIError, match="LEGACY_OUTBOX_ARCHIVE_CONFLICT"):
            with engine.begin() as connection:
                _role_call(
                    connection,
                    "tit_dts_cutover_migration",
                    """
                    SELECT public.archive_legacy_outbox_event_v2(
                        'legacy-event-eligible',repeat('0',64),
                        'MIGRATION_20260729_37_RETRY_PARKED',:proof_hash,
                        :migration_run_id
                    )
                    """,
                    {
                        "proof_hash": archive[1],
                        "migration_run_id": MIGRATION_RUN_ID,
                    },
                )
        with pytest.raises(
            DBAPIError, match="LEGACY_OUTBOX_ARCHIVE_IMMUTABLE"
        ):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        UPDATE public.outbox_events_legacy_archive
                        SET archive_reason=archive_reason
                        WHERE event_id='legacy-event-eligible'
                        """
                    )
                )

        with engine.begin() as connection:
            event_id, _ = _insert_pending(connection, "recovery-replay")
        with pytest.raises(DBAPIError, match="OUTBOX_STATUS_TRANSITION_INVALID"):
            with engine.begin() as connection:
                connection.execute(text("SET LOCAL ROLE tit_growth_app"))
                connection.execute(
                    text(
                        """
                        UPDATE public.outbox_events
                        SET last_error='ARBITRARY_MUTATION',
                            available_at=transaction_timestamp()
                                + interval '1 second',
                            row_version=row_version+1
                        WHERE event_id=:event_id
                        """
                    ),
                    {"event_id": event_id},
                )
        payload_hash, recovery_count, row_version = _drive_to_dead(
            engine, event_id
        )
        assert (recovery_count, row_version) == (0, 9)
        with pytest.raises(DBAPIError):
            with engine.begin() as connection:
                connection.execute(text("SET LOCAL ROLE tit_growth_app"))
                connection.execute(
                    text(
                        """
                        UPDATE public.outbox_events
                        SET status='PENDING',attempt_count=0,last_error=NULL,
                            recovery_count=1,
                            recovered_at=transaction_timestamp(),
                            row_version=row_version+1
                        WHERE event_id=:event_id
                        """
                    ),
                    {"event_id": event_id},
                )

        applied = _recover(
            engine, "recovery-command-response-lost", event_id, payload_hash
        )
        assert applied["replay_status"] == "APPLIED"
        assert applied["recovery_count"] == 1
        assert applied["row_version"] == 10
        replayed = _recover(
            engine, "recovery-command-response-lost", event_id, payload_hash
        )
        assert replayed["replay_status"] == "REPLAYED"
        assert replayed["row_version"] == 10
        with pytest.raises(
            DBAPIError, match="OUTBOX_RECOVERY_COMMAND_CONFLICT"
        ):
            with engine.begin() as connection:
                _role_call(
                    connection,
                    "tit_dts_outbox_recovery_runtime",
                    """
                    SELECT public.recover_outbox_event_v2(
                        'recovery-command-response-lost',:event_id,
                        :payload_hash,0,'different-reason'
                    )
                    """,
                    {"event_id": event_id, "payload_hash": payload_hash},
                )

        with engine.begin() as connection:
            concurrent_event_id, _ = _insert_pending(
                connection, "concurrent-recovery"
            )
        concurrent_hash, _, _ = _drive_to_dead(engine, concurrent_event_id)
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(
                    _recover,
                    engine,
                    f"concurrent-recovery-{index}",
                    concurrent_event_id,
                    concurrent_hash,
                )
                for index in range(2)
            ]
        outcomes = []
        for future in futures:
            try:
                outcomes.append(future.result()["replay_status"])
            except DBAPIError as exc:
                assert "OUTBOX_RECOVERY_STALE" in str(exc)
                outcomes.append("STALE")
        assert sorted(outcomes) == ["APPLIED", "STALE"]

        _run_alembic_expect_failure(
            backend_dir,
            database_url,
            "refusing Outbox v2 downgrade: recovery history exists",
            "downgrade",
            REV84,
        )
    finally:
        engine.dispose()
        subprocess.run(
            [pg_ctl, "-D", str(data_dir), "-m", "fast", "-w", "stop"],
            check=True,
            capture_output=True,
            text=True,
        )
