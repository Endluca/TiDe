from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from app.dts_ingest_store import (
    APPROVED_INSECURE_PRE_HOST,
    DtsIngestDatabaseSettings,
    DtsIngestStoreError,
    DtsProjectionActivationSettings,
    EXPECTED_DTS_STATE_COLUMNS,
    EXPECTED_DTS_STATE_CONSTRAINTS,
    EXPECTED_DTS_STATE_GUARD_TRIGGERS,
    EXPECTED_DTS_STATE_INDEXES,
    EXPECTED_SOURCE_WIDE_COLUMNS,
    EXPECTED_SOURCE_WIDE_TRIGGER_DEFINITIONS,
    MUTABLE_RELATIONS,
    PROJECTION_ADVISORY_LOCK_NAME,
    PostgresDtsEventSink,
    _validate_projection_activation_state,
    _validate_dts_physical_connection_transport,
    build_dts_ingest_engine,
    _dirty_key_rows,
    _source_row_state,
)
from app.dts_source_consumer import (
    DirtyKeySet,
    DtsChangeEvent,
    DtsConfigurationError,
    DtsEventProcessor,
    DtsKafkaShadowConsumer,
    DtsRecordError,
    SOURCE_FIELD_WHITELIST,
    route_dirty_keys,
)


def _event(*, offset: int = 42) -> DtsChangeEvent:
    return DtsChangeEvent(
        source_region="ovs",
        topic="topic-v2",
        partition=0,
        offset=offset,
        record_id=9001,
        source_timestamp=1786342560,
        source_txid="tx-1",
        source_position="lsn:1",
        operation="UPDATE",
        database_name="ovs_db",
        schema_name="public",
        table_name="ovs_appoint",
        before={"id": "99", "t_id": "10", "s_id": "20"},
        after={
            "id": "99",
            "t_id": "10",
            "s_id": "20",
            "status": "end",
            "use_point": "buy",
            "start_time": "2026-08-11 17:00:00",
            "password": "must-not-persist",
            "mobile": "must-not-persist",
        },
    )


def test_database_settings_are_fixed_to_the_confirmed_test_target() -> None:
    values = {
        "TIT_DTS_INGEST_DB_HOST": "db.internal",
        "TIT_DTS_INGEST_DB_PASSWORD": " database-secret ",
    }
    settings = DtsIngestDatabaseSettings.from_env(values)

    assert settings.database == "tide_system_test"
    assert settings.username == "tit_dts_ingest_runtime"
    assert settings.schema == "public"
    assert settings.password == " database-secret "
    assert settings.sqlalchemy_url().query == {"sslmode": "verify-full"}
    summary = settings.safe_summary()
    assert "password" not in summary
    assert "host" not in summary
    assert "database-secret" not in json.dumps(summary)
    assert summary["sslmode"] == "verify-full"

    values["TIT_DTS_INGEST_DB_NAME"] = "tide_system"
    with pytest.raises(DtsConfigurationError, match="DB_NAME_MUST_EQUAL"):
        DtsIngestDatabaseSettings.from_env(values)


def test_database_settings_allow_only_explicit_test_ssl_disable() -> None:
    values = {
        "TIT_DTS_INGEST_DB_HOST": APPROVED_INSECURE_PRE_HOST,
        "TIT_DTS_INGEST_DB_PASSWORD": "database-secret",
        "TIT_DTS_INGEST_DB_SSLMODE": "disable",
        "TIT_DTS_ALLOW_INSECURE_DB": "true",
    }

    settings = DtsIngestDatabaseSettings.from_env(values)

    assert settings.sslmode == "disable"
    assert settings.allow_insecure_db is True
    assert settings.sqlalchemy_url().query == {"sslmode": "disable"}
    assert settings.safe_summary()["sslmode"] == "disable"
    assert settings.safe_summary()["insecure_transport_authorized"] is True

    for invalid in ("", "require", "verify-ca", "VERIFY-FULL"):
        values["TIT_DTS_INGEST_DB_SSLMODE"] = invalid
        with pytest.raises(
            DtsConfigurationError,
            match="TIT_DTS_INGEST_DB_SSLMODE_INVALID",
        ):
            DtsIngestDatabaseSettings.from_env(values)

    values["TIT_DTS_INGEST_DB_SSLMODE"] = "disable"
    for invalid in ("", "TRUE", "1", "yes"):
        values["TIT_DTS_ALLOW_INSECURE_DB"] = invalid
        with pytest.raises(
            DtsConfigurationError,
            match="TIT_DTS_ALLOW_INSECURE_DB_INVALID",
        ):
            DtsIngestDatabaseSettings.from_env(values)

    values["TIT_DTS_ALLOW_INSECURE_DB"] = "false"
    with pytest.raises(
        DtsConfigurationError,
        match="TIT_DTS_ALLOW_INSECURE_DB_REQUIRED_FOR_SSLMODE_DISABLE",
    ):
        DtsIngestDatabaseSettings.from_env(values)

    values["TIT_DTS_ALLOW_INSECURE_DB"] = "true"
    values["TIT_DTS_INGEST_DB_HOST"] = "other.internal"
    with pytest.raises(
        DtsConfigurationError,
        match="TIT_DTS_INGEST_DB_SSLMODE_DISABLE_ENDPOINT_NOT_APPROVED",
    ):
        DtsIngestDatabaseSettings.from_env(values)

    values["TIT_DTS_INGEST_DB_HOST"] = APPROVED_INSECURE_PRE_HOST
    values["TIT_DTS_INGEST_DB_PORT"] = "5433"
    with pytest.raises(
        DtsConfigurationError,
        match="TIT_DTS_INGEST_DB_SSLMODE_DISABLE_ENDPOINT_NOT_APPROVED",
    ):
        DtsIngestDatabaseSettings.from_env(values)

    values["TIT_DTS_INGEST_DB_PORT"] = "5432"
    values["TIT_DTS_INGEST_DB_SSLMODE"] = "verify-full"
    with pytest.raises(
        DtsConfigurationError,
        match="TIT_DTS_ALLOW_INSECURE_DB_REQUIRES_SSLMODE_DISABLE",
    ):
        DtsIngestDatabaseSettings.from_env(values)

    with pytest.raises(
        DtsConfigurationError,
        match="TIT_DTS_ALLOW_INSECURE_DB_INVALID",
    ):
        DtsIngestDatabaseSettings(
            host=APPROVED_INSECURE_PRE_HOST,
            password="database-secret",
            sslmode="disable",
            allow_insecure_db="true",  # type: ignore[arg-type]
        )

    for setting_name, override, error in (
        (
            "database",
            "tide_system",
            "TIT_DTS_INGEST_DB_NAME_MUST_EQUAL_tide_system_test",
        ),
        (
            "username",
            "other_role",
            "TIT_DTS_INGEST_DB_USER_MUST_EQUAL_tit_dts_ingest_runtime",
        ),
        (
            "schema",
            "other_schema",
            "TIT_DTS_INGEST_DB_SCHEMA_MUST_EQUAL_public",
        ),
    ):
        with pytest.raises(DtsConfigurationError, match=error):
            DtsIngestDatabaseSettings(
                host=APPROVED_INSECURE_PRE_HOST,
                password="database-secret",
                sslmode="disable",
                allow_insecure_db=True,
                **{setting_name: override},
            )


