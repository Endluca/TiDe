from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from app.dts_v2_course_projector import (
    DtsV2CourseProjectionBatchResult,
    DtsV2CourseProjectorError,
)
from app.dts_v2_dirty_queue_store import DirtyClaimV2, DirtyKeyV2
from app.dts_v2_domain_worker import DtsV2DomainDependencyPending
from app.dts_v2_source_repository import DtsV2CurrentSourceRow
import app.dts_v2_course_domain_projector as course_domain


def _position(*, offset: int = 8, record_id: str = "9001") -> dict[str, Any]:
    return {
        "v": 1,
        "source_timestamp": "2026-08-22T00:00:00.000000Z",
        "record_id_type": "numeric",
        "record_id": record_id,
        "source_partition_epoch_id": "epoch-course-domain",
        "topic": "topic-course-domain",
        "partition_id": 0,
        "offset_value": offset,
    }


def _source_row(
    table: str,
    source_key: str,
    source_row: dict[str, Any],
    source_field_types: dict[str, str],
    *,
    revision: int = 1,
    is_deleted: bool = False,
    payload_hash: str = "a" * 64,
    offset: int = 8,
) -> DtsV2CurrentSourceRow:
    return DtsV2CurrentSourceRow.from_database_row(
        {
            "source_region": "dom",
            "source_table": table,
            "source_key": source_key,
            "source_key_type": "NUMERIC",
            "source_row_revision": revision,
            "source_row": source_row,
            "source_field_types": source_field_types,
            "source_position_v2": _position(
                offset=offset,
                record_id=source_key,
            ),
            "source_payload_hash": payload_hash,
            "is_deleted": is_deleted,
            "provenance_state": "V2_CONFIRMED",
        }
    )


def _grading_row(*, revision: int = 1) -> DtsV2CurrentSourceRow:
    return _source_row(
        "dom_user_teacher_grading",
        "70",
        {
            "id": 70,
            "appoint_id": 9001,
            "use_point": " BUY ",
            "score": 5,
            "type": "a-value-that-must-not-filter-buy",
            "status": "a-value-that-must-not-filter-the-row",
            "update_time": "2026-08-22T10:00:00+08:00",
        },
        {
            "id": "NUMERIC",
            "appoint_id": "NUMERIC",
            "use_point": "TEXT",
            "score": "NUMERIC",
            "type": "TEXT",
            "status": "TEXT",
            "update_time": "TEMPORAL",
        },
        revision=revision,
    )


def _label_row(*, is_deleted: bool = False) -> DtsV2CurrentSourceRow:
    return _source_row(
        "dom_grading_label_log",
        "80",
        {
            "id": 80,
            "appoint_id": 9001,
            "label_id": 16,
            "label_name": "灯光问题",
            "type": 999,
            "status": "anything",
            "create_time": "2026-08-22T10:01:00+08:00",
            "dt": "2026-08-22T10:01:01+08:00",
        },
        {
            "id": "NUMERIC",
            "appoint_id": "NUMERIC",
            "label_id": "NUMERIC",
            "label_name": "TEXT",
            "type": "NUMERIC",
            "status": "TEXT",
            "create_time": "TEMPORAL",
            "dt": "TEMPORAL",
        },
        is_deleted=is_deleted,
        offset=9,
    )


def _claim(*, work_revision: int = 3) -> DirtyClaimV2:
    return DirtyClaimV2(
        key=DirtyKeyV2("dom", "COURSE", "9001"),
        lease_token="lease-course-9001",
        claimed_work_revision=work_revision,
        row_version=4,
    )


class _RowsResult:
    def __init__(self, rows=(), *, scalar=None) -> None:
        self.rows = list(rows)
        self.scalar = scalar

    def mappings(self):
        return self

    def __iter__(self):
        return iter(self.rows)

    def one_or_none(self):
        if not self.rows:
            return None
        assert len(self.rows) == 1
        return self.rows[0]

    def scalar_one_or_none(self):
        return self.scalar

    def scalar_one(self):
        assert self.scalar is not None
        return self.scalar


