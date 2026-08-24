from __future__ import annotations

import hashlib
import json

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.config_models import TEACHER_PERSONALIZED_COPY_V1_PAYLOAD
from test_dts_v2_ops_case_postgres import (
    _postgres_tools_available,
    ops_case_postgres,
)


DOM_STUDENT = "dom:v1:" + "a" * 64


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _scope(*, complete: bool) -> dict[str, object]:
    return {
        "source_table": "dom_teacher_blacklist",
        "scope_kind": "CURRENT",
        "scope_level": "GLOBAL" if complete else None,
        "scope_key": "*" if complete else None,
        "state": "COMPLETE" if complete else "UNKNOWN",
        "row_version": 1 if complete else None,
        "active_snapshot_id": "blacklist-complete-1" if complete else None,
        "active_fence_hash": "f" * 64 if complete else None,
        "history_from": None,
        "history_through": None,
    }


def _evidence(
    state: str,
    *,
    complete: bool,
    active_count: int,
    missing_count: int,
) -> dict[str, object]:
    return {
        "protocol_version": "blacklist-threshold-evidence-v1",
        "source_region": "dom",
        "teacher_id": "blacklist-t1",
        "teacher_id_type": "TEXT",
        "threshold": 2,
        "threshold_state": state,
        "evidence_status": (
            "SOURCE_MISSING" if state == "SOURCE_MISSING" else "CONFIRMED"
        ),
        "source_collection_complete": complete,
        "distinct_active_student_count": active_count,
        "source_missing_student_count": missing_count,
        "active_student_token_set_hash": hashlib.sha256(
            _canonical([f"student-{index}" for index in range(active_count)]).encode()
        ).hexdigest(),
        "source_missing_student_token_set_hash": hashlib.sha256(
            _canonical([f"missing-{index}" for index in range(missing_count)]).encode()
        ).hexdigest(),
        "scope": _scope(complete=complete),
    }


def _seed_teacher_and_catalog(connection) -> None:
    connection.execute(
        text(
            """
            INSERT INTO public.teachers(
              teacher_id,camp_enrollment_id,name,country,timezone,camp_day,
              online_status,graduation_state,gold_qualified,total_score,
              graduation_threshold,data_mode,source_snapshot_label,payload,
              created_at,updated_at
            ) VALUES (
              'blacklist-t1','camp-blacklist-t1','Blacklist Teacher','US',
              'UTC',10,'NEW','IN_CAMP',false,0,100,'SOURCE',
              'BLACKLIST_TEST',jsonb_build_object(
                'graduation_state','IN_CAMP'
              ),transaction_timestamp(),transaction_timestamp()
            )
            """
        )
    )
    connection.execute(
        text(
            """
            INSERT INTO public.task_templates(
              row_id,template_id,template_version,status,revision,
              output_type,execution_owner,integration_mode,
              external_task_template_code,source_mode,payload,
              created_by,updated_by,created_at,updated_at
            ) VALUES (
              'P-FB-BLACKLIST:v1','P-FB-BLACKLIST',1,'PUBLISHED',1,
              'TEACHER_TASK','TEACHER_APP','OUTBOUND_MANAGED',
              'P-FB-BLACKLIST','REAL',jsonb_build_object(
                'template_id','P-FB-BLACKLIST',
                'title','Blacklist Improvement',
                'category','PERSONALIZED_IMPROVEMENT',
                'content_status','READY','score_type','ZERO','score_value',0
              ),'BLACKLIST_TEST','BLACKLIST_TEST',
              transaction_timestamp(),transaction_timestamp()
            )
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
              'blacklist-copy-v1','teacher_personalized_copy',1,
              'PUBLISHED',true,CAST(:copy_payload AS jsonb),'[]'::jsonb,NULL,
              'blacklist-test','blacklist-test','blacklist-validator',
              'blacklist-publisher',NULL,transaction_timestamp(),
              transaction_timestamp(),transaction_timestamp(),
              transaction_timestamp(),NULL
            )
            """
        ),
        {"copy_payload": _canonical(TEACHER_PERSONALIZED_COPY_V1_PAYLOAD)},
    )