def test_projection_activation_settings_require_timezone_and_bind_stream() -> None:
    values = {
        "TIT_DTS_ACTIVATION_AT": "2026-08-13T00:00:00+08:00",
        "TIT_DTS_REQUIRED_OVS_TOPIC": "ovs-topic",
        "TIT_DTS_REQUIRED_DOM_TOPIC": "dom-topic",
    }

    settings = DtsProjectionActivationSettings.from_env(values)

    assert settings.activation_timestamp_seconds == 1786550400
    settings.require_current_stream(source_region="ovs", topic="ovs-topic")
    settings.require_current_stream(source_region="dom", topic="dom-topic")
    with pytest.raises(
        DtsConfigurationError,
        match="DTS_PROJECTION_STREAM_TOPIC_MISMATCH",
    ):
        settings.require_current_stream(source_region="ovs", topic="dom-topic")

    values["TIT_DTS_ACTIVATION_AT"] = "2026-08-13 00:00:00"
    with pytest.raises(
        DtsConfigurationError,
        match="TIT_DTS_ACTIVATION_AT_REQUIRES_TIMEZONE",
    ):
        DtsProjectionActivationSettings.from_env(values)


def test_dts_engine_reserves_a_second_connection_for_projection_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    fake_engine = object()

    def fake_create_engine(url: object, **kwargs: object) -> object:
        captured["url"] = url
        captured.update(kwargs)
        return fake_engine

    def fake_listen(
        target: object,
        event_name: str,
        callback: object,
    ) -> None:
        captured.setdefault("event_callbacks", {})[event_name] = callback
        captured.setdefault("event_targets", {})[event_name] = target

    monkeypatch.setattr("app.dts_ingest_store.create_engine", fake_create_engine)
    monkeypatch.setattr(
        "app.dts_ingest_store.sqlalchemy_event.listen",
        fake_listen,
    )
    settings = DtsIngestDatabaseSettings(
        host="db.internal",
        password="runtime-only",
    )

    build_dts_ingest_engine(settings, source_region="ovs")

    assert captured["url"].query == {"sslmode": "verify-full"}
    assert captured["pool_size"] == 2
    assert captured["max_overflow"] == 0
    assert captured["event_targets"] == {"connect": fake_engine}
    assert set(captured["event_callbacks"]) == {"connect"}


class _TransportCursor:
    def __init__(
        self,
        session_ssl: bool | None,
        server_ssl: str,
        *,
        execute_error: Exception | None = None,
    ) -> None:
        self.session_ssl = session_ssl
        self.server_ssl = server_ssl
        self.execute_error = execute_error
        self.executed: list[str] = []
        self.closed = False

    def execute(self, statement: str) -> None:
        self.executed.append(statement)
        if self.execute_error is not None:
            raise self.execute_error

    def fetchone(self) -> tuple[bool | None, str]:
        return (self.session_ssl, self.server_ssl)

    def close(self) -> None:
        self.closed = True


class _TransportConnection:
    def __init__(
        self,
        session_ssl: bool | None,
        server_ssl: str,
        *,
        execute_error: Exception | None = None,
    ) -> None:
        self.transport_cursor = _TransportCursor(
            session_ssl,
            server_ssl,
            execute_error=execute_error,
        )
        self.rollback_count = 0

    def cursor(self) -> _TransportCursor:
        return self.transport_cursor

    def rollback(self) -> None:
        self.rollback_count += 1


def test_dts_engine_revalidates_new_and_reused_physical_connections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    fake_engine = object()

    monkeypatch.setattr(
        "app.dts_ingest_store.create_engine",
        lambda *_args, **_kwargs: fake_engine,
    )

    def fake_listen(
        target: object,
        event_name: str,
        callback: object,
    ) -> None:
        assert target is fake_engine
        assert event_name in {"connect", "checkout"}
        captured[event_name] = callback

    monkeypatch.setattr(
        "app.dts_ingest_store.sqlalchemy_event.listen",
        fake_listen,
    )
    settings = DtsIngestDatabaseSettings(
        host=APPROVED_INSECURE_PRE_HOST,
        password="runtime-only",
        sslmode="disable",
        allow_insecure_db=True,
    )

    build_dts_ingest_engine(settings, source_region="ovs")

    connect_callback = captured["connect"]
    checkout_callback = captured["checkout"]
    assert callable(connect_callback)
    assert callable(checkout_callback)
    initial_connection = _TransportConnection(False, "off")
    connect_callback(initial_connection, object())
    assert initial_connection.transport_cursor.executed == [
        "SELECT (SELECT ssl FROM pg_catalog.pg_stat_ssl "
        "WHERE pid = pg_catalog.pg_backend_pid()), current_setting('ssl')"
    ]
    assert initial_connection.transport_cursor.closed is True
    assert initial_connection.rollback_count == 1

    reused_connection = _TransportConnection(False, "on")
    with pytest.raises(
        DtsIngestStoreError,
        match="DTS_TARGET_TLS_AVAILABLE_REQUIRES_VERIFY_FULL",
    ):
        checkout_callback(reused_connection, object(), object())
    assert reused_connection.transport_cursor.closed is True
    assert reused_connection.rollback_count == 1

    plaintext_verify_full = _TransportConnection(False, "on")
    verify_full_settings = DtsIngestDatabaseSettings(
        host="db.internal",
        password="runtime-only",
    )
    with pytest.raises(DtsIngestStoreError, match="DTS_TARGET_TLS_REQUIRED"):
        _validate_dts_physical_connection_transport(
            plaintext_verify_full,
            settings=verify_full_settings,
        )

    missing_session_state = _TransportConnection(None, "off")
    with pytest.raises(
        DtsIngestStoreError,
        match="DTS_TARGET_SSL_SETTING_UNAVAILABLE",
    ):
        connect_callback(missing_session_state, object())

    query_error = RuntimeError("ssl-setting-query-failed")
    broken_connection = _TransportConnection(
        False,
        "off",
        execute_error=query_error,
    )
    with pytest.raises(
        DtsIngestStoreError,
        match="DTS_TARGET_TRANSPORT_INSPECTION_FAILED",
    ) as caught:
        checkout_callback(broken_connection, object(), object())
    assert "ssl-setting-query-failed" not in str(caught.value)
    assert broken_connection.transport_cursor.closed is True
    assert broken_connection.rollback_count == 1


class _ValidationResult:
    def __init__(
        self,
        *,
        one: tuple[object, ...] | None = None,
        all_rows: list[tuple[object, ...]] | None = None,
        first: tuple[object, ...] | None = None,
    ) -> None:
        self._one = one
        self._all_rows = [] if all_rows is None else all_rows
        self._first = first

    def one(self) -> tuple[object, ...]:
        assert self._one is not None
        return self._one

    def all(self) -> list[tuple[object, ...]]:
        return self._all_rows

    def first(self) -> tuple[object, ...] | None:
        return self._first


