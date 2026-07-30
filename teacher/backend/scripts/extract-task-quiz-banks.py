#!/usr/bin/env python3
"""Extract course quiz DOCX files into the backend quiz-bank import source."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from docx import Document


COURSES = {
    "profile-credentials": [
        ("course-407", "01_教师档案与资质/课程407_American TESOL/Quiz/课程407_American TESOL_题库含答案.docx", 61),
    ],
    "device-network": [
        ("course-526", "02_设备与网络检测/课程526_Lesson Readiness Troubleshooting/Quiz/课程526_Troubleshooting_题库含答案.docx", 5),
        ("course-596", "02_设备与网络检测/课程596_AC Update April 2026/Quiz/课程596_AC Update_题库含答案.docx", 15),
    ],
    "platform-policies": [
        ("course-499", "03_平台规则学习/课程499_Platform Policies/Quiz/课程499_Platform Policies_题库含答案.docx", 5),
    ],
    "lesson-preparation": [
        ("course-400", "04_首课准备/课程400_Teaching Readiness Demo 101/Quiz/课程400_Teaching Readiness_题库含答案.docx", 5),
        ("course-500", "04_首课准备/课程500_Lesson Structure 101/Quiz/课程500_Lesson Structure_题库含答案.docx", 5),
    ],
    "me-culture": [
        ("course-520", "06_ME Culture与PARSNIP/课程520_ME Culture/Quiz/课程520_ME Culture_题库含答案.docx", 5),
        ("course-398", "06_ME Culture与PARSNIP/课程398_Global PARSNIP/Quiz/课程398_Global PARSNIP_题库含答案.docx", 25),
    ],
    "free-trial-training": [
        ("course-324", "08_体验课培训_候选待确认/候选课程324_FT Program/Quiz/课程324_FT Program_题库含答案.docx", 22),
        ("course-510", "08_体验课培训_候选待确认/候选课程510_Trial Lesson Delivery/Quiz/课程510_Trial Lesson Delivery_题库含答案.docx", 25),
    ],
    "cocos-training": [
        ("course-630", "09_Cocos课程培训/课程630_Global Communicator ME Version/Quiz/课程630_Global Communicator_题库含答案.docx", 5),
    ],
}


def clean_text(value: str) -> str:
    value = re.sub(r"Page\s+\d+\s+of\s+\d+", "", value, flags=re.IGNORECASE)
    replacements = {
        "otherdisciplines": "other disciplines",
        "anobstacle": "an obstacle",
        "inthe classroom": "in the classroom",
        "Teaching Readiness Deamo": "Teaching Readiness Demo",
        "What is can discourage": "What can discourage",
    }
    for source, replacement in replacements.items():
        value = value.replace(source, replacement)
    value = re.sub(r"\s+([.,!?])", r"\1", value)
    return re.sub(r"\s{2,}", " ", value).strip()


def parse_quiz(path: Path, course_id: str) -> list[dict]:
    lines = [
        clean_text(paragraph.text.strip().replace("\u00a0", " "))
        for paragraph in Document(path).paragraphs
        if paragraph.text.strip()
    ]
    questions: list[dict] = []
    current: dict | None = None
    current_type = "single"

    def finish_current() -> None:
        nonlocal current
        if current is None:
            return
        if not current.get("question") or not current.get("options") or "correct" not in current:
            raise ValueError(f"Incomplete question in {path}: {current}")
        questions.append(current)
        current = None

    for line in lines:
        if line == "【单项选择题】":
            current_type = "single"
            continue
        if line == "【多项选择题】":
            current_type = "multiple"
            continue
        if line == "【判断题】":
            current_type = "boolean"
            continue

        question_match = re.match(r"^(\d+)\.\s*(.*)$", line)
        if question_match:
            finish_current()
            current = {
                "id": f"{course_id}-q{len(questions) + 1}",
                "courseId": course_id,
                "type": current_type,
                "question": question_match.group(2).strip(),
                "options": [],
            }
            continue

        option_match = re.match(r"^([A-Z])\.\s*(.+)$", line)
        if option_match and current is not None:
            current["options"].append(clean_text(option_match.group(2)))
            continue

        if line.startswith("【分数】") and current is not None:
            current["points"] = float(line.split("】", 1)[1].strip())
            continue

        if line.startswith("【答案】") and current is not None:
            answer = line.split("】", 1)[1].strip()
            if current["type"] == "boolean":
                current["options"] = ["True", "False"]
                current["correct"] = 0 if answer == "正确" else 1
            elif current["type"] == "multiple":
                current["correct"] = [ord(letter) - ord("A") for letter in re.findall(r"[A-Z]", answer.upper())]
            else:
                current["correct"] = ord(answer[0].upper()) - ord("A")
            continue

        if line.startswith("【"):
            continue

        if current is not None and not current["options"]:
            current["question"] = " ".join(filter(None, [current["question"], line]))

    finish_current()
    return questions


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("resource_root", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    task_banks: dict[str, list[dict]] = {}
    sources: dict[str, list[str]] = {}
    for task_id, course_entries in COURSES.items():
        task_banks[task_id] = []
        sources[task_id] = []
        for course_id, relative_path, expected_count in course_entries:
            source_path = args.resource_root / relative_path
            questions = parse_quiz(source_path, course_id)
            if len(questions) != expected_count:
                raise ValueError(
                    f"{course_id}: expected {expected_count} questions, found {len(questions)}"
                )
            task_banks[task_id].extend(questions)
            sources[task_id].append(relative_path)

    payload = {
        "generatedFrom": "2026.07 外教新师训练营资源包",
        "sources": sources,
        "taskQuizBanks": task_banks,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
