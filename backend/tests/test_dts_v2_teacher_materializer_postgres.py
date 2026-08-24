from __future__ import annotations

from dataclasses import replace
from datetime import date
import json
from pathlib import Path
from typing import Any, Mapping

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.config_models import SCORE_POLICY_V1_PAYLOAD
from app.db_models import TeacherSourceWideRecord
from app.dts_teacher_aggregate_v2 import TeacherSourceWideProjectionV2
from app.dts_v2_domain_aggregate import (
    build_domain_aggregate_identity_v2,
    canonical_domain_state_v2,
)
from app.dts_v2_score_projection_store import PostgresDtsV2ScoreProjectionStore
from app.dts_v2_course_domain_projector import (
    reconcile_course_teacher_region_evidence_v2,
)
from app.dts_v2_source_repository import DtsV2CurrentSourceRow
from app.dts_v2_teacher_materializer import PostgresDtsV2TeacherMaterializer
from app.dts_v2_teacher_outbox_processor import TeacherMaterializationPlanV2
from test_dts_v2_ops_case_postgres import (
    _postgres_tools_available,
    _run_alembic,
    ops_case_postgres,
)
from test_dts_v2_course_participation_guards_postgres import (
    EPOCH_ID as COURSE_EPOCH_ID,
    TOPIC as COURSE_TOPIC,
    _insert_course,
    _insert_participation,
)
from test_dts_v2_source_current_guards_postgres import (
    _insert_version as _insert_current_source_version,
    _upsert_current,
)


_PROOF_COLUMNS = {
    "first_open_slot_evidence_status",
    "first_booked_evidence_status",
    "first_completed_evidence_status",
    "v2_dom_aggregate_revision",
    "v2_ovs_aggregate_revision",
    "v2_projection_generation",
    "v2_materialized_event_id",
    "v2_materialized_at",
    "v2_row_version",
}


class _TeacherProfileRepository:
    def __init__(self, row: DtsV2CurrentSourceRow) -> None:
        self.row = row

    def read_by_source_keys(self, connection, **kwargs):
        del connection
        return (self.row,) if self.row.source_key in kwargs["source_keys"] else ()


def _teacher_profile_row(
    teacher_id: str,
    *,
    course: str,
    revision: int,
) -> DtsV2CurrentSourceRow:
    return DtsV2CurrentSourceRow.from_database_row(
        {
            "source_region": "dom",
            "source_table": "dom_teacher",
            "source_key": teacher_id,
            "source_key_type": "NUMERIC",
            "source_row_revision": revision,
            "source_row": {"id": int(teacher_id), "course": course},
            "source_field_types": {"id": "NUMERIC", "course": "TEXT"},
            "source_position_v2": {"v": 1, "revision": revision},
            "source_payload_hash": f"{revision:064x}",
            "is_deleted": False,
            "provenance_state": "V2_CONFIRMED",
        }
    )


