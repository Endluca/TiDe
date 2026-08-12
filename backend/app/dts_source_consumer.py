"""Aliyun DTS change-stream normalization and consumer primitives.

This module is intentionally upstream of ``source_wide_worker``.  It consumes
business-database changes and identifies the smallest source-wide dirty keys;
it does not calculate TiDe scores.  The selected sink controls persistence.
"""

from __future__ import annotations

import io
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol


DATA_OPERATIONS = frozenset({"INSERT", "UPDATE", "DELETE"})
CONTROL_OPERATIONS = frozenset(
    {
        "DDL",
        "BEGIN",
        "COMMIT",
        "ROLLBACK",
        "ABORT",
        "HEARTBEAT",
        "CHECKPOINT",
        "COMMAND",
        "FILL",
        "FINISH",
        "CONTROL",
        "RDB",
        "NOOP",
        "INIT",
    }
)
SUPPORTED_REGIONS = frozenset({"ovs", "dom"})
REGIONAL_TABLE_SUFFIXES = frozenset(
    {
        "appoint",
        "complaint",
        "grading_label",
        "grading_label_log",
        "qa_ac_classroom_record",
        "qa_task_close_camera_record",
        "qa_task_fake_early_leave_record",
        "teacher_blacklist",
        "teacher_favorite",
        "user_complaint",
        "user_teacher_grading",
    }
)
DOM_SHARED_TABLE_SUFFIXES = frozenset(
    {
        "complaint_cate",
        "teacher",
        "teacher_absent_reason",
        "teacher_certification",
        "teacher_class_schedule",
        "teacher_penalty",
    }
)
SUPPORTED_TABLE_SUFFIXES_BY_REGION = {
    "ovs": REGIONAL_TABLE_SUFFIXES,
    "dom": REGIONAL_TABLE_SUFFIXES | DOM_SHARED_TABLE_SUFFIXES,
}
# Only fields with a confirmed target-wide dependency may enter the mirror.
# In particular, credentials, phone numbers, certificate URLs and free-form
# teacher profile payloads are deliberately absent.
SOURCE_FIELD_WHITELIST: dict[str, frozenset[str]] = {
    "appoint": frozenset(
        {
            "id", "t_id", "s_id", "date", "time", "start_time", "end_time",
            "week", "status", "use_point", "cancel_reason", "dt",
        }
    ),
    "complaint": frozenset(
        {
            "id", "stu_id", "user_id", "appoint_id", "tea_id", "teacher_id",
            "complaint_type", "complaint_type_child", "complaint_type_grandson",
            "approve", "validity", "course_date", "add_time", "tag_id",
        }
    ),
    "complaint_cate": frozenset(
        {
            "id", "cate_parent", "cate_level", "cate_cn_name", "cate_en_name",
            "status",
        }
    ),
    "grading_label": frozenset(
        {"id", "label_name", "label_name_en", "type", "version", "status"}
    ),
    "grading_label_log": frozenset(
        {
            "id", "appoint_id", "label_id", "label_name", "type", "status",
            "create_time", "dt",
        }
    ),
    "qa_ac_classroom_record": frozenset(
        {"id", "tea_id", "new_teacher", "type", "info", "update_time"}
    ),
    "qa_task_close_camera_record": frozenset(
        {"id", "appoint_id", "start_time", "end_time"}
    ),
    "qa_task_fake_early_leave_record": frozenset(
        {"id", "appoint_id", "start_time", "end_time"}
    ),
    "teacher": frozenset(
        {
            "id", "real_name", "center_type", "is_full_time", "status",
            "status_on_time", "status_off_time", "last_on_time", "course",
        }
    ),
    "teacher_absent_reason": frozenset(
        {"id", "appoint_id", "t_id", "reason_type", "reason_desc", "add_time"}
    ),
    "teacher_blacklist": frozenset(
        {
            "id", "teacher_id", "student_id", "valid_start_time",
            "valid_end_time", "is_valid_forever", "add_time", "update_time",
        }
    ),
    "teacher_certification": frozenset(
        {
            "id", "teacher_id", "certification_type", "certification_status",
            "status",
        }
    ),
    "teacher_class_schedule": frozenset(
        {
            "id", "teacher_id", "date", "date_slot", "time", "status",
            "project_code", "time_slot",
        }
    ),
    "teacher_favorite": frozenset(
        {"id", "tea_id", "stu_id", "add_time"}
    ),
    "teacher_penalty": frozenset(
        {
            "id", "appoint_id", "t_id", "lesson_start_time", "in_time",
            "out_time", "appeal_status",
        }
    ),
    "user_complaint": frozenset(
        {
            "id", "user_id", "appoint_id", "teacher_id", "complaint_type",
            "complaint_type_child", "complaint_type_grandson", "status", "add_time",
        }
    ),
    "user_teacher_grading": frozenset(
        {
            "id", "teacher_id", "appoint_id", "score", "type", "status",
            "is_del", "update_time", "create_time", "start_time", "dt",
        }
    ),
}
INFRASTRUCTURE_TABLES = frozenset({"dts_postgres_heartbeat"})
SCHEMA_BUNDLE_PATH = Path(__file__).with_name("dts_record_schemas.json")


