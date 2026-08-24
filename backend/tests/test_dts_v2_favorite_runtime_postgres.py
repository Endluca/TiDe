from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
from typing import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, URL
from sqlalchemy.exc import DBAPIError

from test_dts_v2_ops_case_postgres import (
    POSTGRES_BINARIES,
    _seed_external_personalized_catalog,
)


DOM_STUDENT = "dom:v1:" + "c" * 64
REVISION_87 = "20260822_87_favorite_runtime"


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
def favorite_runtime_postgres(
    tmp_path: Path,
) -> Iterator[tuple[Engine, Engine]]:
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

    admin = create_engine(url("postgres"))
    worker: Engine | None = None
    try:
        with admin.begin() as connection:
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
            url("postgres"),
            "upgrade",
            "20260807_46_teacher_g01_source",
        )
        with admin.begin() as connection:
            _seed_external_personalized_catalog(connection)
        _run_alembic(backend_dir, url("postgres"), "upgrade", REVISION_87)
        _run_alembic(
            backend_dir,
            url("postgres"),
            "downgrade",
            "20260822_86_ops_case_v2",
        )
        _run_alembic(backend_dir, url("postgres"), "upgrade", REVISION_87)
        with admin.begin() as connection:
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
                      'favorite-test-fleet',jsonb_build_array(
                        jsonb_build_object('test_only',true)
                      ),repeat('a',64),'favorite-test-bootstrap',
                      'NOT_REQUIRED',transaction_timestamp(),
                      'FAVORITE_RUNTIME_TEST'
                    );
                    UPDATE public.dts_pipeline_control
                    SET mode='V2_PRIMARY',row_version=2,
                        changed_at=transaction_timestamp(),
                        changed_by='FAVORITE_RUNTIME_TEST'
                    WHERE control_id='PRIMARY';
                    """
                )
            )
        worker = create_engine(url("tit_growth_app"))
        yield admin, worker
    finally:
        if worker is not None:
            worker.dispose()
        admin.dispose()
        subprocess.run(
            [pg_ctl, "-D", str(data_dir), "-m", "fast", "-w", "stop"],
            check=False,
            capture_output=True,
            text=True,
        )


def _seed_course(
    connection,
    *,
    course_id: str,
    end_time: datetime,
    offset: int,
) -> None:
    source_position = {
        "v": 1,
        "source_timestamp": end_time.isoformat().replace("+00:00", "Z"),
        "record_id_type": "numeric",
        "record_id": course_id,
        "source_partition_epoch_id": "favorite-seed-epoch",
        "topic": "favorite-seed-topic",
        "partition_id": 0,
        "offset_value": offset,
    }
    connection.execute(
        text(
            """
            INSERT INTO public.dts_source_rows(
              source_region,source_table,source_key,source_key_data,
              dependency_keys,source_row,is_deleted,source_timestamp,
              last_record_id,source_position,last_topic,last_partition,
              last_offset,row_version,updated_at,source_row_revision,
              last_source_partition_epoch_id,last_version_kind,
              source_position_v2,record_id_type,record_id_numeric,
              record_id_text,source_timestamp_v2,source_payload_hash,
              provenance_state,source_key_type,source_key_numeric,
              source_key_text,source_schema_profile_id,source_field_types
            ) VALUES (
              'dom','dom_appoint',CAST(:course_id AS text),
              jsonb_build_object('id',CAST(:course_id AS numeric)),
              jsonb_build_object(
                'appoint_ids',jsonb_build_array(CAST(:course_id AS text)),
                'teacher_ids',jsonb_build_array('100'),
                'student_tokens',jsonb_build_array(CAST(:student_token AS text))
              ),
              jsonb_build_object(
                'id',CAST(:course_id AS numeric),'t_id',100,
                'student_token',CAST(:student_token AS text),'status','end'
              ),false,1,CAST(:offset AS bigint),'favorite-seed-position',
              'favorite-seed-topic',0,:offset,1,clock_timestamp(),1,
              'favorite-seed-epoch','CDC',CAST(:source_position AS jsonb),
              'numeric',CAST(:course_id AS numeric),NULL,:end_time,
              repeat('a',64),'V2_CONFIRMED','NUMERIC',
              CAST(:course_id AS numeric),NULL,'favorite-test-profile',
              jsonb_build_object(
                'id','NUMERIC','t_id','NUMERIC','student_token','TEXT',
                'status','TEXT'
              )
            )
            """
        ),
        {
            "course_id": course_id,
            "student_token": DOM_STUDENT,
            "offset": offset,
            "end_time": end_time,
            "source_position": json.dumps(source_position),
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO public.source_courses(
              source_region,source_appoint_id,student_token,source_status,
              current_teacher_id,current_teacher_id_type,
              current_participation_seq,completion_participation_seq,
              completion_teacher_id,completion_teacher_id_type,
              completion_frozen_at,completion_end_time,
              completion_student_token,completion_source_position,
              completion_source_revision,initial_completion_snapshot,
              completion_conflict_status,source_is_deleted,evidence_status,
              last_applied_event_position,last_applied_source_revision,
              row_version
            ) VALUES (
              'dom',:course_id,:student_token,'end','100','NUMERIC',1,
              1,'100','NUMERIC',:end_time,:end_time,:student_token,
              CAST(:source_position AS jsonb),1,
              jsonb_build_object(
                'teacher_id','100','teacher_id_type','NUMERIC',
                'participation_seq',1,'completion_end_time',:end_time
              ),'NONE',false,'CONFIRMED',CAST(:source_position AS jsonb),1,1
            )
            """
        ),
        {
            "course_id": course_id,
            "student_token": DOM_STUDENT,
            "end_time": end_time,
            "source_position": json.dumps(source_position),
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO public.source_course_participations(
              source_region,source_appoint_id,participation_seq,teacher_id,
              teacher_id_type,participation_status,participation_role,
              is_current,assigned_at,assigned_at_evidence_status,ended_at,
              source_deleted,assignment_source_partition_epoch_id,
              assignment_event_topic,assignment_event_partition,
              assignment_event_offset,assignment_source_row_revision,
              assignment_event_phase,row_version
            ) VALUES (
              'dom',:course_id,1,'100','NUMERIC','end','COMPLETION',true,
              :end_time,'CONFIRMED',:end_time,false,
              'favorite-seed-epoch','favorite-seed-topic',0,:offset,1,
              'AFTER',1
            )
            """
        ),
        {"course_id": course_id, "end_time": end_time, "offset": offset},
    )


def _seed_runtime(admin: Engine) -> None:
    now = datetime.now(timezone.utc)
    with admin.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE public.dts_pipeline_control
                SET projection_generation=3,row_version=row_version+1,
                    changed_at=transaction_timestamp(),
                    changed_by='FAVORITE_RUNTIME_TEST'
                WHERE control_id='PRIMARY' AND mode='V2_PRIMARY';
                INSERT INTO public.teachers(
                  teacher_id,camp_enrollment_id,name,country,timezone,camp_day,
                  online_status,graduation_state,gold_qualified,total_score,
                  graduation_threshold,data_mode,source_snapshot_label,payload,
                  created_at,updated_at
                ) VALUES (
                  '100','camp-favorite-100','Favorite Teacher','US','UTC',1,
                  'NEW','IN_CAMP',false,0,100,'SOURCE','favorite-test',
                  jsonb_build_object('graduation_state','IN_CAMP'),
                  transaction_timestamp(),transaction_timestamp()
                );
                SET LOCAL session_replication_role='replica';
                ALTER TABLE public.dts_source_rows
                  DROP CONSTRAINT ck_dts_source_row_v2_transition_shape;
                """
            )
        )
        _seed_course(
            connection,
            course_id="9101",
            end_time=now - timedelta(hours=72),
            offset=101,
        )
        _seed_course(
            connection,
            course_id="9102",
            end_time=now - timedelta(hours=48),
            offset=102,
        )
        _seed_course(
            connection,
            course_id="9199",
            end_time=now + timedelta(hours=2),
            offset=199,
        )
        connection.execute(
            text(
                """
                INSERT INTO public.teacher_student_relationship_current(
                  source_region,teacher_id,teacher_id_type,student_token,
                  is_favorited,is_blocked,last_business_effective_at,
                  effective_time_evidence_status,last_event_sequence,
                  last_source_partition_epoch_id,last_topic,
                  last_partition_id,last_offset_value,
                  last_source_row_revision,row_version,updated_at
                ) VALUES (
                  'dom','100','NUMERIC',:student_token,true,false,
                  :effective_at,'CONFIRMED',1,'favorite-seed-epoch',
                  'favorite-seed-topic',0,999,1,1,clock_timestamp()
                )
                """
            ),
            {
                "student_token": DOM_STUDENT,
                "effective_at": now - timedelta(days=10),
            },
        )