class _SmallConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, statement, parameters=None):
        sql = str(statement)
        params = dict(parameters or {})
        self.calls.append((sql, params))
        if "SELECT 1" in sql and "FROM public.source_courses" in sql:
            return _RowsResult(scalar=1)
        if (
            "FROM public.source_course_labels" in sql
            and "ORDER BY source_log_id" in sql
        ):
            return _RowsResult()
        if (
            "FROM public.source_course_complaints" in sql
            and "ORDER BY source_complaint_id" in sql
        ):
            return _RowsResult()
        raise AssertionError(f"unexpected SQL in fake connection: {sql}")


class _ComplaintRuleConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, statement, parameters=None):
        sql = str(statement)
        params = dict(parameters or {})
        self.calls.append((sql, params))
        if "pg_advisory_xact_lock_shared" in sql:
            return _RowsResult(scalar=True)
        if "FROM public.complaint_rule_imports imported" in sql:
            return _RowsResult(
                [
                    {
                        "rule_id": f"complaint-rule:{'a' * 64}:1",
                        "source_sha256": "a" * 64,
                        "category_l3_normalized": "测试投诉",
                        "severity_rank": 2,
                    }
                ]
            )
        raise AssertionError(f"unexpected SQL in complaint-rule fake: {sql}")


def test_complaint_rule_reader_freezes_only_the_published_catalog() -> None:
    connection = _ComplaintRuleConnection()

    rules = course_domain._read_complaint_rules(connection, {"测试投诉"})

    assert rules["测试投诉"][0].source_sha256 == "a" * 64
    assert len(connection.calls) == 3
    assert "tit:dts-v2-cutover" in connection.calls[0][0]
    assert "ACTIVE_COMPLAINT_RULE_SET" in connection.calls[1][0]
    read_sql = connection.calls[2][0]
    assert "imported.status='PUBLISHED'" in read_sql
    assert "FOR SHARE OF imported,rule" in read_sql


class _Repository:
    def __init__(self, rows) -> None:
        self.rows = tuple(rows)

    def read_for_dependency(self, connection, **kwargs):
        del connection, kwargs
        return self.rows

    def read_by_source_keys(self, connection, **kwargs):
        del connection, kwargs
        return ()


class _ProfileRepository(_Repository):
    def read_by_source_keys(self, connection, **kwargs):
        del connection
        requested = set(kwargs["source_keys"])
        return tuple(
            row for row in self.rows if row.source_key in requested
        )


class _CourseProjector:
    def __init__(self, *, error: str | None = None) -> None:
        self.error = error

    def project_until_current(self, connection, **kwargs):
        del connection
        if self.error is not None:
            raise DtsV2CourseProjectorError(self.error)
        return DtsV2CourseProjectionBatchResult(
            source_region=kwargs["source_region"],
            source_appoint_id=kwargs["source_appoint_id"],
            source_row_revision=10,
            applied_revision_count=1,
            participation_count=2,
        )


class _RevisionStore:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def publish_change(self, connection, **kwargs):
        del connection
        self.calls.append(dict(kwargs))
        aggregate_type = kwargs["aggregate_type"]
        aggregate_id = f"v2:{aggregate_type}:" + "c" * 64
        revision = len(self.calls)
        return SimpleNamespace(
            status="CHANGED",
            identity=SimpleNamespace(aggregate_id=aggregate_id),
            aggregate_revision=revision,
            event=SimpleNamespace(
                event_id=(
                    f"source_wide.changed.v2:{aggregate_type}:"
                    f"{aggregate_id}:{revision}"
                )
            ),
        )


def _participations() -> tuple[course_domain._Participation, ...]:
    assigned = datetime(2026, 8, 22, 1, tzinfo=timezone.utc)
    substituted = datetime(2026, 8, 22, 2, tzinfo=timezone.utc)
    return (
        course_domain._Participation(
            participation_seq=1,
            teacher_id="100",
            teacher_id_type="NUMERIC",
            participation_status="t_absent",
            participation_role="ASSIGNED",
            is_current=False,
            assigned_at=assigned,
            ended_at=substituted,
            absence_reason_detail="No Notification",
            no_notice=True,
            row_version=2,
        ),
        course_domain._Participation(
            participation_seq=2,
            teacher_id="200",
            teacher_id_type="NUMERIC",
            participation_status="on",
            participation_role="ASSIGNED",
            is_current=True,
            assigned_at=substituted,
            ended_at=None,
            absence_reason_detail=None,
            no_notice=None,
            row_version=1,
        ),
    )


