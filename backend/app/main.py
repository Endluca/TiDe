from __future__ import annotations

import logging
import os
import time
from typing import Optional

from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .auth import OperatorIdentity, auth_router, require_roles
from .auth_models import OperatorRole
from .audit_service import AuditService
from .config_routes import router as config_router
from .database import database_health, database_pool_status, engine
from .errors import DomainError
from .frontend_static import install_frontend_static
from .operations_routes import router as operations_router
from .score_read_service import ScoreReadModelNotFound, ScoreReadService
from .support_ticket_routes import router as support_ticket_router
from .task_routes import router as task_router
from .teacher_read_service import DashboardReadService, TeacherReadService
from .runtime_settings import (
    allowed_hosts,
    allowed_origins,
    is_production,
    validate_production_runtime,
)


validate_production_runtime()
logger = logging.getLogger("tit_growth.performance")


def _slow_request_threshold_ms() -> int:
    raw = os.getenv("TIT_SLOW_REQUEST_MS", "1000").strip()
    try:
        value = int(raw)
    except ValueError:
        value = 1000
    return max(100, min(value, 60_000))


SLOW_REQUEST_THRESHOLD_MS = _slow_request_threshold_ms()
app = FastAPI(
    title="TIT Growth System Operational API",
    version="current",
    description="Current local API for shared tasks, scoring, operational views, outputs and audit.",
    docs_url=None if is_production() else "/docs",
    redoc_url=None if is_production() else "/redoc",
    openapi_url=None if is_production() else "/openapi.json",
)
if configured_hosts := allowed_hosts():
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=list(configured_hosts),
    )
if configured_origins := allowed_origins():
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(configured_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
app.add_middleware(GZipMiddleware, minimum_size=512, compresslevel=5)
app.include_router(auth_router)
app.include_router(config_router)
app.include_router(task_router)
app.include_router(operations_router)
app.include_router(support_ticket_router)

score_read_service = ScoreReadService(engine)
teacher_read_service = TeacherReadService(engine)
dashboard_read_service = DashboardReadService(engine)
audit_service = AuditService(engine)


@app.middleware("http")
async def add_api_security_headers(request: Request, call_next):
    started_at = time.perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
    finally:
        duration_ms = (time.perf_counter() - started_at) * 1000
        if (
            request.url.path.startswith("/api/")
            and duration_ms >= SLOW_REQUEST_THRESHOLD_MS
        ):
            route = request.scope.get("route")
            route_path = getattr(route, "path", "unmatched")
            pool = database_pool_status()
            logger.warning(
                "slow_api_request method=%s route=%s status=%s "
                "duration_ms=%.1f pool_checked_out=%s pool_overflow=%s",
                request.method,
                route_path,
                status_code,
                duration_ms,
                pool.get("checked_out", "unknown"),
                pool.get("overflow", "unknown"),
            )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = (
        "camera=(), microphone=(), geolocation=()"
    )
    development_docs = not is_production() and (
        request.url.path.startswith("/docs") or request.url.path == "/redoc"
    )
    if not development_docs:
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; base-uri 'self'; frame-ancestors 'none'; "
            "object-src 'none'; script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "font-src 'self' data:; connect-src 'self'"
        )
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    if is_production():
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )
    return response


@app.exception_handler(DomainError)
async def handle_domain_error(_, exc: DomainError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.response())


@app.exception_handler(SQLAlchemyTimeoutError)
async def handle_database_capacity_error(_, __: SQLAlchemyTimeoutError) -> JSONResponse:
    logger.warning("database_pool_timeout")
    return JSONResponse(
        status_code=503,
        headers={"Retry-After": "1"},
        content={
            "accepted": False,
            "error_code": "DATABASE_CAPACITY_EXCEEDED",
            "field_path": None,
            "retryable": True,
            "message_key": "system.error.busy",
            "details": {},
        },
    )


@app.exception_handler(RequestValidationError)
async def handle_request_validation_error(_, exc: RequestValidationError) -> JSONResponse:
    """Return the frozen teacher-safe error envelope without echoing inputs."""

    errors = exc.errors()
    location = list(errors[0].get("loc", ())) if errors else []
    if location and location[0] in {"body", "path", "query"}:
        location = location[1:]
    field_path = "$"
    for part in location:
        field_path += f"[{part}]" if isinstance(part, int) else f".{part}"
    return JSONResponse(
        status_code=422,
        content={
            "accepted": False,
            "error_code": "PAYLOAD_SCHEMA_INVALID",
            "field_path": field_path,
            "retryable": False,
            "message_key": "task.error.invalid_payload",
            "details": {"reason_code": "REQUEST_SCHEMA_REJECTED"},
        },
    )