class _ValidationConnection:
    def __init__(self, results: list[_ValidationResult]) -> None:
        self._results = iter(results)
        self.statements: list[tuple[str, object]] = []

    def __enter__(self) -> _ValidationConnection:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(
        self,
        statement: object,
        parameters: object = None,
    ) -> _ValidationResult:
        self.statements.append((str(statement), parameters))
        return next(self._results)


class _ValidationEngine:
    dialect = SimpleNamespace(name="postgresql")

    def __init__(self, connection: _ValidationConnection) -> None:
        self._connection = connection

    def connect(self) -> _ValidationConnection:
        return self._connection


def _validation_sink(
    *,
    missing: list[tuple[object, ...]] | None = None,
    invalid: tuple[object, ...] | None = None,
    unexpected: tuple[object, ...] | None = None,
    state_columns: list[tuple[object, ...]] | None = None,
    state_constraints: list[tuple[object, ...]] | None = None,
    state_indexes: list[tuple[object, ...]] | None = None,
    state_triggers: list[tuple[object, ...]] | None = None,
    columns: list[tuple[object, ...]] | None = None,
    triggers: list[tuple[object, ...]] | None = None,
    sslmode: str = "verify-full",
    server_ssl: str = "on",
    session_ssl: bool | None = None,
) -> tuple[PostgresDtsEventSink, _ValidationConnection]:
    if session_ssl is None:
        session_ssl = sslmode == "verify-full"
    expected_columns = list(EXPECTED_SOURCE_WIDE_COLUMNS)
    expected_triggers = [
        (*definition[:2], "O", *definition[2:])
        for definition in EXPECTED_SOURCE_WIDE_TRIGGER_DEFINITIONS
    ]
    expected_state_triggers = [
        (*definition[:2], "O", *definition[2:])
        for definition in EXPECTED_DTS_STATE_GUARD_TRIGGERS
    ]
    connection = _ValidationConnection(
        [
            _ValidationResult(
                one=(
                    "tide_system_test",
                    "tit_dts_ingest_runtime",
                    "public",
                    "off",
                    session_ssl,
                    server_ssl,
                )
            ),
            _ValidationResult(all_rows=[] if missing is None else missing),
            _ValidationResult(first=invalid),
            _ValidationResult(first=unexpected),
            _ValidationResult(
                all_rows=(
                    list(EXPECTED_DTS_STATE_COLUMNS)
                    if state_columns is None
                    else state_columns
                )
            ),
            _ValidationResult(
                all_rows=(
                    list(EXPECTED_DTS_STATE_CONSTRAINTS)
                    if state_constraints is None
                    else state_constraints
                )
            ),
            _ValidationResult(
                all_rows=(
                    list(EXPECTED_DTS_STATE_INDEXES)
                    if state_indexes is None
                    else state_indexes
                )
            ),
            _ValidationResult(
                all_rows=(
                    expected_state_triggers
                    if state_triggers is None
                    else state_triggers
                )
            ),
            _ValidationResult(
                all_rows=expected_columns if columns is None else columns
            ),
            _ValidationResult(
                all_rows=expected_triggers if triggers is None else triggers
            ),
        ]
    )
    sink = object.__new__(PostgresDtsEventSink)
    sink.settings = DtsIngestDatabaseSettings(
        host=(
            APPROVED_INSECURE_PRE_HOST
            if sslmode == "disable"
            else "db.internal"
        ),
        password="database-secret",
        sslmode=sslmode,
        allow_insecure_db=sslmode == "disable",
    )
    sink.engine = _ValidationEngine(connection)
    sink._validated = False
    return sink, connection


def test_dts_runtime_requires_crud_on_exactly_six_tables() -> None:
    sink, connection = _validation_sink()

    sink._validate_runtime()

    assert sink._validated is True
    assert len(connection.statements) == 10
    assert "pg_stat_ssl" in connection.statements[0][0]
    assert "current_setting('ssl')" in connection.statements[0][0]
    required_sql, required_parameters = connection.statements[1]
    assert "ARRAY['SELECT','INSERT','UPDATE','DELETE']" in required_sql
    assert required_parameters == {"relations": sorted(MUTABLE_RELATIONS)}
    invalid_sql, invalid_parameters = connection.statements[2]
    assert "ARRAY['TRUNCATE','TRIGGER']" in invalid_sql
    assert "'DELETE','TRUNCATE','TRIGGER'" not in invalid_sql
    assert invalid_parameters == {"relations": sorted(MUTABLE_RELATIONS)}
    boundary_sql, boundary_parameters = connection.statements[3]
    for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "TRIGGER"):
        assert f"'{privilege}'" in boundary_sql
    assert boundary_parameters == {"relations": sorted(MUTABLE_RELATIONS)}
    state_columns_sql, state_columns_parameters = connection.statements[4]
    assert "pg_catalog.pg_attribute" in state_columns_sql
    assert "pg_catalog.format_type" in state_columns_sql
    assert "attributes.attnotnull" in state_columns_sql
    assert state_columns_parameters == {
        "schema_name": "public",
        "table_names": [
            "dts_ingest_checkpoints",
            "dts_ingest_events",
            "dts_source_rows",
            "dts_dirty_keys",
        ],
    }
    constraints_sql, constraints_parameters = connection.statements[5]
    assert "pg_catalog.pg_constraint" in constraints_sql
    assert "pg_catalog.pg_get_constraintdef" in constraints_sql
    assert "constraints.convalidated" in constraints_sql
    assert constraints_parameters == state_columns_parameters
    indexes_sql, indexes_parameters = connection.statements[6]
    assert "pg_catalog.pg_index" in indexes_sql
    assert "indexes.indisvalid" in indexes_sql
    assert "indexes.indisready" in indexes_sql
    assert "pg_catalog.pg_opclass" in indexes_sql
    assert indexes_parameters == {
        **state_columns_parameters,
        "index_names": [
            "ix_dts_dirty_keys_ready",
            "ix_dts_ingest_events_source_table_processed",
            "ix_dts_source_rows_dependency_keys",
            "ix_dts_source_rows_table_active",
        ],
    }
    guards_sql, guards_parameters = connection.statements[7]
    assert "triggers.tgtype" in guards_sql
    assert "functions.proname" in guards_sql
    assert "functions.prosecdef" in guards_sql
    assert "triggers.tgattr::text" in guards_sql
    assert guards_parameters == state_columns_parameters
    columns_sql, columns_parameters = connection.statements[8]
    assert "pg_catalog.pg_attribute" in columns_sql
    assert "pg_catalog.format_type" in columns_sql
    assert "attributes.attnotnull" in columns_sql
    assert columns_parameters == {
        "schema_name": "public",
        "table_names": ["teacher_source_wide", "lesson_source_wide"],
    }
    triggers_sql, triggers_parameters = connection.statements[9]
    assert "pg_catalog.pg_trigger" in triggers_sql
    assert "triggers.tgenabled" in triggers_sql
    assert "triggers.tgtype" in triggers_sql
    assert "functions.proname" in triggers_sql
    assert "functions.prosecdef" in triggers_sql
    assert triggers_parameters == {
        "schema_name": "public",
        "table_names": ["teacher_source_wide", "lesson_source_wide"],
    }
    assert all(
        "alembic_version" not in sql
        for sql, _parameters in connection.statements
    )