def _course() -> dict[str, Any]:
    return {
        "source_status": "on",
        "current_teacher_id": "200",
        "current_teacher_id_type": "NUMERIC",
        "current_participation_seq": 2,
        "completion_teacher_id": None,
        "completion_teacher_id_type": None,
        "completion_participation_seq": None,
        "completion_conflict_status": "NONE",
        "completion_conflict_case_id": None,
        "conflict_fingerprint": None,
        "source_is_deleted": False,
        "evidence_status": "CONFIRMED",
        "appoint_evidence_status": "CONFIRMED",
        "teacher_region_evidence_status": "CONFIRMED",
        "last_applied_source_revision": 10,
        "row_version": 3,
    }


class _RegionUpdateConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, statement, parameters=None):
        sql = str(statement)
        params = dict(parameters or {})
        self.calls.append((sql, params))
        if "UPDATE public.source_course_participations" in sql:
            return _RowsResult(scalar=params["expected_row_version"] + 1)
        if "UPDATE public.source_courses" in sql:
            return _RowsResult(scalar=params["expected_row_version"] + 1)
        raise AssertionError(f"unexpected SQL in region fake: {sql}")


@pytest.mark.parametrize(
    ("source_region", "teacher_course", "expected_status"),
    [
        ("dom", "adult_english", "CONFIRMED"),
        ("dom", "global_pool", "SOURCE_CONFLICT"),
        ("ovs", "global_cn", "CONFIRMED"),
        ("ovs", "adult_english", "SOURCE_CONFLICT"),
    ],
)
def test_reconcile_teacher_region_uses_profile_proof_for_dom_and_ovs(
    monkeypatch,
    source_region: str,
    teacher_course: str,
    expected_status: str,
) -> None:
    participation = course_domain._Participation(
        participation_seq=1,
        teacher_id="200",
        teacher_id_type="NUMERIC",
        participation_status="on",
        participation_role="NORMAL",
        is_current=True,
        assigned_at=None,
        ended_at=None,
        absence_reason_detail=None,
        no_notice=None,
        row_version=4,
    )
    course = _course()
    course.update(
        {
            "appoint_evidence_status": "CONFIRMED",
            "teacher_region_evidence_status": "SOURCE_MISSING",
            "evidence_status": "SOURCE_MISSING",
            "row_version": 9,
        }
    )
    profile = _source_row(
        "dom_teacher",
        "200",
        {"id": 200, "course": teacher_course},
        {"id": "NUMERIC", "course": "TEXT"},
        revision=7,
        payload_hash="f" * 64,
    )
    monkeypatch.setattr(
        course_domain,
        "_read_course_bundle",
        lambda *args, **kwargs: (course, (participation,)),
    )
    connection = _RegionUpdateConnection()

    result = course_domain.reconcile_course_teacher_region_evidence_v2(
        connection,
        source_region=source_region,
        source_appoint_id="9001",
        source_repository=_ProfileRepository((profile,)),
    )

    assert result == course_domain.CourseTeacherRegionReconcileV2(
        course_changed=True,
        changed_participation_seqs=(1,),
        affected_teacher_ids=("200",),
    )
    participant_update = next(
        params
        for sql, params in connection.calls
        if "UPDATE public.source_course_participations" in sql
    )
    assert participant_update == {
        "source_region": source_region,
        "source_appoint_id": "9001",
        "participation_seq": 1,
        "expected_row_version": 4,
        "expected_region": (
            "ovs" if "global_" in teacher_course.casefold() else "dom"
        ),
        "evidence_status": expected_status,
        "profile_revision": 7,
        "profile_hash": "f" * 64,
    }
    course_update = next(
        params
        for sql, params in connection.calls
        if "UPDATE public.source_courses" in sql
    )
    assert course_update["regional_status"] == expected_status
    assert course_update["combined_status"] == expected_status


def test_child_first_waits_for_typed_course_dependency() -> None:
    source = _grading_row()
    projector = course_domain.DtsV2CourseDomainProjector(
        cutover_coverage_identity={"projection_mode": "SHADOW_BUILD"},
        course_projector=_CourseProjector(
            error="DTS_V2_COURSE_SOURCE_CURRENT_REQUIRED"
        ),
        source_repository=_Repository((source,)),
        revision_store=_RevisionStore(),
    )

    with pytest.raises(DtsV2DomainDependencyPending) as raised:
        projector.process_claim(_SmallConnection(), _claim())

    assert len(raised.value.dependencies) == 1
    dependency = raised.value.dependencies[0]
    assert dependency.dependency_type == "SOURCE_ROW"
    assert dependency.dependency_key == "dom_appoint:9001"
    assert dependency.source_revision == source.source_row_revision
    assert dependency.dependency_hash == source.source_payload_hash


