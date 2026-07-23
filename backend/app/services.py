from __future__ import annotations

import hashlib
import inspect
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Optional
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from jsonschema import Draft202012Validator
from pydantic import ValidationError

from .agent_provider import BoundedAgentProvider
from .config_models import (
    DEFAULT_CONFIG_PAYLOADS,
    AgentPolicyConfig,
    ConfigKey,
    DeliveryPolicyConfig,
    ScoreGraduationConfig,
)
from .config_service import get_published_payload
from .models import (
    CaseDecision,
    EscalationRunRequest,
    ManualTaskIssue,
    NotificationEvent,
    TaskDispatchAck,
    TaskCommand,
    TrustedResult,
)
from .store import InMemoryStore
from .template_rules import (
    signals_within_merge_window,
    template_cooldown_block,
    template_matches_signal,
)


UNTRUSTED_TIMEZONE_SOURCE_MODES = frozenset(
    {"", "MISSING", "SOURCE_MISSING", "MISSING_INPUT_ZERO", "STORAGE_PLACEHOLDER"}
)
SUBMISSION_SCHEMA = json.loads(
    Path(__file__).with_name("internal_task_submission_schema.json").read_text(
        encoding="utf-8"
    )
)
SUBMISSION_VALIDATOR = Draft202012Validator(SUBMISSION_SCHEMA)
COMMAND_LOCK = RLock()
ConfigReader = Callable[[ConfigKey], Optional[dict[str, Any]]]

DEFAULT_DELIVERY_POLICY = DeliveryPolicyConfig(
    normal_reminder_minutes_before_due=60,
    urgent_reminder_minutes_before_due=15,
    p0_response_window_minutes=120,
    p0_reminder_minutes_before_response_due=30,
)
DEFAULT_SCORE_POLICY = ScoreGraduationConfig.model_validate(
    DEFAULT_CONFIG_PAYLOADS[ConfigKey.SCORE_GRADUATION]
)
SCORE_DIMENSION_CONFIG_KEYS = {
    "RELIABILITY": "reliability",
    "USER_FEEDBACK": "user_feedback",
    "CLASS_QUALITY": "classroom_quality",
    "CAPACITY": "capacity",
    "NEW_TEACHER_TASK": "new_teacher_tasks",
}
MILESTONE_POLICY_VERSIONS = frozenset({"v4", "v5", "v6"})
MANDATORY_GROWTH_POLICY_VERSIONS = frozenset({"v3", "v4", "v5", "v6"})
PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
TASK_CATEGORY_ORDER = {
    "MANUAL_CONFIRMATION": 0,
    "REQUIRED_GROWTH": 1,
    "PERSONALIZED_IMPROVEMENT": 2,
}
AGENT_ESCALATED_DUE_HOURS = {"P0": 2, "P1": 12, "P2": 24, "P3": 72}
VERIFICATION_MODE_BY_METHOD = {
    "QUIZ": "SYSTEM_IMMEDIATE",
    "CHECKLIST": "SYSTEM_IMMEDIATE",
    "UPLOAD_REVIEW": "HUMAN_REVIEW",
    "DEVICE_CHECK": "SYSTEM_ASYNC",
    "EXTERNAL_SYNC": "SYSTEM_ASYNC",
    "CONFIRMATION_FORM": "SYSTEM_IMMEDIATE",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:12]}"


def persisted_command(method):
    """Persist a successful domain command and roll back the working set on error."""

    @wraps(method)
    def wrapped(self, *args, **kwargs):
        # v0.2 is deliberately a single-process operational build. This lock
        # closes in-process check-then-write races; multi-worker deployment still
        # requires the later transactional repository.
        with COMMAND_LOCK:
            try:
                result = method(self, *args, **kwargs)
                self.state.persist()
                return result
            except Exception:
                # A domain error can happen after a nested helper touched the
                # working set. Reload to discard that uncommitted partial state.
                self.state.reload()
                raise

    return wrapped


