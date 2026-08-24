from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.dts_v2_dirty_queue_store import DirtyClaimV2, DirtyKeyV2
from app.dts_v2_reference_domain_projector import (
    DtsV2ComplaintCategoryDomainProjector,
    DtsV2LabelDomainProjector,
    DtsV2ReferenceDomainProjectorError,
)
from app.dts_v2_source_repository import DtsV2CurrentSourceRow


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return iter(self._rows)


class _Connection:
    def __init__(self, rows):
        self.rows = rows
        self.statements = []

    def execute(self, statement, params):
        self.statements.append((str(statement), dict(params)))
        return _Result(self.rows)


class _Sources:
    def __init__(self, rows):
        self.rows = tuple(rows)
        self.calls = []

    def read_by_source_keys(self, connection, **kwargs):
        self.calls.append(kwargs)
        return self.rows


class _Revisions:
    def __init__(self, status="CHANGED"):
        self.status = status
        self.calls = []

    def publish_change(self, connection, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            status=self.status,
            aggregate_revision=4,
            event=(
                SimpleNamespace(event_id="complaint-category-event:4")
                if self.status == "CHANGED"
                else None
            ),
        )


class _Fanout:
    def __init__(self):
        self.calls = []

    def enqueue_linked_courses(self, connection, **kwargs):
        self.calls.append(kwargs)
        return {"courses_seen": 1, "courses_enqueued": 1, "courses_noop": 0}


def _row(table, key, values, *, deleted=False):
    return DtsV2CurrentSourceRow(
        source_region=table.split("_", 1)[0],
        source_table=table,
        source_key=key,
        source_key_type="NUMERIC",
        source_row_revision=7,
        source_row=values,
        source_field_types={
            name: ("NUMERIC" if name in {"id", "cate_parent", "cate_level", "type", "version", "status"} else "TEXT")
            for name, value in values.items()
            if value is not None
        },
        source_position={"v": 1},
        source_payload_hash="a" * 64,
        is_deleted=deleted,
    )


def _claim(key_type, region, key):
    return DirtyClaimV2(
        key=DirtyKeyV2(region, key_type, key),
        lease_token="lease",
        claimed_work_revision=3,
        row_version=4,
    )


@pytest.fixture(autouse=True)
def _trigger(monkeypatch):
    monkeypatch.setattr(
        "app.dts_v2_reference_domain_projector._read_claim_trigger_evidence",
        lambda *args, **kwargs: SimpleNamespace(
            source_row_revision=7,
            source_position={"v": 1},
            coverage_identity={"coverage": "ok", "trigger": {"kind": "test"}},
        ),
    )


def test_label_definition_and_all_course_usages_share_one_revision():
    source = _row(
        "dom_grading_label",
        "16",
        {
            "id": "16",
            "label_name": "优秀",
            "label_name_en": "Excellent",
            "type": 2,
            "version": 3,
            "status": 0,
        },
    )
    revisions = _Revisions()
    connection = _Connection(
        [
            {
                "source_appoint_id": "9001",
                "source_log_id": "71",
                "source_log_id_type": "NUMERIC",
                "label_id_type": "NUMERIC",
                "label_name_snapshot": "优秀",
                "create_time": None,
                "dt": None,
                "evidence_status": "CONFIRMED",
                "is_deleted": False,
                "source_row_revision": 9,
            }
        ]
    )
    result = DtsV2LabelDomainProjector(
        cutover_coverage_identity={"scope": "global"},
        source_repository=_Sources([source]),
        revision_store=revisions,
    ).process_claim(connection, _claim("LABEL", "dom", "16"))

    assert result == {"aggregate_events": 1}
    call = revisions.calls[0]
    assert call["aggregate_key"] == {"source_region": "dom", "label_id": "16"}
    assert call["aggregate_state"]["definition"]["values"]["status"] == 0
    assert call["aggregate_state"]["course_labels"][0]["source_appoint_id"] == "9001"
    assert call["changed_fields"] == ("definition", "course_labels")


