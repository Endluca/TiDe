from __future__ import annotations

from types import SimpleNamespace

from app.dts_v2_dirty_queue_store import DirtyClaimV2, DirtyKeyV2
from app.dts_v2_source_repository import DtsV2CurrentSourceRow
from app.dts_v2_teacher_domain_projector import DtsV2TeacherDomainProjector
import app.dts_v2_course_domain_projector as course_domain
import app.dts_v2_teacher_domain_projector as teacher_domain


class _Mappings:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return iter(self.rows)


class _Connection:
    def __init__(self, *, scopes=(), participations=(), relationships=()):
        self.scopes = scopes
        self.participations = participations
        self.relationships = relationships

    def execute(self, statement, params):
        sql = str(statement)
        if "dts_source_scope_states" in sql:
            return _Mappings(self.scopes)
        if "source_course_participations" in sql:
            return _Mappings(self.participations)
        if "teacher_student_relationship_current" in sql:
            return _Mappings(self.relationships)
        if "dts_source_row_versions" in sql:
            return _Mappings(())
        raise AssertionError(sql)


class _Sources:
    def __init__(self, profile=None, auxiliary=()):
        self.profile = profile
        self.auxiliary = tuple(auxiliary)

    def read_by_source_keys(self, connection, **kwargs):
        return () if self.profile is None else (self.profile,)

    def read_for_dependency(self, connection, **kwargs):
        return self.auxiliary