def test_dts_runtime_allows_approved_pre_plaintext_only_while_server_ssl_is_off(
) -> None:
    sink, _connection = _validation_sink(sslmode="disable", server_ssl="off")

    sink._validate_runtime()

    assert sink._validated is True

    stale_sink, _connection = _validation_sink(
        sslmode="disable",
        server_ssl="on",
    )
    with pytest.raises(
        DtsIngestStoreError,
        match="DTS_TARGET_TLS_AVAILABLE_REQUIRES_VERIFY_FULL",
    ):
        stale_sink._validate_runtime()


def test_dts_runtime_requires_server_tls_for_verify_full() -> None:
    sink, _connection = _validation_sink(
        sslmode="verify-full",
        server_ssl="off",
    )

    with pytest.raises(DtsIngestStoreError, match="DTS_TARGET_TLS_REQUIRED"):
        sink._validate_runtime()


@pytest.mark.parametrize(
    ("sqlstate", "expected_code"),
    [
        ("42501", "DTS_TARGET_TRANSPORT_INSPECTION_PERMISSION_DENIED"),
        ("42P01", "DTS_TARGET_TRANSPORT_INSPECTION_UNAVAILABLE"),
        ("42703", "DTS_TARGET_TRANSPORT_INSPECTION_UNAVAILABLE"),
        ("42883", "DTS_TARGET_TRANSPORT_INSPECTION_UNAVAILABLE"),
        ("0A000", "DTS_TARGET_TRANSPORT_INSPECTION_UNAVAILABLE"),
        (None, "DTS_TARGET_TRANSPORT_INSPECTION_FAILED"),
    ],
)
def test_transport_inspection_errors_are_phase_specific_and_safe(
    sqlstate: str | None,
    expected_code: str,
) -> None:
    class InspectionError(RuntimeError):
        pass

    error = InspectionError("permission denied for sensitive catalog query")
    error.sqlstate = sqlstate  # type: ignore[attr-defined]

    class Cursor:
        def execute(self, _statement: str) -> None:
            raise error

        def close(self) -> None:
            pass

    class Connection:
        rolled_back = False

        def cursor(self) -> Cursor:
            return Cursor()

        def rollback(self) -> None:
            self.rolled_back = True

    connection = Connection()
    settings = DtsIngestDatabaseSettings(
        host=APPROVED_INSECURE_PRE_HOST,
        password="database-secret",
        sslmode="disable",
        allow_insecure_db=True,
    )

    with pytest.raises(DtsIngestStoreError, match=expected_code) as caught:
        _validate_dts_physical_connection_transport(
            connection,
            settings=settings,
        )

    assert str(caught.value) == expected_code
    assert "sensitive" not in str(caught.value)
    assert connection.rolled_back is True


def test_dts_runtime_rejects_invalid_privilege_boundaries() -> None:
    missing_sink, _connection = _validation_sink(
        missing=[("public.dts_dirty_keys", "DELETE")]
    )
    with pytest.raises(DtsIngestStoreError, match="DTS_TARGET_PRIVILEGE_MISSING"):
        missing_sink._validate_runtime()

    invalid_sink, _connection = _validation_sink(
        invalid=("public.dts_ingest_events", "TRUNCATE")
    )
    with pytest.raises(DtsIngestStoreError, match="DTS_TARGET_PRIVILEGE_INVALID"):
        invalid_sink._validate_runtime()

    broad_sink, _connection = _validation_sink(
        unexpected=("public.outbox_events", "UPDATE")
    )
    with pytest.raises(DtsIngestStoreError, match="DTS_TARGET_PRIVILEGE_TOO_BROAD"):
        broad_sink._validate_runtime()


@pytest.mark.parametrize(
    "drift",
    ["missing_column", "extra_column", "wrong_type", "wrong_nullability"],
)
def test_dts_runtime_rejects_state_column_drift(drift: str) -> None:
    columns = list(EXPECTED_DTS_STATE_COLUMNS)
    if drift == "missing_column":
        columns.pop()
    elif drift == "extra_column":
        columns.append(("dts_dirty_keys", "unexpected", "text", False))
    elif drift == "wrong_type":
        table_name, column_name, _data_type, not_null = columns[0]
        columns[0] = (table_name, column_name, "text", not_null)
    else:
        table_name, column_name, data_type, not_null = columns[0]
        columns[0] = (table_name, column_name, data_type, not not_null)
    sink, _connection = _validation_sink(state_columns=columns)

    with pytest.raises(
        DtsIngestStoreError,
        match="DTS_TARGET_STATE_SCHEMA_MISMATCH",
    ):
        sink._validate_runtime()


@pytest.mark.parametrize("drift", ["missing", "definition", "unvalidated"])
def test_dts_runtime_rejects_state_constraint_drift(drift: str) -> None:
    constraints = list(EXPECTED_DTS_STATE_CONSTRAINTS)
    if drift == "missing":
        constraints.pop()
    else:
        row = list(constraints[0])
        if drift == "definition":
            row[4] = "CHECK (next_offset >= -1)"
        else:
            row[3] = False
        constraints[0] = tuple(row)
    sink, _connection = _validation_sink(state_constraints=constraints)

    with pytest.raises(
        DtsIngestStoreError,
        match="DTS_TARGET_STATE_CONSTRAINT_MISMATCH",
    ):
        sink._validate_runtime()


@pytest.mark.parametrize("drift", ["missing", "method", "opclass", "invalid"])
def test_dts_runtime_rejects_state_index_drift(drift: str) -> None:
    indexes = list(EXPECTED_DTS_STATE_INDEXES)
    if drift == "missing":
        indexes.pop()
    else:
        row = list(indexes[2])
        if drift == "method":
            row[2] = "btree"
        elif drift == "opclass":
            row[7] = ("jsonb_ops",)
        else:
            row[3] = False
        indexes[2] = tuple(row)
    sink, _connection = _validation_sink(state_indexes=indexes)

    with pytest.raises(
        DtsIngestStoreError,
        match="DTS_TARGET_STATE_INDEX_MISMATCH",
    ):
        sink._validate_runtime()


