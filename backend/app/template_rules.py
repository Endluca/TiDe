from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


TERMINAL_EXECUTION_STATUSES = {
    "COMPLETED",
    "FAILED_FINAL",
    "WAIVED",
    "EXPIRED",
    "CANCELLED_BY_WITHDRAWAL",
}


def template_matches_signal(template: dict, teacher: dict, signal: dict) -> bool:
    """Evaluate only configured, deterministic signal/evidence/scope gates."""

    rule = template.get("trigger_rule") or {}
    signal_codes = rule.get("signal_codes") or [template.get("trigger_code")]
    if signal.get("status") != "VALID" or signal.get("code") not in signal_codes:
        return False

    evidence_rule = rule.get("evidence") or {}
    evidence_status = signal.get("evidence_status") or (
        "CONFIRMED" if signal.get("status") == "VALID" else "PENDING"
    )
    accepted = evidence_rule.get("accepted_statuses") or ["CONFIRMED"]
    if evidence_status not in accepted:
        return False
    if len(signal.get("evidence_refs") or []) < int(evidence_rule.get("minimum_reference_count", 0)):
        return False

    scope = rule.get("scope") or {}
    graduation_states = scope.get("graduation_states") or ["IN_PROGRESS"]
    if teacher.get("graduation_state") not in graduation_states:
        return False
    countries = {str(country).casefold() for country in scope.get("countries") or []}
    if countries and str(teacher.get("country", "")).casefold() not in countries:
        return False
    camp_day = int(teacher.get("camp_day", 0))
    if camp_day < int(scope.get("minimum_camp_day", 0)):
        return False
    if camp_day > int(scope.get("maximum_camp_day", 365)):
        return False
    return True


def template_cooldown_block(
    template: dict,
    teacher_id: str,
    tasks: dict[str, dict],
    executions: dict[str, dict],
) -> dict[str, Any] | None:
    """Return a structured block only after exact signal idempotency is checked."""

    cooldown = (template.get("trigger_rule") or {}).get("cooldown") or {}
    matching = [
        task
        for task in tasks.values()
        if task.get("teacher_id") == teacher_id and task.get("template_id") == template.get("template_id")
        and task.get("assignment_status") not in {"WITHDRAWN", "SUPERSEDED"}
    ]
    active = [
        task
        for task in matching
        if task.get("assignment_status") not in {"WITHDRAWN", "SUPERSEDED"}
        if (executions.get(task["task_id"], {}).get("runtime_status") or "AVAILABLE")
        not in TERMINAL_EXECUTION_STATUSES
    ]
    maximum_active = int(cooldown.get("maximum_active_assignments", 1))
    if len(active) >= maximum_active:
        return {"reason": "ACTIVE_ASSIGNMENT_LIMIT", "existing_task_id": active[-1]["task_id"]}

    hours = int(cooldown.get("hours", 0))
    if not matching:
        return None
    allow_after_completion = bool(cooldown.get("allow_reissue_after_completion", True))
    if not active and not allow_after_completion:
        return {
            "reason": "REISSUE_AFTER_COMPLETION_DISABLED",
            "existing_task_id": matching[-1]["task_id"],
        }
    if hours <= 0:
        return None
    latest = max(matching, key=lambda item: item.get("assigned_at", ""))
    try:
        assigned_at = datetime.fromisoformat(str(latest["assigned_at"]).replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return {"reason": "COOLDOWN_ACTIVE", "existing_task_id": latest["task_id"]}
    if assigned_at.tzinfo is None:
        assigned_at = assigned_at.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) < assigned_at + timedelta(hours=hours):
        return {"reason": "COOLDOWN_ACTIVE", "existing_task_id": latest["task_id"]}
    return None


def signals_within_merge_window(template: dict, signals: list[dict]) -> list[dict]:
    """Apply the configured evidence window relative to the newest matched signal."""

    hours = int(
        ((template.get("trigger_rule") or {}).get("merge") or {}).get(
            "evidence_window_hours", 0
        )
    )
    if hours <= 0 or len(signals) <= 1:
        return signals
    parsed: list[tuple[dict, datetime]] = []
    for signal in signals:
        try:
            occurred_at = datetime.fromisoformat(str(signal["occurred_at"]).replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=timezone.utc)
        parsed.append((signal, occurred_at.astimezone(timezone.utc)))
    if not parsed:
        return signals
    newest = max(timestamp for _, timestamp in parsed)
    threshold = newest - timedelta(hours=hours)
    return [signal for signal, timestamp in parsed if timestamp >= threshold]
