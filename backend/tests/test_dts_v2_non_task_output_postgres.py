from __future__ import annotations

import hashlib
import json

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.dts_v2_domain_aggregate import DtsV2DomainRevisionStore
from app.dts_v2_non_task_output_store import PostgresDtsV2NonTaskOutputStore
from test_dts_v2_ops_case_postgres import ops_case_postgres


COURSE_ID = "93001"
TEACHER_ID = "930"


def _publish(connection, revision_value: int):
    result = DtsV2DomainRevisionStore().publish_change(
        connection,
        aggregate_type="COURSE",
        aggregate_key={
            "source_region": "dom",
            "source_appoint_id": COURSE_ID,
        },
        aggregate_state={"test_revision": revision_value},
        changed_fields=("test_revision",),
        source_row_revision=None,
        source_position=None,
        rule_version="non-task-output-test-v1",
        cutover_coverage_identity={
            "projection_mode": "SHADOW_BUILD",
            "projection_generation": 3,
            "trigger": {
                "input_kind": "SCOPE_REVISION",
                "input_identity": {
                    "source_region": "dom",
                    "source_table": "dom_appoint",
                    "scope_kind": "CURRENT",
                    "scope_level": "GLOBAL",
                    "scope_key": "*",
                },
                "input_revision": revision_value,
                "input_fingerprint": f"{revision_value:064x}",
                "scope_state": "COMPLETE",
                "active_snapshot_id": "non-task-output-test",
                "active_fence_hash": "a" * 64,
            },
        },
    )
    assert result.event is not None
    return result


def _evidence(rule_code: str, **extra):
    value = {
        "protocol_version": "course-trigger-evidence-v1",
        "rule_code": rule_code,
        "source_region": "dom",
        "source_appoint_id": COURSE_ID,
        "participation_seq": 1,
        "teacher_id": TEACHER_ID,
        **extra,
    }
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return value, hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _insert_match(
    connection,
    *,
    match_id: str,
    dedupe_key: str,
    match_kind: str,
    output_type: str,
    output_key: str,
    discriminator: str,
    evidence: dict,
    evidence_hash: str,
    aggregate_revision: int,
    event_id: str,
):
    connection.execute(
        text(
            """
            INSERT INTO public.personalized_trigger_matches(
              trigger_match_id,trigger_code,rule_version,teacher_id,
              lesson_source_region,lesson_id,complaint_rule_id,dedupe_key,
              output_type,output_title,output_id,match_status,
              evidence_snapshot,matched_at,materialized_at,updated_at,
              source_region,source_appoint_id,participation_seq,match_kind,
              match_revision,last_transition_at,target_task_code,
              assignment_dedupe_key,seed_rule_rank,threshold_required,
              source_candidate_active,plan_evidence,plan_evidence_hash,
              teacher_execution_variant,output_key,source_ref,
              assignment_dedupe_key_sort_bytes,source_region_rank,
              source_appoint_id_type,source_appoint_id_type_rank,
              source_appoint_id_numeric,source_appoint_id_sort_bytes,
              evidence_discriminator,evidence_discriminator_type,
              evidence_discriminator_type_rank,
              evidence_discriminator_numeric,
              evidence_discriminator_sort_bytes,dedupe_key_sort_bytes,
              materialization_origin,created_projection_generation,
              serving_projection_generation,is_serving,
              task_assignment_id,ops_case_id,notification_id,
              source_aggregate_revision,projection_generation,
              triggering_event_id
            ) VALUES (
              :match_id,:rule_code,'dts-direct-v2',:teacher_id,
              'dom',:lesson_id,NULL,:match_dedupe_key,:output_type,
              'Trigger output pending materialization',NULL,'MATCHED',
              CAST(:evidence AS jsonb),transaction_timestamp(),NULL,
              transaction_timestamp(),'dom',:source_appoint_id,1,:match_kind,1,
              transaction_timestamp(),NULL,NULL,10,1,true,
              CAST(:evidence AS jsonb),:evidence_hash,'NONE',:output_key,
              'trigger-match:' || :source_ref_dedupe,NULL,0,'NUMERIC',0,
              :course_numeric,NULL,:discriminator,'NUMERIC',0,
              :discriminator_numeric,NULL,
              convert_to(:sort_dedupe_key,'UTF8'),
              'V2_LIVE',3,3,true,NULL,NULL,NULL,:aggregate_revision,3,
              :event_id
            )
            """
        ),
        {
            "match_id": match_id,
            "rule_code": evidence["rule_code"],
            "teacher_id": TEACHER_ID,
            "lesson_id": COURSE_ID,
            "source_appoint_id": COURSE_ID,
            "course_numeric": int(COURSE_ID),
            "match_dedupe_key": dedupe_key,
            "source_ref_dedupe": dedupe_key,
            "sort_dedupe_key": dedupe_key,
            "output_type": output_type,
            "evidence": json.dumps(evidence, ensure_ascii=False),
            "evidence_hash": evidence_hash,
            "output_key": output_key,
            "discriminator": discriminator,
            "discriminator_numeric": int(discriminator),
            "aggregate_revision": aggregate_revision,
            "event_id": event_id,
            "match_kind": match_kind,
        },
    )


