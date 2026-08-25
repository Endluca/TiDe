"""Atomic COURSE SourceWide materialization for the DTS v2 Outbox worker."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, time
import json
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.engine import Connection

from .dts_v2_course_source_wide_plan import (
    CourseParticipationProjectionV2,
    CourseSourceWidePlanV2,
)
from .dts_v2_course_trigger_plan import (
    CourseTriggerPlanV2,
    build_course_trigger_plan_v2,
)
from .dts_v2_lesson_component_store import DtsV2LessonComponentStore


class DtsV2CourseMaterializerError(RuntimeError):
    """The compatibility or score projection cannot be written safely."""


class DtsV2ScoreProjectionRefresher(Protocol):
    def refresh_course_and_teachers(
        self,
        connection: Connection,
        *,
        source_region: str,
        source_appoint_id: str,
        teacher_ids: Sequence[str],
        projection_generation: int,
    ) -> Mapping[str, int]: ...


class DtsV2CourseTriggerMatchStore(Protocol):
    def reconcile_course_matches(
        self,
        connection: Connection,
        plan: CourseTriggerPlanV2,
        *,
        aggregate_revision: int,
        projection_generation: int,
        triggering_event_id: str,
    ) -> Mapping[str, int]: ...


class PostgresDtsV2CourseMaterializer:
    """Write compatibility row, ledger settlements and read models in one tx."""

    def __init__(
        self,
        *,
        score_refresher: DtsV2ScoreProjectionRefresher,
        trigger_match_store: DtsV2CourseTriggerMatchStore,
        component_store: DtsV2LessonComponentStore | None = None,
    ) -> None:
        if not callable(
            getattr(score_refresher, "refresh_course_and_teachers", None)
        ):
            raise DtsV2CourseMaterializerError(
                "DTS_V2_SCORE_REFRESHER_REQUIRED"
            )
        self.score_refresher = score_refresher
        if not callable(
            getattr(trigger_match_store, "reconcile_course_matches", None)
        ):
            raise DtsV2CourseMaterializerError(
                "DTS_V2_COURSE_TRIGGER_MATCH_STORE_REQUIRED"
            )
        self.trigger_matches = trigger_match_store
        self.components = component_store or DtsV2LessonComponentStore()

    def apply_course_plan(
        self,
        connection: Connection,
        plan: CourseSourceWidePlanV2,
        *,
        aggregate_revision: int,
        triggering_event_id: str,
    ) -> Mapping[str, int]:
        if not isinstance(plan, CourseSourceWidePlanV2):
            raise DtsV2CourseMaterializerError(
                "DTS_V2_COURSE_PLAN_REQUIRED"
            )
        if type(aggregate_revision) is not int or aggregate_revision < 1:
            raise DtsV2CourseMaterializerError(
                "DTS_V2_COURSE_REVISION_INVALID"
            )
        if not isinstance(triggering_event_id, str) or not triggering_event_id:
            raise DtsV2CourseMaterializerError(
                "DTS_V2_COURSE_EVENT_ID_INVALID"
            )

        projection_generation = _primary_projection_generation(connection)
        compatibility = _materialize_compatibility_row(connection, plan)
        trigger_counts = self.trigger_matches.reconcile_course_matches(
            connection,
            build_course_trigger_plan_v2(plan),
            aggregate_revision=aggregate_revision,
            projection_generation=projection_generation,
            triggering_event_id=triggering_event_id,
        )
        counts = dict(
            self.components.settle_course(
                connection,
                plan,
                aggregate_revision=aggregate_revision,
                projection_generation=projection_generation,
                triggering_event_id=triggering_event_id,
            )
        )
        refreshed = self.score_refresher.refresh_course_and_teachers(
            connection,
            source_region=plan.source_region,
            source_appoint_id=plan.source_appoint_id,
            teacher_ids=plan.affected_teacher_ids,
            projection_generation=projection_generation,
        )
        return _merge_counts(
            {"compatibility_changes": compatibility},
            trigger_counts,
            counts,
            refreshed,
        )


def _primary_projection_generation(connection: Connection) -> int:
    rows = list(
        connection.execute(
            text(
                """
                SELECT mode,projection_generation
                FROM public.dts_pipeline_control
                WHERE control_id='PRIMARY'
                """
            )
        ).mappings()
    )
    if len(rows) != 1 or rows[0].get("mode") != "V2_PRIMARY":
        raise DtsV2CourseMaterializerError(
            "DTS_V2_COURSE_PRIMARY_MODE_REQUIRED"
        )
    value = rows[0].get("projection_generation")
    if type(value) is not int or value < 1:
        raise DtsV2CourseMaterializerError(
            "DTS_V2_COURSE_PROJECTION_GENERATION_INVALID"
        )
    return value


def _materialize_compatibility_row(
    connection: Connection,
    plan: CourseSourceWidePlanV2,
) -> int:
    selected = _compatibility_participation(plan)
    # A pre-completion tombstone has no current compatibility fact.  A course
    # deleted after frozen completion stays visible while its correction Case
    # is pending; an approved VOID clears the completion pointer and reaches
    # this branch.
    if selected is None and plan.source_deleted:
        return int(
            connection.execute(
                text(
                    """
                    DELETE FROM public.lesson_source_wide
                    WHERE source_region=:source_region AND "课程id"=:course_id
                    """
                ),
                {
                    "source_region": plan.source_region,
                    "course_id": plan.source_appoint_id,
                },
            ).rowcount
        )

    teacher_id = None if selected is None else selected.teacher_id
    student_token = (
        plan.student_token
        if selected is None or not selected.valid_for_scoring
        else selected.student_token
    )
    relationship = _relationship_current(
        connection,
        source_region=plan.source_region,
        teacher_id=teacher_id,
        student_token=student_token,
    )
    lesson_date = _date(
        plan.lesson_local_date
        if selected is None or not selected.valid_for_scoring
        else selected.lesson_local_date
    )
    lesson_time = _time(
        plan.lesson_local_time
        if selected is None or not selected.valid_for_scoring
        else selected.lesson_local_time
    )
    labels = () if selected is None else selected.labels
    feedback_detail = _feedback_detail(labels)
    grading = (
        "SOURCE_MISSING"
        if selected is None
        else selected.grading_classification
    )
    values = {
        "source_region": plan.source_region,
        "course_id": plan.source_appoint_id,
        "lesson_date": lesson_date,
        "lesson_time": lesson_time,
        "is_peak": plan.is_peak if selected is None else selected.is_peak,
        "teacher_id": teacher_id,
        "student_id": student_token,
        "lesson_status": (
            plan.source_status
            if selected is None
            else selected.participation_status
        ),
        "absence_reason": (
            None if selected is None else selected.absence_reason_detail
        ),
        "is_late": None if selected is None else selected.is_late,
        "is_early": None if selected is None else selected.is_early,
        "negative_score": (
            None if selected is None else selected.negative_score
        ),
        "negative_feedback": (
            True if grading == "NEGATIVE" else False
            if grading in {"POSITIVE", "UNCLASSIFIED"} else None
        ),
        "positive_feedback": (
            True if grading == "POSITIVE" else False
            if grading in {"NEGATIVE", "UNCLASSIFIED"} else None
        ),
        "complaint_l1": (
            None if selected is None else selected.complaint_category_l1
        ),
        "complaint_l2": (
            None if selected is None else selected.complaint_category_l2
        ),
        "complaint_l3": (
            None if selected is None else selected.complaint_category_l3
        ),
        "is_blocked": relationship.get("is_blocked"),
        "is_favorited": relationship.get("is_favorited"),
        "feedback_detail": feedback_detail,
        "is_camera_off": None if selected is None else selected.is_camera_off,
    }
    changed = connection.execute(
        text(
            """
            INSERT INTO public.lesson_source_wide (
                source_region,"课程id","上课日期","上课时间","是否高峰",
                "老师id","学员id","课程状态","缺席原因明细","迟到","早退",
                "差评分","差评标签","投诉一级分类","投诉二级分类",
                "投诉三级分类","是否拉黑","收藏","好评标签","评价详情",
                "未开摄像头","cpu占用过高","网络延迟过高"
            ) VALUES (
                :source_region,:course_id,:lesson_date,:lesson_time,:is_peak,
                :teacher_id,:student_id,:lesson_status,:absence_reason,:is_late,
                :is_early,:negative_score,:negative_feedback,:complaint_l1,
                :complaint_l2,:complaint_l3,:is_blocked,:is_favorited,
                :positive_feedback,:feedback_detail,:is_camera_off,NULL,NULL
            )
            ON CONFLICT (source_region,"课程id") DO UPDATE SET
                "上课日期"=EXCLUDED."上课日期",
                "上课时间"=EXCLUDED."上课时间",
                "是否高峰"=EXCLUDED."是否高峰",
                "老师id"=EXCLUDED."老师id",
                "学员id"=EXCLUDED."学员id",
                "课程状态"=EXCLUDED."课程状态",
                "缺席原因明细"=EXCLUDED."缺席原因明细",
                "迟到"=EXCLUDED."迟到",
                "早退"=EXCLUDED."早退",
                "差评分"=EXCLUDED."差评分",
                "差评标签"=EXCLUDED."差评标签",
                "投诉一级分类"=EXCLUDED."投诉一级分类",
                "投诉二级分类"=EXCLUDED."投诉二级分类",
                "投诉三级分类"=EXCLUDED."投诉三级分类",
                "是否拉黑"=EXCLUDED."是否拉黑",
                "收藏"=EXCLUDED."收藏",
                "好评标签"=EXCLUDED."好评标签",
                "评价详情"=EXCLUDED."评价详情",
                "未开摄像头"=EXCLUDED."未开摄像头",
                "cpu占用过高"=NULL,"网络延迟过高"=NULL
            WHERE ROW(
                lesson_source_wide."上课日期",
                lesson_source_wide."上课时间",
                lesson_source_wide."是否高峰",
                lesson_source_wide."老师id",lesson_source_wide."学员id",
                lesson_source_wide."课程状态",
                lesson_source_wide."缺席原因明细",lesson_source_wide."迟到",
                lesson_source_wide."早退",lesson_source_wide."差评分",
                lesson_source_wide."差评标签",
                lesson_source_wide."投诉一级分类",
                lesson_source_wide."投诉二级分类",
                lesson_source_wide."投诉三级分类",
                lesson_source_wide."是否拉黑",lesson_source_wide."收藏",
                lesson_source_wide."好评标签",lesson_source_wide."评价详情",
                lesson_source_wide."未开摄像头",
                lesson_source_wide."cpu占用过高",
                lesson_source_wide."网络延迟过高"
            ) IS DISTINCT FROM ROW(
                EXCLUDED."上课日期",EXCLUDED."上课时间",
                EXCLUDED."是否高峰",EXCLUDED."老师id",EXCLUDED."学员id",
                EXCLUDED."课程状态",EXCLUDED."缺席原因明细",
                EXCLUDED."迟到",EXCLUDED."早退",EXCLUDED."差评分",
                EXCLUDED."差评标签",EXCLUDED."投诉一级分类",
                EXCLUDED."投诉二级分类",EXCLUDED."投诉三级分类",
                EXCLUDED."是否拉黑",EXCLUDED."收藏",EXCLUDED."好评标签",
                EXCLUDED."评价详情",EXCLUDED."未开摄像头",NULL,NULL
            )
            RETURNING "课程id"
            """
        ),
        values,
    ).scalar_one_or_none()
    return int(changed is not None)


def _compatibility_participation(
    plan: CourseSourceWidePlanV2,
) -> CourseParticipationProjectionV2 | None:
    if plan.completion_participation_seq is not None:
        rows = [
            row
            for row in plan.participation_rows
            if row.participation_seq == plan.completion_participation_seq
            and row.teacher_id == plan.completion_teacher_id
        ]
    elif plan.current_participation_seq is not None:
        rows = [
            row
            for row in plan.participation_rows
            if row.participation_seq == plan.current_participation_seq
            and row.teacher_id == plan.current_teacher_id
            and row.is_current
        ]
    else:
        rows = []
    if len(rows) > 1:
        raise DtsV2CourseMaterializerError(
            "DTS_V2_COMPAT_PARTICIPATION_CONFLICT"
        )
    if not rows and (
        plan.completion_participation_seq is not None
        or plan.current_participation_seq is not None
    ):
        raise DtsV2CourseMaterializerError(
            "DTS_V2_COMPAT_PARTICIPATION_MISSING"
        )
    return rows[0] if rows else None


def _relationship_current(
    connection: Connection,
    *,
    source_region: str,
    teacher_id: str | None,
    student_token: str | None,
) -> Mapping[str, Any]:
    if teacher_id is None or student_token is None:
        return {}
    row = connection.execute(
        text(
            """
            SELECT is_favorited,is_blocked
            FROM public.teacher_student_relationship_current
            WHERE source_region=:source_region AND teacher_id=:teacher_id
              AND student_token=:student_token
            FOR SHARE
            """
        ),
        {
            "source_region": source_region,
            "teacher_id": teacher_id,
            "student_token": student_token,
        },
    ).mappings().one_or_none()
    return {} if row is None else row


def _feedback_detail(labels: Sequence[Mapping[str, Any]]) -> str | None:
    values = [
        {
            "label_id": str(row["label_id"]),
            "label_name": row.get("label_name_snapshot"),
        }
        for row in labels
        if row.get("label_id") is not None
    ]
    return (
        None
        if not values
        else json.dumps(
            values,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def _date(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise DtsV2CourseMaterializerError(
            "DTS_V2_COMPAT_LESSON_DATE_INVALID"
        ) from exc


def _time(value: str | None) -> time | None:
    if value is None:
        return None
    try:
        parsed = time.fromisoformat(value)
    except ValueError as exc:
        raise DtsV2CourseMaterializerError(
            "DTS_V2_COMPAT_LESSON_TIME_INVALID"
        ) from exc
    return parsed.replace(tzinfo=None)


def _merge_counts(*values: Mapping[str, int]) -> dict[str, int]:
    result: dict[str, int] = {}
    for mapping in values:
        if not isinstance(mapping, Mapping):
            raise DtsV2CourseMaterializerError(
                "DTS_V2_COURSE_MATERIALIZER_RESULT_INVALID"
            )
        for name, count in mapping.items():
            if (
                not isinstance(name, str)
                or not name
                or type(count) is not int
                or count < 0
            ):
                raise DtsV2CourseMaterializerError(
                    "DTS_V2_COURSE_MATERIALIZER_RESULT_INVALID"
                )
            result[name] = result.get(name, 0) + count
    return result


__all__ = [
    "DtsV2CourseMaterializerError",
    "DtsV2ScoreProjectionRefresher",
    "PostgresDtsV2CourseMaterializer",
]
