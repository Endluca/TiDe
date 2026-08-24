from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Mapping
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import build_engine  # noqa: E402
from app.dts_source_scope_snapshot_coordinator import (  # noqa: E402
    DtsSourceScopeSnapshotCoordinator,
    DtsSourceScopeSnapshotError,
    PostgresDtsSourceScopeSnapshotStore,
    SNAPSHOT_ID_PREFIX,
    database_error_code,
    load_candidate_artifact,
)


_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate, dry-run, publish and read back one externally exported "
            "DTS v2 source-scope snapshot. The command never connects to a "
            "source database or broker."
        )
    )
    subparsers = parser.add_subparsers(dest="action", required=True)

    validate = subparsers.add_parser(
        "validate",
        help="Validate local candidate/profile evidence without database access.",
    )
    _add_candidate_arguments(validate)

    subparsers.add_parser(
        "health",
        help="Check the dedicated database role, rev83 schema and function ACL.",
    )

    dry_run = subparsers.add_parser(
        "dry-run",
        help="Validate evidence and read the target head/scope without writes.",
    )
    _add_candidate_arguments(dry_run)
    _add_runtime_arguments(dry_run)

    apply = subparsers.add_parser(
        "apply",
        help="Run begin, idempotent stage, verify, publish and final readback.",
    )
    _add_candidate_arguments(apply)
    _add_runtime_arguments(apply)

    readback = subparsers.add_parser(
        "readback",
        help="Read one candidate lifecycle without returning rows or tokens.",
    )
    readback.add_argument("--snapshot-id", required=True)
    return parser


def _add_candidate_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--candidate")
    parser.add_argument("--profile-manifest")
    parser.add_argument("--expected-artifact-sha256")
    parser.add_argument("--expected-profile-manifest-sha256")


def _add_runtime_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--owner")
    parser.add_argument("--lease-seconds", type=int)
    parser.add_argument("--stage-batch-size", type=int)


def run(
    args: argparse.Namespace,
    *,
    environ: Mapping[str, str] | None = None,
) -> int:
    values = os.environ if environ is None else environ
    if args.action == "validate":
        candidate = _candidate(args, values)
        _print_json(
            {
                "ok": True,
                "action": "validate",
                "writes_performed": False,
                "candidate": candidate.public_summary(),
            }
        )
        return 0

    database_url = _required_setting(
        values.get("TIT_DTS_SCOPE_COORDINATOR_DATABASE_URL"),
        "TIT_DTS_SCOPE_COORDINATOR_DATABASE_URL_REQUIRED",
    )
    if database_url.startswith("sqlite"):
        raise DtsSourceScopeSnapshotError(
            "DTS_SOURCE_SCOPE_POSTGRESQL_REQUIRED"
        )
    engine = build_engine(database_url, poolclass=NullPool)
    store = PostgresDtsSourceScopeSnapshotStore()
    try:
        if args.action == "health":
            with engine.connect() as connection:
                health = store.read_health(connection)
            _print_json(
                {
                    "ok": health.ready,
                    "action": "health",
                    **health.public_summary(),
                }
            )
            return 0 if health.ready else 3

        if args.action == "readback":
            snapshot_id = _snapshot_id(args.snapshot_id)
            with engine.connect() as connection:
                health = store.read_health(connection)
                if not health.ready:
                    raise DtsSourceScopeSnapshotError(health.code)
                snapshot = store.read_snapshot(connection, snapshot_id)
            if snapshot is None:
                raise DtsSourceScopeSnapshotError(
                    "DTS_SOURCE_SCOPE_SNAPSHOT_NOT_FOUND"
                )
            _print_json(
                {
                    "ok": True,
                    "action": "readback",
                    "writes_performed": False,
                    "snapshot": snapshot,
                }
            )
            return 0

        candidate = _candidate(args, values)
        coordinator = DtsSourceScopeSnapshotCoordinator(
            engine=engine,
            owner=_runtime_owner(args, values),
            lease_seconds=_bounded_int(
                args.lease_seconds,
                values.get("TIT_DTS_SCOPE_COORDINATOR_LEASE_SECONDS"),
                default=300,
                minimum=15,
                maximum=300,
                error="DTS_SOURCE_SCOPE_LEASE_SECONDS_INVALID",
            ),
            stage_batch_size=_bounded_int(
                args.stage_batch_size,
                values.get("TIT_DTS_SCOPE_COORDINATOR_STAGE_BATCH_SIZE"),
                default=100,
                minimum=1,
                maximum=1_000,
                error="DTS_SOURCE_SCOPE_STAGE_BATCH_SIZE_INVALID",
            ),
            store=store,
        )
        if args.action == "dry-run":
            health, target = coordinator.dry_run(candidate)
            _print_json(
                {
                    "ok": True,
                    "action": "dry-run",
                    "writes_performed": False,
                    "database": health.public_summary(),
                    "candidate": candidate.public_summary(),
                    "readback": target.public_summary(),
                    "publication_boundary": (
                        "CURRENT_IDENTICAL_V2_CONFIRMED_ONLY"
                    ),
                }
            )
            return 0
        result = coordinator.apply(candidate)
        _print_json(
            {
                "ok": True,
                "action": "apply",
                "writes_performed": True,
                **result.public_summary(),
            }
        )
        return 0
    finally:
        engine.dispose()


