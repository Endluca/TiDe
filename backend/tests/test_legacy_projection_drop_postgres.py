from __future__ import annotations

import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import DBAPIError


POSTGRES_BINARIES = ("initdb", "pg_ctl", "postgres")
LEGACY_TABLES = (
    "lesson_dimension_scores",
    "lesson_facts",
    "teacher_metric_snapshots",
)


def _postgres_tools_available() -> bool:
    return all(shutil.which(binary) for binary in POSTGRES_BINARIES)


def _run_alembic(backend_dir: Path, database_url: str, *args: str) -> None:
    environment = os.environ.copy()
    environment["APP_ENV"] = "test"
    environment["DATABASE_URL"] = database_url
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=backend_dir,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"alembic {' '.join(args)} failed\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def _run_alembic_expect_failure(
    backend_dir: Path,
    database_url: str,
    expected_error: str,
    *args: str,
) -> None:
    environment = os.environ.copy()
    environment["APP_ENV"] = "test"
    environment["DATABASE_URL"] = database_url
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=backend_dir,
        env=environment,
        capture_output=True,
        text=True,
    )
    output = f"{result.stdout}\n{result.stderr}"
    assert result.returncode != 0, "destructive migration unexpectedly succeeded"
    assert expected_error in output


def _schema_signature(connection) -> dict[str, list[tuple]]:
    table_array = ", ".join(f"'{name}'" for name in LEGACY_TABLES)
    return {
        "columns": list(
            connection.execute(
                text(
                    f"""
                    SELECT
                        table_name,
                        ordinal_position,
                        column_name,
                        data_type,
                        udt_name,
                        character_maximum_length,
                        is_nullable,
                        column_default
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name IN ({table_array})
                    ORDER BY table_name, ordinal_position
                    """
                )
            ).tuples()
        ),
        "constraints": list(
            connection.execute(
                text(
                    f"""
                    SELECT
                        relation.relname,
                        constraint_row.conname,
                        constraint_row.contype,
                        pg_get_constraintdef(constraint_row.oid, true)
                    FROM pg_constraint AS constraint_row
                    JOIN pg_class AS relation
                      ON relation.oid = constraint_row.conrelid
                    JOIN pg_namespace AS namespace
                      ON namespace.oid = relation.relnamespace
                    WHERE namespace.nspname = 'public'
                      AND relation.relname IN ({table_array})
                    ORDER BY relation.relname, constraint_row.conname
                    """
                )
            ).tuples()
        ),
        "indexes": list(
            connection.execute(
                text(
                    f"""
                    SELECT tablename, indexname, indexdef
                    FROM pg_indexes
                    WHERE schemaname = 'public'
                      AND tablename IN ({table_array})
                    ORDER BY tablename, indexname
                    """
                )
            ).tuples()
        ),
        "acl": list(
            connection.execute(
                text(
                    f"""
                    SELECT relation.relname, relation.relacl::text
                    FROM pg_class AS relation
                    JOIN pg_namespace AS namespace
                      ON namespace.oid = relation.relnamespace
                    WHERE namespace.nspname = 'public'
                      AND relation.relname IN ({table_array})
                    ORDER BY relation.relname
                    """
                )
            ).tuples()
        ),
    }


