from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BoundedPlan(BaseModel):
    """The model may only select opaque, server-issued action identifiers."""

    model_config = ConfigDict(extra="forbid")

    selected_action_ids: list[str] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def validate_shape(self) -> "BoundedPlan":
        if len(set(self.selected_action_ids)) != len(self.selected_action_ids):
            raise ValueError("selected_action_ids must be unique")
        if not self.selected_action_ids:
            raise ValueError("a plan must select at least one server action, including NO_ACTION")
        return self


@dataclass(frozen=True)
class PlannerResult:
    plan: BoundedPlan
    planner: str
    mode: str
    model: str | None
    provider_request_id: str | None
    input_hash: str
    latency_ms: int
    usage: dict[str, int]
    fallback_reason: str | None

    def as_record(self) -> dict[str, Any]:
        return {
            **self.plan.model_dump(),
            "planner": self.planner,
            "mode": self.mode,
            "model": self.model,
            "provider_request_id": self.provider_request_id,
            "input_hash": self.input_hash,
            "latency_ms": self.latency_ms,
            "usage": self.usage,
            "fallback_reason": self.fallback_reason,
        }


class BoundedAgentProvider:
    """Selects only from the caller-supplied, fully parameterized action set."""

    def __init__(self) -> None:
        self.provider = os.getenv("AGENT_PROVIDER", "deterministic").strip().lower()
        self.model = os.getenv("OPENAI_AGENT_MODEL", "gpt-5.6-terra").strip()
        self.reasoning_effort = os.getenv("AGENT_REASONING_EFFORT", "low").strip()
        self.timeout_seconds = float(os.getenv("AGENT_TIMEOUT_SECONDS", "20"))

    def plan(
        self,
        teacher_id: str,
        signals: list[dict],
        candidates: list[dict],
        *,
        action_candidates: list[dict] | None = None,
        runtime_policy: dict[str, Any] | None = None,
    ) -> PlannerResult:
        provider = self.provider
        model = self.model
        enabled = True
        kill_switch = False
        max_secondary_tasks = 2
        if runtime_policy is not None:
            provider = str(runtime_policy.get("provider", provider)).strip().lower()
            model = str(runtime_policy.get("model", model)).strip()
            enabled = bool(runtime_policy.get("enabled", True))
            kill_switch = bool(runtime_policy.get("kill_switch", False))
            max_secondary_tasks = max(0, min(2, int(runtime_policy.get("max_secondary_tasks", 2))))

        actions = action_candidates or self._legacy_create_actions(candidates)
        payload = self._safe_payload(
            teacher_id,
            signals,
            candidates,
            actions,
            max_secondary_tasks=max_secondary_tasks,
        )
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        input_hash = hashlib.sha256(encoded).hexdigest()
        fallback = self._deterministic_plan(actions, max_secondary_tasks=max_secondary_tasks)

        if not enabled or kill_switch:
            return PlannerResult(
                plan=fallback,
                planner="DETERMINISTIC_POLICY",
                mode="DETERMINISTIC_FALLBACK",
                model=model if provider == "openai" else None,
                provider_request_id=None,
                input_hash=input_hash,
                latency_ms=0,
                usage={},
                fallback_reason="AGENT_KILL_SWITCH" if kill_switch else "AGENT_DISABLED",
            )

        if provider != "openai":
            return PlannerResult(
                plan=fallback,
                planner="DETERMINISTIC_POLICY",
                mode="DETERMINISTIC",
                model=None,
                provider_request_id=None,
                input_hash=input_hash,
                latency_ms=0,
                usage={},
                fallback_reason=None,
            )

        if not os.getenv("OPENAI_API_KEY"):
            return PlannerResult(
                plan=fallback,
                planner="DETERMINISTIC_POLICY",
                mode="DETERMINISTIC_FALLBACK",
                model=model,
                provider_request_id=None,
                input_hash=input_hash,
                latency_ms=0,
                usage={},
                fallback_reason="PROVIDER_NOT_CONFIGURED",
            )

        started = time.monotonic()
        try:
            from openai import OpenAI

            client = OpenAI(timeout=self.timeout_seconds)
            response = client.responses.parse(
                model=model,
                reasoning={"effort": self.reasoning_effort},
                input=[
                    {
                        "role": "system",
                        "content": (
                            "You are a bounded task lifecycle planner. You may only select opaque action_id values "
                            "from action_candidates. The server has already fixed every action parameter. Never "
                            "invent a task, parameter, message, score, graduation result, complaint conclusion, or "
                            "external action. Prefer recommended actions and the server-provided order. Select "
                            f"no more than one primary plus {max_secondary_tasks} CREATE_FROM_TEMPLATE actions. "
                            "Return only the requested structured selection."
                        ),
                    },
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False, sort_keys=True)},
                ],
                text_format=BoundedPlan,
            )
            parsed = response.output_parsed
            if parsed is None:
                raise ValueError("MODEL_OUTPUT_MISSING")
            self._validate_action_subset(parsed, actions)
            self._validate_plan_limits(parsed, actions, max_secondary_tasks=max_secondary_tasks)
            usage = getattr(response, "usage", None)
            usage_record = {
                key: int(value)
                for key in ("input_tokens", "output_tokens", "total_tokens")
                if (value := getattr(usage, key, None)) is not None
            }
            return PlannerResult(
                plan=parsed,
                planner="OPENAI_RESPONSES",
                mode="OPENAI_STRUCTURED",
                model=model,
                provider_request_id=getattr(response, "id", None),
                input_hash=input_hash,
                latency_ms=int((time.monotonic() - started) * 1000),
                usage=usage_record,
                fallback_reason=None,
            )
        except Exception as exc:  # Provider failures must never break deterministic dispatch.
            return PlannerResult(
                plan=fallback,
                planner="DETERMINISTIC_POLICY",
                mode="DETERMINISTIC_FALLBACK",
                model=model,
                provider_request_id=None,
                input_hash=input_hash,
                latency_ms=int((time.monotonic() - started) * 1000),
                usage={},
                fallback_reason=f"PROVIDER_{type(exc).__name__.upper()}",
            )

    @staticmethod
    def _safe_payload(
        teacher_id: str,
        signals: list[dict],
        candidates: list[dict],
        action_candidates: list[dict],
        *,
        max_secondary_tasks: int = 2,
    ) -> dict:
        return {
            "teacher_id": teacher_id,
            "signals": [
                {
                    "signal_id": item["signal_id"],
                    "code": item["code"],
                    "severity": item.get("severity"),
                    "status": item.get("status"),
                }
                for item in signals
            ],
            "candidate_templates": [
                {
                    "template_id": item["template_id"],
                    "template_version": item["template_version"],
                    "priority": item["priority"],
                    "due_hours": item["due_hours"],
                    "dimension": item["dimension"],
                    "completion_method": item["completion_method"],
                }
                for item in candidates
            ],
            "action_candidates": [
                {
                    "action_id": item["action_id"],
                    "action_type": item["action_type"],
                    "template_id": item.get("template_id"),
                    "task_id": item.get("task_id"),
                    "recommended": bool(item.get("recommended", False)),
                    "reason_code": item.get("reason_code"),
                }
                for item in action_candidates
            ],
            "constraints": {
                "published_templates_only": True,
                "server_generated_action_ids_only": True,
                "server_owned_action_parameters": True,
                "max_primary_tasks": 1,
                "max_secondary_tasks": max_secondary_tasks,
                "freeform_tasks_allowed": False,
                "external_actions_allowed": False,
            },
        }

    @staticmethod
    def _legacy_create_actions(candidates: list[dict]) -> list[dict]:
        """Compatibility for small callers; production passes a server catalog."""
        actions = [
            {
                "action_id": f'CREATE:{item["template_id"]}',
                "action_type": "CREATE_FROM_TEMPLATE",
                "template_id": item["template_id"],
                "recommended": True,
                "reason_code": "PUBLISHED_TRIGGER_MATCH",
            }
            for item in candidates
        ]
        if not actions:
            actions.append(
                {
                    "action_id": "NO_ACTION:NO_PUBLISHED_CANDIDATE",
                    "action_type": "NO_ACTION",
                    "template_id": None,
                    "recommended": True,
                    "reason_code": "NO_PUBLISHED_CANDIDATE",
                }
            )
        return actions

    @staticmethod
    def _deterministic_plan(
        action_candidates: list[dict],
        *,
        max_secondary_tasks: int = 2,
    ) -> BoundedPlan:
        recommended = [item for item in action_candidates if item.get("recommended")]
        selected: list[str] = []
        create_count = 0
        for item in recommended:
            if item["action_type"] == "NO_ACTION":
                if not selected:
                    return BoundedPlan(selected_action_ids=[item["action_id"]])
                continue
            if item["action_type"] == "CREATE_FROM_TEMPLATE":
                if create_count >= 1 + max_secondary_tasks:
                    continue
                create_count += 1
            selected.append(item["action_id"])
        if selected:
            return BoundedPlan(selected_action_ids=selected)
        no_action = next(
            (item["action_id"] for item in action_candidates if item["action_type"] == "NO_ACTION"),
            None,
        )
        if no_action is None:
            raise ValueError("SERVER_ACTION_CATALOG_MISSING_NO_ACTION")
        return BoundedPlan(selected_action_ids=[no_action])

    @staticmethod
    def _validate_action_subset(plan: BoundedPlan, action_candidates: list[dict]) -> None:
        candidate_ids = {item["action_id"] for item in action_candidates}
        if not set(plan.selected_action_ids) <= candidate_ids:
            raise ValueError("MODEL_SELECTED_UNKNOWN_ACTION")

    @staticmethod
    def _validate_plan_limits(
        plan: BoundedPlan,
        action_candidates: list[dict],
        *,
        max_secondary_tasks: int,
    ) -> None:
        by_id = {item["action_id"]: item for item in action_candidates}
        create_count = sum(
            by_id[action_id]["action_type"] == "CREATE_FROM_TEMPLATE"
            for action_id in plan.selected_action_ids
        )
        if create_count > 1 + max_secondary_tasks:
            raise ValueError("MODEL_EXCEEDED_POLICY_TASK_LIMIT")
        selected_types = {by_id[action_id]["action_type"] for action_id in plan.selected_action_ids}
        if "NO_ACTION" in selected_types and len(plan.selected_action_ids) > 1:
            raise ValueError("MODEL_COMBINED_NO_ACTION")
