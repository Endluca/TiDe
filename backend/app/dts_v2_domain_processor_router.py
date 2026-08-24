"""Strict dispatch for the five DTS v2 Domain Projector dirty-key types."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy.engine import Connection

from .dts_v2_dirty_queue_store import DirtyClaimV2
from .dts_v2_domain_worker import DtsV2DomainProcessor


DOMAIN_PROCESSOR_KEY_TYPES = frozenset(
    {"COURSE", "TEACHER", "TEACHER_STUDENT", "LABEL", "COMPLAINT_CATEGORY"}
)


class DtsV2DomainProcessorRouterError(RuntimeError):
    """The runtime processor matrix is incomplete or ambiguous."""


class DtsV2DomainProcessorRouter:
    """Dispatch one claim without silently ignoring an unowned key type."""

    def __init__(
        self,
        processors: Mapping[str, DtsV2DomainProcessor],
        *,
        require_complete_matrix: bool = True,
    ) -> None:
        if not isinstance(processors, Mapping):
            raise DtsV2DomainProcessorRouterError(
                "DTS_V2_DOMAIN_PROCESSOR_MATRIX_INVALID"
            )
        keys = frozenset(processors)
        if not keys or not keys.issubset(DOMAIN_PROCESSOR_KEY_TYPES):
            raise DtsV2DomainProcessorRouterError(
                "DTS_V2_DOMAIN_PROCESSOR_MATRIX_INVALID"
            )
        if require_complete_matrix and keys != DOMAIN_PROCESSOR_KEY_TYPES:
            missing = ",".join(sorted(DOMAIN_PROCESSOR_KEY_TYPES - keys))
            raise DtsV2DomainProcessorRouterError(
                f"DTS_V2_DOMAIN_PROCESSOR_MATRIX_INCOMPLETE:{missing}"
            )
        self._processors = dict(processors)

    def process_claim(
        self,
        connection: Connection,
        claim: DirtyClaimV2,
    ) -> Mapping[str, int]:
        key_type = claim.key.key_type
        processor = self._processors.get(key_type)
        if processor is None:
            raise DtsV2DomainProcessorRouterError(
                f"DTS_V2_DOMAIN_PROCESSOR_UNAVAILABLE:{key_type}"
            )
        result = processor.process_claim(connection, claim)
        if not isinstance(result, Mapping):
            raise DtsV2DomainProcessorRouterError(
                "DTS_V2_DOMAIN_PROCESSOR_RESULT_INVALID"
            )
        return {
            f"{key_type.lower()}.{name}": count
            for name, count in result.items()
        }


__all__ = [
    "DOMAIN_PROCESSOR_KEY_TYPES",
    "DtsV2DomainProcessorRouter",
    "DtsV2DomainProcessorRouterError",
]
