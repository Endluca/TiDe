"""Production construction for the complete DTS v2 Domain Projector matrix."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy.engine import Engine

from .dts_v2_course_domain_projector import DtsV2CourseDomainProjector
from .dts_v2_domain_processor_router import DtsV2DomainProcessorRouter
from .dts_v2_domain_worker import DtsV2DomainWorker
from .dts_v2_runtime_guard import (
    DtsV2PrimaryTransactionGuard,
    PostgresDtsV2PrimaryTransactionGuard,
)
from .dts_v2_reference_domain_projector import (
    DtsV2ComplaintCategoryDomainProjector,
    DtsV2LabelDomainProjector,
)
from .dts_v2_teacher_domain_projector import DtsV2TeacherDomainProjector
from .dts_v2_teacher_student_domain_projector import (
    DtsV2TeacherStudentDomainProjector,
)


_DEFAULT_RUNTIME_GUARD = PostgresDtsV2PrimaryTransactionGuard()


def build_dts_v2_domain_processor(
    *,
    cutover_coverage_identity: Mapping[str, Any],
) -> DtsV2DomainProcessorRouter:
    """Build all five owners; a partial production matrix is impossible."""

    return DtsV2DomainProcessorRouter(
        {
            "COURSE": DtsV2CourseDomainProjector(
                cutover_coverage_identity=cutover_coverage_identity
            ),
            "TEACHER": DtsV2TeacherDomainProjector(
                cutover_coverage_identity=cutover_coverage_identity
            ),
            "TEACHER_STUDENT": DtsV2TeacherStudentDomainProjector(
                cutover_coverage_identity=cutover_coverage_identity
            ),
            "LABEL": DtsV2LabelDomainProjector(
                cutover_coverage_identity=cutover_coverage_identity
            ),
            "COMPLAINT_CATEGORY": DtsV2ComplaintCategoryDomainProjector(
                cutover_coverage_identity=cutover_coverage_identity
            ),
        },
        require_complete_matrix=True,
    )


def build_dts_v2_domain_worker(
    bind: Engine,
    *,
    worker_id: str,
    cutover_coverage_identity: Mapping[str, Any],
    primary_guard: DtsV2PrimaryTransactionGuard | None = (
        _DEFAULT_RUNTIME_GUARD
    ),
    lease_seconds: int = 120,
) -> DtsV2DomainWorker:
    return DtsV2DomainWorker(
        bind,
        worker_id=worker_id,
        processor=build_dts_v2_domain_processor(
            cutover_coverage_identity=cutover_coverage_identity
        ),
        primary_guard=primary_guard,
        lease_seconds=lease_seconds,
    )


__all__ = [
    "build_dts_v2_domain_processor",
    "build_dts_v2_domain_worker",
]