def _materialize(
    worker: Engine,
    *,
    course_id: str,
    end_time: datetime,
) -> dict:
    with worker.begin() as connection:
        return connection.execute(
            text(
                """
                SELECT public.materialize_favorite_observation_v2(
                  'dom',:course_id,'NUMERIC','100','NUMERIC',:student_token,
                  1,:observed_at,'favorite-score-v1',3,:event_id
                )
                """
            ),
            {
                "course_id": course_id,
                "student_token": DOM_STUDENT,
                "observed_at": end_time + timedelta(hours=24),
                "event_id": f"teacher-student-event-{course_id}",
            },
        ).scalar_one()


def _claim(worker: Engine, worker_id: str = "favorite-pg-worker") -> dict | None:
    with worker.begin() as connection:
        return connection.execute(
            text(
                """
                SELECT claim FROM public.claim_favorite_observations_v2(
                  :worker_id,1
                ) AS claim
                """
            ),
            {"worker_id": worker_id},
        ).scalar_one_or_none()


def _complete(worker: Engine, claim: dict, result_status: str) -> dict:
    relation_state = {
        "CONFIRMED_TRUE": True,
        "CONFIRMED_FALSE": False,
        "WAITING_EVIDENCE": None,
    }[result_status]
    evidence_status = (
        "CONFIRMED" if result_status != "WAITING_EVIDENCE" else "SOURCE_MISSING"
    )
    error_code = (
        None
        if result_status != "WAITING_EVIDENCE"
        else "SOURCE_CONFLICT:COURSE_COMPLETION_PENDING"
    )
    with worker.begin() as connection:
        return connection.execute(
            text(
                """
                SELECT public.complete_favorite_observation_v2(
                  :source_region,:source_appoint_id,:observation_revision,
                  :lease_owner,:lease_token,:row_version,
                  :claimed_evidence_revision,:fingerprint,:result_status,
                  :relation_state,:evidence_status,:error_code,
                  'favorite-score-v1'
                )
                """
            ),
            {
                **claim,
                "fingerprint": claim["required_evidence_fingerprint"],
                "result_status": result_status,
                "relation_state": relation_state,
                "evidence_status": evidence_status,
                "error_code": error_code,
            },
        ).scalar_one()


