from __future__ import annotations

from typing import Any


MANDATORY_TASKS = [
    (
        "G01",
        "资料与资质完善",
        "Profile & Credentials Completion",
        3,
        "DAY_1_7",
        "P1",
        7,
        "BEFORE_FIRST_PUSH",
    ),
    (
        "G02",
        "平台政策学习",
        "Platform Policies",
        2,
        "DAY_1_7",
        "P1",
        7,
        "BEFORE_FIRST_PUSH",
    ),
    (
        "G03",
        "不同类型学员应对",
        "How to handle different types of students",
        2,
        "DAY_1_7",
        "P1",
        7,
        None,
    ),
    (
        "G04",
        "首课备课与设备网络检测",
        "Lesson Preparation&Device Network Check",
        3,
        "DAY_1_7",
        "P1",
        7,
        "BEFORE_FIRST_LESSON",
    ),
    ("G05", "TTP 入门", "TTP Orientation", 3, "DAY_8_14", "P2", 14, None),
    (
        "G06",
        "ME 文化与 PARSNIP",
        "ME Culture & PARSNIP",
        4,
        "DAY_8_14",
        "P2",
        14,
        None,
    ),
    ("G07", "可靠性培训", "Reliability Training", 3, "DAY_8_14", "P1", 14, None),
    ("G08", "Cocos 课程培训", "Cocos Course Training", 5, "DAY_15_30", "P2", 30, None),
    (
        "G09",
        "SET 教学基础",
        "SET Teaching Fundamentals",
        5,
        "DAY_15_30",
        "P2",
        30,
        None,
    ),
]

MANDATORY_TASK_CODES = tuple(item[0] for item in MANDATORY_TASKS)
MANDATORY_TASK_CODE_SET = frozenset(MANDATORY_TASK_CODES)
RETIRED_MANDATORY_TASK_CODES = frozenset({"G00"})


# Current approved teacher-facing copy. Mandatory codes and scores remain the
# current G01-G09 catalog; G03 content is explicitly pending Jiahe's final input.
TASK_COPY: dict[str, tuple[str, str, str, str]] = {
    "G01": (
        "Complete the required profile statuses and TESOL learning evidence.",
        "Confirm Self-intro and TESOL, pass all 61 questions, complete the Essay and submit the completion proof.",
        "Self-intro and TESOL are complete, the 61-question check reaches 80%, the Essay is complete and the completion proof is submitted.",
        "Your profile and required TESOL learning evidence are complete.",
    ),
    "G02": (
        "Learn the essential classroom and account-safety rules.",
        "Read the in-platform policy guide and complete its quiz.",
        "The policy guide is confirmed and the quiz requirements pass.",
        "You can apply the core platform policies in class.",
    ),
    "G03": (
        "Build practical responses for different learner needs.",
        "Complete the learning content configured by Jiahe.",
        "Meet every requirement in the published Student Types configuration.",
        "You can adapt your teaching to different learner types.",
    ),
    "G04": (
        "Complete lesson preparation and confirm that your teaching setup is ready before class.",
        "Confirm lesson preparation, check the camera, microphone and network, then take one teaching-environment photo.",
        "Lesson preparation is confirmed, camera, microphone and network pass, and the teaching-environment photo passes AI review.",
        "Your lesson preparation and pre-class setup are recorded as ready.",
    ),
    "G05": (
        "Understand TTP and its key business scenarios.",
        "Watch the in-platform TTP video and confirm every item in the learning checklist.",
        "The TTP video is watched in full and every published checklist item is confirmed.",
        "You understand the key TTP workflow and commitments.",
    ),
    "G06": (
        "Learn cross-cultural classroom guidance.",
        "Complete the configured videos and quiz.",
        "All configured videos and quiz requirements pass.",
        "You can apply the culture guidance appropriately.",
    ),
    "G07": (
        "Strengthen dependable attendance habits.",
        "Complete the configured training and quiz.",
        "All configured training and quiz requirements pass.",
        "You have a clear reliability routine.",
    ),
    "G08": (
        "Learn the core Cocos teaching flow.",
        "Complete the configured in-platform videos and quiz.",
        "All configured videos and quiz requirements pass.",
        "You can prepare for a Cocos class.",
    ),
    "G09": (
        "Learn the fundamentals of SET teaching.",
        "Watch the in-platform Mock video slot and complete the five-question Mock check.",
        "The Mock video is watched in full and the five-question check reaches 80%.",
        "You understand the SET teaching foundation.",
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
    assert len(mandatory_payloads) == 9
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
        "content_status": (
            "PENDING_JIAHE"
            if task_id == "G03"
            else "READY"
        ),
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
