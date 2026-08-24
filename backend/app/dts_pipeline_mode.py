"""Read the database-owned DTS projection mode without inventing a default.

Installations before the DTS v2 control migration have no control table and
therefore continue to use the legacy workers.  Once the table exists, a
missing row or an unknown mode is a deployment error and must fail closed.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text


DTS_V2_PIPELINE_MODES = frozenset(
    {"V1_COMPAT_DUAL_CAPTURE", "V2_PRIMARY", "ROLLED_BACK"}
)


def read_optional_dts_pipeline_mode(connection: Any) -> str | None:
    relation = connection.scalar(
        text("SELECT to_regclass('public.dts_pipeline_control')")
    )
    if relation is None:
        return None
    mode = connection.scalar(
        text(
            "SELECT mode FROM public.dts_pipeline_control "
            "WHERE control_id='PRIMARY'"
        )
    )
    if mode not in DTS_V2_PIPELINE_MODES:
        raise RuntimeError("DTS_PIPELINE_CONTROL_MODE_INVALID")
    return str(mode)


__all__ = ["DTS_V2_PIPELINE_MODES", "read_optional_dts_pipeline_mode"]
