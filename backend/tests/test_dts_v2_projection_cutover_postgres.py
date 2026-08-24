from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import shutil
import socket
import subprocess
from typing import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, URL
from sqlalchemy.exc import DBAPIError

from app.dts_v2_projection_cutover import (
    PostgresDtsV2ProjectionCutoverStore,
    RECONCILIATION_RESULT_TYPES,
)
from app.dts_v2_complaint_rule_catalog import (
    PostgresDtsV2ComplaintRuleCatalog,
)
from test_dts_v2_ops_case_postgres import (
    _postgres_tools_available,
    _run_alembic,
)
from test_dts_v2_source_current_guards_postgres import (
    _seed_external_personalized_catalog,
)


REVISION_94 = "20260822_94_complaint_catalog_fanout"
REVISION_95 = "20260822_95_projection_cutover"
SOURCE_PROFILES = (
    ("dom", "dom_appoint"),
    ("dom", "dom_complaint"),
    ("dom", "dom_complaint_cate"),
    ("dom", "dom_grading_label"),
    ("dom", "dom_grading_label_log"),
    ("dom", "dom_qa_task_close_camera_record"),
    ("dom", "dom_teacher"),
    ("dom", "dom_teacher_absent_reason"),
    ("dom", "dom_teacher_blacklist"),
    ("dom", "dom_teacher_certification"),
    ("dom", "dom_teacher_class_schedule"),
    ("dom", "dom_teacher_favorite"),
    ("dom", "dom_teacher_penalty"),
    ("dom", "dom_user_complaint"),
    ("dom", "dom_user_teacher_grading"),
    ("ovs", "ovs_appoint"),
    ("ovs", "ovs_complaint"),
    ("ovs", "ovs_grading_label"),
    ("ovs", "ovs_grading_label_log"),
    ("ovs", "ovs_qa_task_close_camera_record"),
    ("ovs", "ovs_teacher_blacklist"),
    ("ovs", "ovs_teacher_favorite"),
    ("ovs", "ovs_user_complaint"),
    ("ovs", "ovs_user_teacher_grading"),
)


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


@pytest.fixture()
def projection_cutover_postgres(
    tmp_path: Path,
) -> Iterator[tuple[Engine, Engine, Engine]]:
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
    cutover: Engine | None = None
    teacher: Engine | None = None
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
        _run_alembic(backend_dir, admin_url, "upgrade", REVISION_95)

        # Prove this revision is reversibly additive before any cutover fact
        # exists, then install it again on the same physical database.
        _run_alembic(backend_dir, admin_url, "downgrade", REVISION_94)
        _run_alembic(backend_dir, admin_url, "upgrade", REVISION_95)
        _run_alembic(
            backend_dir,
            admin_url,
            "upgrade",
            "20260823_100_scope_snapshot_diff",
        )

        cutover = create_engine(
            database_url("tit_growth_app")
        )
        teacher = create_engine(database_url("tit_teacher_crud"))
        yield admin, cutover, teacher
    finally:
        if teacher is not None:
            teacher.dispose()
        if cutover is not None:
            cutover.dispose()
        admin.dispose()
        subprocess.run(
            [pg_ctl, "-D", str(data_dir), "-m", "fast", "-w", "stop"],
            check=False,
            capture_output=True,
            text=True,
        )


