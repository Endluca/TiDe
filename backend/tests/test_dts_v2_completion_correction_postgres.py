from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from test_dts_v2_ops_case_postgres import (
    _postgres_tools_available,
    ops_case_postgres,
)


def _seed_pending_course(
    admin: Engine,
    *,
    course_id: str,
    fingerprint: str,
) -> None:
    with admin.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO public.source_courses(
                  source_region,source_appoint_id,
                  completion_conflict_status,conflict_fingerprint,
                  evidence_status,row_version,updated_at
                ) VALUES (
                  'dom',:course_id,'PENDING',:fingerprint,
                  'SOURCE_MISSING',1,transaction_timestamp()
                )
                """
            ),
            {"course_id": course_id, "fingerprint": fingerprint},
        )


def _publish_conflict(
    admin: Engine,
    *,
    course_id: str,
    fingerprint: str,
    revision: int,
) -> tuple[str, str]:
    key = {"source_region": "dom", "source_appoint_id": course_id}
    state = {
        "completion_conflict": {
            "status": "PENDING",
            "case_id": f"course-completion-correction:dom:{course_id}",
            "fingerprint": fingerprint,
            "completion_teacher_id": None,
            "completion_teacher_id_type": None,
            "completion_participation_seq": None,
        }
    }
    key_json = json.dumps(key, separators=(",", ":"))
    state_json = json.dumps(state, separators=(",", ":"))
    with admin.begin() as connection:
        aggregate_id = connection.execute(
            text(
                """
                SELECT 'v2:COMPLETION_CONFLICT:' ||
                  public.dts_canonical_json_sha256_v1(CAST(:key AS jsonb))
                """
            ),
            {"key": key_json},
        ).scalar_one()
        if revision == 1:
            connection.execute(
                text(
                    """
                    INSERT INTO public.domain_aggregate_revisions(
                      aggregate_type,aggregate_id,canonical_key,
                      canonical_key_sha256,revision,last_source_row_revision,
                      last_source_position,aggregate_state,
                      aggregate_state_sha256,updated_at
                    ) VALUES (
                      'COMPLETION_CONFLICT',:aggregate_id,CAST(:key AS jsonb),
                      public.dts_canonical_json_sha256_v1(CAST(:key AS jsonb)),
                      1,NULL,NULL,CAST(:state AS jsonb),
                      public.dts_canonical_json_sha256_v1(CAST(:state AS jsonb)),
                      transaction_timestamp()
                    )
                    """
                ),
                {
                    "aggregate_id": aggregate_id,
                    "key": key_json,
                    "state": state_json,
                },
            )
        else:
            updated = connection.execute(
                text(
                    """
                    UPDATE public.domain_aggregate_revisions
                    SET revision=:revision,aggregate_state=CAST(:state AS jsonb),
                        aggregate_state_sha256=
                          public.dts_canonical_json_sha256_v1(
                            CAST(:state AS jsonb)
                          ),updated_at=transaction_timestamp()
                    WHERE aggregate_type='COMPLETION_CONFLICT'
                      AND aggregate_id=:aggregate_id
                      AND revision=:prior_revision
                    """
                ),
                {
                    "aggregate_id": aggregate_id,
                    "revision": revision,
                    "prior_revision": revision - 1,
                    "state": state_json,
                },
            )
            assert updated.rowcount == 1

        event_id = (
            "source_wide.changed.v2:COMPLETION_CONFLICT:"
            f"{aggregate_id}:{revision}"
        )
        outbox_id = "completion-conflict-outbox:" + hashlib.sha256(
            event_id.encode("utf-8")
        ).hexdigest()
        payload = json.dumps(
            {"aggregate_revision": revision}, separators=(",", ":")
        )
        connection.execute(
            text(
                """
                INSERT INTO public.outbox_events(
                  outbox_id,event_id,aggregate_type,aggregate_id,event_type,
                  payload,payload_sha256,status,available_at,attempt_count,
                  recovery_count,recovered_at,row_version,last_error,
                  settled_by_run_id,created_at,published_at
                ) VALUES (
                  :outbox_id,:event_id,'COMPLETION_CONFLICT',:aggregate_id,
                  'source_wide.changed.v2',CAST(:payload AS jsonb),
                  public.dts_canonical_json_sha256_v1(CAST(:payload AS jsonb)),
                  'PENDING',transaction_timestamp(),0,0,NULL,1,NULL,NULL,
                  transaction_timestamp(),NULL
                )
                """
            ),
            {
                "outbox_id": outbox_id,
                "event_id": event_id,
                "aggregate_id": aggregate_id,
                "payload": payload,
            },
        )
    return aggregate_id, event_id


def _reconcile(
    admin: Engine,
    *,
    course_id: str,
    revision: int,
    event_id: str,
) -> dict[str, object]:
    with admin.begin() as connection:
        connection.execute(text("SET LOCAL ROLE tit_dts_domain_projector_runtime"))
        return connection.execute(
            text(
                """
                SELECT public.reconcile_completion_conflict_case_v2(
                  'dom',:course_id,:revision,:event_id
                )
                """
            ),
            {
                "course_id": course_id,
                "revision": revision,
                "event_id": event_id,
            },
        ).scalar_one()


@pytest.mark.skipif(
    not _postgres_tools_available(),
    reason="local PostgreSQL binaries are required for completion correction",
)
def test_completion_conflict_reconcile_is_mode_safe_idempotent_and_concurrent(
    ops_case_postgres: tuple[Engine, Engine, Engine],
) -> None:
    admin, _, _ = ops_case_postgres
    primary_course = "completion-reconcile-primary"
    fingerprint_1 = "1" * 64
    case_id = f"course-completion-correction:dom:{primary_course}"
    _seed_pending_course(
        admin, course_id=primary_course, fingerprint=fingerprint_1
    )
    _, event_1 = _publish_conflict(
        admin,
        course_id=primary_course,
        fingerprint=fingerprint_1,
        revision=1,
    )

    assert _reconcile(
        admin, course_id=primary_course, revision=1, event_id=event_1
    ) == {"outcome": "CREATED", "case_id": case_id}

    def replay(_: int) -> dict[str, object]:
        return _reconcile(
            admin, course_id=primary_course, revision=1, event_id=event_1
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert list(executor.map(replay, range(2))) == [
            {"outcome": "UNCHANGED", "case_id": case_id},
            {"outcome": "UNCHANGED", "case_id": case_id},
        ]

    growth = create_engine(
        str(admin.url).replace("postgres@", "tit_growth_app@")
    )
    try:
        with growth.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE public.ops_cases
                    SET status='IN_REVIEW',row_version=row_version+1,
                        updated_at=clock_timestamp()
                    WHERE case_id=:case_id
                    """
                ),
                {"case_id": case_id},
            )
        with pytest.raises(DBAPIError):
            with growth.begin() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO public.ops_cases(
                          case_id,case_type,priority,status,
                          external_action_status,created_at,payload,updated_at,
                          source_ref,source_region,source_appoint_id,
                          case_revision,row_version,evidence_fingerprint,
                          recovery_evidence_count
                        ) VALUES (
                          'course-completion-correction:dom:forged',
                          'COURSE_COMPLETION_CORRECTION','P1','OPEN',
                          'NOT_REQUESTED',transaction_timestamp(),'{}'::jsonb,
                          transaction_timestamp(),
                          'course-completion-correction:dom:forged','dom',
                          'forged',1,1,repeat('f',64),0
                        )
                        """
                    )
                )

        fingerprint_2 = "2" * 64
        with admin.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE public.source_courses
                    SET conflict_fingerprint=:fingerprint,
                        row_version=row_version+1,
                        updated_at=clock_timestamp()
                    WHERE source_region='dom'
                      AND source_appoint_id=:course_id
                    """
                ),
                {
                    "course_id": primary_course,
                    "fingerprint": fingerprint_2,
                },
            )
        _, event_2 = _publish_conflict(
            admin,
            course_id=primary_course,
            fingerprint=fingerprint_2,
            revision=2,
        )
        assert _reconcile(
            admin, course_id=primary_course, revision=2, event_id=event_2
        ) == {"outcome": "UPDATED", "case_id": case_id}
        with admin.connect() as connection:
            assert connection.execute(
                text(
                    """
                    SELECT status,case_revision,evidence_fingerprint,
                           completion_conflict_case_id
                    FROM public.ops_cases AS cases
                    JOIN public.source_courses AS courses
                      ON courses.completion_conflict_case_id=cases.case_id
                    WHERE cases.case_id=:case_id
                    """
                ),
                {"case_id": case_id},
            ).one() == ("IN_REVIEW", 2, fingerprint_2, case_id)

        with admin.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE public.dts_pipeline_control
                    SET mode='ROLLED_BACK',row_version=row_version+1,
                        changed_at=transaction_timestamp(),
                        changed_by='COMPLETION_CORRECTION_TEST'
                    WHERE control_id='PRIMARY'
                    """
                )
            )
            connection.execute(
                text(
                    """
                    UPDATE public.source_courses
                    SET conflict_fingerprint=repeat('3',64),
                        row_version=row_version+1,
                        updated_at=clock_timestamp()
                    WHERE source_region='dom'
                      AND source_appoint_id=:course_id
                    """
                ),
                {"course_id": primary_course},
            )
        _, event_3 = _publish_conflict(
            admin,
            course_id=primary_course,
            fingerprint="3" * 64,
            revision=3,
        )
        assert _reconcile(
            admin, course_id=primary_course, revision=3, event_id=event_3
        ) == {"outcome": "SHADOW_ONLY", "case_id": case_id}

        shadow_course = "completion-reconcile-rollback-new"
        _seed_pending_course(
            admin, course_id=shadow_course, fingerprint="4" * 64
        )
        _, shadow_event = _publish_conflict(
            admin,
            course_id=shadow_course,
            fingerprint="4" * 64,
            revision=1,
        )
        assert _reconcile(
            admin, course_id=shadow_course, revision=1, event_id=shadow_event
        ) == {"outcome": "SHADOW_ONLY", "case_id": None}

        request = {
            "protocol_version": "completion-correction-command-v1",
            "decision_id": "completion-decision-maintenance",
            "case_id": case_id,
            "decision": "KEEP_FROZEN_COMPLETION",
            "actor_id": "ops-user",
            "reason": "mode gate",
            "expected_case_revision": 2,
            "expected_conflict_fingerprint": "3" * 64,
            "expected_source_revision": 1,
            "expected_source_position": {
                "v": 1,
                "source_timestamp": "2026-08-22T00:00:00.000000Z",
                "record_id_type": "none",
                "record_id": None,
                "source_partition_epoch_id": "epoch-test",
                "topic": "topic-test",
                "partition_id": 0,
                "offset_value": 1,
            },
            "target_participation_seq": None,
            "completion_snapshot": None,
        }
        request_json = json.dumps(request, separators=(",", ":"))
        with admin.connect() as connection:
            request_hash = connection.execute(
                text(
                    """
                    SELECT public.dts_canonical_json_sha256_v1(
                      CAST(:request AS jsonb)
                    )
                    """
                ),
                {"request": request_json},
            ).scalar_one()
        with pytest.raises(
            DBAPIError, match="CORRECTION_PROJECTION_MAINTENANCE"
        ):
            with growth.begin() as connection:
                connection.execute(
                    text(
                        """
                        SELECT public.apply_completion_correction_decision_v2(
                          CAST(:request AS jsonb),:request_hash
                        )
                        """
                    ),
                    {"request": request_json, "request_hash": request_hash},
                )

        with admin.connect() as connection:
            assert connection.execute(
                text(
                    """
                    SELECT cases.status,cases.case_revision,
                           cases.evidence_fingerprint,
                           courses.completion_conflict_case_id,
                           (SELECT count(*) FROM public.ops_cases
                            WHERE source_region='dom'
                              AND source_appoint_id=:shadow_course)
                    FROM public.ops_cases AS cases
                    JOIN public.source_courses AS courses
                      ON courses.completion_conflict_case_id=cases.case_id
                    WHERE cases.case_id=:case_id
                    """
                ),
                {"case_id": case_id, "shadow_course": shadow_course},
            ).one() == ("IN_REVIEW", 2, fingerprint_2, case_id, 0)
            assert connection.execute(
                text(
                    """
                    SELECT count(*) FROM public.ops_decisions
                    WHERE decision_id='completion-decision-maintenance'
                    """
                )
            ).scalar_one() == 0
    finally:
        growth.dispose()
