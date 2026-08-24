#!/usr/bin/env python3

"""Safely load one non-secret Gaea application configuration file.

The file is deliberately not sourced by a shell.  It is a versioned allowlist
of literal, non-secret settings; platform environment variables keep priority
and remain the only supported place for credentials.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from urllib.parse import urlsplit


EX_CONFIG = 78
MAX_FILE_BYTES = 128 * 1024
KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")
PLACEHOLDER_MARKERS = ("CHANGE_ME", "REPLACE_ME", "REPLACE_WITH_")
IMAGE_INTERNAL_DTS_KEYS = frozenset(
    {"TIT_DTS_INGEST_HEARTBEAT", "TIT_DTS_INGEST_READINESS"}
)

# Only literal, non-secret settings read by the application image belong here.
# Adding a runtime setting is intentionally a code-reviewed schema change.
ALLOWED_FILE_KEYS = frozenset(
    {
        "ACCESS_TOKEN_TTL_SECONDS",
        "AGENT_PROVIDER",
        "AGENT_REASONING_EFFORT",
        "AGENT_TIMEOUT_SECONDS",
        "ALLOWED_EMAIL_DOMAINS",
        "APP_ENV",
        "BACKGROUND_JOBS_ENABLED",
        "BACKGROUND_JOB_LEASE_MS",
        "CDN_API_ENDPOINT",
        "CDN_REGION_ID",
        "CORS_ORIGINS",
        "CRM_ENTRY_URL",
        "CRM_SSO_AUDIENCE",
        "CRM_SSO_CLOCK_TOLERANCE_SECONDS",
        "CRM_SSO_EXCHANGE_TTL_SECONDS",
        "CRM_SSO_ISSUER",
        "CRM_SSO_MAX_TTL_SECONDS",
        "DATABASE_CONNECTION_TIMEOUT_MS",
        "DATABASE_MAX_CONNECTIONS",
        "DATABASE_REQUIRED",
        "DATABASE_STATEMENT_TIMEOUT_MS",
        "EMAIL_VERIFICATION_TTL_MINUTES",
        "FILE_ALLOWED_MIME_TYPES",
        "FILE_STORAGE_PROVIDER",
        "FILE_UPLOAD_TTL_MINUTES",
        "GROWTH_STAGE_NOTIFICATION_POLL_INTERVAL_MS",
        "GROWTH_STAGE_NOTIFICATION_SCHEDULER_ENABLED",
        "KUOZHI_COURSE_CONFIG_PATH",
        "KUOZHI_COURSE_URL",
        "KUOZHI_DETAIL_HOST_IP",
        "KUOZHI_DETAIL_RETRY_COUNT",
        "KUOZHI_DETAIL_TIMEOUT_MS",
        "KUOZHI_DETAIL_URL",
        "KUOZHI_LOGIN_URL",
        "LOCAL_FILE_STORAGE_DIR",
        "LOG_LEVEL",
        "MAIL_API_APP_NAME",
        "MAIL_API_LANG",
        "MAIL_API_TIMEOUT_MS",
        "MAIL_API_URL",
        "MAIL_API_USER_ID",
        "MAIL_API_USER_TYPE",
        "MAIL_DELIVERY_PROVIDER",
        "MAIL_PASSWORD_RESET_TEMPLATE_ID",
        "MAIL_VERIFY_TEMPLATE_ID",
        "MODELARK_BASE_URL",
        "MODELARK_ENABLED",
        "MODELARK_MODEL",
        "MODELARK_TIMEOUT_MS",
        "MULTIPART_UPLOAD_MAX_CONCURRENCY",
        "OPENAI_AGENT_MODEL",
        "OSS_BUCKET",
        "OSS_ENDPOINT",
        "OSS_REGION",
        "OSS_TIMEOUT_MS",
        "PASSWORD_RESET_TTL_MINUTES",
        "PERSONALIZED_TASK_NOTIFICATION_POLL_INTERVAL_MS",
        "PERSONALIZED_TASK_NOTIFICATION_ROLLOUT_AT",
        "PERSONALIZED_TASK_NOTIFICATION_SCHEDULER_ENABLED",
        "PUBLIC_API_URL",
        "PUBLIC_APP_URL",
        "REFRESH_TOKEN_TTL_DAYS",
        "SHIWEN_ALLOW_TIDE_FIXTURE_FALLBACK",
        "SHIWEN_READ_MODE",
        "SHIWEN_TEACHER_IDENTITY_VIEW",
        "SUPPORT_TICKET_CLEANUP_BATCH_SIZE",
        "SUPPORT_TICKET_CLEANUP_POLL_INTERVAL_MS",
        "SYSTEM_NOTIFICATION_BATCH_SIZE",
        "SYSTEM_NOTIFICATION_CONFIG_PATH",
        "SYSTEM_NOTIFICATION_POLL_INTERVAL_MS",
        "SYSTEM_NOTIFICATION_PUBLISHER_ENABLED",
        "TASK_CATALOG_PUBLIC_WRITE",
        "TEACHER_AUTH_MODE",
        "TIDE_TEACHER_HOST",
        "TIDE_TEACHER_NODE_ENV",
        "TIDE_TRUSTED_PROXY_CIDRS",
        "TIT_ALLOWED_HOSTS",
        "TIT_ALLOWED_ORIGINS",
        "TIT_API_KEEPALIVE_SECONDS",
        "TIT_API_LIMIT_CONCURRENCY",
        "TIT_API_WORKERS",
        "TIT_ARGON2_MAX_CONCURRENCY",
        "TIT_DB_APPLICATION_NAME",
        "TIT_DB_CONNECT_TIMEOUT_SECONDS",
        "TIT_DB_IDLE_TRANSACTION_TIMEOUT_MS",
        "TIT_DB_LOCK_TIMEOUT_MS",
        "TIT_DB_MAX_OVERFLOW",
        "TIT_DB_POOL_RECYCLE_SECONDS",
        "TIT_DB_POOL_SIZE",
        "TIT_DB_POOL_TIMEOUT_SECONDS",
        "TIT_DB_STATEMENT_TIMEOUT_MS",
        "TIT_HEALTHCHECK_HOST",
        "TIT_IRREVERSIBLE_QUALIFICATION_GRANTS_ENABLED",
        "TIT_LOGIN_RATE_LIMIT_ATTEMPTS",
        "TIT_LOGIN_RATE_LIMIT_MAX_KEYS",
        "TIT_LOGIN_RATE_LIMIT_WINDOW_SECONDS",
        "TIT_MIGRATION_MODE",
        "TIT_SCORE_DB_APPLICATION_NAME",
        "TIT_SCORE_DB_MAX_OVERFLOW",
        "TIT_SCORE_DB_POOL_SIZE",
        "TIT_SCORE_DB_STATEMENT_TIMEOUT_MS",
        "TIT_SESSION_TTL_HOURS",
        "TIT_SLOW_REQUEST_MS",
        "TIT_SOURCE_WIDE_ENABLED",
        "TIT_SOURCE_WORKER_DB_APPLICATION_NAME",
        "TIT_SOURCE_WORKER_DB_MAX_OVERFLOW",
        "TIT_SOURCE_WORKER_DB_POOL_SIZE",
        "TIT_SOURCE_WORKER_DB_STATEMENT_TIMEOUT_MS",
        "TIT_SOURCE_WORKER_EXPECTED_DATABASE",
        "TIT_SOURCE_WORKER_MAX_PENDING_AGE_SECONDS",
        "TIT_TRUSTED_PROXY_IPS",
        "TIT_V2_DOMAIN_LEASE_SECONDS",
        "TIT_V2_TIME_RECHECK_LEASE_SECONDS",
        "TIT_V2_EXPECTED_DATABASE",
        "TIT_V2_RUNTIME_BATCH_SIZE",
        "TIT_V2_RUNTIME_ENABLED",
        "TRUST_PROXY_HOPS",
    }
)


class RuntimeEnvError(ValueError):
    pass


def _decode_value(raw_value: str, *, line_number: int, key: str) -> str:
    value = raw_value.strip()
    if not value or value[0] not in {"'", '"'}:
        return value
    quote = value[0]
    if len(value) < 2 or value[-1] != quote:
        raise RuntimeEnvError(
            f"line {line_number}: {key} has an unterminated quoted value"
        )
    return value[1:-1]


def _read_runtime_file(path_value: str) -> tuple[dict[str, str], bytes]:
    path = Path(path_value)
    if not path.is_absolute():
        raise RuntimeEnvError("TIT_RUNTIME_ENV_FILE must be an absolute path")
    try:
        resolved = path.resolve(strict=True)
        metadata = resolved.stat()
    except OSError as exc:
        raise RuntimeEnvError("TIT_RUNTIME_ENV_FILE is missing or unreadable") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise RuntimeEnvError("TIT_RUNTIME_ENV_FILE must resolve to a regular file")
    if metadata.st_size > MAX_FILE_BYTES:
        raise RuntimeEnvError("TIT_RUNTIME_ENV_FILE exceeds 128 KiB")
    try:
        raw = resolved.read_bytes()
    except OSError as exc:
        raise RuntimeEnvError("TIT_RUNTIME_ENV_FILE is unreadable") from exc
    if len(raw) > MAX_FILE_BYTES:
        raise RuntimeEnvError("TIT_RUNTIME_ENV_FILE exceeds 128 KiB")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise RuntimeEnvError("TIT_RUNTIME_ENV_FILE must be valid UTF-8") from exc
    if "\x00" in text:
        raise RuntimeEnvError("TIT_RUNTIME_ENV_FILE must not contain NUL bytes")

    values: dict[str, str] = {}
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            raise RuntimeEnvError(
                f"line {line_number}: shell export syntax is not supported"
            )
        if "=" not in stripped:
            raise RuntimeEnvError(
                f"line {line_number}: expected a literal KEY=VALUE assignment"
            )
        raw_key, raw_value = stripped.split("=", 1)
        key = raw_key.strip()
        if not KEY_PATTERN.fullmatch(key):
            raise RuntimeEnvError(f"line {line_number}: invalid variable name")
        if key in values:
            raise RuntimeEnvError(f"line {line_number}: duplicate key {key}")
        if key.startswith("TII_"):
            raise RuntimeEnvError(
                f"line {line_number}: unsupported key {key}; use the reviewed TIT_ name"
            )
        if key.startswith("TIT_DTS_"):
            raise RuntimeEnvError(
                f"line {line_number}: DTS settings are forbidden in application config"
            )
        if key not in ALLOWED_FILE_KEYS:
            raise RuntimeEnvError(
                f"line {line_number}: key {key} is not an allowed non-secret setting"
            )
        value = _decode_value(raw_value, line_number=line_number, key=key)
        if any(marker in value.upper() for marker in PLACEHOLDER_MARKERS):
            raise RuntimeEnvError(
                f"line {line_number}: key {key} still contains a placeholder"
            )
        if "://" in value:
            try:
                parsed = urlsplit(value)
            except ValueError as exc:
                raise RuntimeEnvError(
                    f"line {line_number}: key {key} contains an invalid URL"
                ) from exc
            if parsed.username is not None or parsed.password is not None:
                raise RuntimeEnvError(
                    f"line {line_number}: key {key} must not contain URL credentials"
                )
        values[key] = value
    return values, raw


def _load_configuration(
    environ: dict[str, str],
) -> tuple[dict[str, str], str, int, int]:
    if "TIT_RUNTIME_ENV_FILE" not in environ:
        return dict(environ), "none", 0, 0
    configured_path = environ["TIT_RUNTIME_ENV_FILE"].strip()
    if not configured_path:
        raise RuntimeEnvError("TIT_RUNTIME_ENV_FILE must not be empty")
    file_values, raw = _read_runtime_file(configured_path)
    invalid_ambient = sorted(name for name in environ if name.startswith("TII_"))
    if invalid_ambient:
        raise RuntimeEnvError(
            "platform environment contains unsupported TII_ variable names"
        )
    unexpected_dts = sorted(
        name
        for name in environ
        if name.startswith("TIT_DTS_") and name not in IMAGE_INTERNAL_DTS_KEYS
    )
    if unexpected_dts:
        raise RuntimeEnvError(
            "application platform environment must not contain TIT_DTS_ settings"
        )
    if "TIT_SOURCE_WORKER_DATABASE_URL" in environ:
        raise RuntimeEnvError(
            "TIT_SOURCE_WORKER_DATABASE_URL is forbidden; SourceWide reuses DATABASE_URL"
        )
    legacy_database_keys = sorted(
        name
        for name in environ
        if name == "COMPANY_TEST_DATABASE_ENABLED"
        or name.startswith(("TIDE_ADMIN_DB_", "TIDE_APP_DB_"))
    )
    if legacy_database_keys:
        raise RuntimeEnvError(
            "legacy company-test database variables are forbidden in "
            "PRE/application runtime"
        )

    merged = dict(file_values)
    merged.update(environ)
    merged.setdefault("TIT_V2_RUNTIME_ENABLED", "false")
    required_exact = {
        "APP_ENV": "production",
        "TASK_CATALOG_PUBLIC_WRITE": "false",
        "TIT_MIGRATION_MODE": "false",
    }
    for key, expected in required_exact.items():
        if merged.get(key) != expected:
            raise RuntimeEnvError(f"effective {key} must be exactly {expected}")
    for key in (
        "TIT_IRREVERSIBLE_QUALIFICATION_GRANTS_ENABLED",
        "TIT_SOURCE_WIDE_ENABLED",
        "TIT_V2_RUNTIME_ENABLED",
    ):
        if merged.get(key) not in {"true", "false"}:
            raise RuntimeEnvError(f"effective {key} must be exactly true or false")
    if merged.get("TIT_PROCESS_PROFILE") != "application":
        raise RuntimeEnvError(
            "the application runtime config loader requires TIT_PROCESS_PROFILE=application"
        )
    fingerprint = "sha256:" + hashlib.sha256(raw).hexdigest()
    overridden = sum(1 for key in file_values if key in environ)
    return merged, fingerprint, len(file_values), overridden


def _write_fingerprint(path: Path, fingerprint: str) -> None:
    parent = path.parent
    try:
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if parent.is_symlink() or not parent.is_dir():
            raise RuntimeEnvError("runtime fingerprint directory is invalid")
        if stat.S_IMODE(parent.stat().st_mode) & 0o077:
            raise RuntimeEnvError(
                "runtime fingerprint directory permissions are too broad"
            )
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="ascii",
            dir=parent,
            prefix=".fingerprint-",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            os.chmod(temporary, 0o600)
            handle.write(fingerprint + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        raise RuntimeEnvError("unable to record runtime config fingerprint") from exc


def _require_fingerprint(path: Path, fingerprint: str) -> None:
    try:
        recorded = path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as exc:
        raise RuntimeEnvError("runtime config fingerprint is missing or unreadable") from exc
    if recorded != fingerprint:
        raise RuntimeEnvError(
            "mounted runtime config changed; a RollingUpdate is required"
        )


def _parse_arguments(argv: list[str]) -> tuple[str, Path, list[str]]:
    if (
        len(argv) < 5
        or argv[0] not in {"start", "healthcheck"}
        or argv[1] != "--fingerprint-file"
        or argv[3] != "--"
    ):
        raise RuntimeEnvError(
            "usage: runtime-env.py {start|healthcheck} "
            "--fingerprint-file ABSOLUTE_PATH -- COMMAND [ARG ...]"
        )
    fingerprint_file = Path(argv[2])
    if not fingerprint_file.is_absolute():
        raise RuntimeEnvError("--fingerprint-file must be absolute")
    return argv[0], fingerprint_file, argv[4:]


def main(argv: list[str] | None = None) -> int:
    try:
        mode, fingerprint_file, command = _parse_arguments(
            sys.argv[1:] if argv is None else argv
        )
        merged, fingerprint, key_count, overridden = _load_configuration(
            dict(os.environ)
        )
        if mode == "start":
            _write_fingerprint(fingerprint_file, fingerprint)
            print(
                "runtime env loaded: "
                f"mode={'file' if fingerprint != 'none' else 'platform'} "
                f"file_keys={key_count} platform_overrides={overridden}",
                file=sys.stderr,
                flush=True,
            )
        else:
            _require_fingerprint(fingerprint_file, fingerprint)
        os.execvpe(command[0], command, merged)
    except RuntimeEnvError as exc:
        print(f"runtime env error: {exc}", file=sys.stderr, flush=True)
        return EX_CONFIG
    except OSError:
        print("runtime env error: unable to execute configured command", file=sys.stderr)
        return EX_CONFIG
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
