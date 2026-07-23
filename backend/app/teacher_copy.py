from __future__ import annotations

import re


_HAN_CHARACTER = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def contains_han(value: object) -> bool:
    """Return whether teacher-facing copy contains a Han character."""

    return bool(_HAN_CHARACTER.search(str(value or "")))


def require_english_teacher_copy(value: str, *, field_name: str) -> str:
    """Fail closed when teacher-facing English copy contains Chinese text."""

    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} is required")
    if contains_han(normalized):
        raise ValueError(f"{field_name} must be English teacher-facing copy")
    return normalized


__all__ = ["contains_han", "require_english_teacher_copy"]
