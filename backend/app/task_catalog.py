from __future__ import annotations

from typing import Any


MANDATORY_TASKS = [
    (
        "G01",
        "资料与资质完善",
        "Profile & Credentials Completion",
        4,
        "DAY_1_7",
        "P1",
        7,
        "BEFORE_FIRST_PUSH",
    ),
    (
        "G02",
        "设备与网络检测",
        "Device & Network Check",
        3,
        "DAY_1_7",
        "P1",
        7,
        "BEFORE_FIRST_LESSON",
    ),
    (
        "G03",
        "平台政策学习",
        "Platform Policies",
        3,
        "DAY_1_7",
        "P1",
        7,
        "BEFORE_FIRST_PUSH",
    ),
    (
        "G04",
        "不同类型学员应对",
        "How to handle different types of students",
        1,
        "DAY_1_7",
        "P1",
        7,
        None,
    ),
    (
        "G05",
        "首课备课",
        "Lesson Preparation",
        3,
        "DAY_1_7",
        "P1",
        7,
        "BEFORE_FIRST_LESSON",
    ),
    ("G06", "TTP 入门", "TTP Orientation", 1, "DAY_8_14", "P2", 14, None),
    (
        "G07",
        "ME 文化与 PARSNIP",
        "ME Culture & PARSNIP",
        2,
        "DAY_8_14",
        "P2",
        14,
        None,
    ),
    ("G08", "可靠性培训", "Reliability Training", 2, "DAY_8_14", "P1", 14, None),
    ("G09", "Cocos 课程培训", "Cocos Course Training", 10, "DAY_15_30", "P2", 30, None),
    (
        "G10",
        "SET 教学基础",
        "SET Teaching Fundamentals",
        1,
        "DAY_15_30",
        "P2",
        30,
        None,
    ),
]


# Current teacher-side implementation copy grounded in the 2026-07-20 task list.
# These strings and scores remain frozen while the runtime seed is narrowed to
# the ten mandatory tasks.
TASK_COPY: dict[str, tuple[str, str, str, str]] = {
    "G01": (
        "Your trial-camp profile, self-introduction and required credentials must be completed as part of first-push readiness.",
        "Complete the self-introduction flow, register the required web-app profile, and submit all required credentials for review.",
        "Return COMPLETED only after every required profile and credential item is present and the configured AI or human review has passed.",
        "Earn 4 mandatory-growth points once. This completes one component of first-push readiness and counts toward the 30-point mandatory total.",
    ),
    "G02": (
        "A verified device and network check is required before your first lesson and no later than Day 7.",
        "Open the trusted device-check entry, test your computer, network, microphone, speaker and camera, then follow any repair guidance.",
        "Return COMPLETED only after the trusted check result is PASS. An approved exception must return WAIVED, not COMPLETED.",
        "Earn 3 mandatory-growth points once and complete the device component of first-lesson readiness.",
    ),
    "G03": (
        "Platform policies and compliance rules must be understood before first-push eligibility can be confirmed.",
        "Study the assigned platform-policy content and complete the required knowledge check.",
        "Return COMPLETED only after all required policy modules are viewed and the configured quiz or acknowledgement passes.",
        "Earn 3 mandatory-growth points once and complete the policy component of first-push readiness.",
    ),
    "G04": (
        "Learning how to respond to different student types is required during Day 1-7.",
        "Complete the assigned learning module on recognizing and responding to different types of students.",
        "Return COMPLETED only after the required module and knowledge check are completed in the teacher app.",
        "Earn 1 mandatory-growth point once; it counts toward the 30-point mandatory total.",
    ),
    "G05": (
        "Lesson-preparation routines must be completed before your first lesson and no later than Day 7.",
        "Complete the preparation checklist, including lesson-material review and the required pre-class setup steps.",
        "Return COMPLETED only after every required checklist item is confirmed by the teacher app.",
        "Earn 3 mandatory-growth points once and complete the preparation component of first-lesson readiness.",
    ),
    "G06": (
        "You have entered Day 8-14 and TTP orientation is part of the required trial-camp learning path.",
        "Complete the TTP orientation module and its required checklist.",
        "Return COMPLETED only after all required TTP orientation items are finished.",
        "Earn 1 mandatory-growth point once; it counts toward the 30-point mandatory total.",
    ),
    "G07": (
        "ME culture and PARSNIP boundaries are required learning during Day 8-14.",
        "Study the assigned culture and PARSNIP content, then complete the configured quiz or acknowledgement.",
        "Return COMPLETED only after the required content and knowledge check pass.",
        "Earn 2 mandatory-growth points once; it counts toward the 30-point mandatory total.",
    ),
    "G08": (
        "Reliability, attendance, late and early-leave rules are required learning during Day 8-14.",
        "Complete the reliability training and its attendance-rule knowledge check.",
        "Return COMPLETED only after the required module is finished and the knowledge check passes.",
        "Earn 2 mandatory-growth points once; it counts toward the 30-point mandatory total.",
    ),
    "G09": (
        "Cocos course training is a required Day 15-30 capability task.",
        "Complete the assigned Cocos training in the linked training system.",
        "Return COMPLETED only after the trusted training system returns a valid Cocos completion tag.",
        "Earn 10 mandatory-growth points once; it counts toward the 30-point mandatory total.",
    ),
    "G10": (
        "SET teaching fundamentals are required during Day 15-30.",
        "Complete the assigned SET fundamentals training in the linked training system.",
        "Return COMPLETED only after the trusted training system returns a valid SET completion tag.",
        "Earn 1 mandatory-growth point once; it counts toward the 30-point mandatory total.",
    ),
    "P-REL-MEMO": (
        "A completed lesson was recorded with an unfilled Lesson Memo.",
        "Complete the Lesson Memo guidance and review how to submit an accurate memo after every lesson.",
        "The teacher app marks the assigned Lesson Memo learning activity as completed.",
        "This task carries no points. It closes the identified Lesson Memo reliability gap.",
    ),
    "P-REL-ATTENDANCE": (
        "A lesson record contains a reliability issue such as absence, late arrival or early leave.",
        "Complete the assigned attendance training and pass its quiz.",
        "The teacher app marks the training and quiz as completed.",
        "This task carries no points. It addresses the specific attendance issue shown in the task reason.",
    ),
    "P-FB-NEGATIVE": (
        "The same negative-feedback signal has appeared more than once for this teacher.",
        "Complete the learning activity assigned for the feedback issue shown in the task reason.",
        "The teacher app marks the matching learning activity as completed.",
        "This task carries no points. It targets a repeated learner-feedback issue.",
    ),
    "P-FB-COMPLAINT": (
        "A confirmed general complaint requires a focused learning response.",
        "Complete the learning activity assigned for the complaint category shown in the task reason.",
        "The teacher app marks the matching complaint-learning activity as completed.",
        "This task carries no points. It addresses the identified complaint issue.",
    ),
    "P-FB-BLACKLIST": (
        "More than one learner has independently blacklisted this teacher.",
        "Complete the assigned blacklist-prevention learning activity.",
        "The teacher app marks the blacklist learning activity as completed.",
        "This task carries no points. It addresses repeated learner rejection.",
    ),
}