def test_on_substitution_and_nonend_children_publish_normalized_intents(
    monkeypatch,
) -> None:
    rows = (_grading_row(), _label_row())
    revisions = _RevisionStore()
    projector = course_domain.DtsV2CourseDomainProjector(
        cutover_coverage_identity={"projection_mode": "SHADOW_BUILD"},
        course_projector=_CourseProjector(),
        source_repository=_Repository(rows),
        revision_store=revisions,
    )
    bundle = (_course(), _participations())
    captured_scope_tables: list[tuple[str, ...]] = []
    captured_course_fact: dict[str, Any] = {}
    captured_penalty_facts: list[dict[str, Any]] = []
    captured_labels: list[DtsV2CurrentSourceRow] = []
    reconciled: list[dict[str, Any]] = []
    refreshed_teachers: list[str] = []

    monkeypatch.setattr(course_domain, "_read_course_bundle", lambda *a, **k: bundle)
    monkeypatch.setattr(
        course_domain,
        "reconcile_course_teacher_region_evidence_v2",
        lambda *a, **k: course_domain.CourseTeacherRegionReconcileV2(
            False, (), ("100", "200")
        ),
    )

    def _scope_states(connection, *, source_region, source_tables):
        del connection, source_region
        captured_scope_tables.append(tuple(source_tables))
        return {}

    monkeypatch.setattr(course_domain, "_read_current_global_scope_states", _scope_states)
    monkeypatch.setattr(
        course_domain,
        "_read_claim_trigger_evidence",
        lambda *a, **k: course_domain._TriggerEvidence(
            1,
            _position(),
            {
                "projection_mode": "SHADOW_BUILD",
                "trigger": {
                    "input_kind": "SOURCE_REVISION",
                    "input_identity": {
                        "source_region": "dom",
                        "source_table": "dom_user_teacher_grading",
                        "source_key": "70",
                    },
                    "input_revision": 1,
                    "input_fingerprint": "b" * 64,
                    "source_payload_hash": "a" * 64,
                },
            },
        ),
    )
    monkeypatch.setattr(course_domain, "_read_existing_label", lambda *a, **k: None)

    def _label_upsert(connection, *, source, **kwargs):
        del connection, kwargs
        captured_labels.append(source)
        return True

    monkeypatch.setattr(course_domain, "_upsert_label", _label_upsert)
    monkeypatch.setattr(
        course_domain,
        "_update_participation_absence",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("UNKNOWN absence scope must not clear existing facts")
        ),
    )

    def _participation_fact(connection, **kwargs):
        del connection
        captured_penalty_facts.append(dict(kwargs))
        return True

    monkeypatch.setattr(course_domain, "_upsert_participation_fact", _participation_fact)

    def _course_fact(connection, *, values, **kwargs):
        del connection, kwargs
        captured_course_fact.update(values)
        return True

    monkeypatch.setattr(course_domain, "_upsert_course_fact", _course_fact)
    monkeypatch.setattr(
        course_domain,
        "_read_course_aggregate_state",
        lambda *a, **k: {"course": {"source_status": "on"}},
    )
    monkeypatch.setattr(
        course_domain,
        "_read_label_aggregate_state",
        lambda *a, **k: {"course_labels": [{"source_appoint_id": "9001"}]},
    )
    monkeypatch.setattr(
        course_domain,
        "_read_participation_aggregate_state",
        lambda *a, **k: {"participation": {"seq": k["participation_seq"]}},
    )
    monkeypatch.setattr(
        course_domain,
        "_reconcile_completion_conflict_case",
        lambda *a, **k: reconciled.append(dict(k))
        or {"outcome": "NOT_PENDING", "case_id": None},
    )
    monkeypatch.setattr(
        course_domain,
        "publish_regional_teacher_aggregate_v2",
        lambda *a, **k: refreshed_teachers.append(k["teacher_id"])
        or SimpleNamespace(status="UNCHANGED"),
    )

    result = projector.process_claim(_SmallConnection(), _claim())

    assert result["course_projection_revisions"] == 1
    assert result["label_fact_changes"] == 1
    assert captured_labels == [rows[1]]
    assert captured_course_fact["grading_classification"] == "POSITIVE"
    assert captured_course_fact["grading_evidence_status"] == "CONFIRMED"
    assert captured_course_fact["has_complaint"] is None
    assert captured_course_fact["has_valid_complaint"] is None
    assert captured_course_fact["is_camera_off"] is None
    assert captured_course_fact["camera_evidence_status"] == "SOURCE_MISSING"
    assert captured_course_fact["is_cpu_usage_high"] is None
    assert captured_course_fact["is_network_delay_high"] is None
    assert {row["participation_seq"] for row in captured_penalty_facts} == {1, 2}
    assert all(row["values"]["is_late"] is None for row in captured_penalty_facts)
    assert all(row["values"]["is_early"] is None for row in captured_penalty_facts)
    assert captured_scope_tables == [
        (
            "dom_user_teacher_grading",
            "dom_grading_label_log",
            "dom_complaint",
            "dom_qa_task_close_camera_record",
            "dom_teacher_absent_reason",
            "dom_teacher_penalty",
        )
    ]

    aggregate_types = [call["aggregate_type"] for call in revisions.calls]
    assert aggregate_types == [
        "COMPLETION_CONFLICT",
        "COURSE",
        "LABEL",
        "PARTICIPATION",
        "PARTICIPATION",
    ]
    assert reconciled == [
        {
            "source_region": "dom",
            "source_appoint_id": "9001",
            "expected_aggregate_revision": 1,
            "triggering_event_id": (
                "source_wide.changed.v2:COMPLETION_CONFLICT:"
                "v2:COMPLETION_CONFLICT:" + "c" * 64 + ":1"
            ),
            "expected_planned_case_id": None,
        }
    ]
    assert "COMPLAINT_CATEGORY" not in aggregate_types
    assert refreshed_teachers == ["100", "200"]
    assert all(
        call["cutover_coverage_identity"]["trigger"]["input_kind"]
        == "SOURCE_REVISION"
        for call in revisions.calls
    )