class DtsConfigurationError(RuntimeError):
    """Raised before connecting when the runtime contract is incomplete."""


class DtsRecordError(RuntimeError):
    """Raised when a DTS record cannot be normalized without guessing."""


@dataclass(frozen=True)
class DtsConsumerSettings:
    """One DTS subscription connection without a target-database credential."""

    source_region: str
    broker_urls: tuple[str, ...]
    topic: str
    group_id: str
    account: str
    password: str = field(repr=False)
    start_timestamp_seconds: int | None = None
    partition: int = 0

    @property
    def sasl_username(self) -> str:
        suffix = f"-{self.group_id}"
        return self.account if self.account.endswith(suffix) else f"{self.account}{suffix}"

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> DtsConsumerSettings:
        values = os.environ if environ is None else environ

        def required(name: str) -> str:
            value = values.get(name, "").strip()
            if not value:
                raise DtsConfigurationError(f"{name}_REQUIRED")
            return value

        def required_secret(name: str) -> str:
            value = values.get(name, "")
            if not value:
                raise DtsConfigurationError(f"{name}_REQUIRED")
            return value

        source_region = required("TIT_DTS_SOURCE_REGION").lower()
        if source_region not in SUPPORTED_REGIONS:
            raise DtsConfigurationError("TIT_DTS_SOURCE_REGION_UNSUPPORTED")
        brokers = tuple(
            item.strip()
            for item in required("TIT_DTS_BROKER_URL").split(",")
            if item.strip()
        )
        if not brokers:
            raise DtsConfigurationError("TIT_DTS_BROKER_URL_REQUIRED")
        start_at = values.get("TIT_DTS_START_AT", "").strip()
        return cls(
            source_region=source_region,
            broker_urls=brokers,
            topic=required("TIT_DTS_TOPIC"),
            group_id=required("TIT_DTS_GROUP_ID"),
            account=required("TIT_DTS_ACCOUNT"),
            password=required_secret("TIT_DTS_PASSWORD"),
            start_timestamp_seconds=_parse_start_timestamp_seconds(start_at),
        )

    def safe_summary(self) -> dict[str, Any]:
        """Return connection metadata that cannot expose the password/account."""

        return {
            "source_region": self.source_region,
            "broker_count": len(self.broker_urls),
            "topic": self.topic,
            "group_id": self.group_id,
            "partition": self.partition,
            "start_timestamp_seconds": self.start_timestamp_seconds,
        }


def _parse_start_timestamp_seconds(raw: str) -> int | None:
    if not raw:
        return None
    normalized = raw.replace(" ", "T", 1)
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise DtsConfigurationError("TIT_DTS_START_AT_INVALID") from exc
    if parsed.tzinfo is None:
        raise DtsConfigurationError("TIT_DTS_START_AT_REQUIRES_TIMEZONE")
    # Alibaba Cloud DTS deliberately differs from Kafka's usual millisecond
    # contract: its broker interprets this timestamp lookup value as epoch
    # seconds, matching Aliyun's Java SDK and ten-digit initCheckpoint examples.
    return int(parsed.timestamp())


@lru_cache(maxsize=1)
def _parsed_avro_schema() -> dict[str, Any]:
    try:
        from fastavro import parse_schema
    except ImportError as exc:  # pragma: no cover - runtime dependency guard
        raise DtsConfigurationError("FASTAVRO_DEPENDENCY_REQUIRED") from exc

    schemas = json.loads(SCHEMA_BUNDLE_PATH.read_text(encoding="utf-8"))
    named_schemas: dict[str, Any] = {}
    for child_schema in schemas[:-1]:
        parse_schema(child_schema, named_schemas=named_schemas)
    return parse_schema(schemas[-1], named_schemas=named_schemas)


