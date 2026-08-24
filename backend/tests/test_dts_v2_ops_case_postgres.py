from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, URL
from sqlalchemy.exc import DBAPIError

from app.dts_v2_outbox_worker import (
    DtsV2OutboxProcessingError,
    DtsV2OutboxWorker,
)
from app.dts_v2_technical_cases import (
    DtsV2OutboxTechnicalCaseStore,
    DtsV2TechnicalCaseError,
)
from test_dts_v2_source_current_guards_postgres import (
    _seed_external_personalized_catalog,
)


POSTGRES_BINARIES = ("initdb", "pg_ctl", "postgres")
REVISION_85 = "20260822_85_outbox_three_state"


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


@pytest.fixture()
def ops_case_postgres(tmp_path: Path) -> Iterator[tuple[Engine, Engine, Engine]]:
    backend_dir = Path(__file__).resolve().parents[1]
    data_dir = tmp_path / "postgres-data"
    log_path = tmp_path / "postgres.log"
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])

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
            f"-p {port} -c listen_addresses=127.0.0.1 -c fsync=off",
            "-w",
            "start",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    def url(role: str) -> str:
        return URL.create(
            "postgresql+psycopg",
            username=role,
            host="127.0.0.1",
            port=port,
            database="postgres",
        ).render_as_string(hide_password=False)

    admin_url = url("postgres")
    admin_engine = create_engine(admin_url)
    worker_engine: Engine | None = None
    recovery_engine: Engine | None = None
    try:
        with admin_engine.begin() as connection:
            for role_name in (
                "tit_growth_app",
                "tit_teacher_crud",
                "tit_dts_ingest_runtime",
                "tit_dts_outbox_recovery_runtime",
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
        _run_alembic(
            backend_dir,
            admin_url,
            "upgrade",
            "20260807_46_teacher_g01_source",
        )
        with admin_engine.begin() as connection:
            _seed_external_personalized_catalog(connection)
        _run_alembic(backend_dir, admin_url, "upgrade", "head")

        # Empty history is reversibly additive, and a replay sees the existing
        # restricted runtime role instead of creating a second identity.
        _run_alembic(backend_dir, admin_url, "downgrade", REVISION_85)
        _run_alembic(backend_dir, admin_url, "upgrade", "head")
        with admin_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    SELECT set_config(
                      'tit.dts_initial_epoch_bootstrap','on',true
                    );
                    INSERT INTO public.dts_pipeline_control(
                      control_id,mode,row_version,projection_generation,
                      consumer_group,initial_h0_vector,
                      initial_h0_vector_hash,initial_h0_bootstrap_run_id,
                      time_catchup_status,changed_at,changed_by
                    ) VALUES (
                      'PRIMARY','V1_COMPAT_DUAL_CAPTURE',1,0,
                      'ops-case-test-fleet',jsonb_build_array(
                        jsonb_build_object('test_only',true)
                      ),repeat('a',64),'ops-case-test-bootstrap',
                      'NOT_REQUIRED',transaction_timestamp(),
                      'OPS_CASE_TEST'
                    );
                    UPDATE public.dts_pipeline_control
                    SET mode='V2_PRIMARY',row_version=2,
                        projection_generation=1,
                        changed_at=transaction_timestamp(),
                        changed_by='OPS_CASE_TEST'
                    WHERE control_id='PRIMARY';
                    """
                )
            )

        worker_engine = create_engine(url("tit_dts_outbox_worker_runtime"))
        recovery_engine = create_engine(url("tit_dts_outbox_recovery_runtime"))
        yield admin_engine, worker_engine, recovery_engine
    finally:
        if recovery_engine is not None:
            recovery_engine.dispose()
        if worker_engine is not None:
            worker_engine.dispose()
        admin_engine.dispose()
        subprocess.run(
            [pg_ctl, "-D", str(data_dir), "-m", "fast", "-w", "stop"],
            check=False,
            capture_output=True,
            text=True,
        )


class _SuccessProcessor:
    def process_event(self, connection, event):
        return {"applied": 1}


class _FailureProcessor:
    def process_event(self, connection, event):
        raise DtsV2OutboxProcessingError("OPS_CASE_TEST_TRANSIENT")


def _insert_pending_event(
    engine: Engine,
    *,
    suffix: str,
    attempt_count: int = 7,
    invalid_aggregate_key: bool = False,
) -> tuple[str, str, dict[str, object]]:
    outbox_id = f"ops-case-outbox-{suffix}"
    event_id = f"ops-case-event-{suffix}"
    payload = {
        "aggregate_key": (
            "unsafe"
            if invalid_aggregate_key
            else {
                "source_region": "dom",
                "source_appoint_id": f"course-{suffix}",
            }
        ),
        "aggregate_revision": 1,
    }
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO public.outbox_events(
                  outbox_id,event_id,aggregate_type,aggregate_id,event_type,
                  payload,payload_sha256,status,available_at,attempt_count,
                  recovery_count,recovered_at,row_version,last_error,
                  settled_by_run_id,created_at,published_at
                ) VALUES (
                  :outbox_id,:event_id,'COURSE',:aggregate_id,
                  'source_wide.changed.v2',CAST(:payload AS jsonb),
                  public.dts_canonical_json_sha256_v1(CAST(:payload AS jsonb)),
                  'PENDING',transaction_timestamp()-interval '1 minute',
                  0,0,NULL,1,NULL,NULL,
                  transaction_timestamp(),NULL
                )
                """
            ),
            {
                "outbox_id": outbox_id,
                "event_id": event_id,
                "aggregate_id": f"course-aggregate-{suffix}",
                "payload": json.dumps(payload, separators=(",", ":")),
            },
        )
    for retry_attempt in range(1, attempt_count + 1):
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE public.outbox_events
                    SET attempt_count=:attempt_count,
                        last_error='PREVIOUS_RETRY',
                        available_at=transaction_timestamp()
                            + interval '1 microsecond',
                        row_version=row_version+1
                    WHERE outbox_id=:outbox_id
                    """
                ),
                {
                    "attempt_count": retry_attempt,
                    "outbox_id": outbox_id,
                },
            )
    return outbox_id, event_id, payload


def _case_identity(event_id: str) -> tuple[str, str]:
    source_ref = f"tech-case:projection:{event_id}"
    return "v2case:" + hashlib.sha256(source_ref.encode()).hexdigest(), source_ref


def _expect_db_failure(engine: Engine, sql: str, **parameters: object) -> None:
    with pytest.raises(DBAPIError):
        with engine.begin() as connection:
            connection.execute(text(sql), parameters)


@pytest.mark.skipif(
    not _postgres_tools_available(),
    reason="local PostgreSQL binaries are required for Ops Case contracts",
)
def test_ops_case_identity_acl_and_true_completion_recovery(
    ops_case_postgres: tuple[Engine, Engine, Engine],
) -> None:
    admin, worker_bind, recovery_bind = ops_case_postgres

    with admin.connect() as connection:
        assert connection.execute(
            text(
                """
                SELECT rolcanlogin,rolinherit,rolsuper,rolcreatedb,
                       rolcreaterole,rolreplication,rolbypassrls
                FROM pg_roles
                WHERE rolname='tit_dts_outbox_worker_runtime'
                """
            )
        ).one() == (True, False, False, False, False, False, False)
        assert connection.execute(
            text(
                """
                SELECT
                  has_table_privilege(
                    'tit_dts_outbox_worker_runtime',
                    'public.outbox_events','SELECT'
                  ),
                  has_column_privilege(
                    'tit_dts_outbox_worker_runtime',
                    'public.outbox_events','status','UPDATE'
                  ),
                  has_column_privilege(
                    'tit_dts_outbox_worker_runtime',
                    'public.outbox_events','payload','UPDATE'
                  ),
                  has_table_privilege(
                    'tit_dts_outbox_worker_runtime',
                    'public.ops_cases','INSERT'
                  ),
                  has_function_privilege(
                    'tit_dts_outbox_worker_runtime',
                    'public.record_dts_v2_technical_case('
                    'text,text,text,text,text,text,text,text,text,bigint,text,'
                    'integer,bigint)','EXECUTE'
                  ),
                  has_function_privilege(
                    'tit_growth_app',
                    'public.record_dts_v2_technical_case('
                    'text,text,text,text,text,text,text,text,text,bigint,text,'
                    'integer,bigint)','EXECUTE'
                  )
                """
            )
        ).one() == (True, True, False, False, True, False)

    # Completion correction is keyed by region/course and may initially lack
    # a teacher; an unrelated business Case may not.
    completion_id = "course-completion-correction:dom:completion-1"
    with admin.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO public.ops_cases(
                  case_id,case_type,teacher_id,task_id,priority,status,
                  source_reason,external_action_status,created_at,payload,
                  updated_at,source_ref,source_region,source_appoint_id,
                  case_revision,row_version,evidence_fingerprint,
                  recovery_evidence_count
                ) VALUES (
                  :case_id,'COURSE_COMPLETION_CORRECTION',NULL,NULL,'P1',
                  'OPEN','FIRST_END_TEACHER_MISSING','NOT_REQUESTED',
                  transaction_timestamp(),'{}'::jsonb,
                  transaction_timestamp(),:case_id,'dom','completion-1',
                  1,1,repeat('a',64),0
                )
                """
            ),
            {"case_id": completion_id},
        )
    _expect_db_failure(
        admin,
        """
        INSERT INTO public.ops_cases(
          case_id,case_type,teacher_id,priority,status,
          external_action_status,created_at,payload,updated_at
        ) VALUES (
          'business-null-teacher','SEVERE_COMPLAINT',NULL,'P1','OPEN',
          'NOT_REQUESTED',transaction_timestamp(),'{}'::jsonb,
          transaction_timestamp()
        )
        """,
    )
    _expect_db_failure(
        admin,
        """
        INSERT INTO public.ops_cases(
          case_id,case_type,teacher_id,priority,status,external_action_status,
          created_at,payload,updated_at,source_ref,source_region,
          source_appoint_id,case_revision,row_version,evidence_fingerprint,
          recovery_evidence_count
        ) VALUES (
          'course-completion-correction:dom:completion-1-duplicate',
          'COURSE_COMPLETION_CORRECTION',NULL,'P1','OPEN','NOT_REQUESTED',
          transaction_timestamp(),'{}'::jsonb,transaction_timestamp(),
          'course-completion-correction:dom:completion-1-duplicate','dom',
          'completion-1',1,1,repeat('b',64),0
        )
        """,
    )

    outbox_id, event_id, _ = _insert_pending_event(admin, suffix="auto")
    case_id, source_ref = _case_identity(event_id)
    source_position = {
        "v": 1,
        "source_timestamp": "2026-08-22T00:00:00.000000Z",
        "record_id_type": "none",
        "record_id": None,
        "source_partition_epoch_id": "epoch-ops-case-test",
        "topic": "topic-ops-case-test",
        "partition_id": 0,
        "offset_value": 1,
    }
    with admin.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO public.ops_decisions(
                  decision_id,case_id,decision,note,decided_at,actor_type,
                  payload,expected_case_revision,
                  expected_conflict_fingerprint,expected_source_revision,
                  expected_source_position,downstream_projection_status,
                  projection_event_ids,row_version
                ) VALUES (
                  'completion-decision-auto',:case_id,
                  'KEEP_FROZEN_COMPLETION','verified',
                  transaction_timestamp(),'OPS_USER','{}'::jsonb,1,
                  repeat('a',64),1,CAST(:source_position AS jsonb),'PENDING',
                  jsonb_build_array(CAST(:event_id AS text)),1
                )
                """
            ),
            {
                "case_id": completion_id,
                "event_id": event_id,
                "source_position": json.dumps(
                    source_position, separators=(",", ":")
                ),
            },
        )
    worker = DtsV2OutboxWorker(
        worker_bind,
        processor=_FailureProcessor(),
        technical_cases=DtsV2OutboxTechnicalCaseStore(),
    )
    assert worker.run_once(max_events=1)["dead_letters"] == 1
    with admin.connect() as connection:
        assert connection.execute(
            text(
                """
                SELECT status,attempt_count,row_version
                FROM public.outbox_events WHERE outbox_id=:outbox_id
                """
            ),
            {"outbox_id": outbox_id},
        ).one() == ("DEAD_LETTER", 8, 9)
        case_row = connection.execute(
            text(
                """
                SELECT case_id,case_type,status,case_revision,row_version,
                       source_region,source_appoint_id,teacher_id
                FROM public.ops_cases WHERE source_ref=:source_ref
                """
            ),
            {"source_ref": source_ref},
        ).one()
        assert case_row == (
            case_id,
            "DOWNSTREAM_PROJECTION_DEAD",
            "OPEN",
            1,
            1,
            "dom",
            "course-auto",
            None,
        )
        assert connection.execute(
            text(
                """
                SELECT downstream_projection_status,row_version
                FROM public.ops_decisions
                WHERE decision_id='completion-decision-auto'
                """
            )
        ).one() == ("DEAD_LETTER", 2)

    # Same proof is response-loss safe; a forged source_ref is rejected.
    with worker_bind.begin() as connection:
        assert connection.execute(
            text(
                """
                SELECT public.record_dts_v2_technical_case(
                  :case_id,'DOWNSTREAM_PROJECTION_DEAD',:source_ref,
                  NULL,'dom','course-auto','OPS_CASE_TEST_TRANSIENT',
                  'COURSE','course-aggregate-auto',1,:event_id,8,0
                )
                """
            ),
            {
                "case_id": case_id,
                "source_ref": source_ref,
                "event_id": event_id,
            },
        ).scalar_one() == "UNCHANGED"

    def replay_dead_proof() -> str:
        with worker_bind.begin() as connection:
            return connection.execute(
                text(
                    """
                    SELECT public.record_dts_v2_technical_case(
                      :case_id,'DOWNSTREAM_PROJECTION_DEAD',:source_ref,
                      NULL,'dom','course-auto','OPS_CASE_TEST_TRANSIENT',
                      'COURSE','course-aggregate-auto',1,:event_id,8,0
                    )
                    """
                ),
                {
                    "case_id": case_id,
                    "source_ref": source_ref,
                    "event_id": event_id,
                },
            ).scalar_one()

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(lambda _: replay_dead_proof(), range(2))) == [
            "UNCHANGED",
            "UNCHANGED",
        ]
    with worker_bind.begin() as connection:
        assert connection.execute(
            text(
                """
                SELECT public.record_dts_v2_technical_case(
                  :case_id,'DOWNSTREAM_PROJECTION_DEAD',:source_ref,
                  NULL,'dom','course-auto','OPS_CASE_SECOND_TRANSIENT',
                  'COURSE','course-aggregate-auto',1,:event_id,8,0
                )
                """
            ),
            {
                "case_id": case_id,
                "source_ref": source_ref,
                "event_id": event_id,
            },
        ).scalar_one() == "UPDATED"
    with admin.connect() as connection:
        assert connection.execute(
            text(
                """
                SELECT case_revision,row_version,source_reason
                FROM public.ops_cases WHERE case_id=:case_id
                """
            ),
            {"case_id": case_id},
        ).one() == (2, 2, "OPS_CASE_SECOND_TRANSIENT")
    _expect_db_failure(
        worker_bind,
        """
        SELECT public.record_dts_v2_technical_case(
          :case_id,'DOWNSTREAM_PROJECTION_DEAD','tech-case:projection:wrong',
          NULL,'dom','course-auto','OPS_CASE_TEST_TRANSIENT','COURSE',
          'course-aggregate-auto',1,:event_id,8,0
        )
        """,
        case_id=case_id,
        event_id=event_id,
    )

    growth_url = str(admin.url).replace("postgres@", "tit_growth_app@")
    growth = create_engine(growth_url)
    try:
        _expect_db_failure(
            growth,
            """
            SELECT public.record_dts_v2_technical_case(
              :case_id,'DOWNSTREAM_PROJECTION_DEAD',:source_ref,NULL,
              'dom','course-auto','OPS_CASE_TEST_TRANSIENT','COURSE',
              'course-aggregate-auto',1,:event_id,8,0
            )
            """,
            case_id=case_id,
            source_ref=source_ref,
            event_id=event_id,
        )
        _expect_db_failure(
            growth,
            """
            INSERT INTO public.ops_cases(
              case_id,case_type,teacher_id,priority,status,
              external_action_status,created_at,payload,updated_at,
              source_ref,case_revision,row_version,evidence_fingerprint,
              recovery_evidence_count
            ) VALUES (
              :case_id,'DOWNSTREAM_PROJECTION_DEAD',NULL,'P1','OPEN',
              'NOT_REQUESTED',transaction_timestamp(),'{}'::jsonb,
              transaction_timestamp(),:source_ref,1,1,repeat('f',64),0
            )
            """,
            case_id=case_id,
            source_ref=source_ref,
        )
    finally:
        growth.dispose()

    with recovery_bind.begin() as connection:
        recovery = connection.execute(
            text(
                """
                SELECT public.recover_outbox_event_v2(
                  'ops-case-auto',:event_id,
                  (SELECT payload_sha256 FROM public.outbox_events
                   WHERE event_id=:event_id),0,'verified fix'
                )
                """
            ),
            {"event_id": event_id},
        ).scalar_one()
        assert recovery["status"] == "PENDING"
        assert recovery["recovery_count"] == 1
    _expect_db_failure(
        worker_bind,
        """
        SELECT public.record_dts_v2_technical_case_recovery(
          :case_id,:source_ref,:event_id,1
        )
        """,
        case_id=case_id,
        source_ref=source_ref,
        event_id=event_id,
    )
    probe = worker_bind.connect()
    probe_tx = probe.begin()
    try:
        probe.execute(
            text(
                """
                UPDATE public.outbox_events
                SET status='PUBLISHED',published_at=transaction_timestamp(),
                    last_error=NULL,row_version=row_version+1
                WHERE event_id=:event_id
                """
            ),
            {"event_id": event_id},
        )
        assert probe.execute(
            text(
                """
                SELECT public.record_dts_v2_technical_case_recovery(
                  :case_id,:source_ref,:event_id,1
                )
                """
            ),
            {
                "case_id": case_id,
                "source_ref": source_ref,
                "event_id": event_id,
            },
        ).scalar_one() == "RESOLVED"
    finally:
        probe_tx.rollback()
        probe.close()
    success_worker = DtsV2OutboxWorker(
        worker_bind,
        processor=_SuccessProcessor(),
        technical_cases=DtsV2OutboxTechnicalCaseStore(),
    )
    success_result = success_worker.run_once(max_events=1)
    assert success_result["published"] == 1, success_result
    with admin.connect() as connection:
        assert connection.execute(
            text(
                """
                SELECT status,case_revision,row_version,
                       recovery_evidence_count,last_recovery_count
                FROM public.ops_cases WHERE case_id=:case_id
                """
            ),
            {"case_id": case_id},
        ).one() == ("RESOLVED", 3, 3, 1, 1)
        assert connection.execute(
            text(
                """
                SELECT work_status,case_status_before,case_status_after
                FROM public.ops_case_recovery_events
                WHERE case_id=:case_id
                """
            ),
            {"case_id": case_id},
        ).one() == ("PUBLISHED", "OPEN", "RESOLVED")
        assert connection.execute(
            text(
                """
                SELECT downstream_projection_status,row_version
                FROM public.ops_decisions
                WHERE decision_id='completion-decision-auto'
                """
            )
        ).one() == ("PUBLISHED", 3)
    with worker_bind.begin() as connection:
        assert connection.execute(
            text(
                """
                SELECT public.record_dts_v2_technical_case_recovery(
                  :case_id,:source_ref,:event_id,1
                )
                """
            ),
            {
                "case_id": case_id,
                "source_ref": source_ref,
                "event_id": event_id,
            },
        ).scalar_one() == "UNCHANGED"

    # The DEAD transition and Case command share the outer transaction.  An
    # unsafe subject therefore leaves the original retry generation intact.
    bad_outbox_id, _, _ = _insert_pending_event(
        admin,
        suffix="atomic-bad-subject",
        invalid_aggregate_key=True,
    )
    bad_worker = DtsV2OutboxWorker(
        worker_bind,
        processor=_FailureProcessor(),
        technical_cases=DtsV2OutboxTechnicalCaseStore(),
    )
    with pytest.raises(
        DtsV2TechnicalCaseError,
        match="DTS_V2_TECHNICAL_CASE_AGGREGATE_KEY_INVALID",
    ):
        bad_worker.run_once(max_events=1)
    with admin.connect() as connection:
        assert connection.execute(
            text(
                """
                SELECT status,attempt_count,row_version
                FROM public.outbox_events WHERE outbox_id=:outbox_id
                """
            ),
            {"outbox_id": bad_outbox_id},
        ).one() == ("PENDING", 7, 8)


@pytest.mark.skipif(
    not _postgres_tools_available(),
    reason="local PostgreSQL binaries are required for Ops Case contracts",
)
def test_manual_review_is_not_auto_closed_and_history_blocks_downgrade(
    ops_case_postgres: tuple[Engine, Engine, Engine],
) -> None:
    admin, worker_bind, recovery_bind = ops_case_postgres
    _, event_id, _ = _insert_pending_event(admin, suffix="review")
    case_id, source_ref = _case_identity(event_id)
    failing_worker = DtsV2OutboxWorker(
        worker_bind,
        processor=_FailureProcessor(),
        technical_cases=DtsV2OutboxTechnicalCaseStore(),
    )
    assert failing_worker.run_once(max_events=1)["dead_letters"] == 1

    growth_url = str(admin.url).replace("postgres@", "tit_growth_app@")
    growth = create_engine(growth_url)
    with growth.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE public.ops_cases
                SET status='IN_REVIEW',row_version=row_version+1,
                    updated_at=transaction_timestamp()
                WHERE case_id=:case_id
                """
            ),
            {"case_id": case_id},
        )
    _expect_db_failure(
        growth,
        """
        UPDATE public.ops_cases
        SET status='RESOLVED',row_version=row_version+1,
            updated_at=transaction_timestamp()
        WHERE case_id=:case_id
        """,
        case_id=case_id,
    )
    with recovery_bind.begin() as connection:
        connection.execute(
            text(
                """
                SELECT public.recover_outbox_event_v2(
                  'ops-case-review',:event_id,
                  (SELECT payload_sha256 FROM public.outbox_events
                   WHERE event_id=:event_id),0,'reviewed fix'
                )
                """
            ),
            {"event_id": event_id},
        ).scalar_one()
    success_worker = DtsV2OutboxWorker(
        worker_bind,
        processor=_SuccessProcessor(),
        technical_cases=DtsV2OutboxTechnicalCaseStore(),
    )
    assert success_worker.run_once(max_events=1)["published"] == 1
    with admin.connect() as connection:
        assert connection.execute(
            text(
                """
                SELECT status,recovery_evidence_count
                FROM public.ops_cases WHERE case_id=:case_id
                """
            ),
            {"case_id": case_id},
        ).one() == ("IN_REVIEW", 1)
    with growth.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE public.ops_cases
                SET status='RESOLVED',row_version=row_version+1,
                    updated_at=transaction_timestamp()
                WHERE case_id=:case_id
                """
            ),
            {"case_id": case_id},
        )
    growth.dispose()
    with admin.connect() as connection:
        assert connection.execute(
            text("SELECT status FROM public.ops_cases WHERE case_id=:case_id"),
            {"case_id": case_id},
        ).scalar_one() == "RESOLVED"

    # Reach the rev86 protected-history guard through a non-primary rollback
    # posture.  The runtime cutover command is outside this migration-focused
    # test, so bypass only the transition triggers while preserving the row.
    with admin.begin() as connection:
        connection.execute(text("SET LOCAL session_replication_role='replica'"))
        connection.execute(
            text(
                """
                UPDATE public.dts_pipeline_control
                SET mode='ROLLED_BACK',row_version=row_version+1,
                    changed_at=transaction_timestamp(),
                    changed_by='OPS_CASE_DOWNGRADE_GUARD_TEST'
                WHERE control_id='PRIMARY'
                """
            )
        )

    backend_dir = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment["APP_ENV"] = "test"
    environment["DATABASE_URL"] = admin.url.render_as_string(
        hide_password=False
    )
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", REVISION_85],
        cwd=backend_dir,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "protected history exists" in f"{result.stdout}\n{result.stderr}"
