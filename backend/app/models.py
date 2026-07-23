from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


CompletionMethod = Literal[
    "QUIZ",
    "CHECKLIST",
    "UPLOAD_REVIEW",
    "DEVICE_CHECK",
    "EXTERNAL_SYNC",
    "CONFIRMATION_FORM",
]


class TaskDispatchAck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1, max_length=128)
    event_type: Literal["task.dispatch_ack.v1"]
    idempotency_key: str = Field(min_length=1, max_length=128)
    task_id: str = Field(min_length=1, max_length=128)
    assignment_revision: int = Field(ge=1)
    execution_contract_version: int = Field(ge=1)
    accepted: Literal[True] = True
    accepted_at: str
    runtime_task_ref: str = Field(min_length=1, max_length=128)
    error_code: None = None


class TaskCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command_schema_version: Literal[1] = 1
    event_id: str = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=128)
    command_type: Literal[
        "VIEW_TASK",
        "START_TASK",
        "RETRY_TASK",
        "REQUEST_HELP",
        "REQUEST_REVIEW",
        "SUBMIT_TASK",
        "RESPOND_CONFIRMATION",
    ]
    task_id: str
    expected_execution_contract_version: int = Field(ge=1)
    last_seen_runtime_sequence: int = Field(ge=0)
    occurred_at: str
    completion_method: CompletionMethod
    payload: dict[str, Any] = Field(default_factory=dict)


class TrustedResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_event_id: str
    source: Literal["MOCK_PROVIDER"]
    result: Literal["PASSED", "RETRY_REQUIRED", "REJECTED"]
    result_ref: str


class ManualTaskIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    teacher_id: str = Field(min_length=1, max_length=64)
    template_id: str = Field(min_length=1, max_length=64)
    idempotency_key: str = Field(min_length=1, max_length=128)
    reason_code: Literal["OPS_MANUAL_ASSIGNMENT"]


class CaseDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["KEEP_SCHEDULE", "REQUEST_COURSE_RELEASE", "CLOSE_NO_ACTION"]
    note: str = Field(default="", max_length=500)


class NotificationEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1, max_length=128)
    event_type: Literal["notification.delivery_event.v1"]
    idempotency_key: str = Field(min_length=1, max_length=128)
    notification_id: str = Field(min_length=1, max_length=128)
    task_id: str = Field(min_length=1, max_length=128)
    delivery_status: Literal["STORED", "READ", "CLICKED", "FAILED"]
    occurred_at: str
    error_code: Optional[str] = Field(default=None, max_length=128)


class EscalationRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of: str
