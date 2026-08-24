"""Beijing-date teacher recheck over the proved DTS v2 aggregate vector.

The source aggregates change only when DTS changes.  ``NEW`` to ``EXISTING``
is different: it changes because the Beijing business date advances.  This
worker turns that clock edge into a durable dirty-key input, rebuilds the same
teacher plan used by the TEACHER Outbox consumer, and materializes only the
date-derived fields through a protected PostgreSQL command.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
import json
import re
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from .dts_v2_runtime_guard import (
    DtsV2PrimaryTransactionGuard,
    DtsV2RuntimeTransactionState,
    PostgresDtsV2PrimaryTransactionGuard,
    guarded_runtime_state,
)
from .dts_v2_score_projection_store import PostgresDtsV2ScoreProjectionStore
from .dts_v2_teacher_materializer import DtsV2TeacherScoreRefresher
from .dts_v2_teacher_outbox_processor import (
    DtsV2TeacherAggregateBundleReader,
    TeacherMaterializationPlanV2,
    build_teacher_materialization_plan_v2,
)


TEACHER_TIME_RECHECK_MATERIALIZE_REGPROCEDURE = (
    "public.materialize_teacher_time_recheck_v2("
    "jsonb,bigint,bigint,bigint,text,bigint)"
)
TEACHER_TIME_RECHECK_CAPABILITIES = (
    "public.enqueue_due_teacher_time_rechecks_v2()",
    "public.claim_teacher_time_rechecks_v2(text,integer,integer)",
    "public.complete_teacher_time_recheck_v2("
    "text,date,text,bigint,bigint)",
    "public.fail_teacher_time_recheck_v2("
    "text,date,text,bigint,bigint,text)",
    "public.reap_expired_teacher_time_rechecks_v2(integer)",
    TEACHER_TIME_RECHECK_MATERIALIZE_REGPROCEDURE,
    "public.teacher_time_recheck_result_proof_v1(text,date)",
    "public.dts_v2_teacher_time_recheck_health_v1(bigint)",
)

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class DtsV2TeacherTimeRecheckError(RuntimeError):
    """The clock-driven teacher projection cannot be proved or settled."""


@dataclass(frozen=True)
class TeacherTimeRecheckClaimV2:
    teacher_id: str
    business_date_beijing: date
    lease_token: str
    claimed_work_revision: int
    row_version: int

    def __post_init__(self) -> None:
        _required_text(self.teacher_id, "DTS_V2_TIME_RECHECK_TEACHER_ID_INVALID")
        _required_text(self.lease_token, "DTS_V2_TIME_RECHECK_LEASE_INVALID")
        if self.claimed_work_revision < 1 or self.row_version < 1:
            raise DtsV2TeacherTimeRecheckError(
                "DTS_V2_TIME_RECHECK_LEASE_INVALID"
            )


@dataclass(frozen=True)
class TeacherTimeRecheckHealthV2:
    protocol_version: str
    mode: str
    projection_generation: int
    schedule_due: bool
    current_date_missing_count: int
    runnable_count: int
    active_lease_count: int
    expired_lease_count: int
    dead_count: int
    stale_runnable_count: int
    oldest_runnable_age_seconds: int | None

    @property
    def ready(self) -> bool:
        return (
            not self.schedule_due
            and self.current_date_missing_count == 0
            and self.expired_lease_count == 0
            and self.dead_count == 0
            and self.stale_runnable_count == 0
        )


class TeacherTimeRecheckStoreV2(Protocol):
    def enqueue_due(self, connection: Connection) -> Mapping[str, Any]: ...

    def claim(
        self,
        connection: Connection,
        *,
        worker_id: str,
        batch_size: int,
        lease_seconds: int,
    ) -> tuple[TeacherTimeRecheckClaimV2, ...]: ...

    def complete(
        self, connection: Connection, claim: TeacherTimeRecheckClaimV2
    ) -> Mapping[str, Any]: ...

    def fail(
        self,
        connection: Connection,
        claim: TeacherTimeRecheckClaimV2,
        *,
        error_code: str,
    ) -> Mapping[str, Any]: ...

    def reap(self, connection: Connection, *, batch_size: int) -> int: ...


class PostgresDtsV2TeacherTimeRecheckStore:
    """Typed client; it deliberately contains no direct queue DML."""

    def enqueue_due(self, connection: Connection) -> Mapping[str, Any]:
        return _json_object(
            connection.execute(
                text("SELECT public.enqueue_due_teacher_time_rechecks_v2()")
            ).scalar_one()
        )

    def claim(
        self,
        connection: Connection,
        *,
        worker_id: str,
        batch_size: int,
        lease_seconds: int,
    ) -> tuple[TeacherTimeRecheckClaimV2, ...]:
        _required_text(worker_id, "DTS_V2_TIME_RECHECK_WORKER_ID_INVALID")
        if not 1 <= batch_size <= 1000 or not 15 <= lease_seconds <= 300:
            raise DtsV2TeacherTimeRecheckError(
                "DTS_V2_TIME_RECHECK_CLAIM_INVALID"
            )
        rows = connection.execute(
            text(
                """
                SELECT teacher_id,business_date_beijing,lease_token,
                       claimed_work_revision,row_version
                FROM public.claim_teacher_time_rechecks_v2(
                  :worker_id,:batch_size,:lease_seconds
                )
                """
            ),
            {
                "worker_id": worker_id,
                "batch_size": batch_size,
                "lease_seconds": lease_seconds,
            },
        ).mappings()
        return tuple(
            TeacherTimeRecheckClaimV2(
                teacher_id=str(row["teacher_id"]),
                business_date_beijing=_date(
                    row["business_date_beijing"],
                    "DTS_V2_TIME_RECHECK_BUSINESS_DATE_INVALID",
                ),
                lease_token=str(row["lease_token"]),
                claimed_work_revision=int(row["claimed_work_revision"]),
                row_version=int(row["row_version"]),
            )
            for row in rows
        )

    def complete(
        self, connection: Connection, claim: TeacherTimeRecheckClaimV2
    ) -> Mapping[str, Any]:
        return _json_object(
            connection.execute(
                text(
                    """
                    SELECT public.complete_teacher_time_recheck_v2(
                      :teacher_id,:business_date,:lease_token,
                      :claimed_work_revision,:row_version
                    )
                    """
                ),
                _claim_params(claim),
            ).scalar_one()
        )

    def fail(
        self,
        connection: Connection,
        claim: TeacherTimeRecheckClaimV2,
        *,
        error_code: str,
    ) -> Mapping[str, Any]:
        _required_text(error_code, "DTS_V2_TIME_RECHECK_ERROR_CODE_INVALID")
        return _json_object(
            connection.execute(
                text(
                    """
                    SELECT public.fail_teacher_time_recheck_v2(
                      :teacher_id,:business_date,:lease_token,
                      :claimed_work_revision,:row_version,:error_code
                    )
                    """
                ),
                {**_claim_params(claim), "error_code": error_code},
            ).scalar_one()
        )

    def reap(self, connection: Connection, *, batch_size: int) -> int:
        if not 1 <= batch_size <= 1000:
            raise DtsV2TeacherTimeRecheckError(
                "DTS_V2_TIME_RECHECK_REAP_INVALID"
            )
        value = connection.execute(
            text(
                "SELECT public.reap_expired_teacher_time_rechecks_v2("
                ":batch_size)"
            ),
            {"batch_size": batch_size},
        ).scalar_one()
        if type(value) is not int or value < 0:
            raise DtsV2TeacherTimeRecheckError(
                "DTS_V2_TIME_RECHECK_REAP_RESULT_INVALID"
            )
        return value

    def read_health(
        self,
        connection: Connection,
        *,
        stale_after_seconds: int,
    ) -> TeacherTimeRecheckHealthV2:
        if not 1 <= stale_after_seconds <= 86400:
            raise DtsV2TeacherTimeRecheckError(
                "DTS_V2_TIME_RECHECK_HEALTH_THRESHOLD_INVALID"
            )
        value = _json_object(
            connection.execute(
                text(
                    "SELECT public.dts_v2_teacher_time_recheck_health_v1("
                    ":threshold)"
                ),
                {"threshold": stale_after_seconds},
            ).scalar_one()
        )
        fields = set(TeacherTimeRecheckHealthV2.__dataclass_fields__)
        if set(value) != fields or value.get("protocol_version") != (
            "dts-v2-teacher-time-recheck-health-v1"
        ):
            raise DtsV2TeacherTimeRecheckError(
                "DTS_V2_TIME_RECHECK_HEALTH_SHAPE_INVALID"
            )
        try:
            snapshot = TeacherTimeRecheckHealthV2(**value)
        except TypeError as exc:
            raise DtsV2TeacherTimeRecheckError(
                "DTS_V2_TIME_RECHECK_HEALTH_SHAPE_INVALID"
            ) from exc
        if (
            snapshot.mode
            not in {"V1_COMPAT_DUAL_CAPTURE", "V2_PRIMARY", "ROLLED_BACK"}
            or type(snapshot.projection_generation) is not int
            or snapshot.projection_generation < 0
            or type(snapshot.schedule_due) is not bool
            or any(
                type(getattr(snapshot, name)) is not int
                or getattr(snapshot, name) < 0
                for name in (
                    "current_date_missing_count",
                    "runnable_count",
                    "active_lease_count",
                    "expired_lease_count",
                    "dead_count",
                    "stale_runnable_count",
                )
            )
            or (
                snapshot.oldest_runnable_age_seconds is not None
                and (
                    type(snapshot.oldest_runnable_age_seconds) is not int
                    or snapshot.oldest_runnable_age_seconds < 0
                )
            )
        ):
            raise DtsV2TeacherTimeRecheckError(
                "DTS_V2_TIME_RECHECK_HEALTH_SHAPE_INVALID"
            )
        return snapshot


class PostgresDtsV2TeacherTimeRecheckMaterializer:
    """Persist one date-derived plan and refresh its score in the same tx."""

    def __init__(
        self,
        *,
        score_refresher: DtsV2TeacherScoreRefresher,
        primary_guard: DtsV2PrimaryTransactionGuard | None = None,
    ) -> None:
        if not callable(getattr(score_refresher, "refresh_teacher", None)):
            raise DtsV2TeacherTimeRecheckError(
                "DTS_V2_TIME_RECHECK_SCORE_REFRESHER_REQUIRED"
            )
        self.score_refresher = score_refresher
        self.primary_guard = primary_guard or PostgresDtsV2PrimaryTransactionGuard()

    def apply_teacher_plan(
        self,
        connection: Connection,
        plan: TeacherMaterializationPlanV2,
        *,
        triggering_event_id: str,
        claimed_work_revision: int,
    ) -> Mapping[str, int]:
        if not isinstance(plan, TeacherMaterializationPlanV2):
            raise DtsV2TeacherTimeRecheckError(
                "DTS_V2_TIME_RECHECK_PLAN_REQUIRED"
            )
        if not isinstance(plan.business_date_beijing, date):
            raise DtsV2TeacherTimeRecheckError(
                "DTS_V2_TIME_RECHECK_BUSINESS_DATE_INVALID"
            )
        _required_text(
            triggering_event_id, "DTS_V2_TIME_RECHECK_EVENT_ID_INVALID"
        )
        if claimed_work_revision < 1:
            raise DtsV2TeacherTimeRecheckError(
                "DTS_V2_TIME_RECHECK_WORK_REVISION_INVALID"
            )
        revisions = dict(plan.regional_revisions)
        hashes = dict(plan.regional_state_sha256)
        if (
            set(revisions) != {"dom", "ovs"}
            or any(type(value) is not int or value < 1 for value in revisions.values())
            or set(hashes) != {"dom", "ovs"}
            or any(not isinstance(value, str) or _HASH_RE.fullmatch(value) is None for value in hashes.values())
        ):
            raise DtsV2TeacherTimeRecheckError(
                "DTS_V2_TIME_RECHECK_REGIONAL_VECTOR_INVALID"
            )
        generation = self.primary_guard.acquire(connection, component="OUTBOX")
        if generation is None:
            raise DtsV2TeacherTimeRecheckError(
                "DTS_V2_TIME_RECHECK_PRIMARY_MODE_REQUIRED"
            )
        values = plan.projection.values
        payload = {
            "v": 1,
            "teacher_id": plan.teacher_id,
            "teacher_id_type": plan.teacher_id_type,
            "business_date_beijing": plan.business_date_beijing.isoformat(),
            "regional_state_sha256": hashes,
            "time_values": {
                "job_days": values.get("job_days"),
                "job_month": values.get("job_month"),
                "online_status": values.get("online_status"),
                "online_status_evidence_status": values.get(
                    "online_status_evidence_status"
                ),
            },
        }
        raw = connection.execute(
            text(
                """
                SELECT public.materialize_teacher_time_recheck_v2(
                  CAST(:payload AS jsonb),:dom_revision,:ovs_revision,
                  :projection_generation,:event_id,:work_revision
                )
                """
            ),
            {
                "payload": json.dumps(
                    payload, sort_keys=True, separators=(",", ":")
                ),
                "dom_revision": revisions["dom"],
                "ovs_revision": revisions["ovs"],
                "projection_generation": generation,
                "event_id": triggering_event_id,
                "work_revision": claimed_work_revision,
            },
        ).scalar_one()
        counts = _int_counts(raw)
        proof = _json_object(
            connection.execute(
                text(
                    "SELECT public.teacher_time_recheck_result_proof_v1("
                    ":teacher_id,:business_date)"
                ),
                {
                    "teacher_id": plan.teacher_id,
                    "business_date": plan.business_date_beijing,
                },
            ).scalar_one()
        )
        if (
            proof.get("teacher_id") != plan.teacher_id
            or proof.get("business_date_beijing")
            != plan.business_date_beijing.isoformat()
            or proof.get("dom_aggregate_revision") != revisions["dom"]
            or proof.get("ovs_aggregate_revision") != revisions["ovs"]
            or proof.get("regional_state_sha256") != hashes
            or proof.get("projection_generation") != generation
            or proof.get("triggering_event_id") != triggering_event_id
            or proof.get("claimed_work_revision") != claimed_work_revision
            or proof.get("time_values") != payload["time_values"]
            or not isinstance(proof.get("plan_sha256"), str)
            or _HASH_RE.fullmatch(str(proof.get("plan_sha256"))) is None
        ):
            raise DtsV2TeacherTimeRecheckError(
                "DTS_V2_TIME_RECHECK_RESULT_READBACK_MISMATCH"
            )
        refreshed = self.score_refresher.refresh_teacher(
            connection,
            teacher_id=plan.teacher_id,
            projection_generation=generation,
        )
        return _merge_counts(counts, refreshed)


@dataclass
class TeacherTimeRecheckRunResultV2:
    active: bool = False
    enqueued: int = 0
    superseded: int = 0
    reaped: int = 0
    claimed: int = 0
    completed: int = 0
    retries: int = 0
    dead: int = 0
    materialization_counts: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "enqueued": self.enqueued,
            "superseded": self.superseded,
            "reaped": self.reaped,
            "claimed": self.claimed,
            "completed": self.completed,
            "retries": self.retries,
            "dead": self.dead,
            "materialization_counts": dict(
                sorted(self.materialization_counts.items())
            ),
        }


class DtsV2TeacherTimeRecheckWorker:
    def __init__(
        self,
        bind: Engine,
        *,
        worker_id: str,
        materializer: PostgresDtsV2TeacherTimeRecheckMaterializer,
        store: TeacherTimeRecheckStoreV2 | None = None,
        aggregate_reader: DtsV2TeacherAggregateBundleReader | None = None,
        primary_guard: DtsV2PrimaryTransactionGuard | None = None,
        lease_seconds: int = 120,
    ) -> None:
        if bind.dialect.name != "postgresql":
            raise DtsV2TeacherTimeRecheckError(
                "DTS_V2_TIME_RECHECK_POSTGRESQL_REQUIRED"
            )
        _required_text(worker_id, "DTS_V2_TIME_RECHECK_WORKER_ID_INVALID")
        if not 15 <= lease_seconds <= 300:
            raise DtsV2TeacherTimeRecheckError(
                "DTS_V2_TIME_RECHECK_LEASE_INVALID"
            )
        self.engine = bind
        self.worker_id = worker_id
        self.materializer = materializer
        self.store = store or PostgresDtsV2TeacherTimeRecheckStore()
        self.aggregate_reader = (
            aggregate_reader or DtsV2TeacherAggregateBundleReader()
        )
        self.primary_guard = primary_guard or PostgresDtsV2PrimaryTransactionGuard()
        self.lease_seconds = lease_seconds

    def run_once(self, *, max_claims: int = 25) -> dict[str, Any]:
        if not 1 <= max_claims <= 1000:
            raise DtsV2TeacherTimeRecheckError(
                "DTS_V2_TIME_RECHECK_BATCH_INVALID"
            )
        result = TeacherTimeRecheckRunResultV2()
        with self.engine.begin() as connection:
            claim_state = self._primary_state(connection)
            if claim_state is None:
                return result.as_dict()
            result.active = True
            scheduled = self.store.enqueue_due(connection)
            result.enqueued = _nonnegative_int(scheduled.get("enqueued"))
            result.superseded = _nonnegative_int(scheduled.get("superseded"))
            result.reaped = self.store.reap(connection, batch_size=max_claims)
            claims = self.store.claim(
                connection,
                worker_id=self.worker_id,
                batch_size=max_claims,
                lease_seconds=self.lease_seconds,
            )
        result.claimed = len(claims)
        for claim in claims:
            self._process_one(claim, claim_state=claim_state, result=result)
        return result.as_dict()

    def _process_one(
        self,
        claim: TeacherTimeRecheckClaimV2,
        *,
        claim_state: DtsV2RuntimeTransactionState,
        result: TeacherTimeRecheckRunResultV2,
    ) -> None:
        with self.engine.begin() as connection:
            process_state = self._primary_state(connection)
            if process_state is None or process_state != claim_state:
                return
            handler = connection.begin_nested()
            try:
                current = self.aggregate_reader.read_current_for_teacher(
                    connection, claim.teacher_id
                )
                legacy = connection.execute(
                    text(
                        """
                        SELECT first_open_slot_dt,first_booked_dt,
                               first_completed_dt
                        FROM public.teacher_source_wide
                        WHERE tchr_id=:teacher_id
                          AND v2_row_version IS NOT NULL
                        """
                    ),
                    {"teacher_id": claim.teacher_id},
                ).mappings().one_or_none()
                if legacy is None:
                    raise DtsV2TeacherTimeRecheckError(
                        "DTS_V2_TIME_RECHECK_TEACHER_WIDE_MISSING"
                    )
                plan = build_teacher_materialization_plan_v2(
                    teacher_id=claim.teacher_id,
                    regional_states={
                        region: snapshot.aggregate_state
                        for region, snapshot in current.regions.items()
                    },
                    regional_revisions={
                        region: snapshot.current_revision
                        for region, snapshot in current.regions.items()
                    },
                    regional_state_sha256={
                        region: snapshot.aggregate_state_sha256
                        for region, snapshot in current.regions.items()
                    },
                    business_date_beijing=claim.business_date_beijing,
                    legacy_first_dates=dict(legacy),
                )
                event_id = (
                    "teacher-time-recheck:"
                    f"{claim.business_date_beijing.isoformat()}:"
                    f"{claim.teacher_id}:{claim.claimed_work_revision}"
                )
                counts = self.materializer.apply_teacher_plan(
                    connection,
                    plan,
                    triggering_event_id=event_id,
                    claimed_work_revision=claim.claimed_work_revision,
                )
                completion = self.store.complete(connection, claim)
                handler.commit()
            except Exception:
                handler.rollback()
                failure = self.store.fail(
                    connection,
                    claim,
                    error_code="DOMAIN_PROJECTOR_TRANSIENT",
                )
                status = failure.get("status")
                if status == "RETRY":
                    result.retries += 1
                elif status == "DEAD":
                    result.dead += 1
                elif status != "PENDING":
                    raise DtsV2TeacherTimeRecheckError(
                        "DTS_V2_TIME_RECHECK_FAILURE_RESULT_INVALID"
                    )
                return
            if completion.get("status") == "COMPLETED":
                result.completed += 1
            elif completion.get("status") == "PENDING":
                result.superseded += 1
            else:
                raise DtsV2TeacherTimeRecheckError(
                    "DTS_V2_TIME_RECHECK_COMPLETE_RESULT_INVALID"
                )
            for name, count in _int_counts(counts).items():
                result.materialization_counts[name] = (
                    result.materialization_counts.get(name, 0) + count
                )

    def _primary_state(
        self, connection: Connection
    ) -> DtsV2RuntimeTransactionState | None:
        generation = self.primary_guard.acquire(connection, component="OUTBOX")
        if generation is None:
            return None
        state = guarded_runtime_state(connection)
        if state.projection_generation != generation:
            raise DtsV2TeacherTimeRecheckError(
                "DTS_V2_TIME_RECHECK_GUARD_STATE_MISMATCH"
            )
        return state


def build_teacher_time_recheck_worker(
    bind: Engine,
    *,
    worker_id: str,
    lease_seconds: int = 120,
) -> DtsV2TeacherTimeRecheckWorker:
    guard = PostgresDtsV2PrimaryTransactionGuard()
    return DtsV2TeacherTimeRecheckWorker(
        bind,
        worker_id=worker_id,
        materializer=PostgresDtsV2TeacherTimeRecheckMaterializer(
            score_refresher=PostgresDtsV2ScoreProjectionStore(
                require_guarded_generation=True
            ),
            primary_guard=guard,
        ),
        primary_guard=guard,
        lease_seconds=lease_seconds,
    )


def _claim_params(claim: TeacherTimeRecheckClaimV2) -> dict[str, Any]:
    return {
        "teacher_id": claim.teacher_id,
        "business_date": claim.business_date_beijing,
        "lease_token": claim.lease_token,
        "claimed_work_revision": claim.claimed_work_revision,
        "row_version": claim.row_version,
    }


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as exc:
            raise DtsV2TeacherTimeRecheckError(
                "DTS_V2_TIME_RECHECK_DATABASE_RESULT_INVALID"
            ) from exc
        if isinstance(decoded, dict):
            return decoded
    raise DtsV2TeacherTimeRecheckError(
        "DTS_V2_TIME_RECHECK_DATABASE_RESULT_INVALID"
    )


def _int_counts(value: Any) -> dict[str, int]:
    mapping = _json_object(value)
    result: dict[str, int] = {}
    for name, count in mapping.items():
        if (
            not isinstance(name, str)
            or not name
            or type(count) is not int
            or count < 0
        ):
            raise DtsV2TeacherTimeRecheckError(
                "DTS_V2_TIME_RECHECK_COUNT_RESULT_INVALID"
            )
        result[name] = count
    return result


def _merge_counts(*values: Mapping[str, Any]) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values:
        for name, count in _int_counts(value).items():
            result[name] = result.get(name, 0) + count
    return result


def _nonnegative_int(value: Any) -> int:
    if type(value) is not int or value < 0:
        raise DtsV2TeacherTimeRecheckError(
            "DTS_V2_TIME_RECHECK_COUNT_RESULT_INVALID"
        )
    return value


def _date(value: Any, error: str) -> date:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    raise DtsV2TeacherTimeRecheckError(error)


def _required_text(value: Any, error: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise DtsV2TeacherTimeRecheckError(error)
    return value


__all__ = [
    "DtsV2TeacherTimeRecheckError",
    "DtsV2TeacherTimeRecheckWorker",
    "PostgresDtsV2TeacherTimeRecheckMaterializer",
    "PostgresDtsV2TeacherTimeRecheckStore",
    "TEACHER_TIME_RECHECK_CAPABILITIES",
    "TEACHER_TIME_RECHECK_MATERIALIZE_REGPROCEDURE",
    "TeacherTimeRecheckClaimV2",
    "TeacherTimeRecheckHealthV2",
    "build_teacher_time_recheck_worker",
]
