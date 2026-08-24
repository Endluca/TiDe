from __future__ import annotations

import json
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

from app.dts_v2_domain_aggregate import (
    build_domain_aggregate_identity_v2,
    canonical_domain_state_v2,
)


POSTGRES_BINARIES = ("initdb", "pg_ctl", "postgres")
REVISION_80 = "20260822_80_dts_v2_dirty_queue"
REVISION_81 = "20260822_81_dts_v2_domain_facts"
RULE_SHA = "a" * 64
RULE_ID = f"complaint-rule:{RULE_SHA}:1"
DOM_STUDENT_TOKEN = "dom:v1:" + "b" * 64


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


def _source_position(*, record_id: str, offset: int) -> str:
    return json.dumps(
        {
            "v": 1,
            "source_timestamp": "2026-08-22T00:00:00.000000Z",
            "record_id_type": "numeric",
            "record_id": record_id,
            "source_partition_epoch_id": "epoch-rev81",
            "topic": "topic-rev81",
            "partition_id": 0,
            "offset_value": offset,
        },
        separators=(",", ":"),
    )


def _seed_revision_80_shape(connection) -> None:
    connection.execute(
        text(
            f"""
            CREATE EXTENSION IF NOT EXISTS pgcrypto;
            CREATE ROLE tit_growth_app LOGIN NOINHERIT NOSUPERUSER
                NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
            CREATE ROLE tit_dts_ingest_runtime LOGIN NOINHERIT NOSUPERUSER
                NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
            CREATE ROLE tit_dts_domain_projector_runtime LOGIN NOINHERIT
                NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
            CREATE ROLE rev81_public_probe LOGIN NOINHERIT NOSUPERUSER
                NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;

            CREATE TABLE public.alembic_version (
                version_num varchar(64) PRIMARY KEY
            );
            INSERT INTO public.alembic_version VALUES ('{REVISION_80}');

            CREATE FUNCTION public.dts_canonical_json_v1(value jsonb)
            RETURNS text
            LANGUAGE plpgsql
            IMMUTABLE
            STRICT
            PARALLEL SAFE
            SET search_path = pg_catalog, public
            AS $function$
            DECLARE result text;
            BEGIN
                CASE jsonb_typeof(value)
                    WHEN 'object' THEN
                        SELECT '{{' || coalesce(string_agg(
                            to_jsonb(item.key)::text || ':' ||
                                public.dts_canonical_json_v1(item.value),
                            ',' ORDER BY convert_to(item.key, 'UTF8')
                        ), '') || '}}'
                        INTO result
                        FROM jsonb_each(value) AS item(key, value);
                    WHEN 'array' THEN
                        SELECT '[' || coalesce(string_agg(
                            public.dts_canonical_json_v1(item.value),
                            ',' ORDER BY item.ordinality
                        ), '') || ']'
                        INTO result
                        FROM jsonb_array_elements(value)
                             WITH ORDINALITY AS item(value, ordinality);
                    ELSE result := value::text;
                END CASE;
                RETURN result;
            END
            $function$;

            CREATE FUNCTION public.dts_canonical_json_sha256_v1(value jsonb)
            RETURNS text
            LANGUAGE sql
            IMMUTABLE
            STRICT
            PARALLEL SAFE
            SET search_path = pg_catalog, public
            AS $function$
                SELECT encode(
                    sha256(convert_to(
                        public.dts_canonical_json_v1(value), 'UTF8'
                    )),
                    'hex'
                )
            $function$;

            REVOKE ALL ON FUNCTION
                public.dts_canonical_json_v1(jsonb),
                public.dts_canonical_json_sha256_v1(jsonb)
            FROM PUBLIC;

            CREATE TABLE public.source_courses (
                source_region varchar(8) NOT NULL,
                source_appoint_id varchar(512) NOT NULL,
                PRIMARY KEY (source_region, source_appoint_id)
            );
            CREATE TABLE public.source_course_participations (
                source_region varchar(8) NOT NULL,
                source_appoint_id varchar(512) NOT NULL,
                participation_seq integer NOT NULL,
                PRIMARY KEY (
                    source_region, source_appoint_id, participation_seq
                ),
                FOREIGN KEY (source_region, source_appoint_id)
                    REFERENCES public.source_courses (
                        source_region, source_appoint_id
                    ) DEFERRABLE INITIALLY DEFERRED
            );
            CREATE TABLE public.complaint_rule_imports (
                source_sha256 varchar(64) PRIMARY KEY
            );
            CREATE TABLE public.complaint_category_rules (
                rule_id varchar(160) PRIMARY KEY,
                source_sha256 varchar(64) NOT NULL
                    REFERENCES public.complaint_rule_imports(source_sha256),
                source_row_number integer NOT NULL,
                category_l3_normalized varchar(500) NOT NULL,
                severity_rank integer NOT NULL,
                UNIQUE (source_sha256, category_l3_normalized)
            );

            INSERT INTO public.source_courses VALUES
                ('dom','100'), ('ovs','100');
            INSERT INTO public.source_course_participations VALUES
                ('dom','100',1), ('ovs','100',1);
            INSERT INTO public.complaint_rule_imports VALUES ('{RULE_SHA}');
            INSERT INTO public.complaint_category_rules VALUES (
                '{RULE_ID}','{RULE_SHA}',1,'迟到',2
            );
            """
        )
    )


