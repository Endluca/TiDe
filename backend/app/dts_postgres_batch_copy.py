"""Low-overhead PostgreSQL COPY helpers for transaction-bound DTS batches.

The DTS consumer already owns one SQLAlchemy/psycopg transaction.  Feeding
typed rows through psycopg COPY avoids serializing a multi-megabyte batch to a
second JSON document only for PostgreSQL to parse it back into records.  The
helper deliberately returns ``False`` for non-psycopg test doubles so unit
tests and alternate diagnostic connections retain the existing SQL fallback.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from psycopg.types.json import Jsonb
from sqlalchemy.engine import Connection


def jsonb(value: Any) -> Jsonb:
    """Adapt one already-validated Python JSON value for PostgreSQL COPY."""

    return Jsonb(value)


def copy_rows(
    connection: Connection,
    *,
    statement: str,
    rows: Iterable[Sequence[Any]],
) -> bool:
    """COPY typed rows on the caller's existing transaction when possible."""

    connection_fairy = getattr(connection, "connection", None)
    driver_connection = getattr(
        connection_fairy,
        "driver_connection",
        None,
    )
    if driver_connection is None:
        return False
    module_name = type(driver_connection).__module__
    if not module_name.startswith("psycopg"):
        return False

    with driver_connection.cursor().copy(statement) as copy:
        for row in rows:
            copy.write_row(tuple(row))
    return True