@pytest.mark.parametrize("drift", ["missing", "inactive", "event", "function"])
def test_dts_runtime_rejects_state_guard_trigger_drift(drift: str) -> None:
    rows = [
        (*definition[:2], "O", *definition[2:])
        for definition in EXPECTED_DTS_STATE_GUARD_TRIGGERS
    ]
    if drift == "missing":
        rows.pop()
    else:
        row = list(rows[0])
        if drift == "inactive":
            row[2] = "D"
        elif drift == "event":
            row[3] = 19
        else:
            row[5] = "unexpected_guard"
        rows[0] = tuple(row)
    sink, _connection = _validation_sink(state_triggers=rows)

    with pytest.raises(
        DtsIngestStoreError,
        match="DTS_TARGET_STATE_GUARD_TRIGGER_MISMATCH",
    ):
        sink._validate_runtime()


def test_source_wide_runtime_contract_is_exactly_55_and_23_columns() -> None:
    assert sum(
        row[0] == "teacher_source_wide"
        for row in EXPECTED_SOURCE_WIDE_COLUMNS
    ) == 55
    assert sum(
        row[0] == "lesson_source_wide"
        for row in EXPECTED_SOURCE_WIDE_COLUMNS
    ) == 23
    assert (
        "teacher_source_wide",
        "tchr_id",
        "character varying(64)",
        True,
    ) in EXPECTED_SOURCE_WIDE_COLUMNS
    assert (
        "lesson_source_wide",
        "上课时间",
        "time without time zone",
        False,
    ) in EXPECTED_SOURCE_WIDE_COLUMNS


@pytest.mark.parametrize(
    "drift",
    ["missing_column", "extra_column", "wrong_type", "wrong_nullability"],
)
def test_dts_runtime_rejects_source_wide_column_drift(drift: str) -> None:
    columns = list(EXPECTED_SOURCE_WIDE_COLUMNS)
    if drift == "missing_column":
        columns.pop()
    elif drift == "extra_column":
        columns.append(("lesson_source_wide", "unexpected", "text", False))
    elif drift == "wrong_type":
        table_name, column_name, _data_type, not_null = columns[0]
        columns[0] = (table_name, column_name, "text", not_null)
    else:
        table_name, column_name, data_type, not_null = columns[0]
        columns[0] = (table_name, column_name, data_type, not not_null)
    sink, _connection = _validation_sink(columns=columns)

    with pytest.raises(
        DtsIngestStoreError,
        match="DTS_TARGET_SOURCE_WIDE_SCHEMA_MISMATCH",
    ):
        sink._validate_runtime()


@pytest.mark.parametrize("mode", ["D", "R"])
def test_dts_runtime_rejects_missing_or_inactive_outbox_triggers(
    mode: str,
) -> None:
    trigger_rows = [
        (*definition[:2], "O", *definition[2:])
        for definition in EXPECTED_SOURCE_WIDE_TRIGGER_DEFINITIONS
    ]
    trigger_rows[-1] = (
        *EXPECTED_SOURCE_WIDE_TRIGGER_DEFINITIONS[-1][:2],
        mode,
        *EXPECTED_SOURCE_WIDE_TRIGGER_DEFINITIONS[-1][2:],
    )
    sink, _connection = _validation_sink(triggers=trigger_rows)

    with pytest.raises(
        DtsIngestStoreError,
        match="DTS_TARGET_SOURCE_WIDE_TRIGGER_MISMATCH",
    ):
        sink._validate_runtime()

    missing_sink, _connection = _validation_sink(triggers=trigger_rows[:-1])
    with pytest.raises(
        DtsIngestStoreError,
        match="DTS_TARGET_SOURCE_WIDE_TRIGGER_MISMATCH",
    ):
        missing_sink._validate_runtime()


def test_dts_runtime_accepts_always_enabled_outbox_triggers() -> None:
    trigger_rows = [
        (*definition[:2], "A", *definition[2:])
        for definition in EXPECTED_SOURCE_WIDE_TRIGGER_DEFINITIONS
    ]
    sink, _connection = _validation_sink(triggers=trigger_rows)

    sink._validate_runtime()

    assert sink._validated is True


@pytest.mark.parametrize("drift", ["event", "function", "security", "filter"])
def test_dts_runtime_rejects_outbox_trigger_definition_drift(drift: str) -> None:
    rows = [
        (*definition[:2], "O", *definition[2:])
        for definition in EXPECTED_SOURCE_WIDE_TRIGGER_DEFINITIONS
    ]
    row = list(rows[0])
    if drift == "event":
        row[3] = 21
    elif drift == "function":
        row[5] = "unexpected_outbox_function"
    elif drift == "security":
        row[7] = False
    else:
        row[10] = False
    rows[0] = tuple(row)
    sink, _connection = _validation_sink(triggers=rows)

    with pytest.raises(
        DtsIngestStoreError,
        match="DTS_TARGET_SOURCE_WIDE_TRIGGER_MISMATCH",
    ):
        sink._validate_runtime()


def test_source_mirror_persists_only_confirmed_fields() -> None:
    state = _source_row_state(_event())
    assert state is not None
    source_key, key_data, dependency_keys, source_row, deleted = state

    assert source_key == '{"id":"99"}'
    assert key_data == {"id": "99"}
    assert dependency_keys == {
        "category_ids": [],
        "course_ids": ["99"],
        "label_ids": [],
        "teacher_ids": ["10"],
        "student_ids": ["20"],
    }
    assert deleted is False
    assert source_row["t_id"] == "10"
    assert "password" not in source_row
    assert "mobile" not in source_row
    for fields in SOURCE_FIELD_WHITELIST.values():
        assert "password" not in fields
        assert "mobile" not in fields
        assert "phone" not in fields
        assert "certification_url" not in fields


def test_source_mirror_merges_sparse_update_images_before_dependency_routing() -> None:
    original = _event()
    event = DtsChangeEvent(
        **{
            **original.__dict__,
            "before": {
                "id": "99",
                "t_id": "10",
                "s_id": "20",
                "status": "end",
            },
            "after": {"id": "99", "t_id": "11"},
        }
    )

    state = _source_row_state(event)

    assert state is not None
    _source_key, _key_data, dependency_keys, source_row, deleted = state
    assert source_row == {
        "id": "99",
        "t_id": "11",
        "s_id": "20",
        "status": "end",
    }
    assert dependency_keys == {
        "course_ids": ["99"],
        "teacher_ids": ["11"],
        "student_ids": ["20"],
        "label_ids": [],
        "category_ids": [],
    }
    assert deleted is False


def test_source_mirror_rejects_primary_key_update() -> None:
    original = _event()
    event = DtsChangeEvent(
        **{
            **original.__dict__,
            "before": {"id": "99", "status": "on"},
            "after": {"id": "100", "status": "end"},
        }
    )

    with pytest.raises(
        DtsRecordError,
        match="DTS_SOURCE_PRIMARY_KEY_UPDATE_NOT_ALLOWED",
    ):
        _source_row_state(event)


