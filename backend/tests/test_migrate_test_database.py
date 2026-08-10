from __future__ import annotations

import subprocess

import pytest
from sqlalchemy.exc import ArgumentError

from scripts import migrate_test_database


def test_ambient_libpq_environment_is_rejected_without_exposing_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "do-not-print-this-value"
    monkeypatch.setenv("PGHOSTADDR", "127.0.0.1")
    monkeypatch.setenv("PGOPTIONS", secret)

    with pytest.raises(RuntimeError) as exc_info:
        migrate_test_database._reject_ambient_libpq_environment()

    message = str(exc_info.value)
    assert "PGHOSTADDR" in message
    assert "PGOPTIONS" in message
    assert secret not in message


def test_owner_url_environment_is_rejected_without_exposing_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "do-not-print-this-password"
    monkeypatch.setenv(
        "TIT_DATABASE_OWNER_URL",
        f"postgresql+psycopg://postgres:{secret}@db.invalid/test",
    )

    with pytest.raises(RuntimeError) as exc_info:
        migrate_test_database._database_url()

    assert "macOS Keychain" in str(exc_info.value)
    assert secret not in str(exc_info.value)


def test_expected_database_is_required_before_credentials_are_loaded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TIT_MIGRATION_EXPECTED_DATABASE", raising=False)
    monkeypatch.setattr(
        migrate_test_database,
        "_database_url",
        lambda: (_ for _ in ()).throw(
            AssertionError("credentials must not be loaded before the target guard")
        ),
    )

    with pytest.raises(
        RuntimeError,
        match="TIT_MIGRATION_EXPECTED_DATABASE is required",
    ):
        migrate_test_database.main()


def test_expected_database_is_locked_to_the_approved_v2_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "TIT_MIGRATION_EXPECTED_DATABASE",
        "tit_growth_test",
    )

    with pytest.raises(RuntimeError, match="must be tit_growth_test_v2"):
        migrate_test_database._expected_database()


@pytest.mark.parametrize(
    ("database_url", "error"),
    [
        (
            "postgresql+psycopg://postgres:hidden@wrong.example/"
            "tit_growth_test_v2",
            "unapproved test host",
        ),
        (
            "postgresql+psycopg://postgres:hidden@"
            "ai-efficiency-postgresql-20260722194941.pods.test.51talk.biz:15432/"
            "tit_growth_test_v2",
            "unapproved test port",
        ),
        (
            "postgresql+psycopg://another_owner:hidden@"
            "ai-efficiency-postgresql-20260722194941.pods.test.51talk.biz:5432/"
            "tit_growth_test_v2",
            "unapproved owner role",
        ),
        (
            "postgresql+psycopg://postgres:hidden@"
            "ai-efficiency-postgresql-20260722194941.pods.test.51talk.biz:5432/"
            "postgres",
            "unapproved test database",
        ),
    ],
)
def test_approved_target_guard_rejects_wrong_identity(
    database_url: str,
    error: str,
) -> None:
    with pytest.raises(RuntimeError, match=error):
        migrate_test_database._validate_approved_test_identity(database_url)


@pytest.mark.parametrize(
    "query_override",
    [
        "host=wrong.example",
        "hostaddr=203.0.113.9",
        "port=15432",
        "user=another_owner",
        "dbname=postgres",
        "database=postgres",
        "service=wrong-cluster",
        "servicefile=/tmp/unsafe",
        "options=-c%20role%3Danother_owner",
    ],
)
def test_approved_target_guard_rejects_connection_identity_query_overrides(
    query_override: str,
) -> None:
    database_url = (
        "postgresql+psycopg://postgres:hidden@"
        "ai-efficiency-postgresql-20260722194941.pods.test.51talk.biz/"
        f"tit_growth_test_v2?{query_override}"
    )

    with pytest.raises(RuntimeError, match="unapproved connection query option"):
        migrate_test_database._validate_approved_test_identity(database_url)


def test_approved_target_guard_accepts_only_the_expected_safe_url() -> None:
    migrate_test_database._validate_approved_test_identity(
        "postgresql+psycopg://postgres:hidden@"
        "ai-efficiency-postgresql-20260722194941.pods.test.51talk.biz:5432/"
        "tit_growth_test_v2?sslmode=disable&connect_timeout=8"
    )


def test_approved_target_guard_requires_explicit_port() -> None:
    with pytest.raises(RuntimeError, match="unapproved test port"):
        migrate_test_database._validate_approved_test_identity(
            "postgresql+psycopg://postgres:hidden@"
            "ai-efficiency-postgresql-20260722194941.pods.test.51talk.biz/"
            "tit_growth_test_v2?sslmode=disable&connect_timeout=8"
        )