def _set_match_state(connection, *, status: str, revision: int, event_id: str):
    connection.execute(
        text(
            """
            UPDATE public.personalized_trigger_matches
            SET match_status=:status,
                source_candidate_active=:source_candidate_active,
                match_revision=match_revision+1,
                source_aggregate_revision=:revision,
                triggering_event_id=:event_id,
                last_transition_at=transaction_timestamp(),
                updated_at=transaction_timestamp()
            WHERE source_region='dom' AND source_appoint_id=:course_id
              AND match_kind IN (
                'CAMERA_OFF_NOTIFICATION','SEVERE_COMPLAINT'
              )
            """
        ),
        {
            "status": status,
            "source_candidate_active": status != "SUPPRESSED",
            "revision": revision,
            "event_id": event_id,
            "course_id": COURSE_ID,
        },
    )


def test_taskless_outputs_create_cancel_restore_and_preserve_human_state(
    ops_case_postgres,
) -> None:
    admin, outbox, _recovery = ops_case_postgres
    with admin.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE public.dts_pipeline_control
                SET projection_generation=3,row_version=row_version+1,
                    changed_at=transaction_timestamp(),
                    changed_by='NON_TASK_OUTPUT_TEST'
                WHERE control_id='PRIMARY' AND mode='V2_PRIMARY'
                """
            )
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
                  :teacher_id,'camp-930','Output Teacher','US','UTC',1,
                  'NEW','IN_CAMP',false,0,100,'SOURCE','non-task-test',
                  jsonb_build_object('graduation_state','IN_CAMP'),
                  transaction_timestamp(),transaction_timestamp()
                )
                """
            ),
            {"teacher_id": TEACHER_ID},
        )
        connection.execute(
            text(
                """
                INSERT INTO public.lesson_source_wide(
                  source_region,"课程id","老师id","课程状态","未开摄像头"
                ) VALUES ('dom',:course_id,:teacher_id,'end',true)
                """
            ),
            {"teacher_id": TEACHER_ID, "course_id": COURSE_ID},
        )
        first = _publish(connection, 1)
        camera, camera_hash = _evidence(
            "TR-QUALITY-CAMERA-OFF", is_camera_off=True
        )
        severe, severe_hash = _evidence(
            "TR-FB-SEVERE-COMPLAINT",
            severity_rank=0,
            complaint_type_grandson="83",
        )
        _insert_match(
            connection,
            match_id="TRM-V2-camera-93001",
            dedupe_key="camera-off:TR-QUALITY-CAMERA-OFF:dom:93001:1",
            match_kind="CAMERA_OFF_NOTIFICATION",
            output_type="NOTIFICATION",
            output_key="camera-notification:TR-QUALITY-CAMERA-OFF:dom:93001:1",
            discriminator="1",
            evidence=camera,
            evidence_hash=camera_hash,
            aggregate_revision=first.aggregate_revision,
            event_id=first.event.event_id,
        )
        _insert_match(
            connection,
            match_id="TRM-V2-case-93001",
            dedupe_key="complaint:TR-FB-SEVERE-COMPLAINT:dom:93001:1:83",
            match_kind="SEVERE_COMPLAINT",
            output_type="OPS_CASE",
            output_key=(
                "complaint-case:TR-FB-SEVERE-COMPLAINT:dom:93001:1:83"
            ),
            discriminator="83",
            evidence=severe,
            evidence_hash=severe_hash,
            aggregate_revision=first.aggregate_revision,
            event_id=first.event.event_id,
        )

    store = PostgresDtsV2NonTaskOutputStore()
    with outbox.begin() as connection:
        created = store.reconcile_course_outputs(
            connection,
            source_region="dom",
            source_appoint_id=COURSE_ID,
            aggregate_revision=first.aggregate_revision,
            projection_generation=3,
            triggering_event_id=first.event.event_id,
        )
        assert created["notifications_created"] == 1
        assert created["cases_created"] == 1
        assert created["matches_linked"] == 2

    with admin.begin() as connection:
        assert connection.execute(
            text(
                """
                SELECT count(*) FROM public.personalized_trigger_matches
                WHERE source_region='dom' AND source_appoint_id=:course_id
                  AND match_status='MATERIALIZED' AND output_id IS NOT NULL
                """
            ),
            {"course_id": COURSE_ID},
        ).scalar_one() == 2
        second = _publish(connection, 2)
        _set_match_state(
            connection,
            status="SUPPRESSED",
            revision=second.aggregate_revision,
            event_id=second.event.event_id,
        )

    with outbox.begin() as connection:
        cancelled = store.reconcile_course_outputs(
            connection,
            source_region="dom",
            source_appoint_id=COURSE_ID,
            aggregate_revision=second.aggregate_revision,
            projection_generation=3,
            triggering_event_id=second.event.event_id,
        )
        assert cancelled["notifications_cancelled"] == 1
        assert cancelled["cases_cancelled"] == 1

    with admin.begin() as connection:
        third = _publish(connection, 3)
        _set_match_state(
            connection,
            status="MATERIALIZED",
            revision=third.aggregate_revision,
            event_id=third.event.event_id,
        )

    with outbox.begin() as connection:
        restored = store.reconcile_course_outputs(
            connection,
            source_region="dom",
            source_appoint_id=COURSE_ID,
            aggregate_revision=third.aggregate_revision,
            projection_generation=3,
            triggering_event_id=third.event.event_id,
        )
        assert restored["notifications_restored"] == 1
        assert restored["cases_restored"] == 1

    with admin.begin() as connection:
        notification_id = connection.execute(
            text(
                """
                SELECT notification_id FROM public.notifications
                WHERE source_ref LIKE 'camera-notification:%'
                """
            )
        ).scalar_one()
        case_id = connection.execute(
            text(
                """
                SELECT case_id FROM public.ops_cases
                WHERE source_ref LIKE 'complaint-case:%'
                """
            )
        ).scalar_one()
        connection.execute(
            text(
                """
                UPDATE public.notifications
                SET status='READ',read_at=transaction_timestamp()
                WHERE notification_id=:notification_id
                """
            ),
            {"notification_id": notification_id},
        )
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
        fourth = _publish(connection, 4)
        _set_match_state(
            connection,
            status="SUPPRESSED",
            revision=fourth.aggregate_revision,
            event_id=fourth.event.event_id,
        )

    with outbox.begin() as connection:
        preserved = store.reconcile_course_outputs(
            connection,
            source_region="dom",
            source_appoint_id=COURSE_ID,
            aggregate_revision=fourth.aggregate_revision,
            projection_generation=3,
            triggering_event_id=fourth.event.event_id,
        )
        assert preserved["notifications_cancelled"] == 0
        assert preserved["cases_cancelled"] == 0

    with admin.begin() as connection:
        assert connection.execute(
            text(
                """
                SELECT n.status,c.status,
                       (SELECT count(*) FROM public.notification_events
                        WHERE notification_id=n.notification_id),
                       (SELECT count(*) FROM public.audit_events
                        WHERE case_id=c.case_id)
                FROM public.notifications n CROSS JOIN public.ops_cases c
                WHERE n.notification_id=:notification_id
                  AND c.case_id=:case_id
                """
            ),
            {"notification_id": notification_id, "case_id": case_id},
        ).one() == ("READ", "IN_REVIEW", 3, 3)

    with pytest.raises(DBAPIError, match="permission denied"):
        with outbox.begin() as connection:
            connection.execute(
                text(
                    "UPDATE public.notifications SET status='CANCELLED' "
                    "WHERE notification_id=:notification_id"
                ),
                {"notification_id": notification_id},
            )