def _bootstrap_h0_and_approve_profiles(connection) -> str:
    connection.execute(
        text(
            """
            INSERT INTO public.dts_ingest_checkpoints(
              source_region,topic,partition_id,next_offset,source_timestamp,
              source_position,updated_at
            ) VALUES (
              'dom','projection-cutover-fixture',0,0,0,
              'projection-cutover-fixture',transaction_timestamp()
            )
            """
        )
    )
    epoch_id = connection.execute(
        text(
            "SELECT public.dts_broker_epoch_id_v2("
            "'dom','projection-cutover-fixture',0,'generation-1','opening-1')"
        )
    ).scalar_one()
    routes = [
        {
            "source_region": "dom",
            "topic": "projection-cutover-fixture",
            "partition_id": 0,
            "current_next_offset": 0,
            "consumer_group": "projection-cutover-group",
            "stream_generation_id": "generation-1",
            "epoch_opening_id": "opening-1",
            "source_partition_epoch_id": epoch_id,
        }
    ]
    route_json = _canonical(routes)
    route_hash = connection.execute(
        text(
            "SELECT public.dts_initial_broker_epoch_vector_hash_v2("
            "CAST(:routes AS jsonb))"
        ),
        {"routes": route_json},
    ).scalar_one()
    connection.execute(
        text(
            "SELECT public.bootstrap_initial_broker_epoch_v2("
            "'projection-cutover-h0','projection-cutover-fleet',"
            "CAST(:routes AS jsonb),:route_hash)"
        ),
        {"routes": route_json, "route_hash": route_hash},
    )

    profile_vector = [
        {
            "source_region": region,
            "source_table": table,
            "source_schema_profile_id": "dts-source-schema:v2:"
            + hashlib.sha256(f"{region}:{table}".encode()).hexdigest(),
        }
        for region, table in SOURCE_PROFILES
    ]
    vector_json = _canonical(profile_vector)
    manifest_sha = connection.execute(
        text(
            "SELECT public.dts_canonical_json_sha256_v1("
            "CAST(:vector AS jsonb))"
        ),
        {"vector": vector_json},
    ).scalar_one()
    connection.execute(
        text(
            "SELECT set_config("
            "'tit.dts_source_profile_approval_migration','on',true)"
        )
    )
    connection.execute(
        text(
            """
            INSERT INTO public.dts_source_profile_approvals_v2(
              manifest_sha256,manifest_version,profile_vector,
              profile_vector_hash,status,approved_by_change_id,approved_by
            ) VALUES (
              :manifest_sha,1,CAST(:vector AS jsonb),:manifest_sha,
              'APPROVED','test-only-approved-profile','postgres-test-fixture'
            )
            """
        ),
        {"manifest_sha": manifest_sha, "vector": vector_json},
    )
    return str(manifest_sha)


def _seed_fresh_h0_and_profile(
    connection,
) -> tuple[list[dict[str, object]], str, str]:
    connection.execute(
        text(
            """
            INSERT INTO public.dts_ingest_checkpoints(
              source_region,topic,partition_id,next_offset,source_timestamp,
              source_position,updated_at
            ) VALUES
              ('dom','fresh-dom',0,41,0,'fresh-dom',transaction_timestamp()),
              ('ovs','fresh-ovs',0,73,0,'fresh-ovs',transaction_timestamp())
            """
        )
    )
    routes: list[dict[str, object]] = []
    for region, topic, offset in (
        ("dom", "fresh-dom", 41),
        ("ovs", "fresh-ovs", 73),
    ):
        epoch_id = connection.execute(
            text(
                "SELECT public.dts_broker_epoch_id_v2("
                ":region,:topic,0,'generation-1','opening-1')"
            ),
            {"region": region, "topic": topic},
        ).scalar_one()
        routes.append(
            {
                "source_region": region,
                "topic": topic,
                "partition_id": 0,
                "current_next_offset": offset,
                "consumer_group": f"fresh-{region}-consumer",
                "stream_generation_id": "generation-1",
                "epoch_opening_id": "opening-1",
                "source_partition_epoch_id": epoch_id,
            }
        )
    routes_json = _canonical(routes)
    vector_hash = str(
        connection.execute(
            text(
                "SELECT public.dts_initial_broker_epoch_vector_hash_v2("
                "CAST(:routes AS jsonb))"
            ),
            {"routes": routes_json},
        ).scalar_one()
    )

    profile_vector = [
        {
            "source_region": region,
            "source_table": table,
            "source_schema_profile_id": "dts-source-schema:v2:"
            + hashlib.sha256(f"{region}:{table}".encode()).hexdigest(),
        }
        for region, table in SOURCE_PROFILES
    ]
    profile_json = _canonical(profile_vector)
    manifest_sha = str(
        connection.execute(
            text(
                "SELECT public.dts_canonical_json_sha256_v1("
                "CAST(:profile AS jsonb))"
            ),
            {"profile": profile_json},
        ).scalar_one()
    )
    connection.execute(
        text(
            "SELECT set_config("
            "'tit.dts_source_profile_approval_migration','on',true)"
        )
    )
    connection.execute(
        text(
            """
            INSERT INTO public.dts_source_profile_approvals_v2(
              manifest_sha256,manifest_version,profile_vector,
              profile_vector_hash,status,approved_by_change_id,approved_by
            ) VALUES (
              :manifest_sha,1,CAST(:profile AS jsonb),:manifest_sha,
              'APPROVED','fresh-test-profile','postgres-test-fixture'
            )
            """
        ),
        {"manifest_sha": manifest_sha, "profile": profile_json},
    )
    return routes, vector_hash, manifest_sha