def decode_dts_avro(payload: bytes) -> dict[str, Any]:
    """Decode one Kafka value using Alibaba Cloud's published Avro schema."""

    try:
        from fastavro import schemaless_reader
    except ImportError as exc:  # pragma: no cover - runtime dependency guard
        raise DtsConfigurationError("FASTAVRO_DEPENDENCY_REQUIRED") from exc
    try:
        return schemaless_reader(io.BytesIO(payload), _parsed_avro_schema())
    except Exception as exc:
        raise DtsRecordError("DTS_AVRO_DECODE_FAILED") from exc


@dataclass(frozen=True)
class DtsChangeEvent:
    source_region: str
    topic: str
    partition: int
    offset: int
    record_id: int
    source_timestamp: int
    source_txid: str
    source_position: str
    operation: str
    database_name: str | None
    schema_name: str | None
    table_name: str | None
    before: Mapping[str, Any] | None
    after: Mapping[str, Any] | None

    @property
    def idempotency_key(self) -> tuple[str, str, int, int]:
        return (self.source_region, self.topic, self.partition, self.offset)


def build_change_event(
    record: Mapping[str, Any],
    *,
    source_region: str,
    topic: str,
    partition: int,
    offset: int,
) -> DtsChangeEvent:
    operation = str(record.get("operation") or "").upper()
    if operation not in DATA_OPERATIONS | CONTROL_OPERATIONS:
        raise DtsRecordError("DTS_OPERATION_UNSUPPORTED")
    tags = _string_mapping(record.get("tags"))
    database_name, schema_name, table_name = _extract_object_identity(
        record.get("objectName"),
        tags,
    )
    fields = _normalize_fields(record.get("fields"))
    before = _images_to_row(fields, record.get("beforeImages"), operation)
    after = _images_to_row(fields, record.get("afterImages"), operation)
    if operation in DATA_OPERATIONS and not table_name:
        raise DtsRecordError("DTS_DML_TABLE_NAME_MISSING")
    return DtsChangeEvent(
        source_region=source_region,
        topic=topic,
        partition=partition,
        offset=offset,
        record_id=_required_int(record.get("id"), "DTS_RECORD_ID_INVALID"),
        source_timestamp=_required_int(
            record.get("sourceTimestamp"),
            "DTS_SOURCE_TIMESTAMP_INVALID",
        ),
        source_txid=str(record.get("sourceTxid") or ""),
        source_position=str(record.get("safeSourcePosition") or record.get("sourcePosition") or ""),
        operation=operation,
        database_name=database_name,
        schema_name=schema_name,
        table_name=table_name,
        before=before,
        after=after,
    )


def _required_int(value: Any, error: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise DtsRecordError(error) from exc


def _string_mapping(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): str(item) for key, item in value.items()}


def _extract_object_identity(
    object_name: Any,
    tags: Mapping[str, str],
) -> tuple[str | None, str | None, str | None]:
    lowered = {key.lower(): value for key, value in tags.items()}

    def first(*keys: str) -> str | None:
        for key in keys:
            value = lowered.get(key.lower(), "").strip()
            if value:
                return _strip_identifier(value)
        return None

    database_name = first("databaseName", "database", "dbName", "db")
    schema_name = first("schemaName", "schema")
    table_name = first("tableName", "table")
    raw_object = str(object_name or "").strip()
    if raw_object:
        parts = [
            _strip_identifier(part)
            for part in raw_object.replace("/", ".").split(".")
            if _strip_identifier(part)
        ]
        if not table_name and parts:
            table_name = parts[-1]
        if not schema_name and len(parts) >= 2:
            schema_name = parts[-2]
        if not database_name and len(parts) >= 3:
            database_name = parts[-3]
    return database_name, schema_name, table_name


def _strip_identifier(value: str) -> str:
    return value.strip().strip('`"[]')


def _json_value(value: str) -> Any:
    stripped = value.strip()
    if not stripped or stripped[0] not in "[{":
        return value
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return value


def _normalize_fields(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        value = _json_value(value)
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, (bytes, bytearray, str)):
        raise DtsRecordError("DTS_FIELDS_INVALID")
    names: list[str] = []
    for field_value in value:
        if isinstance(field_value, Mapping):
            name = str(field_value.get("name") or "").strip()
        else:
            name = str(field_value or "").strip()
        if not name:
            raise DtsRecordError("DTS_FIELD_NAME_MISSING")
        names.append(name)
    return tuple(names)


