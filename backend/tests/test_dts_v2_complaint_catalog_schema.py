from __future__ import annotations

from pathlib import Path

from app import db_models


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "migrations"
    / "versions"
    / "20260822_94_complaint_catalog_fanout.py"
)


def test_revision_94_owns_one_protected_catalog_publication_surface() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    assert 'revision: str = "20260822_94_complaint_catalog_fanout"' in source
    assert (
        'down_revision: Union[str, None] = "20260822_93_non_task_outputs"'
        in source
    )
    for fragment in (
        "status IN ('DRAFT','PUBLISHED','RETIRED')",
        "uq_complaint_rule_import_published_v2",
        "uq_complaint_rule_activation_generation_v2",
        "uq_complaint_rule_source_row_v2",
        "complaint-rule:' || lower(source_sha256)",
        "publish_complaint_rule_import_v2(",
        "enqueue_complaint_category_course_fanout_v2(",
        "enqueue_dirty_from_catalog_revision_v2(",
        "tit:dts-v2-cutover",
        "tit:catalog:COMPLAINT_RULE_SET:ACTIVE_COMPLAINT_RULE_SET",
        "COMPLAINT_RULE_PUBLICATION_COMMAND_REQUIRED",
        "COMPLAINT_RULE_CHILD_IMMUTABLE",
        "COMPLAINT_RULE_CATALOG_DOWNGRADE_UNSAFE",
    ):
        assert fragment in source


def test_rule_version_and_publication_receipt_are_mapped_in_orm() -> None:
    import_columns = db_models.ComplaintRuleImportRecord.__table__.columns
    assert {
        "status",
        "publication_revision",
        "activation_generation",
        "row_count",
        "content_hash",
        "published_at",
        "retired_at",
    } <= set(import_columns.keys())

    match = db_models.PersonalizedTriggerMatchRecord.__table__
    assert "complaint_rule_source_sha256" in match.columns
    assert any(
        constraint.name == "fk_trigger_match_complaint_rule_version_v2"
        for constraint in match.foreign_key_constraints
    )
    assert (
        db_models.ComplaintRulePublicationAuditRecord.__table__.name
        == "complaint_rule_publication_audits"
    )
