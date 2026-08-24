from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine

from app.dts_source_profile_registry_v2 import (
    load_dts_source_profile_registry,
)
from app.dts_source_scope_snapshot_coordinator import (
    DtsSourceScopeSnapshotCoordinator,
    DtsSourceScopeSnapshotError,
    PROTOCOL_VERSION,
    SourceScopeDatabaseHealth,
    SourceScopeTargetState,
    _canonical_json,
    _hash_json,
    load_candidate_artifact,
)


def _profile_artifact(
    tmp_path: Path,
    *,
    extra_persisted_field: str | None = None,
) -> tuple[Path, str, str]:
    profile_path = tmp_path / "source-profiles.json"
    selected_fields = ["center_type", "id", "status"]
    field_types = {
        "center_type": "NUMERIC",
        "id": "NUMERIC",
        "status": "TEXT",
    }
    raw_field_types = {
        "center_type": 3,
        "id": 3,
        "status": 253,
    }
    if extra_persisted_field is not None:
        selected_fields.append(extra_persisted_field)
        selected_fields.sort()
        field_types[extra_persisted_field] = "TEXT"
        raw_field_types[extra_persisted_field] = 253
    profile_document = {
        "manifest_version": 1,
        "profiles": [
            {
                "table": "dom_teacher",
                "region": "dom",
                "primary_key_type": "NUMERIC",
                "selected_raw_fields": selected_fields,
                "selected_field_set_policy": "EXACT",
                "persisted_protected_fields": selected_fields,
                "protected_derived_fields": [],
                "image_modes": {
                    "INSERT": "FULL",
                    "UPDATE": "FULL",
                    "DELETE": "FULL",
                },
                "field_type_evidence": field_types,
                "raw_field_type_numbers": raw_field_types,
                "evidence": {
                    "provenance": "reviewed-offline-source-export-fixture",
                    "sha256": "a" * 64,
                },
            }
        ],
    }
    raw = json.dumps(
        profile_document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    profile_path.write_bytes(raw)
    registry = load_dts_source_profile_registry(profile_path)
    profile_hash = registry.manifest_sha256
    profile = registry.profile_for("dom_teacher")
    assert profile is not None
    return profile_path, profile_hash, profile.profile_id


def _candidate_document(
    *,
    profile_hash: str,
    profile_id: str,
    rows: list[dict[str, Any]] | None = None,
    source_partition_epoch_id: str = "epoch-dom-fixture",
    start_next_offset: int = 11,
    end_next_offset: int = 19,
    scope_level: str = "GLOBAL",
    scope_key: str = "*",
) -> dict[str, Any]:
    rows = rows if rows is not None else [
        {
            "source_key_data": {"id": 42},
            "dependency_keys": {
                "category_ids": [],
                "course_ids": [],
                "label_ids": [],
                "student_subjects": [],
                "teacher_ids": ["42"],
            },
            "protected_source_row": {
                "center_type": 1,
                "id": 42,
                "status": "on",
            },
        }
    ]
    manifest_rows = [
        {
            "source_key_data": row["source_key_data"],
            "snapshot_row_hash": _hash_json(row["protected_source_row"]),
            "dependency_keys_hash": _hash_json(row["dependency_keys"]),
        }
        for row in rows
    ]
    content_hash = _hash_json(manifest_rows)
    token = "source-transaction-token-fixture"
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    offsets = [
        {
            "source_region": "dom",
            "source_partition_epoch_id": source_partition_epoch_id,
            "topic": "topic-dom-fixture",
            "partition_id": 0,
            "start_next_offset": start_next_offset,
            "end_next_offset": end_next_offset,
        }
    ]
    fence_hash = _hash_json(offsets)
    source_profile = {
        "manifest_version": 1,
        "manifest_sha256": profile_hash,
        "profile_id": profile_id,
    }
    header = {
        "protocol_version": PROTOCOL_VERSION,
        "snapshot_id": "pending",
        "source_region": "dom",
        "source_table": "dom_teacher",
        "scope_kind": "CURRENT",
        "scope_level": scope_level,
        "scope_key": scope_key,
        "snapshot_as_of": "2026-08-22T12:00:00.000000Z",
        "snapshot_consistency_token": token,
        "snapshot_consistency_token_sha256": token_hash,
        "source_profile": source_profile,
        "source_export_evidence_sha256": "b" * 64,
        "fence_evidence_sha256": "c" * 64,
        "partition_offsets": offsets,
        "history_from": None,
        "history_through": None,
    }
    evidence = {
        key: value
        for key, value in header.items()
        if key
        not in {"snapshot_id", "snapshot_consistency_token"}
    }
    evidence.update(
        {
            "row_count": len(rows),
            "content_hash": content_hash,
            "fence_hash": fence_hash,
        }
    )
    candidate_hash = _hash_json(evidence)
    header["snapshot_id"] = f"scope-v1:{candidate_hash}"
    return {
        **header,
        "rows": rows,
        "row_count": len(rows),
        "content_hash": content_hash,
        "fence_hash": fence_hash,
        "candidate_sha256": candidate_hash,
    }


def _write_candidate_json(
    tmp_path: Path,
    document: dict[str, Any],
) -> tuple[Path, str]:
    path = tmp_path / "candidate.json"
    raw = _canonical_json(document).encode("utf-8")
    path.write_bytes(raw)
    return path, hashlib.sha256(raw).hexdigest()


def _write_candidate_jsonl(
    tmp_path: Path,
    document: dict[str, Any],
) -> tuple[Path, str]:
    path = tmp_path / "candidate.jsonl"
    header_keys = {
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
    lines = [
        _canonical_json(
            {
                "record_type": "snapshot_header",
                **{key: document[key] for key in header_keys},
            }
        )
    ]
    lines.extend(
        _canonical_json({"record_type": "snapshot_row", **row})
        for row in document["rows"]
    )
    lines.append(
        _canonical_json(
            {
                "record_type": "snapshot_manifest",
                "row_count": document["row_count"],
                "content_hash": document["content_hash"],
                "fence_hash": document["fence_hash"],
                "candidate_sha256": document["candidate_sha256"],
            }
        )
    )
    raw = ("\n".join(lines) + "\n").encode("utf-8")
    path.write_bytes(raw)
    return path, hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize("artifact_format", ["json", "jsonl"])
def test_candidate_artifact_is_hash_profile_and_privacy_bound(
    tmp_path: Path,
    artifact_format: str,
) -> None:
    profile_path, profile_hash, profile_id = _profile_artifact(tmp_path)
    document = _candidate_document(
        profile_hash=profile_hash,
        profile_id=profile_id,
    )
    candidate_path, artifact_hash = (
        _write_candidate_json(tmp_path, document)
        if artifact_format == "json"
        else _write_candidate_jsonl(tmp_path, document)
    )

    candidate = load_candidate_artifact(
        candidate_path,
        profile_manifest_path=profile_path,
        expected_artifact_sha256=artifact_hash,
        expected_profile_manifest_sha256=profile_hash,
    )

    assert candidate.row_count == 1
    assert candidate.content_hash == document["content_hash"]
    assert candidate.snapshot_id == document["snapshot_id"]
    public = candidate.public_summary()
    assert "snapshot_consistency_token" not in public
    assert "rows" not in public
    assert public["source_export_evidence_sha256"] == "b" * 64


def test_candidate_rejects_hash_profile_row_order_and_domestic_raw_id(
    tmp_path: Path,
) -> None:
    profile_path, profile_hash, profile_id = _profile_artifact(tmp_path)
    document = _candidate_document(
        profile_hash=profile_hash,
        profile_id=profile_id,
    )
    candidate_path, artifact_hash = _write_candidate_json(tmp_path, document)

    with pytest.raises(
        DtsSourceScopeSnapshotError,
        match="DTS_SOURCE_SCOPE_ARTIFACT_HASH_MISMATCH",
    ):
        load_candidate_artifact(
            candidate_path,
            profile_manifest_path=profile_path,
            expected_artifact_sha256="f" * 64,
            expected_profile_manifest_sha256=profile_hash,
        )

    changed_profile = dict(document)
    changed_profile["source_profile"] = {
        **document["source_profile"],
        "profile_id": "dts-source-schema:v2:" + "0" * 64,
    }
    candidate_path, artifact_hash = _write_candidate_json(
        tmp_path,
        changed_profile,
    )
    with pytest.raises(
        DtsSourceScopeSnapshotError,
        match="DTS_SOURCE_SCOPE_PROFILE_REFERENCE_MISMATCH",
    ):
        load_candidate_artifact(
            candidate_path,
            profile_manifest_path=profile_path,
            expected_artifact_sha256=artifact_hash,
            expected_profile_manifest_sha256=profile_hash,
        )


def test_candidate_rejects_profile_fields_outside_business_whitelist(
    tmp_path: Path,
) -> None:
    profile_path, profile_hash, profile_id = _profile_artifact(
        tmp_path,
        extra_persisted_field="private_note",
    )
    document = _candidate_document(
        profile_hash=profile_hash,
        profile_id=profile_id,
    )
    candidate_path, artifact_hash = _write_candidate_json(tmp_path, document)

    with pytest.raises(
        DtsSourceScopeSnapshotError,
        match="DTS_SOURCE_SCOPE_PROFILE_PERSISTED_FIELD_NOT_ALLOWED",
    ):
        load_candidate_artifact(
            candidate_path,
            profile_manifest_path=profile_path,
            expected_artifact_sha256=artifact_hash,
            expected_profile_manifest_sha256=profile_hash,
        )

class _FakeScopeStore:
    def __init__(self) -> None:
        self.phase: str | None = None
        self.head_version = 1
        self.scope_version = 0
        self.staged = 0
        self.begin_calls = 0
        self.publish_calls = 0
        self.candidate = None
        self.owner = None
        self.profile_bound = False

    def read_health(self, _connection) -> SourceScopeDatabaseHealth:
        return SourceScopeDatabaseHealth(
            ready=True,
            code="DTS_SOURCE_SCOPE_COORDINATOR_READY",
            current_user="tit_growth_app",
            session_user="tit_growth_app",
            schema_ready=True,
            functions_ready=True,
            direct_write_blocked=True,
        )

    def read_target_state(self, _connection, candidate) -> SourceScopeTargetState:
        complete = self.phase == "COMPLETE"
        return SourceScopeTargetState(
            head_row_version=self.head_version,
            current_generation=1 if complete else 0,
            current_snapshot_id=candidate.snapshot_id if complete else None,
            active_candidate_snapshot_id=(
                candidate.snapshot_id
                if self.phase in {"LOADING", "VERIFYING"}
                else None
            ),
            candidate_owner=(
                self.owner if self.phase in {"LOADING", "VERIFYING"} else None
            ),
            candidate_lease_expires_at=(
                datetime(2026, 8, 23, tzinfo=timezone.utc)
                if self.phase in {"LOADING", "VERIFYING"}
                else None
            ),
            scope_state=self.phase,
            scope_row_version=self.scope_version,
            scope_active_snapshot_id=candidate.snapshot_id if complete else None,
            scope_candidate_snapshot_id=(
                candidate.snapshot_id
                if self.phase in {"LOADING", "VERIFYING"}
                else None
            ),
            snapshot_state=self.phase,
            snapshot_row_count=(
                candidate.row_count
                if self.phase in {"VERIFYING", "COMPLETE"}
                else None
            ),
            snapshot_content_hash=(
                candidate.content_hash
                if self.phase in {"VERIFYING", "COMPLETE"}
                else None
            ),
            snapshot_fence_hash=(
                candidate.fence_hash if self.phase is not None else None
            ),
            snapshot_source_schema_profile_id=(
                candidate.source_schema_profile_id
                if self.profile_bound
                else None
            ),
            snapshot_source_field_types=(
                candidate.source_field_types if self.profile_bound else None
            ),
            snapshot_published_generation=1 if complete else None,
            snapshot_error_code=None,
        )

    def begin(self, _connection, *, candidate, owner, **_kwargs):
        self.begin_calls += 1
        self.candidate = candidate
        self.owner = owner
        if self.phase is None:
            self.phase = "LOADING"
            self.head_version += 1
            self.scope_version = 1
        return {
            "status": "STARTED",
            "snapshot_id": candidate.snapshot_id,
            "lease_token": "lease-fixture",
            "head_row_version": self.head_version,
            "scope_row_version": self.scope_version,
        }

    def read_begin_response(self, _connection, candidate):
        return {
            "status": "STARTED",
            "snapshot_id": candidate.snapshot_id,
            "lease_token": "lease-fixture",
            "head_row_version": 2,
            "scope_row_version": 1,
        }

    def bind_profile(self, _connection, *, candidate, **_kwargs):
        self.profile_bound = True
        return {
            "status": "BOUND",
            "snapshot_id": candidate.snapshot_id,
            "source_schema_profile_id": candidate.source_schema_profile_id,
            "source_field_types": candidate.source_field_types,
        }

    def stage_row(self, _connection, **_kwargs):
        self.staged += 1
        return {"status": "STAGED"}

    def heartbeat(self, _connection, *, candidate, **_kwargs):
        self.head_version += 1
        return {
            "status": "HEARTBEAT",
            "snapshot_id": candidate.snapshot_id,
            "head_row_version": self.head_version,
            "scope_row_version": self.scope_version,
        }

    def verify(self, _connection, *, candidate, **_kwargs):
        self.phase = "VERIFYING"
        self.head_version += 1
        self.scope_version += 1
        return {
            "status": "VERIFIED",
            "snapshot_id": candidate.snapshot_id,
            "row_count": candidate.row_count,
            "content_hash": candidate.content_hash,
            "fence_hash": candidate.fence_hash,
            "head_row_version": self.head_version,
            "scope_row_version": self.scope_version,
        }

    def publish(self, _connection, *, candidate, **_kwargs):
        self.publish_calls += 1
        self.phase = "COMPLETE"
        self.head_version += 1
        self.scope_version += 1
        return {"status": "PUBLISHED", "snapshot_id": candidate.snapshot_id}


def test_coordinator_dry_run_is_read_only_and_apply_is_response_lost_safe(
    tmp_path: Path,
) -> None:
    profile_path, profile_hash, profile_id = _profile_artifact(tmp_path)
    document = _candidate_document(
        profile_hash=profile_hash,
        profile_id=profile_id,
    )
    candidate_path, artifact_hash = _write_candidate_json(tmp_path, document)
    candidate = load_candidate_artifact(
        candidate_path,
        profile_manifest_path=profile_path,
        expected_artifact_sha256=artifact_hash,
        expected_profile_manifest_sha256=profile_hash,
    )
    engine = create_engine("sqlite+pysqlite:///:memory:")
    store = _FakeScopeStore()
    coordinator = DtsSourceScopeSnapshotCoordinator(
        engine=engine,
        owner="scope-fixture-owner",
        stage_batch_size=1,
        store=store,  # type: ignore[arg-type]
    )

    _, dry_target = coordinator.dry_run(candidate)
    assert dry_target.snapshot_state is None
    assert store.begin_calls == 0

    first = coordinator.apply(candidate)
    assert first.status == "PUBLISHED"
    assert first.target.snapshot_state == "COMPLETE"
    assert store.staged == 1
    assert store.publish_calls == 1

    replay = coordinator.apply(candidate)
    assert replay.status == "REPLAYED_COMPLETE"
    assert store.staged == 1
    assert store.publish_calls == 1
    assert store.begin_calls == 1