def _images_to_row(
    fields: tuple[str, ...],
    images_value: Any,
    operation: str,
) -> dict[str, Any] | None:
    if isinstance(images_value, str):
        images_value = _json_value(images_value)
    if images_value is None or isinstance(images_value, str):
        return None
    if not isinstance(images_value, Sequence) or isinstance(
        images_value,
        (bytes, bytearray),
    ):
        raise DtsRecordError("DTS_IMAGES_INVALID")
    if len(fields) != len(images_value):
        if operation in DATA_OPERATIONS:
            raise DtsRecordError("DTS_IMAGE_FIELD_COUNT_MISMATCH")
        return None
    return {
        field_name: _decode_image_value(image)
        for field_name, image in zip(fields, images_value)
    }


def _decode_image_value(value: Any) -> Any:
    if value is None or (isinstance(value, str) and value in {"NULL", "NONE"}):
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if not isinstance(value, Mapping):
        return value
    if "timestamp" in value:
        seconds = int(value["timestamp"])
        millis = int(value.get("millis") or 0)
        return datetime.fromtimestamp(
            seconds + millis / 1000,
            tz=timezone.utc,
        ).isoformat()
    if "year" in value:
        return _format_avro_datetime(value)
    if "value" in value:
        nested = value["value"]
        if isinstance(nested, Mapping) and "year" in nested:
            rendered = _format_avro_datetime(nested)
            timezone_name = str(value.get("timezone") or "").strip()
            return f"{rendered} {timezone_name}".strip()
        if isinstance(nested, bytes):
            return nested.decode("utf-8", errors="replace")
        return nested
    return {str(key): _decode_image_value(item) for key, item in value.items()}


def _format_avro_datetime(value: Mapping[str, Any]) -> str:
    year = int(value.get("year") or 0)
    month = int(value.get("month") or 1)
    day = int(value.get("day") or 1)
    hour = int(value.get("hour") or 0)
    minute = int(value.get("minute") or 0)
    second = int(value.get("second") or 0)
    millis = int(value.get("millis") or 0)
    rendered = f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}"
    return f"{rendered}.{millis:03d}" if millis else rendered


@dataclass(frozen=True)
class DirtyKeySet:
    course_ids: frozenset[str] = frozenset()
    teacher_ids: frozenset[str] = frozenset()
    teacher_student_pairs: frozenset[tuple[str, str]] = frozenset()
    label_ids: frozenset[str] = frozenset()
    complaint_category_ids: frozenset[str] = frozenset()
    issues: tuple[str, ...] = ()
    ignored_reason: str | None = None


def route_dirty_keys(event: DtsChangeEvent) -> DirtyKeySet:
    if event.operation not in DATA_OPERATIONS:
        return DirtyKeySet(ignored_reason="CONTROL_RECORD")
    table = event.table_name or ""
    if table in INFRASTRUCTURE_TABLES:
        return DirtyKeySet(ignored_reason="INFRASTRUCTURE_TABLE")
    expected_prefix = f"{event.source_region}_"
    if not table.startswith(expected_prefix):
        return DirtyKeySet(ignored_reason="TABLE_OUTSIDE_REGION_PROFILE")
    suffix = table.removeprefix(expected_prefix)
    if suffix not in SUPPORTED_TABLE_SUFFIXES_BY_REGION[event.source_region]:
        return DirtyKeySet(ignored_reason="TABLE_NOT_IN_METRIC_WHITELIST")

    rows = tuple(row for row in (event.before, event.after) if row is not None)
    course_ids: set[str] = set()
    teacher_ids: set[str] = set()
    pairs: set[tuple[str, str]] = set()
    label_ids: set[str] = set()
    complaint_category_ids: set[str] = set()
    issues: list[str] = []

    for row in rows:
        course_id = _row_id(row, "appoint_id", "apt_id")
        if suffix == "appoint":
            course_id = _row_id(row, "id")
        if course_id:
            course_ids.add(course_id)

        teacher_id = _row_id(row, "t_id", "teacher_id", "tea_id")
        if suffix == "teacher":
            teacher_id = _row_id(row, "id")
        student_id = _row_id(row, "s_id", "student_id", "stu_id", "user_id")
        if teacher_id:
            teacher_ids.add(teacher_id)
        if teacher_id and student_id:
            pairs.add((teacher_id, student_id))

        if suffix in {"grading_label", "grading_label_log"}:
            label_id = _row_id(row, "label_id", "id")
            if label_id:
                label_ids.add(label_id)

        if suffix == "complaint_cate":
            category_id = _row_id(row, "id")
            if category_id:
                complaint_category_ids.add(category_id)

        if suffix == "qa_ac_classroom_record":
            qa_ids, qa_issue = _qa_appoint_ids(row.get("info"))
            course_ids.update(qa_ids)
            if qa_issue:
                issues.append(qa_issue)

    return DirtyKeySet(
        course_ids=frozenset(course_ids),
        teacher_ids=frozenset(teacher_ids),
        teacher_student_pairs=frozenset(pairs),
        label_ids=frozenset(label_ids),
        complaint_category_ids=frozenset(complaint_category_ids),
        issues=tuple(dict.fromkeys(issues)),
    )


