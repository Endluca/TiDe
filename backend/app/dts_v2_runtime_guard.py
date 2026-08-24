"""Transaction-local cutover guard shared by production DTS v2 workers.

The PostgreSQL function is intentionally SECURITY DEFINER.  Runtime roles do
not need (and must not receive) direct SELECT on ``dts_pipeline_control``.
The function takes the cutover shared advisory xact lock, verifies the exact
PRIMARY singleton and exposes the locked mode/generation through
transaction-local GUCs. Domain projection is allowed in every legal mode so
shadow facts keep catching up; production materializers remain PRIMARY-only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.engine import Connection


PRIMARY_GUARD_REGPROCEDURE = (
    "public.dts_v2_runtime_primary_guard_v1(text)"
)
PRIMARY_GUARD_COMPONENTS = frozenset(
    {
        "DOMAIN",
        "OUTBOX",
        "FAVORITE",
        "COURSE",
        "TEACHER",
        "TEACHER_STUDENT",
        "COMPLETION_CONFLICT",
        "TASK_PLAN",
        "SCORE",
    }
)
PIPELINE_MODES = frozenset(
    {"V1_COMPAT_DUAL_CAPTURE", "V2_PRIMARY", "ROLLED_BACK"}
)


@dataclass(frozen=True)
class DtsV2RuntimeTransactionState:
    mode: str
    projection_generation: int


class DtsV2RuntimeGuardError(RuntimeError):
    """The transaction cannot prove one locked V2_PRIMARY generation."""


class DtsV2RuntimeTransactionGuard(Protocol):
    def acquire(
        self,
        connection: Connection,
        *,
        component: str,
    ) -> int | None: ...


# Source-compatible alias used by materializers built before the guard was
# widened to support shadow DOMAIN transactions.
DtsV2PrimaryTransactionGuard = DtsV2RuntimeTransactionGuard


class PostgresDtsV2PrimaryTransactionGuard:
    """Call the protected guard and verify its transaction-local readback."""

    def acquire(
        self,
        connection: Connection,
        *,
        component: str,
    ) -> int | None:
        if component not in PRIMARY_GUARD_COMPONENTS:
            raise DtsV2RuntimeGuardError(
                "DTS_V2_RUNTIME_GUARD_COMPONENT_INVALID"
            )
        generation = connection.execute(
            text(
                "SELECT public.dts_v2_runtime_primary_guard_v1("
                ":component)"
            ),
            {"component": component},
        ).scalar_one_or_none()
        mode = connection.execute(
            text(
                "SELECT NULLIF(current_setting("
                "'tit.dts_v2_mode',true),'')"
            )
        ).scalar_one_or_none()
        if mode not in PIPELINE_MODES:
            raise DtsV2RuntimeGuardError(
                "DTS_V2_RUNTIME_GUARD_MODE_READBACK_INVALID"
            )
        if generation is None:
            if component == "DOMAIN":
                raise DtsV2RuntimeGuardError(
                    "DTS_V2_RUNTIME_GUARD_DOMAIN_STATE_REQUIRED"
                )
            return None
        minimum_generation = 0 if component == "DOMAIN" else 1
        if type(generation) is not int or generation < minimum_generation:
            raise DtsV2RuntimeGuardError(
                "DTS_V2_RUNTIME_GUARD_GENERATION_INVALID"
            )
        readback = connection.execute(
            text(
                "SELECT NULLIF(current_setting("
                "'tit.dts_v2_projection_generation',true),'')::bigint"
            )
        ).scalar_one_or_none()
        if readback != generation:
            raise DtsV2RuntimeGuardError(
                "DTS_V2_RUNTIME_GUARD_GENERATION_READBACK_MISMATCH"
            )
        return generation


def guarded_runtime_state(
    connection: Connection,
) -> DtsV2RuntimeTransactionState:
    """Read the exact cutover state proven in this transaction."""

    mode, generation = connection.execute(
        text(
            "SELECT NULLIF(current_setting('tit.dts_v2_mode',true),''),"
            "NULLIF(current_setting("
            "'tit.dts_v2_projection_generation',true),'')::bigint"
        )
    ).one()
    if (
        mode not in PIPELINE_MODES
        or type(generation) is not int
        or generation < 0
    ):
        raise DtsV2RuntimeGuardError(
            "DTS_V2_RUNTIME_GUARDED_STATE_REQUIRED"
        )
    return DtsV2RuntimeTransactionState(
        mode=str(mode),
        projection_generation=generation,
    )


def guarded_projection_generation(connection: Connection) -> int:
    """Read the generation proven by the guard in this same transaction."""

    generation = connection.execute(
        text(
            "SELECT NULLIF(current_setting("
            "'tit.dts_v2_projection_generation',true),'')::bigint"
        )
    ).scalar_one_or_none()
    if type(generation) is not int or generation < 1:
        raise DtsV2RuntimeGuardError(
            "DTS_V2_RUNTIME_GUARDED_GENERATION_REQUIRED"
        )
    return generation


__all__ = [
    "DtsV2PrimaryTransactionGuard",
    "DtsV2RuntimeTransactionGuard",
    "DtsV2RuntimeTransactionState",
    "DtsV2RuntimeGuardError",
    "PRIMARY_GUARD_COMPONENTS",
    "PRIMARY_GUARD_REGPROCEDURE",
    "PostgresDtsV2PrimaryTransactionGuard",
    "guarded_runtime_state",
    "guarded_projection_generation",
]