def test_deleted_label_remains_an_auditable_definition_tombstone():
    source = _row(
        "ovs_grading_label",
        "16",
        {"id": "16", "label_name": None, "label_name_en": None, "type": None, "version": None, "status": None},
        deleted=True,
    )
    revisions = _Revisions(status="UNCHANGED")
    result = DtsV2LabelDomainProjector(
        cutover_coverage_identity={"scope": "global"},
        source_repository=_Sources([source]),
        revision_store=revisions,
    ).process_claim(_Connection([]), _claim("LABEL", "ovs", "16"))

    assert result == {"aggregate_events": 0}
    assert revisions.calls[0]["aggregate_state"]["definition"]["source_deleted"] is True


def test_complaint_category_fans_out_across_dom_and_ovs_course_facts():
    source = _row(
        "dom_complaint_cate",
        "82",
        {
            "id": "82",
            "cate_parent": "8",
            "cate_level": 3,
            "cate_cn_name": "测试分类",
            "cate_en_name": "Test",
            "status": 1,
        },
    )
    revisions = _Revisions()
    connection = _Connection(
        [
            {
                "source_region": "ovs",
                "source_appoint_id": "9001",
                "source_complaint_id": "5",
                "source_complaint_id_type": "NUMERIC",
                "complaint_type": "8",
                "complaint_type_type": "NUMERIC",
                "complaint_type_child": "80",
                "complaint_type_child_type": "NUMERIC",
                "complaint_type_grandson": "82",
                "complaint_type_grandson_type": "NUMERIC",
                "is_valid": True,
                "complaint_rule_id": None,
                "source_sha256": None,
                "severity_rank": None,
                "category_l1_snapshot": "一级",
                "category_l2_snapshot": "二级",
                "category_l3_snapshot": "测试分类",
                "evidence_status": "CONFIRMED",
                "is_deleted": False,
                "source_row_revision": 4,
            }
        ]
    )
    fanout = _Fanout()
    result = DtsV2ComplaintCategoryDomainProjector(
        cutover_coverage_identity={"scope": "global"},
        source_repository=_Sources([source]),
        revision_store=revisions,
        course_fanout=fanout,
    ).process_claim(connection, _claim("COMPLAINT_CATEGORY", "dom", "82"))

    assert result == {"aggregate_events": 1}
    call = revisions.calls[0]
    assert call["aggregate_key"] == {"source_region": "dom", "category_id": "82"}
    assert call["aggregate_state"]["linked_complaints"][0]["source_region"] == "ovs"
    assert fanout.calls == [
        {
            "category_id": "82",
            "aggregate_revision": 4,
            "triggering_event_id": "complaint-category-event:4",
        }
    ]


def test_missing_reference_current_stays_three_valued_and_still_fans_out():
    revisions = _Revisions()
    projector = DtsV2LabelDomainProjector(
        cutover_coverage_identity={"scope": "global"},
        source_repository=_Sources([]),
        revision_store=revisions,
    )
    assert projector.process_claim(
        _Connection([]), _claim("LABEL", "dom", "16")
    ) == {"aggregate_events": 1}
    state = revisions.calls[0]["aggregate_state"]
    assert state["definition"] is None
    assert state["definition_evidence_status"] == "SOURCE_MISSING"


def test_unchanged_complaint_category_does_not_enqueue_course_fanout():
    revisions = _Revisions(status="UNCHANGED")
    fanout = _Fanout()
    result = DtsV2ComplaintCategoryDomainProjector(
        cutover_coverage_identity={"scope": "global"},
        source_repository=_Sources([]),
        revision_store=revisions,
        course_fanout=fanout,
    ).process_claim(
        _Connection([]), _claim("COMPLAINT_CATEGORY", "dom", "82")
    )

    assert result == {"aggregate_events": 0}
    assert fanout.calls == []


def test_reference_projector_rejects_reserved_trigger_coverage():
    with pytest.raises(
        DtsV2ReferenceDomainProjectorError,
        match="DTS_V2_REFERENCE_COVERAGE_IDENTITY_REQUIRED",
    ):
        DtsV2LabelDomainProjector(
            cutover_coverage_identity={"trigger": {}},
        )