def _row_id(row: Mapping[str, Any], *names: str) -> str | None:
    for name in names:
        value = row.get(name)
        if value is None:
            continue
        rendered = str(value).strip()
        if rendered:
            return rendered
    return None


def source_table_suffix(event: DtsChangeEvent) -> str | None:
    """Return the confirmed profile suffix for one source table."""

    table = event.table_name or ""
    prefix = f"{event.source_region}_"
    if not table.startswith(prefix):
        return None
    suffix = table.removeprefix(prefix)
    if suffix not in SUPPORTED_TABLE_SUFFIXES_BY_REGION[event.source_region]:
        return None
    return suffix


def _qa_appoint_ids(info_value: Any) -> tuple[set[str], str | None]:
    if isinstance(info_value, str):
        try:
            info_value = json.loads(info_value)
        except json.JSONDecodeError:
            return set(), "QA_INFO_INVALID_JSON"
    if info_value is None:
        return set(), None
    if not isinstance(info_value, Mapping):
        return set(), "QA_INFO_NOT_OBJECT"
    result: set[str] = set()
    for key in ("cpu", "network_delay"):
        records = info_value.get(key)
        if records is None:
            continue
        if not isinstance(records, Sequence) or isinstance(records, (str, bytes, bytearray)):
            return result, f"QA_INFO_{key.upper()}_NOT_ARRAY"
        for record in records:
            if not isinstance(record, Mapping):
                continue
            appoint_id = _row_id(record, "appoint_id")
            if appoint_id:
                result.add(appoint_id)
    return result, None


def teacher_matches_region(region: str, course_value: Any) -> bool | None:
    if region not in SUPPORTED_REGIONS:
        raise ValueError("unsupported region")
    if course_value is None:
        return None
    rendered = str(course_value)
    if region == "ovs":
        return "global_cn" in rendered or "global_pool" in rendered
    tokens = {item.strip() for item in rendered.split(",") if item.strip()}
    return "global_cn" not in tokens and "global_pool" not in tokens


def is_peak_lesson(region: str, week_value: Any, time_value: Any) -> bool | None:
    if region not in SUPPORTED_REGIONS:
        raise ValueError("unsupported region")
    week = _optional_int(week_value)
    lesson_time = _parse_time(time_value)
    if week is None or lesson_time is None:
        return None
    weekday = week in {1, 2, 3, 4, 5}
    weekend = week in {0, 6, 7}
    if not weekday and not weekend:
        return False
    morning = time(9, 0) <= lesson_time <= time(11, 30)
    if region == "dom":
        evening = time(18, 0) <= lesson_time <= time(21, 30)
        return evening or (weekend and morning)
    overnight = (
        time(18, 0) <= lesson_time <= time(23, 30)
        or time(0, 0) <= lesson_time <= time(5, 30)
    )
    return overnight or (weekend and morning)


def _optional_int(value: Any) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_time(value: Any) -> time | None:
    if value is None:
        return None
    if isinstance(value, time):
        return value.replace(tzinfo=None)
    rendered = str(value).strip()
    if not rendered:
        return None
    if " " in rendered or "T" in rendered:
        parsed = _parse_datetime(rendered)
        return parsed.time() if parsed else None
    try:
        return time.fromisoformat(rendered).replace(tzinfo=None)
    except ValueError:
        return None


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    rendered = str(value).strip()
    if not rendered:
        return None
    try:
        return date.fromisoformat(rendered[:10])
    except ValueError:
        return None


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)):
        parsed = datetime.fromtimestamp(float(value), tz=timezone.utc)
    else:
        rendered = str(value).strip().replace("Z", "+00:00")
        if not rendered:
            return None
        try:
            parsed = datetime.fromisoformat(rendered.replace(" ", "T", 1))
        except ValueError:
            return None
    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


@dataclass(frozen=True)
class AppointProjectionCandidate:
    action: str
    course_id: str | None
    target_values: Mapping[str, Any]
    required_sources: tuple[str, ...]
    reason: str | None = None


