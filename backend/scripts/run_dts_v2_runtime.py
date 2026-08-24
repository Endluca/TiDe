from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import signal
import sys
import time
from urllib.parse import urlparse
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.pool import NullPool
from sqlalchemy import text

from app.database import build_engine
from app.dts_v2_runtime_composition import (
    DOMAIN_COMPONENT,
    FAVORITE_COMPONENT,
    OUTBOX_COMPONENT,
    RUNTIME_COMPONENTS,
    RUNTIME_ROLES,
    DtsV2RuntimeCompositionError,
    build_domain_worker,
    build_favorite_worker,
    build_outbox_worker,
    read_runtime_health,
    runtime_health_ready,
    validate_runtime_startup,
)
from app.dts_v2_teacher_time_recheck import (
    PostgresDtsV2TeacherTimeRecheckStore,
    TeacherTimeRecheckHealthV2,
    build_teacher_time_recheck_worker,
)
from app.runtime_settings import operations_database_transport_mode


_STOP = False
_URL_ENV = {
    DOMAIN_COMPONENT: "TIT_V2_DOMAIN_DATABASE_URL",
    OUTBOX_COMPONENT: "TIT_V2_OUTBOX_DATABASE_URL",
    FAVORITE_COMPONENT: "TIT_V2_FAVORITE_DATABASE_URL",
}
_DEFAULT_HEARTBEAT = {
    component: Path(f"/tmp/tit-v2-{component}-heartbeat")
    for component in RUNTIME_COMPONENTS
}
_DEFAULT_READINESS = {
    component: Path(f"/tmp/tit-v2-{component}-readiness")
    for component in RUNTIME_COMPONENTS
}


class DtsV2RuntimeRunnerError(RuntimeError):
    pass


@dataclass(frozen=True)
class _OutboxWorkers:
    outbox: Any
    teacher_time_recheck: Any


@dataclass(frozen=True)
class _OutboxRuntimeSnapshot:
    runtime: Any
    teacher_time_recheck: TeacherTimeRecheckHealthV2

    def __getattr__(self, name: str) -> Any:
        return getattr(self.runtime, name)


def _signal_stop(_signum: int, _frame: Any) -> None:
    global _STOP
    _STOP = True


def _positive_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise DtsV2RuntimeRunnerError(f"{name}_INVALID") from exc
    if not minimum <= value <= maximum:
        raise DtsV2RuntimeRunnerError(f"{name}_INVALID")
    return value


def _database_url(component: str) -> str:
    name = _URL_ENV[component]
    value = os.environ.get(name, "").strip()
    if not value.startswith(("postgresql://", "postgresql+psycopg://")):
        raise DtsV2RuntimeRunnerError(f"{name}_POSTGRESQL_REQUIRED")
    parsed = urlparse(
        value.replace("postgresql+psycopg://", "postgresql://", 1)
    )
    if parsed.username != RUNTIME_ROLES[component]:
        raise DtsV2RuntimeRunnerError(
            "DTS_V2_RUNTIME_DATABASE_URL_ROLE_MISMATCH"
        )
    expected = os.environ.get("TIT_V2_EXPECTED_DATABASE", "").strip()
    if not expected:
        raise DtsV2RuntimeRunnerError("TIT_V2_EXPECTED_DATABASE_REQUIRED")
    if parsed.path.removeprefix("/") != expected:
        raise DtsV2RuntimeRunnerError(
            "DTS_V2_RUNTIME_DATABASE_URL_TARGET_MISMATCH"
        )
    if os.environ.get("APP_ENV", "local").strip().lower() in {
        "prod",
        "production",
    }:
        try:
            operations_database_transport_mode(value)
        except ValueError as exc:
            raise DtsV2RuntimeRunnerError(
                "DTS_V2_RUNTIME_DATABASE_TRANSPORT_INVALID"
            ) from exc
    return value


def _safe_identity() -> dict[str, str]:
    return {"pipeline_contract": "single-event-pipeline-v1"}


def _qualification_grants_enabled_from_env() -> bool:
    value = os.environ.get(
        "TIT_IRREVERSIBLE_QUALIFICATION_GRANTS_ENABLED",
        "",
    ).strip()
    if value == "true":
        return True
    if value == "false":
        return False
    raise DtsV2RuntimeRunnerError(
        "TIT_IRREVERSIBLE_QUALIFICATION_GRANTS_ENABLED_INVALID"
    )


