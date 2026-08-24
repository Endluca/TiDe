"""Restricted application client for authoritative DTS source-scope snapshots.

This module deliberately does not connect to a source database or to DTS.  It
accepts one externally exported, hash-bound candidate artifact and invokes the
database-owned source-scope state machine.  PostgreSQL remains responsible for
the candidate lease, idempotency, fence/checkpoint proof, CDC catch-up,
replacement diff and atomic publication.  A missing row is meaningful only
inside a verified GLOBAL snapshot; the client never treats an arbitrary empty
artifact as proof by itself.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from .dts_source_contract_v2 import (
    V2_BUSINESS_SOURCE_TABLES,
    V2_SOURCE_FIELD_WHITELIST,
)
from .dts_source_profile_registry_v2 import (
    DtsSourceProfile,
    DtsSourceProfileManifestError,
    load_dts_source_profile_registry,
)


PROTOCOL_VERSION = "dts-source-scope-snapshot-candidate-v1"
SNAPSHOT_ID_PREFIX = "scope-v1:"
REQUIRED_DATABASE_ROLE = "tit_growth_app"
CURRENT_SCOPE_KIND = "CURRENT"
SUPPORTED_SCOPE_LEVELS = frozenset({"GLOBAL", "TEACHER"})
DEPENDENCY_FIELDS = (
    "category_ids",
    "course_ids",
    "label_ids",
    "student_subjects",
    "teacher_ids",
)

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_DOM_STUDENT_TOKEN_PATTERN = re.compile(r"^dom:v1:[0-9a-f]{64}$")
_DOM_RAW_STUDENT_FIELDS = frozenset(
    {"s_id", "student_id", "stu_id", "user_id"}
)
_STABLE_DATABASE_ERROR_PATTERN = re.compile(
    r"\b(?:CDC|DTS|HISTORY|SNAPSHOT|SOURCE|TEACHER)_[A-Z0-9_]+\b"
)
_MAX_JSON_BYTES = 256 * 1024 * 1024
_MAX_JSONL_BYTES = 8 * 1024 * 1024 * 1024
_MAX_JSONL_LINE_BYTES = 32 * 1024 * 1024
_MAX_ROWS = 5_000_000

_HEADER_FIELDS = frozenset(
    {
        "protocol_version",
        "snapshot_id",
        "source_region",
        "source_table",
        "scope_kind",
        "scope_level",
        "scope_key",
        "snapshot_as_of",
        "snapshot_consistency_token",
        "snapshot_consistency_token_sha256",
        "source_profile",
        "source_export_evidence_sha256",
        "fence_evidence_sha256",
        "partition_offsets",
        "history_from",
        "history_through",
    }
)
_SUMMARY_FIELDS = frozenset(
    {"row_count", "content_hash", "fence_hash", "candidate_sha256"}
)
_ROW_FIELDS = frozenset(
    {"source_key_data", "dependency_keys", "protected_source_row"}
)
_PROFILE_REFERENCE_FIELDS = frozenset(
    {"manifest_version", "manifest_sha256", "profile_id"}
)
_FENCE_FIELDS = frozenset(
    {
        "source_region",
        "source_partition_epoch_id",
        "topic",
        "partition_id",
        "start_next_offset",
        "end_next_offset",
    }
)

BEGIN_SIGNATURE = (
    "public.begin_source_snapshot_candidate_v2(text,text,text,text,text,text,"
    "text,timestamptz,text,jsonb,timestamptz,timestamptz,text,integer,bigint,"
    "bigint)"
)
STAGE_SIGNATURE = (
    "public.stage_source_snapshot_row_v3(text,text,text,jsonb,jsonb,jsonb,text)"
)
BIND_PROFILE_SIGNATURE = (
    "public.bind_source_snapshot_profile_v3(text,text,text,text,jsonb,text)"
)
HEARTBEAT_SIGNATURE = (
    "public.heartbeat_source_snapshot_candidate_v2(text,text,text,text,integer,"
    "bigint)"
)
VERIFY_SIGNATURE = (
    "public.verify_source_snapshot_candidate_v3(text,text,text,text,bigint,"
    "bigint,bigint,text,text,text)"
)
ABORT_SIGNATURE = (
    "public.abort_source_snapshot_candidate_v2(text,text,text,text,bigint,"
    "bigint,text)"
)
PUBLISH_SIGNATURE = (
    "public.publish_source_snapshot_candidate_v3(text,text,text,text,bigint,"
    "bigint,text,text)"
)


class DtsSourceScopeSnapshotError(RuntimeError):
    """Stable, payload-free source-scope coordinator failure."""


@dataclass(frozen=True)
class SourceScopeSnapshotRow:
    source_key_data: Mapping[str, Any]
    dependency_keys: Mapping[str, Any]
    protected_source_row: Mapping[str, Any]


@dataclass(frozen=True)
class SourceScopeSnapshotCandidate:
    path: Path
    artifact_format: str
    artifact_sha256: str
    protocol_version: str
    snapshot_id: str
    source_region: str
    source_table: str
    scope_kind: str
    scope_level: str
    scope_key: str
    snapshot_as_of: datetime
    snapshot_consistency_token: str
    snapshot_consistency_token_sha256: str
    source_profile_manifest_version: int
    source_profile_manifest_sha256: str
    source_schema_profile_id: str
    source_field_types: Mapping[str, str]
    source_export_evidence_sha256: str
    fence_evidence_sha256: str
    partition_offsets: tuple[Mapping[str, Any], ...]
    history_from: datetime | None
    history_through: datetime | None
    row_count: int
    content_hash: str
    fence_hash: str
    candidate_sha256: str
    profile: DtsSourceProfile

    @property
    def begin_command_id(self) -> str:
        return f"scope-begin-v1:{self.candidate_sha256}"

    @property
    def verify_command_id(self) -> str:
        return f"scope-verify-v1:{self.candidate_sha256}"

    @property
    def publish_command_id(self) -> str:
        return f"scope-publish-v1:{self.candidate_sha256}"

    def heartbeat_command_id(self, expected_head_version: int) -> str:
        return (
            f"scope-heartbeat-v1:{self.candidate_sha256}:"
            f"{expected_head_version}"
        )

    def public_summary(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "snapshot_id": self.snapshot_id,
            "candidate_sha256": self.candidate_sha256,
            "artifact_sha256": self.artifact_sha256,
            "source_region": self.source_region,
            "source_table": self.source_table,
            "scope_kind": self.scope_kind,
            "scope_level": self.scope_level,
            "scope_key": self.scope_key,
            "snapshot_as_of": _timestamp_text(self.snapshot_as_of),
            "source_profile_manifest_version": (
                self.source_profile_manifest_version
            ),
            "source_profile_manifest_sha256": (
                self.source_profile_manifest_sha256
            ),
            "source_schema_profile_id": self.source_schema_profile_id,
            "source_field_types": dict(self.source_field_types),
            "source_export_evidence_sha256": (
                self.source_export_evidence_sha256
            ),
            "fence_evidence_sha256": self.fence_evidence_sha256,
            "row_count": self.row_count,
            "content_hash": self.content_hash,
            "fence_hash": self.fence_hash,
        }


@dataclass(frozen=True)
class SourceScopeTargetState:
    head_row_version: int
    current_generation: int
    current_snapshot_id: str | None
    active_candidate_snapshot_id: str | None
    candidate_owner: str | None
    candidate_lease_expires_at: datetime | None
    scope_state: str | None
    scope_row_version: int
    scope_active_snapshot_id: str | None
    scope_candidate_snapshot_id: str | None
    snapshot_state: str | None
    snapshot_row_count: int | None
    snapshot_content_hash: str | None
    snapshot_fence_hash: str | None
    snapshot_source_schema_profile_id: str | None
    snapshot_source_field_types: Mapping[str, str] | None
    snapshot_published_generation: int | None
    snapshot_error_code: str | None

    def public_summary(self) -> dict[str, Any]:
        return {
            "head_row_version": self.head_row_version,
            "current_generation": self.current_generation,
            "current_snapshot_id": self.current_snapshot_id,
            "active_candidate_snapshot_id": self.active_candidate_snapshot_id,
            "candidate_owner": self.candidate_owner,
            "candidate_lease_expires_at": _optional_timestamp_text(
                self.candidate_lease_expires_at
            ),
            "scope_state": self.scope_state,
            "scope_row_version": self.scope_row_version,
            "scope_active_snapshot_id": self.scope_active_snapshot_id,
            "scope_candidate_snapshot_id": self.scope_candidate_snapshot_id,
            "snapshot_state": self.snapshot_state,
            "snapshot_row_count": self.snapshot_row_count,
            "snapshot_content_hash": self.snapshot_content_hash,
            "snapshot_fence_hash": self.snapshot_fence_hash,
            "snapshot_published_generation": (
                self.snapshot_published_generation
            ),
            "snapshot_error_code": self.snapshot_error_code,
        }


@dataclass(frozen=True)
class SourceScopeDatabaseHealth:
    ready: bool
    code: str
    current_user: str | None
    session_user: str | None
    schema_ready: bool
    functions_ready: bool
    direct_write_blocked: bool

    def public_summary(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "code": self.code,
            "current_user": self.current_user,
            "session_user": self.session_user,
            "schema_ready": self.schema_ready,
            "functions_ready": self.functions_ready,
            "direct_write_blocked": self.direct_write_blocked,
        }


@dataclass(frozen=True)
class SourceScopeApplyResult:
    status: str
    staged_count: int
    candidate: SourceScopeSnapshotCandidate
    target: SourceScopeTargetState

    def public_summary(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "staged_count": self.staged_count,
            "candidate": self.candidate.public_summary(),
            "readback": self.target.public_summary(),
        }


class PostgresDtsSourceScopeSnapshotStore:
    """Thin SQL client for the protected source-scope functions."""

    def read_health(self, connection: Connection) -> SourceScopeDatabaseHealth:
        value = connection.execute(
            text(
                """
                SELECT jsonb_build_object(
                    'current_user',current_user,
                    'session_user',session_user,
                    'role_ok',coalesce((
                        SELECT rolcanlogin AND NOT rolinherit AND NOT rolsuper
                           AND NOT rolcreatedb AND NOT rolcreaterole
                           AND NOT rolreplication AND NOT rolbypassrls
                        FROM pg_catalog.pg_roles
                        WHERE rolname=current_user
                    ),false),
                    'schema_ready',
                        to_regclass('public.dts_source_scope_snapshots')
                            IS NOT NULL
                        AND to_regclass(
                            'public.dts_source_table_publish_generations'
                        ) IS NOT NULL
                        AND to_regclass('public.dts_source_scope_states')
                            IS NOT NULL,
                    'functions_ready',
                        coalesce(has_function_privilege(
                            current_user,to_regprocedure(:begin_sig),'EXECUTE'
                        ),false)
                        AND coalesce(has_function_privilege(
                            current_user,to_regprocedure(:stage_sig),'EXECUTE'
                        ),false)
                        AND coalesce(has_function_privilege(
                            current_user,to_regprocedure(:bind_profile_sig),
                            'EXECUTE'
                        ),false)
                        AND coalesce(has_function_privilege(
                            current_user,to_regprocedure(:heartbeat_sig),
                            'EXECUTE'
                        ),false)
                        AND coalesce(has_function_privilege(
                            current_user,to_regprocedure(:verify_sig),'EXECUTE'
                        ),false)
                        AND coalesce(has_function_privilege(
                            current_user,to_regprocedure(:abort_sig),'EXECUTE'
                        ),false)
                        AND coalesce(has_function_privilege(
                            current_user,to_regprocedure(:publish_sig),'EXECUTE'
                        ),false),
                    'direct_write_blocked',
                        NOT coalesce(has_table_privilege(
                            current_user,
                            'public.dts_source_scope_snapshots','INSERT'
                        ),false)
                        AND NOT coalesce(has_table_privilege(
                            current_user,
                            'public.dts_source_scope_states','UPDATE'
                        ),false)
                        AND NOT coalesce(has_table_privilege(
                            current_user,
                            'public.dts_source_snapshot_rows','INSERT'
                        ),false)
                )
                """
            ),
            {
                "begin_sig": BEGIN_SIGNATURE,
                "stage_sig": STAGE_SIGNATURE,
                "bind_profile_sig": BIND_PROFILE_SIGNATURE,
                "heartbeat_sig": HEARTBEAT_SIGNATURE,
                "verify_sig": VERIFY_SIGNATURE,
                "abort_sig": ABORT_SIGNATURE,
                "publish_sig": PUBLISH_SIGNATURE,
            },
        ).scalar_one()
        result = _mapping(value, "DTS_SOURCE_SCOPE_HEALTH_RESPONSE_INVALID")
        current_user = _optional_string(result.get("current_user"))
        session_user = _optional_string(result.get("session_user"))
        schema_ready = result.get("schema_ready") is True
        functions_ready = result.get("functions_ready") is True
        direct_write_blocked = result.get("direct_write_blocked") is True
        role_ready = (
            result.get("role_ok") is True
            and current_user == REQUIRED_DATABASE_ROLE
            and session_user == REQUIRED_DATABASE_ROLE
        )
        ready = (
            role_ready
            and schema_ready
            and functions_ready
            and direct_write_blocked
        )
        return SourceScopeDatabaseHealth(
            ready=ready,
            code=(
                "DTS_SOURCE_SCOPE_COORDINATOR_READY"
                if ready
                else "DTS_SOURCE_SCOPE_COORDINATOR_NOT_READY"
            ),
            current_user=current_user,
            session_user=session_user,
            schema_ready=schema_ready,
            functions_ready=functions_ready,
            direct_write_blocked=direct_write_blocked,
        )

    def read_target_state(
        self,
        connection: Connection,
        candidate: SourceScopeSnapshotCandidate,
    ) -> SourceScopeTargetState:
        row = connection.execute(
            text(
                """
                SELECT
                    head.row_version AS head_row_version,
                    head.current_generation,
                    head.current_snapshot_id,
                    head.active_candidate_snapshot_id,
                    head.candidate_owner,
                    head.candidate_lease_expires_at,
                    scope.state AS scope_state,
                    coalesce(scope.row_version,0) AS scope_row_version,
                    scope.active_snapshot_id AS scope_active_snapshot_id,
                    scope.candidate_snapshot_id AS scope_candidate_snapshot_id,
                    snapshot.epoch_state AS snapshot_state,
                    snapshot.row_count AS snapshot_row_count,
                    snapshot.content_hash AS snapshot_content_hash,
                    snapshot.snapshot_fence_hash,
                    snapshot.source_schema_profile_id AS
                        snapshot_source_schema_profile_id,
                    snapshot.source_field_types AS
                        snapshot_source_field_types,
                    snapshot.published_generation,
                    snapshot.error_code AS snapshot_error_code
                FROM public.dts_source_table_publish_generations head
                LEFT JOIN public.dts_source_scope_states scope
                  ON scope.source_region=head.source_region
                 AND scope.source_table=head.source_table
                 AND scope.scope_kind=:scope_kind
                 AND scope.scope_level=:scope_level
                 AND scope.scope_key=:scope_key
                LEFT JOIN public.dts_source_scope_snapshots snapshot
                  ON snapshot.snapshot_id=:snapshot_id
                WHERE head.source_region=:source_region
                  AND head.source_table=:source_table
                """
            ),
            _candidate_identity_params(candidate),
        ).mappings().one_or_none()
        if row is None:
            _fail("SOURCE_TABLE_GENERATION_HEAD_MISSING")
        return _target_state(row)

    def read_snapshot(
        self,
        connection: Connection,
        snapshot_id: str,
    ) -> dict[str, Any] | None:
        row = connection.execute(
            text(
                """
                SELECT snapshot.snapshot_id,snapshot.source_region,
                       snapshot.source_table,snapshot.scope_kind,
                       snapshot.scope_level,snapshot.scope_key,
                       snapshot.epoch_state,snapshot.snapshot_as_of,
                       snapshot.snapshot_consistency_token_hash,
                       snapshot.snapshot_fence_hash,snapshot.row_count,
                       snapshot.content_hash,snapshot.published_generation,
                       snapshot.source_schema_profile_id,
                       snapshot.source_field_types,
                       snapshot.published_through_offsets,
                       snapshot.published_through_hash,
                       snapshot.generation_diff_count,
                       snapshot.generation_diff_hash,snapshot.error_code,
                       snapshot.created_at,snapshot.verified_at,
                       snapshot.completed_at,snapshot.failed_at,
                       snapshot.invalidated_at,snapshot.row_version,
                       head.current_generation,head.current_snapshot_id,
                       head.active_candidate_snapshot_id,
                       scope.state AS scope_state,
                       scope.active_snapshot_id AS scope_active_snapshot_id,
                       scope.candidate_snapshot_id AS
                            scope_candidate_snapshot_id,
                       scope.row_version AS scope_row_version
                FROM public.dts_source_scope_snapshots snapshot
                JOIN public.dts_source_table_publish_generations head
                  ON head.source_region=snapshot.source_region
                 AND head.source_table=snapshot.source_table
                JOIN public.dts_source_scope_states scope
                  ON scope.source_region=snapshot.source_region
                 AND scope.source_table=snapshot.source_table
                 AND scope.scope_kind=snapshot.scope_kind
                 AND scope.scope_level=snapshot.scope_level
                 AND scope.scope_key=snapshot.scope_key
                WHERE snapshot.snapshot_id=:snapshot_id
                """
            ),
            {"snapshot_id": snapshot_id},
        ).mappings().one_or_none()
        if row is None:
            return None
        return {
            key: _public_scalar(value)
            for key, value in dict(row).items()
        }

    def read_begin_response(
        self,
        connection: Connection,
        candidate: SourceScopeSnapshotCandidate,
    ) -> dict[str, Any]:
        value = connection.execute(
            text(
                """
                SELECT response_payload
                FROM public.dts_source_scope_commands
                WHERE command_id=:command_id
                  AND command_type='BEGIN'
                  AND snapshot_id=:snapshot_id
                  AND scope_identity->>'source_region'=:source_region
                  AND scope_identity->>'source_table'=:source_table
                  AND scope_identity->>'scope_kind'=:scope_kind
                  AND scope_identity->>'scope_level'=:scope_level
                  AND scope_identity->>'scope_key'=:scope_key
                """
            ),
            {
                **_candidate_identity_params(candidate),
                "command_id": candidate.begin_command_id,
            },
        ).scalar_one_or_none()
        if value is None:
            _fail("DTS_SOURCE_SCOPE_BEGIN_EVIDENCE_MISSING")
        return _command_response(
            value,
            snapshot_id=candidate.snapshot_id,
            statuses={"STARTED"},
            error="DTS_SOURCE_SCOPE_BEGIN_RESPONSE_INVALID",
        )

    def bind_profile(
        self,
        connection: Connection,
        *,
        candidate: SourceScopeSnapshotCandidate,
        owner: str,
        lease_token: str,
    ) -> dict[str, Any]:
        value = connection.execute(
            text(
                """
                SELECT public.bind_source_snapshot_profile_v3(
                    :snapshot_id,:owner,:lease_token,:profile_id,
                    CAST(:source_field_types AS jsonb),:actor
                )
                """
            ),
            {
                "snapshot_id": candidate.snapshot_id,
                "owner": owner,
                "lease_token": lease_token,
                "profile_id": candidate.source_schema_profile_id,
                "source_field_types": _canonical_json(
                    candidate.source_field_types
                ),
                "actor": owner,
            },
        ).scalar_one()
        return _command_response(
            value,
            snapshot_id=candidate.snapshot_id,
            statuses={"BOUND", "NOOP"},
            error="DTS_SOURCE_SCOPE_PROFILE_BIND_RESPONSE_INVALID",
        )

    def begin(
        self,
        connection: Connection,
        *,
        candidate: SourceScopeSnapshotCandidate,
        owner: str,
        lease_seconds: int,
        expected_head_version: int,
        expected_scope_version: int,
    ) -> dict[str, Any]:
        value = connection.execute(
            text(
                """
                SELECT public.begin_source_snapshot_candidate_v2(
                    :command_id,:snapshot_id,:source_region,:source_table,
                    :scope_kind,:scope_level,:scope_key,:snapshot_as_of,
                    :snapshot_consistency_token,
                    CAST(:partition_offsets AS jsonb),:history_from,
                    :history_through,:owner,:lease_seconds,
                    :expected_head_version,:expected_scope_version
                )
                """
            ),
            {
                **_candidate_identity_params(candidate),
                "command_id": candidate.begin_command_id,
                "snapshot_as_of": candidate.snapshot_as_of,
                "snapshot_consistency_token": (
                    candidate.snapshot_consistency_token
                ),
                "partition_offsets": _canonical_json(
                    candidate.partition_offsets
                ),
                "history_from": candidate.history_from,
                "history_through": candidate.history_through,
                "owner": owner,
                "lease_seconds": lease_seconds,
                "expected_head_version": expected_head_version,
                "expected_scope_version": expected_scope_version,
            },
        ).scalar_one()
        return _command_response(
            value,
            snapshot_id=candidate.snapshot_id,
            statuses={"STARTED"},
            error="DTS_SOURCE_SCOPE_BEGIN_RESPONSE_INVALID",
        )

    def stage_row(
        self,
        connection: Connection,
        *,
        candidate: SourceScopeSnapshotCandidate,
        owner: str,
        lease_token: str,
        row: SourceScopeSnapshotRow,
    ) -> dict[str, Any]:
        value = connection.execute(
            text(
                """
                SELECT public.stage_source_snapshot_row_v3(
                    :snapshot_id,:owner,:lease_token,
                    CAST(:source_key_data AS jsonb),
                    CAST(:dependency_keys AS jsonb),
                    CAST(:protected_source_row AS jsonb),:actor
                )
                """
            ),
            {
                "snapshot_id": candidate.snapshot_id,
                "owner": owner,
                "lease_token": lease_token,
                "source_key_data": _canonical_json(row.source_key_data),
                "dependency_keys": _canonical_json(row.dependency_keys),
                "protected_source_row": _canonical_json(
                    row.protected_source_row
                ),
                "actor": owner,
            },
        ).scalar_one()
        return _command_response(
            value,
            snapshot_id=None,
            statuses={"STAGED", "NOOP"},
            error="DTS_SOURCE_SCOPE_STAGE_RESPONSE_INVALID",
        )

    def heartbeat(
        self,
        connection: Connection,
        *,
        candidate: SourceScopeSnapshotCandidate,
        owner: str,
        lease_token: str,
        lease_seconds: int,
        expected_head_version: int,
    ) -> dict[str, Any]:
        value = connection.execute(
            text(
                """
                SELECT public.heartbeat_source_snapshot_candidate_v2(
                    :command_id,:snapshot_id,:owner,:lease_token,
                    :lease_seconds,:expected_head_version
                )
                """
            ),
            {
                "command_id": candidate.heartbeat_command_id(
                    expected_head_version
                ),
                "snapshot_id": candidate.snapshot_id,
                "owner": owner,
                "lease_token": lease_token,
                "lease_seconds": lease_seconds,
                "expected_head_version": expected_head_version,
            },
        ).scalar_one()
        return _command_response(
            value,
            snapshot_id=candidate.snapshot_id,
            statuses={"HEARTBEAT"},
            error="DTS_SOURCE_SCOPE_HEARTBEAT_RESPONSE_INVALID",
        )

    def verify(
        self,
        connection: Connection,
        *,
        candidate: SourceScopeSnapshotCandidate,
        owner: str,
        lease_token: str,
        expected_head_version: int,
        expected_scope_version: int,
    ) -> dict[str, Any]:
        value = connection.execute(
            text(
                """
                SELECT public.verify_source_snapshot_candidate_v3(
                    :command_id,:snapshot_id,:owner,:lease_token,
                    :expected_head_version,:expected_scope_version,
                    :expected_row_count,:expected_content_hash,
                    :expected_fence_hash,:actor
                )
                """
            ),
            {
                "command_id": candidate.verify_command_id,
                "snapshot_id": candidate.snapshot_id,
                "owner": owner,
                "lease_token": lease_token,
                "expected_head_version": expected_head_version,
                "expected_scope_version": expected_scope_version,
                "expected_row_count": candidate.row_count,
                "expected_content_hash": candidate.content_hash,
                "expected_fence_hash": candidate.fence_hash,
                "actor": owner,
            },
        ).scalar_one()
        return _command_response(
            value,
            snapshot_id=candidate.snapshot_id,
            statuses={"VERIFIED", "FAILED"},
            error="DTS_SOURCE_SCOPE_VERIFY_RESPONSE_INVALID",
        )

    def publish(
        self,
        connection: Connection,
        *,
        candidate: SourceScopeSnapshotCandidate,
        owner: str,
        lease_token: str,
        expected_head_version: int,
        expected_scope_version: int,
    ) -> dict[str, Any]:
        value = connection.execute(
            text(
                """
                SELECT public.publish_source_snapshot_candidate_v3(
                    :command_id,:snapshot_id,:owner,:lease_token,
                    :expected_head_version,:expected_scope_version,
                    :expected_content_hash,:actor
                )
                """
            ),
            {
                "command_id": candidate.publish_command_id,
                "snapshot_id": candidate.snapshot_id,
                "owner": owner,
                "lease_token": lease_token,
                "expected_head_version": expected_head_version,
                "expected_scope_version": expected_scope_version,
                "expected_content_hash": candidate.content_hash,
                "actor": owner,
            },
        ).scalar_one()
        return _command_response(
            value,
            snapshot_id=candidate.snapshot_id,
            statuses={"PUBLISHED"},
            error="DTS_SOURCE_SCOPE_PUBLISH_RESPONSE_INVALID",
        )


class DtsSourceScopeSnapshotCoordinator:
    """Orchestrate one resumable, database-owned candidate lifecycle."""

    def __init__(
        self,
        *,
        engine: Engine,
        owner: str,
        lease_seconds: int = 300,
        stage_batch_size: int = 100,
        store: PostgresDtsSourceScopeSnapshotStore | None = None,
    ) -> None:
        if not _valid_bounded_string(owner, maximum=128):
            _fail("DTS_SOURCE_SCOPE_OWNER_INVALID")
        if type(lease_seconds) is not int or not 15 <= lease_seconds <= 300:
            _fail("DTS_SOURCE_SCOPE_LEASE_SECONDS_INVALID")
        if (
            type(stage_batch_size) is not int
            or not 1 <= stage_batch_size <= 1_000
        ):
            _fail("DTS_SOURCE_SCOPE_STAGE_BATCH_SIZE_INVALID")
        self.engine = engine
        self.owner = owner
        self.lease_seconds = lease_seconds
        self.stage_batch_size = stage_batch_size
        self.store = store or PostgresDtsSourceScopeSnapshotStore()

    def health(self) -> SourceScopeDatabaseHealth:
        with self.engine.connect() as connection:
            health = self.store.read_health(connection)
        return health

    def dry_run(
        self,
        candidate: SourceScopeSnapshotCandidate,
    ) -> tuple[SourceScopeDatabaseHealth, SourceScopeTargetState]:
        with self.engine.connect() as connection:
            health = self.store.read_health(connection)
            if not health.ready:
                _fail(health.code)
            target = self.store.read_target_state(connection, candidate)
        _require_candidate_can_start(candidate, target, owner=self.owner)
        return health, target

    def apply(
        self,
        candidate: SourceScopeSnapshotCandidate,
    ) -> SourceScopeApplyResult:
        health = self.health()
        if not health.ready:
            _fail(health.code)

        with self.engine.connect() as connection:
            target = self.store.read_target_state(connection, candidate)
        _require_candidate_can_start(candidate, target, owner=self.owner)

        if target.snapshot_state == "COMPLETE":
            _require_complete_readback(candidate, target)
            return SourceScopeApplyResult(
                status="REPLAYED_COMPLETE",
                staged_count=0,
                candidate=candidate,
                target=target,
            )

        if target.snapshot_state in {"LOADING", "VERIFYING"}:
            with self.engine.connect() as connection:
                begin = self.store.read_begin_response(connection, candidate)
        else:
            with self.engine.begin() as connection:
                begin = self.store.begin(
                    connection,
                    candidate=candidate,
                    owner=self.owner,
                    lease_seconds=self.lease_seconds,
                    expected_head_version=target.head_row_version,
                    expected_scope_version=target.scope_row_version,
                )
        lease_token = _required_string(
            begin.get("lease_token"),
            "DTS_SOURCE_SCOPE_BEGIN_RESPONSE_INVALID",
            maximum=160,
        )

        with self.engine.begin() as connection:
            self.store.bind_profile(
                connection,
                candidate=candidate,
                owner=self.owner,
                lease_token=lease_token,
            )

        with self.engine.connect() as connection:
            target = self.store.read_target_state(connection, candidate)
        _require_candidate_identity(candidate, target, owner=self.owner)
        if target.snapshot_state not in {"LOADING", "VERIFYING"}:
            _fail("SOURCE_SCOPE_CANDIDATE_NOT_ACTIVE")

        staged_count = 0
        if target.snapshot_state == "LOADING":
            staged_count = self._stage_candidate(
                candidate,
                lease_token=lease_token,
                initial_head_version=target.head_row_version,
            )
            with self.engine.connect() as connection:
                target = self.store.read_target_state(connection, candidate)
            _require_candidate_identity(candidate, target, owner=self.owner)
            with self.engine.begin() as connection:
                verified = self.store.verify(
                    connection,
                    candidate=candidate,
                    owner=self.owner,
                    lease_token=lease_token,
                    expected_head_version=target.head_row_version,
                    expected_scope_version=target.scope_row_version,
                )
            if verified.get("status") == "FAILED":
                error_code = verified.get("error_code")
                if not isinstance(error_code, str) or not error_code:
                    _fail("DTS_SOURCE_SCOPE_VERIFY_RESPONSE_INVALID")
                _fail(error_code)
            target = _target_after_verified(target, verified)

        if target.snapshot_state != "VERIFYING":
            _fail("SOURCE_SCOPE_PUBLISH_NOT_VERIFIED")
        _require_verifying_readback(candidate, target)
        with self.engine.begin() as connection:
            self.store.publish(
                connection,
                candidate=candidate,
                owner=self.owner,
                lease_token=lease_token,
                expected_head_version=target.head_row_version,
                expected_scope_version=target.scope_row_version,
            )
        with self.engine.connect() as connection:
            final_target = self.store.read_target_state(connection, candidate)
        _require_complete_readback(candidate, final_target)
        return SourceScopeApplyResult(
            status="PUBLISHED",
            staged_count=staged_count,
            candidate=candidate,
            target=final_target,
        )

    def _stage_candidate(
        self,
        candidate: SourceScopeSnapshotCandidate,
        *,
        lease_token: str,
        initial_head_version: int,
    ) -> int:
        head_version = initial_head_version
        staged_count = 0
        batch: list[SourceScopeSnapshotRow] = []

        def flush() -> None:
            nonlocal head_version, staged_count
            if not batch:
                return
            with self.engine.begin() as connection:
                for row in batch:
                    self.store.stage_row(
                        connection,
                        candidate=candidate,
                        owner=self.owner,
                        lease_token=lease_token,
                        row=row,
                    )
            staged_count += len(batch)
            batch.clear()
            with self.engine.begin() as connection:
                heartbeat = self.store.heartbeat(
                    connection,
                    candidate=candidate,
                    owner=self.owner,
                    lease_token=lease_token,
                    lease_seconds=self.lease_seconds,
                    expected_head_version=head_version,
                )
            head_version = _positive_int(
                heartbeat.get("head_row_version"),
                "DTS_SOURCE_SCOPE_HEARTBEAT_RESPONSE_INVALID",
            )

        def consume(row: SourceScopeSnapshotRow) -> None:
            batch.append(row)
            if len(batch) >= self.stage_batch_size:
                flush()

        rescan_candidate_artifact(candidate, row_consumer=consume)
        flush()
        return staged_count


def load_candidate_artifact(
    path: str | Path,
    *,
    profile_manifest_path: str | Path,
    expected_artifact_sha256: str,
    expected_profile_manifest_sha256: str,
) -> SourceScopeSnapshotCandidate:
    """Load and fully verify one JSON or JSONL candidate artifact."""

    artifact_path = Path(path)
    expected_artifact_hash = _sha256(
        expected_artifact_sha256,
        "DTS_SOURCE_SCOPE_ARTIFACT_EXPECTED_HASH_INVALID",
    )
    expected_profile_hash = _sha256(
        expected_profile_manifest_sha256,
        "DTS_SOURCE_SCOPE_PROFILE_EXPECTED_HASH_INVALID",
    )
    try:
        profile_registry = load_dts_source_profile_registry(
            profile_manifest_path,
            allowed_tables=V2_BUSINESS_SOURCE_TABLES,
        )
    except DtsSourceProfileManifestError as exc:
        raise DtsSourceScopeSnapshotError(str(exc)) from exc
    if profile_registry.manifest_sha256 != expected_profile_hash:
        _fail("DTS_SOURCE_SCOPE_PROFILE_MANIFEST_HASH_MISMATCH")
    return _scan_candidate_artifact(
        artifact_path,
        expected_artifact_sha256=expected_artifact_hash,
        profile_registry=profile_registry,
        row_consumer=None,
    )


def rescan_candidate_artifact(
    candidate: SourceScopeSnapshotCandidate,
    *,
    row_consumer: Callable[[SourceScopeSnapshotRow], None] | None = None,
) -> SourceScopeSnapshotCandidate:
    """Re-read the bound artifact before/during staging and reject drift."""

    # The profile was already fully validated and its immutable dataclass is
    # sufficient for a second pass; do not silently reload a different path.
    scanned = _scan_candidate_artifact_with_profile(
        candidate.path,
        expected_artifact_sha256=candidate.artifact_sha256,
        expected_profile_manifest_sha256=(
            candidate.source_profile_manifest_sha256
        ),
        profile=candidate.profile,
        artifact_format=candidate.artifact_format,
        row_consumer=row_consumer,
    )
    if scanned.public_summary() != candidate.public_summary():
        _fail("DTS_SOURCE_SCOPE_ARTIFACT_CHANGED")
    return scanned


def _scan_candidate_artifact(
    path: Path,
    *,
    expected_artifact_sha256: str,
    profile_registry: Any,
    row_consumer: Callable[[SourceScopeSnapshotRow], None] | None,
) -> SourceScopeSnapshotCandidate:
    artifact_format = _artifact_format(path)
    header, summary, row_documents, artifact_hash = _read_artifact(
        path,
        artifact_format=artifact_format,
        row_consumer=None,
        row_validator=None,
    )
    if artifact_hash != expected_artifact_sha256:
        _fail("DTS_SOURCE_SCOPE_ARTIFACT_HASH_MISMATCH")
    source_table = _required_string(
        header.get("source_table"),
        "DTS_SOURCE_SCOPE_TABLE_INVALID",
        maximum=128,
    )
    profile = profile_registry.profile_for(source_table)
    if profile is None:
        _fail("DTS_SOURCE_SCOPE_SOURCE_PROFILE_MISSING")
    candidate = _validate_candidate(
        path=path,
        artifact_format=artifact_format,
        artifact_hash=artifact_hash,
        header=header,
        summary=summary,
        row_documents=row_documents,
        profile=profile,
        profile_manifest_version=profile_registry.manifest_version,
        profile_manifest_sha256=profile_registry.manifest_sha256,
        row_consumer=row_consumer,
    )
    return candidate


def _scan_candidate_artifact_with_profile(
    path: Path,
    *,
    expected_artifact_sha256: str,
    expected_profile_manifest_sha256: str,
    profile: DtsSourceProfile,
    artifact_format: str,
    row_consumer: Callable[[SourceScopeSnapshotRow], None] | None,
) -> SourceScopeSnapshotCandidate:
    header, summary, row_documents, artifact_hash = _read_artifact(
        path,
        artifact_format=artifact_format,
        row_consumer=None,
        row_validator=None,
    )
    if artifact_hash != expected_artifact_sha256:
        _fail("DTS_SOURCE_SCOPE_ARTIFACT_CHANGED")
    return _validate_candidate(
        path=path,
        artifact_format=artifact_format,
        artifact_hash=artifact_hash,
        header=header,
        summary=summary,
        row_documents=row_documents,
        profile=profile,
        profile_manifest_version=1,
        profile_manifest_sha256=expected_profile_manifest_sha256,
        row_consumer=row_consumer,
    )


def _read_artifact(
    path: Path,
    *,
    artifact_format: str,
    row_consumer: Callable[[Mapping[str, Any]], None] | None,
    row_validator: Callable[[Mapping[str, Any]], None] | None,
) -> tuple[
    Mapping[str, Any],
    Mapping[str, Any],
    Iterable[Mapping[str, Any]],
    str,
]:
    del row_consumer, row_validator
    if artifact_format == "JSON":
        try:
            size = path.stat().st_size
            raw = path.read_bytes()
        except OSError as exc:
            raise DtsSourceScopeSnapshotError(
                "DTS_SOURCE_SCOPE_ARTIFACT_UNREADABLE"
            ) from exc
        if size <= 0 or size > _MAX_JSON_BYTES or len(raw) != size:
            _fail("DTS_SOURCE_SCOPE_ARTIFACT_SIZE_INVALID")
        document = _decode_json(raw)
        root = _mapping(document, "DTS_SOURCE_SCOPE_ARTIFACT_JSON_INVALID")
        if set(root) != _HEADER_FIELDS | _SUMMARY_FIELDS | {"rows"}:
            _fail("DTS_SOURCE_SCOPE_ARTIFACT_KEYS_INVALID")
        rows = root.get("rows")
        if not isinstance(rows, list) or len(rows) > _MAX_ROWS:
            _fail("DTS_SOURCE_SCOPE_ROWS_INVALID")
        header = {key: root[key] for key in _HEADER_FIELDS}
        summary = {key: root[key] for key in _SUMMARY_FIELDS}
        return header, summary, rows, hashlib.sha256(raw).hexdigest()

    try:
        size = path.stat().st_size
        handle = path.open("rb")
    except OSError as exc:
        raise DtsSourceScopeSnapshotError(
            "DTS_SOURCE_SCOPE_ARTIFACT_UNREADABLE"
        ) from exc
    if size <= 0 or size > _MAX_JSONL_BYTES:
        handle.close()
        _fail("DTS_SOURCE_SCOPE_ARTIFACT_SIZE_INVALID")
    digest = hashlib.sha256()
    header_record: Mapping[str, Any] | None = None
    trailer_record: Mapping[str, Any] | None = None
    pending_record: Mapping[str, Any] | None = None
    row_count = 0
    with handle:
        while True:
            raw_line = handle.readline(_MAX_JSONL_LINE_BYTES + 1)
            if not raw_line:
                break
            digest.update(raw_line)
            if len(raw_line) > _MAX_JSONL_LINE_BYTES:
                _fail("DTS_SOURCE_SCOPE_ARTIFACT_LINE_TOO_LARGE")
            if not raw_line.strip():
                _fail("DTS_SOURCE_SCOPE_ARTIFACT_JSONL_INVALID")
            value = _mapping(
                _decode_json(raw_line),
                "DTS_SOURCE_SCOPE_ARTIFACT_JSONL_INVALID",
            )
            if header_record is None:
                header_record = value
                continue
            if pending_record is not None:
                if pending_record.get("record_type") != "snapshot_row":
                    _fail("DTS_SOURCE_SCOPE_ARTIFACT_JSONL_INVALID")
                if set(pending_record) != _ROW_FIELDS | {"record_type"}:
                    _fail("DTS_SOURCE_SCOPE_ROW_KEYS_INVALID")
                row_count += 1
                if row_count > _MAX_ROWS:
                    _fail("DTS_SOURCE_SCOPE_ROWS_INVALID")
            pending_record = value
    trailer_record = pending_record
    if header_record is None or trailer_record is None:
        _fail("DTS_SOURCE_SCOPE_ARTIFACT_JSONL_INVALID")
    if (
        header_record.get("record_type") != "snapshot_header"
        or set(header_record) != _HEADER_FIELDS | {"record_type"}
        or trailer_record.get("record_type") != "snapshot_manifest"
        or set(trailer_record) != _SUMMARY_FIELDS | {"record_type"}
    ):
        _fail("DTS_SOURCE_SCOPE_ARTIFACT_JSONL_INVALID")
    header = {key: header_record[key] for key in _HEADER_FIELDS}
    summary = {key: trailer_record[key] for key in _SUMMARY_FIELDS}
    return header, summary, _iter_jsonl_rows(path), digest.hexdigest()


def _iter_jsonl_rows(path: Path) -> Iterator[Mapping[str, Any]]:
    """Stream only row records while keeping the final manifest out."""

    try:
        handle = path.open("rb")
    except OSError as exc:
        raise DtsSourceScopeSnapshotError(
            "DTS_SOURCE_SCOPE_ARTIFACT_UNREADABLE"
        ) from exc
    with handle:
        first = True
        pending: Mapping[str, Any] | None = None
        while True:
            raw_line = handle.readline(_MAX_JSONL_LINE_BYTES + 1)
            if not raw_line:
                break
            if len(raw_line) > _MAX_JSONL_LINE_BYTES or not raw_line.strip():
                _fail("DTS_SOURCE_SCOPE_ARTIFACT_JSONL_INVALID")
            record = _mapping(
                _decode_json(raw_line),
                "DTS_SOURCE_SCOPE_ARTIFACT_JSONL_INVALID",
            )
            if first:
                first = False
                continue
            if pending is not None:
                if pending.get("record_type") != "snapshot_row" or set(
                    pending
                ) != _ROW_FIELDS | {"record_type"}:
                    _fail("DTS_SOURCE_SCOPE_ARTIFACT_JSONL_INVALID")
                yield {key: pending[key] for key in _ROW_FIELDS}
            pending = record
        if pending is None or pending.get("record_type") != "snapshot_manifest":
            _fail("DTS_SOURCE_SCOPE_ARTIFACT_JSONL_INVALID")


def _validate_candidate(
    *,
    path: Path,
    artifact_format: str,
    artifact_hash: str,
    header: Mapping[str, Any],
    summary: Mapping[str, Any],
    row_documents: Iterable[Mapping[str, Any]],
    profile: DtsSourceProfile,
    profile_manifest_version: int,
    profile_manifest_sha256: str,
    row_consumer: Callable[[SourceScopeSnapshotRow], None] | None,
) -> SourceScopeSnapshotCandidate:
    if header.get("protocol_version") != PROTOCOL_VERSION:
        _fail("DTS_SOURCE_SCOPE_PROTOCOL_UNSUPPORTED")
    source_region = _required_string(
        header.get("source_region"),
        "DTS_SOURCE_SCOPE_REGION_INVALID",
        maximum=8,
    )
    source_table = _required_string(
        header.get("source_table"),
        "DTS_SOURCE_SCOPE_TABLE_INVALID",
        maximum=128,
    )
    if (
        source_region not in {"dom", "ovs"}
        or source_table not in V2_BUSINESS_SOURCE_TABLES
        or not source_table.startswith(f"{source_region}_")
        or profile.region != source_region
        or profile.table != source_table
    ):
        _fail("DTS_SOURCE_SCOPE_IDENTITY_INVALID")
    table_suffix = source_table.removeprefix(f"{source_region}_")
    if not profile.persisted_protected_fields.issubset(
        V2_SOURCE_FIELD_WHITELIST[table_suffix]
    ):
        _fail("DTS_SOURCE_SCOPE_PROFILE_PERSISTED_FIELD_NOT_ALLOWED")
    scope_kind = _required_string(
        header.get("scope_kind"),
        "DTS_SOURCE_SCOPE_KIND_INVALID",
        maximum=16,
    )
    if scope_kind != CURRENT_SCOPE_KIND:
        _fail("DTS_SOURCE_SCOPE_HISTORY_PUBLISH_UNSUPPORTED")
    scope_level = _required_string(
        header.get("scope_level"),
        "DTS_SOURCE_SCOPE_LEVEL_INVALID",
        maximum=16,
    )
    scope_key = _required_string(
        header.get("scope_key"),
        "DTS_SOURCE_SCOPE_KEY_INVALID",
        maximum=512,
    )
    if scope_level not in SUPPORTED_SCOPE_LEVELS or (
        (scope_level == "GLOBAL" and scope_key != "*")
        or (scope_level == "TEACHER" and scope_key == "*")
    ):
        _fail("DTS_SOURCE_SCOPE_IDENTITY_INVALID")
    if header.get("history_from") is not None or header.get(
        "history_through"
    ) is not None:
        _fail("DTS_SOURCE_SCOPE_HISTORY_PUBLISH_UNSUPPORTED")

    snapshot_as_of = _timestamp(
        header.get("snapshot_as_of"),
        "DTS_SOURCE_SCOPE_SNAPSHOT_TIME_INVALID",
    )
    token = _required_string(
        header.get("snapshot_consistency_token"),
        "DTS_SOURCE_SCOPE_CONSISTENCY_TOKEN_INVALID",
        maximum=4_096,
        reject_control=True,
    )
    token_hash = _sha256(
        header.get("snapshot_consistency_token_sha256"),
        "DTS_SOURCE_SCOPE_CONSISTENCY_TOKEN_HASH_INVALID",
    )
    if hashlib.sha256(token.encode("utf-8")).hexdigest() != token_hash:
        _fail("DTS_SOURCE_SCOPE_CONSISTENCY_TOKEN_HASH_MISMATCH")
    export_hash = _sha256(
        header.get("source_export_evidence_sha256"),
        "DTS_SOURCE_SCOPE_EXPORT_EVIDENCE_HASH_INVALID",
    )
    fence_evidence_hash = _sha256(
        header.get("fence_evidence_sha256"),
        "DTS_SOURCE_SCOPE_FENCE_EVIDENCE_HASH_INVALID",
    )

    profile_reference = _mapping(
        header.get("source_profile"),
        "DTS_SOURCE_SCOPE_PROFILE_REFERENCE_INVALID",
    )
    if set(profile_reference) != _PROFILE_REFERENCE_FIELDS:
        _fail("DTS_SOURCE_SCOPE_PROFILE_REFERENCE_INVALID")
    if (
        profile_reference.get("manifest_version")
        != profile_manifest_version
        or profile_reference.get("manifest_sha256")
        != profile_manifest_sha256
        or profile_reference.get("profile_id") != profile.profile_id
    ):
        _fail("DTS_SOURCE_SCOPE_PROFILE_REFERENCE_MISMATCH")

    partition_offsets = _partition_offsets(
        header.get("partition_offsets"),
        source_region=source_region,
    )
    fence_hash = _hash_json(partition_offsets)
    declared_fence_hash = _sha256(
        summary.get("fence_hash"),
        "DTS_SOURCE_SCOPE_FENCE_HASH_INVALID",
    )
    if fence_hash != declared_fence_hash:
        _fail("DTS_SOURCE_SCOPE_FENCE_HASH_MISMATCH")

    declared_row_count = summary.get("row_count")
    if (
        type(declared_row_count) is not int
        or not 0 <= declared_row_count <= _MAX_ROWS
    ):
        _fail("DTS_SOURCE_SCOPE_ROW_COUNT_INVALID")
    declared_content_hash = _sha256(
        summary.get("content_hash"),
        "DTS_SOURCE_SCOPE_CONTENT_HASH_INVALID",
    )
    content_digest = hashlib.sha256()
    content_digest.update(b"[")
    row_count = 0
    previous_sort_key: tuple[Any, ...] | None = None
    for row_document in row_documents:
        if row_count >= _MAX_ROWS:
            _fail("DTS_SOURCE_SCOPE_ROWS_INVALID")
        row = _candidate_row(
            row_document,
            profile=profile,
            source_region=source_region,
            scope_level=scope_level,
            scope_key=scope_key,
        )
        sort_key, _ = _source_key_identity(
            row.source_key_data["id"],
            key_type=profile.primary_key_type,
        )
        if previous_sort_key is not None and sort_key <= previous_sort_key:
            _fail(
                "DTS_SOURCE_SCOPE_SOURCE_KEY_DUPLICATE"
                if sort_key == previous_sort_key
                else "DTS_SOURCE_SCOPE_ROW_ORDER_INVALID"
            )
        previous_sort_key = sort_key
        manifest_item = {
            "source_key_data": dict(row.source_key_data),
            "snapshot_row_hash": _hash_json(row.protected_source_row),
            "dependency_keys_hash": _hash_json(row.dependency_keys),
        }
        if row_count:
            content_digest.update(b",")
        content_digest.update(_canonical_json(manifest_item).encode("utf-8"))
        row_count += 1
        if row_consumer is not None:
            row_consumer(row)
    content_digest.update(b"]")
    content_hash = content_digest.hexdigest()
    if row_count != declared_row_count:
        _fail("DTS_SOURCE_SCOPE_ROW_COUNT_MISMATCH")
    if content_hash != declared_content_hash:
        _fail("DTS_SOURCE_SCOPE_CONTENT_HASH_MISMATCH")

    candidate_evidence = {
        "protocol_version": PROTOCOL_VERSION,
        "source_region": source_region,
        "source_table": source_table,
        "scope_kind": scope_kind,
        "scope_level": scope_level,
        "scope_key": scope_key,
        "snapshot_as_of": _timestamp_text(snapshot_as_of),
        "snapshot_consistency_token_sha256": token_hash,
        "source_profile": dict(profile_reference),
        "source_export_evidence_sha256": export_hash,
        "fence_evidence_sha256": fence_evidence_hash,
        "partition_offsets": partition_offsets,
        "history_from": None,
        "history_through": None,
        "row_count": row_count,
        "content_hash": content_hash,
        "fence_hash": fence_hash,
    }
    candidate_hash = _hash_json(candidate_evidence)
    declared_candidate_hash = _sha256(
        summary.get("candidate_sha256"),
        "DTS_SOURCE_SCOPE_CANDIDATE_HASH_INVALID",
    )
    if candidate_hash != declared_candidate_hash:
        _fail("DTS_SOURCE_SCOPE_CANDIDATE_HASH_MISMATCH")
    snapshot_id = _required_string(
        header.get("snapshot_id"),
        "DTS_SOURCE_SCOPE_SNAPSHOT_ID_INVALID",
        maximum=160,
    )
    if snapshot_id != f"{SNAPSHOT_ID_PREFIX}{candidate_hash}":
        _fail("DTS_SOURCE_SCOPE_SNAPSHOT_ID_HASH_MISMATCH")

    source_field_types = _snapshot_source_field_types(profile)
    return SourceScopeSnapshotCandidate(
        path=path,
        artifact_format=artifact_format,
        artifact_sha256=artifact_hash,
        protocol_version=PROTOCOL_VERSION,
        snapshot_id=snapshot_id,
        source_region=source_region,
        source_table=source_table,
        scope_kind=scope_kind,
        scope_level=scope_level,
        scope_key=scope_key,
        snapshot_as_of=snapshot_as_of,
        snapshot_consistency_token=token,
        snapshot_consistency_token_sha256=token_hash,
        source_profile_manifest_version=profile_manifest_version,
        source_profile_manifest_sha256=profile_manifest_sha256,
        source_schema_profile_id=profile.profile_id,
        source_field_types=source_field_types,
        source_export_evidence_sha256=export_hash,
        fence_evidence_sha256=fence_evidence_hash,
        partition_offsets=tuple(partition_offsets),
        history_from=None,
        history_through=None,
        row_count=row_count,
        content_hash=content_hash,
        fence_hash=fence_hash,
        candidate_sha256=candidate_hash,
        profile=profile,
    )


def _candidate_row(
    value: Any,
    *,
    profile: DtsSourceProfile,
    source_region: str,
    scope_level: str,
    scope_key: str,
) -> SourceScopeSnapshotRow:
    row = _mapping(value, "DTS_SOURCE_SCOPE_ROW_INVALID")
    if set(row) != _ROW_FIELDS:
        _fail("DTS_SOURCE_SCOPE_ROW_KEYS_INVALID")
    source_key_data = _mapping(
        row.get("source_key_data"),
        "DTS_SOURCE_SCOPE_SOURCE_KEY_INVALID",
    )
    if set(source_key_data) != {"id"}:
        _fail("DTS_SOURCE_SCOPE_SOURCE_KEY_INVALID")
    _source_key_identity(
        source_key_data["id"],
        key_type=profile.primary_key_type,
    )
    dependency_keys = _dependency_keys(
        row.get("dependency_keys"),
        source_region=source_region,
    )
    if scope_level == "TEACHER" and scope_key not in dependency_keys[
        "teacher_ids"
    ]:
        _fail("DTS_SOURCE_SCOPE_TEACHER_MEMBERSHIP_MISMATCH")
    protected_row = _mapping(
        row.get("protected_source_row"),
        "DTS_SOURCE_SCOPE_PROTECTED_ROW_INVALID",
    )
    if set(protected_row) != set(profile.persisted_protected_fields):
        _fail("DTS_SOURCE_SCOPE_PROTECTED_ROW_PROFILE_MISMATCH")
    protected_id = protected_row.get("id")
    if _source_key_identity(
        protected_id,
        key_type=profile.primary_key_type,
    )[1] != _source_key_identity(
        source_key_data["id"],
        key_type=profile.primary_key_type,
    )[1]:
        _fail("DTS_SOURCE_SCOPE_PROTECTED_ROW_IDENTITY_MISMATCH")
    if source_region == "dom":
        if set(protected_row) & _DOM_RAW_STUDENT_FIELDS:
            _fail("DTS_SOURCE_SCOPE_DOMESTIC_STUDENT_ID_FORBIDDEN")
        student_token = protected_row.get("student_token")
        if student_token is not None and (
            not isinstance(student_token, str)
            or _DOM_STUDENT_TOKEN_PATTERN.fullmatch(student_token) is None
        ):
            _fail("DTS_SOURCE_SCOPE_DOMESTIC_STUDENT_TOKEN_INVALID")
    _canonical_json(protected_row)
    return SourceScopeSnapshotRow(
        source_key_data=dict(source_key_data),
        dependency_keys=dependency_keys,
        protected_source_row=dict(protected_row),
    )


def _snapshot_source_field_types(
    profile: DtsSourceProfile,
) -> dict[str, str]:
    """Freeze complete type evidence for every protected snapshot field."""

    result: dict[str, str] = {}
    for field_name in sorted(profile.persisted_protected_fields):
        if field_name in profile.protected_derived_fields:
            if field_name != "student_token":
                _fail("DTS_SOURCE_SCOPE_DERIVED_FIELD_TYPE_UNSUPPORTED")
            field_type = "TEXT"
        else:
            field_type = profile.field_type_evidence.get(field_name)
        if field_type not in {"NUMERIC", "TEXT", "BOOLEAN", "TEMPORAL"}:
            _fail("DTS_SOURCE_SCOPE_PROFILE_FIELD_TYPES_INCOMPLETE")
        result[field_name] = field_type
    if result.get("id") != profile.primary_key_type:
        _fail("DTS_SOURCE_SCOPE_PROFILE_PRIMARY_KEY_TYPE_MISMATCH")
    return result


def _dependency_keys(value: Any, *, source_region: str) -> dict[str, list[str]]:
    mapping = _mapping(value, "DTS_SOURCE_SCOPE_DEPENDENCY_KEYS_INVALID")
    if set(mapping) != set(DEPENDENCY_FIELDS):
        _fail("DTS_SOURCE_SCOPE_DEPENDENCY_KEYS_INVALID")
    result: dict[str, list[str]] = {}
    for field in DEPENDENCY_FIELDS:
        items = mapping.get(field)
        if not isinstance(items, list):
            _fail("DTS_SOURCE_SCOPE_DEPENDENCY_KEYS_INVALID")
        normalized: list[str] = []
        for item in items:
            if not _valid_bounded_string(item, maximum=512):
                _fail("DTS_SOURCE_SCOPE_DEPENDENCY_KEYS_INVALID")
            if (
                field == "student_subjects"
                and source_region == "dom"
                and _DOM_STUDENT_TOKEN_PATTERN.fullmatch(item) is None
            ):
                _fail("DTS_SOURCE_SCOPE_DOMESTIC_STUDENT_TOKEN_INVALID")
            normalized.append(item)
        if len(set(normalized)) != len(normalized) or normalized != sorted(
            normalized, key=lambda item: item.encode("utf-8")
        ):
            _fail("DTS_SOURCE_SCOPE_DEPENDENCY_KEYS_NOT_CANONICAL")
        result[field] = normalized
    return result


def _partition_offsets(
    value: Any,
    *,
    source_region: str,
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        _fail("DTS_SOURCE_SCOPE_FENCE_VECTOR_INVALID")
    result: list[dict[str, Any]] = []
    identities: set[tuple[str, str, int]] = set()
    for raw_item in value:
        item = _mapping(raw_item, "DTS_SOURCE_SCOPE_FENCE_VECTOR_INVALID")
        if set(item) != _FENCE_FIELDS:
            _fail("DTS_SOURCE_SCOPE_FENCE_VECTOR_INVALID")
        if item.get("source_region") != source_region:
            _fail("DTS_SOURCE_SCOPE_FENCE_VECTOR_INVALID")
        epoch_id = _required_string(
            item.get("source_partition_epoch_id"),
            "DTS_SOURCE_SCOPE_FENCE_VECTOR_INVALID",
            maximum=160,
        )
        topic = _required_string(
            item.get("topic"),
            "DTS_SOURCE_SCOPE_FENCE_VECTOR_INVALID",
            maximum=512,
        )
        partition_id = _nonnegative_int(
            item.get("partition_id"),
            "DTS_SOURCE_SCOPE_FENCE_VECTOR_INVALID",
        )
        start_offset = _nonnegative_int(
            item.get("start_next_offset"),
            "DTS_SOURCE_SCOPE_FENCE_VECTOR_INVALID",
        )
        end_offset = _nonnegative_int(
            item.get("end_next_offset"),
            "DTS_SOURCE_SCOPE_FENCE_VECTOR_INVALID",
        )
        if end_offset < start_offset:
            _fail("DTS_SOURCE_SCOPE_FENCE_VECTOR_INVALID")
        identity = (epoch_id, topic, partition_id)
        if identity in identities:
            _fail("DTS_SOURCE_SCOPE_FENCE_VECTOR_DUPLICATE")
        identities.add(identity)
        result.append(
            {
                "source_region": source_region,
                "source_partition_epoch_id": epoch_id,
                "topic": topic,
                "partition_id": partition_id,
                "start_next_offset": start_offset,
                "end_next_offset": end_offset,
            }
        )
    return sorted(
        result,
        key=lambda item: (
            item["source_region"].encode("utf-8"),
            item["source_partition_epoch_id"].encode("utf-8"),
            item["topic"].encode("utf-8"),
            item["partition_id"],
        ),
    )


def _source_key_identity(
    value: Any,
    *,
    key_type: str,
) -> tuple[tuple[Any, ...], str]:
    if key_type == "TEXT":
        if not _valid_bounded_string(value, maximum=512):
            _fail("DTS_SOURCE_SCOPE_SOURCE_KEY_INVALID")
        return ((1, value.encode("utf-8")), value)
    if key_type != "NUMERIC" or isinstance(value, bool) or value is None:
        _fail("DTS_SOURCE_SCOPE_SOURCE_KEY_INVALID")
    try:
        numeric = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise DtsSourceScopeSnapshotError(
            "DTS_SOURCE_SCOPE_SOURCE_KEY_INVALID"
        ) from exc
    if not numeric.is_finite():
        _fail("DTS_SOURCE_SCOPE_SOURCE_KEY_INVALID")
    rendered = _decimal_text(numeric)
    return ((0, numeric), rendered)


def _candidate_identity_params(
    candidate: SourceScopeSnapshotCandidate,
) -> dict[str, Any]:
    return {
        "snapshot_id": candidate.snapshot_id,
        "source_region": candidate.source_region,
        "source_table": candidate.source_table,
        "scope_kind": candidate.scope_kind,
        "scope_level": candidate.scope_level,
        "scope_key": candidate.scope_key,
    }


def _target_state(row: Mapping[str, Any]) -> SourceScopeTargetState:
    return SourceScopeTargetState(
        head_row_version=_positive_int(
            row.get("head_row_version"),
            "DTS_SOURCE_SCOPE_TARGET_RESPONSE_INVALID",
        ),
        current_generation=_nonnegative_int(
            row.get("current_generation"),
            "DTS_SOURCE_SCOPE_TARGET_RESPONSE_INVALID",
        ),
        current_snapshot_id=_optional_string(row.get("current_snapshot_id")),
        active_candidate_snapshot_id=_optional_string(
            row.get("active_candidate_snapshot_id")
        ),
        candidate_owner=_optional_string(row.get("candidate_owner")),
        candidate_lease_expires_at=_optional_datetime(
            row.get("candidate_lease_expires_at")
        ),
        scope_state=_optional_string(row.get("scope_state")),
        scope_row_version=_nonnegative_int(
            row.get("scope_row_version"),
            "DTS_SOURCE_SCOPE_TARGET_RESPONSE_INVALID",
        ),
        scope_active_snapshot_id=_optional_string(
            row.get("scope_active_snapshot_id")
        ),
        scope_candidate_snapshot_id=_optional_string(
            row.get("scope_candidate_snapshot_id")
        ),
        snapshot_state=_optional_string(row.get("snapshot_state")),
        snapshot_row_count=_optional_nonnegative_int(
            row.get("snapshot_row_count")
        ),
        snapshot_content_hash=_optional_sha256(
            row.get("snapshot_content_hash")
        ),
        snapshot_fence_hash=_optional_sha256(
            row.get("snapshot_fence_hash")
        ),
        snapshot_source_schema_profile_id=_optional_string(
            row.get("snapshot_source_schema_profile_id")
        ),
        snapshot_source_field_types=(
            None
            if row.get("snapshot_source_field_types") is None
            else dict(
                _mapping(
                    row.get("snapshot_source_field_types"),
                    "DTS_SOURCE_SCOPE_TARGET_RESPONSE_INVALID",
                )
            )
        ),
        snapshot_published_generation=_optional_positive_int(
            row.get("published_generation")
        ),
        snapshot_error_code=_optional_string(row.get("snapshot_error_code")),
    )


def _require_candidate_can_start(
    candidate: SourceScopeSnapshotCandidate,
    target: SourceScopeTargetState,
    *,
    owner: str,
) -> None:
    if target.snapshot_state == "COMPLETE":
        _require_complete_readback(candidate, target)
        return
    if target.snapshot_state in {"FAILED", "STALE", "SUPERSEDED"}:
        _fail("SOURCE_SCOPE_SNAPSHOT_ID_TERMINAL")
    if target.active_candidate_snapshot_id is not None and (
        target.active_candidate_snapshot_id != candidate.snapshot_id
        or target.candidate_owner != owner
    ):
        _fail("SOURCE_TABLE_ACTIVE_CANDIDATE_CONFLICT")


def _require_candidate_identity(
    candidate: SourceScopeSnapshotCandidate,
    target: SourceScopeTargetState,
    *,
    owner: str,
) -> None:
    if target.snapshot_state == "COMPLETE":
        return
    if (
        target.active_candidate_snapshot_id != candidate.snapshot_id
        or target.scope_candidate_snapshot_id != candidate.snapshot_id
        or target.candidate_owner != owner
    ):
        _fail("SOURCE_SCOPE_CANDIDATE_AUTHORITY_DENIED")


def _require_verifying_readback(
    candidate: SourceScopeSnapshotCandidate,
    target: SourceScopeTargetState,
) -> None:
    if (
        target.snapshot_state != "VERIFYING"
        or target.scope_state != "VERIFYING"
        or target.snapshot_row_count != candidate.row_count
        or target.snapshot_content_hash != candidate.content_hash
        or target.snapshot_fence_hash != candidate.fence_hash
        or target.snapshot_source_schema_profile_id
        != candidate.source_schema_profile_id
        or target.snapshot_source_field_types != candidate.source_field_types
    ):
        _fail("DTS_SOURCE_SCOPE_VERIFY_READBACK_MISMATCH")


def _require_complete_readback(
    candidate: SourceScopeSnapshotCandidate,
    target: SourceScopeTargetState,
) -> None:
    if (
        target.snapshot_state != "COMPLETE"
        or target.scope_state != "COMPLETE"
        or target.scope_active_snapshot_id != candidate.snapshot_id
        or target.current_snapshot_id != candidate.snapshot_id
        or target.snapshot_row_count != candidate.row_count
        or target.snapshot_content_hash != candidate.content_hash
        or target.snapshot_fence_hash != candidate.fence_hash
        or target.snapshot_source_schema_profile_id
        != candidate.source_schema_profile_id
        or target.snapshot_source_field_types != candidate.source_field_types
        or target.snapshot_published_generation != target.current_generation
    ):
        _fail("DTS_SOURCE_SCOPE_PUBLISH_READBACK_MISMATCH")


def _target_after_verified(
    previous: SourceScopeTargetState,
    response: Mapping[str, Any],
) -> SourceScopeTargetState:
    return SourceScopeTargetState(
        head_row_version=_positive_int(
            response.get("head_row_version"),
            "DTS_SOURCE_SCOPE_VERIFY_RESPONSE_INVALID",
        ),
        current_generation=previous.current_generation,
        current_snapshot_id=previous.current_snapshot_id,
        active_candidate_snapshot_id=previous.active_candidate_snapshot_id,
        candidate_owner=previous.candidate_owner,
        candidate_lease_expires_at=previous.candidate_lease_expires_at,
        scope_state="VERIFYING",
        scope_row_version=_positive_int(
            response.get("scope_row_version"),
            "DTS_SOURCE_SCOPE_VERIFY_RESPONSE_INVALID",
        ),
        scope_active_snapshot_id=previous.scope_active_snapshot_id,
        scope_candidate_snapshot_id=previous.scope_candidate_snapshot_id,
        snapshot_state="VERIFYING",
        snapshot_row_count=_nonnegative_int(
            response.get("row_count"),
            "DTS_SOURCE_SCOPE_VERIFY_RESPONSE_INVALID",
        ),
        snapshot_content_hash=_sha256(
            response.get("content_hash"),
            "DTS_SOURCE_SCOPE_VERIFY_RESPONSE_INVALID",
        ),
        snapshot_fence_hash=_sha256(
            response.get("fence_hash"),
            "DTS_SOURCE_SCOPE_VERIFY_RESPONSE_INVALID",
        ),
        snapshot_source_schema_profile_id=(
            previous.snapshot_source_schema_profile_id
        ),
        snapshot_source_field_types=previous.snapshot_source_field_types,
        snapshot_published_generation=None,
        snapshot_error_code=None,
    )


def _command_response(
    value: Any,
    *,
    snapshot_id: str | None,
    statuses: set[str],
    error: str,
) -> dict[str, Any]:
    result = _mapping(value, error)
    if result.get("status") not in statuses:
        _fail(error)
    if snapshot_id is not None and result.get("snapshot_id") != snapshot_id:
        _fail(error)
    return dict(result)


def _artifact_format(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".json":
        return "JSON"
    if suffix == ".jsonl":
        return "JSONL"
    _fail("DTS_SOURCE_SCOPE_ARTIFACT_FORMAT_UNSUPPORTED")


def _decode_json(raw: bytes) -> Any:
    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_float=Decimal,
            parse_constant=_reject_non_finite,
        )
    except UnicodeDecodeError as exc:
        raise DtsSourceScopeSnapshotError(
            "DTS_SOURCE_SCOPE_ARTIFACT_ENCODING_INVALID"
        ) from exc
    except json.JSONDecodeError as exc:
        raise DtsSourceScopeSnapshotError(
            "DTS_SOURCE_SCOPE_ARTIFACT_JSON_INVALID"
        ) from exc


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("DTS_SOURCE_SCOPE_ARTIFACT_JSON_KEY_DUPLICATE")
        result[key] = value
    return result


def _reject_non_finite(value: str) -> None:
    del value
    _fail("DTS_SOURCE_SCOPE_ARTIFACT_JSON_INVALID")


def _canonical_json(value: Any) -> str:
    if isinstance(value, Mapping):
        items = sorted(value.items(), key=lambda item: item[0].encode("utf-8"))
        if any(not isinstance(key, str) for key, _ in items):
            _fail("DTS_SOURCE_SCOPE_JSON_VALUE_INVALID")
        return "{" + ",".join(
            f"{json.dumps(key, ensure_ascii=False)}:{_canonical_json(item)}"
            for key, item in items
        ) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_canonical_json(item) for item in value) + "]"
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if type(value) is int:
        return str(value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            _fail("DTS_SOURCE_SCOPE_JSON_VALUE_INVALID")
        return _decimal_text(value)
    if isinstance(value, float):
        try:
            decimal = Decimal(str(value))
        except InvalidOperation as exc:
            raise DtsSourceScopeSnapshotError(
                "DTS_SOURCE_SCOPE_JSON_VALUE_INVALID"
            ) from exc
        if not decimal.is_finite():
            _fail("DTS_SOURCE_SCOPE_JSON_VALUE_INVALID")
        return _decimal_text(decimal)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    _fail("DTS_SOURCE_SCOPE_JSON_VALUE_INVALID")


def _decimal_text(value: Decimal) -> str:
    rendered = format(abs(value) if value == 0 else value, "f")
    return rendered


def _hash_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _timestamp(value: Any, error: str) -> datetime:
    if not isinstance(value, str) or not value or value.strip() != value:
        _fail(error)
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise DtsSourceScopeSnapshotError(error) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _fail(error)
    return parsed.astimezone(timezone.utc)


def _timestamp_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")


def _optional_timestamp_text(value: datetime | None) -> str | None:
    return None if value is None else _timestamp_text(value)


def _public_scalar(value: Any) -> Any:
    if isinstance(value, datetime):
        return _timestamp_text(value)
    if isinstance(value, Decimal):
        return _decimal_text(value)
    return value


def _mapping(value: Any, error: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(
        not isinstance(key, str) for key in value
    ):
        _fail(error)
    return value


def _required_string(
    value: Any,
    error: str,
    *,
    maximum: int,
    reject_control: bool = False,
) -> str:
    if not _valid_bounded_string(value, maximum=maximum):
        _fail(error)
    if reject_control and any(ord(character) < 32 for character in value):
        _fail(error)
    return value


def _valid_bounded_string(value: Any, *, maximum: int) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value.strip() == value
        and len(value) <= maximum
    )


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        _fail("DTS_SOURCE_SCOPE_TARGET_RESPONSE_INVALID")
    return value


def _sha256(value: Any, error: str) -> str:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        _fail(error)
    return value


def _optional_sha256(value: Any) -> str | None:
    if value is None:
        return None
    return _sha256(value, "DTS_SOURCE_SCOPE_TARGET_RESPONSE_INVALID")


def _nonnegative_int(value: Any, error: str) -> int:
    if type(value) is not int or value < 0 or value > 9_223_372_036_854_775_807:
        _fail(error)
    return value


def _positive_int(value: Any, error: str) -> int:
    result = _nonnegative_int(value, error)
    if result < 1:
        _fail(error)
    return result


def _optional_nonnegative_int(value: Any) -> int | None:
    if value is None:
        return None
    return _nonnegative_int(value, "DTS_SOURCE_SCOPE_TARGET_RESPONSE_INVALID")


def _optional_positive_int(value: Any) -> int | None:
    if value is None:
        return None
    return _positive_int(value, "DTS_SOURCE_SCOPE_TARGET_RESPONSE_INVALID")


def _optional_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, datetime) or value.tzinfo is None:
        _fail("DTS_SOURCE_SCOPE_TARGET_RESPONSE_INVALID")
    return value


def database_error_code(error: BaseException) -> str:
    """Return only a stable, payload-free database error code."""

    original = getattr(error, "orig", None)
    diagnostic = getattr(original, "diag", None)
    primary = getattr(diagnostic, "message_primary", None)
    if isinstance(primary, str):
        match = _STABLE_DATABASE_ERROR_PATTERN.search(primary)
        if match is not None:
            return match.group(0)
    return "DTS_SOURCE_SCOPE_DATABASE_COMMAND_FAILED"


def _fail(code: str) -> None:
    raise DtsSourceScopeSnapshotError(code)


__all__ = [
    "DtsSourceScopeSnapshotCoordinator",
    "DtsSourceScopeSnapshotError",
    "PostgresDtsSourceScopeSnapshotStore",
    "PROTOCOL_VERSION",
    "REQUIRED_DATABASE_ROLE",
    "SNAPSHOT_ID_PREFIX",
    "SourceScopeApplyResult",
    "SourceScopeDatabaseHealth",
    "SourceScopeSnapshotCandidate",
    "SourceScopeSnapshotRow",
    "SourceScopeTargetState",
    "database_error_code",
    "load_candidate_artifact",
    "rescan_candidate_artifact",
]