def test_dom_shared_tables_route_their_smallest_dirty_keys() -> None:
    teacher_event = _event()
    teacher_event = DtsChangeEvent(
        **{
            **teacher_event.__dict__,
            "source_region": "dom",
            "table_name": "dom_teacher_certification",
            "before": None,
            "after": {
                "id": "7",
                "teacher_id": "10",
                "certification_type": "TESOL",
                "certification_status": 1,
            },
        }
    )
    category_event = DtsChangeEvent(
        **{
            **teacher_event.__dict__,
            "table_name": "dom_complaint_cate",
            "before": None,
            "after": {"id": "13", "cate_parent": "0"},
        }
    )

    assert route_dirty_keys(teacher_event).teacher_ids == {"10"}
    assert route_dirty_keys(category_event).complaint_category_ids == {"13"}
    assert _dirty_key_rows(route_dirty_keys(category_event)) == (
        ("COMPLAINT_CATEGORY", "13", ""),
    )


class _Result:
    def __init__(
        self,
        *,
        scalar: int | None = None,
        rowcount: int = 1,
        mapping: dict[str, object] | None = None,
        rows: list[object] | None = None,
        first: object | None = None,
        one: tuple[object, ...] | None = None,
    ) -> None:
        self._scalar = scalar
        self._mapping = mapping
        self._rows = [] if rows is None else rows
        self._first = first
        self._one = one
        self.rowcount = rowcount

    def scalar_one_or_none(self) -> int | None:
        return self._scalar

    def mappings(self) -> _Result:
        return self

    def scalars(self) -> _Result:
        return self

    def one_or_none(self) -> dict[str, object] | None:
        return self._mapping

    def all(self) -> list[object]:
        return self._rows

    def first(self) -> object | None:
        return self._first

    def one(self) -> tuple[object, ...]:
        assert self._one is not None
        return self._one


class _Connection:
    def __init__(self, results: list[_Result]) -> None:
        self.results = iter(results)
        self.statements: list[object] = []
        self.parameters: list[object | None] = []
        self.commit_count = 0
        self.rollback_count = 0
        self.closed = False
        self.invalidated = False

    def execute(self, statement: object, *_args: object, **_kwargs: object) -> _Result:
        self.statements.append(statement)
        self.parameters.append(_args[0] if _args else None)
        return next(self.results)

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        self.rollback_count += 1

    def close(self) -> None:
        self.closed = True

    def invalidate(self) -> None:
        self.invalidated = True


class _ActivationEngine:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection
        self.disposed = False

    def connect(self) -> _Connection:
        return self.connection

    def dispose(self) -> None:
        self.disposed = True


def _activation_settings() -> DtsProjectionActivationSettings:
    return DtsProjectionActivationSettings(
        activation_timestamp_seconds=1786550400,
        required_ovs_topic="ovs-topic",
        required_dom_topic="dom-topic",
    )


def test_projection_activation_holds_one_global_session_lock_until_close() -> None:
    connection = _Connection(
        [
            _Result(one=(True, 42001)),
            _Result(first=None),
            _Result(scalar=3),
            _Result(first=None),
            _Result(one=(42001, True, "on")),
            _Result(scalar=True),
        ]
    )
    engine = _ActivationEngine(connection)
    sink = object.__new__(PostgresDtsEventSink)
    sink.engine = engine
    sink.settings = DtsIngestDatabaseSettings(
        host="db.internal",
        password="runtime-only",
    )
    sink._validated = True
    sink._projection_lock_connection = None

    sink.acquire_projection_activation(_activation_settings())

    assert sink._projection_lock_connection is connection
    assert connection.commit_count == 1
    lock_sql = str(connection.statements[0])
    assert "pg_catalog.pg_try_advisory_lock" in lock_sql
    assert connection.parameters[0] == {
        "lock_name": PROJECTION_ADVISORY_LOCK_NAME,
    }
    checkpoint_sql = str(connection.statements[1])
    assert "public.dts_ingest_checkpoints" in checkpoint_sql
    assert "checkpoints.partition_id = 0" in checkpoint_sql
    assert "checkpoints.source_timestamp <" in checkpoint_sql
    assert connection.parameters[1] == {
        "required_ovs_topic": "ovs-topic",
        "required_dom_topic": "dom-topic",
        "activation_timestamp_seconds": 1786550400,
    }
    dictionary_sql = str(connection.statements[2])
    assert "categories.source_region = 'dom'" in dictionary_sql
    assert "categories.source_table = 'dom_complaint_cate'" in dictionary_sql
    assert "categories.is_deleted IS FALSE" in dictionary_sql
    dependency_sql = str(connection.statements[3])
    assert "complaints.dependency_keys -> 'category_ids'" in dependency_sql
    for source_table in (
        "dom_complaint",
        "dom_user_complaint",
        "ovs_complaint",
        "ovs_user_complaint",
    ):
        assert f"'{source_table}'" in dependency_sql
    assert "complaints.is_deleted IS FALSE" in dependency_sql

    sink.assert_projection_lock_held()

    assert "pg_catalog.pg_backend_pid" in str(connection.statements[4])
    assert connection.commit_count == 2

    sink.close()

    assert "pg_catalog.pg_advisory_unlock" in str(connection.statements[5])
    assert connection.commit_count == 3
    assert connection.rollback_count == 1
    assert connection.closed is True
    assert connection.invalidated is False
    assert engine.disposed is True


@pytest.mark.parametrize(
    ("results", "error_code"),
    [
        (
            [_Result(first=("dom", "dom-topic"))],
            "DTS_PROJECTION_CHECKPOINT_NOT_READY",
        ),
        (
            [_Result(first=None), _Result(scalar=0)],
            "DTS_PROJECTION_COMPLAINT_DICTIONARY_EMPTY",
        ),
        (
            [
                _Result(first=None),
                _Result(scalar=3),
                _Result(first=("82",)),
            ],
            "DTS_PROJECTION_COMPLAINT_CATEGORY_DEPENDENCY_PENDING",
        ),
    ],
)
def test_projection_activation_state_fails_closed(
    results: list[_Result],
    error_code: str,
) -> None:
    connection = _Connection(results)

    with pytest.raises(DtsIngestStoreError, match=error_code):
        _validate_projection_activation_state(connection, _activation_settings())


def test_projection_activation_rejects_a_second_projector() -> None:
    connection = _Connection([_Result(one=(False, 42002))])
    engine = _ActivationEngine(connection)
    sink = object.__new__(PostgresDtsEventSink)
    sink.engine = engine
    sink._validated = True
    sink._projection_lock_connection = None

    with pytest.raises(
        DtsIngestStoreError,
        match="DTS_PROJECTION_LOCK_NOT_ACQUIRED",
    ):
        sink.acquire_projection_activation(_activation_settings())

    assert connection.rollback_count == 1
    assert connection.closed is True
    assert connection.invalidated is False
    assert sink._projection_lock_connection is None


