"""Durable PostgreSQL state for the Aliyun DTS source-wide consumer."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Engine,
    Float,
    Integer,
    String,
    Text,
    Time,
    URL,
    and_,
    cast,
    create_engine,
    or_,
    select,
    text,
    tuple_,
)
from sqlalchemy import event as sqlalchemy_event
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB, insert

from .db_models import (
    DtsDirtyKeyRecord,
    DtsIngestCheckpointRecord,
    DtsIngestEventRecord,
    DtsSourceRowRecord,
    LessonSourceWideRecord,
    TeacherSourceWideRecord,
)
from .dts_source_consumer import (
    DATA_OPERATIONS,
    SOURCE_FIELD_WHITELIST,
    AppointProjectionCandidate,
    DirtyKeySet,
    DtsChangeEvent,
    DtsConfigurationError,
    DtsRecordError,
    _qa_appoint_ids,
    assert_domestic_event_protected,
    source_table_suffix,
    student_subject,
)
from .runtime_settings import reject_ambient_libpq_connection_identity


EXPECTED_DATABASE = "tide_system_test"
EXPECTED_SCHEMA = "public"
EXPECTED_ROLE = "tit_dts_ingest_runtime"
APPROVED_INSECURE_PRE_HOST = "tide-system.rwlb.singapore.rds.aliyuncs.com"
APPROVED_INSECURE_PRE_PORT = 5432
MUTABLE_RELATIONS = frozenset(
    {
        "public.dts_ingest_checkpoints",
        "public.dts_ingest_events",
        "public.dts_source_rows",
        "public.dts_dirty_keys",
        "public.teacher_source_wide",
        "public.lesson_source_wide",
    }
)
TEACHER_COURSE_SCOPE_FIELDS = frozenset({"course", "status_on_time"})
DIRTY_KEY_UPSERT_BATCH_SIZE = 500
SOURCE_ROW_UPSERT_BATCH_SIZE = 500
PROJECTION_ADVISORY_LOCK_NAME = "tit-dts-wide-projector-v1"
DOMESTIC_STUDENT_TOKEN_SQL_PATTERN = r"^dom:v1:[0-9a-f]{64}$"
DOMESTIC_STUDENT_KEY_FINGERPRINT_PATTERN = r"^[0-9a-f]{64}$"
DOMESTIC_STUDENT_CONTRACT_VERSION = "dom_student_hmac_v1"
DOMESTIC_STUDENT_CONTRACT_SOURCE_TABLE = "__dom_student_privacy_contract__"
SOURCE_WIDE_TABLES = (
    TeacherSourceWideRecord.__table__,
    LessonSourceWideRecord.__table__,
)
DTS_STATE_TABLES = (
    DtsIngestCheckpointRecord.__table__,
    DtsIngestEventRecord.__table__,
    DtsSourceRowRecord.__table__,
    DtsDirtyKeyRecord.__table__,
)


EXPECTED_DTS_STATE_CONSTRAINTS = (
    (
        "dts_ingest_checkpoints",
        "ck_dts_checkpoint_offsets",
        "c",
        True,
        "CHECK (partition_id >= 0 AND next_offset >= 0)",
    ),
    (
        "dts_ingest_checkpoints",
        "ck_dts_checkpoint_region",
        "c",
        True,
        "CHECK (source_region::text = ANY (ARRAY['ovs'::character varying, "
        "'dom'::character varying]::text[]))",
    ),
    (
        "dts_ingest_checkpoints",
        "pk_dts_ingest_checkpoints",
        "p",
        True,
        "PRIMARY KEY (source_region, topic, partition_id)",
    ),
    (
        "dts_ingest_events",
        "ck_dts_event_offsets",
        "c",
        True,
        "CHECK (partition_id >= 0 AND offset_value >= 0 "
        "AND dirty_key_count >= 0)",
    ),
    (
        "dts_ingest_events",
        "ck_dts_event_region",
        "c",
        True,
        "CHECK (source_region::text = ANY (ARRAY['ovs'::character varying, "
        "'dom'::character varying]::text[]))",
    ),
    (
        "dts_ingest_events",
        "ck_dts_event_route_status",
        "c",
        True,
        "CHECK (route_status::text = ANY (ARRAY['PROCESSED'::character varying, "
        "'IGNORED'::character varying]::text[]))",
    ),
    (
        "dts_ingest_events",
        "pk_dts_ingest_events",
        "p",
        True,
        "PRIMARY KEY (source_region, topic, partition_id, offset_value)",
    ),
    (
        "dts_source_rows",
        "ck_dts_source_row_region",
        "c",
        True,
        "CHECK (source_region::text = ANY (ARRAY['ovs'::character varying, "
        "'dom'::character varying]::text[]))",
    ),
    (
        "dts_source_rows",
        "ck_dts_source_row_version",
        "c",
        True,
        "CHECK (last_partition >= 0 AND last_offset >= 0 AND row_version >= 1)",
    ),
    (
        "dts_source_rows",
        "pk_dts_source_rows",
        "p",
        True,
        "PRIMARY KEY (source_region, source_table, source_key)",
    ),
    (
        "dts_dirty_keys",
        "ck_dts_dirty_key_counters",
        "c",
        True,
        "CHECK (pending_event_count >= 1 AND attempt_count >= 0 "
        "AND last_partition >= 0 AND last_offset >= 0 AND row_version >= 1)",
    ),
    (
        "dts_dirty_keys",
        "ck_dts_dirty_key_region",
        "c",
        True,
        "CHECK (last_source_region::text = ANY (ARRAY['ovs'::character varying, "
        "'dom'::character varying]::text[]))",
    ),
    (
        "dts_dirty_keys",
        "ck_dts_dirty_key_status",
        "c",
        True,
        "CHECK (status::text = ANY (ARRAY['PENDING'::character varying, "
        "'PROCESSING'::character varying, 'RETRY'::character varying, "
        "'COMPLETED'::character varying]::text[]))",
    ),
    (
        "dts_dirty_keys",
        "ck_dts_dirty_key_type",
        "c",
        True,
        "CHECK (key_type::text = ANY (ARRAY['COURSE'::character varying, "
        "'TEACHER'::character varying, 'TEACHER_STUDENT'::character varying, "
        "'LABEL'::character varying, 'COMPLAINT_CATEGORY'::character varying]"
        "::text[]))",
    ),
    (
        "dts_dirty_keys",
        "pk_dts_dirty_keys",
        "p",
        True,
        "PRIMARY KEY (key_type, key_part_1, key_part_2)",
    ),
)
EXPECTED_DTS_STATE_INDEXES = (
    (
        "dts_dirty_keys",
        "ix_dts_dirty_keys_pending_fifo",
        "btree",
        True,
        True,
        False,
        ("last_seen_at", "key_type", "key_part_1", "key_part_2"),
        ("timestamptz_ops", "text_ops", "text_ops", "text_ops"),
        True,
    ),
    (
        "dts_dirty_keys",
        "ix_dts_dirty_keys_ready",
        "btree",
        True,
        True,
        False,
        ("status", "next_attempt_at", "last_seen_at"),
        ("text_ops", "timestamptz_ops", "timestamptz_ops"),
        False,
    ),
    (
        "dts_dirty_keys",
        "ix_dts_dirty_keys_retry_due",
        "btree",
        True,
        True,
        False,
        (
            "next_attempt_at",
            "last_seen_at",
            "key_type",
            "key_part_1",
            "key_part_2",
        ),
        (
            "timestamptz_ops",
            "timestamptz_ops",
            "text_ops",
            "text_ops",
            "text_ops",
        ),
        True,
    ),
    (
        "dts_ingest_events",
        "ix_dts_ingest_events_source_table_processed",
        "btree",
        True,
        True,
        False,
        ("source_region", "source_table", "processed_at"),
        ("text_ops", "text_ops", "timestamptz_ops"),
        False,
    ),
    (
        "dts_source_rows",
        "ix_dts_source_rows_dependency_keys",
        "gin",
        True,
        True,
        False,
        ("dependency_keys",),
        ("jsonb_path_ops",),
        False,
    ),
    (
        "dts_source_rows",
        "ix_dts_source_rows_table_active",
        "btree",
        True,
        True,
        False,
        ("source_region", "source_table", "is_deleted"),
        ("text_ops", "text_ops", "bool_ops"),
        False,
    ),
)
EXPECTED_DTS_STATE_GUARD_TRIGGERS = tuple(
    (
        table.name,
        "guard_dts_runtime_state_write",
        31,
        "public",
        "guard_dts_runtime_state_write",
        "",
        False,
        0,
        "",
        True,
    )
    for table in DTS_STATE_TABLES
)
EXPECTED_SOURCE_WIDE_TRIGGER_DEFINITIONS = (
    (
        "lesson_source_wide",
        "guard_dom_lesson_student_privacy_v1",
        23,
        "public",
        "guard_dom_lesson_student_privacy_v1",
        "",
        False,
        0,
        "",
        True,
    ),
    (
        "lesson_source_wide",
        "trg_lesson_source_wide_outbox_v1",
        29,
        "public",
        "emit_source_wide_change_v1",
        "",
        True,
        0,
        "",
        True,
    ),
    (
        "teacher_source_wide",
        "trg_teacher_source_wide_outbox_v1",
        29,
        "public",
        "emit_source_wide_change_v1",
        "",
        True,
        0,
        "",
        True,
    ),
)
EXPECTED_DOMESTIC_PRIVACY_FUNCTIONS = (
    (
        "dom_student_json_is_safe_v1",
        "payload jsonb",
        "plpgsql",
        "i",
        True,
        False,
        ("search_path=pg_catalog",),
        "c8ec7cb970fdbc2f3c0fffce473fa7cefe5ea345e6380cd0c53713a24b2cce70",
    ),
    (
        "guard_dts_runtime_state_write",
        "",
        "plpgsql",
        "v",
        False,
        False,
        ("search_path=pg_catalog, public",),
        "d313d6dd40b98a4086d639e6582e4f8c7d1f64770af9fb4b14e457ae51fe6bff",
    ),
    (
        "guard_dom_lesson_student_privacy_v1",
        "",
        "plpgsql",
        "v",
        False,
        False,
        ("search_path=pg_catalog, public",),
        "cae4238b02a320b77ef43ab7764ee52881822a68c9d05406db56d57e116895ee",
    ),
)


def _postgres_column_type(column_type: Any) -> str:
    postgres_type = column_type.dialect_impl(postgresql.dialect())
    if isinstance(postgres_type, JSONB):
        return "jsonb"
    if isinstance(column_type, Text):
        return "text"
    if isinstance(column_type, String):
        if column_type.length is None:
            return "character varying"
        return f"character varying({column_type.length})"
    if isinstance(column_type, Date):
        return "date"
    if isinstance(column_type, Time):
        return (
            "time with time zone"
            if column_type.timezone
            else "time without time zone"
        )
    if isinstance(column_type, Boolean):
        return "boolean"
    if isinstance(column_type, BigInteger):
        return "bigint"
    if isinstance(column_type, Integer):
        return "integer"
    if isinstance(column_type, DateTime):
        return (
            "timestamp with time zone"
            if column_type.timezone
            else "timestamp without time zone"
        )
    if isinstance(column_type, Float):
        return "double precision"
    raise RuntimeError(
        f"unsupported source-wide contract type: {column_type!r}"
    )


EXPECTED_SOURCE_WIDE_COLUMNS = tuple(
    (
        table.name,
        column.name,
        _postgres_column_type(column.type),
        not column.nullable,
    )
    for table in SOURCE_WIDE_TABLES
    for column in table.columns
)
EXPECTED_DTS_STATE_COLUMNS = tuple(
    (
        table.name,
        column.name,
        _postgres_column_type(column.type),
        not column.nullable,
    )
    for table in DTS_STATE_TABLES
    for column in table.columns
)


class DtsIngestStoreError(RuntimeError):
    """Fail-closed persistence or runtime-identity error."""


@dataclass(frozen=True)
class DtsResumeCheckpoint:
    """Database-authoritative position used to resume the official DTS SDK."""

    next_offset: int
    source_timestamp: int


@dataclass(frozen=True)
class _EventPersistencePlan:
    ignored: bool
    source_write_state: tuple[
        str,
        dict[str, str],
        dict[str, list[str]],
        dict[str, Any],
        bool,
    ] | None
    key_rows: tuple[tuple[str, str, str], ...]
    issue_codes: list[str]


@dataclass(frozen=True)
class DtsIngestDatabaseSettings:
    host: str
    password: str = field(repr=False)
    port: int = 5432
    database: str = EXPECTED_DATABASE
    username: str = EXPECTED_ROLE
    schema: str = EXPECTED_SCHEMA
    sslmode: str = "verify-full"
    allow_insecure_db: bool = False

    def __post_init__(self) -> None:
        for configured, expected, error_code in (
            (
                self.database,
                EXPECTED_DATABASE,
                f"TIT_DTS_INGEST_DB_NAME_MUST_EQUAL_{EXPECTED_DATABASE}",
            ),
            (
                self.username,
                EXPECTED_ROLE,
                f"TIT_DTS_INGEST_DB_USER_MUST_EQUAL_{EXPECTED_ROLE}",
            ),
            (
                self.schema,
                EXPECTED_SCHEMA,
                f"TIT_DTS_INGEST_DB_SCHEMA_MUST_EQUAL_{EXPECTED_SCHEMA}",
            ),
        ):
            if configured != expected:
                raise DtsConfigurationError(error_code)
        if self.sslmode not in {"verify-full", "disable"}:
            raise DtsConfigurationError(
                "TIT_DTS_INGEST_DB_SSLMODE_INVALID"
            )
        if not isinstance(self.allow_insecure_db, bool):
            raise DtsConfigurationError(
                "TIT_DTS_ALLOW_INSECURE_DB_INVALID"
            )
        if self.sslmode == "disable":
            try:
                reject_ambient_libpq_connection_identity()
            except ValueError as exc:
                raise DtsConfigurationError(
                    "DTS_LIBPQ_CONNECTION_IDENTITY_ENV_FORBIDDEN"
                ) from exc
            if not self.allow_insecure_db:
                raise DtsConfigurationError(
                    "TIT_DTS_ALLOW_INSECURE_DB_REQUIRED_FOR_SSLMODE_DISABLE"
                )
            if (
                self.host != APPROVED_INSECURE_PRE_HOST
                or self.port != APPROVED_INSECURE_PRE_PORT
            ):
                raise DtsConfigurationError(
                    "TIT_DTS_INGEST_DB_SSLMODE_DISABLE_ENDPOINT_NOT_APPROVED"
                )
        elif self.allow_insecure_db:
            raise DtsConfigurationError(
                "TIT_DTS_ALLOW_INSECURE_DB_REQUIRES_SSLMODE_DISABLE"
            )

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> DtsIngestDatabaseSettings:
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

        raw_port = values.get("TIT_DTS_INGEST_DB_PORT", "5432").strip()
        try:
            port = int(raw_port)
        except ValueError as exc:
            raise DtsConfigurationError("TIT_DTS_INGEST_DB_PORT_INVALID") from exc
        if not 1 <= port <= 65535:
            raise DtsConfigurationError("TIT_DTS_INGEST_DB_PORT_INVALID")

        sslmode = values.get(
            "TIT_DTS_INGEST_DB_SSLMODE",
            "verify-full",
        ).strip()
        if sslmode not in {"verify-full", "disable"}:
            raise DtsConfigurationError(
                "TIT_DTS_INGEST_DB_SSLMODE_INVALID"
            )

        raw_allow_insecure = values.get(
            "TIT_DTS_ALLOW_INSECURE_DB",
            "false",
        ).strip()
        if raw_allow_insecure not in {"true", "false"}:
            raise DtsConfigurationError(
                "TIT_DTS_ALLOW_INSECURE_DB_INVALID"
            )
        allow_insecure_db = raw_allow_insecure == "true"

        if sslmode == "disable":
            try:
                reject_ambient_libpq_connection_identity(values)
            except ValueError as exc:
                raise DtsConfigurationError(
                    "DTS_LIBPQ_CONNECTION_IDENTITY_ENV_FORBIDDEN"
                ) from exc

        for name, expected in (
            ("TIT_DTS_INGEST_DB_NAME", EXPECTED_DATABASE),
            ("TIT_DTS_INGEST_DB_USER", EXPECTED_ROLE),
            ("TIT_DTS_INGEST_DB_SCHEMA", EXPECTED_SCHEMA),
        ):
            configured = values.get(name, expected).strip()
            if configured != expected:
                raise DtsConfigurationError(f"{name}_MUST_EQUAL_{expected}")

        return cls(
            host=required("TIT_DTS_INGEST_DB_HOST"),
            port=port,
            password=required_secret("TIT_DTS_INGEST_DB_PASSWORD"),
            sslmode=sslmode,
            allow_insecure_db=allow_insecure_db,
        )

    def sqlalchemy_url(self) -> URL:
        return URL.create(
            "postgresql+psycopg",
            username=self.username,
            password=self.password,
            host=self.host,
            port=self.port,
            database=self.database,
            query={"sslmode": self.sslmode},
        )

    def safe_summary(self) -> dict[str, Any]:
        return {
            "database": self.database,
            "schema": self.schema,
            "role": self.username,
            "port": self.port,
            "sslmode": self.sslmode,
            "insecure_transport_authorized": self.allow_insecure_db,
        }


@dataclass(frozen=True)
class DtsProjectionActivationSettings:
    activation_timestamp_seconds: int
    required_ovs_topic: str
    required_dom_topic: str

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> DtsProjectionActivationSettings:
        values = os.environ if environ is None else environ

        def required(name: str) -> str:
            value = values.get(name, "").strip()
            if not value:
                raise DtsConfigurationError(f"{name}_REQUIRED")
            return value

        raw_activation = required("TIT_DTS_ACTIVATION_AT").replace(" ", "T", 1)
        try:
            activation_at = datetime.fromisoformat(raw_activation)
        except ValueError as exc:
            raise DtsConfigurationError("TIT_DTS_ACTIVATION_AT_INVALID") from exc
        if activation_at.tzinfo is None:
            raise DtsConfigurationError(
                "TIT_DTS_ACTIVATION_AT_REQUIRES_TIMEZONE"
            )
        return cls(
            activation_timestamp_seconds=int(activation_at.timestamp()),
            required_ovs_topic=required("TIT_DTS_REQUIRED_OVS_TOPIC"),
            required_dom_topic=required("TIT_DTS_REQUIRED_DOM_TOPIC"),
        )

    def require_current_stream(self, *, source_region: str, topic: str) -> None:
        required_topic = {
            "ovs": self.required_ovs_topic,
            "dom": self.required_dom_topic,
        }.get(source_region)
        if required_topic is None:
            raise DtsConfigurationError("TIT_DTS_SOURCE_REGION_UNSUPPORTED")
        if topic != required_topic:
            raise DtsConfigurationError("DTS_PROJECTION_STREAM_TOPIC_MISMATCH")


def build_dts_ingest_engine(
    settings: DtsIngestDatabaseSettings,
    *,
    source_region: str | None = None,
) -> Engine:
    if source_region is not None and source_region not in {"dom", "ovs"}:
        raise DtsConfigurationError("TIT_DTS_SOURCE_REGION_UNSUPPORTED")
    application_name = (
        f"tit-dts-ingest-{source_region}"
        if source_region is not None
        else "tit-dts-ingest"
    )
    engine = create_engine(
        settings.sqlalchemy_url(),
        future=True,
        pool_pre_ping=True,
        # One connection remains checked out for the projector's session-level
        # advisory lock; ingestion and projection share the second connection.
        pool_size=2,
        max_overflow=0,
        pool_timeout=5,
        pool_recycle=1800,
        connect_args={
            "connect_timeout": 8,
            "application_name": application_name,
            "options": (
                "-c search_path=public "
                "-c lock_timeout=5000 "
                "-c idle_in_transaction_session_timeout=60000 "
                "-c statement_timeout=60000"
            ),
        },
    )
    sqlalchemy_event.listen(
        engine,
        "connect",
        lambda dbapi_connection, _connection_record: (
            _validate_dts_physical_connection_transport(
                dbapi_connection,
                settings=settings,
            )
        ),
    )
    # An established TLS session cannot turn plaintext while it is pooled.
    # The temporary PRE plaintext exception is different: re-check every
    # checkout so enabling server TLS expires the exception immediately.
    if settings.sslmode == "disable":
        sqlalchemy_event.listen(
            engine,
            "checkout",
            lambda dbapi_connection, _connection_record, _connection_proxy: (
                _validate_dts_physical_connection_transport(
                    dbapi_connection,
                    settings=settings,
                )
            ),
        )
    return engine


def _require_session_transport(
    *,
    sslmode: str,
    session_ssl: object,
    server_ssl: str,
) -> None:
    normalized_server_ssl = server_ssl.lower()
    if (
        not isinstance(session_ssl, bool)
        or normalized_server_ssl not in {"on", "off"}
    ):
        raise DtsIngestStoreError("DTS_TARGET_SSL_SETTING_UNAVAILABLE")
    if sslmode == "disable" and (
        session_ssl is not False or normalized_server_ssl != "off"
    ):
        raise DtsIngestStoreError(
            "DTS_TARGET_TLS_AVAILABLE_REQUIRES_VERIFY_FULL"
        )
    if sslmode == "verify-full" and (
        session_ssl is not True or normalized_server_ssl != "on"
    ):
        raise DtsIngestStoreError("DTS_TARGET_TLS_REQUIRED")


def _validate_dts_physical_connection_transport(
    dbapi_connection: Any,
    *,
    settings: DtsIngestDatabaseSettings,
) -> None:
    """Validate a new session, and revalidate PRE plaintext on checkout."""

    try:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute(
                "SELECT "
                "(SELECT ssl FROM pg_catalog.pg_stat_ssl "
                "WHERE pid = pg_catalog.pg_backend_pid()), "
                "current_setting('ssl')"
            )
            row = cursor.fetchone()
        finally:
            cursor.close()
    except DtsIngestStoreError:
        raise
    except Exception as exc:
        sqlstate = getattr(exc, "sqlstate", None)
        if sqlstate is None:
            sqlstate = getattr(getattr(exc, "diag", None), "sqlstate", None)
        error_code = {
            "42501": "DTS_TARGET_TRANSPORT_INSPECTION_PERMISSION_DENIED",
            "42P01": "DTS_TARGET_TRANSPORT_INSPECTION_UNAVAILABLE",
            "42703": "DTS_TARGET_TRANSPORT_INSPECTION_UNAVAILABLE",
            "42883": "DTS_TARGET_TRANSPORT_INSPECTION_UNAVAILABLE",
            "0A000": "DTS_TARGET_TRANSPORT_INSPECTION_UNAVAILABLE",
        }.get(sqlstate, "DTS_TARGET_TRANSPORT_INSPECTION_FAILED")
        raise DtsIngestStoreError(error_code) from None
    finally:
        # The connect hook must not leave an implicit transaction open before
        # SQLAlchemy hands the new DBAPI connection to the pool.
        try:
            dbapi_connection.rollback()
        except Exception:
            raise DtsIngestStoreError(
                "DTS_TARGET_TRANSPORT_INSPECTION_ROLLBACK_FAILED"
            ) from None
    if row is None or len(row) != 2:
        raise DtsIngestStoreError("DTS_TARGET_SSL_SETTING_UNAVAILABLE")
    _require_session_transport(
        sslmode=settings.sslmode,
        session_ssl=row[0],
        server_ssl=str(row[1]),
    )


def _validate_projection_activation_state(
    connection: Any,
    settings: DtsProjectionActivationSettings,
) -> None:
    missing_checkpoint = connection.execute(
        text(
            """
            WITH required_streams(source_region, topic) AS (
                VALUES
                    ('ovs'::text, CAST(:required_ovs_topic AS text)),
                    ('dom'::text, CAST(:required_dom_topic AS text))
            )
            SELECT required_streams.source_region, required_streams.topic
            FROM required_streams
            LEFT JOIN public.dts_ingest_checkpoints checkpoints
              ON checkpoints.source_region = required_streams.source_region
             AND checkpoints.topic = required_streams.topic
             AND checkpoints.partition_id = 0
            WHERE checkpoints.source_region IS NULL
               OR checkpoints.source_timestamp IS NULL
               OR checkpoints.source_timestamp < :activation_timestamp_seconds
            LIMIT 1
            """
        ),
        {
            "required_ovs_topic": settings.required_ovs_topic,
            "required_dom_topic": settings.required_dom_topic,
            "activation_timestamp_seconds": (
                settings.activation_timestamp_seconds
            ),
        },
    ).first()
    if missing_checkpoint is not None:
        raise DtsIngestStoreError("DTS_PROJECTION_CHECKPOINT_NOT_READY")

    active_category_count = connection.execute(
        text(
            """
            SELECT count(*)
            FROM public.dts_source_rows categories
            WHERE categories.source_region = 'dom'
              AND categories.source_table = 'dom_complaint_cate'
              AND categories.is_deleted IS FALSE
              AND NULLIF(BTRIM(categories.source_row ->> 'id'), '') IS NOT NULL
            """
        )
    ).scalar_one_or_none()
    if int(active_category_count or 0) < 1:
        raise DtsIngestStoreError("DTS_PROJECTION_COMPLAINT_DICTIONARY_EMPTY")

    missing_category = connection.execute(
        text(
            """
            WITH active_dictionary AS (
                SELECT DISTINCT categories.source_row ->> 'id' AS category_id
                FROM public.dts_source_rows categories
                WHERE categories.source_region = 'dom'
                  AND categories.source_table = 'dom_complaint_cate'
                  AND categories.is_deleted IS FALSE
                  AND NULLIF(
                      BTRIM(categories.source_row ->> 'id'), ''
                  ) IS NOT NULL
            ),
            referenced_categories AS (
                SELECT DISTINCT category_refs.category_id
                FROM public.dts_source_rows complaints
                CROSS JOIN LATERAL jsonb_array_elements_text(
                    COALESCE(
                        complaints.dependency_keys -> 'category_ids',
                        '[]'::jsonb
                    )
                ) category_refs(category_id)
                WHERE complaints.source_table IN (
                    'dom_complaint',
                    'dom_user_complaint',
                    'ovs_complaint',
                    'ovs_user_complaint'
                )
                  AND complaints.is_deleted IS FALSE
                  AND NULLIF(BTRIM(category_refs.category_id), '') IS NOT NULL
                  AND category_refs.category_id NOT IN ('-1', '0')
            )
            SELECT referenced_categories.category_id
            FROM referenced_categories
            LEFT JOIN active_dictionary
              ON active_dictionary.category_id = referenced_categories.category_id
            WHERE active_dictionary.category_id IS NULL
            LIMIT 1
            """
        )
    ).first()
    if missing_category is not None:
        raise DtsIngestStoreError(
            "DTS_PROJECTION_COMPLAINT_CATEGORY_DEPENDENCY_PENDING"
        )


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, bytes):
        raise DtsRecordError("DTS_BINARY_FIELD_NOT_ALLOWED_IN_MIRROR")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _source_row_state(
    event: DtsChangeEvent,
) -> tuple[
    str,
    dict[str, str],
    dict[str, list[str]],
    dict[str, Any],
    bool,
] | None:
    assert_domestic_event_protected(event)
    suffix = source_table_suffix(event)
    if suffix is None or event.operation not in DATA_OPERATIONS:
        return None
    row = event.before if event.operation == "DELETE" else event.after
    if row is None:
        raise DtsRecordError("DTS_SOURCE_IMAGE_MISSING")
    if event.operation == "UPDATE" and event.before is not None:
        before_id = event.before.get("id")
        after_id = event.after.get("id") if event.after is not None else None
        if (
            before_id is not None
            and after_id is not None
            and str(before_id).strip() != str(after_id).strip()
        ):
            raise DtsRecordError("DTS_SOURCE_PRIMARY_KEY_UPDATE_NOT_ALLOWED")
    raw_id = row.get("id")
    if raw_id is None and event.before is not None:
        raw_id = event.before.get("id")
    if raw_id is None or not str(raw_id).strip():
        raise DtsRecordError("DTS_SOURCE_PRIMARY_KEY_MISSING")
    source_key_data = {"id": str(raw_id).strip()}
    source_key = json.dumps(
        source_key_data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    whitelist = SOURCE_FIELD_WHITELIST[suffix]
    safe_row: dict[str, Any] = {}
    if event.operation == "UPDATE" and event.before is not None:
        safe_row.update(
            {
                name: _json_safe(value)
                for name, value in event.before.items()
                if name in whitelist
            }
        )
    safe_row.update(
        {
            name: _json_safe(value)
            for name, value in row.items()
            if name in whitelist
        }
    )
    safe_row["id"] = source_key_data["id"]
    return (
        source_key,
        source_key_data,
        _dependency_keys(suffix, safe_row),
        safe_row,
        event.operation == "DELETE",
    )


def _normalized_ids(*values: Any) -> list[str]:
    return sorted(
        {
            str(value).strip()
            for value in values
            if value is not None and str(value).strip()
        }
    )


_COMPLAINT_CATEGORY_SENTINELS = frozenset({"-1", "0"})


def _normalized_complaint_category_ids(*values: Any) -> list[str]:
    """Drop source sentinels that mean that a category level is absent."""

    return [
        value
        for value in _normalized_ids(*values)
        if value not in _COMPLAINT_CATEGORY_SENTINELS
    ]


def _dependency_keys(
    suffix: str,
    row: Mapping[str, Any],
) -> dict[str, list[str]]:
    """Build normalized reverse-lookup keys without copying sensitive fields."""

    course_ids = _normalized_ids(row.get("appoint_id"))
    teacher_ids = _normalized_ids(
        row.get("t_id"),
        row.get("teacher_id"),
        row.get("tea_id"),
    )
    student_subjects = _normalized_ids(student_subject(row))
    label_ids = _normalized_ids(row.get("label_id"))
    category_ids = _normalized_complaint_category_ids(
        row.get("complaint_type"),
        row.get("complaint_type_child"),
        row.get("complaint_type_grandson"),
        row.get("cate_parent"),
    )
    if suffix == "appoint":
        course_ids = _normalized_ids(row.get("id"))
    if suffix == "teacher":
        teacher_ids = _normalized_ids(row.get("id"))
    if suffix == "grading_label":
        label_ids = _normalized_ids(row.get("id"))
    if suffix == "complaint_cate":
        category_ids = _normalized_complaint_category_ids(
            row.get("id"),
            row.get("cate_parent"),
        )
    if suffix == "qa_ac_classroom_record":
        qa_ids, _issue = _qa_appoint_ids(row.get("info"))
        course_ids = sorted(qa_ids)
    return {
        "course_ids": course_ids,
        "teacher_ids": teacher_ids,
        "student_subjects": student_subjects,
        "label_ids": label_ids,
        "category_ids": category_ids,
    }


def _dirty_key_rows(dirty_keys: DirtyKeySet) -> tuple[tuple[str, str, str], ...]:
    values: set[tuple[str, str, str]] = set()
    values.update(("COURSE", value, "") for value in dirty_keys.course_ids)
    values.update(("TEACHER", value, "") for value in dirty_keys.teacher_ids)
    values.update(
        ("TEACHER_STUDENT", teacher_id, student_id)
        for teacher_id, student_id in dirty_keys.teacher_student_pairs
    )
    values.update(("LABEL", value, "") for value in dirty_keys.label_ids)
    values.update(
        ("COMPLAINT_CATEGORY", value, "")
        for value in dirty_keys.complaint_category_ids
    )
    return tuple(sorted(values))


def _dependency_dirty_key_rows(
    dependency_keys: Mapping[str, list[str]],
) -> tuple[tuple[str, str, str], ...]:
    """Route both sides of an ownership change from one persisted row image."""

    course_ids = dependency_keys.get("course_ids", [])
    teacher_ids = dependency_keys.get("teacher_ids", [])
    student_subjects = dependency_keys.get("student_subjects", [])
    label_ids = dependency_keys.get("label_ids", [])
    category_ids = dependency_keys.get("category_ids", [])
    values: set[tuple[str, str, str]] = set()
    values.update(("COURSE", value, "") for value in course_ids)
    values.update(("TEACHER", value, "") for value in teacher_ids)
    values.update(("LABEL", value, "") for value in label_ids)
    values.update(("COMPLAINT_CATEGORY", value, "") for value in category_ids)
    values.update(
        ("TEACHER_STUDENT", teacher_id, student_id)
        for teacher_id in teacher_ids
        for student_id in student_subjects
    )
    return tuple(sorted(values))


class PostgresDtsEventSink:
    """Persist ledger, current-state mirror, dirty keys and replay checkpoint."""

    authoritative_checkpoint = True

    def __init__(
        self,
        settings: DtsIngestDatabaseSettings,
        *,
        source_region: str | None = None,
        engine: Engine | None = None,
    ) -> None:
        if source_region is not None and source_region not in {"dom", "ovs"}:
            raise DtsConfigurationError("TIT_DTS_SOURCE_REGION_UNSUPPORTED")
        self.settings = settings
        self.source_region = source_region
        self.engine = engine or build_dts_ingest_engine(
            settings,
            source_region=source_region,
        )
        self._validated = False
        self._projection_lock_connection: Any | None = None
        self._projection_lock_backend_pid: int | None = None

    def close(self) -> None:
        lock_connection = getattr(self, "_projection_lock_connection", None)
        self._projection_lock_connection = None
        self._projection_lock_backend_pid = None
        if lock_connection is not None:
            self._close_projection_lock_connection(
                lock_connection,
                unlock=True,
            )
        self.engine.dispose()

    @staticmethod
    def _close_projection_lock_connection(
        connection: Any,
        *,
        unlock: bool,
    ) -> None:
        try:
            connection.rollback()
            if unlock:
                unlocked = connection.execute(
                    text(
                        "SELECT pg_catalog.pg_advisory_unlock("
                        "pg_catalog.hashtextextended(:lock_name, 0)"
                        ")"
                    ),
                    {"lock_name": PROJECTION_ADVISORY_LOCK_NAME},
                ).scalar_one_or_none()
                connection.commit()
                if unlocked is not True:
                    connection.invalidate()
        except Exception:
            # Never return a possibly lock-owning physical session to the pool.
            connection.invalidate()
        finally:
            connection.close()

    def acquire_projection_activation(
        self,
        settings: DtsProjectionActivationSettings,
    ) -> None:
        self._validate_runtime()
        if self._projection_lock_connection is not None:
            return
        connection = self.engine.connect()
        lock_acquired = False
        try:
            lock_result = connection.execute(
                text(
                    "SELECT pg_catalog.pg_try_advisory_lock("
                    "pg_catalog.hashtextextended(:lock_name, 0)"
                    "), pg_catalog.pg_backend_pid()"
                ),
                {"lock_name": PROJECTION_ADVISORY_LOCK_NAME},
            ).one()
            lock_acquired = lock_result[0] is True
            if not lock_acquired:
                raise DtsIngestStoreError("DTS_PROJECTION_LOCK_NOT_ACQUIRED")
            _validate_projection_activation_state(connection, settings)
            # End SQLAlchemy's implicit transaction while retaining the
            # session-level advisory lock on this checked-out connection.
            connection.commit()
        except Exception:
            self._close_projection_lock_connection(
                connection,
                unlock=lock_acquired,
            )
            raise
        self._projection_lock_connection = connection
        self._projection_lock_backend_pid = int(lock_result[1])

    def assert_projection_lock_held(self) -> None:
        connection = self._projection_lock_connection
        expected_backend_pid = self._projection_lock_backend_pid
        if connection is None or expected_backend_pid is None:
            raise DtsIngestStoreError("DTS_PROJECTION_LOCK_NOT_ACQUIRED")
        try:
            lock_session = connection.execute(
                text(
                    "SELECT pg_catalog.pg_backend_pid(), "
                    "(SELECT ssl FROM pg_catalog.pg_stat_ssl "
                    "WHERE pid = pg_catalog.pg_backend_pid()), "
                    "current_setting('ssl')"
                )
            ).one()
            actual_backend_pid = lock_session[0]
            _require_session_transport(
                sslmode=self.settings.sslmode,
                session_ssl=lock_session[1],
                server_ssl=str(lock_session[2]),
            )
            connection.commit()
        except DtsIngestStoreError:
            self._projection_lock_connection = None
            self._projection_lock_backend_pid = None
            connection.invalidate()
            connection.close()
            raise
        except Exception as exc:
            self._projection_lock_connection = None
            self._projection_lock_backend_pid = None
            connection.invalidate()
            connection.close()
            raise DtsIngestStoreError("DTS_PROJECTION_LOCK_LOST") from exc
        if actual_backend_pid != expected_backend_pid:
            self._projection_lock_connection = None
            self._projection_lock_backend_pid = None
            connection.invalidate()
            connection.close()
            raise DtsIngestStoreError("DTS_PROJECTION_LOCK_LOST")

    def _validate_runtime(self) -> None:
        if self._validated:
            return
        if self.engine.dialect.name != "postgresql":
            raise DtsIngestStoreError("DTS_TARGET_MUST_BE_POSTGRESQL")
        with self.engine.connect() as connection:
            identity = connection.execute(
                text(
                    """
                    SELECT current_database(), current_user, current_schema(),
                           current_setting('transaction_read_only'),
                           (SELECT ssl FROM pg_catalog.pg_stat_ssl
                            WHERE pid = pg_catalog.pg_backend_pid()),
                           current_setting('ssl')
                    """
                )
            ).one()
            if tuple(identity[:3]) != (
                EXPECTED_DATABASE,
                EXPECTED_ROLE,
                EXPECTED_SCHEMA,
            ):
                raise DtsIngestStoreError("DTS_TARGET_IDENTITY_MISMATCH")
            if str(identity[3]).lower() != "off":
                raise DtsIngestStoreError("DTS_TARGET_IS_READ_ONLY")
            _require_session_transport(
                sslmode=self.settings.sslmode,
                session_ssl=identity[4],
                server_ssl=str(identity[5]),
            )

            missing = connection.execute(
                text(
                    """
                    SELECT relation_name, privilege_name
                    FROM unnest(CAST(:relations AS text[])) relation(relation_name)
                    CROSS JOIN unnest(
                        ARRAY['SELECT','INSERT','UPDATE','DELETE']::text[]
                    )
                        privilege(privilege_name)
                    WHERE NOT has_table_privilege(
                        current_user, relation_name, privilege_name
                    )
                    """
                ),
                {"relations": sorted(MUTABLE_RELATIONS)},
            ).all()
            if missing:
                raise DtsIngestStoreError("DTS_TARGET_PRIVILEGE_MISSING")

            invalid_allowed = connection.execute(
                text(
                    """
                    SELECT relation_name, privilege_name
                    FROM unnest(CAST(:relations AS text[]))
                        relation(relation_name)
                    CROSS JOIN unnest(ARRAY['TRUNCATE','TRIGGER']::text[])
                        privilege(privilege_name)
                    WHERE has_table_privilege(
                        current_user, relation_name, privilege_name
                    )
                    """
                ),
                {"relations": sorted(MUTABLE_RELATIONS)},
            ).first()
            if invalid_allowed is not None:
                raise DtsIngestStoreError("DTS_TARGET_PRIVILEGE_INVALID")

            unexpected = connection.execute(
                text(
                    """
                    SELECT namespaces.nspname, relations.relname
                    FROM pg_class relations
                    JOIN pg_namespace namespaces
                      ON namespaces.oid = relations.relnamespace
                    WHERE namespaces.nspname IN ('public', 'tide')
                      AND relations.relkind IN ('r', 'p', 'v', 'm', 'f')
                      AND namespaces.nspname || '.' || relations.relname
                          <> ALL(CAST(:relations AS text[]))
                      AND (
                          has_table_privilege(current_user, relations.oid, 'INSERT')
                          OR has_table_privilege(current_user, relations.oid, 'UPDATE')
                          OR has_table_privilege(current_user, relations.oid, 'DELETE')
                          OR has_table_privilege(current_user, relations.oid, 'TRUNCATE')
                          OR has_table_privilege(current_user, relations.oid, 'TRIGGER')
                      )
                    LIMIT 1
                    """
                ),
                {"relations": sorted(MUTABLE_RELATIONS)},
            ).first()
            if unexpected is not None:
                raise DtsIngestStoreError("DTS_TARGET_PRIVILEGE_TOO_BROAD")

            state_columns = connection.execute(
                text(
                    """
                    SELECT relations.relname,
                           attributes.attname,
                           pg_catalog.format_type(
                               attributes.atttypid,
                               attributes.atttypmod
                           ),
                           attributes.attnotnull
                    FROM pg_catalog.pg_class relations
                    JOIN pg_catalog.pg_namespace namespaces
                      ON namespaces.oid = relations.relnamespace
                    JOIN pg_catalog.pg_attribute attributes
                      ON attributes.attrelid = relations.oid
                    WHERE namespaces.nspname = :schema_name
                      AND relations.relkind IN ('r', 'p')
                      AND relations.relname = ANY(
                          CAST(:table_names AS text[])
                      )
                      AND attributes.attnum > 0
                      AND NOT attributes.attisdropped
                    ORDER BY pg_catalog.array_position(
                                 CAST(:table_names AS text[]),
                                 relations.relname
                             ),
                             attributes.attnum
                    """
                ),
                {
                    "schema_name": EXPECTED_SCHEMA,
                    "table_names": [table.name for table in DTS_STATE_TABLES],
                },
            ).all()
            actual_state_columns = tuple(
                (
                    str(row[0]),
                    str(row[1]),
                    str(row[2]),
                    bool(row[3]),
                )
                for row in state_columns
            )
            if actual_state_columns != EXPECTED_DTS_STATE_COLUMNS:
                raise DtsIngestStoreError("DTS_TARGET_STATE_SCHEMA_MISMATCH")

            state_constraints = connection.execute(
                text(
                    """
                    SELECT relations.relname,
                           constraints.conname,
                           constraints.contype,
                           constraints.convalidated,
                           pg_catalog.pg_get_constraintdef(
                               constraints.oid,
                               true
                           )
                    FROM pg_catalog.pg_constraint constraints
                    JOIN pg_catalog.pg_class relations
                      ON relations.oid = constraints.conrelid
                    JOIN pg_catalog.pg_namespace namespaces
                      ON namespaces.oid = relations.relnamespace
                    WHERE namespaces.nspname = :schema_name
                      AND relations.relname = ANY(
                          CAST(:table_names AS text[])
                      )
                      AND constraints.contype IN ('p', 'c', 'u', 'f', 'x')
                    ORDER BY pg_catalog.array_position(
                                 CAST(:table_names AS text[]),
                                 relations.relname
                             ),
                             constraints.conname
                    """
                ),
                {
                    "schema_name": EXPECTED_SCHEMA,
                    "table_names": [table.name for table in DTS_STATE_TABLES],
                },
            ).all()
            actual_state_constraints = tuple(
                (
                    str(row[0]),
                    str(row[1]),
                    str(row[2]),
                    bool(row[3]),
                    str(row[4]),
                )
                for row in state_constraints
            )
            if actual_state_constraints != EXPECTED_DTS_STATE_CONSTRAINTS:
                raise DtsIngestStoreError(
                    "DTS_TARGET_STATE_CONSTRAINT_MISMATCH"
                )

            state_indexes = connection.execute(
                text(
                    """
                    SELECT table_relations.relname,
                           index_relations.relname,
                           access_methods.amname,
                           indexes.indisvalid,
                           indexes.indisready,
                           indexes.indisunique,
                           ARRAY(
                               SELECT attributes.attname
                               FROM unnest(indexes.indkey) WITH ORDINALITY
                                   AS keys(attnum, key_order)
                               JOIN pg_catalog.pg_attribute attributes
                                 ON attributes.attrelid = indexes.indrelid
                                AND attributes.attnum = keys.attnum
                               WHERE keys.key_order <= indexes.indnkeyatts
                               ORDER BY keys.key_order
                           ),
                           ARRAY(
                               SELECT opclasses.opcname
                               FROM unnest(indexes.indclass) WITH ORDINALITY
                                   AS classes(opclass_oid, key_order)
                               JOIN pg_catalog.pg_opclass opclasses
                                 ON opclasses.oid = classes.opclass_oid
                               WHERE classes.key_order <= indexes.indnkeyatts
                               ORDER BY classes.key_order
                           ),
                           indexes.indpred IS NOT NULL
                    FROM pg_catalog.pg_index indexes
                    JOIN pg_catalog.pg_class index_relations
                      ON index_relations.oid = indexes.indexrelid
                    JOIN pg_catalog.pg_class table_relations
                      ON table_relations.oid = indexes.indrelid
                    JOIN pg_catalog.pg_namespace namespaces
                      ON namespaces.oid = table_relations.relnamespace
                    JOIN pg_catalog.pg_am access_methods
                      ON access_methods.oid = index_relations.relam
                    WHERE namespaces.nspname = :schema_name
                      AND table_relations.relname = ANY(
                          CAST(:table_names AS text[])
                      )
                      AND index_relations.relname = ANY(
                          CAST(:index_names AS text[])
                      )
                    ORDER BY index_relations.relname
                    """
                ),
                {
                    "schema_name": EXPECTED_SCHEMA,
                    "table_names": [table.name for table in DTS_STATE_TABLES],
                    "index_names": [
                        index_name
                        for _table_name, index_name, *_rest
                        in EXPECTED_DTS_STATE_INDEXES
                    ],
                },
            ).all()
            actual_state_indexes = tuple(
                (
                    str(row[0]),
                    str(row[1]),
                    str(row[2]),
                    bool(row[3]),
                    bool(row[4]),
                    bool(row[5]),
                    tuple(str(value) for value in row[6]),
                    tuple(str(value) for value in row[7]),
                    bool(row[8]),
                )
                for row in state_indexes
            )
            if actual_state_indexes != EXPECTED_DTS_STATE_INDEXES:
                raise DtsIngestStoreError("DTS_TARGET_STATE_INDEX_MISMATCH")

            state_trigger_rows = connection.execute(
                text(
                    """
                    SELECT relations.relname,
                           triggers.tgname,
                           triggers.tgenabled,
                           triggers.tgtype,
                           function_namespaces.nspname,
                           functions.proname,
                           pg_catalog.pg_get_function_identity_arguments(
                               functions.oid
                           ),
                           functions.prosecdef,
                           triggers.tgnargs,
                           triggers.tgattr::text,
                           triggers.tgqual IS NULL
                    FROM pg_catalog.pg_trigger triggers
                    JOIN pg_catalog.pg_class relations
                      ON relations.oid = triggers.tgrelid
                    JOIN pg_catalog.pg_namespace namespaces
                      ON namespaces.oid = relations.relnamespace
                    JOIN pg_catalog.pg_proc functions
                      ON functions.oid = triggers.tgfoid
                    JOIN pg_catalog.pg_namespace function_namespaces
                      ON function_namespaces.oid = functions.pronamespace
                    WHERE namespaces.nspname = :schema_name
                      AND relations.relname = ANY(
                          CAST(:table_names AS text[])
                      )
                      AND NOT triggers.tgisinternal
                    ORDER BY pg_catalog.array_position(
                                 CAST(:table_names AS text[]),
                                 relations.relname
                             ),
                             triggers.tgname
                    """
                ),
                {
                    "schema_name": EXPECTED_SCHEMA,
                    "table_names": [table.name for table in DTS_STATE_TABLES],
                },
            ).all()
            actual_state_triggers = tuple(
                (
                    str(row[0]),
                    str(row[1]),
                    int(row[3]),
                    str(row[4]),
                    str(row[5]),
                    str(row[6]),
                    bool(row[7]),
                    int(row[8]),
                    str(row[9]),
                    bool(row[10]),
                )
                for row in state_trigger_rows
            )
            if (
                actual_state_triggers != EXPECTED_DTS_STATE_GUARD_TRIGGERS
                or any(str(row[2]) not in {"O", "A"} for row in state_trigger_rows)
            ):
                raise DtsIngestStoreError(
                    "DTS_TARGET_STATE_GUARD_TRIGGER_MISMATCH"
                )

            source_wide_columns = connection.execute(
                text(
                    """
                    SELECT relations.relname,
                           attributes.attname,
                           pg_catalog.format_type(
                               attributes.atttypid,
                               attributes.atttypmod
                           ),
                           attributes.attnotnull
                    FROM pg_catalog.pg_class relations
                    JOIN pg_catalog.pg_namespace namespaces
                      ON namespaces.oid = relations.relnamespace
                    JOIN pg_catalog.pg_attribute attributes
                      ON attributes.attrelid = relations.oid
                    WHERE namespaces.nspname = :schema_name
                      AND relations.relkind IN ('r', 'p')
                      AND relations.relname = ANY(
                          CAST(:table_names AS text[])
                      )
                      AND attributes.attnum > 0
                      AND NOT attributes.attisdropped
                    ORDER BY pg_catalog.array_position(
                                 CAST(:table_names AS text[]),
                                 relations.relname
                             ),
                             attributes.attnum
                    """
                ),
                {
                    "schema_name": EXPECTED_SCHEMA,
                    "table_names": [table.name for table in SOURCE_WIDE_TABLES],
                },
            ).all()
            actual_source_wide_columns = tuple(
                (
                    str(row[0]),
                    str(row[1]),
                    str(row[2]),
                    bool(row[3]),
                )
                for row in source_wide_columns
            )
            if actual_source_wide_columns != EXPECTED_SOURCE_WIDE_COLUMNS:
                raise DtsIngestStoreError(
                    "DTS_TARGET_SOURCE_WIDE_SCHEMA_MISMATCH"
                )

            trigger_rows = connection.execute(
                text(
                    """
                    SELECT relations.relname,
                           triggers.tgname,
                           triggers.tgenabled,
                           triggers.tgtype,
                           function_namespaces.nspname,
                           functions.proname,
                           pg_catalog.pg_get_function_identity_arguments(
                               functions.oid
                           ),
                           functions.prosecdef,
                           triggers.tgnargs,
                           triggers.tgattr::text,
                           triggers.tgqual IS NULL
                    FROM pg_catalog.pg_trigger triggers
                    JOIN pg_catalog.pg_class relations
                      ON relations.oid = triggers.tgrelid
                    JOIN pg_catalog.pg_namespace namespaces
                      ON namespaces.oid = relations.relnamespace
                    JOIN pg_catalog.pg_proc functions
                      ON functions.oid = triggers.tgfoid
                    JOIN pg_catalog.pg_namespace function_namespaces
                      ON function_namespaces.oid = functions.pronamespace
                    WHERE namespaces.nspname = :schema_name
                      AND relations.relname = ANY(
                          CAST(:table_names AS text[])
                      )
                      AND NOT triggers.tgisinternal
                    ORDER BY relations.relname, triggers.tgname
                    """
                ),
                {
                    "schema_name": EXPECTED_SCHEMA,
                    "table_names": [table.name for table in SOURCE_WIDE_TABLES],
                },
            ).all()
            actual_source_wide_triggers = tuple(
                (
                    str(row[0]),
                    str(row[1]),
                    int(row[3]),
                    str(row[4]),
                    str(row[5]),
                    str(row[6]),
                    bool(row[7]),
                    int(row[8]),
                    str(row[9]),
                    bool(row[10]),
                )
                for row in trigger_rows
            )
            if (
                actual_source_wide_triggers
                != EXPECTED_SOURCE_WIDE_TRIGGER_DEFINITIONS
                or any(str(row[2]) not in {"O", "A"} for row in trigger_rows)
            ):
                raise DtsIngestStoreError(
                    "DTS_TARGET_SOURCE_WIDE_TRIGGER_MISMATCH"
                )

            privacy_function_rows = connection.execute(
                text(
                    """
                    SELECT functions.proname,
                           pg_catalog.pg_get_function_identity_arguments(
                               functions.oid
                           ),
                           languages.lanname,
                           functions.provolatile,
                           functions.proisstrict,
                           functions.prosecdef,
                           COALESCE(functions.proconfig, ARRAY[]::text[]),
                           pg_catalog.encode(
                               pg_catalog.sha256(
                                   pg_catalog.convert_to(
                                       functions.prosrc,
                                       'UTF8'
                                   )
                               ),
                               'hex'
                           ) AS prosrc_sha256
                    FROM pg_catalog.pg_proc functions
                    JOIN pg_catalog.pg_namespace namespaces
                      ON namespaces.oid = functions.pronamespace
                    JOIN pg_catalog.pg_language languages
                      ON languages.oid = functions.prolang
                    WHERE namespaces.nspname = :schema_name
                      AND functions.proname = ANY(
                          CAST(:function_names AS text[])
                      )
                    ORDER BY pg_catalog.array_position(
                                 CAST(:function_names AS text[]),
                                 functions.proname
                             )
                    """
                ),
                {
                    "schema_name": EXPECTED_SCHEMA,
                    "function_names": [
                        definition[0]
                        for definition in EXPECTED_DOMESTIC_PRIVACY_FUNCTIONS
                    ],
                },
            ).all()
            actual_privacy_functions = tuple(
                (
                    str(row[0]),
                    str(row[1]),
                    str(row[2]),
                    str(row[3]),
                    bool(row[4]),
                    bool(row[5]),
                    tuple(str(value) for value in row[6]),
                    str(row[7]),
                )
                for row in privacy_function_rows
            )
            if actual_privacy_functions != EXPECTED_DOMESTIC_PRIVACY_FUNCTIONS:
                raise DtsIngestStoreError(
                    "DTS_TARGET_DOM_PRIVACY_FUNCTION_MISMATCH"
                )
        self._validated = True

    def resume_offset(
        self,
        *,
        source_region: str,
        topic: str,
        partition: int,
    ) -> int | None:
        checkpoint = self.resume_checkpoint(
            source_region=source_region,
            topic=topic,
            partition=partition,
        )
        return None if checkpoint is None else checkpoint.next_offset

    def resume_checkpoint(
        self,
        *,
        source_region: str,
        topic: str,
        partition: int,
    ) -> DtsResumeCheckpoint | None:
        """Read the exact DB offset and its DTS timestamp in one snapshot."""

        self._require_source_region(source_region)
        self._validate_runtime()
        table = DtsIngestCheckpointRecord.__table__
        with self.engine.connect() as connection:
            row = connection.execute(
                select(table.c.next_offset, table.c.source_timestamp).where(
                    table.c.source_region == source_region,
                    table.c.topic == topic,
                    table.c.partition_id == partition,
                )
            ).mappings().one_or_none()
        if row is None:
            return None
        next_offset = row.get("next_offset")
        source_timestamp = row.get("source_timestamp")
        if (
            isinstance(next_offset, bool)
            or not isinstance(next_offset, int)
            or next_offset < 0
            or isinstance(source_timestamp, bool)
            or not isinstance(source_timestamp, int)
            or source_timestamp < 0
        ):
            raise DtsIngestStoreError("DTS_DATABASE_CHECKPOINT_INVALID")
        return DtsResumeCheckpoint(
            next_offset=next_offset,
            source_timestamp=source_timestamp,
        )

    def validate_domestic_student_privacy_state(self) -> None:
        """Refuse startup if an earlier domestic run persisted a raw ID."""

        if self.source_region != "dom":
            return
        with self.engine.connect() as connection:
            violation = connection.execute(
                text(
                    """
                    WITH violations AS (
                        SELECT 'SOURCE_ROW' AS violation
                        FROM public.dts_source_rows rows
                        WHERE rows.source_region = 'dom'
                          AND (
                              rows.source_row ?| CAST(:raw_fields AS text[])
                              OR rows.dependency_keys ? 'student_ids'
                              OR EXISTS (
                                  SELECT 1
                                  FROM jsonb_array_elements_text(
                                      COALESCE(
                                          rows.dependency_keys
                                              -> 'student_subjects',
                                          '[]'::jsonb
                                      )
                                  ) subjects(value)
                                  WHERE subjects.value !~ :token_pattern
                              )
                              OR (
                                  rows.source_row ? 'student_token'
                                  AND rows.source_row ->> 'student_token'
                                      !~ :token_pattern
                              )
                          )
                        LIMIT 1
                    ), dirty_violation AS (
                        SELECT 'DIRTY_KEY' AS violation
                        FROM public.dts_dirty_keys dirty
                        WHERE dirty.last_source_region = 'dom'
                          AND dirty.key_type = 'TEACHER_STUDENT'
                          AND dirty.key_part_2 !~ :token_pattern
                        LIMIT 1
                    ), lesson_violation AS (
                        SELECT 'LESSON_WIDE' AS violation
                        FROM public.lesson_source_wide lessons
                        JOIN public.dts_source_rows appoints
                         ON appoints.source_region = 'dom'
                         AND appoints.source_table = 'dom_appoint'
                         AND appoints.source_row ->> 'id'
                             = lessons."课程id"
                        WHERE lessons."学员id" IS NOT NULL
                          AND lessons."学员id" !~ :token_pattern
                        LIMIT 1
                    ), provenance_violation AS (
                        SELECT 'LESSON_PROVENANCE' AS violation
                        FROM public.lesson_source_wide lessons
                        LEFT JOIN LATERAL (
                            SELECT
                                count(*) FILTER (
                                    WHERE appoints.source_region = 'dom'
                                ) AS dom_sources,
                                count(*) FILTER (
                                    WHERE appoints.source_region = 'ovs'
                                ) AS ovs_sources
                            FROM public.dts_source_rows appoints
                            WHERE appoints.source_table IN (
                                'dom_appoint',
                                'ovs_appoint'
                            )
                              AND appoints.source_row ->> 'id'
                                  = lessons."课程id"
                        ) provenance ON TRUE
                        WHERE (provenance.dom_sources > 0)::int
                            + (provenance.ovs_sources > 0)::int <> 1
                        LIMIT 1
                    )
                    SELECT violation FROM violations
                    UNION ALL
                    SELECT violation FROM dirty_violation
                    UNION ALL
                    SELECT violation FROM lesson_violation
                    UNION ALL
                    SELECT violation FROM provenance_violation
                    LIMIT 1
                    """
                ),
                {
                    "raw_fields": sorted(
                        {"s_id", "student_id", "stu_id", "user_id"}
                    ),
                    "token_pattern": DOMESTIC_STUDENT_TOKEN_SQL_PATTERN,
                },
            ).first()
        if violation is not None:
            raise DtsIngestStoreError(
                "DTS_DOM_STUDENT_PRIVACY_STATE_VIOLATION"
            )

    def validate_domestic_student_privacy_contract(
        self,
        *,
        key_fingerprint: str | None,
        topic: str,
        partition: int,
    ) -> None:
        """Register one DOM HMAC key commitment and reject silent rotation."""

        if self.source_region != "dom":
            return
        if (
            key_fingerprint is None
            or re.fullmatch(
                DOMESTIC_STUDENT_KEY_FINGERPRINT_PATTERN,
                key_fingerprint,
            )
            is None
        ):
            raise DtsIngestStoreError(
                "DTS_DOM_STUDENT_HMAC_FINGERPRINT_INVALID"
            )
        contract_key_data = {"id": DOMESTIC_STUDENT_CONTRACT_VERSION}
        contract_source_key = json.dumps(
            contract_key_data,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        contract_source_row = {
            "contract_version": DOMESTIC_STUDENT_CONTRACT_VERSION,
            "key_fingerprint": key_fingerprint,
        }
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO public.dts_source_rows (
                        source_region, source_table, source_key,
                        source_key_data, dependency_keys, source_row,
                        is_deleted, source_timestamp, last_record_id,
                        source_position, last_topic, last_partition,
                        last_offset, row_version
                    ) VALUES (
                        'dom', :source_table, :source_key,
                        CAST(:source_key_data AS jsonb), '{}'::jsonb,
                        CAST(:source_row AS jsonb), FALSE, 0, 0,
                        'runtime_contract', :topic, :partition, 0, 1
                    )
                    ON CONFLICT (source_region, source_table, source_key)
                    DO NOTHING
                    """
                ),
                {
                    "source_table": DOMESTIC_STUDENT_CONTRACT_SOURCE_TABLE,
                    "source_key": contract_source_key,
                    "source_key_data": json.dumps(
                        contract_key_data,
                        ensure_ascii=True,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    "source_row": json.dumps(
                        contract_source_row,
                        ensure_ascii=True,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    "topic": topic,
                    "partition": partition,
                },
            )
            stored_contract = connection.execute(
                text(
                    """
                    SELECT source_row ->> 'contract_version'
                               AS contract_version,
                           source_row ->> 'key_fingerprint'
                               AS key_fingerprint
                    FROM public.dts_source_rows
                    WHERE source_region = 'dom'
                      AND source_table = :source_table
                      AND source_key = :source_key
                    FOR UPDATE
                    """
                ),
                {
                    "source_table": DOMESTIC_STUDENT_CONTRACT_SOURCE_TABLE,
                    "source_key": contract_source_key,
                },
            ).mappings().one_or_none()
        if stored_contract is None or (
            stored_contract.get("contract_version"),
            stored_contract.get("key_fingerprint"),
        ) != (DOMESTIC_STUDENT_CONTRACT_VERSION, key_fingerprint):
            raise DtsIngestStoreError(
                "DTS_DOM_STUDENT_HMAC_KEY_FINGERPRINT_MISMATCH"
            )

    def apply(
        self,
        event: DtsChangeEvent,
        dirty_keys: DirtyKeySet,
        appoint_candidate: AppointProjectionCandidate | None,
    ) -> bool:
        del appoint_candidate  # Projection starts from the durable dirty queue.
        self._require_source_region(event.source_region)
        self._validate_runtime()
        with self.engine.begin() as connection:
            return self._apply_transaction(connection, event, dirty_keys)

    def apply_batch(
        self,
        items: Sequence[
            tuple[
                DtsChangeEvent,
                DirtyKeySet,
                AppointProjectionCandidate | None,
            ]
        ],
    ) -> tuple[bool, ...]:
        """Persist one ordered stream batch in one PostgreSQL transaction.

        The event ledger is inserted with one statement and the authoritative
        checkpoint is updated once, after every new event in the batch has
        been persisted.  Existing replay receipts are returned as duplicates;
        a gap or a ledger/checkpoint disagreement rolls back the whole batch.
        """

        if not items:
            return ()
        prepared = tuple((event, dirty_keys) for event, dirty_keys, _ in items)
        first_event = prepared[0][0]
        stream_identity = (
            first_event.source_region,
            first_event.topic,
            first_event.partition,
        )
        previous_offset: int | None = None
        for event, _dirty_keys in prepared:
            self._require_source_region(event.source_region)
            # Protect every domestic event before any value from this batch
            # can become an overseas SQL bind parameter.
            assert_domestic_event_protected(event)
            if (
                event.source_region,
                event.topic,
                event.partition,
            ) != stream_identity:
                raise DtsIngestStoreError("DTS_DATABASE_BATCH_STREAM_MISMATCH")
            if previous_offset is not None and event.offset <= previous_offset:
                raise DtsIngestStoreError(
                    "DTS_DATABASE_BATCH_OFFSET_ORDER_INVALID"
                )
            previous_offset = event.offset
        self._validate_runtime()
        with self.engine.begin() as connection:
            return self._apply_batch_transaction(connection, prepared)

    def _require_source_region(self, source_region: str) -> None:
        if self.source_region is not None and source_region != self.source_region:
            raise DtsIngestStoreError("DTS_SOURCE_REGION_MISMATCH")

    @staticmethod
    def _stream_identity(event: DtsChangeEvent) -> str:
        return json.dumps(
            [event.source_region, event.topic, event.partition],
            ensure_ascii=True,
            separators=(",", ":"),
        )

    def _lock_stream_checkpoint(
        self,
        connection: Any,
        event: DtsChangeEvent,
    ) -> int | None:
        checkpoint = DtsIngestCheckpointRecord.__table__
        connection.execute(
            text(
                "SELECT pg_catalog.pg_advisory_xact_lock("
                "pg_catalog.hashtextextended(:stream_identity, 0)"
                ")"
            ),
            {"stream_identity": self._stream_identity(event)},
        )
        return connection.execute(
            select(checkpoint.c.next_offset)
            .where(
                checkpoint.c.source_region == event.source_region,
                checkpoint.c.topic == event.topic,
                checkpoint.c.partition_id == event.partition,
            )
            .with_for_update()
        ).scalar_one_or_none()

    def _plan_event_persistence(
        self,
        connection: Any,
        event: DtsChangeEvent,
        dirty_keys: DirtyKeySet,
    ) -> _EventPersistencePlan:
        source_table = DtsSourceRowRecord.__table__
        ignored = dirty_keys.ignored_reason is not None
        row_state = _source_row_state(event)
        source_write_state: tuple[
            str,
            dict[str, str],
            dict[str, list[str]],
            dict[str, Any],
            bool,
        ] | None = None
        key_row_values = set(_dirty_key_rows(dirty_keys))
        if not ignored and row_state is not None:
            source_key, key_data, _incoming_keys, incoming_row, is_deleted = (
                row_state
            )
            existing = connection.execute(
                select(source_table.c.source_row, source_table.c.is_deleted)
                .where(
                    source_table.c.source_region == event.source_region,
                    source_table.c.source_table == event.table_name,
                    source_table.c.source_key == source_key,
                )
                .with_for_update()
            ).mappings().one_or_none()
            existing_row: dict[str, Any] = {}
            if existing is not None:
                raw_existing_row = existing["source_row"]
                if not isinstance(raw_existing_row, Mapping):
                    raise DtsIngestStoreError("DTS_STORED_SOURCE_ROW_INVALID")
                existing_row = dict(raw_existing_row)

            suffix = source_table_suffix(event)
            if suffix is None:  # pragma: no cover - guarded by source state
                raise DtsIngestStoreError("DTS_SOURCE_TABLE_PROFILE_MISSING")
            old_dependency_keys = _dependency_keys(suffix, existing_row)
            merged_row = {**existing_row, **incoming_row}
            merged_dependency_keys = _dependency_keys(suffix, merged_row)
            key_row_values.update(
                _dependency_dirty_key_rows(old_dependency_keys)
            )
            key_row_values.update(
                _dependency_dirty_key_rows(merged_dependency_keys)
            )
            scope_changed = any(
                existing_row.get(field_name) != merged_row.get(field_name)
                for field_name in TEACHER_COURSE_SCOPE_FIELDS
            )
            should_fanout_teacher_courses = (
                event.source_region == "dom"
                and suffix == "teacher"
                and event.operation in {"INSERT", "UPDATE"}
                and (
                    existing is None
                    or bool(existing.get("is_deleted", False))
                    or scope_changed
                )
            )
            if should_fanout_teacher_courses:
                teacher_ids = merged_dependency_keys["teacher_ids"]
                if teacher_ids:
                    dependency = {"teacher_ids": [teacher_ids[0]]}
                    appoint_dependencies = connection.execute(
                        select(source_table.c.dependency_keys).where(
                            or_(
                                and_(
                                    source_table.c.source_region == "dom",
                                    source_table.c.source_table == "dom_appoint",
                                ),
                                and_(
                                    source_table.c.source_region == "ovs",
                                    source_table.c.source_table == "ovs_appoint",
                                ),
                            ),
                            source_table.c.is_deleted.is_(False),
                            source_table.c.dependency_keys.op("@>")(
                                cast(dependency, JSONB)
                            ),
                        )
                    ).scalars().all()
                    for appoint_dependency in appoint_dependencies:
                        if not isinstance(appoint_dependency, Mapping):
                            raise DtsIngestStoreError(
                                "DTS_STORED_DEPENDENCY_KEYS_INVALID"
                            )
                        key_row_values.update(
                            (
                                "COURSE",
                                str(course_id).strip(),
                                "",
                            )
                            for course_id in appoint_dependency.get(
                                "course_ids", []
                            )
                            if str(course_id).strip()
                        )
            source_write_state = (
                source_key,
                key_data,
                merged_dependency_keys,
                merged_row,
                is_deleted,
            )

        return _EventPersistencePlan(
            ignored=ignored,
            source_write_state=source_write_state,
            key_rows=tuple(sorted(key_row_values)),
            issue_codes=list(
                dict.fromkeys(
                    (
                        *dirty_keys.issues,
                        *((dirty_keys.ignored_reason,) if ignored else ()),
                    )
                )
            ),
        )

    @staticmethod
    def _receipt_values(
        event: DtsChangeEvent,
        plan: _EventPersistencePlan,
    ) -> dict[str, Any]:
        return {
            "source_region": event.source_region,
            "topic": event.topic,
            "partition_id": event.partition,
            "offset_value": event.offset,
            "record_id": event.record_id,
            "source_timestamp": event.source_timestamp,
            "source_txid": event.source_txid,
            "source_position": event.source_position,
            "operation": event.operation,
            "source_database": event.database_name,
            "source_schema": event.schema_name,
            "source_table": event.table_name,
            "route_status": "IGNORED" if plan.ignored else "PROCESSED",
            "dirty_key_count": len(plan.key_rows),
            "issue_codes": plan.issue_codes,
        }

    def _write_event_state(
        self,
        connection: Any,
        event: DtsChangeEvent,
        dirty_keys: DirtyKeySet,
        plan: _EventPersistencePlan,
    ) -> None:
        source_table = DtsSourceRowRecord.__table__
        dirty_table = DtsDirtyKeyRecord.__table__
        if plan.source_write_state is not None:
            source_key, key_data, dependency_keys, source_row, is_deleted = (
                plan.source_write_state
            )
            source_insert = insert(source_table).values(
                source_region=event.source_region,
                source_table=event.table_name,
                source_key=source_key,
                source_key_data=key_data,
                dependency_keys=dependency_keys,
                source_row=source_row,
                is_deleted=is_deleted,
                source_timestamp=event.source_timestamp,
                last_record_id=event.record_id,
                source_position=event.source_position,
                last_topic=event.topic,
                last_partition=event.partition,
                last_offset=event.offset,
                row_version=1,
            )
            excluded = source_insert.excluded
            newer = or_(
                excluded.source_timestamp > source_table.c.source_timestamp,
                and_(
                    excluded.source_timestamp == source_table.c.source_timestamp,
                    excluded.last_record_id > source_table.c.last_record_id,
                ),
                and_(
                    excluded.source_timestamp == source_table.c.source_timestamp,
                    excluded.last_record_id == source_table.c.last_record_id,
                    excluded.last_offset > source_table.c.last_offset,
                ),
            )
            connection.execute(
                source_insert.on_conflict_do_update(
                    index_elements=[
                        source_table.c.source_region,
                        source_table.c.source_table,
                        source_table.c.source_key,
                    ],
                    set_={
                        "source_key_data": excluded.source_key_data,
                        "dependency_keys": excluded.dependency_keys,
                        "source_row": excluded.source_row,
                        "is_deleted": excluded.is_deleted,
                        "source_timestamp": excluded.source_timestamp,
                        "last_record_id": excluded.last_record_id,
                        "source_position": excluded.source_position,
                        "last_topic": excluded.last_topic,
                        "last_partition": excluded.last_partition,
                        "last_offset": excluded.last_offset,
                        "row_version": source_table.c.row_version + 1,
                        "updated_at": text("clock_timestamp()"),
                    },
                    where=newer,
                )
            )

        for batch_start in range(
            0, len(plan.key_rows), DIRTY_KEY_UPSERT_BATCH_SIZE
        ):
            batch = plan.key_rows[
                batch_start : batch_start + DIRTY_KEY_UPSERT_BATCH_SIZE
            ]
            dirty_insert = insert(dirty_table).values(
                [
                    {
                        "key_type": key_type,
                        "key_part_1": key_part_1,
                        "key_part_2": key_part_2,
                        "status": "PENDING",
                        "pending_event_count": 1,
                        "attempt_count": 0,
                        "last_source_region": event.source_region,
                        "last_source_table": event.table_name,
                        "last_topic": event.topic,
                        "last_partition": event.partition,
                        "last_offset": event.offset,
                        "issue_codes": list(dirty_keys.issues),
                        "row_version": 1,
                    }
                    for key_type, key_part_1, key_part_2 in batch
                ]
            )
            excluded = dirty_insert.excluded
            connection.execute(
                dirty_insert.on_conflict_do_update(
                    index_elements=[
                        dirty_table.c.key_type,
                        dirty_table.c.key_part_1,
                        dirty_table.c.key_part_2,
                    ],
                    set_={
                        "status": "PENDING",
                        "pending_event_count": (
                            dirty_table.c.pending_event_count + 1
                        ),
                        "attempt_count": 0,
                        "last_source_region": excluded.last_source_region,
                        "last_source_table": excluded.last_source_table,
                        "last_topic": excluded.last_topic,
                        "last_partition": excluded.last_partition,
                        "last_offset": excluded.last_offset,
                        "issue_codes": dirty_table.c.issue_codes.op("||")(
                            excluded.issue_codes
                        ),
                        "last_error_code": None,
                        "next_attempt_at": None,
                        "claimed_at": None,
                        "claimed_by": None,
                        "row_version": dirty_table.c.row_version + 1,
                        "last_seen_at": text("clock_timestamp()"),
                    },
                )
            )

    @staticmethod
    def _write_checkpoint(
        connection: Any,
        event: DtsChangeEvent,
    ) -> None:
        checkpoint = DtsIngestCheckpointRecord.__table__
        checkpoint_insert = insert(checkpoint).values(
            source_region=event.source_region,
            topic=event.topic,
            partition_id=event.partition,
            next_offset=event.offset + 1,
            source_timestamp=event.source_timestamp,
            source_position=event.source_position,
        )
        excluded = checkpoint_insert.excluded
        connection.execute(
            checkpoint_insert.on_conflict_do_update(
                index_elements=[
                    checkpoint.c.source_region,
                    checkpoint.c.topic,
                    checkpoint.c.partition_id,
                ],
                set_={
                    "next_offset": excluded.next_offset,
                    "source_timestamp": excluded.source_timestamp,
                    "source_position": excluded.source_position,
                    "updated_at": text("clock_timestamp()"),
                },
            )
        )

    def _plan_batch_state(
        self,
        connection: Any,
        prepared: Sequence[tuple[DtsChangeEvent, DirtyKeySet]],
    ) -> tuple[
        tuple[_EventPersistencePlan, ...],
        tuple[dict[str, Any], ...],
    ]:
        """Resolve source state once and fold ordered changes in memory."""

        source_table = DtsSourceRowRecord.__table__
        row_states: list[
            tuple[
                str,
                dict[str, str],
                dict[str, list[str]],
                dict[str, Any],
                bool,
            ]
            | None
        ] = []
        source_identities: set[tuple[str, str, str]] = set()
        for event, dirty_keys in prepared:
            row_state = _source_row_state(event)
            row_states.append(row_state)
            if row_state is not None and dirty_keys.ignored_reason is None:
                source_key = row_state[0]
                if event.table_name is None:  # pragma: no cover - guarded
                    raise DtsIngestStoreError(
                        "DTS_SOURCE_TABLE_PROFILE_MISSING"
                    )
                source_identities.add(
                    (event.source_region, event.table_name, source_key)
                )

        stored_states: dict[tuple[str, str, str], dict[str, Any]] = {}
        if source_identities:
            identity_columns = tuple_(
                source_table.c.source_region,
                source_table.c.source_table,
                source_table.c.source_key,
            )
            stored_rows = connection.execute(
                select(
                    source_table.c.source_region,
                    source_table.c.source_table,
                    source_table.c.source_key,
                    source_table.c.source_row,
                    source_table.c.is_deleted,
                    source_table.c.source_timestamp,
                    source_table.c.last_record_id,
                    source_table.c.last_offset,
                )
                .where(identity_columns.in_(sorted(source_identities)))
                .with_for_update()
            ).mappings().all()
            for stored in stored_rows:
                raw_source_row = stored.get("source_row")
                raw_is_deleted = stored.get("is_deleted")
                source_timestamp = stored.get("source_timestamp")
                last_record_id = stored.get("last_record_id")
                last_offset = stored.get("last_offset")
                if (
                    not isinstance(raw_source_row, Mapping)
                    or not isinstance(raw_is_deleted, bool)
                    or isinstance(source_timestamp, bool)
                    or not isinstance(source_timestamp, int)
                    or isinstance(last_record_id, bool)
                    or not isinstance(last_record_id, int)
                    or isinstance(last_offset, bool)
                    or not isinstance(last_offset, int)
                ):
                    raise DtsIngestStoreError(
                        "DTS_STORED_SOURCE_ROW_INVALID"
                    )
                identity = (
                    str(stored["source_region"]),
                    str(stored["source_table"]),
                    str(stored["source_key"]),
                )
                stored_states[identity] = {
                    "exists": True,
                    "source_row": dict(raw_source_row),
                    "is_deleted": raw_is_deleted,
                    "version": (
                        source_timestamp,
                        last_record_id,
                        last_offset,
                    ),
                    "dependency_keys": _dependency_keys(
                        identity[1].removeprefix("dom_").removeprefix("ovs_"),
                        raw_source_row,
                    ),
                }

        source_writes: dict[tuple[str, str, str], dict[str, Any]] = {}
        plan_drafts: list[dict[str, Any]] = []
        fanout_plan_indexes: dict[str, set[int]] = {}
        for index, ((event, dirty_keys), row_state) in enumerate(
            zip(prepared, row_states)
        ):
            ignored = dirty_keys.ignored_reason is not None
            key_row_values = set(_dirty_key_rows(dirty_keys))
            if not ignored and row_state is not None:
                (
                    source_key,
                    key_data,
                    _incoming_keys,
                    incoming_row,
                    is_deleted,
                ) = row_state
                if event.table_name is None:  # pragma: no cover - guarded
                    raise DtsIngestStoreError(
                        "DTS_SOURCE_TABLE_PROFILE_MISSING"
                    )
                identity = (
                    event.source_region,
                    event.table_name,
                    source_key,
                )
                state = stored_states.get(identity)
                state_exists = state is not None and bool(state["exists"])
                if state is None:
                    state = {
                        "exists": False,
                        "source_row": {},
                        "is_deleted": False,
                        "version": (-1, -1, -1),
                        "dependency_keys": {
                            "course_ids": [],
                            "teacher_ids": [],
                            "student_subjects": [],
                            "label_ids": [],
                            "category_ids": [],
                        },
                    }
                    stored_states[identity] = state
                existing_row = dict(state["source_row"])
                suffix = source_table_suffix(event)
                if suffix is None:  # pragma: no cover - guarded by row state
                    raise DtsIngestStoreError(
                        "DTS_SOURCE_TABLE_PROFILE_MISSING"
                    )
                old_dependency_keys = _dependency_keys(suffix, existing_row)
                merged_row = {**existing_row, **incoming_row}
                merged_dependency_keys = _dependency_keys(suffix, merged_row)
                key_row_values.update(
                    _dependency_dirty_key_rows(old_dependency_keys)
                )
                key_row_values.update(
                    _dependency_dirty_key_rows(merged_dependency_keys)
                )
                scope_changed = any(
                    existing_row.get(field_name)
                    != merged_row.get(field_name)
                    for field_name in TEACHER_COURSE_SCOPE_FIELDS
                )
                should_fanout_teacher_courses = (
                    event.source_region == "dom"
                    and suffix == "teacher"
                    and event.operation in {"INSERT", "UPDATE"}
                    and (
                        not state_exists
                        or bool(state["is_deleted"])
                        or scope_changed
                    )
                )
                if should_fanout_teacher_courses:
                    for teacher_id in merged_dependency_keys["teacher_ids"]:
                        fanout_plan_indexes.setdefault(
                            teacher_id, set()
                        ).add(index)

                incoming_version = (
                    event.source_timestamp,
                    event.record_id,
                    event.offset,
                )
                if not state_exists or incoming_version > state["version"]:
                    state.update(
                        {
                            "exists": True,
                            "source_row": merged_row,
                            "is_deleted": is_deleted,
                            "version": incoming_version,
                            "dependency_keys": merged_dependency_keys,
                        }
                    )
                    source_writes[identity] = {
                        "source_region": event.source_region,
                        "source_table": event.table_name,
                        "source_key": source_key,
                        "source_key_data": key_data,
                        "dependency_keys": merged_dependency_keys,
                        "source_row": merged_row,
                        "is_deleted": is_deleted,
                        "source_timestamp": event.source_timestamp,
                        "last_record_id": event.record_id,
                        "source_position": event.source_position,
                        "last_topic": event.topic,
                        "last_partition": event.partition,
                        "last_offset": event.offset,
                        "row_version": 1,
                    }

            plan_drafts.append(
                {
                    "ignored": ignored,
                    "key_rows": key_row_values,
                    "issue_codes": list(
                        dict.fromkeys(
                            (
                                *dirty_keys.issues,
                                *((
                                    dirty_keys.ignored_reason,
                                ) if ignored else ()),
                            )
                        )
                    ),
                }
            )

        if fanout_plan_indexes:
            teacher_ids = sorted(fanout_plan_indexes)
            appoint_dependencies = connection.execute(
                text(
                    """
                    SELECT dependency_keys
                    FROM public.dts_source_rows
                    WHERE (
                        (source_region = 'dom'
                         AND source_table = 'dom_appoint')
                        OR
                        (source_region = 'ovs'
                         AND source_table = 'ovs_appoint')
                    )
                      AND is_deleted IS FALSE
                      AND (dependency_keys -> 'teacher_ids')
                          ?| CAST(:teacher_ids AS text[])
                    """
                ),
                {"teacher_ids": teacher_ids},
            ).scalars().all()
            appoint_dependencies.extend(
                state["dependency_keys"]
                for identity, state in stored_states.items()
                if identity[1] in {"dom_appoint", "ovs_appoint"}
                and bool(state["exists"])
                and not bool(state["is_deleted"])
            )
            for dependency_keys in appoint_dependencies:
                if not isinstance(dependency_keys, Mapping):
                    raise DtsIngestStoreError(
                        "DTS_STORED_DEPENDENCY_KEYS_INVALID"
                    )
                course_ids = {
                    str(value).strip()
                    for value in dependency_keys.get("course_ids", [])
                    if str(value).strip()
                }
                for teacher_id in dependency_keys.get("teacher_ids", []):
                    for plan_index in fanout_plan_indexes.get(
                        str(teacher_id).strip(), set()
                    ):
                        plan_drafts[plan_index]["key_rows"].update(
                            ("COURSE", course_id, "")
                            for course_id in course_ids
                        )

        plans = tuple(
            _EventPersistencePlan(
                ignored=bool(draft["ignored"]),
                source_write_state=None,
                key_rows=tuple(sorted(draft["key_rows"])),
                issue_codes=list(draft["issue_codes"]),
            )
            for draft in plan_drafts
        )
        return plans, tuple(source_writes.values())

    @staticmethod
    def _write_batch_source_rows(
        connection: Any,
        source_rows: Sequence[dict[str, Any]],
    ) -> None:
        source_table = DtsSourceRowRecord.__table__
        for batch_start in range(
            0, len(source_rows), SOURCE_ROW_UPSERT_BATCH_SIZE
        ):
            batch = source_rows[
                batch_start : batch_start + SOURCE_ROW_UPSERT_BATCH_SIZE
            ]
            source_insert = insert(source_table).values(list(batch))
            excluded = source_insert.excluded
            newer = or_(
                excluded.source_timestamp > source_table.c.source_timestamp,
                and_(
                    excluded.source_timestamp == source_table.c.source_timestamp,
                    excluded.last_record_id > source_table.c.last_record_id,
                ),
                and_(
                    excluded.source_timestamp == source_table.c.source_timestamp,
                    excluded.last_record_id == source_table.c.last_record_id,
                    excluded.last_offset > source_table.c.last_offset,
                ),
            )
            connection.execute(
                source_insert.on_conflict_do_update(
                    index_elements=[
                        source_table.c.source_region,
                        source_table.c.source_table,
                        source_table.c.source_key,
                    ],
                    set_={
                        "source_key_data": excluded.source_key_data,
                        "dependency_keys": excluded.dependency_keys,
                        "source_row": excluded.source_row,
                        "is_deleted": excluded.is_deleted,
                        "source_timestamp": excluded.source_timestamp,
                        "last_record_id": excluded.last_record_id,
                        "source_position": excluded.source_position,
                        "last_topic": excluded.last_topic,
                        "last_partition": excluded.last_partition,
                        "last_offset": excluded.last_offset,
                        "row_version": source_table.c.row_version + 1,
                        "updated_at": text("clock_timestamp()"),
                    },
                    where=newer,
                )
            )

    @staticmethod
    def _write_batch_dirty_keys(
        connection: Any,
        prepared: Sequence[tuple[DtsChangeEvent, DirtyKeySet]],
        plans: Sequence[_EventPersistencePlan],
    ) -> None:
        dirty_table = DtsDirtyKeyRecord.__table__
        aggregate: dict[tuple[str, str, str], dict[str, Any]] = {}
        for (event, dirty_keys), plan in zip(prepared, plans):
            for key_type, key_part_1, key_part_2 in plan.key_rows:
                identity = (key_type, key_part_1, key_part_2)
                row = aggregate.get(identity)
                if row is None:
                    row = {
                        "key_type": key_type,
                        "key_part_1": key_part_1,
                        "key_part_2": key_part_2,
                        "status": "PENDING",
                        "pending_event_count": 0,
                        "attempt_count": 0,
                        "last_source_region": event.source_region,
                        "last_source_table": event.table_name,
                        "last_topic": event.topic,
                        "last_partition": event.partition,
                        "last_offset": event.offset,
                        "issue_codes": [],
                        "row_version": 1,
                    }
                    aggregate[identity] = row
                row["pending_event_count"] += 1
                row["last_source_region"] = event.source_region
                row["last_source_table"] = event.table_name
                row["last_topic"] = event.topic
                row["last_partition"] = event.partition
                row["last_offset"] = event.offset
                row["issue_codes"].extend(dirty_keys.issues)

        rows = tuple(aggregate.values())
        for batch_start in range(
            0, len(rows), DIRTY_KEY_UPSERT_BATCH_SIZE
        ):
            batch = rows[
                batch_start : batch_start + DIRTY_KEY_UPSERT_BATCH_SIZE
            ]
            dirty_insert = insert(dirty_table).values(list(batch))
            excluded = dirty_insert.excluded
            connection.execute(
                dirty_insert.on_conflict_do_update(
                    index_elements=[
                        dirty_table.c.key_type,
                        dirty_table.c.key_part_1,
                        dirty_table.c.key_part_2,
                    ],
                    set_={
                        "status": "PENDING",
                        "pending_event_count": (
                            dirty_table.c.pending_event_count
                            + excluded.pending_event_count
                        ),
                        "attempt_count": 0,
                        "last_source_region": excluded.last_source_region,
                        "last_source_table": excluded.last_source_table,
                        "last_topic": excluded.last_topic,
                        "last_partition": excluded.last_partition,
                        "last_offset": excluded.last_offset,
                        "issue_codes": dirty_table.c.issue_codes.op("||")(
                            excluded.issue_codes
                        ),
                        "last_error_code": None,
                        "next_attempt_at": None,
                        "claimed_at": None,
                        "claimed_by": None,
                        "row_version": dirty_table.c.row_version + 1,
                        "last_seen_at": text("clock_timestamp()"),
                    },
                )
            )

    def _apply_batch_transaction(
        self,
        connection: Any,
        prepared: Sequence[tuple[DtsChangeEvent, DirtyKeySet]],
    ) -> tuple[bool, ...]:
        first_event = prepared[0][0]
        event_table = DtsIngestEventRecord.__table__
        current_next_offset = self._lock_stream_checkpoint(
            connection, first_event
        )
        offsets = [event.offset for event, _dirty_keys in prepared]
        existing_offsets = {
            int(value)
            for value in connection.execute(
                select(event_table.c.offset_value).where(
                    event_table.c.source_region == first_event.source_region,
                    event_table.c.topic == first_event.topic,
                    event_table.c.partition_id == first_event.partition,
                    event_table.c.offset_value.in_(offsets),
                )
            ).scalars().all()
        }

        duplicate_flags: list[bool] = []
        expected_next_offset = current_next_offset
        new_offsets: set[int] = set()
        for event, _dirty_keys in prepared:
            if event.offset in existing_offsets:
                if (
                    expected_next_offset is None
                    or expected_next_offset < event.offset + 1
                ):
                    raise DtsIngestStoreError(
                        "DTS_LEDGER_CHECKPOINT_INCONSISTENT"
                    )
                duplicate_flags.append(True)
                continue
            if (
                expected_next_offset is not None
                and event.offset != expected_next_offset
            ):
                raise DtsIngestStoreError(
                    "DTS_DATABASE_OFFSET_NOT_CONTIGUOUS"
                )
            duplicate_flags.append(False)
            new_offsets.add(event.offset)
            expected_next_offset = event.offset + 1

        new_prepared = tuple(
            item
            for item, duplicate in zip(prepared, duplicate_flags)
            if not duplicate
        )
        receipt_rows: list[dict[str, Any]] = []
        last_advanced_event: DtsChangeEvent | None = None
        if new_prepared:
            plans, source_rows = self._plan_batch_state(
                connection, new_prepared
            )
            self._write_batch_source_rows(connection, source_rows)
            self._write_batch_dirty_keys(connection, new_prepared, plans)
            receipt_rows = [
                self._receipt_values(event, plan)
                for (event, _dirty_keys), plan in zip(new_prepared, plans)
            ]
            last_advanced_event = new_prepared[-1][0]

        if receipt_rows:
            receipt = insert(event_table).values(receipt_rows).on_conflict_do_nothing(
                index_elements=[
                    event_table.c.source_region,
                    event_table.c.topic,
                    event_table.c.partition_id,
                    event_table.c.offset_value,
                ]
            ).returning(event_table.c.offset_value)
            inserted_offsets = {
                int(value)
                for value in connection.execute(receipt).scalars().all()
            }
            if inserted_offsets != new_offsets:
                raise DtsIngestStoreError(
                    "DTS_LEDGER_CHECKPOINT_INCONSISTENT"
                )
        if last_advanced_event is not None:
            self._write_checkpoint(connection, last_advanced_event)
        return tuple(duplicate_flags)

    def _apply_transaction(
        self,
        connection: Any,
        event: DtsChangeEvent,
        dirty_keys: DirtyKeySet,
    ) -> bool:
        # This guard runs before any event value can become an overseas SQL
        # bind parameter.  The database remains a second line of defense, not
        # the first place where a domestic raw student ID is observed.
        assert_domestic_event_protected(event)
        checkpoint = DtsIngestCheckpointRecord.__table__
        event_table = DtsIngestEventRecord.__table__
        source_table = DtsSourceRowRecord.__table__
        dirty_table = DtsDirtyKeyRecord.__table__

        stream_identity = json.dumps(
            [event.source_region, event.topic, event.partition],
            ensure_ascii=True,
            separators=(",", ":"),
        )
        connection.execute(
            text(
                "SELECT pg_catalog.pg_advisory_xact_lock("
                "pg_catalog.hashtextextended(:stream_identity, 0)"
                ")"
            ),
            {"stream_identity": stream_identity},
        )
        current_next_offset = connection.execute(
            select(checkpoint.c.next_offset)
            .where(
                checkpoint.c.source_region == event.source_region,
                checkpoint.c.topic == event.topic,
                checkpoint.c.partition_id == event.partition,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if (
            current_next_offset is not None
            and event.offset > current_next_offset
        ):
            raise DtsIngestStoreError("DTS_DATABASE_OFFSET_NOT_CONTIGUOUS")

        ignored = dirty_keys.ignored_reason is not None
        row_state = _source_row_state(event)
        source_write_state: tuple[
            str,
            dict[str, str],
            dict[str, list[str]],
            dict[str, Any],
            bool,
        ] | None = None
        key_row_values = set(_dirty_key_rows(dirty_keys))
        if not ignored and row_state is not None:
            source_key, key_data, _incoming_keys, incoming_row, is_deleted = row_state
            existing = connection.execute(
                select(source_table.c.source_row, source_table.c.is_deleted)
                .where(
                    source_table.c.source_region == event.source_region,
                    source_table.c.source_table == event.table_name,
                    source_table.c.source_key == source_key,
                )
                .with_for_update()
            ).mappings().one_or_none()
            existing_row: dict[str, Any] = {}
            if existing is not None:
                raw_existing_row = existing["source_row"]
                if not isinstance(raw_existing_row, Mapping):
                    raise DtsIngestStoreError("DTS_STORED_SOURCE_ROW_INVALID")
                existing_row = dict(raw_existing_row)

            suffix = source_table_suffix(event)
            if suffix is None:  # pragma: no cover - guarded by _source_row_state
                raise DtsIngestStoreError("DTS_SOURCE_TABLE_PROFILE_MISSING")
            old_dependency_keys = _dependency_keys(suffix, existing_row)
            merged_row = {**existing_row, **incoming_row}
            merged_dependency_keys = _dependency_keys(suffix, merged_row)
            key_row_values.update(
                _dependency_dirty_key_rows(old_dependency_keys)
            )
            key_row_values.update(
                _dependency_dirty_key_rows(merged_dependency_keys)
            )
            scope_changed = any(
                existing_row.get(field_name) != merged_row.get(field_name)
                for field_name in TEACHER_COURSE_SCOPE_FIELDS
            )
            should_fanout_teacher_courses = (
                event.source_region == "dom"
                and suffix == "teacher"
                and event.operation in {"INSERT", "UPDATE"}
                and (
                    existing is None
                    or bool(existing.get("is_deleted", False))
                    or scope_changed
                )
            )
            if should_fanout_teacher_courses:
                teacher_ids = merged_dependency_keys["teacher_ids"]
                if teacher_ids:
                    dependency = {"teacher_ids": [teacher_ids[0]]}
                    appoint_dependencies = connection.execute(
                        select(source_table.c.dependency_keys).where(
                            or_(
                                and_(
                                    source_table.c.source_region == "dom",
                                    source_table.c.source_table == "dom_appoint",
                                ),
                                and_(
                                    source_table.c.source_region == "ovs",
                                    source_table.c.source_table == "ovs_appoint",
                                ),
                            ),
                            source_table.c.is_deleted.is_(False),
                            source_table.c.dependency_keys.op("@>")(
                                cast(dependency, JSONB)
                            ),
                        )
                    ).scalars().all()
                    for appoint_dependency in appoint_dependencies:
                        if not isinstance(appoint_dependency, Mapping):
                            raise DtsIngestStoreError(
                                "DTS_STORED_DEPENDENCY_KEYS_INVALID"
                            )
                        key_row_values.update(
                            (
                                "COURSE",
                                str(course_id).strip(),
                                "",
                            )
                            for course_id in appoint_dependency.get(
                                "course_ids", []
                            )
                            if str(course_id).strip()
                        )
            source_write_state = (
                source_key,
                key_data,
                merged_dependency_keys,
                merged_row,
                is_deleted,
            )

        key_rows = tuple(sorted(key_row_values))
        issue_codes = list(dict.fromkeys(
            (*dirty_keys.issues, *((dirty_keys.ignored_reason,) if ignored else ()))
        ))
        receipt = insert(event_table).values(
            source_region=event.source_region,
            topic=event.topic,
            partition_id=event.partition,
            offset_value=event.offset,
            record_id=event.record_id,
            source_timestamp=event.source_timestamp,
            source_txid=event.source_txid,
            source_position=event.source_position,
            operation=event.operation,
            source_database=event.database_name,
            source_schema=event.schema_name,
            source_table=event.table_name,
            route_status="IGNORED" if ignored else "PROCESSED",
            dirty_key_count=len(key_rows),
            issue_codes=issue_codes,
        ).on_conflict_do_nothing(
            index_elements=[
                event_table.c.source_region,
                event_table.c.topic,
                event_table.c.partition_id,
                event_table.c.offset_value,
            ]
        ).returning(event_table.c.offset_value)
        # psycopg 3 does not guarantee a meaningful rowcount for this INSERT;
        # SQLAlchemy may expose -1 for both an inserted row and a conflict.
        # RETURNING is the database-authored proof that this transaction
        # inserted the receipt, while no row means the receipt already exists.
        inserted = connection.execute(receipt).scalar_one_or_none() is not None
        if not inserted:
            if current_next_offset is None or current_next_offset < event.offset + 1:
                raise DtsIngestStoreError("DTS_LEDGER_CHECKPOINT_INCONSISTENT")
            return True

        if current_next_offset is not None and event.offset != current_next_offset:
            raise DtsIngestStoreError("DTS_DATABASE_OFFSET_NOT_CONTIGUOUS")

        if source_write_state is not None:
            source_key, key_data, dependency_keys, source_row, is_deleted = (
                source_write_state
            )
            source_insert = insert(source_table).values(
                source_region=event.source_region,
                source_table=event.table_name,
                source_key=source_key,
                source_key_data=key_data,
                dependency_keys=dependency_keys,
                source_row=source_row,
                is_deleted=is_deleted,
                source_timestamp=event.source_timestamp,
                last_record_id=event.record_id,
                source_position=event.source_position,
                last_topic=event.topic,
                last_partition=event.partition,
                last_offset=event.offset,
                row_version=1,
            )
            excluded = source_insert.excluded
            newer = or_(
                excluded.source_timestamp > source_table.c.source_timestamp,
                and_(
                    excluded.source_timestamp == source_table.c.source_timestamp,
                    excluded.last_record_id > source_table.c.last_record_id,
                ),
                and_(
                    excluded.source_timestamp == source_table.c.source_timestamp,
                    excluded.last_record_id == source_table.c.last_record_id,
                    excluded.last_offset > source_table.c.last_offset,
                ),
            )
            connection.execute(
                source_insert.on_conflict_do_update(
                    index_elements=[
                        source_table.c.source_region,
                        source_table.c.source_table,
                        source_table.c.source_key,
                    ],
                    set_={
                        "source_key_data": excluded.source_key_data,
                        "dependency_keys": excluded.dependency_keys,
                        "source_row": excluded.source_row,
                        "is_deleted": excluded.is_deleted,
                        "source_timestamp": excluded.source_timestamp,
                        "last_record_id": excluded.last_record_id,
                        "source_position": excluded.source_position,
                        "last_topic": excluded.last_topic,
                        "last_partition": excluded.last_partition,
                        "last_offset": excluded.last_offset,
                        "row_version": source_table.c.row_version + 1,
                        "updated_at": text("clock_timestamp()"),
                    },
                    where=newer,
                )
            )

        for batch_start in range(0, len(key_rows), DIRTY_KEY_UPSERT_BATCH_SIZE):
            batch = key_rows[
                batch_start : batch_start + DIRTY_KEY_UPSERT_BATCH_SIZE
            ]
            dirty_insert = insert(dirty_table).values(
                [
                    {
                        "key_type": key_type,
                        "key_part_1": key_part_1,
                        "key_part_2": key_part_2,
                        "status": "PENDING",
                        "pending_event_count": 1,
                        "attempt_count": 0,
                        "last_source_region": event.source_region,
                        "last_source_table": event.table_name,
                        "last_topic": event.topic,
                        "last_partition": event.partition,
                        "last_offset": event.offset,
                        "issue_codes": list(dirty_keys.issues),
                        "row_version": 1,
                    }
                    for key_type, key_part_1, key_part_2 in batch
                ]
            )
            excluded = dirty_insert.excluded
            connection.execute(
                dirty_insert.on_conflict_do_update(
                    index_elements=[
                        dirty_table.c.key_type,
                        dirty_table.c.key_part_1,
                        dirty_table.c.key_part_2,
                    ],
                    set_={
                        "status": "PENDING",
                        "pending_event_count": dirty_table.c.pending_event_count + 1,
                        "attempt_count": 0,
                        "last_source_region": excluded.last_source_region,
                        "last_source_table": excluded.last_source_table,
                        "last_topic": excluded.last_topic,
                        "last_partition": excluded.last_partition,
                        "last_offset": excluded.last_offset,
                        "issue_codes": dirty_table.c.issue_codes.op("||")(
                            excluded.issue_codes
                        ),
                        "last_error_code": None,
                        "next_attempt_at": None,
                        "claimed_at": None,
                        "claimed_by": None,
                        "row_version": dirty_table.c.row_version + 1,
                        "last_seen_at": text("clock_timestamp()"),
                    },
                )
            )

        checkpoint_insert = insert(checkpoint).values(
            source_region=event.source_region,
            topic=event.topic,
            partition_id=event.partition,
            next_offset=event.offset + 1,
            source_timestamp=event.source_timestamp,
            source_position=event.source_position,
        )
        excluded = checkpoint_insert.excluded
        connection.execute(
            checkpoint_insert.on_conflict_do_update(
                index_elements=[
                    checkpoint.c.source_region,
                    checkpoint.c.topic,
                    checkpoint.c.partition_id,
                ],
                set_={
                    "next_offset": excluded.next_offset,
                    "source_timestamp": excluded.source_timestamp,
                    "source_position": excluded.source_position,
                    "updated_at": text("clock_timestamp()"),
                },
            )
        )
        return False


__all__ = [
    "DtsIngestDatabaseSettings",
    "DtsResumeCheckpoint",
    "DtsIngestStoreError",
    "DtsProjectionActivationSettings",
    "EXPECTED_DATABASE",
    "EXPECTED_ROLE",
    "EXPECTED_SCHEMA",
    "PostgresDtsEventSink",
    "build_dts_ingest_engine",
]