@pytest.mark.skipif(
    not _postgres_tools_available(),
    reason="local PostgreSQL binaries are required for migration round-trip",
)
def test_revisions_47_to_49_real_postgresql_upgrade_downgrade_round_trip(
    tmp_path: Path,
) -> None:
    """Prove the destructive boundary and exact rollback on real PostgreSQL."""

    backend_dir = Path(__file__).resolve().parents[1]
    data_dir = tmp_path / "postgres-data"
    log_path = tmp_path / "postgres.log"
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        postgres_port = int(probe.getsockname()[1])

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
            f"-p {postgres_port} -c listen_addresses=127.0.0.1 -c fsync=off",
            "-w",
            "start",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    database_url = URL.create(
        "postgresql+psycopg",
        username="postgres",
        host="127.0.0.1",
        port=postgres_port,
        database="postgres",
    ).render_as_string(hide_password=False)
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
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
            database_url,
            "upgrade",
            "20260807_46_teacher_g01_source",
        )
        with engine.connect() as connection:
            baseline = _schema_signature(connection)
            assert len(baseline["columns"]) == 102
            assert len(baseline["indexes"]) == 27

        _run_alembic(
            backend_dir,
            database_url,
            "upgrade",
            "20260807_47_legacy_drop",
        )
        with engine.connect() as connection:
            assert connection.execute(
                text(
                    """
                    SELECT count(*)
                    FROM information_schema.tables
                    WHERE table_schema = 'public'
                      AND table_name IN (
                        'lesson_dimension_scores',
                        'lesson_facts',
                        'teacher_metric_snapshots'
                      )
                    """
                )
            ).scalar_one() == 0

        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO public.teachers (
                        teacher_id, camp_enrollment_id, name, timezone,
                        camp_day, graduation_state, gold_qualified,
                        total_score, graduation_threshold, data_mode,
                        source_snapshot_label, payload, created_at, updated_at
                    ) VALUES (
                        'T-CLEANUP', 'CAMP:T-CLEANUP', 'Cleanup Teacher', 'UTC',
                        1, 'IN_PROGRESS', false, 12.5, 60, 'REAL',
                        'SOURCE_WIDE_CURRENT', '{}'::jsonb,
                        '2026-08-07T00:00:00+00',
                        '2026-08-07T00:00:00+00'
                    );
                    INSERT INTO public.score_accounts (
                        account_id, teacher_id, camp_enrollment_id, dimension,
                        current_score, minimum_score, weight,
                        score_rule_version, version, updated_at, payload
                    ) VALUES (
                        'T-CLEANUP:RELIABILITY', 'T-CLEANUP',
                        'CAMP:T-CLEANUP', 'RELIABILITY', 12.5, 0, 0,
                        'cleanup-v1', 7, '2026-08-07T00:00:00+00',
                        '{"source_mode":"PERSISTED_CURRENT"}'::jsonb
                    );
                    INSERT INTO public.score_component_accounts (
                        component_account_id, teacher_id, camp_enrollment_id,
                        dimension, component_code, source_scope, source_metric,
                        unit_count, points_per_unit, current_score,
                        lesson_attributed_count, lesson_attributed_score,
                        unattributed_score, reconciliation_status,
                        score_rule_version, projection_revision,
                        calculated_at, payload
                    ) VALUES (
                        'T-CLEANUP:REL_ABSENT', 'T-CLEANUP',
                        'CAMP:T-CLEANUP', 'RELIABILITY', 'REL_ABSENT',
                        'TEACHER', 'absent_cnt', 1, -1, 12.5,
                        0, 0, 12.5, 'MATCHED', 'cleanup-v1', 9,
                        '2026-08-07T00:00:00+00',
                        '{"source_mode":"PERSISTED_CURRENT"}'::jsonb
                    )
                    """
                )
            )
        with engine.connect() as connection:
            score_before = connection.execute(
                text(
                    """
                    SELECT teacher_id, dimension, current_score,
                           score_rule_version, version, payload
                    FROM public.score_accounts
                    WHERE teacher_id = 'T-CLEANUP'
                    """
                )
            ).one()
            component_before = connection.execute(
                text(
                    """
                    SELECT teacher_id, dimension, component_code, source_scope,
                           source_metric, unit_count, points_per_unit,
                           current_score, lesson_attributed_count,
                           lesson_attributed_score, unattributed_score,
                           reconciliation_status, score_rule_version,
                           projection_revision, calculated_at, payload
                    FROM public.score_component_accounts
                    WHERE teacher_id = 'T-CLEANUP'
                    """
                )
            ).one()

        _run_alembic(
            backend_dir,
            database_url,
            "upgrade",
            "20260807_48_schema_cleanup",
        )
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO public.complaint_rule_imports (
                        source_sha256, source_filename, raw_rows, imported_at
                    ) VALUES (
                        repeat('b', 64),
                        'complaint-roundtrip.xlsx',
                        jsonb_build_array(jsonb_build_object(
                            'source_row_number', 3,
                            '一级分类', '关于老师',
                            '二级分类', '教学技巧问题',
                            '三级分类', '无纠错',
                            'P级', 'P4',
                            'Course Title in the Learning Hub',
                                E'  Ｃｏｕｒｓｅ   One  ',
                            'link', E'\thttps://example.invalid/learn/1\n'
                        )),
                        '2026-08-07T01:00:00+00'
                    );
                    INSERT INTO public.complaint_category_rules (
                        rule_id, source_sha256, source_row_number,
                        category_l1, category_l2, category_l3,
                        category_l3_normalized, source_level, severity_rank,
                        default_route, learning_title, learning_url, created_at
                    ) VALUES (
                        'CR-ROUNDTRIP-3', repeat('b', 64), 3,
                        '关于老师', '教学技巧问题', '无纠错', '无纠错',
                        'P4', 4, 'TEACHER_TASK', 'Course One',
                        'https://example.invalid/learn/1',
                        '2026-08-07T01:00:00+00'
                    );
                    INSERT INTO public.operator_accounts (
                        operator_id, username, display_name, password_hash,
                        is_active, created_at, updated_at
                    ) VALUES (
                        'OP-ROUNDTRIP', 'roundtrip.operator',
                        'Roundtrip Operator', 'not-a-real-password-hash', true,
                        '2026-08-07T01:00:00+00',
                        '2026-08-07T01:00:00+00'
                    );
                    INSERT INTO public.operator_sessions (
                        session_id, operator_id, token_hash, created_at,
                        expires_at, last_seen_at, revoked_at
                    ) VALUES (
                        'SESSION-ROUNDTRIP', 'OP-ROUNDTRIP', repeat('c', 64),
                        '2026-08-07T01:00:00+00',
                        '2026-08-08T01:00:00+00',
                        '2026-08-07T01:00:00+00', NULL
                    )
                    """
                )
            )

        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE public.complaint_category_rules
                    SET learning_title = 'does-not-match-raw-source'
                    WHERE rule_id = 'CR-ROUNDTRIP-3'
                    """
                )
            )
        _run_alembic_expect_failure(
            backend_dir,
            database_url,
            "complaint rule cannot be losslessly restored from raw_rows",
            "upgrade",
            "head",
        )
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE public.complaint_category_rules
                    SET learning_title = 'Course One'
                    WHERE rule_id = 'CR-ROUNDTRIP-3';
                    UPDATE public.operator_sessions
                    SET last_seen_at = created_at + interval '1 second'
                    WHERE session_id = 'SESSION-ROUNDTRIP'
                    """
                )
            )
        _run_alembic_expect_failure(
            backend_dir,
            database_url,
            "operator session last_seen_at is not redundant",
            "upgrade",
            "head",
        )
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE public.operator_sessions
                    SET last_seen_at = created_at
                    WHERE session_id = 'SESSION-ROUNDTRIP'
                    """
                )
            )

        _run_alembic(
            backend_dir,
            database_url,
            "upgrade",
            "20260807_49_unused_columns",
        )
        with engine.connect() as connection:
            assert connection.execute(
                text(
                    """
                    SELECT count(*)
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND (
                            (table_name = 'complaint_category_rules'
                             AND column_name IN ('learning_title', 'learning_url'))
                         OR (table_name = 'operator_sessions'
                             AND column_name = 'last_seen_at')
                      )
                    """
                )
            ).scalar_one() == 0
            assert connection.execute(
                text(
                    """
                    SELECT
                        raw_rows -> 0 ->> 'Course Title in the Learning Hub',
                        raw_rows -> 0 ->> 'link'
                    FROM public.complaint_rule_imports
                    WHERE source_sha256 = repeat('b', 64)
                    """
                )
            ).one() == (
                "  Ｃｏｕｒｓｅ   One  ",
                "\thttps://example.invalid/learn/1\n",
            )

        _run_alembic(
            backend_dir,
            database_url,
            "downgrade",
            "20260807_48_schema_cleanup",
        )
        with engine.connect() as connection:
            assert connection.execute(
                text(
                    """
                    SELECT learning_title, learning_url
                    FROM public.complaint_category_rules
                    WHERE rule_id = 'CR-ROUNDTRIP-3'
                    """
                )
            ).one() == (
                "Course One",
                "https://example.invalid/learn/1",
            )
            assert connection.execute(
                text(
                    """
                    SELECT last_seen_at = created_at
                    FROM public.operator_sessions
                    WHERE session_id = 'SESSION-ROUNDTRIP'
                    """
                )
            ).scalar_one() is True

        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    DELETE FROM public.complaint_category_rules
                    WHERE rule_id = 'CR-ROUNDTRIP-3';
                    DELETE FROM public.complaint_rule_imports
                    WHERE source_sha256 = repeat('b', 64)
                    """
                )
            )

        _run_alembic(
            backend_dir,
            database_url,
            "upgrade",
            "20260807_49_unused_columns",
        )
        with engine.connect() as connection:
            assert connection.execute(
                text(
                    """
                    SELECT teacher_id, dimension, current_score,
                           score_rule_version, version, payload
                    FROM public.score_accounts
                    WHERE teacher_id = 'T-CLEANUP'
                    """
                )
            ).one() == score_before
            assert connection.execute(
                text(
                    """
                    SELECT teacher_id, dimension, component_code, source_scope,
                           source_metric, unit_count, points_per_unit,
                           current_score, lesson_attributed_count,
                           lesson_attributed_score, unattributed_score,
                           reconciliation_status, score_rule_version,
                           projection_revision, calculated_at, payload
                    FROM public.score_component_accounts
                    WHERE teacher_id = 'T-CLEANUP'
                    """
                )
            ).one() == component_before

        _run_alembic(
            backend_dir,
            database_url,
            "downgrade",
            "20260807_46_teacher_g01_source",
        )
        with engine.connect() as connection:
            assert _schema_signature(connection) == baseline
            assert connection.execute(
                text(
                    """
                    SELECT account_id, camp_enrollment_id,
                           minimum_score, weight
                    FROM public.score_accounts
                    WHERE teacher_id = 'T-CLEANUP'
                    """
                )
            ).one() == (
                "T-CLEANUP:RELIABILITY",
                "CAMP:T-CLEANUP",
                0.0,
                0.0,
            )
            assert connection.execute(
                text(
                    """
                    SELECT component_account_id, camp_enrollment_id,
                           source_teacher_batch_id, source_lesson_batch_id
                    FROM public.score_component_accounts
                    WHERE teacher_id = 'T-CLEANUP'
                    """
                )
            ).one() == (
                "T-CLEANUP:REL_ABSENT",
                "CAMP:T-CLEANUP",
                None,
                None,
            )

        # The current chain becomes forward-only at rev54. Prepare only the
        # personalized catalog rows that an operational database seeds outside
        # Alembic, then prove the remaining forward upgrade and ORM drift check.
        # Existing G01/G08 rows must continue through the migrations unchanged
        # by this fixture; the ephemeral cluster is discarded afterward instead
        # of pretending a rev54 rollback is possible.
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO public.task_templates (
                        row_id, template_id, template_version, status, revision,
                        output_type, execution_owner, integration_mode,
                        external_task_template_code, source_mode, payload,
                        created_by, updated_by, created_at, updated_at
                    ) VALUES (
                        'P-FB-NEGATIVE:v1', 'P-FB-NEGATIVE', 1, 'PUBLISHED', 5,
                        'TEACHER_TASK', 'TEACHER_APP', 'OUTBOUND_MANAGED',
                        'P-FB-NEGATIVE', 'REAL',
                        jsonb_build_object(
                            'template_id', 'P-FB-NEGATIVE',
                            'title', 'Feedback Improvement',
                            'category', 'PERSONALIZED_IMPROVEMENT',
                            'content_status', 'READY',
                            'score_type', 'ZERO',
                            'score_value', 0,
                            'how_summary',
                                'Complete the learning activity assigned for the feedback issue shown in the task reason.',
                            'completion_standard',
                                'The teacher app marks the matching learning activity as completed.'
                        ),
                        'POSTGRES_ROUND_TRIP_FIXTURE',
                        'POSTGRES_ROUND_TRIP_FIXTURE',
                        '2026-08-11T00:00:00+00',
                        '2026-08-11T00:00:00+00'
                    ),
                    (
                        'P-REL-MEMO:v1', 'P-REL-MEMO', 1, 'PUBLISHED', 2,
                        'TEACHER_TASK', 'TEACHER_APP', 'OUTBOUND_MANAGED',
                        'P-REL-MEMO', 'REAL',
                        jsonb_build_object(
                            'template_id', 'P-REL-MEMO',
                            'title', 'Lesson Memo Improvement',
                            'category', 'PERSONALIZED_IMPROVEMENT',
                            'content_status', 'READY',
                            'score_type', 'ZERO',
                            'score_value', 0,
                            'why_template',
                                'A completed lesson was recorded with an unfilled Lesson Memo.',
                            'benefit',
                                'This task carries no points. It closes the identified Lesson Memo reliability gap.'
                        ),
                        'POSTGRES_ROUND_TRIP_FIXTURE',
                        'POSTGRES_ROUND_TRIP_FIXTURE',
                        '2026-08-11T00:00:00+00',
                        '2026-08-11T00:00:00+00'
                    ),
                    (
                        'P-REL-ATTENDANCE:v1', 'P-REL-ATTENDANCE', 1,
                        'PUBLISHED', 2,
                        'TEACHER_TASK', 'TEACHER_APP', 'OUTBOUND_MANAGED',
                        'P-REL-ATTENDANCE', 'REAL',
                        jsonb_build_object(
                            'template_id', 'P-REL-ATTENDANCE',
                            'title', 'Attendance Improvement',
                            'category', 'PERSONALIZED_IMPROVEMENT',
                            'content_status', 'READY',
                            'score_type', 'ZERO',
                            'score_value', 0,
                            'why_template',
                                'A lesson record contains a reliability issue such as absence, late arrival or early leave.'
                        ),
                        'POSTGRES_ROUND_TRIP_FIXTURE',
                        'POSTGRES_ROUND_TRIP_FIXTURE',
                        '2026-08-11T00:00:00+00',
                        '2026-08-11T00:00:00+00'
                    )
                    """
                )
            )
        _run_alembic(
            backend_dir,
            database_url,
            "upgrade",
            "20260819_63_dts_direct_privacy",
        )
        with engine.connect() as connection:
            assert connection.execute(
                text(
                    """
                    SELECT version_num
                    FROM public.alembic_version
                    """
                )
            ).scalar_one() == "20260819_63_dts_direct_privacy"
            assert connection.execute(
                text(
                    """
                    SELECT
                        row_id,
                        payload->>'ops_name_zh',
                        payload->>'title',
                        payload->>'why_template',
                        payload->>'how_summary',
                        payload->>'completion_standard',
                        payload->>'benefit'
                    FROM public.task_templates
                    WHERE row_id IN ('G06:v1', 'G09:v1', 'G10:v1')
                    ORDER BY row_id
                    """
                )
            ).all() == [
                (
                    "G06:v1",
                    "TTP 入门",
                    "TTP Orientation",
                    "Understand TTP and its key business scenarios.",
                    "Watch the in-platform TTP video and confirm every item "
                    "in the learning checklist.",
                    "The TTP video is watched in full and every published "
                    "checklist item is confirmed.",
                    "You understand the key TTP workflow and commitments.",
                ),
                (
                    "G09:v1",
                    "Global Communicator 培训",
                    "Global Communicator Training",
                    "Learn the core Global Communicator teaching flow.",
                    "Complete the configured in-platform videos and quiz.",
                    "All configured videos and quiz requirements pass.",
                    "You can now confidently prepare for a Global "
                    "Communicator lesson.",
                ),
                (
                    "G10:v1",
                    "SET 教学基础",
                    "SET Teaching Fundamentals",
                    "Learn the fundamentals of SET teaching.",
                    "Watch the in-platform Mock video slot and complete the "
                    "five-question Mock check.",
                    "The Mock video is watched in full and the five-question "
                    "check reaches 80%.",
                    "You understand the SET teaching foundation.",
                ),
            ]

        _run_alembic(backend_dir, database_url, "upgrade", "head")
        with engine.begin() as connection:
            assert connection.execute(
                text("SELECT version_num FROM public.alembic_version")
            ).scalar_one() == "20260825_105_pipeline_read_acl"
            assert connection.execute(
                text(
                    """
                    SELECT
                      has_table_privilege(
                        'tit_growth_app','public.outbox_events','SELECT'
                      ),
                      has_table_privilege(
                        'tit_growth_app','public.outbox_events','INSERT'
                      ),
                      has_table_privilege(
                        'tit_growth_app','public.outbox_events','UPDATE'
                      ),
                      has_table_privilege(
                        'tit_growth_app','public.outbox_events','DELETE'
                      ),
                      has_table_privilege(
                        'tit_growth_app','public.outbox_events','TRUNCATE'
                      )
                    """
                )
            ).one() == (True, True, True, True, False)
            connection.execute(text("SET LOCAL ROLE tit_growth_app"))
            assert connection.execute(
                text(
                    """
                    SELECT outbox_id
                    FROM public.outbox_events
                    WHERE status='PENDING'
                    ORDER BY available_at,created_at,outbox_id
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                    """
                )
            ).all() == []
            assert connection.execute(
                text(
                    "SELECT qualification_grants_enabled "
                    "FROM public.dts_pipeline_control "
                    "WHERE control_id='PRIMARY'"
                )
            ).scalar_one() is False
            assert connection.execute(
                text(
                    """
                    SELECT count(*) FROM public.teachers
                    WHERE teacher_id = 'T-CLEANUP'
                    """
                )
            ).scalar_one() == 0
            assert connection.execute(
                text(
                    """
                    SELECT count(*)
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'lesson_source_wide'
                    """
                )
            ).scalar_one() == 23
            assert connection.execute(
                text(
                    """
                    SELECT count(*)
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'lesson_source_wide'
                      AND column_name = '假早退'
                    """
                )
            ).scalar_one() == 0
            lesson_view_definition = connection.execute(
                text(
                    """
                    SELECT pg_get_viewdef(
                        'public.teacher_lesson_score_current'::regclass,
                        true
                    )
                    """
                )
            ).scalar_one()
            assert "is_false_early_leave" not in lesson_view_definition
            assert "假早退" not in lesson_view_definition
            assert connection.execute(
                text(
                    """
                    SELECT
                        row_id,
                        payload->>'ops_name_zh',
                        payload->>'title',
                        payload->>'why_template',
                        payload->>'how_summary',
                        payload->>'completion_standard',
                        payload->>'benefit'
                    FROM public.task_templates
                    WHERE row_id IN ('G06:v1', 'G09:v1', 'G10:v1')
                    ORDER BY row_id
                    """
                )
            ).all() == [
                (
                    "G06:v1",
                    "TTP 入门",
                    "TTP Orientation",
                    "Understand TTP and its key business scenarios.",
                    "Complete the TTP video and Quiz in Kuozhi.",
                    "The TTP video reaches 100% progress and the Quiz is "
                    "completed in Kuozhi.",
                    "You understand the key TTP workflow and commitments.",
                ),
                (
                    "G09:v1",
                    "Global Communicator 培训",
                    "Global Communicator Training",
                    "Learn the core Global Communicator teaching flow.",
                    "Complete all six Global Communicator Sample Lessons "
                    "videos in Kuozhi.",
                    "All six required videos reach 100% progress in Kuozhi.",
                    "You can now confidently prepare for a Global "
                    "Communicator lesson.",
                ),
                (
                    "G10:v1",
                    "SET 教学基础",
                    "SET Teaching Fundamentals",
                    "Learn the fundamentals of SET teaching.",
                    "Complete the three SET videos and their three paired "
                    "quizzes in Kuozhi.",
                    "All three required videos and all three paired quizzes "
                    "reach 100% progress in Kuozhi.",
                    "You understand the SET teaching foundation.",
                ),
            ]
            assert connection.execute(
                text(
                    """
                    SELECT count(*)
                    FROM pg_trigger
                    WHERE tgrelid = ANY (ARRAY[
                        'public.score_entries'::regclass,
                        'public.idempotency_records'::regclass,
                        'public.config_publication_audits'::regclass,
                        'public.ops_decisions'::regclass
                    ])
                      AND tgname = 'guard_runtime_append_only_fact'
                      AND NOT tgisinternal
                    """
                )
            ).scalar_one() == 4
            connection.execute(text("SET LOCAL ROLE tit_growth_app"))
            connection.execute(
                text(
                    """
                    INSERT INTO public.idempotency_records (
                        scope, idempotency_key, request_hash, resource_id,
                        response_payload, created_at
                    ) VALUES (
                        'ACL-APPEND-ONLY-PROBE', 'K1', repeat('a', 64), 'R1',
                        '{}'::jsonb, now()
                    )
                    """
                )
            )

        for forbidden_statement in (
            """
            UPDATE public.idempotency_records
            SET request_hash = repeat('b', 64)
            WHERE scope = 'ACL-APPEND-ONLY-PROBE'
              AND idempotency_key = 'K1'
            """,
            """
            DELETE FROM public.idempotency_records
            WHERE scope = 'ACL-APPEND-ONLY-PROBE'
              AND idempotency_key = 'K1'
            """,
        ):
            with pytest.raises(DBAPIError, match="append-only for runtime roles"):
                with engine.begin() as connection:
                    connection.execute(text("SET LOCAL ROLE tit_growth_app"))
                    connection.execute(text(forbidden_statement))

        with engine.connect() as connection:
            assert connection.execute(
                text(
                    """
                    SELECT count(*), min(request_hash)
                    FROM public.idempotency_records
                    WHERE scope = 'ACL-APPEND-ONLY-PROBE'
                      AND idempotency_key = 'K1'
                    """
                )
            ).one() == (1, "a" * 64)
        _run_alembic(backend_dir, database_url, "check")
    finally:
        engine.dispose()
        subprocess.run(
            [pg_ctl, "-D", str(data_dir), "-m", "immediate", "-w", "stop"],
            check=True,
            capture_output=True,
            text=True,
        )