def _seed_complete_scope_and_lesson(connection) -> None:
    # These rows are test-only stand-ins for coordinator-published snapshots.
    # Production cannot bypass the coordinator guards with its restricted roles.
    connection.execute(text("SET LOCAL session_replication_role='replica'"))
    for position, (region, table) in enumerate(SOURCE_PROFILES, start=1):
        connection.execute(
            text(
                """
                INSERT INTO public.dts_source_scope_states(
                  source_region,source_table,scope_kind,scope_level,scope_key,
                  state,active_snapshot_id,candidate_snapshot_id,completed_at,
                  invalidated_at,row_version,updated_at
                ) VALUES (
                  :region,:source_table,'CURRENT','GLOBAL','*','COMPLETE',
                  :snapshot_id,NULL,transaction_timestamp(),NULL,1,
                  transaction_timestamp()
                )
                """
            ),
            {
                "region": region,
                "source_table": table,
                "snapshot_id": f"cutover-complete-{position}",
            },
        )

    connection.execute(
        text(
            """
            INSERT INTO public.task_templates(
              row_id,template_id,template_version,status,revision,
              output_type,execution_owner,integration_mode,
              external_task_template_code,source_mode,payload,
              created_by,updated_by,created_at,updated_at
            ) VALUES (
              'P-FB-COMPLAINT:v1','P-FB-COMPLAINT',1,'PUBLISHED',1,
              'TEACHER_TASK','TEACHER_APP','OUTBOUND_MANAGED',
              'P-FB-COMPLAINT','REAL',jsonb_build_object(
                'template_id','P-FB-COMPLAINT','title','Complaint Improvement',
                'category','PERSONALIZED_IMPROVEMENT',
                'content_status','READY','score_type','ZERO','score_value',0
              ),'CUTOVER_TEST','CUTOVER_TEST',transaction_timestamp(),
              transaction_timestamp()
            ),(
              'P-FB-BLACKLIST:v1','P-FB-BLACKLIST',1,'PUBLISHED',1,
              'TEACHER_TASK','TEACHER_APP','OUTBOUND_MANAGED',
              'P-FB-BLACKLIST','REAL',jsonb_build_object(
                'template_id','P-FB-BLACKLIST','title','Blacklist Improvement',
                'category','PERSONALIZED_IMPROVEMENT',
                'content_status','READY','score_type','ZERO','score_value',0
              ),'CUTOVER_TEST','CUTOVER_TEST',transaction_timestamp(),
              transaction_timestamp()
            )
            """
        )
    )

    student_token = "dom:v1:" + "1" * 64
    connection.execute(
        text(
            """
            INSERT INTO public.source_courses(
              source_region,source_appoint_id,student_token,source_status,
              current_teacher_id,current_teacher_id_type,
              current_participation_seq,source_is_deleted,evidence_status,
              appoint_evidence_status,teacher_region_evidence_status,
              row_version,updated_at
            ) VALUES (
              'dom','cutover-course-1',:student_token,'on','teacher-cutover',
              'TEXT',1,false,'CONFIRMED','CONFIRMED','CONFIRMED',1,
              transaction_timestamp()
            )
            """
        ),
        {"student_token": student_token},
    )
    connection.execute(
        text(
            """
            INSERT INTO public.source_course_participations(
              source_region,source_appoint_id,participation_seq,teacher_id,
              teacher_id_type,participation_status,participation_role,
              is_current,assigned_at,assigned_at_evidence_status,ended_at,
              teacher_expected_source_region,teacher_region_evidence_status,
              teacher_profile_source_row_revision,
              teacher_profile_source_payload_hash,source_deleted,
              assignment_source_partition_epoch_id,assignment_event_topic,
              assignment_event_partition,assignment_event_offset,
              assignment_source_row_revision,assignment_event_phase,row_version
            ) VALUES (
              'dom','cutover-course-1',1,'teacher-cutover','TEXT','on',
              'NORMAL',true,transaction_timestamp(),'CONFIRMED',NULL,'dom',
              'CONFIRMED',1,repeat('2',64),false,'cutover-epoch',
              'cutover-topic',0,1,1,'AFTER',1
            )
            """
        )
    )
    connection.execute(
        text(
            """
            INSERT INTO public.source_course_fact_current(
              source_region,source_appoint_id,grading_classification,
              negative_score,grading_evidence_status,grading_error_code,
              has_complaint,has_valid_complaint,complaint_evidence_status,
              complaint_error_code,is_camera_off,camera_evidence_status,
              is_cpu_usage_high,cpu_evidence_status,is_network_delay_high,
              network_evidence_status,source_version_vector,source_version_hash
            ) VALUES (
              'dom','cutover-course-1','POSITIVE',NULL,'CONFIRMED',NULL,
              false,false,'CONFIRMED',NULL,false,'CONFIRMED',NULL,
              'SOURCE_MISSING',NULL,'SOURCE_MISSING','{}'::jsonb,
              public.dts_canonical_json_sha256_v1('{}'::jsonb)
            )
            """
        )
    )
    connection.execute(text("SET LOCAL session_replication_role='origin'"))


