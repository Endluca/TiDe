from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.dts_v2_dirty_queue_store import DirtyClaimV2, DirtyKeyV2
from app.dts_v2_domain_processor_router import (
    DOMAIN_PROCESSOR_KEY_TYPES,
    DtsV2DomainProcessorRouter,
    DtsV2DomainProcessorRouterError,
)


@dataclass
class _Processor:
    name: str

    def process_claim(self, connection: object, claim: DirtyClaimV2):
        return {self.name: 1}


def _claim(key_type: str) -> DirtyClaimV2:
    key_part_2 = "student" if key_type == "TEACHER_STUDENT" else ""
    return DirtyClaimV2(
        key=DirtyKeyV2("dom", key_type, "1", key_part_2),
        lease_token="lease",
        claimed_work_revision=1,
        row_version=1,
    )


def test_complete_matrix_dispatches_and_namespaces_counts() -> None:
    router = DtsV2DomainProcessorRouter(
        {key: _Processor(key.lower()) for key in DOMAIN_PROCESSOR_KEY_TYPES}
    )

    assert router.process_claim(object(), _claim("COURSE")) == {
        "course.course": 1
    }


def test_production_matrix_cannot_start_with_missing_processor() -> None:
    with pytest.raises(
        DtsV2DomainProcessorRouterError,
        match="MATRIX_INCOMPLETE:COMPLAINT_CATEGORY,LABEL,TEACHER,TEACHER_STUDENT",
    ):
        DtsV2DomainProcessorRouter({"COURSE": _Processor("course")})


def test_shadow_test_mode_still_fails_if_unregistered_key_is_claimed() -> None:
    router = DtsV2DomainProcessorRouter(
        {"COURSE": _Processor("course")},
        require_complete_matrix=False,
    )

    with pytest.raises(
        DtsV2DomainProcessorRouterError,
        match="PROCESSOR_UNAVAILABLE:TEACHER",
    ):
        router.process_claim(object(), _claim("TEACHER"))
