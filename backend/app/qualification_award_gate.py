"""Fail-closed gate for creating new irreversible qualification facts."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass


QUALIFICATION_GRANTS_ENABLED_ENV = (
    "TIT_IRREVERSIBLE_QUALIFICATION_GRANTS_ENABLED"
)


class QualificationAwardGateConfigurationError(RuntimeError):
    """The runtime gate is not one of the two exact supported values."""

    error_code = "IRREVERSIBLE_QUALIFICATION_AWARD_GATE_INVALID"

    def __init__(self) -> None:
        super().__init__(self.error_code)


@dataclass(frozen=True)
class QualificationGrantDecision:
    grants_enabled: bool
    graduation_earned: bool
    gold_earned: bool


def irreversible_qualification_grants_enabled(
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Return the exact runtime switch, defaulting to a fail-closed ``false``."""

    values = os.environ if environ is None else environ
    raw = values.get(QUALIFICATION_GRANTS_ENABLED_ENV, "false")
    if raw == "false":
        return False
    if raw == "true":
        return True
    raise QualificationAwardGateConfigurationError()


def resolve_irreversible_qualification_grants(
    *,
    previous_graduation_earned: bool,
    previous_gold_earned: bool,
    graduation_current: bool,
    gold_current: bool,
    grants_enabled: bool,
) -> QualificationGrantDecision:
    """Preserve earned facts while gating every new ``false -> true`` transition."""

    if not grants_enabled:
        return QualificationGrantDecision(
            grants_enabled=False,
            graduation_earned=bool(previous_graduation_earned),
            gold_earned=bool(previous_gold_earned),
        )

    gold_earned = bool(previous_gold_earned or gold_current)
    graduation_earned = bool(
        previous_graduation_earned or graduation_current or gold_earned
    )
    return QualificationGrantDecision(
        grants_enabled=True,
        graduation_earned=graduation_earned,
        gold_earned=gold_earned,
    )


__all__ = [
    "QUALIFICATION_GRANTS_ENABLED_ENV",
    "QualificationGrantDecision",
    "QualificationAwardGateConfigurationError",
    "irreversible_qualification_grants_enabled",
    "resolve_irreversible_qualification_grants",
]
