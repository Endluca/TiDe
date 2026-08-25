"""Typed client for the protected DTS v2 dirty-key command surface.

This module deliberately contains no table INSERT/UPDATE/DELETE statements.
Queue authority lives in PostgreSQL SECURITY DEFINER functions so a crashed or
compromised Worker cannot forge a revision, lease, retry or completion state.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Mapping, Sequence

from sqlalchemy import text


_REGIONS = frozenset({"dom", "ovs"})
_DOMAIN_KEY_TYPES = frozenset(
    {"COURSE", "TEACHER", "TEACHER_STUDENT", "LABEL", "COMPLAINT_CATEGORY"}
)
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_DEPENDENCY_TYPE_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_DOM_STUDENT_TOKEN_RE = re.compile(r"^dom:v1:[0-9a-f]{64}$")


class DtsV2DirtyQueueError(RuntimeError):
    """The caller supplied an invalid typed dirty-queue command."""


@dataclass(frozen=True)
class DirtyKeyV2:
    source_region: str
    key_type: str
    key_part_1: str
    key_part_2: str = ""

    def __post_init__(self) -> None:
        if self.source_region not in _REGIONS:
            raise DtsV2DirtyQueueError("DIRTY_KEY_REGION_INVALID")
        if self.key_type not in _DOMAIN_KEY_TYPES:
            raise DtsV2DirtyQueueError("DIRTY_KEY_TYPE_INVALID")
        # Dirty identities are already canonicalized from their typed source
        # fields.  TEXT identifiers are byte-significant business values and
        # must never be normalized with ``strip()`` here.  PostgreSQL rejects
        # only an empty/all-whitespace part, matching the v2 identity CHECK.
        if (
            not isinstance(self.key_part_1, str)
            or not self.key_part_1
            or not self.key_part_1.strip()
        ):
            raise DtsV2DirtyQueueError("DIRTY_KEY_PART_INVALID")
        if not isinstance(self.key_part_2, str):
            raise DtsV2DirtyQueueError("DIRTY_KEY_PART_INVALID")
        if self.key_type in {"COURSE", "TEACHER", "LABEL"}:
            if self.key_part_2 != "":
                raise DtsV2DirtyQueueError("DIRTY_KEY_PART_INVALID")
        elif self.key_type == "COMPLAINT_CATEGORY":
            if self.source_region != "dom" or self.key_part_2 != "":
                raise DtsV2DirtyQueueError("DIRTY_KEY_PART_INVALID")
        elif not self.key_part_2:
            raise DtsV2DirtyQueueError("DIRTY_KEY_PART_INVALID")
        if (
            self.key_type == "TEACHER_STUDENT"
            and self.source_region == "dom"
            and not _DOM_STUDENT_TOKEN_RE.fullmatch(self.key_part_2)
        ):
            raise DtsV2DirtyQueueError("DIRTY_DOM_STUDENT_TOKEN_INVALID")


@dataclass(frozen=True)
class DirtyDependencyV2:
    dependency_type: str
    dependency_region: str
    dependency_key: str
    source_revision: int
    dependency_hash: str

    def __post_init__(self) -> None:
        if not _DEPENDENCY_TYPE_RE.fullmatch(self.dependency_type):
            raise DtsV2DirtyQueueError("DIRTY_DEPENDENCY_TYPE_INVALID")
        if self.dependency_region not in _REGIONS or not self.dependency_key:
            raise DtsV2DirtyQueueError("DIRTY_DEPENDENCY_IDENTITY_INVALID")
        if self.source_revision < 1 or not _HASH_RE.fullmatch(
            self.dependency_hash
        ):
            raise DtsV2DirtyQueueError("DIRTY_DEPENDENCY_EVIDENCE_INVALID")

    def as_payload(self) -> dict[str, Any]:
        return {
            "dependency_type": self.dependency_type,
            "dependency_region": self.dependency_region,
            "dependency_key": self.dependency_key,
            "source_revision": self.source_revision,
            "dependency_hash": self.dependency_hash,
        }


@dataclass(frozen=True)
class DirtyClaimV2:
    key: DirtyKeyV2
    lease_token: str
    claimed_work_revision: int
    row_version: int


def _json_result(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        decoded = json.loads(value)
        if isinstance(decoded, dict):
            return decoded
    raise DtsV2DirtyQueueError("DIRTY_DATABASE_RESULT_INVALID")


def _source_revision_enqueue_parameters(
    *,
    source_region: str,
    source_table: str,
    source_key: str,
    source_row_revision: int,
    dirty_key: DirtyKeyV2,
) -> dict[str, Any]:
    cross_region_category = (
        dirty_key.source_region == "dom"
        and dirty_key.key_type == "COMPLAINT_CATEGORY"
        and source_region in _REGIONS
    )
    cross_region_teacher_peer = (
        source_region == "dom"
        and source_table == "dom_teacher"
        and dirty_key.source_region == "ovs"
        and dirty_key.key_type == "TEACHER"
        and dirty_key.key_part_1 == source_key
        and dirty_key.key_part_2 == ""
    )
    if (
        source_region != dirty_key.source_region
        and not cross_region_category
        and not cross_region_teacher_peer
    ):
        raise DtsV2DirtyQueueError("DIRTY_SOURCE_REGION_MISMATCH")
    if not source_table or not source_key or source_row_revision < 1:
        raise DtsV2DirtyQueueError("DIRTY_SOURCE_REFERENCE_INVALID")
    return {
        "source_region": source_region,
        "source_table": source_table,
        "source_key": source_key,
        "source_row_revision": source_row_revision,
        "key_type": dirty_key.key_type,
        "key_part_1": dirty_key.key_part_1,
        "key_part_2": dirty_key.key_part_2,
        "peer_teacher": cross_region_teacher_peer,
    }


class DtsV2DirtyQueueStore:
    """Invoke dirty queue commands on an existing transaction connection."""

    def enqueue_source_revision(
        self,
        connection: Any,
        *,
        source_region: str,
        source_table: str,
        source_key: str,
        source_row_revision: int,
        dirty_key: DirtyKeyV2,
    ) -> dict[str, Any]:
        parameters = _source_revision_enqueue_parameters(
            source_region=source_region,
            source_table=source_table,
            source_key=source_key,
            source_row_revision=source_row_revision,
            dirty_key=dirty_key,
        )
        function_name = (
            "enqueue_peer_teacher_dirty_from_source_revision_v2"
            if parameters.pop("peer_teacher")
            else "enqueue_dirty_from_source_revision_v2"
        )
        value = connection.execute(
            text(
                f"""
                SELECT public.{function_name}(
                    :source_region,:source_table,:source_key,
                    :source_row_revision,:key_type,:key_part_1,:key_part_2
                )
                """
            ),
            parameters,
        ).scalar_one()
        return _json_result(value)

    def enqueue_source_revisions_batch(
        self,
        connection: Any,
        *,
        commands: Sequence[Mapping[str, Any]],
    ) -> tuple[dict[str, Any], ...]:
        if not commands:
            return ()
        payload: list[dict[str, Any]] = []
        for ordinal, command in enumerate(commands):
            dirty_key = command.get("dirty_key")
            if not isinstance(dirty_key, DirtyKeyV2):
                raise DtsV2DirtyQueueError("DIRTY_KEY_INVALID")
            source_row_revision = command.get("source_row_revision")
            if (
                isinstance(source_row_revision, bool)
                or not isinstance(source_row_revision, int)
            ):
                raise DtsV2DirtyQueueError("DIRTY_SOURCE_REFERENCE_INVALID")
            parameters = _source_revision_enqueue_parameters(
                source_region=str(command.get("source_region") or ""),
                source_table=str(command.get("source_table") or ""),
                source_key=str(command.get("source_key") or ""),
                source_row_revision=source_row_revision,
                dirty_key=dirty_key,
            )
            payload.append({"ordinal": ordinal, **parameters})
        rows = connection.execute(
            text(
                """
                WITH inputs AS MATERIALIZED (
                  SELECT *
                  FROM jsonb_to_recordset(CAST(:commands AS jsonb)) AS item(
                    ordinal integer,source_region text,source_table text,
                    source_key text,source_row_revision bigint,
                    key_type text,key_part_1 text,key_part_2 text,
                    peer_teacher boolean
                  )
                ), enqueued AS MATERIALIZED (
                  SELECT ordinal,
                    CASE WHEN peer_teacher THEN
                      public.enqueue_peer_teacher_dirty_from_source_revision_v2(
                        source_region,source_table,source_key,
                        source_row_revision,key_type,key_part_1,key_part_2
                      )
                    ELSE
                      public.enqueue_dirty_from_source_revision_v2(
                        source_region,source_table,source_key,
                        source_row_revision,key_type,key_part_1,key_part_2
                      )
                    END response
                  FROM inputs
                  ORDER BY ordinal
                )
                SELECT ordinal,response
                FROM enqueued
                ORDER BY ordinal
                """
            ),
            {
                "commands": json.dumps(
                    payload,
                    ensure_ascii=True,
                    separators=(",", ":"),
                )
            },
        ).mappings()
        results = tuple(_json_result(row["response"]) for row in rows)
        if len(results) != len(payload):
            raise DtsV2DirtyQueueError("DIRTY_DATABASE_RESULT_INVALID")
        return results

    def claim_domain(
        self,
        connection: Any,
        *,
        worker_id: str,
        batch_size: int,
        lease_seconds: int,
    ) -> tuple[DirtyClaimV2, ...]:
        if not worker_id or not 1 <= batch_size <= 1000:
            raise DtsV2DirtyQueueError("DIRTY_CLAIM_INVALID")
        if not 15 <= lease_seconds <= 300:
            raise DtsV2DirtyQueueError("DIRTY_LEASE_INVALID")
        rows = connection.execute(
            text(
                """
                SELECT source_region,key_type,key_part_1,key_part_2,
                       lease_token,claimed_work_revision,row_version
                FROM public.claim_domain_dirty_keys_v2(
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
            DirtyClaimV2(
                key=DirtyKeyV2(
                    source_region=str(row["source_region"]),
                    key_type=str(row["key_type"]),
                    key_part_1=str(row["key_part_1"]),
                    key_part_2=str(row["key_part_2"]),
                ),
                lease_token=str(row["lease_token"]),
                claimed_work_revision=int(row["claimed_work_revision"]),
                row_version=int(row["row_version"]),
            )
            for row in rows
        )

    def renew_domain(
        self,
        connection: Any,
        claim: DirtyClaimV2,
        *,
        lease_seconds: int,
    ) -> int:
        if not 15 <= lease_seconds <= 300:
            raise DtsV2DirtyQueueError("DIRTY_LEASE_INVALID")
        return int(
            connection.execute(
                text(
                    """
                    SELECT public.renew_domain_dirty_key_v2(
                        :source_region,:key_type,:key_part_1,:key_part_2,
                        :lease_token,:row_version,:lease_seconds
                    )
                    """
                ),
                {
                    **self._claim_params(claim),
                    "lease_seconds": lease_seconds,
                },
            ).scalar_one()
        )

    def complete_domain(
        self, connection: Any, claim: DirtyClaimV2
    ) -> dict[str, Any]:
        return self._claim_json_command(
            connection,
            claim,
            "complete_domain_dirty_key_v2",
        )

    def wait_domain(
        self,
        connection: Any,
        claim: DirtyClaimV2,
        dependencies: Sequence[DirtyDependencyV2],
    ) -> dict[str, Any]:
        if not dependencies:
            raise DtsV2DirtyQueueError("DIRTY_DEPENDENCY_INVALID")
        ordered = sorted(
            dependencies,
            key=lambda item: (
                item.dependency_type.encode("utf-8"),
                item.dependency_region.encode("utf-8"),
                item.dependency_key.encode("utf-8"),
            ),
        )
        identities = [
            (item.dependency_type, item.dependency_region, item.dependency_key)
            for item in ordered
        ]
        if len(set(identities)) != len(identities):
            raise DtsV2DirtyQueueError("DIRTY_DEPENDENCY_DUPLICATE")
        payload = json.dumps(
            [item.as_payload() for item in ordered],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        value = connection.execute(
            text(
                """
                SELECT public.wait_domain_dirty_key_v2(
                    :source_region,:key_type,:key_part_1,:key_part_2,
                    :lease_token,:claimed_work_revision,:row_version,
                    CAST(:dependencies AS jsonb)
                )
                """
            ),
            {**self._claim_params(claim), "dependencies": payload},
        ).scalar_one()
        return _json_result(value)

    def fail_domain(
        self,
        connection: Any,
        claim: DirtyClaimV2,
        *,
        error_code: str,
    ) -> dict[str, Any]:
        if not error_code:
            raise DtsV2DirtyQueueError("DIRTY_ERROR_INVALID")
        value = connection.execute(
            text(
                """
                SELECT public.fail_domain_dirty_key_v2(
                    :source_region,:key_type,:key_part_1,:key_part_2,
                    :lease_token,:claimed_work_revision,:row_version,
                    :error_code
                )
                """
            ),
            {**self._claim_params(claim), "error_code": error_code},
        ).scalar_one()
        return _json_result(value)

    def reap_domain(self, connection: Any, *, batch_size: int) -> int:
        if not 1 <= batch_size <= 1000:
            raise DtsV2DirtyQueueError("DIRTY_REAPER_INVALID")
        return int(
            connection.execute(
                text(
                    "SELECT public.reap_expired_domain_dirty_keys_v2("
                    ":batch_size)"
                ),
                {"batch_size": batch_size},
            ).scalar_one()
        )

    def recover(
        self,
        connection: Any,
        *,
        dirty_key: DirtyKeyV2,
        expected_dead_generation: int,
        expected_row_version: int,
        operator_request_id: str,
        reason: str,
    ) -> dict[str, Any]:
        if (
            expected_dead_generation < 1
            or expected_row_version < 1
            or not operator_request_id
            or not reason
        ):
            raise DtsV2DirtyQueueError("DIRTY_RECOVERY_INVALID")
        value = connection.execute(
            text(
                """
                SELECT public.recover_dts_dirty_key_v2(
                    :source_region,:key_type,:key_part_1,:key_part_2,
                    :dead_generation,:row_version,:operator_request_id,:reason
                )
                """
            ),
            {
                "source_region": dirty_key.source_region,
                "key_type": dirty_key.key_type,
                "key_part_1": dirty_key.key_part_1,
                "key_part_2": dirty_key.key_part_2,
                "dead_generation": expected_dead_generation,
                "row_version": expected_row_version,
                "operator_request_id": operator_request_id,
                "reason": reason,
            },
        ).scalar_one()
        return _json_result(value)

    @staticmethod
    def _claim_params(claim: DirtyClaimV2) -> dict[str, Any]:
        return {
            "source_region": claim.key.source_region,
            "key_type": claim.key.key_type,
            "key_part_1": claim.key.key_part_1,
            "key_part_2": claim.key.key_part_2,
            "lease_token": claim.lease_token,
            "claimed_work_revision": claim.claimed_work_revision,
            "row_version": claim.row_version,
        }

    def _claim_json_command(
        self,
        connection: Any,
        claim: DirtyClaimV2,
        function_name: str,
    ) -> dict[str, Any]:
        if function_name != "complete_domain_dirty_key_v2":
            raise DtsV2DirtyQueueError("DIRTY_COMMAND_INVALID")
        value = connection.execute(
            text(
                f"""
                SELECT public.{function_name}(
                    :source_region,:key_type,:key_part_1,:key_part_2,
                    :lease_token,:claimed_work_revision,:row_version
                )
                """
            ),
            self._claim_params(claim),
        ).scalar_one()
        return _json_result(value)