@app.get("/api/health")
def health() -> dict:
    if is_production():
        return {
            "status": "ok",
            "database": {"status": database_health()["status"]},
        }
    data_mode_counts = teacher_read_service.data_mode_counts()
    modes = set(data_mode_counts)
    if modes and modes <= {"REAL"}:
        persistence_mode = "persistent_real"
    elif not modes or modes <= {"MOCK"}:
        persistence_mode = "persistent_mock"
    else:
        persistence_mode = "persistent_mixed"
    return {
        "status": "ok",
        "mode": persistence_mode,
        "database": database_health(),
        "data_mode_counts": data_mode_counts,
        "runtime": {"single_process_required": False},
    }


@app.get("/api/health/db")
def health_database() -> dict:
    result = database_health()
    return {"status": result["status"]} if is_production() else result


@app.get("/api/dashboard")
def dashboard(
    _operator: OperatorIdentity = Depends(require_roles(OperatorRole.VIEWER)),
) -> dict:
    return dashboard_read_service.dashboard()


@app.get("/api/teachers")
def list_teachers(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=24, ge=1, le=100),
    keyword: Optional[str] = Query(default=None),
    data_mode: Optional[str] = Query(default=None),
    employment_status: Optional[str] = Query(default=None),
    _operator: OperatorIdentity = Depends(require_roles(OperatorRole.VIEWER)),
) -> dict:
    return teacher_read_service.list_teachers(
        page=page,
        page_size=page_size,
        keyword=keyword,
        data_mode=data_mode,
        employment_status=employment_status,
    )


@app.get("/api/teacher-options")
def teacher_options(
    keyword: Optional[str] = Query(default=None, max_length=200),
    limit: int = Query(default=30, ge=1, le=100),
    page: int = Query(default=1, ge=1),
    _operator: OperatorIdentity = Depends(require_roles(OperatorRole.VIEWER)),
) -> list[dict]:
    return teacher_read_service.teacher_options(
        keyword=keyword,
        limit=limit,
        page=page,
    )


@app.get("/api/teachers/{teacher_id}")
def teacher_detail(
    teacher_id: str,
    _operator: OperatorIdentity = Depends(require_roles(OperatorRole.VIEWER)),
) -> dict:
    try:
        detail = teacher_read_service.teacher_detail(teacher_id)
    except LookupError as exc:
        raise DomainError(
            "TEACHER_NOT_FOUND",
            "teacher.error.not_found",
            status_code=404,
        ) from exc
    try:
        scorecard = score_read_service.teacher_scorecard(
            teacher_id,
            lesson_page=1,
            lesson_page_size=1,
        )
    except ScoreReadModelNotFound:
        return detail
    return {
        **detail,
        "raw_total_score": scorecard["raw_total_score"],
        "total_score": scorecard["raw_total_score"],
        "external_display_score": scorecard["public_total_score"],
        "dimensions": scorecard["dimensions"],
        "score_rule_version": scorecard["score_rule_version"],
        "score_read_model": {
            "calculated_at": scorecard["calculated_at"],
            "source": scorecard["source"],
        },
    }


@app.get("/api/teachers/{teacher_id}/scorecard")
def teacher_scorecard(
    teacher_id: str,
    lesson_page: int = Query(default=1, ge=1),
    lesson_page_size: int = Query(default=12, ge=1, le=100),
    _operator: OperatorIdentity = Depends(require_roles(OperatorRole.VIEWER)),
) -> dict:
    try:
        return score_read_service.teacher_scorecard(
            teacher_id,
            lesson_page=lesson_page,
            lesson_page_size=lesson_page_size,
        )
    except ScoreReadModelNotFound as exc:
        raise DomainError(
            "TEACHER_NOT_FOUND",
            "teacher.error.not_found",
            status_code=404,
        ) from exc


@app.get("/api/events")
def list_events(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    teacher_id: Optional[str] = Query(default=None),
    keyword: Optional[str] = Query(default=None, max_length=200),
    _operator: OperatorIdentity = Depends(
        require_roles(OperatorRole.AUDITOR, OperatorRole.SENIOR_REVIEWER)
    ),
) -> dict:
    return audit_service.list_event_page(
        page=page,
        page_size=page_size,
        teacher_id=teacher_id,
        keyword=keyword,
    )


# Keep this last. Starlette matches routes in registration order, so the
# packaged frontend must never shadow an API route or production's disabled
# documentation endpoints.
install_frontend_static(app)
