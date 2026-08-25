"""Atomic single-pipeline source capture, dirty enqueue and checkpoint sink.

One PostgreSQL transaction owns source version/current, authoritative
SOURCE_REVISION dirty inputs, immutable ingest receipt and checkpoint. After a
destructive reset the first event at/after ``TIT_DTS_START_TIMESTAMP`` creates
the stream epoch/checkpoint inside the same transaction; no legacy projector,
dual capture, reconciliation or cutover path participates.

Control and retired/out-of-profile records have no v2 source row or business
dirty key.  They receive only a payload-free routing receipt and advance the
same epoch-aware checkpoint.  Direct projection is never supported here.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from datetime import date, datetime, time, timezone
from decimal import Decimal
from time import perf_counter
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError

from .dts_ingest_store import (
    EXPECTED_DATABASE,
    EXPECTED_ROLE,
    EXPECTED_SCHEMA,
    DtsIngestDatabaseSettings,
    DtsIngestStoreError,
    DtsResumeCheckpoint,
    PostgresDtsEventSink,
    _require_session_transport,
)
from .dts_postgres_batch_copy import copy_rows, jsonb
from .dts_source_consumer import (
    DATA_OPERATIONS,
    AppointProjectionCandidate,
    DirtyKeySet,
    DtsChangeEvent,
    DtsRecordError,
    assert_domestic_event_protected,
)
from .dts_source_contract_v2 import V2_BUSINESS_SOURCE_SUFFIXES_BY_REGION
from .dts_v2_dirty_queue_store import DirtyKeyV2, DtsV2DirtyQueueStore
from .dts_v2_shadow_source_writer import (
    DtsV2ShadowSourceWriteResult,
    DtsV2ShadowSourceWriter,
    DtsV2ShadowSourceWriterError,
)


V2_PRIMARY_MODE = "V2_PRIMARY"
_V2_IDENTITY_VERSION = "V2_EPOCH"
_MISSING_SOURCE_CURRENT_ISSUE = "SOURCE_CHANGE_WITHOUT_CURRENT_IGNORED"
_DTS_DIRTY_GUARD_V96_PROSRC_SHA256 = (
    "43faaad828f20a8988c4cf1212490b48ad8aa28782112a5e97c355d307a6d2e2"
)


class DtsV2DualCaptureStoreError(DtsIngestStoreError):
    """The single source pipeline cannot prove an atomic epoch-aware write."""


class PostgresDtsV2DualCaptureSink(PostgresDtsEventSink):
    """Persist the complete single-pipeline ingest transition atomically."""

    def __init__(
        self,
        settings: DtsIngestDatabaseSettings,
        *,
        pipeline_mode: str = V2_PRIMARY_MODE,
        source_region: str,
        source_partition_epoch_id: str,
        consumer_group: str,
        start_timestamp_seconds: int = 0,
        control_group: str | None = None,
        source_profile_manifest_sha256: str | None = None,
        engine: Engine | None = None,
        v2_writer: Any | None = None,
        dirty_queue_store: Any | None = None,
    ) -> None:
        del control_group
        if source_region not in {"dom", "ovs"}:
            raise DtsV2DualCaptureStoreError(
                "DTS_V2_DUAL_CAPTURE_SOURCE_REGION_REQUIRED"
            )
        if pipeline_mode != V2_PRIMARY_MODE:
            raise DtsV2DualCaptureStoreError(
                "DTS_SINGLE_PIPELINE_MODE_INVALID"
            )
        if (
            isinstance(start_timestamp_seconds, bool)
            or not isinstance(start_timestamp_seconds, int)
            or start_timestamp_seconds < 0
        ):
            raise DtsV2DualCaptureStoreError(
                "DTS_SINGLE_PIPELINE_START_TIMESTAMP_INVALID"
            )
        super().__init__(
            settings,
            source_region=source_region,
            engine=engine,
        )
        self.source_partition_epoch_id = _required_text(
            source_partition_epoch_id,
            "DTS_V2_DUAL_CAPTURE_EPOCH_ID_REQUIRED",
        )
        self.pipeline_mode = pipeline_mode
        self.consumer_group = _required_text(
            consumer_group,
            "DTS_V2_DUAL_CAPTURE_CONSUMER_GROUP_REQUIRED",
        )
        self.start_timestamp_seconds = start_timestamp_seconds
        self.source_profile_manifest_sha256 = _required_sha256(
            source_profile_manifest_sha256,
            "DTS_V2_SOURCE_PROFILE_MANIFEST_SHA256_REQUIRED",
        )
        writer = (
            DtsV2ShadowSourceWriter(enabled=True)
            if v2_writer is None
            else v2_writer
        )
        if not callable(getattr(writer, "apply_cdc", None)):
            raise DtsV2DualCaptureStoreError(
                "DTS_V2_DUAL_CAPTURE_WRITER_INVALID"
            )
        if getattr(writer, "enabled", True) is not True:
            raise DtsV2DualCaptureStoreError(
                "DTS_V2_DUAL_CAPTURE_WRITER_DISABLED"
            )
        self._v2_writer = writer
        queue_store = (
            DtsV2DirtyQueueStore()
            if dirty_queue_store is None
            else dirty_queue_store
        )
        if not callable(getattr(queue_store, "enqueue_source_revision", None)):
            raise DtsV2DualCaptureStoreError(
                "DTS_V2_DUAL_CAPTURE_DIRTY_STORE_INVALID"
            )
        self._dirty_queue_store = queue_store
        self._last_batch_metrics: dict[str, int] = {}

    def consume_last_batch_metrics(self) -> dict[str, int]:
        metrics = self._last_batch_metrics
        self._last_batch_metrics = {}
        return metrics

    def enable_direct_projection(self, projector: Any) -> None:
        del projector
        raise DtsV2DualCaptureStoreError(
            "DTS_V2_DUAL_CAPTURE_DIRECT_FORBIDDEN"
        )

    def validate_startup(
        self,
        *,
        source_region: str,
        topic: str,
        partition: int,
    ) -> DtsResumeCheckpoint | None:
        """Validate current state; an empty reset intentionally returns None."""

        self._require_source_region(source_region)
        self._require_queued_only()
        self._validate_runtime()
        with self.engine.begin() as connection:
            state = self._validate_dual_capture_state(
                connection,
                source_region=source_region,
                topic=topic,
                partition=partition,
                lock_checkpoint=False,
            )
        if state is None:
            return None
        return DtsResumeCheckpoint(
            next_offset=state["next_offset"],
            source_timestamp=state["source_timestamp"],
        )

    def resume_checkpoint(
        self,
        *,
        source_region: str,
        topic: str,
        partition: int,
    ) -> DtsResumeCheckpoint | None:
        return self.validate_startup(
            source_region=source_region,
            topic=topic,
            partition=partition,
        )

    def apply(
        self,
        event: DtsChangeEvent,
        dirty_keys: DirtyKeySet,
        appoint_candidate: AppointProjectionCandidate | None,
    ) -> bool:
        # Keep the one-event API on exactly the same persistence path as the
        # normal consumer batch.  This prevents diagnostic/recovery callers
        # from silently re-enabling the retired per-event dirty/ledger path.
        return self.apply_batch(
            ((event, dirty_keys, appoint_candidate),)
        )[0]

    def apply_batch(
        self,
        items: Sequence[
            tuple[
                DtsChangeEvent,
                DirtyKeySet,
                AppointProjectionCandidate | None,
            ]
        ],
    ) -> tuple[bool, ...]:
        if not items:
            return ()
        self._last_batch_metrics = {}
        prepared = tuple((event, dirty_keys) for event, dirty_keys, _ in items)
        first_event = prepared[0][0]
        stream_identity = (
            first_event.source_region,
            first_event.topic,
            first_event.partition,
        )
        previous_offset: int | None = None
        for event, _dirty_keys in prepared:
            self._validate_event(event)
            if (
                event.source_region,
                event.topic,
                event.partition,
            ) != stream_identity:
                raise DtsV2DualCaptureStoreError(
                    "DTS_V2_DUAL_CAPTURE_BATCH_STREAM_MISMATCH"
                )
            if previous_offset is not None and event.offset <= previous_offset:
                raise DtsV2DualCaptureStoreError(
                    "DTS_V2_DUAL_CAPTURE_BATCH_OFFSET_ORDER_INVALID"
                )
            previous_offset = event.offset

        self._require_queued_only()
        self._validate_runtime()
        with self.engine.begin() as connection:
            state_lock_started = perf_counter()
            checkpoint = self._validate_dual_capture_state(
                connection,
                source_region=first_event.source_region,
                topic=first_event.topic,
                partition=first_event.partition,
                lock_checkpoint=True,
            )
            if checkpoint is None:
                # A normal hot batch needs only the stream/epoch/checkpoint
                # locks.  The full catalog/ACL/state proof is repeated only
                # when the hot read cannot find a complete stream, which is
                # the expected first-batch path after a destructive reset.
                startup_state = self._validate_dual_capture_state(
                    connection,
                    source_region=first_event.source_region,
                    topic=first_event.topic,
                    partition=first_event.partition,
                    lock_checkpoint=False,
                )
                if startup_state is None:
                    self._initialize_stream_if_missing(connection, first_event)
                checkpoint = self._validate_dual_capture_state(
                    connection,
                    source_region=first_event.source_region,
                    topic=first_event.topic,
                    partition=first_event.partition,
                    lock_checkpoint=True,
                )
            if checkpoint is None:
                raise DtsV2DualCaptureStoreError(
                    "DTS_SINGLE_PIPELINE_STREAM_INIT_MISSING"
                )
            checkpoint_row_version = checkpoint["checkpoint_row_version"]
            next_offset = checkpoint["next_offset"]
            state_lock_elapsed_ms = _elapsed_ms(
                state_lock_started,
                perf_counter(),
            )
            prepare_batch = getattr(self._v2_writer, "prepare_batch", None)
            if callable(prepare_batch):
                return self._apply_optimized_batch_transaction(
                    connection,
                    prepared,
                    checkpoint_row_version=checkpoint_row_version,
                    next_offset=next_offset,
                    prepare_batch=prepare_batch,
                    state_lock_elapsed_ms=state_lock_elapsed_ms,
                )
            duplicates: list[bool] = []
            # Do not bulk-fold v2 current.  Applying each event in source order
            # inside this one transaction preserves A -> B -> A transitions for
            # repeated changes to the same source row.
            for event, dirty_keys in prepared:
                (
                    duplicate,
                    checkpoint_row_version,
                    next_offset,
                ) = self._apply_dual_event(
                    connection,
                    event,
                    dirty_keys,
                    expected_checkpoint_row_version=checkpoint_row_version,
                    expected_next_offset=next_offset,
                )
                duplicates.append(duplicate)
            return tuple(duplicates)

    def _apply_optimized_batch_transaction(
        self,
        connection: Connection,
        prepared: Sequence[tuple[DtsChangeEvent, DirtyKeySet]],
        *,
        checkpoint_row_version: int,
        next_offset: int,
        prepare_batch: Any,
        state_lock_elapsed_ms: int,
    ) -> tuple[bool, ...]:
        """Persist a continuous batch with one ledger and checkpoint write.

        Exact replay records remain on the established validation path.  The
        new suffix is already serialized by the stream checkpoint lock, so it
        can share source-table locks/current prefetch and defer its immutable
        ledger plus checkpoint transition until every ordered source mutation
        has succeeded in this transaction.
        """

        replay_items: list[tuple[DtsChangeEvent, DirtyKeySet]] = []
        new_items: list[tuple[DtsChangeEvent, DirtyKeySet]] = []
        expected_new_offset = next_offset
        for item in prepared:
            event = item[0]
            if event.offset < next_offset:
                if new_items:
                    raise DtsV2DualCaptureStoreError(
                        "DTS_DATABASE_BATCH_OFFSET_ORDER_INVALID"
                    )
                replay_items.append(item)
                continue
            if event.offset != expected_new_offset:
                raise DtsV2DualCaptureStoreError(
                    "DTS_DATABASE_OFFSET_NOT_CONTIGUOUS"
                )
            new_items.append(item)
            expected_new_offset = event.offset + 1

        duplicate_flags: list[bool] = []
        for event, dirty_keys in replay_items:
            duplicate, checkpoint_row_version, replay_next_offset = (
                self._apply_dual_event(
                    connection,
                    event,
                    dirty_keys,
                    expected_checkpoint_row_version=checkpoint_row_version,
                    expected_next_offset=next_offset,
                )
            )
            if not duplicate or replay_next_offset != next_offset:
                raise DtsV2DualCaptureStoreError(
                    "DTS_V2_DUAL_CAPTURE_REPLAY_INVALID"
                )
            duplicate_flags.append(True)

        if not new_items:
            return tuple(duplicate_flags)

        transaction_body_started = perf_counter()
        phase_started = perf_counter()
        batch_context = prepare_batch(
            connection,
            tuple(event for event, _dirty_keys in new_items),
            self.source_partition_epoch_id,
        )
        prepare_finished = perf_counter()
        deferred_ledger: list[dict[str, Any]] = []
        deferred_dirty: list[dict[str, Any]] = []
        initial_checkpoint_version = checkpoint_row_version
        expected_next_offset = next_offset
        for event, dirty_keys in new_items:
            duplicate, checkpoint_row_version, expected_next_offset = (
                self._apply_dual_event(
                    connection,
                    event,
                    dirty_keys,
                    expected_checkpoint_row_version=checkpoint_row_version,
                    expected_next_offset=expected_next_offset,
                    batch_context=batch_context,
                    deferred_ledger=deferred_ledger,
                    deferred_dirty=deferred_dirty,
                )
            )
            if duplicate:
                raise DtsV2DualCaptureStoreError(
                    "DTS_V2_DUAL_CAPTURE_LEDGER_VERSION_INCONSISTENT"
                )
            duplicate_flags.append(False)
        route_finished = perf_counter()

        flush_batch = getattr(self._v2_writer, "flush_batch", None)
        if callable(flush_batch) and batch_context is not None:
            flush_batch(connection, batch_context)
        elif deferred_dirty:
            raise DtsV2DualCaptureStoreError(
                "DTS_V2_DUAL_CAPTURE_DEFERRED_WRITER_INVALID"
            )
        flush_finished = perf_counter()
        writer_metrics: dict[str, int] = {}
        consume_flush_metrics = getattr(
            self._v2_writer,
            "consume_last_flush_metrics",
            None,
        )
        if callable(consume_flush_metrics):
            writer_metrics = dict(consume_flush_metrics())
        raw_dirty_count, coalesced_dirty_count = (
            self._enqueue_deferred_dirty_batch(connection, deferred_dirty)
        )
        dirty_finished = perf_counter()
        self._write_new_ledger_batch(connection, deferred_ledger)
        ledger_finished = perf_counter()
        last_event = new_items[-1][0]
        self._advance_batch_checkpoint(
            connection,
            event=last_event,
            expected_checkpoint_row_version=initial_checkpoint_version,
            next_checkpoint_row_version=checkpoint_row_version,
            expected_next_offset=next_offset,
            advanced_next_offset=expected_next_offset,
        )
        checkpoint_finished = perf_counter()
        self._last_batch_metrics = {
            "db_state_lock_elapsed_ms": state_lock_elapsed_ms,
            "db_batch_prepare_elapsed_ms": _elapsed_ms(
                phase_started,
                prepare_finished,
            ),
            "db_event_route_elapsed_ms": _elapsed_ms(
                prepare_finished,
                route_finished,
            ),
            "db_source_flush_elapsed_ms": _elapsed_ms(
                route_finished,
                flush_finished,
            ),
            "db_dirty_elapsed_ms": _elapsed_ms(
                flush_finished,
                dirty_finished,
            ),
            "db_ledger_elapsed_ms": _elapsed_ms(
                dirty_finished,
                ledger_finished,
            ),
            "db_checkpoint_elapsed_ms": _elapsed_ms(
                ledger_finished,
                checkpoint_finished,
            ),
            "db_transaction_body_elapsed_ms": _elapsed_ms(
                transaction_body_started,
                checkpoint_finished,
            ),
            "db_raw_dirty_command_count": raw_dirty_count,
            "db_coalesced_dirty_command_count": coalesced_dirty_count,
            **writer_metrics,
        }
        return tuple(duplicate_flags)

    def _initialize_stream_if_missing(
        self,
        connection: Connection,
        event: DtsChangeEvent,
    ) -> None:
        if event.source_timestamp < self.start_timestamp_seconds:
            raise DtsV2DualCaptureStoreError(
                "DTS_EVENT_BEFORE_CONFIGURED_START"
            )
        initialized = connection.execute(
            text(
                """
                SELECT public.initialize_dts_event_stream_v1(
                  :source_region,:topic,:partition,:epoch_id,
                  :consumer_group,:initial_offset,:source_timestamp,
                  :source_position,:profile_manifest_sha256
                )
                """
            ),
            {
                "source_region": event.source_region,
                "topic": event.topic,
                "partition": event.partition,
                "epoch_id": self.source_partition_epoch_id,
                "consumer_group": self.consumer_group,
                "initial_offset": event.offset,
                "source_timestamp": event.source_timestamp,
                "source_position": event.source_position,
                "profile_manifest_sha256": (
                    self.source_profile_manifest_sha256
                ),
            },
        ).scalar_one()
        if initialized not in {"INITIALIZED", "EXISTS"}:
            raise DtsV2DualCaptureStoreError(
                "DTS_SINGLE_PIPELINE_STREAM_INIT_INVALID"
            )

    def _validate_event(self, event: DtsChangeEvent) -> None:
        self._require_source_region(event.source_region)
        try:
            assert_domestic_event_protected(event)
        except DtsRecordError as exc:
            raise DtsV2DualCaptureStoreError(str(exc)) from exc

    def _require_queued_only(self) -> None:
        if getattr(self, "_direct_projector", None) is not None:
            raise DtsV2DualCaptureStoreError(
                "DTS_V2_DUAL_CAPTURE_DIRECT_FORBIDDEN"
            )

    def _validate_runtime(self) -> None:
        """Validate the narrower rev82 dual-capture capability surface."""

        if self._validated:
            return
        if self.engine.dialect.name != "postgresql":
            raise DtsV2DualCaptureStoreError(
                "DTS_TARGET_MUST_BE_POSTGRESQL"
            )
        required_table_privileges = (
            ("public.dts_pipeline_control", "SELECT"),
            ("public.dts_source_partition_epochs", "SELECT"),
            ("public.dts_source_row_versions", "SELECT"),
            ("public.dts_source_row_versions", "INSERT"),
            ("public.dts_source_rows", "SELECT"),
            ("public.dts_source_rows", "INSERT"),
            ("public.dts_source_rows", "UPDATE"),
            ("public.dts_ingest_events", "SELECT"),
            ("public.dts_ingest_events", "INSERT"),
            ("public.dts_ingest_checkpoints", "SELECT"),
            ("public.dts_ingest_checkpoints", "UPDATE"),
            ("public.dts_dirty_keys", "SELECT"),
            ("public.lesson_source_wide", "SELECT"),
        )
        forbidden_table_privileges = (
            ("public.dts_source_partition_epochs", "INSERT"),
            ("public.dts_source_partition_epochs", "UPDATE"),
            ("public.dts_source_partition_epochs", "DELETE"),
            ("public.dts_source_row_versions", "UPDATE"),
            ("public.dts_source_row_versions", "DELETE"),
            ("public.dts_source_rows", "DELETE"),
            ("public.dts_ingest_events", "UPDATE"),
            ("public.dts_ingest_events", "DELETE"),
            ("public.dts_ingest_checkpoints", "INSERT"),
            ("public.dts_ingest_checkpoints", "DELETE"),
            *(
                (f"public.{table_name}", privilege)
                for table_name in (
                    "dts_dirty_keys",
                    "dts_dirty_key_inputs",
                    "dts_dirty_key_dependencies",
                    "dts_dirty_key_state_audits",
                )
                for privilege in (
                    "INSERT",
                    "UPDATE",
                    "DELETE",
                    "TRUNCATE",
                    "TRIGGER",
                )
            ),
        )
        try:
            with self.engine.connect() as connection:
                identity = connection.execute(
                    text(
                        """
                        SELECT current_database(),current_user,current_schema(),
                               current_setting('transaction_read_only'),
                               (SELECT ssl FROM pg_catalog.pg_stat_ssl
                                WHERE pid=pg_catalog.pg_backend_pid()),
                               current_setting('ssl')
                        """
                    )
                ).one()
                if tuple(identity[:3]) != (
                    EXPECTED_DATABASE,
                    EXPECTED_ROLE,
                    EXPECTED_SCHEMA,
                ):
                    raise DtsV2DualCaptureStoreError(
                        "DTS_TARGET_IDENTITY_MISMATCH"
                    )
                if str(identity[3]).lower() != "off":
                    raise DtsV2DualCaptureStoreError(
                        "DTS_TARGET_IS_READ_ONLY"
                    )
                _require_session_transport(
                    sslmode=self.settings.sslmode,
                    session_ssl=identity[4],
                    server_ssl=str(identity[5]),
                )
                missing = connection.execute(
                    text(
                        """
                        SELECT relation_name,privilege_name
                        FROM unnest(
                            CAST(:relations AS text[]),
                            CAST(:privileges AS text[])
                        ) AS required(relation_name,privilege_name)
                        WHERE NOT has_table_privilege(
                            current_user,relation_name,privilege_name
                        )
                        """
                    ),
                    {
                        "relations": [
                            relation for relation, _ in required_table_privileges
                        ],
                        "privileges": [
                            privilege for _, privilege in required_table_privileges
                        ],
                    },
                ).first()
                if missing is not None:
                    raise DtsV2DualCaptureStoreError(
                        "DTS_V2_DUAL_CAPTURE_PRIVILEGE_MISSING"
                    )
                excessive = connection.execute(
                    text(
                        """
                        SELECT relation_name,privilege_name
                        FROM unnest(
                            CAST(:relations AS text[]),
                            CAST(:privileges AS text[])
                        ) AS forbidden(relation_name,privilege_name)
                        WHERE has_table_privilege(
                            current_user,relation_name,privilege_name
                        )
                        """
                    ),
                    {
                        "relations": [
                            relation for relation, _ in forbidden_table_privileges
                        ],
                        "privileges": [
                            privilege for _, privilege in forbidden_table_privileges
                        ],
                    },
                ).first()
                if excessive is not None:
                    raise DtsV2DualCaptureStoreError(
                        "DTS_V2_DUAL_CAPTURE_PRIVILEGE_TOO_BROAD"
                    )
                function_privileges = connection.execute(
                    text(
                        """
                        SELECT
                          has_function_privilege(
                            current_user,
                            'public.initialize_dts_event_stream_v1('
                            'text,text,integer,text,text,bigint,bigint,text,text)',
                            'EXECUTE'
                          ),
                          has_function_privilege(
                            current_user,
                            'public.enqueue_dirty_from_source_revision_v2('
                            'text,text,text,bigint,text,text,text)',
                            'EXECUTE'
                          ),
                          has_function_privilege(
                            current_user,
                            'public.enqueue_dirty_from_source_revisions_batch_v3('
                            'jsonb)',
                            'EXECUTE'
                          ),
                          has_function_privilege(
                            current_user,
                            'public.dts_active_source_scope_tables_v1('
                            'text,text[])',
                            'EXECUTE'
                          ),
                          has_function_privilege(
                            current_user,
                            'public.lock_dts_source_partition_epoch_for_ingest_v2('
                            'text,text,text,integer)',
                            'EXECUTE'
                          ),
                          has_function_privilege(
                            current_user,
                            'public.lock_dts_source_table_for_ingest_v3('
                            'text,text)',
                            'EXECUTE'
                          ),
                          has_function_privilege(
                            current_user,
                            'public.scope_membership_apply_cdc_v3('
                            'text,text,text,bigint,jsonb,jsonb,boolean)',
                            'EXECUTE'
                          ),
                          has_function_privilege(
                            current_user,
                            'public._upsert_dts_dirty_key_input_v2('
                            'text,text,text,text,text,jsonb,bigint,text)',
                            'EXECUTE'
                          ),
                          has_function_privilege(
                            current_user,
                            'public.dts_v2_source_field_types_valid(jsonb)',
                            'EXECUTE'
                          ),
                          has_function_privilege(
                            current_user,
                            'public.dts_v2_source_row_transition_valid('
                            'text,jsonb,bigint,text,text,jsonb,text,numeric,text,'
                            'timestamptz,text,text,text,numeric,text,text,jsonb)',
                            'EXECUTE'
                          ),
                          has_function_privilege(
                            current_user,
                            'public.dts_v2_assert_source_current_pair('
                            'text,text,text)',
                            'EXECUTE'
                          ),
                          has_function_privilege(
                            current_user,
                            'public.dts_v2_source_current_pair_guard()',
                            'EXECUTE'
                          ),
                          has_function_privilege(
                            current_user,
                            'public.check_dts_dirty_key_integrity_v2()',
                            'EXECUTE'
                          )
                        """
                    )
                ).one()
                if tuple(function_privileges) != (
                    True,
                    True,
                    True,
                    True,
                    True,
                    True,
                    True,
                    False,
                    True,
                    True,
                    True,
                    False,
                    False,
                ):
                    raise DtsV2DualCaptureStoreError(
                        "DTS_V2_DUAL_CAPTURE_FUNCTION_PRIVILEGE_INVALID"
                    )

                protected_functions = connection.execute(
                    text(
                        """
                        SELECT function_name,functions.prosecdef,
                               'search_path=pg_catalog, public' = ANY(
                                 COALESCE(functions.proconfig,ARRAY[]::text[])
                               )
                        FROM unnest(ARRAY[
                          'initialize_dts_event_stream_v1(text,text,integer,text,text,bigint,bigint,text,text)',
                          'enqueue_dirty_from_source_revision_v2(text,text,text,bigint,text,text,text)',
                          'enqueue_dirty_from_source_revisions_batch_v3(jsonb)',
                          'dts_active_source_scope_tables_v1(text,text[])',
                          'lock_dts_source_partition_epoch_for_ingest_v2(text,text,text,integer)',
                          'lock_dts_source_table_for_ingest_v3(text,text)',
                          'scope_membership_apply_cdc_v3(text,text,text,bigint,jsonb,jsonb,boolean)',
                          'check_dts_dirty_key_integrity_v2()'
                        ]::text[]) WITH ORDINALITY AS expected(
                          function_name,function_order
                        )
                        LEFT JOIN pg_catalog.pg_proc functions
                          ON functions.oid = pg_catalog.to_regprocedure(
                            'public.' || expected.function_name
                          )
                        ORDER BY expected.function_order
                        """
                    )
                ).all()
                if tuple(
                    (str(row[0]), bool(row[1]), bool(row[2]))
                    for row in protected_functions
                ) != (
                    (
                        "initialize_dts_event_stream_v1("
                        "text,text,integer,text,text,bigint,bigint,text,text)",
                        True,
                        True,
                    ),
                    (
                        "enqueue_dirty_from_source_revision_v2("
                        "text,text,text,bigint,text,text,text)",
                        True,
                        True,
                    ),
                    (
                        "enqueue_dirty_from_source_revisions_batch_v3(jsonb)",
                        True,
                        True,
                    ),
                    (
                        "dts_active_source_scope_tables_v1(text,text[])",
                        True,
                        True,
                    ),
                    (
                        "lock_dts_source_partition_epoch_for_ingest_v2("
                        "text,text,text,integer)",
                        True,
                        True,
                    ),
                    (
                        "lock_dts_source_table_for_ingest_v3(text,text)",
                        True,
                        True,
                    ),
                    (
                        "scope_membership_apply_cdc_v3("
                        "text,text,text,bigint,jsonb,jsonb,boolean)",
                        True,
                        True,
                    ),
                    (
                        "check_dts_dirty_key_integrity_v2()",
                        True,
                        True,
                    ),
                ):
                    raise DtsV2DualCaptureStoreError(
                        "DTS_V2_DUAL_CAPTURE_FUNCTION_DEFINITION_INVALID"
                    )

                dirty_guard = connection.execute(
                    text(
                        """
                        SELECT triggers.tgenabled,triggers.tgtype,
                               function_namespaces.nspname,
                               functions.proname,languages.lanname,
                               functions.provolatile,functions.proisstrict,
                               functions.prosecdef,
                               COALESCE(functions.proconfig,ARRAY[]::text[]),
                               pg_catalog.encode(
                                 pg_catalog.sha256(pg_catalog.convert_to(
                                   functions.prosrc,'UTF8'
                                 )),'hex'
                               ),
                               has_function_privilege(
                                 current_user,
                                 'public.guard_dts_dirty_key_state_write_v96()',
                                 'EXECUTE'
                               )
                        FROM pg_catalog.pg_trigger triggers
                        JOIN pg_catalog.pg_class relations
                          ON relations.oid=triggers.tgrelid
                        JOIN pg_catalog.pg_namespace relation_namespaces
                          ON relation_namespaces.oid=relations.relnamespace
                        JOIN pg_catalog.pg_proc functions
                          ON functions.oid=triggers.tgfoid
                        JOIN pg_catalog.pg_namespace function_namespaces
                          ON function_namespaces.oid=functions.pronamespace
                        JOIN pg_catalog.pg_language languages
                          ON languages.oid=functions.prolang
                        WHERE relation_namespaces.nspname='public'
                          AND relations.relname='dts_dirty_keys'
                          AND triggers.tgname='guard_dts_runtime_state_write'
                          AND NOT triggers.tgisinternal
                        """
                    )
                ).all()
                if len(dirty_guard) != 1 or (
                    str(dirty_guard[0][0]),
                    int(dirty_guard[0][1]),
                    str(dirty_guard[0][2]),
                    str(dirty_guard[0][3]),
                    str(dirty_guard[0][4]),
                    str(dirty_guard[0][5]),
                    bool(dirty_guard[0][6]),
                    bool(dirty_guard[0][7]),
                    tuple(str(value) for value in dirty_guard[0][8]),
                    str(dirty_guard[0][9]),
                    bool(dirty_guard[0][10]),
                ) != (
                    "O",
                    31,
                    "public",
                    "guard_dts_dirty_key_state_write_v96",
                    "plpgsql",
                    "v",
                    False,
                    False,
                    ("search_path=pg_catalog, public",),
                    _DTS_DIRTY_GUARD_V96_PROSRC_SHA256,
                    False,
                ):
                    raise DtsV2DualCaptureStoreError(
                        "DTS_V2_DUAL_CAPTURE_DIRTY_GUARD_INVALID"
                    )
        except DtsV2DualCaptureStoreError:
            raise
        except SQLAlchemyError as exc:
            raise DtsV2DualCaptureStoreError(
                "DTS_V2_DUAL_CAPTURE_RUNTIME_UNREADABLE"
            ) from exc
        self._validated = True

    def _apply_dual_event(
        self,
        connection: Connection,
        event: DtsChangeEvent,
        route_hints: DirtyKeySet,
        *,
        expected_checkpoint_row_version: int,
        expected_next_offset: int,
        batch_context: Any | None = None,
        deferred_ledger: list[dict[str, Any]] | None = None,
        deferred_dirty: list[dict[str, Any]] | None = None,
    ) -> tuple[bool, int, int]:
        if event.offset > expected_next_offset:
            raise DtsV2DualCaptureStoreError(
                "DTS_DATABASE_OFFSET_NOT_CONTIGUOUS"
            )
        replay_expected = event.offset < expected_next_offset
        v2_result: DtsV2ShadowSourceWriteResult | None = None
        ignored_missing_current = (
            replay_expected
            and self._was_missing_current_ignored(
                connection,
                event=event,
            )
        )
        if _is_v2_business_event(event) and not ignored_missing_current:
            try:
                if batch_context is None:
                    v2_result = self._v2_writer.apply_cdc(
                        connection,
                        event,
                        self.source_partition_epoch_id,
                    )
                else:
                    v2_result = self._v2_writer.apply_cdc(
                        connection,
                        event,
                        self.source_partition_epoch_id,
                        batch_context=batch_context,
                    )
            except DtsV2ShadowSourceWriterError as exc:
                # These attributes are consumed only by the process-level
                # safe diagnostic formatter.  They identify the failed CDC
                # envelope without exposing source row values or identities.
                exc.safe_source_region = event.source_region
                exc.safe_source_table = event.table_name
                exc.safe_source_operation = event.operation
                exc.safe_source_offset = event.offset
                raise
            if (
                v2_result.source_region != event.source_region
                or v2_result.source_table != event.table_name
                or not isinstance(v2_result.source_key, str)
                or not v2_result.source_key
                or v2_result.source_key_type not in {"NUMERIC", "TEXT"}
                or not isinstance(v2_result.protected_payload_hash, str)
                or len(v2_result.protected_payload_hash) != 64
                or any(
                    character not in "0123456789abcdef"
                    for character in v2_result.protected_payload_hash
                )
            ):
                raise DtsV2DualCaptureStoreError(
                    "DTS_V2_DUAL_CAPTURE_SOURCE_RESULT_INVALID"
                )
            ignored_missing_current = (
                v2_result.status == "IGNORED_MISSING_CURRENT"
            )
            if replay_expected != (
                v2_result.status == "REPLAYED"
                or (
                    replay_expected
                    and ignored_missing_current
                )
            ):
                raise DtsV2DualCaptureStoreError(
                    "DTS_V2_DUAL_CAPTURE_LEDGER_VERSION_INCONSISTENT"
                )

        revision_dirty_keys = _result_dirty_keys(v2_result)
        if (
            v2_result is not None
            and v2_result.status
            not in {"REPLAYED", "IGNORED_MISSING_CURRENT"}
        ):
            revision = v2_result.source_row_revision
            if (
                isinstance(revision, bool)
                or not isinstance(revision, int)
                or revision < 1
            ):
                raise DtsV2DualCaptureStoreError(
                    "DTS_V2_DUAL_CAPTURE_SOURCE_REVISION_INVALID"
                )
            is_deferred_source_write = bool(
                batch_context is not None
                and event.offset
                in getattr(batch_context, "deferred_offsets", set())
            )
            for dirty_key in revision_dirty_keys:
                command = {
                    "source_region": v2_result.source_region,
                    "source_table": v2_result.source_table,
                    "source_key": v2_result.source_key,
                    "source_row_revision": revision,
                    "dirty_key": dirty_key,
                }
                if is_deferred_source_write:
                    if deferred_dirty is None:
                        raise DtsV2DualCaptureStoreError(
                            "DTS_V2_DUAL_CAPTURE_DEFERRED_DIRTY_INVALID"
                        )
                    deferred_dirty.append(command)
                else:
                    self._enqueue_source_revision(connection, command)

        route_status = (
            "IGNORED"
            if v2_result is None or ignored_missing_current
            else "PROCESSED"
        )
        issue_codes = _ledger_issue_codes(route_hints)
        if ignored_missing_current:
            issue_codes = tuple(
                dict.fromkeys((*issue_codes, _MISSING_SOURCE_CURRENT_ISSUE))
            )
        position = _source_position_v2(
            event,
            source_partition_epoch_id=self.source_partition_epoch_id,
        )
        payload_hash = _event_payload_hash(
            event,
            source_partition_epoch_id=self.source_partition_epoch_id,
            protected_source_hash=(
                None
                if v2_result is None or ignored_missing_current
                else v2_result.protected_payload_hash
            ),
        )
        ledger_parameters = self._ledger_parameters(
            event=event,
            source_position=position,
            event_payload_hash=payload_hash,
            route_status=route_status,
            dirty_key_count=len(revision_dirty_keys),
            issue_codes=issue_codes,
        )
        if deferred_ledger is None:
            duplicate = self._write_or_validate_ledger(
                connection,
                event=event,
                source_position=position,
                event_payload_hash=payload_hash,
                route_status=route_status,
                dirty_key_count=len(revision_dirty_keys),
                issue_codes=issue_codes,
            )
        else:
            if replay_expected:
                raise DtsV2DualCaptureStoreError(
                    "DTS_V2_DUAL_CAPTURE_REPLAY_INVALID"
                )
            deferred_ledger.append(ledger_parameters)
            duplicate = False
        if duplicate != replay_expected or (
            v2_result is not None
            and not ignored_missing_current
            and duplicate != (v2_result.status == "REPLAYED")
        ):
            raise DtsV2DualCaptureStoreError(
                "DTS_V2_DUAL_CAPTURE_LEDGER_VERSION_INCONSISTENT"
            )
        if deferred_ledger is None:
            checkpoint_row_version = self._advance_or_validate_checkpoint(
                connection,
                event=event,
                duplicate=duplicate,
                expected_checkpoint_row_version=expected_checkpoint_row_version,
                expected_next_offset=expected_next_offset,
            )
        else:
            checkpoint_row_version = expected_checkpoint_row_version + 1
        return (
            duplicate,
            checkpoint_row_version,
            expected_next_offset if duplicate else event.offset + 1,
        )

    def _enqueue_source_revision(
        self,
        connection: Connection,
        command: Mapping[str, Any],
    ) -> None:
        enqueue_result = self._dirty_queue_store.enqueue_source_revision(
            connection,
            source_region=command["source_region"],
            source_table=command["source_table"],
            source_key=command["source_key"],
            source_row_revision=command["source_row_revision"],
            dirty_key=command["dirty_key"],
        )
        if enqueue_result.get("status") != "ENQUEUED":
            raise DtsV2DualCaptureStoreError(
                "DTS_V2_DUAL_CAPTURE_DIRTY_ENQUEUE_INCONSISTENT"
            )

    def _enqueue_deferred_dirty_batch(
        self,
        connection: Connection,
        commands: Sequence[Mapping[str, Any]],
    ) -> tuple[int, int]:
        if not commands:
            return (0, 0)
        coalesced_commands = _coalesce_deferred_dirty_commands(commands)
        enqueue_batch = getattr(
            self._dirty_queue_store,
            "enqueue_source_revisions_batch",
            None,
        )
        if callable(enqueue_batch):
            results = enqueue_batch(connection, commands=coalesced_commands)
            if len(results) != len(coalesced_commands) or any(
                result.get("status") != "ENQUEUED" for result in results
            ):
                raise DtsV2DualCaptureStoreError(
                    "DTS_V2_DUAL_CAPTURE_DIRTY_ENQUEUE_INCONSISTENT"
                )
            return (len(commands), len(coalesced_commands))
        for command in coalesced_commands:
            self._enqueue_source_revision(connection, command)
        return (len(commands), len(coalesced_commands))

    def _was_missing_current_ignored(
        self,
        connection: Connection,
        *,
        event: DtsChangeEvent,
    ) -> bool:
        return _ledger_has_ignored_missing_current(
            connection,
            event=event,
        )

    def _validate_dual_capture_state(
        self,
        connection: Connection,
        *,
        source_region: str,
        topic: str,
        partition: int,
        lock_checkpoint: bool,
    ) -> Mapping[str, Any] | None:
        if lock_checkpoint:
            return self._lock_batch_state(
                connection,
                source_region=source_region,
                topic=topic,
                partition=partition,
            )
        try:
            missing_relations = connection.execute(
                text(
                    """
                    SELECT required.relation_name
                    FROM (VALUES
                        ('public.dts_pipeline_control'),
                        ('public.dts_source_partition_epochs'),
                        ('public.dts_source_row_versions'),
                        ('public.dts_ingest_events'),
                        ('public.dts_ingest_checkpoints'),
                        ('public.dts_source_rows'),
                        ('public.dts_dirty_keys'),
                        ('public.dts_dirty_key_inputs'),
                        ('public.dts_dirty_key_dependencies'),
                        ('public.dts_dirty_key_state_audits')
                    ) AS required(relation_name)
                    WHERE pg_catalog.to_regclass(required.relation_name) IS NULL
                    ORDER BY required.relation_name
                    """
                )
            ).scalars().all()
            if missing_relations:
                raise DtsV2DualCaptureStoreError(
                    "DTS_V2_DUAL_CAPTURE_SCHEMA_NOT_READY"
                )
            function_state = connection.execute(
                text(
                    """
                    SELECT
                      to_regprocedure(
                        'public.initialize_dts_event_stream_v1('
                        'text,text,integer,text,text,bigint,bigint,text,text)'
                      ) IS NOT NULL,
                      to_regprocedure(
                        'public.enqueue_dirty_from_source_revision_v2('
                        'text,text,text,bigint,text,text,text)'
                      ) IS NOT NULL,
                      to_regprocedure(
                        'public.enqueue_dirty_from_source_revisions_batch_v3('
                        'jsonb)'
                      ) IS NOT NULL,
                      to_regprocedure(
                        'public.dts_active_source_scope_tables_v1('
                        'text,text[])'
                      ) IS NOT NULL,
                      to_regprocedure(
                        'public.lock_dts_source_partition_epoch_for_ingest_v2('
                        'text,text,text,integer)'
                      ) IS NOT NULL,
                      to_regprocedure(
                        'public.lock_dts_source_table_for_ingest_v3('
                        'text,text)'
                      ) IS NOT NULL,
                      to_regprocedure(
                        'public.scope_membership_apply_cdc_v3('
                        'text,text,text,bigint,jsonb,jsonb,boolean)'
                      ) IS NOT NULL,
                      has_function_privilege(
                        'tit_dts_ingest_runtime',
                        'public.initialize_dts_event_stream_v1('
                        'text,text,integer,text,text,bigint,bigint,text,text)',
                        'EXECUTE'
                      ),
                      has_function_privilege(
                        'tit_dts_ingest_runtime',
                        'public.enqueue_dirty_from_source_revision_v2('
                        'text,text,text,bigint,text,text,text)',
                        'EXECUTE'
                      ),
                      has_function_privilege(
                        'tit_dts_ingest_runtime',
                        'public.enqueue_dirty_from_source_revisions_batch_v3('
                        'jsonb)',
                        'EXECUTE'
                      ),
                      has_function_privilege(
                        'tit_dts_ingest_runtime',
                        'public.dts_active_source_scope_tables_v1('
                        'text,text[])',
                        'EXECUTE'
                      ),
                      has_function_privilege(
                        'tit_dts_ingest_runtime',
                        'public.lock_dts_source_partition_epoch_for_ingest_v2('
                        'text,text,text,integer)',
                        'EXECUTE'
                      ),
                      has_function_privilege(
                        'tit_dts_ingest_runtime',
                        'public.lock_dts_source_table_for_ingest_v3('
                        'text,text)',
                        'EXECUTE'
                      ),
                      has_function_privilege(
                        'tit_dts_ingest_runtime',
                        'public.scope_membership_apply_cdc_v3('
                        'text,text,text,bigint,jsonb,jsonb,boolean)',
                        'EXECUTE'
                      ),
                      has_function_privilege(
                        'tit_dts_ingest_runtime',
                        'public._upsert_dts_dirty_key_input_v2('
                        'text,text,text,text,text,jsonb,bigint,text)',
                        'EXECUTE'
                      ),
                      EXISTS (
                        SELECT 1
                        FROM unnest(ARRAY[
                          'public.dts_dirty_keys',
                          'public.dts_dirty_key_inputs',
                          'public.dts_dirty_key_dependencies',
                          'public.dts_dirty_key_state_audits'
                        ]::text[]) AS relation(name)
                        CROSS JOIN unnest(ARRAY[
                          'INSERT','UPDATE','DELETE','TRUNCATE','TRIGGER'
                        ]::text[]) AS privilege(name)
                        WHERE has_table_privilege(
                          'tit_dts_ingest_runtime',relation.name,privilege.name
                        )
                      )
                    """
                )
            ).one()
            if tuple(function_state) != (
                True,
                True,
                True,
                True,
                True,
                True,
                True,
                True,
                True,
                True,
                True,
                True,
                True,
                True,
                False,
                False,
            ):
                raise DtsV2DualCaptureStoreError(
                    "DTS_V2_DUAL_CAPTURE_DIRTY_API_INVALID"
                )

            control_rows = connection.execute(
                text(
                    """
                    SELECT mode,row_version
                    FROM public.dts_pipeline_control
                    WHERE control_id = 'PRIMARY'
                    """
                )
            ).mappings().all()
            if len(control_rows) != 1:
                raise DtsV2DualCaptureStoreError(
                    "DTS_V2_DUAL_CAPTURE_CONTROL_MISSING"
                )
            control = control_rows[0]
            if control.get("mode") != self.pipeline_mode:
                raise DtsV2DualCaptureStoreError(
                    "DTS_V2_DUAL_CAPTURE_MODE_MISMATCH"
                )
            _positive_version(
                control.get("row_version"),
                "DTS_V2_DUAL_CAPTURE_CONTROL_INVALID",
            )

            checkpoint_rows = connection.execute(
                text(
                    """
                    SELECT next_offset,source_timestamp,
                           source_partition_epoch_id,consumer_group,
                           checkpoint_row_version,is_current_epoch
                    FROM public.dts_ingest_checkpoints
                    WHERE source_region=:source_region
                      AND topic=:topic AND partition_id=:partition
                    """
                ),
                {
                    "source_region": source_region,
                    "topic": topic,
                    "partition": partition,
                },
            ).mappings().all()
            if not checkpoint_rows:
                stale_epoch = connection.execute(
                    text(
                        """
                        SELECT 1 FROM public.dts_source_partition_epochs
                        WHERE source_region=:source_region
                          AND topic=:topic AND partition_id=:partition
                        LIMIT 1
                        """
                    ),
                    {
                        "source_region": source_region,
                        "topic": topic,
                        "partition": partition,
                    },
                ).scalar_one_or_none()
                if stale_epoch is not None:
                    raise DtsV2DualCaptureStoreError(
                        "DTS_SINGLE_PIPELINE_PARTIAL_STREAM_STATE"
                    )
                return None
            if len(checkpoint_rows) != 1:
                raise DtsV2DualCaptureStoreError(
                    "DTS_V2_DUAL_CAPTURE_CHECKPOINT_CONFLICT"
                )

            epoch_rows = connection.execute(
                text(
                    """
                    SELECT source_region, source_partition_epoch_id, topic,
                           partition_id, epoch_kind, status
                    FROM public.dts_source_partition_epochs
                    WHERE source_region = :source_region
                      AND source_partition_epoch_id = :epoch_id
                    """
                ),
                {
                    "source_region": source_region,
                    "epoch_id": self.source_partition_epoch_id,
                },
            ).mappings().all()
            if len(epoch_rows) != 1:
                raise DtsV2DualCaptureStoreError(
                    "DTS_V2_DUAL_CAPTURE_ACTIVE_EPOCH_MISSING"
                )
            epoch = epoch_rows[0]
            if epoch.get("epoch_kind") != "BROKER" or epoch.get("status") != "ACTIVE":
                raise DtsV2DualCaptureStoreError(
                    "DTS_V2_DUAL_CAPTURE_ACTIVE_EPOCH_MISMATCH"
                )
            if (
                epoch.get("source_region"),
                epoch.get("topic"),
                epoch.get("partition_id"),
            ) != (source_region, topic, partition):
                raise DtsV2DualCaptureStoreError(
                    "DTS_V2_DUAL_CAPTURE_STREAM_MISMATCH"
                )

            checkpoint_rows = connection.execute(
                text(
                    """
                    SELECT next_offset, source_timestamp,
                           source_partition_epoch_id, consumer_group,
                           checkpoint_row_version, is_current_epoch
                    FROM public.dts_ingest_checkpoints
                    WHERE source_region = :source_region
                      AND topic = :topic
                      AND partition_id = :partition
                    FOR SHARE
                    """
                ),
                {
                    "source_region": source_region,
                    "topic": topic,
                    "partition": partition,
                },
            ).mappings().all()
        except DtsV2DualCaptureStoreError:
            raise
        except SQLAlchemyError as exc:
            raise DtsV2DualCaptureStoreError(
                "DTS_V2_DUAL_CAPTURE_STATE_UNREADABLE"
            ) from exc

        if len(checkpoint_rows) != 1:
            raise DtsV2DualCaptureStoreError(
                "DTS_V2_DUAL_CAPTURE_CHECKPOINT_MISSING"
            )
        checkpoint = checkpoint_rows[0]
        self._validate_checkpoint_state(checkpoint)
        return checkpoint

    def _lock_batch_state(
        self,
        connection: Connection,
        *,
        source_region: str,
        topic: str,
        partition: int,
    ) -> Mapping[str, Any] | None:
        """Lock the live single-pipeline stream in one PostgreSQL round trip.

        Startup already proves schema, ACLs, function definitions and the
        complete reset state.  Re-running those catalog reads for every batch
        costs several China-to-Singapore round trips and cannot strengthen the
        transaction.  The hot path keeps the mutable invariants: stream
        serialization, active epoch, current mode and checkpoint identity.
        """

        try:
            rows = connection.execute(
                text(
                    """
                    WITH active_epoch AS MATERIALIZED (
                        SELECT source_region,source_partition_epoch_id,
                               topic,partition_id
                        FROM public.dts_source_partition_epochs
                        WHERE source_region=:source_region
                          AND source_partition_epoch_id=:epoch_id
                          AND topic=:topic
                          AND partition_id=:partition
                          AND epoch_kind='BROKER'
                          AND status='ACTIVE'
                    ),
                    stream_lock AS MATERIALIZED (
                        SELECT pg_catalog.pg_advisory_xact_lock(
                            pg_catalog.hashtextextended(:stream_identity,0)
                        ) AS acquired
                    ),
                    epoch_lock AS MATERIALIZED (
                        SELECT
                          public.lock_dts_source_partition_epoch_for_ingest_v2(
                            epoch.source_region,
                            epoch.source_partition_epoch_id,
                            epoch.topic,
                            epoch.partition_id
                          ) AS locked
                        FROM active_epoch AS epoch
                        CROSS JOIN stream_lock
                    ),
                    checkpoint AS MATERIALIZED (
                        SELECT current_checkpoint.next_offset,
                               current_checkpoint.source_timestamp,
                               current_checkpoint.source_partition_epoch_id,
                               current_checkpoint.consumer_group,
                               current_checkpoint.checkpoint_row_version,
                               current_checkpoint.is_current_epoch
                        FROM public.dts_ingest_checkpoints
                          AS current_checkpoint
                        CROSS JOIN epoch_lock
                        WHERE current_checkpoint.source_region=:source_region
                          AND current_checkpoint.topic=:topic
                          AND current_checkpoint.partition_id=:partition
                        FOR UPDATE OF current_checkpoint
                    )
                    SELECT checkpoint.next_offset,
                           checkpoint.source_timestamp,
                           checkpoint.source_partition_epoch_id,
                           checkpoint.consumer_group,
                           checkpoint.checkpoint_row_version,
                           checkpoint.is_current_epoch,
                           control.mode,
                           control.row_version AS control_row_version,
                           epoch_lock.locked AS epoch_locked
                    FROM checkpoint
                    CROSS JOIN epoch_lock
                    LEFT JOIN public.dts_pipeline_control AS control
                      ON control.control_id='PRIMARY'
                    """
                ),
                {
                    "stream_identity": self._stream_identity(
                        _stream_event(source_region, topic, partition)
                    ),
                    "source_region": source_region,
                    "epoch_id": self.source_partition_epoch_id,
                    "topic": topic,
                    "partition": partition,
                },
            ).mappings().all()
        except SQLAlchemyError as exc:
            raise DtsV2DualCaptureStoreError(
                "DTS_V2_DUAL_CAPTURE_STATE_UNREADABLE"
            ) from exc

        if not rows:
            return None
        if len(rows) != 1:
            raise DtsV2DualCaptureStoreError(
                "DTS_V2_DUAL_CAPTURE_CHECKPOINT_CONFLICT"
            )
        state = rows[0]
        if state.get("mode") is None:
            raise DtsV2DualCaptureStoreError(
                "DTS_V2_DUAL_CAPTURE_CONTROL_MISSING"
            )
        if state.get("mode") != self.pipeline_mode:
            raise DtsV2DualCaptureStoreError(
                "DTS_V2_DUAL_CAPTURE_MODE_MISMATCH"
            )
        _positive_version(
            state.get("control_row_version"),
            "DTS_V2_DUAL_CAPTURE_CONTROL_INVALID",
        )
        if state.get("epoch_locked") is not True:
            raise DtsV2DualCaptureStoreError(
                "DTS_V2_DUAL_CAPTURE_ACTIVE_EPOCH_MISMATCH"
            )
        self._validate_checkpoint_state(state)
        return state

    def _validate_checkpoint_state(
        self,
        checkpoint: Mapping[str, Any],
    ) -> None:
        if (
            checkpoint.get("source_partition_epoch_id")
            != self.source_partition_epoch_id
            or checkpoint.get("consumer_group") != self.consumer_group
            or checkpoint.get("is_current_epoch") is not True
        ):
            raise DtsV2DualCaptureStoreError(
                "DTS_V2_DUAL_CAPTURE_CHECKPOINT_IDENTITY_MISMATCH"
            )
        _positive_version(
            checkpoint.get("checkpoint_row_version"),
            "DTS_V2_DUAL_CAPTURE_CHECKPOINT_INVALID",
        )
        _non_negative_int(
            checkpoint.get("next_offset"),
            "DTS_V2_DUAL_CAPTURE_CHECKPOINT_INVALID",
        )
        _non_negative_int(
            checkpoint.get("source_timestamp"),
            "DTS_V2_DUAL_CAPTURE_CHECKPOINT_INVALID",
        )

    def _write_or_validate_ledger(
        self,
        connection: Connection,
        *,
        event: DtsChangeEvent,
        source_position: Mapping[str, Any],
        event_payload_hash: str,
        route_status: str,
        dirty_key_count: int,
        issue_codes: Sequence[str],
    ) -> bool:
        parameters = self._ledger_parameters(
            event=event,
            source_position=source_position,
            event_payload_hash=event_payload_hash,
            route_status=route_status,
            dirty_key_count=dirty_key_count,
            issue_codes=issue_codes,
        )
        inserted = connection.execute(
            text(
                """
                INSERT INTO public.dts_ingest_events (
                    source_region,topic,partition_id,offset_value,
                    record_id,source_timestamp,source_txid,source_position,
                    operation,source_database,source_schema,source_table,
                    route_status,dirty_key_count,issue_codes,
                    identity_version,source_partition_epoch_id,
                    source_position_v2,event_payload_hash
                ) VALUES (
                    :source_region,:topic,:partition,:offset,
                    :record_id,:source_timestamp,:source_txid,
                    :opaque_source_position,:operation,:source_database,
                    :source_schema,:source_table,:route_status,
                    :dirty_key_count,CAST(:issue_codes AS jsonb),
                    'V2_EPOCH',:epoch_id,CAST(:source_position AS jsonb),
                    :event_payload_hash
                )
                ON CONFLICT (
                    source_region,topic,partition_id,offset_value
                ) DO NOTHING
                RETURNING offset_value
                """
            ),
            parameters,
        ).scalar_one_or_none()
        if inserted == event.offset:
            return False

        row = connection.execute(
            text(
                """
                SELECT record_id,source_timestamp,source_txid,source_position,
                       operation,source_database,source_schema,source_table,
                       route_status,dirty_key_count,issue_codes,
                       identity_version,source_partition_epoch_id,
                       source_position_v2,event_payload_hash
                FROM public.dts_ingest_events
                WHERE source_region = :source_region
                  AND topic = :topic
                  AND partition_id = :partition
                  AND offset_value = :offset
                """
            ),
            parameters,
        ).mappings().one_or_none()
        expected = (
            event.record_id,
            event.source_timestamp,
            event.source_txid,
            event.source_position,
            event.operation,
            event.database_name,
            event.schema_name,
            event.table_name,
            route_status,
            dirty_key_count,
            list(issue_codes),
            _V2_IDENTITY_VERSION,
            self.source_partition_epoch_id,
            source_position,
            event_payload_hash,
        )
        actual = None if row is None else tuple(row.values())
        if actual != expected:
            raise DtsV2DualCaptureStoreError(
                "DTS_V2_DUAL_CAPTURE_LEDGER_IDENTITY_CONFLICT"
            )
        return True

    def _ledger_parameters(
        self,
        *,
        event: DtsChangeEvent,
        source_position: Mapping[str, Any],
        event_payload_hash: str,
        route_status: str,
        dirty_key_count: int,
        issue_codes: Sequence[str],
    ) -> dict[str, Any]:
        return {
            "source_region": event.source_region,
            "topic": event.topic,
            "partition": event.partition,
            "offset": event.offset,
            "record_id": event.record_id,
            "source_timestamp": event.source_timestamp,
            "source_txid": event.source_txid,
            "opaque_source_position": event.source_position,
            "operation": event.operation,
            "source_database": event.database_name,
            "source_schema": event.schema_name,
            "source_table": event.table_name,
            "route_status": route_status,
            "dirty_key_count": dirty_key_count,
            "issue_codes": _json_dump(list(issue_codes)),
            "epoch_id": self.source_partition_epoch_id,
            "source_position": _json_dump(source_position),
            "event_payload_hash": event_payload_hash,
        }

    def _write_new_ledger_batch(
        self,
        connection: Connection,
        records: Sequence[Mapping[str, Any]],
    ) -> None:
        if not records:
            return
        copied = copy_rows(
            connection,
            statement="""
                COPY public.dts_ingest_events (
                    source_region,topic,partition_id,offset_value,record_id,
                    source_timestamp,source_txid,source_position,operation,
                    source_database,source_schema,source_table,route_status,
                    dirty_key_count,issue_codes,identity_version,
                    source_partition_epoch_id,source_position_v2,
                    event_payload_hash
                ) FROM STDIN
            """,
            rows=(
                (
                    record["source_region"],
                    record["topic"],
                    record["partition"],
                    record["offset"],
                    record["record_id"],
                    record["source_timestamp"],
                    record["source_txid"],
                    record["opaque_source_position"],
                    record["operation"],
                    record["source_database"],
                    record["source_schema"],
                    record["source_table"],
                    record["route_status"],
                    record["dirty_key_count"],
                    jsonb(json.loads(str(record["issue_codes"]))),
                    _V2_IDENTITY_VERSION,
                    record["epoch_id"],
                    jsonb(json.loads(str(record["source_position"]))),
                    record["event_payload_hash"],
                )
                for record in records
            ),
        )
        if copied:
            return
        payload = [
            {
                **record,
                "partition_id": record["partition"],
                "offset_value": record["offset"],
                "issue_codes": json.loads(str(record["issue_codes"])),
                "source_position": json.loads(str(record["source_position"])),
            }
            for record in records
        ]
        inserted_count = connection.execute(
            text(
                """
                WITH incoming AS MATERIALIZED (
                  SELECT *
                  FROM jsonb_to_recordset(CAST(:records AS jsonb)) AS item(
                    source_region text,topic text,partition_id integer,
                    offset_value bigint,record_id bigint,source_timestamp bigint,
                    source_txid text,opaque_source_position text,
                    operation text,source_database text,source_schema text,
                    source_table text,route_status text,dirty_key_count integer,
                    issue_codes jsonb,epoch_id text,source_position jsonb,
                    event_payload_hash text
                  )
                ), inserted AS (
                  INSERT INTO public.dts_ingest_events (
                    source_region,topic,partition_id,offset_value,
                    record_id,source_timestamp,source_txid,source_position,
                    operation,source_database,source_schema,source_table,
                    route_status,dirty_key_count,issue_codes,
                    identity_version,source_partition_epoch_id,
                    source_position_v2,event_payload_hash
                  )
                  SELECT source_region,topic,partition_id,offset_value,
                         record_id,source_timestamp,source_txid,
                         opaque_source_position,operation,source_database,
                         source_schema,source_table,route_status,
                         dirty_key_count,issue_codes,'V2_EPOCH',epoch_id,
                         source_position,event_payload_hash
                  FROM incoming
                  ORDER BY offset_value
                ON CONFLICT (
                    source_region,topic,partition_id,offset_value
                ) DO NOTHING
                  RETURNING offset_value
                )
                SELECT count(*) FROM inserted
                """
            ),
            {
                "records": json.dumps(
                    payload,
                    ensure_ascii=True,
                    separators=(",", ":"),
                )
            },
        ).scalar_one()
        if inserted_count != len(records):
            raise DtsV2DualCaptureStoreError(
                "DTS_V2_DUAL_CAPTURE_LEDGER_IDENTITY_CONFLICT"
            )

    def _advance_batch_checkpoint(
        self,
        connection: Connection,
        *,
        event: DtsChangeEvent,
        expected_checkpoint_row_version: int,
        next_checkpoint_row_version: int,
        expected_next_offset: int,
        advanced_next_offset: int,
    ) -> None:
        updated = connection.execute(
            text(
                """
                UPDATE public.dts_ingest_checkpoints
                SET next_offset = :advanced_next_offset,
                    source_timestamp = :source_timestamp,
                    source_position = :source_position,
                    updated_at = clock_timestamp(),
                    source_partition_epoch_id = :epoch_id,
                    consumer_group = :consumer_group,
                    checkpoint_row_version = :next_version,
                    is_current_epoch = true
                WHERE source_region = :source_region
                  AND topic = :topic AND partition_id = :partition
                  AND next_offset = :expected_next_offset
                  AND source_partition_epoch_id = :epoch_id
                  AND consumer_group = :consumer_group
                  AND checkpoint_row_version = :expected_version
                  AND is_current_epoch IS TRUE
                RETURNING checkpoint_row_version
                """
            ),
            {
                "source_region": event.source_region,
                "topic": event.topic,
                "partition": event.partition,
                "expected_next_offset": expected_next_offset,
                "advanced_next_offset": advanced_next_offset,
                "source_timestamp": event.source_timestamp,
                "source_position": event.source_position,
                "epoch_id": self.source_partition_epoch_id,
                "consumer_group": self.consumer_group,
                "expected_version": expected_checkpoint_row_version,
                "next_version": next_checkpoint_row_version,
            },
        ).scalar_one_or_none()
        if updated != next_checkpoint_row_version:
            raise DtsV2DualCaptureStoreError(
                "DTS_V2_DUAL_CAPTURE_CHECKPOINT_CONFLICT"
            )

    def _advance_or_validate_checkpoint(
        self,
        connection: Connection,
        *,
        event: DtsChangeEvent,
        duplicate: bool,
        expected_checkpoint_row_version: int,
        expected_next_offset: int,
    ) -> int:
        if duplicate:
            row = connection.execute(
                text(
                    """
                    SELECT next_offset, source_partition_epoch_id,
                           consumer_group, checkpoint_row_version,
                           is_current_epoch
                    FROM public.dts_ingest_checkpoints
                    WHERE source_region = :source_region
                      AND topic = :topic
                      AND partition_id = :partition
                    FOR UPDATE
                    """
                ),
                {
                    "source_region": event.source_region,
                    "topic": event.topic,
                    "partition": event.partition,
                },
            ).mappings().one_or_none()
            if row is None:
                raise DtsV2DualCaptureStoreError(
                    "DTS_V2_DUAL_CAPTURE_CHECKPOINT_CONFLICT"
                )
            next_offset = _non_negative_int(
                row.get("next_offset"),
                "DTS_V2_DUAL_CAPTURE_CHECKPOINT_CONFLICT",
            )
            checkpoint_row_version = _positive_version(
                row.get("checkpoint_row_version"),
                "DTS_V2_DUAL_CAPTURE_CHECKPOINT_CONFLICT",
            )
            if (
                next_offset < event.offset + 1
                or row.get("source_partition_epoch_id")
                != self.source_partition_epoch_id
                or row.get("consumer_group") != self.consumer_group
                or checkpoint_row_version != expected_checkpoint_row_version
                or next_offset != expected_next_offset
                or row.get("is_current_epoch") is not True
            ):
                raise DtsV2DualCaptureStoreError(
                    "DTS_V2_DUAL_CAPTURE_CHECKPOINT_CONFLICT"
                )
            return expected_checkpoint_row_version

        next_version = expected_checkpoint_row_version + 1
        if event.offset != expected_next_offset:
            raise DtsV2DualCaptureStoreError(
                "DTS_V2_DUAL_CAPTURE_CHECKPOINT_CONFLICT"
            )
        updated = connection.execute(
            text(
                """
                UPDATE public.dts_ingest_checkpoints
                SET next_offset = :advanced_next_offset,
                    source_timestamp = :source_timestamp,
                    source_position = :source_position,
                    updated_at = clock_timestamp(),
                    source_partition_epoch_id = :epoch_id,
                    consumer_group = :consumer_group,
                    checkpoint_row_version = :next_version,
                    is_current_epoch = true
                WHERE source_region = :source_region
                  AND topic = :topic
                  AND partition_id = :partition
                  AND next_offset = :expected_next_offset
                  AND source_partition_epoch_id = :epoch_id
                  AND consumer_group = :consumer_group
                  AND checkpoint_row_version = :expected_version
                  AND is_current_epoch IS TRUE
                RETURNING checkpoint_row_version
                """
            ),
            {
                "source_region": event.source_region,
                "topic": event.topic,
                "partition": event.partition,
                "expected_next_offset": expected_next_offset,
                "advanced_next_offset": event.offset + 1,
                "source_timestamp": event.source_timestamp,
                "source_position": event.source_position,
                "epoch_id": self.source_partition_epoch_id,
                "consumer_group": self.consumer_group,
                "expected_version": expected_checkpoint_row_version,
                "next_version": next_version,
            },
        ).scalar_one_or_none()
        if updated != next_version:
            raise DtsV2DualCaptureStoreError(
                "DTS_V2_DUAL_CAPTURE_CHECKPOINT_CONFLICT"
            )
        return next_version


# Public runtime name. The historical class name remains as an import alias so
# existing unit fixtures do not create a second implementation path.
PostgresDtsSourceEventSink = PostgresDtsV2DualCaptureSink


def _coalesce_deferred_dirty_commands(
    commands: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """Union dirty fanout for one source identity at its final batch revision.

    The source-version ledger keeps every intermediate transition.  Dirty work
    is different: workers cannot observe the transaction until the final
    current is committed, and the database command surface deliberately accepts
    only that final revision.  Preserve the union of every affected domain key
    while removing duplicate commands and rebinding them to the final source
    revision.
    """

    final_revisions: dict[tuple[str, str, str], int] = {}
    ordered_unique: dict[
        tuple[str, str, str, str, str, str], dict[str, Any]
    ] = {}
    for command in commands:
        source_region = command.get("source_region")
        source_table = command.get("source_table")
        source_key = command.get("source_key")
        revision = command.get("source_row_revision")
        dirty_key = command.get("dirty_key")
        if (
            not isinstance(source_region, str)
            or not source_region
            or not isinstance(source_table, str)
            or not source_table
            or not isinstance(source_key, str)
            or not source_key
            or isinstance(revision, bool)
            or not isinstance(revision, int)
            or revision < 1
            or not isinstance(dirty_key, DirtyKeyV2)
        ):
            raise DtsV2DualCaptureStoreError(
                "DTS_V2_DUAL_CAPTURE_DEFERRED_DIRTY_INVALID"
            )
        source_identity = (source_region, source_table, source_key)
        final_revisions[source_identity] = max(
            revision,
            final_revisions.get(source_identity, 0),
        )
        command_identity = (
            *source_identity,
            dirty_key.key_type,
            dirty_key.key_part_1,
            dirty_key.key_part_2,
        )
        ordered_unique.setdefault(command_identity, dict(command))

    coalesced: list[dict[str, Any]] = []
    for command_identity, command in ordered_unique.items():
        command["source_row_revision"] = final_revisions[
            command_identity[:3]
        ]
        coalesced.append(command)
    return tuple(coalesced)


def _is_v2_business_event(event: DtsChangeEvent) -> bool:
    if event.operation not in DATA_OPERATIONS:
        return False
    table = event.table_name or ""
    prefix = f"{event.source_region}_"
    return table.startswith(prefix) and table.removeprefix(prefix) in (
        V2_BUSINESS_SOURCE_SUFFIXES_BY_REGION[event.source_region]
    )


def _result_dirty_keys(
    result: DtsV2ShadowSourceWriteResult | None,
) -> tuple[DirtyKeyV2, ...]:
    if result is None:
        return ()
    if result.status not in {
        "APPLIED",
        "IGNORED_MISSING_CURRENT",
        "NOOP",
        "SEMANTIC_REPLAY",
        "REPLAYED",
    }:
        raise DtsV2DualCaptureStoreError(
            "DTS_V2_DUAL_CAPTURE_SOURCE_RESULT_INVALID"
        )
    raw_keys = result.dirty_keys
    if not isinstance(raw_keys, tuple) or any(
        not isinstance(key, DirtyKeyV2) for key in raw_keys
    ):
        raise DtsV2DualCaptureStoreError(
            "DTS_V2_DUAL_CAPTURE_DIRTY_KEYS_INVALID"
        )
    if result.status == "IGNORED_MISSING_CURRENT" and raw_keys:
        raise DtsV2DualCaptureStoreError(
            "DTS_V2_DUAL_CAPTURE_DIRTY_KEYS_INVALID"
        )
    identities = tuple(
        (
            key.source_region,
            key.key_type,
            key.key_part_1,
            key.key_part_2,
        )
        for key in raw_keys
    )
    if len(set(identities)) != len(identities) or identities != tuple(
        sorted(identities)
    ):
        raise DtsV2DualCaptureStoreError(
            "DTS_V2_DUAL_CAPTURE_DIRTY_KEYS_INVALID"
        )
    return raw_keys


def _ledger_has_ignored_missing_current(
    connection: Connection,
    *,
    event: DtsChangeEvent,
) -> bool:
    if event.operation not in {"UPDATE", "DELETE"}:
        return False
    row = connection.execute(
        text(
            """
            SELECT route_status,issue_codes
            FROM public.dts_ingest_events
            WHERE source_region=:source_region
              AND topic=:topic
              AND partition_id=:partition
              AND offset_value=:offset
            """
        ),
        {
            "source_region": event.source_region,
            "topic": event.topic,
            "partition": event.partition,
            "offset": event.offset,
        },
    ).mappings().one_or_none()
    if row is None:
        return False
    issue_codes = row.get("issue_codes")
    return (
        row.get("route_status") == "IGNORED"
        and isinstance(issue_codes, list)
        and _MISSING_SOURCE_CURRENT_ISSUE in issue_codes
    )


def _ledger_issue_codes(route_hints: DirtyKeySet) -> tuple[str, ...]:
    values = (*route_hints.issues, route_hints.ignored_reason)
    return tuple(
        dict.fromkeys(
            value
            for value in values
            if isinstance(value, str) and value
        )
    )


def _required_text(value: Any, error: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DtsV2DualCaptureStoreError(error)
    return value.strip()


def _positive_version(value: Any, error: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise DtsV2DualCaptureStoreError(error)
    return value


def _required_sha256(value: Any, error: str) -> str:
    text_value = _required_text(value, error)
    if len(text_value) != 64 or any(
        character not in "0123456789abcdef" for character in text_value
    ):
        raise DtsV2DualCaptureStoreError(error)
    return text_value


def _non_negative_int(value: Any, error: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DtsV2DualCaptureStoreError(error)
    return value


def _source_position_v2(
    event: DtsChangeEvent,
    *,
    source_partition_epoch_id: str,
) -> dict[str, Any]:
    if (
        not isinstance(event.topic, str)
        or not event.topic
        or isinstance(event.partition, bool)
        or not isinstance(event.partition, int)
        or event.partition < 0
        or isinstance(event.offset, bool)
        or not isinstance(event.offset, int)
        or event.offset < 0
        or isinstance(event.record_id, bool)
        or not isinstance(event.record_id, int)
        or isinstance(event.source_timestamp, bool)
        or not isinstance(event.source_timestamp, int)
        or event.source_timestamp < 0
    ):
        raise DtsV2DualCaptureStoreError(
            "DTS_V2_DUAL_CAPTURE_SOURCE_POSITION_INVALID"
        )
    try:
        timestamp = datetime.fromtimestamp(event.source_timestamp, timezone.utc)
    except (OverflowError, OSError, TypeError, ValueError) as exc:
        raise DtsV2DualCaptureStoreError(
            "DTS_V2_DUAL_CAPTURE_SOURCE_POSITION_INVALID"
        ) from exc
    return {
        "v": 1,
        "source_timestamp": timestamp.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "record_id_type": "numeric",
        "record_id": str(event.record_id),
        "source_partition_epoch_id": source_partition_epoch_id,
        "topic": event.topic,
        "partition_id": event.partition,
        "offset_value": event.offset,
    }


def _event_payload_hash(
    event: DtsChangeEvent,
    *,
    source_partition_epoch_id: str,
    protected_source_hash: str | None,
) -> str:
    if not isinstance(event.source_position, str):
        raise DtsV2DualCaptureStoreError(
            "DTS_V2_DUAL_CAPTURE_SOURCE_POSITION_INVALID"
        )
    payload = {
        "v": 1,
        "source_position": _source_position_v2(
            event,
            source_partition_epoch_id=source_partition_epoch_id,
        ),
        "source_txid": event.source_txid,
        "opaque_source_position_hash": hashlib.sha256(
            event.source_position.encode("utf-8")
        ).hexdigest(),
        "operation": event.operation,
        "source_database": event.database_name,
        "source_schema": event.schema_name,
        "source_table": event.table_name,
        "before": _json_value(event.before),
        "after": _json_value(event.after),
        "source_field_types": _json_value(event.source_field_types),
        "source_field_type_numbers": _json_value(
            event.source_field_type_numbers
        ),
        "source_image_profile_id": event.source_image_profile_id,
        "protected_source_hash": protected_source_hash,
    }
    return hashlib.sha256(_json_dump(payload).encode("utf-8")).hexdigest()


def _json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise DtsV2DualCaptureStoreError(
                "DTS_V2_DUAL_CAPTURE_JSON_INVALID"
            )
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise DtsV2DualCaptureStoreError(
                "DTS_V2_DUAL_CAPTURE_JSON_INVALID"
            )
        return format(value, "f")
    if isinstance(value, bytes):
        raise DtsV2DualCaptureStoreError(
            "DTS_V2_DUAL_CAPTURE_JSON_INVALID"
        )
    if isinstance(value, float) and not math.isfinite(value):
        raise DtsV2DualCaptureStoreError(
            "DTS_V2_DUAL_CAPTURE_JSON_INVALID"
        )
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise DtsV2DualCaptureStoreError("DTS_V2_DUAL_CAPTURE_JSON_INVALID")


def _json_dump(value: Any) -> str:
    return json.dumps(
        _json_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _elapsed_ms(started: float, finished: float) -> int:
    return max(0, int((finished - started) * 1000))


def _stream_event(
    source_region: str,
    topic: str,
    partition: int,
) -> DtsChangeEvent:
    """Build the identity-only object required by the inherited stream lock."""

    return DtsChangeEvent(
        source_region=source_region,
        topic=topic,
        partition=partition,
        offset=0,
        record_id=0,
        source_timestamp=0,
        source_txid="",
        source_position="",
        operation="NOOP",
        database_name=None,
        schema_name=None,
        table_name=None,
        before=None,
        after=None,
    )


__all__ = [
    "V2_PRIMARY_MODE",
    "DtsV2DualCaptureStoreError",
    "PostgresDtsSourceEventSink",
    "PostgresDtsV2DualCaptureSink",
]