@pytest.mark.skipif(
    not _postgres_tools_available(),
    reason="local PostgreSQL binaries are required for favorite runtime",
)
def test_favorite_global_generation_unique_reselect_and_mode_gate(
    favorite_runtime_postgres: tuple[Engine, Engine],
) -> None:
    admin, worker = favorite_runtime_postgres
    _seed_runtime(admin)
    with admin.connect() as connection:
        course_times = dict(
            connection.execute(
                text(
                    """
                    SELECT source_appoint_id,completion_end_time
                    FROM public.source_courses
                    WHERE source_appoint_id IN ('9101','9102','9199')
                    """
                )
            ).all()
        )

    assert _materialize(
        worker, course_id="9102", end_time=course_times["9102"]
    )["projection_generation"] == 3
    later_claim = _claim(worker)
    assert later_claim is not None
    later_done = _complete(worker, later_claim, "CONFIRMED_TRUE")
    assert later_done["attributions_awarded"] == 1
    assert later_done["score_entries_created"] == 1

    assert _materialize(
        worker, course_id="9101", end_time=course_times["9101"]
    )["status"] == "CREATED"
    earlier_claim = _claim(worker)
    assert earlier_claim is not None
    earlier_done = _complete(worker, earlier_claim, "CONFIRMED_TRUE")
    assert earlier_done["attributions_reselected"] == 1
    assert earlier_done["score_entries_created"] == 2

    with admin.connect() as connection:
        attribution = connection.execute(
            text(
                """
                SELECT source_appoint_id,status,award_generation,
                       award_projection_generation
                FROM public.course_favorite_attributions
                WHERE source_region='dom' AND teacher_id='100'
                  AND student_token=:student_token
                """
            ),
            {"student_token": DOM_STUDENT},
        ).mappings().one()
        entries = connection.execute(
            text(
                """
                SELECT entry_type,projection_generation,count(*)
                FROM public.score_entries
                WHERE entry_type IN ('FAVORITE_AWARD','FAVORITE_REVERSAL')
                GROUP BY entry_type,projection_generation
                ORDER BY entry_type
                """
            )
        ).all()
    assert dict(attribution) == {
        "source_appoint_id": "9101",
        "status": "AWARDED",
        "award_generation": 2,
        "award_projection_generation": 3,
    }
    assert all(row.projection_generation == 3 for row in entries)
    assert sum(row.count for row in entries) == 3

    # A late historical correction changes the evidence fingerprint for the
    # whole pair: the first favorite interval ended before course 9101's
    # observation and a new interval began before course 9102's observation.
    # Both observations must be re-evaluated before the unique attribution can
    # move back to the still-true later course.
    with admin.begin() as connection:
        connection.execute(text("SET LOCAL session_replication_role='replica'"))
        connection.execute(
            text(
                """
                INSERT INTO public.teacher_student_relationship_events(
                  source_region,source_partition_epoch_id,topic,partition_id,
                  offset_value,source_table,source_record_id,
                  source_record_id_type,source_record_id_numeric,
                  source_record_id_text,source_row_revision,
                  relationship_type,operation,old_teacher_id,
                  old_teacher_id_type,old_student_token,new_teacher_id,
                  new_teacher_id_type,new_student_token,old_valid_start_at,
                  old_valid_end_at,new_valid_start_at,new_valid_end_at,
                  old_is_valid_forever,new_is_valid_forever,effective_at,
                  effective_time_evidence_status,source_timestamp
                ) VALUES (
                  'dom','favorite-seed-epoch','favorite-seed-topic',0,1001,
                  'dom_teacher_favorite','7001','NUMERIC',7001,NULL,2,
                  'FAVORITE','DELETE','100','NUMERIC',:student_token,
                  NULL,NULL,NULL,:first_start,:first_end,NULL,NULL,false,NULL,
                  :first_end,'CONFIRMED',:first_end
                ),(
                  'dom','favorite-seed-epoch','favorite-seed-topic',0,1002,
                  'dom_teacher_favorite','7002','NUMERIC',7002,NULL,1,
                  'FAVORITE','INSERT',NULL,NULL,NULL,'100','NUMERIC',
                  :student_token,NULL,NULL,:second_start,NULL,NULL,true,
                  :second_start,'CONFIRMED',:second_start
                )
                """
            ),
            {
                "student_token": DOM_STUDENT,
                "first_start": course_times["9101"] - timedelta(days=1),
                "first_end": course_times["9101"] + timedelta(hours=23),
                "second_start": course_times["9101"] + timedelta(hours=25),
            },
        )
        connection.execute(
            text(
                """
                UPDATE public.teacher_student_relationship_current
                SET is_favorited=true,last_business_effective_at=
                      :second_start,
                    last_offset_value=1002,
                    last_source_row_revision=last_source_row_revision+1,
                    row_version=row_version+1,updated_at=clock_timestamp()
                WHERE source_region='dom' AND teacher_id='100'
                  AND student_token=:student_token
                """
            ),
            {
                "student_token": DOM_STUDENT,
                "second_start": course_times["9101"] + timedelta(hours=25),
            },
        )
    earlier_correction = _materialize(
        worker, course_id="9101", end_time=course_times["9101"]
    )
    later_correction = _materialize(
        worker, course_id="9102", end_time=course_times["9102"]
    )
    assert earlier_correction["status"] == "REQUEUED"
    assert later_correction["status"] == "REQUEUED"
    earlier_correction_claim = _claim(worker)
    assert earlier_correction_claim is not None
    earlier_correction_done = _complete(
        worker,
        earlier_correction_claim,
        "CONFIRMED_FALSE",
    )
    assert earlier_correction_done["attributions_reversed"] == 1
    assert earlier_correction_done["attributions_reselected"] == 0
    assert earlier_correction_done["score_entries_created"] == 1
    later_correction_claim = _claim(worker)
    assert later_correction_claim is not None
    later_correction_done = _complete(
        worker,
        later_correction_claim,
        "CONFIRMED_TRUE",
    )
    assert later_correction_done["attributions_awarded"] == 1
    assert later_correction_done["score_entries_created"] == 1
    with admin.connect() as connection:
        corrected_attribution = connection.execute(
            text(
                """
                SELECT source_appoint_id,status,award_generation,
                       award_projection_generation
                FROM public.course_favorite_attributions
                WHERE source_region='dom' AND teacher_id='100'
                  AND student_token=:student_token
                """
            ),
            {"student_token": DOM_STUDENT},
        ).mappings().one()
        corrected_entry_count = connection.execute(
            text(
                """
                SELECT count(*) FROM public.score_entries
                WHERE entry_type IN (
                  'FAVORITE_AWARD','FAVORITE_REVERSAL'
                )
                """
            )
        ).scalar_one()
    assert dict(corrected_attribution) == {
        "source_appoint_id": "9102",
        "status": "AWARDED",
        "award_generation": 3,
        "award_projection_generation": 3,
    }
    assert corrected_entry_count == 5
    assert _materialize(
        worker, course_id="9101", end_time=course_times["9101"]
    )["status"] == "UNCHANGED"
    with admin.connect() as connection:
        assert connection.execute(
            text(
                """
                SELECT count(*) FROM public.score_entries
                WHERE entry_type IN (
                  'FAVORITE_AWARD','FAVORITE_REVERSAL'
                )
                """
            )
        ).scalar_one() == 5

    future = _materialize(
        worker, course_id="9199", end_time=course_times["9199"]
    )
    assert future["status"] == "CREATED"
    assert _claim(worker) is None

    with admin.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE public.dts_pipeline_control
                SET mode='ROLLED_BACK',row_version=row_version+1,
                    changed_at=transaction_timestamp(),
                    changed_by='FAVORITE_RUNTIME_TEST'
                WHERE control_id='PRIMARY'
                """
            )
        )
    assert _claim(worker) is None
    with worker.begin() as connection:
        assert connection.execute(
            text("SELECT public.reap_expired_favorite_observations_v2(10)")
        ).scalar_one() == {
            "reaped": 0,
            "retry": 0,
            "dead": 0,
            "stale_requeued": 0,
        }
    with pytest.raises(DBAPIError):
        _materialize(worker, course_id="9102", end_time=course_times["9102"])


@pytest.mark.skipif(
    not _postgres_tools_available(),
    reason="local PostgreSQL binaries are required for favorite runtime",
)
def test_completion_conflict_holds_existing_award_without_new_score(
    favorite_runtime_postgres: tuple[Engine, Engine],
) -> None:
    admin, worker = favorite_runtime_postgres
    _seed_runtime(admin)
    with admin.connect() as connection:
        end_time = connection.execute(
            text(
                """
                SELECT completion_end_time FROM public.source_courses
                WHERE source_region='dom' AND source_appoint_id='9101'
                """
            )
        ).scalar_one()
    _materialize(worker, course_id="9101", end_time=end_time)
    first_claim = _claim(worker)
    assert first_claim is not None
    _complete(worker, first_claim, "CONFIRMED_TRUE")

    with admin.begin() as connection:
        connection.execute(text("SET LOCAL session_replication_role='replica'"))
        connection.execute(
            text(
                """
                UPDATE public.source_courses
                SET completion_conflict_status='PENDING',
                    evidence_status='SOURCE_CONFLICT',row_version=row_version+1
                WHERE source_region='dom' AND source_appoint_id='9101'
                """
            )
        )
    requeued = _materialize(worker, course_id="9101", end_time=end_time)
    assert requeued["status"] == "REQUEUED"
    conflict_claim = _claim(worker)
    assert conflict_claim is not None
    conflict_done = _complete(worker, conflict_claim, "WAITING_EVIDENCE")
    assert conflict_done["score_entries_created"] == 0
    assert conflict_done["attributions_awarded"] == 0
    assert conflict_done["attributions_reversed"] == 0

    with admin.connect() as connection:
        assert connection.execute(
            text(
                """
                SELECT status FROM public.course_favorite_attributions
                WHERE source_region='dom' AND teacher_id='100'
                  AND student_token=:student_token
                """
            ),
            {"student_token": DOM_STUDENT},
        ).scalar_one() == "AWARDED_PENDING_EVIDENCE"
        assert connection.execute(
            text(
                """
                SELECT count(*) FROM public.score_entries
                WHERE entry_type IN ('FAVORITE_AWARD','FAVORITE_REVERSAL')
                """
            )
        ).scalar_one() == 1


@pytest.mark.skipif(
    not _postgres_tools_available(),
    reason="local PostgreSQL binaries are required for favorite runtime",
)
def test_concurrent_claim_crash_reclaim_and_replay_are_idempotent(
    favorite_runtime_postgres: tuple[Engine, Engine],
) -> None:
    admin, worker = favorite_runtime_postgres
    _seed_runtime(admin)
    with admin.connect() as connection:
        course_times = dict(
            connection.execute(
                text(
                    """
                    SELECT source_appoint_id,completion_end_time
                    FROM public.source_courses
                    WHERE source_appoint_id IN ('9101','9102')
                    """
                )
            ).all()
        )
    for course_id in ("9101", "9102"):
        _materialize(
            worker,
            course_id=course_id,
            end_time=course_times[course_id],
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(
            pool.map(
                lambda worker_id: _claim(worker, worker_id),
                ("favorite-worker-a", "favorite-worker-b"),
            )
        )
    assert all(claim is not None for claim in claims)
    concrete_claims = [claim for claim in claims if claim is not None]
    assert len(
        {
            (claim["source_appoint_id"], claim["observation_revision"])
            for claim in concrete_claims
        }
    ) == 2
    crashed = next(
        claim
        for claim in concrete_claims
        if claim["source_appoint_id"] == "9101"
    )
    surviving = next(
        claim
        for claim in concrete_claims
        if claim["source_appoint_id"] == "9102"
    )

    with admin.begin() as connection:
        connection.execute(text("SET LOCAL session_replication_role='replica'"))
        connection.execute(
            text(
                """
                UPDATE public.course_favorite_observations
                SET lease_acquired_at=clock_timestamp()-interval '2 minutes',
                    lease_expires_at=clock_timestamp()-interval '1 minute',
                    row_version=row_version+1,updated_at=clock_timestamp()
                WHERE source_region=:source_region
                  AND source_appoint_id=:source_appoint_id
                  AND observation_revision=:observation_revision
                """
            ),
            crashed,
        )
    with worker.begin() as connection:
        reaped = connection.execute(
            text("SELECT public.reap_expired_favorite_observations_v2(10)")
        ).scalar_one()
    assert reaped == {
        "reaped": 1,
        "retry": 1,
        "dead": 0,
        "stale_requeued": 0,
    }
    with admin.begin() as connection:
        connection.execute(text("SET LOCAL session_replication_role='replica'"))
        connection.execute(
            text(
                """
                UPDATE public.course_favorite_observations
                SET next_attempt_at=clock_timestamp()-interval '1 second',
                    row_version=row_version+1,updated_at=clock_timestamp()
                WHERE source_region=:source_region
                  AND source_appoint_id=:source_appoint_id
                  AND observation_revision=:observation_revision
                  AND status='RETRY'
                """
            ),
            crashed,
        )
    reclaimed = _claim(worker, "favorite-worker-c")
    assert reclaimed is not None
    assert reclaimed["source_appoint_id"] == crashed["source_appoint_id"]
    assert reclaimed["lease_token"] != crashed["lease_token"]
    with pytest.raises(DBAPIError):
        _complete(worker, crashed, "CONFIRMED_TRUE")

    first_done = _complete(worker, reclaimed, "CONFIRMED_TRUE")
    second_done = _complete(worker, surviving, "CONFIRMED_TRUE")
    assert first_done["score_entries_created"] == 1
    assert second_done["score_entries_created"] == 0
    with admin.connect() as connection:
        score_count_before_replay = connection.execute(
            text(
                """
                SELECT count(*) FROM public.score_entries
                WHERE entry_type IN (
                  'FAVORITE_AWARD','FAVORITE_REVERSAL'
                )
                """
            )
        ).scalar_one()
    for course_id in ("9101", "9102"):
        assert _materialize(
            worker,
            course_id=course_id,
            end_time=course_times[course_id],
        )["status"] == "UNCHANGED"
    with admin.connect() as connection:
        assert connection.execute(
            text(
                """
                SELECT count(*) FROM public.score_entries
                WHERE entry_type IN (
                  'FAVORITE_AWARD','FAVORITE_REVERSAL'
                )
                """
            )
        ).scalar_one() == score_count_before_replay

    with worker.begin() as connection:
        with pytest.raises(DBAPIError):
            connection.execute(
                text(
                    "DELETE FROM public.course_favorite_observations "
                    "WHERE false"
                )
            )