def _publish_test_complaint_catalog(connection) -> None:
    source_sha = hashlib.sha256(b"cutover-test-complaint-catalog").hexdigest()
    rule_id = f"complaint-rule:{source_sha}:1"
    connection.execute(
        text(
            """
            INSERT INTO public.complaint_rule_imports(
              source_sha256,source_filename,raw_rows,imported_at,status,
              publication_revision,activation_generation,row_count,
              content_hash,published_at,retired_at
            ) VALUES (
              :source_sha,'cutover-test.xlsx',
              CAST(:raw_rows AS jsonb),transaction_timestamp(),
              'DRAFT',1,NULL,1,repeat('0',64),NULL,NULL
            );
            """
        ),
        {
            "source_sha": source_sha,
            "raw_rows": _canonical([{"source_row_number": 1}]),
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO public.complaint_category_rules(
              rule_id,source_sha256,source_row_number,category_l1,category_l2,
              category_l3,category_l3_normalized,source_level,severity_rank,
              default_route,created_at
            ) VALUES (
              :rule_id,:source_sha,1,'一级','二级','三级','三级',
              'P2',2,'TEACHER_TASK',transaction_timestamp()
            )
            """
        ),
        {"rule_id": rule_id, "source_sha": source_sha},
    )
    connection.execute(
        text(
            """
            UPDATE public.complaint_rule_imports
            SET content_hash=public.complaint_rule_catalog_content_hash_v1(
              :source_sha
            ) WHERE source_sha256=:source_sha
            """
        ),
        {"source_sha": source_sha},
    )
    connection.execute(
        text("SET LOCAL ROLE tit_growth_app")
    )
    PostgresDtsV2ComplaintRuleCatalog().publish(
        connection,
        source_sha256=source_sha,
        expected_revision=0,
        idempotency_key="cutover-test-publish",
    )
    connection.execute(text("RESET ROLE"))


def _install_reconciliation_providers(connection, manifest_sha: str) -> None:
    result_types = list(RECONCILIATION_RESULT_TYPES)
    result_manifest = [
        {
            "result_type": result_type,
            "row_count": 0,
            "content_hash": hashlib.sha256(result_type.encode()).hexdigest(),
        }
        for result_type in result_types
    ]
    connection.execute(
        text(
            """
            CREATE OR REPLACE FUNCTION
              public.dts_v1_compat_dirty_not_complete_count_v1()
            RETURNS bigint LANGUAGE sql STABLE
            SET search_path=pg_catalog,public
            AS $function$ SELECT 0::bigint $function$
            """
        )
    )
    connection.exec_driver_sql(
        f"""
            CREATE FUNCTION public.dts_v2_full_reconciliation_manifest_v1(
              p_evaluation_as_of timestamptz
            ) RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER
            SET search_path=pg_catalog,public
            AS $function$
              SELECT jsonb_build_object(
                'protocol_version','dts-v2-full-reconciliation-manifest-v1',
                'evaluation_as_of',to_char(
                  p_evaluation_as_of AT TIME ZONE 'UTC',
                  'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
                ),
                'source_fence_hash',public.dts_canonical_json_sha256_v1(
                  public.dts_v2_current_fence_vector_v1()
                ),
                'source_profile_manifest_sha256','{manifest_sha}',
                'result_types',CAST('{_canonical(result_types)}' AS jsonb),
                'v1_result_manifest',
                  CAST('{_canonical(result_manifest)}' AS jsonb),
                'v2_result_manifest',
                  CAST('{_canonical(result_manifest)}' AS jsonb)
              )
            $function$
            """
    )


def _lesson_row(engine: Engine) -> dict[str, object]:
    with engine.connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT source_region,source_appoint_id,participation_seq,
                  business_facts,dimensions
                FROM public.teacher_lesson_score_current
                WHERE source_appoint_id='cutover-course-1'
                """
            )
        ).mappings().one()
    return dict(row)


