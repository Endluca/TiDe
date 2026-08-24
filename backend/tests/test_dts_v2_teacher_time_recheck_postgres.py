from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
import json

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.dts_v2_score_projection_store import PostgresDtsV2ScoreProjectionStore
from app.dts_v2_teacher_materializer import PostgresDtsV2TeacherMaterializer
from app.dts_v2_teacher_outbox_processor import (
    build_teacher_materialization_plan_v2,
)
from app.dts_v2_teacher_time_recheck import (
    PostgresDtsV2TeacherTimeRecheckStore,
    build_teacher_time_recheck_worker,
)
from test_dts_v2_ops_case_postgres import (
    _postgres_tools_available,
    ops_case_postgres,
)
from test_dts_v2_teacher_materializer_postgres import (
    _put_regional_aggregate,
    _seed_score_policy,
)
from test_dts_v2_teacher_outbox_processor import _states


@pytest.mark.skipif(
    not _postgres_tools_available(),
    reason="local PostgreSQL binaries are required for rev97 contracts",
)
def test_teacher_time_recheck_daily_transition_acl_and_standby(
    ops_case_postgres,
) -> None:
    admin, outbox, _recovery = ops_case_postgres
    teacher_id = "7"
    with admin.begin() as connection:
        business_date = connection.execute(
            text(
                "SELECT (transaction_timestamp() AT TIME ZONE "
                "'Asia/Shanghai')::date"
            )
        ).scalar_one()
        _seed_score_policy(connection)
        states = deepcopy(_states())
        onboard_date = business_date - timedelta(days=30)
        states["dom"]["profile"]["values"]["status_on_time"] = (
            onboard_date.isoformat()
        )
        for state in states.values():
            for scope in state["scope_evidence"]:
                if scope["scope_kind"] == "HISTORY":
                    scope["history_through"] = business_date.isoformat()
        hashes = {
            region: _put_regional_aggregate(
                connection,
                teacher_id=teacher_id,
                source_region=region,
                revision=1,
                state=state,
            )
            for region, state in states.items()
        }

    initial_plan = build_teacher_materialization_plan_v2(
        teacher_id=teacher_id,
        regional_states=states,
        regional_revisions={"dom": 1, "ovs": 1},
        regional_state_sha256=hashes,
        business_date_beijing=business_date - timedelta(days=1),
        legacy_first_dates={},
    )
    assert initial_plan.projection.values["job_days"] == 29
    assert initial_plan.projection.values["online_status"] == "NEW"
    with outbox.begin() as connection:
        PostgresDtsV2TeacherMaterializer(
            score_refresher=PostgresDtsV2ScoreProjectionStore(
                require_guarded_generation=True
            )
        ).apply_teacher_plan(
            connection,
            initial_plan,
            triggering_event_id="teacher-time-recheck-initial",
        )

    with admin.connect() as connection:
        assert connection.execute(
            text(
                """
                SELECT wide.job_days,teacher.online_status,teacher.camp_day,
                       dirty.status,dirty.key_part_2
                FROM public.teacher_source_wide wide
                JOIN public.teachers teacher
                  ON teacher.teacher_id=wide.tchr_id
                JOIN public.dts_dirty_keys dirty
                  ON dirty.source_region='dom'
                 AND dirty.key_type='TEACHER_TIME_RECHECK'
                 AND dirty.key_part_1=wide.tchr_id
                WHERE wide.tchr_id=:teacher_id
                """
            ),
            {"teacher_id": teacher_id},
        ).one() == (
            29,
            "NEW",
            29,
            "PENDING",
            business_date.isoformat(),
        )

    worker = build_teacher_time_recheck_worker(
        outbox,
        worker_id="teacher-time-recheck-pg",
        lease_seconds=120,
    )
    result = worker.run_once(max_claims=5)
    assert result["active"] is True
    assert result["claimed"] == 1
    assert result["completed"] == 1
    assert result["retries"] == 0
    assert result["dead"] == 0

    with admin.connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT wide.job_days,wide.job_month,teacher.online_status,
                       teacher.camp_day,dirty.status,
                       result.business_date_beijing,
                       result.dom_aggregate_revision,
                       result.ovs_aggregate_revision,
                       result.claimed_work_revision,
                       result.time_values->>'online_status',
                       result.plan_sha256=
                         public.dts_canonical_json_sha256_v1(
                           jsonb_build_object(
                             'protocol','teacher-time-recheck-plan-v1',
                             'payload',jsonb_build_object(
                               'v',1,'teacher_id',result.teacher_id,
                               'teacher_id_type',result.teacher_id_type,
                               'business_date_beijing',
                                 result.business_date_beijing,
                               'regional_state_sha256',
                                 result.regional_state_sha256,
                               'time_values',result.time_values
                             ),
                             'dom_aggregate_revision',
                               result.dom_aggregate_revision,
                             'ovs_aggregate_revision',
                               result.ovs_aggregate_revision,
                             'projection_generation',
                               result.projection_generation,
                             'triggering_event_id',
                               result.triggering_event_id,
                             'claimed_work_revision',
                               result.claimed_work_revision
                           )
                         ) AS plan_hash_matches
                FROM public.teacher_source_wide wide
                JOIN public.teachers teacher
                  ON teacher.teacher_id=wide.tchr_id
                JOIN public.dts_dirty_keys dirty
                  ON dirty.source_region='dom'
                 AND dirty.key_type='TEACHER_TIME_RECHECK'
                 AND dirty.key_part_1=wide.tchr_id
                JOIN public.dts_teacher_time_recheck_results result
                  ON result.teacher_id=wide.tchr_id
                WHERE wide.tchr_id=:teacher_id
                  AND result.business_date_beijing=:business_date
                """
            ),
            {
                "teacher_id": teacher_id,
                "business_date": business_date,
            },
        ).one()
        assert row == (
            30,
            2.0,
            "EXISTING",
            30,
            "COMPLETED",
            business_date,
            1,
            1,
            1,
            "EXISTING",
            True,
        )
        assert connection.execute(
            text(
                """
                SELECT
                  has_function_privilege(
                    'tit_growth_app',
                    'public.claim_teacher_time_rechecks_v2('
                    'text,integer,integer)','EXECUTE'
                  ),
                  has_function_privilege(
                    'tit_growth_app',
                    'public.materialize_teacher_time_recheck_v2('
                    'jsonb,bigint,bigint,bigint,text,bigint)','EXECUTE'
                  ),
                  has_table_privilege(
                    'tit_growth_app',
                    'public.dts_teacher_time_recheck_results','SELECT'
                  ),
                  has_table_privilege(
                    'tit_growth_app',
                    'public.dts_dirty_keys','UPDATE'
                  )
                """
            )
        ).one() == (True, True, False, False)

    with pytest.raises(DBAPIError, match="EVIDENCE_IMMUTABLE"):
        with admin.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE public.dts_teacher_time_recheck_results
                    SET plan_sha256=repeat('f',64)
                    WHERE teacher_id=:teacher_id
                      AND business_date_beijing=:business_date
                    """
                ),
                {
                    "teacher_id": teacher_id,
                    "business_date": business_date,
                },
            )

    with outbox.begin() as connection:
        proof = connection.execute(
            text(
                "SELECT public.teacher_time_recheck_result_proof_v1("
                ":teacher_id,:business_date)"
            ),
            {
                "teacher_id": teacher_id,
                "business_date": business_date,
            },
        ).scalar_one()
    replay_payload = {
        "v": 1,
        "teacher_id": teacher_id,
        "teacher_id_type": "NUMERIC",
        "business_date_beijing": business_date.isoformat(),
        "regional_state_sha256": proof["regional_state_sha256"],
        "time_values": proof["time_values"],
    }
    with outbox.begin() as connection:
        assert connection.execute(
            text(
                """
                SELECT public.materialize_teacher_time_recheck_v2(
                  CAST(:payload AS jsonb),:dom_revision,:ovs_revision,
                  :generation,:event_id,:work_revision
                )
                """
            ),
            {
                "payload": json.dumps(
                    replay_payload, sort_keys=True, separators=(",", ":")
                ),
                "dom_revision": proof["dom_aggregate_revision"],
                "ovs_revision": proof["ovs_aggregate_revision"],
                "generation": proof["projection_generation"],
                "event_id": proof["triggering_event_id"],
                "work_revision": proof["claimed_work_revision"],
            },
        ).scalar_one() == {
            "teacher_source_changes": 0,
            "teacher_identity_changes": 0,
            "time_recheck_result_changes": 0,
        }
    invalid_payload = {
        "v": 1,
        "teacher_id": teacher_id,
        "teacher_id_type": "NUMERIC",
        "business_date_beijing": (
            business_date - timedelta(days=1)
        ).isoformat(),
        "regional_state_sha256": proof["regional_state_sha256"],
        "time_values": proof["time_values"],
    }
    with pytest.raises(DBAPIError, match="DATE_REGRESSION"):
        with outbox.begin() as connection:
            connection.execute(
                text(
                    """
                    SELECT public.materialize_teacher_time_recheck_v2(
                      CAST(:payload AS jsonb),1,1,1,'obsolete-replay',1
                    )
                    """
                ),
                {
                    "payload": json.dumps(
                        invalid_payload,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                },
            ).scalar_one()

    with admin.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE public.dts_pipeline_control
                SET mode='ROLLED_BACK',row_version=row_version+1,
                    changed_at=transaction_timestamp(),
                    changed_by='TIME_RECHECK_STANDBY_TEST'
                WHERE control_id='PRIMARY'
                """
            )
        )
    standby = worker.run_once(max_claims=5)
    assert standby["active"] is False
    assert standby["claimed"] == 0
    with outbox.begin() as connection:
        health = PostgresDtsV2TeacherTimeRecheckStore().read_health(
            connection, stale_after_seconds=900
        )
    assert health.mode == "ROLLED_BACK"
    assert health.ready is True
