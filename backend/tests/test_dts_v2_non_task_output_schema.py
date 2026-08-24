from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "migrations"
    / "versions"
    / "20260822_93_non_task_outputs.py"
)


def _source() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_revision_follows_runtime_course_health_and_installs_one_command():
    source = _source()

    assert 'revision: str = "20260822_93_non_task_outputs"' in source
    assert (
        'down_revision: Union[str, None] = '
        '"20260822_92_runtime_course_health"'
    ) in source
    assert "CREATE FUNCTION public.reconcile_course_non_task_outputs_v2(" in source
    assert "SECURITY DEFINER" in source
    assert "dts_v2_runtime_primary_guard_v1(" in source
    assert "GRANT EXECUTE ON FUNCTION" in source
    assert "TO tit_dts_outbox_worker_runtime" in source


def test_notification_and_case_identity_and_lifecycle_are_frozen():
    source = _source()

    for fragment in (
        "'v2notif:' || encode(sha256",
        "'v2case:' || encode(sha256",
        "'COMPLAINT_NETWORK_NOTIFICATION'",
        "'CAMERA_OFF_NOTIFICATION'",
        "'SEVERE_COMPLAINT'",
        "notification_row.status='STORED'",
        "notification_row.status='CANCELLED'",
        "case_row.status='OPEN'",
        "case_row.status='CANCELLED'",
        "'SOURCE_EVIDENCE_SUPERSEDED'",
        "'SOURCE_CANCELLED'",
        "'SOURCE_RESTORED'",
    ):
        assert fragment in source
    assert "notification_row.status='READ'" not in source
    assert "notification_row.status='CLICKED'" not in source
    assert "case_row.status='IN_REVIEW'" not in source
    assert "SET task_id=" not in source


def test_teacher_notification_copy_is_english_and_only_proves_storage():
    source = _source()

    assert "'In-Class Quality Alert'" in source
    assert "'Evidence: the camera was off for this lesson." in source
    assert "'Evidence: this lesson received a network or device complaint." in source
    assert "'STORED','CREATED_STORED'" in source
    assert "DELIVERED" not in source
    assert "SENT" not in source


def test_output_writes_are_command_owned_and_audited():
    source = _source()

    assert "append_dts_v2_notification_lifecycle_event_v1" in source
    assert "append_dts_v2_case_lifecycle_audit_v1" in source
    assert "REVOKE INSERT,UPDATE,DELETE,TRUNCATE ON TABLE" in source
    assert "public.personalized_trigger_matches,public.notifications" in source
    assert "public.notification_events,public.ops_cases,public.audit_events" in source
