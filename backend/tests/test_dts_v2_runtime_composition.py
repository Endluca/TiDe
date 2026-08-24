from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.dts_v2_course_materialization_store import (
    PostgresDtsV2ProtectedCourseMaterializer,
)
from app.dts_v2_runtime_composition import (
    DOMAIN_COMPONENT,
    FAVORITE_COMPONENT,
    OUTBOX_COMPONENT,
    RUNTIME_CAPABILITIES,
    DtsV2RuntimeCompositionError,
    _health_snapshot,
    build_outbox_worker,
    runtime_health_ready,
    validate_runtime_startup,
)
from app.dts_v2_teacher_time_recheck import TEACHER_TIME_RECHECK_CAPABILITIES


class _Mappings:
    def __init__(self, value):
        self.value = value

    def one_or_none(self):
        return self.value

    def one(self):
        return self.value


class _Result:
    def __init__(self, value):
        self.value = value

    def mappings(self):
        return _Mappings(self.value)


class _Connection:
    def __init__(self, *, missing: str | None = None):
        self.missing = missing

    def execute(self, statement, parameters=None):
        sql = str(statement)
        if "FROM pg_roles" in sql:
            return _Result(
                {
                    "database_name": "tit_growth",
                    "current_role": "tit_growth_app",
                    "session_role": "tit_growth_app",
                    "rolcanlogin": True,
                    "rolinherit": False,
                    "rolsuper": False,
                    "rolcreatedb": False,
                    "rolcreaterole": False,
                    "rolreplication": False,
                    "rolbypassrls": False,
                    "public_create": False,
                    "pipeline_select": False,
                }
            )
        signature = parameters["signature"]
        return _Result(
            {
                "oid": None if signature == self.missing else 1,
                "executable": signature != self.missing,
            }
        )


def _health(mode: str, generation: int):
    return _health_snapshot(
        {
            "protocol_version": "dts-v2-domain-runtime-health-v1",
            "mode": mode,
            "projection_generation": generation,
            "runnable_count": 2,
            "active_lease_count": 1,
            "expired_lease_count": 0,
            "business_wait_count": 3,
            "dead_count": 0,
            "stale_runnable_count": 0,
            "oldest_runnable_age_seconds": 4,
            "oldest_active_lease_age_seconds": 2,
        },
        component=DOMAIN_COMPONENT,
    )


def test_capability_registry_is_complete_and_missing_function_fails_closed() -> None:
    assert set(RUNTIME_CAPABILITIES) == {
        DOMAIN_COMPONENT,
        OUTBOX_COMPONENT,
        FAVORITE_COMPONENT,
    }
    missing = RUNTIME_CAPABILITIES[DOMAIN_COMPONENT][-1]
    with pytest.raises(
        DtsV2RuntimeCompositionError,
        match="DTS_V2_RUNTIME_CAPABILITY_MISSING",
    ):
        validate_runtime_startup(
            _Connection(missing=missing),
            component=DOMAIN_COMPONENT,
            expected_database="tit_growth",
        )


def test_outbox_startup_requires_teacher_time_recheck_capabilities() -> None:
    assert set(TEACHER_TIME_RECHECK_CAPABILITIES).issubset(
        RUNTIME_CAPABILITIES[OUTBOX_COMPONENT]
    )


def test_domain_shadow_health_is_ready_but_materializers_require_primary() -> None:
    shadow = _health("V1_COMPAT_DUAL_CAPTURE", 0)
    assert runtime_health_ready(shadow, component=DOMAIN_COMPONENT) is True
    assert runtime_health_ready(shadow, component=OUTBOX_COMPONENT) is False
    assert runtime_health_ready(shadow, component=FAVORITE_COMPONENT) is False


def test_production_outbox_never_falls_back_to_direct_course_dml() -> None:
    worker = build_outbox_worker(
        SimpleNamespace(dialect=SimpleNamespace(name="postgresql")),
    )
    course = worker.processor._processors["COURSE"]
    assert isinstance(
        course.materializer,
        PostgresDtsV2ProtectedCourseMaterializer,
    )
