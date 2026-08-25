from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from app.dts_v2_runtime_composition import (
    DOMAIN_COMPONENT,
    FAVORITE_COMPONENT,
    OUTBOX_COMPONENT,
    validate_runtime_startup,
)
from test_dts_v2_ops_case_postgres import (
    _postgres_tools_available,
    _run_alembic,
    ops_case_postgres,
)


def _canonical(value: dict[str, object]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _seed_completed_peak_course(connection) -> tuple[dict[str, object], str]:
    source_region = "dom"
    course_id = "course-score-1"
    teacher_id = "9200"
    student_token = "dom:v1:" + "1" * 64
    source_position = {"topic": "course-score", "partition": 0, "offset": 1}
    connection.execute(text("SET LOCAL session_replication_role='replica'"))
    connection.execute(
        text(
            """
            INSERT INTO public.teachers(
              teacher_id,camp_enrollment_id,name,country,timezone,camp_day,
              online_status,graduation_state,gold_qualified,total_score,
              graduation_threshold,data_mode,source_snapshot_label,payload,
              created_at,updated_at
            ) VALUES (
              :teacher_id,'camp-course-score-1','Course Score Teacher','US',
              'UTC',1,'NEW','IN_CAMP',false,0,100,'SOURCE','course-score-test',
              jsonb_build_object('graduation_state','IN_CAMP'),
              transaction_timestamp(),transaction_timestamp()
            )
            """
        ),
        {"teacher_id": teacher_id},
    )
    connection.execute(
        text(
            """
            INSERT INTO public.source_courses(
              source_region,source_appoint_id,student_token,source_status,
              current_teacher_id,current_teacher_id_type,
              current_participation_seq,is_peak,
              completion_participation_seq,completion_teacher_id,
              completion_teacher_id_type,completion_frozen_at,
              completion_end_time,completion_student_token,completion_is_peak,
              completion_lesson_local_date,completion_lesson_local_time,
              completion_source_position,completion_source_revision,
              initial_completion_snapshot,completion_conflict_status,
              source_is_deleted,evidence_status,appoint_evidence_status,
              teacher_region_evidence_status,last_applied_event_position,
              last_applied_source_revision,row_version,updated_at
            ) VALUES (
              :source_region,:course_id,:student_token,'end',
              CAST(:teacher_id AS varchar),'NUMERIC',1,true,1,
              CAST(:teacher_id AS varchar),'NUMERIC',
              '2026-08-22 10:00:00+00','2026-08-22 10:00:00+00',
              :student_token,true,'2026-08-22','18:30:00',
              CAST(:source_position AS jsonb),1,
              jsonb_build_object(
                'status','end','teacher_id',CAST(:teacher_id AS varchar),
                'teacher_id_type','NUMERIC','participation_seq',1
              ),'NONE',false,'CONFIRMED','CONFIRMED','CONFIRMED',
              CAST(:source_position AS jsonb),1,1,transaction_timestamp()
            )
            """
        ),
        {
            "source_region": source_region,
            "course_id": course_id,
            "teacher_id": teacher_id,
            "student_token": student_token,
            "source_position": _canonical(source_position),
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO public.source_course_participations(
              source_region,source_appoint_id,participation_seq,teacher_id,
              teacher_id_type,participation_status,participation_role,
              is_current,assigned_at,assigned_at_evidence_status,ended_at,
              teacher_expected_source_region,teacher_region_evidence_status,
              teacher_profile_source_row_revision,
              teacher_profile_source_payload_hash,source_deleted,
              assignment_source_partition_epoch_id,assignment_event_topic,
              assignment_event_partition,assignment_event_offset,
              assignment_source_row_revision,assignment_event_phase,row_version
            ) VALUES (
              :source_region,:course_id,1,:teacher_id,'NUMERIC','end',
              'COMPLETION',true,'2026-08-22 09:00:00+00','CONFIRMED',
              '2026-08-22 10:00:00+00',:source_region,'CONFIRMED',1,
              repeat('2',64),false,'course-score-epoch','course-score-topic',
              0,1,1,'AFTER',1
            )
            """
        ),
        {
            "source_region": source_region,
            "course_id": course_id,
            "teacher_id": teacher_id,
        },
    )
    key: dict[str, object] = {
        "source_region": source_region,
        "source_appoint_id": course_id,
    }
    state = {"course": "score-test"}
    connection.execute(
        text(
            """
            INSERT INTO public.domain_aggregate_revisions(
              aggregate_type,aggregate_id,canonical_key,
              canonical_key_sha256,revision,last_source_row_revision,
              last_source_position,aggregate_state,
              aggregate_state_sha256,updated_at
            ) VALUES (
              'COURSE','v2:COURSE:' ||
                public.dts_canonical_json_sha256_v1(CAST(:key AS jsonb)),
              CAST(:key AS jsonb),
              public.dts_canonical_json_sha256_v1(CAST(:key AS jsonb)),
              1,NULL,NULL,CAST(:state AS jsonb),
              public.dts_canonical_json_sha256_v1(CAST(:state AS jsonb)),
              transaction_timestamp()
            )
            """
        ),
        {"key": _canonical(key), "state": _canonical(state)},
    )
    aggregate_hash = connection.execute(
        text(
            """
            SELECT aggregate_state_sha256
            FROM public.domain_aggregate_revisions
            WHERE aggregate_type='COURSE' AND canonical_key=CAST(:key AS jsonb)
            """
        ),
        {"key": _canonical(key)},
    ).scalar_one()
    request: dict[str, object] = {
        "protocol_version": "course-materialization-request-v1",
        "source_region": source_region,
        "source_appoint_id": course_id,
        "aggregate_state_sha256": aggregate_hash,
        "course_row_version": 1,
        "course_fact_row_version": None,
        "participation_versions": [
            {
                "participation_seq": 1,
                "participation_row_version": 1,
                "participation_fact_row_version": None,
            }
        ],
        "affected_teacher_ids": [teacher_id],
    }
    return request, teacher_id


@pytest.mark.skipif(
    not _postgres_tools_available(),
    reason="local PostgreSQL binaries are required for rev92 contracts",
)
def test_runtime_course_command_health_acl_and_atomic_failure(
    ops_case_postgres,
) -> None:
    admin, outbox, _recovery = ops_case_postgres
    backend_dir = Path(__file__).resolve().parents[1]
    database_url = admin.url.render_as_string(hide_password=False)
    domain = create_engine(
        admin.url.set(username="tit_growth_app")
    )
    app = create_engine(admin.url.set(username="tit_growth_app"))
    try:
        # The shared ops-case fixture intentionally remains pinned at rev100
        # so it can exercise the original materialization contracts.  Mirror
        # the current runtime read ACLs here because startup composition reads
        # the pipeline singleton and Domain queue evidence before constructing
        # any worker.
        with admin.begin() as connection:
            connection.execute(
                text(
                    "GRANT SELECT ON TABLE public.dts_pipeline_control,"
                    "public.dts_dirty_keys,public.dts_dirty_key_inputs "
                    "TO tit_growth_app"
                )
            )
        with admin.connect() as connection:
            assert connection.execute(
                text(
                    """
                    SELECT
                      has_function_privilege(
                        'tit_growth_app',
                        'public.materialize_course_source_wide_v2('
                        'jsonb,bigint,bigint,text,text)','EXECUTE'
                      ),
                      has_table_privilege(
                        'tit_growth_app',
                        'public.lesson_source_wide','INSERT'
                      ),
                      has_table_privilege(
                        'tit_growth_app',
                        'public.lesson_score_component_settlements','UPDATE'
                      ),
                      has_table_privilege(
                        'tit_growth_app',
                        'public.score_entries','INSERT'
                      ),
                      has_table_privilege(
                        'tit_growth_app',
                        'public.dts_pipeline_control','SELECT'
                      )
                    """
                )
            ).one() == (True, False, False, True, True)

        with admin.begin() as connection:
            key = {
                "source_region": "dom",
                "source_appoint_id": "course-health-1",
            }
            connection.execute(
                text(
                    """
                    INSERT INTO public.source_courses(
                      source_region,source_appoint_id,source_is_deleted,
                      evidence_status,appoint_evidence_status,
                      teacher_region_evidence_status,row_version,updated_at
                    ) VALUES (
                      'dom','course-health-1',false,'SOURCE_MISSING',
                      'SOURCE_MISSING','SOURCE_MISSING',1,
                      transaction_timestamp()
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO public.domain_aggregate_revisions(
                      aggregate_type,aggregate_id,canonical_key,
                      canonical_key_sha256,revision,last_source_row_revision,
                      last_source_position,aggregate_state,
                      aggregate_state_sha256,updated_at
                    ) VALUES (
                      'COURSE',
                      'v2:COURSE:' || public.dts_canonical_json_sha256_v1(
                        CAST(:key AS jsonb)
                      ),CAST(:key AS jsonb),
                      public.dts_canonical_json_sha256_v1(CAST(:key AS jsonb)),
                      1,NULL,NULL,CAST(:state AS jsonb),
                      public.dts_canonical_json_sha256_v1(
                        CAST(:state AS jsonb)
                      ),
                      transaction_timestamp()
                    )
                    """
                ),
                {
                    "key": _canonical(key),
                    "state": _canonical({"course": "health-test"}),
                },
            )
        # Bind the request to the actual aggregate hash created above.
        with admin.connect() as connection:
            aggregate_hash = connection.execute(
                text(
                    """
                    SELECT aggregate_state_sha256
                    FROM public.domain_aggregate_revisions
                    WHERE aggregate_type='COURSE'
                      AND canonical_key->>'source_appoint_id'='course-health-1'
                    """
                )
            ).scalar_one()

        request = {
            "protocol_version": "course-materialization-request-v1",
            "source_region": "dom",
            "source_appoint_id": "course-health-1",
            "aggregate_state_sha256": aggregate_hash,
            "course_row_version": 1,
            "course_fact_row_version": None,
            "participation_versions": [],
            "affected_teacher_ids": [],
        }
        canonical = _canonical(request)
        expected_hash = hashlib.sha256(canonical.encode()).hexdigest()
        with outbox.begin() as connection:
            result = connection.execute(
                text(
                    """
                    SELECT public.materialize_course_source_wide_v2(
                      CAST(:request AS jsonb),1,1,'course-health-event-1',
                      :request_hash
                    )
                    """
                ),
                {"request": canonical, "request_hash": expected_hash},
            ).scalar_one()
            assert result["request_sha256"] == expected_hash
            assert result["projection_generation"] == 1
            assert result["counts"]["compatibility_changes"] == 1

        with admin.begin() as connection:
            score_request, score_teacher_id = _seed_completed_peak_course(
                connection
            )
        score_canonical = _canonical(score_request)
        score_hash = hashlib.sha256(score_canonical.encode()).hexdigest()
        with outbox.begin() as connection:
            score_result = connection.execute(
                text(
                    """
                    SELECT public.materialize_course_source_wide_v2(
                      CAST(:request AS jsonb),1,1,'course-score-event-1',
                      :request_hash
                    )
                    """
                ),
                {"request": score_canonical, "request_hash": score_hash},
            ).scalar_one()
            assert score_result["counts"]["component_awards"] == 1
            assert score_result["counts"]["score_entries"] == 1
        with outbox.begin() as connection:
            replay = connection.execute(
                text(
                    """
                    SELECT public.materialize_course_source_wide_v2(
                      CAST(:request AS jsonb),1,1,'course-score-event-replay',
                      :request_hash
                    )
                    """
                ),
                {"request": score_canonical, "request_hash": score_hash},
            ).scalar_one()
            assert replay["counts"]["component_awards"] == 0
            assert replay["counts"]["score_entries"] == 0
            assert replay["counts"]["compatibility_changes"] == 0
        forged_request = dict(score_request)
        forged_request["affected_teacher_ids"] = ["attacker"]
        forged_canonical = _canonical(forged_request)
        with pytest.raises(DBAPIError):
            with outbox.begin() as connection:
                connection.execute(
                    text(
                        """
                        SELECT public.materialize_course_source_wide_v2(
                          CAST(:request AS jsonb),1,1,
                          'course-score-event-forged',:request_hash
                        )
                        """
                    ),
                    {
                        "request": forged_canonical,
                        "request_hash": hashlib.sha256(
                            forged_canonical.encode()
                        ).hexdigest(),
                    },
                ).scalar_one()
        with admin.connect() as connection:
            assert connection.execute(
                text(
                    """
                    SELECT settlement.status,settlement.component_code,
                           entry.teacher_id,entry.delta_score,
                           entry.projection_generation
                    FROM public.lesson_score_component_settlements settlement
                    JOIN public.score_entries entry ON
                      entry.score_entry_id=
                        settlement.current_award_score_entry_id
                    WHERE settlement.source_region='dom'
                      AND settlement.source_appoint_id='course-score-1'
                    """
                )
            ).one() == (
                "AWARDED",
                "PEAK_COMPLETED",
                score_teacher_id,
                2.0,
                1,
            )
            assert connection.execute(
                text(
                    """
                    SELECT count(*) FROM public.score_entries
                    WHERE source_region='dom'
                      AND source_appoint_id='course-score-1'
                    """
                )
            ).scalar_one() == 1

        with admin.connect() as connection:
            before = connection.execute(
                text(
                    """
                    SELECT source_region,"课程id","老师id","课程状态"
                    FROM public.lesson_source_wide
                    WHERE source_region='dom' AND "课程id"='course-health-1'
                    """
                )
            ).one()

        for parameters in (
            {"request_hash": "f" * 64, "generation": 1},
            {"request_hash": expected_hash, "generation": 2},
        ):
            with pytest.raises(DBAPIError):
                with outbox.begin() as connection:
                    connection.execute(
                        text(
                            """
                            SELECT public.materialize_course_source_wide_v2(
                              CAST(:request AS jsonb),1,:generation,
                              'course-health-event-bad',:request_hash
                            )
                            """
                        ),
                        {"request": canonical, **parameters},
                    ).scalar_one()
        with app.begin() as connection:
            shared_role_replay = connection.execute(
                text(
                    """
                    SELECT public.materialize_course_source_wide_v2(
                      CAST(:request AS jsonb),1,1,'shared-role',:request_hash
                    )
                    """
                ),
                {"request": canonical, "request_hash": expected_hash},
            ).scalar_one()
            assert shared_role_replay["counts"]["compatibility_changes"] == 0
        with admin.connect() as connection:
            assert connection.execute(
                text(
                    """
                    SELECT source_region,"课程id","老师id","课程状态"
                    FROM public.lesson_source_wide
                    WHERE source_region='dom' AND "课程id"='course-health-1'
                    """
                )
            ).one() == before

        with domain.begin() as connection:
            validate_runtime_startup(
                connection,
                component=DOMAIN_COMPONENT,
                expected_database="postgres",
            )
            domain_health = connection.execute(
                text("SELECT public.dts_v2_domain_runtime_health_v1(900)")
            ).scalar_one()
        with outbox.begin() as connection:
            validate_runtime_startup(
                connection,
                component=OUTBOX_COMPONENT,
                expected_database="postgres",
            )
            validate_runtime_startup(
                connection,
                component=FAVORITE_COMPONENT,
                expected_database="postgres",
            )
            outbox_health = connection.execute(
                text("SELECT public.dts_v2_outbox_runtime_health_v1(900)")
            ).scalar_one()
            favorite_health = connection.execute(
                text("SELECT public.dts_v2_favorite_runtime_health_v1(900)")
            ).scalar_one()
        assert domain_health["protocol_version"] == (
            "dts-v2-domain-runtime-health-v1"
        )
        assert outbox_health["protocol_version"] == (
            "dts-v2-outbox-runtime-health-v1"
        )
        assert favorite_health["protocol_version"] == (
            "dts-v2-favorite-runtime-health-v1"
        )
        assert set(domain_health) == set(outbox_health) == set(favorite_health)
        for engine, statement in (
            (app, "SELECT public.dts_v2_outbox_runtime_health_v1(900)"),
            (outbox, "SELECT public.dts_v2_domain_runtime_health_v1(900)"),
            (domain, "SELECT public.dts_v2_favorite_runtime_health_v1(900)"),
        ):
            with engine.begin() as connection:
                assert connection.execute(text(statement)).scalar_one()["mode"] == (
                    "V2_PRIMARY"
                )

        with admin.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE public.dts_pipeline_control SET
                      mode='ROLLED_BACK',row_version=row_version+1,
                      changed_at=transaction_timestamp(),
                      changed_by='COURSE_HEALTH_TEST'
                    WHERE control_id='PRIMARY'
                    """
                )
            )
        with pytest.raises(DBAPIError):
            with outbox.begin() as connection:
                connection.execute(
                    text(
                        """
                        SELECT public.materialize_course_source_wide_v2(
                          CAST(:request AS jsonb),1,1,'rolled-back',:request_hash
                        )
                        """
                    ),
                    {"request": canonical, "request_hash": expected_hash},
                ).scalar_one()
        with admin.connect() as connection:
            assert connection.execute(
                text("SELECT version_num FROM public.alembic_version")
            ).scalar_one() == "20260823_100_scope_snapshot_diff"
    finally:
        app.dispose()
        domain.dispose()