def test_url_database_mismatch_fails_before_connecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    password = "do-not-print-this-password"
    database_url = (
        f"postgresql+psycopg://owner:{password}@127.0.0.1/tit_growth_test"
    )
    monkeypatch.setattr(
        migrate_test_database,
        "_current_identity",
        lambda _url: (_ for _ in ()).throw(
            AssertionError("URL mismatch must fail before connecting")
        ),
    )

    with pytest.raises(RuntimeError) as exc_info:
        migrate_test_database._validate_database_target(
            database_url,
            "tit_growth_test_v2",
        )

    message = str(exc_info.value)
    assert "expected=tit_growth_test_v2" in message
    assert "url_database=tit_growth_test" in message
    assert password not in message


def test_invalid_url_error_does_not_retain_credentials() -> None:
    password = "do-not-print-this-password"

    with pytest.raises(RuntimeError) as exc_info:
        migrate_test_database._database_name_from_url(
            f"not a database URL containing {password}"
        )

    assert str(exc_info.value) == "TIT database owner URL is invalid"
    assert exc_info.value.__cause__ is None
    assert password not in str(exc_info.value)


def test_live_guard_initialization_error_does_not_retain_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    password = "do-not-print-this-password"
    database_url = (
        f"postgresql+psycopg://owner:{password}@127.0.0.1/tit_growth_test_v2"
    )
    monkeypatch.setattr(
        migrate_test_database,
        "create_engine",
        lambda url, **_kwargs: (_ for _ in ()).throw(
            ArgumentError(f"cannot initialize {url}")
        ),
    )

    with pytest.raises(RuntimeError) as exc_info:
        migrate_test_database._current_identity(database_url)

    assert str(exc_info.value) == "Unable to initialize the database target guard"
    assert exc_info.value.__cause__ is None
    assert password not in str(exc_info.value)


def test_live_database_mismatch_fails_before_alembic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    password = "do-not-print-this-password"
    database_url = (
        f"postgresql+psycopg://owner:{password}@127.0.0.1/tit_growth_test_v2"
    )
    monkeypatch.setenv(
        "TIT_MIGRATION_EXPECTED_DATABASE",
        "tit_growth_test_v2",
    )
    monkeypatch.setattr(
        migrate_test_database,
        "_database_url",
        lambda: database_url,
    )
    monkeypatch.setattr(
        migrate_test_database,
        "_validate_approved_test_identity",
        lambda _url: None,
    )
    monkeypatch.setattr(
        migrate_test_database,
        "_current_identity",
        lambda _url: ("tit_growth_test", "postgres"),
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Alembic must not run after a live database mismatch")
        ),
    )

    with pytest.raises(RuntimeError) as exc_info:
        migrate_test_database.main()

    message = str(exc_info.value)
    assert "expected=tit_growth_test_v2" in message
    assert "actual=tit_growth_test" in message
    assert password not in message


def test_live_user_mismatch_fails_before_alembic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = (
        "postgresql+psycopg://postgres:hidden@"
        "ai-efficiency-postgresql-20260722194941.pods.test.51talk.biz:5432/"
        "tit_growth_test_v2"
    )
    monkeypatch.setenv(
        "TIT_MIGRATION_EXPECTED_DATABASE",
        "tit_growth_test_v2",
    )
    monkeypatch.setattr(
        migrate_test_database,
        "_database_url",
        lambda: database_url,
    )
    monkeypatch.setattr(
        migrate_test_database,
        "_current_identity",
        lambda _url: ("tit_growth_test_v2", "another_owner"),
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Alembic must not run after a live user mismatch")
        ),
    )

    with pytest.raises(RuntimeError, match="actual_user=another_owner"):
        migrate_test_database.main()


def test_matching_url_and_live_database_run_alembic_after_both_guards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = (
        "postgresql+psycopg://owner:secret@127.0.0.1/tit_growth_test_v2"
    )
    events: list[str] = []
    commands: list[list[str]] = []

    monkeypatch.setenv(
        "TIT_MIGRATION_EXPECTED_DATABASE",
        "tit_growth_test_v2",
    )
    monkeypatch.setattr(
        migrate_test_database,
        "_reject_ambient_libpq_environment",
        lambda: events.append("libpq-environment-checked"),
    )
    monkeypatch.setattr(
        migrate_test_database,
        "_database_url",
        lambda: database_url,
    )
    monkeypatch.setattr(
        migrate_test_database,
        "_validate_approved_test_identity",
        lambda _url: events.append("approved-target-checked"),
    )

    def current_identity(received_url: str) -> tuple[str, str]:
        assert received_url == database_url
        events.append("live-database-checked")
        return "tit_growth_test_v2", "postgres"

    def run(command: list[str], **kwargs: object) -> None:
        assert events == [
            "libpq-environment-checked",
            "approved-target-checked",
            "live-database-checked",
        ]
        assert kwargs["env"]["DATABASE_URL"] == database_url  # type: ignore[index]
        commands.append(command)

    monkeypatch.setattr(
        migrate_test_database,
        "_current_identity",
        current_identity,
    )
    monkeypatch.setattr(subprocess, "run", run)

    assert migrate_test_database.main() == 0
    assert [command[1:] for command in commands] == [
        ["upgrade", "head"],
        ["current"],
    ]
