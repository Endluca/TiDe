from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool, text

from app.runtime_settings import (
    is_production_migration,
    validate_alembic_runtime,
    validate_production_migration_identity,
    validate_production_migration_transport,
)


validate_alembic_runtime()

from app.database import Base, DEFAULT_DATABASE_URL, build_engine  # noqa: E402
from app.migration_ownership import include_owned_object  # noqa: E402
from app import db_models  # noqa: F401
from app import config_models  # noqa: F401
from app import auth_models  # noqa: F401


config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL))
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_object=include_owned_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = build_engine(
        config.get_main_option("sqlalchemy.url"),
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        if is_production_migration():
            identity = connection.execute(
                text(
                    """
                    SELECT
                        current_user,
                        current_database(),
                        role.rolsuper
                    FROM pg_roles AS role
                    WHERE role.rolname = current_user
                    """
                )
            ).one()
            validate_production_migration_identity(
                role=identity[0],
                database=identity[1],
                is_superuser=identity[2],
            )
            transport = connection.execute(
                text(
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
            ).one()
            validate_production_migration_transport(
                session_ssl=transport[0],
                server_ssl=transport[1],
            )
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            include_object=include_owned_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