def _publish_revision(connection, *, revision: int, evidence: dict[str, object]) -> str:
    key = {
        "source_region": "dom",
        "teacher_id": "blacklist-t1",
        "student_token": DOM_STUDENT,
    }
    state = {
        "protocol_version": "teacher-student-domain-v1",
        "blacklist_threshold": evidence,
    }
    key_json = _canonical(key)
    state_json = _canonical(state)
    aggregate_id = connection.execute(
        text(
            "SELECT 'v2:TEACHER_STUDENT:' || "
            "public.dts_canonical_json_sha256_v1(CAST(:key AS jsonb))"
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
                  'TEACHER_STUDENT',:aggregate_id,CAST(:key AS jsonb),
                  public.dts_canonical_json_sha256_v1(CAST(:key AS jsonb)),
                  1,NULL,NULL,CAST(:state AS jsonb),
                  public.dts_canonical_json_sha256_v1(CAST(:state AS jsonb)),
                  transaction_timestamp()
                )
                """
            ),
            {"aggregate_id": aggregate_id, "key": key_json, "state": state_json},
        )
    else:
        connection.execute(
            text(
                """
                UPDATE public.domain_aggregate_revisions
                SET revision=:revision,aggregate_state=CAST(:state AS jsonb),
                    aggregate_state_sha256=
                      public.dts_canonical_json_sha256_v1(
                        CAST(:state AS jsonb)
                      ),updated_at=transaction_timestamp()
                WHERE aggregate_type='TEACHER_STUDENT'
                  AND aggregate_id=:aggregate_id
                """
            ),
            {
                "revision": revision,
                "state": state_json,
                "aggregate_id": aggregate_id,
            },
        )
    event_id = f"blacklist-threshold-test:{revision}"
    payload = _canonical({"aggregate_revision": revision, "aggregate_key": key})
    connection.execute(
        text(
            """
            INSERT INTO public.outbox_events(
              outbox_id,event_id,aggregate_type,aggregate_id,event_type,
              payload,payload_sha256,status,available_at,attempt_count,
              recovery_count,recovered_at,row_version,last_error,
              settled_by_run_id,created_at,published_at
            ) VALUES (
              :outbox_id,:event_id,'TEACHER_STUDENT',:aggregate_id,
              'source_wide.changed.v2',CAST(:payload AS jsonb),
              public.dts_canonical_json_sha256_v1(CAST(:payload AS jsonb)),
              'PENDING',transaction_timestamp(),0,0,NULL,1,NULL,NULL,
              transaction_timestamp(),NULL
            )
            """
        ),
        {
            "outbox_id": "blacklist-outbox-" + str(revision),
            "event_id": event_id,
            "aggregate_id": aggregate_id,
            "payload": payload,
        },
    )
    return event_id


def _call(worker, *, revision: int, event_id: str, evidence: dict[str, object]):
    with worker.begin() as connection:
        return connection.execute(
            text(
                """
                SELECT public.reconcile_blacklist_threshold_v2(
                  'dom','blacklist-t1',:revision,1,:event_id,
                  CAST(:evidence AS jsonb)
                )
                """
            ),
            {
                "revision": revision,
                "event_id": event_id,
                "evidence": _canonical(evidence),
            },
        ).scalar_one()


def _complete_blacklist_scope(connection) -> None:
    connection.execute(text("SET LOCAL session_replication_role='replica'"))
    connection.execute(
        text(
            """
            INSERT INTO public.dts_source_scope_snapshots(
              snapshot_id,source_region,source_table,scope_kind,scope_level,
              scope_key,epoch_state,snapshot_as_of,
              snapshot_consistency_token,snapshot_consistency_token_hash,
              snapshot_fence_vector,partition_offsets,snapshot_fence_hash,
              row_count,content_hash,base_publish_generation,
              published_generation,generation_diff_count,
              generation_diff_hash,begin_request_hash,verify_request_hash,
              publish_request_hash,terminal_request_hash,created_by,
              verified_at,completed_at,row_version
            ) VALUES (
              'blacklist-complete-1','dom','dom_teacher_blacklist','CURRENT',
              'GLOBAL','*','COMPLETE',transaction_timestamp(),'test-token',
              repeat('1',64),jsonb_build_array(
                jsonb_build_object('partition_id',0,'end_next_offset',0)
              ),jsonb_build_array(
                jsonb_build_object('partition_id',0,'end_next_offset',0)
              ),repeat('f',64),0,repeat('2',64),0,1,0,repeat('3',64),
              repeat('4',64),repeat('5',64),repeat('6',64),repeat('7',64),
              'BLACKLIST_TEST',transaction_timestamp(),
              transaction_timestamp(),1
            )
            """
        )
    )
    connection.execute(
        text(
            """
            INSERT INTO public.dts_source_scope_states(
              source_region,source_table,scope_kind,scope_level,scope_key,
              state,active_snapshot_id,candidate_snapshot_id,completed_at,
              invalidated_at,row_version,updated_at
            ) VALUES (
              'dom','dom_teacher_blacklist','CURRENT','GLOBAL','*',
              'COMPLETE','blacklist-complete-1',NULL,
              transaction_timestamp(),NULL,1,transaction_timestamp()
            )
            """
        )
    )


@pytest.mark.skipif(
    not _postgres_tools_available(),
    reason="local PostgreSQL binaries are required for blacklist threshold test",
)
def test_blacklist_sql_threshold_is_three_state_and_never_guesses_suppression(
    ops_case_postgres,
) -> None:
    admin, worker, _recovery = ops_case_postgres
    with admin.begin() as connection:
        _seed_teacher_and_catalog(connection)

    active = _evidence(
        "ACTIVE", complete=False, active_count=2, missing_count=1
    )
    with admin.begin() as connection:
        active_event = _publish_revision(connection, revision=1, evidence=active)
    assert _call(
        worker, revision=1, event_id=active_event, evidence=active
    )["match_status"] == "MATCHED"

    invalid_suppressed = _evidence(
        "SUPPRESSED", complete=False, active_count=1, missing_count=0
    )
    with admin.begin() as connection:
        invalid_event = _publish_revision(
            connection, revision=2, evidence=invalid_suppressed
        )
    with pytest.raises(DBAPIError, match="THRESHOLD_BOUNDARY_INVALID"):
        _call(
            worker,
            revision=2,
            event_id=invalid_event,
            evidence=invalid_suppressed,
        )

    missing = _evidence(
        "SOURCE_MISSING", complete=False, active_count=1, missing_count=1
    )
    with admin.begin() as connection:
        missing_event = _publish_revision(connection, revision=3, evidence=missing)
    assert _call(
        worker, revision=3, event_id=missing_event, evidence=missing
    )["match_status"] == "MATCHED"
    with admin.connect() as connection:
        assert connection.execute(
            text(
                """
                SELECT match_status,source_candidate_active,
                       plan_evidence->>'threshold_state'
                FROM public.personalized_trigger_matches
                WHERE dedupe_key='blacklist-threshold:dom:blacklist-t1'
                """
            )
        ).one() == ("MATCHED", True, "SOURCE_MISSING")

    with admin.begin() as connection:
        _complete_blacklist_scope(connection)
    suppressed = _evidence(
        "SUPPRESSED", complete=True, active_count=1, missing_count=0
    )
    with admin.begin() as connection:
        suppressed_event = _publish_revision(
            connection, revision=4, evidence=suppressed
        )
    assert _call(
        worker,
        revision=4,
        event_id=suppressed_event,
        evidence=suppressed,
    )["match_status"] == "SUPPRESSED"
    with admin.connect() as connection:
        assert connection.execute(
            text(
                """
                SELECT match_status,source_candidate_active
                FROM public.personalized_trigger_matches
                WHERE dedupe_key='blacklist-threshold:dom:blacklist-t1'
                """
            )
        ).one() == ("SUPPRESSED", False)