def _build_worker(component: str, engine, identity: dict[str, str]):
    batch_size = _positive_int("TIT_V2_RUNTIME_BATCH_SIZE", 25, 1, 1000)
    if component == DOMAIN_COMPONENT:
        return (
            build_domain_worker(
                engine,
                worker_id=f"dts-v2-domain:{os.getpid()}",
                cutover_coverage_identity={
                    "protocol_version": "dts-single-runtime-coverage-v1",
                    **identity,
                },
                lease_seconds=_positive_int(
                    "TIT_V2_DOMAIN_LEASE_SECONDS", 120, 15, 300
                ),
            ),
            batch_size,
        )
    if component == FAVORITE_COMPONENT:
        return build_favorite_worker(engine), batch_size
    if component == OUTBOX_COMPONENT:
        return (
            _OutboxWorkers(
                outbox=build_outbox_worker(engine),
                teacher_time_recheck=build_teacher_time_recheck_worker(
                    engine,
                    worker_id=f"dts-v2-time-recheck:{os.getpid()}",
                    lease_seconds=_positive_int(
                        "TIT_V2_TIME_RECHECK_LEASE_SECONDS", 120, 15, 300
                    ),
                ),
            ),
            batch_size,
        )
    raise DtsV2RuntimeRunnerError("DTS_V2_RUNTIME_COMPONENT_INVALID")


def _run_worker_once(component: str, worker: Any, batch_size: int) -> dict[str, Any]:
    if component == DOMAIN_COMPONENT:
        reaped = worker.reap_expired(max_claims=batch_size)
        result = dict(worker.run_once(max_claims=batch_size))
        result["expired_reaped"] = reaped
        return result
    if component == OUTBOX_COMPONENT:
        outbox = dict(worker.outbox.run_once(max_events=batch_size))
        recheck = dict(
            worker.teacher_time_recheck.run_once(max_claims=batch_size)
        )
        return {
            **{f"outbox_{key}": value for key, value in outbox.items()},
            **{
                f"teacher_time_recheck_{key}": value
                for key, value in recheck.items()
            },
        }
    return dict(
        worker.run_once(
            worker_id=f"dts-v2-favorite:{os.getpid()}",
            max_observations=batch_size,
            reap_limit=batch_size,
        )
    )


def _runtime_snapshot(
    engine,
    *,
    component: str,
    expected_database: str,
    threshold: int,
):
    with engine.begin() as connection:
        validate_runtime_startup(
            connection,
            component=component,
            expected_database=expected_database,
        )
        snapshot = read_runtime_health(
            connection,
            component=component,
            stale_after_seconds=threshold,
        )
        if snapshot.mode != "V2_PRIMARY" or snapshot.projection_generation != 1:
            raise DtsV2RuntimeRunnerError(
                "DTS_SINGLE_PIPELINE_DATABASE_STATE_INVALID"
            )
        if component == OUTBOX_COMPONENT:
            database_gate = connection.execute(
                text(
                    "SELECT qualification_grants_enabled "
                    "FROM public.dts_pipeline_control "
                    "WHERE control_id='PRIMARY'"
                )
            ).scalar_one_or_none()
            if type(database_gate) is not bool:
                raise DtsV2RuntimeRunnerError(
                    "DTS_V2_QUALIFICATION_GATE_UNAVAILABLE"
                )
            if database_gate is not _qualification_grants_enabled_from_env():
                raise DtsV2RuntimeRunnerError(
                    "DTS_V2_QUALIFICATION_GATE_CONFIG_MISMATCH"
                )
            recheck = PostgresDtsV2TeacherTimeRecheckStore().read_health(
                connection,
                stale_after_seconds=threshold,
            )
            if (
                recheck.mode != snapshot.mode
                or recheck.projection_generation
                != snapshot.projection_generation
            ):
                raise DtsV2RuntimeRunnerError(
                    "DTS_V2_TIME_RECHECK_HEALTH_STATE_MISMATCH"
                )
            return _OutboxRuntimeSnapshot(
                runtime=snapshot,
                teacher_time_recheck=recheck,
            )
        return snapshot


def _component_active(component: str, snapshot: Any) -> bool:
    del component
    return snapshot.mode == "V2_PRIMARY" and snapshot.projection_generation == 1


def _component_ready(component: str, snapshot: Any) -> bool:
    if _component_active(component, snapshot):
        runtime_ready = runtime_health_ready(
            snapshot.runtime
            if isinstance(snapshot, _OutboxRuntimeSnapshot)
            else snapshot,
            component=component,
        )
        if component == OUTBOX_COMPONENT and isinstance(
            snapshot, _OutboxRuntimeSnapshot
        ):
            return runtime_ready and snapshot.teacher_time_recheck.ready
        return runtime_ready
    return False