def _candidate(
    args: argparse.Namespace,
    environ: Mapping[str, str],
):
    candidate_path = _required_setting(
        args.candidate
        or environ.get("TIT_DTS_SCOPE_CANDIDATE_PATH"),
        "TIT_DTS_SCOPE_CANDIDATE_PATH_REQUIRED",
    )
    profile_path = _required_setting(
        args.profile_manifest
        or environ.get("TIT_DTS_SCOPE_PROFILE_MANIFEST_PATH"),
        "TIT_DTS_SCOPE_PROFILE_MANIFEST_PATH_REQUIRED",
    )
    artifact_hash = _required_sha256(
        args.expected_artifact_sha256
        or environ.get("TIT_DTS_SCOPE_CANDIDATE_FILE_SHA256"),
        "TIT_DTS_SCOPE_CANDIDATE_FILE_SHA256_REQUIRED",
    )
    profile_hash = _required_sha256(
        args.expected_profile_manifest_sha256
        or environ.get("TIT_DTS_SCOPE_PROFILE_MANIFEST_SHA256"),
        "TIT_DTS_SCOPE_PROFILE_MANIFEST_SHA256_REQUIRED",
    )
    return load_candidate_artifact(
        candidate_path,
        profile_manifest_path=profile_path,
        expected_artifact_sha256=artifact_hash,
        expected_profile_manifest_sha256=profile_hash,
    )


def _runtime_owner(
    args: argparse.Namespace,
    environ: Mapping[str, str],
) -> str:
    return _required_setting(
        args.owner or environ.get("TIT_DTS_SCOPE_COORDINATOR_OWNER"),
        "TIT_DTS_SCOPE_COORDINATOR_OWNER_REQUIRED",
    )


def _snapshot_id(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value.startswith(SNAPSHOT_ID_PREFIX)
        or _SHA256_PATTERN.fullmatch(value.removeprefix(SNAPSHOT_ID_PREFIX))
        is None
    ):
        raise DtsSourceScopeSnapshotError(
            "DTS_SOURCE_SCOPE_SNAPSHOT_ID_INVALID"
        )
    return value


def _required_setting(value: object, error: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.strip() != value
    ):
        raise DtsSourceScopeSnapshotError(error)
    return value


def _required_sha256(value: object, error: str) -> str:
    text_value = _required_setting(value, error)
    if _SHA256_PATTERN.fullmatch(text_value) is None:
        raise DtsSourceScopeSnapshotError(error)
    return text_value


def _bounded_int(
    cli_value: int | None,
    env_value: object,
    *,
    default: int,
    minimum: int,
    maximum: int,
    error: str,
) -> int:
    raw_value: object = cli_value if cli_value is not None else env_value
    if raw_value is None or raw_value == "":
        return default
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise DtsSourceScopeSnapshotError(error) from exc
    if not minimum <= value <= maximum:
        raise DtsSourceScopeSnapshotError(error)
    return value


def _print_json(value: Mapping[str, object], *, stderr: bool = False) -> None:
    print(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        file=sys.stderr if stderr else sys.stdout,
        flush=True,
    )


def main(argv: list[str] | None = None) -> int:
    try:
        return run(_parser().parse_args(argv))
    except DtsSourceScopeSnapshotError as exc:
        error_code = str(exc)
    except SQLAlchemyError as exc:
        error_code = database_error_code(exc)
    except Exception:
        error_code = "DTS_SOURCE_SCOPE_UNEXPECTED_ERROR"
    _print_json(
        {"ok": False, "error_code": error_code},
        stderr=True,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