def _assert_teacher_region_conflict_and_repair(connection) -> None:
    course_id = "98001"
    teacher_id = "7"
    connection.execute(
        text(
            """
            INSERT INTO public.dts_source_partition_epochs (
              source_region,source_partition_epoch_id,topic,partition_id,
              epoch_kind,status,stream_generation_id,epoch_opening_id,
              epoch_sequence,start_offset,v2_epoch_bootstrap_floor,
              activation_mode
            ) VALUES (
              'dom',:epoch_id,:topic,0,'BROKER','ACTIVE',
              'teacher-region-generation','teacher-region-opening',1,0,0,
              'H0_BOOTSTRAP'
            )
            """
        ),
        {"epoch_id": COURSE_EPOCH_ID, "topic": COURSE_TOPIC},
    )
    source_row = {
        "id": int(course_id),
        "t_id": teacher_id,
        "status": "on",
    }
    source_field_types = {
        "id": "NUMERIC",
        "status": "TEXT",
        "t_id": "NUMERIC",
    }
    _insert_current_source_version(
        connection,
        revision=1,
        operation="INSERT",
        before_row=None,
        after_row=source_row,
        field_types=source_field_types,
        source_key=course_id,
        source_key_type="NUMERIC",
        epoch_id=COURSE_EPOCH_ID,
        topic=COURSE_TOPIC,
        offset_value=1,
    )
    _upsert_current(
        connection,
        revision=1,
        source_row=source_row,
        is_deleted=False,
        source_key=course_id,
        source_key_type="NUMERIC",
        epoch_id=COURSE_EPOCH_ID,
        topic=COURSE_TOPIC,
        field_types=source_field_types,
        offset_value=1,
    )
    _insert_course(
        connection,
        course_id,
        current_teacher_id=teacher_id,
        current_teacher_id_type="NUMERIC",
        current_participation_seq=1,
        last_offset=1,
        last_revision=1,
    )
    _insert_participation(
        connection,
        course_id,
        seq=1,
        teacher_id=teacher_id,
        teacher_id_type="NUMERIC",
        offset=1,
        revision=1,
        is_current=True,
    )
    connection.execute(
        text(
            """
            UPDATE public.source_courses
            SET appoint_evidence_status='CONFIRMED',
                row_version=row_version+1,
                updated_at=clock_timestamp()
            WHERE source_region='dom' AND source_appoint_id=:course_id
            """
        ),
        {"course_id": course_id},
    )

    conflict = reconcile_course_teacher_region_evidence_v2(
        connection,
        source_region="dom",
        source_appoint_id=course_id,
        source_repository=_TeacherProfileRepository(
            _teacher_profile_row(
                teacher_id,
                course="global_pool",
                revision=1,
            )
        ),
    )
    assert conflict.course_changed is True
    assert conflict.changed_participation_seqs == (1,)
    assert connection.execute(
        text(
            """
            SELECT c.evidence_status,c.teacher_region_evidence_status,
                   p.teacher_expected_source_region,
                   p.teacher_region_evidence_status,
                   c.current_teacher_id,p.teacher_id
            FROM public.source_courses c
            JOIN public.source_course_participations p
              USING (source_region,source_appoint_id)
            WHERE c.source_region='dom' AND c.source_appoint_id=:course_id
            """
        ),
        {"course_id": course_id},
    ).one() == (
        "SOURCE_CONFLICT",
        "SOURCE_CONFLICT",
        "ovs",
        "SOURCE_CONFLICT",
        teacher_id,
        teacher_id,
    )

    repaired = reconcile_course_teacher_region_evidence_v2(
        connection,
        source_region="dom",
        source_appoint_id=course_id,
        source_repository=_TeacherProfileRepository(
            _teacher_profile_row(
                teacher_id,
                course="adult_english",
                revision=2,
            )
        ),
    )
    assert repaired.course_changed is True
    assert repaired.changed_participation_seqs == (1,)
    assert connection.execute(
        text(
            """
            SELECT c.evidence_status,c.teacher_region_evidence_status,
                   p.teacher_expected_source_region,
                   p.teacher_region_evidence_status,
                   p.teacher_profile_source_row_revision
            FROM public.source_courses c
            JOIN public.source_course_participations p
              USING (source_region,source_appoint_id)
            WHERE c.source_region='dom' AND c.source_appoint_id=:course_id
            """
        ),
        {"course_id": course_id},
    ).one() == ("CONFIRMED", "CONFIRMED", "dom", "CONFIRMED", 2)