def project_appoint_candidate(event: DtsChangeEvent) -> AppointProjectionCandidate | None:
    expected_table = f"{event.source_region}_appoint"
    if event.table_name != expected_table or event.operation not in DATA_OPERATIONS:
        return None
    row = event.after if event.operation != "DELETE" else event.before
    if row is None:
        raise DtsRecordError("DTS_APPOINT_IMAGE_MISSING")
    course_id = _row_id(row, "id")
    if event.operation == "DELETE":
        return AppointProjectionCandidate(
            action="DELETE",
            course_id=course_id,
            target_values={},
            required_sources=(),
        )

    required_fields = ("id", "t_id", "status", "use_point")
    missing = tuple(name for name in required_fields if row.get(name) is None)
    if missing:
        return AppointProjectionCandidate(
            action="PENDING",
            course_id=course_id,
            target_values={},
            required_sources=(),
            reason="APPOINT_FULL_IMAGE_REQUIRED:" + ",".join(missing),
        )
    if (
        str(row.get("use_point")) != "buy"
        or str(row.get("status")) in {"cancel", "on"}
        or row.get("s_id") is None
    ):
        return AppointProjectionCandidate(
            action="DELETE",
            course_id=course_id,
            target_values={},
            required_sources=(),
            reason="APPOINT_OUTSIDE_SCRIPT_SCOPE",
        )

    start_value = row.get("start_time")
    lesson_date = _parse_date(row.get("date")) or _parse_date(start_value)
    lesson_time = _parse_time(row.get("time")) or _parse_time(start_value)
    return AppointProjectionCandidate(
        action="UPSERT_CANDIDATE",
        course_id=course_id,
        target_values={
            "课程id": course_id,
            "上课日期": lesson_date,
            "上课时间": lesson_time,
            "是否高峰": is_peak_lesson(
                event.source_region,
                row.get("week"),
                lesson_time,
            ),
            "老师id": _row_id(row, "t_id"),
            "学员id": _row_id(row, "s_id"),
            "课程状态": str(row.get("status")),
        },
        required_sources=("dom_teacher",),
        reason="TEACHER_SCOPE_AND_ONBOARD_WINDOW_NOT_YET_VERIFIED",
    )