class DomainError(Exception):
    def __init__(
        self,
        code: str,
        message_key: str,
        *,
        status_code: int = 400,
        field_path: str | None = None,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.message_key = message_key
        self.status_code = status_code
        self.field_path = field_path
        self.retryable = retryable
        self.details = details or {}

    def response(self) -> dict:
        return {
            "accepted": False,
            "error_code": self.code,
            "field_path": self.field_path,
            "retryable": self.retryable,
            "message_key": self.message_key,
            "details": self.details,
        }


class GrowthService:
    def __init__(
        self,
        state: InMemoryStore,
        agent_provider: BoundedAgentProvider | None = None,
        config_reader: ConfigReader | None = None,
    ) -> None:
        self.state = state
        self.agent_provider = agent_provider or BoundedAgentProvider()
        self.config_reader = config_reader or get_published_payload

    def _agent_policy(self) -> AgentPolicyConfig | None:
        payload = self.config_reader(ConfigKey.AGENT_POLICY)
        if payload is None:
            return None
        try:
            return AgentPolicyConfig.model_validate(payload)
        except (ValidationError, TypeError, ValueError):
            # A corrupt/unreadable published payload must never broaden the
            # Agent's authority. Falling back keeps the existing bounded policy.
            return None

    def _delivery_policy(self) -> tuple[DeliveryPolicyConfig, str]:
        payload = self.config_reader(ConfigKey.DELIVERY_POLICY)
        if payload is not None:
            try:
                return DeliveryPolicyConfig.model_validate(payload), "PUBLISHED"
            except (ValidationError, TypeError, ValueError):
                pass
        return DEFAULT_DELIVERY_POLICY, "CODE_DEFAULT"

    def _score_policy(self) -> tuple[ScoreGraduationConfig, str]:
        payload = self.config_reader(ConfigKey.SCORE_GRADUATION)
        if payload is not None:
            # v1 used capped, weighted dimensions and cannot faithfully express
            # either event-based policy.  v2 remains readable for historical
            # publications; v6 is the current policy and keeps v5 scoring.
            if not isinstance(payload, dict) or payload.get("policy_version") not in {
                "v2",
                "v3",
                "v4",
                "v5",
                "v6",
            }:
                return DEFAULT_SCORE_POLICY, "CODE_DEFAULT_LEGACY_CONFIG"
            try:
                return ScoreGraduationConfig.model_validate(payload), "PUBLISHED"
            except (ValidationError, TypeError, ValueError):
                return DEFAULT_SCORE_POLICY, "CODE_DEFAULT_INVALID_CONFIG"
        return DEFAULT_SCORE_POLICY, "CODE_DEFAULT"

    @staticmethod
    def _score_number(value: Any, default: float = 0.0) -> float:
        if value is None or isinstance(value, bool):
            return default
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return default
        if parsed != parsed or parsed in {float("inf"), float("-inf")}:
            return default
        return parsed

    @staticmethod
    def _dominant_source_mode(*modes: str | None) -> str:
        normalized = [str(mode).upper() for mode in modes if mode]
        if not normalized:
            return "MISSING_INPUT_ZERO"
        # The most synthetic input wins so a dimension can never look more
        # trustworthy than its least trustworthy score component.
        precedence = (
            "SOURCE_MISSING",
            "MISSING_INPUT_ZERO",
            "TASK_STATUS_INVALID",
            "COMPLAINT_LEVEL_MAPPING_INCOMPLETE",
            "TASK_BASELINE_INCOMPLETE",
            # Compatibility only: no current projection emits this old name.
            "TASK_STATUS_PARTIAL",
            "LEGACY_DIMENSION",
            "MOCK_SIMULATION",
            "MOCK_PROXY",
            "MOCK",
            "SYSTEM_TASK_STATUS",
            "DERIVED_REAL",
            "REAL",
        )
        for source_mode in precedence:
            if source_mode in normalized:
                return source_mode
        return normalized[0] if len(set(normalized)) == 1 else "MIXED"

    @staticmethod
    def _score_component(
        *,
        code: str,
        metric: str,
        value: float,
        score: float,
        source_mode: str,
        points_per_unit: float | None = None,
        maximum_points: float | None = None,
        achievement_rate: float | None = None,
        operator: str | None = None,
        threshold: float | None = None,
        milestone_achieved: bool | None = None,
        settlement_mode: str | None = None,
    ) -> dict[str, Any]:
        component: dict[str, Any] = {
            "code": code,
            "metric": metric,
            "value": round(value, 4),
            "score": round(score, 2),
            "source_mode": source_mode,
        }
        if points_per_unit is not None:
            component["points_per_unit"] = points_per_unit
        if maximum_points is not None:
            component["maximum_points"] = maximum_points
        if achievement_rate is not None:
            component["achievement_rate"] = achievement_rate
        if operator is not None:
            component["operator"] = operator
        if threshold is not None:
            component["threshold"] = threshold
        if milestone_achieved is not None:
            component["milestone_achieved"] = milestone_achieved
        if settlement_mode is not None:
            component["settlement_mode"] = settlement_mode
        return component

    @staticmethod
    def _hard_gate_item(
        *,
        code: str,
        metric: str,
        operator: str,
        threshold: Any,
        actual: Any,
        met: bool,
        source_mode: str,
    ) -> dict[str, Any]:
        return {
            "code": code,
            "metric": metric,
            "operator": operator,
            "threshold": threshold,
            "actual": actual,
            "met": met,
            "source_mode": source_mode,
        }

    @staticmethod
    def _metric_payload(teacher: dict) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        nested_payload = teacher.get("payload") if isinstance(teacher.get("payload"), dict) else {}
        metric_inputs = teacher.get("metric_inputs")
        if metric_inputs is None:
            metric_inputs = nested_payload.get("metric_inputs")
        provenance = teacher.get("metric_provenance")
        if provenance is None:
            provenance = nested_payload.get("metric_provenance", nested_payload.get("provenance", {}))
        return (
            metric_inputs if isinstance(metric_inputs, dict) else None,
            provenance if isinstance(provenance, dict) else {},
        )

    def _score_account_values(
        self,
        teacher_ids: set[str] | list[str] | tuple[str, ...],
    ) -> dict[str, dict[str, dict[str, Any]]]:
        """Return trusted task-status score overlays when the store supports them."""

        reader = getattr(self.state, "score_account_values", None)
        return reader(teacher_ids) if callable(reader) else {}

    def _project_teacher_scoring(
        self,
        teacher: dict,
        resolved_score_policy: tuple[ScoreGraduationConfig, str] | None = None,
        score_account_overrides: dict[str, dict[str, Any]] | None = None,
    ) -> dict:
        projected = deepcopy(teacher)
        policy, policy_source = resolved_score_policy or self._score_policy()
        metrics, metric_provenance = self._metric_payload(projected)
        if score_account_overrides is None:
            score_account_overrides = self._score_account_values(
                {str(projected["teacher_id"])}
            ).get(str(projected["teacher_id"]), {})
        labels = {
            "RELIABILITY": "可靠性",
            "USER_FEEDBACK": "用户反馈",
            "CLASS_QUALITY": "课堂质量",
            "CAPACITY": (
                "供给达标（Peak slots）"
                if policy.policy_version in MILESTONE_POLICY_VERSIONS
                else "供给任务（选修）"
                if policy.policy_version == "v3"
                else "产能"
            ),
            "NEW_TEACHER_TASK": (
                "成长任务（必修）"
                if policy.policy_version in MANDATORY_GROWTH_POLICY_VERSIONS
                else "新师任务"
            ),
        }

        def provenance(metric: str, *, fallback_mode: str = "MISSING_INPUT_ZERO", note: str = "") -> dict:
            item = metric_provenance.get(metric)
            if isinstance(item, dict) and item.get("source_mode"):
                return item
            return {
                "source_mode": fallback_mode,
                "source_field": None,
                "batch_id": projected.get("source_batch_id"),
                "note": note or "Metric was absent; scoring used an explicit safe default.",
            }

        def source_mode(metric: str, **kwargs: Any) -> str:
            return str(provenance(metric, **kwargs)["source_mode"])

        dimensions: list[dict[str, Any]] = []
        score_by_code: dict[str, float] = {}
        metric_values: dict[str, Any] = {}
        metric_sources: dict[str, str] = {}
        mandatory_task_assignment_count = 0
        mandatory_task_completed_count = 0
        mandatory_task_expected_count = 10

        if metrics is not None:
            def nonnegative(metric: str, default: float = 0.0) -> float:
                return max(self._score_number(metrics.get(metric), default), 0.0)

            capacity_account = (
                None
                if policy.policy_version in MILESTONE_POLICY_VERSIONS
                else score_account_overrides.get("CAPACITY")
            )
            mandatory_task_assignment_count = int(
                nonnegative("mandatory_task_assignment_count")
            )
            mandatory_task_completed_count = int(
                nonnegative("mandatory_task_completed_count")
            )
            mandatory_task_expected_count = int(
                nonnegative("mandatory_task_expected_count", 10)
            )
            task_account = score_account_overrides.get("NEW_TEACHER_TASK")
            if task_account:
                mandatory_task_assignment_count = int(
                    self._score_number(task_account.get("assignment_count"), 0)
                )
                mandatory_task_completed_count = int(
                    self._score_number(task_account.get("completed_count"), 0)
                )
                mandatory_task_expected_count = int(
                    self._score_number(task_account.get("expected_count"), 10)
                )
            task_value = (
                max(self._score_number(task_account.get("score")), 0.0)
                if task_account
                else nonnegative("new_teacher_task_score")
            )
            if policy.policy_version in MILESTONE_POLICY_VERSIONS:
                capacity_rule = policy.scoring_items.capacity
                peak_slot_count = nonnegative("peak_slot_cnt")
                current_threshold_met = peak_slot_count >= capacity_rule.threshold
                capacity_milestone_achieved = bool(
                    metrics.get("capacity_milestone_achieved", False)
                ) or current_threshold_met
                capacity_value = peak_slot_count
                capacity_score = (
                    float(capacity_rule.score_value)
                    if capacity_milestone_achieved
                    else 0.0
                )
                capacity_source_mode = source_mode(
                    "capacity_score",
                    fallback_mode=(
                        "DERIVED_REAL"
                        if source_mode("peak_slot_cnt") in {"REAL", "DERIVED_REAL"}
                        else source_mode("peak_slot_cnt")
                    ),
                    note=(
                        "Capacity is awarded only by the locked Peak-slot milestone; "
                        "historical task score accounts are ignored."
                    ),
                )
            else:
                capacity_rule = policy.scoring_items.capacity
                capacity_value = (
                    max(self._score_number(capacity_account.get("score")), 0.0)
                    if capacity_account
                    else nonnegative("capacity_score")
                )
                capacity_score = min(capacity_value, capacity_rule.maximum_points)
                peak_slot_count = 0.0
                capacity_milestone_achieved = False
                capacity_source_mode = (
                    str(capacity_account["source_mode"])
                    if capacity_account
                    else source_mode("capacity_score")
                )
            task_score = min(task_value, policy.scoring_items.new_teacher_tasks.maximum_points)

            praise = nonnegative("feedback_praise_cnt")
            favorite = nonnegative("feedback_favorite_cnt")
            rebook = nonnegative("completed_again_student_15d_cnt")
            praise_score = praise * policy.scoring_items.feedback_praise.points_per_unit
            favorite_score = favorite * policy.scoring_items.feedback_favorite.points_per_unit
            rebook_score = rebook * policy.scoring_items.feedback_rebook_15d.points_per_unit

            if metrics.get("on_time_completed_cnt") is not None:
                on_time = nonnegative("on_time_completed_cnt")
                on_time_mode = source_mode("on_time_completed_cnt")
            elif metrics.get("total_completed_cnt") is not None:
                # Compatibility for payloads produced before the derived field
                # was promoted: a punctual completion excludes late or early
                # lessons. Counts cannot make the result negative.
                on_time = max(
                    nonnegative("total_completed_cnt")
                    - nonnegative("late_cnt")
                    - nonnegative("early_cnt"),
                    0.0,
                )
                derived_inputs_mode = self._dominant_source_mode(
                    source_mode("total_completed_cnt"),
                    source_mode("late_cnt"),
                    source_mode("early_cnt"),
                )
                on_time_mode = (
                    "DERIVED_REAL"
                    if derived_inputs_mode in {"REAL", "DERIVED_REAL"}
                    else derived_inputs_mode
                )
            else:
                # Old transitional payloads called this stricter source metric
                # perfect_cnt. It is not used when the formal on-time inputs are
                # available.
                on_time = nonnegative("perfect_cnt")
                on_time_mode = source_mode("perfect_cnt")
            peak = nonnegative("peak_completed_cnt")
            on_time_score = on_time * policy.scoring_items.reliability_on_time.points_per_unit
            peak_score = peak * policy.scoring_items.reliability_peak.points_per_unit

            completed = nonnegative("total_completed_cnt")
            quality_rule = policy.scoring_items.classroom_quality
            if getattr(quality_rule, "metric", None) == "perfect_cnt":
                perfect = nonnegative("perfect_cnt")
                quality_source_mode = source_mode(
                    "perfect_cnt",
                    fallback_mode="SOURCE_MISSING",
                    note="perfect_cnt was unavailable; classroom-quality scoring used zero.",
                )
                if quality_source_mode in {"REAL", "DERIVED_REAL"}:
                    quality_source_mode = "DERIVED_REAL"
                quality_score = perfect * quality_rule.points_per_unit
                quality_component = self._score_component(
                    code="CLASS_QUALITY_PERFECT_COUNT",
                    metric="perfect_cnt",
                    value=perfect,
                    points_per_unit=quality_rule.points_per_unit,
                    score=quality_score,
                    source_mode=quality_source_mode,
                )
            else:
                quality_rate = self._score_number(
                    metrics.get("class_quality_no_issue_rate"),
                    0.0,
                )
                quality_rate = min(max(quality_rate, 0.0), 1.0)
                quality_rate_mode = source_mode(
                    "class_quality_no_issue_rate",
                    fallback_mode="SOURCE_MISSING",
                    note="Classroom-quality evidence was unavailable; scoring used zero.",
                )
                quality_score = completed * quality_rule.points_per_unit * quality_rate
                quality_component = self._score_component(
                    code="CLASS_QUALITY_NO_ISSUE",
                    metric="total_completed_cnt",
                    value=completed,
                    points_per_unit=quality_rule.points_per_unit,
                    achievement_rate=quality_rate,
                    score=quality_score,
                    source_mode=self._dominant_source_mode(
                        source_mode("total_completed_cnt"), quality_rate_mode
                    ),
                )

            component_sets = {
                "RELIABILITY": [
                    self._score_component(
                        code="ON_TIME_COMPLETED",
                        metric="on_time_completed_cnt",
                        value=on_time,
                        points_per_unit=policy.scoring_items.reliability_on_time.points_per_unit,
                        score=on_time_score,
                        source_mode=on_time_mode,
                    ),
                    self._score_component(
                        code="PEAK_COMPLETED",
                        metric="peak_completed_cnt",
                        value=peak,
                        points_per_unit=policy.scoring_items.reliability_peak.points_per_unit,
                        score=peak_score,
                        source_mode=source_mode("peak_completed_cnt"),
                    ),
                ],
                "USER_FEEDBACK": [
                    self._score_component(
                        code="FEEDBACK_PRAISE",
                        metric="feedback_praise_cnt",
                        value=praise,
                        points_per_unit=policy.scoring_items.feedback_praise.points_per_unit,
                        score=praise_score,
                        source_mode=source_mode("feedback_praise_cnt"),
                    ),
                    self._score_component(
                        code="FEEDBACK_FAVORITE",
                        metric="feedback_favorite_cnt",
                        value=favorite,
                        points_per_unit=policy.scoring_items.feedback_favorite.points_per_unit,
                        score=favorite_score,
                        source_mode=source_mode("feedback_favorite_cnt"),
                    ),
                    self._score_component(
                        code="FEEDBACK_REBOOK_15D",
                        metric="completed_again_student_15d_cnt",
                        value=rebook,
                        points_per_unit=policy.scoring_items.feedback_rebook_15d.points_per_unit,
                        score=rebook_score,
                        source_mode=source_mode("completed_again_student_15d_cnt"),
                    ),
                ],
                "CLASS_QUALITY": [quality_component],
                "CAPACITY": [
                    self._score_component(
                        code=(
                            capacity_rule.milestone_id
                            if policy.policy_version in MILESTONE_POLICY_VERSIONS
                            else "CAPACITY_BASE"
                        ),
                        metric=(
                            capacity_rule.metric
                            if policy.policy_version in MILESTONE_POLICY_VERSIONS
                            else "capacity_score"
                        ),
                        value=capacity_value,
                        maximum_points=capacity_rule.maximum_points,
                        score=capacity_score,
                        source_mode=capacity_source_mode,
                        operator=(
                            capacity_rule.operator
                            if policy.policy_version in MILESTONE_POLICY_VERSIONS
                            else None
                        ),
                        threshold=(
                            capacity_rule.threshold
                            if policy.policy_version in MILESTONE_POLICY_VERSIONS
                            else None
                        ),
                        milestone_achieved=(
                            capacity_milestone_achieved
                            if policy.policy_version in MILESTONE_POLICY_VERSIONS
                            else None
                        ),
                        settlement_mode=(
                            capacity_rule.settlement_mode
                            if policy.policy_version in MILESTONE_POLICY_VERSIONS
                            else None
                        ),
                    )
                ],
                "NEW_TEACHER_TASK": [
                    self._score_component(
                        code="NEW_TEACHER_TASK_BASE",
                        metric="new_teacher_task_score",
                        value=task_value,
                        maximum_points=policy.scoring_items.new_teacher_tasks.maximum_points,
                        score=task_score,
                        source_mode=(
                            str(task_account["source_mode"])
                            if task_account
                            else source_mode("new_teacher_task_score")
                        ),
                    )
                ],
            }
            score_by_code = {
                "RELIABILITY": on_time_score + peak_score,
                "USER_FEEDBACK": praise_score + favorite_score + rebook_score,
                "CLASS_QUALITY": quality_score,
                "CAPACITY": capacity_score,
                "NEW_TEACHER_TASK": task_score,
            }
            for code in SCORE_DIMENSION_CONFIG_KEYS:
                components = component_sets[code]
                dimensions.append(
                    {
                        "code": code,
                        "label": labels[code],
                        "score": round(score_by_code[code], 2),
                        "source_mode": self._dominant_source_mode(
                            *(component["source_mode"] for component in components)
                        ),
                        "components": components,
                    }
                )

            severe_redline = bool(metrics.get("severe_redline_event", False))
            l0_evidence = score_account_overrides.get("L0_COMPLAINT")
            l0_complaint_count = (
                max(int(self._score_number(l0_evidence.get("count"), 0)), 0)
                if l0_evidence
                else max(int(self._score_number(metrics.get("l0_complaint_cnt"), 0)), 0)
            )
            metric_values = {
                "total_completed_cnt": completed,
                "late_cnt": nonnegative("late_cnt"),
                "early_cnt": nonnegative("early_cnt"),
                "real_absent_cnt": nonnegative("real_absent_cnt"),
                "severe_redline_event": severe_redline,
                "l0_complaint_cnt": l0_complaint_count,
            }
            metric_sources = {
                key: source_mode(
                    key,
                    fallback_mode="SOURCE_MISSING",
                    note="Metric evidence was unavailable; scoring used zero.",
                )
                for key in metric_values
            }
            if l0_evidence:
                metric_sources["l0_complaint_cnt"] = str(
                    l0_evidence["source_mode"]
                )
        else:
            # Compatibility only: old local Mock teachers have five already-
            # scored dimensions but no v2 metric inputs. Their score remains
            # visible and auditable; new course formulas are not reverse-engineered.
            legacy_by_code = {
                item.get("code"): item
                for item in projected.get("dimensions", [])
                if item.get("code") in SCORE_DIMENSION_CONFIG_KEYS
            }
            for code in SCORE_DIMENSION_CONFIG_KEYS:
                legacy = legacy_by_code.get(code, {})
                account_override = score_account_overrides.get(code)
                score = self._score_number(
                    account_override.get("score") if account_override else legacy.get("score"),
                    0.0,
                )
                if (
                    policy.policy_version in MILESTONE_POLICY_VERSIONS
                    and code == "CAPACITY"
                ):
                    # A historical pre-v4 Mock/account value is not evidence that
                    # CAPACITY_PEAK_SLOT_40 was ever achieved.
                    account_override = None
                    score = 0.0
                if policy.policy_version in MANDATORY_GROWTH_POLICY_VERSIONS:
                    # v3 contains no deductions.  Historical negative Mock
                    # dimensions remain stored, but the current projection does
                    # not carry a deduction into the v3 total.
                    score = max(score, 0.0)
                score_by_code[code] = score
                dimensions.append(
                    {
                        "code": code,
                        "label": legacy.get("label", labels[code]),
                        "score": round(score, 2),
                        "source_mode": (
                            str(account_override["source_mode"])
                            if account_override
                            else legacy.get("source_mode", "LEGACY_DIMENSION")
                        ),
                        "components": deepcopy(legacy.get("components", [])),
                    }
                )
            metric_values = {
                "total_completed_cnt": max(
                    self._score_number(projected.get("lessons_completed"), 0.0), 0.0
                ),
                "late_cnt": 0.0,
                "early_cnt": 0.0,
                "real_absent_cnt": 0.0,
                "severe_redline_event": False,
                "l0_complaint_cnt": 0,
            }
            metric_sources = {
                "total_completed_cnt": "LEGACY_DIMENSION",
                "late_cnt": "MOCK",
                "early_cnt": "MOCK",
                "real_absent_cnt": "MOCK",
                "severe_redline_event": "MOCK",
                "l0_complaint_cnt": "SOURCE_MISSING",
            }

        projected["dimensions"] = dimensions
        capacity_score = score_by_code["CAPACITY"]
        mandatory_growth_score = score_by_code["NEW_TEACHER_TASK"]
        base_score = capacity_score + mandatory_growth_score
        user_feedback_score = score_by_code["USER_FEEDBACK"]
        reliability_score = score_by_code["RELIABILITY"]
        raw_total = round(sum(score_by_code.values()), 2)
        thresholds = policy.thresholds
        if policy.policy_version in MANDATORY_GROWTH_POLICY_VERSIONS:
            external_score = min(raw_total, thresholds.gold_external_score)
        elif raw_total < thresholds.graduation_raw_score:
            external_score = (
                raw_total
                * thresholds.graduation_external_score
                / thresholds.graduation_raw_score
            )
        elif raw_total < thresholds.gold_raw_score:
            external_score = thresholds.graduation_external_score + (
                (raw_total - thresholds.graduation_raw_score)
                * (thresholds.gold_external_score - thresholds.graduation_external_score)
                / (thresholds.gold_raw_score - thresholds.graduation_raw_score)
            )
        else:
            external_score = thresholds.gold_external_score

        dimension_source = {
            item["code"]: item["source_mode"] for item in projected["dimensions"]
        }
        graduation_config = policy.hard_gates.graduation
        graduation_score_met = raw_total >= thresholds.graduation_raw_score
        if policy.policy_version in {"v5", "v6"}:
            task_source_mode = dimension_source["NEW_TEACHER_TASK"]
            total_score_source_mode = self._dominant_source_mode(
                *dimension_source.values()
            )
            graduation_items = [
                self._hard_gate_item(
                    code="ALL_MANDATORY_GROWTH_TASKS_COMPLETED",
                    metric="mandatory_task_completed_count",
                    operator="==",
                    threshold=graduation_config.required_mandatory_task_count,
                    actual=mandatory_task_completed_count,
                    met=(
                        mandatory_task_assignment_count
                        == mandatory_task_expected_count
                        == graduation_config.required_mandatory_task_count
                        and mandatory_task_completed_count
                        == graduation_config.required_mandatory_task_count
                        and task_source_mode == "SYSTEM_TASK_STATUS"
                    ),
                    source_mode=task_source_mode,
                ),
                self._hard_gate_item(
                    code="NO_L0_COMPLAINT",
                    metric="l0_complaint_cnt",
                    operator="<=",
                    threshold=graduation_config.maximum_l0_complaint_count,
                    actual=metric_values["l0_complaint_cnt"],
                    met=(
                        metric_sources["l0_complaint_cnt"]
                        in {"REAL", "DERIVED_REAL"}
                        and metric_values["l0_complaint_cnt"]
                        <= graduation_config.maximum_l0_complaint_count
                    ),
                    source_mode=metric_sources["l0_complaint_cnt"],
                ),
                self._hard_gate_item(
                    code="MINIMUM_TOTAL_SCORE",
                    metric="raw_total_score",
                    operator=">=",
                    threshold=thresholds.graduation_raw_score,
                    actual=raw_total,
                    met=graduation_score_met,
                    source_mode=total_score_source_mode,
                ),
            ]
            graduation_gates_met = all(item["met"] for item in graduation_items)
            graduation_criteria_met = graduation_gates_met
        else:
            graduation_base_actual = (
                mandatory_growth_score
                if policy.policy_version in MANDATORY_GROWTH_POLICY_VERSIONS
                else base_score
            )
            graduation_base_source = (
                dimension_source["NEW_TEACHER_TASK"]
                if policy.policy_version in MANDATORY_GROWTH_POLICY_VERSIONS
                else self._dominant_source_mode(
                    dimension_source["CAPACITY"],
                    dimension_source["NEW_TEACHER_TASK"],
                )
            )
            graduation_items = [
                self._hard_gate_item(
                    code=(
                        "REQUIRED_MANDATORY_GROWTH_SCORE"
                        if policy.policy_version in MANDATORY_GROWTH_POLICY_VERSIONS
                        else "MINIMUM_BASE_SCORE"
                    ),
                    metric=(
                        "new_teacher_task_score"
                        if policy.policy_version in MANDATORY_GROWTH_POLICY_VERSIONS
                        else "base_score"
                    ),
                    operator=">=",
                    threshold=graduation_config.minimum_base_score,
                    actual=round(graduation_base_actual, 2),
                    met=(
                        graduation_base_actual >= graduation_config.minimum_base_score
                        and (
                            policy.policy_version
                            not in MANDATORY_GROWTH_POLICY_VERSIONS
                            or graduation_base_source == "SYSTEM_TASK_STATUS"
                        )
                    ),
                    source_mode=graduation_base_source,
                ),
                self._hard_gate_item(
                    code="MINIMUM_COMPLETED_LESSONS",
                    metric="total_completed_cnt",
                    operator=">=",
                    threshold=graduation_config.minimum_completed_lessons,
                    actual=metric_values["total_completed_cnt"],
                    met=(
                        metric_values["total_completed_cnt"]
                        >= graduation_config.minimum_completed_lessons
                    ),
                    source_mode=metric_sources["total_completed_cnt"],
                ),
                self._hard_gate_item(
                    code="POSITIVE_USER_FEEDBACK",
                    metric="user_feedback_score",
                    operator=">",
                    threshold=graduation_config.minimum_user_feedback_score_exclusive,
                    actual=round(user_feedback_score, 2),
                    met=(
                        user_feedback_score
                        > graduation_config.minimum_user_feedback_score_exclusive
                    ),
                    source_mode=dimension_source["USER_FEEDBACK"],
                ),
                self._hard_gate_item(
                    code="POSITIVE_RELIABILITY",
                    metric="reliability_score",
                    operator=">",
                    threshold=graduation_config.minimum_reliability_score_exclusive,
                    actual=round(reliability_score, 2),
                    met=(
                        reliability_score
                        > graduation_config.minimum_reliability_score_exclusive
                    ),
                    source_mode=dimension_source["RELIABILITY"],
                ),
                self._hard_gate_item(
                    code="NO_SEVERE_REDLINE",
                    metric="severe_redline_event",
                    operator="<=",
                    threshold=graduation_config.allow_severe_redline,
                    actual=metric_values["severe_redline_event"],
                    met=(
                        graduation_config.allow_severe_redline
                        or (
                            metric_sources["severe_redline_event"]
                            in {"REAL", "DERIVED_REAL"}
                            and not metric_values["severe_redline_event"]
                        )
                    ),
                    source_mode=metric_sources["severe_redline_event"],
                ),
            ]
            graduation_gates_met = all(item["met"] for item in graduation_items)
            graduation_criteria_met = graduation_score_met and graduation_gates_met
        graduation_source_modes = {item["source_mode"] for item in graduation_items}
        inherited_gate_source_mode = (
            "DERIVED_REAL"
            if graduation_source_modes
            <= {"REAL", "DERIVED_REAL", "SYSTEM_TASK_STATUS"}
            else "MIXED_DERIVED"
        )

        gold_score_met = raw_total >= thresholds.gold_raw_score
        inherited_gold_item = self._hard_gate_item(
            code="REQUIRES_GRADUATION_CRITERIA",
            metric="graduation_criteria_met",
            operator="==",
            threshold=True,
            actual=graduation_criteria_met,
            met=graduation_criteria_met,
            source_mode=inherited_gate_source_mode,
        )
        if policy.policy_version == "v6":
            gold_items = [
                inherited_gold_item,
                self._hard_gate_item(
                    code="MINIMUM_GOLD_TOTAL_SCORE",
                    metric="raw_total_score",
                    operator=">=",
                    threshold=thresholds.gold_raw_score,
                    actual=raw_total,
                    met=gold_score_met,
                    source_mode=self._dominant_source_mode(*dimension_source.values()),
                ),
            ]
        else:
            gold_config = policy.hard_gates.gold
            gold_items = [
                inherited_gold_item,
                self._hard_gate_item(
                    code="REQUIRED_BASE_SCORE",
                    metric="base_score",
                    operator="==",
                    threshold=gold_config.required_base_score,
                    actual=round(base_score, 2),
                    met=abs(base_score - gold_config.required_base_score) < 1e-9,
                    source_mode=self._dominant_source_mode(
                        dimension_source["CAPACITY"],
                        dimension_source["NEW_TEACHER_TASK"],
                    ),
                ),
                self._hard_gate_item(
                    code="MINIMUM_COMPLETED_LESSONS",
                    metric="total_completed_cnt",
                    operator=">=",
                    threshold=gold_config.minimum_completed_lessons,
                    actual=metric_values["total_completed_cnt"],
                    met=(
                        metric_values["total_completed_cnt"]
                        >= gold_config.minimum_completed_lessons
                    ),
                    source_mode=metric_sources["total_completed_cnt"],
                ),
                self._hard_gate_item(
                    code="MINIMUM_USER_FEEDBACK_SCORE",
                    metric="user_feedback_score",
                    operator=">=",
                    threshold=gold_config.minimum_user_feedback_score,
                    actual=round(user_feedback_score, 2),
                    met=user_feedback_score >= gold_config.minimum_user_feedback_score,
                    source_mode=dimension_source["USER_FEEDBACK"],
                ),
                self._hard_gate_item(
                    code="MAXIMUM_LATE_COUNT",
                    metric="late_cnt",
                    operator="<=",
                    threshold=gold_config.maximum_late_count,
                    actual=metric_values["late_cnt"],
                    met=metric_values["late_cnt"] <= gold_config.maximum_late_count,
                    source_mode=metric_sources["late_cnt"],
                ),
                self._hard_gate_item(
                    code="MAXIMUM_EARLY_COUNT",
                    metric="early_cnt",
                    operator="<=",
                    threshold=gold_config.maximum_early_count,
                    actual=metric_values["early_cnt"],
                    met=metric_values["early_cnt"] <= gold_config.maximum_early_count,
                    source_mode=metric_sources["early_cnt"],
                ),
                self._hard_gate_item(
                    code="MAXIMUM_REAL_ABSENT_COUNT",
                    metric="real_absent_cnt",
                    operator="<=",
                    threshold=gold_config.maximum_real_absent_count,
                    actual=metric_values["real_absent_cnt"],
                    met=(
                        metric_sources["real_absent_cnt"] in {"REAL", "DERIVED_REAL"}
                        and metric_values["real_absent_cnt"]
                        <= gold_config.maximum_real_absent_count
                    ),
                    source_mode=metric_sources["real_absent_cnt"],
                ),
            ]
        gold_gates_met = all(item["met"] for item in gold_items)
        # Gold is a higher tier, never an alternative path around graduation.
        gold_criteria_met = graduation_criteria_met and gold_score_met and gold_gates_met

        original_state = projected.get("graduation_state", "IN_PROGRESS")
        if original_state == "GRADUATED" or graduation_criteria_met:
            effective_state = "GRADUATED"
        else:
            effective_state = "IN_PROGRESS"

        projected.update(
            {
                "raw_total_score": raw_total,
                # Backward-compatible raw score. Consumers that show the locked
                # 0-200 teacher-facing scale must use external_display_score.
                "total_score": raw_total,
                "external_display_score": round(external_score, 2),
                "base_score": round(base_score, 2),
                "graduation_threshold": thresholds.graduation_raw_score,
                "gold_threshold": thresholds.gold_raw_score,
                "graduation_external_score": thresholds.graduation_external_score,
                "gold_external_score": thresholds.gold_external_score,
                "graduation_state": effective_state,
                "graduation_score_threshold_met": graduation_score_met,
                "graduation_criteria_met": graduation_criteria_met,
                "gold_score_threshold_met": gold_score_met,
                "gold_criteria_met": gold_criteria_met,
                "hard_gates": {
                    "graduation": {"met": graduation_gates_met, "items": graduation_items},
                    "gold": {
                        "met": gold_gates_met,
                        "inherits_graduation": True,
                        "items": gold_items,
                    },
                },
                # v1 response aliases kept during the UI transition.
                "graduation_total_threshold_met": graduation_score_met,
                "graduation_dimension_floors_met": graduation_gates_met,
                "score_policy_version": policy.policy_version,
                "score_policy_sha256": hashlib.sha256(
                    json.dumps(
                        policy.model_dump(mode="json"),
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest(),
                "score_projection_scope": "CURRENT_RUNTIME",
                "source_snapshot_score_policy_version": projected.get(
                    "score_policy_version"
                ),
                "source_snapshot_score_policy_sha256": projected.get(
                    "score_policy_sha256"
                ),
                "score_policy_source": policy_source,
                "graduation_effect": policy.graduation_effect,
            }
        )
        return projected

    def _delivery_policy_for_task(self, task: dict) -> DeliveryPolicyConfig:
        snapshot = task.get("delivery_policy_snapshot")
        if snapshot is not None:
            try:
                return DeliveryPolicyConfig.model_validate(snapshot)
            except (ValidationError, TypeError, ValueError):
                pass
        return self._delivery_policy()[0]

    @staticmethod
    def _operator_fields(actor_id: str | None) -> dict[str, str]:
        if not actor_id:
            return {}
        return {"actor_type": "OPERATOR", "actor_id": actor_id}

    def _assert_global_event(
        self,
        event_id: str,
        request_hash: str,
        event_type: str,
    ) -> None:
        existing_hash = self.state.global_event_hashes.get(event_id)
        if existing_hash is None:
            return
        if (
            existing_hash != request_hash
            or self.state.global_event_types.get(event_id) != event_type
        ):
            raise DomainError(
                "DUPLICATE_CONFLICT",
                "event.error.global_event_id_conflict",
                status_code=409,
                field_path="$.event_id",
                details={"existing_event_type": self.state.global_event_types.get(event_id)},
            )

    def _register_global_event(
        self,
        event_id: str,
        request_hash: str,
        event_type: str,
    ) -> None:
        self.state.global_event_hashes[event_id] = request_hash
        self.state.global_event_types[event_id] = event_type

    def reset(self) -> dict:
        self.state.reset()
        return {"ok": True, "reset_at": now_iso()}

    def dashboard(self) -> dict:
        resolved_score_policy = self._score_policy()
        score_policy, score_policy_source = resolved_score_policy
        score_policy_sha256 = hashlib.sha256(
            json.dumps(
                score_policy.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        teacher_rows = list(self.state.teachers.values())
        score_account_overrides = self._score_account_values(
            {str(item["teacher_id"]) for item in teacher_rows}
        )
        teachers = []
        for item in teacher_rows:
            account_override = score_account_overrides.get(str(item["teacher_id"]), {})
            teachers.append(
                self._project_teacher_scoring(
                    item,
                    resolved_score_policy,
                    account_override,
                )
            )
        tasks = list(self.state.tasks.values())
        cases = list(self.state.ops_cases.values())
        shared_task_counts = self.state.shared_assignment_counts()
        data_mode_counts: dict[str, int] = {}
        employment_status_counts: dict[str, int] = {}
        funnel_by_employment_status = {
            status: {
                "teacher_count": 0,
                "graduation_score_reached_count": 0,
                "graduation_criteria_met_count": 0,
                "gold_eligible_count": 0,
            }
            for status in ("on", "off", "hei")
        }
        for teacher in teachers:
            data_mode = str(teacher.get("data_mode") or "UNKNOWN").upper()
            data_mode_counts[data_mode] = data_mode_counts.get(data_mode, 0) + 1
            employment_status = str(teacher.get("employment_status") or "UNKNOWN").lower()
            employment_status_counts[employment_status] = (
                employment_status_counts.get(employment_status, 0) + 1
            )
            normalized_employment_status = str(
                teacher.get("employment_status") or ""
            ).strip().casefold()
            employment_funnel = funnel_by_employment_status.get(
                normalized_employment_status
            )
            if employment_funnel is not None:
                employment_funnel["teacher_count"] += 1
                employment_funnel["graduation_score_reached_count"] += int(
                    teacher["graduation_score_threshold_met"]
                )
                employment_funnel["graduation_criteria_met_count"] += int(
                    teacher["graduation_criteria_met"]
                )
                employment_funnel["gold_eligible_count"] += int(
                    teacher["gold_criteria_met"]
                )

        def counts_as_active(teacher: dict) -> bool:
            if teacher.get("employment_status") is not None:
                return (
                    str(teacher["employment_status"]).lower() == "on"
                    and teacher["graduation_state"] != "GRADUATED"
                )
            # Compatibility with the four legacy local Mock teachers.
            return teacher["graduation_state"] == "IN_PROGRESS"

        return {
            "as_of": now_iso(),
            "graduation_threshold": score_policy.thresholds.graduation_raw_score,
            "gold_threshold": score_policy.thresholds.gold_raw_score,
            "graduation_external_score": score_policy.thresholds.graduation_external_score,
            "gold_external_score": score_policy.thresholds.gold_external_score,
            "graduation_effect": score_policy.graduation_effect,
            "score_policy_version": score_policy.policy_version,
            "score_policy_sha256": score_policy_sha256,
            "score_projection_scope": "CURRENT_RUNTIME",
            "score_policy_source": score_policy_source,
            "teacher_count": len(teachers),
            "active_teacher_count": sum(counts_as_active(item) for item in teachers),
            "settlement_pending_count": sum(item["graduation_state"] == "SETTLEMENT_PENDING" for item in teachers),
            "graduation_score_reached_count": sum(
                item["graduation_score_threshold_met"] for item in teachers
            ),
            "graduation_criteria_met_count": sum(
                item["graduation_criteria_met"] for item in teachers
            ),
            "gold_score_reached_count": sum(item["gold_score_threshold_met"] for item in teachers),
            "gold_eligible_count": sum(item["gold_criteria_met"] for item in teachers),
            "gold_criteria_met_count": sum(item["gold_criteria_met"] for item in teachers),
            "data_mode_counts": data_mode_counts,
            "employment_status_counts": employment_status_counts,
            "funnel_by_employment_status": funnel_by_employment_status,
            "issued_task_count": shared_task_counts["total"],
            "active_shared_task_count": shared_task_counts["active"],
            "unacknowledged_task_count": 0,
            "open_case_count": sum(item["status"] == "OPEN" for item in cases),
            "completed_execution_count": shared_task_counts["completed"],
            "notification_integration_failure_count": sum(
                item["status"] == "INTEGRATION_FAILED" for item in self.state.notifications.values()
            ),
            "p0_confirmation_waiting_count": sum(self._counts_as_teacher_waiting(task) for task in tasks),
            "dimension_averages": self._dimension_averages(teachers),
        }

    @staticmethod
    def _dimension_averages(teachers: list[dict]) -> list[dict]:
        if not teachers:
            return []
        bucket: dict[str, dict] = {}
        for teacher in teachers:
            for dimension in teacher["dimensions"]:
                entry = bucket.setdefault(
                    dimension["code"],
                    {
                        "code": dimension["code"],
                        "label": dimension["label"],
                        "minimum": dimension.get("minimum"),
                        "weight": dimension.get("weight"),
                        "total": 0,
                        "count": 0,
                    },
                )
                entry["total"] += dimension["score"]
                entry["count"] += 1
        return [
            {
                "code": item["code"],
                "label": item["label"],
                "average": round(item["total"] / item["count"], 1),
                "minimum": item["minimum"],
                "weight": item["weight"],
            }
            for item in bucket.values()
        ]

    @staticmethod
    def _teacher_list_summary(projected: dict) -> dict:
        """Return the deliberately small card contract for the paged teacher list.

        A detail projection contains raw metric inputs, provenance, hard-gate
        evidence and dimension components. Those fields are useful after a
        teacher is opened, but returning them for every teacher made the list
        response several megabytes. Selecting the allow-list here also prevents
        a future detail-only field from accidentally leaking back into the list.
        """

        scalar_fields = (
            "teacher_id",
            "name",
            "avatar",
            "camp_day",
            "lessons_completed",
            "raw_total_score",
            "total_score",
            "external_display_score",
            "base_score",
            "graduation_threshold",
            "gold_threshold",
            "graduation_external_score",
            "gold_external_score",
            "graduation_effect",
            "graduation_score_threshold_met",
            "graduation_criteria_met",
            "gold_score_threshold_met",
            "gold_criteria_met",
            "graduation_state",
            "data_mode",
            "employment_status",
            "source_batch_id",
            "source_snapshot_label",
            "score_policy_version",
            "score_policy_source",
            "updated_at",
            "active_task_count",
            "open_case_count",
            "next_best_action",
        )
        summary = {
            field: deepcopy(projected[field])
            for field in scalar_fields
            if field in projected
        }
        summary["dimensions"] = [
            {
                field: deepcopy(dimension[field])
                for field in ("code", "label", "score", "source_mode")
                if field in dimension
            }
            for dimension in projected.get("dimensions", [])
        ]
        summary["risk_tags"] = deepcopy(projected.get("risk_tags", []))
        return summary

    def list_teachers(
        self,
        *,
        page: int = 1,
        page_size: int = 24,
        keyword: str | None = None,
        data_mode: str | None = None,
        employment_status: str | None = None,
    ) -> dict:
        """Filter before projection and score only the requested page."""

        all_teachers = list(self.state.teachers.values())
        normalized_keyword = (keyword or "").strip().casefold()
        normalized_data_mode = (data_mode or "").strip().upper()
        normalized_employment = (employment_status or "").strip().casefold()
        if normalized_data_mode == "ALL":
            normalized_data_mode = ""
        if normalized_employment == "all":
            normalized_employment = ""

        def matches(teacher: dict) -> bool:
            teacher_mode = str(teacher.get("data_mode") or "UNKNOWN").upper()
            teacher_employment = str(teacher.get("employment_status") or "UNKNOWN").casefold()
            if normalized_data_mode and teacher_mode != normalized_data_mode:
                return False
            if normalized_employment and teacher_employment != normalized_employment:
                return False
            if not normalized_keyword:
                return True
            haystack = " ".join(
                str(value or "")
                for value in (
                    teacher.get("teacher_id"),
                    teacher.get("name"),
                    teacher.get("employment_status"),
                )
            ).casefold()
            return normalized_keyword in haystack

        mode_rank = {"REAL": 0, "MIXED": 1, "MOCK": 2, "UNKNOWN": 3}
        matched = sorted(
            (teacher for teacher in all_teachers if matches(teacher)),
            key=lambda teacher: (
                mode_rank.get(str(teacher.get("data_mode") or "UNKNOWN").upper(), 3),
                str(teacher.get("teacher_id") or "").casefold(),
            ),
        )
        total = len(matched)
        total_pages = (total + page_size - 1) // page_size if total else 0
        start = (page - 1) * page_size
        page_teachers = matched[start : start + page_size]

        # Runtime counts are computed once for the requested page. The score
        # policy is likewise resolved once, rather than once per teacher.
        page_teacher_ids = {str(teacher["teacher_id"]) for teacher in page_teachers}
        active_task_counts = {teacher_id: 0 for teacher_id in page_teacher_ids}
        open_case_counts = {teacher_id: 0 for teacher_id in page_teacher_ids}
        terminal_statuses = {"COMPLETED", "FAILED_FINAL", "CANCELLED_BY_WITHDRAWAL"}
        for task in self.state.tasks.values():
            teacher_id = str(task.get("teacher_id"))
            if teacher_id in active_task_counts and self._execution_status(task["task_id"]) not in terminal_statuses:
                active_task_counts[teacher_id] += 1
        for case in self.state.ops_cases.values():
            teacher_id = str(case.get("teacher_id"))
            if teacher_id in open_case_counts and case.get("status") == "OPEN":
                open_case_counts[teacher_id] += 1

        resolved_score_policy = self._score_policy()
        score_account_overrides = self._score_account_values(page_teacher_ids)
        items: list[dict] = []
        for teacher in page_teachers:
            teacher_id = str(teacher["teacher_id"])
            account_override = score_account_overrides.get(teacher_id, {})
            row = self._project_teacher_scoring(
                teacher,
                resolved_score_policy,
                account_override,
            )
            row["active_task_count"] = active_task_counts[teacher_id]
            row["open_case_count"] = open_case_counts[teacher_id]
            items.append(self._teacher_list_summary(row))

        available_data_modes = sorted(
            {str(teacher.get("data_mode") or "UNKNOWN").upper() for teacher in all_teachers},
            key=lambda value: (mode_rank.get(value, 3), value),
        )
        available_employment_statuses = sorted(
            {str(teacher.get("employment_status") or "UNKNOWN") for teacher in all_teachers},
            key=lambda value: value.casefold(),
        )
        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "filters": {
                "keyword": (keyword or "").strip(),
                "data_mode": normalized_data_mode or None,
                "employment_status": normalized_employment or None,
                "available_data_modes": available_data_modes,
                "available_employment_statuses": available_employment_statuses,
            },
        }

    def teacher_options(self) -> list[dict]:
        """Return selector data without invoking the score policy or projection."""

        def option(teacher: dict) -> dict:
            data_mode = str(teacher.get("data_mode") or "UNKNOWN").upper()
            graduation_state = str(teacher.get("graduation_state") or "IN_PROGRESS")
            timezone_source_mode = self._teacher_timezone_source_mode(teacher)
            timezone_missing = not str(teacher.get("timezone") or "").strip()
            timezone_untrusted = (
                data_mode in {"MIXED", "REAL"}
                and timezone_source_mode in UNTRUSTED_TIMEZONE_SOURCE_MODES
            )
            blockers: list[str] = []
            if graduation_state != "IN_PROGRESS":
                blockers.append("GRADUATED")
            if timezone_missing or timezone_untrusted:
                blockers.append("TIMEZONE_UNAVAILABLE")
            return {
                "teacher_id": str(teacher["teacher_id"]),
                "name": str(teacher.get("name") or teacher["teacher_id"]),
                "data_mode": data_mode,
                "employment_status": teacher.get("employment_status"),
                "graduation_state": graduation_state,
                "task_issuance_blockers": blockers,
            }

        return sorted(
            [option(teacher) for teacher in self.state.teachers.values()],
            key=lambda teacher: teacher["teacher_id"].casefold(),
        )

    def teacher_detail(self, teacher_id: str) -> dict:
        teacher = self._teacher(teacher_id)
        resolved_score_policy = self._score_policy()
        score_account_overrides = self._score_account_values({teacher_id}).get(
            teacher_id,
            {},
        )
        # task_assignments is the current cross-end task fact.  Keep the
        # retired legacy task projection below only for compatibility with
        # endpoints that have not yet migrated.
        from .task_service import TaskService

        task_assignments = TaskService(
            getattr(self.state, "engine", None)
        ).list_assignments(teacher_id=teacher_id)
        teacher_task_ids = {
            task["task_id"]
            for task in self.state.tasks.values()
            if task["teacher_id"] == teacher_id
        }
        projected_teacher = self._project_teacher_scoring(
            teacher,
            resolved_score_policy,
            score_account_overrides,
        )
        return {
            **projected_teacher,
            "tasks": [self.task_detail(task["task_id"]) for task in self.state.tasks.values() if task["teacher_id"] == teacher_id],
            "task_assignments": task_assignments,
            "ops_cases": [deepcopy(case) for case in self.state.ops_cases.values() if case["teacher_id"] == teacher_id],
            "notifications": [
                deepcopy(notification)
                for notification in self.state.notifications.values()
                if notification["teacher_id"] == teacher_id
            ],
            "events": [
                deepcopy(event)
                for event in self.state.events
                if event.get("teacher_id") == teacher_id
                or event.get("task_id") in teacher_task_ids
            ],
        }

    def list_templates(self) -> list[dict]:
        return [deepcopy(item) for item in self.state.template_versions.values()]

    @persisted_command
    def evaluate_triggers(self, teacher_id: str) -> dict:
        teacher = self._project_teacher_scoring(self._teacher(teacher_id))
        valid_signals = [item for item in teacher["signals"] if item["status"] == "VALID"]
        active_tasks = [
            task
            for task in self.state.tasks.values()
            if task["teacher_id"] == teacher_id and not self._task_is_terminal(task)
        ]
        # An already-issued obligation remains governed by the immutable
        # template snapshot that created it. Publishing v2 can affect future
        # assignments, but must not silently rewrite a live v1 obligation.
        active_template_ids = {task["template_id"] for task in active_tasks}
        candidates: list[tuple[dict, dict]] = []
        for signal in valid_signals:
            signal_templates = [
                template
                for template in self.state.templates.values()
                if template["status"] == "PUBLISHED"
                and template["template_id"] not in active_template_ids
                and template_matches_signal(template, teacher, signal)
            ]
            candidates.extend((signal, template) for template in signal_templates)
        for task in active_tasks:
            template = self._template_snapshot_for_task(task)
            matching_signals = [
                signal for signal in valid_signals if template_matches_signal(template, teacher, signal)
            ]
            candidates.extend((signal, template) for signal in matching_signals)

        priority_order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
        candidates.sort(
            key=lambda item: (
                priority_order[item[1]["priority"]],
                item[1]["due_hours"],
                item[1]["template_id"],
            )
        )
        # A template can be supported by several signals, but one evaluation may
        # only issue one assignment for that template. The first item is already
        # the highest-priority evidence after sorting.
        unique_candidates: list[tuple[dict, dict]] = []
        seen_template_ids: set[str] = set()
        for candidate in candidates:
            template_id = candidate[1]["template_id"]
            if template_id in seen_template_ids:
                continue
            seen_template_ids.add(template_id)
            unique_candidates.append(candidate)
        candidates = unique_candidates
        has_existing_tasks = any(task["teacher_id"] == teacher_id for task in self.state.tasks.values())
        decision_route = "AGENT" if len(candidates) > 1 or has_existing_tasks else "DIRECT"
        action_catalog = self._build_agent_action_catalog(teacher, valid_signals, candidates)
        planning_run = self._planning_record(
            teacher_id,
            valid_signals,
            candidates,
            action_catalog,
            decision_route,
        )
        selected_action_ids = set(planning_run["selected_action_ids"])
        selected_actions = [
            action for action in action_catalog if action["action_id"] in selected_action_ids
        ]
        action_results = [self._execute_agent_action(teacher, action) for action in selected_actions]
        preferred_task_ids = [
            result["task_id"]
            for result in action_results
            if result.get("task_id") and result["action_type"] in {"CREATE_FROM_TEMPLATE", "REOPEN"}
        ]
        self._rebalance_teacher_tasks(teacher_id, preferred_task_ids=preferred_task_ids)

        created = [
            self.task_detail(result["task_id"])
            for result in action_results
            if result["action_type"] in {"CREATE_FROM_TEMPLATE", "REOPEN"}
            and result["status"] == "EXECUTED"
        ]
        selected_template_ids = set(planning_run["selected_template_ids"])
        existing_template_ids = {
            task["template_id"]
            for task in self.state.tasks.values()
            if task["teacher_id"] == teacher_id
        }
        skipped = [
            {
                "template_id": template["template_id"],
                "reason": (
                    "DEDUPED" if template["template_id"] in existing_template_ids else "AGENT_NOT_SELECTED"
                ),
                "existing_task_id": next(
                    (
                        task["task_id"]
                        for task in self.state.tasks.values()
                        if task["teacher_id"] == teacher_id and task["template_id"] == template["template_id"]
                    ),
                    None,
                ),
            }
            for _, template in candidates
            if template["template_id"] not in selected_template_ids
        ]
        return {
            "teacher_id": teacher_id,
            "decision_route": decision_route,
            "planning_run": planning_run,
            "created": created,
            "skipped": skipped,
            "executed_actions": action_results,
            "evaluated_at": now_iso(),
        }

    def _template_snapshot_for_task(self, task: dict) -> dict:
        row_id = f'{task["template_id"]}:v{int(task["template_version"])}'
        stored = self.state.template_versions.get(row_id)
        template = deepcopy(stored or self.state.templates.get(task["template_id"]) or {})
        template.update(
            template_id=task["template_id"],
            template_version=int(task["template_version"]),
            template_revision=int(task.get("template_revision_at_issue", 1)),
            status="PUBLISHED",
            priority=task.get("priority_at_issue", task["priority"]),
            due_hours=int(task["due_hours_snapshot"]),
            task_category=task["task_category"],
            dimension=task["dimension"],
            completion_method=task["completion_method"],
            trigger_rule=deepcopy(task.get("trigger_rule_snapshot") or {}),
            localized_content=deepcopy(task["localized_content"]),
            action_schema=deepcopy(task["action_schema"]),
            public_reason_code=task["public_reason"]["code"],
        )
        signal_codes = (template.get("trigger_rule") or {}).get("signal_codes") or []
        template["trigger_code"] = signal_codes[0] if signal_codes else None
        return template

    @staticmethod
    def _template_fingerprint(template: dict) -> dict:
        def digest(value: Any) -> str:
            return hashlib.sha256(
                json.dumps(
                    value,
                    sort_keys=True,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    default=str,
                ).encode("utf-8")
            ).hexdigest()[:16]

        return {
            "template_id": template["template_id"],
            "template_version": int(template["template_version"]),
            "template_revision": int(template.get("template_revision", 1)),
            "action_schema_hash": digest(template.get("action_schema") or {}),
            "trigger_rule_hash": digest(template.get("trigger_rule") or {}),
        }

    @staticmethod
    def _action_id(teacher_id: str, action_type: str, payload: dict[str, Any]) -> str:
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()[:16]
        return f"ACT-{teacher_id}-{action_type}-{digest}"

    def _build_agent_action_catalog(
        self,
        teacher: dict,
        signals: list[dict],
        candidates: list[tuple[dict, dict]],
    ) -> list[dict]:
        teacher_id = teacher["teacher_id"]
        now = datetime.now(timezone.utc).replace(microsecond=0)
        actions: list[dict] = []
        candidate_template_ids = [template["template_id"] for _, template in candidates]
        candidate_rank = {template_id: index + 1 for index, template_id in enumerate(candidate_template_ids)}
        signal_by_template = {
            template["template_id"]: signals_within_merge_window(
                template,
                [signal for signal in signals if template_matches_signal(template, teacher, signal)],
            )
            for _, template in candidates
        }

        def add(
            action_type: str,
            *,
            template_id: str | None = None,
            task_id: str | None = None,
            recommended: bool,
            reason_code: str,
            parameters: dict[str, Any],
        ) -> None:
            identity_parameters = parameters
            if action_type == "REMIND":
                identity_parameters = {"reminder_window": now.strftime("%Y-%m-%dT%H:00Z")}
            identity = {
                "teacher_id": teacher_id,
                "action_type": action_type,
                "template_id": template_id,
                "task_id": task_id,
                "parameters": identity_parameters,
            }
            actions.append(
                {
                    "action_id": self._action_id(teacher_id, action_type, identity),
                    "action_type": action_type,
                    "template_id": template_id,
                    "task_id": task_id,
                    "recommended": recommended,
                    "reason_code": reason_code,
                    "parameters": deepcopy(parameters),
                }
            )

        for signal, template in candidates:
            template_id = template["template_id"]
            template_fingerprint = self._template_fingerprint(template)
            matching = [
                task
                for task in self.state.tasks.values()
                if task["teacher_id"] == teacher_id and task["template_id"] == template_id
            ]
            matching.sort(key=lambda item: (item.get("created_at") or item.get("assigned_at") or "", item["task_id"]))
            nonterminal = [task for task in matching if not self._task_is_terminal(task)]
            current = nonterminal[-1] if nonterminal else None
            supporting_signals = signal_by_template.get(template_id, [signal])
            if current is None:
                terminal = matching[-1] if matching else None
                known_signal_ids = set(terminal.get("evidence_signal_ids", [])) if terminal else set()
                new_signals = [item for item in supporting_signals if item["signal_id"] not in known_signal_ids]
                if terminal and new_signals:
                    reopen_signal = new_signals[0]
                    cooldown_block = template_cooldown_block(
                        template,
                        teacher_id,
                        self.state.tasks,
                        self.state.executions,
                    )
                    if cooldown_block is None:
                        add(
                            "REOPEN",
                            template_id=template_id,
                            task_id=terminal["task_id"],
                            recommended=True,
                            reason_code="VALID_SIGNAL_RECURRED",
                            parameters={
                                "signal": deepcopy(reopen_signal),
                                "previous_task_id": terminal["task_id"],
                                "dedupe_key": f'{teacher_id}:{template_id}:reopen:{reopen_signal["signal_id"]}',
                                "template_fingerprint": template_fingerprint,
                            },
                        )
                elif not matching:
                    add(
                        "CREATE_FROM_TEMPLATE",
                        template_id=template_id,
                        recommended=True,
                        reason_code="PUBLISHED_TRIGGER_MATCH",
                        parameters={
                            "signal": deepcopy(signal),
                            "dedupe_key": f'{teacher_id}:{template_id}:{signal["signal_id"]}',
                            "server_rank": candidate_rank[template_id],
                            "template_fingerprint": template_fingerprint,
                        },
                    )
                continue

            known_signal_ids = set(current.get("evidence_signal_ids", []))
            new_signals = [item for item in supporting_signals if item["signal_id"] not in known_signal_ids]
            if new_signals:
                merge_strategy = (
                    (template.get("trigger_rule") or {}).get("merge") or {}
                ).get("strategy", "MERGE_INTO_ACTIVE_TASK")
                cooldown_block = template_cooldown_block(
                    template,
                    teacher_id,
                    self.state.tasks,
                    self.state.executions,
                )
                if merge_strategy == "NEW_TASK_PER_SIGNAL" and cooldown_block is None:
                    new_signal = new_signals[0]
                    add(
                        "CREATE_FROM_TEMPLATE",
                        template_id=template_id,
                        recommended=True,
                        reason_code="NEW_SIGNAL_NEW_ASSIGNMENT",
                        parameters={
                            "signal": deepcopy(new_signal),
                            "dedupe_key": f'{teacher_id}:{template_id}:{new_signal["signal_id"]}',
                            "server_rank": candidate_rank[template_id],
                            "template_fingerprint": template_fingerprint,
                        },
                    )
                elif merge_strategy == "MERGE_INTO_ACTIVE_TASK":
                    add(
                        "MERGE_EVIDENCE",
                        template_id=template_id,
                        task_id=current["task_id"],
                        recommended=True,
                        reason_code="COMPATIBLE_NEW_EVIDENCE",
                        parameters={"signals": deepcopy(new_signals)},
                    )

            target_rank = candidate_rank[template_id]
            baseline_priority = current.get("priority_at_issue", template["priority"])
            priority_candidates = [baseline_priority, current["priority"]]
            priority_candidates.extend(
                item["severity"]
                for item in supporting_signals
                if item.get("severity") in PRIORITY_ORDER
            )
            target_priority = min(
                priority_candidates,
                key=lambda priority: PRIORITY_ORDER.get(priority, 99),
            )
            reprioritize_needed = (
                current["priority"] != target_priority
                or current.get("display_rank") != target_rank
                or bool(current.get("is_primary")) != (target_rank == 1)
            )
            add(
                "REPRIORITIZE",
                template_id=template_id,
                task_id=current["task_id"],
                recommended=reprioritize_needed,
                reason_code=(
                    "VALID_SIGNAL_PRIORITY_ESCALATION"
                    if PRIORITY_ORDER.get(target_priority, 99)
                    < PRIORITY_ORDER.get(baseline_priority, 99)
                    else "SERVER_RANK_PROJECTION"
                ),
                parameters={"priority": target_priority, "server_rank": target_rank},
            )

            if current.get("assigned_at") and current.get("original_due_at"):
                assigned_at = self._parse_datetime(current["assigned_at"])
                original_due_at = self._parse_datetime(current["original_due_at"])
                current_due_at = self._parse_datetime(current["due_at"])
                target_due = original_due_at
                escalated = (
                    PRIORITY_ORDER.get(target_priority, 99)
                    < PRIORITY_ORDER.get(baseline_priority, 99)
                )
                if escalated:
                    escalation_signals = [
                        item
                        for item in supporting_signals
                        if item.get("severity") == target_priority
                    ]
                    anchors: list[datetime] = []
                    for escalation_signal in escalation_signals:
                        try:
                            anchors.append(self._parse_datetime(escalation_signal["occurred_at"]))
                        except (KeyError, DomainError):
                            continue
                    anchor = max([assigned_at, *anchors])
                    target_due = min(
                        original_due_at,
                        anchor + timedelta(hours=AGENT_ESCALATED_DUE_HOURS[target_priority]),
                    )
                target_due_at = target_due.isoformat().replace("+00:00", "Z")
                revise_needed = current_due_at > target_due
                add(
                    "REVISE_DUE_AT",
                    template_id=template_id,
                    task_id=current["task_id"],
                    recommended=revise_needed,
                    reason_code=(
                        "VALID_SIGNAL_DUE_ESCALATION"
                        if escalated
                        else "ENFORCE_ORIGINAL_DUE_BOUND"
                    ),
                    parameters={"due_at": target_due_at},
                )

            if self._task_is_current_actionable(current):
                due_at = self._parse_datetime(current["due_at"]) if current.get("due_at") else None
                reminder_recommended = bool(due_at and due_at <= now + timedelta(hours=1))
                add(
                    "REMIND",
                    template_id=template_id,
                    task_id=current["task_id"],
                    recommended=reminder_recommended,
                    reason_code="TASK_DUE_SOON" if reminder_recommended else "REMINDER_AVAILABLE",
                    parameters={"scheduled_at": now.isoformat().replace("+00:00", "Z")},
                )
                existing_open_case = any(
                    case["task_id"] == current["task_id"] and case["status"] in {"OPEN", "ACTION_REQUESTED"}
                    for case in self.state.ops_cases.values()
                )
                overdue = bool(due_at and due_at <= now)
                add(
                    "ESCALATE_TO_OPS",
                    template_id=template_id,
                    task_id=current["task_id"],
                    recommended=overdue and not existing_open_case,
                    reason_code="TASK_OVERDUE_REQUIRES_REVIEW" if overdue else "OPS_ESCALATION_AVAILABLE",
                    parameters={},
                )

        for task in self.state.tasks.values():
            if task["teacher_id"] != teacher_id or self._task_is_terminal(task):
                continue
            if task.get("decision_route") != "AGENT" or task["template_id"] in candidate_template_ids:
                continue
            execution_status = self._execution_status(task["task_id"])
            if execution_status == "STARTED":
                add(
                    "ESCALATE_TO_OPS",
                    template_id=task["template_id"],
                    task_id=task["task_id"],
                    recommended=True,
                    reason_code="STARTED_TASK_SIGNAL_INVALIDATED",
                    parameters={},
                )
            else:
                add(
                    "WITHDRAW",
                    template_id=task["template_id"],
                    task_id=task["task_id"],
                    recommended=True,
                    reason_code="TRIGGER_FACT_INVALIDATED",
                    parameters={},
                )

        no_action_payload = {"state": "NO_SAFE_MUTATION"}
        add(
            "NO_ACTION",
            recommended=not any(action["recommended"] for action in actions),
            reason_code="NO_SAFE_ACTION_REQUIRED",
            parameters=no_action_payload,
        )
        return actions

    def _planning_record(
        self,
        teacher_id: str,
        signals: list[dict],
        candidates: list[tuple[dict, dict]],
        action_catalog: list[dict],
        route: str,
    ) -> dict:
        signal_ids = sorted(item["signal_id"] for item in signals)
        template_ids = [item[1]["template_id"] for item in candidates]
        template_fingerprints = [
            self._template_fingerprint(template) for _, template in candidates
        ]
        # Include the currently published version as configuration context even
        # when a live task is correctly evaluated against an older frozen
        # snapshot.  Publishing v2 therefore invalidates the plan cache without
        # rewriting the active v1 assignment.
        for template_id in template_ids:
            published = self.state.templates.get(template_id)
            if published is not None:
                fingerprint = self._template_fingerprint(published)
                if fingerprint not in template_fingerprints:
                    template_fingerprints.append(fingerprint)
        template_fingerprints.sort(
            key=lambda item: (
                item["template_id"],
                item["template_version"],
                item["template_revision"],
            )
        )
        template_state_hash = hashlib.sha256(
            json.dumps(
                template_fingerprints,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:16]
        agent_policy = self._agent_policy()
        runtime_policy = agent_policy.model_dump(mode="json") if agent_policy else None
        policy_identity = runtime_policy or {
            "source": "CODE_DEFAULT",
            "provider": getattr(self.agent_provider, "provider", "custom"),
            "model": getattr(self.agent_provider, "model", None),
            "max_primary_tasks": 1,
            "max_secondary_tasks": 2,
            "allow_task_invention": False,
        }
        policy_hash = hashlib.sha256(
            json.dumps(policy_identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:16]
        state_hash = hashlib.sha256(
            json.dumps(
                [
                    {
                        "action_id": action["action_id"],
                        "recommended": action["recommended"],
                    }
                    for action in action_catalog
                ],
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:16]
        plan_key = (
            f'{teacher_id}:{",".join(signal_ids)}:{",".join(template_ids)}:'
            f'{template_state_hash}:{policy_hash}:{state_hash}'
        )
        existing = self.state.agent_plans.get(plan_key)
        if existing:
            return deepcopy(existing)

        if route == "DIRECT":
            selected_action_ids = [
                action["action_id"]
                for action in action_catalog
                if action["recommended"] and action["action_type"] != "NO_ACTION"
            ]
            if not selected_action_ids:
                selected_action_ids = [
                    action["action_id"] for action in action_catalog if action["action_type"] == "NO_ACTION"
                ]
            result_record = {
                "selected_action_ids": selected_action_ids,
                "planner": "DETERMINISTIC_RULE_ENGINE",
                "mode": "DETERMINISTIC",
                "model": None,
                "provider_request_id": None,
                "input_hash": state_hash,
                "latency_ms": 0,
                "usage": {},
                "fallback_reason": None,
            }
        else:
            provider_parameters = inspect.signature(self.agent_provider.plan).parameters
            kwargs: dict[str, Any] = {}
            if "runtime_policy" in provider_parameters:
                kwargs["runtime_policy"] = runtime_policy
            if "action_candidates" in provider_parameters:
                kwargs["action_candidates"] = action_catalog
            result = self.agent_provider.plan(
                teacher_id,
                signals,
                [template for _, template in candidates],
                **kwargs,
            )
            result_record = result.as_record()

        max_secondary_tasks = agent_policy.max_secondary_tasks if agent_policy else 2
        action_by_id = {action["action_id"]: action for action in action_catalog}
        selected_ids = result_record.get("selected_action_ids", [])
        selected_types = {
            action_by_id[action_id]["action_type"]
            for action_id in selected_ids
            if action_id in action_by_id
        }
        create_count = sum(
            action_by_id[action_id]["action_type"] == "CREATE_FROM_TEMPLATE"
            for action_id in selected_ids
            if action_id in action_by_id
        )
        selected_is_safe = (
            len(selected_ids) == len(set(selected_ids))
            and len(selected_ids) <= 16
            and set(selected_ids) <= set(action_by_id)
            and create_count <= 1 + max_secondary_tasks
            and not ("NO_ACTION" in selected_types and len(selected_ids) > 1)
            and bool(selected_ids)
        )
        if not selected_is_safe:
            from .agent_provider import BoundedAgentProvider

            fallback = BoundedAgentProvider._deterministic_plan(
                action_catalog,
                max_secondary_tasks=max_secondary_tasks,
            )
            result_record.update(
                selected_action_ids=fallback.selected_action_ids,
                planner="DETERMINISTIC_POLICY",
                mode="DETERMINISTIC_FALLBACK",
                provider_request_id=None,
                usage={},
                fallback_reason="AGENT_PLAN_POLICY_REJECTED",
            )

        selected_set = set(result_record["selected_action_ids"])
        ordered_selected_actions = [
            action for action in action_catalog if action["action_id"] in selected_set
        ]
        selected_template_ids: list[str] = []
        for action in ordered_selected_actions:
            if action.get("template_id") and action["action_type"] in {"CREATE_FROM_TEMPLATE", "REOPEN"}:
                if action["template_id"] not in selected_template_ids:
                    selected_template_ids.append(action["template_id"])
        result_record["selected_action_ids"] = [action["action_id"] for action in ordered_selected_actions]
        result_record["selected_template_ids"] = selected_template_ids
        result_record["primary_template_id"] = selected_template_ids[0] if selected_template_ids else None
        plan = {
            "plan_id": new_id("PLAN"),
            "route": route,
            "teacher_id": teacher_id,
            "signal_ids": signal_ids,
            "candidate_template_ids": template_ids,
            "candidate_template_fingerprints": template_fingerprints,
            "template_state_hash": template_state_hash,
            "action_candidates": [
                {
                    "action_id": action["action_id"],
                    "action_type": action["action_type"],
                    "template_id": action.get("template_id"),
                    "task_id": action.get("task_id"),
                    "recommended": action["recommended"],
                    "reason_code": action["reason_code"],
                }
                for action in action_catalog
            ],
            **result_record,
            "policy_source": "PUBLISHED" if agent_policy else "CODE_DEFAULT",
            "policy_hash": policy_hash,
            "ranking_policy": "PRIORITY_THEN_DUE_HOURS_THEN_TEMPLATE_ID",
            "constraints": [
                "SERVER_GENERATED_ACTION_IDS_ONLY",
                "PUBLISHED_TEMPLATES_ONLY",
                "NO_FREEFORM_TASKS",
                "NO_EXTERNAL_HIGH_RISK_ACTIONS",
            ],
            "invented_task_count": 0,
            "created_at": now_iso(),
        }
        self.state.agent_plans[plan_key] = plan
        self._append_event(
            "agent.plan_committed.v1",
            teacher_id=teacher_id,
            payload={
                "plan_id": plan["plan_id"],
                "selected_action_ids": plan["selected_action_ids"],
                "selected_template_ids": plan["selected_template_ids"],
                "constraints": plan["constraints"],
                "mode": plan["mode"],
            },
        )
        return deepcopy(plan)

    def _execute_agent_action(self, teacher: dict, action: dict) -> dict:
        action_type = action["action_type"]
        parameters = action["parameters"]
        task_id = action.get("task_id")
        result: dict[str, Any] = {
            "action_id": action["action_id"],
            "action_type": action_type,
            "task_id": task_id,
            "template_id": action.get("template_id"),
            "status": "EXECUTED",
        }

        if action_type == "CREATE_FROM_TEMPLATE":
            template = self._template(action["template_id"])
            if template["status"] != "PUBLISHED":
                raise DomainError(
                    "AGENT_ACTION_NOT_ALLOWED",
                    "agent.error.template_not_published",
                    status_code=409,
                )
            dedupe_key = parameters["dedupe_key"]
            existing_task_id = self.state.dedupe_keys.get(dedupe_key)
            if existing_task_id:
                result.update(status="NO_CHANGE", task_id=existing_task_id)
            else:
                signal = {**parameters["signal"], "route": "AGENT"}
                task = self._issue_task(teacher, template, signal, dedupe_key)
                result["task_id"] = task["task_id"]

        elif action_type == "MERGE_EVIDENCE":
            task = self._agent_target_task(teacher["teacher_id"], task_id)
            existing_ids = set(task.get("evidence_signal_ids", []))
            merged = [signal for signal in parameters["signals"] if signal["signal_id"] not in existing_ids]
            if not merged:
                result["status"] = "NO_CHANGE"
            else:
                task.setdefault("evidence_signal_ids", []).extend(signal["signal_id"] for signal in merged)
                task.setdefault("source_signals", []).extend(deepcopy(merged))
                evidence_refs = task.setdefault("evidence_refs", [])
                for signal in merged:
                    for evidence_ref in signal.get("evidence_refs", []):
                        if evidence_ref not in evidence_refs:
                            evidence_refs.append(evidence_ref)
                result["merged_signal_ids"] = [signal["signal_id"] for signal in merged]

        elif action_type == "REPRIORITIZE":
            task = self._agent_target_task(teacher["teacher_id"], task_id)
            target_priority = parameters["priority"]
            baseline_priority = task.get("priority_at_issue", task["priority"])
            if (
                target_priority not in PRIORITY_ORDER
                or PRIORITY_ORDER[target_priority] > PRIORITY_ORDER.get(baseline_priority, 99)
            ):
                raise DomainError(
                    "AGENT_ACTION_NOT_ALLOWED",
                    "agent.error.priority_outside_template",
                    status_code=409,
                )
            if task["priority"] == target_priority:
                result["status"] = "NO_CHANGE"
            else:
                task["priority"] = target_priority
                self._publish_assignment_revision(task, operation="REPRIORITIZE")
                self._reschedule_due_reminder(task, reason="PRIORITY_REVISED")
                result["assignment_revision"] = task["assignment_revision"]

        elif action_type == "REVISE_DUE_AT":
            task = self._agent_target_task(teacher["teacher_id"], task_id)
            if not task.get("assigned_at") or not task.get("original_due_at"):
                raise DomainError(
                    "AGENT_ACTION_NOT_ALLOWED",
                    "agent.error.task_not_activated",
                    status_code=409,
                )
            target_due = self._parse_datetime(parameters["due_at"])
            assigned_at = self._parse_datetime(task["assigned_at"])
            original_due_at = self._parse_datetime(task["original_due_at"])
            if not assigned_at <= target_due <= original_due_at:
                raise DomainError(
                    "AGENT_ACTION_NOT_ALLOWED",
                    "agent.error.due_outside_template_bound",
                    status_code=409,
                )
            due_at = target_due.isoformat().replace("+00:00", "Z")
            if task.get("due_at") == due_at:
                result["status"] = "NO_CHANGE"
            else:
                task["due_at"] = due_at
                self._publish_assignment_revision(task, operation="DUE_DATE_REVISED")
                self._reschedule_due_reminder(task, reason="DUE_AT_REVISED")
                result.update(due_at=due_at, assignment_revision=task["assignment_revision"])

        elif action_type == "REMIND":
            task = self._agent_target_task(teacher["teacher_id"], task_id)
            if not self._task_is_current_actionable(task):
                raise DomainError(
                    "AGENT_ACTION_NOT_ALLOWED",
                    "agent.error.reminder_not_actionable",
                    status_code=409,
                )
            output = self._ensure_reminder_output(
                task,
                scheduled_at=parameters["scheduled_at"],
                reminder_scope=f'AGENT_LIFECYCLE:{action["action_id"]}',
                merge_allowed=task["priority"] != "P0",
            )
            result["output_id"] = output["output_id"]

        elif action_type == "ESCALATE_TO_OPS":
            task = self._agent_target_task(teacher["teacher_id"], task_id)
            case = self._create_agent_ops_case(task, action)
            result["case_id"] = case["case_id"]

        elif action_type == "REOPEN":
            previous = self._agent_target_task(teacher["teacher_id"], task_id, allow_terminal=True)
            if not self._task_is_terminal(previous):
                raise DomainError(
                    "AGENT_ACTION_NOT_ALLOWED",
                    "agent.error.reopen_requires_terminal_task",
                    status_code=409,
                )
            template = self._template(action["template_id"])
            if template["status"] != "PUBLISHED" or previous["template_id"] != template["template_id"]:
                raise DomainError(
                    "AGENT_ACTION_NOT_ALLOWED",
                    "agent.error.reopen_template_mismatch",
                    status_code=409,
                )
            dedupe_key = parameters["dedupe_key"]
            existing_task_id = self.state.dedupe_keys.get(dedupe_key)
            if existing_task_id:
                result.update(status="NO_CHANGE", task_id=existing_task_id)
            else:
                signal = {**parameters["signal"], "route": "AGENT"}
                task = self._issue_task(
                    teacher,
                    template,
                    signal,
                    dedupe_key,
                    previous_task_id=previous["task_id"],
                )
                result["task_id"] = task["task_id"]

        elif action_type == "WITHDRAW":
            task = self._agent_target_task(teacher["teacher_id"], task_id)
            if self._execution_status(task["task_id"]) == "STARTED":
                raise DomainError(
                    "AGENT_ACTION_NOT_ALLOWED",
                    "agent.error.started_task_requires_ops",
                    status_code=409,
                )
            task["assignment_status"] = "WITHDRAWN"
            task["slot_state"] = "TERMINAL"
            task["is_primary"] = False
            task["display_rank"] = 999
            self._cancel_planned_reminders(task, reason="TASK_WITHDRAWN")
            self._cancel_task_notification(task, reason="TASK_WITHDRAWN")
            execution = self.state.executions.get(task["task_id"])
            if execution and execution["runtime_status"] not in {
                "COMPLETED",
                "FAILED_FINAL",
                "CANCELLED_BY_WITHDRAWAL",
            }:
                self._transition(
                    task,
                    execution,
                    "TASK_CANCELLED_BY_WITHDRAWAL",
                    "CANCELLED_BY_WITHDRAWAL",
                    None,
                )
            self._publish_assignment_revision(task, operation="WITHDRAW")
            result["assignment_revision"] = task["assignment_revision"]

        elif action_type == "NO_ACTION":
            result["status"] = "NO_CHANGE"

        else:
            raise DomainError(
                "AGENT_ACTION_NOT_ALLOWED",
                "agent.error.unknown_action_type",
                status_code=409,
            )

        self._append_event(
            "agent.lifecycle_action_executed.v1",
            teacher_id=teacher["teacher_id"],
            task_id=result.get("task_id"),
            case_id=result.get("case_id"),
            payload={
                "action_id": action["action_id"],
                "action_type": action_type,
                "status": result["status"],
                "reason_code": action["reason_code"],
            },
        )
        return result

    def _agent_target_task(
        self,
        teacher_id: str,
        task_id: str | None,
        *,
        allow_terminal: bool = False,
    ) -> dict:
        task = self._task(task_id or "")
        if task["teacher_id"] != teacher_id or (self._task_is_terminal(task) and not allow_terminal):
            raise DomainError(
                "AGENT_ACTION_NOT_ALLOWED",
                "agent.error.target_outside_teacher_scope",
                status_code=409,
            )
        return task

    def _create_agent_ops_case(self, task: dict, action: dict) -> dict:
        existing = next(
            (
                case
                for case in self.state.ops_cases.values()
                if case["task_id"] == task["task_id"]
                and case["status"] in {"OPEN", "ACTION_REQUESTED"}
            ),
            None,
        )
        if existing:
            return existing
        case = {
            "case_id": new_id("CASE"),
            "case_type": "OPS-C04",
            "teacher_id": task["teacher_id"],
            "task_id": task["task_id"],
            "priority": task["priority"],
            "status": "OPEN",
            "summary": "任务生命周期需要运营复核；Agent 未执行任何外部业务动作。",
            "source_reason": action["reason_code"],
            "recommended_action": "REVIEW_TASK_LIFECYCLE",
            "external_action_status": "NOT_REQUESTED",
            "created_at": now_iso(),
            "decision": None,
        }
        self.state.ops_cases[case["case_id"]] = case
        self._create_output(
            output_type="OPS_REVIEW_CASE",
            display_type="OPS_CASE",
            audience_type="OPS",
            recipient_id="OPS_QUEUE",
            recipient_name="Teacher Operations",
            channel="OPS_ACTION_QUEUE",
            source_type="AGENT_LIFECYCLE",
            source_id=action["action_id"],
            teacher_id=case["teacher_id"],
            task_id=case["task_id"],
            case_id=case["case_id"],
            status="DELIVERED",
            title=case["case_type"],
            body=case["summary"],
            created_at=case["created_at"],
            delivered_at=case["created_at"],
            retryable=False,
            requires_human_approval=True,
            idempotency_key=f'agent_escalation:{action["action_id"]}',
            payload=self._ops_case_safe_event(case),
        )
        return case

    @persisted_command
    def manual_issue(self, request: ManualTaskIssue, *, actor_id: str | None = None) -> dict:
        teacher = self._project_teacher_scoring(self._teacher(request.teacher_id))
        template = self.state.templates.get(request.template_id)
        if template is None:
            versions = [
                item
                for item in self.state.template_versions.values()
                if item["template_id"] == request.template_id
            ]
            if not versions:
                raise DomainError("TASK_NOT_OWNED", "template.error.not_found", status_code=404)
            latest = max(versions, key=lambda item: int(item["template_version"]))
            raise DomainError(
                "COMMAND_NOT_ALLOWED",
                "task.error.template_not_published",
                status_code=409,
                details={"template_id": request.template_id, "status": latest["status"]},
            )

        allowed_graduation_states = (
            ((template.get("trigger_rule") or {}).get("scope") or {}).get("graduation_states")
            or ["IN_PROGRESS"]
        )
        if teacher.get("graduation_state") not in allowed_graduation_states:
            raise DomainError(
                "TASK_SCOPE_MISMATCH",
                "task.error.teacher_graduation_state_not_applicable",
                status_code=409,
                field_path="$.teacher_id",
                details={
                    "teacher_id": request.teacher_id,
                    "graduation_state": teacher.get("graduation_state"),
                    "allowed_graduation_states": allowed_graduation_states,
                },
            )

        dedupe_key = f"manual:{request.idempotency_key}"
        existing_task_id = self.state.dedupe_keys.get(dedupe_key)
        if existing_task_id:
            existing = self._task(existing_task_id)
            if (
                existing["teacher_id"] != request.teacher_id
                or existing["template_id"] != request.template_id
                or existing.get("source_signal", {}).get("code") != request.reason_code
            ):
                raise DomainError(
                    "DUPLICATE_CONFLICT",
                    "task.error.idempotency_conflict",
                    status_code=409,
                )
            return self.task_detail(existing_task_id)

        # A different transport idempotency key must not bypass the frozen
        # one-live-obligation-per-template rule.  This endpoint creates a
        # TEACHER_TASK (not an OPS work item), so it remains subject to the
        # teacher queue limits and the template's active-assignment ceiling.
        existing_active = next(
            (
                task
                for task in self.state.tasks.values()
                if task["teacher_id"] == request.teacher_id
                and task["template_id"] == request.template_id
                and not self._task_is_terminal(task)
            ),
            None,
        )
        if existing_active:
            raise DomainError(
                "ACTIVE_ASSIGNMENT_LIMIT_REACHED",
                "task.error.active_assignment_limit",
                status_code=409,
                details={"existing_task_id": existing_active["task_id"], "maximum_active": 1},
            )

        signal_digest = hashlib.sha256(request.idempotency_key.encode("utf-8")).hexdigest()[:16]
        signal = {
            "signal_id": f"MANUAL-{signal_digest}",
            "code": request.reason_code,
            "status": "VALID",
            "severity": template["priority"],
            "occurred_at": now_iso(),
            "route": "MANUAL",
            "evidence_refs": [],
        }
        task = self._issue_task(teacher, template, signal, dedupe_key)
        self._rebalance_teacher_tasks(request.teacher_id, preferred_task_ids=[task["task_id"]])
        self._append_event(
            "task.manual_assignment.v1",
            teacher_id=request.teacher_id,
            task_id=task["task_id"],
            **self._operator_fields(actor_id),
            payload={
                "template_id": request.template_id,
                "reason_code": request.reason_code,
            },
        )
        return self.task_detail(task["task_id"])

    def replay_first_lesson_absence(self) -> dict:
        teacher = self._teacher("T-1001")
        trigger_result = self.evaluate_triggers(teacher["teacher_id"])
        return {
            "scenario": "FIRST_LESSON_PHYSICAL_ABSENCE_CONFIRMED",
            "data_mode": "MOCK",
            "teacher_id": teacher["teacher_id"],
            "lesson_fact": deepcopy(teacher["lesson_facts"][0]),
            "lesson_dimension_scores": deepcopy(teacher["lesson_dimension_scores"]),
            "score_entries": deepcopy(teacher["score_entries"]),
            "trigger_result": trigger_result,
        }

    def _issue_task(
        self,
        teacher: dict,
        template: dict,
        signal: dict,
        dedupe_key: str,
        *,
        previous_task_id: str | None = None,
    ) -> dict:
        teacher_timezone = self._require_teacher_timezone(teacher)
        teacher_timezone_source_mode = self._teacher_timezone_source_mode(teacher)
        if not teacher_timezone_source_mode:
            teacher_timezone_source_mode = (
                "MOCK" if str(teacher.get("data_mode") or "").upper() == "MOCK" else "PROFILE_VALUE"
            )
        task_id = new_id("TASK")
        created_at = now_iso()
        delivery_policy, delivery_policy_source = self._delivery_policy()
        governance = deepcopy(template.get("governance") or {})
        score_policy = deepcopy(governance.get("score_policy") or {})
        task = {
            "task_id": task_id,
            "obligation_id": new_id("OBL"),
            "teacher_id": teacher["teacher_id"],
            "camp_enrollment_id": teacher.get("camp_enrollment_id", f'CAMP-{teacher["teacher_id"]}'),
            "template_id": template["template_id"],
            "template_version": template["template_version"],
            "template_revision_at_issue": int(template.get("template_revision", 1)),
            "name": template["localized_content"]["title"],
            "localized_content": deepcopy(template["localized_content"]),
            "task_category": template["task_category"],
            "dimension": template["dimension"],
            "completion_method": template["completion_method"],
            "verification_mode": template.get("verification_mode")
            or VERIFICATION_MODE_BY_METHOD[template["completion_method"]],
            "priority": template["priority"],
            "priority_at_issue": template["priority"],
            "due_hours_snapshot": int(template["due_hours"]),
            "is_primary": False,
            "display_rank": 999,
            "slot_state": "ROADMAP",
            "initial_projection_pending": True,
            "assignment_revision": 1,
            "execution_contract_version": 1,
            "assignment_status": "SCHEDULED",
            "acknowledged_revision": None,
            "created_at": created_at,
            "scheduled_at": created_at,
            "activated_at": None,
            "assigned_at": None,
            "original_due_at": None,
            "due_at": None,
            "deadline_semantics": "NOT_STARTED_UNTIL_ACTIVATION",
            "response_deadline_source": (
                "NOTIFICATION_STORED" if template["completion_method"] == "CONFIRMATION_FORM" else None
            ),
            "delivery_policy_snapshot": delivery_policy.model_dump(mode="json"),
            "delivery_policy_source": delivery_policy_source,
            "public_reason": {"code": template["public_reason_code"], "params": {}},
            "trigger_rule_snapshot": deepcopy(template.get("trigger_rule", {})),
            "source_signal": deepcopy(signal),
            "source_signals": [deepcopy(signal)],
            "evidence_signal_ids": [signal["signal_id"]],
            "evidence_refs": list(dict.fromkeys(signal.get("evidence_refs", []))),
            "decision_route": signal.get("route", "DIRECT"),
            "output_type": "TEACHER_TASK",
            "audience": "TEACHER",
            "is_required": template["task_category"] in {"REQUIRED_GROWTH", "MANUAL_CONFIRMATION"},
            "is_rectification": template["task_category"] == "PERSONALIZED_IMPROVEMENT",
            "stage_id": governance.get("stage_id", "EVENT_DRIVEN"),
            "teacher_timezone": teacher_timezone,
            "teacher_timezone_source_mode": teacher_timezone_source_mode,
            "locale": "en-US",
            "expires_at": None,
            "dependencies": deepcopy(governance.get("dependencies") or []),
            "retry_policy_ref": "retry_policy_v1",
            "help_capability": True,
            "review_capability": True,
            "policy_version": "trigger_policy_v1",
            "reward_display": {
                "score_eligible": bool(score_policy.get("score_eligible", False)),
                "score_bundle_id": score_policy.get("score_bundle_id"),
                "points": 0,
                "settlement_mode": "SCORE_POLICY_EVIDENCE_ONLY",
            },
            "template_governance_snapshot": governance,
            "previous_task_id": previous_task_id,
            "supersedes_task_id": previous_task_id,
            "action_schema": deepcopy(template["action_schema"]),
            "allowed_actions": [
                "VIEW_TASK",
                "START_TASK",
                "RESPOND_CONFIRMATION" if template["completion_method"] == "CONFIRMATION_FORM" else "SUBMIT_TASK",
                "RETRY_TASK",
                "REQUEST_HELP",
                "REQUEST_REVIEW",
            ],
        }
        self.state.tasks[task_id] = task
        self.state.dedupe_keys[dedupe_key] = task_id
        self._append_event(
            "task.scheduled.v1",
            teacher_id=teacher["teacher_id"],
            task_id=task_id,
            payload={"template_id": template["template_id"], "priority": template["priority"]},
        )
        return task

    @staticmethod
    def _teacher_timezone_source_mode(teacher: dict) -> str:
        provenance = ((teacher.get("profile_provenance") or {}).get("timezone") or {})
        return str(provenance.get("source_mode") or "").strip().upper()

    @staticmethod
    def _validate_timezone_identifier(timezone_name: str, *, details: dict[str, Any]) -> str:
        try:
            ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise DomainError(
                "TEACHER_TIMEZONE_INVALID",
                "task.error.teacher_timezone_invalid",
                status_code=409,
                field_path="$.teacher_id",
                details={**details, "teacher_timezone": timezone_name},
            ) from exc
        return timezone_name

    @classmethod
    def _require_teacher_timezone(cls, teacher: dict) -> str:
        """Fail closed when a teacher deadline cannot be placed on a real clock."""

        timezone_name = str(teacher.get("timezone") or "").strip()
        source_mode = cls._teacher_timezone_source_mode(teacher)
        data_mode = str(teacher.get("data_mode") or "").upper()
        details = {
            "teacher_id": teacher.get("teacher_id"),
            "timezone_source_mode": source_mode or "MISSING",
        }
        source_is_untrusted = source_mode in UNTRUSTED_TIMEZONE_SOURCE_MODES
        if not timezone_name or (data_mode in {"MIXED", "REAL"} and source_is_untrusted):
            raise DomainError(
                "TEACHER_TIMEZONE_UNAVAILABLE",
                "task.error.teacher_timezone_unavailable",
                status_code=409,
                field_path="$.teacher_id",
                details=details,
            )
        return cls._validate_timezone_identifier(timezone_name, details=details)

    def _task_timezone_for_projection(self, task: dict) -> str:
        teacher = self.state.teachers.get(task["teacher_id"], {})
        stored_timezone = str(task.get("teacher_timezone") or "").strip()
        if not stored_timezone:
            return self._require_teacher_timezone(teacher)

        details = {
            "teacher_id": task.get("teacher_id"),
            "timezone_source_mode": task.get("teacher_timezone_source_mode") or "MISSING",
            "task_id": task.get("task_id"),
        }
        self._validate_timezone_identifier(stored_timezone, details=details)
        teacher_data_mode = str(teacher.get("data_mode") or "").upper()
        snapshot_source_mode = str(task.get("teacher_timezone_source_mode") or "").upper()
        if teacher_data_mode in {"MIXED", "REAL"} and snapshot_source_mode in UNTRUSTED_TIMEZONE_SOURCE_MODES:
            # Legacy tasks did not snapshot timezone provenance. They may only
            # continue when the current trusted profile independently confirms
            # exactly the same timezone; an old UTC storage placeholder must
            # never become a teacher-facing deadline timezone by accident.
            current_timezone = self._require_teacher_timezone(teacher)
            if current_timezone != stored_timezone:
                details["current_teacher_timezone"] = current_timezone
                details["stored_teacher_timezone"] = stored_timezone
                raise DomainError(
                    "TEACHER_TIMEZONE_SNAPSHOT_CONFLICT",
                    "task.error.teacher_timezone_snapshot_conflict",
                    status_code=409,
                    field_path="$.teacher_id",
                    details=details,
                )
        return stored_timezone

    def _teacher_safe_assignment_projection(
        self,
        task: dict,
        *,
        updated_at: str | None = None,
    ) -> dict:
        """Build the teacher payload from an allowlist, never from subtraction."""

        execution = self.state.executions.get(task["task_id"])
        execution_status = execution["runtime_status"] if execution else "NOT_ACKNOWLEDGED"
        action_schema = deepcopy(task["action_schema"])
        config = action_schema.get("config", {})
        attempt_no = int((execution or {}).get("attempt_no", 0))
        maximum_attempts = int(config.get("max_attempts", 1))
        projection = {
            "task_id": task["task_id"],
            "assignment_revision": int(task["assignment_revision"]),
            "execution_contract_version": int(task["execution_contract_version"]),
            "runtime_sequence": int((execution or {}).get("runtime_sequence", 0)),
            "obligation_id": task["obligation_id"],
            "teacher_id": task["teacher_id"],
            "camp_enrollment_id": task["camp_enrollment_id"],
            "template_id": task["template_id"],
            "template_version": int(task["template_version"]),
            "output_type": "TEACHER_TASK",
            "task_category": task["task_category"],
            "audience": "TEACHER",
            "dimension": task["dimension"],
            "completion_method": task["completion_method"],
            "verification_mode": task.get(
                "verification_mode",
                VERIFICATION_MODE_BY_METHOD[task["completion_method"]],
            ),
            "is_required": bool(
                task.get("is_required", task["task_category"] != "PERSONALIZED_IMPROVEMENT")
            ),
            "is_rectification": bool(
                task.get("is_rectification", task["task_category"] == "PERSONALIZED_IMPROVEMENT")
            ),
            "stage_id": task.get("stage_id", "NEW_TEACHER_PROBATION"),
            "priority": task["priority"],
            "is_primary": bool(task["is_primary"]),
            "display_rank": int(task["display_rank"]),
            "assigned_at": task.get("assigned_at"),
            "activated_at": task.get("activated_at"),
            "original_due_at": task.get("original_due_at"),
            "due_at": task.get("due_at"),
            "expires_at": task.get("expires_at"),
            "teacher_timezone": self._task_timezone_for_projection(task),
            "execution_status": execution_status,
            "due_status": (execution or {}).get("due_status", "ON_TIME"),
            "display_status_code": execution_status,
            "allowed_actions": deepcopy(task["allowed_actions"]),
            "attempt_no": attempt_no,
            "remaining_attempts": max(0, maximum_attempts - attempt_no),
            "action_schema_version": int(action_schema["action_schema_version"]),
            "localized_content": deepcopy(task["localized_content"]),
            "action_schema": action_schema,
            "dependencies": deepcopy(task.get("dependencies", [])),
            "retry_policy_ref": task.get("retry_policy_ref", "retry_policy_v1"),
            "help_capability": bool(task.get("help_capability", True)),
            "review_capability": bool(task.get("review_capability", True)),
            "policy_version": task.get("policy_version", "trigger_policy_v1"),
            "public_reason": deepcopy(task["public_reason"]),
            "reward_display": deepcopy(
                task.get("reward_display", {"score_eligible": False, "points": 0})
            ),
            "updated_at": updated_at
            or (execution or {}).get("last_event_at")
            or task.get("assigned_at")
            or task["created_at"],
        }
        digest = hashlib.sha256(
            json.dumps(
                projection,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        projection["payload_hash"] = f"sha256:{digest}"
        return projection

    def _activate_scheduled_task(self, task: dict) -> None:
        if task["assignment_status"] != "SCHEDULED":
            return
        activated_at = datetime.now(timezone.utc).replace(microsecond=0)
        due_at = activated_at + timedelta(hours=int(task["due_hours_snapshot"]))
        activated_iso = activated_at.isoformat().replace("+00:00", "Z")
        due_iso = due_at.isoformat().replace("+00:00", "Z")
        task.update(
            assignment_status="ISSUED",
            slot_state="ACTIVE",
            activated_at=activated_iso,
            assigned_at=activated_iso,
            original_due_at=due_iso,
            due_at=due_iso,
            deadline_semantics="TASK_ASSIGNMENT_DUE_FROM_ACTIVATION",
        )
        self._resolve_slot_policy_case(task, resolved_at=activated_iso)
        self._append_event(
            "task.assignment_activated.audit.v1",
            task_id=task["task_id"],
            payload={
                "template_id": task["template_id"],
                "priority": task["priority"],
                "assignment_revision": task["assignment_revision"],
            },
        )
        self._create_assignment_output(task, operation="ISSUE", created_at=activated_iso)
        if task["completion_method"] != "CONFIRMATION_FORM":
            delivery_policy = self._delivery_policy_for_task(task)
            lead_minutes = (
                delivery_policy.urgent_reminder_minutes_before_due
                if task["priority"] in {"P0", "P1"}
                else delivery_policy.normal_reminder_minutes_before_due
            )
            reminder_at = max(activated_at, due_at - timedelta(minutes=lead_minutes))
            self._ensure_reminder_output(
                task,
                scheduled_at=reminder_at.isoformat().replace("+00:00", "Z"),
                reminder_scope=f'TASK_DUE:r{task["assignment_revision"]}',
                merge_allowed=True,
            )

    def _create_assignment_output(
        self,
        task: dict,
        *,
        operation: str,
        created_at: str | None = None,
    ) -> dict:
        """Create an immutable delivery record for one assignment revision."""

        revision = int(task["assignment_revision"])
        timestamp = created_at or now_iso()
        for output in self.state.outbound_outputs.values():
            previous_payload = output.get("payload") or {}
            previous_task_payload = previous_payload.get("task") or previous_payload
            if (
                output.get("display_type") == "TASK_ASSIGNMENT"
                and output.get("task_id") == task["task_id"]
                and output.get("status") == "REQUESTED"
                and int(previous_task_payload.get("assignment_revision", 0)) < revision
            ):
                output["status"] = "CANCELLED"
                output["updated_at"] = timestamp
                output["superseded_by_revision"] = revision

        event_type = {
            "ISSUE": "task.issued.v1",
            "REPRIORITIZE": "task.reprioritized.v1",
            "DUE_DATE_REVISED": "task.due_date_revised.v1",
            "CONTENT_REVISION": "task.content_revision_available.v1",
            "WITHDRAW": "task.withdrawn.v1",
        }.get(operation, "task.reprioritized.v1")
        event_idempotency_key = f'task_assignment:{task["task_id"]}:r{revision}'
        event_payload = {
            "event_schema_version": 1,
            "event_id": new_id("EVT"),
            "event_type": event_type,
            "occurred_at": timestamp,
            "idempotency_key": event_idempotency_key,
            "trace_id": f'trace:{task["obligation_id"]}',
            "correlation_id": task["camp_enrollment_id"],
            "causation_id": task["obligation_id"],
            "producer": "TASK_TRIGGER_CENTER",
            "task": self._teacher_safe_assignment_projection(task, updated_at=timestamp),
        }

        teacher = self.state.teachers.get(task["teacher_id"], {})
        output = self._create_output(
            output_type="TEACHER_TASK",
            display_type="TASK_ASSIGNMENT",
            delivery_kind=f"TASK_{operation}",
            audience_type="TEACHER",
            recipient_id=task["teacher_id"],
            recipient_name=teacher.get("name"),
            channel="TEACHER_TASK_RUNTIME",
            source_type="TASK_ASSIGNMENT_REVISION",
            source_id=f'{task["task_id"]}:r{revision}',
            teacher_id=task["teacher_id"],
            task_id=task["task_id"],
            status="REQUESTED",
            title=task["name"],
            body=task["public_reason"]["code"],
            created_at=timestamp,
            sent_at=None,
            attempt_count=0,
            retryable=True,
            idempotency_key=event_idempotency_key,
            payload=event_payload,
        )
        self._append_event(
            "task.assignment_output_recorded.audit.v1",
            teacher_id=task["teacher_id"],
            task_id=task["task_id"],
            payload={
                "output_id": output["output_id"],
                "assignment_revision": revision,
                "operation": operation,
            },
        )
        return output

    def _publish_assignment_revision(
        self,
        task: dict,
        *,
        operation: str = "REPRIORITIZE",
    ) -> dict | None:
        if task.get("assignment_status") == "SCHEDULED":
            return None
        task["assignment_revision"] = int(task.get("assignment_revision", 1)) + 1
        if operation != "WITHDRAW":
            task["assignment_status"] = "REVISION_PENDING"
        return self._create_assignment_output(task, operation=operation)

    def _assignment_output(self, task_id: str, assignment_revision: int) -> dict | None:
        return next(
            (
                output
                for output in self.state.outbound_outputs.values()
                if output.get("display_type") == "TASK_ASSIGNMENT"
                and output.get("task_id") == task_id
                and int(
                    (
                        ((output.get("payload") or {}).get("task") or (output.get("payload") or {}))
                    ).get("assignment_revision", 0)
                )
                == assignment_revision
            ),
            None,
        )

    def _task_has_pending_assignment_ack(self, task: dict) -> bool:
        if task.get("assignment_status") == "SCHEDULED":
            return False
        revision = int(task.get("assignment_revision", 0))
        if int(task.get("acknowledged_revision") or 0) >= revision:
            return False
        output = self._assignment_output(task["task_id"], revision)
        return bool(output and output.get("status") == "REQUESTED")

    def _rebalance_teacher_tasks(
        self,
        teacher_id: str,
        *,
        preferred_task_ids: list[str] | None = None,
    ) -> None:
        """Project the bounded teacher work queue and publish rank changes.

        Priority is the first ordering rule.  Operator/Agent preference can
        break ties but can never make a lower-priority task primary.  At most
        three teacher tasks occupy the current queue and at most two of those
        can be personalized.  SUBMITTED/VERIFYING tasks live in the review
        projection and do not occupy a current slot.  An already-published
        assignment is never revoked merely to make room for a P0; the P0 stays
        on the roadmap and Operations receives a policy-exception case.
        """

        preferred = {task_id: index for index, task_id in enumerate(preferred_task_ids or [])}
        tasks = [task for task in self.state.tasks.values() if task["teacher_id"] == teacher_id]

        def sort_key(task: dict) -> tuple:
            return (
                PRIORITY_ORDER.get(task["priority"], 99),
                TASK_CATEGORY_ORDER.get(task.get("task_category"), 99),
                preferred.get(task["task_id"], 10_000),
                task.get("due_at") or task.get("scheduled_at") or "9999-12-31T23:59:59Z",
                task["task_id"],
            )

        active = [task for task in tasks if self._task_occupies_slot(task)]
        planned: list[dict] = []
        candidates = sorted(
            [
                task
                for task in tasks
                if not self._task_is_terminal(task)
                and (
                    task["assignment_status"] == "SCHEDULED"
                    or (
                        self._task_slot_state(task) == "ROADMAP"
                        and self._execution_status(task["task_id"]) == "RETRY_REQUIRED"
                    )
                )
            ],
            key=sort_key,
        )

        for candidate in candidates:
            projected = active + planned
            personalized_count = sum(
                task.get("task_category") == "PERSONALIZED_IMPROVEMENT" for task in projected
            )
            global_blocked = len(projected) >= 3
            personalized_blocked = (
                candidate.get("task_category") == "PERSONALIZED_IMPROVEMENT"
                and personalized_count >= 2
            )
            if global_blocked or personalized_blocked:
                if candidate["priority"] == "P0":
                    self._ensure_slot_policy_case(candidate)
                continue

            planned.append(candidate)

        projected = sorted(active + planned, key=sort_key)
        projected_ids = {task["task_id"] for task in projected}
        planned_ids = {task["task_id"] for task in planned}
        for rank, task in enumerate(projected, start=1):
            target_primary = rank == 1
            changed = (
                task.get("is_primary") != target_primary
                or int(task.get("display_rank", 999)) != rank
            )
            task["is_primary"] = target_primary
            task["display_rank"] = rank
            if task["task_id"] in planned_ids:
                task.pop("initial_projection_pending", None)
                continue
            if changed:
                if task.pop("initial_projection_pending", False):
                    continue
                if not self._task_is_terminal(task):
                    self._publish_assignment_revision(task)

        for task in tasks:
            if task["task_id"] in projected_ids:
                continue
            task["is_primary"] = False
            task["display_rank"] = 999

        for task in sorted(planned, key=sort_key):
            if task["assignment_status"] == "SCHEDULED":
                self._activate_scheduled_task(task)
                continue
            # A trusted verifier may request a retry after the original task
            # left the current queue for review.  Re-entry is capacity-bound
            # and is published as a new assignment projection; the execution
            # contract and original due-time facts remain unchanged.
            task["slot_state"] = "ACTIVE"
            self._resolve_slot_policy_case(task, resolved_at=now_iso())
            self._publish_assignment_revision(task)
            self._reschedule_due_reminder(task, reason="RETRY_SLOT_REACTIVATED")
            self._append_event(
                "task.retry_slot_activated.v1",
                teacher_id=task["teacher_id"],
                task_id=task["task_id"],
                payload={"assignment_revision": task["assignment_revision"]},
            )

    def _ensure_slot_policy_case(self, task: dict) -> dict:
        existing = next(
            (
                case
                for case in self.state.ops_cases.values()
                if case.get("task_id") == task["task_id"]
                and case.get("case_type") == "OPS-C04"
                and case.get("source_reason") == "P0_SLOT_CAPACITY_BLOCKED"
                and case.get("status") in {"OPEN", "ACTION_REQUESTED"}
            ),
            None,
        )
        if existing:
            return existing
        created_at = now_iso()
        case = {
            "case_id": new_id("CASE"),
            "case_type": "OPS-C04",
            "teacher_id": task["teacher_id"],
            "task_id": task["task_id"],
            "priority": "P0",
            "status": "OPEN",
            "summary": "P0 任务无法在安全并发上限内激活，需要运营决定是否调整现有任务。",
            "source_reason": "P0_SLOT_CAPACITY_BLOCKED",
            "recommended_action": "REVIEW_ACTIVE_TASK_CAPACITY",
            "external_action_status": "NOT_REQUESTED",
            "created_at": created_at,
            "decision": None,
        }
        self.state.ops_cases[case["case_id"]] = case
        self._create_output(
            output_type="OPS_REVIEW_CASE",
            display_type="OPS_CASE",
            audience_type="OPS",
            recipient_id="OPS_QUEUE",
            recipient_name="Teacher Operations",
            channel="OPS_ACTION_QUEUE",
            source_type="TASK_SLOT_POLICY",
            source_id=task["task_id"],
            teacher_id=task["teacher_id"],
            task_id=task["task_id"],
            case_id=case["case_id"],
            status="DELIVERED",
            title="OPS-C04",
            body=case["summary"],
            created_at=created_at,
            delivered_at=created_at,
            retryable=False,
            requires_human_approval=True,
            idempotency_key=f'p0_slot_policy:{task["task_id"]}',
            payload=self._ops_case_safe_event(case),
        )
        return case

    def _resolve_slot_policy_case(self, task: dict, *, resolved_at: str) -> None:
        for case in self.state.ops_cases.values():
            if (
                case.get("task_id") == task["task_id"]
                and case.get("case_type") == "OPS-C04"
                and case.get("source_reason") == "P0_SLOT_CAPACITY_BLOCKED"
                and case.get("status") in {"OPEN", "ACTION_REQUESTED"}
            ):
                case["status"] = "CLOSED"
                case["resolved_at"] = resolved_at
                case["resolution_reason"] = "TASK_SLOT_BECAME_AVAILABLE"
                case["decision"] = "AUTO_RESOLVED_ON_ACTIVATION"
                self._append_event(
                    "ops_case.auto_resolved.v1",
                    teacher_id=task["teacher_id"],
                    task_id=task["task_id"],
                    case_id=case["case_id"],
                    payload={
                        "case_type": "OPS-C04",
                        "resolution_reason": case["resolution_reason"],
                    },
                )

    def _task_is_terminal(self, task: dict) -> bool:
        if task.get("assignment_status") in {"WITHDRAWN", "SUPERSEDED"}:
            return True
        return self._execution_status(task["task_id"]) in {
            "COMPLETED",
            "FAILED_FINAL",
            "CANCELLED_BY_WITHDRAWAL",
        }

    def _task_slot_state(self, task: dict) -> str:
        """Return the persisted slot projection, with legacy-row inference."""

        explicit = task.get("slot_state")
        if explicit in {"ROADMAP", "ACTIVE", "IN_REVIEW", "TERMINAL"}:
            return explicit
        if self._task_is_terminal(task):
            return "TERMINAL"
        execution_status = self._execution_status(task["task_id"])
        if execution_status in {"SUBMITTED", "VERIFYING"}:
            return "IN_REVIEW"
        if task.get("assignment_status") == "SCHEDULED" or execution_status == "RETRY_REQUIRED":
            return "ROADMAP"
        return "ACTIVE"

    def _task_is_current_actionable(self, task: dict) -> bool:
        if self._task_slot_state(task) != "ACTIVE":
            return False
        if task.get("assignment_status") not in {"ACKNOWLEDGED", "REVISION_PENDING"}:
            return False
        return self._execution_status(task["task_id"]) in {
            "AVAILABLE",
            "VIEWED",
            "STARTED",
            "RETRY_REQUIRED",
        }

    def _task_occupies_slot(self, task: dict) -> bool:
        if self._task_slot_state(task) != "ACTIVE":
            return False
        if task.get("assignment_status") in {"WITHDRAWN", "SUPERSEDED", "SCHEDULED"}:
            return False
        if task.get("assignment_status") not in {
            "ISSUED",
            "REVISION_PENDING",
            "ACKNOWLEDGED",
        }:
            return False
        return self._execution_status(task["task_id"]) in {
            "NOT_ACKNOWLEDGED",
            "AVAILABLE",
            "VIEWED",
            "STARTED",
            "RETRY_REQUIRED",
        }

    @persisted_command
    def acknowledge(self, task_id: str, ack: TaskDispatchAck) -> dict:
        """Accept one exact assignment revision from the teacher runtime.

        Assignment publication is append-only.  The consumer therefore ACKs
        the revision it actually stored; an ACK for an older revision must not
        make a newer instruction look delivered.
        """

        task = self._task(task_id)
        if ack.task_id != task_id:
            raise DomainError(
                "TASK_NOT_OWNED",
                "task.error.not_found",
                status_code=404,
                field_path="$.task_id",
            )
        ack_dict = ack.model_dump(mode="json")
        ack_hash = hashlib.sha256(
            json.dumps(ack_dict, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        self._assert_global_event(ack.event_id, ack_hash, ack.event_type)
        key_scope = f"assignment_ack:{ack.idempotency_key}"
        event_scope = f"assignment_ack_event:{ack.event_id}"

        def cache_response(response: dict) -> dict:
            self._register_global_event(ack.event_id, ack_hash, ack.event_type)
            self.state.command_hashes[key_scope] = ack_hash
            self.state.command_results[key_scope] = deepcopy(response)
            self.state.command_event_hashes[event_scope] = ack_hash
            self.state.command_event_results[event_scope] = deepcopy(response)
            return response

        cached = self.state.command_results.get(key_scope)
        if cached is not None:
            if self.state.command_hashes.get(key_scope) != ack_hash:
                raise DomainError(
                    "DUPLICATE_CONFLICT",
                    "task.error.idempotency_conflict",
                    status_code=409,
                )
            self._register_global_event(ack.event_id, ack_hash, ack.event_type)
            return deepcopy(cached)
        cached = self.state.command_event_results.get(event_scope)
        if cached is not None:
            if self.state.command_event_hashes.get(event_scope) != ack_hash:
                raise DomainError(
                    "DUPLICATE_CONFLICT",
                    "task.error.event_id_conflict",
                    status_code=409,
                )
            self._register_global_event(ack.event_id, ack_hash, ack.event_type)
            return deepcopy(cached)

        self._parse_datetime(ack.accepted_at)
        if ack.execution_contract_version != task["execution_contract_version"]:
            raise DomainError(
                "EXECUTION_CONTRACT_VERSION_MISMATCH",
                "task.error.refresh_and_retry",
                status_code=409,
                field_path="$.execution_contract_version",
                details={
                    "current_execution_contract_version": task["execution_contract_version"]
                },
            )
        current_revision = int(task["assignment_revision"])
        if ack.assignment_revision != current_revision:
            raise DomainError(
                "STALE_ASSIGNMENT_REVISION",
                "task.error.refresh_and_retry",
                status_code=409,
                field_path="$.assignment_revision",
                details={"current_assignment_revision": current_revision},
            )
        if task["assignment_status"] not in {
            "ISSUED",
            "REVISION_PENDING",
            "ACKNOWLEDGED",
            "WITHDRAWN",
        }:
            raise DomainError("COMMAND_NOT_ALLOWED", "task.error.ack_not_allowed", status_code=409)

        assignment_output = self._assignment_output(task_id, current_revision)
        if assignment_output is None:
            raise DomainError(
                "COMMAND_NOT_ALLOWED",
                "task.error.assignment_revision_not_published",
                status_code=409,
            )
        if (
            task.get("acknowledged_revision") == current_revision
            and assignment_output.get("status") == "DELIVERED"
        ):
            # A consumer may lose the HTTP response and retry with a freshly
            # generated transport event.  The assignment revision is the
            # semantic idempotency boundary: preserve the first delivery
            # timestamp/runtime reference and only remember the new envelope.
            return cache_response(self.task_detail(task_id))
        assignment_output["status"] = "DELIVERED"
        assignment_output["sent_at"] = ack.accepted_at
        assignment_output["delivered_at"] = ack.accepted_at
        assignment_output["attempt_count"] = max(1, int(assignment_output["attempt_count"]))
        assignment_output["updated_at"] = ack.accepted_at

        task["acknowledged_revision"] = current_revision
        task["runtime_task_ref"] = ack.runtime_task_ref
        if task["assignment_status"] == "WITHDRAWN":
            task["withdrawal_acknowledged_at"] = ack.accepted_at
        else:
            first_ack = task_id not in self.state.executions
            task["assignment_status"] = "ACKNOWLEDGED"
            task["acknowledged_at"] = task.get("acknowledged_at") or ack.accepted_at
            if first_ack:
                self.state.executions[task_id] = {
                    "task_id": task_id,
                    "runtime_status": "AVAILABLE",
                    "verification_result": None,
                    "runtime_sequence": 0,
                    "due_status": "ON_TIME",
                    "last_event_at": ack.accepted_at,
                    "selected_option_code": None,
                    "result_ref": None,
                }
                self._request_notification(task)

        self._append_event(
            "task.dispatch_ack_recorded.audit.v1",
            teacher_id=task["teacher_id"],
            task_id=task_id,
            payload={
                "accepted": True,
                "event_id": ack.event_id,
                "idempotency_key": ack.idempotency_key,
                "assignment_revision": current_revision,
                "execution_contract_version": ack.execution_contract_version,
                "runtime_task_ref": ack.runtime_task_ref,
                "operation": str(assignment_output.get("delivery_kind") or "TASK_ISSUE").removeprefix("TASK_"),
            },
        )
        return cache_response(self.task_detail(task_id))

    def _request_notification(self, task: dict) -> dict:
        existing_id = self.state.notification_by_task.get(task["task_id"])
        if existing_id:
            return self.state.notifications[existing_id]
        notification_id = new_id("NOTIFY")
        requested_at = now_iso()
        notification_template_id = (
            "TASK_ISSUED_P0" if task["priority"] == "P0" else "TASK_ISSUED_STANDARD"
        )
        notification_template_version = 1
        event_idempotency_key = (
            f'notice:{task["task_id"]}:{notification_template_id}:v{notification_template_version}'
        )
        notification = {
            "notification_id": notification_id,
            "task_id": task["task_id"],
            "teacher_id": task["teacher_id"],
            "template_id": notification_template_id,
            "template_version": notification_template_version,
            "channel": "WEBAPP_INBOX",
            "priority": task["priority"],
            "safe_params": {"task_title": task["localized_content"]["title"]},
            "deep_link": f'/tasks/{task["task_id"]}',
            "locale": task.get("locale", "en-US"),
            "expires_at": task.get("due_at"),
            "status": "REQUESTED",
            "requested_at": requested_at,
            "stored_at": None,
            "read_at": None,
            "clicked_at": None,
            "response_due_at": None,
            "failure_reason": None,
        }
        self.state.notifications[notification_id] = notification
        self.state.notification_by_task[task["task_id"]] = notification_id
        notification_projection = {
            "notification_id": notification_id,
            "task_id": task["task_id"],
            "teacher_id": task["teacher_id"],
            "template_id": notification_template_id,
            "template_version": notification_template_version,
            "priority": task["priority"],
            "safe_params": deepcopy(notification["safe_params"]),
            "deep_link": notification["deep_link"],
            "locale": notification["locale"],
            "expires_at": notification["expires_at"],
        }
        notification_event = {
            "event_id": new_id("EVT"),
            "event_type": "notification.requested.v1",
            "idempotency_key": event_idempotency_key,
            "occurred_at": requested_at,
            "trace_id": f'trace:{task["obligation_id"]}',
            "notification": notification_projection,
        }
        self._append_event(
            "notification.request_recorded.audit.v1",
            teacher_id=task["teacher_id"],
            task_id=task["task_id"],
            payload=deepcopy(notification_event),
        )
        teacher = self.state.teachers.get(task["teacher_id"], {})
        self._create_output(
            output_type="DELIVERY_INTENT",
            display_type="IN_APP_NOTIFICATION",
            delivery_kind="IN_APP_NOTIFICATION",
            audience_type="TEACHER",
            recipient_id=task["teacher_id"],
            recipient_name=teacher.get("name"),
            channel="WEBAPP_INBOX",
            source_type="NOTIFICATION",
            source_id=notification_id,
            teacher_id=task["teacher_id"],
            task_id=task["task_id"],
            status="REQUESTED",
            title=task["name"],
            body=task["public_reason"]["code"],
            created_at=notification["requested_at"],
            sent_at=None,
            attempt_count=0,
            retryable=True,
            idempotency_key=event_idempotency_key,
            payload=notification_event,
        )
        return notification

    def _cancel_task_notification(self, task: dict, *, reason: str) -> None:
        notification_id = self.state.notification_by_task.get(task["task_id"])
        notification = self.state.notifications.get(notification_id) if notification_id else None
        if notification is None:
            return
        cancelled_at = now_iso()
        notification["cancelled_at"] = cancelled_at
        notification["cancellation_reason"] = reason
        if notification.get("status") in {"REQUESTED", "INTEGRATION_FAILED"}:
            notification["status"] = "CANCELLED"
            notification["failure_reason"] = None
        output = self._find_output(
            "IN_APP_NOTIFICATION",
            task_id=task["task_id"],
            source_id=notification_id,
        )
        if output is not None:
            output["task_withdrawn_at"] = cancelled_at
            output["cancel_reason"] = reason
            if output.get("status") in {"REQUESTED", "FAILED"}:
                output["status"] = "CANCELLED"
                output["retryable"] = False
                output["next_retry_at"] = None
                output["last_error"] = None
                self._cancel_output_outbox(
                    output,
                    reason=reason,
                    cancelled_at=cancelled_at,
                )
            output["updated_at"] = cancelled_at

    @persisted_command
    def record_notification_event(self, notification_id: str, update: NotificationEvent) -> dict:
        notification = self._notification(notification_id)
        if update.notification_id != notification_id or update.task_id != notification["task_id"]:
            raise DomainError(
                "TASK_NOT_OWNED",
                "notification.error.not_found",
                status_code=404,
                field_path=(
                    "$.notification_id"
                    if update.notification_id != notification_id
                    else "$.task_id"
                ),
            )
        self._parse_datetime(update.occurred_at)
        if update.delivery_status == "FAILED" and not update.error_code:
            raise DomainError(
                "PAYLOAD_SCHEMA_INVALID",
                "notification.error.error_code_required",
                status_code=422,
                field_path="$.error_code",
            )
        if update.delivery_status != "FAILED" and update.error_code is not None:
            raise DomainError(
                "PAYLOAD_SCHEMA_INVALID",
                "notification.error.error_code_not_allowed",
                status_code=422,
                field_path="$.error_code",
            )
        update_hash = hashlib.sha256(
            json.dumps(
                update.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        notification_event_type = update.event_type
        self._assert_global_event(
            update.event_id,
            update_hash,
            notification_event_type,
        )
        event_scope = f"event:{update.event_id}"
        key_scope = f"key:{update.idempotency_key}"
        cached = self.state.notification_event_results.get(event_scope)
        if cached:
            if self.state.notification_event_hashes[event_scope] != update_hash:
                raise DomainError("DUPLICATE_CONFLICT", "notification.error.idempotency_conflict", status_code=409)
            self._register_global_event(
                update.event_id,
                update_hash,
                notification_event_type,
            )
            return deepcopy(cached)
        cached = self.state.notification_event_results.get(key_scope)
        if cached:
            if self.state.notification_event_hashes[key_scope] != update_hash:
                raise DomainError(
                    "DUPLICATE_CONFLICT",
                    "notification.error.idempotency_conflict",
                    status_code=409,
                )
            self._register_global_event(update.event_id, update_hash, notification_event_type)
            return deepcopy(cached)
        if notification.get("status") == "CANCELLED" or notification.get("cancelled_at"):
            raise DomainError(
                "COMMAND_NOT_ALLOWED",
                "notification.error.cancelled",
                status_code=409,
            )

        status = update.delivery_status
        if status == "STORED":
            if notification["status"] == "INTEGRATION_FAILED":
                raise DomainError("COMMAND_NOT_ALLOWED", "notification.error.retry_requires_new_request", status_code=409)
            if not notification["stored_at"]:
                stored_at = self._parse_datetime(update.occurred_at)
                notification["status"] = "STORED"
                notification["stored_at"] = update.occurred_at
                task = self._task(notification["task_id"])
                delivery_policy = self._delivery_policy_for_task(task)
                if task["priority"] == "P0" and task["completion_method"] == "CONFIRMATION_FORM":
                    timeout_seconds = delivery_policy.p0_response_window_minutes * 60
                else:
                    timeout_seconds = int(task["action_schema"]["config"].get("response_timeout_seconds", 0))
                if timeout_seconds:
                    notification["response_due_at"] = (
                        stored_at + timedelta(seconds=timeout_seconds)
                    ).isoformat().replace("+00:00", "Z")
        elif status == "FAILED":
            if notification["stored_at"]:
                raise DomainError("COMMAND_NOT_ALLOWED", "notification.error.cannot_fail_after_stored", status_code=409)
            notification["status"] = "INTEGRATION_FAILED"
            notification["failure_reason"] = update.error_code
        else:
            if not notification["stored_at"]:
                raise DomainError("COMMAND_NOT_ALLOWED", "notification.error.not_stored", status_code=409)
            if status == "READ":
                if notification["status"] != "CLICKED":
                    notification["status"] = "READ"
                if not notification["read_at"]:
                    notification["read_at"] = update.occurred_at
            elif status == "CLICKED":
                notification["status"] = "CLICKED"
                if not notification["clicked_at"]:
                    notification["clicked_at"] = update.occurred_at

        self.state.notification_events[update.event_id] = {
            "notification_event_id": update.event_id,
            "event_id": update.event_id,
            "event_type": update.event_type,
            "idempotency_key": update.idempotency_key,
            "notification_id": notification_id,
            "task_id": update.task_id,
            "delivery_status": status,
            "occurred_at": update.occurred_at,
            "error_code": update.error_code,
            "failure_reason": update.error_code,
            "request_hash": update_hash,
        }
        response = deepcopy(notification)
        self.state.notification_event_results[event_scope] = response
        self.state.notification_event_hashes[event_scope] = update_hash
        self.state.notification_event_results[key_scope] = response
        self.state.notification_event_hashes[key_scope] = update_hash
        self._append_event(
            "notification.delivery_event_recorded.audit.v1",
            teacher_id=notification["teacher_id"],
            task_id=notification["task_id"],
            payload={
                "notification_id": notification_id,
                "delivery_status": status,
                "error_code": update.error_code,
            },
        )
        notification_output = self._find_output(
            "IN_APP_NOTIFICATION",
            task_id=notification["task_id"],
            source_id=notification_id,
        )
        if notification_output:
            if status in {"FAILED", "STORED"}:
                notification_output["sent_at"] = update.occurred_at
                notification_output["attempt_count"] = max(1, int(notification_output["attempt_count"]))
            if status == "FAILED":
                notification_output["status"] = "FAILED"
                notification_output["last_error"] = notification["failure_reason"]
                notification_output["next_retry_at"] = (
                    self._parse_datetime(update.occurred_at) + timedelta(minutes=5)
                ).isoformat().replace("+00:00", "Z")
            elif status in {"STORED", "READ", "CLICKED"}:
                notification_output["status"] = notification["status"]
                notification_output["delivered_at"] = notification["stored_at"] or update.occurred_at
                notification_output["last_error"] = None
                notification_output["next_retry_at"] = None
            notification_output["updated_at"] = now_iso()
        if status == "STORED" and notification.get("response_due_at"):
            task = self._task(notification["task_id"])
            if task["priority"] == "P0" and task["completion_method"] == "CONFIRMATION_FORM":
                delivery_policy = self._delivery_policy_for_task(task)
                reminder_at = self._parse_datetime(notification["response_due_at"]) - timedelta(
                    minutes=delivery_policy.p0_reminder_minutes_before_response_due
                )
                self._ensure_reminder_output(
                    task,
                    scheduled_at=reminder_at.isoformat().replace("+00:00", "Z"),
                    reminder_scope="P0_RESPONSE_WINDOW",
                    merge_allowed=False,
                    expires_at=notification["response_due_at"],
                )
        self._register_global_event(
            update.event_id,
            update_hash,
            notification_event_type,
        )
        return response

    @persisted_command
    def execute_command(
        self,
        task_id: str,
        command: TaskCommand,
        *,
        actor_id: str | None = None,
    ) -> dict:
        task = self._task(task_id)
        if command.task_id != task_id:
            raise DomainError("TASK_NOT_OWNED", "task.error.not_found", status_code=404)
        execution = self._execution(task_id)
        command_dict = command.model_dump()
        command_hash = hashlib.sha256(json.dumps(command_dict, sort_keys=True).encode("utf-8")).hexdigest()
        command_event_type = f"task.command.v1:{command.command_type}"
        self._assert_global_event(command.event_id, command_hash, command_event_type)
        if command.idempotency_key in self.state.command_results:
            if self.state.command_hashes[command.idempotency_key] != command_hash:
                raise DomainError("DUPLICATE_CONFLICT", "task.error.idempotency_conflict", status_code=409)
            self._register_global_event(command.event_id, command_hash, command_event_type)
            return deepcopy(self.state.command_results[command.idempotency_key])
        if command.event_id in self.state.command_event_results:
            if self.state.command_event_hashes[command.event_id] != command_hash:
                raise DomainError("DUPLICATE_CONFLICT", "task.error.event_id_conflict", status_code=409)
            self._register_global_event(command.event_id, command_hash, command_event_type)
            return deepcopy(self.state.command_event_results[command.event_id])
        if task.get("assignment_status") not in {"ACKNOWLEDGED", "REVISION_PENDING"}:
            raise DomainError(
                "COMMAND_NOT_ALLOWED",
                "task.error.assignment_not_executable",
                status_code=409,
                details={"assignment_status": task.get("assignment_status")},
            )
        if (
            command.command_type == "RESPOND_CONFIRMATION"
            and execution.get("runtime_status") == "COMPLETED"
        ):
            raise DomainError(
                "CONFIRMATION_ALREADY_FINAL",
                "task.confirmation.already_final",
                status_code=409,
            )
        if command.command_type in {
            "START_TASK",
            "RETRY_TASK",
            "SUBMIT_TASK",
            "RESPOND_CONFIRMATION",
        } and self._task_slot_state(task) != "ACTIVE":
            raise DomainError(
                "COMMAND_NOT_ALLOWED",
                "task.error.not_in_current_queue",
                status_code=409,
                details={"slot_state": self._task_slot_state(task)},
            )
        if command.command_type not in {"VIEW_TASK", "REQUEST_REVIEW"} and execution.get("runtime_status") in {
            "COMPLETED",
            "FAILED_FINAL",
            "CANCELLED_BY_WITHDRAWAL",
        }:
            raise DomainError(
                "COMMAND_NOT_ALLOWED",
                "task.error.execution_terminal",
                status_code=409,
                details={"runtime_status": execution.get("runtime_status")},
            )
        self._parse_datetime(command.occurred_at)
        if command.expected_execution_contract_version != task["execution_contract_version"]:
            raise DomainError(
                "EXECUTION_CONTRACT_VERSION_MISMATCH",
                "task.error.refresh_and_retry",
                status_code=409,
                field_path="$.expected_execution_contract_version",
                details={"current_execution_contract_version": task["execution_contract_version"]},
            )
        if command.last_seen_runtime_sequence != execution["runtime_sequence"]:
            raise DomainError(
                "STALE_RUNTIME_SEQUENCE",
                "task.error.refresh_and_retry",
                status_code=409,
                field_path="$.last_seen_runtime_sequence",
                details={"current_runtime_sequence": execution["runtime_sequence"]},
            )
        if command.completion_method != task["completion_method"]:
            raise DomainError(
                "COMPLETION_METHOD_MISMATCH",
                "task.error.completion_method_mismatch",
                field_path="$.completion_method",
            )
        if command.command_type in {"SUBMIT_TASK", "RESPOND_CONFIRMATION"}:
            schema_errors = sorted(SUBMISSION_VALIDATOR.iter_errors(command_dict), key=lambda item: list(item.path))
            if schema_errors:
                first = schema_errors[0]
                field_path = "$" + "".join(f"[{part}]" if isinstance(part, int) else f".{part}" for part in first.path)
                raise DomainError(
                    "PAYLOAD_SCHEMA_INVALID",
                    "task.error.invalid_payload",
                    status_code=422,
                    field_path=field_path,
                    details={"reason_code": "JSON_SCHEMA_REJECTED"},
                )
        elif command.payload:
            raise DomainError(
                "PAYLOAD_SCHEMA_INVALID",
                "task.error.empty_payload_required",
                status_code=422,
                field_path="$.payload",
                details={"reason_code": "COMMAND_PAYLOAD_MUST_BE_EMPTY"},
            )

        if command.command_type == "VIEW_TASK":
            if execution["runtime_status"] in {"AVAILABLE", "VIEWED"}:
                self._transition(task, execution, "TASK_VIEWED", "VIEWED", None)
        elif command.command_type == "START_TASK":
            if execution["runtime_status"] not in {"AVAILABLE", "VIEWED"}:
                raise DomainError("COMMAND_NOT_ALLOWED", "task.error.start_not_allowed", status_code=409)
            self._transition(task, execution, "TASK_STARTED", "STARTED", None)
        elif command.command_type == "RETRY_TASK":
            if execution["runtime_status"] != "RETRY_REQUIRED":
                raise DomainError(
                    "COMMAND_NOT_ALLOWED",
                    "task.error.retry_not_allowed",
                    status_code=409,
                )
            self._transition(task, execution, "TASK_STARTED", "STARTED", None)
        elif command.command_type in {"REQUEST_HELP", "REQUEST_REVIEW"}:
            runtime_event_code = (
                "HELP_REQUESTED"
                if command.command_type == "REQUEST_HELP"
                else "REVIEW_REQUESTED"
            )
            self._transition(
                task,
                execution,
                runtime_event_code,
                execution["runtime_status"],
                execution.get("verification_result"),
            )
            self._create_teacher_request_case(task, source_reason=runtime_event_code)
        elif command.command_type == "RESPOND_CONFIRMATION":
            self._respond_confirmation(task, execution, command.payload)
        elif command.command_type == "SUBMIT_TASK":
            self._submit_task(task, execution, command.payload)

        self._append_event(
            "task.operator_command.v1",
            teacher_id=task["teacher_id"],
            task_id=task_id,
            **self._operator_fields(actor_id),
            payload={
                "command_type": command.command_type,
                "command_event_id": command.event_id,
                "idempotency_key": command.idempotency_key,
            },
        )
        result = self.task_detail(task_id)
        self.state.command_hashes[command.idempotency_key] = command_hash
        self.state.command_results[command.idempotency_key] = deepcopy(result)
        self.state.command_event_hashes[command.event_id] = command_hash
        self.state.command_event_results[command.event_id] = deepcopy(result)
        self._register_global_event(command.event_id, command_hash, command_event_type)
        return result

    def _create_teacher_request_case(self, task: dict, *, source_reason: str) -> dict:
        existing = next(
            (
                case
                for case in self.state.ops_cases.values()
                if case.get("task_id") == task["task_id"]
                and case.get("source_reason") == source_reason
                and case.get("status") in {"OPEN", "ACTION_REQUESTED"}
            ),
            None,
        )
        if existing:
            return existing
        created_at = now_iso()
        is_help = source_reason == "HELP_REQUESTED"
        case = {
            "case_id": new_id("CASE"),
            "case_type": "OPS-C04",
            "teacher_id": task["teacher_id"],
            "task_id": task["task_id"],
            "priority": task["priority"],
            "status": "OPEN",
            "summary": (
                "老师请求任务协助，需要运营提供受控支持。"
                if is_help
                else "老师请求复核任务结果，需要运营检查事实与验证记录。"
            ),
            "source_reason": source_reason,
            "recommended_action": "PROVIDE_TASK_SUPPORT" if is_help else "REVIEW_TASK_RESULT",
            "external_action_status": "NOT_REQUESTED",
            "created_at": created_at,
            "decision": None,
        }
        self.state.ops_cases[case["case_id"]] = case
        self._append_event(
            "ops_case.teacher_request_recorded.audit.v1",
            teacher_id=task["teacher_id"],
            task_id=task["task_id"],
            case_id=case["case_id"],
            payload={"case_type": case["case_type"], "source_reason": source_reason},
        )
        self._create_output(
            output_type="OPS_REVIEW_CASE",
            display_type="OPS_CASE",
            audience_type="OPS",
            recipient_id="OPS_QUEUE",
            recipient_name="Teacher Operations",
            channel="OPS_ACTION_QUEUE",
            source_type="TEACHER_TASK_REQUEST",
            source_id=case["case_id"],
            teacher_id=task["teacher_id"],
            task_id=task["task_id"],
            case_id=case["case_id"],
            status="DELIVERED",
            title=case["case_type"],
            body=case["summary"],
            created_at=created_at,
            delivered_at=created_at,
            retryable=False,
            requires_human_approval=True,
            idempotency_key=f'teacher_request_case:{task["task_id"]}:{source_reason}',
            payload=self._ops_case_safe_event(case),
        )
        return case

    def _respond_confirmation(self, task: dict, execution: dict, payload: dict) -> None:
        if task["completion_method"] != "CONFIRMATION_FORM":
            raise DomainError("COMMAND_NOT_ALLOWED", "task.error.confirmation_not_allowed", status_code=409)
        if execution["runtime_status"] not in {"AVAILABLE", "VIEWED", "STARTED"}:
            raise DomainError("CONFIRMATION_ALREADY_FINAL", "task.confirmation.already_final", status_code=409)
        timeout_case = next(
            (
                case
                for case in self.state.ops_cases.values()
                if case["task_id"] == task["task_id"] and case.get("source_reason") == "RESPONSE_TIMEOUT"
            ),
            None,
        )
        if timeout_case:
            raise DomainError(
                "CONFIRMATION_EXPIRED",
                "task.confirmation.expired_after_ops_escalation",
                status_code=409,
                details={"case_id": timeout_case["case_id"]},
            )
        config = task["action_schema"]["config"]
        if payload.get("question_id") != config["question_id"] or payload.get("question_version") != config["question_version"]:
            raise DomainError("INVALID_CONFIRMATION_OPTION", "task.confirmation.version_mismatch", status_code=409)
        allowed_options = {item["option_code"] for item in config["options"]}
        option = payload.get("selected_option_code")
        if option not in allowed_options:
            raise DomainError(
                "INVALID_CONFIRMATION_OPTION",
                "task.confirmation.invalid_option",
                field_path="$.payload.selected_option_code",
            )
        if config.get("requires_consequence_ack") and not payload.get("consequence_acknowledged"):
            raise DomainError(
                "CONFIRMATION_ACK_REQUIRED",
                "task.confirmation.consequence_ack_required",
                field_path="$.payload.consequence_acknowledged",
            )
        execution["selected_option_code"] = option
        self._transition(task, execution, "CONFIRMATION_RECORDED", "COMPLETED", "PASSED")
        if option == "CANNOT_CONTINUE":
            self._create_first_lesson_case(task, source_reason="CANNOT_CONTINUE")

    def _create_first_lesson_case(self, task: dict, *, source_reason: str) -> dict:
        existing = next(
            (
                case
                for case in self.state.ops_cases.values()
                if case["task_id"] == task["task_id"] and case["case_type"] == "OPS-C01"
            ),
            None,
        )
        if existing:
            return existing
        is_timeout = source_reason == "RESPONSE_TIMEOUT"
        case = {
            "case_id": new_id("CASE"),
            "case_type": "OPS-C01",
            "teacher_id": task["teacher_id"],
            "task_id": task["task_id"],
            "priority": "P0",
            "status": "OPEN",
            "summary": (
                "确认响应窗口已到期，但老师尚未提交答复；不得推断为无法继续，需要运营介入。"
                if is_timeout
                else "老师确认无法继续授课，需要运营核对后续课程范围与处置权限。"
            ),
            "source_reason": source_reason,
            "recommended_action": "REVIEW_COURSE_RELEASE_SCOPE",
            "external_action_status": "NOT_REQUESTED",
            "created_at": now_iso(),
            "decision": None,
        }
        self.state.ops_cases[case["case_id"]] = case
        self._append_event(
            "ops_case.created.audit.v1",
            teacher_id=task["teacher_id"],
            task_id=task["task_id"],
            payload={
                "case_id": case["case_id"],
                "case_type": case["case_type"],
                "source_reason": source_reason,
            },
        )
        self._create_output(
            output_type="OPS_REVIEW_CASE",
            display_type="OPS_CASE",
            audience_type="OPS",
            recipient_id="OPS_QUEUE",
            recipient_name="Teacher Operations",
            channel="OPS_ACTION_QUEUE",
            source_type="OPS_CASE",
            source_id=case["case_id"],
            teacher_id=case["teacher_id"],
            task_id=case["task_id"],
            case_id=case["case_id"],
            status="DELIVERED",
            title=case["case_type"],
            body=case["summary"],
            created_at=case["created_at"],
            delivered_at=case["created_at"],
            retryable=False,
            requires_human_approval=True,
            idempotency_key=f'ops_case:{case["case_id"]}:created',
            payload=self._ops_case_safe_event(case),
        )
        return case

    @persisted_command
    def run_escalations(self, request: EscalationRunRequest) -> dict:
        as_of = self._parse_datetime(request.as_of)
        created: list[dict] = []
        skipped: list[dict] = []
        for task in self.state.tasks.values():
            if task["completion_method"] != "CONFIRMATION_FORM" or task["assignment_status"] != "ACKNOWLEDGED":
                continue
            execution = self.state.executions.get(task["task_id"])
            if not execution or execution["runtime_status"] == "COMPLETED":
                continue
            notification_id = self.state.notification_by_task.get(task["task_id"])
            notification = self.state.notifications.get(notification_id) if notification_id else None
            if notification and notification["status"] == "INTEGRATION_FAILED":
                skipped.append({"task_id": task["task_id"], "reason": "NOTIFICATION_INTEGRATION_FAILED"})
                continue
            if not notification or not notification.get("response_due_at"):
                skipped.append({"task_id": task["task_id"], "reason": "RESPONSE_WINDOW_NOT_STARTED"})
                continue
            if self._parse_datetime(notification["response_due_at"]) > as_of:
                skipped.append({"task_id": task["task_id"], "reason": "NOT_DUE"})
                continue
            existing = next(
                (case for case in self.state.ops_cases.values() if case["task_id"] == task["task_id"]),
                None,
            )
            if existing:
                skipped.append({"task_id": task["task_id"], "reason": "CASE_EXISTS"})
                continue
            created.append(deepcopy(self._create_first_lesson_case(task, source_reason="RESPONSE_TIMEOUT")))
        return {"as_of": request.as_of, "created": created, "skipped": skipped}

    def _submit_task(self, task: dict, execution: dict, payload: dict) -> None:
        if task["completion_method"] == "CONFIRMATION_FORM":
            raise DomainError("COMMAND_NOT_ALLOWED", "task.error.use_confirmation_command", status_code=409)
        allowed_source_statuses = {"AVAILABLE", "VIEWED", "STARTED", "RETRY_REQUIRED"}
        if execution["runtime_status"] not in allowed_source_statuses:
            raise DomainError(
                "COMMAND_NOT_ALLOWED",
                "task.error.submit_not_allowed",
                status_code=409,
                details={"runtime_status": execution["runtime_status"]},
            )
        if task["completion_method"] == "QUIZ":
            self._validate_mock_quiz_submission(task, execution, payload)
            self._transition(task, execution, "TASK_SUBMITTED", "SUBMITTED", "PENDING")
            execution["score_percent"] = 100
            self._transition(task, execution, "VERIFICATION_PASSED", "COMPLETED", "PASSED")
            return
        if task["completion_method"] == "CHECKLIST":
            self._validate_checklist_submission(task, payload)
            self._transition(task, execution, "TASK_SUBMITTED", "SUBMITTED", "PENDING")
            self._transition(task, execution, "VERIFICATION_PASSED", "COMPLETED", "PASSED")
            return
        self._transition(task, execution, "TASK_SUBMITTED", "VERIFYING", "PENDING")
        call_id = new_id("PROVIDER_CALL")
        config = task["action_schema"].get("config", {})
        self.state.provider_calls[call_id] = {
            "provider_call_id": call_id,
            "provider_event_id": None,
            "task_id": task["task_id"],
            "provider_id": config.get("provider_id", task["completion_method"]),
            "call_type": task["completion_method"],
            "status": "PENDING",
            "request_payload": deepcopy(payload),
            "result_payload": None,
            "created_at": now_iso(),
            "completed_at": None,
        }

    def _validate_mock_quiz_submission(self, task: dict, execution: dict, payload: dict) -> None:
        expected_session_id = f'quiz-session-{task["task_id"]}'
        if payload.get("quiz_session_id") != expected_session_id:
            raise DomainError(
                "QUIZ_SESSION_INVALID",
                "task.quiz.session_not_bound",
                field_path="$.payload.quiz_session_id",
            )
        allowed_questions = {"mock-question-1": {"mock-option-a"}}
        seen_questions: set[str] = set()
        for index, answer in enumerate(payload.get("answers", [])):
            question_id = answer.get("question_id")
            if question_id not in allowed_questions:
                raise DomainError(
                    "UNKNOWN_QUIZ_ITEM",
                    "task.quiz.question_not_in_session",
                    field_path=f"$.payload.answers[{index}].question_id",
                )
            if question_id in seen_questions:
                raise DomainError(
                    "UNKNOWN_QUIZ_ITEM",
                    "task.quiz.duplicate_question",
                    field_path=f"$.payload.answers[{index}].question_id",
                )
            seen_questions.add(question_id)
            selected = set(answer.get("selected_option_ids", []))
            if not selected or not selected <= allowed_questions[question_id]:
                raise DomainError(
                    "INVALID_QUIZ_OPTION",
                    "task.quiz.option_not_allowed",
                    field_path=f"$.payload.answers[{index}].selected_option_ids",
                )
        if seen_questions != set(allowed_questions):
            raise DomainError(
                "UNKNOWN_QUIZ_ITEM",
                "task.quiz.required_question_missing",
                field_path="$.payload.answers",
            )
        execution["quiz_session_id"] = expected_session_id

    def _validate_checklist_submission(self, task: dict, payload: dict) -> None:
        config = task["action_schema"]["config"]
        if payload.get("checklist_id") != config["checklist_id"] or payload.get("checklist_version") != config["checklist_version"]:
            raise DomainError(
                "UNKNOWN_CHECKLIST_ITEM",
                "task.checklist.version_mismatch",
                field_path="$.payload.checklist_id",
            )
        items = {item["item_id"]: item for item in config["items"]}
        checked = set(payload.get("checked_item_ids", []))
        unknown = checked - set(items)
        if unknown:
            raise DomainError(
                "UNKNOWN_CHECKLIST_ITEM",
                "task.checklist.item_not_allowed",
                field_path="$.payload.checked_item_ids",
                details={"unknown_item_ids": sorted(unknown)},
            )
        required = {item_id for item_id, item in items.items() if item["required"]}
        missing = required - checked
        if missing:
            raise DomainError(
                "REQUIRED_CHECKLIST_ITEM_MISSING",
                "task.checklist.required_item_missing",
                field_path="$.payload.checked_item_ids",
                details={"missing_item_ids": sorted(missing)},
            )
        attestation = config.get("attestation", {})
        if attestation.get("required") and (
            not payload.get("attestation_accepted")
            or payload.get("attestation_version") != attestation.get("version")
        ):
            raise DomainError(
                "ATTESTATION_REQUIRED",
                "task.checklist.attestation_required",
                field_path="$.payload.attestation_accepted",
            )

    @persisted_command
    def apply_trusted_result(self, task_id: str, result: TrustedResult) -> dict:
        task = self._task(task_id)
        execution = self._execution(task_id)
        result_hash = hashlib.sha256(
            json.dumps(
                {"task_id": task_id, "result": result.model_dump()},
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        cached = self.state.provider_event_results.get(result.provider_event_id)
        if cached:
            if self.state.provider_event_hashes[result.provider_event_id] != result_hash:
                raise DomainError("DUPLICATE_CONFLICT", "task.error.provider_event_conflict", status_code=409)
            return deepcopy(cached)
        if task["completion_method"] not in {"UPLOAD_REVIEW", "DEVICE_CHECK", "EXTERNAL_SYNC"}:
            raise DomainError("COMMAND_NOT_ALLOWED", "task.error.trusted_result_not_allowed", status_code=409)
        if execution["runtime_status"] != "VERIFYING":
            raise DomainError("COMMAND_NOT_ALLOWED", "task.error.result_not_expected", status_code=409)
        if result.result == "PASSED":
            event_code = {
                "UPLOAD_REVIEW": "HUMAN_REVIEW_COMPLETED",
                "DEVICE_CHECK": "VERIFICATION_PASSED",
                "EXTERNAL_SYNC": "EXTERNAL_COMPLETION_SYNCED",
            }[task["completion_method"]]
            execution["result_ref"] = result.result_ref
            self._transition(task, execution, event_code, "COMPLETED", "PASSED", provider_event_id=result.provider_event_id)
        elif result.result == "RETRY_REQUIRED":
            self._transition(
                task,
                execution,
                "VERIFICATION_RETRY_REQUIRED",
                "RETRY_REQUIRED",
                "RETRY_REQUIRED",
                provider_event_id=result.provider_event_id,
            )
        else:
            self._transition(
                task,
                execution,
                "VERIFICATION_REJECTED",
                "FAILED_FINAL",
                "REJECTED",
                provider_event_id=result.provider_event_id,
            )
        response = self.task_detail(task_id)
        pending_call = next(
            (
                call
                for call in self.state.provider_calls.values()
                if call["task_id"] == task_id and call["status"] == "PENDING"
            ),
            None,
        )
        if pending_call:
            pending_call["provider_event_id"] = result.provider_event_id
            pending_call["status"] = result.result
            pending_call["result_payload"] = result.model_dump()
            pending_call["completed_at"] = now_iso()
        self.state.provider_event_hashes[result.provider_event_id] = result_hash
        self.state.provider_event_results[result.provider_event_id] = deepcopy(response)
        return response

    @persisted_command
    def decide_case(
        self,
        case_id: str,
        decision: CaseDecision,
        *,
        actor_id: str | None = None,
    ) -> dict:
        case = self._case(case_id)
        if case["status"] != "OPEN":
            raise DomainError("COMMAND_NOT_ALLOWED", "case.error.already_closed", status_code=409)
        if decision.decision == "REQUEST_COURSE_RELEASE" and case["case_type"] != "OPS-C01":
            raise DomainError(
                "COMMAND_NOT_ALLOWED",
                "case.error.course_release_not_allowed",
                status_code=409,
            )
        case["decision"] = decision.decision
        case["decision_note"] = decision.note
        case["status"] = "CLOSED" if decision.decision != "REQUEST_COURSE_RELEASE" else "ACTION_REQUESTED"
        case["external_action_status"] = (
            "REQUESTED_PENDING_APPROVAL" if decision.decision == "REQUEST_COURSE_RELEASE" else "NOT_REQUESTED"
        )
        case["decided_at"] = now_iso()
        case["decided_by"] = actor_id
        decision_id = new_id("DECISION")
        self.state.ops_decisions[decision_id] = {
            "decision_id": decision_id,
            "case_id": case_id,
            "decision": decision.decision,
            "note": decision.note,
            "decided_at": case["decided_at"],
            "actor_type": "OPERATOR",
            "actor_id": actor_id,
        }
        self._append_event(
            "ops_case.decided.v1",
            teacher_id=case["teacher_id"],
            task_id=case["task_id"],
            case_id=case_id,
            **self._operator_fields(actor_id),
            payload={
                "case_id": case_id,
                "decision": decision.decision,
                "actor_id": actor_id,
            },
        )
        if decision.decision == "REQUEST_COURSE_RELEASE":
            action_idempotency_key = f"course_release:{case_id}:decision"
            self._create_output(
                output_type="SYSTEM_ACTION_REQUEST",
                display_type="EXTERNAL_ACTION_REQUEST",
                audience_type="EXTERNAL_SYSTEM",
                recipient_id="COURSE_OPERATIONS_APPROVER",
                recipient_name="Course Operations Approval",
                channel="APPROVAL_QUEUE",
                source_type="OPS_DECISION",
                source_id=decision_id,
                teacher_id=case["teacher_id"],
                task_id=case.get("task_id"),
                case_id=case_id,
                status="ACTION_PENDING",
                title="Course release request",
                body="Pending authorized review; no external course change has been executed.",
                created_at=case["decided_at"],
                retryable=False,
                requires_human_approval=True,
                idempotency_key=action_idempotency_key,
                payload=self._system_action_safe_event(
                    case=case,
                    request_id=decision_id,
                    occurred_at=case["decided_at"],
                    idempotency_key=action_idempotency_key,
                ),
            )
        return deepcopy(case)

    def list_outputs(
        self,
        *,
        type_filter: str | None = None,
        status: str | None = None,
        teacher_id: str | None = None,
    ) -> list[dict]:
        outputs = [deepcopy(item) for item in self.state.outbound_outputs.values()]
        # Agent/provider records are visible for debugging on the same page, but
        # they are not persisted as one of the four business output categories.
        outputs.extend(self._agent_debug_outputs())
        if type_filter:
            outputs = [
                item
                for item in outputs
                if item.get("output_type") == type_filter or item["display_type"] == type_filter
            ]
        if status:
            outputs = [item for item in outputs if item["status"] == status]
        if teacher_id:
            outputs = [item for item in outputs if item.get("teacher_id") == teacher_id]
        return sorted(
            outputs,
            key=lambda item: (item["created_at"], item["output_id"]),
            reverse=True,
        )

    def _agent_debug_outputs(self) -> list[dict]:
        debug: list[dict] = []
        for plan in self.state.agent_plans.values():
            if plan.get("route") != "AGENT":
                continue
            fallback_reason = plan.get("fallback_reason")
            debug.append(
                {
                    "output_id": f'DEBUG-{plan["plan_id"]}',
                    "output_type": None,
                    "display_type": "PROVIDER_REQUEST",
                    "delivery_kind": "AGENT_PLANNING",
                    "non_business": True,
                    "audience_type": "EXTERNAL_SYSTEM",
                    "recipient_id": plan.get("model") or plan.get("planner"),
                    "recipient_name": "Bounded Agent Planner",
                    "channel": plan.get("planner", "DETERMINISTIC_POLICY"),
                    "source_type": "AGENT_DECISION",
                    "source_id": plan["plan_id"],
                    "teacher_id": plan.get("teacher_id"),
                    "task_id": None,
                    "case_id": None,
                    "status": "DELIVERED",
                    "title": "受约束任务编排",
                    "body": (
                        f'执行 {len(plan.get("selected_action_ids", []))} 个受约束生命周期动作'
                        + (f'；已安全降级：{fallback_reason}' if fallback_reason else "")
                    ),
                    "scheduled_at": None,
                    "created_at": plan["created_at"],
                    "sent_at": plan["created_at"],
                    "delivered_at": plan["created_at"],
                    "attempt_count": 1,
                    "max_attempts": 1,
                    "next_retry_at": None,
                    "last_error": fallback_reason,
                    "retryable": False,
                    "requires_human_approval": False,
                    "payload": {
                        "mode": plan.get("mode"),
                        "model": plan.get("model"),
                        "candidate_template_ids": plan.get("candidate_template_ids", []),
                        "selected_action_ids": plan.get("selected_action_ids", []),
                        "selected_template_ids": plan.get("selected_template_ids", []),
                        "primary_template_id": plan.get("primary_template_id"),
                        "decision_codes": plan.get("decision_codes", []),
                        "input_hash": plan.get("input_hash"),
                        "latency_ms": plan.get("latency_ms", 0),
                        "usage": plan.get("usage", {}),
                    },
                }
            )

        for call in self.state.provider_calls.values():
            raw_status = call.get("status")
            status = "REQUESTED" if raw_status == "PENDING" else "DELIVERED"
            debug.append(
                {
                    "output_id": f'DEBUG-{call["provider_call_id"]}',
                    "output_type": None,
                    "display_type": "PROVIDER_REQUEST",
                    "delivery_kind": "TASK_VERIFICATION",
                    "non_business": True,
                    "audience_type": "EXTERNAL_SYSTEM",
                    "recipient_id": call.get("provider_id"),
                    "recipient_name": "Task Verification Provider",
                    "channel": call.get("provider_id"),
                    "source_type": "PROVIDER_CALL",
                    "source_id": call["provider_call_id"],
                    "teacher_id": self.state.tasks.get(call["task_id"], {}).get("teacher_id"),
                    "task_id": call.get("task_id"),
                    "case_id": None,
                    "status": status,
                    "title": f'{call.get("call_type", "Provider")} 验证调用',
                    "body": f'Provider 状态：{raw_status}',
                    "scheduled_at": None,
                    "created_at": call["created_at"],
                    "sent_at": call["created_at"],
                    "delivered_at": call.get("completed_at"),
                    "attempt_count": 1,
                    "max_attempts": 1,
                    "next_retry_at": None,
                    "last_error": None,
                    "retryable": False,
                    "requires_human_approval": False,
                    "payload": {
                        "call_type": call.get("call_type"),
                        "provider_event_id": call.get("provider_event_id"),
                        "request_field_names": sorted((call.get("request_payload") or {}).keys()),
                        "result": (call.get("result_payload") or {}).get("result"),
                    },
                }
            )
        return debug

    def output_summary(self) -> dict:
        outputs = list(self.state.outbound_outputs.values())

        def counts(field: str) -> dict[str, int]:
            result: dict[str, int] = {}
            for output in outputs:
                key = output[field]
                result[key] = result.get(key, 0) + 1
            return dict(sorted(result.items()))

        return {
            "as_of": now_iso(),
            "total": len(outputs),
            "by_type": counts("output_type"),
            "by_output_type": counts("output_type"),
            "by_display_type": counts("display_type"),
            "by_status": counts("status"),
            "failed_retryable_count": sum(
                item["status"] == "FAILED" and item["retryable"] for item in outputs
            ),
            "waiting_human_approval_count": sum(
                item["status"] == "ACTION_PENDING" and item["requires_human_approval"]
                for item in outputs
            ),
            "planned_reminder_count": sum(
                item["display_type"] == "REMINDER" and item["status"] == "PLANNED"
                for item in outputs
            ),
        }

    @persisted_command
    def retry_output(self, output_id: str, *, actor_id: str | None = None) -> dict:
        output = self.state.outbound_outputs.get(output_id)
        if not output:
            raise DomainError("TASK_NOT_OWNED", "output.error.not_found", status_code=404)
        if output["requires_human_approval"]:
            raise DomainError(
                "COMMAND_NOT_ALLOWED",
                "output.error.human_approval_required",
                status_code=409,
            )
        if output["status"] != "FAILED" or not output["retryable"]:
            raise DomainError(
                "COMMAND_NOT_ALLOWED",
                "output.error.retry_not_allowed",
                status_code=409,
                details={"status": output["status"], "retryable": output["retryable"]},
            )
        if int(output["attempt_count"]) >= int(output["max_attempts"]):
            raise DomainError(
                "COMMAND_NOT_ALLOWED",
                "output.error.retry_limit_reached",
                status_code=409,
            )
        linked_notification = None
        if output["display_type"] == "IN_APP_NOTIFICATION":
            notification_id = output.get("source_id") or (output.get("payload") or {}).get(
                "notification_id"
            )
            linked_notification = self.state.notifications.get(notification_id)
            if (
                linked_notification is None
                or linked_notification.get("status") != "INTEGRATION_FAILED"
            ):
                raise DomainError(
                    "COMMAND_NOT_ALLOWED",
                    "notification.error.retry_state_mismatch",
                    status_code=409,
                )
        output["status"] = "REQUESTED"
        output["attempt_count"] += 1
        output["next_retry_at"] = None
        output["last_error"] = None
        output["sent_at"] = None
        output["delivered_at"] = None
        output["updated_at"] = now_iso()
        if linked_notification is not None:
            linked_notification["status"] = "REQUESTED"
            linked_notification["requested_at"] = output["updated_at"]
            linked_notification["failure_reason"] = None
            linked_notification["stored_at"] = None
            linked_notification["read_at"] = None
            linked_notification["clicked_at"] = None
            linked_notification["response_due_at"] = None
        self._append_event(
            "outbound_output.retry_requested.v1",
            teacher_id=output.get("teacher_id"),
            task_id=output.get("task_id"),
            case_id=output.get("case_id"),
            **self._operator_fields(actor_id),
            payload={
                "output_id": output_id,
                "attempt_count": output["attempt_count"],
                "display_type": output["display_type"],
                "actor_id": actor_id,
            },
        )
        return deepcopy(output)

    @staticmethod
    def _contract_event_hash(event: dict) -> str:
        """Hash a frozen event without making the hash self-referential."""

        canonical = {key: value for key, value in event.items() if key != "payload_hash"}
        digest = hashlib.sha256(
            json.dumps(
                canonical,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return f"sha256:{digest}"

    def _build_contract_event(
        self,
        *,
        event_type: str,
        occurred_at: str,
        trace_id: str,
        correlation_id: str,
        causation_id: str,
        policy_version: str,
        business_fields: dict,
    ) -> dict:
        """Build one exact, versioned cross-domain event envelope."""

        event = {
            "event_id": new_id("EVT"),
            "event_type": event_type,
            "occurred_at": occurred_at,
            "trace_id": trace_id,
            "correlation_id": correlation_id,
            "causation_id": causation_id,
            "policy_version": policy_version,
            **deepcopy(business_fields),
        }
        event["payload_hash"] = self._contract_event_hash(event)
        return event

    @staticmethod
    def _allowed_ops_actions(case: dict) -> list[str]:
        if case["case_type"] == "OPS-C01":
            return ["KEEP_SCHEDULE", "REQUEST_COURSE_RELEASE", "CLOSE_NO_ACTION"]
        return ["KEEP_SCHEDULE", "CLOSE_NO_ACTION"]

    def _ops_case_safe_event(self, case: dict) -> dict:
        task = self.state.tasks.get(case.get("task_id") or "", {})
        teacher = self.state.teachers.get(case["teacher_id"], {})
        response_minutes = {"P0": 30, "P1": 240, "P2": 1440, "P3": 2880}.get(
            case["priority"],
            1440,
        )
        return self._build_contract_event(
            event_type="ops_case.created.v1",
            occurred_at=case["created_at"],
            trace_id=f'trace:{task.get("obligation_id", case["case_id"])}',
            correlation_id=case.get("task_id") or case["teacher_id"],
            causation_id=case.get("task_id") or case["case_id"],
            policy_version="ops_case_policy_v1",
            business_fields={
                "case_id": case["case_id"],
                "case_type": case["case_type"],
                "teacher_id": case["teacher_id"],
                "camp_enrollment_id": task.get(
                    "camp_enrollment_id",
                    teacher.get("camp_enrollment_id", f'CAMP-{case["teacher_id"]}'),
                ),
                "priority": case["priority"],
                "sla_policy": {
                    "policy_ref": "OPS_CASE_PRIORITY_SLA_V1",
                    "response_within_minutes": response_minutes,
                },
                "evidence_refs": deepcopy(task.get("evidence_refs", [])),
                "allowed_ops_actions": self._allowed_ops_actions(case),
                "owner_queue": "TEACHER_OPERATIONS",
            },
        )

    def _system_action_safe_event(
        self,
        *,
        case: dict,
        request_id: str,
        occurred_at: str,
        idempotency_key: str,
    ) -> dict:
        task = self.state.tasks.get(case.get("task_id") or "", {})
        teacher = self.state.teachers.get(case["teacher_id"], {})
        camp_enrollment_id = task.get(
            "camp_enrollment_id",
            teacher.get("camp_enrollment_id", f'CAMP-{case["teacher_id"]}'),
        )
        return self._build_contract_event(
            event_type="system_action.requested.v1",
            occurred_at=occurred_at,
            trace_id=f'trace:{task.get("obligation_id", case["case_id"])}',
            correlation_id=case["case_id"],
            causation_id=request_id,
            policy_version="external_action_policy_v1",
            business_fields={
                "request_id": request_id,
                "action_type": "COURSE_RELEASE_REQUESTED",
                "target_ref": {
                    "target_type": "OPS_CASE",
                    "case_id": case["case_id"],
                    "teacher_id": case["teacher_id"],
                    "camp_enrollment_id": camp_enrollment_id,
                },
                # The lesson warehouse does not yet provide a trustworthy list
                # of future appointments.  This is intentionally non-executable
                # until the authorized approval domain resolves an exact scope.
                "scope": {
                    "status": "UNRESOLVED_REQUIRES_SCOPE_SELECTION",
                    "eligible_scope": "FUTURE_BOOKED_COURSES",
                    "appointment_ids": [],
                    "execution_allowed": False,
                },
                "risk_level": "HIGH",
                "approval_policy": {
                    "mode": "EXTERNAL_AUTHORIZED_APPROVER_REQUIRED",
                    "required_role": "COURSE_OPERATIONS_APPROVER",
                    "scope_selection_required": True,
                    "execution_allowed_before_approval": False,
                },
                "rollback_policy": {
                    "status": "UNRESOLVED_REQUIRES_PLAN",
                    "automatic_rollback": False,
                    "student_notification_plan_required": True,
                },
                "idempotency_key": idempotency_key,
            },
        )

    def _delivery_intent_safe_event(
        self,
        *,
        task: dict,
        delivery_intent_id: str,
        occurred_at: str,
        reminder_scope: str,
        merge_allowed: bool,
        expires_at: str | None,
    ) -> dict:
        return self._build_contract_event(
            event_type="delivery_intent.requested.v1",
            occurred_at=occurred_at,
            trace_id=f'trace:{task["obligation_id"]}',
            correlation_id=task["task_id"],
            causation_id=f'{task["task_id"]}:{reminder_scope}',
            policy_version=task.get("policy_version", "trigger_policy_v1"),
            business_fields={
                "delivery_intent_id": delivery_intent_id,
                "channel": "WEBAPP_INBOX",
                "template_id": "TASK_DUE_REMINDER",
                "version": 1,
                "safe_params": {
                    "task_id": task["task_id"],
                    "title": task["name"],
                    "due_at": task.get("due_at"),
                    "reminder_scope": reminder_scope,
                    "merge_allowed": merge_allowed,
                    "merge_group_key": (
                        f'{task["teacher_id"]}:NORMAL_TASK_REMINDERS'
                        if merge_allowed
                        else None
                    ),
                },
                "recipient_ref": {
                    "recipient_type": "TEACHER",
                    "teacher_id": task["teacher_id"],
                },
                "priority": task["priority"],
                "expires_at": expires_at,
            },
        )

    def _create_output(
        self,
        *,
        output_type: str,
        display_type: str,
        audience_type: str,
        source_type: str,
        source_id: str,
        status: str,
        title: str,
        created_at: str,
        idempotency_key: str,
        delivery_kind: str | None = None,
        recipient_id: str | None = None,
        recipient_name: str | None = None,
        channel: str | None = None,
        teacher_id: str | None = None,
        task_id: str | None = None,
        case_id: str | None = None,
        body: str = "",
        scheduled_at: str | None = None,
        sent_at: str | None = None,
        delivered_at: str | None = None,
        attempt_count: int = 0,
        max_attempts: int = 3,
        next_retry_at: str | None = None,
        last_error: str | None = None,
        retryable: bool = False,
        requires_human_approval: bool = False,
        payload: dict | None = None,
    ) -> dict:
        allowed_output_types = {
            "TEACHER_TASK",
            "OPS_REVIEW_CASE",
            "SYSTEM_ACTION_REQUEST",
            "DELIVERY_INTENT",
        }
        if output_type not in allowed_output_types:
            raise ValueError(f"Unsupported business output_type: {output_type}")
        existing = next(
            (
                item
                for item in self.state.outbound_outputs.values()
                if item["idempotency_key"] == idempotency_key
            ),
            None,
        )
        if existing:
            return existing
        output_id = new_id("OUTPUT")
        output = {
            "output_id": output_id,
            "output_type": output_type,
            "display_type": display_type,
            "delivery_kind": delivery_kind,
            "audience_type": audience_type,
            "recipient_id": recipient_id,
            "recipient_name": recipient_name,
            "channel": channel,
            "source_type": source_type,
            "source_id": source_id,
            "teacher_id": teacher_id,
            "task_id": task_id,
            "case_id": case_id,
            "status": status,
            "title": title,
            "body": body,
            "scheduled_at": scheduled_at,
            "created_at": created_at,
            "sent_at": sent_at,
            "delivered_at": delivered_at,
            "attempt_count": attempt_count,
            "max_attempts": max_attempts,
            "next_retry_at": next_retry_at,
            "last_error": last_error,
            "retryable": retryable,
            "requires_human_approval": requires_human_approval,
            "payload": deepcopy(payload or {}),
            "idempotency_key": idempotency_key,
            "updated_at": created_at,
        }
        self.state.outbound_outputs[output_id] = output
        canonical_event_types = {
            "task.issued.v1",
            "task.reprioritized.v1",
            "task.due_date_revised.v1",
            "task.content_revision_available.v1",
            "task.withdrawn.v1",
            "notification.requested.v1",
            "ops_case.created.v1",
            "system_action.requested.v1",
            "delivery_intent.requested.v1",
        }
        canonical_event = output["payload"]
        if (
            isinstance(canonical_event, dict)
            and canonical_event.get("event_type") in canonical_event_types
            and canonical_event.get("event_id")
            and not any(
                event.get("event_id") == canonical_event["event_id"]
                for event in self.state.events
            )
        ):
            # The exact same object is the business output payload and the
            # transactional outbox event; no second event id is generated.
            self.state.events.append(deepcopy(canonical_event))
        self._append_event(
            "outbound_output.created.v1",
            teacher_id=teacher_id,
            task_id=task_id,
            case_id=case_id,
            payload={
                "output_id": output_id,
                "output_type": output_type,
                "display_type": display_type,
                "status": status,
            },
        )
        return output

    def _find_output(
        self,
        display_type: str,
        *,
        task_id: str | None = None,
        source_id: str | None = None,
    ) -> dict | None:
        return next(
            (
                item
                for item in self.state.outbound_outputs.values()
                if item["display_type"] == display_type
                and (task_id is None or item.get("task_id") == task_id)
                and (source_id is None or item.get("source_id") == source_id)
            ),
            None,
        )

    def _ensure_reminder_output(
        self,
        task: dict,
        *,
        scheduled_at: str,
        reminder_scope: str,
        merge_allowed: bool,
        expires_at: str | None = None,
    ) -> dict:
        teacher = self.state.teachers.get(task["teacher_id"], {})
        created_at = now_iso()
        delivery_intent_id = new_id("INTENT")
        return self._create_output(
            output_type="DELIVERY_INTENT",
            display_type="REMINDER",
            delivery_kind="REMINDER",
            audience_type="TEACHER",
            recipient_id=task["teacher_id"],
            recipient_name=teacher.get("name"),
            channel="WEBAPP_INBOX",
            source_type="TASK_REMINDER_POLICY",
            source_id=f'{task["task_id"]}:{reminder_scope}',
            teacher_id=task["teacher_id"],
            task_id=task["task_id"],
            status="PLANNED",
            title=f'Reminder: {task["name"]}',
            body=task["public_reason"]["code"],
            scheduled_at=scheduled_at,
            created_at=created_at,
            retryable=True,
            idempotency_key=f'reminder:{task["task_id"]}:{reminder_scope}',
            payload=self._delivery_intent_safe_event(
                task=task,
                delivery_intent_id=delivery_intent_id,
                occurred_at=created_at,
                reminder_scope=reminder_scope,
                merge_allowed=merge_allowed,
                expires_at=expires_at or task.get("due_at"),
            ),
        )

    def _cancel_planned_reminders(
        self,
        task: dict,
        *,
        reason: str,
        timestamp: str | None = None,
    ) -> None:
        cancelled_at = timestamp or now_iso()
        for output in self.state.outbound_outputs.values():
            if (
                output.get("task_id") == task["task_id"]
                and output.get("display_type") == "REMINDER"
                and output.get("status") == "PLANNED"
            ):
                output["status"] = "CANCELLED"
                output["updated_at"] = cancelled_at
                output["cancel_reason"] = reason
                self._cancel_output_outbox(
                    output,
                    reason=reason,
                    cancelled_at=cancelled_at,
                )

    def _cancel_output_outbox(
        self,
        output: dict,
        *,
        reason: str,
        cancelled_at: str,
    ) -> None:
        event_id = (output.get("payload") or {}).get("event_id")
        if not event_id:
            return
        for outbox in self.state.outbox_events.values():
            if outbox.get("event_id") != event_id or outbox.get("status") != "PENDING":
                continue
            outbox["status"] = "CANCELLED"
            outbox["last_error"] = reason
            outbox["cancelled_at"] = cancelled_at

    def _reschedule_due_reminder(self, task: dict, *, reason: str) -> None:
        self._cancel_planned_reminders(task, reason=reason)
        if (
            task.get("completion_method") == "CONFIRMATION_FORM"
            or not task.get("due_at")
            or task.get("assignment_status") in {"SCHEDULED", "WITHDRAWN", "SUPERSEDED"}
            or self._task_is_terminal(task)
        ):
            return
        delivery_policy = self._delivery_policy_for_task(task)
        lead_minutes = (
            delivery_policy.urgent_reminder_minutes_before_due
            if task["priority"] in {"P0", "P1"}
            else delivery_policy.normal_reminder_minutes_before_due
        )
        current = datetime.now(timezone.utc).replace(microsecond=0)
        due_at = self._parse_datetime(task["due_at"])
        reminder_at = max(current, due_at - timedelta(minutes=lead_minutes))
        self._ensure_reminder_output(
            task,
            scheduled_at=reminder_at.isoformat().replace("+00:00", "Z"),
            reminder_scope=f'TASK_DUE:r{task["assignment_revision"]}',
            merge_allowed=True,
        )

    def action_queue(self) -> list[dict]:
        queue: list[dict] = []
        for case in self.state.ops_cases.values():
            if case["status"] in {"OPEN", "ACTION_REQUESTED"}:
                queue.append(
                    {
                        "queue_id": case["case_id"],
                        "queue_type": "OPS_REVIEW_CASE",
                        "priority": case["priority"],
                        "teacher_id": case["teacher_id"],
                        "title": case["case_type"],
                        "summary": case.get("summary") or "运营 Case 待查看。",
                        "status": case["status"],
                        "created_at": case["created_at"],
                    }
                )
        for task in self.state.tasks.values():
            if self._task_has_pending_assignment_ack(task):
                is_withdrawal = task["assignment_status"] == "WITHDRAWN"
                is_revision = task["assignment_status"] == "REVISION_PENDING"
                queue.append(
                    {
                        "queue_id": task["task_id"],
                        "queue_type": "DISPATCH_PENDING",
                        "priority": task["priority"],
                        "teacher_id": task["teacher_id"],
                        "title": task["name"],
                        "summary": (
                            f'任务撤回 r{task["assignment_revision"]} 已发布，等待老师端 ACK。'
                            if is_withdrawal
                            else (
                                f'任务修订 r{task["assignment_revision"]} 已发布，等待老师端 ACK。'
                                if is_revision
                                else "任务已签发，等待老师端 ACK。"
                            )
                        ),
                        "status": (
                            "WITHDRAWAL_PENDING" if is_withdrawal else task["assignment_status"]
                        ),
                        "assignment_revision": task["assignment_revision"],
                        "created_at": task["assigned_at"],
                    }
                )
            elif task["assignment_status"] == "ACKNOWLEDGED":
                execution = self.state.executions.get(task["task_id"])
                has_case = any(case["task_id"] == task["task_id"] for case in self.state.ops_cases.values())
                if task["priority"] == "P0" and execution and execution["runtime_status"] != "COMPLETED" and not has_case:
                    notification_id = self.state.notification_by_task.get(task["task_id"])
                    notification = self.state.notifications.get(notification_id) if notification_id else None
                    status = notification["status"] if notification else "NOT_REQUESTED"
                    is_integration_failure = status == "INTEGRATION_FAILED"
                    queue.append(
                        {
                            "queue_id": task["task_id"],
                            "queue_type": "INTEGRATION_EXCEPTION" if is_integration_failure else "P0_CONFIRMATION_WAITING",
                            "priority": task["priority"],
                            "teacher_id": task["teacher_id"],
                            "title": task["name"],
                            "summary": (
                                "站内通知投递失败，属于集成异常；不得计为老师未回复。"
                                if is_integration_failure
                                else f"老师端已 ACK；站内通知状态：{status}。尚未收到确认答复。"
                            ),
                            "status": status,
                            "response_due_at": notification.get("response_due_at") if notification else None,
                            "created_at": task["acknowledged_at"],
                        }
                    )
        priority_order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
        return sorted(queue, key=lambda item: (priority_order.get(item["priority"], 99), item["created_at"]))

    def list_tasks(self, teacher_id: str | None = None) -> list[dict]:
        tasks = self.state.tasks.values()
        if teacher_id:
            tasks = [item for item in tasks if item["teacher_id"] == teacher_id]
        return [self.task_detail(item["task_id"]) for item in tasks]

    def task_detail(self, task_id: str) -> dict:
        source_task = self._task(task_id)
        task = deepcopy(source_task)
        task["slot_state"] = self._task_slot_state(source_task)
        task["execution"] = deepcopy(self.state.executions.get(task_id))
        task["ops_cases"] = [
            deepcopy(case) for case in self.state.ops_cases.values() if case["task_id"] == task_id
        ]
        notification_id = self.state.notification_by_task.get(task_id)
        task["notification"] = (
            deepcopy(self.state.notifications.get(notification_id)) if notification_id else None
        )
        task["events"] = [deepcopy(event) for event in self.state.events if event.get("task_id") == task_id]
        return task

    def list_cases(self) -> list[dict]:
        return [deepcopy(item) for item in self.state.ops_cases.values()]

    def list_notifications(self) -> list[dict]:
        return [deepcopy(item) for item in self.state.notifications.values()]

    def list_events(self) -> list[dict]:
        return [deepcopy(item) for item in reversed(self.state.events)]

    def _transition(
        self,
        task: dict,
        execution: dict,
        runtime_event_code: str,
        runtime_status: str,
        verification_result: str | None,
        *,
        provider_event_id: str | None = None,
    ) -> None:
        execution["runtime_sequence"] += 1
        execution["runtime_status"] = runtime_status
        execution["verification_result"] = verification_result
        transition_at = now_iso()
        execution["last_event_at"] = transition_at
        teacher_originated_codes = {
            "TASK_VIEWED",
            "TASK_STARTED",
            "TASK_SUBMITTED",
            "CONFIRMATION_RECORDED",
            "HELP_REQUESTED",
            "REVIEW_REQUESTED",
        }
        actor_type = (
            "EXTERNAL_SYSTEM"
            if provider_event_id
            else ("TEACHER" if runtime_event_code in teacher_originated_codes else "TASK_TRIGGER_CENTER")
        )
        self._append_event(
            "task.runtime_event.v1",
            event_schema_version=1,
            idempotency_key=f'runtime:{task["task_id"]}:sequence_{execution["runtime_sequence"]}',
            trace_id=f'trace:{task["obligation_id"]}',
            correlation_id=task["task_id"],
            causation_id=provider_event_id or task["obligation_id"],
            task_id=task["task_id"],
            execution_contract_version=task["execution_contract_version"],
            runtime_event_code=runtime_event_code,
            runtime_sequence=execution["runtime_sequence"],
            runtime_status=runtime_status,
            verification_result=verification_result,
            actor_type=actor_type,
            actor_id=provider_event_id or ("TEACHER_RUNTIME" if actor_type == "TEACHER" else "TASK_TRIGGER_CENTER"),
            occurred_at=transition_at,
            payload={
                "attempt_no": int(execution.get("attempt_no", 0)),
                "result_ref": execution.get("result_ref"),
                "completed_at": (
                    transition_at
                    if runtime_status in {"COMPLETED", "FAILED_FINAL", "CANCELLED_BY_WITHDRAWAL"}
                    else None
                ),
            },
        )
        if runtime_status in {"SUBMITTED", "VERIFYING"}:
            task["slot_state"] = "IN_REVIEW"
            task["is_primary"] = False
            task["display_rank"] = 999
            self._cancel_planned_reminders(task, reason=f"TASK_{runtime_status}")
            self._rebalance_teacher_tasks(task["teacher_id"])
        elif runtime_status == "RETRY_REQUIRED":
            task["slot_state"] = "ROADMAP"
            task["is_primary"] = False
            task["display_rank"] = 999
            self._cancel_planned_reminders(task, reason="TASK_RETRY_WAITING_FOR_SLOT")
            self._rebalance_teacher_tasks(task["teacher_id"])
        elif runtime_status in {"COMPLETED", "FAILED_FINAL", "CANCELLED_BY_WITHDRAWAL"}:
            task["slot_state"] = "TERMINAL"
            task["is_primary"] = False
            task["display_rank"] = 999
            self._cancel_planned_reminders(task, reason=f"TASK_{runtime_status}")
            self._rebalance_teacher_tasks(task["teacher_id"])

    def _append_event(self, event_type: str, **fields: Any) -> dict:
        event = {
            "event_id": new_id("EVT"),
            "event_type": event_type,
            "occurred_at": now_iso(),
            **fields,
        }
        self.state.events.append(event)
        return event

    @staticmethod
    def _parse_datetime(value: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise DomainError(
                "PAYLOAD_SCHEMA_INVALID",
                "notification.error.invalid_occurred_at",
                status_code=422,
                field_path="$.occurred_at",
            ) from exc
        if parsed.tzinfo is None:
            raise DomainError(
                "PAYLOAD_SCHEMA_INVALID",
                "notification.error.timezone_required",
                status_code=422,
                field_path="$.occurred_at",
            )
        return parsed.astimezone(timezone.utc)

    def _execution_status(self, task_id: str) -> str:
        execution = self.state.executions.get(task_id)
        return execution["runtime_status"] if execution else "NOT_ACKNOWLEDGED"

    def _counts_as_teacher_waiting(self, task: dict) -> bool:
        if task["priority"] != "P0" or task["assignment_status"] not in {
            "ACKNOWLEDGED",
            "REVISION_PENDING",
        }:
            return False
        if self._execution_status(task["task_id"]) == "COMPLETED":
            return False
        if any(case["task_id"] == task["task_id"] for case in self.state.ops_cases.values()):
            return False
        notification_id = self.state.notification_by_task.get(task["task_id"])
        notification = self.state.notifications.get(notification_id) if notification_id else None
        return bool(notification and notification["status"] != "INTEGRATION_FAILED")

    def _teacher(self, teacher_id: str) -> dict:
        teacher = self.state.teachers.get(teacher_id)
        if not teacher:
            raise DomainError("TASK_NOT_OWNED", "teacher.error.not_found", status_code=404)
        return teacher

    def _template(self, template_id: str) -> dict:
        template = self.state.templates.get(template_id)
        if not template:
            raise DomainError("TASK_NOT_OWNED", "template.error.not_found", status_code=404)
        return template

    def _task(self, task_id: str) -> dict:
        task = self.state.tasks.get(task_id)
        if not task:
            raise DomainError("TASK_NOT_OWNED", "task.error.not_found", status_code=404)
        return task

    def _execution(self, task_id: str) -> dict:
        execution = self.state.executions.get(task_id)
        if not execution:
            raise DomainError("COMMAND_NOT_ALLOWED", "task.error.not_acknowledged", status_code=409)
        return execution

    def _case(self, case_id: str) -> dict:
        case = self.state.ops_cases.get(case_id)
        if not case:
            raise DomainError("TASK_NOT_OWNED", "case.error.not_found", status_code=404)
        return case

    def _notification(self, notification_id: str) -> dict:
        notification = self.state.notifications.get(notification_id)
        if not notification:
            raise DomainError("TASK_NOT_OWNED", "notification.error.not_found", status_code=404)
        return notification