class _QueuedConnection:
    def __init__(self, rows) -> None:
        self.rows = list(rows)
        self.calls: list[str] = []

    def execute(self, statement, parameters=None):
        del parameters
        self.calls.append(str(statement))
        assert self.rows, f"unexpected SQL: {statement}"
        return _RowsResult(self.rows.pop(0))


@pytest.mark.parametrize("latest_kind", ["DEPENDENCY_WAKE", "OPERATOR_RECOVERY"])
def test_wake_and_recovery_reuse_latest_causal_source_trigger(latest_kind: str) -> None:
    identity = {
        "source_region": "dom",
        "source_table": "dom_user_teacher_grading",
        "source_key": "70",
    }
    protected_hash = "a" * 64
    fingerprint = course_domain._json_hash(
        {
            "protocol": "dirty-source-v1",
            "identity": identity,
            "revision": 1,
            "version_kind": "AFTER",
            "operation": "INSERT",
            "is_deleted": False,
            "protected_source_row_hash": protected_hash,
        }
    )
    connection = _QueuedConnection(
        (
            (
                {
                    "input_kind": latest_kind,
                    "input_identity": {"opaque": "scheduler"},
                    "input_revision": 2,
                    "input_fingerprint": "c" * 64,
                    "dirty_work_revision": 3,
                },
            ),
            (
                {
                    "input_kind": "SOURCE_REVISION",
                    "input_identity": identity,
                    "input_revision": 1,
                    "input_fingerprint": fingerprint,
                    "dirty_work_revision": 1,
                },
            ),
            (
                {
                    "source_position": _position(),
                    "version_kind": "AFTER",
                    "operation": "INSERT",
                    "protected_source_row_hash": protected_hash,
                    "source_row_revision": 1,
                    "source_payload_hash": protected_hash,
                    "is_deleted": False,
                    "provenance_state": "V2_CONFIRMED",
                },
            ),
        )
    )

    evidence = course_domain._read_claim_trigger_evidence(
        connection,
        _claim(),
        base_coverage_identity={"projection_mode": "SHADOW_BUILD"},
    )

    assert evidence.source_row_revision == 1
    assert evidence.source_position == _position()
    assert evidence.coverage_identity["trigger"] == {
        "input_kind": "SOURCE_REVISION",
        "input_identity": identity,
        "input_revision": 1,
        "input_fingerprint": fingerprint,
        "source_payload_hash": protected_hash,
    }


