from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


DEFAULT_DATABASE_URL = "postgresql+psycopg://tit_growth_app@/tit_growth?host=/tmp"


class Base(DeclarativeBase):
    pass


def database_url() -> str:
    return os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)


def build_engine(url: str | None = None) -> Engine:
    resolved = url or database_url()
    connect_args = {"check_same_thread": False} if resolved.startswith("sqlite") else {}
    return create_engine(
        resolved,
        future=True,
        pool_pre_ping=True,
        connect_args=connect_args,
    )


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
