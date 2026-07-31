from __future__ import annotations

from typing import Any


# These public objects are intentionally present in the same logical database
# but have a different migration owner.  TiDe may consume them through a
# contract; Alembic must not propose dropping or altering them.
EXTERNAL_TABLES = frozenset(
    {
        ("public", "teacher_support_tickets"),
    }
)


def _table_identity(value: Any) -> tuple[str, str] | None:
    if value is None:
        return None
    name = getattr(value, "name", None)
    if not isinstance(name, str):
        return None
    schema = getattr(value, "schema", None) or "public"
    return str(schema), name


def include_owned_object(
    obj: Any,
    name: str | None,
    type_: str,
    reflected: bool,
    compare_to: Any,
) -> bool:
    del name, reflected, compare_to
    table = obj if type_ == "table" else getattr(obj, "table", None)
    return _table_identity(table) not in EXTERNAL_TABLES

