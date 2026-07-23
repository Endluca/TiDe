from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


OutputType = Literal[
    "TEACHER_TASK",
    "OPS_CASE",
    "NOTIFICATION",
    "RECOMMENDATION",
    "ACTION_REQUEST",
]
SourceMode = Literal["REAL", "MOCK", "MIXED", "MOCK_PROXY", "MOCK_SIMULATION"]
IntegrationMode = Literal["OUTBOUND_MANAGED", "INBOUND_STATUS_ONLY"]


class TaskTemplateDefinition(BaseModel):
    """Definition used to render and settle one shared-database task."""

    model_config = ConfigDict(extra="forbid")

    output_type: OutputType = "TEACHER_TASK"
    audience: Literal["TEACHER", "OPS", "INTERNAL"] = "TEACHER"
    owner: str = Field(min_length=2, max_length=120)
    execution_owner: Literal["TEACHER_APP"] = "TEACHER_APP"
    integration_mode: IntegrationMode = "OUTBOUND_MANAGED"
    category: Literal[
        "MANDATORY_GROWTH",
        "SUPPLY_OPTIONAL",
        "PERSONALIZED_IMPROVEMENT",
        "MANUAL_CONFIRMATION",
    ]
    dimension: Literal[
        "RELIABILITY",
        "USER_FEEDBACK",
        "CLASS_QUALITY",
        "CAPACITY",
        "NEW_TEACHER_TASK",
    ]
    stage: str = Field(min_length=2, max_length=64)
    ops_name_zh: str = Field(min_length=2, max_length=120)
    content_locale: str = Field(default="en", min_length=2, max_length=16)
    title: str = Field(min_length=2, max_length=160)
    why_template: str = Field(min_length=4, max_length=1200)
    how_summary: str = Field(min_length=4, max_length=1600)
    completion_standard: str = Field(min_length=4, max_length=1200)
    benefit: str = Field(min_length=2, max_length=800)
    help_ref: Optional[str] = Field(default=None, max_length=512)
    priority: Literal["P0", "P1", "P2", "P3"]
    due_rule: dict[str, Any]
    appeal_mode: Literal[
        "NOT_ALLOWED",
        "EXPLANATION_ALLOWED",
        "RETEST_ALLOWED",
        "HUMAN_REVIEW",
    ]
    external_task_template_code: str = Field(
        min_length=3,
        max_length=128,
        pattern=r"^[A-Z][A-Z0-9_.:-]*$",
    )
    action_url: Optional[str] = Field(default=None, max_length=1024)
    score_type: Literal["FIXED", "ZERO", "NOT_APPLICABLE"]
    score_value: float = Field(ge=0, le=1000)
    source_mode: SourceMode
    source_refs: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_task_contract(self) -> "TaskTemplateDefinition":
        if self.output_type != "TEACHER_TASK":
            raise ValueError("task templates must produce TEACHER_TASK outputs")
        if self.audience != "TEACHER":
            raise ValueError("task templates must target the teacher audience")
        if self.score_type == "FIXED" and self.score_value <= 0:
            raise ValueError("FIXED score_type requires a positive score_value")
        if self.score_type in {"ZERO", "NOT_APPLICABLE"} and self.score_value != 0:
            raise ValueError("ZERO and NOT_APPLICABLE require score_value=0")
        if self.category == "PERSONALIZED_IMPROVEMENT" and (
            self.score_type != "ZERO" or self.score_value != 0
        ):
            raise ValueError("personalized tasks must use ZERO score and score_value=0")
        if self.integration_mode == "INBOUND_STATUS_ONLY" and self.category != "MANDATORY_GROWTH":
            raise ValueError("INBOUND_STATUS_ONLY is reserved for mandatory growth tasks")
        self.source_refs = list(
            dict.fromkeys(item.strip() for item in self.source_refs if item.strip())
        )
        return self


class CreateTaskTemplateRequest(TaskTemplateDefinition):
    template_id: str = Field(
        min_length=3,
        max_length=64,
        pattern=r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*$",
    )
    idempotency_key: str = Field(min_length=8, max_length=256)


class UpdateTaskTemplateRequest(TaskTemplateDefinition):
    expected_revision: int = Field(ge=1)


class PublishTaskTemplateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