PERSONALIZED_TASKS = [
    ("P-REL-MEMO", "Lesson Memo 改善", "Lesson Memo Improvement", "RELIABILITY", "P1", 48),
    ("P-REL-ATTENDANCE", "出席改善", "Attendance Improvement", "RELIABILITY", "P1", 48),
    ("P-FB-NEGATIVE", "差评改善", "Feedback Improvement", "USER_FEEDBACK", "P1", 72),
    ("P-FB-COMPLAINT", "投诉改善", "Complaint Improvement", "USER_FEEDBACK", "P1", 72),
    ("P-FB-BLACKLIST", "拉黑改善", "Blacklist Improvement", "USER_FEEDBACK", "P1", 72),
]


def task_template_seed_payloads() -> list[dict[str, Any]]:
    mandatory_payloads = [
        _template(
            task_id,
            ops_name,
            title,
            category="MANDATORY_GROWTH",
            dimension="NEW_TEACHER_TASK",
            stage=stage,
            priority=priority,
            due_rule={
                "type": "CAMP_DAY_OR_EVENT_DEADLINE",
                "camp_day": camp_day,
                "event": event,
                "fallback_hours": 168,
            },
            appeal_mode="HUMAN_REVIEW",
            score_type="FIXED",
            score_value=score,
            source_mode="REAL",
            integration_mode="INBOUND_STATUS_ONLY",
        )
        for task_id, ops_name, title, score, stage, priority, camp_day, event in MANDATORY_TASKS
    ]
    personalized_payloads = [
        _template(
            task_id,
            ops_name,
            title,
            category="PERSONALIZED_IMPROVEMENT",
            dimension=dimension,
            stage="TRIGGERED",
            priority=priority,
            due_rule={"type": "AFTER_TRIGGER", "hours": due_hours},
            appeal_mode="EXPLANATION_ALLOWED",
            score_type="ZERO",
            score_value=0,
            source_mode="REAL",
            integration_mode="OUTBOUND_MANAGED",
        )
        for task_id, ops_name, title, dimension, priority, due_hours in PERSONALIZED_TASKS
    ]
    payloads = mandatory_payloads + personalized_payloads
    assert len(mandatory_payloads) == 10
    assert len(personalized_payloads) == 5
    assert {item["template_id"] for item in payloads} == set(TASK_COPY)
    return payloads


def _template(
    task_id: str,
    ops_name_zh: str,
    title: str,
    *,
    category: str,
    dimension: str,
    stage: str,
    priority: str,
    due_rule: dict[str, Any],
    appeal_mode: str,
    score_type: str,
    score_value: float,
    source_mode: str,
    integration_mode: str,
) -> dict[str, Any]:
    why_template, how_summary, completion_standard, benefit = TASK_COPY[task_id]
    return {
        "template_id": task_id,
        "output_type": "TEACHER_TASK",
        "audience": "TEACHER",
        "owner": "TIT_GROWTH_OPS",
        "execution_owner": "TEACHER_APP",
        "integration_mode": integration_mode,
        "category": category,
        "dimension": dimension,
        "stage": stage,
        "ops_name_zh": ops_name_zh,
        "content_locale": "en",
        "title": title,
        "why_template": why_template,
        "how_summary": how_summary,
        "completion_standard": completion_standard,
        "benefit": benefit,
        "help_ref": "teacher-support://task-help",
        "priority": priority,
        "due_rule": due_rule,
        "appeal_mode": appeal_mode,
        "external_task_template_code": "TIT." + task_id.replace("-", "."),
        "action_url": None,
        "score_type": score_type,
        "score_value": score_value,
        "source_mode": source_mode,
        "source_refs": ["contracts/教师端共享任务表契约.md"],
    }