def test_only_wake_without_causal_input_fails_closed() -> None:
    connection = _QueuedConnection(
        (
            (
                {
                    "input_kind": "DEPENDENCY_WAKE",
                    "input_identity": {"dependency_type": "SOURCE_ROW"},
                    "input_revision": 2,
                    "input_fingerprint": "c" * 64,
                    "dirty_work_revision": 2,
                },
            ),
            (),
        )
    )
    with pytest.raises(
        course_domain.DtsV2CourseDomainProjectorError,
        match="CAUSAL_INPUT_REQUIRED",
    ):
        course_domain._read_claim_trigger_evidence(
            connection,
            _claim(),
            base_coverage_identity={"projection_mode": "SHADOW_BUILD"},
        )


@pytest.mark.parametrize("scope_state", ["STALE", "COMPLETE"])
def test_scope_trigger_uses_authoritative_source_scope_aggregate(
    scope_state: str,
) -> None:
    identity = {
        "source_region": "dom",
        "source_table": "dom_complaint",
        "scope_kind": "CURRENT",
        "scope_level": "GLOBAL",
        "scope_key": "*",
    }
    active_snapshot_id = "snapshot-1"
    fence = "d" * 64
    aggregate_state = {
        "protocol": "source-scope-state-v1",
        **identity,
        "scope_row_version": 7,
        "state": scope_state,
        "active_snapshot_id": active_snapshot_id,
        "active_epoch_id": active_snapshot_id,
        "active_fence_hash": fence,
    }
    fingerprint = course_domain._json_hash(
        {
            "protocol": "dirty-scope-v1",
            "identity": identity,
            "scope_row_version": 7,
            "state": scope_state,
            "active_snapshot_id": active_snapshot_id,
            "active_epoch_id": active_snapshot_id,
            "active_fence_hash": fence,
        }
    )
    input_row = {
        "input_kind": "SCOPE_REVISION",
        "input_identity": identity,
        "input_revision": 7,
        "input_fingerprint": fingerprint,
        "dirty_work_revision": 3,
    }
    connection = _QueuedConnection(
        (
            (input_row,),
            (input_row,),
            (
                {
                    "canonical_key": identity,
                    "aggregate_state": aggregate_state,
                },
            ),
        )
    )

    evidence = course_domain._read_claim_trigger_evidence(
        connection,
        _claim(),
        base_coverage_identity={"projection_mode": "SHADOW_BUILD"},
    )

    assert evidence.source_row_revision is None
    assert evidence.source_position is None
    assert evidence.coverage_identity["trigger"] == {
        "input_kind": "SCOPE_REVISION",
        "input_identity": identity,
        "input_revision": 7,
        "input_fingerprint": fingerprint,
        "scope_state": scope_state,
        "active_snapshot_id": active_snapshot_id,
        "active_fence_hash": fence,
    }


def test_catalog_and_time_recheck_are_not_relabelled_as_source_provenance() -> None:
    for input_kind, error in (
        ("CATALOG_REVISION", "CATALOG_TRIGGER_UNSUPPORTED"),
        ("TIME_RECHECK", "TIME_RECHECK_TRIGGER_FORBIDDEN"),
    ):
        connection = _QueuedConnection(
            (
                (
                    {
                        "input_kind": input_kind,
                        "input_identity": {"generation": "1"},
                        "input_revision": 1,
                        "input_fingerprint": "e" * 64,
                        "dirty_work_revision": 1,
                    },
                ),
            )
        )
        with pytest.raises(
            course_domain.DtsV2CourseDomainProjectorError,
            match=error,
        ):
            course_domain._read_claim_trigger_evidence(
                connection,
                _claim(),
                base_coverage_identity={"projection_mode": "SHADOW_BUILD"},
            )


def test_constructor_reserves_per_claim_trigger_coverage() -> None:
    with pytest.raises(
        course_domain.DtsV2CourseDomainProjectorError,
        match="TRIGGER_COVERAGE_RESERVED",
    ):
        course_domain.DtsV2CourseDomainProjector(
            cutover_coverage_identity={"trigger": {"input_kind": "fake"}}
        )
