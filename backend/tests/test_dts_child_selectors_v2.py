from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app.dts_child_selectors_v2 import (
    CloseCameraFact,
    ComplaintFact,
    DomGradingFact,
    GradingLabelLogFact,
    SelectorSourceIdentity,
    TeacherCertificationFact,
    select_current_close_camera_state,
    select_current_complaints,
    select_current_dom_grading,
    select_current_grading_labels,
    select_current_tesol_state,
)


def _identity(
    source_id: str,
    *,
    source_id_type: str = "NUMERIC",
    revision: int = 1,
) -> SelectorSourceIdentity:
    return SelectorSourceIdentity(source_id, source_id_type, revision)  # type: ignore[arg-type]


def _at(hour: int) -> datetime:
    return datetime(2026, 8, 22, hour, tzinfo=timezone.utc)


def _grading(
    source_id: str,
    *,
    update_time: datetime | None = None,
    use_point: object = "buy",
    score: object = 5,
    grading_type: object = None,
    is_del: object = None,
    is_deleted: bool = False,
    source_id_type: str = "NUMERIC",
) -> DomGradingFact:
    return DomGradingFact(
        identity=_identity(source_id, source_id_type=source_id_type),
        use_point=use_point,
        score=score,
        grading_type=grading_type,
        update_time=update_time,
        is_del=is_del,
        is_deleted=is_deleted,
    )


def test_grading_latest_unknown_clears_old_and_delete_restores_old() -> None:
    positive = _grading("1", update_time=_at(10))
    unknown = _grading("2", update_time=_at(11), use_point="other")

    selected = select_current_dom_grading((positive, unknown))
    assert selected is not None
    assert selected.fact.identity.source_id == "2"
    assert selected.classification is None

    restored = select_current_dom_grading(
        (positive, _grading("2", update_time=_at(11), is_deleted=True))
    )
    assert restored is not None
    assert restored.fact.identity.source_id == "1"
    assert restored.classification == "POSITIVE"


def test_grading_filters_only_delete_and_is_del_not_status() -> None:
    selected = select_current_dom_grading(
        (
            _grading("1", update_time=_at(10), score=1),
            _grading("2", update_time=_at(12), score=5, is_del=1),
            _grading("3", update_time=_at(11), grading_type="ignored"),
        )
    )

    assert selected is not None
    assert selected.fact.identity.source_id == "3"
    assert selected.classification == "POSITIVE"


def test_selector_numeric_id_order_and_type_drift_are_explicit() -> None:
    selected = select_current_dom_grading((_grading("9"), _grading("10")))
    assert selected is not None
    assert selected.fact.identity.source_id == "10"

    text_selected = select_current_dom_grading(
        (
            _grading("9", source_id_type="TEXT"),
            _grading("10", source_id_type="TEXT"),
        )
    )
    assert text_selected is not None
    assert text_selected.fact.identity.source_id == "9"

    with pytest.raises(ValueError, match="type drift"):
        select_current_dom_grading(
            (_grading("9"), _grading("9", source_id_type="TEXT"))
        )


def test_label_logs_dedupe_by_typed_label_and_latest_null_name_suppresses_old() -> None:
    rows = (
        GradingLabelLogFact(
            _identity("1"), "7", "NUMERIC", "态度", create_time=_at(10)
        ),
        GradingLabelLogFact(
            _identity("2"), "7.0", "NUMERIC", None, create_time=_at(11)
        ),
        GradingLabelLogFact(
            _identity("3"), "8", "NUMERIC", "发音", create_time=_at(9)
        ),
        GradingLabelLogFact(
            _identity("4"), None, None, "缺主键", create_time=_at(12)
        ),
    )

    result = select_current_grading_labels(rows)

    assert [(row.label_id, row.label_name) for row in result.labels] == [
        ("7", None),
        ("8", "发音"),
    ]
    assert [row.source_id for row in result.pending_source_ids] == ["4"]

    restored = select_current_grading_labels(
        (*rows[:1], GradingLabelLogFact(_identity("2"), "7", "NUMERIC", None, is_deleted=True))
    )
    assert restored.labels[0].label_name == "态度"


def test_label_selector_rejects_label_id_type_drift() -> None:
    with pytest.raises(ValueError, match="label id type drift"):
        select_current_grading_labels(
            (
                GradingLabelLogFact(_identity("1"), "7", "NUMERIC", "数字"),
                GradingLabelLogFact(_identity("2"), "7", "TEXT", "文本"),
            )
        )


def _complaint(
    source_id: str,
    *,
    add_time: datetime | None,
    grandson: object = None,
    approve: object = "y",
    deleted: bool = False,
) -> ComplaintFact:
    return ComplaintFact(
        identity=_identity(source_id),
        complaint_type=13,
        complaint_type_grandson=grandson,
        approve=approve,
        validity=1,
        add_time=add_time,
        course_date=date(2026, 8, 22),
        is_deleted=deleted,
    )


def test_complaint_keeps_all_valid_and_routes_by_latest_valid() -> None:
    result = select_current_complaints(
        (
            _complaint("1", add_time=_at(10)),
            _complaint("2", add_time=_at(12), grandson=82),
            _complaint("3", add_time=_at(11), grandson=81),
            _complaint("4", add_time=_at(13), approve="n"),
        )
    )

    assert [row.identity.source_id for row in result.valid_facts] == ["1", "3"]
    assert result.latest_valid is not None
    assert result.latest_valid.identity.source_id == "3"


def test_complaint_delete_latest_restores_previous_valid() -> None:
    result = select_current_complaints(
        (
            _complaint("1", add_time=_at(10)),
            _complaint("3", add_time=_at(11), deleted=True),
        )
    )

    assert result.latest_valid is not None
    assert result.latest_valid.identity.source_id == "1"


def test_tesol_and_camera_preserve_complete_scope_three_value_semantics() -> None:
    tesol = TeacherCertificationFact(_identity("1"), "16", 1)
    deleted_tesol = TeacherCertificationFact(_identity("1"), "16", 1, True)

    assert select_current_tesol_state((tesol,), scope_complete=False) is True
    assert select_current_tesol_state((deleted_tesol,), scope_complete=False) is None
    assert select_current_tesol_state((deleted_tesol,), scope_complete=True) is False

    camera = CloseCameraFact(_identity("1"))
    assert select_current_close_camera_state((camera,), scope_complete=False) is True
    assert select_current_close_camera_state((), scope_complete=False) is None
    assert select_current_close_camera_state((), scope_complete=True) is False


def test_selector_rejects_naive_time_instead_of_guessing_source_timezone() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        _grading("1", update_time=datetime(2026, 8, 22, 10))