def test_projection_activation_releases_lock_when_readiness_gate_fails() -> None:
    connection = _Connection(
        [
            _Result(one=(True, 42003)),
            _Result(first=("dom", "dom-topic")),
            _Result(scalar=True),
        ]
    )
    engine = _ActivationEngine(connection)
    sink = object.__new__(PostgresDtsEventSink)
    sink.engine = engine
    sink._validated = True
    sink._projection_lock_connection = None

    with pytest.raises(
        DtsIngestStoreError,
        match="DTS_PROJECTION_CHECKPOINT_NOT_READY",
    ):
        sink.acquire_projection_activation(_activation_settings())

    assert "pg_catalog.pg_advisory_unlock" in str(connection.statements[2])
    assert connection.rollback_count == 1
    assert connection.commit_count == 1
    assert connection.closed is True
    assert sink._projection_lock_connection is None


def test_projection_stops_if_the_lock_connection_changes_session() -> None:
    connection = _Connection(
        [
            _Result(one=(True, 42004)),
            _Result(first=None),
            _Result(scalar=3),
            _Result(first=None),
            _Result(scalar=99999),
        ]
    )
    engine = _ActivationEngine(connection)
    sink = object.__new__(PostgresDtsEventSink)
    sink.engine = engine
    sink._validated = True
    sink._projection_lock_connection = None
    sink._projection_lock_backend_pid = None
    sink.acquire_projection_activation(_activation_settings())

    with pytest.raises(DtsIngestStoreError, match="DTS_PROJECTION_LOCK_LOST"):
        sink.assert_projection_lock_held()

    assert connection.invalidated is True
    assert connection.closed is True
    assert sink._projection_lock_connection is None
    assert sink._projection_lock_backend_pid is None


def _compiled_dirty_rows(statement: object) -> set[tuple[str, str, str]]:
    params = statement.compile(dialect=postgresql.dialect()).params
    indexes = sorted(
        int(name.removeprefix("key_type_m"))
        for name in params
        if name.startswith("key_type_m")
    )
    return {
        (
            params[f"key_type_m{index}"],
            params[f"key_part_1_m{index}"],
            params[f"key_part_2_m{index}"],
        )
        for index in indexes
    }


def test_transaction_writes_receipt_mirror_dirty_keys_then_checkpoint() -> None:
    sink = object.__new__(PostgresDtsEventSink)
    event = _event()
    dirty = route_dirty_keys(event)
    # advisory lock, checkpoint SELECT, source-row SELECT, receipt, mirror,
    # one batched dirty-key UPSERT and checkpoint UPSERT
    connection = _Connection(
        [_Result(), _Result(scalar=None), _Result(mapping=None)]
        + [_Result()] * 4
    )

    duplicate = sink._apply_transaction(connection, event, dirty)

    assert duplicate is False
    assert len(connection.statements) == 7
    assert "pg_catalog.pg_advisory_xact_lock" in str(connection.statements[0])
    assert connection.parameters[0] == {
        "stream_identity": '["ovs","topic-v2",0]',
    }
    assert _compiled_dirty_rows(connection.statements[5]) == {
        ("COURSE", "99", ""),
        ("TEACHER", "10", ""),
        ("TEACHER_STUDENT", "10", "20"),
    }
    compiled_dirty = connection.statements[5].compile(
        dialect=postgresql.dialect()
    )
    dirty_sql = str(compiled_dirty)
    assert any(
        f"attempt_count = %({name})s" in dirty_sql and value == 0
        for name, value in compiled_dirty.params.items()
    )


def test_partial_update_recomputes_dependencies_and_dirties_old_and_new_owners() -> None:
    sink = object.__new__(PostgresDtsEventSink)
    original = _event(offset=42)
    event = DtsChangeEvent(
        **{
            **original.__dict__,
            "before": {"id": "99"},
            "after": {"id": "99", "t_id": "11"},
        }
    )
    connection = _Connection(
        [
            _Result(),  # advisory lock
            _Result(scalar=None),
            _Result(
                mapping={
                    "source_row": {
                        "id": "99",
                        "t_id": "10",
                        "s_id": "20",
                        "status": "end",
                    }
                }
            ),
            _Result(),  # receipt
            _Result(),  # source mirror
            _Result(),  # batched dirty keys
            _Result(),  # checkpoint
        ]
    )

    duplicate = sink._apply_transaction(
        connection,
        event,
        route_dirty_keys(event),
    )

    assert duplicate is False
    mirror_params = connection.statements[4].compile(
        dialect=postgresql.dialect()
    ).params
    assert mirror_params["source_row"] == {
        "id": "99",
        "t_id": "11",
        "s_id": "20",
        "status": "end",
    }
    assert mirror_params["dependency_keys"] == {
        "course_ids": ["99"],
        "teacher_ids": ["11"],
        "student_ids": ["20"],
        "label_ids": [],
        "category_ids": [],
    }
    dirty_rows = _compiled_dirty_rows(connection.statements[5])
    assert dirty_rows == {
        ("COURSE", "99", ""),
        ("TEACHER", "10", ""),
        ("TEACHER", "11", ""),
        ("TEACHER_STUDENT", "10", "20"),
        ("TEACHER_STUDENT", "11", "20"),
    }


@pytest.mark.parametrize(
    "case",
    ["insert", "revive", "onboard_change", "region_change"],
)
def test_dom_teacher_upsert_redirties_existing_dom_and_ovs_courses(
    case: str,
) -> None:
    sink = object.__new__(PostgresDtsEventSink)
    original = _event(offset=42)
    existing_row = {
        "id": "10",
        "status": "pending",
        "status_on_time": "2026-08-12 00:00:00",
        "course": "kids",
    }
    operation = "INSERT" if case == "insert" else "UPDATE"
    before = None if case == "insert" else dict(existing_row)
    after = {"id": "10", "real_name": "Teacher", "status": "active"}
    if case == "onboard_change":
        after["status_on_time"] = "2026-08-13 00:00:00"
    if case == "region_change":
        after["course"] = "global_cn"
    event = DtsChangeEvent(
        **{
            **original.__dict__,
            "source_region": "dom",
            "table_name": "dom_teacher",
            "operation": operation,
            "before": before,
            "after": after,
        }
    )
    existing = None if case == "insert" else {
        "source_row": existing_row,
        "is_deleted": case == "revive",
    }
    connection = _Connection(
        [
            _Result(),  # advisory lock
            _Result(scalar=None),
            _Result(mapping=existing),
            _Result(
                rows=[
                    {
                        "course_ids": ["dom-course"],
                        "teacher_ids": ["10"],
                    },
                    {
                        "course_ids": ["ovs-course"],
                        "teacher_ids": ["10"],
                    },
                ]
            ),
            _Result(),  # receipt
            _Result(),  # source mirror
            _Result(),  # batched dirty keys
            _Result(),  # checkpoint
        ]
    )

    duplicate = sink._apply_transaction(
        connection,
        event,
        route_dirty_keys(event),
    )

    assert duplicate is False
    appoint_lookup = connection.statements[3]
    appoint_lookup_sql = str(appoint_lookup)
    appoint_lookup_params = appoint_lookup.compile(
        dialect=postgresql.dialect()
    ).params
    string_params = {
        value
        for value in appoint_lookup_params.values()
        if isinstance(value, str)
    }
    assert {"dom_appoint", "ovs_appoint"}.issubset(
        string_params
    )
    assert {"dom", "ovs"}.issubset(string_params)
    assert "dts_source_rows.is_deleted IS false" in appoint_lookup_sql
    assert "dts_source_rows.dependency_keys @> CAST" in appoint_lookup_sql
    dirty_rows = _compiled_dirty_rows(connection.statements[6])
    assert dirty_rows == {
        ("COURSE", "dom-course", ""),
        ("COURSE", "ovs-course", ""),
        ("TEACHER", "10", ""),
    }


