from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Engine, case, func, select

from .database import engine as default_engine
from .database import session_scope
from .db_models import (
    IdempotencyRecord,
    TaskAssignmentRecord,
    TaskTemplateRecord,
    TeacherRecord,
)
from .errors import DomainError
from .task_models import (
    CreateTaskTemplateRequest,
    PublishTaskTemplateRequest,
    UpdateTaskTemplateRequest,
)
from .teacher_copy import (
    normalize_personalized_assignment_title,
)
from .task_catalog import MANDATORY_TASK_CODE_SET


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    normalized = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return normalized.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _raise(
    code: str,
    message_key: str,
    *,
    status_code: int = 400,
    details: dict[str, Any] | None = None,
) -> None:
    raise DomainError(
        code,
        message_key,
        status_code=status_code,
        details=details,
    )


class TaskService:
    """Current task-template configuration and shared assignment reads."""

    FIXED_GROWTH_TASK_CODES = MANDATORY_TASK_CODE_SET

    def __init__(self, bind: Engine | None = None) -> None:
        self.engine = bind or default_engine

    @staticmethod
    def _idempotent_response(
        session: Any,
        *,
        scope: str,
        key: str,
        request_hash: str,
    ) -> dict[str, Any] | None:
        record = session.get(IdempotencyRecord, (scope, key))
        if record is None:
            return None
        if record.request_hash != request_hash:
            _raise(
                "DUPLICATE_CONFLICT",
                "task.error.idempotency_conflict",
                status_code=409,
            )
        return deepcopy(record.response_payload or {})

    @staticmethod
    def _save_idempotency(
        session: Any,
        *,
        scope: str,
        key: str,
        request_hash: str,
        resource_id: str,
        response: dict[str, Any],
    ) -> None:
        session.add(
            IdempotencyRecord(
                scope=scope,
                idempotency_key=key,
                request_hash=request_hash,
                resource_id=resource_id,
                response_payload=deepcopy(response),
            )
        )

    @staticmethod
    def _template_record(
        session: Any,
        template_id: str,
    ) -> TaskTemplateRecord:
        records = session.scalars(
            select(TaskTemplateRecord).where(
                TaskTemplateRecord.template_id == template_id,
                TaskTemplateRecord.status != "RETIRED",
            )
        ).all()
        if not records:
            _raise(
                "TASK_TEMPLATE_NOT_FOUND",
                "task.error.template_not_found",
                status_code=404,
            )
        if len(records) != 1:
            _raise(
                "TASK_TEMPLATE_DATA_CONFLICT",
                "task.error.template_data_conflict",
                status_code=409,
            )
        return records[0]

    @classmethod
    def _validate_template_integration_mode(
        cls,
        template_id: str,
        integration_mode: str,
    ) -> None:
        is_fixed_growth = template_id in cls.FIXED_GROWTH_TASK_CODES
        if is_fixed_growth and integration_mode != "INBOUND_STATUS_ONLY":
            _raise(
                "FIXED_TASK_REQUIRES_INBOUND_STATUS_ONLY",
                "task.error.fixed_task_requires_inbound_status_only",
                status_code=409,
                details={"template_id": template_id},
            )
        if not is_fixed_growth and integration_mode == "INBOUND_STATUS_ONLY":
            _raise(
                "INBOUND_STATUS_ONLY_RESERVED_FOR_FIXED_TASKS",
                "task.error.inbound_status_only_reserved_for_fixed_tasks",
                status_code=409,
                details={
                    "template_id": template_id,
                    "allowed_template_ids": sorted(cls.FIXED_GROWTH_TASK_CODES),
                },
            )

    @staticmethod
    def _template_response(record: TaskTemplateRecord) -> dict[str, Any]:
        payload = deepcopy(record.payload)
        payload.pop("template_version", None)
        payload.pop("source_version", None)
        payload.update(
            status=record.status,
            revision=record.revision,
            integration_mode=record.integration_mode,
            updated_by=record.updated_by,
            created_at=_iso(record.created_at),
            updated_at=_iso(record.updated_at),
        )
        return payload

    @staticmethod
    def _assignment_response(
        assignment: TaskAssignmentRecord,
        teacher: TeacherRecord,
        template: TaskTemplateRecord,
    ) -> dict[str, Any]:
        template_payload = template.payload if isinstance(template.payload, dict) else {}
        stored_title = assignment.display_title or template_payload.get("title")
        title = (
            normalize_personalized_assignment_title(
                assignment.task_code,
                stored_title,
            )
            if assignment.task_kind == "PERSONALIZED_IMPROVEMENT"
            else stored_title
        )
        return {
            "assignment_id": assignment.assignment_id,
            "teacher_id": assignment.teacher_id,
            "teacher_name": teacher.name,
            "task_code": assignment.task_code,
            "task_kind": assignment.task_kind,
            "creator_system": assignment.creator_system,
            "status": assignment.status,
            "priority": assignment.priority,
            "why": assignment.why,
            "title": title,
            "evidence_snapshot": deepcopy(assignment.evidence_snapshot or {}),
            "what_to_do": template_payload.get("how_summary"),
            "completion_standard": template_payload.get("completion_standard"),
            "outcome": template_payload.get("benefit"),
            "due_at": _iso(assignment.due_at) if assignment.due_at else None,
            "timezone_used": assignment.timezone_used,
            "timezone_source": assignment.timezone_source,
            "timezone_verified_at": (
                _iso(assignment.timezone_verified_at)
                if assignment.timezone_verified_at
                else None
            ),
            "status_reason_code": assignment.status_reason_code,
            "source_mode": assignment.source_mode,
            "dedupe_key": assignment.dedupe_key,
            "created_by": assignment.created_by,
            "updated_by": assignment.updated_by,
            "row_version": assignment.row_version,
            "assigned_at": _iso(assignment.assigned_at),
            "status_changed_at": _iso(assignment.status_changed_at),
            "completed_at": _iso(assignment.completed_at) if assignment.completed_at else None,
            "created_at": _iso(assignment.created_at),
            "updated_at": _iso(assignment.updated_at),
        }

    @staticmethod
    def _require_status_revision(
        status: str,
        revision: int,
        *,
        allowed: set[str],
        expected_revision: int,
        kind: str,
    ) -> None:
        if status not in allowed:
            _raise(f"{kind}_IMMUTABLE", "task.error.immutable", status_code=409)
        if revision != expected_revision:
            _raise(
                f"{kind}_REVISION_CONFLICT",
                "task.error.revision_conflict",
                status_code=409,
                details={"expected_revision": revision},
            )

    def list_templates(self, status: str | None = None) -> list[dict[str, Any]]:
        with session_scope(self.engine) as session:
            statement = select(TaskTemplateRecord).where(
                TaskTemplateRecord.status != "RETIRED"
            )
            if status:
                statement = statement.where(TaskTemplateRecord.status == status)
            records = session.scalars(statement).all()
            template_ids = [item.template_id for item in records]
            if len(template_ids) != len(set(template_ids)):
                _raise(
                    "TASK_TEMPLATE_DATA_CONFLICT",
                    "task.error.template_data_conflict",
                    status_code=409,
                )
            return sorted(
                (self._template_response(item) for item in records),
                key=lambda item: item["template_id"],
            )

    def create_template(
        self,
        request: CreateTaskTemplateRequest,
        actor_id: str,
    ) -> dict[str, Any]:
        definition = request.model_dump(
            mode="json",
            exclude={"template_id", "idempotency_key"},
        )
        request_hash = _hash({"template_id": request.template_id, **definition})
        with session_scope(self.engine) as session:
            self._validate_template_integration_mode(
                request.template_id,
                request.integration_mode,
            )
            existing = self._idempotent_response(
                session,
                scope="TASK_TEMPLATE_CREATE",
                key=request.idempotency_key,
                request_hash=request_hash,
            )
            if existing is not None:
                return existing
            if session.scalar(
                select(TaskTemplateRecord.row_id).where(
                    TaskTemplateRecord.template_id == request.template_id
                )
            ):
                _raise(
                    "TASK_TEMPLATE_EXISTS",
                    "task.error.template_exists",
                    status_code=409,
                )
            now = _now()
            response = {
                "template_id": request.template_id,
                "status": "DRAFT",
                "revision": 1,
                **definition,
                "created_by": actor_id,
                "updated_by": actor_id,
                "created_at": _iso(now),
                "updated_at": _iso(now),
            }
            session.add(
                TaskTemplateRecord(
                    row_id=f"{request.template_id}:v1",
                    template_id=request.template_id,
                    template_version=1,
                    status="DRAFT",
                    revision=1,
                    output_type=request.output_type,
                    execution_owner=request.execution_owner,
                    integration_mode=request.integration_mode,
                    external_task_template_code=request.external_task_template_code,
                    source_mode=request.source_mode,
                    payload=deepcopy(response),
                    created_by=actor_id,
                    updated_by=actor_id,
                    created_at=now,
                    updated_at=now,
                )
            )
            self._save_idempotency(
                session,
                scope="TASK_TEMPLATE_CREATE",
                key=request.idempotency_key,
                request_hash=request_hash,
                resource_id=f"{request.template_id}:v1",
                response=response,
            )
            return response

    def update_template(
        self,
        template_id: str,
        request: UpdateTaskTemplateRequest,
        actor_id: str,
    ) -> dict[str, Any]:
        with session_scope(self.engine) as session:
            record = self._template_record(session, template_id)
            self._require_status_revision(
                record.status,
                record.revision,
                allowed={"DRAFT"},
                expected_revision=request.expected_revision,
                kind="TASK_TEMPLATE",
            )
            self._validate_template_integration_mode(
                template_id,
                request.integration_mode,
            )
            now = _now()
            definition = request.model_dump(mode="json", exclude={"expected_revision"})
            response = {
                "template_id": template_id,
                "status": record.status,
                "revision": record.revision + 1,
                **definition,
                "created_by": record.created_by,
                "updated_by": actor_id,
                "created_at": _iso(record.created_at),
                "updated_at": _iso(now),
            }
            record.revision += 1
            record.output_type = request.output_type
            record.execution_owner = request.execution_owner
            record.integration_mode = request.integration_mode
            record.external_task_template_code = request.external_task_template_code
            record.source_mode = request.source_mode
            record.payload = deepcopy(response)
            record.updated_by = actor_id
            record.updated_at = now
            return response

    def publish_template(
        self,
        template_id: str,
        request: PublishTaskTemplateRequest,
        actor_id: str,
    ) -> dict[str, Any]:
        with session_scope(self.engine) as session:
            record = self._template_record(session, template_id)
            self._validate_template_integration_mode(
                template_id,
                record.integration_mode,
            )
            self._require_status_revision(
                record.status,
                record.revision,
                allowed={"DRAFT"},
                expected_revision=request.expected_revision,
                kind="TASK_TEMPLATE",
            )
            now = _now()
            record.status = "PUBLISHED"
            record.revision += 1
            record.updated_by = actor_id
            record.updated_at = now
            record.payload = {
                **deepcopy(record.payload),
                "status": "PUBLISHED",
                "revision": record.revision,
                "updated_by": actor_id,
                "updated_at": _iso(now),
                "published_at": _iso(now),
                "published_by": actor_id,
            }
            return self._template_response(record)

    @staticmethod
    def _assignment_statement(
        *,
        teacher_id: str | None = None,
        status: str | None = None,
        task_kind: str | None = None,
        include_mock: bool = True,
    ) -> Any:
        statement = (
            select(TaskAssignmentRecord, TeacherRecord, TaskTemplateRecord)
            .join(TeacherRecord, TeacherRecord.teacher_id == TaskAssignmentRecord.teacher_id)
            .join(
                TaskTemplateRecord,
                TaskTemplateRecord.row_id
                == TaskAssignmentRecord.template_version_id,
            )
            .where(TaskTemplateRecord.status == "PUBLISHED")
        )
        if teacher_id:
            statement = statement.where(TaskAssignmentRecord.teacher_id == teacher_id)
        if status:
            statement = statement.where(TaskAssignmentRecord.status == status)
        if task_kind:
            statement = statement.where(TaskAssignmentRecord.task_kind == task_kind)
        if not include_mock:
            statement = statement.where(
                ~TaskAssignmentRecord.source_mode.like("MOCK%")
            )
        return statement

    def list_assignments(
        self,
        *,
        teacher_id: str | None = None,
        status: str | None = None,
        task_kind: str | None = None,
        include_mock: bool = True,
    ) -> list[dict[str, Any]]:
        with session_scope(self.engine) as session:
            statement = self._assignment_statement(
                teacher_id=teacher_id,
                status=status,
                task_kind=task_kind,
                include_mock=include_mock,
            )
            rows = session.execute(
                statement.order_by(
                    TaskAssignmentRecord.updated_at.desc(),
                    TaskAssignmentRecord.assignment_id,
                )
            ).all()
            return [self._assignment_response(*row) for row in rows]

    def list_assignment_page(
        self,
        *,
        teacher_id: str | None = None,
        status: str | None = None,
        task_kind: str | None = None,
        include_mock: bool = True,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        with session_scope(self.engine) as session:
            statement = self._assignment_statement(
                teacher_id=teacher_id,
                status=status,
                task_kind=task_kind,
                include_mock=include_mock,
            )
            total = int(
                session.scalar(
                    select(func.count()).select_from(
                        statement.order_by(None).subquery()
                    )
                )
                or 0
            )
            rows = session.execute(
                statement.order_by(
                    TaskAssignmentRecord.updated_at.desc(),
                    TaskAssignmentRecord.assignment_id,
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).all()
            return {
                "items": [self._assignment_response(*row) for row in rows],
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": (total + page_size - 1) // page_size,
            }

    @staticmethod
    def _assignment_title_expression() -> Any:
        return func.coalesce(
            TaskAssignmentRecord.display_title,
            TaskTemplateRecord.payload["title"].as_string(),
            TaskAssignmentRecord.task_code,
        )

    @staticmethod
    def _task_progress_source_filter() -> Any:
        non_mock = ~TaskAssignmentRecord.source_mode.like("MOCK%")
        if os.getenv("APP_ENV", "").strip().lower() == "test":
            return non_mock | (
                TaskAssignmentRecord.source_mode == "MOCK_SIMULATION"
            )
        return non_mock

    def list_task_progress(self, *, keyword: str | None = None) -> dict[str, Any]:
        """Return one compact row per visible task definition/title."""

        title = self._assignment_title_expression().label("title")
        needle = str(keyword or "").strip().casefold()

        with session_scope(self.engine) as session:
            rows = session.execute(
                select(
                    TaskAssignmentRecord.task_code,
                    title,
                    TaskAssignmentRecord.task_kind,
                    TaskAssignmentRecord.teacher_id,
                    TaskAssignmentRecord.status,
                )
                .select_from(TaskAssignmentRecord)
                .join(
                    TaskTemplateRecord,
                    TaskTemplateRecord.row_id
                    == TaskAssignmentRecord.template_version_id,
                )
                .where(
                    self._task_progress_source_filter(),
                    TaskTemplateRecord.status == "PUBLISHED",
                )
                .order_by(
                    case(
                        (TaskAssignmentRecord.task_kind == "FIXED_GROWTH", 0),
                        else_=1,
                    ),
                    TaskAssignmentRecord.task_code,
                    title,
                )
            ).all()

            grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
            for row in rows:
                normalized_title = (
                    normalize_personalized_assignment_title(
                        row.task_code,
                        row.title,
                    )
                    if row.task_kind == "PERSONALIZED_IMPROVEMENT"
                    else row.title
                )
                searchable = (
                    f"{row.task_code} {normalized_title or ''} {row.teacher_id}"
                ).casefold()
                if needle and needle not in searchable:
                    continue
                key = (row.task_code, normalized_title, row.task_kind)
                item = grouped.setdefault(
                    key,
                    {
                        "task_code": row.task_code,
                        "title": normalized_title,
                        "task_kind": row.task_kind,
                        "_teacher_ids": set(),
                        "assignment_count": 0,
                        "not_started": 0,
                        "in_progress": 0,
                        "completed": 0,
                        "other": 0,
                    },
                )
                item["_teacher_ids"].add(row.teacher_id)
                item["assignment_count"] += 1
                if row.status in {"ASSIGNED", "VIEWED"}:
                    item["not_started"] += 1
                elif row.status in {"IN_PROGRESS", "SUBMITTED", "UNDER_REVIEW"}:
                    item["in_progress"] += 1
                elif row.status == "COMPLETED":
                    item["completed"] += 1
                else:
                    item["other"] += 1

            items: list[dict[str, Any]] = []
            for item in grouped.values():
                assignment_count = int(item["assignment_count"])
                completed_count = int(item["completed"])
                teacher_ids = item.pop("_teacher_ids")
                item["assigned_teacher_count"] = len(teacher_ids)
                item["completion_rate"] = (
                    completed_count / assignment_count
                    if assignment_count
                    else 0
                )
                items.append(item)
            return {"items": items, "total": len(items)}

    def list_task_progress_assignments(
        self,
        *,
        task_code: str,
        title: str,
        task_kind: str,
        page: int,
        page_size: int,
        keyword: str | None = None,
    ) -> dict[str, Any]:
        """Page assignment facts for one task-progress row."""

        filters = (
            TaskAssignmentRecord.task_code == task_code,
            TaskAssignmentRecord.task_kind == task_kind,
            self._task_progress_source_filter(),
            TaskTemplateRecord.status == "PUBLISHED",
        )

        with session_scope(self.engine) as session:
            candidate_rows = session.execute(
                select(
                    TaskAssignmentRecord.assignment_id,
                    TaskAssignmentRecord.teacher_id,
                    TaskAssignmentRecord.task_code,
                    TaskAssignmentRecord.task_kind,
                    TaskAssignmentRecord.display_title,
                    TaskTemplateRecord.payload,
                )
                .select_from(TaskAssignmentRecord)
                .join(
                    TaskTemplateRecord,
                    TaskTemplateRecord.row_id
                    == TaskAssignmentRecord.template_version_id,
                )
                .where(*filters)
                .order_by(
                    TaskAssignmentRecord.updated_at.desc(),
                    TaskAssignmentRecord.assignment_id,
                )
            ).all()
            needle = str(keyword or "").strip().casefold()
            title_matches_keyword = needle in (
                f"{task_code} {title}".casefold()
            )
            matching_ids: list[str] = []
            for row in candidate_rows:
                normalized_title = (
                    normalize_personalized_assignment_title(
                        row.task_code,
                        row.display_title
                        or (
                            row.payload.get("title")
                            if isinstance(row.payload, dict)
                            else None
                        ),
                    )
                    if row.task_kind == "PERSONALIZED_IMPROVEMENT"
                    else (
                        row.display_title
                        or (
                            row.payload.get("title")
                            if isinstance(row.payload, dict)
                            else row.task_code
                        )
                    )
                )
                if normalized_title != title:
                    continue
                if (
                    needle
                    and not title_matches_keyword
                    and needle not in row.teacher_id.casefold()
                ):
                    continue
                matching_ids.append(row.assignment_id)
            total = len(matching_ids)
            page_ids = matching_ids[
                (page - 1) * page_size : page * page_size
            ]
            page_rows = (
                session.execute(
                    select(
                        TaskAssignmentRecord,
                        TeacherRecord,
                        TaskTemplateRecord,
                    )
                    .join(
                        TeacherRecord,
                        TeacherRecord.teacher_id
                        == TaskAssignmentRecord.teacher_id,
                    )
                    .join(
                        TaskTemplateRecord,
                        TaskTemplateRecord.row_id
                        == TaskAssignmentRecord.template_version_id,
                    )
                    .where(
                        TaskAssignmentRecord.assignment_id.in_(page_ids)
                    )
                    .order_by(
                        TaskAssignmentRecord.updated_at.desc(),
                        TaskAssignmentRecord.assignment_id,
                    )
                ).all()
                if page_ids
                else []
            )
            return {
                "items": [
                    self._assignment_response(*row) for row in page_rows
                ],
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": (
                    (total + page_size - 1) // page_size if total else 0
                ),
            }