@pytest.mark.skipif(
    not _postgres_tools_available(),
    reason="local PostgreSQL binaries are required for cutover tests",
)
def test_single_pipeline_reset_clears_history_and_first_event_creates_h0(
    projection_cutover_postgres: tuple[Engine, Engine, Engine],
) -> None:
    admin, _cutover, _teacher = projection_cutover_postgres
    backend_dir = Path(__file__).resolve().parents[1]
    with admin.begin() as connection:
        template_count = connection.execute(
            text("SELECT count(*) FROM public.task_templates")
        ).scalar_one()
        connection.execute(
            text(
                """
                INSERT INTO public.dts_ingest_checkpoints(
                  source_region,topic,partition_id,next_offset,
                  source_timestamp,source_position,updated_at
                ) VALUES (
                  'ovs','old-topic',0,99,1786523000,'old',clock_timestamp()
                )
                """
            )
        )
    admin_url = admin.url.render_as_string(hide_password=False)
    _run_alembic(backend_dir, admin_url, "upgrade", "head")

    with admin.connect() as connection:
        assert connection.execute(
            text(
                "SELECT mode,row_version,projection_generation,"
                "qualification_grants_enabled,time_catchup_status "
                "FROM public.dts_pipeline_control WHERE control_id='PRIMARY'"
            )
        ).one() == ("V2_PRIMARY", 1, 1, False, "NOT_REQUIRED")
        assert connection.execute(
            text(
                "SELECT active_projection,row_version "
                "FROM public.dts_projection_read_routes "
                "WHERE route_id='PRIMARY'"
            )
        ).one() == ("V2", 2)
        assert connection.execute(
            text("SELECT count(*) FROM public.dts_ingest_checkpoints")
        ).scalar_one() == 0
        assert connection.execute(
            text("SELECT count(*) FROM public.dts_v2_reconciliation_runs")
        ).scalar_one() == 0
        assert connection.execute(
            text("SELECT count(*) FROM public.dts_projection_switch_audits")
        ).scalar_one() == 0
        assert connection.execute(
            text("SELECT count(*) FROM public.dts_source_scope_states")
        ).scalar_one() == 0
        assert connection.execute(
            text("SELECT count(*) FROM public.dts_pipeline_reset_audits")
        ).scalar_one() == 1
        assert connection.execute(
            text("SELECT count(*) FROM public.task_templates")
        ).scalar_one() == template_count
        manifest_sha = connection.execute(
            text(
                "SELECT source_profile_manifest_sha256 "
                "FROM public.dts_pipeline_reset_audits"
            )
        ).scalar_one()

    ingest_url = admin.url.set(username="tit_dts_ingest_runtime").render_as_string(
        hide_password=False
    )
    ingest = create_engine(ingest_url)
    try:
        with ingest.begin() as connection:
            assert connection.execute(
                text(
                    "SELECT public.initialize_dts_event_stream_v1("
                    "'ovs','new-topic',0,'epoch-ovs-reset-1',"
                    "'new-consumer',123,1786523400,'first-position',:manifest)"
                ),
                {"manifest": manifest_sha},
            ).scalar_one() == "INITIALIZED"
        with ingest.begin() as connection:
            assert connection.execute(
                text(
                    "SELECT public.initialize_dts_event_stream_v1("
                    "'ovs','new-topic',0,'epoch-ovs-reset-1',"
                    "'new-consumer',999,1786523500,'later-position',:manifest)"
                ),
                {"manifest": manifest_sha},
            ).scalar_one() == "EXISTS"
    finally:
        ingest.dispose()

    with admin.connect() as connection:
        assert connection.execute(
            text(
                "SELECT next_offset,source_timestamp,"
                "source_partition_epoch_id,consumer_group,is_current_epoch "
                "FROM public.dts_ingest_checkpoints"
            )
        ).one() == (
            123,
            1786523400,
            "epoch-ovs-reset-1",
            "new-consumer",
            True,
        )
        assert connection.execute(
            text(
                "SELECT has_function_privilege("
                "'tit_dts_ingest_runtime',"
                "'public.claim_v1_compat_dirty_key_v1(text)','EXECUTE')"
            )
        ).scalar_one() is False