def reduce_latest_complaints(
    user_rows: Sequence[Mapping[str, Any]],
    complaint_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Apply the confirmed OBS complaint latest-row and validity rules."""

    latest_user = _latest_rows(user_rows, student_names=("user_id",))
    latest_complaint = _latest_rows(
        complaint_rows,
        student_names=("stu_id", "user_id"),
    )
    result: list[dict[str, Any]] = []
    for key in sorted(set(latest_user) | set(latest_complaint)):
        user = latest_user.get(key, {})
        complaint = latest_complaint.get(key, {})
        merged = {
            "user_id": key[0],
            "appoint_id": key[1],
            "teacher_id": _coalesce(
                user.get("teacher_id"),
                complaint.get("tea_id"),
                complaint.get("teacher_id"),
            ),
            "complaint_type": _coalesce(
                user.get("complaint_type"),
                complaint.get("complaint_type"),
            ),
            "complaint_type_child": _coalesce(
                user.get("complaint_type_child"),
                complaint.get("complaint_type_child"),
            ),
            "complaint_type_grandson": _coalesce(
                user.get("complaint_type_grandson"),
                complaint.get("complaint_type_grandson"),
            ),
            "approve": complaint.get("approve"),
            "validity": complaint.get("validity"),
            "course_date": _coalesce(
                complaint.get("course_date"),
                user.get("add_time"),
            ),
        }
        if _is_valid_complaint(merged):
            result.append(merged)
    return result


def _latest_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    student_names: tuple[str, ...],
) -> dict[tuple[str, str], Mapping[str, Any]]:
    latest: dict[tuple[str, str], Mapping[str, Any]] = {}
    for row in rows:
        student_id = _row_id(row, *student_names)
        appoint_id = _row_id(row, "appoint_id")
        if not student_id or not appoint_id:
            continue
        key = (student_id, appoint_id)
        current = latest.get(key)
        if current is None or _sortable_id(row.get("id")) > _sortable_id(current.get("id")):
            latest[key] = row
    return latest


def _sortable_id(value: Any) -> tuple[int, int | str]:
    try:
        return (1, int(value))
    except (TypeError, ValueError):
        return (0, str(value or ""))


def _coalesce(*values: Any) -> Any:
    return next((value for value in values if value is not None), None)


def _is_valid_complaint(row: Mapping[str, Any]) -> bool:
    complaint_type = _optional_int(row.get("complaint_type"))
    grandson = _optional_int(row.get("complaint_type_grandson"))
    validity = _optional_int(row.get("validity"))
    return (
        complaint_type == 13
        and grandson != 82
        and str(row.get("approve") or "").lower() == "y"
        and validity == 1
    )


def derive_penalty_flags(
    penalty_rows: Sequence[Mapping[str, Any]],
    *,
    lesson_start: Any,
    lesson_end: Any,
) -> tuple[bool | None, bool | None]:
    """Apply the confirmed OBS >30-second penalty-time rules."""

    start = _parse_datetime(lesson_start)
    end = _parse_datetime(lesson_end)
    if start is None or end is None:
        return None, None
    valid_rows = []
    for row in penalty_rows:
        appeal_status = _optional_int(row.get("appeal_status"))
        if appeal_status is not None and appeal_status != 2:
            valid_rows.append(row)
    in_times = [
        parsed
        for row in valid_rows
        if (parsed := _parse_datetime(row.get("in_time"))) is not None
    ]
    out_times = [
        parsed
        for row in valid_rows
        if (parsed := _parse_datetime(row.get("out_time"))) is not None
    ]
    latest_in = max(in_times, default=None)
    latest_out = max(out_times, default=None)
    is_late = latest_in is not None and (latest_in - start).total_seconds() > 30
    sentinel = datetime(1970, 1, 1, 8, 0, 0)
    is_early = (
        latest_out is not None
        and latest_out > sentinel
        and latest_out < end
        and (end - latest_out).total_seconds() > 30
    )
    return is_late, is_early


@dataclass(frozen=True)
class ProcessResult:
    status: str
    dirty_keys: DirtyKeySet
    appoint_candidate: AppointProjectionCandidate | None


class DtsEventSink(Protocol):
    authoritative_checkpoint: bool

    def apply(
        self,
        event: DtsChangeEvent,
        dirty_keys: DirtyKeySet,
        appoint_candidate: AppointProjectionCandidate | None,
    ) -> bool:
        """Return True when this idempotency key was already processed."""

    def resume_offset(
        self,
        *,
        source_region: str,
        topic: str,
        partition: int,
    ) -> int | None:
        """Return the durable next offset, if this sink owns replay state."""


class InMemoryShadowSink:
    """Non-durable sink for transport validation and unit tests only."""

    authoritative_checkpoint = False

    def __init__(self) -> None:
        self._seen: set[tuple[str, str, int, int]] = set()
        self.processed: list[tuple[DtsChangeEvent, DirtyKeySet, AppointProjectionCandidate | None]] = []

    def apply(
        self,
        event: DtsChangeEvent,
        dirty_keys: DirtyKeySet,
        appoint_candidate: AppointProjectionCandidate | None,
    ) -> bool:
        if event.idempotency_key in self._seen:
            return True
        self._seen.add(event.idempotency_key)
        self.processed.append((event, dirty_keys, appoint_candidate))
        return False

    def resume_offset(
        self,
        *,
        source_region: str,
        topic: str,
        partition: int,
    ) -> int | None:
        del source_region, topic, partition
        return None


class DtsEventProcessor:
    def __init__(self, sink: DtsEventSink) -> None:
        self._sink = sink

    def process(self, event: DtsChangeEvent) -> ProcessResult:
        dirty_keys = route_dirty_keys(event)
        candidate = project_appoint_candidate(event)
        duplicate = self._sink.apply(event, dirty_keys, candidate)
        if duplicate:
            status = "DUPLICATE"
        elif dirty_keys.ignored_reason:
            status = "IGNORED"
        else:
            status = "PROCESSED"
        return ProcessResult(
            status=status,
            dirty_keys=dirty_keys,
            appoint_candidate=candidate,
        )

    @property
    def authoritative_checkpoint(self) -> bool:
        return bool(getattr(self._sink, "authoritative_checkpoint", False))

    def resume_offset(
        self,
        *,
        source_region: str,
        topic: str,
        partition: int,
    ) -> int | None:
        return self._sink.resume_offset(
            source_region=source_region,
            topic=topic,
            partition=partition,
        )


class DtsKafkaConsumer:
    """Single-partition DTS consumer with explicit post-sink offset commits."""

    def __init__(
        self,
        settings: DtsConsumerSettings,
        processor: DtsEventProcessor,
        *,
        idle_timeout_ms: int = 10_000,
    ) -> None:
        self.settings = settings
        self.processor = processor
        self.idle_timeout_ms = idle_timeout_ms

    def run(self, *, max_messages: int, commit_offsets: bool) -> dict[str, int]:
        if max_messages < 1:
            raise ValueError("max_messages must be positive")
        try:
            from kafka import KafkaConsumer, TopicPartition
            from kafka.structs import OffsetAndMetadata
        except ImportError as exc:  # pragma: no cover - runtime dependency guard
            raise DtsConfigurationError("KAFKA_PYTHON_DEPENDENCY_REQUIRED") from exc

        consumer = KafkaConsumer(
            bootstrap_servers=list(self.settings.broker_urls),
            enable_auto_commit=False,
            group_id=self.settings.group_id,
            sasl_mechanism="PLAIN",
            security_protocol="SASL_PLAINTEXT",
            sasl_plain_username=self.settings.sasl_username,
            sasl_plain_password=self.settings.password,
            consumer_timeout_ms=self.idle_timeout_ms,
        )
        topic_partition = TopicPartition(
            self.settings.topic,
            self.settings.partition,
        )
        counters = {"seen": 0, "processed": 0, "ignored": 0, "duplicates": 0, "committed": 0}
        try:
            consumer.assign([topic_partition])
            self._seek_initial_position(consumer, topic_partition)
            for message in consumer:
                record = decode_dts_avro(message.value)
                event = build_change_event(
                    record,
                    source_region=self.settings.source_region,
                    topic=message.topic,
                    partition=message.partition,
                    offset=message.offset,
                )
                result = self.processor.process(event)
                counters["seen"] += 1
                if result.status == "PROCESSED":
                    counters["processed"] += 1
                elif result.status == "DUPLICATE":
                    counters["duplicates"] += 1
                else:
                    counters["ignored"] += 1
                if commit_offsets:
                    consumer.commit(
                        offsets={
                            topic_partition: OffsetAndMetadata(
                                message.offset + 1,
                                "tit-dts-consumer-v1",
                                -1,
                            )
                        }
                    )
                    counters["committed"] += 1
                if counters["seen"] >= max_messages:
                    break
        finally:
            consumer.close()
        return counters

    def _seek_initial_position(self, consumer: Any, topic_partition: Any) -> None:
        committed = consumer.committed(topic_partition)
        if self.processor.authoritative_checkpoint:
            database_offset = self.processor.resume_offset(
                source_region=self.settings.source_region,
                topic=self.settings.topic,
                partition=self.settings.partition,
            )
            if database_offset is not None:
                if committed is not None and committed > database_offset:
                    raise DtsConfigurationError(
                        "DTS_KAFKA_OFFSET_AHEAD_OF_DATABASE"
                    )
                consumer.seek(topic_partition, database_offset)
                return
            # A new target ledger must replay from the explicit subscription
            # boundary even if an earlier shadow run advanced this group.
            committed = None
        if committed is not None:
            consumer.seek(topic_partition, committed)
            return
        if self.settings.start_timestamp_seconds is None:
            raise DtsConfigurationError("TIT_DTS_START_AT_REQUIRED_FOR_NEW_GROUP")
        offsets = consumer.offsets_for_times(
            {topic_partition: self.settings.start_timestamp_seconds}
        )
        resolved = offsets.get(topic_partition)
        if resolved is None:
            raise DtsConfigurationError("DTS_START_AT_OUTSIDE_AVAILABLE_RANGE")
        consumer.seek(topic_partition, resolved.offset)


# Compatibility name for the no-write command and existing callers.  The
# selected sink, not the Kafka transport, determines whether a run is shadow
# validation or durable ingestion.
DtsKafkaShadowConsumer = DtsKafkaConsumer


__all__ = [
    "AppointProjectionCandidate",
    "DirtyKeySet",
    "DtsChangeEvent",
    "DtsConfigurationError",
    "DtsConsumerSettings",
    "DtsEventProcessor",
    "DtsKafkaConsumer",
    "DtsKafkaShadowConsumer",
    "DtsRecordError",
    "InMemoryShadowSink",
    "ProcessResult",
    "SOURCE_FIELD_WHITELIST",
    "build_change_event",
    "decode_dts_avro",
    "derive_penalty_flags",
    "is_peak_lesson",
    "project_appoint_candidate",
    "reduce_latest_complaints",
    "route_dirty_keys",
    "source_table_suffix",
    "teacher_matches_region",
]
