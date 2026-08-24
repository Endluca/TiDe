from __future__ import annotations

import json

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.config_models import SCORE_POLICY_V1_PAYLOAD
from test_dts_v2_ops_case_postgres import ops_case_postgres


def test_score_commands_are_idempotent_vector_guarded_and_acl_protected(
    ops_case_postgres,
) -> None:
    admin, worker, _recovery = ops_case_postgres
    with admin.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE public.dts_pipeline_control
                SET projection_generation=1,row_version=row_version+1,
                    changed_at=transaction_timestamp(),
                    changed_by='SCORE_PROJECTION_TEST'
                WHERE control_id='PRIMARY' AND mode='V2_PRIMARY'
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO public.config_versions(
                  version_id,config_key,version_number,status,high_impact,
                  payload,validation_errors,source_version_id,created_by,
                  updated_by,validated_by,published_by,retired_by,
                  created_at,updated_at,validated_at,published_at,retired_at
                ) VALUES (
                  'score-projection-v1','SCORE_GRADUATION',1,'PUBLISHED',true,
                  CAST(:payload AS jsonb),'[]'::jsonb,NULL,
                  'score-test-creator','score-test-publisher',
                  'score-test-validator','score-test-publisher',NULL,
                  transaction_timestamp(),transaction_timestamp(),
                  transaction_timestamp(),transaction_timestamp(),NULL
                )
                """
            ),
            {
                "payload": json.dumps(
                    SCORE_POLICY_V1_PAYLOAD,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO public.teachers(
                  teacher_id,camp_enrollment_id,name,country,timezone,camp_day,
                  online_status,graduation_state,gold_qualified,total_score,
                  graduation_threshold,data_mode,source_snapshot_label,payload,
                  created_at,updated_at
                ) VALUES (
                  'score-t1','score-camp-t1','Score Teacher','US','UTC',45,
                  'LEFT','IN_CAMP',false,0,100,'SOURCE',
                  'SOURCE_WIDE_CURRENT',
                  jsonb_build_object('graduation_state','IN_CAMP'),
                  transaction_timestamp(),transaction_timestamp()
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO public.teacher_source_wide(
                  tchr_id,status,peak_slot_cnt,feedback_valid_complaint_cnt,
                  absent_cnt,late_cnt,early_cnt
                ) VALUES ('score-t1','off',0,0,0,0,0)
                """
            )
        )

    with worker.begin() as connection:
        vector = connection.execute(
            text(
                "SELECT public.teacher_score_projection_vector_v2('score-t1')"
            )
        ).scalar_one()
        assert vector["teacher_id"] == "score-t1"
        assert vector["control"]["projection_generation"] == 1

    with worker.begin() as connection:
        result = connection.execute(
            text(
                """
                SELECT public.rebuild_teacher_score_and_qualification_v2(
                  'score-t1',CAST(:vector AS jsonb),1
                )
                """
            ),
            {
                "vector": json.dumps(
                    vector, sort_keys=True, separators=(",", ":")
                )
            },
        ).scalar_one()
        assert result == {
            "score_component_changes": 15,
            "score_account_changes": 5,
            "qualification_changes": 1,
            "teacher_changes": 1,
        }

    # Response loss is safe: replaying the exact same dependency vector is a
    # semantic no-op because projection outputs are not vector inputs.
    with worker.begin() as connection:
        assert connection.execute(
            text(
                """
                SELECT public.rebuild_teacher_score_and_qualification_v2(
                  'score-t1',CAST(:vector AS jsonb),1
                )
                """
            ),
            {
                "vector": json.dumps(
                    vector, sort_keys=True, separators=(",", ":")
                )
            },
        ).scalar_one() == {
            "score_component_changes": 0,
            "score_account_changes": 0,
            "qualification_changes": 0,
            "teacher_changes": 0,
        }

    with admin.begin() as connection:
        state = connection.execute(
            text(
                """
                SELECT teacher.online_status,teacher.total_score,
                  qualification.graduation_qualified,
                  qualification.gold_qualified,
                  qualification.gate_results->>'projection_generation' AS gen,
                  (SELECT count(*) FROM public.score_component_accounts
                   WHERE teacher_id=teacher.teacher_id) AS component_count,
                  (SELECT count(*) FROM public.score_accounts
                   WHERE teacher_id=teacher.teacher_id) AS account_count
                FROM public.teachers teacher
                JOIN public.teacher_qualifications qualification
                  ON qualification.teacher_id=teacher.teacher_id
                WHERE teacher.teacher_id='score-t1'
                """
            )
        ).mappings().one()
        assert dict(state) == {
            "online_status": "LEFT",
            "total_score": 0.0,
            "graduation_qualified": False,
            "gold_qualified": False,
            "gen": "1",
            "component_count": 15,
            "account_count": 5,
        }

    with pytest.raises(DBAPIError, match="DTS_V2_SCORE_PROJECTION_VECTOR_STALE"):
        with admin.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE public.teacher_source_wide
                    SET peak_slot_cnt=1 WHERE tchr_id='score-t1'
                    """
                )
            )
        with worker.begin() as connection:
            connection.execute(
                text(
                    "SELECT public.rebuild_teacher_score_and_qualification_v2("
                    "'score-t1',CAST(:vector AS jsonb),1)"
                ),
                {"vector": json.dumps(vector)},
            ).scalar_one()

    with pytest.raises(DBAPIError, match="permission denied for function"):
        with admin.begin() as connection:
            connection.execute(text("SET LOCAL ROLE tit_growth_app"))
            connection.execute(
                text(
                    "SELECT public.teacher_score_projection_vector_v2("
                    "'score-t1')"
                )
            ).scalar_one()

    with pytest.raises(DBAPIError, match="permission denied for view"):
        with admin.begin() as connection:
            connection.execute(text("SET LOCAL ROLE tit_growth_app"))
            connection.execute(
                text(
                    "SELECT count(*) FROM public.teacher_scorecard_v2_v1 "
                    "WHERE teacher_id='score-t1'"
                )
            ).scalar_one()

    # Branch views are private after rev95; use the owner only to verify that
    # the protected rebuild produced the expected row.
    with admin.begin() as connection:
        assert connection.execute(
            text(
                "SELECT count(*) FROM public.teacher_scorecard_v2_v1 "
                "WHERE teacher_id='score-t1'"
            )
        ).scalar_one() == 1


def test_lesson_rebuild_rejects_unproven_course_identity(
    ops_case_postgres,
) -> None:
    admin, worker, _recovery = ops_case_postgres
    with admin.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE public.dts_pipeline_control
                SET projection_generation=1,row_version=row_version+1,
                    changed_at=transaction_timestamp(),
                    changed_by='SCORE_PROJECTION_TEST'
                WHERE control_id='PRIMARY' AND mode='V2_PRIMARY'
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO public.config_versions(
                  version_id,config_key,version_number,status,high_impact,
                  payload,validation_errors,source_version_id,created_by,
                  updated_by,validated_by,published_by,retired_by,
                  created_at,updated_at,validated_at,published_at,retired_at
                ) VALUES (
                  'score-projection-v1','SCORE_GRADUATION',1,'PUBLISHED',true,
                  CAST(:payload AS jsonb),'[]'::jsonb,NULL,'creator','publisher',
                  'validator','publisher',NULL,transaction_timestamp(),
                  transaction_timestamp(),transaction_timestamp(),
                  transaction_timestamp(),NULL
                )
                """
            ),
            {"payload": json.dumps(SCORE_POLICY_V1_PAYLOAD)},
        )

    with pytest.raises(DBAPIError, match="DTS_V2_SCORE_COURSE_NOT_FOUND"):
        with worker.begin() as connection:
            connection.execute(
                text(
                    "SELECT public.rebuild_lesson_score_result_v2("
                    "'dom','missing-course',1)"
                )
            ).scalar_one()