def _insert_valid_rows(connection) -> None:
    label_position = _source_position(record_id="10", offset=10)
    complaint_position = _source_position(record_id="20", offset=20)
    connection.execute(
        text(
            """
            INSERT INTO public.source_course_labels (
                source_region,source_log_id,source_log_id_type,
                source_log_id_numeric,source_log_id_text,source_appoint_id,
                label_id,label_id_type,label_id_numeric,label_id_text,
                label_name_snapshot,create_time,dt,source_position,
                source_row_revision,evidence_status,evidence_error_code,
                is_deleted,source_version
            ) VALUES (
                'dom','10','NUMERIC',10,NULL,'100',
                '5','NUMERIC',5,NULL,'灯光过暗/亮',now(),now(),
                CAST(:label_position AS jsonb),1,'CONFIRMED',NULL,false,
                jsonb_build_object(
                    'source_table','dom_grading_label_log',
                    'source_row_revision',1
                )
            ),(
                'dom','11','NUMERIC',11,NULL,'100',
                '5','NUMERIC',5,NULL,'旧名称',now(),now(),
                CAST(:tombstone_position AS jsonb),2,'CONFIRMED',NULL,true,
                jsonb_build_object(
                    'source_table','dom_grading_label_log',
                    'source_row_revision',2
                )
            )
            """
        ),
        {
            "label_position": label_position,
            "tombstone_position": _source_position(record_id="11", offset=11),
        },
    )
    connection.execute(
        text(
            f"""
            INSERT INTO public.source_course_complaints (
                source_region,source_complaint_id,source_complaint_id_type,
                source_complaint_id_numeric,source_complaint_id_text,
                source_appoint_id,
                source_teacher_id,source_teacher_id_type,
                source_teacher_id_numeric,source_teacher_id_text,
                complaint_type,complaint_type_type,
                complaint_type_numeric,complaint_type_text,
                complaint_type_child,complaint_type_child_type,
                complaint_type_child_numeric,complaint_type_child_text,
                complaint_type_grandson,complaint_type_grandson_type,
                complaint_type_grandson_numeric,complaint_type_grandson_text,
                approve,validity,add_time,course_date,is_valid,
                complaint_rule_id,source_sha256,severity_rank,
                category_l1_snapshot,category_l2_snapshot,
                category_l3_snapshot,category_l3_normalized,
                evidence_status,evidence_error_code,is_deleted,
                source_version,source_position,source_row_revision
            ) VALUES (
                'dom','20','NUMERIC',20,NULL,'100',
                'T1','TEXT',NULL,'T1',
                '13','NUMERIC',13,NULL,
                '50','NUMERIC',50,NULL,
                '90','NUMERIC',90,NULL,
                'y',1,now(),DATE '2026-08-22',true,
                '{RULE_ID}','{RULE_SHA}',2,
                '投诉','出席问题','迟到','迟到',
                'CONFIRMED',NULL,false,
                jsonb_build_object(
                    'source_table','dom_complaint','source_row_revision',1
                ),
                CAST(:complaint_position AS jsonb),1
            )
            """
        ),
        {"complaint_position": complaint_position},
    )
    connection.execute(
        text(
            """
            INSERT INTO public.source_course_fact_current (
                source_region,source_appoint_id,
                current_grading_source_id,current_grading_source_id_type,
                current_grading_source_id_numeric,current_grading_source_id_text,
                grading_classification,negative_score,
                grading_evidence_status,grading_error_code,
                latest_valid_complaint_id,latest_valid_complaint_id_type,
                latest_valid_complaint_id_numeric,
                latest_valid_complaint_id_text,
                has_complaint,has_valid_complaint,
                latest_category_l1_snapshot,latest_category_l2_snapshot,
                latest_category_l3_snapshot,
                complaint_evidence_status,complaint_error_code,
                is_camera_off,camera_evidence_status,
                is_cpu_usage_high,cpu_evidence_status,
                is_network_delay_high,network_evidence_status,
                source_version_vector,source_version_hash
            ) VALUES (
                'dom','100','30','NUMERIC',30,NULL,
                'POSITIVE',NULL,'CONFIRMED',NULL,
                '20','NUMERIC',20,NULL,true,true,
                '投诉','出席问题','迟到','CONFIRMED',NULL,
                false,'CONFIRMED',NULL,'SOURCE_MISSING',
                NULL,'SOURCE_MISSING',jsonb_build_object(),
                public.dts_canonical_json_sha256_v1(jsonb_build_object())
            )
            """
        )
    )
    connection.execute(
        text(
            """
            INSERT INTO public.source_participation_fact_current (
                source_region,source_appoint_id,participation_seq,
                is_late,late_evidence_status,is_early,early_evidence_status,
                penalty_source_keys,penalty_source_keys_hash,
                source_version_vector,source_version_hash
            ) VALUES (
                'dom','100',1,false,'CONFIRMED',false,'CONFIRMED',
                '[]'::jsonb,
                public.dts_canonical_json_sha256_v1('[]'::jsonb),
                jsonb_build_object(),
                public.dts_canonical_json_sha256_v1(jsonb_build_object())
            )
            """
        )
    )

    identity = build_domain_aggregate_identity_v2(
        "COURSE",
        {"source_region": "dom", "source_appoint_id": "100"},
    )
    _, state_sha = canonical_domain_state_v2({})
    connection.execute(
        text(
            """
            INSERT INTO public.domain_aggregate_revisions (
                aggregate_type,aggregate_id,canonical_key,
                canonical_key_sha256,revision,last_source_row_revision,
                last_source_position,aggregate_state,aggregate_state_sha256
            ) VALUES (
                :aggregate_type,:aggregate_id,CAST(:canonical_key AS jsonb),
                :canonical_key_sha256,1,1,
                CAST(:source_position AS jsonb),jsonb_build_object(),:state_sha
            )
            """
        ),
        {
            "aggregate_type": identity.aggregate_type,
            "aggregate_id": identity.aggregate_id,
            "canonical_key": identity.canonical_key_json,
            "canonical_key_sha256": identity.aggregate_id.rsplit(":", 1)[1],
            "source_position": complaint_position,
            "state_sha": state_sha,
        },
    )


