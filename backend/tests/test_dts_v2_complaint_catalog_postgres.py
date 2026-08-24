from __future__ import annotations

import json

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.dts_v2_complaint_category_fanout import (
    PostgresDtsV2ComplaintCategoryFanout,
)
from app.dts_v2_complaint_rule_catalog import PostgresDtsV2ComplaintRuleCatalog
from app.dts_v2_domain_aggregate import DtsV2DomainRevisionStore
from test_dts_v2_ops_case_postgres import ops_case_postgres


RULE_SHA = "a" * 64
RULE_ID = f"complaint-rule:{RULE_SHA}:1"
NEXT_RULE_SHA = "b" * 64
NEXT_RULE_ID = f"complaint-rule:{NEXT_RULE_SHA}:1"


def test_protected_complaint_catalog_publish_replay_and_immutability(
    ops_case_postgres,
) -> None:
    admin, _worker, _recovery = ops_case_postgres
    with admin.begin() as connection:
        parameters = {
            "sha": RULE_SHA,
            "rule_id": RULE_ID,
            "raw_rows": json.dumps([{"source_row_number": 1}]),
        }
        connection.execute(
            text(
                """
                INSERT INTO public.complaint_rule_imports(
                  source_sha256,source_filename,raw_rows,imported_at,
                  status,publication_revision,activation_generation,
                  row_count,content_hash,published_at,retired_at
                ) VALUES (
                  :sha,'rules.xlsx',CAST(:raw_rows AS jsonb),
                  transaction_timestamp(),'DRAFT',1,NULL,1,
                  repeat('0',64),NULL,NULL
                )
                """
            ),
            parameters,
        )
        connection.execute(
            text(
                """
                INSERT INTO public.complaint_category_rules(
                  rule_id,source_sha256,source_row_number,
                  category_l1,category_l2,category_l3,
                  category_l3_normalized,source_level,severity_rank,
                  default_route,created_at
                ) VALUES (
                  :rule_id,:sha,1,'一级','二级','三级','三级',
                  'P2',2,'TEACHER_TASK',transaction_timestamp()
                )
                """
            ),
            parameters,
        )
        connection.execute(
            text(
                """
                UPDATE public.complaint_rule_imports
                SET content_hash=
                  public.complaint_rule_catalog_content_hash_v1(:sha)
                WHERE source_sha256=:sha
                """
            ),
            parameters,
        )
        health_before = connection.execute(
            text("SELECT public.dts_v2_complaint_catalog_health_v1()")
        ).scalar_one()
        assert health_before["ready"] is False

        connection.execute(
            text("SET LOCAL ROLE tit_dts_complaint_rule_publisher_runtime")
        )
        catalog = PostgresDtsV2ComplaintRuleCatalog()
        applied = catalog.publish(
            connection,
            source_sha256=RULE_SHA,
            expected_revision=0,
            idempotency_key="publish-a",
        )
        replayed = catalog.publish(
            connection,
            source_sha256=RULE_SHA,
            expected_revision=0,
            idempotency_key="publish-a",
        )
        assert applied["activation_generation"] == 1
        assert applied["replay_status"] == "APPLIED"
        assert replayed["replay_status"] == "REPLAYED"

        ready = connection.execute(
            text("SELECT public.dts_v2_complaint_catalog_health_v1()")
        ).scalar_one()
        assert ready["ready"] is True
        assert ready["source_sha256"] == RULE_SHA

    with admin.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO public.source_courses(
                  source_region,source_appoint_id,source_is_deleted,
                  evidence_status,appoint_evidence_status,
                  teacher_region_evidence_status,row_version,updated_at
                ) VALUES (
                  'ovs','complaint-course-1',false,'SOURCE_MISSING','SOURCE_MISSING',
                  'SOURCE_MISSING',1,transaction_timestamp()
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO public.source_course_complaints(
                  source_region,source_complaint_id,
                  source_complaint_id_type,source_complaint_id_numeric,
                  source_complaint_id_text,source_appoint_id,
                  complaint_type_grandson,complaint_type_grandson_type,
                  complaint_type_grandson_numeric,
                  complaint_rule_id,source_sha256,severity_rank,
                  category_l1_snapshot,category_l2_snapshot,
                  category_l3_snapshot,category_l3_normalized,
                  evidence_status,is_deleted,source_version,
                  source_position,source_row_revision
                ) VALUES (
                  'ovs','501','NUMERIC',501,NULL,'complaint-course-1',
                  '82','NUMERIC',82,:rule_id,:sha,2,
                  '一级','二级','三级','三级','CONFIRMED',false,
                  '{"source":"test"}'::jsonb,
                  jsonb_build_object(
                    'v',1,'source_timestamp',NULL,
                    'record_id_type','numeric','record_id','501',
                    'source_partition_epoch_id','category-fanout-test',
                    'topic','ovs-complaint','partition_id',0,
                    'offset_value',1
                  ),1
                )
                """
            ),
            {"rule_id": RULE_ID, "sha": RULE_SHA},
        )

    with admin.begin() as connection:
        connection.execute(
            text("SET LOCAL ROLE tit_dts_domain_projector_runtime")
        )
        category = DtsV2DomainRevisionStore().publish_change(
            connection,
            aggregate_type="COMPLAINT_CATEGORY",
            aggregate_key={"source_region": "dom", "category_id": "82"},
            aggregate_state={"definition": {"name": "三级"}},
            changed_fields=("definition",),
            source_row_revision=None,
            source_position=None,
            rule_version="complaint-category-test-v1",
            cutover_coverage_identity={
                "projection_mode": "SHADOW_BUILD",
                "projection_generation": 1,
                "trigger": {
                    "input_kind": "SCOPE_REVISION",
                    "input_identity": {
                        "source_region": "dom",
                        "source_table": "dom_complaint_cate",
                        "scope_kind": "CURRENT",
                        "scope_level": "GLOBAL",
                        "scope_key": "*",
                    },
                    "input_revision": 1,
                    "input_fingerprint": "c" * 64,
                    "scope_state": "COMPLETE",
                    "active_snapshot_id": "category-fanout-test",
                    "active_fence_hash": "d" * 64,
                },
            },
        )
        assert category.event is not None
        fanout = PostgresDtsV2ComplaintCategoryFanout().enqueue_linked_courses(
            connection,
            category_id="82",
            aggregate_revision=category.aggregate_revision,
            triggering_event_id=category.event.event_id,
        )
        assert fanout == {
            "courses_enqueued": 1,
            "courses_noop": 0,
            "courses_seen": 1,
        }

    with admin.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO public.complaint_rule_imports(
                  source_sha256,source_filename,raw_rows,imported_at,
                  status,publication_revision,activation_generation,
                  row_count,content_hash,published_at,retired_at
                ) VALUES (
                  :sha,'rules-v2.xlsx',CAST(:raw_rows AS jsonb),
                  transaction_timestamp(),'DRAFT',1,NULL,1,
                  repeat('0',64),NULL,NULL
                )
                """
            ),
            {
                "sha": NEXT_RULE_SHA,
                "raw_rows": json.dumps([{"source_row_number": 1}]),
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO public.complaint_category_rules(
                  rule_id,source_sha256,source_row_number,
                  category_l1,category_l2,category_l3,
                  category_l3_normalized,source_level,severity_rank,
                  default_route,created_at
                ) VALUES (
                  :rule_id,:sha,1,'一级','二级','三级','三级',
                  'P3',3,'OPS_CASE',transaction_timestamp()
                )
                """
            ),
            {"rule_id": NEXT_RULE_ID, "sha": NEXT_RULE_SHA},
        )
        connection.execute(
            text(
                """
                UPDATE public.complaint_rule_imports SET content_hash=
                  public.complaint_rule_catalog_content_hash_v1(:sha)
                WHERE source_sha256=:sha
                """
            ),
            {"sha": NEXT_RULE_SHA},
        )
        connection.execute(
            text("SET LOCAL ROLE tit_dts_complaint_rule_publisher_runtime")
        )
        changed = PostgresDtsV2ComplaintRuleCatalog().publish(
            connection,
            source_sha256=NEXT_RULE_SHA,
            expected_revision=1,
            idempotency_key="publish-b",
        )
        assert changed["previous_source_sha256"] == RULE_SHA
        assert changed["activation_generation"] == 2
        assert changed["affected_category_count"] == 1
        assert changed["courses_seen"] == 1
        assert changed["courses_enqueued"] == 1

    with admin.connect() as connection:
        assert connection.execute(
            text(
                """
                SELECT count(*) FROM public.dts_dirty_key_inputs
                WHERE source_region='ovs' AND key_type='COURSE'
                  AND key_part_1='complaint-course-1'
                  AND input_kind='CATALOG_REVISION'
                """
            )
        ).scalar_one() == 1

    with pytest.raises(DBAPIError, match="COMPLAINT_RULE_CHILD_IMMUTABLE"):
        with admin.begin() as connection:
            connection.execute(
                text(
                    "UPDATE public.complaint_category_rules "
                    "SET severity_rank=3 WHERE rule_id=:rule_id"
                ),
                {"rule_id": RULE_ID},
            )
