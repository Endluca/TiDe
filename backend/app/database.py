from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import Pool


DEFAULT_DATABASE_URL = "postgresql+psycopg://tit_growth_app@/tit_growth?host=/tmp"


def _bounded_environment_int(
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, min(value, maximum))


class Base(DeclarativeBase):
    pass


def database_url() -> str:
    return os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)


def build_engine(
    url: str | None = None,
    *,
    poolclass: type[Pool] | None = None,
) -> Engine:
    resolved = url or database_url()
    if resolved.startswith("sqlite"):
        sqlite_options: dict[str, Any] = {
            "future": True,
            "pool_pre_ping": True,
            "connect_args": {"check_same_thread": False},
        }
        if poolclass is not None:
            sqlite_options["poolclass"] = poolclass
        return create_engine(
            resolved,
            **sqlite_options,
        )

    connect_args: dict[str, Any] = {
        "connect_timeout": _bounded_environment_int(
            "TIT_DB_CONNECT_TIMEOUT_SECONDS",
            8,
            minimum=1,
            maximum=60,
        ),
        "application_name": os.getenv(
            "TIT_DB_APPLICATION_NAME",
            "tit-growth",
        ).strip()
        or "tit-growth",
    }
    postgres_options = [
        (
            "lock_timeout",
            _bounded_environment_int(
                "TIT_DB_LOCK_TIMEOUT_MS",
                5_000,
                minimum=0,
                maximum=300_000,
            ),
        ),
        (
            "idle_in_transaction_session_timeout",
            _bounded_environment_int(
                "TIT_DB_IDLE_TRANSACTION_TIMEOUT_MS",
                60_000,
                minimum=0,
                maximum=3_600_000,
            ),
        ),
    ]
    statement_timeout_ms = _bounded_environment_int(
        "TIT_DB_STATEMENT_TIMEOUT_MS",
        0,
        minimum=0,
        maximum=3_600_000,
    )
    if statement_timeout_ms:
        postgres_options.append(("statement_timeout", statement_timeout_ms))
    connect_args["options"] = " ".join(
        f"-c {name}={value}" for name, value in postgres_options
    )

    engine_options: dict[str, Any] = {
        "future": True,
        "pool_pre_ping": True,
        "connect_args": connect_args,
    }
    if poolclass is not None:
        engine_options["poolclass"] = poolclass
    else:
        engine_options.update(
            pool_size=_bounded_environment_int(
                "TIT_DB_POOL_SIZE",
                5,
                minimum=1,
                maximum=100,
            ),
            max_overflow=_bounded_environment_int(
                "TIT_DB_MAX_OVERFLOW",
                2,
                minimum=0,
                maximum=100,
            ),
            pool_timeout=_bounded_environment_int(
                "TIT_DB_POOL_TIMEOUT_SECONDS",
                5,
                minimum=1,
                maximum=120,
            ),
            pool_recycle=_bounded_environment_int(
                "TIT_DB_POOL_RECYCLE_SECONDS",
                1_800,
                minimum=60,
                maximum=86_400,
            ),
            pool_use_lifo=True,
        )
    selected_engine = create_engine(resolved, **engine_options)
    if (
        os.getenv("APP_ENV", "local").strip().lower()
        in {"prod", "production"}
    ):
        from .runtime_settings import (
            DATABASE_TRANSPORT_PRE_PRIVATE_LINE_PLAINTEXT,
            is_production_migration,
            migration_database_transport_mode,
            operations_database_transport_mode,
        )

        transport_mode = (
            migration_database_transport_mode(resolved)
            if is_production_migration()
            else operations_database_transport_mode(resolved)
        )
        if (
            transport_mode == DATABASE_TRANSPORT_PRE_PRIVATE_LINE_PLAINTEXT
        ):

            @event.listens_for(selected_engine, "connect")
            def _validate_pre_private_line_transport(
                dbapi_connection: Any,
                _connection_record: Any,
            ) -> None:
                cursor = dbapi_connection.cursor()
                try:
                    cursor.execute(
                        """
                        SELECT
                            COALESCE(
                                (
                                    SELECT ssl
                                    FROM pg_stat_ssl
                                    WHERE pid = pg_backend_pid()
                                ),
                                false
                            ),
                            current_setting('ssl')
                        """
                    )
                    row = cursor.fetchone()
                    if row != (False, "off"):
                        raise RuntimeError(
                            "PRE_PRIVATE_LINE_DATABASE_TRANSPORT_MISMATCH"
                        )
                finally:
                    try:
                        cursor.close()
                    finally:
                        dbapi_connection.rollback()

            setattr(
                selected_engine,
                "_tit_pre_private_line_transport_guard",
                True,
            )

    return selected_engine


engine = build_engine()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


@contextmanager
def session_scope(bind: Engine | None = None) -> Iterator[Session]:
    maker = SessionLocal if bind is None or bind is engine else sessionmaker(
        bind=bind,
        expire_on_commit=False,
        class_=Session,
    )
    session = maker()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def database_health(bind: Engine | None = None) -> dict:
    selected = bind or engine
    with selected.connect() as connection:
        connection.execute(text("SELECT 1"))
    return {
        "status": "ok",
        "dialect": selected.dialect.name,
        "driver": selected.dialect.driver,
        "postgresql_target": selected.dialect.name == "postgresql",
    }


def database_pool_status(bind: Engine | None = None) -> dict[str, int | str]:
    """Return bounded, credential-free pool diagnostics for operations checks."""

    selected = bind or engine
    pool = selected.pool
    status: dict[str, int | str] = {"pool_class": type(pool).__name__}
    for key, method_name in (
        ("pool_size", "size"),
        ("checked_in", "checkedin"),
        ("checked_out", "checkedout"),
        ("overflow", "overflow"),
    ):
        method = getattr(pool, method_name, None)
        if callable(method):
            value = int(method())
            status[key] = max(0, value) if key == "overflow" else value
    return status
