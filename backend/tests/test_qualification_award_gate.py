from __future__ import annotations

import pytest

from app.qualification_award_gate import (
    QualificationAwardGateConfigurationError,
    irreversible_qualification_grants_enabled,
    resolve_irreversible_qualification_grants,
)


def test_irreversible_qualification_grants_default_false_and_parse_strictly() -> None:
    assert irreversible_qualification_grants_enabled({}) is False
    assert irreversible_qualification_grants_enabled(
        {"TIT_IRREVERSIBLE_QUALIFICATION_GRANTS_ENABLED": "false"}
    ) is False
    assert irreversible_qualification_grants_enabled(
        {"TIT_IRREVERSIBLE_QUALIFICATION_GRANTS_ENABLED": "true"}
    ) is True

    for value in ("", "TRUE", "False", "1", "yes", " true"):
        with pytest.raises(
            QualificationAwardGateConfigurationError,
            match="^IRREVERSIBLE_QUALIFICATION_AWARD_GATE_INVALID$",
        ):
            irreversible_qualification_grants_enabled(
                {"TIT_IRREVERSIBLE_QUALIFICATION_GRANTS_ENABLED": value}
            )


def test_disabled_gate_preserves_old_facts_but_blocks_every_new_grant() -> None:
    blocked = resolve_irreversible_qualification_grants(
        previous_graduation_earned=False,
        previous_gold_earned=False,
        graduation_current=True,
        gold_current=True,
        grants_enabled=False,
    )
    assert blocked.graduation_earned is False
    assert blocked.gold_earned is False

    preserved = resolve_irreversible_qualification_grants(
        previous_graduation_earned=True,
        previous_gold_earned=True,
        graduation_current=False,
        gold_current=False,
        grants_enabled=False,
    )
    assert preserved.graduation_earned is True
    assert preserved.gold_earned is True


def test_enabled_gate_keeps_the_existing_irreversible_semantics() -> None:
    decision = resolve_irreversible_qualification_grants(
        previous_graduation_earned=False,
        previous_gold_earned=False,
        graduation_current=False,
        gold_current=True,
        grants_enabled=True,
    )
    assert decision.graduation_earned is True
    assert decision.gold_earned is True