def test_dom_teacher_non_scope_update_does_not_fanout_courses() -> None:
    sink = object.__new__(PostgresDtsEventSink)
    original = _event(offset=42)
    existing_row = {
        "id": "10",
        "real_name": "Old Name",
        "status_on_time": "2026-08-13 00:00:00",
        "course": "global_cn",
    }
    event = DtsChangeEvent(
        **{
            **original.__dict__,
            "source_region": "dom",
            "table_name": "dom_teacher",
            "before": dict(existing_row),
            "after": {"id": "10", "real_name": "New Name"},
        }
    )
    connection = _Connection(
        [
            _Result(),  # advisory lock
            _Result(scalar=None),
            _Result(
                mapping={"source_row": existing_row, "is_deleted": False}
            ),
            _Result(),  # receipt
            _Result(),  # source mirror
            _Result(),  # batched teacher dirty key
            _Result(),  # checkpoint
        ]
    )

    duplicate = sink._apply_transaction(
        connection,
        event,
        route_dirty_keys(event),
    )

    assert duplicate is False
    assert len(connection.statements) == 7
    assert _compiled_dirty_rows(connection.statements[5]) == {
        ("TEACHER", "10", ""),
    }


def test_dom_teacher_course_fanout_batches_dirty_key_upserts() -> None:
    sink = object.__new__(PostgresDtsEventSink)
    original = _event(offset=42)
    event = DtsChangeEvent(
        **{
            **original.__dict__,
            "source_region": "dom",
            "table_name": "dom_teacher",
            "operation": "INSERT",
            "before": None,
            "after": {"id": "10", "status_on_time": "2026-08-13"},
        }
    )
    appoint_dependencies = [
        {"course_ids": [str(course_id)], "teacher_ids": ["10"]}
        for course_id in range(501)
    ]
    connection = _Connection(
        [
            _Result(),  # advisory lock
            _Result(scalar=None),
            _Result(mapping=None),
            _Result(rows=appoint_dependencies),
            _Result(),  # receipt
            _Result(),  # source mirror
            _Result(),  # first dirty-key batch
            _Result(),  # second dirty-key batch
            _Result(),  # checkpoint
        ]
    )

    duplicate = sink._apply_transaction(
        connection,
        event,
        route_dirty_keys(event),
    )

    assert duplicate is False
    first_batch = _compiled_dirty_rows(connection.statements[6])
    second_batch = _compiled_dirty_rows(connection.statements[7])
    assert len(first_batch) == 500
    assert len(second_batch) == 2
    assert first_batch | second_batch == {
        *(('COURSE', str(course_id), '') for course_id in range(501)),
        ("TEACHER", "10", ""),
    }


def test_sparse_delete_keeps_tombstone_dependencies_and_routes_previous_owner() -> None:
    sink = object.__new__(PostgresDtsEventSink)
    original = _event(offset=42)
    event = DtsChangeEvent(
        **{
            **original.__dict__,
            "operation": "DELETE",
            "before": {"id": "99"},
            "after": None,
        }
    )
    connection = _Connection(
        [
            _Result(),  # advisory lock
            _Result(scalar=None),
            _Result(
                mapping={
                    "source_row": {
                        "id": "99",
                        "t_id": "10",
                        "s_id": "20",
                        "status": "end",
                    },
                    "is_deleted": False,
                }
            ),
            _Result(),  # receipt
            _Result(),  # source mirror
            _Result(),  # batched dirty keys
            _Result(),  # checkpoint
        ]
    )

    duplicate = sink._apply_transaction(
        connection,
        event,
        route_dirty_keys(event),
    )

    assert duplicate is False
    mirror_params = connection.statements[4].compile(
        dialect=postgresql.dialect()
    ).params
    assert mirror_params["is_deleted"] is True
    assert mirror_params["source_row"] == {
        "id": "99",
        "t_id": "10",
        "s_id": "20",
        "status": "end",
    }
    assert mirror_params["dependency_keys"] == {
        "course_ids": ["99"],
        "teacher_ids": ["10"],
        "student_ids": ["20"],
        "label_ids": [],
        "category_ids": [],
    }
    dirty_rows = _compiled_dirty_rows(connection.statements[5])
    assert dirty_rows == {
        ("COURSE", "99", ""),
        ("TEACHER", "10", ""),
        ("TEACHER_STUDENT", "10", "20"),
    }


def test_duplicate_offset_keeps_mirror_dirty_queue_and_checkpoint_unchanged() -> None:
    sink = object.__new__(PostgresDtsEventSink)
    event = _event(offset=42)
    connection = _Connection(
        [
            _Result(),  # advisory lock
            _Result(scalar=43),
            _Result(
                mapping={"source_row": event.after, "is_deleted": False}
            ),
            _Result(rowcount=0),
        ]
    )

    duplicate = sink._apply_transaction(
        connection,
        event,
        route_dirty_keys(event),
    )

    assert duplicate is True
    assert len(connection.statements) == 4


def test_transaction_rejects_a_new_noncontiguous_offset() -> None:
    sink = object.__new__(PostgresDtsEventSink)
    connection = _Connection([_Result(), _Result(scalar=41)])

    with pytest.raises(DtsIngestStoreError, match="OFFSET_NOT_CONTIGUOUS"):
        sink._apply_transaction(connection, _event(offset=42), route_dirty_keys(_event()))


def test_durable_checkpoint_wins_over_a_lagging_kafka_commit() -> None:
    class Sink:
        authoritative_checkpoint = True

        def resume_offset(self, **_kwargs: object) -> int:
            return 42

        def apply(self, *_args: object) -> bool:
            return False

    class Consumer:
        def __init__(self, committed: int) -> None:
            self._committed = committed

        def committed(self, _partition: object, *, timeout_ms: int) -> int:
            assert timeout_ms == 15_000
            return self._committed

        def beginning_offsets(self, partitions: list[object]):
            return {partition: 0 for partition in partitions}

        def end_offsets(self, partitions: list[object]):
            return {partition: 100 for partition in partitions}

    settings = SimpleNamespace(
        source_region="ovs",
        topic="topic-v2",
        partition=0,
    )
    runner = DtsKafkaShadowConsumer(settings, DtsEventProcessor(Sink()))
    consumer = Consumer(41)

    assert runner._resolve_initial_offset(consumer, "partition-0") == 42

    with pytest.raises(DtsConfigurationError, match="AHEAD_OF_DATABASE"):
        runner._resolve_initial_offset(Consumer(43), "partition-0")