def _seed_score_policy(connection) -> None:
    connection.execute(
        text(
            """
            UPDATE public.dts_pipeline_control
            SET projection_generation=1,row_version=row_version+1,
                changed_at=transaction_timestamp(),
                changed_by='TEACHER_MATERIALIZER_TEST'
            WHERE control_id='PRIMARY' AND mode='V2_PRIMARY';
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
              'teacher-materializer-score-v1','SCORE_GRADUATION',1,
              'PUBLISHED',true,CAST(:payload AS jsonb),'[]'::jsonb,NULL,
              'teacher-test','teacher-test','teacher-test','teacher-test',NULL,
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


def _put_regional_aggregate(
    connection,
    *,
    teacher_id: str,
    source_region: str,
    revision: int,
    state: Mapping[str, Any],
) -> str:
    identity = build_domain_aggregate_identity_v2(
        "TEACHER",
        {"source_region": source_region, "teacher_id": teacher_id},
    )
    state_json, state_hash = canonical_domain_state_v2(state)
    connection.execute(
        text(
            """
            INSERT INTO public.domain_aggregate_revisions(
              aggregate_type,aggregate_id,canonical_key,
              canonical_key_sha256,revision,last_source_row_revision,
              last_source_position,aggregate_state,aggregate_state_sha256,
              updated_at
            ) VALUES (
              'TEACHER',:aggregate_id,CAST(:canonical_key AS jsonb),
              :key_sha256,:revision,NULL,NULL,CAST(:state AS jsonb),
              :state_sha256,transaction_timestamp()
            ) ON CONFLICT (aggregate_type,aggregate_id) DO UPDATE SET
              revision=excluded.revision,
              aggregate_state=excluded.aggregate_state,
              aggregate_state_sha256=excluded.aggregate_state_sha256,
              updated_at=transaction_timestamp()
            """
        ),
        {
            "aggregate_id": identity.aggregate_id,
            "canonical_key": identity.canonical_key_json,
            "key_sha256": identity.aggregate_id.rsplit(":", 1)[-1],
            "revision": revision,
            "state": state_json,
            "state_sha256": state_hash,
        },
    )
    return state_hash


def _source_values(
    teacher_id: str,
    *,
    peak_slot_cnt: int,
    online_status: str = "NEW",
) -> dict[str, Any]:
    values = {
        column.name: None
        for column in TeacherSourceWideRecord.__table__.columns
        if column.name not in _PROOF_COLUMNS
    }
    values.update(
        {
            "tchr_id": teacher_id,
            "real_name": "Teacher V2",
            "center_type_id": "1",
            "center_type_desc": "CBT",
            "bu": "FT",
            "status": {
                "NEW": "on",
                "EXISTING": "on",
                "LEFT": "off",
                "BLOCKED": "hei",
            }[online_status],
            "status_on_date": date(2026, 8, 1),
            "job_days": 10,
            "job_month": 1.0,
            "teach_area_type": "K12",
            "onboard_date": date(2026, 8, 1),
            "onboard_30d_end_date": date(2026, 8, 30),
            "total_booked_cnt": 0,
            "peak_booked_cnt": 0,
            "total_completed_cnt": 0,
            "peak_completed_cnt": 0,
            "absent_cnt": 0,
            "late_cnt": 0,
            "early_cnt": 0,
            "anomaly_cnt": 0,
            "perfect_cnt": 0,
            "no_notice_cnt": 0,
            "first_completed_student_cnt": 0,
            "feedback_total_eval_cnt": 0,
            "feedback_praise_cnt": 0,
            "feedback_negative_cnt": 0,
            "feedback_complaint_cnt": 0,
            "feedback_valid_complaint_cnt": 0,
            "feedback_favorite_cnt": 0,
            "feedback_block_cnt": 0,
            "total_slot_cnt": max(peak_slot_cnt, 1),
            "reg_slot_cnt": 0,
            "peak_slot_cnt": peak_slot_cnt,
            "slot_days": 1,
            "peak_slot_days": 1 if peak_slot_cnt else 0,
            "reliability_absent_rate": 0.0,
            "reliability_late_rate": 0.0,
            "reliability_early_leave_rate": 0.0,
            "reliability_late_early_rate": 0.0,
            "feedback_praise_rate": 0.0,
            "feedback_negative_rate": 0.0,
            "feedback_complaint_rate": 0.0,
            "feedback_favorite_rate": None,
            "feedback_block_rate": None,
            "feedback_eval_rate": 0.0,
            "capacity_avg_completed_per_day": 0.0,
            "capacity_peak_slot_rate": 1.0 if peak_slot_cnt else 0.0,
            "capacity_key_slot_day_rate": 1.0 if peak_slot_cnt else 0.0,
            "is_cpl_tesol": True,
            "is_self_introduce": None,
            "online_status": online_status,
            "online_status_evidence_status": "CONFIRMED",
        }
    )
    return values


def _plan(
    teacher_id: str,
    *,
    dom_revision: int,
    ovs_revision: int,
    dom_hash: str,
    ovs_hash: str,
    peak_slot_cnt: int,
    online_status: str = "NEW",
) -> TeacherMaterializationPlanV2:
    return TeacherMaterializationPlanV2(
        teacher_id=teacher_id,
        teacher_id_type="TEXT",
        business_date_beijing=date(2026, 8, 22),
        projection=TeacherSourceWideProjectionV2(
            values=_source_values(
                teacher_id,
                peak_slot_cnt=peak_slot_cnt,
                online_status=online_status,
            ),
            first_date_evidence={
                "first_open_slot_dt_evidence_status": "SOURCE_MISSING",
                "first_booked_dt_evidence_status": "SOURCE_MISSING",
                "first_completed_dt_evidence_status": "SOURCE_MISSING",
            },
            metric_evidence={"peak_slot_cnt": "CONFIRMED"},
        ),
        regional_revisions={"dom": dom_revision, "ovs": ovs_revision},
        regional_state_sha256={"dom": dom_hash, "ovs": ovs_hash},
    )


def _payload(plan: TeacherMaterializationPlanV2) -> str:
    values = {
        key: value.isoformat() if isinstance(value, date) else value
        for key, value in plan.projection.values.items()
    }
    return json.dumps(
        {
            "v": 1,
            "teacher_id": plan.teacher_id,
            "teacher_id_type": plan.teacher_id_type,
            "regional_state_sha256": dict(plan.regional_state_sha256),
            "values": values,
            "first_date_evidence": dict(plan.projection.first_date_evidence),
            "metric_evidence": dict(plan.projection.metric_evidence),
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _serving_row(connection, teacher_id: str) -> dict[str, Any]:
    return dict(
        connection.execute(
            text(
                """
                SELECT source.v2_dom_aggregate_revision,
                       source.v2_ovs_aggregate_revision,
                       source.v2_projection_generation,
                       source.v2_materialized_event_id,
                       source.v2_row_version,source.peak_slot_cnt,
                       teacher.online_status,teacher.total_score,
                       (teacher.payload->>'raw_total_score')::double precision
                         AS raw_total_score
                FROM public.teacher_source_wide source
                JOIN public.teachers teacher
                  ON teacher.teacher_id=source.tchr_id
                WHERE source.tchr_id=:teacher_id
                """
            ),
            {"teacher_id": teacher_id},
        ).mappings().one()
    )


@pytest.mark.skipif(
    not _postgres_tools_available(),
    reason="local PostgreSQL binaries are required for rev89 contracts",
)
def test_teacher_materializer_is_atomic_replay_safe_and_milestone_locked(
    ops_case_postgres,
) -> None:
    admin, worker, _recovery = ops_case_postgres
    teacher_id = "teacher-v2-materializer"
    with admin.connect() as connection:
        assert connection.execute(
            text(
                """
                SELECT has_table_privilege(
                  'tit_dts_outbox_worker_runtime',
                  'public.teacher_source_wide','SELECT'
                )
                """
            )
        ).scalar_one() is True

    with admin.connect() as connection:
        assert connection.execute(
            text("SELECT version_num FROM public.alembic_version")
        ).scalar_one() == "20260823_100_scope_snapshot_diff"
    with admin.begin() as connection:
        _assert_teacher_region_conflict_and_repair(connection)
    with admin.begin() as connection:
        _seed_score_policy(connection)
        dom_hash = _put_regional_aggregate(
            connection,
            teacher_id=teacher_id,
            source_region="dom",
            revision=1,
            state={"case": "dom-1", "peak_slot_cnt": 39},
        )
        ovs_hash = _put_regional_aggregate(
            connection,
            teacher_id=teacher_id,
            source_region="ovs",
            revision=1,
            state={"case": "ovs-1"},
        )

    materializer = PostgresDtsV2TeacherMaterializer(
        score_refresher=PostgresDtsV2ScoreProjectionStore()
    )
    plan = _plan(
        teacher_id,
        dom_revision=1,
        ovs_revision=1,
        dom_hash=dom_hash,
        ovs_hash=ovs_hash,
        peak_slot_cnt=39,
    )
    with worker.begin() as connection:
        first = materializer.apply_teacher_plan(
            connection,
            plan,
            triggering_event_id="teacher-event-dom-1",
        )
    assert first["teacher_source_changes"] == 1
    assert first["capacity_score_entries"] == 0

    with admin.connect() as connection:
        first_row = _serving_row(connection, teacher_id)
        assert first_row == {
            "v2_dom_aggregate_revision": 1,
            "v2_ovs_aggregate_revision": 1,
            "v2_projection_generation": 1,
            "v2_materialized_event_id": "teacher-event-dom-1",
            "v2_row_version": 1,
            "peak_slot_cnt": 39,
            "online_status": "NEW",
            "total_score": 0.0,
            "raw_total_score": 0.0,
        }
        assert connection.execute(
            text(
                "SELECT count(*) FROM public.task_assignments "
                "WHERE teacher_id=:teacher_id AND task_code LIKE 'G%'")
            ,
            {"teacher_id": teacher_id},
        ).scalar_one() == 9

    # Exact response-loss replay and a superseded regional event resolving to
    # the same locked current vector are both true no-ops.  Event provenance
    # and serving row_version must not drift.
    with worker.begin() as connection:
        exact_replay = materializer.apply_teacher_plan(
            connection,
            plan,
            triggering_event_id="teacher-event-dom-1",
        )
        superseded_replay = materializer.apply_teacher_plan(
            connection,
            plan,
            triggering_event_id="teacher-event-dom-0-superseded",
        )
    assert all(value == 0 for value in exact_replay.values())
    assert all(value == 0 for value in superseded_replay.values())
    with admin.connect() as connection:
        replay_row = _serving_row(connection, teacher_id)
    assert replay_row["v2_materialized_event_id"] == "teacher-event-dom-1"
    assert replay_row["v2_row_version"] == 1

    # DOM can advance independently and crossing 40 writes exactly one
    # append-only capacity achievement.
    with admin.begin() as connection:
        dom_hash = _put_regional_aggregate(
            connection,
            teacher_id=teacher_id,
            source_region="dom",
            revision=2,
            state={"case": "dom-2", "peak_slot_cnt": 40},
        )
    plan_40 = _plan(
        teacher_id,
        dom_revision=2,
        ovs_revision=1,
        dom_hash=dom_hash,
        ovs_hash=ovs_hash,
        peak_slot_cnt=40,
    )
    with worker.begin() as connection:
        crossed = materializer.apply_teacher_plan(
            connection,
            plan_40,
            triggering_event_id="teacher-event-dom-2",
        )
    assert crossed["capacity_score_entries"] == 1

    # OVS can then advance independently while the metric falls below 40;
    # the achievement and its score are irreversible.
    with admin.begin() as connection:
        ovs_hash = _put_regional_aggregate(
            connection,
            teacher_id=teacher_id,
            source_region="ovs",
            revision=2,
            state={"case": "ovs-2", "peak_slot_cnt": 39},
        )
    plan_39_again = _plan(
        teacher_id,
        dom_revision=2,
        ovs_revision=2,
        dom_hash=dom_hash,
        ovs_hash=ovs_hash,
        peak_slot_cnt=39,
    )
    with worker.begin() as connection:
        materializer.apply_teacher_plan(
            connection,
            plan_39_again,
            triggering_event_id="teacher-event-ovs-2",
        )

    with admin.begin() as connection:
        dom_hash = _put_regional_aggregate(
            connection,
            teacher_id=teacher_id,
            source_region="dom",
            revision=3,
            state={"case": "dom-3", "peak_slot_cnt": 40},
        )
    plan_40_again = _plan(
        teacher_id,
        dom_revision=3,
        ovs_revision=2,
        dom_hash=dom_hash,
        ovs_hash=ovs_hash,
        peak_slot_cnt=40,
    )
    with worker.begin() as connection:
        crossed_again = materializer.apply_teacher_plan(
            connection,
            plan_40_again,
            triggering_event_id="teacher-event-dom-3",
        )
    assert crossed_again["capacity_score_entries"] == 0

    # A BLOCKED teacher is still materialized and score-refreshed.  Online
    # availability is not a score accumulation stop switch.
    with admin.begin() as connection:
        ovs_hash = _put_regional_aggregate(
            connection,
            teacher_id=teacher_id,
            source_region="ovs",
            revision=3,
            state={"case": "ovs-3", "online_status": "BLOCKED"},
        )
    blocked_plan = _plan(
        teacher_id,
        dom_revision=3,
        ovs_revision=3,
        dom_hash=dom_hash,
        ovs_hash=ovs_hash,
        peak_slot_cnt=40,
        online_status="BLOCKED",
    )
    with worker.begin() as connection:
        materializer.apply_teacher_plan(
            connection,
            blocked_plan,
            triggering_event_id="teacher-event-ovs-3",
        )

    with admin.connect() as connection:
        final_row = _serving_row(connection, teacher_id)
        assert final_row["online_status"] == "BLOCKED"
        assert final_row["total_score"] == 10.0
        assert final_row["raw_total_score"] == 10.0
        assert connection.execute(
            text(
                """
                SELECT count(*) FROM public.score_entries
                WHERE teacher_id=:teacher_id
                  AND reason_code='CAPACITY_PEAK_SLOT_40_ACHIEVED'
                """
            ),
            {"teacher_id": teacher_id},
        ).scalar_one() == 1

    # The protected command independently rejects a forged generation, and
    # the whole failed transaction leaves the serving vector unchanged.
    with pytest.raises(DBAPIError, match="PRIMARY_GENERATION_MISMATCH"):
        with worker.begin() as connection:
            connection.execute(
                text(
                    """
                    SELECT public.materialize_teacher_source_wide_v2(
                      CAST(:payload AS jsonb),3,3,2,'forged-generation'
                    )
                    """
                ),
                {"payload": _payload(blocked_plan)},
            ).scalar_one()
    with admin.connect() as connection:
        after_failure = _serving_row(connection, teacher_id)
    assert after_failure == final_row

    with pytest.raises(DBAPIError, match="permission denied for function"):
        with admin.begin() as connection:
            connection.execute(text("SET LOCAL ROLE tit_growth_app"))
            connection.execute(
                text(
                    """
                    SELECT public.materialize_teacher_source_wide_v2(
                      CAST(:payload AS jsonb),3,3,1,'illegal-role'
                    )
                    """
                ),
                {"payload": _payload(blocked_plan)},
            ).scalar_one()

    missing_teacher = "teacher-v2-missing-peer"
    with admin.begin() as connection:
        missing_dom_hash = _put_regional_aggregate(
            connection,
            teacher_id=missing_teacher,
            source_region="dom",
            revision=1,
            state={"case": "only-dom"},
        )
    missing_plan = _plan(
        missing_teacher,
        dom_revision=1,
        ovs_revision=1,
        dom_hash=missing_dom_hash,
        ovs_hash="f" * 64,
        peak_slot_cnt=39,
    )
    with pytest.raises(DBAPIError, match="REGIONAL_VECTOR_STALE"):
        with worker.begin() as connection:
            materializer.apply_teacher_plan(
                connection,
                missing_plan,
                triggering_event_id="teacher-event-missing-peer",
            )
    with admin.connect() as connection:
        assert connection.execute(
            text(
                "SELECT count(*) FROM public.teacher_source_wide "
                "WHERE tchr_id=:teacher_id"
            ),
            {"teacher_id": missing_teacher},
        ).scalar_one() == 0
        assert connection.execute(
            text(
                """
                SELECT to_regprocedure(
                  'public.materialize_teacher_source_wide_v2(jsonb,bigint,bigint,bigint,text)'
                ) IS NOT NULL
                AND EXISTS (
                  SELECT 1 FROM information_schema.columns
                  WHERE table_schema='public'
                    AND table_name='teacher_source_wide'
                    AND column_name='v2_projection_generation'
                )
                """
            )
        ).scalar_one() is True