def _safe_payload(component: str, snapshot, identity: dict[str, str]) -> dict[str, Any]:
    payload = {
        "protocol_version": "dts-v2-runtime-file-v1",
        "component": component,
        "mode": snapshot.mode,
        "projection_generation": snapshot.projection_generation,
        "active": _component_active(component, snapshot),
        **identity,
        "runnable_count": snapshot.runnable_count,
        "active_lease_count": snapshot.active_lease_count,
        "expired_lease_count": snapshot.expired_lease_count,
        "business_wait_count": snapshot.business_wait_count,
        "dead_count": snapshot.dead_count,
        "stale_runnable_count": snapshot.stale_runnable_count,
        "oldest_runnable_age_seconds": snapshot.oldest_runnable_age_seconds,
        "oldest_active_lease_age_seconds": (
            snapshot.oldest_active_lease_age_seconds
        ),
        "observed_unix_seconds": int(time.time()),
    }
    if isinstance(snapshot, _OutboxRuntimeSnapshot):
        recheck = snapshot.teacher_time_recheck
        payload["teacher_time_recheck"] = {
            "schedule_due": recheck.schedule_due,
            "current_date_missing_count": recheck.current_date_missing_count,
            "runnable_count": recheck.runnable_count,
            "active_lease_count": recheck.active_lease_count,
            "expired_lease_count": recheck.expired_lease_count,
            "dead_count": recheck.dead_count,
            "stale_runnable_count": recheck.stale_runnable_count,
            "oldest_runnable_age_seconds": (
                recheck.oldest_runnable_age_seconds
            ),
        }
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="ascii",
    )
    temporary.replace(path)


def _remove(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _file_is_fresh(path: Path, max_age_seconds: int) -> bool:
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return False
    return 0 <= age <= max_age_seconds


def run(args: argparse.Namespace) -> int:
    component = args.component
    identity = _safe_identity()
    expected_database = os.environ.get("TIT_V2_EXPECTED_DATABASE", "").strip()
    engine = build_engine(_database_url(component), poolclass=NullPool)
    try:
        snapshot = _runtime_snapshot(
            engine,
            component=component,
            expected_database=expected_database,
            threshold=args.stale_after_seconds,
        )
        if args.healthcheck:
            if not _file_is_fresh(
                args.heartbeat_path, args.max_heartbeat_age_seconds
            ) or not _file_is_fresh(
                args.readiness_path, args.max_readiness_age_seconds
            ):
                raise DtsV2RuntimeRunnerError(
                    "DTS_V2_RUNTIME_HEALTH_FILE_STALE"
                )
            if not _component_ready(component, snapshot):
                raise DtsV2RuntimeRunnerError(
                    "DTS_V2_RUNTIME_DATABASE_NOT_READY"
                )
            return 0

        worker = None
        batch_size = _positive_int(
            "TIT_V2_RUNTIME_BATCH_SIZE", 25, 1, 1000
        )
        while not _STOP:
            snapshot = _runtime_snapshot(
                engine,
                component=component,
                expected_database=expected_database,
                threshold=args.stale_after_seconds,
            )
            active = _component_active(component, snapshot)
            if active:
                if worker is None:
                    worker, batch_size = _build_worker(
                        component, engine, identity
                    )
                result = _run_worker_once(component, worker, batch_size)
                snapshot = _runtime_snapshot(
                    engine,
                    component=component,
                    expected_database=expected_database,
                    threshold=args.stale_after_seconds,
                )
            else:
                raise DtsV2RuntimeRunnerError(
                    "DTS_SINGLE_PIPELINE_DATABASE_STATE_INVALID"
                )
            payload = _safe_payload(component, snapshot, identity)
            payload["last_run_counts"] = {
                key: value
                for key, value in sorted(result.items())
                if type(value) is int
            }
            _write_json(args.heartbeat_path, payload)
            if _component_ready(component, snapshot):
                _write_json(args.readiness_path, payload)
            else:
                _remove(args.readiness_path)
            if not args.watch:
                return 0
            deadline = time.monotonic() + args.interval_seconds
            while not _STOP and time.monotonic() < deadline:
                time.sleep(min(0.25, deadline - time.monotonic()))
        return 0
    finally:
        engine.dispose()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--component",
        choices=sorted(RUNTIME_COMPONENTS),
        required=True,
    )
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--healthcheck", action="store_true")
    parser.add_argument("--interval-seconds", type=float, default=3.0)
    parser.add_argument("--stale-after-seconds", type=int, default=900)
    parser.add_argument("--max-heartbeat-age-seconds", type=int, default=90)
    parser.add_argument("--max-readiness-age-seconds", type=int, default=90)
    parser.add_argument("--heartbeat-path", type=Path)
    parser.add_argument("--readiness-path", type=Path)
    return parser


def main() -> int:
    parser = _parser()
    args = parser.parse_args()
    if args.interval_seconds <= 0 or args.interval_seconds > 60:
        parser.error("--interval-seconds must be in (0,60]")
    args.heartbeat_path = args.heartbeat_path or _DEFAULT_HEARTBEAT[args.component]
    args.readiness_path = args.readiness_path or _DEFAULT_READINESS[args.component]
    signal.signal(signal.SIGTERM, _signal_stop)
    signal.signal(signal.SIGINT, _signal_stop)
    try:
        return run(args)
    except (DtsV2RuntimeCompositionError, DtsV2RuntimeRunnerError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception:
        print("DTS_V2_RUNTIME_UNEXPECTED", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
