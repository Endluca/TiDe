from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path
import shutil
import socket
import subprocess

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

from app import dts_source_contract_v2 as source_contract
from app.dts_source_consumer import (
    DtsChangeEvent,
    DtsConsumerSettings,
    protect_domestic_student_ids,
)
from app.dts_source_contract_v2 import with_v2_source_image_completeness
from app.dts_source_scope_snapshot_coordinator import (
    DtsSourceScopeSnapshotCoordinator,
    PostgresDtsSourceScopeSnapshotStore,
    _canonical_json,
    load_candidate_artifact,
)
from app.dts_v2_shadow_source_writer import DtsV2ShadowSourceWriter
from test_dts_source_scope_snapshot_coordinator import (
    _candidate_document,
    _profile_artifact,
    _write_candidate_json,
)
from test_dts_v2_source_current_guards_postgres import (
    _postgres_tools_available,
    _run_alembic,
    _seed_external_personalized_catalog,
)


@pytest.mark.skipif(
    not _postgres_tools_available(),
    reason="local PostgreSQL binaries are required for source-scope runtime",
)
def test_restricted_runtime_dry_run_publish_replay_and_readback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend_dir = Path(__file__).resolve().parents[1]
    data_dir = tmp_path / "postgres-data"
    log_path = tmp_path / "postgres.log"
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    subprocess.run(
        [
            shutil.which("initdb") or "initdb",
            "-D",
            str(data_dir),
            "-A",
            "trust",
            "-U",
            "postgres",
            "--no-locale",
            "--encoding=UTF8",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    pg_ctl = shutil.which("pg_ctl") or "pg_ctl"
    subprocess.run(
        [
            pg_ctl,
            "-D",
            str(data_dir),
            "-l",
            str(log_path),
            "-o",
            f"-p {port} -c listen_addresses=127.0.0.1 -c fsync=off",
            "-w",
            "start",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    def database_url(role: str) -> str:
        return URL.create(
            "postgresql+psycopg",
            username=role,
            host="127.0.0.1",
            port=port,
            database="postgres",
        ).render_as_string(hide_password=False)

    admin_url = database_url("postgres")
    admin = create_engine(admin_url)
    restricted = None
    ingest = None
    try:
        with admin.begin() as connection:
            for role_name in (
                "tit_growth_app",
                "tit_teacher_crud",
                "tit_dts_ingest_runtime",
                "tide_support_ticket_owner",
            ):
                connection.execute(
                    text(
                        f"CREATE ROLE {role_name} LOGIN NOINHERIT "
                        "NOSUPERUSER NOCREATEDB NOCREATEROLE "
                        "NOREPLICATION NOBYPASSRLS"
                    )
                )

        _run_alembic(
            backend_dir,
            admin_url,
            "upgrade",
            "20260807_46_teacher_g01_source",
        )
        with admin.begin() as connection:
            _seed_external_personalized_catalog(connection)
        _run_alembic(
            backend_dir,
            admin_url,
            "upgrade",
            "20260823_100_scope_snapshot_diff",
        )
        with admin.connect() as connection:
            assert connection.execute(
                text("SELECT version_num FROM public.alembic_version")
            ).scalar_one() == "20260823_100_scope_snapshot_diff"

        with admin.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO public.dts_ingest_checkpoints (
                        source_region,topic,partition_id,next_offset,
                        source_timestamp,source_position,updated_at
                    ) VALUES (
                        'dom','topic-dom-fixture',0,0,0,
                        'scope-runtime-fixture',CURRENT_TIMESTAMP
                    )
                    """
                )
            )
            epoch_id = connection.execute(
                text(
                    "SELECT public.dts_broker_epoch_id_v2("
                    "'dom','topic-dom-fixture',0,'generation-fixture',"
                    "'opening-fixture')"
                )
            ).scalar_one()
            routes = [
                {
                    "source_region": "dom",
                    "topic": "topic-dom-fixture",
                    "partition_id": 0,
                    "current_next_offset": 0,
                    "consumer_group": "scope-runtime-group",
                    "stream_generation_id": "generation-fixture",
                    "epoch_opening_id": "opening-fixture",
                    "source_partition_epoch_id": epoch_id,
                }
            ]
            vector_hash = connection.execute(
                text(
                    "SELECT public.dts_initial_broker_epoch_vector_hash_v2("
                    "CAST(:routes AS jsonb))"
                ),
                {"routes": json.dumps(routes)},
            ).scalar_one()
            connection.execute(
                text(
                    "SELECT public.bootstrap_initial_broker_epoch_v2("
                    "'scope-runtime-h0','scope-runtime-group',"
                    "CAST(:routes AS jsonb),:vector_hash)"
                ),
                {"routes": json.dumps(routes), "vector_hash": vector_hash},
            )

        profile_path, profile_hash, profile_id = _profile_artifact(tmp_path)
        teacher_42_on = {
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
        document = _candidate_document(
            profile_hash=profile_hash,
            profile_id=profile_id,
            rows=[teacher_42_on],
            source_partition_epoch_id=epoch_id,
            start_next_offset=0,
            end_next_offset=0,
        )
        candidate_path, artifact_hash = _write_candidate_json(
            tmp_path,
            document,
        )
        candidate = load_candidate_artifact(
            candidate_path,
            profile_manifest_path=profile_path,
            expected_artifact_sha256=artifact_hash,
            expected_profile_manifest_sha256=profile_hash,
        )

        restricted = create_engine(
            database_url("tit_growth_app")
        )
        coordinator = DtsSourceScopeSnapshotCoordinator(
            engine=restricted,
            owner="scope-runtime-fixture",
            store=PostgresDtsSourceScopeSnapshotStore(),
        )
        health, target = coordinator.dry_run(candidate)
        assert health.ready is True
        assert target.scope_state is None
        with admin.connect() as connection:
            assert connection.execute(
                text("SELECT count(*) FROM public.dts_source_scope_commands")
            ).scalar_one() == 0

        published = coordinator.apply(candidate)
        assert published.status == "PUBLISHED"
        assert published.staged_count == 1
        assert published.target.snapshot_state == "COMPLETE"
        assert published.target.snapshot_content_hash == candidate.content_hash
        assert published.target.snapshot_fence_hash == candidate.fence_hash
        assert (
            published.target.snapshot_source_schema_profile_id == profile_id
        )
        assert published.target.snapshot_source_field_types == {
            "center_type": "NUMERIC",
            "id": "NUMERIC",
            "status": "TEXT",
        }

        replay = coordinator.apply(candidate)
        assert replay.status == "REPLAYED_COMPLETE"
        with restricted.connect() as connection:
            readback = PostgresDtsSourceScopeSnapshotStore().read_snapshot(
                connection,
                candidate.snapshot_id,
            )
        assert readback is not None
        assert readback["epoch_state"] == "COMPLETE"
        assert "snapshot_consistency_token" not in readback
        assert readback["snapshot_consistency_token_hash"] == hashlib.sha256(
            b"source-transaction-token-fixture"
        ).hexdigest()

        with admin.connect() as connection:
            current_42 = connection.execute(
                text(
                    """
                    SELECT source_row_revision,is_deleted,source_row,
                           last_version_kind
                    FROM public.dts_source_rows
                    WHERE source_region='dom'
                      AND source_table='dom_teacher'
                      AND source_key='42'
                    """
                )
            ).mappings().one()
            first_operations = connection.execute(
                text(
                    """
                    SELECT operation,source_row_revision,diff_step
                    FROM public.dts_source_row_versions
                    WHERE snapshot_id=:snapshot_id
                    ORDER BY offset_value
                    """
                ),
                {"snapshot_id": candidate.snapshot_id},
            ).mappings().all()
        assert current_42["source_row_revision"] == 1
        assert current_42["is_deleted"] is False
        assert current_42["source_row"]["status"] == "on"
        assert current_42["last_version_kind"] == "SNAPSHOT_DIFF"
        assert [row["operation"] for row in first_operations] == [
            "SNAPSHOT_INSERT"
        ]

        # A teacher-scoped replacement controls only that membership set.
        # Removing the row from the teacher scope must not tombstone the
        # global source current.
        teacher_document = _candidate_document(
            profile_hash=profile_hash,
            profile_id=profile_id,
            rows=[teacher_42_on],
            source_partition_epoch_id=epoch_id,
            start_next_offset=0,
            end_next_offset=0,
            scope_level="TEACHER",
            scope_key="42",
        )
        teacher_path, teacher_hash = _write_candidate_json(
            tmp_path,
            teacher_document,
        )
        teacher_candidate = load_candidate_artifact(
            teacher_path,
            profile_manifest_path=profile_path,
            expected_artifact_sha256=teacher_hash,
            expected_profile_manifest_sha256=profile_hash,
        )
        assert coordinator.apply(teacher_candidate).status == "PUBLISHED"

        teacher_empty_document = _candidate_document(
            profile_hash=profile_hash,
            profile_id=profile_id,
            rows=[],
            source_partition_epoch_id=epoch_id,
            start_next_offset=0,
            end_next_offset=0,
            scope_level="TEACHER",
            scope_key="42",
        )
        teacher_empty_path, teacher_empty_hash = _write_candidate_json(
            tmp_path,
            teacher_empty_document,
        )
        teacher_empty_candidate = load_candidate_artifact(
            teacher_empty_path,
            profile_manifest_path=profile_path,
            expected_artifact_sha256=teacher_empty_hash,
            expected_profile_manifest_sha256=profile_hash,
        )
        assert coordinator.apply(teacher_empty_candidate).status == "PUBLISHED"
        with admin.connect() as connection:
            teacher_current = connection.execute(
                text(
                    """
                    SELECT source_row_revision,is_deleted
                    FROM public.dts_source_rows
                    WHERE source_region='dom'
                      AND source_table='dom_teacher'
                      AND source_key='42'
                    """
                )
            ).mappings().one()
            teacher_membership = connection.execute(
                text(
                    """
                    SELECT active_snapshot_id,snapshot_is_present,
                           cdc_overlay_is_present
                    FROM public.dts_source_scope_memberships
                    WHERE source_region='dom'
                      AND source_table='dom_teacher'
                      AND scope_kind='CURRENT'
                      AND scope_level='TEACHER'
                      AND scope_key='42'
                      AND source_key='42'
                    """
                )
            ).mappings().one()
        assert teacher_current == {
            "source_row_revision": 1,
            "is_deleted": False,
        }
        assert teacher_membership["active_snapshot_id"] == (
            teacher_empty_candidate.snapshot_id
        )
        assert teacher_membership["snapshot_is_present"] is False
        assert teacher_membership["cdc_overlay_is_present"] is None

        # A later GLOBAL snapshot applies ordinary update/insert differences.
        teacher_42_off = {
            **teacher_42_on,
            "protected_source_row": {
                "center_type": 1,
                "id": 42,
                "status": "off",
            },
        }
        teacher_43_on = {
            **teacher_42_on,
            "source_key_data": {"id": 43},
            "dependency_keys": {
                **teacher_42_on["dependency_keys"],
                "teacher_ids": ["43"],
            },
            "protected_source_row": {
                "center_type": 5,
                "id": 43,
                "status": "on",
            },
        }
        second_document = _candidate_document(
            profile_hash=profile_hash,
            profile_id=profile_id,
            rows=[teacher_42_off, teacher_43_on],
            source_partition_epoch_id=epoch_id,
            start_next_offset=0,
            end_next_offset=0,
        )
        second_path, second_hash = _write_candidate_json(
            tmp_path,
            second_document,
        )
        second_candidate = load_candidate_artifact(
            second_path,
            profile_manifest_path=profile_path,
            expected_artifact_sha256=second_hash,
            expected_profile_manifest_sha256=profile_hash,
        )
        assert coordinator.apply(second_candidate).status == "PUBLISHED"
        with admin.connect() as connection:
            second_current = connection.execute(
                text(
                    """
                    SELECT source_key,source_row_revision,is_deleted,
                           source_row->>'status' status
                    FROM public.dts_source_rows
                    WHERE source_region='dom'
                      AND source_table='dom_teacher'
                    ORDER BY source_key_numeric
                    """
                )
            ).mappings().all()
            second_operations = connection.execute(
                text(
                    """
                    SELECT operation,source_row_revision,diff_step
                    FROM public.dts_source_row_versions
                    WHERE snapshot_id=:snapshot_id
                    ORDER BY offset_value
                    """
                ),
                {"snapshot_id": second_candidate.snapshot_id},
            ).mappings().all()
        assert second_current == [
            {
                "source_key": "42",
                "source_row_revision": 2,
                "is_deleted": False,
                "status": "off",
            },
            {
                "source_key": "43",
                "source_row_revision": 1,
                "is_deleted": False,
                "status": "on",
            },
        ]
        assert [row["operation"] for row in second_operations] == [
            "SNAPSHOT_UPDATE",
            "SNAPSHOT_INSERT",
        ]

        # CDC at the published barrier remains orderable after SNAPSHOT_DIFF
        # and updates both GLOBAL and TEACHER membership overlays atomically.
        teacher_fields = frozenset({"center_type", "id", "status"})
        monkeypatch.setitem(
            source_contract.V2_COMPLETE_IMAGE_SOURCE_FIELDS_BY_TABLE,
            "dom_teacher",
            teacher_fields,
        )
        monkeypatch.setitem(
            source_contract.V2_SOURCE_PRIMARY_KEY_TYPES_BY_TABLE,
            "dom_teacher",
            "NUMERIC",
        )
        monkeypatch.setitem(
            source_contract.V2_SOURCE_SCHEMA_PROFILE_IDS_BY_TABLE,
            "dom_teacher",
            profile_id,
        )
        monkeypatch.setitem(
            source_contract.V2_SOURCE_SELECTED_FIELD_SET_POLICY_BY_TABLE,
            "dom_teacher",
            "EXACT",
        )
        monkeypatch.setitem(
            source_contract.V2_SOURCE_IMAGE_MODES_BY_TABLE,
            "dom_teacher",
            {"INSERT": "FULL", "UPDATE": "FULL", "DELETE": "FULL"},
        )
        monkeypatch.setitem(
            source_contract.V2_PERSISTED_PROTECTED_FIELDS_BY_TABLE,
            "dom_teacher",
            teacher_fields,
        )
        monkeypatch.setitem(
            source_contract.V2_PROTECTED_DERIVED_FIELDS_BY_TABLE,
            "dom_teacher",
            frozenset(),
        )
        cdc_event = DtsChangeEvent(
            source_region="dom",
            topic="topic-dom-fixture",
            partition=0,
            offset=0,
            record_id=1,
            source_timestamp=1_787_500_001,
            source_txid="scope-runtime-cdc-1",
            source_position="scope-runtime-cdc-position-1",
            operation="UPDATE",
            database_name="source",
            schema_name="public",
            table_name="dom_teacher",
            before={"center_type": 1, "id": 42, "status": "off"},
            after={"center_type": 1, "id": 42, "status": "on"},
            source_field_types={
                "center_type": "NUMERIC",
                "id": "NUMERIC",
                "status": "TEXT",
            },
        )
        cdc_event = with_v2_source_image_completeness(cdc_event)
        cdc_event = protect_domestic_student_ids(
            cdc_event,
            DtsConsumerSettings(
                source_region="dom",
                broker_urls=("broker.invalid:9092",),
                topic="topic-dom-fixture",
                group_id="scope-runtime-cdc",
                account="test-account",
                password="test-password",
                execution_region="cn",
                domestic_student_hmac_key="11" * 32,
            ),
        )
        ingest = create_engine(database_url("tit_dts_ingest_runtime"))
        with ingest.begin() as connection:
            cdc_result = DtsV2ShadowSourceWriter(enabled=True).apply_cdc(
                connection,
                cdc_event,
                epoch_id,
            )
        assert cdc_result.status == "APPLIED"
        with admin.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE public.dts_ingest_checkpoints
                    SET next_offset=1,source_position='scope-runtime-cdc-1',
                        updated_at=clock_timestamp()
                    WHERE source_region='dom'
                      AND topic='topic-dom-fixture'
                      AND partition_id=0
                    """
                )
            )
        with admin.connect() as connection:
            cdc_current = connection.execute(
                text(
                    """
                    SELECT source_row_revision,last_version_kind,
                           source_row->>'status' status
                    FROM public.dts_source_rows
                    WHERE source_region='dom'
                      AND source_table='dom_teacher'
                      AND source_key='42'
                    """
                )
            ).mappings().one()
            cdc_memberships = connection.execute(
                text(
                    """
                    SELECT scope_level,scope_key,snapshot_is_present,
                           cdc_overlay_is_present,last_cdc_source_revision
                    FROM public.dts_source_scope_memberships
                    WHERE source_region='dom'
                      AND source_table='dom_teacher'
                      AND source_key='42'
                    ORDER BY scope_level,scope_key
                    """
                )
            ).mappings().all()
        assert cdc_current == {
            "source_row_revision": 3,
            "last_version_kind": "CDC",
            "status": "on",
        }
        assert cdc_memberships == [
            {
                "scope_level": "GLOBAL",
                "scope_key": "*",
                "snapshot_is_present": True,
                "cdc_overlay_is_present": True,
                "last_cdc_source_revision": 3,
            },
            {
                "scope_level": "TEACHER",
                "scope_key": "42",
                "snapshot_is_present": False,
                "cdc_overlay_is_present": True,
                "last_cdc_source_revision": 3,
            },
        ]

        # Missing rows in a GLOBAL replacement become explicit tombstones.
        empty_document = _candidate_document(
            profile_hash=profile_hash,
            profile_id=profile_id,
            rows=[],
            source_partition_epoch_id=epoch_id,
            start_next_offset=1,
            end_next_offset=1,
        )
        empty_path, empty_hash = _write_candidate_json(
            tmp_path,
            empty_document,
        )
        empty_candidate = load_candidate_artifact(
            empty_path,
            profile_manifest_path=profile_path,
            expected_artifact_sha256=empty_hash,
            expected_profile_manifest_sha256=profile_hash,
        )
        assert coordinator.apply(empty_candidate).status == "PUBLISHED"
        with admin.connect() as connection:
            tombstones = connection.execute(
                text(
                    """
                    SELECT source_key,source_row_revision,is_deleted
                    FROM public.dts_source_rows
                    WHERE source_region='dom'
                      AND source_table='dom_teacher'
                    ORDER BY source_key_numeric
                    """
                )
            ).mappings().all()
        assert tombstones == [
            {
                "source_key": "42",
                "source_row_revision": 4,
                "is_deleted": True,
            },
            {
                "source_key": "43",
                "source_row_revision": 2,
                "is_deleted": True,
            },
        ]

        # The local canonicaliser is part of the pre-write hash gate and must
        # agree with the PostgreSQL jsonb canonical function, including scale.
        numeric_document = {
            "a": Decimal("123.00"),
            "b": Decimal("0.000"),
            "c": ["中文", 42],
        }
        with admin.connect() as connection:
            database_hash = connection.execute(
                text(
                    "SELECT public.dts_canonical_json_sha256_v1("
                    "CAST(:document AS jsonb))"
                ),
                {"document": _canonical_json(numeric_document)},
            ).scalar_one()
        assert database_hash == hashlib.sha256(
            _canonical_json(numeric_document).encode("utf-8")
        ).hexdigest()
    finally:
        if ingest is not None:
            ingest.dispose()
        if restricted is not None:
            restricted.dispose()
        admin.dispose()
        subprocess.run(
            [pg_ctl, "-D", str(data_dir), "-m", "fast", "-w", "stop"],
            check=False,
            capture_output=True,
            text=True,
        )
