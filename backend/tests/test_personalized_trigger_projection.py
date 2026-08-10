from __future__ import annotations

from app import lesson_ingestion
from app import personalized_trigger_projection as projection


def test_legacy_importer_uses_the_canonical_trigger_projection() -> None:
    assert lesson_ingestion.TRIGGER_RULE_VERSION == projection.TRIGGER_RULE_VERSION
    assert lesson_ingestion._LessonRow is projection.LessonTriggerRow
    assert lesson_ingestion._feedback_labels is projection.feedback_labels
    assert lesson_ingestion._template_map is projection.published_template_map
    assert lesson_ingestion._build_output_specs is projection.build_output_specs
    assert lesson_ingestion._materialize_outputs is projection.materialize_outputs