def _assert_statement_rejected(engine, statement: str, **parameters) -> None:
    with pytest.raises(DBAPIError):
        with engine.begin() as connection:
            connection.execute(text(statement), parameters)


@pytest.mark.skipif(
    not _postgres_tools_available(),
    reason="local PostgreSQL binaries are required for rev81 schema tests",
)
def test_revision_81_real_postgres_constraints_acl_and_downgrade(
    tmp_path: Path,
) -> None:
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
            _seed_revision_80_shape(connection)
        _run_alembic(backend_dir, database_url, "upgrade", REVISION_81)

        with engine.begin() as connection:
            assert connection.execute(
                text("SELECT version_num FROM public.alembic_version")
            ).scalar_one() == REVISION_81
            _insert_valid_rows(connection)
            assert connection.execute(
                text(
                    """
                    SELECT count(*),count(*) FILTER (WHERE is_deleted)
                    FROM public.source_course_labels
                    """
                )
            ).one() == (2, 1)

            privilege_rows = connection.execute(
                text(
                    """
                    SELECT role_name,table_name,
                           has_table_privilege(
                               role_name,'public.' || table_name,'INSERT'
                           ) AS can_insert,
                           has_table_privilege(
                               role_name,'public.' || table_name,'UPDATE'
                           ) AS can_update,
                           has_table_privilege(
                               role_name,'public.' || table_name,'DELETE'
                           ) AS can_delete
                    FROM (VALUES
                        ('tit_growth_app','source_course_labels'),
                        ('tit_dts_ingest_runtime','source_course_labels'),
                        ('tit_dts_domain_projector_runtime',
                         'source_course_labels'),
                        ('tit_dts_domain_projector_runtime',
                         'domain_aggregate_revisions')
                    ) AS expected(role_name,table_name)
                    ORDER BY role_name,table_name
                    """
                )
            ).all()
            assert privilege_rows == [
                (
                    "tit_dts_domain_projector_runtime",
                    "domain_aggregate_revisions",
                    False,
                    False,
                    False,
                ),
                (
                    "tit_dts_domain_projector_runtime",
                    "source_course_labels",
                    True,
                    True,
                    False,
                ),
                (
                    "tit_dts_ingest_runtime",
                    "source_course_labels",
                    False,
                    False,
                    False,
                ),
                (
                    "tit_growth_app",
                    "source_course_labels",
                    False,
                    False,
                    False,
                ),
            ]

        _assert_statement_rejected(
            engine,
            """
            INSERT INTO public.source_course_labels (
                source_region,source_log_id,source_log_id_type,
                source_log_id_numeric,source_log_id_text,source_appoint_id,
                label_id,label_id_type,label_id_numeric,label_id_text,
                source_position,source_row_revision,evidence_status,
                is_deleted,source_version
            ) VALUES (
                'dom','12','NUMERIC',NULL,'12','100',
                '5','NUMERIC',5,NULL,CAST(:position AS jsonb),
                1,'CONFIRMED',false,jsonb_build_object()
            )
            """,
            position=_source_position(record_id="12", offset=12),
        )
        _assert_statement_rejected(
            engine,
            """
            INSERT INTO public.source_course_labels (
                source_region,source_log_id,source_log_id_type,
                source_log_id_numeric,source_log_id_text,source_appoint_id,
                label_id,label_id_type,label_id_numeric,label_id_text,
                source_position,source_row_revision,evidence_status,
                is_deleted,source_version
            ) VALUES (
                'dom','13','NUMERIC',13,NULL,'100',
                '5','NUMERIC',5,NULL,jsonb_build_object(),
                1,'CONFIRMED',false,jsonb_build_object()
            )
            """,
        )
        _assert_statement_rejected(
            engine,
            """
            INSERT INTO public.source_participation_fact_current (
                source_region,source_appoint_id,participation_seq,
                is_late,late_evidence_status,is_early,early_evidence_status,
                penalty_source_keys,penalty_source_keys_hash,
                source_version_vector,source_version_hash
            ) VALUES (
                'dom','100',99,false,'CONFIRMED',false,'CONFIRMED',
                '[]'::jsonb,
                public.dts_canonical_json_sha256_v1('[]'::jsonb),
                jsonb_build_object(),
                public.dts_canonical_json_sha256_v1(jsonb_build_object())
            )
            """,
        )
        _assert_statement_rejected(
            engine,
            """
            INSERT INTO public.source_course_fact_current (
                source_region,source_appoint_id,grading_classification,
                grading_evidence_status,grading_error_code,
                complaint_evidence_status,complaint_error_code,
                is_camera_off,camera_evidence_status,
                is_cpu_usage_high,cpu_evidence_status,
                is_network_delay_high,network_evidence_status,
                source_version_vector,source_version_hash
            ) VALUES (
                'ovs','100','UNCLASSIFIED','CONFIRMED',NULL,
                'CONFIRMED',NULL,false,'CONFIRMED',
                true,'CONFIRMED',NULL,'SOURCE_MISSING',jsonb_build_object(),
                public.dts_canonical_json_sha256_v1(jsonb_build_object())
            )
            """,
        )

        invalid_key = json.dumps(
            {
                "source_region": "dom",
                "teacher_id": "T1",
                "student_token": "raw-student-1",
            },
            separators=(",", ":"),
        )
        _assert_statement_rejected(
            engine,
            """
            WITH key AS (SELECT CAST(:key AS jsonb) AS value)
            INSERT INTO public.domain_aggregate_revisions (
                aggregate_type,aggregate_id,canonical_key,
                canonical_key_sha256,revision,aggregate_state,
                aggregate_state_sha256
            )
            SELECT 'TEACHER_STUDENT',
                   'v2:TEACHER_STUDENT:' ||
                       public.dts_canonical_json_sha256_v1(value),
                   value,public.dts_canonical_json_sha256_v1(value),1,
                   jsonb_build_object(),
                   public.dts_canonical_json_sha256_v1(jsonb_build_object())
            FROM key
            """,
            key=invalid_key,
        )
        valid_teacher_student_key = json.dumps(
            {
                "source_region": "dom",
                "teacher_id": "T1",
                "student_token": DOM_STUDENT_TOKEN,
            },
            separators=(",", ":"),
        )
        _assert_statement_rejected(
            engine,
            """
            WITH key AS (SELECT CAST(:key AS jsonb) AS value)
            INSERT INTO public.domain_aggregate_revisions (
                aggregate_type,aggregate_id,canonical_key,
                canonical_key_sha256,revision,aggregate_state,
                aggregate_state_sha256
            )
            SELECT 'TEACHER_STUDENT',
                   'v2:TEACHER_STUDENT:' ||
                       public.dts_canonical_json_sha256_v1(value),
                   value,public.dts_canonical_json_sha256_v1(value),1,
                   jsonb_build_object(),repeat('0',64)
            FROM key
            """,
            key=valid_teacher_student_key,
        )

        for role_name in (
            "tit_growth_app",
            "tit_dts_ingest_runtime",
            "rev81_public_probe",
        ):
            _assert_statement_rejected(
                engine,
                f"""
                SET LOCAL ROLE {role_name};
                INSERT INTO public.source_course_labels (
                    source_region,source_log_id,source_log_id_type,
                    source_log_id_numeric,source_log_id_text,source_appoint_id,
                    label_id,label_id_type,label_id_numeric,label_id_text,
                    source_position,source_row_revision,evidence_status,
                    is_deleted,source_version
                ) VALUES (
                    'dom','99','NUMERIC',99,NULL,'100',
                    '5','NUMERIC',5,NULL,CAST(:position AS jsonb),
                    1,'CONFIRMED',false,jsonb_build_object()
                )
                """,
                position=_source_position(record_id="99", offset=99),
            )

        _run_alembic(backend_dir, database_url, "downgrade", REVISION_80)
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT version_num FROM public.alembic_version")
            ).scalar_one() == REVISION_80
            assert connection.execute(
                text(
                    """
                    SELECT count(*)
                    FROM information_schema.tables
                    WHERE table_schema='public'
                      AND table_name IN (
                        'source_course_labels','source_course_complaints',
                        'source_course_fact_current',
                        'source_participation_fact_current',
                        'domain_aggregate_revisions'
                      )
                    """
                )
            ).scalar_one() == 0
            assert connection.execute(
                text("SELECT to_regclass('public.complaint_category_rules')")
            ).scalar_one() == "complaint_category_rules"
    finally:
        engine.dispose()
        subprocess.run(
            [pg_ctl, "-D", str(data_dir), "-m", "immediate", "-w", "stop"],
            check=True,
            capture_output=True,
            text=True,
        )