@pytest.mark.skipif(
    not _postgres_tools_available(),
    reason="local PostgreSQL binaries are required for cutover tests",
)
def test_projection_cutover_is_fail_closed_atomic_replayable_and_reversible(
    projection_cutover_postgres: tuple[Engine, Engine, Engine],
) -> None:
    admin, cutover, teacher = projection_cutover_postgres
    store = PostgresDtsV2ProjectionCutoverStore()
    with admin.begin() as connection:
        manifest_sha = _bootstrap_h0_and_approve_profiles(connection)
        _seed_complete_scope_and_lesson(connection)
        _publish_test_complaint_catalog(connection)

    # A source-profile approval alone can never become operator-asserted PASS.
    with pytest.raises(DBAPIError) as provider_missing:
        with cutover.begin() as connection:
            store.preview_reconciliation_evidence(
                connection,
                evaluation_as_of=datetime.now(timezone.utc),
                source_profile_manifest_sha256=manifest_sha,
            )
    assert "DTS_V2_FULL_RECONCILIATION_UNAVAILABLE" in str(
        provider_missing.value.orig
    )
    with admin.connect() as connection:
        assert connection.execute(
            text("SELECT count(*) FROM public.dts_v2_reconciliation_runs")
        ).scalar_one() == 0
        assert connection.execute(
            text("SELECT count(*) FROM public.dts_projection_switch_audits")
        ).scalar_one() == 0

    with admin.begin() as connection:
        _install_reconciliation_providers(connection, manifest_sha)
        technical_gate = connection.execute(
            text(
                "SELECT public.dts_v2_cutover_technical_gate_manifest_v1("
                "transaction_timestamp())"
            )
        ).scalar_one()
        assert technical_gate == {
            "protocol_version": "dts-v2-cutover-technical-gate-v1",
            "full_reconciliation_provider": "AVAILABLE",
            "compat_dirty_provider": "AVAILABLE",
            "dirty_not_complete_count": 0,
            "compat_dirty_not_complete_count": 0,
            "v2_outbox_pending_count": 0,
            "v2_outbox_dead_letter_count": 0,
            "open_ingest_issue_count": 0,
            "incomplete_source_scope_count": 0,
            "due_favorite_unsettled_count": 0,
            "legacy_output_issue_count": 0,
            "published_complaint_catalog_count": 1,
            "published_task_template_count": 14,
        }

    # A structurally valid PASS from an earlier Beijing business date is
    # retained as evidence but is expired for routing and cannot switch.
    with cutover.begin() as connection:
        expired_preview = store.preview_reconciliation_evidence(
            connection,
            evaluation_as_of=datetime.now(timezone.utc) - timedelta(days=2),
            source_profile_manifest_sha256=manifest_sha,
        )
        store.record_reconciliation_request(
            connection, run_id="expired-pass", request=expired_preview
        )
    with pytest.raises(DBAPIError) as expired_switch:
        with cutover.begin() as connection:
            store.switch_projection(
                connection,
                run_id="expired-pass",
                expected_control_version=1,
                expected_route_version=1,
                target_mode="V2_PRIMARY",
            )
    assert "DTS_V2_CUTOVER_INPUT_CHANGED" in str(expired_switch.value.orig)
    with admin.connect() as connection:
        assert connection.execute(
            text("SELECT count(*) FROM public.dts_projection_switch_audits")
        ).scalar_one() == 0

    v1_row = _lesson_row(teacher)
    assert (
        v1_row["source_region"],
        v1_row["source_appoint_id"],
        v1_row["participation_seq"],
    ) == ("dom", "cutover-course-1", 1)
    assert "has_positive_feedback_tag" in v1_row["business_facts"][
        "user_feedback"
    ]
    assert v1_row["business_facts"]["user_feedback"][
        "has_positive_feedback_tag"
    ] is None
    assert all(
        dimension["evidence_status"] == "NOT_APPLICABLE"
        and dimension["evidence_coverage"] is None
        and isinstance(dimension["components"], list)
        for dimension in v1_row["dimensions"]
    )

    evaluation = datetime.now(timezone.utc)
    with cutover.begin() as connection:
        preview = store.preview_reconciliation_evidence(
            connection,
            evaluation_as_of=evaluation,
            source_profile_manifest_sha256=manifest_sha,
        )
        applied = store.record_reconciliation_request(
            connection, run_id="cutover-pass-1", request=preview
        )
        replayed = store.record_reconciliation_request(
            connection, run_id="cutover-pass-1", request=preview
        )
    assert applied["status"] == "APPLIED"
    assert replayed["status"] == "REPLAYED"

    # Wrong CAS versions abort without route, control, or audit residue.
    with pytest.raises(DBAPIError) as stale_switch:
        with cutover.begin() as connection:
            store.switch_projection(
                connection,
                run_id="cutover-pass-1",
                expected_control_version=99,
                expected_route_version=99,
                target_mode="V2_PRIMARY",
            )
    assert "PROJECTION_ROUTE_CONFLICT" in str(stale_switch.value.orig)
    with admin.connect() as connection:
        assert connection.execute(
            text(
                "SELECT mode,row_version,projection_generation "
                "FROM public.dts_pipeline_control WHERE control_id='PRIMARY'"
            )
        ).one() == ("V1_COMPAT_DUAL_CAPTURE", 1, 0)
        assert connection.execute(
            text(
                "SELECT active_projection,row_version "
                "FROM public.dts_projection_read_routes "
                "WHERE route_id='PRIMARY'"
            )
        ).one() == ("V1_COMPAT", 1)
        assert connection.execute(
            text("SELECT count(*) FROM public.dts_projection_switch_audits")
        ).scalar_one() == 0

    with cutover.begin() as connection:
        switched = store.switch_projection(
            connection,
            run_id="cutover-pass-1",
            expected_control_version=1,
            expected_route_version=1,
            target_mode="V2_PRIMARY",
        )
        switch_replay = store.switch_projection(
            connection,
            run_id="cutover-pass-1",
            expected_control_version=1,
            expected_route_version=1,
            target_mode="V2_PRIMARY",
        )
        readiness = store.read_readiness(connection)
    assert switched["status"] == "APPLIED"
    assert switch_replay["status"] == "REPLAYED"
    assert readiness.ready is True
    assert readiness.code == "READY_V2_PRIMARY"

    v2_row = _lesson_row(teacher)
    feedback = v2_row["business_facts"]["user_feedback"]
    assert feedback["grading_classification"] == "POSITIVE"
    assert feedback["has_positive_feedback_tag"] is True
    assert feedback["has_negative_feedback_tag"] is False
    assert feedback["is_favorited"] is None
    assert feedback["is_rebooked"] is None
    with admin.connect() as connection:
        assert connection.execute(
            text(
                """
                SELECT count(*) FROM information_schema.columns
                WHERE table_schema='public'
                  AND table_name='teacher_lesson_score_current'
                  AND column_name='lesson_id'
                """
            )
        ).scalar_one() == 0

    # The runtime role can invoke commands, but cannot mutate route facts.
    with pytest.raises(DBAPIError):
        with cutover.begin() as connection:
            connection.execute(
                text(
                    "UPDATE public.dts_projection_read_routes "
                    "SET active_projection='V1_COMPAT' "
                    "WHERE route_id='PRIMARY'"
                )
            )

    # Rollback remains available when reconciliation providers are down and
    # changes only route/control facts; the v2 fact and PASS remain intact.
    with admin.begin() as connection:
        connection.execute(
            text(
                "DROP FUNCTION public."
                "dts_v2_full_reconciliation_manifest_v1(timestamptz)"
            )
        )
        connection.execute(
            text(
                "DROP FUNCTION public."
                "dts_v1_compat_dirty_not_complete_count_v1()"
            )
        )
    with cutover.begin() as connection:
        rolled_back = store.switch_projection(
            connection,
            run_id="rollback-1",
            expected_control_version=2,
            expected_route_version=2,
            target_mode="ROLLED_BACK",
        )
        rollback_replay = store.switch_projection(
            connection,
            run_id="rollback-1",
            expected_control_version=2,
            expected_route_version=2,
            target_mode="ROLLED_BACK",
        )
        readiness = store.read_readiness(connection)
    assert rolled_back["status"] == "APPLIED"
    assert rollback_replay["status"] == "REPLAYED"
    assert readiness.ready is True
    assert readiness.code == "READY_ROLLED_BACK"

    _lesson_row(teacher)  # the V1 branch remains queryable after rollback
    with admin.connect() as connection:
        assert connection.execute(
            text(
                "SELECT mode,projection_generation FROM "
                "public.dts_pipeline_control WHERE control_id='PRIMARY'"
            )
        ).one() == ("ROLLED_BACK", 1)
        assert connection.execute(
            text(
                "SELECT count(*) FROM public.source_course_fact_current "
                "WHERE source_appoint_id='cutover-course-1'"
            )
        ).scalar_one() == 1
        assert connection.execute(
            text(
                "SELECT count(*) FROM public.dts_v2_reconciliation_runs "
                "WHERE run_id='cutover-pass-1'"
            )
        ).scalar_one() == 1
