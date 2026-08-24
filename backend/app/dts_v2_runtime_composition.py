"""Fail-closed production composition for DTS v2 runtime processes.

This module deliberately separates capability validation from construction.
No process may claim work until its exact database role, protected command
surface, and health snapshot function have been proved.  Production COURSE
construction always selects the protected command client; an injected
materializer exists only for isolated tests.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from .dts_v2_completion_conflict_outbox_processor import (
    DtsV2CompletionConflictOutboxProcessor,
)
from .dts_v2_course_outbox_processor import (
    DtsV2CourseMaterializer,
    DtsV2CourseOutboxProcessor,
)
from .dts_v2_course_materialization_store import (
    COURSE_MATERIALIZE_REGPROCEDURE,
    PostgresDtsV2ProtectedCourseMaterializer,
)
from .dts_v2_domain_runtime import build_dts_v2_domain_worker
from .dts_v2_domain_worker import DtsV2DomainWorker
from .dts_v2_non_task_output_store import (
    NON_TASK_OUTPUT_REGPROCEDURE,
    PostgresDtsV2NonTaskOutputStore,
)
from .dts_v2_favorite_runtime import (
    DtsV2FavoriteObservationWorker,
    PostgresDtsV2FavoriteMaterializer,
)
from .dts_v2_outbox_processor_router import DtsV2OutboxProcessorRouter
from .dts_v2_outbox_worker import DtsV2OutboxWorker
from .dts_v2_runtime_guard import PostgresDtsV2PrimaryTransactionGuard
from .dts_v2_score_projection_store import PostgresDtsV2ScoreProjectionStore
from .dts_v2_task_plan_outbox_processor import DtsV2TaskPlanOutboxProcessor
from .dts_v2_teacher_materializer import PostgresDtsV2TeacherMaterializer
from .dts_v2_teacher_outbox_processor import DtsV2TeacherOutboxProcessor
from .dts_v2_teacher_time_recheck import TEACHER_TIME_RECHECK_CAPABILITIES
from .dts_v2_teacher_student_outbox_processor import (
    DtsV2TeacherStudentOutboxProcessor,
)
from .dts_v2_technical_cases import DtsV2OutboxTechnicalCaseStore
from .dts_v2_trigger_match_store import PostgresDtsV2TriggerMatchStore
from .dts_v2_validated_outbox_processor import DtsV2ValidatedOutboxProcessor


DOMAIN_COMPONENT = "domain"
OUTBOX_COMPONENT = "outbox"
FAVORITE_COMPONENT = "favorite"
RUNTIME_COMPONENTS = frozenset(
    {DOMAIN_COMPONENT, OUTBOX_COMPONENT, FAVORITE_COMPONENT}
)
RUNTIME_ROLES = {
    DOMAIN_COMPONENT: "tit_growth_app",
    OUTBOX_COMPONENT: "tit_growth_app",
    FAVORITE_COMPONENT: "tit_growth_app",
}
HEALTH_REGPROCEDURES = {
    DOMAIN_COMPONENT: "public.dts_v2_domain_runtime_health_v1(bigint)",
    OUTBOX_COMPONENT: "public.dts_v2_outbox_runtime_health_v1(bigint)",
    FAVORITE_COMPONENT: "public.dts_v2_favorite_runtime_health_v1(bigint)",
}
HEALTH_PROTOCOLS = {
    DOMAIN_COMPONENT: "dts-v2-domain-runtime-health-v1",
    OUTBOX_COMPONENT: "dts-v2-outbox-runtime-health-v1",
    FAVORITE_COMPONENT: "dts-v2-favorite-runtime-health-v1",
}

_COMMON = (
    "public.dts_v2_runtime_primary_guard_v1(text)",
    "public.dts_canonical_json_sha256_v1(jsonb)",
)
RUNTIME_CAPABILITIES: Mapping[str, tuple[str, ...]] = {
    DOMAIN_COMPONENT: _COMMON
    + (
        "public.claim_domain_dirty_keys_v2(text,integer,integer)",
        "public.renew_domain_dirty_key_v2(text,text,text,text,text,bigint,integer)",
        "public.complete_domain_dirty_key_v2(text,text,text,text,text,bigint,bigint)",
        "public.wait_domain_dirty_key_v2(text,text,text,text,text,bigint,bigint,jsonb)",
        "public.fail_domain_dirty_key_v2(text,text,text,text,text,bigint,bigint,text)",
        "public.reap_expired_domain_dirty_keys_v2(integer)",
        "public.publish_domain_aggregate_revision_v2("
        "text,jsonb,jsonb,text,jsonb,bigint,jsonb,text,jsonb)",
        "public.enqueue_complaint_category_course_fanout_v2("
        "text,bigint,text,text)",
        "public.dts_v2_complaint_catalog_health_v1()",
        HEALTH_REGPROCEDURES[DOMAIN_COMPONENT],
    ),
    OUTBOX_COMPONENT: _COMMON
    + (
        # The protected COURSE compatibility/component command is a hard
        # startup gate; the legacy direct-DML client is never auto-selected.
        COURSE_MATERIALIZE_REGPROCEDURE,
        NON_TASK_OUTPUT_REGPROCEDURE,
        "public.reconcile_course_trigger_matches_v2("
        "text,text,bigint,bigint,text,jsonb,text)",
        "public.rebuild_lesson_score_result_v2(text,text,bigint)",
        "public.teacher_score_projection_vector_v2(text)",
        "public.rebuild_teacher_score_and_qualification_v2(text,jsonb,bigint)",
        "public.materialize_teacher_source_wide_v2(jsonb,bigint,bigint,bigint,text)",
        "public.materialize_favorite_observation_v2("
        "text,text,text,text,text,text,integer,timestamp with time zone,"
        "text,bigint,text)",
        "public.reconcile_completion_conflict_case_v2(text,text,bigint,text)",
        "public.materialize_task_plan_v2(text,text,bigint,text)",
        "public.reconcile_blacklist_threshold_v2("
        "text,text,bigint,bigint,text,jsonb)",
        "public.record_dts_v2_technical_case("
        "text,text,text,text,text,text,text,text,text,bigint,text,"
        "integer,bigint)",
        "public.record_dts_v2_technical_case_recovery(text,text,text,bigint)",
        *TEACHER_TIME_RECHECK_CAPABILITIES,
        HEALTH_REGPROCEDURES[OUTBOX_COMPONENT],
    ),
    FAVORITE_COMPONENT: _COMMON
    + (
        "public.claim_favorite_observations_v2(text,integer)",
        "public.heartbeat_favorite_observation_v2("
        "text,text,bigint,text,text,bigint,bigint)",
        "public.complete_favorite_observation_v2("
        "text,text,bigint,text,text,bigint,bigint,text,text,boolean,"
        "text,text,text)",
        "public.fail_favorite_observation_v2("
        "text,text,bigint,text,text,bigint,bigint,text)",
        "public.reap_expired_favorite_observations_v2(integer)",
        "public.favorite_observation_evidence_fingerprint_v1(text,text,text)",
        "public.rebuild_lesson_score_result_v2(text,text,bigint)",
        "public.teacher_score_projection_vector_v2(text)",
        "public.rebuild_teacher_score_and_qualification_v2(text,jsonb,bigint)",
        HEALTH_REGPROCEDURES[FAVORITE_COMPONENT],
    ),
}

_IDENTITY_SQL = text(
    """
    SELECT current_database() AS database_name,current_user::text AS current_role,
           session_user::text AS session_role,role.rolcanlogin,
           role.rolinherit,role.rolsuper,role.rolcreatedb,role.rolcreaterole,
           role.rolreplication,role.rolbypassrls,
           has_schema_privilege(current_user,'public','CREATE') AS public_create,
           has_table_privilege(
             current_user,'public.dts_pipeline_control','SELECT'
           ) AS pipeline_select
    FROM pg_roles role WHERE role.rolname=session_user
    """
)


class DtsV2RuntimeCompositionError(RuntimeError):
    """A production runtime boundary cannot be proved."""


@dataclass(frozen=True)
class DtsV2RuntimeHealthSnapshot:
    protocol_version: str
    mode: str
    projection_generation: int
    runnable_count: int
    active_lease_count: int
    expired_lease_count: int
    business_wait_count: int
    dead_count: int
    stale_runnable_count: int
    oldest_runnable_age_seconds: int | None
    oldest_active_lease_age_seconds: int | None

    @property
    def ready(self) -> bool:
        return (
            self.expired_lease_count == 0
            and self.dead_count == 0
            and self.stale_runnable_count == 0
        )


def validate_runtime_startup(
    connection: Connection,
    *,
    component: str,
    expected_database: str,
) -> None:
    _component(component)
    if not expected_database or expected_database.strip() != expected_database:
        _fail("DTS_V2_EXPECTED_DATABASE_INVALID")
    row = connection.execute(_IDENTITY_SQL).mappings().one_or_none()
    if row is None:
        _fail("DTS_V2_RUNTIME_LOGIN_ROLE_NOT_FOUND")
    if row.get("database_name") != expected_database:
        _fail("DTS_V2_RUNTIME_DATABASE_IDENTITY_MISMATCH")
    if (
        row.get("current_role") != row.get("session_role")
        or row.get("session_role") != RUNTIME_ROLES[component]
    ):
        _fail("DTS_V2_RUNTIME_LOGIN_ROLE_MISMATCH")
    if (
        row.get("rolcanlogin") is not True
        or row.get("rolinherit") is not False
        or any(
            row.get(name) is True
            for name in (
                "rolsuper",
                "rolcreatedb",
                "rolcreaterole",
                "rolreplication",
                "rolbypassrls",
                "public_create",
                "pipeline_select",
            )
        )
    ):
        _fail("DTS_V2_RUNTIME_ROLE_PRIVILEGES_INVALID")

    missing: list[str] = []
    forbidden: list[str] = []
    for signature in RUNTIME_CAPABILITIES[component]:
        capability = connection.execute(
            text(
                "SELECT to_regprocedure(:signature) AS oid,"
                "CASE WHEN to_regprocedure(:signature) IS NULL THEN false "
                "ELSE has_function_privilege("
                "current_user,to_regprocedure(:signature),'EXECUTE') END "
                "AS executable"
            ),
            {"signature": signature},
        ).mappings().one()
        if capability.get("oid") is None:
            missing.append(signature)
        elif capability.get("executable") is not True:
            forbidden.append(signature)
    if missing:
        _fail("DTS_V2_RUNTIME_CAPABILITY_MISSING:" + ",".join(missing))
    if forbidden:
        _fail("DTS_V2_RUNTIME_CAPABILITY_FORBIDDEN:" + ",".join(forbidden))


def read_runtime_health(
    connection: Connection,
    *,
    component: str,
    stale_after_seconds: int,
) -> DtsV2RuntimeHealthSnapshot:
    _component(component)
    if type(stale_after_seconds) is not int or not 1 <= stale_after_seconds <= 86400:
        _fail("DTS_V2_RUNTIME_HEALTH_THRESHOLD_INVALID")
    sql = {
        DOMAIN_COMPONENT: (
            "SELECT public.dts_v2_domain_runtime_health_v1(:threshold)"
        ),
        OUTBOX_COMPONENT: (
            "SELECT public.dts_v2_outbox_runtime_health_v1(:threshold)"
        ),
        FAVORITE_COMPONENT: (
            "SELECT public.dts_v2_favorite_runtime_health_v1(:threshold)"
        ),
    }[component]
    value = connection.execute(
        text(sql), {"threshold": stale_after_seconds}
    ).scalar_one()
    return _health_snapshot(value, component=component)


def build_domain_worker(
    bind: Engine,
    *,
    worker_id: str,
    cutover_coverage_identity: Mapping[str, Any],
    lease_seconds: int,
) -> DtsV2DomainWorker:
    return build_dts_v2_domain_worker(
        bind,
        worker_id=worker_id,
        cutover_coverage_identity=cutover_coverage_identity,
        primary_guard=PostgresDtsV2PrimaryTransactionGuard(),
        lease_seconds=lease_seconds,
    )


def build_outbox_worker(
    bind: Engine,
    *,
    course_materializer: DtsV2CourseMaterializer | None = None,
) -> DtsV2OutboxWorker:
    guard = PostgresDtsV2PrimaryTransactionGuard()
    score_store = PostgresDtsV2ScoreProjectionStore(
        require_guarded_generation=True
    )
    if course_materializer is None:
        course_materializer = PostgresDtsV2ProtectedCourseMaterializer(
            score_refresher=score_store,
            trigger_match_store=PostgresDtsV2TriggerMatchStore(),
            non_task_output_store=PostgresDtsV2NonTaskOutputStore(),
            primary_guard=guard,
        )
    validated = DtsV2ValidatedOutboxProcessor()
    router = DtsV2OutboxProcessorRouter(
        {
            "COURSE": DtsV2CourseOutboxProcessor(
                materializer=course_materializer
            ),
            "PARTICIPATION": validated,
            "LABEL": validated,
            "COMPLAINT_CATEGORY": validated,
            "SOURCE_SCOPE": validated,
            "TEACHER": DtsV2TeacherOutboxProcessor(
                materializer=PostgresDtsV2TeacherMaterializer(
                    score_refresher=score_store,
                    primary_guard=guard,
                )
            ),
            "TEACHER_STUDENT": DtsV2TeacherStudentOutboxProcessor(
                materializer=PostgresDtsV2FavoriteMaterializer(
                    require_guarded_generation=True
                )
            ),
            "COMPLETION_CONFLICT": (
                DtsV2CompletionConflictOutboxProcessor()
            ),
            "TASK_PLAN": DtsV2TaskPlanOutboxProcessor(),
        },
        require_complete_matrix=True,
    )
    return DtsV2OutboxWorker(
        bind,
        processor=router,
        technical_cases=DtsV2OutboxTechnicalCaseStore(),
        primary_guard=guard,
    )


def build_favorite_worker(
    bind: Engine,
) -> DtsV2FavoriteObservationWorker:
    return DtsV2FavoriteObservationWorker(
        bind,
        projection_rebuilder=PostgresDtsV2ScoreProjectionStore(
            require_guarded_generation=True
        ),
        primary_guard=PostgresDtsV2PrimaryTransactionGuard(),
    )


def _health_snapshot(
    value: Any,
    *,
    component: str,
) -> DtsV2RuntimeHealthSnapshot:
    fields = tuple(DtsV2RuntimeHealthSnapshot.__dataclass_fields__)
    if not isinstance(value, Mapping) or set(value) != set(fields):
        _fail("DTS_V2_RUNTIME_HEALTH_SHAPE_INVALID")
    payload = dict(value)
    if payload.get("protocol_version") != HEALTH_PROTOCOLS[component]:
        _fail("DTS_V2_RUNTIME_HEALTH_PROTOCOL_INVALID")
    if payload.get("mode") not in {
        "V1_COMPAT_DUAL_CAPTURE",
        "V2_PRIMARY",
        "ROLLED_BACK",
    }:
        _fail("DTS_V2_RUNTIME_HEALTH_MODE_INVALID")
    for name in (
        "projection_generation",
        "runnable_count",
        "active_lease_count",
        "expired_lease_count",
        "business_wait_count",
        "dead_count",
        "stale_runnable_count",
    ):
        minimum = 0
        if type(payload.get(name)) is not int or payload[name] < minimum:
            _fail("DTS_V2_RUNTIME_HEALTH_COUNT_INVALID")
    for name in (
        "oldest_runnable_age_seconds",
        "oldest_active_lease_age_seconds",
    ):
        if payload.get(name) is not None and (
            type(payload[name]) is not int or payload[name] < 0
        ):
            _fail("DTS_V2_RUNTIME_HEALTH_AGE_INVALID")
    return DtsV2RuntimeHealthSnapshot(**payload)


def runtime_health_ready(
    snapshot: DtsV2RuntimeHealthSnapshot,
    *,
    component: str,
) -> bool:
    _component(component)
    if not isinstance(snapshot, DtsV2RuntimeHealthSnapshot):
        _fail("DTS_V2_RUNTIME_HEALTH_SNAPSHOT_REQUIRED")
    if component == DOMAIN_COMPONENT:
        return snapshot.ready
    return (
        snapshot.mode == "V2_PRIMARY"
        and snapshot.projection_generation >= 1
        and snapshot.ready
    )


def _component(value: str) -> None:
    if value not in RUNTIME_COMPONENTS:
        _fail("DTS_V2_RUNTIME_COMPONENT_INVALID")


def _fail(code: str) -> None:
    raise DtsV2RuntimeCompositionError(code)


__all__ = [
    "DOMAIN_COMPONENT",
    "FAVORITE_COMPONENT",
    "HEALTH_REGPROCEDURES",
    "OUTBOX_COMPONENT",
    "RUNTIME_CAPABILITIES",
    "RUNTIME_COMPONENTS",
    "RUNTIME_ROLES",
    "DtsV2RuntimeCompositionError",
    "DtsV2RuntimeHealthSnapshot",
    "build_domain_worker",
    "build_favorite_worker",
    "build_outbox_worker",
    "read_runtime_health",
    "runtime_health_ready",
    "validate_runtime_startup",
]