class _Revisions:
    def __init__(self, status="CHANGED"):
        self.status = status
        self.calls = []

    def publish_change(self, connection, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(status=self.status)


def _source(table, key, values, *, deleted=False):
    return DtsV2CurrentSourceRow(
        source_region="dom",
        source_table=table,
        source_key=key,
        source_key_type="NUMERIC",
        source_row_revision=5,
        source_row=values,
        source_field_types={name: "TEXT" for name, value in values.items() if value is not None} | {"id": "NUMERIC"},
        source_position={"v": 1},
        source_payload_hash="a" * 64,
        is_deleted=deleted,
    )


def _claim(region="dom"):
    return DirtyClaimV2(
        key=DirtyKeyV2(region, "TEACHER", "7"),
        lease_token="lease",
        claimed_work_revision=2,
        row_version=3,
    )


def _patch_trigger(monkeypatch):
    monkeypatch.setattr(
        "app.dts_v2_teacher_domain_projector._read_claim_trigger_evidence",
        lambda *args, **kwargs: SimpleNamespace(
            source_row_revision=5,
            source_position={"v": 1},
            coverage_identity={"scope": "ok", "trigger": {"kind": "test"}},
        ),
    )


def test_dom_teacher_bundle_contains_profile_aux_and_regional_facts(monkeypatch):
    _patch_trigger(monkeypatch)
    profile = _source("dom_teacher", "7", {"id": "7", "real_name": "T", "status": "on"})
    certificate = _source(
        "dom_teacher_certification",
        "91",
        {"id": "91", "teacher_id": "7", "certification_code": "16", "certification_status": "1"},
    )
    revisions = _Revisions()
    connection = _Connection(
        participations=[{"source_appoint_id": "9001", "participation_seq": 1, "student_token": "dom:v1:" + "b" * 64}],
        relationships=[
            {
                "teacher_id_type": "NUMERIC",
                "student_token": "dom:v1:" + "b" * 64,
                "is_favorited": True,
                "favorite_evidence_status": "SOURCE_MISSING",
                "is_blocked": False,
                "block_evidence_status": "CONFIRMED",
            }
        ],
    )
    result = DtsV2TeacherDomainProjector(
        cutover_coverage_identity={"fleet": "v2"},
        source_repository=_Sources(profile, [certificate]),
        revision_store=revisions,
    ).process_claim(connection, _claim())

    assert result == {
        "aggregate_events": 1,
        "course_aggregate_events": 0,
        "teacher_aggregate_events": 1,
        "teacher_region_changes": 0,
    }
    state = revisions.calls[0]["aggregate_state"]
    assert state["protocol_version"] == "teacher-domain-v1"
    assert state["profile"]["values"]["status"] == "on"
    assert state["certifications"][0]["values"]["certification_code"] == "16"
    assert state["participations"][0]["source_appoint_id"] == "9001"
    assert state["relationships"][0]["is_favorited"] is True
    assert (
        state["relationships"][0]["favorite_evidence_status"]
        == "SOURCE_MISSING"
    )
    assert state["relationships"][0]["block_evidence_status"] == "CONFIRMED"
    assert state["history"] == {
        "appoint_versions": [],
        "schedule_versions": [],
    }


def test_ovs_bundle_uses_explicit_dom_profile_reference_without_guessing(monkeypatch):
    _patch_trigger(monkeypatch)
    revisions = _Revisions(status="UNCHANGED")
    result = DtsV2TeacherDomainProjector(
        cutover_coverage_identity={"fleet": "v2"},
        source_repository=_Sources(),
        revision_store=revisions,
    ).process_claim(_Connection(), _claim("ovs"))

    assert result == {
        "aggregate_events": 0,
        "course_aggregate_events": 0,
        "teacher_aggregate_events": 0,
        "teacher_region_changes": 0,
    }
    state = revisions.calls[0]["aggregate_state"]
    assert state["profile"] is None
    assert state["profile_evidence_status"] == "CROSS_REGION_REFERENCE"
    assert state["profile_reference"] == {"source_region": "dom", "teacher_id": "7"}


def test_missing_dom_profile_is_not_silently_treated_as_deleted(monkeypatch):
    _patch_trigger(monkeypatch)
    revisions = _Revisions()
    DtsV2TeacherDomainProjector(
        cutover_coverage_identity={"fleet": "v2"},
        source_repository=_Sources(),
        revision_store=revisions,
    ).process_claim(_Connection(), _claim())
    assert revisions.calls[0]["aggregate_state"]["profile_evidence_status"] == "SOURCE_MISSING"


def test_dom_profile_trigger_rechecks_all_courses_and_publishes_both_peers(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        teacher_domain,
        "_read_claim_trigger_evidence",
        lambda *args, **kwargs: SimpleNamespace(
            source_row_revision=8,
            source_position={"v": 1},
            coverage_identity={
                "trigger": {
                    "input_kind": "SOURCE_REVISION",
                    "input_identity": {
                        "source_region": "dom",
                        "source_table": "dom_teacher",
                        "source_key": "7",
                    },
                }
            },
        ),
    )
    monkeypatch.setattr(
        teacher_domain,
        "_read_teacher_course_identities",
        lambda *args, **kwargs: (("dom", "100"), ("ovs", "200")),
    )
    monkeypatch.setattr(
        course_domain,
        "reconcile_course_teacher_region_evidence_v2",
        lambda *args, **kwargs: course_domain.CourseTeacherRegionReconcileV2(
            course_changed=True,
            changed_participation_seqs=(1,),
            affected_teacher_ids=("7",),
        ),
    )
    monkeypatch.setattr(
        course_domain,
        "_read_course_aggregate_state",
        lambda *args, **kwargs: {
            "course": {
                "source_region": kwargs["source_region"],
                "source_appoint_id": kwargs["source_appoint_id"],
                "evidence_status": "SOURCE_CONFLICT",
            }
        },
    )
    published_peers: list[tuple[str, str]] = []
    monkeypatch.setattr(
        teacher_domain,
        "publish_regional_teacher_aggregate_v2",
        lambda *args, **kwargs: published_peers.append(
            (kwargs["source_region"], kwargs["teacher_id"])
        )
        or SimpleNamespace(status="CHANGED"),
    )
    revisions = _Revisions()

    result = DtsV2TeacherDomainProjector(
        cutover_coverage_identity={"fleet": "v2"},
        source_repository=_Sources(),
        revision_store=revisions,
    ).process_claim(object(), _claim("dom"))

    assert result == {
        "aggregate_events": 4,
        "course_aggregate_events": 2,
        "teacher_aggregate_events": 2,
        "teacher_region_changes": 4,
    }
    assert [call["aggregate_key"] for call in revisions.calls] == [
        {"source_region": "dom", "source_appoint_id": "100"},
        {"source_region": "ovs", "source_appoint_id": "200"},
    ]
    assert published_peers == [("dom", "7"), ("ovs", "7")]
