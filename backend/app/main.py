from __future__ import annotations

from typing import Optional

from fastapi import Depends, FastAPI, Query
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .auth import OperatorIdentity, auth_router, require_roles
from .auth_models import OperatorRole
from .config_routes import router as config_router
from .database import database_health
from .services import DomainError, GrowthService
from .operations_routes import router as operations_router
from .store import store
from .task_routes import router as task_router


app = FastAPI(
    title="TIT Growth System Operational API",
    version="current",
    description="Current local API for shared tasks, scoring, operational views, outputs and audit.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5174", "http://127.0.0.1:5174"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth_router)
app.include_router(config_router)
app.include_router(task_router)
app.include_router(operations_router)

service = GrowthService(store)


@app.exception_handler(DomainError)
async def handle_domain_error(_, exc: DomainError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.response())


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
    data_mode_counts: dict[str, int] = {}
    for teacher in store.teachers.values():
        data_mode = str(teacher.get("data_mode") or "MOCK").upper()
        data_mode_counts[data_mode] = data_mode_counts.get(data_mode, 0) + 1
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
        "runtime": {"single_process_required": True},
    }


@app.get("/api/health/db")
def health_database() -> dict:
    return database_health()


@app.get("/api/dashboard")
def dashboard(
    _operator: OperatorIdentity = Depends(require_roles(OperatorRole.VIEWER)),
) -> dict:
    return service.dashboard()


@app.get("/api/teachers")
def list_teachers(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=24, ge=1, le=100),
    keyword: Optional[str] = Query(default=None),
    data_mode: Optional[str] = Query(default=None),
    employment_status: Optional[str] = Query(default=None),
    _operator: OperatorIdentity = Depends(require_roles(OperatorRole.VIEWER)),
) -> dict:
    return service.list_teachers(
        page=page,
        page_size=page_size,
        keyword=keyword,
        data_mode=data_mode,
        employment_status=employment_status,
    )


@app.get("/api/teacher-options")
def teacher_options(
    _operator: OperatorIdentity = Depends(require_roles(OperatorRole.VIEWER)),
) -> list[dict]:
    return service.teacher_options()


@app.get("/api/teachers/{teacher_id}")
def teacher_detail(
    teacher_id: str,
    _operator: OperatorIdentity = Depends(require_roles(OperatorRole.VIEWER)),
) -> dict:
    return service.teacher_detail(teacher_id)


@app.get("/api/ops/action-queue")
def action_queue(
    _operator: OperatorIdentity = Depends(require_roles(OperatorRole.VIEWER)),
) -> list[dict]:
    return service.action_queue()


@app.get("/api/ops/cases")
def list_cases(
    _operator: OperatorIdentity = Depends(require_roles(OperatorRole.VIEWER)),
) -> list[dict]:
    return service.list_cases()


@app.get("/api/events")
def list_events(
    _operator: OperatorIdentity = Depends(
        require_roles(OperatorRole.AUDITOR, OperatorRole.SENIOR_REVIEWER)
    ),
) -> list[dict]:
    return service.list_events()


@app.get("/api/outputs")
def list_outputs(
    output_type: Optional[str] = Query(default=None, alias="type"),
    status: Optional[str] = Query(default=None),
    teacher_id: Optional[str] = Query(default=None),
    _operator: OperatorIdentity = Depends(require_roles(OperatorRole.VIEWER)),
) -> list[dict]:
    return service.list_outputs(type_filter=output_type, status=status, teacher_id=teacher_id)


@app.get("/api/outputs/summary")
def output_summary(
    _operator: OperatorIdentity = Depends(require_roles(OperatorRole.VIEWER)),
) -> dict:
    return service.output_summary()


@app.post("/api/outputs/{output_id}/retry")
def retry_output(
    output_id: str,
    operator: OperatorIdentity = Depends(
        require_roles(OperatorRole.CASE_OPERATOR, OperatorRole.SENIOR_REVIEWER)
    ),
) -> dict:
    return service.retry_output(output_id, actor_id=operator.operator_id)
